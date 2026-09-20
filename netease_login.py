# -*- coding: utf-8 -*-
"""netease_login.py — 网易云**扫码登录**（拿自己的账号 cookie）+ cookie 可用性自检。

用途
----
免登录时网易云只给 128~320kbps，且欧美版权曲只放 30~45 秒试听。
登录自己的账号后可以拿整曲，黑胶会员还能拿无损。

登录流程（eapi 加密，与 `netease.py` 同一套算法）
------------------------------------------------
1. `POST /eapi/login/qrcode/unikey`  → 拿 unikey
2. 二维码内容 = `https://music.163.com/login?codekey=<unikey>`，用网易云 App 扫
3. `POST /eapi/login/qrcode/client/login` 轮询：
      801 等待扫码 / 802 已扫码待确认 / 803 登录成功（响应里带回 cookie）/ 800 二维码过期

⚠️ 登录接口的 payload 必须带一个 `header` 配置（os / appver / osver / deviceId / requestId），
只传 `type` 会返回 `code=400`。

cookie 存放
-----------
`netease_cookie.txt`（工作区根目录，已在 .gitignore 里）或环境变量 `TS_NETEASE_COOKIE`。
扫码成功后写文件；**不要提交到仓库**。

CLI:
    python netease_login.py            # 显示二维码并等待扫码
    python netease_login.py --check    # 只看当前 cookie 是否有效
    python netease_login.py --logout   # 删除本地 cookie
"""
import json
import os
import random
import sys
import time

try:
    import requests
except Exception:                                          # pragma: no cover
    requests = None

ROOT = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(ROOT, "netease_cookie.txt")
ENV_COOKIE = "TS_NETEASE_COOKIE"
REFERER = "https://music.163.com/"
UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_EAPI = "NMI/2.5.0 (iPhone; iOS 16.0; Scale/3.00)"
EAPI_HOST = "https://interface3.music.163.com"
UNIKEY_API = "/api/login/qrcode/unikey"
QRLOGIN_API = "/api/login/qrcode/client/login"

DEFAULT_CONFIG = {"os": "pc", "appver": "", "osver": "", "deviceId": "pyncm!"}
DEFAULT_COOKIES = {"os": "pc", "appver": "", "osver": "", "deviceId": "pyncm!"}

# 轮询返回码
ST_WAIT = 801          # 未扫码
ST_SCANNED = 802       # 已扫码，待确认
ST_OK = 803            # 登录成功
ST_EXPIRED = 800       # 二维码过期


def _eapi_params(path, payload):
    """复用 netease.py 里的 eapi 加密（AES-128-ECB + md5 摘要）。"""
    import netease as N
    return N.eapi_params(path, payload)


def _eapi_url(path):
    """eapi 的**请求 URL**：加密摘要用 `/api/...`，请求地址用 `/eapi/...`。

    漏掉这个转换会得到 `code=400 参数错误`。
    """
    return EAPI_HOST + path.replace("/api/", "/eapi/", 1)


def _eapi_post(path, payload, cookies=None):
    """发一个 eapi 请求，自动补 header 配置。"""
    cfg = dict(DEFAULT_CONFIG)
    cfg["requestId"] = str(random.randrange(20000000, 30000000))
    body = dict(payload)
    body.setdefault("header", json.dumps(cfg, separators=(",", ":")))
    ck = dict(DEFAULT_COOKIES)
    ck.update(cookies or {})
    r = requests.post(_eapi_url(path), data={"params": _eapi_params(path, body)},
                      headers={"User-Agent": UA_EAPI, "Referer": REFERER,
                               "Content-Type": "application/x-www-form-urlencoded"},
                      cookies=ck, timeout=20)
    try:
        return r.json()
    except Exception:
        return {"code": -1, "raw": r.text[:200]}


# --------------------------------------------------------------------------
# cookie 存取
# --------------------------------------------------------------------------
def load_cookie():
    """环境变量优先，其次本地文件。返回整串或 None。"""
    v = (os.environ.get(ENV_COOKIE) or "").strip()
    if v:
        return v
    if os.path.isfile(COOKIE_FILE):
        try:
            return open(COOKIE_FILE, encoding="utf-8").read().strip() or None
        except Exception:
            return None
    return None


def save_cookie(cookie):
    """把 cookie 写到本地文件（只留 MUSIC_U 与 appver）。返回写入路径。"""
    d = _cookie_dict(cookie)
    keep = {k: v for k, v in d.items() if k in ("MUSIC_U", "__csrf", "appver", "os", "osver")}
    if "MUSIC_U" not in keep:
        raise ValueError("cookie 里没有 MUSIC_U")
    txt = ";".join("%s=%s" % (k, v) for k, v in keep.items()) + ";"
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(txt)
    return COOKIE_FILE


def logout():
    """删除本地 cookie 文件。"""
    if os.path.isfile(COOKIE_FILE):
        os.remove(COOKIE_FILE)
        return True
    return False


def _cookie_dict(cookie):
    d = {}
    for part in (cookie or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            d[k.strip()] = v.strip()
    return d


# --------------------------------------------------------------------------
# cookie 可用性自检
# --------------------------------------------------------------------------
def account_info(cookie=None):
    """查当前 cookie 的账号信息，顺带判断是否有效。

    返回 {"ok","nickname","vip_type","user_id","reason"}
      vip_type: 0 无 / 10 黑胶 / 11 黑胶+（网易云的取值）
    """
    ck = cookie if cookie is not None else load_cookie()
    if not ck:
        return {"ok": False, "reason": "没有 cookie", "nickname": None,
                "vip_type": None, "user_id": None}
    try:
        r = requests.get("https://music.163.com/api/nuser/account/get",
                         headers={"User-Agent": UA_PC, "Referer": REFERER},
                         cookies=_cookie_dict(ck), timeout=15)
        j = r.json()
    except Exception as e:
        return {"ok": False, "reason": "请求失败：%s" % str(e)[:80], "nickname": None,
                "vip_type": None, "user_id": None}
    prof = j.get("profile") or {}
    acct = j.get("account") or {}
    if not prof.get("nickname") and not acct.get("id"):
        return {"ok": False, "reason": "cookie 无效或已过期", "nickname": None,
                "vip_type": None, "user_id": None}
    return {"ok": True, "reason": "已登录", "nickname": prof.get("nickname"),
            "vip_type": prof.get("vipType"), "user_id": acct.get("id"),
            "vip_label": {0: "无会员", 10: "黑胶 VIP", 11: "黑胶 SVIP"}.get(
                prof.get("vipType"), "未知")}


def quality_hint(cookie=None):
    """按当前 cookie 给一句人话提示：能拿到什么音质。"""
    info = account_info(cookie)
    if not info["ok"]:
        return "未登录：免登录最高一般到 320kbps，部分曲目只有 30~45 秒试听"
    if (info.get("vip_type") or 0) >= 10:
        return "已登录 %s（%s）：可尝试无损" % (info["nickname"], info.get("vip_label"))
    return "已登录 %s（无会员）：一般可拿 320kbps 整曲，无损仍需会员" % info["nickname"]


# --------------------------------------------------------------------------
# 扫码登录
# --------------------------------------------------------------------------
def generate_qr_key():
    """取二维码 key；失败返回 (None, 原因)。"""
    j = _eapi_post(UNIKEY_API, {"type": 1})
    if j.get("code") == 200 and j.get("unikey"):
        return j["unikey"], "ok"
    return None, "取二维码失败：code=%s %s" % (j.get("code"), j.get("message") or "")


def qr_url(unikey):
    return "https://music.163.com/login?codekey=%s" % unikey


def qr_matrix(url):
    """二维码的布尔矩阵（True=黑块），供 GUI 画布直接画。"""
    import qrcode
    q = qrcode.QRCode(border=2)
    q.add_data(url)
    q.make(fit=True)
    return q.get_matrix()


def poll_qr_key(unikey):
    """轮询一次。返回 (状态码, cookie串或None, 说明)。"""
    j = _eapi_post(QRLOGIN_API, {"key": unikey, "type": 1})
    code = j.get("code")
    if code == ST_OK:
        return code, j.get("cookie"), "登录成功"
    if code == ST_SCANNED:
        return code, None, "已扫码，请在手机上确认"
    if code == ST_EXPIRED:
        return code, None, "二维码已过期"
    if code == ST_WAIT:
        return code, None, "等待扫码"
    return code, None, "未知状态：%s" % json.dumps(j, ensure_ascii=False)[:120]


def login(save=True, progress=None, poll=2.0, timeout=300):
    """走完整扫码流程：显示二维码 → 轮询 → 保存 cookie。

    `progress(status_code, message)` 会被反复调用，GUI 可以据此更新界面。
    返回 (cookie or None, 说明)。
    """
    def log(m):
        if progress:
            try:
                progress(m)
            except Exception:
                pass
    unikey, msg = generate_qr_key()
    if not unikey:
        return None, msg
    url = qr_url(unikey)
    try:
        import qrcode
        q = qrcode.QRCode(border=1)
        q.add_data(url)
        q.make(fit=True)
        q.print_ascii(invert=True)
    except Exception:
        pass
    log("二维码：%s" % url)
    log("请用网易云音乐 App 扫码")
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        code, cookie, msg = poll_qr_key(unikey)
        if msg != last:
            log(msg)
            last = msg
        if code == ST_OK and cookie:
            if save:
                p = save_cookie(cookie)
                log("已保存到 %s" % os.path.basename(p))
            return cookie, "登录成功"
        if code == ST_EXPIRED:
            return None, "二维码已过期，请重试"
        time.sleep(poll)
    return None, "超时未完成登录"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="网易云扫码登录 / cookie 自检")
    ap.add_argument("--check", action="store_true", help="只看当前 cookie 状态")
    ap.add_argument("--logout", action="store_true", help="删除本地 cookie")
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args(argv)

    if a.logout:
        print("已删除本地 cookie" if logout() else "本来就没有本地 cookie")
        return 0
    if a.check:
        info = account_info()
        print(json.dumps(info, ensure_ascii=False, indent=2))
        print(quality_hint())
        return 0 if info["ok"] else 1

    info = account_info()
    print("当前状态：%s" % quality_hint())
    if info["ok"]:
        print("已登录，无需重新扫码；要换账号先跑 --logout")
        return 0
    print("\n开始扫码登录…")
    cookie, msg = login(progress=lambda m: print("  " + m), timeout=a.timeout)
    if not cookie:
        print("失败：%s" % msg)
        return 1
    print("\n" + quality_hint())
    return 0


if __name__ == "__main__":
    sys.exit(_main())
