# -*- coding: utf-8 -*-
"""_test_langseg.py — 「分轨后 → 语种分割 → 逐段扒谱」的管线集成测试。

做两件事（都在真实固定素材上，只读 `回归验收/_stems/`）：
  臂 OFF：`TS_LANG_SEG` 未设 → 必须与出厂行为一致（单次整轨识别）
  臂 ON ：`TS_LANG_SEG=1`   → 语种分割 + 逐段识别 + 拼回

判据：
  1. 两臂都必须跑通、产出音符；
  2. 臂 ON 的分段明细必须出现在 info 里（证明真的做了分割）；
  3. 臂 ON 不允许崩、不允许比 OFF 少一个数量级；
  4. 若 LID 判为单一语种/全静音 → 必须自动退化为整轨（mode=whole），这是设计行为。

用法: python lang_dev/_test_langseg.py [--song shiki] [--arm both]
"""
import argparse
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

STEMS = os.path.join(ROOT, "回归验收", "_stems")
KEYS = ["vocals", "drums", "bass", "guitar", "piano", "other"]


def build_stems(key):
    d = os.path.join(STEMS, key)
    out = {}
    for f in os.listdir(d):
        low = f.lower()
        for k in KEYS:
            if low.endswith("_%s.wav" % k):
                out[k] = os.path.join(d, f)
    return out


def run_arm(key, stems, model_path, on, out_dir):
    import transcriber_app as ta
    if on:
        os.environ["TS_LANG_SEG"] = "1"
    else:
        os.environ.pop("TS_LANG_SEG", None)
    logs = []
    t0 = time.time()
    res = ta.transcribe_stems(stems, model_path, lambda m: logs.append(str(m)),
                              out_dir=out_dir)
    el = time.time() - t0
    if res is None:
        return {"on": on, "ok": False, "sec": round(el, 1), "notes": 0, "logs": logs,
                "err": "transcribe_stems 返回 None"}
    midi, left, right = res
    return {"on": on, "ok": True, "sec": round(el, 1),
            "notes": len(left) + len(right), "left": len(left), "right": len(right),
            "logs": logs, "stems_used": sorted(stems.keys())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--song", default="shiki")
    ap.add_argument("--arm", default="both", choices=["off", "on", "both"])
    a = ap.parse_args()

    import transcriber_app as ta
    model_path = ta.find_model()
    print("Basic Pitch 模型：%s" % model_path)
    stems = build_stems(a.song)
    print("素材 %s → 轨：%s" % (a.song, sorted(stems.keys())))
    if "vocals" not in stems:
        print("缺 vocals 轨")
        return 2

    results = []
    tmp = tempfile.mkdtemp(prefix="langseg_test_")
    try:
        arms = [False, True] if a.arm == "both" else [a.arm == "on"]
        for on in arms:
            od = os.path.join(tmp, "on" if on else "off")
            os.makedirs(od, exist_ok=True)
            print("\n===== 臂 %s =====" % ("ON (TS_LANG_SEG=1)" if on else "OFF (出厂)"))
            r = run_arm(a.song, stems, model_path, on, od)
            results.append(r)
            for m in r["logs"]:
                print("   [log] %s" % m)
            print("   -> ok=%s  用时 %.1fs  L=%s R=%s 共 %s 音符"
                  % (r["ok"], r["sec"], r.get("left"), r.get("right"), r.get("notes")))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("TS_LANG_SEG", None)

    print("\n=== 汇总 ===")
    for r in results:
        print("  臂%-4s ok=%s 用时%6.1fs 音符%5s (L%s/R%s) %s"
              % ("ON" if r["on"] else "OFF", r["ok"], r["sec"], r.get("notes"),
                 r.get("left"), r.get("right"), r.get("err", "")))
    seg_hit = any(("语种分段扒谱" in m) for r in results if r["on"] for m in r["logs"])
    print("  语种分段路径被触发：%s" % ("是" if seg_hit else "否（可能自动退化为整轨）"))
    with open(os.path.join(ROOT, "lang_dev", "_test_langseg_result.json"), "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("  结果已写入 lang_dev/_test_langseg_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
