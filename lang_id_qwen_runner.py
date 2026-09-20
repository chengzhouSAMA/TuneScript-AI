# -*- coding: utf-8 -*-
"""lang_id_qwen_runner.py — Qwen3-ASR 语种识别的**独立进程 runner**（sidecar）。"""
import argparse
import json
import os
import sys
import time

SR = 16000


def _load_audio(path, sr=SR):
    import numpy as np
    import soundfile as sf
    y, file_sr = sf.read(path, dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    if file_sr != sr:
        try:
            import librosa
            y = librosa.resample(y, orig_sr=file_sr, target_sr=sr)
        except Exception:
            n = int(len(y) * sr / float(file_sr))
            idx = np.linspace(0, len(y) - 1, n)
            y = np.interp(idx, np.arange(len(y)), y).astype("float32")
    return np.ascontiguousarray(y, dtype="float32"), sr


def _spans(dur, win, hop, maxw):
    """生成覆盖整曲的窗（末尾贴尾补一窗）。"""
    spans = []
    t = 0.0
    while t + win <= dur + 1e-6 and len(spans) < maxw:
        spans.append((t, t + win))
        t += hop
    if not spans:
        return [(0.0, dur)]
    if spans[-1][1] < dur - 1e-6 and len(spans) < maxw:
        tail = (max(0.0, dur - win), dur)
        if tail[0] > spans[-1][0] + 1e-6:
            spans.append(tail)
    return spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default="-", help="JSON 文件路径，或 - 表示从 stdin 读")
    a = ap.parse_args()

    raw = sys.stdin.read() if a.job == "-" else open(a.job, encoding="utf-8").read()
    job = json.loads(raw)
    try:
        import numpy as np
        import torch
        threads = int(job.get("threads") or 0)
        if threads > 0:
            torch.set_num_threads(threads)
        from qwen_asr import Qwen3ASRModel

        model_dir = job.get("model") or os.environ.get(
            "TS_QWEN_MODEL",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "lang_id_models", "Qwen3-ASR-0.6B"))
        # ---- 模型初始化：可选挂载强制对齐器 ----
        # `aligner` 给出权重目录时会同时加载 Qwen3-ForcedAligner-0.6B，
        # 于是 `return_time_stamps=True` 才能真正产出**字符级时间戳**。
        aligner = job.get("aligner") or None
        ret_ts = bool(job.get("return_time_stamps"))
        # 联网取回的歌词 → **上下文偏置（context biasing）**，让识别偏向已知歌词。
        ctx = job.get("context") or ""
        t0 = time.time()
        _kw = {}
        if aligner:
            _kw["forced_aligner"] = aligner
            _kw["forced_aligner_kwargs"] = dict(dtype=torch.float32, device_map="cpu")
        model = Qwen3ASRModel.from_pretrained(
            model_dir, dtype=torch.float32, device_map="cpu",
            max_inference_batch_size=1,
            max_new_tokens=int(job.get("max_new_tokens") or 256), **_kw)
        load_s = time.time() - t0

        sr = int(job.get("sr") or SR)
        if job.get("wavs"):
            wavs = [{"key": w.get("key") or os.path.basename(w["path"]), "path": w["path"]}
                    for w in job["wavs"]]
        else:
            wavs = [{"key": job.get("key") or os.path.basename(job["wav"]), "path": job["wav"]}]
        configs = job.get("configs") or [{"win": float(job.get("win") or 10.0),
                                          "hop": float(job.get("hop") or job.get("win") or 10.0)}]
        maxw = int(job.get("max_windows") or 200)
        # 强制语种：日语专项要用它把 ASR 钉在日语上，防止它漂到中文
        force_lang = job.get("language") or None

        results = []
        for w in wavs:
            y, real_sr = _load_audio(w["path"], sr)
            dur = len(y) / float(real_sr)
            # 显式时间片模式（重试用）：`"spans": [[t0,t1], ...]` 直接给要识别的区间，
            # 不走 win/hop 网格 —— 低质量段重试需要**非均匀**切窗。
            explicit = job.get("spans")
            plan = ([{"win": None, "hop": None, "spans": [tuple(map(float, s)) for s in explicit]}]
                    if explicit else configs)
            for cfg in plan:
                win = cfg.get("win")
                win = float(win) if win else None
                hop = float(cfg.get("hop") or win or 0.0) or None
                if "spans" in cfg:
                    spans = [(max(0.0, a), min(dur, b)) for a, b in cfg["spans"]]
                    spans = [(a, b) for a, b in spans if b > a]
                else:
                    spans = _spans(dur, win, hop, maxw)
                # 语种可以按 config 覆盖（重试实验需要逐配置对比"自动 vs 强制"）
                fl = cfg.get("language") or force_lang or None
                need = int((win or max((b - a) for a, b in spans)) * real_sr)
                chunks = []
                for (s, e) in spans:
                    c = y[int(s * real_sr):int(e * real_sr)]
                    if len(c) < need:
                        c = np.pad(c, (0, need - len(c)))
                    chunks.append(c)
                ti = time.time()
                # context 支持：单个字符串（所有窗共用）或与窗等长的列表
                if isinstance(ctx, list):
                    ctxs = (ctx + [""] * len(chunks))[:len(chunks)] if ctx else None
                else:
                    ctxs = ([ctx] * len(chunks)) if ctx and ctx.strip() else None
                outs = model.transcribe(audio=[(c, real_sr) for c in chunks],
                                        context=ctxs,
                                        language=([fl] * len(chunks) if fl else None),
                                        return_time_stamps=ret_ts)
                infer_s = time.time() - ti

                def _ts(r):
                    """把 time_stamps 规范化成 [[text,start,end], ...]（相对该窗起点）。"""
                    raw = getattr(r, "time_stamps", None)
                    out = []
                    for group in (raw or []):
                        items = group if isinstance(group, (list, tuple)) else [group]
                        for it in items:
                            out.append([getattr(it, "text", ""),
                                        round(float(getattr(it, "start_time", 0.0)), 3),
                                        round(float(getattr(it, "end_time", 0.0)), 3)])
                    return out

                results.append({
                    "key": w["key"], "win": win, "hop": hop, "force_language": fl,
                    "context_used": bool(ctxs), "return_time_stamps": ret_ts,
                    "aligner": bool(aligner),
                    "duration": round(dur, 3), "infer_s": round(infer_s, 2),
                    "windows": [{"t0": round(s, 3), "t1": round(e, 3),
                                 "language": (getattr(r, "language", "") or "").strip(),
                                 "text": (getattr(r, "text", "") or "")[:200],
                                 "time_stamps": _ts(r)}
                                for (s, e), r in zip(spans, outs)]})
        out = {"ok": True, "model": str(model_dir), "torch": torch.__version__,
               "model_load_s": round(load_s, 2), "results": results}
    except Exception as e:
        import traceback
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:400]),
               "trace": traceback.format_exc()[-1500:]}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
