# -*- coding: utf-8 -*-
"""_test_en_align.py — 英语人声：均分时间 vs **歌词+强制对齐** 的真实时间。

上一轮 `_test_en_phonetics.py` 里英语音节→音符命中率 69.0%（tol 0.12），
比日语摩拉的 83.9% 低。但那时音节时间是**窗内均分**的，
限制来自时间来源而不是音素分析本身。这里用已有的能力补上：

    联网取歌词 → 作 context 喂 Qwen → 开强制对齐 → 逐字符真实时间戳
    → 按字符时间给音节定时 → 重新对表

用法: python lang_dev/_test_en_align.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    import asr_refine as AR
    import en_phoneme as EP
    import lyrics_fetch as LF
    import lyrics_match as LM

    voc = os.path.join(ROOT, "lang_dev", "_out_en", "Hello - Adele_vocals.wav")
    if not os.path.isfile(voc):
        print("缺 %s，先跑 lang_dev/_test_en_phonetics.py" % voc)
        return 2
    dur = 30.0
    spans = [[0.0, 10.0], [10.0, 20.0], [20.0, 30.0]]

    print("=" * 74)
    print("① 取官方歌词（网易云）")
    print("=" * 74)
    cands = LF.dedup_candidates(LF.search_and_fetch("Hello Adele", limit=6))
    if not cands:
        print("  取词失败")
        return 2
    best = None
    for c in cands:
        if c["title"].strip().lower().startswith("hello") and "adele" in " ".join(c["artists"]).lower():
            best = c
            break
    best = best or cands[0]
    print("  选中 %s / %s，歌词行 %d，起点 %s s"
          % (best["title"], "/".join(best["artists"]), best.get("n_ts_lines"),
             best.get("offset_hint")))
    # 每窗的 context 取该窗内、以及窗起点之前最近的一行（锚点行规则）
    ctxs = []
    for (s, e) in spans:
        lines = [r["text"] for r in (best.get("ts_lines") or []) if s - 3.0 <= r["t"] <= e]
        ctxs.append("\n".join(lines))
    for (s, e), c in zip(spans, ctxs):
        print("   %5.1f-%5.1f context 首行: %s" % (s, e, (c.split("\n")[0] if c else "(空)")))

    print("\n" + "=" * 74)
    print("② 对照：均分时间 vs 歌词context+强制对齐")
    print("=" * 74)
    py = os.path.join(ROOT, "lang_id_venv314", "Scripts", "python.exe")
    runner = os.path.join(ROOT, "lang_id_qwen_runner.py")
    model = os.path.join(ROOT, "lang_id_models", "Qwen3-ASR-0.6B")
    aligner = os.path.join(ROOT, "lang_id_models", "Qwen3-ForcedAligner-0.6B")

    def run(job):
        p = subprocess.run([py, runner], input=json.dumps(job), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=3600)
        o = json.loads(p.stdout) if (p.stdout or "").strip() else {}
        return (o.get("results") or [{}])[0].get("windows") or [] if o.get("ok") else []

    base_rows = run({"model": model, "wav": voc, "threads": 10, "spans": spans,
                     "language": "English"})
    align_rows = run({"model": model, "wav": voc, "threads": 10, "spans": spans,
                      "language": "English", "context": ctxs,
                      "aligner": aligner, "return_time_stamps": True})
    if not base_rows or not align_rows:
        print("  runner 失败")
        return 2

    for tag, rows in (("A 均分（无对齐）", base_rows), ("B 歌词context + 强制对齐", align_rows)):
        for w in rows:
            print("  [%s] %5.1f-%5.1f  ts=%-3d  %s"
                  % (tag[:1], w["t0"], w["t1"], len(w.get("time_stamps") or []),
                     (w["text"] or "")[:64]))

    import transcriber_app as ta
    from basic_pitch.inference import Model as BpModel
    notes = ta.transcribe_notes(voc, BpModel(ta.find_model()), lambda m: None,
                                label="人声", min_len=60)
    print("\n  Basic Pitch 音符 %d 个" % len(notes))

    def measure(rows, use_align):
        grid = []
        for w, (s, e) in zip(rows, spans):
            t = (w.get("text") or "").strip()
            if not t:
                continue
            ts = w.get("time_stamps") or []
            if use_align and ts:
                ct = [[x[0], s + x[1], s + x[2]] for x in ts]
                syls = EP.from_char_times(t, ct, t0=0.0)
                if syls:
                    grid.extend(syls)
                    continue
            grid.extend(EP.distribute(EP.syllables_of(t), s, e))
        for k, u in enumerate(grid):
            u["index"] = k
        return grid, AR.notes_vs_units(notes, grid, tol=0.12)

    for tag, rows, ua in (("A 均分", base_rows, False), ("B 对齐", align_rows, True)):
        g, r = measure(rows, ua)
        print("  %-4s 音节 %3d  命中 %3d/%3d = %5.1f%%   （tol 0.12s）"
              % (tag, len(g), r["n_hit"], r["n_units"], 100 * r["hit_rate"]))
        if tag.startswith("B") and g:
            print("      前 10 个音节的真实时间：")
            for u in g[:10]:
                print("        %6.2f-%6.2f  %-9s %s"
                      % (u["t0"], u["t1"], u.get("ipa", ""), u.get("word", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
