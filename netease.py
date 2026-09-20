# -*- coding: utf-8 -*-
"""netease.py — 网易云音乐搜索 + 下载（供转谱使用）。

CLI:
python netease.py --search "バカみたいに 柿崎ユウタ"
python netease.py --id 2103987239 --outdir ./下载 --quality exhigh
"""
import hashlib
import json
import os
import re
import sys
import time

try:
    import requests
except Exception:                                          # pragma: no cover
    requests = None

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_EAPI = "NMI/2.5.0 (iPhone; iOS 16.0; Scale/3.00)"
REFERER = "https://music.163.com/"
TIMEOUT = 20

# eapi 参数加密用的固定 key（公开算法）
EAPI_KEY = b"e82ckenh8dichen8"

# 音质档位 -> 该档位的目标码率（用于明文接口的 br 参数）
LEVEL_BR = {"standard": 128000, "higher": 192000, "exhigh": 320000,
            "lossless": 999000, "hires": 1900000}

# 请求档位失败时的降级顺序（从高到低）
FALLBACK = ["hires", "lossless", "exhigh", "higher", "standard"]

ENV_COOKIE = "TS_NETEASE_COOKIE"
ENV_TRIAL = "TS_NETEASE_ALLOW_TRIAL"      # "1" 允许下载试听片段（默认拒绝）
COOKIE_FILE = "netease_cookie.txt"


# --------------------------------------------------------------------------
# Cookie
# --------------------------------------------------------------------------
def load_cookie():
    """按 环境变量 -> 本地文件 的顺序取 cookie。没有就返回 None。

    文件路径交给 `netease_login.cookie_path()` 统一决定，
    它按「exe 同目录优先」定位——不能用 `__file__`，那在打包后指向解包临时目录。
    """
    v = (os.environ.get(ENV_COOKIE) or "").strip()
    if v:
        return v
    try:
        import netease_login as L
        return L.load_cookie()
    except Exception:
        return None


def _cookie_dict(cookie):
    """把 `MUSIC_U=xxx; appver=8.9.75;` 解析成 dict。"""
    d = {}
    for part in (cookie or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def cookie_status():
    """检查当前 cookie 是否有效（没有 cookie 也返回一个明确结论）。

    返回 {"has","ok","reason","nickname","vip_type","vip_label"}。
    下载前提示用：cookie 失效时音质会静默降级、欧美版权曲会给试听段。
    """
    try:
        import netease_login as L          # 延迟导入：登录模块反过来要用本模块的 eapi 加密
        info = dict(L.account_info())
        info["has"] = bool(L.load_cookie())
        return info
    except Exception as e:
        return {"has": False, "ok": False, "nickname": None, "vip_type": None,
                "vip_label": None, "reason": "检查失败：%s" % str(e)[:80]}


# --------------------------------------------------------------------------
# 搜索 / 详情
# --------------------------------------------------------------------------
def search(keyword, limit=10):
    """关键词搜索。返回 [{id, name, artists, album, duration_ms}]。"""
    if not requests:
        return []
    try:
        r = requests.get("https://music.163.com/api/search/get/web",
                         params={"s": keyword, "type": 1, "limit": limit, "offset": 0},
                         headers={"User-Agent": UA_PC, "Referer": REFERER}, timeout=TIMEOUT)
        songs = ((r.json().get("result") or {}).get("songs") or [])
    except Exception:
        return []
    out = []
    for s in songs:
        out.append({"id": s.get("id"), "name": s.get("name") or "",
                    "artists": [a.get("name") for a in (s.get("artists") or [])],
                    "album": (s.get("album") or {}).get("name") or "",
                    "duration_ms": s.get("duration")})
    return out


def song_detail(song_id):
    """歌曲详情（补时长等；搜索接口偶尔不给）。"""
    if not requests:
        return {}
    try:
        r = requests.get("https://music.163.com/api/song/detail",
                         params={"ids": "[%d]" % song_id},
                         headers={"User-Agent": UA_PC, "Referer": REFERER}, timeout=TIMEOUT)
        songs = (r.json().get("songs") or [])
        if not songs:
            return {}
        s = songs[0]
        return {"id": s.get("id"), "name": s.get("name") or "",
                "artists": [a.get("name") for a in (s.get("artists") or [])],
                "album": (s.get("album") or {}).get("name") or "",
                "duration_ms": s.get("duration")}
    except Exception:
        return {}


# --------------------------------------------------------------------------
# eapi 参数加密
# --------------------------------------------------------------------------
def eapi_params(url_path, payload):
    """网易云 eapi 加密：AES-128-ECB(PKCS7) + md5 摘要。"""
    from Crypto.Cipher import AES
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.md5(("nobody%suse%smd5forencrypt" % (url_path, text)).encode()).hexdigest()
    data = "%s-36cd479b6b5-%s-36cd479b6b5-%s" % (url_path, text, digest)
    raw = data.encode()
    pad = 16 - len(raw) % 16
    cipher = AES.new(EAPI_KEY, AES.MODE_ECB)
    return cipher.encrypt(raw + bytes([pad]) * pad).hex().upper()


# --------------------------------------------------------------------------
# 取直链
# --------------------------------------------------------------------------
def _probe_plain(song_id, br):
    """明文接口 api/song/enhance/player/url —— 简单、无需加密，优先试它。"""
    r = requests.get("https://music.163.com/api/song/enhance/player/url",
                     params={"id": song_id, "ids": "[%d]" % song_id, "br": br},
                     headers={"User-Agent": UA_PC, "Referer": REFERER}, timeout=TIMEOUT)
    d = (r.json().get("data") or [{}])[0]
    return _norm(d, source="plain")


def _probe_eapi(song_id, level, cookie=None):
    """eapi 加密接口 —— 覆盖更广，能拿到 320k。"""
    path = "/api/song/enhance/player/url/v1"
    payload = {"ids": "[%d]" % song_id, "level": level, "encodeType": "flac",
               "header": json.dumps({"os": "ios", "appver": "9.0.10"},
                                    separators=(",", ":"))}
    cookies = {"os": "ios", "appver": "9.0.10"}
    if cookie:
        cookies.update(_cookie_dict(cookie))
    r = requests.post("https://interface3.music.163.com/eapi/song/enhance/player/url/v1",
                      data={"params": eapi_params(path, payload)},
                      headers={"User-Agent": UA_EAPI, "Referer": REFERER,
                               "Content-Type": "application/x-www-form-urlencoded"},
                      cookies=cookies, timeout=TIMEOUT)
    d = (r.json().get("data") or [{}])[0]
    return _norm(d, source="eapi")


def _norm(d, source=""):
    """把接口返回归一化；**记下服务端实际给了什么**（可能被静默降级）。"""
    return {"source": source, "code": d.get("code"), "url": d.get("url"),
            "br": d.get("br") or 0, "size": d.get("size") or 0,
            "fmt": (d.get("type") or "").lower(),
            "level": d.get("level"), "trial": d.get("freeTrialInfo")}


def resolve(song_id, level="exhigh", cookie=None):
    """取可下载直链。返回 dict（永远不抛异常）。"""
    cookie = cookie if cookie is not None else load_cookie()
    tried = []

    def ok(x):
        return bool(x.get("url")) and int(x.get("br") or 0) > 0

    if not requests:
        return {"ok": False, "reason": "requests 不可用", "tried": tried}

    plans = []
    br = LEVEL_BR.get(level, 320000)
    for b in (br, 320000, 128000):
        plans.append(("plain", b))
    idx = FALLBACK.index(level) if level in FALLBACK else 2
    for lv in FALLBACK[idx:]:
        plans.append(("eapi", lv))

    seen = set()
    for kind, arg in plans:
        key = (kind, arg)
        if key in seen:
            continue
        seen.add(key)
        try:
            x = (_probe_plain(song_id, arg) if kind == "plain"
                 else _probe_eapi(song_id, arg, cookie))
        except Exception as e:
            tried.append({"try": "%s:%s" % (kind, arg), "err": str(e)[:60]})
            continue
        tried.append({"try": "%s:%s" % (kind, arg), "code": x["code"],
                      "br": x["br"], "level": x["level"], "fmt": x["fmt"],
                      "trial": bool(x["trial"])})
        if not ok(x):
            continue
        if x["trial"] and os.environ.get(ENV_TRIAL, "0") != "1":
            # 只给了试听片段 —— 拿去转谱会得到"45 秒的歌"
            return {"ok": False, "tried": tried, "trial": x["trial"],
                    "reason": "该曲免登录只能拿试听片段（%s~%s 秒），已拒绝。"
                              "填 cookie（%s）或设 %s=1 可强制继续。"
                              % ((x["trial"] or {}).get("start"), (x["trial"] or {}).get("end"),
                                 ENV_COOKIE, ENV_TRIAL)}
        got_lv = x["level"] or ""
        return {"ok": True, "url": x["url"], "br": x["br"], "size": x["size"],
                "fmt": x["fmt"], "level": got_lv, "source": x["source"],
                "downgraded": bool(got_lv and got_lv != level), "tried": tried,
                "trial": x["trial"],
                "reason": "请求 %s -> 实际 %s %dkbps %s"
                          % (level, got_lv or "?", int(x["br"] / 1000), x["fmt"])}
    return {"ok": False, "tried": tried,
            "reason": "所有接口都拿不到直链（下架 / 版权受限 / 需要会员 cookie）"}


# --------------------------------------------------------------------------
# 下载
# --------------------------------------------------------------------------
_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(s, maxlen=80):
    s = _BAD.sub("_", (s or "").strip()).strip("._ ")
    return (s[:maxlen] or "netease")


def download(url, dest, progress=None, chunk=1 << 16, expect_size=0):
    """流式下载到 dest。返回 (路径, 字节数)；失败抛异常由调用方兜底。"""
    def log(m):
        if progress:
            try:
                progress(m)
            except Exception:
                pass
    d = os.path.dirname(os.path.abspath(dest))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = dest + ".part"
    got = 0
    t0 = time.time()
    with requests.get(url, headers={"User-Agent": UA_PC, "Referer": REFERER},
                      stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        with open(tmp, "wb") as f:
            for b in r.iter_content(chunk):
                if not b:
                    continue
                f.write(b)
                got += len(b)
                if total and got % (1 << 20) < chunk:
                    log("下载中 %d%%（%.1f/%.1f MB）"
                        % (100 * got // total, got / 1048576, total / 1048576))
    os.replace(tmp, dest)
    log("下载完成 %.1f MB，用时 %.1fs" % (got / 1048576, time.time() - t0))
    partial = bool(expect_size and got < expect_size * 0.97)
    return dest, got, partial


def fetch_song(query=None, song_id=None, out_dir=".", level="exhigh",
               cookie=None, pick=0, progress=None):
    """搜索（或按 id）→ 取直链 → 下载。返回 (路径 or None, info)。"""
    def log(m):
        if progress:
            try:
                progress(m)
            except Exception:
                pass
    info = {"query": query, "song_id": song_id, "level": level, "candidates": [],
            "path": None, "ok": False, "reason": "", "resolve": None}
    # cookie 自检：有 cookie 但已失效时要明说，否则用户会以为"开了会员却没生效"
    st = cookie_status()
    info["cookie"] = {k: st.get(k) for k in ("has", "ok", "reason", "nickname",
                                             "vip_type", "vip_label")}
    if st.get("has") and not st.get("ok"):
        log("cookie 已失效（%s），本次按未登录处理" % st.get("reason"))
    elif st.get("ok"):
        log("已登录：%s（%s）" % (st.get("nickname"), st.get("vip_label")))

    if song_id is None:
        cands = search(query, limit=10)
        info["candidates"] = cands
        if not cands:
            info["reason"] = "搜索没有结果"
            return None, info
        if pick >= len(cands):
            pick = 0
        song_id = cands[pick]["id"]
        info["picked"] = cands[pick]
        log("选中：%s - %s（id=%s）"
            % (cands[pick]["name"], "/".join(cands[pick]["artists"]), song_id))
    else:
        d = song_detail(song_id)
        if d:
            info["picked"] = d

    info["song_id"] = song_id
    res = resolve(song_id, level=level, cookie=cookie)
    info["resolve"] = {k: res.get(k) for k in
                       ("ok", "reason", "level", "br", "fmt", "source",
                        "downgraded", "size", "trial")}
    info["tried"] = res.get("tried")
    if not res.get("ok"):
        info["reason"] = res.get("reason") or "取直链失败"
        log("取直链失败：%s" % info["reason"])
        return None, info

    log(res["reason"])
    if res.get("downgraded"):
        log("注意：服务端把音质降级了（无损通常需要黑胶会员 cookie）")

    pk = info.get("picked") or {}
    name = "%s - %s.%s" % (safe_name(pk.get("name") or str(song_id)),
                           safe_name("/".join(pk.get("artists") or []) or "unknown"),
                           res["fmt"] or "mp3")
    dest = os.path.join(out_dir, name)
    try:
        path, n, partial = download(res["url"], dest, progress=progress,
                                    expect_size=int(res.get("size") or 0))
    except Exception as e:
        info["reason"] = "下载失败：%s" % str(e)[:120]
        log(info["reason"])
        return None, info

    info.update({"ok": True, "path": path, "bytes": n, "partial": partial,
                 "reason": res["reason"]})
    if partial:
        log("警告：实际字节数少于服务端声明的 size，文件可能不完整")
    return path, info


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="网易云音乐搜索 + 下载（供转谱）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--search", help="关键词搜索")
    g.add_argument("--id", type=int, help="歌曲 ID")
    ap.add_argument("--pick", type=int, default=0, help="搜索结果取第几个（默认 0）")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--quality", default="exhigh",
                    choices=["standard", "higher", "exhigh", "lossless", "hires"])
    a = ap.parse_args(argv)

    print("cookie：%s" % ("已提供（可能可取无损）" if load_cookie() else "无（最高一般到 320k）"))
    if a.search:
        cs = search(a.search, limit=10)
        print("\n搜索结果 %d 条：" % len(cs))
        for i, c in enumerate(cs):
            print("  [%d] %-32s %-22s %s" % (i, c["name"][:30],
                                             "/".join(c["artists"])[:20],
                                             "%.0fs" % ((c["duration_ms"] or 0) / 1000)))
        if not cs:
            return 2
    path, info = fetch_song(query=a.search, song_id=a.id, out_dir=a.outdir,
                            level=a.quality, pick=a.pick, progress=print)
    print("\n结果：ok=%s" % info["ok"])
    if info.get("resolve"):
        print("  音质：%s" % info["resolve"])
    print("  %s" % (path or info["reason"]))
    return 0 if info["ok"] else 1


if __name__ == "__main__":
    sys.exit(_main())
