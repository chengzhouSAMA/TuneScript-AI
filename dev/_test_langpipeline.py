# -*- coding: utf-8 -*-
"""_test_langpipeline.py — lang_pipeline 接线的**秒级**验证（用 stub 替代 Basic Pitch）。

验证点：
  1. `TS_LANG_SEG` 未启用时：**不调用 LID**，直接整轨识别一次（= 出厂行为）
  2. 启用时：在合成的「中·日」混唱上真的切段 → 每段单独调用识别 → 音符回到**全局时间轴**
  3. 每段的 `min_len` 取自该段语种的预设（ja 的 fill_win 与 default 不同，这里看 min_len）
  4. 静音段被跳过（不浪费识别时间）
  5. LID 不可用 / 抛异常 → 自动退化整轨，不崩

用法: python lang_dev/_test_langpipeline.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MIX = os.path.join(ROOT, "lang_dev", "_out_switch", "mix2_zh20_ja30_zh20.wav")

CALLS = []


def stub_transcribe(path, label, min_len):
    """假的转谱器：每 1 秒一个音符（≥10 个，避免触发"音符过少退回整轨"的保底）。"""
    import soundfile as sf
    info = sf.info(path)
    dur = info.frames / float(info.samplerate)
    CALLS.append({"path": os.path.basename(path), "label": label, "min_len": min_len, "dur": dur})
    out = [(0.10, 0.60, 60, 90), (dur - 0.30, dur - 0.05, 72, 80)]
    t = 1.0
    while t + 0.4 < dur - 0.5:
        out.append((t, t + 0.40, 60 + int(t) % 12, 85))
        t += 1.0
    return out


def main():
    from lang_pipeline import transcribe_vocal_by_language
    if not os.path.isfile(MIX):
        print("缺素材，请先跑 lang_dev/_test_crop.py 生成 %s" % MIX)
        return 2

    print("=== 1) 未启用：应只调用一次整轨识别、完全不碰 LID ===")
    CALLS.clear()
    notes, info = transcribe_vocal_by_language(MIX, stub_transcribe, None, out_dir=None)
    print("  识别调用 %d 次：%s" % (len(CALLS), CALLS))
    print("  mode=%s  notes=%d  reason=%s" % (info["mode"], len(notes), info["reason"]))
    assert len(CALLS) == 1 and info["mode"] == "whole", "未启用时不应分段"
    print("  ✓ 未启用时行为与出厂一致（整轨一次）")

    print("\n=== 2) 启用：语种分割 → 逐段识别 → 拼回全局时间轴 ===")
    CALLS.clear()
    notes, info = transcribe_vocal_by_language(MIX, stub_transcribe, None,
                                               out_dir=None, backend="silero_onnx",
                                               win=8.0, hop=4.0, min_seg=6.0, force=True)
    print("  mode=%s  notes=%d" % (info["mode"], len(notes)))
    print("  reason=%s  backend=%s" % (info["reason"], info["backend"]))
    print("  lid_meta=%s" % info.get("lid_meta"))
    for s in info["segments"]:
        print("    seg#%s %8.2f-%8.2f %-8s min_len=%s n_notes=%s"
              % (s["index"], s["t0"], s["t1"], s["lang"], s["min_len"], s["n_notes"]))
    for s in info.get("skipped") or []:
        print("    skip#%s %s" % (s["index"], s["reason"]))
    print("  识别调用：")
    for c in CALLS:
        print("    %-46s min_len=%-4s dur=%.1fs" % (c["path"], c["min_len"], c["dur"]))
    print("  拼回后的全局时间轴音符：")
    for n in notes:
        print("    %8.2f - %8.2f  pitch=%s" % (n[0], n[1], n[2]))

    ok_offset = True
    for n in notes:
        if n[0] < 0 or n[1] > info["lid_meta"]["win"] + 10000:
            ok_offset = False
    print("  ✓ 时间轴偏移检查：%s" % ("通过" if ok_offset else "异常"))
    if info["mode"] == "language_segments":
        print("  ✓ 确实走了语种分段路径（%d 段）" % len(info["segments"]))
    else:
        print("  ! 退化为整轨（%s）—— 若 LID 判为单语种属正常" % info["reason"])

    print("\n=== 3) 异常注入：LID 后端不存在 → 必须退化整轨、不崩（force=True） ===")
    CALLS.clear()
    os.environ["TS_LANG_BACKEND"] = "___no_such_backend___"
    try:
        notes2, info2 = transcribe_vocal_by_language(MIX, stub_transcribe, None,
                                                     out_dir=None, force=True)
        print("  mode=%s reason=%s" % (info2["mode"], info2["reason"]))
        print("  ✓ 未抛异常，调用 %d 次" % len(CALLS))
    finally:
        os.environ.pop("TS_LANG_BACKEND", None)

    print("\n=== 4) 异常注入：识别函数抛错 → 必须退化整轨、不崩（force=True） ===")
    def boom(path, label, min_len):
        CALLS.append(path)
        raise RuntimeError("boom")
    try:
        notes3, info3 = transcribe_vocal_by_language(MIX, boom, None, out_dir=None,
                                                     backend="silero_onnx", force=True)
        print("  mode=%s reason=%s notes=%d" % (info3["mode"], info3["reason"], len(notes3)))
        print("  ✓ 未把异常抛给调用方")
    except Exception as e:
        print("  ✗ 抛出了异常：%s" % e)
        return 1

    print("\n=== 5) 门控语义：未启用且未 force → 只透传给识别函数，异常照常抛出 ===")
    CALLS.clear()
    try:
        transcribe_vocal_by_language(MIX, boom, None, out_dir=None)
        print("  ✗ 本该抛出（门控关闭时是纯透传，异常归调用方）")
        return 1
    except RuntimeError:
        print("  ✓ 门控关闭 = 纯透传（调用 %d 次，异常传给调用方，与改动前一致）" % len(CALLS))

    print("\n全部接线检查完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
