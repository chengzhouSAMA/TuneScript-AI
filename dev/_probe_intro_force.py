# -*- coding: utf-8 -*-
"""_probe_intro_force.py — 前奏重试实验：**自动语种 vs 强制日语**，逐档细窗。

目的（主人要求）：把"识别不出来的"前奏段再切细、并用谐音音节方式识别。
本脚本先回答最关键的一步 —— **强制日语**能不能把 0~10 s 从中文乱码里救回来。

在 lang_id_venv314 里跑：
    lang_id_venv314\\Scripts\\python.exe lang_dev/_probe_intro_force.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "lang_id_venv314", "Scripts", "python.exe")
RUNNER = os.path.join(ROOT, "lang_id_qwen_runner.py")
WAV = os.path.join(ROOT, "lang_dev", "_out_intro", "shiki_intro_0_30.wav")

CONFIGS = [
    {"win": 10.0, "hop": 10.0, "language": None,        "tag": "自动 W10"},
    {"win": 10.0, "hop": 10.0, "language": "Japanese",  "tag": "强制日 W10"},
    {"win": 5.0,  "hop": 5.0,  "language": None,        "tag": "自动 W5"},
    {"win": 5.0,  "hop": 5.0,  "language": "Japanese",  "tag": "强制日 W5"},
    {"win": 3.0,  "hop": 3.0,  "language": "Japanese",  "tag": "强制日 W3"},
    {"win": 2.0,  "hop": 2.0,  "language": "Japanese",  "tag": "强制日 W2"},
]


def main():
    job = {"model": os.path.join(ROOT, "lang_id_models", "Qwen3-ASR-0.6B"),
           "wav": WAV, "threads": 10, "max_windows": 60,
           "configs": CONFIGS}
    print("跑 %d 个配置（模型只加载一次）…" % len(CONFIGS))
    p = subprocess.run([PY, RUNNER], input=json.dumps(job), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=7200)
    if not (p.stdout or "").strip():
        print("runner 无输出 rc=%s\n%s" % (p.returncode, (p.stderr or "")[-1500:]))
        return 2
    out = json.loads(p.stdout)
    if not out.get("ok"):
        print("runner 报错：%s\n%s" % (out.get("error"), out.get("trace", "")[-800:]))
        return 2
    print("torch=%s  载入 %.1fs" % (out["torch"], out["model_load_s"]))
    for r in out["results"]:
        print("\n===== win=%g hop=%g  force=%s  推理 %.1fs ====="
              % (r["win"], r["hop"], r.get("force_language"), r["infer_s"]))
        for w in r["windows"]:
            print("  %5.1f-%5.1f %-9s %s" % (w["t0"], w["t1"], w["language"], w["text"]))
    with open(os.path.join(ROOT, "lang_dev", "_probe_intro_force.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n结果已写入 lang_dev/_probe_intro_force.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
