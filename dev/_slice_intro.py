# -*- coding: utf-8 -*-
"""_slice_intro.py — 把 shiki 人声轨的前奏段切出来，供细窗 ASR 侦察。

用法: python lang_dev/_slice_intro.py [--t0 0] [--t1 30]
输出: lang_dev/_out_intro/shiki_intro_<t0>_<t1>.wav
"""
import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from audio_crop import read_wav, write_wav          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--t1", type=float, default=30.0)
    a = ap.parse_args()
    d = os.path.join(ROOT, "回归验收", "_stems", "shiki")
    src = os.path.join(d, [f for f in os.listdir(d)
                           if "vocals" in f.lower() and f.lower().endswith(".wav")][0])
    y, sr = read_wav(src)
    if y.ndim == 2:
        y = y.mean(axis=1)
    seg = y[int(a.t0 * sr):int(a.t1 * sr)]
    out = os.path.join(ROOT, "lang_dev", "_out_intro")
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, "shiki_intro_%g_%g.wav" % (a.t0, a.t1))
    write_wav(p, seg, sr)
    print("源 %s" % src)
    print("切片 %.1f~%.1fs → %s（%.2fs @ %d Hz）" % (a.t0, a.t1, p, len(seg) / float(sr), sr))
    return 0


if __name__ == "__main__":
    sys.exit(main())
