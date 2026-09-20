# -*- coding: utf-8 -*-
"""asr_refine.py — 「识别不出来的段落 → 再切割重试 → 谐音音节(罗马音摩拉)分解」。"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ja_romaji as JR                                        # noqa: E402
import en_phoneme as EP                                       # noqa: E402

ENV_RETRY = "TS_ASR_RETRY"            # "1" 启用重试（默认 0）
ENV_RETRY_MIN = "TS_ASR_RETRY_MIN"    # 子窗最小秒数（默认 2.0）
ENV_RETRY_SCORE = "TS_ASR_RETRY_SCORE"  # 低于此质量分触发重试（默认 0.75）


def score(text, expect_lang=None):
    """ASR 输出的质量分与问题标签（= ja_romaji.quality 的转发，集中入口便于替换）。"""
    return JR.quality(text, expect_lang=expect_lang)


def _split_span(t0, t1, min_len):
    """把一个区间对半切；小于 min_len*2 就不再切，返回 None。"""
    if (t1 - t0) < min_len * 2 - 1e-6:
        return None
    mid = (t0 + t1) / 2.0
    return [(t0, mid), (mid, t1)]


def retry_windows(spans, call_asr, expect_lang="ja", min_len=2.0, threshold=0.75,
                  max_depth=2, log=None, keep_best=True, force_first_pass=False):
    """对给定区间逐个判质量，低分的**再切割重试**（强制语种），取质量最高者。

    参数
    -
    spans      : [(t0, t1), ...] 待处理的区间
    call_asr   : `fn(spans, language=None) -> [{"t0","t1","language","text"}, ...]`
    **一次调用处理一批区间**（Qwen sidecar 一次进程内跑完整批，
    避免每窗重复加载模型）
    expect_lang: 期望语种（用于质量判据），如 "ja"
    min_len    : 子窗最小秒数（默认 2.0；再小 Qwen 会抓不住）
    threshold  : 质量低于它就重试
    max_depth  : 最多切几刀（深度）：1 = 对半，2 = 再对半

    返回 (rows, stats)
    rows  = [{"t0","t1","language","text","score","issues","depth","accepted"}, ...]
    **已按时间排序、互不重叠**；depth=0 表示原始窗
    stats = {"n_in","n_retried","n_improved","n_still_bad","asr_calls","spans_called"}
    """
    def _log(m):
        if log:
            try:
                log(m)
            except Exception:
                pass

    stats = {"n_in": len(spans), "n_retried": 0, "n_improved": 0,
             "n_still_bad": 0, "asr_calls": 0, "spans_called": 0}

    def run(batch, forced=True):
        if not batch:
            return []
        stats["asr_calls"] += 1
        stats["spans_called"] += len(batch)
        lang = _LANG_NAME.get(expect_lang, None) if forced else None
        out = call_asr(batch, language=lang)
        return out or []

    first = run(list(spans), forced=bool(force_first_pass))
    frontier = []
    for r in first:
        q = score(r.get("text", ""), expect_lang)
        frontier.append(dict(r, score=q["score"], issues=q["issues"], depth=0))

    for depth in range(1, int(max_depth) + 1):
        bad = [r for r in frontier if r["score"] < threshold]
        if not bad:
            break
        subs, owner = [], {}
        for r in bad:
            pair = _split_span(r["t0"], r["t1"], min_len)
            if not pair:
                continue
            stats["n_retried"] += 1
            owner[r["t0"]] = r
            for (a, b) in pair:
                owner[(a, b)] = r
            subs.extend(pair)
        if not subs:
            break
        got = run(subs)
        # 逐个子窗判分，并更新其父窗的"最好成绩"
        by_span = {(round(g["t0"], 3), round(g["t1"], 3)): g for g in got}
        for (a, b) in subs:
            g = by_span.get((round(a, 3), round(b, 3)))
            if g is None:
                continue
            q = score(g.get("text", ""), expect_lang)
            child = dict(g, score=q["score"], issues=q["issues"], depth=depth)
            parent = owner.get((a, b))
            if keep_best and parent is not None and child["score"] > parent["score"]:
                # 子窗更好 → 用子窗替换父窗（父窗从 frontier 移除，子窗入队）
                if parent in frontier:
                    frontier.remove(parent)
                frontier.append(child)
                stats["n_improved"] += 1

    rows = sorted([r for r in frontier if r.get("t0") is not None],
                  key=lambda r: r["t0"])
    for r in rows:
        r["accepted"] = r["score"] >= threshold
    stats["n_still_bad"] = sum(1 for r in rows if not r["accepted"])
    return rows, stats


_LANG_NAME = {"ja": "Japanese", "zh": "Chinese", "yue": "Cantonese", "en": "English"}


def detect_lang(text):
    """按字符集粗判语种：含假名 → ja，含拉丁字母 → en，否则 unknown。

    用途：ASR 输出里日英不会混，但调用方未必知道当前段是什么语言，
    这里给 `phonetic_timeline` 一个默认分派依据。
    """
    t = text or ""
    kana = sum(1 for c in t if JR.is_kana(c))
    latin = sum(1 for c in t if c.isascii() and c.isalpha())
    if kana and kana >= latin:
        return "ja"
    if latin:
        return "en"
    return "unknown"


def phonetic_timeline(text, lang=None, t0=None, t1=None, char_times=None):
    """按语种给出**音节轴**（"谐音音节"的统一入口）。

    日语 → 罗马音摩拉（`ja_romaji`）；英语 → IPA 音节（`en_phoneme`）。
    英语不用摩拉是因为它是重音计时语言，一个音节可跨多个音素，
    对应的单位是**元音核**。

    返回 {"lang","n_units","units":[{...,"t0","t1"}],"label","timing"}
      · `label` 是整串的展示形式（罗马音 / IPA）
      · `units` 每项含 `unit`(摩拉罗马音或音节 ARPAbet)、`ipa`、`t0`、`t1`
    """
    lang = (lang or detect_lang(text) or "unknown").lower()
    out = {"text": text, "lang": lang, "n_units": 0, "units": [],
           "label": "", "timing": "char_align" if char_times else "uniform",
           "oov": []}
    if lang == "ja":
        r = romaji_timeline(text, t0=t0, t1=t1, char_times=char_times)
        out["n_units"] = r["n_morae"]
        out["label"] = r["romaji"]
        out["kana"] = r["kana"]
        out["units"] = [{"unit": m["romaji"], "ipa": "", "mora": m["mora"],
                         "t0": m["t0"], "t1": m["t1"]} for m in r["morae"]]
        return out
    if lang == "en":
        r = EP.analyze(text)
        syls = r["syllables"]
        if char_times:
            units = EP.from_char_times(text, char_times, t0=0.0)
        elif t0 is not None and t1 is not None:
            units = EP.distribute(syls, float(t0), float(t1))
        else:
            units = EP.distribute(syls, 0.0, float(max(1, len(syls)) * 0.35))
        out["n_units"] = len(units)
        out["label"] = r["ipa"]
        out["oov"] = r["oov"]
        out["units"] = [{"unit": u["syllable"], "ipa": u["ipa"], "word": u.get("word", ""),
                         "stress": u.get("stress", 0), "t0": u["t0"], "t1": u["t1"]}
                        for u in units]
        return out
    return out


def syllable_grid(rows, lang=None):
    """把多行识别结果拼成**全局音节轴**（日语摩拉 / 英语 IPA 音节统一结构）。

    返回 [{"t0","t1","unit","ipa","lang","index","src_t0","src_t1","text"}, ...]
    每个窗的时长在其音节间均分（有强制对齐时可换成真实时间戳）。
    """
    out, k = [], 0
    for r in sorted(rows, key=lambda x: x["t0"]):
        t = (r.get("text") or "").strip()
        if not t:
            continue
        tl = phonetic_timeline(t, lang=lang, t0=r["t0"], t1=r["t1"])
        for u in tl["units"]:
            u = dict(u)
            u["index"] = k
            u["lang"] = tl["lang"]
            u["src_t0"] = r["t0"]
            u["src_t1"] = r["t1"]
            u["text"] = t
            out.append(u)
            k += 1
    return out


def notes_vs_units(notes, units, tol=0.12):
    """量化「唱出来的音节有没有变成音符」。

    对英日语通用（单位是摩拉还是音节由 `syllable_grid` 决定）。

    返回 {"n_units","n_hit","hit_rate","missing":[...]}
    判据：某音节起点附近 tol 秒内存在一个音符起音 → 记命中。
    """
    starts = sorted(float(n[0]) for n in (notes or []))
    hit, miss = 0, []
    for u in units:
        t = float(u["t0"])
        if any(abs(s - t) <= tol for s in starts):
            hit += 1
        else:
            miss.append(u)
    n = len(units)
    return {"n_units": n, "n_hit": hit, "missing": miss,
            "hit_rate": round(hit / float(n), 4) if n else 0.0}


def merge_phonetic(rows, lang=None, join=" "):
    """把多行重试结果拼成一条展示串（日语罗马音 / 英语 IPA）。"""
    parts = []
    for r in sorted(rows, key=lambda x: x["t0"]):
        t = (r.get("text") or "").strip()
        if not t:
            continue
        tl = phonetic_timeline(t, lang=lang)
        if tl["label"]:
            parts.append(tl["label"])
    return join.join(parts)


def romaji_timeline(text, t0=None, t1=None, char_times=None, sep_lines=False):
    """日语文本 → 罗马音摩拉时间轴（"谐音音节"的直接产物）。

    返回 {"n_morae","romaji","morae":[{"mora","romaji","t0","t1"}, ...]}
    """
    r = JR.analyze(text)
    morae = r["morae"]
    out = {"text": text, "kana": r["kana"], "n_morae": r["n_morae"],
           "romaji": r["romaji"], "kana_coverage": r["kana_coverage"],
           "morae": [], "timing": "char_align" if char_times else "uniform"}
    if char_times:
        out["morae"] = JR.from_char_times(text, char_times, t0=0.0)
    elif t0 is not None and t1 is not None:
        out["morae"] = JR.distribute(morae, float(t0), float(t1))
    else:
        out["morae"] = JR.distribute(morae, 0.0, float(max(1, len(morae)) * 0.25))
    return out


def merge_romaji(rows, join=""):
    """把多行重试结果拼成一条罗马音串（按时间顺序，'/' 分隔摩拉）。"""
    parts = []
    for r in sorted(rows, key=lambda x: x["t0"]):
        if not (r.get("text") or "").strip():
            continue
        parts.append(JR.to_romaji(r["text"]))
    return join.join(p for p in parts if p)


def mora_grid(rows):
    """把多行识别结果拼成**全局摩拉轴**。

    返回 [{"t0","t1","mora","romaji","index","src_t0","src_t1","text"}, ...]
    每个窗的时长在其摩拉间均分（有强制对齐时可换成真实时间戳）。
    """
    out, k = [], 0
    for r in sorted(rows, key=lambda x: x["t0"]):
        t = (r.get("text") or "").strip()
        if not t:
            continue
        tl = romaji_timeline(t, t0=r["t0"], t1=r["t1"])
        for m in tl["morae"]:
            m = dict(m)
            m["index"] = k
            m["src_t0"] = r["t0"]
            m["src_t1"] = r["t1"]
            m["text"] = t
            out.append(m)
            k += 1
    return out


def notes_vs_morae(notes, morae, tol=0.12):
    """量化「日语的**字**有没有变成**音符**」。

    返回 {"n_morae","n_hit","hit_rate","missing":[...]}
    判据：某摩拉的起音时刻附近 tol 秒内存在一个音符起音 → 记命中。
    """
    starts = sorted(float(n[0]) for n in (notes or []))
    hit, miss = 0, []
    for m in morae:
        t = float(m["t0"])
        ok = any(abs(s - t) <= tol for s in starts)
        if ok:
            hit += 1
        else:
            miss.append(m)
    n = len(morae)
    return {"n_morae": n, "n_hit": hit, "missing": miss,
            "hit_rate": round(hit / float(n), 4) if n else 0.0}
