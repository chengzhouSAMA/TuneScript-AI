# -*- coding: utf-8 -*-
"""_probe_qwen.py — 量 Qwen3-ASR-0.6B 在**纯 CPU** 上的单窗耗时与语种输出。

必须在 lang_id_venv314 里跑：
    lang_id_venv314\\Scripts\\python.exe lang_dev/_probe_qwen.py [--sec 10] [--reps 3]

目的（先量再设计）：决定 LID 用多大的窗、一首歌大概要多久、
以及"整曲一次"与"逐窗多次"哪个更划算。
"""
import argparse
import os
import sys
import time

import numpy as np
import soundfile as sf
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "lang_id_models", "Qwen3-ASR-0.6B")
STEMS = os.path.join(ROOT, "回归验收", "_stems")


def find_vocals(key):
    d = os.path.join(STEMS, key)
    for f in os.listdir(d):
        if "vocals" in f.lower() and f.lower().endswith(".wav"):
            return os.path.join(d, f)
    raise SystemExit("no vocals for " + key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sec", type=float, default=10.0, help="每次送入的音频秒数")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--threads", type=int, default=0, help=">0 则 torch.set_num_threads")
    a = ap.parse_args()

    if a.threads > 0:
        torch.set_num_threads(a.threads)
    print("torch %s  threads=%d  model=%s" % (torch.__version__, torch.get_num_threads(), MODEL))
    t0 = time.time()
    from qwen_asr import Qwen3ASRModel
    model = Qwen3ASRModel.from_pretrained(
        MODEL, dtype=torch.float32, device_map="cpu",
        max_inference_batch_size=1, max_new_tokens=256,
    )
    print("模型加载 %.1f s" % (time.time() - t0))

    songs = [("gouzhi", "zh"), ("shiki", "ja"), ("jiabin", "yue"), ("monitoring", "ja")]
    for key, truth in songs:
        p = find_vocals(key)
        y, sr = sf.read(p, dtype="float32", always_2d=True)
        y = y.mean(axis=1)
        if sr != 16000:
            import librosa
            y = librosa.resample(y, orig_sr=sr, target_sr=16000)
            sr = 16000
        # 取第一个有声区（跳过静音前奏）
        fs = int(0.5 * sr)
        off = 0
        for i in range(len(y) // fs):
            seg = y[i * fs:(i + 1) * fs]
            if 20 * np.log10(max(1e-9, float(np.sqrt(np.mean(seg ** 2))))) > -50:
                off = i * fs
                break
        n = int(a.sec * sr)
        chunk = y[off:off + n]
        if len(chunk) < n:
            chunk = np.pad(chunk, (0, n - len(chunk)))
        for r in range(a.reps):
            t = time.time()
            res = model.transcribe(audio=(chunk, sr), language=None)
            dt = time.time() - t
            r0 = res[0]
            print("  %-11s(真值 %-4s) rep%d  %5.2fs  → lang=%-10s text=%r"
                  % (key, truth, r, dt, getattr(r0, "language", "?"),
                     (getattr(r0, "text", "") or "")[:40]))
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
