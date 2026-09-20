# -*- coding: utf-8 -*-
"""_test_en_phonetics.py — 用真实英文人声验证"英语音标音节"路径。

项目此前没有英语素材。主流英文歌在网易云免登录只有 30~45 秒试听，
所以这里显式打开 `TS_NETEASE_ALLOW_TRIAL=1` 取一段真实英文唱段 ——
验证音素/音节路径不需要整曲，30 秒足够。

流程：网易云取试听段 → 六轨分离 → Qwen 识别英文 → 英语音节轴 → 与音符对表

用法: python lang_dev/_test_en_phonetics.py ["关键词"]
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "Hello Adele"
    os.environ["TS_NETEASE_ALLOW_TRIAL"] = "1"
    import netease as N
    import asr_refine as AR
    import audio_crop as AC

    out = os.path.join(ROOT, "lang_dev", "_out_en")
    os.makedirs(out, exist_ok=True)

    print("=" * 74)
    print("① 取一段真实英文唱段（试听段，显式允许）")
    print("=" * 74)
    path, info = N.fetch_song(query=kw, out_dir=out, level="exhigh", progress=print)
    if not path:
        print("取不到：%s" % info.get("reason"))
        return 2
    trial = (info.get("resolve") or {}).get("trial") or {}
    print("  文件 %s（%.1f MB）" % (os.path.basename(path), os.path.getsize(path) / 1048576))
    print("  试听区间 %s~%s 秒；音质 %s" % (trial.get("start"), trial.get("end"),
                                            (info.get("resolve") or {}).get("reason")))

    print("\n" + "=" * 74)
    print("② 六轨分离")
    print("=" * 74)
    import transcriber_app as ta
    base = os.path.splitext(os.path.basename(path))[0]
    stems = ta.separate_stems(path, out, base, print)
    print("  轨：%s" % sorted(stems.keys()))
    voc = stems.get("vocals")
    if not voc:
        print("  没拿到人声轨")
        return 2

    print("\n" + "=" * 74)
    print("③ Qwen 识别英文（自动语种）")
    print("=" * 74)
    py = os.path.join(ROOT, "lang_id_venv314", "Scripts", "python.exe")
    runner = os.path.join(ROOT, "lang_id_qwen_runner.py")
    dur = AC.read_wav(voc)[0].shape[0] / 22050.0
    spans = []
    t = 0.0
    while t + 10.0 <= dur:
        spans.append([round(t, 2), round(t + 10.0, 2)])
        t += 10.0
    if not spans:
        spans = [[0.0, round(dur, 2)]]
    job = {"model": os.path.join(ROOT, "lang_id_models", "Qwen3-ASR-0.6B"),
           "wav": voc, "threads": 10, "spans": spans, "max_windows": 40}
    p = subprocess.run([py, runner], input=json.dumps(job), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=3600)
    r = json.loads(p.stdout) if (p.stdout or "").strip() else {}
    if not r.get("ok"):
        print("  ASR 失败：%s" % str(r.get("error"))[:200])
        return 2
    rows = r["results"][0]["windows"]
    for w in rows:
        print("   %5.1f-%5.1f %-9s %s" % (w["t0"], w["t1"], w["language"], (w["text"] or "")[:70]))

    print("\n" + "=" * 74)
    print("④ 英语音节轴（IPA 音标）+ 与音符对表")
    print("=" * 74)
    en_rows = [w for w in rows if AR.detect_lang(w["text"] or "") == "en"]
    if not en_rows:
        print("  没有判为英语的段落（试听段可能主要是伴奏）")
        return 2
    grid = AR.syllable_grid(en_rows, lang="en")
    print("  音节 %d 个；IPA：" % len(grid))
    for w in en_rows:
        tl = AR.phonetic_timeline(w["text"], lang="en")
        print("   [%5.1f-%5.1f] %s" % (w["t0"], w["t1"], tl["label"][:78]))
        if tl["oov"]:
            print("        未收录词（拼读兜底）：%s" % ", ".join(tl["oov"][:8]))
    print("\n  前 14 个音节（时间 / IPA / ARPAbet）：")
    for u in grid[:14]:
        print("   %6.2f-%6.2f  %-9s %-14s %s"
              % (u["t0"], u["t1"], u["ipa"], u["unit"], u.get("word", "")))

    from basic_pitch.inference import Model as BpModel
    model = BpModel(ta.find_model())
    notes = ta.transcribe_notes(voc, model, lambda m: None, label="人声", min_len=60)
    print("\n  Basic Pitch 音符：%d 个" % len(notes))
    for tol in (0.12, 0.20, 0.30):
        rr = AR.notes_vs_units(notes, grid, tol=tol)
        print("   tol=%.2fs → 音节命中 %d/%d = %.1f%%"
              % (tol, rr["n_hit"], rr["n_units"], 100 * rr["hit_rate"]))
    rr = AR.notes_vs_units(notes, grid, tol=0.20)
    print("\n  没被弹出来的音节（前 12）：")
    for m in rr["missing"][:12]:
        print("   %6.2f  %-9s %-12s %s" % (m["t0"], m["ipa"], m["unit"], m.get("word", "")))

    with open(os.path.join(ROOT, "lang_dev", "_test_en_phonetics.json"), "w",
              encoding="utf-8") as f:
        json.dump({"wav": voc, "spans": spans, "rows": rows,
                   "n_syllables": len(grid), "n_notes": len(notes),
                   "hit": rr["n_hit"], "hit_rate": rr["hit_rate"]},
                  f, ensure_ascii=False, indent=2)
    print("\n结果已写入 lang_dev/_test_en_phonetics.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
