# -*- coding: utf-8 -*-
"""_eval_lid_qwen.py — 用 Qwen3-ASR-0.6B 在 5 首真实曲上量 LID（与 Silero 同协议）。

在 **lang_id_venv314** 里跑（Qwen 的依赖与主环境隔离）：
    lang_id_venv314\\Scripts\\python.exe lang_dev/_eval_lid_qwen.py

素材与真值同 `_eval_lid.py`（回归验收/_stems/，只读）。
真值出处见 `_README.md` §3.1（反乌托邦的歌词语言经过外部核实）。

说明：Qwen3-ASR **不暴露语种概率**，语种是 ASR 的副产物 —— 所以本脚本同时打印
转写文本片段，便于判断"语种判错"是不是由"转写崩了"引起的。
"""
import json
import os
import subprocess
import sys
import time

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "lang_id_qwen_runner.py")
PY = os.path.join(ROOT, "lang_id_venv314", "Scripts", "python.exe")
STEMS = os.path.join(ROOT, "回归验收", "_stems")

TRUTH = [("fanwut", "zh"), ("gouzhi", "zh"), ("jiabin", "yue"),
         ("monitoring", "ja"), ("shiki", "ja")]

# (名称, win, hop)
CONFIGS = [("W15/H15", 15.0, 15.0), ("W30/H15", 30.0, 15.0), ("W60/H30", 60.0, 30.0)]

# Qwen 返回的语种名 -> 内部码
NAME2CODE = {
    "chinese": "zh", "mandarin": "zh", "cantonese": "yue", "japanese": "ja",
    "english": "en", "korean": "ko", "spanish": "es", "french": "fr", "german": "de",
    "italian": "it", "portuguese": "pt", "russian": "ru", "thai": "th",
    "vietnamese": "vi", "indonesian": "id", "arabic": "ar", "turkish": "tr",
    "hindi": "hi", "malay": "ms", "dutch": "nl", "swedish": "sv", "danish": "da",
    "finnish": "fi", "polish": "pl", "czech": "cs", "filipino": "fil",
    "persian": "fa", "greek": "el", "hungarian": "hu", "macedonian": "mk",
    "romanian": "ro",
}


def find(key):
    d = os.path.join(STEMS, key)
    for f in os.listdir(d):
        if "vocals" in f.lower() and f.lower().endswith(".wav"):
            return os.path.join(d, f)
    raise SystemExit("缺素材 " + key)


def wav_db(path, t0, t1):
    y, sr = sf.read(path, dtype="float32", always_2d=True, start=int(t0 * 16000),
                    stop=int(t1 * 16000))
    y = y.mean(axis=1)
    if len(y) == 0:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(y.astype(np.float64)))))
    return -120.0 if r <= 1e-12 else 20 * np.log10(r)


def decide(windows, path, silence_db=-50.0, loudness=True):
    """按「有声窗 + 时长×响度加权」表决（与 audio_crop 同规则）。"""
    tally, n_ok, n_all = {}, 0, len(windows)
    dbs = []
    for w in windows:
        codes = [NAME2CODE.get(w["language"].strip().lower())] if w["language"].strip() else []
        db = wav_db(path, w["t0"], w["t1"])
        dbs.append(round(db, 1))
        if not codes or db <= silence_db:
            continue
        n_ok += 1
        if loudness:
            ref = 0.0                      # 稍后再归一；先存 db
        tally.setdefault(codes[0], []).append((max(0.1, w["t1"] - w["t0"]), db))
    if not tally:
        return {"code": "unknown", "prob": 0.0, "n_ok": n_ok, "n_all": n_all, "dbs": dbs,
                "votes": {}}
    votes = {}
    for c, items in tally.items():
        if loudness:
            ref = max(d for _w, d in items)
            votes[c] = sum(w * max(0.05, min(1.0, 10 ** ((d - ref) / 20.0))) for w, d in items)
        else:
            votes[c] = sum(w for w, _d in items)
    tot = sum(votes.values())
    best = max(votes.items(), key=lambda kv: kv[1])
    code = best[0] if best[1] / tot >= 0.5 else "unknown"
    return {"code": code, "prob": round(best[1] / tot, 4), "n_ok": n_ok, "n_all": n_all,
            "dbs": dbs, "votes": {k: round(v, 2) for k, v in sorted(votes.items(), key=lambda kv: -kv[1])}}


def main():
    if not os.path.isfile(PY):
        print("找不到 %s（Qwen 专用 venv）" % PY)
        return 2
    songs = [(k, find(k), t) for k, t in TRUTH]
    job = {"model": os.path.join(ROOT, "lang_id_models", "Qwen3-ASR-0.6B"),
           "threads": 10,
           "wavs": [{"key": k, "path": p} for k, p, _t in songs],
           "configs": [{"win": w, "hop": h} for _n, w, h in CONFIGS],
           "max_windows": 120}
    print("启动 Qwen runner（模型只加载一次）…")
    t0 = time.time()
    proc = subprocess.run([PY, RUNNER], input=json.dumps(job), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=7200)
    el = time.time() - t0
    if proc.returncode != 0 and not proc.stdout.strip():
        print("runner 失败 rc=%s\n%s" % (proc.returncode, (proc.stderr or "")[-2000:]))
        return 2
    try:
        out = json.loads(proc.stdout)
    except Exception as e:
        print("解析失败 %s\nstdout 尾部：%s" % (e, proc.stdout[-1500:]))
        return 2
    if not out.get("ok"):
        print("runner 报错：%s\n%s" % (out.get("error"), out.get("trace", "")[-1200:]))
        return 2
    print("torch=%s  模型加载 %.1fs  总计 %.1fs" % (out["torch"], out["model_load_s"], el))

    cfg_of = {(r["key"], r["win"], r["hop"]): r for r in out["results"]}
    summary = []
    for name, win, hop in CONFIGS:
        print("\n########## %s  (win=%.0f hop=%.0f) ##########" % (name, win, hop))
        strict = lenient = 0
        for key, path, truth in songs:
            r = cfg_of.get((key, win, hop))
            if r is None:
                continue
            d = decide(r["windows"], path)
            strict_ok = d["code"] == truth
            lenient_ok = strict_ok or (truth == "yue" and d["code"] == "zh")
            strict += int(strict_ok)
            lenient += int(lenient_ok)
            print("  %s %-11s 真值=%-4s -> %-8s p=%.2f (%d/%d 有效窗, %.1fs推理)"
                  % ("OK " if lenient_ok else "XX ", key, truth, d["code"], d["prob"],
                     d["n_ok"], d["n_all"], r["infer_s"]))
            for w in r["windows"][:6]:
                print("        %7.1f-%7.1f  %-10s %r" % (w["t0"], w["t1"], w["language"],
                                                         (w["text"] or "")[:46]))
            if len(r["windows"]) > 6:
                print("        … 共 %d 窗" % len(r["windows"]))
        summary.append((name, win, hop, strict, lenient))
        print("  -> strict %d/%d  lenient %d/%d" % (strict, len(songs), lenient, len(songs)))

    print("\n=== Qwen 汇总 ===")
    print("  %-10s %6s %6s %8s %8s" % ("配置", "win", "hop", "strict", "lenient"))
    for n, w, h, s, l in summary:
        print("  %-10s %6.0f %6.0f %8s %8s" % (n, w, h, "%d/5" % s, "%d/5" % l))
    with open(os.path.join(ROOT, "lang_dev", "_eval_lid_qwen_result.json"), "w",
              encoding="utf-8") as f:
        json.dump({"runner": out, "summary": summary}, f, ensure_ascii=False, indent=2)
    print("\n结果已写入 lang_dev/_eval_lid_qwen_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
