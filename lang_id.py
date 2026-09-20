# -*- coding: utf-8 -*-
"""lang_id.py — 歌曲人声语种识别（LID）适配层。

作用
----
给「按语种自动分配人声识别模式」提供**语言判定**这一个前端传感器：
输入人声轨（或整曲混音），输出每个时间窗的语种与置信度，以及全曲的语种决策。

设计纪律（对齐项目既有约定）
----------------------------
1. **零强依赖**：onnxruntime 缺席 / 模型文件缺失 / 任何异常 → 一律返回
   ``available() is False`` 或 ``None``，**绝不抛异常打断转谱管线**。
2. **可插拔后端**：内置 `silero_onnx`（Silero LID lang95，95 语种，MIT，
   ~17MB，onnxruntime 已在项目环境里）。用 `register_backend()` 可加新后端
   （如 SpeechBrain VoxLingua107 ECAPA 107 语种 / Qwen3-ASR-0.6B 30 语种且原生支持歌唱），
   上层调用点不需要改。
3. **不迷信输出**：实测静音/白噪声会给出 ``nn``(Norwegian Nynorsk) 且
   ``p≈0.68`` 的**假阳性** → 因此判定必须同时过 **置信度门** 与 **能量门**
   （见 `decide()`），且语种不确定时返回 ``unknown`` 而不是硬猜。
4. **不改产物**：本模块只读音频、只返回数据，不写任何文件。

ONNX 契约（由 `lang_dev/_probe_onnx.py` 实探，非猜测）
-----------------------------------------------------
- 输入 ``input``  : float32, rank 2, ``[batch, samples]``，原始波形（16 kHz 单声道），
                    **长度可变**（0.25s~30s 实测均可）
- 输出 ``output`` : float32, rank 2, ``[batch, 95]``，**未归一化 logits（需 softmax）**
  （另有第二输出 ``2038`` shape ``[8, 58]`` = 语种**组**头，本项目不用）
- 标签：``lang_dict_95.json``；关键索引 ``ja=40``、``zh=1``、``zh-CN=18``、
  ``zh-HK=85``（粤语）、``zh-TW=94``、``en=30``

用法
----
    from lang_id import LanguageDetector
    det = LanguageDetector()
    if det.available():
        info = det.detect_song("xxx_vocals.wav")   # 整曲决策
        wins = det.classify_windows("xxx_vocals.wav")  # 逐窗明细

CLI: python lang_id.py --audio xxx_vocals.wav [--win 4 --hop 2]
"""
import json
import os
import sys

import numpy as np

try:
    import onnxruntime as _ort
except Exception:                                     # pragma: no cover
    _ort = None

try:
    import soundfile as _sf
except Exception:                                     # pragma: no cover
    _sf = None

try:
    import librosa as _librosa
except Exception:                                     # pragma: no cover
    _librosa = None


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
SR = 16000                       # 模型要求的采样率（Silero 系固定 16 kHz）
ROOT = os.path.dirname(os.path.abspath(__file__))


def _exe_dir():
    """**exe/脚本所在目录**。

    打包成 onefile exe 后 `__file__` 指向解包临时目录（`_MEIPASS`），
    所以必须优先用 `sys.executable` 的目录 —— 否则挂在 exe 旁边的
    模型/venv 永远找不到。开发态则等于脚本目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return ROOT


def _bundle_dir():
    """PyInstaller 解包目录（onefile 时是临时目录）；非打包态返回 None。"""
    return getattr(sys, "_MEIPASS", None)


def resolve_resource(rel, extra_env=None):
    """按**外部优先、打包内含次之、脚本目录兜底**的顺序解析一个资源目录/文件。

    优先级（与项目既有约定一致：`find_*_checkpoint()` 优先 exe 同目录，
    重度权重外挂在 dist/ 下 mt3/、piano_btd/）：
      1. 环境变量 `extra_env`（若给）
      2. **exe 同目录** 下的相对路径（用户可把大权重/venv 放在 exe 旁边）
      3. 打包内含（`_MEIPASS`，spec 的 datas 落在那里）
      4. 源码脚本目录（开发态）
    """
    if extra_env:
        v = os.environ.get(extra_env)
        if v and os.path.exists(v):
            return v
    cands = [os.path.join(_exe_dir(), rel)]
    bd = _bundle_dir()
    if bd:
        cands.append(os.path.join(bd, rel))
    cands.append(os.path.join(ROOT, rel))
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[0]          # 都不存在时返回首选路径，便于报错信息可读


DEFAULT_MODEL_DIR = resolve_resource("lang_id_models")

# 标签前缀 -> 项目内部语种码。
# zh-HK 单列为 yue（粤语）：本项目已有「嘉宾(粤语版)」这类素材，粤语的音节密度
# 与普通话差异明显，值得单独一套预设；其余中文变体统一归 zh。
PREFIX_TO_CODE = {
    "zh": "zh", "zh-cn": "zh", "zh-tw": "zh", "zh-hk": "yue",
    "ja": "ja", "en": "en", "ko": "ko",
}

# 语种码 -> 中文显示名（日志/报告用）
CODE_NAMES = {
    "zh": "中文(普通话)", "yue": "粤语", "ja": "日语", "en": "英语", "ko": "韩语",
    "unknown": "未判定",
}

# 环境开关（与项目其它开关同风格；TS_LANG_ID=0 一键关掉，回到改动前行为）
ENV_ENABLE = "TS_LANG_ID"            # "0" 关闭（默认开，但仅在模型可用时生效）
ENV_MODEL_DIR = "TS_LANG_MODEL_DIR"  # 模型目录覆盖
ENV_BACKEND = "TS_LANG_BACKEND"      # 后端名覆盖，默认 silero_onnx
ENV_MIN_PROB = "TS_LANG_MIN_PROB"    # 置信度门，默认 0.35
ENV_MIN_DB = "TS_LANG_MIN_DB"        # 人声能量门（相对满量程 dBFS），默认 -45
ENV_CANDIDATES = "TS_LANG_CANDIDATES"    # 候选语种白名单，如 "zh,ja,en,yue"；空=全部 95
ENV_MIN_CAND_MASS = "TS_LANG_MIN_CAND_MASS"  # 候选集最小概率质量，默认 0.10


def _env_float(name, default):
    try:
        v = os.environ.get(name)
        return default if v is None or v == "" else float(v)
    except Exception:
        return default


def _log(msg):
    """轻量日志：沿用项目的 progress 风格，失败绝不影响主流程。"""
    try:
        sys.stderr.write("[lang_id] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass


# --------------------------------------------------------------------------
# 音频读取
# --------------------------------------------------------------------------
def load_audio_mono16k(path, sr=SR):
    """读成 (float32 mono @16kHz, 实际采样率)。读不了就抛异常（由调用方兜底）。"""
    y = None
    if _sf is not None:
        try:
            y, file_sr = _sf.read(path, dtype="float32", always_2d=True)
            y = y.mean(axis=1)                        # 多声道下混
            if file_sr != sr and _librosa is not None:
                y = _librosa.resample(y, orig_sr=file_sr, target_sr=sr)
            return np.ascontiguousarray(y, dtype=np.float32), sr
        except Exception:
            y = None
    if _librosa is not None:
        y, _ = _librosa.load(path, sr=sr, mono=True)
        return np.ascontiguousarray(y, dtype=np.float32), sr
    raise RuntimeError("无法读取音频：soundfile 与 librosa 都不可用")


def _softmax(x):
    x = np.asarray(x, dtype=np.float64)
    x = x - np.max(x)
    e = np.exp(x)
    return e / np.sum(e)


def _dbfs(x):
    """信号 RMS 的 dBFS（满量程=0）。空信号返回 -inf。"""
    if x is None or len(x) == 0:
        return float("-inf")
    r = float(np.sqrt(np.mean(np.square(np.asarray(x, dtype=np.float64)))))
    return float("-inf") if r <= 1e-12 else 20.0 * np.log10(r)


# --------------------------------------------------------------------------
# 后端：可插拔
# --------------------------------------------------------------------------
_BACKENDS = {}


def register_backend(name, factory):
    """注册一个 LID 后端。factory(model_dir, **kw) -> 具备 available/predict 的对象。

    新后端的契约（最小实现）：
        .available() -> bool
        .label_dict() -> {str(idx): "ja, Japanese"}     # 索引 -> 标签
        .predict(batch2d: np.ndarray) -> (batch, N) 原始 logits
    """
    _BACKENDS[str(name)] = factory


def list_backends():
    return sorted(_BACKENDS.keys())


def auto_backend_name():
    """`auto` 的解析结果：**优先 Qwen3-ASR**（歌唱/带伴奏整曲场景明显更强，
    且原生支持粤语），不可用则退回 Silero lang95（体积小、零依赖、CPU 秒级）。
    可用 `TS_LANG_BACKEND` 强制指定任一后端。"""
    try:
        if QwenAsrSidecarBackend().available():
            return QwenAsrSidecarBackend.NAME
    except Exception:
        pass
    return SileroOnnxBackend.NAME


class SileroOnnxBackend(object):
    """Silero LID lang95 的 ONNX 后端（deepghs/silero-lang95-onnx，MIT）。"""

    NAME = "silero_onnx"
    ONNX_FILE = "lang_classifier_95.onnx"
    DICT_FILE = "lang_dict_95.json"

    def __init__(self, model_dir):
        self.model_dir = model_dir
        self._sess = None
        self._labels = None
        self.reason = ""

    def available(self):
        if _ort is None:
            self.reason = "onnxruntime 未安装"
            return False
        p = os.path.join(self.model_dir, self.ONNX_FILE)
        if not os.path.isfile(p):
            self.reason = "模型文件缺失：%s" % p
            return False
        try:
            self._ensure()
            return True
        except Exception as e:
            self.reason = "加载失败：%s" % str(e)[:200]
            return False

    def _ensure(self):
        if self._sess is None:
            opts = _ort.SessionOptions()
            opts.log_severity_level = 3          # 静音 warning
            self._sess = _ort.InferenceSession(
                os.path.join(self.model_dir, self.ONNX_FILE),
                sess_options=opts, providers=["CPUExecutionProvider"])
            self._in_name = self._sess.get_inputs()[0].name
            self._out_name = self._sess.get_outputs()[0].name   # "output" = 95 类 logits
        if self._labels is None:
            with open(os.path.join(self.model_dir, self.DICT_FILE), encoding="utf-8") as f:
                self._labels = json.load(f)
        return self._sess

    def label_dict(self):
        self._ensure()
        return self._labels

    def predict(self, batch2d):
        self._ensure()
        x = np.ascontiguousarray(batch2d, dtype=np.float32)
        if x.ndim == 1:
            x = x[None, :]
        return np.asarray(self._sess.run([self._out_name], {self._in_name: x})[0], dtype=np.float32)


register_backend(SileroOnnxBackend.NAME, lambda model_dir, **kw: SileroOnnxBackend(model_dir))


# --------------------------------------------------------------------------
# 后端：Qwen3-ASR（独立进程 sidecar）
# --------------------------------------------------------------------------
# Qwen 返回的语种名 -> 内部语种码（覆盖它支持的 30 语种 + 22 中文方言的主要项）
QWEN_NAME_TO_CODE = {
    "chinese": "zh", "mandarin": "zh", "chinese (mandarin)": "zh",
    "cantonese": "yue", "cantonese (hong kong accent)": "yue",
    "cantonese (guangdong accent)": "yue",
    "japanese": "ja", "english": "en", "korean": "ko", "spanish": "es",
    "french": "fr", "german": "de", "italian": "it", "portuguese": "pt",
    "russian": "ru", "thai": "th", "vietnamese": "vi", "indonesian": "id",
    "arabic": "ar", "turkish": "tr", "hindi": "hi", "malay": "ms", "dutch": "nl",
    "swedish": "sv", "danish": "da", "finnish": "fi", "polish": "pl",
    "czech": "cs", "filipino": "fil", "persian": "fa", "greek": "el",
    "hungarian": "hu", "macedonian": "mk", "romanian": "ro",
}
# 中文方言名 -> zh（本产品它们共用中文预设）
for _d in ("anhui", "dongbei", "fujian", "gansu", "guizhou", "hebei", "henan",
           "hubei", "hunan", "jiangxi", "ningxia", "shandong", "shaanxi",
           "shanxi", "sichuan", "tianjin", "yunnan", "zhejiang",
           "wu language", "wu", "minnan language", "minnan"):
    QWEN_NAME_TO_CODE[_d] = "zh"

ENV_QWEN_PYTHON = "TS_QWEN_PYTHON"
ENV_QWEN_RUNNER = "TS_QWEN_RUNNER"
ENV_QWEN_MODEL = "TS_QWEN_MODEL"
ENV_QWEN_THREADS = "TS_QWEN_THREADS"


class QwenAsrSidecarBackend(object):
    """Qwen3-ASR 语种识别后端（**独立进程 sidecar**）。

    为什么必须是 sidecar
    --------------------
    1. `qwen-asr` 的源码用了 PEP 604（`X | Y`），**Python 3.9 运行时报错**
       —— 与本项目 `mt3_infer` 当年踩的坑同类；
    2. 主程序环境是 Python 3.9 + torch 2.8.0+cpu（basic_pitch / demucs 依赖它），
       而 Qwen3-ASR 需要 Python ≥3.10 + 另一套 torch → **不能污染主环境**。

    所以本后端用 `subprocess` 调 `lang_id_qwen_runner.py`（跑在专用 venv 里），
    通过 stdin/stdout 交换 JSON。主程序侧只依赖标准库。

    与 Silero 后端的差异（重要）
    ----------------------------
    - **不提供 `predict()`**（不做逐窗前向），只提供 `detect_segments()`
      → `LanguageDetector.classify_windows()` 会自动走分段路径；
    - **Qwen 不暴露语种概率**（语种是 ASR 的副产物），故 `prob` 一律记 1.0，
      真正的取舍交给主进程的「时长 × 响度」加权表决与能量门；
      这也意味着 `TS_LANG_MIN_PROB` 对本后端无效。
    """

    NAME = "qwen3asr"

    def __init__(self, model_dir=None, python_exe=None, runner=None, threads=None):
        # 模型目录：env → exe 同目录 → 打包内含 → 脚本目录
        # （**故意不复用上层的通用 model_dir**：Silero 与 Qwen 的模型不在同一个目录）
        self.model_dir = (os.environ.get(ENV_QWEN_MODEL) or model_dir
                          or resolve_resource(os.path.join("lang_id_models", "Qwen3-ASR-0.6B"),
                                              ENV_QWEN_MODEL))
        # 专用解释器与 runner：都允许放在 exe 旁边（大权重/venv 外挂的既有约定）
        self.python_exe = (os.environ.get(ENV_QWEN_PYTHON) or python_exe
                           or resolve_resource(os.path.join("lang_id_venv314", "Scripts", "python.exe"),
                                               ENV_QWEN_PYTHON))
        self.runner = (os.environ.get(ENV_QWEN_RUNNER) or runner
                       or resolve_resource("lang_id_qwen_runner.py", ENV_QWEN_RUNNER))
        self.threads = int(threads if threads is not None
                           else (os.environ.get(ENV_QWEN_THREADS) or 0))
        self.reason = ""
        self.last_meta = {}

    # ---- 可用性 ----
    def available(self):
        for p, what in ((self.python_exe, "Qwen 专用解释器"),
                        (self.runner, "runner 脚本")):
            if not os.path.isfile(p):
                self.reason = "%s不存在：%s" % (what, p)
                return False
        if not os.path.isdir(self.model_dir):
            self.reason = "模型目录不存在：%s" % self.model_dir
            return False
        # 必须是**Qwen 自己的**模型目录：曾因上层把通用 model_dir（lang_id_models/）
        # 透传下来，导致 runner 拿着 Silero 的目录去 from_pretrained 而静默失败。
        if not os.path.isfile(os.path.join(self.model_dir, "config.json")):
            self.reason = ("模型目录里没有 config.json（不是 Qwen3-ASR 权重目录）：%s"
                           % self.model_dir)
            return False
        return True

    def label_dict(self):
        return {}

    # 注意：**故意不定义 `predict()`** —— 本后端不做逐窗前向。
    # `LanguageDetector.classify_array()` 用 `hasattr(backend,'predict')` 判断后端类型，
    # 早期版本在这里放了一个"只负责抛 NotImplementedError"的 predict，
    # 使 hasattr 恒为真 → 贴尾窗那一步炸出去 → 整条语种分割被异常退化。

    # ---- 分段识别 ----
    def detect_segments(self, wav_path, win=10.0, hop=5.0, sr=SR, max_windows=400):
        """跑一次 runner（整曲所有窗在同一进程内）→ 归一化的窗列表。

        返回 [{"t0","t1","language","text"}]；失败返回 []（并写入 self.reason）。
        """
        if not self.available():
            return []
        job = {"model": self.model_dir, "wav": wav_path, "win": float(win),
               "hop": float(hop), "sr": int(sr), "max_windows": int(max_windows),
               "threads": self.threads}
        try:
            import subprocess
            p = subprocess.run([self.python_exe, self.runner],
                               input=json.dumps(job), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=7200)
        except Exception as e:
            self.reason = "runner 调用失败：%s" % str(e)[:160]
            return []
        if not (p.stdout or "").strip():
            self.reason = "runner 无输出（rc=%s）：%s" % (p.returncode, (p.stderr or "")[-160:])
            return []
        try:
            out = json.loads(p.stdout)
        except Exception as e:
            self.reason = "runner 输出解析失败：%s" % str(e)[:120]
            return []
        if not out.get("ok"):
            self.reason = "runner 报错：%s" % str(out.get("error"))[:200]
            return []
        self.last_meta = {k: out.get(k) for k in ("torch", "model_load_s")}
        res = (out.get("results") or [{}])[0]
        _log("Qwen runner：torch=%s 加载 %.1fs 推理 %.1fs（%d 窗）"
             % (self.last_meta.get("torch"), self.last_meta.get("model_load_s") or 0.0,
                res.get("infer_s") or 0.0, len(res.get("windows") or [])))
        return res.get("windows") or []


# 注意：工厂会被以 factory(model_dir) 位置调用；Qwen 后端的模型目录与 Silero **不同**
# （lang_id_models/Qwen3-ASR-0.6B），所以这里**故意忽略**上层透传的通用 model_dir，
# 只用 TS_QWEN_MODEL 或自身默认值 —— 否则会拿 Silero 的目录去 from_pretrained 而静默失败。
register_backend(QwenAsrSidecarBackend.NAME,
                 lambda model_dir=None, **kw: QwenAsrSidecarBackend(**kw))


# --------------------------------------------------------------------------
# 检测器
# --------------------------------------------------------------------------
class LanguageDetector(object):
    """人声语种检测器。所有对外方法在模型不可用时都**安全退化**。"""

    def __init__(self, model_dir=None, backend=None, min_prob=None, min_db=None, enabled=None,
                 candidates=None, min_cand_mass=None):
        self.model_dir = (os.environ.get(ENV_MODEL_DIR) or model_dir or DEFAULT_MODEL_DIR)
        _want = os.environ.get(ENV_BACKEND) or backend or "auto"
        self.backend_name = auto_backend_name() if str(_want).strip().lower() == "auto" else _want
        self.min_prob = _env_float(ENV_MIN_PROB, 0.35) if min_prob is None else float(min_prob)
        self.min_db = _env_float(ENV_MIN_DB, -45.0) if min_db is None else float(min_db)
        self.min_cand_mass = (_env_float(ENV_MIN_CAND_MASS, 0.10) if min_cand_mass is None
                              else float(min_cand_mass))
        # 候选语种白名单：空/None = 不限制（全部 95 类）。
        # 动机（2026-09-20 实测）：95 类全开时，日语术力口会大量投票给 km/bn/my/nn 这类
        # 完全无关的语种，整曲多数票被稀释。产品实际只需要 zh/ja/en/yue 四套预设，
        # 把 softmax 限制在候选集内并重新归一，能显著提高信噪比（见 lang_dev/_eval_lid.py）。
        if candidates is None:
            env_c = os.environ.get(ENV_CANDIDATES, "")
            cand = [c.strip() for c in env_c.split(",") if c.strip()] if env_c else None
        elif isinstance(candidates, str):
            cand = [c.strip() for c in candidates.split(",") if c.strip()]
        else:
            cand = list(candidates)
        self.candidates = set(cand) if cand else None
        if enabled is None:
            enabled = os.environ.get(ENV_ENABLE, "1") != "0"
        self.enabled = bool(enabled)
        self._backend = None
        self._code_of_idx = None
        self.reason = "未初始化"

    # ---- 生命周期 ----
    def available(self):
        """模型/依赖是否就绪。未就绪时 self.reason 说明原因。"""
        if not self.enabled:
            self.reason = "已被 %s=0 关闭" % ENV_ENABLE
            return False
        if self._backend is None:
            factory = _BACKENDS.get(self.backend_name)
            if factory is None:
                self.reason = "未知后端：%s（可用：%s）" % (self.backend_name, ", ".join(list_backends()))
                return False
            try:
                self._backend = factory(self.model_dir)
            except Exception as e:
                self.reason = "后端构造失败：%s" % str(e)[:200]
                return False
        ok = bool(self._backend.available())
        self.reason = "" if ok else getattr(self._backend, "reason", "不可用")
        if ok and self._code_of_idx is None:
            self._build_code_map()
        return ok

    def _build_code_map(self):
        labels = self._backend.label_dict()
        m = {}
        for idx, lab in labels.items():
            head = str(lab).split(",")[0].strip().lower()
            m[int(idx)] = PREFIX_TO_CODE.get(head, head)
        self._code_of_idx = m

    def code_name(self, code):
        return CODE_NAMES.get(code, code)

    # ---- 单窗 / 逐窗 ----
    def classify_array(self, y, sr=SR):
        """对一段波形判语种。返回 dict 或 None（不可用/过短）。

        返回：{"code","name","prob","top":[(code,prob),...],"db","ok"}
        其中 ok=False 表示「过了模型但没过多重门控」→ 语种不可信。
        """
        if not self.available():
            return None
        # 分段型后端（Qwen sidecar）不做单段前向 —— 直接返回 None 而不是抛异常。
        # 踩坑：`segment_by_language()` 末尾补"贴尾窗"时会调本函数，
        # 早期版本让 NotImplementedError 冒出去，导致整条语种分割路径被异常退化掉。
        if not hasattr(self._backend, "predict"):
            return None
        y = np.asarray(y, dtype=np.float32).reshape(-1)
        if len(y) < int(0.25 * sr):                   # 实测下限 0.25s
            return None
        db = _dbfs(y)
        try:
            logits = self._backend.predict(y[None, :])[0]
        except NotImplementedError:
            return None
        p = _softmax(logits)

        cand_mass = 1.0
        if self.candidates:
            # 只在候选语种内比较：把候选类的概率质量取出并重新归一。
            # mass 太小 = 模型确信「这段话不属于我们的任何候选语种」→ 判 unknown。
            kept = [i for i, c in self._code_of_idx.items() if c in self.candidates]
            if not kept:
                return None
            cand_mass = float(np.sum(p[kept]))
            q = np.zeros_like(p)
            q[kept] = p[kept]
            if cand_mass > 1e-9:
                q = q / cand_mass
            p = q

        order = np.argsort(p)[::-1]
        top = [(self._code_of_idx.get(int(i), "?"), float(p[i])) for i in order[:5]]
        best_code, best_p = top[0]
        ok = (best_p >= self.min_prob) and (db >= self.min_db) and (best_code != "unknown")
        if self.candidates and cand_mass < self.min_cand_mass:
            ok = False                        # 候选集外：不硬判
        return {"code": best_code, "name": self.code_name(best_code), "prob": best_p,
                "top": top, "db": db, "cand_mass": round(cand_mass, 4), "ok": bool(ok)}

    def classify_windows(self, wav_path, win=4.0, hop=2.0, sr=SR, max_windows=400):
        """滑窗逐段判语种。返回 [{"t0","t1", ...classify_array 的字段}, ...]。

        两条路径：
          · 后端有 `detect_segments()`（Qwen sidecar）→ 一次性跑完所有窗，再由本进程
            补齐 `db / ok / prob`，从而保留「响度加权」和「能量门」这些主进程侧的逻辑；
          · 否则（Silero）→ 逐窗本地前向。
        """
        if not self.available():
            return []
        if hasattr(self._backend, "detect_segments"):
            raw = self._backend.detect_segments(wav_path, win=win, hop=hop, sr=sr,
                                                max_windows=max_windows)
            if not raw:
                self.reason = getattr(self._backend, "reason", "") or "分段后端无结果"
                return []
            return self._decorate_segments(raw, wav_path, sr)
        y, sr = load_audio_mono16k(wav_path, sr)
        n = len(y)
        wlen, hlen = int(win * sr), int(hop * sr)
        if n < wlen:
            r = self.classify_array(y, sr)
            return [] if r is None else [dict(r, t0=0.0, t1=n / float(sr))]
        out = []
        t = 0
        while t + wlen <= n and len(out) < max_windows:
            r = self.classify_array(y[t:t + wlen], sr)
            if r is not None:
                out.append(dict(r, t0=t / float(sr), t1=(t + wlen) / float(sr)))
            t += hlen
        return out

    def _decorate_segments(self, raw, wav_path, sr=SR):
        """把分段后端（Qwen）的原始窗补成与本地后端同构的窗字典。

        - `code`：按 `QWEN_NAME_TO_CODE` 归一（未知名字 -> unknown）
        - `db`  ：**由主进程自己算**（后端只给时间与语种）→ 响度加权/能量门在本地可复现
        - `prob`：Qwen 不暴露概率 → 记 1.0（真正的取舍交给时长×响度表决与能量门）
        """
        try:
            y, _sr = load_audio_mono16k(wav_path, sr)
        except Exception:
            y = None
        out = []
        for w in raw:
            name = (w.get("language") or "").strip().lower()
            code = QWEN_NAME_TO_CODE.get(name, "unknown")
            if self.candidates and code not in self.candidates:
                code = "unknown"          # 候选集之外 → 视为不可用（与本地后端同语义）
            t0, t1 = float(w.get("t0", 0.0)), float(w.get("t1", 0.0))
            db = -120.0
            if y is not None:
                db = _dbfs(y[int(t0 * sr):int(t1 * sr)])
            ok = (code != "unknown") and (db >= self.min_db)
            out.append({"code": code, "name": self.code_name(code), "prob": 1.0,
                        "top": [(code, 1.0)], "db": db, "cand_mass": 1.0, "ok": bool(ok),
                        "t0": t0, "t1": t1, "text": w.get("text", ""),
                        "raw_language": w.get("language", "")})
        return out

    # ---- 整曲决策 ----
    def detect_song(self, wav_path, win=4.0, hop=2.0, sr=SR, min_voiced=2):
        """整曲语种决策。

        判据（三选一，全部要过门控）：
          1. **时长加权多数票**：只统计 ok=True 的窗，按窗长加权；
          2. 获胜语种必须拿到 >=50% 的**有效票权**（否则 unknown，避免硬猜）；
          3. 有效窗数 >= min_voiced（默认 2），否则 unknown。

        返回 dict（永不为 None，模型不可用时 ok=False）：
          {"ok","reason","code","name","prob","votes":{code:weight},
           "n_windows","n_voiced","windows":[...]}
        """
        res = {"ok": False, "reason": "", "code": "unknown", "name": CODE_NAMES["unknown"],
               "prob": 0.0, "votes": {}, "n_windows": 0, "n_voiced": 0, "windows": []}
        if not self.available():
            res["reason"] = self.reason or "LID 不可用"
            return res
        try:
            wins = self.classify_windows(wav_path, win=win, hop=hop, sr=sr)
        except Exception as e:
            res["reason"] = "读取/推理失败：%s" % str(e)[:200]
            return res
        res["windows"] = wins
        res["n_windows"] = len(wins)
        valid = [w for w in wins if w.get("ok")]
        res["n_voiced"] = len(valid)
        if not valid:
            # 把后端的失败原因带出来，否则"0 个窗"会被误读成"没检测到人声"
            be_reason = getattr(self._backend, "reason", "") if self._backend else ""
            res["reason"] = ("无有效人声窗（全部未过置信/能量门）"
                             + ("；后端：%s" % be_reason if be_reason else ""))
            return res
        votes = {}
        for w in valid:
            votes[w["code"]] = votes.get(w["code"], 0.0) + max(0.1, w["t1"] - w["t0"])
        res["votes"] = {k: round(v, 3) for k, v in sorted(votes.items(), key=lambda kv: -kv[1])}
        total = sum(votes.values())
        best = max(votes.items(), key=lambda kv: kv[1])
        res["prob"] = round(best[1] / total, 4)
        res["code"] = best[0]
        res["name"] = self.code_name(best[0])
        if len(valid) < int(min_voiced):
            res["reason"] = "有效窗不足（%d < %d）" % (len(valid), int(min_voiced))
            return res
        if res["prob"] < 0.5:
            res["reason"] = "无多数语种（最大票权 %.0f%% < 50%%）" % (100 * res["prob"])
            res["code"] = "unknown"
            res["name"] = CODE_NAMES["unknown"]
            return res
        res["ok"] = True
        res["reason"] = "占有效票权 %.0f%%（%d/%d 窗有效）" % (100 * res["prob"], len(valid), len(wins))
        return res


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="人声语种识别（Silero LID lang95 / ONNX）")
    ap.add_argument("--audio", required=True, help="音频路径（人声轨或整曲）")
    ap.add_argument("--win", type=float, default=4.0, help="窗长秒（默认 4）")
    ap.add_argument("--hop", type=float, default=2.0, help="窗移秒（默认 2）")
    ap.add_argument("--min-prob", type=float, default=None, help="置信度门（默认 0.35）")
    ap.add_argument("--min-db", type=float, default=None, help="能量门 dBFS（默认 -45）")
    ap.add_argument("--candidates", default=None,
                    help="候选语种白名单（逗号分隔，如 zh,ja,en,yue）；不传=全部 95 类")
    a = ap.parse_args(argv)

    det = LanguageDetector(min_prob=a.min_prob, min_db=a.min_db, candidates=a.candidates)
    if not det.available():
        print("LID 不可用：%s" % det.reason)
        return 2
    res = det.detect_song(a.audio, win=a.win, hop=a.hop)
    print("=== 逐窗 ===")
    for w in res["windows"]:
        flag = "OK " if w["ok"] else "·  "
        print("  %s %7.2f-%7.2f  %-4s p=%.3f  %5.1f dBFS  top=%s"
              % (flag, w["t0"], w["t1"], w["code"], w["prob"], w["db"],
                 ", ".join("%s:%.2f" % t for t in w["top"][:3])))
    print("=== 整曲决策 ===")
    print("  ok=%s  code=%s(%s)  votes=%s" % (res["ok"], res["code"], res["name"], res["votes"]))
    print("  reason=%s  有效窗 %d/%d" % (res["reason"], res["n_voiced"], res["n_windows"]))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
