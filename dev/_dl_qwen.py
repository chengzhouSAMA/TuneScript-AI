# -*- coding: utf-8 -*-
"""_dl_qwen.py — 下载 Qwen3-ASR-0.6B 权重到 lang_id_models/（走 hf-mirror）。

在 lang_id_venv314 里运行：
    lang_id_venv314\\Scripts\\python.exe lang_dev/_dl_qwen.py [--repo Qwen/Qwen3-ASR-0.6B]
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Qwen/Qwen3-ASR-0.6B")
    ap.add_argument("--dest", default=None)
    a = ap.parse_args()
    dest = a.dest or os.path.join(ROOT, "lang_id_models", a.repo.split("/")[-1])
    os.makedirs(dest, exist_ok=True)
    print("HF_ENDPOINT =", os.environ.get("HF_ENDPOINT", "(未设置)"))
    print("repo -> %s\n dest -> %s" % (a.repo, dest))
    from huggingface_hub import snapshot_download
    p = snapshot_download(
        repo_id=a.repo,
        local_dir=dest,
        allow_patterns=["*.json", "*.txt", "*.safetensors", "*.model", "*.py"],
        max_workers=4,
    )
    print("DONE ->", p)
    total = 0
    for d, _s, fs in os.walk(p):
        for f in fs:
            fp = os.path.join(d, f)
            total += os.path.getsize(fp)
            if os.path.getsize(fp) > 1 << 20:
                print("  %10.1f MB  %s" % (os.path.getsize(fp) / 1048576.0,
                                           os.path.relpath(fp, p)))
    print("合计 %.2f GB" % (total / 1073741824.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
