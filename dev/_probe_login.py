# -*- coding: utf-8 -*-
"""_probe_login.py — 实测网易云扫码登录端点与 cookie 校验端点。

要探三件事：
  1. 取二维码 key：`eapi/login/qrcode/unikey`
  2. 轮询登录状态：`eapi/login/qrcode/client/login`（801 待扫 / 802 待确认 / 803 成功）
  3. cookie 是否有效：`api/nuser/account/get`

用法: python lang_dev/_probe_login.py [--cookie "MUSIC_U=..."]
"""
import argparse
import hashlib
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import netease as N                                        # noqa: E402

UA_EAPI = N.UA_EAPI
REFERER = N.REFERER


def eapi_post(url_path, payload, cookie=None, host="https://interface3.music.163.com"):
    params = N.eapi_params(url_path, payload)
    cookies = {"os": "ios", "appver": "9.0.10"}
    if cookie:
        cookies.update(N._cookie_dict(cookie))
    r = requests.post(host + url_path, data={"params": params},
                      headers={"User-Agent": UA_EAPI, "Referer": REFERER,
                               "Content-Type": "application/x-www-form-urlencoded"},
                      cookies=cookies, timeout=20)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookie", default=None)
    a = ap.parse_args()

    print("=== 1) 取二维码 key ===")
    try:
        r = eapi_post("/api/login/qrcode/unikey", {"type": 1})
        j = r.json()
        print("  HTTP %s  code=%s  unikey=%s" % (r.status_code, j.get("code"),
                                                 (j.get("unikey") or "")[:16] + "…"))
        unikey = j.get("unikey")
    except Exception as e:
        print("  失败：%s" % str(e)[:160])
        return 2

    if unikey:
        print("\n=== 2) 轮询一次（此时应返回 801 待扫码）===")
        try:
            r = eapi_post("/api/login/qrcode/client/login", {"key": unikey, "type": 1})
            print("  HTTP %s  body=%s" % (r.status_code, r.text[:120]))
            print("  二维码内容应为：https://music.163.com/login?codekey=%s" % unikey[:16] + "…")
        except Exception as e:
            print("  失败：%s" % str(e)[:160])

    print("\n=== 3) cookie 校验端点 ===")
    ck = a.cookie or N.load_cookie()
    print("  使用 cookie：%s" % ("有" if ck else "无"))
    urls = ["https://music.163.com/api/nuser/account/get",
            "https://music.163.com/api/v1/user/detail/0",
            "https://music.163.com/api/w/nuser/account/get"]
    hdr = {"User-Agent": N.UA_PC, "Referer": REFERER}
    for u in urls:
        try:
            r = requests.get(u, headers=hdr, cookies=N._cookie_dict(ck) if ck else None,
                             timeout=15)
            j = r.json()
            prof = j.get("profile") or {}
            acct = j.get("account") or {}
            print("  %-52s HTTP %s  code=%s  nickname=%s  vipType=%s  userId=%s"
                  % (u.replace("https://music.163.com", ""), r.status_code, j.get("code"),
                     prof.get("nickname"), prof.get("vipType"), acct.get("id")))
        except Exception as e:
            print("  %-52s 失败 %s" % (u[-40:], str(e)[:80]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
