# -*- coding: utf-8 -*-
"""_probe_lyrics_api.py — 实测：哪些"无 cookie"歌词端点在本机可用。

目标（主人要求）：爬网易云 / QQ音乐的歌词，**不使用 cookie**。
先探通端点再写正式模块 —— 这两家的接口经常变，必须实测。

用法: python lang_dev/_probe_lyrics_api.py [--kw "バカみたいに 柿崎ユウタ"]
"""
import argparse
import json
import sys

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def show(tag, ok, detail):
    print("  [%s] %-34s %s" % ("OK " if ok else "XX ", tag, detail))


def probe_netease_search(kw):
    """网易云搜索：两个常见端点都试。"""
    out = []
    # ① 老的 web 搜索接口
    try:
        r = requests.get("https://music.163.com/api/search/get/web",
                         params={"s": kw, "type": 1, "limit": 5, "offset": 0},
                         headers={"User-Agent": UA, "Referer": "https://music.163.com/"},
                         timeout=15)
        j = r.json()
        songs = ((j.get("result") or {}).get("songs") or [])
        out.append(("netease/search/get/web", r.status_code, len(songs),
                    songs[0] if songs else None))
    except Exception as e:
        out.append(("netease/search/get/web", None, 0, str(e)[:100]))
    # ② cloudsearch（POST）
    try:
        r = requests.post("https://music.163.com/api/cloudsearch/pc",
                          data={"s": kw, "type": 1, "limit": 5, "offset": 0},
                          headers={"User-Agent": UA, "Referer": "https://music.163.com/"},
                          timeout=15)
        j = r.json()
        songs = ((j.get("result") or {}).get("songs") or [])
        out.append(("netease/cloudsearch/pc", r.status_code, len(songs),
                    songs[0] if songs else None))
    except Exception as e:
        out.append(("netease/cloudsearch/pc", None, 0, str(e)[:100]))
    return out


def probe_netease_lyric(song_id):
    try:
        r = requests.get("https://music.163.com/api/song/lyric",
                         params={"id": song_id, "lv": 1, "kv": 1, "tv": -1},
                         headers={"User-Agent": UA, "Referer": "https://music.163.com/"},
                         timeout=15)
        j = r.json()
        lrc = ((j.get("lrc") or {}).get("lyric") or "")
        return r.status_code, len(lrc), lrc[:200].replace("\n", " | ")
    except Exception as e:
        return None, 0, str(e)[:100]


def probe_qq_search(kw):
    out = []
    try:
        r = requests.get("https://c.y.qq.com/soso/fcgi-bin/client_search_cpus",
                         params={"w": kw, "format": "json", "n": 5, "p": 1,
                                 "cr": 1, "new_json": 1},
                         headers={"User-Agent": UA, "Referer": "https://y.qq.com/"},
                         timeout=15)
        j = r.json()
        lst = ((j.get("data") or {}).get("song") or {}).get("list") or []
        out.append(("qq/client_search_cpus", r.status_code, len(lst), lst[0] if lst else None))
    except Exception as e:
        out.append(("qq/client_search_cpus", None, 0, str(e)[:100]))
    try:
        r = requests.get("https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg",
                         params={"key": kw, "format": "json"},
                         headers={"User-Agent": UA, "Referer": "https://y.qq.com/"},
                         timeout=15)
        j = r.json()
        lst = ((j.get("data") or {}).get("song") or {}).get("itemlist") or []
        out.append(("qq/smartbox_new", r.status_code, len(lst), lst[0] if lst else None))
    except Exception as e:
        out.append(("qq/smartbox_new", None, 0, str(e)[:100]))
    return out


def probe_qq_lyric(songmid):
    try:
        r = requests.get("https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
                         params={"songmid": songmid, "format": "json", "nobase64": 1,
                                 "g_tk": 5381},
                         headers={"User-Agent": UA,
                                  "Referer": "https://y.qq.com/portal/player.html"},
                         timeout=15)
        j = r.json()
        lrc = j.get("lyric") or ""
        return r.status_code, len(lrc), lrc[:200].replace("\n", " | ")
    except Exception as e:
        return None, 0, str(e)[:100]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", default="バカみたいに 柿崎ユウタ")
    a = ap.parse_args()
    print("查询词：%s\n" % a.kw)

    print("=== 网易云 搜索 ===")
    ns = probe_netease_search(a.kw)
    for tag, code, n, first in ns:
        if isinstance(first, str):
            show(tag, False, "异常 %s" % first)
        else:
            show(tag, code == 200 and n > 0, "HTTP %s  结果 %d 首  首条=%s"
                 % (code, n, json.dumps({
                     "id": first.get("id"), "name": first.get("name"),
                     "artists": [x.get("name") for x in (first.get("artists") or [])],
                     "album": (first.get("album") or {}).get("name")},
                     ensure_ascii=False)[:110] if first else "(无)"))
    sid = None
    for _t, c, n, f in ns:
        if isinstance(f, dict) and f.get("id"):
            sid = f["id"]
            break
    if sid:
        print("\n=== 网易云 歌词 (id=%s) ===" % sid)
        code, ln, head = probe_netease_lyric(sid)
        show("netease/song/lyric", code == 200 and ln > 0,
             "HTTP %s  长度 %d  %s" % (code, ln, head))

    print("\n=== QQ音乐 搜索 ===")
    qs = probe_qq_search(a.kw)
    mid = None
    for tag, code, n, first in qs:
        if isinstance(first, str):
            show(tag, False, "异常 %s" % first)
        else:
            show(tag, code == 200 and n > 0, "HTTP %s  结果 %d 首  首条=%s"
                 % (code, n, json.dumps(first, ensure_ascii=False)[:110] if first else "(无)"))
            if first and mid is None:
                mid = first.get("songmid") or first.get("mid")
    if mid:
        print("\n=== QQ音乐 歌词 (songmid=%s) ===" % mid)
        code, ln, head = probe_qq_lyric(mid)
        show("qq/fcg_query_lyric_new", code == 200 and ln > 0,
             "HTTP %s  长度 %d  %s" % (code, ln, head))
    return 0


if __name__ == "__main__":
    sys.exit(main())
