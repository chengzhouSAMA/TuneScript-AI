# -*- coding: utf-8 -*-
"""_repro_context.py — Qwen「歌词 context + 强制对齐」的**可复现性检验**。

背景（必须做这一步的原因）：
本项目有过多起「同一输入两次结果不同」的记录 ——
Demucs 分离不可复现（人声轨 SHA 都不同）、打包 exe 与开发态不逐字节相同。
而 `_demo_lyrics_align.py` 两次运行里 C 臂分数从 **0.725 掉到 0.489**，
在把"歌词 context 有效"写进结论之前，**必须先确认这是 context 造成的还是随机性**。

做法：**完全相同的 job** 跑 N 次，逐窗比较文本是否逐字相同、相似度是否稳定。
  · 全部相同      → 可复现，分数差异只能归因于 context
  · 部分/全部不同 → **不可复现**，任何单次 A/B 都不可信（必须多跑取中位）

用法: lang_id_venv314\\Scripts\\python.exe lang_dev/_repro_context.py [--reps 3]
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PY = os.path.join(ROOT, "lang_id_venv314", "Scripts", "python.exe")
RUNNER = os.path.join(ROOT, "lang_id_qwen_runner.py")
MODEL = os.path.join(ROOT, "lang_id_models", "Qwen3-ASR-0.6B")
ALIGNER = os.path.join(ROOT, "lang_id_models", "Qwen3-ForcedAligner-0.6B")
WAV = os.path.join(ROOT, "lang_dev", "_out_lyrics_align", "seg_0_30.wav")

SPANS = [[0.0, 10.0], [10.0, 20.0], [20.0, 30.0]]
# 与 demo 第二次运行一致的 context（严格窗内）
CTXS = ["さよなら",
        "少しだけ違った表現\n少しかしら",   # 占位，运行时会被真实歌词覆盖
        "増える感情表現\nレパートリー"]
LYRIC = [None]  # 运行时填充


def run_once(use_context=True, reps_tag=""):
    job = {"model": MODEL, "wav": WAV, "threads": 10, "spans": SPANS,
           "language": "Japanese", "aligner": ALIGNER, "return_time_stamps": True}
    if use_context:
        job["context"] = LYRIC[0]
    p = subprocess.run([PY, RUNNER], input=json.dumps(job), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=7200)
    if not (p.stdout or "").strip():
        print("runner 无输出：%s" % (p.stderr or "")[-400:])
        return None
    o = json.loads(p.stdout)
    if not o.get("ok"):
        print("runner 报错：%s" % str(o.get("error"))[:200])
        return None
    return o["results"][0]["windows"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--no-context", action="store_true", help="对照组：不喂 context")
    a = ap.parse_args()

    if not os.path.isfile(WAV):
        print("缺 %s（先跑 lang_dev/_demo_lyrics_align.py）" % WAV)
        return 2

    # 用真实歌词填 context
    import lyrics_fetch as LF
    import lyrics_match as LM
    meta = LF.parse_song_meta(os.path.join(ROOT, "回归验收", "_stems", "shiki",
                                           "柿崎ユウタ - バカみたいに（像个笨蛋一样） - KomisI-w_vocals.wav"))
    cands = LF.dedup_candidates(LF.search_and_fetch(meta["query"], limit=6))
    best, _ = LM.pick_best_candidate("", cands) if not cands else (None, None)
    # 直接用第一首同名的（曲目已在上一步确认过）
    best = next(c for c in cands if c["title"].startswith("バカみたいに"))
    ctxs = []
    for (s, e) in SPANS:
        lines = [r["text"] for r in (best.get("ts_lines") or []) if s <= r["t"] <= e]
        ctxs.append("\n".join(lines) if lines else "")
    LYRIC[0] = ctxs
    tag = "无 context（对照）" if a.no_context else "有 context"
    print("配置：%s   重复 %d 次\n每窗 context：%s\n" % (tag, a.reps, ctxs))

    runs = []
    for i in range(a.reps):
        w = run_once(use_context=not a.no_context)
        if w is None:
            return 2
        runs.append(w)
        print("  run%d：%s" % (i, [ (x["text"] or "")[:26] for x in w ]))

    print("\n=== 逐窗一致性 ===")
    all_same = True
    for k, (s, e) in enumerate(SPANS):
        texts = [r[k]["text"] for r in runs]
        same = all(t == texts[0] for t in texts)
        all_same = all_same and same
        print("  窗 %4.1f-%4.1f  %s" % (s, e, "逐字相同 ✓" if same else "**不同 ✗**"))
        if not same:
            for i, t in enumerate(texts):
                print("      run%d: %s" % (i, t[:70]))
    # 时间戳一致性
    ts_same = all(
        [ [round(x[1],3) for x in r[k]["time_stamps"]] for r in runs[1:] ] ==
        [ [round(x[1],3) for x in runs[0][k]["time_stamps"]] for r in runs[1:] ]
        for k in range(len(SPANS))) if all(r[k].get("time_stamps") for r in runs for k in range(len(SPANS))) else None
    print("\n=== 结论 ===")
    if all_same:
        print("  ✅ 文本**逐字可复现** → 两次 demo 的分数差异**不能**用随机性解释，")
        print("     需要回到 context 内容差异上去找原因（这是好事：说明 A/B 可信）。")
    else:
        print("  ❌ 文本**不可复现** → 单次 A/B 的结果不可信！")
        print("     任何「context 有效」的结论都必须**多跑取中位/多数**后才能下。")
    if ts_same is not None:
        print("  时间戳逐窗一致：%s" % ("是" if ts_same else "**否**"))
    with open(os.path.join(ROOT, "lang_dev", "_repro_context.json"), "w",
              encoding="utf-8") as f:
        json.dump({"config": tag, "ctxs": ctxs, "runs": runs,
                   "text_reproducible": all_same, "ts_reproducible": ts_same},
                  f, ensure_ascii=False, indent=2)
    print("  明细已写入 lang_dev/_repro_context.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
