# -*- coding: utf-8 -*-
"""audio_crop.py — 音频裁剪 / 人声分段 / 语言分段裁剪。

CLI 例
-
python audio_crop.py --mode crop   --audio in.wav --outdir out --start 30 --end 45
python audio_crop.py --mode energy --audio in.wav --outdir out --min-gap 1.0
python audio_crop.py --mode lang   --audio vocals.wav --outdir out --win 10 --hop 5
python audio_crop.py --mode lang   --audio vocals.wav --outdir out --dry-run
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lang_id import LanguageDetector, load_audio_mono16k, _dbfs   # noqa: E402

try:
    import soundfile as _sf
except Exception:                                                  # pragma: no cover
    _sf = None


# --------------------------------------------------------------------------
# 基础 IO
# --------------------------------------------------------------------------
def read_wav(path):
    """读成 (float32 [n] 或 [n,ch], sr)。优先保留原始采样率与声道。"""
    if _sf is None:
        y, sr = load_audio_mono16k(path)
        return y, sr
    try:
        data, sr = _sf.read(path, dtype="float32", always_2d=False)
        return np.asarray(data, dtype=np.float32), int(sr)
    except Exception:
        y, sr = load_audio_mono16k(path)
        return y, sr


def write_wav(path, data, sr, subtype="PCM_16"):
    """写 WAV。目录不存在会自动建。"""
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    if _sf is not None:
        _sf.write(path, data, sr, subtype=subtype)
        return path
    import wave
    a = np.asarray(data)
    if a.ndim == 2:
        a = a.mean(axis=1)
    pcm = np.clip(a, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2").tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm)
    return path


def slice_audio(data, sr, t0, t1):
    """按秒切片（自动夹到合法范围）。返回 (片段, 实际 t0, 实际 t1)。"""
    n = data.shape[0]
    i0 = max(0, min(n, int(round(t0 * sr))))
    i1 = max(0, min(n, int(round(t1 * sr))))
    if i1 <= i0:
        return None, t0, t0
    return data[i0:i1], i0 / float(sr), i1 / float(sr)


def fmt_tc(t):
    """时间码 mm-ss（文件名安全）。"""
    t = max(0.0, float(t))
    return "%02d-%05.2f" % (int(t // 60), t % 60)


# --------------------------------------------------------------------------
# 模式 2：按能量分段
# --------------------------------------------------------------------------
def segment_by_energy(wav_path, min_gap=1.0, frame=0.05, rel_db=-35.0, min_seg=0.5,
                      pad=0.10, sr=None):
    """按 RMS 能量把「有声段」切出来。"""
    data, file_sr = read_wav(wav_path)
    if data.ndim == 2:
        data = data.mean(axis=1)
    if sr is None:
        sr = file_sr
    n = len(data)
    fs = max(1, int(frame * sr))
    nf = n // fs
    if nf < 1:
        return [{"index": 0, "t0": 0.0, "t1": n / float(sr), "db": _dbfs(data)}]
    rms = np.sqrt(np.mean(np.square(data[:nf * fs].reshape(nf, fs)), axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-9))
    peak = float(np.percentile(db, 95))
    voiced = db > (peak + rel_db)              # rel_db 为负值
    segs = []
    i = 0
    while i < nf:
        if not voiced[i]:
            i += 1
            continue
        j = i
        gap = 0
        last = i
        while j < nf:
            if voiced[j]:
                last = j
                gap = 0
            else:
                gap += 1
                if gap * frame > min_gap:
                    break
            j += 1
        t0 = max(0.0, i * frame - pad)
        t1 = min(n / float(sr), (last + 1) * frame + pad)
        if t1 - t0 >= min_seg:
            segs.append({"t0": round(t0, 3), "t1": round(t1, 3)})
        i = j
    for k, s in enumerate(segs):
        s["index"] = k
    return segs


# --------------------------------------------------------------------------
# 模式 3：按语种分段（核心）
# --------------------------------------------------------------------------
def _loud_weights(wins, floor=0.05):
    """把每个窗的 dBFS 映射成**投票权重**（越响权重越高）。"""
    dbs = [float(w.get("db", -120.0)) for w in wins]
    ref = max(dbs) if dbs else -120.0
    out = []
    for d in dbs:
        d = max(ref - 40.0, min(ref, d))          # 只在最响窗以下 40 dB 内比较
        w = 10.0 ** ((d - ref) / 20.0)
        out.append(max(floor, min(1.0, w)))
    return out


def _seg_db(data, sr, t0, t1):
    """某时间区间内的 RMS dBFS（重叠解析用）。"""
    if data is None:
        return float("-inf")
    i0 = max(0, int(t0 * sr))
    i1 = min(len(data), int(t1 * sr))
    if i1 <= i0:
        return float("-inf")
    return _dbfs(data[i0:i1])


def resolve_overlaps_by_loudness(segments, wav_path=None, data=None, sr=None):
    """重叠解析：把可能**在时间上重叠**的分片，整理成互不重叠的划分。"""
    if not segments:
        return segments
    # 没有重叠 -> 直接返回，保证"无重叠时零行为变化"
    ordered = sorted(segments, key=lambda s: (s["t0"], s["t1"]))
    if all(a["t1"] <= b["t0"] + 1e-9 for a, b in zip(ordered, ordered[1:])):
        return segments

    if data is None and wav_path:
        data, sr = read_wav(wav_path)
        if data.ndim == 2:
            data = data.mean(axis=1)
    sr = sr or 16000

    bounds = sorted({round(s["t0"], 6) for s in segments} | {round(s["t1"], 6) for s in segments})
    pieces = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < 1e-6:
            continue
        mid = (a + b) / 2.0
        covering = [s for s in segments if s["t0"] <= mid < s["t1"]]
        if not covering:
            continue
        if len(covering) == 1:
            win = covering[0]
        else:
            win = max(covering, key=lambda s: _seg_db(data, sr, a, b))
        pieces.append({"t0": a, "t1": b, "lang": win.get("lang"),
                       "prob": win.get("prob", 0.0), "votes": win.get("votes", {}),
                       "n_windows": win.get("n_windows", 0),
                       "_n_overlap": len(covering)})
    # 合并相邻同语种
    merged = []
    for p in pieces:
        if merged and merged[-1]["lang"] == p["lang"]:
            merged[-1]["t1"] = p["t1"]
            merged[-1]["_n_overlap"] = max(merged[-1]["_n_overlap"], p["_n_overlap"])
        else:
            merged.append(dict(p))
    for k, s in enumerate(merged):
        s["index"] = k
        s["t0"] = round(s["t0"], 3)
        s["t1"] = round(s["t1"], 3)
        s["overlap_resolved"] = s.pop("_n_overlap") > 1
    return merged


def _time_vote_units(wins, voiced, loud_weights, topk=3):
    """逐时间点的「音量优先」语种表决。"""
    edges = sorted({round(w["t0"], 6) for w in wins} | {round(w["t1"], 6) for w in wins})
    units = []
    for a, b in zip(edges, edges[1:]):
        if b - a < 1e-6:
            continue
        mid = (a + b) / 2.0
        tally = {}
        dbmax = float("-inf")
        for i, w in enumerate(wins):
            if not (w["t0"] <= mid < w["t1"]) or not voiced[i]:
                continue
            dbmax = max(dbmax, float(w.get("db", -120.0)))
            for code, p in (w.get("top") or [])[:topk]:
                if code in ("unknown", "?"):
                    continue
                tally[code] = tally.get(code, 0.0) + float(p) * loud_weights[i]
        if not tally:
            units.append({"t0": a, "t1": b, "code": None, "prob": 0.0, "ok": False,
                          "db": dbmax if dbmax != float("-inf") else -120.0,
                          "top": [], "votes": {}})
            continue
        ordered = sorted(tally.items(), key=lambda kv: -kv[1])
        tot = sum(tally.values())
        code, best = ordered[0]
        units.append({"t0": a, "t1": b, "code": code,
                      "prob": round(best / tot, 4) if tot > 0 else 0.0, "ok": True,
                      "db": dbmax, "top": ordered[:5],
                      "votes": {k: round(v, 3) for k, v in ordered}})
    return units


def _fill_and_smooth(labels, voiced=None, kernel=3):

    """补齐「有声但判不准」的窗，并做多数投票平滑。"""
    n = len(labels)
    if voiced is None:
        voiced = [True] * n
    out = list(labels)

    # 1) 只填「有声但无标签」的窗：取**距离最近**的有标签窗（而非只看左边）。
    #    一旦置信度不足，就会继承**前一个**语种 → 交界被系统性推后。
    conf = [i for i in range(n) if out[i] is not None]
    if conf:
        for i in range(n):
            if out[i] is None and voiced[i]:
                j = min(conf, key=lambda k: (abs(k - i), k))   # 同距取靠前者
                out[i] = out[j]

    # 2) 多数投票平滑（只在连续有声段内，不跨静音）
    if kernel >= 3 and n >= kernel:
        sm = list(out)
        h = kernel // 2
        for i in range(n):
            if out[i] is None:
                continue
            lo, hi = max(0, i - h), min(n, i + h + 1)
            win = [out[j] for j in range(lo, hi) if out[j] is not None and voiced[j]]
            if win:
                vals, cnt = np.unique(win, return_counts=True)
                sm[i] = str(vals[int(np.argmax(cnt))])
        out = sm
    return out


def segment_by_language(wav_path, det=None, win=10.0, hop=5.0, min_seg=8.0,
                        smooth=3, min_db=None, silence_db=-50.0, boundary_shift=0.0,
                        min_keep_prob=0.60, loudness_priority=True, topk=3):
    """把一段人声按语种切成若干分片。

    返回 (segments, meta)：
    segments = [{"index","t0","t1","lang","prob","n_windows","votes"}, ...]
    meta     = {"detector_available", "reason", "duration", "win", "hop", "labels", "voiced"}
    纪律：
    **静音段（< silence_db）单独成段且 lang=None**，绝不继承相邻语种
    语种判不准但**有声**的窗，由最近的有声邻居补齐；
    **相同时间点按音量优先**（`loudness_priority=True`，默认）：每个时间点被
    win/hop 个重叠窗覆盖，逐点按「窗口概率 × 该窗响度权重」表决，唱得响的窗话语权更大；
    全曲 LID 不可用时返回单个覆盖全曲的 `lang=None` 分片（等价于"不分段"）。
    """
    data, sr = read_wav(wav_path)
    if data.ndim == 2:
        data = data.mean(axis=1)
    dur = len(data) / float(sr)
    meta = {"detector_available": False, "reason": "", "duration": round(dur, 3),
            "win": win, "hop": hop, "labels": []}
    whole = [{"index": 0, "t0": 0.0, "t1": round(dur, 3), "lang": None, "prob": 0.0,
              "n_windows": 0, "votes": {}}]

    if det is None:
        det = LanguageDetector(min_db=min_db)
    if not det.available():
        meta["reason"] = det.reason or "LID 不可用"
        return whole, meta
    meta["detector_available"] = True

    try:
        wins = det.classify_windows(wav_path, win=win, hop=hop)
    except Exception as e:
        meta["reason"] = "推理失败：%s" % str(e)[:160]
        return whole, meta
    if not wins:
        meta["reason"] = "音频短于一个窗（%.1fs < %.1fs）" % (dur, win)
        return whole, meta

    # 覆盖到尾部：最后一个窗若没够到结尾，补一个贴尾窗
    if wins[-1]["t1"] < dur - 1e-6:
        tail = det.classify_array(data[int(max(0.0, dur - win) * sr):], sr)
        if tail is not None:
            wins.append(dict(tail, t0=max(0.0, dur - win), t1=dur))

    # ---- 单元化：默认走「逐时间点音量优先表决」；关掉则退化为按窗序列分组 ----
    voiced = [w["db"] > silence_db for w in wins]
    loud_weights = _loud_weights(wins)
    if loudness_priority:
        units = _time_vote_units(wins, voiced, loud_weights, topk=topk)
    else:
        units = [dict(w) for w in wins]
    meta["loudness_priority"] = bool(loudness_priority)
    meta["n_units"] = len(units)

    u_voiced = [u["db"] > silence_db for u in units]
    u_raw = [u["code"] if u["ok"] else None for u in units]
    meta["labels"] = u_raw
    meta["voiced"] = u_voiced
    meta["silence_db"] = silence_db
    labels = _fill_and_smooth(u_raw, voiced=u_voiced, kernel=smooth)

    # 连续同标签 → 一个候选分片
    groups = []
    for i, lab in enumerate(labels):
        t0 = units[i]["t0"]
        t1 = units[i]["t1"] if i == len(units) - 1 else units[i + 1]["t0"]
        if groups and groups[-1]["lang"] == lab:
            groups[-1]["t1"] = max(groups[-1]["t1"], t1)
            groups[-1]["idxs"].append(i)
        else:
            groups.append({"lang": lab, "t0": t0, "t1": max(t1, t0), "idxs": [i]})
    groups[0]["t0"] = 0.0
    groups[-1]["t1"] = dur

    # 太短的组：并入邻居。规则（顺序即优先级）：
    #   1. 静音组（lang=None）本身不含语言信息 → 并入权重更大的邻居（消掉零碎静音段）；
    #   2. 有声组 → 优先并入**同语种**的相邻有声组，否则并入权重更大的相邻有声组；
    #      两侧都没有有声组时**保留原样**（宁可留一个短段，也不硬贴一个语种）。
    def weight(g):
        return sum(units[i]["prob"] * max(0.1, units[i]["t1"] - units[i]["t0"]) for i in g["idxs"])

    def merge_into(groups, k, tgt_idx):
        g, tgt = groups[k], groups[tgt_idx]
        tgt["idxs"] = sorted(tgt["idxs"] + g["idxs"])
        tgt["t0"] = min(tgt["t0"], g["t0"])
        tgt["t1"] = max(tgt["t1"], g["t1"])
        groups.pop(k)

    changed = True
    while changed and len(groups) > 1:
        changed = False
        for k, g in enumerate(groups):
            if (g["t1"] - g["t0"]) >= min_seg:
                continue
            left = k - 1 if k > 0 else None
            right = k + 1 if k + 1 < len(groups) else None
            cands = [j for j in (left, right) if j is not None]
            if not cands:
                continue
            if g["lang"] is None:
                pick = max(cands, key=lambda j: weight(groups[j]))
            else:
                voiced_c = [j for j in cands if groups[j]["lang"] is not None]
                if not voiced_c:
                    continue
                same = [j for j in voiced_c if groups[j]["lang"] == g["lang"]]
                pick = same[0] if same else max(voiced_c, key=lambda j: weight(groups[j]))
            merge_into(groups, k, pick)
            changed = True
            break

    # 吸收后可能出现同语种相邻 → 再合并一次
    i = 0
    while i + 1 < len(groups):
        if groups[i]["lang"] == groups[i + 1]["lang"]:
            groups[i]["idxs"] = sorted(groups[i]["idxs"] + groups[i + 1]["idxs"])
            groups[i]["t1"] = max(groups[i]["t1"], groups[i + 1]["t1"])
            groups.pop(i + 1)
        else:
            i += 1

    # 低置信段：并回邻居（防「假换语种」）。
    # ——假换语种比漏检更糟（会把整段塞进错误预设），故设一道可配闸门。
    def group_prob(g):
        pr = [units[i]["prob"] for i in g["idxs"] if units[i]["ok"]]
        return float(np.mean(pr)) if pr else 0.0

    changed = True
    while changed and len(groups) > 1:
        changed = False
        for k, g in enumerate(groups):
            if g["lang"] is None or group_prob(g) >= min_keep_prob:
                continue
            cands = [j for j in (k - 1, k + 1) if 0 <= j < len(groups)]
            if not cands:
                continue
            merge_into(groups, k, max(cands, key=lambda j: weight(groups[j])))
            changed = True
            break
    # 再合并一次同语种相邻
    i = 0
    while i + 1 < len(groups):
        if groups[i]["lang"] == groups[i + 1]["lang"]:
            groups[i]["idxs"] = sorted(groups[i]["idxs"] + groups[i + 1]["idxs"])
            groups[i]["t1"] = max(groups[i]["t1"], groups[i + 1]["t1"])
            groups.pop(i + 1)
        else:
            i += 1

    segs = []
    for k, g in enumerate(groups):
        idxs = g["idxs"]
        votes = {}
        pr = []
        for i in idxs:
            if units[i]["ok"]:
                votes[units[i]["code"]] = votes.get(units[i]["code"], 0.0) + max(
                    0.1, units[i]["t1"] - units[i]["t0"])
                pr.append(units[i]["prob"])
        segs.append({
            "index": k,
            "t0": round(g["t0"], 3), "t1": round(g["t1"], 3),
            "lang": g["lang"],
            "prob": round(float(np.mean(pr)), 4) if pr else 0.0,
            "n_windows": len(idxs),
            "votes": {kk: round(vv, 3) for kk, vv in sorted(votes.items(), key=lambda kv: -kv[1])},
        })

    # 边界补偿（默认 0 = 不做任何修正）。
    # 0~0.6×win（win=10 时偏晚 2.5~6 s）。逐窗取证显示机制是——**拼接点之后第一个
    # 纯新语种窗仍被判成旧语种**（mix1 的 [30,40]s 是纯日语，却给出 zh p=0.962）。
    # 因项目没有真实"中·日混唱"素材（5 首固定曲全是单语种），**边界精度无法在真实
    # 素材上验收** → 故默认**不套用**基于伪影测得的修正（避免过拟合），只提供旋钮。
    # 重叠解析（防御性）：把可能**在时间上重叠**的分片整理成互不重叠的划分——
    # 同一时间点被多个分片覆盖时，取**该区间内更响**的那一段。
    # 说明：当前切分本身不产生重叠（相邻组首尾相接），故这一步现在是**零行为变化**；
    _n_before = len(segs)
    segs = resolve_overlaps_by_loudness(segs, wav_path=wav_path, data=data, sr=sr)
    meta["overlap_collapsed"] = (_n_before != len(segs))

    if boundary_shift:
        for s in segs[1:]:
            s["t0"] = round(max(0.0, s["t0"] + boundary_shift), 3)
        for s in segs[:-1]:
            s["t1"] = round(max(s["t0"], s["t1"] + boundary_shift), 3)
        for a, b in zip(segs, segs[1:]):
            b["t0"] = a["t1"]                    # 保持首尾相接
    meta["boundary_shift"] = boundary_shift
    meta["boundary_note"] = (
        "合成硬拼接实测交界偏晚 0~0.6×win；含接缝伪影，真实混唱未验收" if not boundary_shift
        else "已施加 boundary_shift=%.2fs" % boundary_shift)
    meta["reason"] = "切出 %d 段" % len(segs)
    return segs, meta


# --------------------------------------------------------------------------
# 分片落盘 + 契约
# --------------------------------------------------------------------------
def write_segments(wav_path, segments, outdir, prefix=None, keep_silence=False,
                   subtype="PCM_16"):
    """把分片写成 WAV，返回新的 manifest（segments 会补上 path）。"""
    data, sr = read_wav(wav_path)
    if prefix is None:
        prefix = os.path.splitext(os.path.basename(wav_path))[0]
    out = []
    for s in segments:
        s = dict(s)
        chunk, a0, a1 = slice_audio(data, sr, s["t0"], s["t1"])
        if chunk is None or len(chunk) == 0:
            continue
        if not keep_silence and _dbfs(chunk) < -70:
            s["path"] = None
            s["note"] = "近静音，跳过落盘"
            out.append(s)
            continue
        tag = s.get("lang") or "unknown"
        name = "%s__%s__%s-%s.wav" % (prefix, tag, fmt_tc(s["t0"]), fmt_tc(s["t1"]))
        path = os.path.join(outdir, name)
        write_wav(path, chunk, sr, subtype=subtype)
        s["path"] = path
        s["t0"], s["t1"] = round(a0, 3), round(a1, 3)
        out.append(s)
    return out


def build_manifest(wav_path, segments, mode, extra=None):
    m = {
        "source": os.path.abspath(wav_path),
        "mode": mode,
        "n_segments": len(segments),
        "segments": segments,
    }
    if extra:
        m.update(extra)
    return m


def manifest_notes(segments, notes_by_index, sort=True):
    """胶水：把各分片**段内局部时间**的音符拼回**全局时间轴**。"""
    out = []
    for s in segments:
        idx = s.get("index")
        base = float(s.get("t0", 0.0))
        for n in (notes_by_index.get(idx) or []):
            t0, t1, p, v = n[0], n[1], n[2], n[3]
            if t1 <= t0:
                continue
            out.append((round(base + t0, 6), round(base + t1, 6), p, v))
    if sort:
        out.sort(key=lambda x: (x[0], x[2]))
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="音频裁剪 / 人声分段 / 语言分段裁剪")
    ap.add_argument("--mode", choices=["crop", "energy", "lang"], default="crop")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--outdir", default=None, help="输出目录（lang/energy 模式必填）")
    ap.add_argument("--start", type=float, default=0.0, help="crop 模式：起点秒")
    ap.add_argument("--end", type=float, default=None, help="crop 模式：终点秒")
    ap.add_argument("--min-gap", type=float, default=1.0, help="energy 模式：静音间隔秒")
    ap.add_argument("--rel-db", type=float, default=-35.0, help="energy 模式：相对门 dB")
    ap.add_argument("--min-seg", type=float, default=8.0, help="lang 模式：最短段秒")
    ap.add_argument("--win", type=float, default=10.0, help="lang 模式：LID 窗长秒")
    ap.add_argument("--hop", type=float, default=5.0, help="lang 模式：LID 窗移秒")
    ap.add_argument("--candidates", default="zh,ja,en,yue", help="候选语种白名单")
    ap.add_argument("--boundary-shift", type=float, default=0.0,
                    help="交界补偿秒（负值=提前）。实测偏晚 0~0.6×win，含接缝伪影，默认 0")
    ap.add_argument("--min-keep-prob", type=float, default=0.60,
                    help="段平均置信度低于此值则并回邻居（防假换语种），默认 0.60")
    ap.add_argument("--no-loudness-priority", action="store_true",
                    help="关掉「相同时间点按音量优先」的逐点表决（退化为按窗序列分组）")
    ap.add_argument("--topk", type=int, default=3,
                    help="逐点表决时每窗取前 k 个候选语种（默认 3）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.audio):
        print("找不到音频：%s" % a.audio)
        return 2

    if a.mode == "crop":
        data, sr = read_wav(a.audio)
        dur = data.shape[0] / float(sr)
        t1 = dur if a.end is None else a.end
        chunk, a0, a1 = slice_audio(data, sr, a.start, t1)
        if chunk is None:
            print("裁剪区间为空")
            return 2
        print("源 %.2fs / %d Hz  →  裁剪 %.2f~%.2fs（%.2fs）" % (dur, sr, a0, a1, a1 - a0))
        if a.dry_run:
            return 0
        outdir = a.outdir or "."
        name = "%s__crop__%s-%s.wav" % (os.path.splitext(os.path.basename(a.audio))[0],
                                        fmt_tc(a0), fmt_tc(a1))
        p = write_wav(os.path.join(outdir, name), chunk, sr)
        print("已写出：%s" % p)
        return 0

    outdir = a.outdir
    if not outdir and not a.dry_run:
        print("--outdir 必填")
        return 2
    if outdir and not a.dry_run:
        os.makedirs(outdir, exist_ok=True)

    if a.mode == "energy":
        segs = segment_by_energy(a.audio, min_gap=a.min_gap, rel_db=a.rel_db,
                                 min_seg=min(a.min_seg, 0.1))
        print("切出 %d 个有声段：" % len(segs))
        for s in segs:
            print("  #%d  %8.2f - %8.2f  (%.2fs)" % (s["index"], s["t0"], s["t1"], s["t1"] - s["t0"]))
        if a.dry_run:
            return 0
        segs = write_segments(a.audio, segs, outdir)
        man = build_manifest(a.audio, segs, "energy", {"min_gap": a.min_gap, "rel_db": a.rel_db})
    else:
        det = LanguageDetector(candidates=a.candidates)
        print("LID：%s" % ("可用" if det.available() else "不可用 — %s" % det.reason))
        segs, meta = segment_by_language(a.audio, det=det, win=a.win, hop=a.hop,
                                         min_seg=a.min_seg,
                                         boundary_shift=a.boundary_shift,
                                         min_keep_prob=a.min_keep_prob,
                                         loudness_priority=not a.no_loudness_priority,
                                         topk=a.topk)
        print("时长 %.2fs  %s" % (meta["duration"], meta["reason"]))
        for s in segs:
            print("  #%d  %8.2f - %8.2f  %-7s p=%.2f  窗=%d  votes=%s"
                  % (s["index"], s["t0"], s["t1"], s["lang"] or "unknown", s["prob"],
                     s["n_windows"], json.dumps(s["votes"], ensure_ascii=False)))
        if a.dry_run:
            return 0
        segs = write_segments(a.audio, segs, outdir)
        man = build_manifest(a.audio, segs, "lang", {
            "win": a.win, "hop": a.hop, "min_seg": a.min_seg,
            "candidates": a.candidates, "detector_available": meta["detector_available"],
            "detector_reason": meta["reason"], "labels": meta["labels"]})

    mpath = os.path.join(outdir, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    print("manifest：%s" % mpath)
    for s in segs:
        if s.get("path"):
            print("  -> %s" % s["path"])
    return 0


if __name__ == "__main__":
    sys.exit(_main())
