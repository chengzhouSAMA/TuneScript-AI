# -*- coding: utf-8 -*-
"""_demo_ja_syllable.py — 端到端演示：识别不出来的段落 → 再切割重试 → 罗马音摩拉。

以用户的例子为准：`shiki`（バカみたいに）前奏 0~30 s，官方歌词开头是
    さよなら / 少しだけ違っただけの愛情表現 / メランコリー / 普段通り 独りきり段取り
而自动模式下 Qwen3-ASR 会把 0~10 s 判成 **Chinese** 并输出中文乱码。

流程（= asr_refine 的三段式）：
  ① 基线识别（win=10, 自动语种）
  ② 对质量分 < 阈值的窗 → 对半切、**强制 Japanese** 重试；仍差再对半
  ③ 把每个（最好版本的）窗的日语文本转成 **罗马音摩拉时间轴**
     并对照官方歌词，看谐音音节能不能对上

在 lang_id_venv314 里跑（Qwen 依赖隔离）：
    lang_id_venv314\\Scripts\\python.exe lang_dev/_demo_ja_syllable.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PY = os.path.join(ROOT, "lang_id_venv314", "Scripts", "python.exe")
RUNNER = os.path.join(ROOT, "lang_id_qwen_runner.py")
WAV = os.path.join(ROOT, "lang_dev", "_out_intro", "shiki_intro_0_30.wav")

CALLS = {"n": 0, "spans": 0}


def call_asr(spans, language=None):
    """一次把所有区间交给 runner（同一进程内跑完，模型只加载一次）。"""
    CALLS["n"] += 1
    CALLS["spans"] += len(spans)
    job = {"model": os.path.join(ROOT, "lang_id_models", "Qwen3-ASR-0.6B"),
           "wav": WAV, "threads": 10, "max_windows": 200,
           "spans": [[float(a), float(b)] for a, b in spans]}
    if language:
        job["language"] = language
    p = subprocess.run([PY, RUNNER], input=json.dumps(job), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=7200)
    out = json.loads(p.stdout) if (p.stdout or "").strip() else {"ok": False}
    if not out.get("ok"):
        print("  !! runner 失败：%s" % str(out.get("error"))[:160])
        return []
    return (out.get("results") or [{}])[0].get("windows") or []


def main():
    if not os.path.isfile(WAV):
        print("缺素材，先跑: python lang_dev/_slice_intro.py --t0 0 --t1 30")
        return 2
    import asr_refine as AR
    import ja_romaji as JR

    base = [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]
    print("=" * 78)
    print("① 基线识别（win=10s，自动语种）")
    print("=" * 78)
    first = call_asr(base, language=None)
    for r in first:
        q = AR.score(r.get("text", ""), "ja")
        print("  %5.1f-%5.1f %-9s score=%.2f %-22s %s"
              % (r["t0"], r["t1"], r["language"], q["score"],
                 ",".join(q["issues"]), r["text"][:52]))

    print("\n" + "=" * 78)
    print("② 低质量段对半切 + **强制日语** 重试（最多两刀 = 最小 2.5 s）")
    print("=" * 78)
    rows, stats = AR.retry_windows(base, call_asr, expect_lang="ja",
                                   min_len=2.5, threshold=0.75, max_depth=2,
                                   log=lambda m: print("   [retry] %s" % m))
    for r in rows:
        print("  %5.2f-%5.2f d=%d %-9s score=%.2f %-16s %s"
              % (r["t0"], r["t1"], r["depth"], r["language"], r["score"],
                 ",".join(r["issues"])[:16], (r["text"] or "(空)")[:50]))
    print("  统计：%s" % stats)

    print("\n" + "=" * 78)
    print("③ 谐音音节：日语 → 假名 → 罗马音摩拉（一摩拉 ≈ 一个音符）")
    print("=" * 78)
    total_morae = 0
    for r in rows:
        t = (r.get("text") or "").strip()
        if not t:
            continue
        tl = AR.romaji_timeline(t, t0=r["t0"], t1=r["t1"])
        total_morae += tl["n_morae"]
        print("\n  [%5.2f-%5.2f] %s" % (r["t0"], r["t1"], t))
        print("      假名  : %s" % tl["kana"])
        print("      罗马音: %s  (%d 摩拉, 假名覆盖 %.2f, 定时=%s)"
              % (tl["romaji"], tl["n_morae"], tl["kana_coverage"], tl["timing"]))
        if tl["morae"]:
            print("      摩拉轴: %s"
                  % " ".join("%s@%.2f" % (m["romaji"], m["t0"]) for m in tl["morae"][:14]))
    print("\n  合计摩拉 %d 个" % total_morae)

    print("\n" + "=" * 78)
    print("④ 对照官方歌词（TuneCore）开头")
    print("=" * 78)
    official = "さよなら 少しだけ違っただけの愛情表現 メランコリー 普段通り独りきり段取り"
    o = JR.analyze(official)
    print("  官方：%s" % official)
    print("  罗马音：%s" % o["romaji"])
    print("  摩拉数：%d" % o["n_morae"])
    got = AR.merge_romaji(rows)
    print("  识别：%s" % (got[:200] or "(空)"))
    with open(os.path.join(ROOT, "lang_dev", "_demo_ja_syllable.json"), "w",
              encoding="utf-8") as f:
        json.dump({"baseline": first, "rows": rows, "stats": stats,
                   "official_romaji": o["romaji"], "official_morae": o["n_morae"],
                   "asr_calls": CALLS}, f, ensure_ascii=False, indent=2)
    print("\n  结果已写入 lang_dev/_demo_ja_syllable.json（ASR 调用 %d 次 / %d 个区间）"
          % (CALLS["n"], CALLS["spans"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
