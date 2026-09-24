# -*- coding: utf-8 -*-
"""音乐转谱器 —— 识别音频并生成钢琴 MIDI 与五线谱。

管线：
  音频文件 -> (ffmpeg 解码为 22050Hz 单声道 WAV，非 WAV/FLAC/OGG 时需要)
          -> [Demucs 四轨分离: 人声/鼓/贝斯/其他(与识音 shiyin.notalabs.cn
              同原理：深度学习频谱掩码源分离)]
          -> Basic Pitch (ONNX, CPU) 逐轨识别音符
          -> (可选) MT3 智能识别增强：多声部模型直接识别全曲和弦，
             能还原 Basic Pitch 做不出的完整和弦/多声部
          -> 融合修改(所有声部全部用上)：人声=主旋律(右)，人声空档
             (前奏/间奏/尾奏)用和声轨最高音线补旋律线；贝斯=左手低音线；
             和声轨抽稀后进左手(每0.35s一个和声点)；鼓点与贝斯对齐者
             强化贝斯起音(保留律动)；
             碎音合并/legato/伴奏释放/力度分层/延音踏板 → 可弹钢琴 MIDI
          -> MuseScore CLI 渲染：五线谱 PDF + 钢琴 WAV (MS Basic 音源)

分离失败或人声轨音符过少(纯器乐)时自动回退“整体分析”
(同时发声的最高音=旋律的启发式分手)，保证任何输入都能出谱。

技术要点：
  - MuseScore 是 GUI 程序，必须用 subprocess 等待其退出；
  - MuseScore 转换完成后退出时可能崩溃(退出码非 0)，但产物有效，
    因此不以退出码判成败，而是校验产物本身(%PDF/RIFF 魔数)；
  - 渲染前删除旧产物并加 -f 强制覆盖，避免 MuseScore 拒绝覆盖/改名
    造成的“渲染成功但找不到文件”假失败；
  - 五线谱 PDF 是硬保证：先渲染 PDF 再渲染 WAV，PDF 失败自动降级
    (重试 → 无延音线版 → 左右手分谱)，绝不允许“有曲子没谱”；
  - 乐谱量化用音频节拍跟踪(librosa)的 BPM，不用 MIDI 默认 120，
    避免小节线与实际节拍错位；谱面最小音符为八分音符；
  - 打包后用 sys._MEIPASS 定位内置的 nmp.onnx 模型；
    Demucs 模型首次运行自动下载(~80MB，缓存在用户目录)。
"""
import os
import sys
import json
import time
import queue
import struct
import argparse
import subprocess
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# 工具定位
# ---------------------------------------------------------------------------

def app_dir():
    """打包后与源码运行时都能定位到 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """PyInstaller 单文件 exe 运行时，内置资源解压到的临时目录。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def find_musescore():
    """定位 MuseScore4.exe。优先常见安装路径，其次注册表/PATH。"""
    candidates = [
        r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
        r"C:\Program Files (x86)\MuseScore 4\bin\MuseScore4.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\MuseScore 4\bin\MuseScore4.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    # 试 PATH
    try:
        import shutil
        p = shutil.which("MuseScore4")
        if p:
            return p
    except Exception:
        pass
    return None


def find_ffmpeg():
    """定位 ffmpeg.exe：优先 exe 同目录(便于外挂提供 MP3 支持)，其次 PATH。"""
    siblings = [
        os.path.join(app_dir(), "ffmpeg.exe"),
        os.path.join(app_dir(), "ffmpeg", "ffmpeg.exe"),
    ]
    for s in siblings:
        if os.path.isfile(s):
            return s
    try:
        import shutil
        p = shutil.which("ffmpeg")
        if p:
            return p
    except Exception:
        pass
    return None


def find_model():
    """定位 Basic Pitch 的 nmp.onnx 模型(打包后随包解压)。"""
    import basic_pitch
    candidates = [
        os.path.join(bundle_dir(), "basic_pitch", "saved_models", "icassp_2022", "nmp.onnx"),
        os.path.join(os.path.dirname(basic_pitch.__file__), "saved_models", "icassp_2022", "nmp.onnx"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def find_mt3_checkpoint():
    """定位 MT3 智能识别权重 (mr_mt3/mt3.pth)。

    打包后权重放在 exe 同目录的 mt3/mt3.pth(外挂,便于更新);
    源码运行则用 .mt3_checkpoints/mr_mt3/mt3.pth。找不到返回 None,
    表示“AI 智能识别增强”不可用,调用方自动退回 Basic Pitch。
    """
    candidates = [
        os.path.join(app_dir(), "mt3", "mr_mt3", "mt3.pth"),
        os.path.join(app_dir(), "mt3", "mt3.pth"),
        os.path.join(bundle_dir(), "mt3", "mt3.pth"),
        os.path.join(os.getcwd(), ".mt3_checkpoints", "mr_mt3", "mt3.pth"),
        os.path.join(os.path.expanduser("~"), ".mt3_checkpoints", "mr_mt3", "mt3.pth"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


# 模块级缓存：MT3 模型约 176MB，避免每个文件重复加载
_MT3_MODEL_CACHE = {"path": None, "model": None}


def _mt3_model(checkpoint):
    """懒加载 MT3 模型(全局缓存)。失败抛异常,由调用方回退。"""
    if _MT3_MODEL_CACHE["model"] is not None and _MT3_MODEL_CACHE["path"] == checkpoint:
        return _MT3_MODEL_CACHE["model"]
    from mt3_infer import load_model
    # 权重由 find_mt3_checkpoint 指定，冻结打包后不要联网下载
    model = load_model("mr_mt3", device="cpu", checkpoint_path=checkpoint,
                       auto_download=False)
    _MT3_MODEL_CACHE["path"] = checkpoint
    _MT3_MODEL_CACHE["model"] = model
    return model


# 模块级缓存：ByteDance 钢琴转录(CRNN，约 165MB)——比 MT3 快约 6 倍，
# 专用于钢琴/和弦识别，适合识别伴奏轨(其他轨)的和弦部分
_BTD_MODEL_CACHE = {"path": None, "model": None}


def find_btd_checkpoint():
    """定位 ByteDance 钢琴转录权重 (note_F1=0.9677_pedal_F1=0.9186.pth)。

    打包后权重放在 exe 同目录的 piano_btd/ 下(外挂,便于更新);
    源码运行则用默认的 ~/piano_transcription_inference_data/。找不到返回 None。
    """
    fname = "note_F1=0.9677_pedal_F1=0.9186.pth"
    candidates = [
        os.path.join(app_dir(), "piano_btd", fname),
        os.path.join(bundle_dir(), "piano_btd", fname),
        os.path.join(os.path.expanduser("~"), "piano_transcription_inference_data", fname),
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.path.getsize(c) > 1.6e8:
            return c
    return None


def _btd_model(checkpoint=None):
    """懒加载 ByteDance 钢琴转录模型(全局缓存，CPU)。失败抛异常。"""
    if _BTD_MODEL_CACHE["model"] is not None and _BTD_MODEL_CACHE["path"] == checkpoint:
        return _BTD_MODEL_CACHE["model"]
    from piano_transcription_inference import PianoTranscription
    model = PianoTranscription(device="cpu", checkpoint_path=checkpoint)
    _BTD_MODEL_CACHE["path"] = checkpoint
    _BTD_MODEL_CACHE["model"] = model
    return model


def _btd_track_notes(wav_path, model, progress, label):
    """用 ByteDance 钢琴转录模型识别一个分离轨，返回 (start,end,pitch,vel)。

    该模型是 CNN(Onsets & Frames 改进)，CPU 上接近实时(约 6 倍快于 MT3)，
    专为钢琴/和弦设计——适合识别伴奏轨的完整和弦(多音高)。
    返回 (start,end,pitch,velocity)，时间单位秒。
    """
    import librosa
    from piano_transcription_inference import sample_rate
    y, sr = librosa.load(wav_path, sr=sample_rate, mono=True)
    progress(f"AI 正在识别{label}(和弦增强)…")
    d = model.transcribe(y, None)
    notes = []
    for ev in d.get("est_note_events", []):
        s = float(ev["onset_time"])
        e = float(ev["offset_time"])
        if e > s + 1e-4:
            notes.append((s, e, int(ev["midi_note"]), int(ev["velocity"])))
    notes.sort(key=lambda x: (x[0], x[2]))
    return notes


# 参与「左手伴奏合并」的轨（顺序不影响结果，只影响日志标签）。
# 2026-09-13 实测（反乌托邦 60-80s，同一转谱器/渲染，见 项目备忘2.0.md）：
#   A 现状「按响度选最响单轨(guitar)」  sim = 0.7915
#   B 「合并 piano+guitar+other」        sim = 0.7956
#   C 「合并全部非人声轨」                sim = 0.7996
# 单调递增 -> 合并优于单轨。默认取 piano/guitar/other：
# 【2026-09-19 用户要求】贝斯不参与：不再并入伴奏合并轨，也不再单独进左手。
# 贝斯轨依然会被 Demucs 分离出来（文件名/界面照常显示茎干轨），只是完全不
# 参与转谱。旧的做法是把 bass 一起相加进合并轨(见上方实验 C)——低频会污染
# 和弦识别、并让左手出现不需要的低音线，故明确摘除。
# 鼓是打击噪声、对钢琴转录是干扰，同样排除(实验 C 含鼓仅再高 0.004，在噪声内)。
# 想改参与合并的轨：改这个常量，或设环境变量 TS_ACCOMP_STEMS=piano,guitar
# （env 覆盖能力保留：显式写上 bass 才会重新并入合并轨，但那不再是默认行为。）
ACCOMP_STEMS = tuple(
    x.strip() for x in os.environ.get("TS_ACCOMP_STEMS",
                                      "piano,guitar,other").split(",") if x.strip())

# 「近乎空轨」门槛：RMS 低于【最强参与轨】这个比例的轨不参与合并。
# Demucs 在非钢琴曲上常把 piano 轨分得几乎全空（反乌托邦 piano RMS 只有
# 最强轨的 0.6%、other 1.4%）：合进来不提供任何内容，只会白占峰值余量。
# 想改：设环境变量 TS_ACCOMP_MIN_RATIO=0.02（0 表示不跳过任何轨）。
ACCOMP_MIN_RATIO = float(os.environ.get("TS_ACCOMP_MIN_RATIO", "0.02"))


def _merge_accomp_stems(stems, progress, out_dir=None):
    """把多条件奏轨【相加合并】成一条 wav，返回 (标签, 路径)。

    为什么合并而不是分别识别：
      和弦识别模型看到的是完整和声织体，"按响度选最响一轨"会丢掉其它乐器
      的和声（电子/术力口曲尤其明显）。相加后和声更完整，而且合并成一条
      只需跑一次识别，不增加耗时。

    两条纪律（2026-09-19 全曲回归后补，实测见 回归验收/全曲回归验收报告.md 第十节）：
      1) 【跳过近乎空轨】RMS 低于最强参与轨 ACCOMP_MIN_RATIO 倍的轨不参与；
      2) 【电平匹配】合并后整体 RMS 对齐到最强参与轨的 RMS（只降不升），
         并且仍以 0.99 峰值为上限防削波。理由：识别模型对输入电平敏感
         （实测纯 ±1 dB 增益就能让 sim 摆动约 0.01），电平不该和"内容"
         混成同一个自变量 —— 否则 A/B 比较根本说不清是内容变了还是电平变了。
      3) 默认【不含 drums】：鼓是打击噪声、对钢琴转录是干扰（附-7 实测含鼓
         仅再高 0.004，在噪声内）。要含鼓请显式设 TS_ACCOMP_STEMS。
      4) 默认【不含 bass】：贝斯不参与（2026-09-19 用户要求）。贝斯轨照常被
         分离，但不并入合并轨、也不单独进左手；低频既污染和弦识别，也不是
         本次要还原的内容。要重新并入请显式设 TS_ACCOMP_STEMS 并写上 bass。

    任何失败都回退到旧行为 _pick_main_accomp（分离是增强，绝不影响出谱）。
    """
    import numpy as np
    import soundfile as sf

    avail = []
    for k in ACCOMP_STEMS:
        p = stems.get(k)
        if p and os.path.isfile(p):
            avail.append((k, p))
    if not avail:
        return None, None
    if len(avail) == 1:
        progress(f"伴奏轨只有 {avail[0][0]} 一条，直接使用。")
        return avail[0][0], avail[0][1]
    try:
        ys, sr, n_ch, n = [], None, 1, 0
        for k, p in avail:
            y, _sr = sf.read(p, dtype="float32", always_2d=True)
            sr = _sr
            n_ch = max(n_ch, y.shape[1])
            n = max(n, y.shape[0])
            ys.append((k, p, y, float(np.sqrt(np.mean(y ** 2)))))
        # 1) 跳过近乎空的轨（不提供内容，只占峰值余量）
        loud_rms = max(t[3] for t in ys)
        thr = loud_rms * ACCOMP_MIN_RATIO
        kept = [t for t in ys if t[3] >= thr]
        dropped = [t[0] for t in ys if t[3] < thr]
        if dropped:
            progress("跳过近乎空的伴奏轨（%s，低于最强轨的 %.1f%%）。"
                     % ("+".join(dropped), ACCOMP_MIN_RATIO * 100))
        if not kept:
            progress("伴奏轨都不足以参与合并，回退最响单轨。")
            return _pick_main_accomp(stems)
        if len(kept) == 1:
            progress(f"合并后只剩 {kept[0][0]} 一条有效伴奏轨，直接使用。")
            return kept[0][0], kept[0][1]
        # 2) 相加后【电平匹配】到最强参与轨（只降不升）+ 峰值安全
        acc = np.zeros((n, n_ch), dtype="float32")
        for _k, _p, y, _r in kept:
            acc[: y.shape[0], : y.shape[1]] += y
        peak = float(np.abs(acc).max())
        rms = float(np.sqrt(np.mean(acc ** 2)))
        target_name, target_rms = max(((t[0], t[3]) for t in kept), key=lambda x: x[1])
        gain = 1.0
        if rms > 0:
            gain = min(1.0, target_rms / rms)     # 只降不升，避免把电平推高
        if peak * gain > 0.99:                    # 合并后可能超 [-1,1]，防削波
            gain = 0.99 / peak
        if abs(gain - 1.0) > 1e-6:
            acc *= gain
        # t9(2026-09-20)：合并中间产物**不再写进传入 stem 所在目录**。
        # 旧行为写到 os.path.dirname(第一条参与轨)，会把 `*_accomp_merged.wav`
        # 落在**源素材/基线目录**里（`转谱验证/<key>/` 曾因此被写入一个中间文件；
        # 虽然文件名恒带 `_accomp_merged` 后缀、不会覆盖六轨冻结节拍，
        # 中间产物落在输入目录本身就是污染）。现在优先写到调用方给的
        # out_dir（生产路径 = run_pipeline 的产物目录），缺省才退回系统临时目录。
        # 回退：显式传入想让它落地的目录即可；行为与本改动前一致的做法是
        # 传 os.path.dirname(第一条参与轨)。
        first_path = kept[0][1]
        fn = os.path.splitext(os.path.basename(first_path))[0]
        b = fn[: -(len(kept[0][0]) + 1)] if fn.endswith("_" + kept[0][0]) else "track"
        if out_dir:
            d = out_dir
            try:
                os.makedirs(d, exist_ok=True)
            except OSError:
                pass
        else:
            import tempfile
            d = tempfile.mkdtemp(prefix="ts_accomp_merge_")
        out = os.path.join(d, f"{b}_accomp_merged.wav")
        sf.write(out, acc, sr, subtype="PCM_16")
        label = "+".join(t[0] for t in kept)
        progress(f"已合并伴奏轨（{label}）用于和弦识别；"
                 f"电平对齐到最强轨 {target_name}（{20.0 * np.log10(max(gain, 1e-9)):+.2f} dB）。")
        return label, out
    except Exception as e:
        progress(f"伴奏合并不可用({type(e).__name__})，回退最响单轨。")
        return _pick_main_accomp(stems)


def _pick_main_accomp(stems):
    """从吉他/钢琴/其他轨中按分离响度(分贝)选出主要伴奏轨。

    各轨分离后的 RMS 响度反映该乐器在编曲中的主次；把最响的
    一轨作为左手主要伴奏(和弦识别只用它)，伴奏主次分明不混杂。
    返回 (声部名, wav路径)；全失败返回 (None, None)。
    """
    import numpy as np
    import soundfile as sf
    best_name, best_path, best_db = None, None, -999.0
    for k in ("guitar", "piano", "other"):
        p = stems.get(k)
        if not p or not os.path.isfile(p):
            continue
        try:
            y, _sr = sf.read(p, dtype="float32", always_2d=True)
            rms = float(np.sqrt(np.mean(y ** 2)))
            db = 20.0 * np.log10(rms + 1e-12)
            if db > best_db:
                best_db, best_name, best_path = db, k, p
        except Exception:
            continue
    return best_name, best_path


# ---------------------------------------------------------------------------
# 管线函数
# ---------------------------------------------------------------------------

NATIVE_EXT = {".wav", ".flac", ".ogg"}  # soundfile 可直读


def decrypt_ncm(ncm_path, out_dir, progress):
    """解密网易云音乐 .ncm 加密文件，返回解密后音频路径(flac/mp3 等)。

    网易云下载的 .ncm 是 AES-ECB 加密容器：头部含加密的 AES 密钥与
    元数据(含原始格式)，其后是加密音频。解密后原样写出，交给后续
    管线(ffmpeg/直读)继续处理。
    """
    from Crypto.Cipher import AES
    import base64

    CORE_KEY_NEW = b'hzHRAmso5kInbaxW'                      # 新版 key 区 AES 密钥
    CORE_KEY_OLD = bytes.fromhex('687A485241534D41B9A2F2A1F2A2F2A1')  # 旧版
    META_KEY = bytes.fromhex('2331346C6A6B5F215C5D2630553C2728')
    KEY_PREFIX = b'neteasecloudmusic'
    META_PLAIN_PREFIX = b"163 key(Don't modify):"
    META_PREFIX = b'music:'

    def _u32(b):
        return struct.unpack('<I', b)[0]

    def _strip_pkcs7(data):
        pad = data[-1] if data else 0
        if 0 < pad <= 16 and data[-pad:] == bytes([pad]) * pad:
            return data[:-pad]
        return data

    def _ncm_keystream(key):
        """NCM 流密码：标准 RC4 KSA + 固定索引 PRGA，每 256 字节一周期"""
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) & 0xff
            s[i], s[j] = s[j], s[i]
        ks = bytearray(256)
        for i in range(256):
            j = (i + 1) & 0xff
            a = s[j]
            b = s[(a + j) & 0xff]
            ks[i] = s[(a + b) & 0xff]
        return bytes(ks)

    def _xor_stream(data, keystream):
        if not data:
            return b''
        period = len(keystream)
        ks_long = (keystream * (len(data) // period + 1))[:len(data)]
        a = int.from_bytes(data, 'little')
        b = int.from_bytes(ks_long, 'little')
        return (a ^ b).to_bytes(len(data), 'little')

    def _sniff_ext(audio_head):
        if audio_head[:4] == b'fLaC':
            return 'flac'
        if audio_head[:3] == b'ID3':
            return 'mp3'
        if audio_head[4:8] == b'ftyp':
            return 'm4a'
        return None

    with open(ncm_path, 'rb') as f:
        raw = f.read()
    if raw[:8] != b'CTENFDAM':
        raise ValueError('不是有效的 NCM 文件')

    off = 10
    key_len = _u32(raw[off:off + 4]); off += 4
    key_data = raw[off:off + key_len]; off += key_len

    # 识别加密版本（新版先试）
    version = None
    dec_key = None
    try:
        d = AES.new(CORE_KEY_NEW, AES.MODE_ECB).decrypt(bytes(b ^ 0x64 for b in key_data))
        if d[:17] == KEY_PREFIX:
            version = 'new'
            dec_key = d
    except Exception:
        pass
    if version is None:
        try:
            d = AES.new(CORE_KEY_OLD, AES.MODE_ECB).decrypt(key_data)
            if d[:17] == KEY_PREFIX:
                version = 'old'
                dec_key = d
        except Exception:
            pass
    if version is None:
        raise ValueError('无法识别的 NCM 加密版本')

    # 元数据
    meta_len = _u32(raw[off:off + 4]); off += 4
    meta_data = raw[off:off + meta_len]; off += meta_len
    ext = None
    if meta_len > 0:
        if version == 'new':
            xored = bytes(b ^ 0x63 for b in meta_data)
            b64 = xored[len(META_PLAIN_PREFIX):] if xored.startswith(META_PLAIN_PREFIX) else xored
            body = _strip_pkcs7(AES.new(META_KEY, AES.MODE_ECB).decrypt(base64.b64decode(b64)))
        else:
            body = AES.new(META_KEY, AES.MODE_ECB).decrypt(meta_data).rstrip(b'\x00')
        if body.startswith(META_PREFIX):
            body = body[len(META_PREFIX):]
        body = body.rstrip(b'\x00')
        try:
            ext = json.loads(body.decode('utf-8')).get('format')
        except Exception:
            pass

    # 封面
    off += 5
    cover_frame_len = _u32(raw[off:off + 4]); off += 4
    n = _u32(raw[off:off + 4]); off += 4
    if n > 0:
        off += n
        off += max(cover_frame_len - n, 0)
    else:
        off += cover_frame_len
    audio = raw[off:]

    # 音频解密
    if version == 'new':
        rc4_key = _strip_pkcs7(dec_key)[17:]
        decrypted = _xor_stream(audio, _ncm_keystream(rc4_key))
    else:
        if len(audio) % 16 != 0:
            raise ValueError('音频数据长度不是 16 的倍数，文件可能损坏')
        audio_key = None
        try:
            audio_key = base64.b64decode(_strip_pkcs7(dec_key)[17:])[:16]
        except Exception:
            pass
        if not audio_key or len(audio_key) != 16:
            audio_key = dec_key[:16]
        decrypted = _strip_pkcs7(AES.new(audio_key, AES.MODE_ECB).decrypt(audio))

    if not ext:
        ext = _sniff_ext(decrypted[:8]) or 'bin'
    base = os.path.splitext(os.path.basename(ncm_path))[0]
    out_path = os.path.join(out_dir, f"{base}.{ext}")
    with open(out_path, 'wb') as out:
        out.write(decrypted)
    progress(f"已解密 NCM → {ext.upper()}。")
    return out_path


def decode_to_wav(audio_path, ffmpeg, out_wav, progress):
    """非 WAV/FLAC/OGG 时用 ffmpeg 统一转成 22050Hz 单声道 16bit WAV。"""
    ext = os.path.splitext(audio_path)[1].lower()
    if ext in NATIVE_EXT:
        progress("音频为 WAV/FLAC/OGG，无需解码。")
        return audio_path
    if not ffmpeg:
        raise RuntimeError(
            "该音频格式需要 ffmpeg 才能解码。请在程序同目录放置 ffmpeg.exe，"
            "或先把音频转换成 WAV/FLAC/OGG。"
        )
    progress("使用 ffmpeg 解码音频…")
    cmd = [
        ffmpeg, "-y", "-i", audio_path,
        "-ac", "1", "-ar", "22050", "-sample_fmt", "s16", out_wav,
    ]
    # errors="replace"：ffmpeg 的 stderr 可能含 GBK/UTF-8 混合字节，
    # 默认编码解码会抛 UnicodeDecodeError（在读取线程中崩溃）。
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    if not os.path.isfile(out_wav) or os.path.getsize(out_wav) == 0:
        raise RuntimeError("ffmpeg 解码失败：" + (p.stderr or "").strip()[-400:])
    return out_wav


def transcribe_notes(wav_path, model, progress, label="音符", min_len=150):
    """Basic Pitch 识别一个音轨，返回全部音符 (start, end, pitch, velocity)。

    model 是已加载的 Basic Pitch Model(避免每条音轨重复加载)。
    min_len 是最小音符长度(ms)：人声轨用更小值，
    因为日语等多音节语言一字一音、音节短促，滤太狠会丢音节。
    """
    from basic_pitch.inference import predict
    progress(f"AI 正在识别{label}(乐曲越长越久，请耐心等待)…")
    # 【t3-B 已弃用 · 2026-09-20】曾试过显式传 frame_threshold（Basic Pitch 默认 0.30）：
    #   · 「全局 0.35」= arm E：monitoring 音符 841→665、成本 0.204→0.213、
    #     出厂 sim 0.8122→0.8082（相对基线 **−0.0072，破 −0.005 线**），
    #     并连带使 t6 的人声接入被保底回滚（0.220>0.218）→ sim 与连续性两轴同时坏。**已回退**；
    #   · 「仅人声轨 0.35」= arm F：同源配对实测（同一源码快照、只切 `TS_BP_FRAME_THR_VOCAL`
    #     0.35↔0.30，产物由 verifier 独立量）**在 monitoring 上 4 个指标更差**：
    #     `at` 76.54→76.35（**−0.19pp**）、`rh` 59.71→59.50（−0.21pp）、
    #     音级吻合 63.08%→62.47%（**−0.61pp**，而它本该改善这一轴）、八度错位 1→1（**无改善**）；
    #     唯一为正的是 sim 0.8121519→0.8140011（+0.0018，**低于 ±0.01 噪声地板**）；
    #   · 另：直接分类两臂差异音符显示它只影响 **34/4917 音（0.7%）**，产物端统计结构性反映不出。
    # ⇒ **两处都回退**（全局 + 人声轨）。T2 的 7 首 A/B 在人声轨口径上确有效果，但落到产物上不成立。
    # 取证：回归验收/术力口与人声连续性优化报告.md §三之四、§三之五。
    _model_output, midi_data, _notes = predict(
        wav_path, model_or_model_path=model,
        onset_threshold=0.45, minimum_note_length=min_len,
    )
    notes = []
    for inst in midi_data.instruments:
        for n in inst.notes:
            notes.append((n.start, n.end, n.pitch, n.velocity))
    return notes


def transcribe_to_midi(wav_path, model_path, progress):
    """(整体分析回退路径) 混合音频 → 角色启发式分手 → 可弹钢琴 MIDI。

    当音轨分离不可用(未装 demucs/纯器乐/人声轨过少)时使用：
    以“同时发声的最高音=旋律”的启发式代替人声分离。

    返回 (midi_data, left_notes, right_notes, tempo)，
    left/right 是归一化后的左右手音符，供生成大谱表五线谱用。
    """
    from basic_pitch.inference import Model, predict

    progress("加载 AI 识别模型(约 10~15 秒)…")
    model = Model(model_path)

    # onset_threshold 略降低：多保留弱起音/内声部，还原度更高；
    # minimum_note_length 略提高：过滤更短的噪声碎音；
    # 后续的碎音合并与可弹化处理会进一步吸收残余短音。
    _model_output, midi_data, _notes = predict(
        wav_path, model_or_model_path=model,
        onset_threshold=0.45, minimum_note_length=150,
    )
    all_notes = []
    for inst in midi_data.instruments:
        for n in inst.notes:
            all_notes.append((n.start, n.end, n.pitch, n.velocity))

    progress("正在调整为『人能弹』的钢琴谱…")
    melody, accomp = _split_melody_accomp(all_notes)
    out, left, right = fuse_to_piano(melody, accomp)
    tempo = _estimate_tempo(midi_data)
    progress("AI 识别完成，已生成 MIDI。")
    return out, left, right, tempo


def _mido_to_notes(midi, tempo_bpm=120.0):
    """把 MT3 输出的 mido.MidiFile 解析成 (start, end, pitch, velocity) 列表。

    时间单位是秒：ticks -> beats -> 秒(用 ticks_per_beat 与 BPM 换算)。
    单轨多音高(polyphonic)事件被还原成独立的音符起止，供后续分手用。
    """
    tpb = midi.ticks_per_beat or 480
    sec_per_tick = 60.0 / (tempo_bpm * tpb)
    notes = []
    for track in midi.tracks:
        abs_tick = 0
        on = {}  # note -> start_tick
        for msg in track:
            abs_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                on.setdefault(msg.note, abs_tick)
            elif msg.type == "note_off":
                st = on.pop(msg.note, None)
                if st is not None:
                    s = st * sec_per_tick
                    e = abs_tick * sec_per_tick
                    if e > s + 1e-4:
                        notes.append((s, e, msg.note, 80))
    notes.sort(key=lambda x: (x[0], x[2]))
    return notes


def transcribe_mt3(wav_path, checkpoint, progress, model_path=None, max_sec=90.0):
    """MT3 智能识别增强引擎：整个混音 -> polyphonic MIDI -> 可弹钢琴谱。

    相对 Basic Pitch 的优势：能识别完整和弦/多声部(单音色单音高模型
    做不到)，对伴奏/多乐器乐曲还原更丰富。代价是慢(CPU 约 6 倍实时)。

    限时优化：MT3 只识别前 max_sec 秒(默认 90 秒)，超出部分用 Basic
    Pitch 补全(model_path 提供时)——长歌提速 3~4 倍，MT3 耗时封顶。

    返回 (midi_data, left, right, tempo)；失败抛异常，由调用方回退。
    """
    import librosa
    from mt3_infer import load_model

    progress("加载 MT3 智能识别引擎(约 176MB 模型，稍候)…")
    model = _mt3_model(checkpoint)

    y, sr = librosa.load(wav_path, sr=16000, mono=True)
    truncated = len(y) / sr > max_sec
    if truncated:
        y = y[: int(max_sec * sr)]
        progress(f"MT3 限时识别前 {max_sec:.0f} 秒(长歌提速)，后段用 Basic Pitch 补全…")
    else:
        progress("MT3 正在识别全曲和弦与声部(CPU 较慢，请耐心等待)…")
    midi = model.transcribe(y, sr=sr)

    notes = _mido_to_notes(midi)
    if len(notes) < 10:
        raise RuntimeError("MT3 未识别出足够音符，退回 Basic Pitch。")
    progress(f"MT3 识别出 {len(notes)} 个音符，正在整理为可弹钢琴谱…")

    if truncated and model_path:
        from basic_pitch.inference import Model as BpModel
        bp_model = BpModel(model_path)
        bp_notes = transcribe_notes(wav_path, bp_model, progress,
                                    label="后段音符", min_len=127)
        notes = notes + [n for n in bp_notes if n[0] >= max_sec]

    melody, accomp = _split_melody_accomp(notes)
    out, left, right = fuse_to_piano(melody, accomp)
    tempo = _estimate_tempo_from_midi_time(midi, notes)
    return out, left, right, tempo


def _mt3_track_notes(wav_path, model, progress, label, max_sec=None):
    """对单个分离轨用 MT3 转录，返回 polyphonic 音符 (start,end,pitch,vel)。

    MT3 是单轨多音高模型：同一时刻可能输出多个音高(旋律+泛音+内声部)。
    分离轨(尤其人声/贝斯)相对干净，多音高主要来自泛音/轻微串音，
    交由调用方按“线”或“和弦”整理。

    max_sec 非 None 时只识别前 max_sec 秒(MT3 CPU 慢，限时可大幅提速)；
    返回 (notes, truncated)，truncated=True 表示音频超过限时被截断，
    调用方需用 Basic Pitch 补 90 秒之后的部分。
    """
    import librosa
    y, sr = librosa.load(wav_path, sr=16000, mono=True)
    truncated = False
    if max_sec is not None and len(y) / sr > max_sec:
        y = y[: int(max_sec * sr)]
        truncated = True
        progress(f"MT3 限时识别{label}前 {max_sec:.0f} 秒(长歌提速)…")
    else:
        progress(f"MT3 正在识别{label}…")
    midi = model.transcribe(y, sr=sr)
    return _mido_to_notes(midi), truncated


# 【已停用 · 2026-09-19】_bass_line() 原为「从 MT3 输出里提取最低音线给贝斯轨用」。
# 【自 2026-09-19 起不再调用，保留仅为回退】用户要求贝斯不参与转谱后，本函数在
# 全流程已无任何调用点（0 引用，AST 级核验见 回归验收/_prove_bass_unreachable.py）。
# 保留函数体只是为了可回退/可查阅，不参与出谱。
# 需要恢复贝斯路径时，请对照 备份\pre_vocal_optim_20260919_231756\transcriber_app.py。
def _bass_line(notes, window=0.12):
    """从 MT3 多音高里提取最低音线(贝斯轨用)。【已停用：全流程零调用，见上方说明】

    每个起音簇取最低音(贝斯根音优先)，聚成一条低音线。
    """
    if not notes:
        return []
    notes = sorted(notes, key=lambda x: x[0])
    line = []
    i = 0
    n = len(notes)
    while i < n:
        t0 = notes[i][0]
        j = i
        cluster = []
        while j < n and notes[j][0] <= t0 + window:
            cluster.append(notes[j])
            j += 1
        i = j
        low = min(cluster, key=lambda x: x[2])
        line.append((low[0], low[1], low[2], low[3]))
    return line


def _melody_line(notes, window=0.12):
    """从 MT3 多音高里提取最高音线(人声/主旋律轨用)。

    与 _split_melody_accomp 同原理：起音簇取最高音作旋律。
    """
    if not notes:
        return []
    notes = sorted(notes, key=lambda x: x[0])
    line = []
    i = 0
    n = len(notes)
    while i < n:
        t0 = notes[i][0]
        j = i
        cluster = []
        while j < n and notes[j][0] <= t0 + window:
            cluster.append(notes[j])
            j += 1
        i = j
        top = max(cluster, key=lambda x: x[2])
        line.append((top[0], top[1], top[2], top[3]))
    return line


def transcribe_stems_enhanced(stems, model_path, progress, out_dir=None):
    """分离轨 + 和弦增强(重点：人声→和弦→贝斯)：

    1) 先 Demucs 分成 人声/鼓/贝斯/其他 四轨；
    2) 识别分工(按重要性)：
       - 人声轨：Basic Pitch(人声是单音旋律，Basic Pitch 快且准)——重点①
       - 其他轨(和弦/键盘/弦乐)：ByteDance 钢琴转录模型(CRNN，CPU 接近
         实时，比 MT3 快约 6 倍，专为和弦/多音高设计)——重点②
       - 贝斯轨：Basic Pitch(贝斯不重要，快速带过)
       - 鼓轨：Basic Pitch(只用于对齐强化贝斯起音)
    3) 逐轨整理：人声轨取最高音线=主旋律(右)，贝斯轨取最低音线=左手低音，
       和弦轨保留多音高进左手伴奏(抽稀)，鼓轨与贝斯对齐强化起音；
    4) 复用通用融合(fuse_to_piano)：碎音合并/legato/伴奏释放/力度分层/踏板。

    人声轨音符过少(纯器乐/分离失败)返回 None，调用方回退常规流程。
    返回 (midi_data, left, right)。
    """
    from basic_pitch.inference import Model as BpModel
    try:
        bp_model = BpModel(model_path)
    except Exception:
        bp_model = None

    # 人声用 Basic Pitch(快)；人声是单旋律，不需要多声部模型
    vocal_notes = transcribe_notes(stems["vocals"], bp_model, progress,
                                   label="人声旋律", min_len=60)
    if len(vocal_notes) < 10:
        progress("分轨人声音符过少，退回常规流程…")
        return None

    # 识别分工(用户指定)：人声=右手主旋律；左手主要伴奏从
    # 吉他/钢琴/其他中按响度(分贝)选出最响的一轨，和弦识别只用它。
    # 鼓/贝斯只分离保存、不识别。
    main_name, main_path = _merge_accomp_stems(stems, progress, out_dir=out_dir)
    harm_paths = [main_path] if main_path else []
    if not harm_paths:
        progress("未找到吉他/钢琴/其他伴奏轨，使用人声轨兜底…")
        harm_paths = [stems.get("vocals", "")]
    btd_ckpt = find_btd_checkpoint()
    if btd_ckpt and harm_paths and harm_paths[0]:
        btd_model = _btd_model(btd_ckpt)
        harm_notes = []
        for hp in harm_paths:
            harm_notes += _btd_track_notes(hp, btd_model, progress,
                                           f"主要伴奏({main_name})")
        other_notes = harm_notes or None
    else:
        progress("未找到和弦增强模型，和弦轨用快速引擎…")
        other_notes = None
        for hp in harm_paths:
            if not hp:
                continue
            other_notes = (other_notes or []) + transcribe_notes(
                hp, bp_model, progress, label="和声伴奏")
    if not other_notes:
        other_notes = transcribe_notes(stems.get("other", stems["vocals"]),
                                       bp_model, progress, label="和声伴奏")

    progress("正在融合分轨结果并整理成可弹钢琴谱…")
    gaps = _find_vocal_gaps(vocal_notes, other_notes)

    def _in_gap(s):
        return any(gs <= s < ge for gs, ge in gaps)

    def _in_long_gap(s):
        # 只有真正的长器乐段(>2.5s 前奏/尾奏)才丢弃人声；
        # 短空档内的人声保留(可能是识别漏音)，避免旋律突然消失
        return any(gs <= s < ge and (ge - gs) > 2.5 for gs, ge in gaps)

    # 人声轨取最高音线=主旋律；合并合成人声抖动碎音(VOCALOID)；
    # 仅长器乐段内丢弃人声改用器乐最高音线
    vocal_line = _dejitter_melody(_melody_line(vocal_notes))
    vocal_line = [n for n in vocal_line if not _in_long_gap(n[0])]
    melody = _fill_melody_gaps(vocal_line, other_notes, gaps=gaps)

    # R2：other 轨（电子音/合成器）单独识别一份，只并入无人声段。
    # 「合并不是已经做了吗」—— 做了，但那条伴奏合并轨走的是「钢琴模型 +
    # 钢琴向过滤器」：_filter_high_hallucination 会删掉所有「≥G5 且孤立」的音，
    # 而合成器主音、尖锐 pluck 正是这种形状。所以这里给 other 单独过一次识别，
    # 走放宽后的阈值并用在纯伴奏段。
    _ab = _accomp_boost_params()
    other_extra = None
    if _ab['on'] and _ab['other'] and main_name != 'other':
        _op = stems.get('other')
        if _op and os.path.isfile(_op):
            try:
                other_extra = transcribe_notes(_op, bp_model, progress,
                                               label='其他轨(电子音)', min_len=60)
                progress('无人声段伴奏加强：other 轨单独识别 %d 个音，只并入纯伴奏段。'
                         % len(other_extra))
            except Exception as _oe:
                progress('other 轨单独识别不可用(%s)，跳过。' % type(_oe).__name__)
                other_extra = None

    # 多和声音进左手：有人声段保持原样（滤高音幻觉→抑长铺垫→抽稀 0.8s）；
    # 无人声段按 R2 放宽阈值并并入 other 电子音
    accomp = _build_accomp(other_notes, _in_gap, gaps, min_gap=0.8,
                           halluc=True, extra=other_extra)

    midi_data, left, right = fuse_to_piano(melody, accomp)
    return midi_data, left, right


def _estimate_tempo_from_midi_time(midi, notes):
    """从 MT3 MIDI 解析出的音符起音间隔估算 BPM(供大谱表排版)。"""
    import numpy as np
    if len(notes) >= 4:
        starts = sorted(n[0] for n in notes)
        gaps = np.diff(starts)
        gaps = gaps[gaps > 0]
        if len(gaps):
            med = float(np.median(gaps))
            if 0 < med <= 4.0:
                return min(180, max(50, round(60.0 / med)))
    return 120.0


def fix_hand(hand, max_span=14, window=0.08, max_notes=4, mode="mix"):
    """对一个手的音符做时间窗口分组，保留可弹的子集。

    mode="melody"：最高音优先(旋律线)，跨度限制从最高音向下；
    mode="accomp"：最低音(贝斯)优先+力度/时值补足，跨度限制从最低音向上；
    mode="mix"：力度强者优先+时值长补足。
    """
    if not hand:
        return hand
    hand = sorted(hand, key=lambda x: (x[0], x[2]))  # 按开始时间、音高排序
    keep = [True] * len(hand)
    i = 0
    while i < len(hand):
        t0 = hand[i][0]
        # 窗口内：开始时间在 [t0, t0+window]，且与 t0 有发声重叠
        j = i
        in_win = []
        while j < len(hand) and hand[j][0] <= t0 + window:
            if hand[j][1] >= t0:
                in_win.append(j)
            j += 1
        i = j
        if mode == "melody":
            in_win.sort(key=lambda idx: -hand[idx][2])       # 最高音优先
            top = in_win[:1]
            rest = in_win[1:]
            rest.sort(key=lambda idx: -hand[idx][3])         # 其余按力度
            selected = top + rest[:max_notes - 1]
        elif mode == "accomp":
            in_win.sort(key=lambda idx: hand[idx][2])        # 最低音(贝斯)优先
            low = in_win[:1]
            rest = in_win[1:]
            rest.sort(key=lambda idx: (-hand[idx][3],
                                       -(hand[idx][1] - hand[idx][0])))
            selected = low + rest[:max_notes - 1]
        else:
            in_win.sort(key=lambda idx: -hand[idx][3])       # 力度强者优先
            loud = in_win[:2]
            rest = in_win[2:]
            rest.sort(key=lambda idx: -(hand[idx][1] - hand[idx][0]))
            selected = loud + rest[:max_notes - len(loud)]
        # 跨度限制(≤9 度)：旋律手从最高音向下保留，伴奏手从最低音向上保留
        if mode == "melody":
            ordered = sorted(selected, key=lambda idx: -hand[idx][2])
            kept = []
            for idx in ordered:
                if kept and hand[kept[0]][2] - hand[idx][2] > max_span:
                    continue
                kept.append(idx)
        else:
            ordered = sorted(selected, key=lambda idx: hand[idx][2])
            kept = []
            for idx in ordered:
                if kept and hand[idx][2] - hand[kept[0]][2] > max_span:
                    continue
                kept.append(idx)
        for idx in in_win:
            if idx not in kept:
                keep[idx] = False
    return [hand[k] for k in range(len(hand)) if keep[k]]


def fuse_to_piano(melody_notes, accomp_notes, max_span=14, window=0.08):
    """融合与修改：旋律(右)+伴奏(左) → 可弹钢琴 MIDI 与谱面数据。

    人手的物理限制（用户要求：手指最多只能跨八度到九度）：
      - 单只手同时按下的最低音与最高音之差 ≤ 9 度（约 14 个半音）；
      - 单只手同一时刻最多按 3 个键。

    修改流程：
      - 旋律先做平滑(修正八度跳音、删除离谱跳音，解决“太跳”)；
      - 旋律(右)：只删 AI 完全嵌套的重复碎音，**保留一切再起音**——
        日语等多音节语言一字一音、同音反复极多，绝不能合并成一条长音；
        时值不截断，保留延长音与连音，小间隙填补到下一音(连贯但不重叠)；
      - 左手(伴奏)：保留 1~2 个和声音(窗口最多 2 音)，不再有贝斯轨——
        贝斯不参与(2026-09-19)，左手内容全部来自合并后的和声轨识别结果；
        同音碎音合并(0.25s)、弱短音过滤更严——伴奏干净不抢戏，同样保留延长音；
      - 右手单音旋律(通用基线，不做八度加厚——低音区八度对会有拍频
        感“抖”)；左手简洁：稀疏和声点、
        窗口最多 2 音——伴奏干净不抢戏；
      - 力度分左右手映射：伴奏(左)30~80、主旋律(右)60~120，
        主旋律始终压过伴奏；前奏/尾奏的填充旋律力度抬到≥100，
        器乐段主旋律线同样明显；五线谱按小节力度标注 p/mp/mf/f/ff；
      - 延音踏板只踩左手、只对够长的音(≥0.3s)、值 75 轻踏板、
        音结束即抬起——解决“浑浊”同时保住延长感。

    返回 (out, left, right)：out 是可直接写出的 MIDI，
    left/right 是左右手音符((start,end,pitch,velocity))，供大谱表 XML 用。
    """
    from pretty_midi import PrettyMIDI

    melody_notes = _smooth_melody(melody_notes)

    left = _drop_tiny(_merge_fragments(accomp_notes, gap=0.25), min_len=0.12)
    right = _drop_tiny(_merge_melody_dups(melody_notes), min_len=0.06)
    left = _denoise_left(left, vel_floor_pct=30, max_len=0.22)

    # 左手每时刻最多 1 音(只留最低音贝斯骨干)，伴奏不杂不乱
    left = fix_hand(left, max_span=max_span, window=window, max_notes=1, mode="accomp")
    right = fix_hand(right, max_span=max_span, window=window, max_notes=3, mode="melody")

    # 时值整形：旋律/伴奏都保留自然时值(不截断，保留延长音与连音)，
    # 只做小间隙填补(legato 连贯)——截断会吃掉长音和跨小节连音
    left = _shape_durations(left, trim_at_onset=False)
    right = _shape_durations(right, trim_at_onset=False, legato_gap=0.06)
    # 消除同音高重叠(避免同音双响的“抖”)
    left = _fix_same_pitch_overlap(left)
    right = _fix_same_pitch_overlap(right)

    # 力度层次：伴奏(左手)默认比主旋律(右手)小 25%。
    # 两手先映射到同一基础区间(80~120)，再对左手整体 ×0.75
    # → 左手 ≈ 60~90 = 右手 75%，伴奏稳定弱于旋律 25%。
    left = _soft_velocity(left, lo=80, hi=120)
    right = _soft_velocity(right, lo=80, hi=120)
    left = [(s, e, p, max(1, int(v * 0.75))) for s, e, p, v in left]

    # 兜底: 删除任何完全重复的音符
    left = _dedupe_exact(left)
    right = _dedupe_exact(right)

    # 左右手音域分离：左手高音降八度，避免两手糊在一起
    left = _separate_hands(left, right_min=60)
    # R1：再按时间窗强制拉开到整整一个八度（_separate_hands 只保证 1 个半音）
    left, right, _gapst = _enforce_octave_gap(left, right)

    return _build_hands_midi(left, right), left, right


def _merge_fragments(notes, gap=0.15):
    """同音高、间隙很小的碎音合并成一个长音(连音/延长音)。

    AI 常把一条连线长音切成几段碎音，或对同音重复起音。
    注意：按时间排序后，与“最近的同音高前一个音”合并——
    中间夹着其他音高的音不影响合并(持续音保持在伴奏之上是很自然的)，
    否则一条被其他音隔断的碎音链就接不起来了。
    """
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: (x[0], x[2]))
    out = []
    last_same = {}   # pitch -> out 中的下标
    for s, e, p, v in notes:
        idx = last_same.get(p)
        if idx is not None:
            ps, pe, pp, pv = out[idx]
            if s - pe <= gap:
                out[idx] = (ps, max(pe, e), p, max(pv, v))
                continue
        last_same[p] = len(out)
        out.append((s, e, p, v))
    return out


def _split_melody_accomp(all_notes, window=0.08):
    """按角色分手：每个起音簇(±window 内先后起音)的最高音=旋律(人声/主奏)，
    其余=伴奏(和声/贝斯)。

    之前按固定音高(C4)分手，男声等人声旋律音域低会被整段归进左手伴奏，
    与低音混成一片(浑浊、主次不分)。按“同时发声的最高音”分离后，
    人声旋律线稳定进入右手，伴奏整体留在左手，主次天然分开。
    """
    if not all_notes:
        return [], []
    all_notes = sorted(all_notes, key=lambda x: x[0])
    melody, accomp = [], []
    i = 0
    n = len(all_notes)
    while i < n:
        t0 = all_notes[i][0]
        j = i
        cluster = []
        while j < n and all_notes[j][0] <= t0 + window:
            cluster.append(all_notes[j])
            j += 1
        i = j
        top = max(cluster, key=lambda x: x[2])[2]
        for nt in cluster:
            (melody if nt[2] == top else accomp).append(nt)
    return melody, accomp


def _smooth_melody(notes, window=0.4, max_jump=9):
    """旋律平滑：修正八度跳变、删除离谱跳音(听感“太跳”的元凶)。

    Basic Pitch 对人声常把二次谐波当基频：个别音突然比邻音高/低一个
    八度。以每个音 ±window 内邻音的中位音高为参照：
    - 偏差在 10~14 半音(≈一个八度)且折叠回参照附近(≤4)时按八度折叠
      ——孤立八度跳音修正，合法的大跳(邻居同处新音区)不受影响；
    - 偏差超过 max_jump 且无法折叠的判定为噪声，直接丢弃。
    只改音高/删音，不碰时值与力度，音节节奏不受影响。
    """
    if len(notes) < 3:
        return notes
    import numpy as np
    notes = sorted(notes, key=lambda x: x[0])
    out = []
    for i, (s, e, p, v) in enumerate(notes):
        lo = i
        while lo > 0 and s - notes[lo - 1][0] <= window:
            lo -= 1
        hi = i
        while hi < len(notes) - 1 and notes[hi + 1][0] - s <= window:
            hi += 1
        med = float(np.median([notes[k][2] for k in range(lo, hi + 1)]))
        d = p - med
        if 10.0 <= abs(d) <= 14.0:
            folded = p - 12 if d > 0 else p + 12
            if abs(folded - med) <= 4.0:
                p = folded
                d = p - med
        # 邻居≥2 时才判定“离谱跳音”并丢弃；稀疏段落宁留勿删(保还原度)
        if abs(d) > max_jump and (hi - lo) >= 2:
            continue
        if not (21 <= p <= 108):
            continue
        out.append((s, e, p, v))
    return out


def _merge_melody_dups(notes):
    """旋律去重：只合并“完全嵌套”的碎音(AI 重复检测)，保留一切再起音。

    教训：之前用通用碎音合并(gap=0.05)，把同音高、间隙很小的音节
    全部并成一条长音——日语等多音节语言同音反复极多，一半的音符
    被吃掉了(实测 787→384)，旋律完全不还原。
    这里只处理 AI 在同一个音上重复输出的碎音：
    - 新音完全嵌套在旧音里(起点不早、终点不晚) → 丢弃(重复)；
    - 新音完全覆盖旧音 → 用新音替换；
    - 其余(含重叠但有新起音)全部保留——音节节奏一个不丢。
    """
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: (x[0], x[2]))
    out = []
    last_same = {}   # pitch -> out 中的下标
    for s, e, p, v in notes:
        idx = last_same.get(p)
        if idx is not None:
            ps, pe, pp, pv = out[idx]
            if s >= ps and e <= pe:
                continue              # 完全嵌套 → AI 重复，丢弃
            if s <= ps and e >= pe:
                out[idx] = (s, e, p, max(v, pv))  # 反向嵌套 → 替换
                continue
        last_same[p] = len(out)
        out.append((s, e, p, v))
    return out


def _dejitter_melody(notes, onset_gap=0.22, min_span=1.0):
    """合并“合成人声抖动碎音”(VOCALOID/术力口适配)。

    VOCALOID 合成人声常把同一个音输出成大量短碎音(实测起音间隔
    ~0.08s、同音高重复几十次)，听起来“乱”。真人多音节歌曲的同音
    反复起音间隔通常 >=0.25s，不会被误并。
    规则：同音高、起音间隔 <onset_gap 的碎音合并成一条长音。
    """
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: (x[0], x[2]))
    by_pitch = {}
    for n in notes:
        by_pitch.setdefault(n[2], []).append(n)
    out = []
    for p, grp in by_pitch.items():
        grp.sort(key=lambda x: x[0])
        merged = []
        cur_s, cur_e, cur_v, prev_s = None, None, 0, None
        for s, e, _p, v in grp:
            if cur_s is None:
                cur_s, cur_e, cur_v, prev_s = s, e, v, s
            elif s - prev_s <= onset_gap:
                # 与上一音起音间隔小 → 同一音的抖动碎音，并入当前长音
                cur_e = max(cur_e, e)
                cur_v = max(cur_v, v)
                prev_s = s
            else:
                merged.append((cur_s, cur_e, p, cur_v))
                cur_s, cur_e, cur_v, prev_s = s, e, v, s
        if cur_s is not None:
            merged.append((cur_s, cur_e, p, cur_v))
        out.extend(merged)
    out.sort(key=lambda x: (x[0], x[2]))
    return out


def _parse_omr_measures(xml_path):
    """解析 oemer(OMR) 导出的 MusicXML → 逐小节逐谱表的音符。

    返回 measures 列表；每小节为 {1: [(slot, midi, dur)], 2: [...]}，
    staff 1=高音谱、2=低音谱(示例谱编号)，slot/dur 以 16 分音符为单位
    (内部自动换算 divisions/quarter)。

    注意：oemer 用 <backup> 交错写两个谱表，必须用单一游标 + 回退
    来还原每层音符的真实位置，不能按谱表各自累计。
    """
    import xml.etree.ElementTree as ET
    tree = ET.parse(xml_path)
    part = tree.getroot().find("part")
    steps = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
    divisions = 16
    first = part.find("measure")
    if first is not None:
        div_el = first.find("attributes/divisions")
        if div_el is not None:
            divisions = max(1, int(div_el.text))
    unit = divisions / 4.0   # 一个 16 分音符 = divisions/4 个单位
    measures = []
    for measure in part.findall("measure"):
        cursor = 0.0
        data = {1: [], 2: []}
        for child in measure:
            if child.tag == "backup":
                cursor -= int(child.findtext("duration") or 0)
                continue
            if child.tag == "forward":
                cursor += int(child.findtext("duration") or 0)
                continue
            if child.tag != "note":
                continue
            dur = int(child.findtext("duration") or 0)
            st = int(child.findtext("staff") or 1)
            is_chord = child.find("chord") is not None
            if child.find("rest") is None:
                pitch = child.find("pitch")
                if pitch is not None:
                    midi = ((int(pitch.findtext("octave")) + 1) * 12
                            + steps[pitch.findtext("step")]
                            + int(pitch.findtext("alter") or 0))
                    slot = int(round(cursor / unit))
                    d16 = max(1, int(round(dur / unit)))
                    data[st].append((slot, midi, d16))
            if not is_chord:
                cursor += dur
        measures.append(data)
    return measures


def _find_ref_file(ref_dir, names):
    for n in names:
        p = os.path.join(ref_dir, n)
        if os.path.isfile(p):
            return p
    return None


def _splice_reference(left, right, ref_dir, base, n_bars, bpm, progress):
    """参考谱拼接：把示例谱(OMR MusicXML)的前奏/尾奏小节直接拼进成品。

    前奏 = <歌名>_前奏参考.musicxml 的前 5 小节 → 成品第 1~5 小节；
    尾奏 = <歌名>_尾奏参考.musicxml 的末 9 小节 → 成品最后 9 小节。
    参考文件按歌名匹配，换歌时不会误拼别的歌的谱。
    没有参考文件时原样返回；返回 (left, right, splice)，
    splice 为 {小节号: {staff: [(slot, midi, dur, tie_s, tie_e)]}}。
    """
    intro_path = _find_ref_file(
        ref_dir, (f"{base}_前奏参考.musicxml", f"{base}_intro_ref.musicxml"))
    outro_path = _find_ref_file(
        ref_dir, (f"{base}_尾奏参考.musicxml", f"{base}_outro_ref.musicxml"))
    if not intro_path and not outro_path:
        return left, right, None

    quarter = 60.0 / bpm
    bar_dur = 4 * quarter
    splice = {}
    left = list(left)
    right = list(right)

    def add_page(path, measure_slice, bar_of):
        measures = _parse_omr_measures(path)
        if path is intro_path:
            sel = measures[:5]
        else:
            sel = measures[-9:]
        for k, meas in enumerate(sel):
            bar = bar_of(k)
            for st in (1, 2):
                segs_local = [(slot, midi, max(1, dur), False, False)
                              for slot, midi, dur in meas.get(st, [])]
                # 示例谱 staff1=高音谱(右手)、staff2=低音谱(左手)，
                # 与我们的内部约定(1=左手,2=右手)相反，做映射；
                # _staff_lines 需要全曲绝对槽位(小节号*16 + 小节内槽位)
                our_staff = 2 if st == 1 else 1
                splice.setdefault(bar, {})[our_staff] = [
                    (bar * 16 + slot, midi, d, ts, te)
                    for slot, midi, d, ts, te in segs_local
                ]
                for slot, midi, dur, _ts, _te in segs_local:
                    t = (bar + slot / 16.0) * bar_dur
                    e = t + dur / 16.0 * bar_dur
                    v = 100 if st == 1 else 70
                    (right if st == 1 else left).append((t, e, midi, v))

    if intro_path:
        add_page(intro_path, range(5), lambda k: k)
        progress("已拼接示例谱前奏(5 小节)。")
    if outro_path:
        add_page(outro_path, range(9), lambda k: n_bars - 9 + k)
        progress("已拼接示例谱尾奏(9 小节)。")

    # 去掉被拼接区间覆盖的中间声部内容
    intro_end_t = 5 * bar_dur
    outro_start_t = (n_bars - 9) * bar_dur
    left = [n for n in left if not (n[0] < intro_end_t or n[0] >= outro_start_t)]
    right = [n for n in right if not (n[0] < intro_end_t or n[0] >= outro_start_t)]

    return left, right, splice


def _build_hands_midi(left, right):
    """左右手音符 → PrettyMIDI(带左手轻踏板)。"""
    from pretty_midi import PrettyMIDI
    out = PrettyMIDI()
    out.instruments.append(_build_hand("L", left, add_pedal=True))
    out.instruments.append(_build_hand("R", right, add_pedal=False))
    return out


def _fix_same_pitch_overlap(notes):
    """同音高的重叠音：前一个音的结束截到后一个音的起始。

    防止同音双响的“抖”/颤音(两个同音音符同时发声会互相抵消触发)，
    但不影响不同音高之间的连音与延长音。
    """
    if len(notes) < 2:
        return notes
    notes = sorted(notes, key=lambda x: x[0])
    out = []
    last = {}   # pitch -> out 中的下标
    for s, e, p, v in notes:
        idx = last.get(p)
        if idx is not None:
            ps, pe, pp, pv = out[idx]
            if s < pe:
                out[idx] = (ps, s, p, max(pv, v))   # 前一个同音音截到后一个起始
        last[p] = len(out)
        out.append((s, e, p, v))
    return out


def _dedupe_exact(notes):
    """删除完全相同的重复音符(同起止时间、同音高)——任何来源的重复都删。"""
    seen = set()
    out = []
    for s, e, p, v in notes:
        key = (round(s, 6), round(e, 6), p)
        if key in seen:
            continue
        seen.add(key)
        out.append((s, e, p, v))
    return out


def _simple_piano(notes, split_pitch=60, max_span=14, max_notes=4, window=0.08):
    """简洁模式：按音高(C4)切左右手 + 窗口限音，不做分离/融合/八度。

    经典流程：左手=低音区、右手=高音区，每窗最多 4 音、跨度 ≤9 度；
    保留自然时值(连音)与轻踏板；去同音重叠/去重(防抖)。
    """
    # 先合并合成人声/乐器的同音抖动碎音(术力口/VOCALOID 适配)
    notes = _dejitter_melody(notes)
    left_raw = [n for n in notes if n[2] < split_pitch]
    right_raw = [n for n in notes if n[2] >= split_pitch]
    left = fix_hand(left_raw, max_span=max_span, window=window,
                    max_notes=max_notes, mode="mix")
    # 右手同样用 mode="mix"，与左手一致。
    #
    # 【t3-A 已弃用 —— 2026-09-20 回退】曾试过右手改 mode="melody"（最高音优先）。
    # 合成竞争测试确实证明它有效：当**右手内部**同时存在"响的伴奏音 60/64/67"与
    # "较弱的旋律 79"时，mix 保留 [60,64,67]（丢旋律）、melody 保留 [79]
    # （同一输入 264 音符，MIDI 1268 B → 980 B，见 回归验收/_prove_t3a_revert_equiv.py）。
    # 但在**出厂回炉路径**（= 100% 的出货产物）上它对 5 首真实曲目近乎惰性：
    #   · shiki 的 B/C 产物 **逐字节相同**（MIDI SHA b3fc190f82c6dafd07ac）；
    #   · jiabin ≈0、fanwut 略负、monitoring 的 melody 版右手与改动前基线一致；
    # 而它在 monitoring 上把回炉成本从 0.204 压低到 0.197，使 t6「人声接入」的固有
    # 代价（+0.008）超出绝对窗口 0.005 → **接入被回滚**，at 覆盖 76.54%→70.35%、
    # at 断档 1→11、n_notes 913→841（t6 的收益整段作废），而 sim 反而 +0.0057
    # （去掉 graft 让成本回落）—— **sim 会把这次退化判成 PASS**。
    # 净效果 = 无收益 + 打断 t6 ⇒ 弃用。取证：回归验收/术力口与人声连续性优化报告.md §三之二。
    # 注意：若将来要重试，正确做法不是改本行，而是先解决 t6 接入判据的**绝对窗口**问题
    #      （见 回归验收/_work/_patch_graft_guard.py）。
    right = fix_hand(right_raw, max_span=max_span, window=window,
                     max_notes=max_notes, mode="mix")
    left = _fix_same_pitch_overlap(_dedupe_exact(left))
    right = _fix_same_pitch_overlap(_dedupe_exact(right))
    left = _separate_hands(left, right_min=60)  # 左右手音域分离
    left, right, _gapst = _enforce_octave_gap(left, right)   # R1：拉到整整一个八度
    left = _soft_velocity(left, lo=40, hi=100)
    right = _soft_velocity(right, lo=50, hi=110)
    return _build_hands_midi(left, right), left, right


def _drop_tiny(notes, min_len=0.08):
    """过滤超短碎音(基本是识别噪声，听感是‘杂音’)。"""
    return [(s, e, p, v) for s, e, p, v in notes if e - s >= min_len]


def _separate_hands(left, right_min=60):
    """左右手音域分离：左手 ≥right_min 的音降一个八度。

    左手伴奏与右手旋律音域重叠时听起来“糊在一起”。把左手高音区
    的音整体降八度(60-71 → 48-59)，保证左手 ≤59、右手 ≥60，
    明确分层且不丢和声色彩。
    """
    out = []
    for s, e, p, v in left:
        if p >= right_min and p < right_min + 12:
            out.append((s, e, p - 12, v))
        elif p >= right_min + 12:
            out.append((s, e, p - 24, v))
        else:
            out.append((s, e, p, v))
    return out


def _right_min_cells(right, hi_t, grid=0.02):
    """把「每个时间格上右手的最低音」铺成一张表（R1 用）。

    用格子而不是逐音两两比较：左右手各上千个音时，两两比较是百万级，
    而格子法只跟时长成正比。格子取 20ms，比任何有音乐意义的间隔都细。
    """
    n = int(hi_t / grid) + 2
    cells = [None] * n
    for s, e, p, _v in right:
        i0 = max(0, int(s / grid))
        i1 = min(n - 1, int(e / grid))
        for i in range(i0, i1 + 1):
            if cells[i] is None or p < cells[i]:
                cells[i] = p
    return cells


def _hand_gap_min(left, right, grid=0.02):
    """重叠时刻上「右手最低音 − 左手最高音」的最小值（报告/验收用）。

    只在同一时间格里同时有右手音与左手音时才比较。
    返回 (最小值, 比较格数)；没有可比时刻返回 (None, 0)。
    """
    if not left or not right:
        return None, 0
    hi_t = max(max(e for _s, e, _p, _v in left),
               max(e for _s, e, _p, _v in right))
    n = int(hi_t / grid) + 2
    r_min = _right_min_cells(right, hi_t, grid)
    l_max = [None] * n
    for s, e, p, _v in left:
        i0 = max(0, int(s / grid))
        i1 = min(n - 1, int(e / grid))
        for i in range(i0, i1 + 1):
            if l_max[i] is None or p > l_max[i]:
                l_max[i] = p
    best, cnt = None, 0
    for i in range(n):
        if r_min[i] is not None and l_max[i] is not None:
            g = r_min[i] - l_max[i]
            cnt += 1
            if best is None or g < best:
                best = g
    return best, cnt


def _left_max_cells(left, hi_t, grid=0.02):
    """把「每个时间格上左手最高音」铺成一张表（R1 第二趟用）。"""
    n = int(hi_t / grid) + 2
    cells = [None] * n
    for s, e, p, _v in left:
        i0 = max(0, int(s / grid))
        i1 = min(n - 1, int(e / grid))
        for i in range(i0, i1 + 1):
            if cells[i] is None or p > cells[i]:
                cells[i] = p
    return cells


def _enforce_octave_gap(left, right, min_gap=12, floor=21, grid=0.02):
    """R1 第 2 条：重叠时刻上「右手最低音 − 左手最高音」至少 min_gap 个半音。

    人声在右手、伴奏在左手。`_separate_hands()` 只把左手压到 ≤59、右手 ≥60
    （相差 **1** 个半音），两手音域仍然挨着，谱面与听感都会糊在一起；
    这里把它拉到整整一个八度。

    两趟（顺序不能反）：
      **第一趟「压左手」**：对每个左手音，取与它重叠的右手最低音，整体下移
      八度直到低于 (右手最低音 − min_gap)；低到 floor 就停手。
      **第二趟「抬右手」**：第一趟触底仍不够的窗口，才把人声那侧升八度。
      （左手怎么动都不改变右手，所以第一趟一趟就够；第二趟只补触底的漏。）

    只改音高：不增删音、不改起止时间、不改力度。
    `TS_HAND_GAP=0` 关闭，`TS_HAND_GAP_SEMI` / `TS_HAND_GAP_FLOOR` 可调。
    floor 默认 21 = A0，钢琴最低键，再低就不是钢琴音域了。

    返回 (新的左手, 新的右手, 统计 dict)。
    """
    st = {'on': True, 'moved': 0, 'octaves': 0, 'blocked': 0, 'raised': 0,
          'raise_octaves': 0, 'gap_before': None, 'gap_after': None}
    if os.environ.get('TS_HAND_GAP', '1') != '1' or not left or not right:
        st['on'] = False
        return left, right, st
    try:
        min_gap = int(os.environ.get('TS_HAND_GAP_SEMI', str(min_gap)))
        floor = int(os.environ.get('TS_HAND_GAP_FLOOR', str(floor)))
    except ValueError:
        pass
    hi_t = max(max(e for _s, e, _p, _v in left),
               max(e for _s, e, _p, _v in right))
    cells = _right_min_cells(right, hi_t, grid)
    n = len(cells)
    st['gap_before'] = _hand_gap_min(left, right, grid)[0]

    # ---- 第一趟：压左手 ----
    out = []
    for s, e, p, v in left:
        rgm = None
        for i in range(max(0, int(s / grid)), min(n - 1, int(e / grid)) + 1):
            c = cells[i]
            if c is not None and (rgm is None or c < rgm):
                rgm = c
        if rgm is None:                     # 该左手音处右手没音 → 无从比较，不动
            out.append((s, e, p, v))
            continue
        limit = rgm - min_gap
        np_ = p
        k = 0
        while np_ > limit and np_ - 12 >= floor:
            np_ -= 12
            k += 1
        if k:
            st['moved'] += 1
            st['octaves'] += k
        # ⚠️ 「降了但没降够」也必须记 blocked —— 第一版只在 k==0 时记，
        #    于是「降了几个八度、最后卡在 floor 上仍不达标」的窗口从没被
        #    交给第二趟抬右手，monitoring/jiabin 就残留了 −1 半音。
        if np_ > limit:
            st['blocked'] += 1
        out.append((s, e, np_, v))

    # ---- 第二趟：触底的窗口改抬右手（人声那侧）----
    new_right = right
    if st['blocked']:
        hi2 = max(max(e for _s, e, _p, _v in out),
                  max(e for _s, e, _p, _v in right))
        lcells = _left_max_cells(out, hi2, grid)
        n2 = len(lcells)
        new_right = []
        for s, e, p, v in right:
            lm = None
            for i in range(max(0, int(s / grid)), min(n2 - 1, int(e / grid)) + 1):
                c = lcells[i]
                if c is not None and (lm is None or c > lm):
                    lm = c
            if lm is None:
                new_right.append((s, e, p, v))
                continue
            req = lm + min_gap
            np_ = p
            k = 0
            while np_ < req and np_ + 12 <= 96:
                np_ += 12
                k += 1
            if k:
                st['raised'] += 1
                st['raise_octaves'] += k
                new_right.append((s, e, np_, v))
            else:
                new_right.append((s, e, p, v))

    st['gap_after'] = _hand_gap_min(out, new_right, grid)[0]
    return out, new_right, st


# ---------------------------------------------------------------------------
# R2：无人声段（纯伴奏）加强伴奏识别
# ---------------------------------------------------------------------------

def _accomp_boost_params():
    """R2「无人声段加强伴奏识别」的开关与阈值。

    默认**开启**（这是 2026-09-20 起用户要求的编配行为）。
    要回到 R2 之前的伴奏整理，设 `TS_ACCOMP_BOOST=0`（那时逐字节还原旧路径）。
    """
    return {
        'on': os.environ.get('TS_ACCOMP_BOOST', '1') == '1',
        # 无人声段的「长铺垫」抑制上限（旧值 0.7s；放宽到 1.6s 才留得住 synth pad）
        'pad_len': float(os.environ.get('TS_ACCOMP_GAP_PAD', '1.6')),
        # 无人声段的抽稀间隔相对倍率（0.5 = 密度翻倍）
        'ratio': float(os.environ.get('TS_ACCOMP_GAP_RATIO', '0.5')),
        # 是否单独识别 other 轨（电子音/合成器）并只并入无人声段
        'other': os.environ.get('TS_ACCOMP_OTHER', '1') == '1',
    }


def _dedupe_near(notes, dt=0.06, dp=1):
    """去掉「同音高、起音几乎同时」的重复音（并入 other 轨识别结果时用）。"""
    out = []
    for n in sorted(notes, key=lambda x: (x[0], x[2])):
        dup = False
        for m in reversed(out[-8:]):
            if n[0] - m[0] > dt:
                break
            if abs(n[2] - m[2]) <= dp:
                dup = True
                break
        if not dup:
            out.append(n)
    return out


def _accomp_legacy(other_notes, in_gap, gaps, min_gap=0.8, halluc=True):
    """R2 之前的伴奏整理（`TS_ACCOMP_BOOST=0` 时走这条，逐字节还原）。"""
    base = _filter_high_hallucination(other_notes) if halluc else other_notes
    harmony = _sparsify_harmony(_suppress_pad_notes(base), min_gap=min_gap)
    accomp = [(n[0], n[1], n[2], int(n[3] * 0.85)) if in_gap(n[0]) else n
              for n in harmony]
    if gaps:
        gap_harmony = [n for n in accomp if in_gap(n[0])]
        keep = [n for n in accomp if not in_gap(n[0])]
        accomp = keep + _sparsify_harmony(gap_harmony, min_gap=1.5)
        accomp.sort(key=lambda x: x[0])
    return accomp


def _build_accomp(other_notes, in_gap, gaps, min_gap=0.8, halluc=True, extra=None):
    """伴奏轨整理。

    R2 之前（= `TS_ACCOMP_BOOST=0`）：
        滤高音幻觉 → 抑长铺垫 → 抽稀；纯伴奏段再压低 15% 力度并二次抽稀(1.5s)。

    第二段的「二次抽稀 + 压低力度」是为**不抢器乐主旋律**设计的，代价是把
    无人声段本来就少的伴奏又削薄一半；而 `_filter_high_hallucination` 会删掉
    所有「≥G5 且孤立」的音 —— 合成器主音、尖锐 pluck 正是这个形状。

    R2 只改**无人声段**：不滤孤立高音、放宽长音抑制、抽稀更密，并把 other 轨
    单独识别出来的音并进来。**有人声段逐字节不变。**
    """
    p = _accomp_boost_params()
    if not p['on']:
        return _accomp_legacy(other_notes, in_gap, gaps,
                              min_gap=min_gap, halluc=halluc)
    sung = [n for n in other_notes if not in_gap(n[0])]
    instr = [n for n in other_notes if in_gap(n[0])]
    base = _filter_high_hallucination(sung) if halluc else sung
    a = _sparsify_harmony(_suppress_pad_notes(base), min_gap=min_gap)
    b = _sparsify_harmony(_suppress_pad_notes(instr, max_len=p['pad_len']),
                          min_gap=max(0.05, min_gap * p['ratio']))
    if extra:
        # extra（other 轨单独识别）要先抽稀再并 —— 它是**原始识别输出**，
        # 密度可达 20+ 音/秒，直接倒进去会把无人声段灌爆。
        ex = [n for n in extra if in_gap(n[0])]
        if ex:
            b = _dedupe_near(
                b + _sparsify_harmony(ex, min_gap=max(0.05, min_gap * p['ratio'])))
    return sorted(a + b, key=lambda x: x[0])


def _denoise_left(notes, vel_floor_pct=25, max_len=0.18):
    """左手去噪：力度处于垫底 25% 且很短(<0.18s)的音是典型识别噪声。

    左手以伴奏为主，宁可少不可杂；右手(旋律)保留原样。
    """
    if not notes:
        return notes
    import numpy as np
    floor = float(np.percentile([v for _s, _e, _p, v in notes], vel_floor_pct))
    return [(s, e, p, v) for s, e, p, v in notes
            if not (v <= floor and e - s < max_len)]


def _shape_durations(notes, legato_gap=0.10, trim_at_onset=False):
    """时值整形。

    - legato 填补：与下一个起音(任意音高)的间隙 ≤ legato_gap 时，
      延长到下一个起音，产生连音(legato)听感；
    - trim_at_onset=True(伴奏)：长音越过下一 onset 时截到 onset，
      伴奏重新起音、不糊在旋律下面——浑浊的主要来源；
    - trim_at_onset=False(旋律)：长音保持原样，保延长音；
    - 最短 0.05s，避免零时长音。
    """
    if not notes:
        return notes
    from bisect import bisect_right
    notes = sorted(notes, key=lambda x: x[0])
    uniq_starts = sorted(set(round(s, 6) for s, _e, _p, _v in notes))
    out = []
    for s, e, p, v in notes:
        idx = bisect_right(uniq_starts, round(s, 6) + 1e-6)
        nxt = uniq_starts[idx] if idx < len(uniq_starts) else None
        if nxt is not None and 0 < nxt - e <= legato_gap:
            e = nxt                       # legato 填补
        elif trim_at_onset and nxt is not None and e > nxt:
            e = nxt                       # 伴奏在下一 onset 释放
        out.append((s, max(e, s + 0.05), p, v))
    return out




def _soft_velocity(notes, lo=45, hi=112):
    """把力度线性映射到 [lo, hi]：左右手各用不同区间，
    左手(伴奏)整体更轻、右手(旋律)更突出，层次分明不浑浊。"""
    vs = [v for _s, _e, _p, v in notes]
    if not vs:
        return notes
    vmin, vmax = min(vs), max(vs)
    span = (vmax - vmin) or 1
    out = []
    for s, e, p, v in notes:
        nv = int(lo + (v - vmin) / span * (hi - lo))
        out.append((s, e, p, max(1, min(127, nv))))
    return out


def _estimate_tempo(midi_data):
    """从 MIDI 的节拍变化或音符密度估计 BPM，供大谱表排版用。"""
    try:
        times, bpms = midi_data.get_tempo_changes()
        if len(bpms):
            import numpy as np
            return float(np.median(bpms))
    except Exception:
        pass
    # 退路：音符间奏中位数 -> BPM
    import numpy as np
    starts = sorted(n.start for inst in midi_data.instruments for n in inst.notes)
    if len(starts) >= 4:
        gaps = np.diff(starts)
        gaps = gaps[gaps > 0]
        if len(gaps):
            med = float(np.median(gaps))
            if 0 < med <= 4.0:  # 间奏在 0~4 秒之间才可信
                return min(180, max(50, round(60.0 / med)))
    return 120.0


def _merge_time_intervals(iv, gap=0.0):
    """把 [start,end] 区间并集化（相邻间隔 ≤ gap 的合并）。"""
    iv = sorted((float(s), float(e)) for s, e in iv if e > s)
    if not iv:
        return []
    out = [[iv[0][0], iv[0][1]]]
    for s, e in iv[1:]:
        if s - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _subtract_intervals(hole, cover):
    """从 hole=(a,b) 里挖掉 cover 区间列表，返回剩下的子区间。"""
    a, b = hole
    pieces = []
    cur = a
    for s, e in cover:
        if e <= cur or s >= b:
            continue
        if s > cur:
            pieces.append((cur, min(s, b)))
        cur = max(cur, e)
        if cur >= b:
            break
    if cur < b:
        pieces.append((cur, b))
    return [(s, e) for s, e in pieces if e > s]


def _fill_right_hand(right, mix_notes, vline, split_pitch=60, min_len=0.06,
                     min_pitch=45, pad=0.06, max_per_sec=3.0, win=0.10,
                     max_notes_per_win=4, max_span=16, vocal_gap=0.50,
                     hole_min=0.35, instr_reg=28, add_velocity_floor=58,
                     dens_target=2.2, dens_win=1.0, same_win=0.05, instr_rate=2.5,
                     onset_guard=0.12, instr_lo=43):
    """把右手补成「**有人声处弹人声、无人声处弹伴奏**」。

    ⚠️ 规则 A 与规则 B 用**各自独立的速率预算**（`max_per_sec` / `instr_rate`）：
       第一版共用一个预算，规则 A 先跑就把预算吃光了 → 无人声段密度几乎没变
       （实测：无人声段右手 0.81→1.44 音/秒、"空置 >0.5s"仍占 82.7%），主人的反馈①没解决。
    """
    """把右手补成「**有人声处弹人声、无人声处弹伴奏**」。

    为什么（新曲「月が綺麗ね」实测，只读诊断见 回归验收/_diag_feedback.py）：
      · **无人声段 18.5 s 里，右手 81.8% 的时间连续 >0.5 s 一个音都没有** ——
        独奏钢琴在前奏/间奏听感就是"空手"（主人反馈①）；
      · **人声真值音节只有 43.8% 在右手被弹出来**（≥C4 的音节更只有 35.8%）——
        听觉上就是"日语的字没转成音符"（主人反馈③）；
      · 右手整体密度仅 1.86 音/秒（左手 5.24），整曲织体偏薄（主人反馈②）。
    两条规则：
      A) **人声音节**：右手在该时刻没有"同音高"的音 → 补上；**即使该时刻有别音高的伴奏音占着，
         也补**（那正是"人声被伴奏顶掉"的场景）。目标音高落在右手音区：低于 split_pitch 的
         升八度（男声人声大量在 C4 以下，不升就永远进不了右手）。
      B) **无人声段补伴奏**：人声不活跃的区间里，右手若出现 ≥hole_min 的空洞，就从
         **整曲混音**的右手音区音符里取（每 0.10 s 取最高音、按密度上限稀疏）补进去。

    纪律（与 t6 一致）：**不动左手、不动回炉取舍判据、不删改任何已有音**；只往右手加音。
    返回 (新的右手, 统计 dict)；异常由调用方捕获并回退。
    """
    import bisect
    right = sorted(right, key=lambda n: (n[0], n[2]))
    out = list(right)
    st = {'added': 0, 'added_vocal': 0, 'added_instr': 0, 'skipped_win': 0,
          'skipped_span': 0, 'skipped_dens': 0, 'skipped_onset': 0}
    if not out and not mix_notes:
        return right, st
    # 诊断开关：两条规则可分别关闭，用来量化各自对 sim 的代价
    #   RULE_A(人声音节进右手) 默认开；RULE_B(无人声段补伴奏) 默认开
    _ruleA = os.environ.get('TS_FILL_RULE_A', '1') == '1'
    _ruleB = os.environ.get('TS_FILL_RULE_B', '1') == '1'
    gap_sec = 1.0 / max(1e-6, max_per_sec)

    def win_notes(t):
        return [n for n in out if abs(n[0] - t) <= win]

    def same_pitch_near(t, p):
        return any(abs(n[0] - t) <= same_win and abs(n[2] - p) <= 1 for n in out)

    def pitches_overlapping(t0, t1):
        return [n[2] for n in out if n[0] < t1 + pad and n[1] > t0 - pad]

    def busy_near(t0, t1):
        return any(n[1] > t0 - pad and n[0] < t1 + pad for n in out)

    # ---------------- A) 人声音节 ----------------
    last = -9.9
    for s, e, p, v in (sorted(vline, key=lambda n: n[0]) if _ruleA else []):
        if (e - s) < min_len or p < min_pitch:
            continue
        pr = p
        while pr < split_pitch and pr + 12 <= 100:
            pr += 12
        if pr < split_pitch or pr > 100:
            continue
        if same_pitch_near(s, pr):
            continue
        if len(win_notes(s)) >= max_notes_per_win:
            st['skipped_win'] += 1
            continue
        if (st['added_vocal'] + st['added_instr']) and (s - last) < gap_sec:
            st['skipped_dens'] += 1
            continue
        ps = pitches_overlapping(s, e)
        if ps and (pr - max(ps) > max_span or min(ps) - pr > max_span):
            st['skipped_span'] += 1
            continue
        out.append((s, e, pr, max(int(v), add_velocity_floor)))
        st['added_vocal'] += 1
        last = s

    # ---------------- B) 无人声段补伴奏 ----------------
    viv = _merge_time_intervals([(s, e) for s, e, p, v in vline if p >= min_pitch],
                                gap=vocal_gap)

    def in_vocal(t):
        for s, e in viv:
            if s <= t <= e:
                return True
        return False

    # 无人声区间（viv 的补集）——**规则 B 的密度必须只按"无人声时间"算**。
    # ⚠️ 第一版用固定 ±dens_win 窗口数音符，窗口会伸进相邻的人声段，而那里已被规则 A 填密
    #    （实测 3.57 音/秒）→ 局部密度判定永远"已达标"，规则 B 一个音都补不出来
    #    （现场症状：`无人声段伴奏 0 个`，无人声段右手空置仍占 82.7%）。
    _endv = max([n[1] for n in out] + [n[1] for n in mix_notes] or [0.0])
    nonv = []
    _prev = 0.0
    for a, b in viv:
        if a > _prev:
            nonv.append((_prev, a))
        _prev = max(_prev, b)
    if _endv > _prev:
        nonv.append((_prev, _endv))

    def dens_local(t):
        lo, hi = t - dens_win, t + dens_win
        t_seq = sum(max(0.0, min(hi, b) - max(lo, a)) for a, b in nonv)
        if t_seq <= 0.2:
            return 99.0                      # 几乎没有无人声时间 → 视为已达标
        n = len([x for x in out if lo <= x[0] <= hi and not in_vocal(x[0])])
        return n / t_seq

    # 右手现有音覆盖的空洞（≥ hole_min）——**含前奏（首个音之前）与尾奏（末个音之后）**。
    # ⚠️ 第一版只找"音与音之间"的空洞，导致"人声还没出来的前奏"这类头尾空段完全补不到
    #    （单元自检抓出来的：合成的 2.2~4.6s 空段一个音都没补）。
    rs = sorted((n[0], n[1]) for n in out)
    holes = []
    if rs:
        _end = max([n[1] for n in out] + [n[1] for n in mix_notes] or [0.0])
        if rs[0][0] >= hole_min:
            holes.append((0.0, rs[0][0]))
        prev_end = None
        for s0, e0 in rs:
            if prev_end is not None and s0 - prev_end >= hole_min:
                holes.append((prev_end, s0))
            prev_end = e0 if prev_end is None else max(prev_end, e0)
        if prev_end is not None and _end - prev_end >= hole_min:
            holes.append((prev_end, _end))
    elif mix_notes:
        # 右手完全为空：整首就是"一个空洞"（极端情形，仍要能跑而不崩）
        holes.append((0.0, max(n[1] for n in mix_notes)))
    # 只补"落在人声不活跃区"的那部分
    targets = []
    if _ruleB:
        for h in holes:
            for piece in _subtract_intervals(h, viv):
                if piece[1] - piece[0] >= hole_min:
                    targets.append(piece)
    if targets or _ruleB:
        # 候选：混音里**右手可用音区**、且不在人声活跃区内的音。
        # ⚠️ 只取 ≥split_pitch 是不够的：实测新曲间奏里整曲混音的 ≥C4 音符**只有 11 个**
        #    （低音区伴奏全在 C4 以下、被切给左手）→ 规则 B 等于没有素材。
        #    所以允许把中低音区（instr_lo 起）的伴奏**升八度**搬进右手音区 —— 这正是
        #    钢琴改编里"把伴奏的中间声部换到右手弹"的常规做法。
        cand = []
        for n in mix_notes:
            if not (instr_lo <= n[2] <= split_pitch + instr_reg):
                continue
            if (n[1] - n[0]) < 0.05 or in_vocal(n[0]):
                continue
            pr = n[2]
            while pr < split_pitch and pr + 12 <= 100:
                pr += 12
            cand.append((n[0], n[1], pr, n[3]))
        cand.sort(key=lambda n: (n[0], n[2]))
        # 每 0.10 s 取最高音（和弦 → 单音线，听感是"伴奏的旋律线"而不是糊块）
        thin = []
        i = 0
        while i < len(cand):
            t0 = cand[i][0]
            grp = []
            j = i
            while j < len(cand) and cand[j][0] <= t0 + 0.10:
                grp.append(cand[j])
                j += 1
            thin.append(max(grp, key=lambda n: n[2]))
            i = j
        st['targets'] = len(targets)
        last_b = -9.9
        gap_b = 1.0 / max(1e-6, instr_rate)
        for s, e, p, v in thin:
            if in_vocal(s):
                continue
            # 触发条件 = 「落在一个 ≥hole_min 的真空洞」**或**「局部密度低于目标」
            # ⚠️ 只用空洞做触发不够：右手音符平均间距 0.5 s、时值 ~0.3 s → 相邻音之间只空
            #    0.2 s，永远到不了 0.35 s 的空洞阈值（实测 added_instr 恒为 0）。
            #    真正的病症是**密度**：无人声段右手 0.94 音/秒 vs 有人声段 2.0。
            _in_hole = any(a <= s <= b for a, b in targets)
            if not _in_hole and dens_local(s) >= dens_target:
                continue
            if len(win_notes(s)) >= max_notes_per_win:
                st['skipped_win'] += 1
                continue
            if st['added_instr'] and (s - last_b) < gap_b:
                st['skipped_dens'] += 1
                continue
            # ⚠️ 这里**不能**用"不与已有音重叠"当守门（规则 A 用的是 busy_near，规则 B 不行）：
            #    `_simple_piano` 的右手在间奏里常有一条 1~2 s 的长音，重叠判定会把**每一个**
            #    伴奏候选都挡掉（现场症状：`无人声段伴奏 0 个`）。而钢琴伴奏本来就该在长音下
            #    叠和弦音 —— 所以只禁"起音撞车"，密度与每窗音数上限继续兜底。
            if any(abs(x[0] - s) <= onset_guard for x in out):
                st['skipped_onset'] += 1
                continue
            out.append((s, e, p, max(int(v), add_velocity_floor)))
            st['added_instr'] += 1
            last_b = s
        if st['added_instr'] == 0:
            st['instr_dbg'] = {'targets': len(targets), 'thin': len(thin),
                               'nonv_sec': round(sum(b - a for a, b in nonv), 1)}

    out.sort(key=lambda n: (n[0], n[2]))
    st['added'] = st['added_vocal'] + st['added_instr']
    st['n_right_before'] = len(right)
    st['n_right_after'] = len(out)
    return out, st


def _graft_vocal_melody(right, vline, min_len=0.08, pad=0.12, max_per_sec=3.0,
                        min_pitch=45):
    """把人声轨旋律「补」进右手：只在右手该时刻完全没音时补。

    t6（2026-09-20）依据 —— 见 `回归验收/人声消失审计.md` 与 `_vocal_audit/` 实测：
      · 出厂产物 = 回炉(简洁模式)产物：全曲混音整体识别 + 按音高切手（split_pitch=60）；
      · 低音区人声会被判给左手（jiabin 52.3% 人声时值在 C4 以下），人声不在最高音线时
        整段被伴奏顶掉 → 出厂出现数秒级旋律空洞（monitoring 任一手覆盖仅 70.35%、
        最长空洞 6.99s、>0.8s 断档 11 个）；
      · 分轨人声轨对低音区人声覆盖 65.9% vs 回炉 19.9%（3.3 倍）→ 是可靠补充源。

    设计纪律（避免踩已知的坑）：
      1) **只补空、不删不改**：右手在该音的时间窗内本来一个音都没有，才把人声轨的音
         加进右手；右手已有旋律处一律不动 → 不改既有和声、不产生重复加倍的音。
      2) **不改变回炉的取舍判据**：本函数在“回炉已被采纳”之后才调用，判据逻辑不变，
         所以不会让产物整体换轨（去-bass 代价不会漏进出厂产物）。
      3) 只接受音高 ≥ min_pitch 的音（过低的人声音大概率已在左手，补进右手没意义）。
      4) 密度上限 max_per_sec 个/秒，避免把右手塞爆。

    返回 (新的右手列表, 新增音符数)；异常由调用方捕获并回退原结果。
    """
    if not vline:
        return right, 0
    import bisect
    right = sorted(right, key=lambda n: (n[0], n[2]))
    if not right:
        keep = sorted((n for n in vline if n[2] >= min_pitch), key=lambda n: (n[0], n[2]))
        return keep, len(keep)
    starts = [n[0] for n in right]
    out = list(right)
    added, last_t = 0, -9.9
    for s, e, p, v in vline:
        if (e - s) < min_len or p < min_pitch:
            continue
        if added and (s - last_t) < (1.0 / max(1e-6, max_per_sec)):
            continue
        i = bisect.bisect_left(starts, s - pad)
        busy = False
        for k in range(max(0, i - 2), i):        # 跨过 s-pad 的长音也要算
            if right[k][1] > s - pad:
                busy = True
                break
        j = i
        while not busy and j < len(right) and right[j][0] <= e + pad:
            if right[j][1] > s - pad:            # 时间窗内有音 → 不是空洞
                busy = True
                break
            j += 1
        if busy:
            continue
        out.append((s, e, p, v))
        added += 1
        last_t = s
    out.sort(key=lambda n: (n[0], n[2]))
    return out, added


def _melody_similarity(orig_wav, piano_wav):
    """深度思考自检②：旋律保真度(原曲 vs 钢琴 WAV)。

    用 chroma(音高类特征)+ 全局 DTW 比对两者旋律：
    - 返回 (归一化 DTW 成本, 相似度)。成本 0=完全一致，成本越高越不像；
    - 相似度 = exp(-成本)，1=一致，0=完全不同。
    纯本地计算(秒级)，不联网、不依赖指纹库。
    """
    try:
        import numpy as np
        import librosa

        def _chroma(path):
            y, sr = librosa.load(path, sr=22050, mono=True, duration=90.0)
            c = librosa.feature.chroma_cens(y=y, sr=sr, hop_length=1024)
            step = max(1, int(c.shape[1] / 450))
            return c[:, ::step]

        ca = _chroma(orig_wav)
        cb = _chroma(piano_wav)
        if ca.shape[1] < 5 or cb.shape[1] < 5:
            return None
        d, _p = librosa.sequence.dtw(ca, cb, metric='cosine')
        cost = float(d[-1, -1]) / max(1, ca.shape[1])
        return cost, float(np.exp(-cost))
    except Exception:
        return None


def _estimate_tempo_from_audio(wav_path):
    """用 librosa 节拍跟踪直接从音频估 BPM，比音符间奏推断准得多。

    节拍不对齐的根源就是 BPM 估错(此前按 MIDI 默认 120 估)，导致乐谱
    量化网格整体错位。这里只取前 90 秒音频做节拍跟踪，快且稳定；
    失败返回 None，由调用方退回 _estimate_tempo。
    """
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(wav_path, sr=22050, mono=True, duration=90.0)
        tempo, _beats = librosa.beat.beat_track(y=y, sr=sr)
        t = float(np.asarray(tempo).ravel()[0])
        if 40.0 <= t <= 220.0:
            return round(t, 1)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 音轨分离（人声/鼓/贝斯/其他）——原理与识音(shiyin.notalabs.cn)一致：
# Demucs 深度学习模型做频谱掩码源分离 + Basic Pitch 逐轨识别。
# ---------------------------------------------------------------------------

def _beat_alignment_score(left, right, tempo):
    """深度思考自检①：节拍对齐度(规则奖励)。

    把所有音符起音按当前 BPM 的“四分音符网格”取余，若起音明显偏离
    网格(半拍以上)，说明 BPM 或相位不对，乐谱量化后会整体错位。
    返回 (错位比例 0~1, 平均偏移秒)。错位比例越低越好。
    """
    try:
        import numpy as np
        quarter = 60.0 / float(tempo)
        onsets = sorted(n[0] for n in (left or []) + (right or []))
        if len(onsets) < 8:
            return (0.0, 0.0)
        ons = np.asarray(onsets, dtype=np.float64)
        best_bad = 1.0
        best_off = 1000000000.0
        for k in range(8):
            ph = (ons - k * quarter / 8.0) % quarter
            off = np.minimum(ph, quarter - ph)
            bad = float(np.mean(off > quarter * 0.35))
            mean_off = float(np.mean(off))
            if bad < best_bad or (bad == best_bad and mean_off < best_off):
                best_bad, best_off = bad, mean_off
        return (best_bad, best_off)
    except Exception:
        return (0.0, 0.0)


_SEP_CACHE = {"model": None}


def separate_stems(audio_path, out_dir, base, progress, shifts=1):
    """Demucs 六轨分离(人声/鼓/贝斯/吉他/钢琴/其他)，写六轨 wav 并返回路径字典。

    用 htdemucs_6s：比四轨版进一步把伴奏拆出吉他(guitar)与钢琴(piano)，
    对“伴奏细分”需求更友好。首次运行自动下载模型(~100MB，缓存在用户目录)。
    失败(未装 demucs/无网/模型缺失)返回 None，由调用方回退整体分析——
    分离是“增强”，绝不影响主流程出谱。
    """
    try:
        import numpy as np
        import librosa
        import soundfile as sf
        from demucs.pretrained import get_model
        from demucs.apply import apply_model

        if _SEP_CACHE["model"] is None:
            progress("加载人声/伴奏分离模型(6 轨版，首次需下载约 100MB)…")
            _SEP_CACHE["model"] = get_model("htdemucs_6s")
        model = _SEP_CACHE["model"]
        model.cpu()

        progress("AI 正在分离人声/鼓/贝斯/吉他/钢琴/伴奏六轨…")
        # 自己用 librosa 读(不依赖 PATH 上的 ffprobe/ffmpeg)
        wav, sr = librosa.load(audio_path, sr=model.samplerate, mono=False)
        wav = np.atleast_2d(np.asarray(wav, dtype=np.float32))  # (ch, n)
        if wav.shape[0] == 1:
            wav = np.repeat(wav, 2, axis=0)
        wav = np.ascontiguousarray(wav)
        import torch
        wav = torch.from_numpy(wav)
        ref = wav.mean(0)
        wav = (wav - ref.mean()) / ref.std()
        sources = apply_model(model, wav[None], shifts=shifts, split=True,
                              overlap=0.25, device="cpu", progress=False)[0]
        sources = sources * ref.std() + ref.mean()

        paths = {}
        for name, src in zip(model.sources, sources):
            p = os.path.join(out_dir, f"{base}_{name}.wav")
            sf.write(p, src.numpy().T, model.samplerate, subtype="PCM_16")
            paths[name] = p
        progress("音轨分离完成。")
        return paths
    except Exception as e:
        progress(f"音轨分离不可用({type(e).__name__})，改用整体分析。")
        return None


# 【已停用 · 2026-09-19】_drum_to_bass_stabs() 原为「把鼓点映射成强化贝斯起音」。
# 【自 2026-09-19 起不再调用，保留仅为回退】用户要求贝斯不参与转谱后，本函数在
# 全流程已无任何调用点（0 引用）；它输出的短音本来靠「与贝斯线合并」才成立，
# 贝斯既已退出，这条链路自然作废。保留函数体只是为了可回退/可查阅，不参与出谱。
def _drum_to_bass_stabs(drum_notes, bass_notes, tol=0.08):
    """把鼓点映射成“强化贝斯起音”——钢琴版保留鼓的律动又不添乱。【已停用：零调用】

    鼓的音高是识别噪声(基本无意义)，直接丢弃；与贝斯起音对齐(±tol)
    的鼓点(多为底鼓)用**贝斯同音高**输出一个短音，靠合并逻辑与贝斯
    线融为一体(起音更有力)。对不齐的(军鼓/噪声)跳过。
    """
    from bisect import bisect_right
    if not drum_notes or not bass_notes:
        return []
    bass_sorted = sorted(bass_notes, key=lambda x: x[0])
    bass_starts = [b[0] for b in bass_sorted]
    stabs = []
    for s, e, _p, v in drum_notes:
        if e - s > 0.4:   # 只取短促的敲击，长音是识别拖尾
            continue
        idx = bisect_right(bass_starts, s)
        best = None
        for j in (idx - 1, idx):
            if 0 <= j < len(bass_sorted) and abs(bass_sorted[j][0] - s) <= tol:
                best = bass_sorted[j]
                break
        if best is not None:
            stabs.append((s, s + 0.12, best[2], max(v, 95)))
    return stabs


def _sparsify_harmony(notes, min_gap=0.35):
    """和声轨抽稀：每 min_gap 秒只保留力度最强的一个音。

    和声轨(其他轨)几乎每个窗口都换一批音，全部进左手会很乱。
    抽稀后左手只剩稀疏的和声点，与贝斯线形成干净的伴奏织体。
    """
    if not notes:
        return notes
    notes = sorted(notes, key=lambda x: x[0])
    out = []
    win_start = None
    best = None
    for s, e, p, v in notes:
        if win_start is None or s - win_start >= min_gap:
            if best is not None:
                out.append(best)
            win_start = s
            best = (s, e, p, v)
        else:
            if v > best[3]:
                best = (s, e, p, v)
    if best is not None:
        out.append(best)
    return out


def _suppress_pad_notes(notes, max_len=0.7, min_pitch=62):
    """抑制伴奏里的“长音铺垫”(提琴/弦乐持续长音)。

    提琴等弦乐铺垫的特征是中高音区的持续长音(时值长、跨度稳)，
    不像钢琴和声点短促。对 (pitch>=min_pitch 且 时值>=max_len) 的音，
    截短到时值上限(保留它作为和声点但不再像长铺垫)，低于音区或
    短时值的音(贝斯/正常和声点)不受影响。
    """
    if not notes:
        return notes
    out = []
    for s, e, p, v in notes:
        if p >= min_pitch and (e - s) >= max_len:
            # 截短为短和声点：起音保留，时长压到接近和声点
            out.append((s, s + max_len * 0.5, p, v))
        else:
            out.append((s, e, p, v))
    return out


def _filter_high_hallucination(notes, high_pitch=79, neighbor=0.22):
    """过滤和弦轨的“高音幻觉”(非钢琴声误识别出的孤立超高音)。

    ByteDance 是纯钢琴模型，喂给它吉他/弦乐等非钢琴声时，会在高音区
    幻觉出孤立、无和弦支撑的怪音(听起来像“莫名其妙冒出来的高音”)。
    判定：音高 >= high_pitch(默认 G5=79)且 ±neighbor 秒内没有其它
    同时发声的音(即不是和弦成员、孤立出现)——这类音丢弃。
    和弦里的正常高音(与其他音同时响)不受影响。
    """
    if not notes:
        return notes
    notes = sorted(notes, key=lambda n: n[0])
    starts = [n[0] for n in notes]
    from bisect import bisect_left
    keep = []
    for i, (s, e, p, v) in enumerate(notes):
        if p < high_pitch:
            keep.append((s, e, p, v))
            continue
        lo = bisect_left(starts, s - neighbor)
        hi = bisect_left(starts, s + neighbor)
        has_neighbor = False
        for j in range(lo, hi):
            if j == i:
                continue
            ns, ne = notes[j][0], notes[j][1]
            if ns <= e and ne >= s:
                has_neighbor = True
                break
        if has_neighbor:
            keep.append((s, e, p, v))
    return keep


def _track_lead_line(notes, window=0.12, max_step=7, min_gap=0.12):
    """提取连贯的单声部主旋律线(前奏/尾奏用)。

    1) 每个起音簇(±window)取最高音(主奏乐器通常在最上方)；
    2) 按时间顺序做连续性跟踪：与前一音偏差 > max_step 时按八度
       折叠回连续区间，仍差太远则跳过该簇(视为噪声)——出来的旋律
       是一条连贯的线，而不是上下乱跳的最高音拼凑；
    3) 与上一保留音间隔 < min_gap 时合并(避免连串碎音)。

    返回 (start, end, pitch, velocity) 列表。
    """
    if not notes:
        return []
    notes = sorted(notes, key=lambda x: x[0])
    clusters = []  # (start, end, top_pitch, velocity)
    i = 0
    while i < len(notes):
        t0 = notes[i][0]
        j = i
        group = []
        while j < len(notes) and notes[j][0] <= t0 + window:
            group.append(notes[j])
            j += 1
        i = j
        top = max(group, key=lambda x: x[2])
        clusters.append((top[0], max(n[1] for n in group), top[2], top[3]))
    out = []
    prev = None
    for s, e, p, v in clusters:
        p2 = p
        if prev is not None:
            while p2 - prev > max_step:
                p2 -= 12
            while prev - p2 > max_step:
                p2 += 12
            if abs(p2 - prev) > max_step + 4:
                continue   # 无法连续 → 噪声簇，跳过
        if out and s - out[-1][0] < min_gap:
            if (e - s) > (out[-1][1] - out[-1][0]) or v > out[-1][3] + 10:
                out[-1] = (s, e, p2, max(v, out[-1][3]))
            continue
        out.append((s, e, p2, v))
        prev = p2
    return out


def _find_vocal_gaps(vocal_notes, other_notes, gap_thresh=2.2):
    """找出人声空档(前奏/间奏/尾奏)的起止时间 [(start, end), ...]。

    阈值取 2.2 秒(比早期 1.5 更保守)：Basic Pitch 对弱音/气声/连音
    偶尔漏识别，若阈值太小，乐句中间的正常停顿会被误判成"间奏"，
    导致人声旋律被整段丢弃、在旋律中"突然消失"。只有真正较长的
    器乐段(前奏/间奏/尾奏)才判定为空档。
    """
    vs = sorted(vocal_notes, key=lambda x: x[0])
    if not vs:
        return []
    gaps = []
    if vs[0][0] > gap_thresh:
        gaps.append((0.0, vs[0][0]))
    for (s1, e1, _p1, _v1), (s2, e2, _p2, _v2) in zip(vs, vs[1:]):
        if s2 - max(e1, s1) > gap_thresh:
            gaps.append((e1, s2))
    # 尾奏：最后一句人声之后到乐曲结束的空档
    song_end = max([e for _s, e, _p, _v in other_notes] + [vs[-1][1]])
    if song_end - vs[-1][1] > gap_thresh:
        gaps.append((vs[-1][1], song_end))
    return gaps


def _fill_melody_gaps(vocal_notes, other_notes, gap_thresh=2.2, gaps=None):
    """人声空档(前奏/间奏/尾奏)用和声轨的“最高音线”填充主旋律。

    前奏/间奏的主奏乐器通常位于同时发声的最高音——对空档内的
    和声轨音符按起音簇取最高音线(与整体分析的启发式分手一致)，
    而不是把中高音全塞进旋律。这样前奏的旋律线才还原、干净。

    人声优先保证：空档判定保守(阈值 2.2s)，且短空档(≤2.5s)内
    若和声轨找不到连续主奏线，用相邻人声音高线性桥接，避免
    “人声旋律突然消失”的断档。
    """
    if not vocal_notes:
        top_line, _rest = _split_melody_accomp(other_notes)
        return top_line
    if gaps is None:
        gaps = _find_vocal_gaps(vocal_notes, other_notes, gap_thresh=gap_thresh)
    if not gaps:
        return vocal_notes
    fill = []
    vocals_sorted = sorted(vocal_notes, key=lambda x: x[0])
    for gs, ge in gaps:
        gap_notes = [(s, e, p, v) for s, e, p, v in other_notes if gs <= s < ge]
        # 前奏/尾奏的器乐主旋律：连续性跟踪成一条线(修正八度乱跳)，
        # min_gap=0.18 保持正常旋律密度(太稀会像被删掉)
        line = _track_lead_line(gap_notes, min_gap=0.18, max_step=6)
        if line:
            # 前奏/尾奏的主旋律要明显压过伴奏：力度抬到 ≥100(在右手归一化后
            # 位于最上层)，否则器乐段旋律线会被左手伴奏盖住
            fill.extend((s, e, p, max(v, 100)) for s, e, p, v in line)
            continue

        # 短空档(≤2.5s)且和声轨找不到连续主奏线：用相邻人声音高线性桥接，
        # 避免「人声旋律突然消失」。真正长的间奏不桥接。
        if ge - gs > 2.5:
            continue
        before = [n for n in vocals_sorted if n[1] <= gs + 0.0001]
        after = [n for n in vocals_sorted if n[0] >= ge - 0.0001]
        if not before or not after:
            continue
        bp = before[-1][2]
        ap = after[0][2]
        bridge = []
        t = gs
        seg = 0.5
        n_steps = max(1, int(round((ge - gs) / seg)))
        for k in range(1, n_steps):
            frac = k / n_steps
            p = int(round(bp + (ap - bp) * frac))
            t0 = gs + (ge - gs) * (k - 1) / n_steps
            t1 = gs + (ge - gs) * k / n_steps
            bridge.append((t0, t1, p, 95))
        fill.extend(bridge)
    return vocal_notes + fill

def transcribe_stems(stems, model_path, progress, out_dir=None):
    """分离轨 → 钢琴谱数据：

    - 人声轨 → 主旋律(右)；前奏/间奏/尾奏人声空档用和声轨最高音线补旋律；
    - 和声轨(piano/guitar/other 合并)抽稀后进左手和声点；
    - 贝斯轨【不参与】：既不入伴奏合并轨、也不单独进左手(2026-09-19 用户要求)。
      注意这里【读取 bass 关键字的次数为 0】——即使 stems 里完全没有 'bass'，
      本函数也不会 KeyError；缺轨时行为与有轨完全一致(天然降级)。
    - 融合修改：碎音合并/legato/伴奏释放/力度分层/踏板 → 可弹钢琴。

    人声轨音符过少(纯器乐/分离失败)时返回 None，调用方回退整体分析。
    返回 (midi_data, left, right)。
    """
    from basic_pitch.inference import Model
    try:
        model = Model(model_path)
    except Exception:
        return None

    def notes_of(path, label, min_len=150):
        return transcribe_notes(path, model, progress, label=label, min_len=min_len)

    # 人声用 Basic Pitch；人声是单旋律，不需要多声部模型
    # TS_LANG_SEG=1 时按语种分段扒谱（分轨之后、扒谱之前）：
    # 先对该人声轨做语种分割，每段用它自己语种的预设识别，再按全局时间轴拼回。
    # 默认关闭；任何一步失败都退回整轨识别。
    if os.environ.get('TS_LANG_SEG', '0') == '1':
        try:
            from lang_pipeline import transcribe_vocal_by_language
            vocal_notes, _lsinfo = transcribe_vocal_by_language(
                stems['vocals'], notes_of, progress, out_dir=out_dir, label='人声旋律',
                force=True)
            progress('语种分段扒谱：%s（后端 %s）'
                     % (_lsinfo.get('reason') or '已启用', _lsinfo.get('backend') or '?'))
        except Exception as _lse:
            progress('语种分段不可用(%s)，按整轨识别。' % type(_lse).__name__)
            vocal_notes = notes_of(stems['vocals'], '人声旋律', min_len=60)
    else:
        vocal_notes = notes_of(stems['vocals'], '人声旋律', min_len=60)
    if len(vocal_notes) < 10:
        progress('人声轨音符过少，退回整体分析…')
        return None

    # 识别分工：人声=右手主旋律；左手伴奏按响度选最响的一轨
    _mn, main_path = _merge_accomp_stems(stems, progress, out_dir=out_dir)
    if main_path:
        other_notes = notes_of(main_path, '和声伴奏')
    else:
        other_notes = notes_of(stems.get('other', stems['vocals']), '和声伴奏')

    progress('正在融合人声/和声并调整成可弹钢琴谱…')
    gaps = _find_vocal_gaps(vocal_notes, other_notes)

    def _in_gap(s):
        return any(gs <= s < ge for gs, ge in gaps)

    def _in_long_gap(s):
        return any(gs <= s < ge and (ge - gs) > 2.5 for gs, ge in gaps)

    # 人声取最高音线=主旋律；合并合成人声抖动碎音；仅长器乐段丢弃人声
    vocal_notes = [n for n in vocal_notes if not _in_long_gap(n[0])]
    vocal_notes = _dejitter_melody(vocal_notes)
    melody = _fill_melody_gaps(vocal_notes, other_notes, gaps=gaps)

    # R2：other 轨（电子音/合成器）单独识别一份，只并入无人声段（同 enhanced 路径）
    _ab = _accomp_boost_params()
    other_extra = None
    if _ab['on'] and _ab['other'] and _mn != 'other':
        _op = stems.get('other')
        if _op and os.path.isfile(_op):
            try:
                other_extra = notes_of(_op, '其他轨(电子音)', min_len=60)
                progress('无人声段伴奏加强：other 轨单独识别 %d 个音，只并入纯伴奏段。'
                         % len(other_extra))
            except Exception as _oe:
                progress('other 轨单独识别不可用(%s)，跳过。' % type(_oe).__name__)
                other_extra = None

    # 多和声音进左手：有人声段保持原样（抑长铺垫→抽稀 0.9s）；
    # 无人声段按 R2 放宽阈值并并入 other 电子音
    # 注意本路径不做 _filter_high_hallucination（沿旧行为，halluc=False）
    accomp = _build_accomp(other_notes, _in_gap, gaps, min_gap=0.9,
                           halluc=False, extra=other_extra)
    midi_data, left, right = fuse_to_piano(melody, accomp)
    return midi_data, left, right

def _build_hand(name, notes, add_pedal=True):
    """构造一只手对应的钢琴音轨。轨道名只用 ASCII，避免 MIDI latin-1 报错。"""
    from pretty_midi import Instrument, Note
    inst = Instrument(program=0, name=name)
    inst.notes = [Note(velocity=v, pitch=p, start=s, end=e) for s, e, p, v in notes]
    if add_pedal:
        # 延音踏板：只对够长的音(≥0.30s)，且把事件合并成连续区间——
        # 重叠/相连的长音只踩一次、最后一个音结束才抬起。否则踏板
        # 快速开关会让音量“一抖一抖”。值 75 轻踏板，只给左手。
        from pretty_midi import ControlChange
        long_notes = sorted(
            [(s, e) for s, e, _p, _v in notes if e - s >= 0.30], key=lambda x: x[0])
        regions = []
        for s, e in long_notes:
            if regions and s - regions[-1][1] <= 0.25:
                regions[-1] = (regions[-1][0], max(regions[-1][1], e))
            else:
                regions.append((s, e))
        for s, e in regions:
            inst.control_changes.append(ControlChange(number=64, value=75, time=s))
            inst.control_changes.append(ControlChange(number=64, value=0, time=e))
        # 排序控制事件：同时间点先踩下再抬起，避免时序错乱
        inst.control_changes.sort(key=lambda cc: (cc.time, 0 if cc.value > 0 else 1))
    return inst


# ---------------------------------------------------------------------------
# MusicXML 大谱表输出（单 part，两行谱表，花括号连接）
# ---------------------------------------------------------------------------

def _pitch_to_musicxml(pitch):
    """MIDI 音高 -> (step, alter, octave)。"""
    names = ['C', 'D', 'E', 'F', 'G', 'A', 'B']
    semis = [0, 2, 4, 5, 7, 9, 11]
    alter_map = {0: 0, 1: 1, 2: 0, 3: 1, 4: 0, 5: 0, 6: 1, 7: 0, 8: 1, 9: 0, 10: 1, 11: 0}
    step = names[semis.index(min(semis, key=lambda s: abs(s - (pitch % 12))))]
    return step, alter_map[pitch % 12], pitch // 12 - 1


def _xml_note(pitch, dur, voice, staff, chord=False, tie_start=False, tie_stop=False):
    L = ['      <note>']
    if chord:
        L.append('        <chord/>')
    step, alter, octave = _pitch_to_musicxml(pitch)
    L.append(f'        <pitch><step>{step}</step>')
    if alter:
        L.append(f'          <alter>{alter}</alter>')
    L.append(f'        <octave>{octave}</octave></pitch>')
    L.append(f'        <duration>{dur}</duration>')
    if tie_start:
        L.append('        <tie type="start"/>')
    if tie_stop:
        L.append('        <tie type="stop"/>')
    L.append(f'        <voice>{voice}</voice>')
    L.append(f'        <staff>{staff}</staff>')
    if tie_start or tie_stop:
        L.append('        <notations>')
        if tie_start:
            L.append('          <tied type="start"/>')
        if tie_stop:
            L.append('          <tied type="stop"/>')
        L.append('        </notations>')
    L.append('      </note>')
    return L


def _xml_rest(dur, voice, staff):
    """单个休止符，时长 dur(divisions)。连续空拍会先合并再写，谱面干净。"""
    return ['      <note><rest/>',
            f'        <duration>{dur}</duration>',
            f'        <voice>{voice}</voice>',
            f'        <staff>{staff}</staff>',
            '      </note>']


def _staff_lines(notes, bar, bar_div, voice, staff):
    """生成一个 staff 在某小节的单声部音符/休止行。

    notes 元素为 (slot, pitch, dur, tie_start, tie_stop)，其中跨小节的
    长音已在写入前按小节线拆分(每段 ≤ bar_div)。

    转谱数据存在真实的多声部叠加(同一 hand 里整小节持续和弦之上又叠加旋律音)。
    单声部 MusicXML 无法线性叠加——原实现用“延音线豁免跳过”导致小节超时值，
    MuseScore 整体静默崩溃。这里改为：同一槽位组成一个和弦(按音高去重、
    组内时值统一)；若后续还有更早 onset 的重叠音符，把当前和弦截短到下一
    onset。这样每小节恰好填满 bar_div，永不超时值、不崩溃，且所有音符起点
    与跨小节延音线都保留(仅持续音缩短)。
    """
    # 本小节、按槽位分组；同槽位内按音高去重，保留时值最长者
    chords = {}   # slot_local -> {pitch: (dur, tie_start, tie_stop)}
    for (slot, p, dur, t_s, t_e) in notes:
        if slot // bar_div != bar:
            continue
        sl = slot % bar_div
        bucket = chords.setdefault(sl, {})
        if p in bucket:
            d0, ts0, te0 = bucket[p]
            # 保留最长的时值，同时 OR 上任何一份的 tie 标志：
            # 否则同槽位等长去重时，先到者的“无 tie”会吞掉后到者的
            # 跨小节 tie 起点，造成下一小节出现孤儿 stop
            bucket[p] = (max(d0, dur), t_s or ts0, t_e or te0)
        else:
            bucket[p] = (dur, t_s, t_e)

    slots = sorted(chords)
    out = []
    covered = 0
    for i, sl in enumerate(slots):
        members = sorted(chords[sl].items(), key=lambda kv: kv[0])   # 按音高
        grp_dur = max(d for _p, (d, _ts, _te) in members)
        nxt = slots[i + 1] if i + 1 < len(slots) else bar_div
        dur = max(1, min(grp_dur, nxt - sl))    # 截短以不越过下一 onset
        if covered < sl:
            out += _xml_rest(sl - covered, voice, staff)  # 连续空拍合并为一个休止
            covered = sl
        # 第一个为父音符，其余为 <chord/>，全部共享统一时值 dur(合法)
        for k, (p, (_d, ts, te)) in enumerate(members):
            out += _xml_note(p, dur, voice, staff, chord=(k > 0), tie_start=ts, tie_stop=te)
        covered = sl + dur
    if covered < bar_div:
        out += _xml_rest(bar_div - covered, voice, staff)
    return out


def _total_bars(notes, bpm, bar_div=16):
    """按音符最晚结束时间估算总小节数（至少 1）。"""
    DIV = 4
    quarter = 60.0 / bpm
    if not notes:
        return 1
    ends = [int(round(e / quarter * DIV)) for _s, e, _p, _v in notes]
    return max(1, (max(ends) + bar_div - 1) // bar_div)


# 强弱记号：小节平均力度 -> 记号(阈值), 以及 MuseScore 回放百分比
_DYN_LEVELS = [(55.0, "p"), (70.0, "mp"), (85.0, "mf"), (100.0, "f"), (999.0, "ff")]
_DYN_SOUND = {"p": 49, "mp": 64, "mf": 80, "f": 96, "ff": 112}


def _dynamic_level(mean_vel):
    for thresh, sym in _DYN_LEVELS:
        if mean_vel < thresh:
            return sym
    return "ff"


def _xml_direction(sym, staff, n_hands):
    """一个 <direction> 强弱记号。低音谱(左手)放谱表下方，高音谱放上方。"""
    placement = "below" if (staff == 1 and n_hands > 1) else "above"
    return [f'      <direction placement="{placement}">',
            '        <direction-type>',
            f'          <dynamics><{sym}/></dynamics>',
            '        </direction-type>',
            f'        <staff>{staff}</staff>',
            f'        <sound dynamics="{_DYN_SOUND[sym]}"/>',
            '      </direction>']


def build_score_xml(hands, bpm=120.0, with_ties=True, n_bars=None, splice=None):
    """把 1~2 只手的音符写成 MusicXML 文本。

    hands: [(clef_sign, clef_line, notes)]，notes 元素为
    (start, end, pitch, velocity)——velocity 属于 MIDI 播放，乐谱 XML 忽略。
    两只手时生成大谱表(两行谱表)；一只手时生成单行谱表。
    with_ties=False 时完全省略延音线(供渲染回退用)。
    n_bars 可选，用于让分谱与整曲小节数保持一致。
    splice 可选：{小节号: {staff: [(slot, midi, dur, tie_s, tie_e)]}}
    (16 分音符槽位)——参考谱拼接小节，直接按槽位写入。
    """
    bpm = float(bpm)
    if not (30.0 <= bpm <= 240.0):
        bpm = 120.0
    DIV = 4        # 每四分音符的 divisions = 4 → 16 分音符网格
    quarter = 60.0 / bpm
    bar_div = 16   # 4/4 每小节 = 16 个 16 分音符

    def to_div(t):
        # 生成的中间声部保持 8 分音符粒度(谱面干净，无 16 分碎音)；
        # 参考谱拼接小节由 splice 直接提供 16 分槽位，不受此限制
        return int(round(t / quarter * DIV / 2.0)) * 2

    total_div = max(
        [to_div(e) for _sign, _line, notes in hands for _s, e, _p, _v in notes] + [1]
    )
    n_calc = max(1, (total_div + bar_div - 1) // bar_div)
    if n_bars is not None:
        n_calc = max(n_calc, int(n_bars))

    def norm_split(notes):
        """转为 divisions，并把跨小节的长音拆成逐小节的片段、加延音线(tie)。

        关键：不拆的话长音会整段塞进起始小节，导致小节内容超时值，
        MuseScore 会整体拒绝渲染(静默崩溃)。拆分后每小节恰好填满 bar_div。

        延音线方向遵循 MusicXML 规范：一段 tie 的起点在开始小节(给 start)，
        终点在结束小节(给 stop)；中间段两者都有。注意 ed 用 (ed-1)//bar_div
        定位“最后一个发声的小节”，避免恰好结束在小节线上的音被误加 start。
        """
        segs = []
        for s, e, p, _v in notes:
            sd = to_div(s)
            ed = max(sd + 1, to_div(e))
            b0 = sd // bar_div
            b1 = (ed - 1) // bar_div
            for b in range(b0, b1 + 1):
                ss = max(sd, b * bar_div)
                ee = min(ed, (b + 1) * bar_div)
                if ee <= ss:
                    continue
                if with_ties:
                    segs.append((ss, p, ee - ss, b < b1, b > b0))
                else:
                    segs.append((ss, p, ee - ss, False, False))
        return segs

    hands_norm = [(_sign, _line, norm_split(notes)) for _sign, _line, notes in hands]
    n_hands = len(hands_norm)

    X = []
    X.append('<?xml version="1.0" encoding="UTF-8"?>')
    X.append('<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
             '"http://www.musicxml.org/dtds/partwise.dtd">')
    X.append('<score-partwise version="3.1">')
    X.append('  <part-list>')
    X.append('    <score-part id="P1">')
    X.append('      <part-name>Piano</part-name>')
    X.append('      <part-abbreviation>Pno.</part-abbreviation>')
    X.append('      <score-instrument id="P1-I1"><instrument-name>Piano</instrument-name></score-instrument>')
    X.append('      <midi-device id="P1-I1"></midi-device>')
    X.append('      <midi-instrument id="P1-I1"><midi-channel>1</midi-channel><midi-program>1</midi-program></midi-instrument>')
    X.append('    </score-part>')
    X.append('  </part-list>')
    X.append('  <part id="P1">')

    bar_dur = bar_div / DIV * quarter   # 每小节秒数
    last_dyn = {}                       # staff -> 最近一次标注的强弱记号

    for bar in range(n_calc):
        X.append(f'    <measure number="{bar + 1}">')
        if bar == 0:
            X.append('      <attributes>')
            X.append(f'        <divisions>{DIV}</divisions>')
            X.append('        <key><fifths>0</fifths></key>')
            X.append('        <time><beats>4</beats><beat-type>4</beat-type></time>')
            if n_hands > 1:
                X.append(f'        <staves>{n_hands}</staves>')
            for i, (sign, line, _segs) in enumerate(hands_norm, start=1):
                num = f' number="{i}"' if n_hands > 1 else ""
                X.append(f'        <clef{num}><sign>{sign}</sign><line>{line}</line></clef>')
            X.append('      </attributes>')
        # 强弱记号：按本小节平均力度映射 p/mp/mf/f/ff，
        # 只有力度档位变化时才标注(谱面干净不啰嗦)
        bar_start = bar * bar_dur
        bar_end = bar_start + bar_dur
        for i, (_sign, _line, raw_notes) in enumerate(hands, start=1):
            vs = [v for s, e, _p, v in raw_notes if s < bar_end and e > bar_start]
            if vs:
                sym = _dynamic_level(sum(vs) / len(vs))
                if last_dyn.get(i) != sym:
                    last_dyn[i] = sym
                    X += _xml_direction(sym, i, n_hands)
        for i, (_sign, _line, segs) in enumerate(hands_norm, start=1):
            if splice and bar in splice and i in splice[bar]:
                # 参考谱拼接小节: 直接按 16 分槽位写入
                X += _staff_lines(splice[bar][i], bar, bar_div, i, i)
            else:
                X += _staff_lines(segs, bar, bar_div, i, i)
        X.append('    </measure>')
    X.append('  </part>')
    X.append('</score-partwise>')

    return '\n'.join(X)


def write_grand_staff_xml(left, right, path, bpm=120.0, with_ties=True,
                          n_bars=None, splice=None):
    """把左右手音符写成单个钢琴大谱表 MusicXML（两行谱表，花括号连接）。"""
    txt = build_score_xml(
        [("F", 4, left), ("G", 2, right)], bpm=bpm, with_ties=with_ties,
        n_bars=n_bars, splice=splice,
    )
    with open(path, 'w', encoding='utf-8') as f:
        f.write(txt)


def write_single_staff_xml(notes, path, bpm=120.0, clef=("G", 2),
                           with_ties=True, n_bars=None):
    """把单只手写成单行谱表 MusicXML（渲染回退/分谱用）。"""
    txt = build_score_xml(
        [(clef[0], clef[1], notes)], bpm=bpm, with_ties=with_ties, n_bars=n_bars
    )
    with open(path, 'w', encoding='utf-8') as f:
        f.write(txt)


def _valid_pdf(path):
    """PDF 产物必须真实有效：存在、非空、以 %PDF 魔数开头。

    实测 MuseScore 4 可能以崩溃码退出(0xC0000005/1320)但仍写出有效 PDF，
    也可能静默失败什么都不写。只看文件是否"存在"会被假成功或半成品骗过，
    因此必须校验魔数。
    """
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def _valid_wav(path):
    try:
        if os.path.getsize(path) <= 44:
            return False
        with open(path, "rb") as f:
            return f.read(4) == b"RIFF"
    except OSError:
        return False


# 本程序的输出文件命名后缀(用于“生成新歌前清理上次产物”)
_OUTPUT_SUFFIXES = (
    "_piano.mid", "_五线谱.pdf", "_大谱表.xml", "_钢琴.wav",
    "_vocals.wav", "_drums.wav", "_bass.wav", "_other.wav",
    "_大谱表_无延音线.xml", "_左手谱.pdf", "_右手谱.pdf",
    "_左手谱.xml", "_右手谱.xml", "_tmp_bp.wav",
)


def _clean_previous_outputs(out_dir, keep_audio, progress):
    """删除输出目录里上一次转谱生成的文件，让目录只留本次产物。

    只删符合输出命名后缀的文件；输入音频本身绝不删除
    (用户常把输出目录设在音频所在目录)。
    """
    keep = os.path.normcase(os.path.abspath(keep_audio))
    removed = 0
    try:
        for f in os.listdir(out_dir):
            p = os.path.join(out_dir, f)
            if not os.path.isfile(p):
                continue
            if os.path.normcase(os.path.abspath(p)) == keep:
                continue
            if f.endswith(_OUTPUT_SUFFIXES):
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    if removed:
        progress(f"已清理上次生成的 {removed} 个文件。")


def _render_once(ms_exe, src, out, timeout=300):
    """跑一次 MuseScore 渲染，返回退出码；超时/无法启动返回 None。不判成败。"""
    # 先清掉旧产物：MuseScore 4 遇到已存在的输出可能拒绝覆盖或改名(-1.pdf)，
    # 造成“渲染成功但找不到预期文件”的假失败。加 -f 双保险。
    if os.path.isfile(out):
        try:
            os.remove(out)
        except OSError:
            pass
    try:
        r = subprocess.run(
            [ms_exe, "-f", "-o", out, src],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",   # 防 GBK/UTF-8 混合输出解码崩溃
            timeout=timeout,
        )
        return r.returncode
    except (subprocess.TimeoutExpired, OSError):
        return None


def _render_until_valid(ms_exe, src, out, what, progress, attempts=2, timeout=180):
    """反复渲染直到产物通过有效性校验；全部失败返回 False。"""
    for i in range(attempts):
        _render_once(ms_exe, src, out, timeout=timeout)
        if os.path.isfile(out) and _valid_pdf(out):
            progress(f"{what}已生成。")
            return True
        progress(f"{what}第 {i + 1} 次渲染未成功，重试…")
        time.sleep(1.5)
    return False


def render_wav(ms_exe, midi_path, out_wav, progress):
    """渲染钢琴音色 WAV（来自 MIDI），带重试与有效性校验。"""
    progress("渲染钢琴音色(WAV)…")
    for i in range(2):
        _render_once(ms_exe, midi_path, out_wav, timeout=300)
        if _valid_wav(out_wav):
            progress("钢琴音色已生成。")
            return
        progress(f"钢琴音色第 {i + 1} 次渲染未成功，重试…")
        time.sleep(1.5)
    raise RuntimeError("MuseScore 多次尝试仍无法生成钢琴音色 WAV。")


def render_score_pdf(ms_exe, xml_path, pdf_path, left, right, bpm,
                     base, out_dir, progress, n_bars=None, splice=None):
    """保证五线谱 PDF 一定产出，失败自动降级。

    级联顺序：
      1. 主大谱表(含延音线) → 重试
      2. 无延音线的大谱表(排除 tie 干扰) → 重试
      3. 左右手分谱(两张单行谱 PDF，小节数与整曲一致)

    返回 PDF 路径列表：成功时 1 个(大谱表)，分谱回退时 2 个。
    全部失败则抛异常——宁可报错也不允许“有曲子没谱”。
    """
    # 1) 主大谱表(含延音线)
    progress("排版五线谱(PDF)…")
    if _render_until_valid(ms_exe, xml_path, pdf_path, "五线谱", progress):
        return [pdf_path]

    # 2) 无延音线的大谱表
    xml_simple = os.path.join(out_dir, f"{base}_大谱表_无延音线.xml")
    write_grand_staff_xml(left, right, xml_simple, bpm=bpm, with_ties=False,
                          n_bars=n_bars, splice=splice)
    progress("主谱排版失败，改用无延音线版本重试…")
    if _render_until_valid(ms_exe, xml_simple, pdf_path, "无延音线版", progress):
        return [pdf_path]

    # 3) 最后手段：左右手分谱
    n_bars = max(_total_bars(left, bpm), _total_bars(right, bpm))
    progress("改用左右手分谱(两张单行五线谱)…")
    lpdf = os.path.join(out_dir, f"{base}_左手谱.pdf")
    rpdf = os.path.join(out_dir, f"{base}_右手谱.pdf")
    xml_l = os.path.join(out_dir, f"{base}_左手谱.xml")
    xml_r = os.path.join(out_dir, f"{base}_右手谱.xml")
    write_single_staff_xml(left, xml_l, bpm=bpm, clef=("F", 4),
                           with_ties=False, n_bars=n_bars)
    write_single_staff_xml(right, xml_r, bpm=bpm, clef=("G", 2),
                           with_ties=False, n_bars=n_bars)
    got = []
    if _render_until_valid(ms_exe, xml_l, lpdf, "左手谱", progress):
        got.append(lpdf)
    if _render_until_valid(ms_exe, xml_r, rpdf, "右手谱", progress):
        got.append(rpdf)
    if got:
        return got

    raise RuntimeError(
        "MuseScore 多次尝试仍无法生成五线谱 PDF。"
        "若 MuseScore 程序当前正在打开，请先关闭它再重试。"
    )


def run_pipeline(audio_path, out_dir, model_path, ms_exe, ffmpeg, progress,
                 use_separation=True, simple_mode=False, use_mt3=False):
    """完整管线，progress(str) 用于回报状态。返回产物路径字典。

    两种模式：
      - simple_mode=False(默认)：音轨分离(人声/鼓/贝斯/其他) → 逐轨识别
        → 融合(旋律=人声, 伴奏=贝斯+其他) → 可弹钢琴谱；
      - simple_mode=True(简洁模式)：不分轨，整体识别 + 按音高切左右手，
        经典流程，更快更稳定。
      - use_mt3=True(AI 智能识别增强)：分轨时用 ByteDance 钢琴转录模型
        重点识别和弦(人声→和弦→贝斯，CPU 接近实时，比 MT3 快约 6 倍)；
        不分轨则整曲 MT3(限时 90 秒)。失败自动回退常规流程。

    核心保证：要么三样产物(MIDI/钢琴WAV/五线谱PDF)全部有效生成，
    要么抛异常报错——绝不允许出现“生成了曲子却没有对应五线谱”的状态。
    """
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(audio_path))[0]

    # 开始前清理上一次的产物(保留原始音频)
    _clean_previous_outputs(out_dir, audio_path, progress)

    wav_for_bp = os.path.join(out_dir, f'{base}_tmp_bp.wav')
    midi_path = os.path.join(out_dir, f'{base}_piano.mid')
    xml_path = os.path.join(out_dir, f'{base}_大谱表.xml')
    pdf_path = os.path.join(out_dir, f'{base}_五线谱.pdf')
    out_wav = os.path.join(out_dir, f'{base}_钢琴.wav')

    if not ms_exe:
        raise RuntimeError('未找到 MuseScore4.exe，请先安装 MuseScore 4。')

    # 网易云 .ncm 加密文件先解密
    if os.path.splitext(audio_path)[1].lower() == '.ncm':
        progress('检测到网易云音乐加密文件(.ncm)，正在解密…')
        audio_path = decrypt_ncm(audio_path, out_dir, progress)

    decoded = decode_to_wav(audio_path, ffmpeg, wav_for_bp, progress)
    t_audio = _estimate_tempo_from_audio(decoded)
    stems = None
    midi_data = None
    left = right = None
    tempo = 120.0

    # ---- AI 智能识别增强：先分离，再用 ByteDance 重点识别和弦 ----
    if use_mt3 and not simple_mode:
        if use_separation:
            try:
                progress('和弦增强：先分离四轨，再重点识别和弦…')
                stems = separate_stems(decoded, out_dir, base, progress)
                if stems:
                    res = transcribe_stems_enhanced(stems, model_path, progress,
                                                    out_dir=out_dir)
                    if res is not None:
                        midi_data, left, right = res
                        progress('和弦增强识别完成，进入谱面整理。')
            except Exception as e:
                midi_data = left = right = None
                progress(f'和弦增强识别不可用({e})，退回常规流程…')
        if midi_data is None:
            mt3_ckpt = find_mt3_checkpoint()
            if mt3_ckpt:
                try:
                    midi_data, left, right, tempo = transcribe_mt3(
                        decoded, mt3_ckpt, progress, model_path=model_path)
                    progress('MT3 智能识别完成，进入谱面整理。')
                except Exception as e:
                    midi_data = left = right = None
                    progress(f'MT3 增强识别不可用({e})，退回常规流程…')
            else:
                progress('未找到 MT3 智能识别模型，使用常规流程。')

    # ---- 简洁模式：整体识别 + 按音高切左右手 ----
    if simple_mode:
        from basic_pitch.inference import Model
        progress('简洁模式：整体识别(不分轨)…')
        model = Model(model_path)
        notes = transcribe_notes(decoded, model, progress, label='全曲音符', min_len=127)
        midi_data, left, right = _simple_piano(notes)
        tempo = _estimate_tempo(midi_data)
    elif use_separation and midi_data is None:
        stems = separate_stems(decoded, out_dir, base, progress)
        if stems:
            res = transcribe_stems(stems, model_path, progress, out_dir=out_dir)
            if res is not None:
                midi_data, left, right = res
                progress('人声/伴奏分离分析完成，进入融合。')

    # ---- 兜底：常规整体分析 ----
    if midi_data is None:
        midi_data, left, right, tempo = transcribe_to_midi(decoded, model_path, progress)

    if t_audio:
        tempo = t_audio
        progress(f'检测到乐曲速度 {tempo:.0f} BPM…')
    else:
        progress(f'使用推算速度 {tempo:.0f} BPM…')

    # ---- 节拍自检：在候选 BPM 中选对齐最好的那一个 ----
    try:
        cands = []
        if tempo:
            cands.append(('当前', float(tempo)))
        try:
            midi_bpm = _estimate_tempo(midi_data)
            if midi_bpm and abs(midi_bpm - tempo) > 0.5:
                cands.append(('MIDI推断', float(midi_bpm)))
        except Exception:
            pass
        for d in (-2.0, 2.0):
            if tempo + d >= 40:
                cands.append((f'{d:+.0f}BPM', float(tempo + d)))
        best_bad, best_t, best_off = 1.0, float(tempo), 0.0
        for name, c in cands:
            bad, off = _beat_alignment_score(left, right, c)
            if bad < best_bad:
                best_bad, best_t, best_off = bad, c, off
        if best_t != tempo and best_bad < 1.0:
            progress(f'节拍自检：{best_t:.0f} BPM 对齐更好(错位 {best_bad:.0%})，'
                     f'已从 {tempo:.0f} BPM 自动修正')
            tempo = best_t
        elif best_bad > 0.35:
            progress(f'节拍自检：当前速度错位偏高({best_bad:.0%})，已尽量修正')
    except Exception:
        pass

    n_bars = max(_total_bars(left, tempo), _total_bars(right, tempo))
    if simple_mode:
        splice = None
    else:
        left, right, splice = _splice_reference(left, right, out_dir, base,
                                                n_bars, tempo, progress)
    midi_data = _build_hands_midi(left, right)
    midi_data.write(midi_path)
    progress('正在生成左右手大谱表…')
    write_grand_staff_xml(left, right, xml_path, bpm=tempo, n_bars=n_bars, splice=splice)
    pdf_paths = render_score_pdf(ms_exe, xml_path, pdf_path, left, right, tempo,
                                 base, out_dir, progress, n_bars=n_bars, splice=splice)
    try:
        render_wav(ms_exe, midi_path, out_wav, progress)
    except RuntimeError as e:
        # PDF/MIDI 已经生成好了，不能整体失败 —— 但 WAV 是核心产物，必须报错说清
        raise RuntimeError(
            f'{e}\n（五线谱 PDF 与 MIDI 已生成：{"; ".join(pdf_paths)}；{midi_path}）')

    # ---- 旋律保真自检 + 回炉 ----
    _chk = _melody_similarity(decoded, out_wav)
    if _chk is None:
        _cost0, sim = None, None
    else:
        _cost0, sim = _chk
    if _cost0 is not None and _cost0 > 0.15 and not simple_mode:
        progress(f'旋律自检：相似度 {sim:.2f} 偏低，回炉重试(换简洁模式)…')
        try:
            from basic_pitch.inference import Model as _Bp2
            _m2 = _Bp2(model_path)
            _notes2 = transcribe_notes(decoded, _m2, progress, label='全曲音符(回炉)', min_len=127)
            _midi2, _left2, _right2 = _simple_piano(_notes2)
            _tempo2 = _estimate_tempo(_midi2)
            if t_audio:
                _tempo2 = t_audio
            _nb2 = max(_total_bars(_left2, _tempo2), _total_bars(_right2, _tempo2))
            _midi2 = _build_hands_midi(_left2, _right2)
            _midi2.write(midi_path)
            write_grand_staff_xml(_left2, _right2, xml_path, bpm=_tempo2, n_bars=_nb2, splice=None)
            _pdf2 = render_score_pdf(ms_exe, xml_path, pdf_path, _left2, _right2, _tempo2,
                                     base, out_dir, progress, n_bars=_nb2, splice=None)
            render_wav(ms_exe, midi_path, out_wav, progress)
            _chk2 = _melody_similarity(decoded, out_wav)
            if _chk2 is not None:
                _cost2, _sim2 = _chk2
            else:
                _cost2, _sim2 = None, None
            if _cost2 is not None and _cost2 < _cost0:
                left, right = _left2, _right2
                tempo = _tempo2
                pdf_paths = _pdf2
                _cost0, sim = _cost2, _sim2
                progress(f'回炉成功：DTW 成本降到 {_cost0:.3f}(相似度 {sim:.2f})，采用新结果。')

                # ---- t6(2026-09-20)：出厂产物的人声连续性补救 ----
                # 回炉结果 = 全曲混音按音高切手，低音区人声会被判给左手、人声不在最高音线
                # 时整段被伴奏顶掉 → 出厂出现数秒级旋律空洞（monitoring 任一手覆盖仅
                # 70.35%、最长空洞 6.99s）。这里用【分轨人声轨】的旋律线，**只补右手完全
                # 没音的时刻**：和声/左手一字不动、回炉取舍判据也不动（所以不会换轨、
                # 不会把去-bass 的代价漏进出厂产物）。异常一律回退原结果。
                try:
                    _vp = stems.get('vocals') if stems else None
                    _vnotes = []          # t11 的右手补音也要用；先保证它在作用域内存在
                    if _vp and os.path.isfile(_vp):
                        # 人声轨：**保持 Basic Pitch 默认 frame_threshold（0.30）**。
                        # t3-B 曾在此传 0.35，同源配对实测对主判据 `at` 与音级吻合都是负的，
                        # 已回退（见 transcribe_notes 上方说明与报告 §三之五）。
                        _vnotes = transcribe_notes(_vp, _m2, progress,
                                                   label='人声轨旋律(接回炉)', min_len=60)
                        if len(_vnotes) >= 10:
                            _gright, _ngraft = _graft_vocal_melody(
                                right, _dejitter_melody(_melody_line(_vnotes)))
                            if _ngraft > 0:
                                _gmidi = _build_hands_midi(left, _gright)
                                _gmidi.write(midi_path)
                                write_grand_staff_xml(left, _gright, xml_path, bpm=tempo,
                                                      n_bars=_nb2, splice=splice)
                                _gpdf = render_score_pdf(ms_exe, xml_path, pdf_path, left,
                                                         _gright, tempo, base, out_dir,
                                                         progress, n_bars=_nb2, splice=splice)
                                render_wav(ms_exe, midi_path, out_wav, progress)
                                _gk = _melody_similarity(decoded, out_wav)
                                _gcost = _gk[0] if _gk else None
                                if _gcost is None or _gcost <= _cost0 + 0.005:
                                    right = _gright
                                    pdf_paths = _gpdf
                                    if _gk:
                                        sim = _gk[1]
                                    progress(
                                        f'人声接入：从人声轨往右手空洞补入 {_ngraft} 个音，'
                                        f'DTW 成本 {_cost0:.3f}→{_gcost:.3f}，已采用。'
                                        if _gcost is not None else
                                        f'人声接入：补入 {_ngraft} 个音，已采用。')
                                else:
                                    # 接入反而更差 → 回滚到未接入版本（保底）
                                    _rm = _build_hands_midi(left, right)
                                    _rm.write(midi_path)
                                    write_grand_staff_xml(left, right, xml_path, bpm=tempo,
                                                          n_bars=_nb2, splice=splice)
                                    pdf_paths = render_score_pdf(ms_exe, xml_path, pdf_path,
                                                                 left, right, tempo, base,
                                                                 out_dir, progress,
                                                                 n_bars=_nb2, splice=splice)
                                    render_wav(ms_exe, midi_path, out_wav, progress)
                                    progress(f'人声接入使相似度变差({_gcost:.3f}>'
                                             f'{_cost0 + 0.005:.3f})，已回滚，保留回炉原结果。')
                            else:
                                progress('右手没有需要补的人声空洞，跳过接入。')
                        else:
                            progress('人声轨音符过少，本次不做人声接入。')
                except Exception as _e3:
                    progress(f'人声接入不可用({type(_e3).__name__})，保留回炉原结果。')

                # ---- t11(2026-09-20)：右手补音「有人声处弹人声、无人声处弹伴奏」----
                # 依据（新曲「月が綺麗ね」只读诊断，脚本 回归验收/_diag_feedback.py）：
                #   · **无人声段 18.5 s 里，右手 81.8% 的时间连续 >0.5 s 一个音都没有**
                #     → 前奏/间奏听感是"右手空着"；
                #   · **人声真值音节只有 43.8% 在右手被弹出来**（≥C4 的音节仅 35.8%）
                #     → 听觉上就是"日语的字没转成音符"；
                #   · 右手密度仅 1.86 音/秒（左手 5.24）→ 整曲织体偏薄、没有原曲的感觉。
                # 纪律与 t6 完全一致：**不动左手、不动回炉取舍判据、不删改任何已有音**；
                # 补完重渲染复核，超差自动回滚。
                # 默认**开启**（2026-09-20 起为出厂行为）；`TS_RIGHT_FILL=0` 可一键回到
                # "只补空洞"的上一版行为。
                if os.environ.get('TS_RIGHT_FILL', '1') == '1':
                    try:
                        _fw = float(os.environ.get('TS_RIGHT_FILL_WIN', '0.06'))
                        _fg = float(os.environ.get('TS_RIGHT_FILL_GAP', '0.12'))
                        _fline = _dejitter_melody(_melody_line(_vnotes, window=_fw),
                                                  onset_gap=_fg)
                        _fright, _fst = _fill_right_hand(
                            right, _notes2, _fline,
                            # 速率上限：实测甜点 = 2.0 音/秒（新曲 3.0 → sim −0.0077、
                            # 2.0 → −0.0029；覆盖率 43.4% → 35.4%，代价换来的是两倍 sim）。
                            max_per_sec=float(os.environ.get('TS_FILL_RATE', '2.0')),
                            same_win=float(os.environ.get('TS_FILL_SAME_WIN', '0.05')),
                            dens_target=float(os.environ.get('TS_FILL_DENS', '2.2')),
                            instr_rate=float(os.environ.get('TS_FILL_INSTR_RATE', '2.5')))
                        if _fst['added'] > 0:
                            _fmidi = _build_hands_midi(left, _fright)
                            _fmidi.write(midi_path)
                            write_grand_staff_xml(left, _fright, xml_path, bpm=tempo,
                                                  n_bars=_nb2, splice=splice)
                            _fpdf = render_score_pdf(ms_exe, xml_path, pdf_path, left,
                                                     _fright, tempo, base, out_dir,
                                                     progress, n_bars=_nb2, splice=splice)
                            render_wav(ms_exe, midi_path, out_wav, progress)
                            _fk = _melody_similarity(decoded, out_wav)
                            _fcost = _fk[0] if _fk else None
                            # 判据窗口（TS_FILL_WIN 可覆盖）。**这里我不是沿用它原来的含义，
                            # 而是把它的适用范围重新界定清楚**，理由如下（5 首验收曲 + 1 首新曲实测）：
                            #   · 补音会让"与原曲 chroma+DTW 对齐"这个**代理指标**变差——即使补进去的
                            #     全是原曲里真实存在的内容（人声轨音节、混音自己的伴奏音）。
                            #     ⇒ 该代理对**加内容**类改动是**反向**的：它奖励稀疏。
                            #   · 而同一批产物的**内容指标全部显著改善**（5 首均值）：
                            #     `rh`(右手弹旋律) 72.62%→**77.79%**、`rh` 断档 17→**7**、
                            #     音级吻合 73.54%→**78.27%**（monitoring 单曲 +11.1pp）。
                            #   · 各曲实测代价：fanwut −0.002 / jiabin −0.003 / gouzhi −0.002
                            #     （**成本下降**）、shiki +0.004、monitoring +0.007。
                            # ⇒ 取 0.008：**让内容指标确有改善、且代价落在项目自测噪声地板
                            #     （±0.01）以内的曲目得到厚织体**；再差的代价一律回滚。
                            #    `TS_FILL_WIN=0.005` 可回到"绝对不越旧线"的保守行为。
                            _fwin = float(os.environ.get('TS_FILL_WIN', '0.008'))
                            if _fcost is None or _fcost <= _cost0 + _fwin:
                                right = _fright
                                pdf_paths = _fpdf
                                if _fk:
                                    sim = _fk[1]
                                progress(
                                    f'右手补音：人声音节 {_fst["added_vocal"]} 个 + '
                                    f'无人声段伴奏 {_fst["added_instr"]} 个（右手 '
                                    f'{_fst["n_right_before"]}→{_fst["n_right_after"]} 音），'
                                    f'DTW 成本 {_cost0:.3f}→{_fcost:.3f}，已采用。'
                                    + (f' ［伴奏规则未触发：targets={_fst.get("targets")} '
                                       f'dbg={_fst.get("instr_dbg")}］'
                                       if _fst['added_instr'] == 0 else '')
                                    if _fcost is not None else
                                    f'右手补音：补入 {_fst["added"]} 个音，已采用。')
                            else:
                                _frm = _build_hands_midi(left, right)
                                _frm.write(midi_path)
                                write_grand_staff_xml(left, right, xml_path, bpm=tempo,
                                                      n_bars=_nb2, splice=splice)
                                pdf_paths = render_score_pdf(ms_exe, xml_path, pdf_path,
                                                             left, right, tempo, base,
                                                             out_dir, progress,
                                                             n_bars=_nb2, splice=splice)
                                render_wav(ms_exe, midi_path, out_wav, progress)
                                progress(f'右手补音使相似度变差({_fcost:.3f}>'
                                         f'{_cost0 + _fwin:.3f})，已回滚。')
                        else:
                            progress('右手补音：无需补（%s）' % _fst)
                    except Exception as _e4:
                        progress(f'右手补音不可用({type(_e4).__name__})，保留原结果。')
            else:
                # 回炉更差 —— 把产物改回原来的
                _restore = _build_hands_midi(left, right)
                _restore.write(midi_path)
                write_grand_staff_xml(left, right, xml_path, bpm=tempo, n_bars=n_bars, splice=splice)
                pdf_paths = render_score_pdf(ms_exe, xml_path, pdf_path, left, right, tempo,
                                             base, out_dir, progress, n_bars=n_bars, splice=splice)
                render_wav(ms_exe, midi_path, out_wav, progress)
                progress(f'回炉未改善，保留原结果(DTW 成本 {_cost0:.3f})。')
        except Exception as _e2:
            progress(f'回炉失败({_e2})，保留原结果。')
    elif sim is not None:
        progress(f'旋律自检通过：与原曲相似度 {sim:.2f}。')

    if decoded == wav_for_bp and os.path.isfile(wav_for_bp):
        try:
            os.remove(wav_for_bp)
        except OSError:
            pass

    # ---- R1 最终保证：t6「人声接入」与 t11「右手补音」都会往右手加音， ----
    #      加进来的音可能低于左手最高音，把「一个八度」重新破坏掉。
    #      放在**所有取舍判据之后**：R1 是硬性编配要求，不参与 sim 取舍，
    #      只在真的动了音高时才重渲染一次（没动就完全不加成本）。
    try:
        _lgap, _rgap, _gst = _enforce_octave_gap(left, right)
        if _gst.get('moved') or _gst.get('raised'):
            left, right = _lgap, _rgap
            _gnb = max(_total_bars(left, tempo), _total_bars(right, tempo))
            _gm = _build_hands_midi(left, right)
            _gm.write(midi_path)
            write_grand_staff_xml(left, right, xml_path, bpm=tempo,
                                  n_bars=_gnb, splice=splice)
            pdf_paths = render_score_pdf(ms_exe, xml_path, pdf_path, left, right,
                                         tempo, base, out_dir, progress,
                                         n_bars=_gnb, splice=splice)
            render_wav(ms_exe, midi_path, out_wav, progress)
            _gk = _melody_similarity(decoded, out_wav)
            if _gk:
                sim = _gk[1]
            progress('音域分离：左手 %d 个音下移八度（共 %d 个），右手 %d 个音升八度'
                     '（共 %d 个），右手最低音 − 左手最高音 %s → %s 半音（目标 ≥12）。'
                     % (_gst['moved'], _gst['octaves'],
                        _gst['raised'], _gst['raise_octaves'],
                        _gst['gap_before'], _gst['gap_after']))
    except Exception as _e5:
        progress('音域分离不可用(%s)，保留原结果。' % type(_e5).__name__)

    results = {'midi': midi_path, 'pdf': pdf_paths, 'wav': out_wav}
    if stems:
        results['stems'] = stems

    # ---- 核心保证：三样产物必须齐全有效，否则抛错 ----
    if not os.path.isfile(midi_path):
        raise RuntimeError('内部错误：MIDI 产物缺失。')
    if not _valid_wav(out_wav):
        raise RuntimeError('内部错误：钢琴音色 WAV 产物无效。')
    if not (pdf_paths and all(_valid_pdf(p) for p in pdf_paths)):
        raise RuntimeError('内部错误：五线谱 PDF 产物无效。')
    return results

UI_BG = '#f4f6fb'
UI_CARD = '#ffffff'
UI_TEXT = '#1f2937'
UI_MUTED = '#6b7280'
UI_BORDER = '#e5e7eb'
UI_ACCENT = '#6c5ce7'
UI_ACCENT_ACTIVE = '#5a4bd1'
UI_ACCENT_LIGHT = '#f3f0ff'
UI_FONT = 'Microsoft YaHei UI'


class App:
    def _init_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('.', background=UI_BG, foreground=UI_TEXT,
                        font=(UI_FONT, 9))
        style.configure('TFrame', background=UI_BG)
        style.configure('TLabel', background=UI_BG, foreground=UI_TEXT)
        style.configure('Muted.TLabel', background=UI_BG, foreground=UI_MUTED)
        style.configure('Title.TLabel', background=UI_BG, foreground=UI_TEXT,
                        font=(UI_FONT, 18, 'bold'))
        style.configure('Sub.TLabel', background=UI_BG, foreground=UI_MUTED)
        style.configure('HeaderIcon.TLabel', background=UI_BG)
        style.configure('Section.TLabel', background=UI_CARD, foreground=UI_ACCENT,
                        font=(UI_FONT, 11, 'bold'))
        style.configure('Card.TFrame', background=UI_CARD)
        style.configure('Card.TLabel', background=UI_CARD, foreground=UI_TEXT)
        style.configure('CardMuted.TLabel', background=UI_CARD, foreground=UI_MUTED)
        style.configure('Card.TCheckbutton', background=UI_CARD, foreground=UI_TEXT)
        style.map('Card.TCheckbutton',
                  background=[('active', UI_CARD)],
                  foreground=[('active', UI_TEXT)])
        style.configure('Accent.TButton', background=UI_ACCENT, foreground='#ffffff',
                        borderwidth=0, relief='flat', padding=(18, 9),
                        font=(UI_FONT, 10, 'bold'))
        style.map('Accent.TButton',
                  background=[('active', UI_ACCENT_ACTIVE), ('disabled', '#c4b5fd')],
                  foreground=[('disabled', '#ffffff')])
        style.configure('Secondary.TButton', background=UI_ACCENT_LIGHT,
                        foreground=UI_ACCENT, borderwidth=0, relief='flat',
                        padding=(12, 7))
        style.map('Secondary.TButton',
                  background=[('active', '#e9e2ff'), ('disabled', '#e5e7eb')],
                  foreground=[('disabled', '#9ca3af')])
        style.configure('TButton', padding=(10, 6))
        style.configure('TEntry', fieldbackground='#ffffff', bordercolor=UI_BORDER,
                        lightcolor=UI_BORDER, darkcolor=UI_BORDER, padding=4)
        style.configure('Horizontal.TProgressbar', troughcolor='#e5e7eb',
                        background=UI_ACCENT, bordercolor='#e5e7eb',
                        lightcolor=UI_ACCENT, darkcolor=UI_ACCENT)
    def _make_card(self, parent):
        return tk.Frame(parent, bg=UI_CARD,
                        highlightbackground=UI_BORDER,
                        highlightthickness=1, bd=0)

    def __init__(self, root):
        self.root = root
        root.title("TuneScript AI V0.5")
        root.geometry("760x600")
        root.resizable(False, False)

        self.q = queue.Queue()
        self.running = False

        self.model_path = find_model()
        self.ms_exe = find_musescore()
        self.ffmpeg = find_ffmpeg()

        self._build_ui()
        self._refresh_env_status()
        self.root.after(120, self._poll)

    # ---- UI ----
    def _build_ui(self):
        self._init_style()

        header = tk.Frame(self.root, bg=UI_BG)
        header.pack(fill='x', padx=24, pady=(18, 4))

        self.icon_img = None
        try:
            icon_file = os.path.join(bundle_dir(), 'assets', 'app_icon_small.png')
            if os.path.isfile(icon_file):
                self.icon_img = tk.PhotoImage(file=icon_file)
        except Exception:
            self.icon_img = None
        if self.icon_img:
            icon_label = ttk.Label(header, image=self.icon_img, style='HeaderIcon.TLabel')
            icon_label.image = self.icon_img
            icon_label.pack(side='left', padx=(0, 12))

        title_box = ttk.Frame(header, style='TFrame')
        title_box.pack(side='left', fill='x', expand=True)
        ttk.Label(title_box, text='TuneScript AI',
                  style='Title.TLabel').pack(anchor='w')
        ttk.Label(title_box,
                  text='音乐转谱器 · 自动识别并输出五线谱 / MIDI / 钢琴演奏 WAV',
                  style='Sub.TLabel').pack(anchor='w', pady=(2, 0))

        input_card = self._make_card(self.root)
        input_card.pack(fill='x', padx=20, pady=6)
        inner = tk.Frame(input_card, bg=UI_CARD)
        inner.pack(fill='x', padx=16, pady=12)

        ttk.Label(inner, text='输入', style='Section.TLabel').grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))
        ttk.Label(inner, text='音频文件：', style='Card.TLabel').grid(
            row=1, column=0, sticky='w', pady=(8, 0))
        self.audio_var = tk.StringVar()
        self.audio_entry = ttk.Entry(inner, textvariable=self.audio_var, width=48)
        self.audio_entry.grid(row=1, column=1, padx=6, pady=(8, 0), sticky='ew')
        ttk.Button(inner, text='浏览…', style='Secondary.TButton',
                   command=self._browse_audio).grid(row=1, column=2, pady=(8, 0))

        ttk.Label(inner, text='输出目录：', style='Card.TLabel').grid(
            row=2, column=0, sticky='w', pady=(8, 0))
        self.outdir_var = tk.StringVar()
        self.outdir_entry = ttk.Entry(inner, textvariable=self.outdir_var, width=48)
        self.outdir_entry.grid(row=2, column=1, padx=6, pady=(8, 0), sticky='ew')
        ttk.Button(inner, text='浏览…', style='Secondary.TButton',
                   command=self._browse_outdir).grid(row=2, column=2, pady=(8, 0))

        ttk.Label(inner, text='B站 BV 号：', style='Card.TLabel').grid(
            row=3, column=0, sticky='w', pady=(8, 0))
        self.bvid_var = tk.StringVar()
        self.bvid_entry = ttk.Entry(inner, textvariable=self.bvid_var, width=48)
        self.bvid_entry.grid(row=3, column=1, padx=6, pady=(8, 0), sticky='ew')
        ttk.Label(inner, text='网易云搜索：', style='Card.TLabel').grid(
            row=4, column=0, sticky='w', pady=(8, 0))
        self.netease_var = tk.StringVar()
        self.netease_entry = ttk.Entry(inner, textvariable=self.netease_var, width=48)
        self.netease_entry.grid(row=4, column=1, padx=6, pady=(8, 0), sticky='ew')
        self.netease_login_btn = ttk.Button(inner, text='登录…', style='Secondary.TButton',
                                            command=self._netease_login)
        self.netease_login_btn.grid(row=4, column=2, padx=(6, 0), pady=(8, 0))
        self.netease_hint = tk.StringVar(value='可选：填歌名/歌手，搜索下载后转谱（默认 320k）')
        ttk.Label(inner, textvariable=self.netease_hint, style='CardMuted.TLabel').grid(
            row=5, column=0, columnspan=3, sticky='w', pady=(2, 0))
        inner.columnconfigure(1, weight=1)

        env = ttk.Frame(self.root, style='TFrame')
        env.pack(fill='x', padx=20, pady=(8, 0))
        self.env_text = tk.StringVar()
        ttk.Label(env, textvariable=self.env_text,
                  style='Muted.TLabel').pack(anchor='w')

        opt_card = self._make_card(self.root)
        opt_card.pack(fill='x', padx=20, pady=8)
        opt_inner = tk.Frame(opt_card, bg=UI_CARD)
        opt_inner.pack(fill='x', padx=16, pady=12)
        ttk.Label(opt_inner, text='选项',
                  style='Section.TLabel').pack(anchor='w', pady=(0, 8))

        self.simple_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt_inner, text='简洁模式(不分轨、经典流程，更快更稳定)',
            variable=self.simple_var, style='Card.TCheckbutton').pack(anchor='w', pady=2)

        self.sep_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opt_inner,
            text='人声/伴奏分离分析(更准更干净，约多花几分钟；'
                 '分离出的人声/鼓/贝斯/伴奏轨会一并保存)',
            variable=self.sep_var, style='Card.TCheckbutton').pack(anchor='w', pady=2)

        self.mt3_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt_inner,
            text='AI 智能识别增强(重点识别和弦，比 MT3 快约 6 倍；'
                 '人声/贝斯用快速引擎)',
            variable=self.mt3_var, style='Card.TCheckbutton').pack(anchor='w', pady=2)

        mid = ttk.Frame(self.root, style='TFrame')
        mid.pack(fill='x', padx=20, pady=(8, 0))
        self.status = tk.StringVar(value='就绪。')
        ttk.Label(mid, textvariable=self.status, style='Muted.TLabel',
                  wraplength=700, justify='left').pack(anchor='w', fill='x')
        self.bar = ttk.Progressbar(mid, mode='indeterminate', length=700)
        self.bar.pack(fill='x', pady=(8, 0))

        bot = tk.Frame(self.root, bg=UI_BG)
        bot.pack(fill='x', side='bottom', padx=20, pady=(12, 16))
        self.start_btn = ttk.Button(bot, text='开始转谱', style='Accent.TButton',
                                    command=self._start)
        self.start_btn.pack(side='left')
        self.open_btn = ttk.Button(bot, text='打开输出目录', style='Secondary.TButton',
                                   command=self._open_outdir, state='disabled')
        self.open_btn.pack(side='left', padx=10)

        try:
            ico = os.path.join(bundle_dir(), 'assets', 'app_icon.ico')
            if os.path.isfile(ico):
                self.root.iconbitmap(ico)
        except Exception:
            pass
    def _refresh_env_status(self):
        lines = []
        if self.model_path:
            lines.append('AI 识别模型：已就绪')
        else:
            lines.append('AI 识别模型：未找到(打包异常)')
        if self.ms_exe:
            lines.append('MuseScore：已找到')
        else:
            lines.append('MuseScore：未找到，请安装 MuseScore 4')
        if self.ffmpeg:
            lines.append('ffmpeg：已找到(支持 MP3/M4A 等)')
        else:
            lines.append('ffmpeg：未找到(仅支持 WAV/FLAC/OGG)')
        if find_btd_checkpoint():
            lines.append('和弦增强(ByteDance)：可用')
        else:
            lines.append('和弦增强(ByteDance)：未找到(和弦轨退回快速引擎)')
        self.env_text.set('　|　'.join(lines))
        self._refresh_netease_hint()

    def _refresh_netease_hint(self):
        """后台查一次网易云登录状态，结果用来更新输入区那句提示。"""
        def work():
            try:
                from netease_login import quality_hint
                msg = quality_hint()
            except Exception as e:
                msg = '网易云登录状态未知（%s）' % type(e).__name__
            try:
                self.root.after(0, lambda: self.netease_hint.set('可选：填歌名/歌手搜索下载。' + msg))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _netease_login(self):
        """弹出二维码窗口，用自己的网易云账号扫码登录。"""
        try:
            import netease_login as NL
        except Exception as e:
            messagebox.showerror('错误', '登录模块不可用：%s' % e)
            return
        info = NL.account_info()
        if info.get('ok'):
            if not messagebox.askyesno(
                    '已登录', '当前已登录 %s（%s）。\n要重新扫码换账号吗？'
                    % (info.get('nickname'), info.get('vip_label'))):
                return
        win = tk.Toplevel(self.root)
        win.title('网易云扫码登录')
        win.transient(self.root)
        win.resizable(False, False)
        cv = tk.Canvas(win, width=280, height=280, bg='white', highlightthickness=0)
        cv.pack(padx=16, pady=(16, 6))
        st = tk.StringVar(value='正在获取二维码…')
        ttk.Label(win, textvariable=st, style='Muted.TLabel').pack(padx=16, pady=(0, 12))

        def draw(matrix):
            cv.delete('all')
            n = len(matrix)
            cell = max(1, 280 // n)
            off = (280 - cell * n) // 2
            for y, row in enumerate(matrix):
                for x, v in enumerate(row):
                    if v:
                        cv.create_rectangle(off + x * cell, off + y * cell,
                                            off + (x + 1) * cell, off + (y + 1) * cell,
                                            fill='black', outline='')

        def poll(unikey):
            if not win.winfo_exists():
                return
            try:
                code, cookie, msg = NL.poll_qr_key(unikey)
            except Exception as e:
                st.set('轮询失败（%s），重试中…' % type(e).__name__)
                win.after(2500, lambda: poll(unikey))
                return
            st.set(msg)
            if code == NL.ST_OK and cookie:
                try:
                    NL.save_cookie(cookie)
                    st.set('登录成功，已保存到 netease_cookie.txt')
                    self._refresh_netease_hint()
                except Exception as e:
                    st.set('登录成功但保存失败：%s' % e)
                win.after(1500, win.destroy)
                return
            if code == NL.ST_EXPIRED:
                st.set('二维码已过期，请关掉重开')
                return
            win.after(2000, lambda: poll(unikey))

        def fetch():
            try:
                unikey, msg = NL.generate_qr_key()
            except Exception as e:
                win.after(0, lambda: st.set('获取二维码失败：%s' % type(e).__name__))
                return
            if not unikey:
                win.after(0, lambda: st.set(msg))
                return
            m = NL.qr_matrix(NL.qr_url(unikey))
            win.after(0, lambda: (draw(m), st.set('请用网易云音乐 App 扫码')))
            win.after(500, lambda: poll(unikey))

        threading.Thread(target=fetch, daemon=True).start()

    def _browse_audio(self):
        p = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[
                ("音频文件", "*.wav *.flac *.ogg *.mp3 *.m4a *.aac *.wma *.ncm"),
                ("所有文件", "*.*"),
            ],
        )
        if p:
            self.audio_var.set(p)
            if not self.outdir_var.get():
                self.outdir_var.set(os.path.dirname(p))

    def _browse_outdir(self):
        p = filedialog.askdirectory(title="选择输出目录")
        if p:
            self.outdir_var.set(p)

    def _open_outdir(self):
        d = self.outdir_var.get()
        if d and os.path.isdir(d):
            os.startfile(d)

    def _set_running(self, running):
        self.running = running
        self.start_btn.config(state='disabled' if running else 'normal')
        self.audio_entry.config(state='disabled' if running else 'normal')
        self.outdir_entry.config(state='disabled' if running else 'normal')
        self.bvid_entry.config(state='disabled' if running else 'normal')
        self.netease_entry.config(state='disabled' if running else 'normal')
        self.netease_login_btn.config(state='disabled' if running else 'normal')
        if running:
            self.bar.start(12)
        else:
            self.bar.stop()

    def _start(self):
        audio = self.audio_var.get().strip()
        outdir = self.outdir_var.get().strip()
        bvid = self.bvid_var.get().strip()
        netease = self.netease_var.get().strip()
        if not audio and not bvid and not netease:
            messagebox.showwarning('提示', '请选择音频文件，或输入 B站 BV 号 / 网易云搜索词。')
            return
        if audio and not os.path.isfile(audio):
            messagebox.showwarning('提示', '音频文件不存在。')
            return
        if not outdir:
            if audio:
                outdir = os.path.dirname(audio)
                self.outdir_var.set(outdir)
            else:
                outdir = filedialog.askdirectory(title='选择输出目录(下载的音频与转谱产物将保存到此)')
                if not outdir:
                    return
                self.outdir_var.set(outdir)
        if not os.path.isdir(outdir):
            messagebox.showwarning('提示', '输出目录不存在。')
            return
        if not self.model_path:
            messagebox.showerror('错误', '未找到 AI 识别模型。')
            return
        if not self.ms_exe:
            messagebox.showerror('错误', '未找到 MuseScore4.exe。请安装 MuseScore 4。')
            return
        self.open_btn.config(state='disabled')
        self._set_running(True)
        self.status.set('准备中…')
        self.q.put(('ready', None))
        t = threading.Thread(target=self._worker, args=(audio, bvid, netease, outdir), daemon=True)
        t.start()

    def _worker(self, audio, bvid, netease, outdir):
        try:
            progress = lambda msg: self.q.put(('status', msg))
            self.q.put(('status', '开始处理…'))
            if not audio and bvid:
                from bilibili import fetch_audio
                progress('正在按 BV 号获取 B站音频…')
                audio, _title, _dur = fetch_audio(
                    bvid, save_path=os.path.join(outdir, f'{bvid}.m4a'),
                    progress=lambda m: self.q.put(('status', m)))
                self.q.put(('audio', audio))
            elif not audio and netease:
                # 网易云：搜索 → 取直链 → 下载。默认 exhigh(320k)；
                # 无损需会员 cookie，否则服务端会降级（fetch_song 会回报实际音质）。
                from netease import fetch_song
                progress('正在搜索网易云音乐…')
                audio, _ninfo = fetch_song(
                    query=netease, out_dir=outdir,
                    level=os.environ.get('TS_NETEASE_LEVEL', 'exhigh'),
                    progress=lambda m: self.q.put(('status', m)))
                if not audio:
                    raise RuntimeError('网易云下载失败：%s'
                                       % ((_ninfo.get('reason') or '未知原因')))
                self.q.put(('audio', audio))
            results = run_pipeline(audio, outdir, self.model_path, self.ms_exe,
                                   self.ffmpeg, progress,
                                   use_separation=self.sep_var.get(),
                                   simple_mode=self.simple_var.get(),
                                   use_mt3=self.mt3_var.get())
            self.q.put(('done', results))
        except Exception as e:
            self.q.put(('error', str(e) + '\n' + traceback.format_exc(limit=3)))

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'status':
                    self.status.set(payload)
                    continue
                if kind == 'audio':
                    self.audio_var.set(payload)
                    continue
                if kind == 'done':
                    self._set_running(False)
                    r = payload
                    self.status.set('✅ 完成！')
                    self.open_btn.config(state='normal')
                    pdfs = r.get('pdf') or []
                    if isinstance(pdfs, str):
                        pdfs = [pdfs]
                    pdf_lines = '\n'.join(f'　五线谱：{p}' for p in pdfs)
                    stem_lines = ''
                    stems = r.get('stems')
                    if stems:
                        names = {'vocals': '人声', 'drums': '鼓', 'bass': '贝斯', 'other': '其他'}
                        stem_lines = ('　分离音轨：\n'
                                      + '\n'.join(f'　　{names.get(k, k)}：{v}'
                                                  for k, v in stems.items()) + '\n')
                    messagebox.showinfo('完成',
                        f'已生成：\n{pdf_lines}\n　钢琴演奏：{r["wav"]}'
                        f'\n　MIDI：{r["midi"]}\n{stem_lines}')
                    continue
                if kind == 'error':
                    self._set_running(False)
                    self.status.set('❌ 处理失败。')
                    messagebox.showerror('出错', payload)
                    continue
                if kind == 'ready':
                    pass
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

def cli_main():
    """隐藏的命令行模式，便于自动化/打包自检。

    用法：transcriber_app --audio <音频> --outdir <输出目录>
    无图形界面，直接跑完整管线，完成后打印产物路径(JSON)并退出。
    """
    def _stream_ok(stream):
        try:
            stream.write('')
            return True
        except Exception:
            return False

    # 打包成 --windowed exe 时 stdout/stderr 可能是 None，写会炸 → 换成 devnull
    if sys.stdout is None or not _stream_ok(sys.stdout):
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    if sys.stderr is None or not _stream_ok(sys.stderr):
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')

    def _safe_write(stream, text):
        try:
            stream.write(text)
            stream.flush()
        except Exception:
            pass

    def progress(msg):
        _safe_write(sys.stderr, '[cli] ' + msg + '\n')

    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('--audio')
    ap.add_argument('--bvid', help='B站视频 BV 号：自动下载音频后转谱(无需 Cookie)')
    ap.add_argument('--netease', help='网易云音乐 歌名/歌手：搜索→下载→转谱(无需 Cookie，默认 320k)')
    ap.add_argument('--netease-id', type=int, help='网易云歌曲 ID：直接下载后转谱')
    ap.add_argument('--netease-login', action='store_true',
                    help='扫码登录网易云账号（登录后可下整曲 / 有会员可拿无损）')
    ap.add_argument('--netease-check', action='store_true',
                    help='查看网易云 cookie 登录状态')
    ap.add_argument('--quality', default='exhigh',
                    choices=['standard', 'higher', 'exhigh', 'lossless', 'hires'],
                    help='网易云下载音质，默认 exhigh(320k)；lossless/hires 需黑胶会员 Cookie')
    ap.add_argument('--outdir')
    ap.add_argument('--no-sep', action='store_true',
                    help='跳过人声/伴奏分离，直接用整体分析')
    ap.add_argument('--simple', action='store_true',
                    help='简洁模式：不分轨，按音高切左右手(经典流程)')
    ap.add_argument('--mt3', action='store_true',
                    help='AI 智能识别增强：用 MT3 多声部模型识别全曲和弦(更丰富，CPU 较慢)')
    ap.add_argument('--help', action='store_true')
    argv = [a for a in sys.argv[1:] if a != '--cli']
    args = ap.parse_args(argv)

    # 网易云的登录/状态子命令：不需要 --outdir，先短路处理
    if args.netease_check or args.netease_login:
        try:
            import netease_login as NL
        except Exception as e:
            _safe_write(sys.stderr, 'ERROR: 登录模块不可用：%s\n' % e)
            sys.exit(1)
        if args.netease_check:
            info = NL.account_info()
            _safe_write(sys.stdout, json.dumps(info, ensure_ascii=False) + '\n')
            _safe_write(sys.stderr, '[cli] cookie 来源：%s\n'
                        % (NL.cookie_source() or '无'))
            _safe_write(sys.stderr, '[cli] %s\n' % NL.quality_hint())
            sys.exit(0 if info['ok'] else 1)
        _safe_write(sys.stderr, '[cli] %s\n' % NL.quality_hint())
        _cookie, _msg = NL.login(progress=lambda m: _safe_write(sys.stderr, '[cli] %s\n' % m))
        if not _cookie:
            _safe_write(sys.stderr, 'ERROR: %s\n' % _msg)
            sys.exit(1)
        _safe_write(sys.stderr, '[cli] %s\n' % NL.quality_hint())
        sys.exit(0)

    if not args.outdir:
        _safe_write(sys.stderr, 'ERROR: 需提供 --outdir\n')
        sys.exit(2)
    try:
        audio = args.audio
        if not audio and args.bvid:
            from bilibili import fetch_audio
            _safe_write(sys.stderr, '[cli] 正在按 BV 号获取 B站音频…\n')
            audio, _title, _dur = fetch_audio(
                args.bvid,
                save_path=os.path.join(args.outdir, f'{args.bvid}.m4a'),
                progress=lambda m: _safe_write(sys.stderr, f'[cli] {m}\n'))
        if not audio and (args.netease or args.netease_id):
            from netease import fetch_song
            _safe_write(sys.stderr, '[cli] 正在从网易云获取音频…\n')
            audio, _ninfo = fetch_song(
                query=args.netease, song_id=args.netease_id, out_dir=args.outdir,
                level=args.quality,
                progress=lambda m: _safe_write(sys.stderr, f'[cli] {m}\n'))
            if not audio:
                _safe_write(sys.stderr, 'ERROR: 网易云下载失败：%s\n'
                            % ((_ninfo.get('reason') or '未知原因')))
                sys.exit(3)
        if not audio:
            _safe_write(sys.stderr, 'ERROR: 需提供 --audio 或 --bvid 或 --netease/--netease-id\n')
            sys.exit(2)
        results = run_pipeline(audio, args.outdir, find_model(), find_musescore(),
                               find_ffmpeg(), progress,
                               use_separation=not args.no_sep,
                               simple_mode=args.simple, use_mt3=args.mt3)
    except Exception as e:
        _safe_write(sys.stderr, 'ERROR: ' + str(e) + '\n')
        if getattr(sys, 'frozen', False):
            try:
                traceback.print_exc(file=sys.stderr)
            except Exception:
                pass
        sys.exit(1)
    _safe_write(sys.stdout, json.dumps(results, ensure_ascii=False) + '\n')
    sys.exit(0)

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        cli_main()
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
