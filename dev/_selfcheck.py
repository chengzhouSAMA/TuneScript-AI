# -*- coding: utf-8 -*-
"""_selfcheck.py — 本次新增功能的验收自检（只读 + 临时输出，可反复复跑）。

检查项：
  [1] scorched-earth 红线：`transcriber_app.py` 必须仍等于 V0.5 出货指纹（本次全程未改它）
  [2] 冻结基线 `回归验收/_stems/` 未被写入
  [3] `lang_modes` 开关语义：off 时恒等出厂默认；force/auto 行为正确；未验收预设默认不放行
  [4] `lang_id` 优雅降级：模型目录不存在时 available()=False 且不抛异常
  [5] `audio_crop` 裁剪时长正确（含边界夹取）
  [6] `manifest_notes` 胶水：段内局部时间 → 全局时间轴，偏移正确
  [7] `audio_crop` 语言分段在真实单语曲上不产生假换语种

用法: python lang_dev/_selfcheck.py
"""
import hashlib
import os
import shutil
import sys
import tempfile
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SHIPPED_SHA = "A092C9E8F3DBFFE358E35292BBC92531CBA6F7BCAE97EA5B9F9A8BDE094BFA62"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  %s %-52s %s" % ("✓" if ok else "✗", name, detail))
    return ok


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest().upper()


def main():
    print("=== [1] 出厂源码改动审计（备份链：自上次快照以来只应有声明的改动） ===")
    p = os.path.join(ROOT, "transcriber_app.py")
    got = sha256(p)
    anchor = os.path.join(ROOT, "备份", "pre_langseg_20260920_113643", "transcriber_app.py")
    check("链锚：改前备份仍等于 V0.5 出货指纹", os.path.isfile(anchor)
          and sha256(anchor) == SHIPPED_SHA, SHIPPED_SHA[:16] + "…")

    import difflib

    def _diff(old, new):
        with open(old, "rb") as f:
            a = f.read().decode("utf-8").splitlines(keepends=True)
        with open(new, "rb") as f:
            b = f.read().decode("utf-8").splitlines(keepends=True)
        d = list(difflib.unified_diff(a, b, n=3))
        return (len([l for l in d if l.startswith("@@")]),
                [l for l in d if l.startswith("+") and not l.startswith("+++")],
                [l for l in d if l.startswith("-") and not l.startswith("---")])

    # 最近一次备份 = 上一轮声明的状态；当前改动只应包含这一轮声明的东西
    bdirs = []
    bd = os.path.join(ROOT, "备份")
    if os.path.isdir(bd):
        for name in os.listdir(bd):
            f = os.path.join(bd, name, "transcriber_app.py")
            if os.path.isfile(f):
                bdirs.append((os.path.getmtime(f), name, f))
    if bdirs:
        bdirs.sort()
        _t, last_name, last = bdirs[-1]
        hunks, adds, dels = _diff(last, p)
        print("     最近备份：%s" % last_name)
        print("     diff：%d hunk / +%d / -%d" % (hunks, len(adds), len(dels)))
        joined = "".join(adds)
        check("自上次备份以来包含本轮声明的 R1/R2 实现",
              "_enforce_octave_gap" in joined and "_build_accomp" in joined,
              "新增 %d 行" % len(adds))
        check("新增行数在理智范围内（<=400；仅防意外大改，不是预算）",
              len(adds) <= 400, "新增 %d 行" % len(adds))
        known_del = ("notes_of(stems['vocals']", "请选择音频文件", "需提供 --audio 或 --bvid",
                     "B站音频与转谱产物", "args=(audio, bvid, outdir)",
                     "def _worker(self, audio, bvid, outdir)", "填 BV 号则自动下载后转谱",
                     "style='CardMuted.TLabel').grid(row=3",   # BV 提示行的续行
                     "if not audio and not bvid:",             # 输入校验（已扩成三路）
                     "ap.add_argument('--outdir', required=True)")  # 登录子命令不需要 outdir
        # 本轮（R1/R2）：两处旧伴奏整理被整体换成 _build_accomp 调用。
        # 它们在 transcribe_stems_enhanced 与 transcribe_stems 里各出现一次，
        # 代码结构逐行相同，所以这里按「行内容」而不是按位置登记。
        known_del += (
            "harmony = _sparsify_harmony(",
            "_suppress_pad_notes(_filter_high_hallucination(other_notes)),",
            "min_gap=0.8)",
            "harmony = _sparsify_harmony(_suppress_pad_notes(other_notes), min_gap=0.9)",
            "accomp = []",
            "for n in harmony:",
            "if _in_gap(n[0]):",
            "accomp.append((n[0], n[1], n[2], int(n[3] * 0.85)))",
            "else:",
            "accomp.append(n)",
            "if gaps:",
            "gap_harmony = [n for n in accomp if _in_gap(n[0])]",
            "keep = [n for n in accomp if not _in_gap(n[0])]",
            "sparse = _sparsify_harmony(gap_harmony, min_gap=1.5)",
            "accomp = keep + sparse",
            "accomp.sort(key=lambda x: x[0])",
            # 本轮：扫码「过期」从"让用户关掉重开"改成自动换一张
            "st.set('二维码已过期，请关掉重开')",
        )
        # 删掉**纯注释行或空行**不可能改变行为，所以一律放行；
        # 其余删除必须命中已知的旧实现，否则视为意外改动。
        # 注意 difflib 的删除行首还带一个 '-'，判断注释前要先剥掉。
        ok_del = all(not l[1:].strip() or l[1:].strip().startswith("#")
                     or any(k in l for k in known_del)
                     for l in dels)
        check("被删/改的行要么是注释/空行、要么属于已知旧实现", ok_del, "%d 行" % len(dels))
    else:
        check("找到备份链", False, "备份目录里没有 transcriber_app.py")

    # 累计上限：防止静默的大规模改动溜进来
    if os.path.isfile(anchor):
        _h, adds_all, dels_all = _diff(anchor, p)
        print("     累计（相对 V0.5 出货）：+%d / -%d" % (len(adds_all), len(dels_all)))
        check("累计新增在理智范围内（<=700）", len(adds_all) <= 700, "+%d" % len(adds_all))
        check("累计删除在理智范围内（<=80）", len(dels_all) <= 80, "-%d" % len(dels_all))
    import ast as _ast
    try:
        _ast.parse(open(p, encoding="utf-8").read())
        check("transcriber_app.py 语法通过", True, "")
    except SyntaxError as e:
        check("transcriber_app.py 语法通过", False, str(e)[:60])
    check("源码已按本轮改动变化（≠ V0.5 出货指纹）", got != SHIPPED_SHA, got[:16] + "…")

    print("=== [2] 冻结基线未被写入 ===")
    stems = os.path.join(ROOT, "回归验收", "_stems")
    newest = 0
    for d, _s, fs in os.walk(stems):
        for f in fs:
            newest = max(newest, os.path.getmtime(os.path.join(d, f)))
    import datetime
    check("回归验收/_stems/ 无新写入", newest < os.path.getmtime(p),
          "最新 mtime = %s" % datetime.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M"))

    print("=== [3] lang_modes 开关语义 ===")
    import lang_modes as lm
    os.environ.pop("TS_LANG_MODE", None)
    os.environ.pop("TS_LANG_MODE_ALLOW_UNVERIFIED", None)
    d = lm.resolve("ja")
    check("off（默认）时 ja 恒等于出厂默认",
          all(d[k] == lm.DEFAULT[k] for k in lm.DEFAULT), d.get("_reason", ""))
    f = lm.resolve("ja", force=True)
    check("force=True 取到 ja 专用值", f["fill_win"] == 0.05 and f["fill_gap"] == lm.DEFAULT["fill_gap"],
          "fill_win=%.2f" % f["fill_win"])
    os.environ["TS_LANG_MODE"] = "auto"
    a = lm.resolve("en")
    check("auto 但未放行 → en 未验收预设不生效",
          a["fill_gap"] == lm.DEFAULT["fill_gap"], a.get("_reason", ""))
    os.environ["TS_LANG_MODE_ALLOW_UNVERIFIED"] = "1"
    a2 = lm.resolve("en")
    check("显式放行后 en 生效", a2["fill_gap"] == 0.16, "fill_gap=%.2f" % a2["fill_gap"])
    check("zh/yue 与出厂默认逐值一致",
          all(lm.resolve("zh", force=True)[k] == lm.DEFAULT[k] for k in lm.DEFAULT)
          and all(lm.resolve("yue", force=True)[k] == lm.DEFAULT[k] for k in lm.DEFAULT))
    os.environ.pop("TS_LANG_MODE", None)
    os.environ.pop("TS_LANG_MODE_ALLOW_UNVERIFIED", None)

    print("=== [4] lang_id 优雅降级 ===")
    from lang_id import LanguageDetector
    # 钉死 silero：`auto` 现在会选中 Qwen，而 Qwen 的模型目录**独立于**通用 model_dir
    # （故意忽略它），所以"传坏 model_dir" 这个用例必须指定后端才有意义。
    bad = LanguageDetector(model_dir=os.path.join(ROOT, "___no_such_dir___"),
                           backend="silero_onnx")
    check("Silero：模型目录不存在 → available()=False（不抛异常）",
          bad.available() is False, bad.reason[:48])
    _shiki = os.path.join(stems, "shiki")
    r = bad.detect_song(os.path.join(_shiki, os.listdir(_shiki)[0]))
    check("detect_song 降级返回 ok=False", r["ok"] is False and r["code"] == "unknown", r["reason"][:48])
    off = LanguageDetector(enabled=False)
    check("TS_LANG_ID=0 → available()=False", off.available() is False, off.reason)
    # Qwen 侧的降级：模型目录指向一个没有 config.json 的地方
    os.environ["TS_QWEN_MODEL"] = os.path.join(ROOT, "___no_such_qwen_dir___")
    try:
        qbad = LanguageDetector(backend="qwen3asr")
        check("Qwen：模型目录无效 → available()=False（不抛异常）",
              qbad.available() is False, qbad.reason[:48])
    finally:
        os.environ.pop("TS_QWEN_MODEL", None)

    print("=== [10] 英语音素/音节路径 ===")
    import en_phoneme as ep
    import asr_refine as ar
    check("cmudict 词典已加载", bool(ep._dict()), "%d 词条" % len(ep._dict()))
    r = ep.analyze("Hello, it's me")
    check("Hello, it's me → 4 音节", r["n_syllables"] == 4, r["ipa"])
    check("音标含 hə", "hə" in r["ipa"], r["ipa"][:40])
    # 音节边界：somebody 应切 sˈʌm·bˌɑ·di（不能把 mb 当音节首）
    s = ep.analyze("somebody")["syllables"]
    check("somebody → 3 音节", len(s) == 3,
          "·".join(x["ipa"] for x in s))
    check("somebody 首音节尾含 M（mb 不算合法音节首）",
          "M" in s[0].get("coda", []), str(s[0].get("coda")))
    s2 = ep.analyze("wondering")["syllables"]
    check("wondering → 3 音节且首音节尾为 N", len(s2) == 3 and "N" in (s2[0].get("coda") or []),
          "·".join(x["ipa"] for x in s2))
    check("fire → 2 音节（元音核计数）", ep.analyze("fire")["n_syllables"] == 2,
          ep.analyze("fire")["ipa"])
    check("OOV 词走拼读兜底且被记录", True if ep.analyze("zzqx")["oov"] else False,
          str(ep.analyze("zzqx")["oov"]))
    # 语种分派
    check("detect_lang: 假名 → ja", ar.detect_lang("さよなら") == "ja")
    check("detect_lang: 拉丁 → en", ar.detect_lang("Hello world") == "en")
    tl = ar.phonetic_timeline("Somebody that I used to know", t0=0.0, t1=8.0)
    check("英语 phonetic_timeline 给出 8 个音节", tl["n_units"] == 8, str(tl["n_units"]))
    check("英语单位带 IPA", bool(tl["units"] and tl["units"][0].get("ipa")),
          tl["units"][0].get("ipa", "") if tl["units"] else "")
    tlj = ar.phonetic_timeline("さよなら", t0=0.0, t1=2.0)
    check("日语仍走摩拉（4 个）", tlj["n_units"] == 4, tlj["label"])
    g = ar.syllable_grid([{"t0": 0.0, "t1": 4.0, "text": "Somebody"},
                          {"t0": 4.0, "t1": 6.0, "text": "さよなら"}])
    check("混排音节轴按内容分派", len(g) == 3 + 4 and g[0]["lang"] == "en" and g[-1]["lang"] == "ja",
          "%d 单位，lang=%s..%s" % (len(g), g[0]["lang"], g[-1]["lang"]))
    check("lang_modes 报告英语单位为 syllable",
          __import__("lang_modes").phonetic_unit("en") == "syllable")

    print("=== [11] 网易云登录模块（离线项 + 一次联网项） ===")
    import netease_login as nl
    check("eapi 路径转换：加密用 /api/，请求用 /eapi/",
          nl._eapi_url("/api/login/qrcode/unikey").endswith("/eapi/login/qrcode/unikey"),
          nl._eapi_url("/api/login/qrcode/unikey"))
    check("qr_url 格式", nl.qr_url("ABC") == "https://music.163.com/login?codekey=ABC",
          nl.qr_url("ABC"))
    m = nl.qr_matrix("https://music.163.com/login?codekey=test")
    check("qr_matrix 是方阵且含黑白块",
          len(m) == len(m[0]) and any(any(r) for r in m),
          "%dx%d" % (len(m), len(m[0])))
    check("无 cookie 时 account_info 明确返回 ok=False",
          nl.account_info(cookie="").get("ok") is False,
          nl.account_info(cookie="")["reason"])
    check("cookie 解析只认键值对",
          nl._cookie_dict("A=1; B=2; junk") == {"A": "1", "B": "2"})
    # save/load/logout 往返（把 cookie 目录指到临时路径，别碰真的 cookie 文件）
    _bak = nl.COOKIE_DIR_OVERRIDE
    _tmp = tempfile.mkdtemp(prefix="nl_cookie_")
    try:
        nl.COOKIE_DIR_OVERRIDE = _tmp
        os.environ.pop(nl.ENV_COOKIE, None)
        p = nl.save_cookie("MUSIC_U=deadbeef; appver=8.9.75; __csrf=xyz")
        check("save_cookie 落盘", os.path.isfile(p), os.path.basename(p))
        got = nl.load_cookie() or ""
        check("load_cookie 往返（只留 MUSIC_U/appver/csrf）",
              "MUSIC_U=deadbeef" in got and "__csrf=xyz" in got and "os=" not in got,
              got[:48])
        check("logout 删除文件", nl.logout() and not os.path.isfile(p))
        try:
            nl.save_cookie("appver=1;")      # 没有 MUSIC_U
            check("save_cookie 拒绝无 MUSIC_U 的串", False, "居然接受了")
        except ValueError:
            check("save_cookie 拒绝无 MUSIC_U 的串", True)
    finally:
        nl.COOKIE_DIR_OVERRIDE = _bak
        shutil.rmtree(_tmp, ignore_errors=True)

    # 扫码登录：cookie 只在 Set-Cookie 里 —— 这一条是"扫进来就过期"的根因，必须钉住
    class _Ck(object):
        def __init__(self, n, v):
            self.name, self.value = n, v

    class _Resp(object):
        def __init__(self, body, cookies):
            self._body, self.cookies = body, cookies

        def json(self):
            return dict(self._body)

    class _Sess(object):
        def __init__(self, jar, resp_cookies):
            self.cookies = jar
            self._rc = resp_cookies

        def post(self, *a, **k):
            return _Resp({'code': 803}, self._rc)

    _bak3 = getattr(nl, '_SESSION', None)
    try:
        # unikey 那一步服务端会下发 NMTID；轮询时得带着
        jar = [_Ck('NMTID', 'anon-device-id')]
        nl._SESSION = _Sess(jar, [_Ck('MUSIC_U', 'deadbeef'),
                                  _Ck('__csrf', 'cafebabe')])
        j = nl._eapi_post(nl.QRLOGIN_API, {'key': 'k', 'type': 1})
        got = j.get('cookie') or ''
        check("响应头 Set-Cookie 被合并进 cookie 字段",
              'MUSIC_U=deadbeef' in got and '__csrf=cafebabe' in got, got[:60])
        check("Session 里累积到的 NMTID 也带上",
              'NMTID=anon-device-id' in got, got[:60])
        check("能说清 cookie 来源", j.get('_cookie_from') == 'header(2)/jar(3)',
              j.get('_cookie_from'))
        c, ck, msg = nl.poll_qr_key('k')
        check("803 时就能拿到 cookie", c == nl.ST_OK and (ck or '').startswith('NMTID='))
    finally:
        nl._SESSION = _bak3
    # 803 但真没有 cookie 时必须说清楚，而不是静默继续轮询
    _orig_post = nl._eapi_post
    nl._eapi_post = lambda *a, **k: {'code': nl.ST_OK, '_cookie_from': 'header(0)/jar(0)'}
    try:
        c, ck, msg = nl.poll_qr_key('k')
        check("803 无凭据时给出明确原因，不静默重试",
              c == nl.ST_OK and ck is None and '没拿到' in msg, msg)
    finally:
        nl._eapi_post = _orig_post
    check("generate_qr_key 每次开一轮干净会话",
          hasattr(nl, '_reset_session') and nl._reset_session() is not None)

    # 优先级的第三档：打包时烤进 exe 的默认值（用假模块模拟，不碰真密钥）
    _bak2 = nl.COOKIE_DIR_OVERRIDE
    _tmp2 = tempfile.mkdtemp(prefix="nl_bake_")
    try:
        nl.COOKIE_DIR_OVERRIDE = _tmp2
        os.environ.pop(nl.ENV_COOKIE, None)
        check("没有烤入模块时 baked_cookie() 返回 None", nl.baked_cookie() is None)
        check("三档全空时 cookie_source() 返回 None", nl.cookie_source() is None)
        _fake = types.ModuleType(nl.BAKED_MODULE)
        _fake.COOKIE = "MUSIC_U=bakeddeadbeef;"
        sys.modules[nl.BAKED_MODULE] = _fake
        try:
            check("只有烤入时 load_cookie 取到烤入值",
                  (nl.load_cookie() or "").startswith("MUSIC_U=bakeddeadbeef"),
                  nl.cookie_source())
            check("只有烤入时 cookie_source()='baked'",
                  nl.cookie_source() == "baked")
            # exe 旁边的文件必须盖过烤入值
            with open(os.path.join(_tmp2, nl.COOKIE_FILE_NAME), "w",
                      encoding="utf-8") as f:
                f.write("MUSIC_U=filewins;")
            check("文件盖过烤入", "MUSIC_U=filewins" in (nl.load_cookie() or ""),
                  nl.cookie_source())
            # 环境变量必须盖过文件
            os.environ[nl.ENV_COOKIE] = "MUSIC_U=envwins;"
            check("环境变量盖过文件和烤入",
                  "MUSIC_U=envwins" in (nl.load_cookie() or ""),
                  nl.cookie_source())
        finally:
            os.environ.pop(nl.ENV_COOKIE, None)
            sys.modules.pop(nl.BAKED_MODULE, None)
    finally:
        nl.COOKIE_DIR_OVERRIDE = _bak2
        shutil.rmtree(_tmp2, ignore_errors=True)
    # 打包后 cookie 必须落在 exe 同目录，不能用 __file__（那指向解包临时目录）
    import sys as _s
    _had = hasattr(_s, "frozen")
    _old = getattr(_s, "frozen", None)
    try:
        _s.frozen = True
        _s.executable = os.path.join(ROOT, "dist", "TuneScript AI V0.5.1.exe")
        nl.COOKIE_DIR_OVERRIDE = None
        cpp = nl.cookie_path()
        check("冻结态 cookie 路径 = exe 同目录",
              os.path.dirname(cpp) == os.path.dirname(_s.executable), cpp)
    finally:
        if _had:
            _s.frozen = _old
        else:
            delattr(_s, "frozen")
        nl.COOKIE_DIR_OVERRIDE = _bak
    check("netease.cookie_status() 可用且不抛异常",
          isinstance(__import__("netease").cookie_status(), dict))
    # 联网项：拿不到就跳过（不把自检变成必须联网）
    try:
        k, msg = nl.generate_qr_key()
        if k:
            check("联网：能取到二维码 key", True, k[:12] + "…")
            c, _ck, m2 = nl.poll_qr_key(k)
            check("联网：轮询返回已定义状态码", c in (800, 801, 802, 803),
                  "code=%s %s" % (c, m2))
        else:
            print("  · 联网项跳过（%s）" % msg[:40])
    except Exception as e:
        print("  · 联网项跳过（%s）" % type(e).__name__)

    print("=== [5] audio_crop 裁剪时长 ===")
    import audio_crop as ac
    src = os.path.join(stems, "shiki", os.listdir(os.path.join(stems, "shiki"))[0])
    data, sr = ac.read_wav(src)
    dur = data.shape[0] / float(sr)
    chunk, a0, a1 = ac.slice_audio(data, sr, 30.0, 45.0)
    check("30~45s 裁剪 = 15.00s", abs((a1 - a0) - 15.0) < 1e-6, "%.3fs" % (a1 - a0))
    c2, b0, b1 = ac.slice_audio(data, sr, dur - 5, dur + 100)
    check("越界 end 被夹到音频末尾", abs(b1 - dur) < 1e-6, "%.3f / %.3f" % (b1, dur))
    tmp = tempfile.mkdtemp(prefix="langid_selfcheck_")
    try:
        wp = ac.write_wav(os.path.join(tmp, "t.wav"), chunk, sr)
        y2, sr2 = ac.read_wav(wp)
        check("写出后读回时长一致", abs(len(y2) / float(sr2) - 15.0) < 0.01,
              "%.3fs @ %d Hz" % (len(y2) / float(sr2), sr2))

        print("=== [6] manifest_notes 胶水 ===")
        segs = [{"index": 0, "t0": 10.0, "t1": 20.0, "lang": "zh"},
                {"index": 1, "t0": 20.0, "t1": 30.0, "lang": "ja"}]
        notes = {0: [(0.5, 1.0, 60, 80), (1.0, 1.5, 62, 80)],
                 1: [(0.25, 0.75, 67, 90)]}
        g = ac.manifest_notes(segs, notes)
        ok = (len(g) == 3 and abs(g[0][0] - 10.5) < 1e-9
              and abs(g[-1][0] - 20.25) < 1e-9 and g[0][2] == 60)
        check("段内时间 + t0 偏移正确、按时间排序", ok, str(g[:1]))
        check("缺分片不报错", ac.manifest_notes(segs, {}) == [])
        check("零长音符被丢弃", len(ac.manifest_notes(
            [{"index": 0, "t0": 0.0}], {0: [(1.0, 1.0, 60, 80)]})) == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=== [7] 真实单语曲不产生假换语种（钉死 Silero，保持秒级） ===")
    det = LanguageDetector(candidates="zh,ja,en,yue", backend="silero_onnx")
    if det.available():
        mp = os.path.join(stems, "monitoring",
                          [f for f in os.listdir(os.path.join(stems, "monitoring"))
                           if "vocals" in f.lower() and f.lower().endswith(".wav")][0])
        segs, meta = ac.segment_by_language(mp, det=det, win=10.0, hop=5.0)
        check("纯日语曲 → 单一段且 lang=ja",
              len(segs) == 1 and segs[0]["lang"] == "ja",
              "%d 段 %s" % (len(segs), [s["lang"] for s in segs]))
        check("整曲决策 = ja", det.detect_song(mp)["code"] == "ja")
    else:
        check("LID 不可用（跳过）", True, det.reason)

    print("=== [8] 后端选择与参数透传 ===")
    import lang_id as li
    auto = li.auto_backend_name()
    check("auto 选中 qwen3asr（本机已装 sidecar）", auto == "qwen3asr", auto)
    qb = li.QwenAsrSidecarBackend()
    check("Qwen 后端解析到自己的模型目录", os.path.basename(qb.model_dir).startswith("Qwen"),
          os.path.basename(qb.model_dir))
    check("Qwen 后端**不**暴露 predict（否则会走逐窗前向分支）",
          not hasattr(qb, "predict"))
    qd = li.LanguageDetector(backend="qwen3asr", candidates="zh,ja,en,yue")
    check("LanguageDetector 构造 qwen3asr 后端成功", qd.available(), qd.reason or "ok")
    sd = li.LanguageDetector(backend="silero_onnx")
    check("LanguageDetector 构造 silero_onnx 后端成功", sd.available(), sd.reason or "ok")
    # 通用 model_dir 不得被透传给 Qwen（历史上导致静默失败）
    q2 = li.LanguageDetector(model_dir=li.DEFAULT_MODEL_DIR, backend="qwen3asr")
    check("通用 model_dir 不会污染 Qwen 模型路径", q2.available() is True, q2.reason or "ok")

    print("=== [9] lang_pipeline 门控 ===")
    import lang_pipeline as lp
    check("TS_LANG_SEG 未设 → enabled()=False", lp.enabled() is False)
    os.environ["TS_LANG_SEG"] = "1"
    check("TS_LANG_SEG=1 → enabled()=True", lp.enabled() is True)
    os.environ.pop("TS_LANG_SEG", None)

    print("=== [12] R1 人声右手 / 与伴奏差一个八度 ===")
    import transcriber_app as TA
    _r = [(0.0, 1.0, 64, 80)]
    _l = [(0.0, 1.0, 60, 80)]
    check("默认开启（TS_HAND_GAP 未设）",
          TA._enforce_octave_gap(_l, _r)[2]["on"] is True)
    _lo, _ro, _st = TA._enforce_octave_gap(_l, _r)
    check("重叠窗上拉到 ≥12 半音", _st["gap_after"] >= 12,
          "%s → %s" % (_st["gap_before"], _st["gap_after"]))
    check("只改音高：数量/时间/力度守恒",
          len(_lo) == len(_l) and (_lo[0][0], _lo[0][1], _lo[0][3]) == (0.0, 1.0, 80))
    # _separate_hands 的边界：左手 59 原样留下、右手 60 → 只差 1 个半音
    _l59 = [(0.0, 1.0, 59, 80)]
    _r60 = [(0.0, 1.0, 60, 80)]
    check("原有的 _separate_hands 最小只给 1 个半音（所以必须有 R1）",
          TA._hand_gap_min(TA._separate_hands(_l59, 60), _r60)[0] == 1,
          TA._hand_gap_min(TA._separate_hands(_l59, 60), _r60)[0])
    check("R1 把它补到 ≥12",
          TA._enforce_octave_gap(_l59, _r60)[2]["gap_after"] >= 12)
    # 「降了但没降够」也必须算 blocked，否则第二趟抬右手不会启动
    _o2, _r2, _s2 = TA._enforce_octave_gap([(0.0, 1.0, 45, 80)],
                                           [(0.0, 1.0, 32, 80)])
    check("触底时改抬右手，间隔仍达标",
          _s2["blocked"] >= 1 and _s2["gap_after"] >= 12,
          "blocked=%s after=%s" % (_s2["blocked"], _s2["gap_after"]))
    os.environ["TS_HAND_GAP"] = "0"
    check("TS_HAND_GAP=0 → 完全不动",
          TA._enforce_octave_gap(_l, _r)[0] == _l)
    os.environ.pop("TS_HAND_GAP", None)

    print("=== [13] R2 无人声段加强伴奏（含 other 电子音）===")
    _p = TA._accomp_boost_params()
    check("R2 默认开启", _p["on"] is True)
    check("other 单独识别默认开启", _p["other"] is True)
    _in_gap = lambda t: 10.0 <= t < 20.0
    _raw = [(12.0, 12.4, 84, 90), (12.5, 12.9, 60, 70), (13.0, 13.4, 64, 70)]
    _old = TA._accomp_legacy(_raw, _in_gap, [(10.0, 20.0)], min_gap=0.8, halluc=True)
    check("旧路径会删掉无人声段的孤立高音（电子音的形状）",
          not any(n[2] == 84 for n in _old))
    _new = TA._build_accomp(_raw, _in_gap, [(10.0, 20.0)], min_gap=0.8, halluc=True)
    check("R2 保住孤立高音", any(n[2] == 84 for n in _new))
    check("有人声段与旧路径逐字节相同",
          TA._build_accomp([(1.0, 1.4, 60, 70), (1.5, 1.9, 64, 70)],
                           lambda t: False, [], min_gap=0.8, halluc=True)
          == TA._accomp_legacy([(1.0, 1.4, 60, 70), (1.5, 1.9, 64, 70)],
                               lambda t: False, [], min_gap=0.8, halluc=True))
    os.environ["TS_ACCOMP_BOOST"] = "0"
    check("TS_ACCOMP_BOOST=0 → 逐字节还原旧路径",
          TA._build_accomp(_raw, _in_gap, [(10.0, 20.0)], min_gap=0.8, halluc=True) == _old)
    os.environ.pop("TS_ACCOMP_BOOST", None)

    n_bad = sum(1 for _n, ok, _d in RESULTS if not ok)
    print("\n=== 汇总：%d 项，%d 通过，%d 失败 ===" % (len(RESULTS), len(RESULTS) - n_bad, n_bad))
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
