# -*- coding: utf-8 -*-
"""lyrics_match.py — Qwen 首轮识别结果 ↔ 联网歌词 的**比对**。

主人要求：「在第一次识别 qwen 识别歌词后进行比对，然后强制对齐」。
本模块就是中间那一步，做三件事：

1. **曲目确认**（`pick_best_candidate`）—— 搜索结果里可能混进别的歌
   （实测搜 `バカみたいに 柿崎ユウタ` 会混进同歌手的 `月が綺麗ねと言われたい！`），
   用 ASR 文本与各候选歌词的整体相似度挑出真正的那一首；
2. **逐窗比对**（`match_windows`）—— 对每个 ASR 窗，找出时间上对应、文本最像的歌词行，
   给出相似度与判定；
3. **幻觉/无歌词识别** —— 这是本项目最需要的一条：
   实测 `shiki` 官方 LRC 第一句在 **9.88 s**，所以 **0~9.88 s 根本没有歌词**，
   而 Qwen 在那里输出了 `じゃじゃじゃま、じゅうじゅうどま。`——**纯幻觉**。
   比对能把它直接判掉（`no_lyric`），而不是拿去"谐音音节化"。

相似度怎么算（日语关键设计）
----------------------------
Qwen 输出**假名为主**（`さようなら。少し開けた角の…`），而歌词是**汉字/假名混排**
（`少しだけ違っただけの愛情表現`）。直接比字符会误判，所以
**两边都先用 pykakasi 读成假名**再比（`ja_romaji.text_to_kana`）；pykakasi 缺席时退化为原文比较。

不抛异常、不写盘、不执行外部数据。
"""
import difflib
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ja_romaji as JR                                    # noqa: E402

# 拉丁/数字/符号之外的装饰字符先去掉，再统一假名
_STRIP = re.compile(r"[\s\u3000、。，．,.!?！？…‥「」『』（）()【】\[\]〈〉《》～~ー\-—―:：;；'\"“”‘’*/\\|]+")
_KEEP = re.compile(r"[ぁ-ゖァ-ヺ一-龥々ーA-Za-z0-9]")


def normalize(text, to_kana=True, drop_long_mark=False):
    """归一化：去空白/标点 → （可选）汉字读音→假名 → 只留假名/汉字/字母数字。

    `drop_long_mark=True` 时去掉长音符 `ー`：ASR 与歌词对长音的处理常不一致
    （`メランコリー` vs `メランコリ`），去掉后相似度更稳。
    """
    s = text or ""
    s = _STRIP.sub("", s)
    if to_kana:
        s = JR.text_to_kana(s)
        s = JR.kata_to_hira(s)
    s = "".join(c for c in s if _KEEP.match(c))
    if drop_long_mark:
        s = s.replace("ー", "")
    return s


def dice_bigram(a, b, to_kana=True, drop_long_mark=True):
    """字符 bigram 的 Dice 系数（0~1）。

    为什么还要这个：`SequenceMatcher.ratio()` 对**插入/漏识**惩罚很重
    （实测 ASR 长串里只要有一半跑偏，ratio 就掉到 0.3 以下）；
    而"识别对了一部分"这件事，用 n-gram 重合度衡量更稳定、更能分出高低。
    """
    x = normalize(a, to_kana, drop_long_mark)
    y = normalize(b, to_kana, drop_long_mark)
    if len(x) < 2 or len(y) < 2:
        return 0.0
    gx = set(x[i:i + 2] for i in range(len(x) - 1))
    gy = set(y[i:i + 2] for i in range(len(y) - 1))
    if not gx or not gy:
        return 0.0
    return round(2.0 * len(gx & gy) / float(len(gx) + len(gy)), 4)


def similarity(a, b, to_kana=True, drop_long_mark=True):
    """0~1 相似度 = max(序列相似度, bigram Dice)，两者取优（各有盲区）。"""
    x = normalize(a, to_kana, drop_long_mark)
    y = normalize(b, to_kana, drop_long_mark)
    if not x or not y:
        return 0.0
    r = float(difflib.SequenceMatcher(None, x, y).ratio())
    d = dice_bigram(a, b, to_kana, drop_long_mark)
    return round(max(r, d), 4)


def candidate_score(asr_text, cand):
    """ASR 文本 ↔ 某候选歌词整体 的相似度（双向：整体 + 最优窗口）。"""
    lines = [r["text"] for r in (cand.get("ts_lines") or [])]
    if not lines:
        lines = cand.get("lines") or []
    whole = "".join(lines)
    s_whole = similarity(asr_text, whole)
    # 再用"滑窗取最优"补一次：ASR 可能只覆盖歌词的一部分
    best = 0.0
    n = len(lines)
    for i in range(n):
        acc = ""
        for j in range(i, min(n, i + 6)):
            acc += lines[j]
            best = max(best, similarity(asr_text, acc))
    return {"whole": round(s_whole, 4), "best_window": round(best, 4),
            "score": round(max(s_whole, best), 4)}


def pick_best_candidate(asr_text, cands, min_score=0.25):
    """在候选里挑出与 ASR 最像的那一首 → **确认曲目**。

    返回 (best_or_None, ranked) ；ranked = [(cand, score_dict), ...] 降序。
    """
    ranked = []
    for c in cands or []:
        sc = candidate_score(asr_text, c)
        ranked.append((c, sc))
    ranked.sort(key=lambda kv: -kv[1]["score"])
    if not ranked or ranked[0][1]["score"] < min_score:
        return None, ranked
    return ranked[0][0], ranked


def lines_in_span(cand, t0, t1, pad=0.6):
    """落在 [t0-pad, t1+pad] 内的歌词行（带时间戳）。"""
    out = []
    for r in (cand.get("ts_lines") or []):
        if r.get("t") is None:
            continue
        if (t0 - pad) <= r["t"] <= (t1 + pad):
            out.append(r)
    return out


def match_windows(asr_rows, cand, hi=0.50, mid=0.28, pad=0.6):
    """逐窗比对。返回 (rows, summary)。

    每行新增：`best_score` / `best_text` / `best_t` / `in_span` / `verdict`
      verdict ∈ {confirmed, weak, hallucination, no_lyric}
        · no_lyric      —— 该时间窗**歌词里根本没有行**（前奏/间奏）→ 后续不应"谐音音节化"
        · confirmed     —— 与歌词相似度 >= hi
        · weak          —— mid ~ hi
        · hallucination —— 窗内有歌词行，但相似度 < mid（识别跑偏）
    """
    rows = []
    for r in asr_rows or []:
        t0, t1 = float(r.get("t0", 0.0)), float(r.get("t1", 0.0))
        span = lines_in_span(cand, t0, t1, pad=pad)
        txt = r.get("text") or ""
        sc, bt, btt = 0.0, "", None
        for line in span:
            s = similarity(txt, line["text"])
            if s > sc:
                sc, bt, btt = s, line["text"], line["t"]
        # 窗内没歌词 → 再看整体最优（防 LRC 时间轴偏移）
        if not span:
            for line in (cand.get("ts_lines") or []):
                s = similarity(txt, line["text"])
                if s > sc:
                    sc, bt, btt = s, line["text"], line.get("t")
        if not span:
            verdict = "no_lyric"
        elif sc >= hi:
            verdict = "confirmed"
        elif sc >= mid:
            verdict = "weak"
        else:
            verdict = "hallucination"
        rows.append(dict(r, best_score=round(sc, 4), best_text=bt, best_t=btt,
                         in_span=len(span), verdict=verdict))
    summary = {"n": len(rows)}
    for v in ("confirmed", "weak", "hallucination", "no_lyric"):
        summary[v] = sum(1 for r in rows if r["verdict"] == v)
    conf = [r["best_score"] for r in rows if r["verdict"] != "no_lyric"]
    summary["mean_score"] = round(sum(conf) / len(conf), 4) if conf else 0.0
    return rows, summary


def aligned_lyric_text(cand, t0=None, t1=None):
    """取用于**强制对齐**的歌词文本（可按时间段裁剪），返回单个字符串。

    强制对齐需要"连续的文本"，所以按时间顺序拼接（保留换行便于核对）。
    """
    lines = [r["text"] for r in (cand.get("ts_lines") or [])]
    if t0 is not None or t1 is not None:
        lines = [r["text"] for r in (cand.get("ts_lines") or [])
                 if (t0 is None or r["t"] >= t0) and (t1 is None or r["t"] <= t1)]
    return "\n".join(lines)


def _main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Qwen 首轮识别 ↔ 联网歌词 比对")
    ap.add_argument("--demo-json", default=os.path.join(ROOT, "lang_dev",
                                                        "_demo_ja_syllable.json"))
    ap.add_argument("--from-file", default=None, help="用它解析歌手/歌名（不传则用 demo 的曲目）")
    ap.add_argument("--limit", type=int, default=6)
    a = ap.parse_args(argv)

    import lyrics_fetch as LF
    if not os.path.isfile(a.demo_json):
        print("缺 %s（先跑 lang_dev/_demo_ja_syllable.py）" % a.demo_json)
        return 2
    d = json.load(open(a.demo_json, encoding="utf-8"))
    rows = d.get("rows") or []
    asr_all = "".join((r.get("text") or "") for r in rows)
    print("ASR 首轮（%d 窗），拼接文本：\n  %s\n" % (len(rows), asr_all[:150]))

    q = ("バカみたいに 柿崎ユウタ" if not a.from_file
         else LF.parse_song_meta(a.from_file)["query"])
    cands = LF.dedup_candidates(LF.search_and_fetch(q, limit=a.limit))
    print("候选 %d 个，逐个打分：" % len(cands))
    best, ranked = LF_rank = pick_best_candidate(asr_all, cands)
    for c, sc in ranked:
        mark = " <== 选中" if (best is not None and c is best) else ""
        print("   %-8s %-26s 行数=%-4s 起点=%-7s score=%.3f (whole=%.3f window=%.3f)%s"
              % (c["provider"], c["title"][:24], c.get("n_ts_lines"),
                 c.get("offset_hint"), sc["score"], sc["whole"], sc["best_window"], mark))
    if best is None:
        print("没有候选达到阈值 → 无法确认曲目")
        return 2

    print("\n=== 逐窗比对（选中：%s / %s）===" % (best["title"], "/".join(best["artists"])))
    mrows, summ = match_windows(rows, best)
    for r in mrows:
        print("  %5.2f-%5.2f %-13s score=%.2f 窗内歌词%d行  最佳='%s'%s"
              % (r["t0"], r["t1"], r["verdict"], r["best_score"], r["in_span"],
                 (r["best_text"] or "")[:26],
                 (" @%.2fs" % r["best_t"]) if r.get("best_t") is not None else ""))
        print("        ASR: %s" % (r.get("text") or "(空)")[:64])
    print("\n汇总：%s" % summ)
    print("歌词起点（跳过署名行）= %s s" % best.get("offset_hint"))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
