# -*- coding: utf-8 -*-
"""_probe_netease_dl.py — 实测：网易云**免登录**到底能拿到什么音质。

背景：Netease_url 用的核心接口是 eapi（加密）：
    https://interface3.music.163.com/eapi/song/enhance/player/url/v1
音质档位：standard(128k) / exhigh(320k) / lossless(FLAC) / hires / jyeffect / sky / jymaster
但**高音质一般要登录 cookie（无损还要黑胶会员）**。

本脚本用真实歌曲分别试几条路子，看无 cookie 时实际能拿到什么，
再决定我们的模块要怎么做降级。

用法: python lang_dev/_probe_netease_dl.py
"""
import hashlib
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_EAPI = "NMI/2.5.0 (iPhone; iOS 16.0; Scale/3.00)"

# 试几首不同热度的歌（ID 来自前面的搜索实测）
SONGS = [
    (2103987239, "バカみたいに / 柿崎ユウタ"),
    (347230, "海阔天空 / Beyond"),
    (186016, "晴天 / 周杰伦"),
    (569213220, "光年之外 / G.E.M."),
]

EAPI_KEY = b"e82ckenh8dichen8"


def eapi_params(url_path, payload):
    """网易云 eapi 参数加密：AES-128-ECB + md5 摘要（公开算法）。"""
    from Crypto.Cipher import AES
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    message = "nobody%suse%smd5forencrypt" % (url_path, text)
    digest = hashlib.md5(message.encode()).hexdigest()
    data = "%s-36cd479b6b5-%s-36cd479b6b5-%s" % (url_path, text, digest)
    # PKCS7 填充
    pad = 16 - len(data.encode()) % 16
    raw = data.encode() + bytes([pad]) * pad
    cipher = AES.new(EAPI_KEY, AES.MODE_ECB)
    return cipher.encrypt(raw).hex().upper()


def try_plain_api(song_id, br):
    """① 老的明文接口 api/song/enhance/player/url。"""
    try:
        r = requests.get("https://music.163.com/api/song/enhance/player/url",
                         params={"id": song_id, "ids": "[%d]" % song_id, "br": br},
                         headers={"User-Agent": UA_PC, "Referer": "https://music.163.com/"},
                         timeout=15)
        j = r.json()
        d = (j.get("data") or [{}])[0]
        return {"code": d.get("code"), "br": d.get("br"), "size": d.get("size"),
                "type": d.get("type"), "level": d.get("level"),
                "url": (d.get("url") or "")[:60] or None}
    except Exception as e:
        return {"err": str(e)[:80]}


def try_outer_url(song_id):
    """② 外链直取：/song/media/outer/url?id=X.mp3（会 302 到真实地址）。"""
    try:
        r = requests.get("https://music.163.com/song/media/outer/url?id=%d.mp3" % song_id,
                         headers={"User-Agent": UA_PC, "Referer": "https://music.163.com/"},
                         timeout=15, allow_redirects=False)
        loc = r.headers.get("Location") or ""
        return {"status": r.status_code,
                "location": (loc[:80] + "…") if len(loc) > 80 else loc,
                "is_music126": "music.126.net" in loc}
    except Exception as e:
        return {"err": str(e)[:80]}


def try_eapi(song_id, level, cookie=None):
    """③ eapi 加密接口（Netease_url 用的就是这条）。"""
    try:
        path = "/api/song/enhance/player/url/v1"
        payload = {"ids": "[%d]" % song_id, "level": level, "encodeType": "flac",
                   "header": json.dumps({"os": "ios", "appver": "9.0.10"}, separators=(",", ":"))}
        params = eapi_params(path, payload)
        cookies = {"os": "ios", "appver": "9.0.10"}
        if cookie:
            cookies["MUSIC_U"] = cookie
        r = requests.post("https://interface3.music.163.com/eapi/song/enhance/player/url/v1",
                          data={"params": params},
                          headers={"User-Agent": UA_EAPI, "Referer": "https://music.163.com/",
                                   "Content-Type": "application/x-www-form-urlencoded"},
                          cookies=cookies, timeout=20)
        j = r.json()
        d = (j.get("data") or [{}])[0]
        return {"http": r.status_code, "code": d.get("code"), "br": d.get("br"),
                "size": d.get("size"), "type": d.get("type"), "level": d.get("level"),
                "freeTrialInfo": d.get("freeTrialInfo"),
                "url": ((d.get("url") or "")[:56] + "…") if d.get("url") else None}
    except Exception as e:
        return {"err": str(e)[:90]}


def main():
    print("网易云免登录下载能力实测\n" + "=" * 74)
    for sid, name in SONGS:
        print("\n### %s (id=%s)" % (name, sid))
        print("  ① 明文 api   br=320000 : %s" % json.dumps(try_plain_api(sid, 320000), ensure_ascii=False))
        print("  ① 明文 api   br=999000 : %s" % json.dumps(try_plain_api(sid, 999000), ensure_ascii=False))
        print("  ② outer 外链           : %s" % json.dumps(try_outer_url(sid), ensure_ascii=False))
        for lv in ("standard", "exhigh", "lossless"):
            print("  ③ eapi %-9s(无cookie): %s"
                  % (lv, json.dumps(try_eapi(sid, lv), ensure_ascii=False)))
    print("\n" + "=" * 74)
    print("说明：br 单位是 bps（128000=128k，320000=320k，999000≈FLAC）。")
    print("      若 ③ 全部返回 br=128000 或 code=404/403，说明高音质确实需要登录 cookie。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
