# -*- coding: utf-8 -*-
"""_harmonic_check.py — 判断"识别不出来"的那段到底是**真人声**还是**分离残留**。

动机（项目已有先例，备忘 附-14.7）：不带 bleed 闸门时，会把 fanwut 尾奏的
**Demucs 分离残留**（−25.6 dB、谐波性 0.26，而真唱段 0.73）算成"人声活跃"。
所以对 `shiki` 前奏 0~10 s 必须先问：**这点声音是真人在唱，还是分轨残留？**

判据：`librosa.effects.hpss` 做谐波/打击分离，取
    谐波能量占比 = E_harmonic / (E_harmonic + E_percussive)
真唱段（有明显基频与共振峰）该值高；宽带残留/噪声该值低。

用法: python lang_dev/_harmonic_check.py
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from audio_crop import read_wav          # noqa: E402


def main():
    import librosa
    d = os.path.join(ROOT, "回归验收", "_stems", "shiki")
    src = os.path.join(d, [f for f in os.listdir(d)
                           if "vocals" in f.lower() and f.lower().endswith(".wav")][0])
    # 同时取一份"原始混音"做对照（如果存在）
    y, sr = read_wav(src)
    if y.ndim == 2:
        y = y.mean(axis=1)
    if sr != 22050:
        y = librosa.resample(y, orig_sr=sr, target_sr=22050)
        sr = 22050
    print("素材 %s\n长度 %.1fs @ %d Hz\n" % (os.path.basename(src), len(y) / float(sr), sr))
    print("  %-12s %8s %10s %10s %8s" % ("区间", "dBFS", "谐波占比", "谱平坦度", "过零率"))
    rows = []
    for a in range(0, 60, 5):
        seg = y[int(a * sr):int((a + 5) * sr)]
        if len(seg) < sr:
            break
        rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
        db = -120.0 if rms <= 1e-12 else 20 * np.log10(rms)
        h, p = librosa.effects.hpss(seg)
        eh, ep = float(np.sum(h ** 2)), float(np.sum(p ** 2))
        harm = eh / (eh + ep) if (eh + ep) > 0 else 0.0
        flat = float(np.mean(librosa.feature.spectral_flatness(y=seg)))
        zcr = float(np.mean(librosa.feature.zero_crossing_rate(seg)))
        rows.append((a, db, harm, flat, zcr))
        tag = ""
        if a < 10:
            tag = "  <-- 前奏（ASR 识别不出来）"
        elif a == 10:
            tag = "  <-- 从这里开始 ASR 判为日语"
        print("  %3d-%3ds     %8.1f %10.3f %10.4f %8.3f%s" % (a, a + 5, db, harm, flat, zcr, tag))
    early = [r for r in rows if r[0] < 10]
    later = [r for r in rows if r[0] >= 10]
    if early and later:
        he = np.mean([r[2] for r in early])
        hl = np.mean([r[2] for r in later])
        fe = np.mean([r[3] for r in early])
        fl = np.mean([r[3] for r in later])
        print("\n前奏(0-10s)  平均谐波占比 %.3f  平均谱平坦度 %.4f" % (he, fe))
        print("后续(10s+)   平均谐波占比 %.3f  平均谱平坦度 %.4f" % (hl, fl))
        print("\n判读：")
        print("  谐波占比越高 = 越像'有基频的歌声'；谱平坦度越低 = 越像乐音而非噪声。")
        if he < hl * 0.8 or fe > fl * 1.3:
            print("  ⇒ 前奏段**明显更不像歌声**（谐波低/平坦度高）→ 大概率是分离残留或器乐，")
            print("     对它做'谐音音节识别'会是在给噪声编音节。**(先确认再决定)**")
        else:
            print("  ⇒ 前奏段的谐波特征与后续唱段接近 → 可能是**真的在唱**（或气声/弱声），")
            print("     值得按用户的思路细切 + 谐音音节重试。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
