# -*- coding: utf-8 -*-
"""_bleed_check.py — 决定性检验：前奏 0~10 s 是**分离残留**还是**真人声**？

思路：Demucs 的分离残留本质上是**其它分轨漏进 vocals 的那部分**，所以
`vocals[0:10s]` 与 `drums/other/guitar/piano[0:10s]` 会有**异常高的相关**；
而真唱段与伴奏是独立的 → 相关应显著更低。

同时对照：真唱段（如 20~30 s）的同一组相关性。

另外附带：在**原始混音**上做同一对比（如果有原曲文件），以及对其 0~10 s 直接跑 ASR。

用法: python lang_dev/_bleed_check.py
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from audio_crop import read_wav          # noqa: E402

STEM_DIR = os.path.join(ROOT, "回归验收", "_stems", "shiki")
SR = 22050


def load(path, sr=SR):
    import librosa
    y, s = read_wav(path)
    if y.ndim == 2:
        y = y.mean(axis=1)
    if s != sr:
        y = librosa.resample(y, orig_sr=s, target_sr=sr)
    return np.asarray(y, dtype=np.float64)


def main():
    files = {}
    for f in os.listdir(STEM_DIR):
        low = f.lower()
        for k in ("vocals", "drums", "bass", "guitar", "piano", "other"):
            if low.endswith("_%s.wav" % k):
                files[k] = os.path.join(STEM_DIR, f)
    if "vocals" not in files:
        print("缺 vocals")
        return 2
    print("轨：%s" % sorted(files.keys()))
    voc = load(files["vocals"])
    others = {k: load(p) for k, p in files.items() if k != "vocals"}

    def corr(a, b):
        n = min(len(a), len(b))
        a, b = a[:n], b[:n]
        if a.std() < 1e-9 or b.std() < 1e-9:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])

    print("\n  %-14s %s" % ("区间", "vocals 与各轨的相关系数"))
    for (a, b, tag) in ((0, 10, "前奏（ASR 失败）"), (10, 30, "首个唱段"), (20, 30, "唱段"), (60, 70, "中段")):
        i0, i1 = int(a * SR), int(b * SR)
        seg = voc[i0:i1]
        row = []
        for k in ("drums", "bass", "guitar", "other", "piano"):
            if k in others:
                row.append("%s=%+.3f" % (k, corr(seg, others[k][i0:i1])))
        mx = max(abs(float(x.split("=")[1])) for x in row) if row else 0
        print("  %2d-%2ds %-10s %s   |max|=%.3f" % (a, b, tag, "  ".join(row), mx))

    print("\n判读：")
    print("  若前奏的 |max| 明显高于唱段 → 前奏的'人声'其实是别的轨漏进来的**分离残留**。")
    print("  若两者接近 → 前奏更像真的在唱（弱/气声），值得细切重试。")

    # 顺带：如果桌面上有原曲，也对原曲 0~10s 做个能量对照
    cands = []
    for d in (os.path.join(os.path.expanduser("~"), "Desktop"),
              os.path.join(ROOT, "转谱验证")):
        if os.path.isdir(d):
            for f in os.listdir(d):
                if "バカ" in f or "柿崎" in f:
                    cands.append(os.path.join(d, f))
    if cands:
        print("\n找到可能的原曲/相关文件：")
        for c in cands[:6]:
            print("   %s" % c)
    else:
        print("\n（未在桌面/转谱验证 找到原曲文件，无法做混音侧对照）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
