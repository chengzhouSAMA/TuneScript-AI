# -*- coding: utf-8 -*-
"""en_phoneme.py — 英语"音标音节"分解：文本 → 音素(ARPAbet) → 音节 → **IPA 音标**。

为什么单位是"音节"而不是"音素"
------------------------------
日语是摩拉计时，一个假名一个音（见 `ja_romaji.py`）。
英语是重音计时：一个音节可能跨好几个音素（`through` = θ r uː 三音素一音节）。
所以英语里"一音节 ≈ 一个音符"的单位是**元音核（vowel nucleus）**，
音节边界按**最大音节首（maximal onset）**切分。

数据来源
--------
`cmudict`（PyPI，纯 Python，自带 CMU 发音词典 12.6 万词条，离线可用）。
未收录的词按后缀剥离 + 拼读规则兜底，并在 `oov` 里列出来。

用法
----
    from en_phoneme import analyze, syllables_of, to_ipa

    r = analyze("Hello, it's me")
    # -> {"ipa": "həˈloʊ ɪts miː",
    #     "syllables": [{"onset":["HH"],"nucleus":"AH0","coda":["L"],"ipa":"hə","stress":0}, ...],
    #     "n_syllables": 4, "oov": []}

CLI:
    python en_phoneme.py --text "Hello, it's me"
"""
import os
import re
import sys

try:
    import cmudict as _cmudict
    _CMU = None

    def _dict():
        global _CMU
        if _CMU is None:
            _CMU = _cmudict.dict()
        return _CMU
except Exception:                                          # pragma: no cover
    def _dict():
        return {}


# --------------------------------------------------------------------------
# 音素表
# --------------------------------------------------------------------------
# ARPAbet 元音（音节核）
VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY",
          "IH", "IY", "OW", "OY", "UH", "UW"}

# ARPAbet -> IPA（重音另加 ˌ / ˈ）
IPA = {
    "AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "aʊ", "AY": "aɪ",
    "EH": "ɛ", "ER": "ɝ", "EY": "eɪ", "IH": "ɪ", "IY": "i", "OW": "oʊ",
    "OY": "ɔɪ", "UH": "ʊ", "UW": "u",
    "B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "F": "f", "G": "ɡ",
    "HH": "h", "JH": "dʒ", "K": "k", "L": "l", "M": "m", "N": "n",
    "NG": "ŋ", "P": "p", "R": "ɹ", "S": "s", "SH": "ʃ", "T": "t",
    "TH": "θ", "V": "v", "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ",
}
# 非重读时替换成弱元音
WEAK = {"AH": "ə", "ER": "ɚ", "IH": "ɪ", "IY": "i", "UW": "u", "AX": "ə"}

_STRESS = {"1": "ˈ", "2": "ˌ", "0": ""}

_LEGAL_ONSETS = None


def _base(ph):
    """去掉重音数字：AH0 -> AH"""
    return ph.rstrip("012")


def _stress(ph):
    m = re.search(r"([012])$", ph)
    return m.group(1) if m else "0"


def ph_to_ipa(ph):
    """单个 ARPAbet 音素 -> IPA 片段（含重音符号）。"""
    b, s = _base(ph), _stress(ph)
    if b in VOWELS:
        v = WEAK.get(b, None) if s == "0" else None
        return _STRESS[s] + (v if v else IPA.get(b, b.lower()))
    return IPA.get(b, b.lower())


def _legal_onsets(min_count=20):
    """从词典里统计"合法的音节首辅音簇"（词首第一个元音之前的辅音组合）。

    必须按**出现频次**过滤：词典里混着方言/缩写词条，
    会出现 `N D`（1 次）、`M B`（2 次）这种并非合法音节首的簇，
    不滤掉就会把 `somebody` 切成 `sʌ·mbɑ·di`、`wondering` 切成 `wʌ·ndɚ·ɪŋ`。
    真正的音节首簇都是高频的（`S T` 1900 次、`T R` 1199 次、`F L` 651 次）。
    """
    global _LEGAL_ONSETS
    if _LEGAL_ONSETS is not None:
        return _LEGAL_ONSETS
    from collections import Counter
    cnt = Counter()
    for _w, prons in _dict().items():
        for pr in prons:
            cl = []
            for ph in pr:
                if _base(ph) in VOWELS:
                    break
                cl.append(_base(ph))
            cnt[tuple(cl)] += 1
    onsets = {c for c, n in cnt.items() if n >= min_count and len(c) <= 3}
    onsets.add(())
    _LEGAL_ONSETS = onsets
    return onsets


# --------------------------------------------------------------------------
# 文本 -> 音素
# --------------------------------------------------------------------------
_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
_SUFFIX = ("'s", "'re", "'ve", "'ll", "'d", "n't", "'m", "es", "ed", "s", "ing", "ly" )


def word_to_phonemes(word):
    """一个词 -> ARPAbet 音素列表；未收录返回 None。"""
    w = word.lower().replace("’", "'")
    d = _dict()
    if w in d:
        return list(d[w][0])
    for suf in _SUFFIX:
        if w.endswith(suf) and len(w) > len(suf) + 1:
            stem = w[: -len(suf)]
            if stem in d:
                return list(d[stem][0]) + (["Z"] if suf in ("'s", "s") else [])
    return None


def _lts(word):
    """未收录词的拼读兜底：按常见字母组合给一串音素（只保证音节数大致正确）。"""
    w = re.sub(r"[^a-z']", "", word.lower())
    rules = [("tion", ["SH", "AH0", "N"]), ("sion", ["ZH", "AH0", "N"]),
             ("ough", ["AH0"]), ("augh", ["AO1"]), ("eigh", ["EY1"]),
             ("igh", ["AY1"]), ("tch", ["CH"]), ("dge", ["JH"]),
             ("ch", ["CH"]), ("sh", ["SH"]), ("th", ["TH"]), ("ph", ["F"]),
             ("wh", ["W"]), ("ck", ["K"]), ("ng", ["NG"]), ("qu", ["K", "W"]),
             ("ee", ["IY1"]), ("ea", ["IY1"]), ("oo", ["UW1"]), ("ou", ["AW1"]),
             ("ow", ["OW1"]), ("ai", ["EY1"]), ("ay", ["EY1"]), ("oi", ["OY1"]),
             ("oy", ["OY1"]), ("er", ["ER0"]), ("ar", ["AA1", "R"]),
             ("or", ["AO1", "R"]), ("ir", ["ER1"]), ("ur", ["ER1"]),
             ("a", ["AE1"]), ("e", ["EH1"]), ("i", ["IH1"]), ("o", ["AA1"]),
             ("u", ["AH1"]), ("y", ["IY1"]),
             ("b", ["B"]), ("c", ["K"]), ("d", ["D"]), ("f", ["F"]),
             ("g", ["G"]), ("h", ["HH"]), ("j", ["JH"]), ("k", ["K"]),
             ("l", ["L"]), ("m", ["M"]), ("n", ["N"]), ("p", ["P"]),
             ("r", ["R"]), ("s", ["S"]), ("t", ["T"]), ("v", ["V"]),
             ("w", ["W"]), ("x", ["K", "S"]), ("z", ["Z"])]
    out, i = [], 0
    while i < len(w):
        for pat, ph in rules:
            if w.startswith(pat, i):
                out.extend(ph)
                i += len(pat)
                break
        else:
            i += 1
    # 结尾不发音的 e
    if w.endswith("e") and len(out) > 1 and len(w) > 2:
        for k in range(len(out) - 1, -1, -1):
            if _base(out[k]) in VOWELS:
                if k == len(out) - 1:
                    out.pop(k)
                break
    return out


def text_to_phonemes(text):
    """整句 -> [(word, [音素...]), ...]，同时返回未收录词表。"""
    out, oov = [], []
    for m in _WORD.finditer(text or ""):
        w = m.group(0)
        ph = word_to_phonemes(w)
        if ph is None:
            ph = _lts(w)
            if w.lower() not in [x.lower() for x in oov]:
                oov.append(w)
        out.append((w, ph))
    return out, oov


# --------------------------------------------------------------------------
# 音节切分（最大音节首）
# --------------------------------------------------------------------------
def syllabify(phonemes):
    """音素序列 -> 音节列表。

    切法：以每个元音核为锚；两个核之间的辅音串按**最大音节首**分配 ——
    从右往左取尽可能长的合法音节首给下一音节，其余归上一音节的尾。
    返回 [{"onset":[...], "nucleus":ph, "coda":[...], "stress":0|1|2}, ...]
    """
    idx = [i for i, p in enumerate(phonemes) if _base(p) in VOWELS]
    if not idx:
        return []
    legal = _legal_onsets()
    out = []
    for k, vi in enumerate(idx):
        prev = idx[k - 1] if k > 0 else -1
        nxt = idx[k + 1] if k + 1 < len(idx) else len(phonemes)
        if k == 0:
            onset = list(phonemes[:vi])
        else:
            between = list(phonemes[prev + 1:vi])
            onset = []
            for take in range(min(3, len(between)), 0, -1):
                cand = tuple(_base(x) for x in between[-take:])
                if cand in legal:
                    onset = between[-take:]
                    break
            if not onset and between:
                onset = [between[-1]]
            if onset:
                out[-1]["coda"] = between[: len(between) - len(onset)]
        coda = []
        if k + 1 < len(idx):
            # 尾音素由下一轮分配（这里先留空，避免与下一音节重叠）
            coda = []
        else:
            coda = list(phonemes[vi + 1:nxt])
        out.append({"onset": list(onset), "nucleus": phonemes[vi], "coda": coda,
                    "stress": int(_stress(phonemes[vi]))})
    return out


def _syl_ipa(s):
    return ("".join(ph_to_ipa(p) for p in s["onset"]) + ph_to_ipa(s["nucleus"])
            + "".join(ph_to_ipa(p) for p in s["coda"]))


def _syl_arpabet(s):
    return " ".join(s["onset"] + [s["nucleus"]] + s["coda"])


# --------------------------------------------------------------------------
# 对外接口
# --------------------------------------------------------------------------
def syllables_of(text):
    """文本 -> 音节列表（跨词拼接）。"""
    pairs, _oov = text_to_phonemes(text)
    out = []
    for w, ph in pairs:
        for s in syllabify(ph):
            s = dict(s)
            s["word"] = w
            s["ipa"] = _syl_ipa(s)
            s["arpabet"] = _syl_arpabet(s)
            out.append(s)
    for k, s in enumerate(out):
        s["index"] = k
    return out


def to_ipa(text):
    """文本 -> IPA 音标串（音节用 · 分隔，词用空格分隔）。"""
    pairs, _oov = text_to_phonemes(text)
    parts = []
    for _w, ph in pairs:
        syls = syllabify(ph)
        if not syls:
            continue
        parts.append("·".join(_syl_ipa(s) for s in syls))
    return " ".join(parts)


def analyze(text):
    """一次给出：音节列表、音标串、音节数、未收录词。"""
    syls = syllables_of(text)
    _pairs, oov = text_to_phonemes(text)
    return {"text": text, "syllables": syls, "n_syllables": len(syls),
            "ipa": to_ipa(text), "oov": oov,
            "has_cmudict": bool(_dict())}


def distribute(syllables, t0, t1):
    """把一段时长均分给各音节（无强制对齐时的兜底）。"""
    n = len(syllables)
    if n == 0 or t1 <= t0:
        return []
    step = (t1 - t0) / float(n)
    out = []
    for k, s in enumerate(syllables):
        out.append({"index": k, "syllable": s.get("arpabet", ""), "ipa": s.get("ipa", ""),
                    "word": s.get("word", ""), "stress": s.get("stress", 0),
                    "t0": round(t0 + k * step, 4), "t1": round(t0 + (k + 1) * step, 4)})
    return out


_NORM = re.compile(r"[^a-z0-9']")


def _norm(s):
    return _NORM.sub("", (s or "").lower())


def from_char_times(text, char_times, t0=0.0):
    """用对齐器给的时间戳给音节定时。

    `char_times` 是 [(片段文本, start, end), ...]，**片段可能是字也可能是词**
    （Qwen3-ForcedAligner 给的是词级），所以不能按单字符建映射。
    这里把各片段归一化后拼接成一条串，并记下每个字符的时间；
    再按顺序在串里定位每个词，取该词的时间跨度，词内音节均分。
    """
    seq = []                       # [(char, start, end)]
    for (chunk, s, e) in (char_times or []):
        n = _norm(chunk)
        if not n:
            continue
        step = (e - s) / float(len(n))
        for i, c in enumerate(n):
            seq.append((c, s + i * step, s + (i + 1) * step))
    if not seq:
        return []
    flat = "".join(c for c, _s, _e in seq)
    pairs, _oov = text_to_phonemes(text)
    out, cursor, k = [], 0, 0
    for w, ph in pairs:
        nw = _norm(w)
        if not nw:
            continue
        pos = flat.find(nw, cursor)
        if pos < 0:
            pos = flat.find(nw)          # 允许回退找一次（对齐器可能丢了标点）
        if pos < 0 or pos >= len(seq):
            continue
        end = min(pos + len(nw), len(seq))
        ws, we = seq[pos][1], seq[end - 1][2]
        cursor = end
        syls = syllabify(ph)
        if not syls:
            continue
        step = (we - ws) / float(len(syls))
        for j, s in enumerate(syls):
            out.append({"index": k, "syllable": _syl_arpabet(s), "ipa": _syl_ipa(s),
                        "word": w, "stress": s["stress"],
                        "t0": round(t0 + ws + j * step, 4),
                        "t1": round(t0 + ws + (j + 1) * step, 4)})
            k += 1
    return out


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="英语文本 -> 音素 -> 音节 -> IPA 音标")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--file")
    a = ap.parse_args(argv)
    text = a.text if a.text is not None else open(a.file, encoding="utf-8").read()
    lines = [l for l in text.splitlines() if l.strip()] or [text]
    print("cmudict：%s" % ("已加载" if _dict() else "不可用（只剩拼读兜底）"))
    for line in lines:
        r = analyze(line)
        print("\n原文 : %s" % r["text"])
        print("音标 : %s" % r["ipa"])
        print("音节 : %d 个  %s"
              % (r["n_syllables"],
                 " ".join("%s[%s]%s" % (s["ipa"], s["arpabet"],
                                        "·重" if s["stress"] == 1 else "")
                          for s in r["syllables"][:24])))
        if r["oov"]:
            print("未收录: %s（已用拼读兜底）" % ", ".join(r["oov"][:10]))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
