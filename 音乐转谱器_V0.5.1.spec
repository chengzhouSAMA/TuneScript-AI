# -*- mode: python ; coding: utf-8 -*-
#
# TuneScript AI V0.5.1 —— 在 V0.5 基础上把「语种识别 / 语种分割扒谱 / 日语罗马音摩拉 /
# 联网取歌词外挂 / 比对 / 强制对齐」打包进来。
#
# 与 V0.5 的差别（其余逐字节相同）：
#   datas       += lang_id_models/（Silero lang95 ONNX 17 MB + 标签表）、lang_id_qwen_runner.py、
#                  pykakasi 的 data/*.db（9.8 MB，缺了汉字就读不出假名）
#   hiddenimports += 本次新增的 8 个模块 + onnxruntime + pykakasi 全部子模块
#
# 为什么 Qwen 权重不进 exe：Qwen3-ASR-0.6B(1.75 GB) + ForcedAligner(1.71 GB) + 独立
# Python 3.14 venv ≈ 4 GB，塞进 onefile 会让每次启动都解压 4 GB。
# 项目既有约定就是**重度权重外挂**（dist/ 下已有 mt3/、piano_btd/），
# 本次沿用：把 lang_id_models/ 与 lang_id_venv314/ 放在 **exe 同目录**即可，
# lang_id.resolve_resource() 会按「env → exe 同目录 → 打包内含 → 脚本目录」自动找。

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

BASE = SPECPATH

# ---- pykakasi：必须带 data/*.db，否则汉字→假名会失效 ----
_pk_data = collect_data_files('pykakasi')
_pk_mods = collect_submodules('pykakasi')

# ---- 本次新增的模块（多数是函数内 import / try-import，静态分析可能漏掉） ----
_new_mods = [
    'lang_id', 'audio_crop', 'lang_modes', 'lang_pipeline',
    'ja_romaji', 'asr_refine', 'lyrics_fetch', 'lyrics_match',
    'onnxruntime', 'onnxruntime.capi', 'onnxruntime.capi._pybind_state',
    'onnxruntime.capi.onnxruntime_inference_collection',
    'jaconv', 'deprecated', 'wrapt',
]

a = Analysis(
    [os.path.join(BASE, 'transcriber_app.py')],
    pathex=[BASE],
    binaries=[],
    datas=[
        ('C:\\Users\\35968\\AppData\\Local\\Packages\\PythonSoftwareFoundation.Python.3.9_qbz5n2kfra8p0\\LocalCache\\local-packages\\Python39\\site-packages\\basic_pitch\\saved_models\\icassp_2022', 'basic_pitch/saved_models/icassp_2022'),
        ('C:\\Users\\35968\\AppData\\Local\\Packages\\PythonSoftwareFoundation.Python.3.9_qbz5n2kfra8p0\\LocalCache\\local-packages\\Python39\\site-packages\\demucs\\remote', 'demucs/remote'),
        ('C:\\Users\\35968\\AppData\\Local\\Packages\\PythonSoftwareFoundation.Python.3.9_qbz5n2kfra8p0\\LocalCache\\local-packages\\Python39\\site-packages\\mt3_infer\\config', 'mt3_infer/config'),
        ('assets/app_icon.png', 'assets'),
        ('assets/app_icon_small.png', 'assets'),
        ('assets/app_icon.ico', 'assets'),
        # ==== V0.5.1 新增 ====
        # ⚠️ 只打**这三个 Silero 文件**，绝不能整目录打 ——
        #    `lang_id_models/` 里还有 Qwen3-ASR-0.6B(1.75 GB) 与
        #    Qwen3-ForcedAligner-0.6B(1.71 GB)，整目录会把 exe 撑到 3.4 GB。
        #    （第一次构建就是这么翻车的：529 MB → 3362 MB。）
        (os.path.join(BASE, 'lang_id_models', 'lang_classifier_95.onnx'), 'lang_id_models'),
        (os.path.join(BASE, 'lang_id_models', 'lang_dict_95.json'), 'lang_id_models'),
        (os.path.join(BASE, 'lang_id_models', 'lang_group_dict_95.json'), 'lang_id_models'),
        (os.path.join(BASE, 'lang_id_qwen_runner.py'), '.'),
    ] + _pk_data,
    hiddenimports=[
        'bilibili', 'requests',
        'demucs.pretrained', 'demucs.apply', 'demucs.htdemucs',
        'demucs.transformer', 'demucs.repitch', 'demucs.states',
        'demucs.spec', 'demucs.wav', 'demucs.demucs', 'demucs.hdemucs',
        'torchaudio', 'soundfile', 'audioread', 'einops', 'julius',
        'omegaconf', 'dora_search', 'openunmix',
        'Crypto.Cipher.AES', 'Crypto.Cipher._mode_ecb',
        'tkinterdnd2', 'tkinterdnd2.TkinterDnD', 'tkinterdnd2.DND_FILES',
        'mt3_infer', 'mt3_infer.api', 'mt3_infer.base',
        'mt3_infer.adapters', 'mt3_infer.adapters.mr_mt3',
        'mt3_infer.adapters.mt3_pytorch', 'mt3_infer.adapters.yourmt3',
        'mt3_infer.adapters.vocab_utils',
        'mt3_infer.utils.download', 'mt3_infer.utils.midi',
        'mt3_infer.models.mr_mt3', 'mt3_infer.models.mr_mt3.t5',
        'mt3_infer.models.mt3_pytorch.t5',
        'mt3_infer.models.mt3_pytorch.contrib.event_codec',
        'mt3_infer.models.mt3_pytorch.contrib.note_sequences',
        'mt3_infer.models.mt3_pytorch.contrib.spectrograms_torch',
        'mt3_infer.models.mt3_pytorch.contrib.vocabularies',
        'mido', 'transformers', 'huggingface_hub', 'tokenizers',
        'safetensors', 'absl', 'absl.flags', 'tqdm',
        'piano_transcription_inference',
        'piano_transcription_inference.inference',
        'piano_transcription_inference.models',
        'piano_transcription_inference.pytorch_utils',
        'piano_transcription_inference.utilities',
        'piano_transcription_inference.config',
    ] + _new_mods + _pk_mods,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TuneScript AI V0.5.1',
    icon='assets/app_icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
