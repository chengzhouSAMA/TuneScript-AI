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
COOKIE_FILE_NAME = "netease_cookie.txt"
COOKIE_DIR_OVERRIDE = None      # 测试用：把 cookie 目录指到临时路径
ENV_COOKIE = "TS_NETEASE_COOKIE"
# 构建期「烤进 exe」的默认 cookie 所在模块名。仓库里没有这个文件，
# 只有 spec 在打包时、且构建目录下确实放了 netease_cookie.txt 才会临时生成。
BAKED_MODULE = "netease_cookie_baked"


def cookie_dir():
    """cookie 文件所在目录：打包后取 **exe 同目录**，开发态取脚本目录。

    不能用 `__file__` 定位：onefile 打包后它指向解包出来的临时目录，
    写进去的 cookie 会随临时目录被清掉，也读不到用户放在 exe 旁边的文件。
    """
    if COOKIE_DIR_OVERRIDE:
        return COOKIE_DIR_OVERRIDE
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return ROOT


def cookie_path():
    """cookie 文件的完整路径（用户就是往这个文件里填 MUSIC_U）。"""
    return os.path.join(cookie_dir(), COOKIE_FILE_NAME)
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

# 扫码流程共用的 HTTP 会话（见 `_session()`）
_SESSION = None


def _eapi_params(path, payload):
    """复用 netease.py 里的 eapi 加密（AES-128-ECB + md5 摘要）。"""
    import netease as N
    return N.eapi_params(path, payload)


def _eapi_url(path):
    """eapi 的**请求 URL**：加密摘要用 `/api/...`，请求地址用 `/eapi/...`。

    漏掉这个转换会得到 `code=400 参数错误`。
    """
    return EAPI_HOST + path.replace("/api/", "/eapi/", 1)


def _session():
    """整个扫码流程共用一个 requests.Session。

    为什么要共用：unikey 那一步服务端会下发 cookie，轮询时得带着；
    每次开新连接等于每次都"换了个人"，登录成功后拿到的凭据也对不上。
    """
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
    return _SESSION


def _reset_session():
    """重新开始一次扫码：把上一次登录留下的 cookie 清干净。"""
    global _SESSION
    try:
        if _SESSION is not None:
            _SESSION.cookies.clear()
    except Exception:
        pass
    _SESSION = requests.Session()
    return _SESSION


def _eapi_post(path, payload, cookies=None):
    """发一个 eapi 请求，自动补 header 配置。

    ⚠️ **登录成功的 cookie 只在响应头 `Set-Cookie` 里，body 里没有。**
    所以这里把响应（以及本次登录 Session）拿到的 cookie 合并进返回值的 `cookie` 字段。
    漏掉这一步的后果很隐蔽：803 明明成功了却拿不到凭据 → 代码继续轮询 →
    已经被消费掉的 key 下一次返回 800 → 界面上显示「扫进来就过期」。
    """
    cfg = dict(DEFAULT_CONFIG)
    cfg["requestId"] = str(random.randrange(20000000, 30000000))
    body = dict(payload)
    body.setdefault("header", json.dumps(cfg, separators=(",", ":")))
    ck = dict(DEFAULT_COOKIES)
    ck.update(cookies or {})
    s = _session()
    r = s.post(_eapi_url(path), data={"params": _eapi_params(path, body)},
               headers={"User-Agent": UA_EAPI, "Referer": REFERER,
                        "Content-Type": "application/x-www-form-urlencoded"},
               cookies=ck, timeout=20)
    try:
        j = r.json()
    except Exception:
        return {"code": -1, "raw": r.text[:200]}
    if not isinstance(j, dict):
        return {"code": -1, "raw": str(j)[:200]}
    # 本次响应的 Set-Cookie 优先，其次才是 Session 里累积到的
    jar = {}
    for c in s.cookies:
        jar[c.name] = c.value
    n_resp = 0
    for c in r.cookies:
        jar[c.name] = c.value
        n_resp += 1
    if jar:
        if not j.get("cookie"):
            j["cookie"] = ";".join("%s=%s" % (k, v) for k, v in jar.items())
            j["_cookie_from"] = "header(%d)/jar(%d)" % (n_resp, len(jar))
        else:
            j["_cookie_from"] = "body"
        j["_cookie_names"] = sorted(jar)
    return j


# --------------------------------------------------------------------------
# cookie 存取
# --------------------------------------------------------------------------
def baked_cookie():
    """打包时烤进 exe 的默认 cookie；没有就返回 None。"""
    mod = sys.modules.get(BAKED_MODULE)
    if mod is None:
        try:
            mod = __import__(BAKED_MODULE)
        except Exception:
            return None
    return (getattr(mod, "COOKIE", "") or "").strip() or None


def cookie_source():
    """当前 cookie 来自哪里：'env' / 'file' / 'baked'，都没有则 None。"""
    if (os.environ.get(ENV_COOKIE) or "").strip():
        return "env"
    if os.path.isfile(cookie_path()):
        return "file"
    if baked_cookie():
        return "baked"
    return None


def load_cookie():
    """按优先级取 cookie：环境变量 → exe 同目录文件 → 打包时烤入。全无则 None。

    烤入的只是**默认值**，放一个文件或设一个环境变量就能随时盖掉它。
    """
    v = (os.environ.get(ENV_COOKIE) or "").strip()
    if v:
        return v
    p = cookie_path()
    if os.path.isfile(p):
        try:
            t = open(p, encoding="utf-8").read().strip()
            if t:
                return t
        except Exception:
            pass
    return baked_cookie()


def save_cookie(cookie):
    """把 cookie 写到本地文件（只留必要字段）。返回写入路径。"""
    d = _cookie_dict(cookie)
    keep = {k: v for k, v in d.items() if k in ("MUSIC_U", "__csrf", "appver", "os", "osver")}
    if "MUSIC_U" not in keep:
        raise ValueError("cookie 里没有 MUSIC_U")
    txt = ";".join("%s=%s" % (k, v) for k, v in keep.items()) + ";"
    p = cookie_path()
    with open(p, "w", encoding="utf-8") as f:
        f.write(txt)
    return p


def logout():
    """删除本地 cookie 文件，并把扫码会话里的凭据一起清掉。"""
    _reset_session()
    p = cookie_path()
    if os.path.isfile(p):
        os.remove(p)
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
    """取二维码 key；失败返回 (None, 原因)。每次调用都开一轮干净的会话。"""
    _reset_session()
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
        ck = j.get("cookie")
        if ck:
            return code, ck, "登录成功"
        # 拿到了 803 却没有凭据：说清楚来源，别让上层继续傻轮询
        return code, None, ("登录成功但没拿到 cookie（来源 %s，字段 %s）"
                            % (j.get("_cookie_from") or "无",
                               ",".join(j.get("_cookie_names") or []) or "无"))
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
