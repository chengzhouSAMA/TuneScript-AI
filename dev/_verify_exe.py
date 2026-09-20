# -*- coding: utf-8 -*-
"""_verify_exe.py — 检查打包好的 exe 里**到底有没有**本次新增的东西。

两个层次（必须都查，否则会误报）：
  1. **CArchive（文件层）**：datas / binaries 落在 exe 里 —— 模型权重、字典、runner 脚本；
  2. **PYZ（模块层）**：Python 模块被编译进 `PYZ.pyz`，**不**逐个出现在 CArchive 目录表中，
     所以"在 CArchive 里找不到 `lang_id`"并不代表模块没打进去 —— 必须解出 PYZ 再查。

（本项目对 V0.5 就是用 CArchiveReader 解内嵌模块来验的，见 备忘 附-13.2。）

用法: python lang_dev/_verify_exe.py ["dist/TuneScript AI V0.5.1.exe"]
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- CArchive 层：必须存在的数据文件 ----
MUST_DATA = [
    ("basic_pitch/saved_models/icassp_2022", "Basic Pitch 模型"),
    ("demucs/remote", "Demucs 远端配置"),
    ("mt3_infer/config", "MT3 配置"),
    ("lang_id_models/lang_classifier_95.onnx", "Silero LID ONNX 权重 (17MB)"),
    ("lang_id_models/lang_dict_95.json", "Silero 标签表"),
    ("lang_id_models/lang_group_dict_95.json", "Silero 语种组表"),
    ("lang_id_qwen_runner.py", "Qwen sidecar runner 脚本"),
    ("pykakasi/data/kanwadict4.db", "pykakasi 汉字字典 (9.8MB)"),
    ("pykakasi/data/hepburndict3.db", "pykakasi 罗马音字典"),
    ("cmudict/data", "cmudict 发音词典数据（英语音素必带）"),
]

# ---- PYZ 层：必须存在的 Python 模块 ----
MUST_MOD = [
    ("__main__", "主程序（PyInstaller 把入口脚本存为 __main__）"),
    ("lang_id", "LID 适配层"),
    ("netease", "网易云搜索下载"),
    ("en_phoneme", "英语音素/音节/IPA"),
    ("cmudict", "CMU 发音词典"),
    ("netease_login", "网易云扫码登录"),
    ("qrcode", "二维码生成"),
    ("audio_crop", "音频裁剪 / 语言分段"),
    ("lang_modes", "语种预设表"),
    ("lang_pipeline", "语种分割扒谱"),
    ("ja_romaji", "日语→罗马音摩拉"),
    ("asr_refine", "未识别段再切割重试"),
    ("lyrics_fetch", "联网取歌词外挂"),
    ("lyrics_match", "歌词比对"),
    ("onnxruntime", "ONNX Runtime"),
    ("pykakasi", "pykakasi"),
    ("requests", "requests（取歌词用）"),
]

# 明确**不**应该被塞进来的大块头（防止再次把 CUDA provider 打进 exe）
MUST_NOT = [
    ("onnxruntime_providers_cuda.dll", "CUDA provider（本机是 CPU 跑，不该打包）"),
    ("onnxruntime_providers_tensorrt.dll", "TensorRT provider（同上）"),
]


def _collect_strings(code, acc=None, depth=0):
    """递归收集 code 对象里的字符串常量与名字。

    PYZ 里存的是编译后的 code 而非源码，所以两个地方都要看：
    `sys.executable` 这种属性访问落在 co_names，不落在 co_consts。
    """
    if acc is None:
        acc = []
    if depth > 12:
        return acc
    for field in ("co_consts", "co_names", "co_varnames"):
        for c in getattr(code, field, ()) or ():
            if isinstance(c, str):
                acc.append(c)
            elif hasattr(c, "co_consts"):
                _collect_strings(c, acc, depth + 1)
    return acc


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "dist", "TuneScript AI V0.5.1.exe")
    if not os.path.isfile(exe):
        print("找不到 exe：%s" % exe)
        return 2
    import hashlib
    h = hashlib.sha256()
    with open(exe, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    size = os.path.getsize(exe)
    print("exe    : %s" % exe)
    print("大小   : %.1f MB" % (size / 1048576.0))
    print("sha256 : %s\n" % h.hexdigest().upper())

    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    a = CArchiveReader(exe)
    toc = a.toc
    low = [str(n).lower().replace("\\", "/") for n in toc]
    print("CArchive 条目：%d" % len(low))

    def has(sub, pool):
        s = sub.lower().replace("\\", "/")
        return any(s in n for n in pool)

    bad = 0
    print("\n=== 文件层（CArchive）===")
    for sub, what in MUST_DATA:
        ok = has(sub, low)
        bad += (0 if ok else 1)
        print("  %s %-46s %s" % ("✓" if ok else "✗", sub, what))

    # ---- 解出 PYZ 再查模块层 ----
    print("\n=== 模块层（PYZ）===")
    pyz_name = None
    for n in toc:
        if str(n).lower().endswith("pyz.pyz") or str(n).lower() == "pyz.pyz":
            pyz_name = n
            break
    mods = []
    if pyz_name is None:
        print("  ! 找不到 PYZ 条目，跳过模块检查")
    else:
        tmpd = tempfile.mkdtemp(prefix="exeverify_")
        p = os.path.join(tmpd, "PYZ.pyz")
        try:
            with open(p, "wb") as f:
                f.write(a.extract(pyz_name))
            z = ZlibArchiveReader(p)
            mods = [str(k).lower().replace("\\", "/") for k in z.toc]
            print("  PYZ 内模块数：%d" % len(mods))
        except Exception as e:
            print("  ! 读取 PYZ 失败：%s" % str(e)[:160])
        finally:
            import shutil
            shutil.rmtree(tmpd, ignore_errors=True)
    if mods:
        for sub, what in MUST_MOD:
            ok = has(sub, mods)
            bad += (0 if ok else 1)
            print("  %s %-30s %s" % ("✓" if ok else "✗", sub, what))

    # ---- 内容层：cookie 路径修复必须真的编译进了 exe（模块在 ≠ 内容是新的）----
    print("\n=== 内容层（netease_login 里的 cookie 定位方式）===")
    WANT_CONST = (("netease_cookie.txt", "cookie 文件名"),
                  ("frozen", "冻结态判断（sys.frozen）"),
                  ("executable", "取 exe 自身目录（sys.executable）"))
    if pyz_name is None:
        bad += len(WANT_CONST)
    else:
        tmpd2 = tempfile.mkdtemp(prefix="execonst_")
        p2 = os.path.join(tmpd2, "PYZ.pyz")
        try:
            with open(p2, "wb") as f:
                f.write(a.extract(pyz_name))
            z2 = ZlibArchiveReader(p2)
            key = None
            for k in z2.toc:
                if str(k).lower().replace("\\", "/") == "netease_login":
                    key = k
                    break
            if key is None:
                print("  ! PYZ 里没找到 netease_login")
                bad += len(WANT_CONST)
            else:
                strs = _collect_strings(z2.extract(key))
                print("  常量数：%d" % len(strs))
                for want, what in WANT_CONST:
                    ok = any(want in s for s in strs)
                    bad += (0 if ok else 1)
                    print("  %s %-26s %s" % ("✓" if ok else "✗", want, what))
        except Exception as e:
            print("  ! 常量提取失败：%s" % str(e)[:160])
            bad += len(WANT_CONST)
        finally:
            import shutil
            shutil.rmtree(tmpd2, ignore_errors=True)

    print("\n=== 体积提示（不计入问题）===")
    for sub, what in MUST_NOT:
        present = has(sub, low)
        print("  %s %-42s %s" % ("·出现" if present else "·未打包", sub, what))
    print("  注：V0.5 出货 exe 里同样含这两个 provider（且只有 529 MB），")
    print("      所以它们不是体积元凶；真正的元凶是**整目录打包 lang_id_models**。")

    total = len(MUST_DATA) + (len(MUST_MOD) if mods else 0) + 3
    print("\n=== 汇总：检查 %d 项，%d 问题 ===" % (total, bad))
    if bad:
        print("需修 spec 后重新构建。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
