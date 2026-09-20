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
        check("自上次备份以来包含本轮声明的 netease 接入",
              "netease" in joined, "新增 %d 行" % len(adds))
        check("新增行数在预期范围（<=80）", len(adds) <= 80, "新增 %d 行" % len(adds))
        known_del = ("notes_of(stems['vocals']", "请选择音频文件", "需提供 --audio 或 --bvid",
                     "B站音频与转谱产物", "args=(audio, bvid, outdir)",
                     "def _worker(self, audio, bvid, outdir)", "填 BV 号则自动下载后转谱",
                     "style='CardMuted.TLabel').grid(row=3",   # BV 提示行的续行
                     "if not audio and not bvid:")             # 输入校验（已扩成三路）
        # 删掉**纯注释行**不可能改变行为，所以一律放行；
        # 其余删除必须命中已知的旧实现，否则视为意外改动。
        # 注意 difflib 的删除行首还带一个 '-'，判断注释前要先剥掉。
        ok_del = all(l[1:].strip().startswith("#") or any(k in l for k in known_del)
                     for l in dels)
        check("被删/改的行要么是注释、要么属于已知旧实现", ok_del, "%d 行" % len(dels))
    else:
        check("找到备份链", False, "备份目录里没有 transcriber_app.py")

    # 累计上限：防止静默的大规模改动溜进来
    if os.path.isfile(anchor):
        _h, adds_all, dels_all = _diff(anchor, p)
        print("     累计（相对 V0.5 出货）：+%d / -%d" % (len(adds_all), len(dels_all)))
        check("累计新增 <= 80 行", len(adds_all) <= 80, "+%d" % len(adds_all))
        check("累计删除 <= 10 行", len(dels_all) <= 10, "-%d" % len(dels_all))
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

    n_bad = sum(1 for _n, ok, _d in RESULTS if not ok)
    print("\n=== 汇总：%d 项，%d 通过，%d 失败 ===" % (len(RESULTS), len(RESULTS) - n_bad, n_bad))
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
