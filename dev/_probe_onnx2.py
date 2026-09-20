# -*- coding: utf-8 -*-
"""_probe_onnx2.py — 探长度约束 + 关键标签索引（只读探查）。

用法: python lang_dev/_probe_onnx2.py
"""
import json
import os

import numpy as np
import onnxruntime as ort

MD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lang_id_models")
s = ort.InferenceSession(os.path.join(MD, "lang_classifier_95.onnx"), providers=["CPUExecutionProvider"])
with open(os.path.join(MD, "lang_dict_95.json"), encoding="utf-8") as f:
    d = json.load(f)


def softmax(x):
    x = x - np.max(x)
    e = np.exp(x)
    return e / e.sum()


print("=== 与中文/日语/英语相关的全部标签 ===")
for k in sorted(d, key=int):
    lab = d[k]
    head = lab.split(",")[0].strip()
    if head.startswith(("zh", "ja", "en", "yue", "cmn")) or "Chinese" in lab or "Japanese" in lab:
        print("  %3s  %s" % (k, lab))

print("\n=== 输入长度约束（batched，2D） ===")
rs = np.random.RandomState(1)
for sec in (0.25, 0.5, 1.0, 2.0, 3.0, 4.0, 8.0, 16.0, 30.0):
    n = int(16000 * sec)
    x = (0.05 * rs.randn(n)).astype(np.float32)[None, :]
    try:
        y = np.asarray(s.run(["output"], {"input": x})[0])
        print("  %5.2fs (%7d samples) -> %s" % (sec, n, y.shape))
    except Exception as e:
        print("  %5.2fs (%7d samples) -> ERROR %s" % (sec, n, str(e)[:110]))

print("\n=== 批量（batch>1） ===")
x = (0.05 * rs.randn(3, 32000)).astype(np.float32)
try:
    y = np.asarray(s.run(["output"], {"input": x})[0])
    print("  batch=3 -> %s" % (y.shape,))
    for i in range(y.shape[0]):
        p = softmax(y[i])
        print("    [%d] top=%s p=%.4f" % (i, d[str(int(np.argmax(p)))], float(p.max())))
except Exception as e:
    print("  batch=3 -> ERROR %s" % str(e)[:150])

print("\n=== 输出是否为 logits（看取值范围） ===")
x = (0.05 * rs.randn(1, 64000)).astype(np.float32)
y = np.asarray(s.run(["output"], {"input": x})[0])[0]
print("  min=%.3f max=%.3f sum=%.3f  -> %s"
      % (y.min(), y.max(), y.sum(), "logits(需 softmax)" if not (0.99 < y.sum() < 1.01) else "已归一化"))
