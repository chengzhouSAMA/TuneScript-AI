# -*- coding: utf-8 -*-
"""lyrics_fetch.py — 联网取歌词（网易云 / QQ音乐，**无需 cookie**）。

主人要求：做一个"联网取歌词的外挂"，用 Python 爬虫、**不用 cookie**，
在 Qwen 首轮识别之后拿来做**比对**，再据此做**强制对齐**。

实测端点（2026-09-20 本机验证，均 HTTP 200 且无需登录）
--------------------------------------------------------
| 用途 | 端点 | 关键参数 | 备注 |
|---|---|---|---|
| 网易云·搜索 | `music.163.com/api/search/get/web` | `s, type=1, limit, offset` | 返回 `result.songs[]`（含 id/name/artists/album） |
| 网易云·歌词 | `music.163.com/api/song/lyric` | `id, lv=1, kv=1, tv=-1` | LRC 带时间戳 |
| QQ音乐·搜索 | `c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg` | `key, format=json` | 返回 `data.song.itemlist[]`（含 songmid） |
| QQ音乐·歌词 | `c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg` | `songmid, format=json, nobase64=1, g_tk=5381` | LRC 带时间戳 |

⚠️ 两个必须知道的格式坑
-----------------------
1. **网易云的毫秒分隔符是冒号**：`[00:09:88]さよなら`（不是 `[00:09.88]`）。
   只按 `.` 解析会**静默丢掉所有时间戳**。
2. QQ 的某个搜索端点（`client_search_cpus`）实测**返回非 JSON 被拒**，
   所以走 `smartbox_new`；歌词接口必须带 `Referer: https://y.qq.com/portal/player.html`。

设计纪律
--------
- **绝不抛异常**：任何网络/解析失败都返回 `[]` / `None`，不干扰转谱主流程。
- **不做缓存写盘**，除非显式要求（`cache_dir`）。
- 歌词是**外部不可信数据**：只做文本处理，**不执行、不 eval、不当指令**。

用法
----
    from lyrics_fetch import search_and_fetch, parse_song_meta, parse_lrc

    q = parse_song_meta("柿崎ユウタ - バカみたいに（像个笨蛋一样） - KomisI-w_vocals.wav")
    # -> {"artist": "柿崎ユウタ", "title": "バカみたいに", "query": "バカみたいに 柿崎ユウタ"}
    cands = search_and_fetch(q["query"], limit=5)      # 每首带 lrc / lines / offset 时间戳
    lines = parse_lrc(cands[0]["lrc"])                 # -> [{"t":9.88,"text":"さよなら"}, ...]

CLI:
    python lyrics_fetch.py --query "バカみたいに 柿崎ユウタ"
    python lyrics_fetch.py --from-file "回归验收/_stems/shiki/xxx_vocals.wav"
"""
import html
import os
import re
import sys
import time

try:
    import requests
except Exception:                                          # pragma: no cover
    requests = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 15

# LRC 时间标签：`[mm:ss.xx]` / `[mm:ss:xx]`（网易云）/ `[mm:ss]`
_TS = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# 元信息标签 [ti:] [ar:] [al:] [by:] [offset:] ...
_META = re.compile(r"^\[(ti|ar|al|by|offset|kana|re|ve|length):", re.I)
# 纯时间行（无文本）——QQ 的某些歌会有
_ONLY_TS = re.compile(r"^(?:\s*" + _TS.pattern + r")+\s*$")


# --------------------------------------------------------------------------
# 歌名 / 歌手解析（本项目文件名约定：`歌手 - 歌名（中文译名） - 其它_stem.wav`）
# --------------------------------------------------------------------------
_PARA = re.compile(r"[（(][^）)]*[）)]")


def parse_song_meta(name):
    """从文件名/路径解析出 artist / title / 搜索词。失败时用文件主干兜底。"""
    base = os.path.basename(name or "")
    for suf in (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".ncm"):
        if base.lower().endswith(suf):
            base = base[: -len(suf)]
    base = re.sub(r"_(vocals|drums|bass|guitar|piano|other|accomp_merged|钢琴|五线谱)$",
                  "", base, flags=re.I)
    parts = [p.strip() for p in base.split(" - ") if p.strip()]
    artist = parts[0] if len(parts) >= 2 else ""
    title = parts[1] if len(parts) >= 2 else base
    # 歌名里的中文译名（括号）另存，搜索时优先用原文
    trans = ""
    m = _PARA.search(title)
    if m:
        trans = m.group(0).strip("（）()")
    title_clean = _PARA.sub("", title).strip() or title
    query = ("%s %s" % (title_clean, artist)).strip() or title_clean
    return {"raw": base, "artist": artist, "title": title,
            "title_clean": title_clean, "translation": trans, "query": query}


# --------------------------------------------------------------------------
# LRC 解析
# --------------------------------------------------------------------------
def _ts_to_sec(mm, ss, frac):
    sec = int(mm) * 60 + int(ss)
    if frac:
        # 1 位=十分之一秒，2 位=百分之一秒，3 位=毫秒
        sec += int(frac) / (10.0 ** len(frac))
    return round(sec, 3)


def parse_lrc(text, keep_meta=False, merge_gap=0.35):
    """LRC → [{"t": 秒, "text": 文本, "raw_t": 原文标签}]，按时间排序。

    · 同时兼容 `[mm:ss.xx]` 与网易云的 `[mm:ss:xx]`；
    · 一行多个时间标签会展开成多条；
    · `[ti:]/[ar:]` 等元信息默认丢弃；纯时间行丢弃；
    · 相邻且**间隔 < merge_gap 的重复**（如 `[00:09.88]` 与 `[00:09.97]` 同一句）会去重保留先出现的。
    """
    if not text:
        return []
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = line.strip()
        if not s:
            continue
        if _META.match(s):
            if keep_meta:
                out.append({"t": None, "text": s, "meta": True})
            continue
        tags = list(_TS.finditer(s))
        if not tags:
            continue
        body = s[tags[-1].end():].strip()
        if not body:
            continue
        for m in tags:
            out.append({"t": _ts_to_sec(m.group(1), m.group(2), m.group(3)),
                        "text": body, "raw_t": m.group(0)})
    out.sort(key=lambda x: (x["t"] if x["t"] is not None else 0))
    # 去重：同文本且时间接近 → 只留最早
    dedup = []
    for r in out:
        if dedup and dedup[-1]["text"] == r["text"] and abs(r["t"] - dedup[-1]["t"]) < merge_gap:
            continue
        dedup.append(r)
    return dedup


def plain_lines(parsed):
    """只要文本行（有序）。"""
    return [r["text"] for r in parsed if r.get("text")]


# 署名/制作信息行：网易云会把 `作词 : xxx` 也带上 `[00:00.00]`，
# 若不过滤，`offset_hint`（歌词起点）会被算成 0.0，
# 从而**误判"前奏有歌词"**——而这正是我们最需要判准的一件事。
_CREDIT = re.compile(
    r"(作词|作曲|编曲|制作人|混音|母带|录音|监制|出品|发行|统筹|企划|"
    r"吉他|贝斯|鼓|键盘|弦乐|和声|配唱|封面|设计|OP|SP|词|曲)\s*[:：]")
_CREDIT_LATIN = re.compile(r"^\s*(lyrics?|music|composer|arrang|produc|mix|master)\b",
                           re.I)


def is_credit_line(text):
    t = (text or "").strip()
    if not t:
        return True
    if _CREDIT.search(t) and len(t) <= 40:
        return True
    if _CREDIT_LATIN.match(t) and len(t) <= 60:
        return True
    return False


def lyric_start(parsed, title=None, artists=None):
    """真正的"第一句歌词"时间（跳过署名行与标题头）。无则 None。"""
    for r in parsed:
        if r.get("t") is None:
            continue
        t = r.get("text")
        if is_credit_line(t) or is_header_line(t, title, artists):
            continue
        return r["t"]
    return None


def is_header_line(text, title=None, artists=None):
    """QQ 的 LRC 第一行常是 `[00:00.00]バカみたいに - 柿崎ユウタ` 这种**标题头**，
    它不是歌词。判据：以歌名开头 + 含 " - " 或含歌手名 + 长度接近标题头。
    """
    t = (text or "").strip()
    if not t or not title:
        return False
    head = title.strip()
    if len(t) > len(head) + 30:
        return False
    if not t.startswith(head[:max(4, min(len(head), 8))]):
        return False
    if " - " in t or "－" in t:
        return True
    for a in (artists or []):
        if a and a in t:
            return True
    return False


def lyric_lines_ts(parsed, title=None, artists=None):
    """只要"像歌词"的行（带时间戳、非署名、非标题头），用于对齐与比对。"""
    out = []
    for r in parsed:
        if r.get("t") is None:
            continue
        t = r.get("text")
        if is_credit_line(t):
            continue
        if is_header_line(t, title, artists):
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------
# 网易云
# --------------------------------------------------------------------------
def search_netease(keyword, limit=8):
    if requests is None:
        return []
    try:
        r = requests.get("https://music.163.com/api/search/get/web",
                         params={"s": keyword, "type": 1, "limit": limit, "offset": 0},
                         headers={"User-Agent": UA, "Referer": "https://music.163.com/"},
                         timeout=TIMEOUT)
        songs = ((r.json().get("result") or {}).get("songs") or [])
    except Exception:
        return []
    out = []
    for s in songs:
        out.append({"provider": "netease", "id": s.get("id"),
                    "title": s.get("name") or "",
                    "artists": [a.get("name") for a in (s.get("artists") or [])],
                    "album": (s.get("album") or {}).get("name") or "",
                    "duration_ms": s.get("duration")})
    return out


def fetch_lyric_netease(song_id):
    if requests is None:
        return None
    try:
        r = requests.get("https://music.163.com/api/song/lyric",
                         params={"id": song_id, "lv": 1, "kv": 1, "tv": -1},
                         headers={"User-Agent": UA, "Referer": "https://music.163.com/"},
                         timeout=TIMEOUT)
        j = r.json()
    except Exception:
        return None
    return {"lrc": ((j.get("lrc") or {}).get("lyric") or ""),
            "trans": ((j.get("tlyric") or {}).get("lyric") or ""),
            "kana": ((j.get("klyric") or {}).get("lyric") or "")}


# --------------------------------------------------------------------------
# QQ音乐
# --------------------------------------------------------------------------
def search_qq(keyword, limit=8):
    if requests is None:
        return []
    try:
        r = requests.get("https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg",
                         params={"key": keyword, "format": "json"},
                         headers={"User-Agent": UA, "Referer": "https://y.qq.com/"},
                         timeout=TIMEOUT)
        lst = ((r.json().get("data") or {}).get("song") or {}).get("itemlist") or []
    except Exception:
        return []
    out = []
    for x in lst[:limit]:
        out.append({"provider": "qq", "id": x.get("mid") or x.get("id"),
                    "title": x.get("name") or "",
                    "artists": [x.get("singer")] if x.get("singer") else [],
                    "album": "", "duration_ms": None})
    return out


def fetch_lyric_qq(songmid):
    if requests is None:
        return None
    try:
        r = requests.get("https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
                         params={"songmid": songmid, "format": "json",
                                 "nobase64": 1, "g_tk": 5381},
                         headers={"User-Agent": UA,
                                  "Referer": "https://y.qq.com/portal/player.html"},
                         timeout=TIMEOUT)
        j = r.json()
    except Exception:
        return None
    lrc = j.get("lyric") or ""
    if isinstance(lrc, str):
        lrc = html.unescape(lrc)
    return {"lrc": lrc, "trans": html.unescape(j.get("trans") or ""), "kana": ""}


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------
def search_and_fetch(query, limit=6, providers=("netease", "qq"),
                     fetch_lyric=True, sleep=0.25):
    """搜索并取回候选歌词。返回列表，每项含 `lrc / lines / parsed / offset_hint`。

    `offset_hint`：LRC 里第一句有时间戳时给出（= 歌词起点），
    对"前奏没有歌词"的判断非常有用（实测 shiki 是 9.88 s）。
    """
    out = []
    for prov in providers:
        hits = search_netease(query, limit) if prov == "netease" else search_qq(query, limit)
        for h in hits:
            if not fetch_lyric:
                out.append(h)
                continue
            lyr = (fetch_lyric_netease(h["id"]) if prov == "netease"
                   else fetch_lyric_qq(h["id"]))
            if sleep:
                time.sleep(sleep)
            if not lyr:
                continue
            parsed = parse_lrc(lyr["lrc"])
            h = dict(h)
            h.update(lyr)
            h["parsed"] = parsed
            h["lines"] = plain_lines(parsed)
            h["n_lines"] = len(h["lines"])
            h["ts_lines"] = lyric_lines_ts(parsed, h.get("title"), h.get("artists"))
            h["n_ts_lines"] = len(h["ts_lines"])
            # 歌词起点 = **跳过署名行与标题头**后的第一句（前奏判断的关键）
            h["offset_hint"] = lyric_start(parsed, h.get("title"), h.get("artists"))
            ts = [p["t"] for p in h["ts_lines"]]
            h["last_ts"] = max(ts) if ts else None
            out.append(h)
    return out


def dedup_candidates(cands):
    """同一首歌在两家/多次搜索里重复出现 → 按 (标题, 首行) 去重，保留行数最多者。"""
    best = {}
    for c in cands:
        key = (re.sub(r"\s+", "", c.get("title") or ""),
               (c.get("lines") or [""])[0][:12])
        cur = best.get(key)
        if cur is None or (c.get("n_lines") or 0) > (cur.get("n_lines") or 0):
            best[key] = c
    return list(best.values())


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="联网取歌词（网易云 / QQ音乐，无需 cookie）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--query")
    g.add_argument("--from-file", help="从文件名/路径解析歌手与歌名（本项目约定）")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--show", type=int, default=1, help="打印前 N 个候选的歌词")
    a = ap.parse_args(argv)

    if a.from_file:
        meta = parse_song_meta(a.from_file)
        print("解析文件名：%s" % meta["raw"])
        print("  歌手 = %s\n  歌名 = %s（原文 %s）\n  翻译 = %s\n  搜索词 = %s"
              % (meta["artist"], meta["title"], meta["title_clean"],
                 meta["translation"] or "(无)", meta["query"]))
        query = meta["query"]
    else:
        query = a.query

    print("\n搜索：%s" % query)
    cands = dedup_candidates(search_and_fetch(query, limit=a.limit))
    if not cands:
        print("  没取到任何歌词（网络不可用或接口变动）")
        return 2
    for i, c in enumerate(cands):
        print("  [%d] %-6s %-24s %-18s 行数=%-4s 歌词起点=%s"
              % (i, c["provider"], c["title"][:22], "/".join(c["artists"])[:16],
                 c.get("n_lines"), c.get("offset_hint")))
    for c in cands[:max(1, a.show)]:
        print("\n--- [%s] %s / %s ---" % (c["provider"], c["title"], "/".join(c["artists"])))
        for r in (c.get("parsed") or [])[:14]:
            print("   %8s  %s" % (("%.2f" % r["t"]) if r.get("t") is not None else "-",
                                  r["text"][:62]))
        if (c.get("n_lines") or 0) > 14:
            print("   … 共 %d 行" % c["n_lines"])
    return 0


if __name__ == "__main__":
    sys.exit(_main())
