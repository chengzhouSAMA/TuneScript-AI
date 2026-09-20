# -*- coding: utf-8 -*-
"""_demo_lyrics_align.py — 完整链路 demo（主人指定的外挂方案）。

    联网取歌词 → Qwen 首轮识别 → **比对**（确认曲目 / 判掉无歌词段）
    → 歌词作 **context** 重新识别 + **强制对齐** → 真实逐字时间戳 → 罗马音摩拉

在 lang_id_venv314 里跑（Qwen 依赖隔离）：
    lang_id_venv314\\Scripts\\python.exe lang_dev/_demo_lyrics_align.py [--t0 0 --t1 30]

对比三种配置（同一批时间窗，单变量）：
    A 自动语种，无 context          ← 基线
    B 强制日语，无 context          ← 上一轮的结论（乱码变日语摩拉）
    C 强制日语 + **歌词 context** + 强制对齐   ← 本轮新增
再对每个窗算"与官方歌词的相似度"，看 C 是否真的更好。
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
WAV_FULL = None


def call_runner(job):
    p = subprocess.run([PY, RUNNER], input=json.dumps(job), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=10800)
    if not (p.stdout or "").strip():
        print("  !! runner 无输出 rc=%s\n%s" % (p.returncode, (p.stderr or "")[-800:]))
        return None
    out = json.loads(p.stdout)
    if not out.get("ok"):
        print("  !! runner 报错：%s" % str(out.get("error"))[:300])
        return None
    return out


def main():
    global WAV_FULL
    ap = argparse.ArgumentParser()
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--t1", type=float, default=30.0)
    ap.add_argument("--song-file",
                    default=os.path.join(ROOT, "回归验收", "_stems", "shiki",
                                         "柿崎ユウタ - バカみたいに（像个笨蛋一样） - KomisI-w_vocals.wav"))
    ap.add_argument("--win", type=float, default=10.0)
    ap.add_argument("--ctx-pad", type=float, default=1.0,
                    help="构建 context 时允许越出窗边界的秒数（0=严格窗内）。"
                         "**这个值会显著改变结果**，见 README §25.2")
    a = ap.parse_args()

    import audio_crop as AC
    import lyrics_fetch as LF
    import lyrics_match as LM
    import ja_romaji as JR

    # ---------- 0) 切片 ----------
    d = os.path.dirname(a.song_file)
    src = a.song_file if os.path.isfile(a.song_file) else os.path.join(
        d, [f for f in os.listdir(d) if "vocals" in f.lower()][0])
    y, sr = AC.read_wav(src)
    if y.ndim == 2:
        y = y.mean(axis=1)
    outdir = os.path.join(ROOT, "lang_dev", "_out_lyrics_align")
    os.makedirs(outdir, exist_ok=True)
    WAV_FULL = os.path.join(outdir, "seg_%g_%g.wav" % (a.t0, a.t1))
    AC.write_wav(WAV_FULL, y[int(a.t0 * sr):int(a.t1 * sr)], sr)
    dur = (a.t1 - a.t0)
    spans = []
    t = 0.0
    while t + a.win <= dur + 1e-6:
        spans.append([round(t, 3), round(t + a.win, 3)])
        t += a.win
    if not spans or spans[-1][1] < dur - 1e-6:
        spans.append([round(max(0.0, dur - a.win), 3), round(dur, 3)])
    print("素材 %s\n分析区间 %.1f~%.1fs（%d 个 %.0fs 窗）→ %s\n"
          % (os.path.basename(src), a.t0, a.t1, len(spans), a.win, WAV_FULL))

    # ---------- 1) 联网取歌词 ----------
    meta = LF.parse_song_meta(src)
    print("=" * 78)
    print("① 联网取歌词（网易云 / QQ音乐，无 cookie）")
    print("=" * 78)
    print("  文件名解析：歌手=%s  歌名=%s  搜索词=%s" % (meta["artist"], meta["title_clean"], meta["query"]))
    cands = LF.dedup_candidates(LF.search_and_fetch(meta["query"], limit=6))
    if not cands:
        print("  取词失败（网络/接口）")
        return 2
    for c in cands[:5]:
        print("   %-8s %-26s 行数=%-4s 歌词起点=%s" % (c["provider"], c["title"][:24],
                                                     c.get("n_ts_lines"), c.get("offset_hint")))

    # ---------- 2) 首轮识别 ----------
    print("\n" + "=" * 78)
    print("② Qwen 首轮识别（A 基线：自动语种）")
    print("=" * 78)
    jA = {"model": MODEL, "wav": WAV_FULL, "threads": 10, "spans": spans}
    rA = call_runner(jA)
    if not rA:
        return 2
    rowsA = rA["results"][0]["windows"]
    for r in rowsA:
        print("   %5.1f-%5.1f %-9s %s" % (r["t0"], r["t1"], r["language"], (r["text"] or "")[:66]))

    # ---------- 3) 比对 ----------
    print("\n" + "=" * 78)
    print("③ 比对（确认曲目 + 判无歌词/幻觉段）")
    print("=" * 78)
    asr_all = "".join((r.get("text") or "") for r in rowsA)
    best, ranked = LM.pick_best_candidate(asr_all, cands)
    for c, sc in ranked[:5]:
        print("   %-8s %-24s score=%.3f%s" % (c["provider"], c["title"][:22], sc["score"],
                                              "  <== 选中" if c is best else ""))
    if best is None:
        print("  无法确认曲目")
        return 2
    mrows, summ = LM.match_windows(rowsA, best, hi=0.50, mid=0.28, pad=0.6)
    for r in mrows:
        print("   %5.1f-%5.1f %-13s score=%.2f 窗内歌词%d行 最佳='%s'"
              % (r["t0"], r["t1"], r["verdict"], r["best_score"], r["in_span"],
                 (r["best_text"] or "")[:24]))
    print("   汇总：%s" % summ)
    print("   ✅ 官方歌词起点 = %s s → **%s 之前没有歌词**"
          % (best.get("offset_hint"),
             ("%.2f" % (a.t0 + best["offset_hint"])) if best.get("offset_hint") is not None else "?"))

    # ---------- 4) 为每个窗准备 context ----------
    # ★ 关键纪律：`--ctx-pad` 控制 context 是否允许越出窗边界。
    #   实测（lang_dev/_repro_context.py 已证 Qwen 逐字可复现，排除了随机性）：
    #   pad=0（严格窗内）vs pad=1.0（含边界行）在 10~20 s 窗上是 **0.351 vs 0.935** ——
    #   即 context 的**内容边界**会显著改变输出。故把它做成显式参数并默认 1.0（实测更优），
    #   但**不对"为什么"下断言**：这需要更多曲目才能定论。
    verdict_by_span = {(round(r["t0"] + a.t0, 3), round(r["t1"] + a.t0, 3)): r["verdict"]
                       for r in mrows}
    ctxs, ctx_gate = [], []
    for (s, e) in spans:
        g0, g1 = a.t0 + s, a.t0 + e
        v = verdict_by_span.get((round(g0, 3), round(g1, 3)))
        lines = [r["text"] for r in (best.get("ts_lines") or [])
                 if (g0 - a.ctx_pad) <= r["t"] <= (g1 + a.ctx_pad)]
        if v == "no_lyric" or not lines:
            ctxs.append("")
            ctx_gate.append("无歌词 → 不喂 context（挡幻觉）")
        else:
            ctxs.append("\n".join(lines))
            ctx_gate.append("%d 行" % len(lines))
    print("\n   每窗 context（经比对门控）：")
    for (s, e), cx, g in zip(spans, ctxs, ctx_gate):
        first = (cx.split("\n")[0] if cx else "(空)")
        print("     %5.1f-%5.1f  [%s]  %s" % (s, e, g, first[:56]))

    # ---------- 5) B / C 对照 ----------
    print("\n" + "=" * 78)
    print("④ B：强制日语（无 context）  vs  C：强制日语 + 歌词 context + 强制对齐")
    print("=" * 78)
    jB = {"model": MODEL, "wav": WAV_FULL, "threads": 10, "spans": spans,
          "language": "Japanese"}
    jC = {"model": MODEL, "wav": WAV_FULL, "threads": 10, "spans": spans,
          "language": "Japanese", "context": ctxs,
          "aligner": ALIGNER, "return_time_stamps": True}
    rB = call_runner(jB)
    rC = call_runner(jC)
    if not rB or not rC:
        return 2
    rowsB = rB["results"][0]["windows"]
    rowsC = rC["results"][0]["windows"]
    print("   B 载入 %.1fs 推理 %.1fs ；C 载入 %.1fs 推理 %.1fs（含对齐器）"
          % (rB["model_load_s"], rB["results"][0]["infer_s"],
             rC["model_load_s"], rC["results"][0]["infer_s"]))

    def vs(rows, tag):
        tot, n = 0.0, 0
        print("\n   --- %s ---" % tag)
        for r, (s, e) in zip(rows, spans):
            g0, g1 = a.t0 + s, a.t0 + e
            lines = [x["text"] for x in (best.get("ts_lines") or [])
                     if g0 - 1.0 <= x["t"] <= g1 + 1.0]
            ref = "".join(lines)
            sc = LM.similarity(r.get("text") or "", ref) if ref else 0.0
            if ref:
                tot += sc
                n += 1
            print("     %5.1f-%5.1f  vs官方=%.3f  ts=%-4d  %s"
                  % (s, e, sc, len(r.get("time_stamps") or []), (r.get("text") or "")[:52]))
        return (tot / n if n else 0.0)

    ma = vs(rowsA, "A 基线（自动语种，无 context）")
    mb = vs(rowsB, "B 强制日语（无 context）")
    mc = vs(rowsC, "C 强制日语 + 歌词 context + 强制对齐")
    print("\n   与官方歌词的平均相似度：A=%.3f  B=%.3f  C=%.3f" % (ma, mb, mc))

    # ---------- 6) 真实时间戳 → 罗马音摩拉 ----------
    print("\n" + "=" * 78)
    print("⑤ 强制对齐 → **真实时间戳**的罗马音摩拉")
    print("=" * 78)
    for r, (s, e) in zip(rowsC, spans):
        ts = r.get("time_stamps") or []
        if not ts:
            continue
        print("\n   [%5.1f-%5.1f] %s" % (s, e, (r.get("text") or "")[:60]))
        char_times = [[t[0], s + t[1], s + t[2]] for t in ts]
        tl = JR.from_char_times("".join(t[0] for t in ts), char_times, t0=0.0)
        if not tl:
            print("      （没解析出摩拉）")
            continue
        print("      摩拉 %d 个，真实时间戳前 12 个：" % len(tl))
        print("      " + " ".join("%s@%.2f" % (m["romaji"], m["t0"]) for m in tl[:12]))

    with open(os.path.join(ROOT, "lang_dev", "_demo_lyrics_align.json"), "w",
              encoding="utf-8") as f:
        json.dump({"meta": meta, "spans": spans, "ctxs": ctxs,
                   "candidate": {k: v for k, v in best.items()
                                 if k in ("provider", "title", "artists", "offset_hint",
                                          "n_ts_lines")},
                   "A": rowsA, "B": rowsB, "C": rowsC,
                   "summary": summ, "sim": {"A": ma, "B": mb, "C": mc}},
                  f, ensure_ascii=False, indent=2)
    print("\n结果已写入 lang_dev/_demo_lyrics_align.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
