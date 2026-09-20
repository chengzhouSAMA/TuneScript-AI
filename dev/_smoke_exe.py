# -*- coding: utf-8 -*-
"""_smoke_exe.py — 对打包好的 exe 做**端到端冒烟测试**（不是"启动看看"）。

两臂：
  OFF：出厂行为（TS_LANG_SEG 不设）→ 必须跑通、产物 magic 齐全
  ON ：`TS_LANG_SEG=1` + `TS_LANG_BACKEND=silero_onnx`
       → 除跑通外，日志里必须出现「语种分段扒谱：…（后端 silero_onnx）」。
       这一条同时证明**三件事**：exe 里的新模块能 import、
       **打包进去的 Silero ONNX 被找到了**、onnxruntime 在 exe 里可用。

为什么要用 PIPE 接住输出：onefile **windowed** exe 的 sys.stdout/stderr 通常是 None
（CLI 会换成 devnull，看不到日志）；给它一个真实句柄后 `[cli]` 日志才会写出来。
这是项目排查打包问题的既有手段（备忘 附-19.3）。

用法: python lang_dev/_smoke_exe.py ["dist/TuneScript AI V0.5.1.exe"] [--arm both]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXE = os.path.join(ROOT, "dist", "TuneScript AI V0.5.1.exe")
CLIP = os.path.join(ROOT, "lang_dev", "_out_intro", "shiki_intro_0_30.wav")

MAGIC = {".pdf": b"%PDF", ".mid": b"MThd", ".wav": b"RIFF", ".xml": b"<?xml"}


def _smart_decode(b):
    """exe 的 print 走 Windows 控制台编码（中文常是 GBK/cp936），
    用 errors='replace' 的 utf-8 解码会得到乱码 → 逐个候选编码试，取替换字符最少者。"""
    if not b:
        return ""
    best, best_bad = "", 1 << 30
    for enc in ("utf-8", "gbk", "cp936", "utf-16"):
        try:
            s = b.decode(enc)
        except Exception:
            continue
        bad = s.count("\ufffd")
        if bad < best_bad:
            best, best_bad = s, bad
    return best


def check_outputs(outdir):
    """检查产物与 magic number。

    注意 magic 要用 `startswith`（`<?xml` 是 5 字节，读 4 字节永远比不中 —— 初版就栽在这）。
    """
    got = {}
    for d, _s, fs in os.walk(outdir):
        for f in fs:
            p = os.path.join(d, f)
            ext = os.path.splitext(f)[1].lower()
            if ext not in MAGIC:
                continue
            try:
                with open(p, "rb") as fh:
                    head = fh.read(8)
            except Exception:
                head = b""
            got.setdefault(ext, []).append((os.path.basename(f),
                                            head.startswith(MAGIC[ext]), head[:4]))
    return got


def run_arm(exe, tag, env_extra, clip, timeout=1800):
    outdir = tempfile.mkdtemp(prefix="smoke_%s_" % tag)
    env = os.environ.copy()
    env.update(env_extra)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [exe, "--cli", "--audio", clip, "--outdir", outdir]
    print("\n===== 臂 %s =====" % tag)
    print("  环境：%s" % (env_extra or "(出厂默认)"))
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("  ✗ 超时（>%ds）" % timeout)
        return {"tag": tag, "ok": False, "err": "timeout", "outdir": outdir}
    el = time.time() - t0
    out = _smart_decode(p.stdout)
    err = _smart_decode(p.stderr)
    logs = out + "\n" + err
    got = check_outputs(outdir)
    n_ok = sum(1 for ext, lst in got.items() for _n, ok, _h in lst if ok)
    n_all = sum(len(lst) for lst in got.values())
    print("  退出码 %s   用时 %.1fs" % (p.returncode, el))
    print("  产物：%s" % (", ".join("%s×%d" % (e, len(l)) for e, l in sorted(got.items()))
                          or "(无)"))
    print("  magic 通过 %d/%d" % (n_ok, n_all))
    # 关键日志行
    marks = [l.strip() for l in logs.splitlines()
             if any(k in l for k in ("语种分段", "语种分割", "旋律自检", "分离", "[cli]",
                                     "Traceback", "Error", "error"))]
    for l in marks[-14:]:
        print("    | %s" % l[:150])
    return {"tag": tag, "ok": p.returncode == 0 and n_all > 0 and n_ok == n_all,
            "rc": p.returncode, "sec": round(el, 1), "outdir": outdir,
            "outputs": {e: [n for n, _o, _h in l] for e, l in got.items()},
            "magic_ok": n_ok, "n_files": n_all, "logs": logs[-6000:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exe", nargs="?", default=DEFAULT_EXE)
    ap.add_argument("--arm", default="both", choices=["off", "on", "both"])
    ap.add_argument("--clip", default=CLIP)
    a = ap.parse_args()
    if not os.path.isfile(a.exe):
        print("找不到 exe：%s" % a.exe)
        return 2
    if not os.path.isfile(a.clip):
        print("找不到测试音频：%s（先跑 lang_dev/_slice_intro.py）" % a.clip)
        return 2
    print("exe  : %.1f MB" % (os.path.getsize(a.exe) / 1048576.0))
    print("音频 : %s" % a.clip)

    arms = []
    if a.arm in ("off", "both"):
        arms.append(("OFF 出厂", {}))
    if a.arm in ("on", "both"):
        arms.append(("ON 语种分段+内置Silero",
                     {"TS_LANG_SEG": "1", "TS_LANG_BACKEND": "silero_onnx"}))

    res = [run_arm(a.exe, t, e, a.clip) for t, e in arms]
    print("\n=== 汇总 ===")
    for r in res:
        print("  %-26s ok=%-5s rc=%-4s %6ss  magic %s/%s"
              % (r["tag"], r.get("ok"), r.get("rc"), r.get("sec"),
                 r.get("magic_ok"), r.get("n_files")))
    # ON 臂必须出现语种分段日志
    on = [r for r in res if r["tag"].startswith("ON")]
    if on:
        hit = "语种分段" in (on[0].get("logs") or "")
        print("\n  exe 内语种分段路径被触发：%s" % ("是 ✅" if hit else "否 ❌"))
        if not hit:
            print("  （若为「LID 不可用」说明打包内模型没被找到；以日志为准）")
        res[0]["_seg_hit"] = hit
    with open(os.path.join(ROOT, "lang_dev", "_smoke_exe.json"), "w",
              encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("\n明细已写入 lang_dev/_smoke_exe.json")
    return 0 if all(r.get("ok") for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
