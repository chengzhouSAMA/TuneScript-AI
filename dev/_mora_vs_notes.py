# -*- coding: utf-8 -*-
"""_mora_vs_notes.py — 量化「日语的**字**有没有变成**音符**」（项目里"用户反馈③"）。

做法：对同一段人声
  · 用 Basic Pitch 取音符起音（= 扒谱侧实际看到的东西）
  · 用 `_demo_ja_syllable.py` 产出的**罗马音摩拉轴**当作"字"的时间期望
  · 统计每个摩拉起点附近（±tol）有没有音符起音

这是用户要求的"日语切成罗马音做**精确**识别"的验收指标：
罗马音摩拉数 ≠ 音符数时，就能指出**具体是哪几个字没被弹出来**。

用法: python lang_dev/_mora_vs_notes.py [--tol 0.12]
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WAV = os.path.join(ROOT, "lang_dev", "_out_intro", "shiki_intro_0_30.wav")
DEMO = os.path.join(ROOT, "lang_dev", "_demo_ja_syllable.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=0.12)
    a = ap.parse_args()

    if not os.path.isfile(DEMO):
        print("缺 %s，先跑 lang_dev/_demo_ja_syllable.py" % DEMO)
        return 2
    import asr_refine as AR
    import transcriber_app as ta

    d = json.load(open(DEMO, encoding="utf-8"))
    rows = d["rows"]
    morae = AR.mora_grid(rows)
    print("摩拉轴：%d 个（来自 %d 个识别窗）" % (len(morae), len(rows)))

    from basic_pitch.inference import Model as BpModel
    model = BpModel(ta.find_model())
    logs = []
    notes = ta.transcribe_notes(WAV, model, lambda m: logs.append(m),
                                label="人声旋律", min_len=60)
    print("Basic Pitch 音符：%d 个（0~30s）" % len(notes))
    if notes:
        print("  音符起点范围 %.2f ~ %.2f s；前 12 个起点：%s"
              % (notes[0][0], max(n[0] for n in notes),
                 " ".join("%.2f" % n[0] for n in sorted(notes, key=lambda x: x[0])[:12])))

    for tol in (a.tol, 0.06, 0.20):
        r = AR.notes_vs_morae(notes, morae, tol=tol)
        print("  tol=%.2fs → 命中 %d/%d = %.1f%%"
              % (tol, r["n_hit"], r["n_morae"], 100 * r["hit_rate"]))
    r = AR.notes_vs_morae(notes, morae, tol=a.tol)
    miss = r["missing"]
    print("\n未对上音符的摩拉（前 20 个，即'没被弹出来的字'）：")
    for m in miss[:20]:
        print("   %6.2f-%6.2f  %-6s (%s)   来源窗 %.1f-%.1f  %s"
              % (m["t0"], m["t1"], m["romaji"], m["mora"], m["src_t0"], m["src_t1"],
                 (m.get("text") or "")[:26]))
    per_src = {}
    for m in morae:
        k = (m["src_t0"], m["src_t1"])
        s = per_src.setdefault(k, {"n": 0, "hit": 0, "text": m.get("text", "")})
        s["n"] += 1
    for m in miss:
        k = (m["src_t0"], m["src_t1"])
        if k in per_src:
            per_src[k]["hit"] += 1
    print("\n按窗统计：")
    for (t0, t1), s in sorted(per_src.items()):
        print("   %5.2f-%5.2f  摩拉 %2d  未命中 %2d  命中率 %5.1f%%  %s"
              % (t0, t1, s["n"], s["hit"], 100 * (1 - s["hit"] / float(s["n"])),
                 s["text"][:34]))
    with open(os.path.join(ROOT, "lang_dev", "_mora_vs_notes.json"), "w",
              encoding="utf-8") as f:
        json.dump({"n_morae": len(morae), "n_notes": len(notes),
                   "tol": a.tol, "hit": r["n_hit"], "hit_rate": r["hit_rate"],
                   "per_window": {"%s-%s" % k: v for k, v in per_src.items()}},
                  f, ensure_ascii=False, indent=2)
    print("\n结果已写入 lang_dev/_mora_vs_notes.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
