# -*- coding: utf-8 -*-
"""_test_crop.py — 用**合成中/日混唱素材**验证「语言分段裁剪」的边界检测。

为什么造合成素材：项目现有 5 首固定素材**全是单语种**（见 _eval_lid.py），
无法用来验证"把不同语言的同一段人声音频裁剪开"这个能力本身。
合成素材的**真值边界是精确已知的**，因此能给出可判定的边界误差。

构造（全部写到 lang_dev/_out_switch/，绝不污染 回归验收/_stems/）：
  混唱1  [中文 30s][日语 30s]               真值边界 30.0
  混唱2  [中文 20s][日语 30s][中文 20s]      真值边界 20.0 / 50.0

⚠️ 两处必须做对的构造纪律（都是本项目踩过的"真值错了"坑）：
  ① 素材必须取**人声活跃区**。`勾指起誓` 的人声轨前 15 s 是 −84 dBFS 数字静音，
     直接取 [0:25] 拼出来的"中文段"其实是静音 → 真值边界根本不成立；
     故先用能量找出活跃区起点再切片（`vocal_offset()`）。
  ② 真值边界必须由**构造脚本打印并写进结果 JSON**，便于事后核对。

判据：检出的相邻两种语种的交界，与真值边界之差 <= hop + 1s 算命中。

用法: python lang_dev/_test_crop.py
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lang_id import LanguageDetector, load_audio_mono16k, _dbfs     # noqa: E402
from audio_crop import segment_by_language, write_wav               # noqa: E402

STEMS = os.path.join(ROOT, "回归验收", "_stems")
OUT = os.path.join(ROOT, "lang_dev", "_out_switch")

# (配置名, win, hop)
CONFIGS = [("10s/5s", 10.0, 5.0), ("10s/2.5s", 10.0, 2.5), ("6s/2s", 6.0, 2.0)]


def find(key):
    d = os.path.join(STEMS, key)
    hits = [f for f in os.listdir(d) if "vocals" in f.lower() and f.lower().endswith(".wav")]
    if not hits:
        raise SystemExit("缺素材：%s" % key)
    return os.path.join(d, hits[0])


def vocal_offset(y, sr, frame=0.5, db_gate=-50.0):
    """返回首个「有声帧」的时间（秒）。用于把静音前奏跳过。"""
    fs = max(1, int(frame * sr))
    nf = len(y) // fs
    for i in range(nf):
        seg = y[i * fs:(i + 1) * fs]
        if _dbfs(seg) > db_gate:
            return i * frame
    return 0.0


def build():
    os.makedirs(OUT, exist_ok=True)
    sr = 16000
    zh_raw = load_audio_mono16k(find("gouzhi"))[0]      # 勾指起誓 · 洛天依 → zh
    ja_raw = load_audio_mono16k(find("shiki"))[0]       # バカみたいに · 柿崎ユウタ → ja
    z0, j0 = vocal_offset(zh_raw, sr), vocal_offset(ja_raw, sr)
    print("  人声活跃起点：zh(gouzhi)=%.1fs  ja(shiki)=%.1fs" % (z0, j0))
    zh = zh_raw[int(z0 * sr):]
    ja = ja_raw[int(j0 * sr):]

    mk = [("mix1_zh30_ja30", [(zh, 30.0), (ja, 30.0)], [30.0]),
          ("mix2_zh20_ja30_zh20", [(zh, 20.0), (ja, 30.0), (zh, 20.0)], [20.0, 50.0])]
    out = []
    for name, parts, bounds in mk:
        chunks = []
        for y, sec in parts:
            need = int(sec * sr)
            if len(y) < need:
                raise SystemExit("素材太短：需要 %ss" % sec)
            chunks.append(y[:need])
        mix = np.concatenate(chunks).astype(np.float32)
        p = os.path.join(OUT, name + ".wav")
        write_wav(p, mix, sr)
        out.append((name, p, bounds))
        print("  造出 %-22s %.1fs  真值边界=%s  整体 %.1f dBFS"
              % (name, len(mix) / float(sr), bounds, _dbfs(mix)))
    return out


def boundaries(segs):
    """检出交界：相邻分片语种不同时的交界时间。"""
    bs = []
    for a, b in zip(segs, segs[1:]):
        if (a.get("lang") or "?") != (b.get("lang") or "?"):
            bs.append(round((a["t1"] + b["t0"]) / 2.0, 2))
    return bs


def main():
    print("=== 构造合成混唱素材 ===")
    mixes = build()
    results = []
    for cfg_name, win, hop in CONFIGS:
        # 钉死 Silero：本脚本测的是**分段/边界逻辑**，与 LID 引擎无关，
        # 用 Qwen 会让 6 个配置各跑一遍慢 100 倍的自回归解码。
        det = LanguageDetector(candidates="zh,ja,en,yue", backend="silero_onnx")
        if not det.available():
            print("LID 不可用：%s" % det.reason)
            return 2
        for name, path, truth in mixes:
            segs, meta = segment_by_language(path, det=det, win=win, hop=hop,
                                             min_seg=max(6.0, win * 0.8))
            det_b = boundaries(segs)
            # 命中判据：每个真值边界都要有一个检出边界落在 ±hop 内
            hits = sum(1 for t in truth if any(abs(d - t) <= hop + 1e-6 for d in det_b))
            results.append({"config": cfg_name, "mix": name, "truth": truth,
                            "detected": det_b, "hit": hits, "n_truth": len(truth),
                            "segs": segs})
            print("\n[%s] %s" % (cfg_name, name))
            for s in segs:
                print("   %8.2f - %8.2f  %-7s p=%.2f  votes=%s"
                      % (s["t0"], s["t1"], s["lang"] or "unknown", s["prob"],
                         json.dumps(s["votes"], ensure_ascii=False)[:80]))
            print("   真值边界 %s → 检出 %s   命中 %d/%d"
                  % (truth, det_b, hits, len(truth)))

    print("\n=== 汇总 ===")
    print("  %-10s %-22s %-16s %-16s %s" % ("配置", "素材", "真值边界", "检出边界", "命中"))
    for r in results:
        print("  %-10s %-22s %-16s %-16s %d/%d"
              % (r["config"], r["mix"], r["truth"], r["detected"], r["hit"], r["n_truth"]))

    with open(os.path.join(ROOT, "lang_dev", "_test_crop_result.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n结果已写入 lang_dev/_test_crop_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
