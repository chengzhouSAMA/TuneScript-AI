# -*- coding: utf-8 -*-
"""ja_romaji.py — 日语「谐音音节」分解：文本 → 假名 → **罗马音摩拉(mora)** 序列。

用法
-
from ja_romaji import analyze, morae_of, to_romaji
r = analyze("さようなら")
# -> {"kana": "さようなら", "morae": [{"mora":"さ","romaji":"sa"}, ...],
#     "romaji": "sa/yo/u/na/ra", "n_morae": 5, "kana_coverage": 1.0}

CLI: python ja_romaji.py --text "バカみたいにほら愛してたくせに"
python ja_romaji.py --file lyrics.txt
"""
import os
import re
import sys
import unicodedata

try:
    import pykakasi as _pk
    _KAKASI = _pk.kakasi()
except Exception:                                          # pragma: no cover
    _KAKASI = None

HAS_PYKAKASI = _KAKASI is not None


# --------------------------------------------------------------------------
# 假名表
# --------------------------------------------------------------------------
def _hira_range(cp):
    return 0x3041 <= cp <= 0x309F


def kata_to_hira(s):
    """片假名 → 平假名（含 ヷヸヹヺ、长音符保留为 ー）。"""
    out = []
    for ch in s:
        cp = ord(ch)
        if 0x30A1 <= cp <= 0x30F6:
            out.append(chr(cp - 0x60))
        elif 0x30F7 <= cp <= 0x30FA:          # ヷヸヹヺ
            out.append(chr(cp - 0x60))
        else:
            out.append(ch)
    return "".join(out)


# 单假名 -> 罗马音
BASE = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n",
    "ゔ": "vu", "ゕ": "ka", "ゖ": "ke",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa",
}

# 拗音/外来音：两假名 -> 一摩拉
DIGRAPH = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "ぢゃ": "ja", "ぢゅ": "ju", "ぢょ": "jo",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo", "ふゅ": "fyu",
    "てぃ": "ti", "てゅ": "tyu", "でぃ": "di", "でゅ": "dyu",
    "とぅ": "tu", "どぅ": "du",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
    "しぇ": "she", "じぇ": "je", "ちぇ": "che",
    "つぁ": "tsa", "つぃ": "tsi", "つぇ": "tse", "つぉ": "tso",
    "すぃ": "si", "ずぃ": "zi",
    "きぇ": "kye", "ぎぇ": "gye",
}

SMALL = "ゃゅょぁぃぅぇぉゎ"
VOWELS = "aiueo"
_KANA_RE = re.compile(r"[ぁ-ゖゝゞー]")
_KANJI_RE = re.compile(r"[\u4e00-\u9fff々]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def is_kana(ch):
    return bool(_KANA_RE.match(ch))


def kana_ratio(s):
    """假名占非空白字符的比例（判断"这段文本是不是真日语"的关键指标）。"""
    core = [c for c in s if not c.isspace() and not unicodedata.category(c).startswith("P")]
    if not core:
        return 0.0
    return sum(1 for c in core if is_kana(c)) / float(len(core))


# --------------------------------------------------------------------------
# 汉字 -> 假名
# --------------------------------------------------------------------------
def text_to_kana(text):
    """汉字读出来 → 平假名。没有 pykakasi 就原样返回（并在调用方体现覆盖率）。"""
    s = kata_to_hira(text or "")
    if _KAKASI is None:
        return s
    try:
        out = []
        for item in _KAKASI.convert(text or ""):
            h = item.get("hira") or item.get("orig") or ""
            out.append(kata_to_hira(h))
        return "".join(out)
    except Exception:
        return s


# --------------------------------------------------------------------------
# 摩拉切分 / 罗马音
# --------------------------------------------------------------------------
def morae_of(kana):
    """把（平）假名字符串切成摩拉列表。

    返回 [{"mora": "きょ", "romaji": "kyo"}, ...]，未识别的字符跳过并记 `skipped`。
    """
    s = kata_to_hira(kana or "")
    out = []
    i = 0
    n = len(s)
    while i < n:
        two = s[i:i + 2]
        if len(two) == 2 and two in DIGRAPH:
            out.append({"mora": two, "romaji": DIGRAPH[two]})
            i += 2
            continue
        ch = s[i]
        if ch == "っ":                      # 促音：自成一摩拉，罗马音稍后补
            out.append({"mora": "っ", "romaji": ""})
            i += 1
            continue
        if ch == "ー":                      # 长音：自成一摩拉，罗马音稍后补
            out.append({"mora": "ー", "romaji": ""})
            i += 1
            continue
        if ch in BASE:
            out.append({"mora": ch, "romaji": BASE[ch]})
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        # 汉字 / 拉丁 / 其他：跳过（保留在 kana 文本里，覆盖率会体现）
        i += 1
    _fix_geminate_and_long(out)
    return out


def _fix_geminate_and_long(morae):
    """补上促音(っ)与长音(ー)的罗马音：促音=重复后一摩拉首辅音，长音=重复前一摩拉元音。"""
    for i, m in enumerate(morae):
        if m["mora"] == "っ":
            nxt = None
            for j in range(i + 1, len(morae)):
                if morae[j]["mora"] != "っ" and morae[j]["romaji"]:
                    nxt = morae[j]["romaji"]
                    break
            m["romaji"] = (nxt[0] if nxt and nxt[0] not in VOWELS else "")
        elif m["mora"] == "ー":
            prv = None
            for j in range(i - 1, -1, -1):
                if morae[j]["romaji"]:
                    prv = morae[j]["romaji"]
                    break
            v = ""
            for c in reversed(prv or ""):
                if c in VOWELS:
                    v = c
                    break
            m["romaji"] = v
    # 促音没有可用辅音时并入前一个摩拉，避免产生空音节
    return [m for m in morae if m["romaji"] or m["mora"] not in ("っ", "ー")]


def to_romaji(text):
    """文本 → 罗马音串（'/' 分隔摩拉，便于肉眼核对音节数）。"""
    return "/".join(m["romaji"] for m in morae_of(text_to_kana(text)) if m["romaji"])


def analyze(text, sep="/"):
    """一次给出：假名文本、摩拉列表、罗马音串、摩拉数、假名覆盖率。"""
    kana = text_to_kana(text)
    morae = [m for m in morae_of(kana) if m["romaji"]]
    for k, m in enumerate(morae):
        m["index"] = k
    return {
        "orig": text,
        "kana": kana,
        "morae": morae,
        "n_morae": len(morae),
        "romaji": sep.join(m["romaji"] for m in morae),
        "kana_coverage": round(kana_ratio(text), 4),
        "has_pykakasi": HAS_PYKAKASI,
    }


def distribute(morae, t0, t1):
    """把一段时长**均分**给各摩拉（没有强制对齐时的兜底）。

    返回 [{"mora","romaji","t0","t1","index"}, ...]。
    """
    n = len(morae)
    if n == 0 or t1 <= t0:
        return []
    step = (t1 - t0) / float(n)
    out = []
    for k, m in enumerate(morae):
        out.append({"index": k, "mora": m["mora"], "romaji": m["romaji"],
                    "t0": round(t0 + k * step, 4), "t1": round(t0 + (k + 1) * step, 4)})
    return out


def from_char_times(text, char_times, t0=0.0):
    """用**字符级时间戳**（强制对齐结果）给摩拉定时。"""
    kana = text_to_kana(text)
    morae = morae_of(kana)
    out = []
    k = 0
    for (ch, s, e) in char_times:
        # 该字符对应的假名（可能 1 对 1，汉字转出来可能多个假名）
        sub = kata_to_hira(ch)
        grp = morae_of(sub)
        if not grp:
            continue
        step = (e - s) / float(len(grp))
        for j, m in enumerate(grp):
            out.append({"index": k, "mora": m["mora"], "romaji": m["romaji"],
                        "t0": round(t0 + s + j * step, 4),
                        "t1": round(t0 + s + (j + 1) * step, 4), "src_char": ch})
            k += 1
    return out


# --------------------------------------------------------------------------
# 质量判据（"识别不出来"长什么样）
# --------------------------------------------------------------------------
def detect_charset(text):
    """粗判文本的字符集成分，用于发现"日语歌被判成中文"这类错误。"""
    core = [c for c in (text or "") if not c.isspace()]
    n = len(core)
    if n == 0:
        return {"n": 0, "kana": 0.0, "kanji": 0.0, "latin": 0.0, "empty": True}
    kana = sum(1 for c in core if is_kana(c))
    kanji = sum(1 for c in core if _KANJI_RE.match(c))
    latin = sum(1 for c in core if _LATIN_RE.match(c))
    return {"n": n, "kana": round(kana / n, 4), "kanji": round(kanji / n, 4),
            "latin": round(latin / n, 4), "empty": False}


def repeat_ratio(text, ngram=2):
    """重复度：重复 n-gram 的占比（乱码常表现为高度重复，如 `じゃんじゃんじゃん…`）。"""
    s = "".join(c for c in (text or "") if not c.isspace())
    if len(s) < ngram * 2:
        return 0.0
    grams = [s[i:i + ngram] for i in range(len(s) - ngram + 1)]
    uniq = len(set(grams))
    return round(1.0 - uniq / float(len(grams)), 4)


def quality(text, expect_lang=None):
    """给一段 ASR 输出打质量分（越高越好）与问题标签。"""
    issues = []
    cs = detect_charset(text)
    rr = repeat_ratio(text, 2)
    if cs["empty"]:
        issues.append("empty")
        return {"score": 0.0, "issues": issues, "charset": cs, "repeat": rr}
    score = 1.0
    if expect_lang == "ja":
        # 日语：假名占比是核心信号；汉字也算对（但读不出音节）；拉丁少量可接受
        good = cs["kana"] + cs["kanji"] * 0.6
        if cs["kana"] < 0.15:
            issues.append("kana_too_low")          # 典型：被判成中文
        score *= min(1.0, good / 0.6)
    if rr > 0.35:
        issues.append("repetitive")
        score *= max(0.15, 1.0 - rr)
    if cs["n"] <= 2:
        issues.append("too_short")
        score *= 0.4
    return {"score": round(score, 4), "issues": issues, "charset": cs, "repeat": rr}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="日语 → 假名 → 罗马音摩拉分解")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--file")
    a = ap.parse_args(argv)
    text = a.text if a.text is not None else open(a.file, encoding="utf-8").read()
    if "\n" in text.strip():
        lines = [l for l in text.splitlines() if l.strip()]
    else:
        lines = [text]
    print("pykakasi：%s" % ("已装" if HAS_PYKAKASI else "未装（汉字读不出来，仅处理假名）"))
    for line in lines:
        r = analyze(line)
        print("\n原文 : %s" % r["orig"])
        print("假名 : %s" % r["kana"])
        print("罗马音: %s" % r["romaji"])
        print("摩拉 : %d 个  假名覆盖率 %.2f  %s"
              % (r["n_morae"], r["kana_coverage"],
                 " ".join("%s(%s)" % (m["mora"], m["romaji"]) for m in r["morae"][:40])
                 + (" …" if r["n_morae"] > 40 else "")))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
