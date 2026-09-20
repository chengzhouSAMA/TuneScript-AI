# -*- coding: utf-8 -*-
"""_eval_lid.py — 在真实 vocals 轨上量 LID 的准确率（项目纪律：先建立可测量真相）。

素材：`回归验收/_stems/`（回归管线复用的固定六轨，只读，不写任何文件）。
**真值出处必须可追溯**——本脚本第一版把 `反乌托邦` 标成了 ja，导致 8 组配置全被判失败；
经核实其作词/作曲/编曲均为乌托邦P、演唱为星尘 Infinity/诗岸，**歌词全中文**
（https://www.huaiyinjie.com/new/44353.html ）→ 真值应为 zh。README 见 lang_dev/_README.md。

  fanwut      反乌托邦 - 乌托邦P              真值 zh  （中文合成人声 星尘/诗岸）
  gouzhi      勾指起誓 - 洛天依Official,ilem   真值 zh  （中文 VOCALOID 洛天依）
  jiabin      嘉宾 (粤语版) - 张远             真值 yue （真人粤语）
  monitoring  モニタリング - DECO27,初音ミク     真值 ja  （日语 VOCALOID 初音ミク）
  shiki       柿崎ユウタ - バカみたいに          真值 ja  （真人日语）

评分：
  strict  判定 == 真值
  lenient 粤语与中文互认（产品侧 zh / yue 共用同一套中文预设时成立）
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lang_id import LanguageDetector                        # noqa: E402

CAND = "zh,ja,en,yue"

# (配置名, win, hop, candidates, min_prob)
CONFIGS = [
    ("A 基线 4s/95类",        4.0, 2.0, None, 0.35),
    ("B 长窗 10s/95类",      10.0, 5.0, None, 0.35),
    ("C 4s/候选集",           4.0, 2.0, CAND, 0.35),
    ("D 10s/候选集",         10.0, 5.0, CAND, 0.35),
    ("E 4s/候选集+高置信",    4.0, 2.0, CAND, 0.50),
    ("F 10s/候选集+高置信",  10.0, 5.0, CAND, 0.50),
    ("G 20s/候选集",         20.0, 10.0, CAND, 0.35),
    ("H 30s/候选集",         30.0, 15.0, CAND, 0.35),
]
QUICK = ["A 基线 4s/95类", "D 10s/候选集", "F 10s/候选集+高置信"]

TRUTH = [("fanwut", "zh"), ("gouzhi", "zh"), ("jiabin", "yue"),
         ("monitoring", "ja"), ("shiki", "ja")]


def find_songs():
    base = os.path.join(ROOT, "回归验收", "_stems")
    out = []
    for key, truth in TRUTH:
        d = os.path.join(base, key)
        if not os.path.isdir(d):
            continue
        hits = [f for f in os.listdir(d) if "vocals" in f.lower() and f.lower().endswith(".wav")]
        if hits:
            out.append((key, os.path.join(d, hits[0]), truth))
    return out


def main():
    quick = "--quick" in sys.argv
    songs = find_songs()
    if not songs:
        print("找不到 vocals 素材")
        return 2
    print("素材：")
    for k, p, t in songs:
        print("  %-8s 真值=%-4s %s" % (k, t, os.path.basename(p)))

    configs = [c for c in CONFIGS if (not quick or c[0] in QUICK)]
    table = []
    for name, win, hop, cand, mp in configs:
        # 本脚本是**第一部分（Silero）**的评测基线，必须钉死后端：
        # 否则 `auto` 会切到 Qwen，5 首 × 8 配置要跑几十分钟，且与历史数字不可比。
        det = LanguageDetector(candidates=cand, min_prob=mp, backend="silero_onnx")
        if not det.available():
            print("LID 不可用：%s" % det.reason)
            return 2
        row = {"config": name, "win": win, "hop": hop, "cand": cand or "95类", "min_prob": mp,
               "songs": [], "strict": 0, "lenient": 0}
        t_start = time.time()
        for key, path, truth in songs:
            r = det.detect_song(path, win=win, hop=hop)
            strict = r["ok"] and r["code"] == truth
            lenient = strict or (truth == "yue" and r["ok"] and r["code"] == "zh")
            row["songs"].append({"key": key, "truth": truth, "code": r["code"],
                                 "ok": r["ok"], "prob": r["prob"], "votes": r["votes"],
                                 "n_voiced": r["n_voiced"], "n_windows": r["n_windows"],
                                 "strict": bool(strict), "lenient": bool(lenient)})
            row["strict"] += int(strict)
            row["lenient"] += int(lenient)
        row["sec"] = round(time.time() - t_start, 1)
        table.append(row)
        print("\n[%s]  win=%.0f hop=%.0f  候选=%s  min_prob=%.2f   (%.1fs)"
              % (name, win, hop, row["cand"], mp, row["sec"]))
        for s in row["songs"]:
            mark = "OK " if s["lenient"] else "XX "
            print("   %s %-8s 真值=%-4s -> %-8s p=%.2f  有效窗 %d/%d  %s"
                  % (mark, s["key"], s["truth"], s["code"], s["prob"],
                     s["n_voiced"], s["n_windows"], json.dumps(s["votes"], ensure_ascii=False)[:90]))
        print("   -> strict %d/%d   lenient %d/%d" % (row["strict"], len(songs),
                                                      row["lenient"], len(songs)))

    print("\n=== 汇总 ===")
    print("  %-24s %-12s %6s %8s %8s %6s" % ("配置", "候选", "窗", "strict", "lenient", "秒"))
    for r in table:
        print("  %-24s %-12s %6.0f %8s %8s %6.1f"
              % (r["config"], r["cand"], r["win"],
                 "%d/%d" % (r["strict"], len(songs)), "%d/%d" % (r["lenient"], len(songs)), r["sec"]))

    outdir = os.path.join(ROOT, "lang_dev")
    with open(os.path.join(outdir, "_eval_lid_result.json"), "w", encoding="utf-8") as f:
        json.dump({"songs": [{"key": k, "truth": t, "path": p} for k, p, t in songs],
                   "configs": table}, f, ensure_ascii=False, indent=2)
    print("\n结果已写入 lang_dev/_eval_lid_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
