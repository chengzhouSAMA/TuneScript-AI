# -*- coding: utf-8 -*-
"""_probe_onnx.py — 实探 Silero LID ONNX 的输入输出契约（只读探查，不猜）。

用法: python lang_dev/_probe_onnx.py
"""
import json
import os
import sys

import numpy as np
import onnxruntime as ort

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD = os.path.join(ROOT, "lang_id_models")
ONNX = os.path.join(MD, "lang_classifier_95.onnx")

print("=== ONNX IO 契约 ===")
s = ort.InferenceSession(ONNX, providers=["CPUExecutionProvider"])
inp = s.get_inputs()
out = s.get_outputs()
for i in inp:
    print("  IN   name=%r shape=%s type=%s" % (i.name, i.shape, i.type))
for o in out:
    print("  OUT  name=%r shape=%s type=%s" % (o.name, o.shape, o.type))

print("\n=== 标签字典 ===")
with open(os.path.join(MD, "lang_dict_95.json"), encoding="utf-8") as f:
    d = json.load(f)
with open(os.path.join(MD, "lang_group_dict_95.json"), encoding="utf-8") as f:
    g = json.load(f)
print("  lang_dict: type=%s len=%d" % (type(d).__name__, len(d)))
if isinstance(d, dict):
    items = sorted(d.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0)
    print("  first 8:", items[:8])
    print("  last  4:", items[-4:])
    rev = {}
    for k, v in d.items():
        rev.setdefault(str(v), []).append(k)
    for want in ("zh", "ja", "en", "ko", "yue", "cantonese", "cmn", "wuu"):
        if want in rev:
            print("  idx(%-9s) = %s" % (want, rev[want]))
print("  lang_group_dict: len=%d head=%s" % (len(g), (list(g.items())[:5] if isinstance(g, dict) else g[:5])))

print("\n=== 用静音 / 噪声 / 正弦试跑，确认能否前向 ===")
name_in = inp[0].name
name_out = out[0].name
for tag, x in (("zeros_2s", np.zeros(32000, dtype=np.float32)),
               ("noise_2s", np.random.RandomState(0).randn(32000).astype(np.float32) * 0.05),
               ("sine_2s", (0.3 * np.sin(2 * np.pi * 440 * np.arange(32000) / 16000)).astype(np.float32))):
    for shape_tag, feed_x in (("1d", x), ("2d", x[None, :])):
        try:
            y = s.run([name_out], {name_in: feed_x})[0]
            y = np.asarray(y)
            top = int(np.argmax(y.reshape(-1)))
            print("  %-9s %-3s -> out.shape=%s  argmax=%d  label=%s  p=%.4f"
                  % (tag, shape_tag, y.shape, top, d.get(str(top), "?"), float(y.reshape(-1)[top])))
        except Exception as e:
            print("  %-9s %-3s -> ERROR %s" % (tag, shape_tag, str(e)[:120]))
