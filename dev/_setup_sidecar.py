# -*- coding: utf-8 -*-
"""_setup_sidecar.py — 把"可选 Qwen 外挂"接到 dist/ 下（**不复制数据**，用目录联接）。

为什么这么做
------------
Qwen3-ASR-0.6B(1.75 GB) + ForcedAligner(1.71 GB) + 独立 Python 3.14 venv 约 4 GB，
塞进 onefile exe 会让**每次启动都解压 4 GB**，不可接受。
而项目**既有约定就是重度权重外挂**：`dist/` 下已经有 `mt3/`（176 MB）与
`piano_btd/`（165 MB）。本次沿用同一约定：

    dist/lang_id_models/        ← 目录联接（junction）到工作区的 lang_id_models/
    dist/lang_id_venv314/       ← 目录联接（junction）到工作区的 lang_id_venv314/
    dist/lang_id_qwen_runner.py ← 硬链接（hardlink，同卷可用）

于是 exe 侧的 `lang_id.resolve_resource()`（优先级 env → exe 同目录 → 打包内含 → 脚本目录）
会优先命中 exe 旁边的这些目录，Qwen 外挂即可用；**没装也完全没问题**——
`TS_LANG_BACKEND=auto` 会自动退回 exe 内置的 Silero lang95。

联接可整体删除，**不占用额外磁盘、不影响 exe**。

用法:
    python lang_dev/_setup_sidecar.py            # 建立联接
    python lang_dev/_setup_sidecar.py --remove   # 拆除联接
    python lang_dev/_setup_sidecar.py --status   # 只看状态
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")

DIRS = [("lang_id_models", "Qwen3-ASR-0.6B / Qwen3-ForcedAligner-0.6B / Silero onnx"),
        ("lang_id_venv314", "Qwen 专用 Python 3.14 环境（torch 2.14 CPU + qwen-asr）")]
FILES = [("lang_id_qwen_runner.py", "Qwen sidecar runner")]


def is_link(path):
    """判断是否联接/符号链接：Windows 上 os.path.islink 对 junction 返回 True（Py3.8+）。"""
    return os.path.islink(path) or (os.path.isdir(path) and
                                    bool(os.path.realpath(path) != os.path.abspath(path)))


def status():
    print("dist = %s\n" % DIST)
    for name, what in DIRS:
        p = os.path.join(DIST, name)
        if not os.path.exists(p):
            print("  %-24s 缺失           %s" % (name, what))
        elif is_link(p):
            print("  %-24s 联接 → %s" % (name, os.path.realpath(p)))
        else:
            print("  %-24s 实体目录（非联接）%s" % (name, what))
    for name, what in FILES:
        p = os.path.join(DIST, name)
        print("  %-24s %s" % (name, "存在" if os.path.exists(p) else "缺失"))
    exe = os.path.join(DIST, "TuneScript AI V0.5.1.exe")
    print("\n  exe: %s" % ("存在（%.1f MB）" % (os.path.getsize(exe) / 1048576.0)
                           if os.path.exists(exe) else "缺失"))


def setup_link(src, dst):
    if os.path.exists(dst):
        print("  跳过（已存在）：%s" % os.path.basename(dst))
        return True
    if not os.path.exists(src):
        print("  ✗ 源不存在：%s" % src)
        return False
    r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0 and os.path.exists(dst)
    print("  %s 目录联接 %s → %s" % ("✓" if ok else "✗", os.path.basename(dst), src))
    if not ok:
        print("     %s" % (r.stdout or r.stderr or "").strip()[:160])
    return ok


def setup_hardlink(src, dst):
    if os.path.exists(dst):
        print("  跳过（已存在）：%s" % os.path.basename(dst))
        return True
    if not os.path.exists(src):
        print("  ✗ 源不存在：%s" % src)
        return False
    r = subprocess.run(["cmd", "/c", "mklink", "/H", dst, src],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0 and os.path.exists(dst)
    print("  %s 硬链接 %s" % ("✓" if ok else "✗", os.path.basename(dst)))
    if not ok:
        # 硬链接失败就退回复制（同卷一般不会失败）
        import shutil
        try:
            shutil.copy2(src, dst)
            print("     （硬链接失败，已改为复制）")
            ok = True
        except Exception as e:
            print("     %s" % str(e)[:120])
    return ok


def remove():
    for name, _w in DIRS:
        p = os.path.join(DIST, name)
        if os.path.exists(p):
            subprocess.run(["cmd", "/c", "rmdir", p], capture_output=True)
            print("  已移除联接 %s" % name)
    for name, _w in FILES:
        p = os.path.join(DIST, name)
        if os.path.exists(p):
            os.remove(p)
            print("  已移除文件 %s" % name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        status()
        return 0
    if a.remove:
        remove()
        return 0
    print("建立 Qwen 外挂联接（不复制数据）…\n")
    ok = True
    for name, _w in DIRS:
        ok &= setup_link(os.path.join(ROOT, name), os.path.join(DIST, name))
    for name, _w in FILES:
        ok &= setup_hardlink(os.path.join(ROOT, name), os.path.join(DIST, name))
    print("")
    status()
    print("\n结果：%s" % ("全部就绪 ✅" if ok else "有失败项（见上）"))
    print("提示：exe 里已内置 Silero lang95（17 MB），所以**即使不做这一步也能用**；")
    print("      做了这一步才会自动选用 Qwen3-ASR（更准但慢约 90×）。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
