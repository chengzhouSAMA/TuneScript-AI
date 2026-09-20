# -*- coding: utf-8 -*-
"""lang_pipeline.py — 「语种分割 → 逐段按语种预设扒谱 → 拼回全局时间轴」一步封装。

主人指定的管线位置
------------------
    … → Demucs 分轨 → **【这里】语种分割** → 扒谱（人声按语言逐段识别）→ 融合 → 渲染

本模块就是「【这里】」这一步：输入**已分离的人声轨**，输出**全局时间轴上的音符**，
中间自己做 LID、分段、裁剪、逐段识别、拼回。

四条纪律
--------
1. **绝不改变出厂默认行为**：只有 `TS_LANG_SEG=1` 时才走分割路径；否则调用方应直接
   沿用原来的整轨识别（本模块也提供同样的整轨兜底）。
2. **任何一步失败都退化为整轨识别**，绝不抛异常打断转谱。
3. **不在源素材目录写任何文件**：裁剪分片一律写到 `out_dir/_langseg/` 或系统临时目录。
   （项目里 `_merge_accomp_stems` 曾把中间产物写进源目录，见 备忘 附-14.7，是同一类坑。）
4. **静音段跳过识别**：`勾指起誓` 人声轨前 21.5 s 是 −84 dBFS 数字静音，
   对它跑 Basic Pitch 既慢又无意义；跳过并记账。

与「相同时间点按音量优先」的关系
--------------------------------
分段本身由 `audio_crop.segment_by_language(..., loudness_priority=True)` 完成——
它在**每个时间点**按「窗口概率 × 该窗响度」表决，唱得响的段落在语种判定上话语权更大；
分片若出现时间重叠，再由 `resolve_overlaps_by_loudness()` 取该区间更响的一段。
本模块只负责"按分段一路跑下去"，不重复实现该规则。
"""
import os
import shutil
import tempfile

from lang_id import LanguageDetector
from lang_modes import resolve, DEFAULT as DEFAULT_PRESET
from audio_crop import (read_wav, segment_by_language, write_segments, manifest_notes,
                        _dbfs)

ENV_ENABLE_SEG = "TS_LANG_SEG"          # "1" 启用分段扒谱（默认 0 = 出厂行为）
ENV_KEEP = "TS_LANG_SEG_KEEP"           # "1" 保留裁剪分片（默认 0，跑完删）
ENV_SKIP_DB = "TS_LANG_SEG_SKIP_DB"     # 低于此 dBFS 的段直接跳过识别（默认 -60）


def _env_on(name, default="0"):
    return os.environ.get(name, default) == "1"


def _float_env(name, default):
    try:
        v = os.environ.get(name)
        return float(v) if v not in (None, "") else float(default)
    except Exception:
        return float(default)


def enabled():
    """分段扒谱是否启用（默认关闭 → 出厂行为逐字节不变）。"""
    return _env_on(ENV_ENABLE_SEG, "0")


def transcribe_vocal_by_language(vocal_wav, transcribe_fn, progress=None,
                                 out_dir=None, win=None, hop=None, min_seg=None,
                                 candidates=None, backend=None, label="人声旋律",
                                 force=False):
    """按语种分割人声轨，逐段用各自的语种预设识别，拼回全局时间轴。

    参数
    ----
    vocal_wav    : 人声轨 wav 路径
    transcribe_fn: `fn(wav_path, label, min_len) -> [(start, end, pitch, velocity), ...]`
                   **返回段内局部时间**（调用方就是现有的 `notes_of`）
    progress     : 进度回调（可省略）
    out_dir      : 分片输出目录；None 时用系统临时目录（跑完自动清理）
    win/hop      : LID 窗长/窗移（默认 30/15）。
                   **为什么是 30/15**（lang_dev/_eval_lid_qwen.py 实测，5 首固定素材）：
                   `W15/H15` 与 `W30/H15` 都是 strict 4/5、lenient 5/5；`W60/H30` 掉到 3/5。
                   选 `W30/H15` 是因为 hop=win/2 → **每个时间点被 2 个窗覆盖**，
                   正是「相同时间点按音量优先」逐点表决所需要的重叠度；
                   代价是窗数约 2×（一首 3 分钟歌的 LID 从 ~50 s 涨到 ~140 s，纯 CPU）。
    min_seg      : 最短分段秒（默认 max(8, win*0.8)）
    candidates   : 候选语种白名单（默认取 TS_LANG_CANDIDATES，或 zh,ja,en,yue）
    backend      : LID 后端（默认 TS_LANG_BACKEND -> auto：Qwen 优先，退 Silero）

    返回
    ----
    (notes, info)
      notes : [(start, end, pitch, velocity)] **全局时间轴**
      info  : dict —— 决策、逐段明细、耗时、是否退化；可写进结果 JSON 供核查
    """
    def log(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    info = {"mode": "whole", "reason": "", "segments": [], "n_notes": 0,
            "backend": "", "preset": {}, "skipped": [], "chunks_dir": None}
    if not vocal_wav or not os.path.isfile(vocal_wav):
        info["reason"] = "人声轨不存在"
        return [], info

    preset_default = resolve("default")
    # ---- 整轨兜底（与出厂行为一致）----
    def whole():
        notes = transcribe_fn(vocal_wav, label, preset_default["bp_min_len_vocal"])
        info["mode"] = "whole"
        info["n_notes"] = len(notes)
        return notes, info

    # ★ 自带门控（不依赖调用方）：未启用时直接整轨识别，**连 LID 都不碰**。
    #   这样即使将来有人在别处调用本函数，也不会意外改变出厂行为。
    if not force and not enabled():
        info["reason"] = "%s 未启用（默认 off）" % ENV_ENABLE_SEG
        return whole()

    try:
        det = LanguageDetector(backend=backend, candidates=candidates)
        if not det.available():
            info["reason"] = "LID 不可用：%s" % det.reason
            log("语种分割跳过：%s" % det.reason)
            return whole()
        info["backend"] = det.backend_name

        win = _float_env("TS_LANG_SEG_WIN", 30.0) if win is None else win
        hop = _float_env("TS_LANG_SEG_HOP", win / 2.0) if hop is None else hop
        if min_seg is None:
            min_seg = max(8.0, win * 0.8)

        segs, meta = segment_by_language(vocal_wav, det=det, win=win, hop=hop,
                                         min_seg=min_seg,
                                         loudness_priority=True)
        info["lid_meta"] = {"win": win, "hop": hop, "min_seg": min_seg,
                            "detector": det.backend_name,
                            "n_windows": len(meta.get("labels") or []),
                            "reason": meta.get("reason", "")}

        usable = [s for s in segs if s.get("lang")]
        if not usable:
            info["reason"] = "未切出任何有语种的分段（整曲单一语种或全静音）"
            log("语种分割：未切出多语种分段，按整轨识别。")
            return whole()

        # ---- 分片落盘 ----
        tmp = None
        if out_dir:
            cdir = os.path.join(out_dir, "_langseg")
            os.makedirs(cdir, exist_ok=True)
        else:
            tmp = tempfile.mkdtemp(prefix="langseg_")
            cdir = tmp
        info["chunks_dir"] = cdir if _env_on(ENV_KEEP, "0") else None

        try:
            segs = write_segments(vocal_wav, segs, cdir)
            skip_db = _float_env(ENV_SKIP_DB, -60.0)
            notes_by_index = {}
            for s in segs:
                p = s.get("path")
                if not p:
                    info["skipped"].append({"index": s["index"], "reason": "无分片（近静音）"})
                    continue
                if _dbfs(read_wav(p)[0]) < skip_db:
                    info["skipped"].append({"index": s["index"], "reason": "低于 %.0f dBFS" % skip_db})
                    continue
                pset = resolve(s.get("lang") or "unknown")
                ml = int(pset["bp_min_len_vocal"])
                notes_by_index[s["index"]] = transcribe_fn(
                    p, "%s(%s)" % (label, s.get("lang")), ml)
                info["segments"].append({
                    "index": s["index"], "t0": s["t0"], "t1": s["t1"],
                    "lang": s.get("lang"), "prob": s.get("prob"),
                    "min_len": ml, "n_notes": len(notes_by_index[s["index"]]),
                    "preset_reason": pset.get("_reason", "")})

            if not notes_by_index:
                info["reason"] = "所有分段都被跳过（近静音）"
                return whole()

            notes = manifest_notes(segs, notes_by_index)
            info["mode"] = "language_segments"
            info["n_notes"] = len(notes)
            info["reason"] = "切出 %d 段，识别 %d 段" % (len(segs), len(notes_by_index))
            log("语种分段扒谱：%d 段 → 识别 %d 段 → %d 个音符（后端 %s）"
                % (len(segs), len(notes_by_index), len(notes), det.backend_name))
            if len(notes) < 10:
                info["reason"] += "；音符过少，退回整轨识别"
                log("分段结果音符过少（%d），退回整轨识别。" % len(notes))
                return whole()
            return notes, info
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        info["reason"] = "异常退化：%s: %s" % (type(e).__name__, str(e)[:160])
        log("语种分段不可用（%s），按整轨识别。" % type(e).__name__)
        try:
            return whole()
        except Exception:
            return [], info


def describe(preset):
    """把预设渲染成一行可读文本（日志/报告用）。"""
    keys = [k for k in DEFAULT_PRESET if k in preset]
    return " ".join("%s=%s" % (k, preset[k]) for k in keys)
