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


def _bass_line(notes, window=0.12):
    """从 MT3 多音高里提取最低音线(贝斯轨用)。

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


def transcribe_stems_enhanced(stems, model_path, progress):
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

    # 和弦(其他轨)用 ByteDance 钢琴转录模型——多音高和弦识别(重点增强)
    btd_ckpt = find_btd_checkpoint()
    if btd_ckpt:
        btd_model = _btd_model(btd_ckpt)
        other_notes = _btd_track_notes(stems["other"], btd_model, progress, "和弦伴奏")
    else:
        progress("未找到和弦增强模型，和弦轨用快速引擎…")
        other_notes = transcribe_notes(stems["other"], bp_model, progress,
                                       label="和声伴奏")

    # 贝斯完全不需要(不识别、不使用)；鼓也不使用(无贝斯可对齐)
    # 左手伴奏 = 多和声音(其他轨)，滤高音幻觉、抑长铺垫后稀疏保留

    progress("正在融合分轨结果并整理成可弹钢琴谱…")
    gaps = _find_vocal_gaps(vocal_notes, other_notes)

    def _in_gap(s):
        return any(gs <= s < ge for gs, ge in gaps)

    def _in_long_gap(s):
        # 只有真正的长器乐段(>2.5s 前奏/尾奏)才丢弃人声；
        # 短空档内的人声保留(可能是识别漏音)，避免旋律突然消失
        return any(gs <= s < ge and (ge - gs) > 2.5 for gs, ge in gaps)

    # 人声轨取最高音线=主旋律；仅长器乐段内丢弃人声改用器乐最高音线
    vocal_line = _melody_line(vocal_notes)
    vocal_line = [n for n in vocal_line if not _in_long_gap(n[0])]
    melody = _fill_melody_gaps(vocal_line, other_notes, gaps=gaps)

    # 多和声音进左手：滤高音幻觉→抑长铺垫→抽稀(0.8s 一个和声点)，
    # 左手只保留最突出的和声，密度低、干净
    harmony = _sparsify_harmony(
        _suppress_pad_notes(_filter_high_hallucination(other_notes)),
        min_gap=0.8)

    # 前奏/尾奏(空档)内只保留最突出的声音：再二次抽稀(1.5s 一个音)，
    # 让器乐段极简，不抢主旋律
    accomp = []
    for n in harmony:
        if _in_gap(n[0]):
            accomp.append((n[0], n[1], n[2], int(n[3] * 0.85)))
        else:
            accomp.append(n)
    if gaps:
        gap_harmony = [n for n in accomp if _in_gap(n[0])]
        keep = [n for n in accomp if not _in_gap(n[0])]
        sparse = _sparsify_harmony(gap_harmony, min_gap=1.5)
        accomp = keep + sparse
        accomp.sort(key=lambda x: x[0])

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
      - 左手(伴奏)：只保留贝斯 + 1 个和声音(窗口最多 2 音)，
        同音碎音合并(0.25s)、弱短音过滤更严——伴奏干净不抢戏，同样保留延长音；
      - 右手单音旋律(通用基线，不做八度加厚——低音区八度对会有拍频
        感“抖”)；左手简洁：贝斯单音为骨干 + 稀疏和声点(0.5s一个)、
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
    left_raw = [n for n in notes if n[2] < split_pitch]
    right_raw = [n for n in notes if n[2] >= split_pitch]
    left = fix_hand(left_raw, max_span=max_span, window=window,
                    max_notes=max_notes, mode="mix")
    right = fix_hand(right_raw, max_span=max_span, window=window,
                     max_notes=max_notes, mode="mix")
    left = _fix_same_pitch_overlap(_dedupe_exact(left))
    right = _fix_same_pitch_overlap(_dedupe_exact(right))
    left = _soft_velocity(left, lo=40, hi=100)
    right = _soft_velocity(right, lo=50, hi=110)
    return _build_hands_midi(left, right), left, right


def _drop_tiny(notes, min_len=0.08):
    """过滤超短碎音(基本是识别噪声，听感是‘杂音’)。"""
    return [(s, e, p, v) for s, e, p, v in notes if e - s >= min_len]


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
            step = max(1, int(c.shape[1] / (90 * 5)))
            return c[:, ::step]

        ca = _chroma(orig_wav)
        cb = _chroma(piano_wav)
        if ca.shape[1] < 5 or cb.shape[1] < 5:
            return None
        # 全局 DTW(非子序列)：整曲旋律走向对齐
        d, _p = librosa.sequence.dtw(ca, cb, metric="cosine")
        cost = float(d[-1, -1]) / max(1, ca.shape[1])
        return (cost, float(np.exp(-cost)))
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
            return 0.0, 0.0
        ons = np.asarray(onsets, dtype=np.float64)
        # 网格相位未知，扫 8 个相位候选取最优，避免整体偏移误判
        best_bad = 1.0
        best_off = 1e9
        for k in range(8):
            ph = (ons - k * quarter / 8.0) % quarter
            off = np.minimum(ph, quarter - ph)
            bad = float(np.mean(off > quarter * 0.35))
            mean_off = float(np.mean(off))
            if bad < best_bad or (bad == best_bad and mean_off < best_off):
                best_bad, best_off = bad, mean_off
        return best_bad, best_off
    except Exception:
        return 0.0, 0.0


# ---------------------------------------------------------------------------
# 音轨分离（人声/鼓/贝斯/其他）——原理与识音(shiyin.notalabs.cn)一致：
# Demucs 深度学习模型做频谱掩码源分离 + Basic Pitch 逐轨识别。
# ---------------------------------------------------------------------------

_SEP_CACHE = {"model": None}


def separate_stems(audio_path, out_dir, base, progress, shifts=1):
    """Demucs 四轨分离(人声/鼓/贝斯/其他)，写四轨 wav 并返回路径字典。

    首次运行自动下载 htdemucs 模型(~80MB，缓存在用户目录)。
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
            progress("加载人声/伴奏分离模型(首次需下载约 80MB)…")
            _SEP_CACHE["model"] = get_model("htdemucs")
        model = _SEP_CACHE["model"]
        model.cpu()

        progress("AI 正在分离人声/鼓/贝斯/伴奏四轨…")
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


def _drum_to_bass_stabs(drum_notes, bass_notes, tol=0.08):
    """把鼓点映射成“强化贝斯起音”——钢琴版保留鼓的律动又不添乱。

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
    notes = sorted(notes, key=lambda x: x[0])
    starts = [n[0] for n in notes]
    from bisect import bisect_left
    keep = []
    for i, (s, e, p, v) in enumerate(notes):
        if p < high_pitch:
            keep.append((s, e, p, v))
            continue
        # 找 ±neighbor 内是否另有同时发声的音(和弦成员)
        lo = bisect_left(starts, s - neighbor)
        hi = bisect_left(starts, s + neighbor)
        has_neighbor = False
        for j in range(lo, hi):
            if j == i:
                continue
            ns, ne = notes[j][0], notes[j][1]
            if ns <= e and ne >= s:  # 时间上重叠 → 是和弦成员
                has_neighbor = True
                break
        if has_neighbor:
            keep.append((s, e, p, v))
        # 否则孤立高音 → 幻觉，丢弃
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
            fill.extend((s, e, p, max(v, 100)) for s, e, p, v in line)
            continue
        # 器乐轨没有连续主奏线：用空档前后的人声音高桥接，旋律不中断。
        # 只对较短的间奏(≤2.5s)桥接；更长的真间奏(前奏/尾奏)留白
        # 让乐句呼吸，不强行续写。
        if ge - gs > 2.5:
            continue
        before = [n for n in vocals_sorted if n[1] <= gs + 1e-4]
        after = [n for n in vocals_sorted if n[0] >= ge - 1e-4]
        if not before or not after:
            continue
        bp = before[-1][2]
        ap = after[0][2]
        bridge = []
        t = gs
        seg = 0.5  # 桥接音符步长
        n_steps = max(1, int(round((ge - gs) / seg)))
        for k in range(1, n_steps):
            frac = k / n_steps
            p = int(round(bp + (ap - bp) * frac))
            t0 = gs + (ge - gs) * (k - 1) / n_steps
            t1 = gs + (ge - gs) * k / n_steps
            bridge.append((t0, t1, p, 95))
        fill.extend(bridge)
    return vocal_notes + fill


def transcribe_stems(stems, model_path, progress):
    """分离轨 → 钢琴谱数据(所有声部全部用上)：

    - 人声轨 → 主旋律(右)；前奏/间奏/尾奏人声空档用和声轨最高音线补旋律；
    - 贝斯轨 → 左手低音线；和声轨抽稀(每0.35s最强音)后进左手和声点；
    - 鼓轨 → 与贝斯对齐的鼓点强化贝斯起音(保留律动)，其余跳过；
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

    # 人声轨用最小音符 60ms：日语等多音节语言一字一音、音节短促，
    # 保留快速音节的起音节奏(歌曲的“特色”所在)
    vocal_notes = notes_of(stems["vocals"], "人声旋律", min_len=60)
    if len(vocal_notes) < 10:
        progress("人声轨音符过少，退回整体分析…")
        return None
    # 贝斯完全不需要(不识别、不使用)；鼓也不使用(无贝斯可对齐)
    other_notes = notes_of(stems["other"], "和声伴奏")

    progress("正在融合人声/和声并调整成可弹钢琴谱…")
    gaps = _find_vocal_gaps(vocal_notes, other_notes)

    def _in_gap(s):
        return any(gs <= s < ge for gs, ge in gaps)

    def _in_long_gap(s):
        return any(gs <= s < ge and (ge - gs) > 2.5 for gs, ge in gaps)

    # 仅长器乐段(>2.5s 前奏/尾奏)内的人声丢弃，改用器乐主旋律线；
    # 短空档内的人声保留(可能是识别漏音)，避免旋律突然消失
    vocal_notes = [n for n in vocal_notes if not _in_long_gap(n[0])]

    melody = _fill_melody_gaps(vocal_notes, other_notes, gaps=gaps)

    # 多和声音进左手：抑长铺垫后抽稀(0.9s 一个和声点)，密度低
    harmony = _sparsify_harmony(_suppress_pad_notes(other_notes), min_gap=0.9)

    # 前奏/尾奏(空档)内二次抽稀(1.5s 一个音)，只留最突出的声音
    accomp = []
    for n in harmony:
        if _in_gap(n[0]):
            accomp.append((n[0], n[1], n[2], int(n[3] * 0.85)))
        else:
            accomp.append(n)
    if gaps:
        gap_harmony = [n for n in accomp if _in_gap(n[0])]
        keep = [n for n in accomp if not _in_gap(n[0])]
        sparse = _sparsify_harmony(gap_harmony, min_gap=1.5)
        accomp = keep + sparse
        accomp.sort(key=lambda x: x[0])
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

    # 生成新歌前清理上次的输出(输入音频本身绝不删除)
    _clean_previous_outputs(out_dir, audio_path, progress)

    wav_for_bp = os.path.join(out_dir, f"{base}_tmp_bp.wav")
    midi_path = os.path.join(out_dir, f"{base}_piano.mid")
    xml_path = os.path.join(out_dir, f"{base}_大谱表.xml")
    pdf_path = os.path.join(out_dir, f"{base}_五线谱.pdf")
    out_wav = os.path.join(out_dir, f"{base}_钢琴.wav")

    if not ms_exe:
        raise RuntimeError("未找到 MuseScore4.exe，请先安装 MuseScore 4。")

    # 网易云音乐加密文件(.ncm)先解密成原始音频，再走正常解码
    if os.path.splitext(audio_path)[1].lower() == '.ncm':
        progress("检测到网易云音乐加密文件(.ncm)，正在解密…")
        audio_path = decrypt_ncm(audio_path, out_dir, progress)

    decoded = decode_to_wav(audio_path, ffmpeg, wav_for_bp, progress)

    # 乐谱排版速度：优先用音频节拍跟踪(准)，失败退回 MIDI 推断
    t_audio = _estimate_tempo_from_audio(decoded)

    stems = None
    midi_data = None
    left = right = None
    tempo = 120.0
    if use_mt3 and not simple_mode:
        # AI 智能识别增强：分轨时用 ByteDance 钢琴转录模型识别和弦(重点
        # 人声→和弦→贝斯，CPU 接近实时，比 MT3 快约 6 倍)；不分轨则整曲 MT3。
        # 任何失败都回退常规流程。
        if use_separation:
            try:
                progress("和弦增强：先分离四轨，再重点识别和弦…")
                stems = separate_stems(decoded, out_dir, base, progress)
                if stems:
                    res = transcribe_stems_enhanced(stems, model_path, progress)
                    if res is not None:
                        midi_data, left, right = res
                        progress("和弦增强识别完成，进入谱面整理。")
            except Exception as e:
                midi_data = left = right = None
                progress(f"和弦增强识别不可用({e})，退回常规流程…")
        if midi_data is None:
            # 未分轨或分轨增强失败 → 整曲 MT3(需 MT3 权重)
            mt3_ckpt = find_mt3_checkpoint()
            if mt3_ckpt:
                try:
                    midi_data, left, right, tempo = transcribe_mt3(
                        decoded, mt3_ckpt, progress, model_path=model_path
                    )
                    progress("MT3 智能识别完成，进入谱面整理。")
                except Exception as e:
                    midi_data = left = right = None
                    progress(f"MT3 增强识别不可用({e})，退回常规流程…")
            else:
                progress("未找到 MT3 智能识别模型，使用常规流程。")
    if simple_mode:
        # 简洁模式：整体识别 + 按音高切左右手(不分轨、不融合)
        from basic_pitch.inference import Model
        progress("简洁模式：整体识别(不分轨)…")
        model = Model(model_path)
        notes = transcribe_notes(decoded, model, progress,
                                 label="全曲音符", min_len=127)
        midi_data, left, right = _simple_piano(notes)
        tempo = _estimate_tempo(midi_data)
    elif use_separation and midi_data is None:
        # 音轨分离分析(增强) → 融合；失败回退整体分析
        stems = separate_stems(decoded, out_dir, base, progress)
        if stems:
            res = transcribe_stems(stems, model_path, progress)
            if res is not None:
                midi_data, left, right = res
                progress("人声/伴奏分离分析完成，进入融合。")
    if midi_data is None:
        midi_data, left, right, tempo = transcribe_to_midi(
            decoded, model_path, progress
        )

    if t_audio:
        tempo = t_audio
        progress(f"检测到乐曲速度 {tempo:.0f} BPM…")
    else:
        progress(f"使用推算速度 {tempo:.0f} BPM…")

    # —— 深度思考自检①：节拍对齐 ——
    # 规则奖励式验证：测当前 BPM 下音符起音对节拍网格的错位比例；
    # 若错位高，从候选 BPM(MIDI 推断、±2 档)里选对齐最好的，自动修正。
    try:
        cands = []
        if tempo:
            cands.append(("当前", float(tempo)))
        try:
            midi_bpm = _estimate_tempo(midi_data)
            if midi_bpm and abs(midi_bpm - tempo) > 0.5:
                cands.append(("MIDI推断", float(midi_bpm)))
        except Exception:
            pass
        for d in (-2.0, 2.0):
            if tempo + d >= 40:
                cands.append((f"{d:+.0f}BPM", float(tempo + d)))
        best_bad, best_t, best_off = 1.0, float(tempo), 0.0
        for name, c in cands:
            bad, off = _beat_alignment_score(left, right, c)
            if bad < best_bad:
                best_bad, best_t, best_off = bad, c, off
        if best_t != tempo and best_bad < 1.0:
            progress(
                f"节拍自检：{best_t:.0f} BPM 对齐更好(错位 {best_bad:.0%})，"
                f"已从 {tempo:.0f} BPM 自动修正")
            tempo = best_t
        elif best_bad > 0.35:
            progress(f"节拍自检：当前速度错位偏高({best_bad:.0%})，已尽量修正")
    except Exception:
        pass

    # 参考谱拼接(前奏/尾奏)：输出目录有 <歌名>_前奏参考.musicxml /
    # <歌名>_尾奏参考.musicxml 时，直接拼进成品对应小节(简洁模式不拼接)
    n_bars = max(_total_bars(left, tempo), _total_bars(right, tempo))
    if simple_mode:
        splice = None
    else:
        left, right, splice = _splice_reference(
            left, right, out_dir, base, n_bars, tempo, progress)
    midi_data = _build_hands_midi(left, right)
    midi_data.write(midi_path)

    # 生成单个钢琴大谱表 MusicXML（左右手两行谱，花括号连接，非四手联弹）
    progress("正在生成左右手大谱表…")
    write_grand_staff_xml(left, right, xml_path, bpm=tempo,
                          n_bars=n_bars, splice=splice)

    # 先保证五线谱 PDF(带重试与降级回退)，再渲染钢琴音色：
    # 顺序上让“谱”永远先于“声”成功，彻底杜绝“有曲子没谱”的状态。
    pdf_paths = render_score_pdf(
        ms_exe, xml_path, pdf_path, left, right, tempo, base, out_dir, progress,
        n_bars=n_bars, splice=splice,
    )
    try:
        render_wav(ms_exe, midi_path, out_wav, progress)
    except RuntimeError as e:
        raise RuntimeError(
            f"{e}\n（五线谱 PDF 与 MIDI 已生成："
            f"{'; '.join(pdf_paths)}；{midi_path}）"
        )

    # —— 深度思考自检②：旋律保真度验证(回炉机制) ——
    # 原曲 vs 钢琴 WAV 的 chroma+DTW 归一化成本；成本 >0.15(明显跑偏)
    # 时自动换一条管线(简洁模式)重跑一次，取成本更低的结果。
    _chk = _melody_similarity(decoded, out_wav)
    if _chk is None:
        _cost0, sim = None, None
    else:
        _cost0, sim = _chk
    if _cost0 is not None and _cost0 > 0.15 and not simple_mode:
        progress(f"旋律自检：相似度 {sim:.2f} 偏低，回炉重试(换简洁模式)…")
        try:
            from basic_pitch.inference import Model as _Bp2
            _m2 = _Bp2(model_path)
            _notes2 = transcribe_notes(decoded, _m2, progress,
                                       label="全曲音符(回炉)", min_len=127)
            _midi2, _left2, _right2 = _simple_piano(_notes2)
            _tempo2 = _estimate_tempo(_midi2)
            if t_audio:
                _tempo2 = t_audio
            _nb2 = max(_total_bars(_left2, _tempo2), _total_bars(_right2, _tempo2))
            _midi2 = _build_hands_midi(_left2, _right2)
            _midi2.write(midi_path)
            write_grand_staff_xml(_left2, _right2, xml_path, bpm=_tempo2,
                                  n_bars=_nb2, splice=None)
            _pdf2 = render_score_pdf(
                ms_exe, xml_path, pdf_path, _left2, _right2, _tempo2,
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
                progress(f"回炉成功：DTW 成本降到 {_cost0:.3f}(相似度 {sim:.2f})，采用新结果。")
            else:
                # 回炉没更好，还原第一版结果
                _restore = _build_hands_midi(left, right)
                _restore.write(midi_path)
                write_grand_staff_xml(left, right, xml_path, bpm=tempo,
                                      n_bars=n_bars, splice=splice)
                pdf_paths = render_score_pdf(
                    ms_exe, xml_path, pdf_path, left, right, tempo,
                    base, out_dir, progress, n_bars=n_bars, splice=splice)
                render_wav(ms_exe, midi_path, out_wav, progress)
                progress(f"回炉未改善，保留原结果(DTW 成本 {_cost0:.3f})。")
        except Exception as _e2:
            progress(f"回炉失败({_e2})，保留原结果。")
    elif sim is not None:
        progress(f"旋律自检通过：与原曲相似度 {sim:.2f}。")

    # 清理临时解码文件
    if decoded == wav_for_bp and os.path.isfile(wav_for_bp):
        try:
            os.remove(wav_for_bp)
        except OSError:
            pass

    results = {"midi": midi_path, "pdf": pdf_paths, "wav": out_wav}
    if stems:
        results["stems"] = stems

    # 最终防线：任何产物缺失或无效都不算成功
    if not os.path.isfile(midi_path):
        raise RuntimeError("内部错误：MIDI 产物缺失。")
    if not _valid_wav(out_wav):
        raise RuntimeError("内部错误：钢琴音色 WAV 产物无效。")
    if not pdf_paths or not all(_valid_pdf(p) for p in pdf_paths):
        raise RuntimeError("内部错误：五线谱 PDF 产物无效。")
    return results


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        root.title("TuneScript AI V0.3")
        root.geometry("640x400")
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
        pad = {"padx": 12, "pady": 6}

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="音频文件：").grid(row=0, column=0, sticky="w")
        self.audio_var = tk.StringVar()
        self.audio_entry = ttk.Entry(top, textvariable=self.audio_var, width=46)
        self.audio_entry.grid(row=0, column=1, padx=4)
        ttk.Button(top, text="浏览…", command=self._browse_audio).grid(row=0, column=2)

        ttk.Label(top, text="输出目录：").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.outdir_var = tk.StringVar()
        self.outdir_entry = ttk.Entry(top, textvariable=self.outdir_var, width=46)
        self.outdir_entry.grid(row=1, column=1, padx=4, pady=(8, 0))
        ttk.Button(top, text="浏览…", command=self._browse_outdir).grid(row=1, column=2, pady=(8, 0))

        env = ttk.Frame(self.root)
        env.pack(fill="x", **pad)
        self.env_text = tk.StringVar()
        ttk.Label(env, textvariable=self.env_text, foreground="#555").pack(anchor="w")

        opt = ttk.Frame(self.root)
        opt.pack(fill="x", **pad)
        self.simple_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt, text="简洁模式(不分轨、经典流程，更快更稳定)",
            variable=self.simple_var,
        ).pack(anchor="w")
        self.sep_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opt, text="人声/伴奏分离分析(更准更干净，约多花几分钟；分离出的人声/鼓/贝斯/伴奏轨会一并保存)",
            variable=self.sep_var,
        ).pack(anchor="w")
        self.mt3_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt, text="AI 智能识别增强(重点识别和弦，比 MT3 快约 6 倍；人声/贝斯用快速引擎)",
            variable=self.mt3_var,
        ).pack(anchor="w")

        mid = ttk.Frame(self.root)
        mid.pack(fill="x", **pad)
        self.status = tk.StringVar(value="就绪。")
        ttk.Label(mid, textvariable=self.status, wraplength=600, justify="left").pack(anchor="w", fill="x")
        self.bar = ttk.Progressbar(mid, mode="indeterminate", length=600)
        self.bar.pack(fill="x", pady=(6, 0))

        bot = ttk.Frame(self.root)
        bot.pack(fill="x", side="bottom", **pad)
        self.start_btn = ttk.Button(bot, text="开始转谱", command=self._start)
        self.start_btn.pack(side="left")
        self.open_btn = ttk.Button(bot, text="打开输出目录", command=self._open_outdir, state="disabled")
        self.open_btn.pack(side="left", padx=8)

    def _refresh_env_status(self):
        lines = []
        if self.model_path:
            lines.append("AI 识别模型：已就绪")
        else:
            lines.append("AI 识别模型：未找到(打包异常)")
        if self.ms_exe:
            lines.append("MuseScore：已找到")
        else:
            lines.append("MuseScore：未找到，请安装 MuseScore 4")
        if self.ffmpeg:
            lines.append("ffmpeg：已找到(支持 MP3/M4A 等)")
        else:
            lines.append("ffmpeg：未找到(仅支持 WAV/FLAC/OGG)")
        if find_btd_checkpoint():
            lines.append("和弦增强(ByteDance)：可用")
        else:
            lines.append("和弦增强(ByteDance)：未找到(和弦轨退回快速引擎)")
        self.env_text.set("　|　".join(lines))

    # ---- 事件 ----
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
        self.start_btn.config(state="disabled" if running else "normal")
        self.audio_entry.config(state="disabled" if running else "normal")
        self.outdir_entry.config(state="disabled" if running else "normal")
        if running:
            self.bar.start(12)
        else:
            self.bar.stop()

    def _start(self):
        audio = self.audio_var.get().strip()
        outdir = self.outdir_var.get().strip()
        if not audio:
            messagebox.showwarning("提示", "请先选择音频文件。")
            return
        if not os.path.isfile(audio):
            messagebox.showwarning("提示", "音频文件不存在。")
            return
        if not outdir:
            outdir = os.path.dirname(audio)
            self.outdir_var.set(outdir)
        if not os.path.isdir(outdir):
            messagebox.showwarning("提示", "输出目录不存在。")
            return

        if not self.model_path:
            messagebox.showerror("错误", "未找到 AI 识别模型。")
            return
        if not self.ms_exe:
            messagebox.showerror("错误", "未找到 MuseScore4.exe。请安装 MuseScore 4。")
            return

        self.open_btn.config(state="disabled")
        self._set_running(True)
        self.status.set("准备中…")
        self.q.put(("ready", None))
        t = threading.Thread(target=self._worker, args=(audio, outdir), daemon=True)
        t.start()

    def _worker(self, audio, outdir):
        try:
            progress = lambda msg: self.q.put(("status", msg))
            self.q.put(("status", "开始处理…"))
            results = run_pipeline(
                audio, outdir, self.model_path, self.ms_exe, self.ffmpeg, progress,
                use_separation=self.sep_var.get(),
                simple_mode=self.simple_var.get(),
                use_mt3=self.mt3_var.get(),
            )
            self.q.put(("done", results))
        except Exception as e:
            self.q.put(("error", str(e) + "\n" + traceback.format_exc(limit=3)))

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "status":
                    self.status.set(payload)
                elif kind == "done":
                    self._set_running(False)
                    r = payload
                    self.status.set("✅ 完成！")
                    self.open_btn.config(state="normal")
                    pdfs = r.get("pdf") or []
                    if isinstance(pdfs, str):
                        pdfs = [pdfs]
                    pdf_lines = "\n".join(f"　五线谱：{p}" for p in pdfs)
                    stem_lines = ""
                    stems = r.get("stems")
                    if stems:
                        names = {"vocals": "人声", "drums": "鼓", "bass": "贝斯", "other": "其他"}
                        stem_lines = "　分离音轨：\n" + "\n".join(
                            f"　　{names.get(k, k)}：{v}" for k, v in stems.items()
                        ) + "\n"
                    messagebox.showinfo(
                        "完成",
                        "已生成：\n"
                        f"{pdf_lines}\n"
                        f"　钢琴演奏：{r['wav']}\n"
                        f"　MIDI：{r['midi']}\n"
                        f"{stem_lines}",
                    )
                elif kind == "error":
                    self._set_running(False)
                    self.status.set("❌ 处理失败。")
                    messagebox.showerror("出错", payload)
                elif kind == "ready":
                    pass
        except queue.Empty:
            pass
        self.root.after(120, self._poll)




def cli_main():
    """隐藏的命令行模式，便于自动化/打包自检。

    用法：transcriber_app --audio <音频> --outdir <输出目录>
    无图形界面，直接跑完整管线，完成后打印产物路径(JSON)并退出。
    """
    # noconsole 打包且未附加任何控制台时，sys.stdout/stderr 可能为 None，
    # 或在沙箱/无控制台环境下是写入即抛 OSError 的坏句柄。
    # 先探测：坏句柄整体替换为 devnull，让库内部的打印(basic_pitch 等)也不崩；
    # 本函数自己的输出再走 _safe_write 双保险。
    def _stream_ok(stream):
        try:
            stream.write("")
            return True
        except Exception:
            return False

    if sys.stdout is None or not _stream_ok(sys.stdout):
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None or not _stream_ok(sys.stderr):
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    def _safe_write(stream, text):
        try:
            stream.write(text)
            stream.flush()
        except Exception:
            pass

    def progress(msg):
        _safe_write(sys.stderr, "[cli] " + msg + "\n")

    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--no-sep", action="store_true",
                    help="跳过人声/伴奏分离，直接用整体分析")
    ap.add_argument("--simple", action="store_true",
                    help="简洁模式：不分轨，按音高切左右手(经典流程)")
    ap.add_argument("--mt3", action="store_true",
                    help="AI 智能识别增强：用 MT3 多声部模型识别全曲和弦(更丰富，CPU 较慢)")
    ap.add_argument("--help", action="store_true")
    # 剥掉 main() 已消耗的 "--cli"，避免 argparse 报未知参数
    argv = [a for a in sys.argv[1:] if a != "--cli"]
    args = ap.parse_args(argv)

    try:
        results = run_pipeline(
            args.audio, args.outdir,
            find_model(), find_musescore(), find_ffmpeg(),
            progress,
            use_separation=not args.no_sep,
            simple_mode=args.simple,
            use_mt3=args.mt3,
        )
    except Exception as e:
        _safe_write(sys.stderr, "ERROR: " + str(e) + "\n")
        if getattr(sys, "frozen", False):
            try:
                traceback.print_exc(file=sys.stderr)
            except Exception:
                pass
        sys.exit(1)

    _safe_write(sys.stdout, json.dumps(results, ensure_ascii=False) + "\n")
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
