# -*- coding: utf-8 -*-
"""_probe_ctx_anchor.py — context 的"锚点"机制验证（单变量、只用一个窗）。

疑问：为什么同为 10~20 s 窗，context 用 7 行（含边界行 `さよなら`）得 0.935，
      用 5 行（从 `少しだけ違った…` 起）只得 0.351？

假设：**该窗的开头落在 `さよなら` 这一行中间**（官方 LRC `[00:09.88]さよなら`，
窗从 10.0 s 开始）。若 context 缺了"窗开头正在唱的那一行"，模型就失去锚点。

做法：同一个 10~20 s 窗，只改 context 的首行，其余完全相同：
  V1 从 `さよなら` 起（= 窗开头所在行）
  V2 从 `少しだけ違っただけの愛情表現` 起（= 窗内第一行，但窗开头不在它上面）
  V3 空（无 context）
若 V1 >> V2 ≈ V3 → 假设成立：**context 必须包含"窗起点正在唱的那一行"**。

用法: lang_id_venv314\\Scripts\\python.exe lang_dev/_probe_ctx_anchor.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PY = os.path.join(ROOT, "lang_id_venv314", "Scripts", "python.exe")
RUNNER = os.path.join(ROOT, "lang_id_qwen_runner.py")
MODEL = os.path.join(ROOT, "lang_id_models", "Qwen3-ASR-0.6B")
WAV = os.path.join(ROOT, "lang_dev", "_out_lyrics_align", "seg_0_30.wav")
SPAN = [[10.0, 20.0]]

L = ["さよなら", "少しだけ違っただけの愛情表現", "メランコリー",
     "普段通り\u3000独りきり段取り", "私、多くは求めていないのに、",
     "隠した手のひらの分だけ", "増える感情表現"]
VARIANTS = [("V1 从『さよなら』起（窗开头所在行）", "\n".join(L[0:7])),
            ("V2 从『少しだけ違った…』起", "\n".join(L[1:7])),
            ("V3 空 context（对照）", "")]


def run(ctx):
    job = {"model": MODEL, "wav": WAV, "threads": 10, "spans": SPAN,
           "language": "Japanese", "aligner": None, "return_time_stamps": False}
    if ctx:
        job["context"] = ctx
    p = subprocess.run([PY, RUNNER], input=json.dumps(job), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=3600)
    o = json.loads(p.stdout)
    if not o.get("ok"):
        print("  报错：%s" % str(o.get("error"))[:200])
        return None
    return o["results"][0]["windows"][0]


def main():
    if not os.path.isfile(WAV):
        print("缺 %s" % WAV)
        return 2
    import lyrics_match as LM
    ref = "".join(L)
    print("参照（官方歌词 10~20 s 区间，%d 行）：\n  %s\n" % (len(L), ref))
    out = []
    for tag, ctx in VARIANTS:
        r = run(ctx)
        if r is None:
            continue
        sc = LM.similarity(r.get("text") or "", ref)
        out.append({"tag": tag, "ctx": ctx, "text": r.get("text"), "sim": sc})
        print("%s" % tag)
        print("  context 首行：%s" % (ctx.split("\n")[0] if ctx else "(空)"))
        print("  输出：%s" % (r.get("text") or "")[:88])
        print("  与官方相似度：%.3f\n" % sc)
    print("=== 结论 ===")
    if len(out) == 3:
        v1, v2, v3 = out[0]["sim"], out[1]["sim"], out[2]["sim"]
        if v1 > v2 + 0.15:
            print("  ✅ 假设成立：V1(%.3f) 远高于 V2(%.3f) 与 V3(%.3f)" % (v1, v2, v3))
            print("  ⇒ **context 必须包含「窗起点正在唱的那一行」**（窗常从词中间开始）。")
            print("  ⇒ 所以构建 context 要用**重叠**判定（允许向前超出窗边界），")
            print("     而不是「严格窗内」——后者会切掉锚点行，效果反而更差。")
        else:
            print("  ✗ 不是「锚点行」造成的：V1=%.3f V2=%.3f V3=%.3f" % (v1, v2, v3))
            print("    需另找原因（可能是 context 长度/换行结构的影响）。")
    with open(os.path.join(ROOT, "lang_dev", "_probe_ctx_anchor.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n结果已写入 lang_dev/_probe_ctx_anchor.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
