# -*- coding: utf-8 -*-
"""全曲回归验收 · 单次运行。

复用基线（转谱验证/）里已分离好的 htdemucs_6s 六轨，跳过 Demucs 分离，
其余走【真实 run_pipeline】全流程（和弦增强 + 节拍自检 + 谱面 + 渲染 + 回炉 + 有效性校验）。

- arm B（新）: 伴奏 = _merge_accomp_stems（piano+guitar+other+bass 相加合并）
- arm A（旧）: 伴奏 = _pick_main_accomp（按 RMS 选最响单轨）——对照臂，用来验证
  本 harness 能复现 _report.json 里记录的历史基线。

用法：
    python 回归验收/regress_one.py --song fanwut --arm B
"""
import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
DEV = os.path.join(ROOT, 'ai_transcriber_dev')
sys.path.insert(0, ROOT)
sys.path.insert(0, DEV)

def _load_code():
    """支持 `--code <path>`：把被测源码复制成**私有快照**再导入。

    为什么必须这样（2026-09-20 事故）：本脚本原先 `sys.path.insert(ROOT)` 后**实时
    导入工作区源码**，而团队是并发作业的 —— 曾发生"整曲跑到一半队友 edit 了
    transcriber_app.py"，导致三首曲目读到不同版本源码、产物无法归因。
    改为快照后：一次验收的 5 首曲**永远跑在同一份字节上**，且把 SHA256 写进结果。
    不传 --code 时行为与改动前完全一致（仍实时导入工作区源码）。
    """
    argv = sys.argv[1:]
    code = ''
    for i, a in enumerate(argv):
        if a == '--code' and i + 1 < len(argv):
            code = argv[i + 1]
        elif a.startswith('--code='):
            code = a.split('=', 1)[1]
    if not code:
        import transcriber_app as _ta
        return _ta, os.path.abspath(_ta.__file__), None
    code = os.path.abspath(code)
    import hashlib
    import shutil
    d = os.path.join(HERE, '_work', '_regress_code')
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, 'transcriber_app.py')
    shutil.copy2(code, dst)
    sys.path.insert(0, d)
    import transcriber_app as _ta
    assert os.path.abspath(_ta.__file__) == os.path.abspath(dst), \
        '导入到了错误的 transcriber_app: %s' % _ta.__file__
    with open(code, 'rb') as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return _ta, code, sha


ta, CODE_PATH, CODE_SHA = _load_code()

VER = os.path.join(ROOT, '转谱验证')
AUDIO_DIR = r'C:\Users\35968\Desktop\测试用曲'
STEM_KEYS = ['vocals', 'drums', 'bass', 'guitar', 'piano', 'other']

# key -> (歌名/文件主名, _report.json 记录的历史 sim)
SONGS = {
    'jiabin': ('嘉宾 (粤语版) - 张远', 0.9321528776944605),
    'shiki': ('柿崎ユウタ - バカみたいに（像个笨蛋一样） - KomisI-w', 0.8887012795475049),
    'fanwut': ('反乌托邦 - 乌托邦P', 0.8631469074295381),
}


def find_audio(song):
    for p in glob.glob(os.path.join(AUDIO_DIR, song + '.*')):
        if os.path.splitext(p)[1].lower() in ('.flac', '.wav', '.ogg', '.mp3', '.m4a',
                                              '.aac', '.ncm'):
            return p
    raise SystemExit('找不到原始音频: %s' % song)


def link_stems(key, song):
    """把基线六轨挂进工作目录（默认硬链接，不复制数据）。

    ⚠️ 安全须知（2026-09-19 补）：硬链接**不是写保护**。整曲管线里的
    `_merge_accomp_stems` 会把合并结果写到 `os.path.dirname(第一条参与轨)`，
    而在「只剩单条有效参与轨」时它会 **直接返回那条轨的路径**（不新建文件）；
    此时若后续环节对该路径做原地写，就会顺着硬链接**写穿到 `转谱验证/`
    的冻结 stem**。`sf.write` 恰是原地覆盖、不解链接。

    因此需要彻底隔离时请设 `TS_REGRESS_STEM_COPY=1`（或删掉 `_stems/<key>/`
    后带该变量重跑）——此时改为真实复制，管线所有写入只落在 `_stems/` 内。
    """
    copy_mode = os.environ.get('TS_REGRESS_STEM_COPY') == '1'
    dst = os.path.join(HERE, '_stems', key)
    os.makedirs(dst, exist_ok=True)
    out = {}
    for k in STEM_KEYS:
        # 源素材优先 转谱验证/<key>/（三首冻结曲），否则退回 _work/<key>/
        # （monitoring/gouzhi 等新曲的六轨由 verifier 固化在 _work 下；不写 转谱验证/）。
        src = os.path.join(VER, key, '%s_%s.wav' % (song, k))
        if not os.path.isfile(src):
            src = os.path.join(HERE, '_work', key, '%s_%s.wav' % (song, k))
        if not os.path.isfile(src):
            continue
        tgt = os.path.join(dst, '%s_%s.wav' % (song, k))
        if not os.path.isfile(tgt):
            if copy_mode:
                import shutil
                shutil.copy2(src, tgt)
            else:
                try:
                    os.link(src, tgt)
                except OSError:
                    import shutil
                    shutil.copy2(src, tgt)
        elif copy_mode and os.path.samefile(src, tgt):
            # 已存在同名硬链接且要求隔离 → 替换为真实副本，切断写穿通道
            import shutil
            os.remove(tgt)
            shutil.copy2(src, tgt)
        out[k] = tgt
    return out


def register_keys(spec):
    """把 key=歌名=历史基线sim 的曲目注册进 SONGS（分号分隔）。用于新曲。"""
    for item in (spec or '').split(';'):
        item = item.strip()
        if not item:
            continue
        parts = item.split('=')
        if len(parts) != 3:
            raise SystemExit('--keys 格式应为 key=歌名=基线sim，收到: %r' % item)
        SONGS[parts[0].strip()] = (parts[1].strip(), float(parts[2]))


def main():
    # --keys 必须先解析：它注册新曲后，--song 的合法性才完整
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--keys', default='')
    known, _rest = pre.parse_known_args()
    register_keys(known.keys)

    ap = argparse.ArgumentParser()
    ap.add_argument('--song', required=True, choices=sorted(SONGS))
    ap.add_argument('--arm', required=True, choices=['A', 'B'])
    ap.add_argument('--keys', default='',
                    help='诊断用：额外注册曲目，格式 key=歌名=历史基线sim;key2=...；'
                         '用于 monitoring/gouzhi 等新曲（三首原有曲不受影响）')
    ap.add_argument('--no-reheat', action='store_true',
                    help='诊断用：屏蔽「旋律保真回炉」，让分轨结果留到最终产物，'
                         '以便隔离伴奏改动的真实影响（非出厂路径）')
    ap.add_argument('--gain', type=float, default=1.0,
                    help='诊断用：只给「伴奏轨送进 ByteDance 前」乘一个增益，'
                         '内容与其它一切不变 —— 用来量测纯电平扰动的影响')
    ap.add_argument('--accomp', default='',
                    help='诊断用：覆盖 ACCOMP_STEMS，例如 piano,guitar,other（去掉 bass）')
    ap.add_argument('--variant', default='', help='结果文件名后缀标签')
    ap.add_argument('--code', default='',
                    help='被测源码路径；给了就复制成私有快照再导入（防并发编辑污染）')
    args = ap.parse_args()

    key, arm = args.song, args.arm
    suffix = ('_nr' if args.no_reheat else '') + (('_' + args.variant) if args.variant else '')
    song, base_sim = SONGS[key]
    t_start = time.time()

    log_path = os.path.join(HERE, 'logs', '%s_%s%s.log' % (key, arm, suffix))
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logf = open(log_path, 'w', encoding='utf-8')

    def progress(msg):
        line = '[%7.1fs] %s' % (time.time() - t_start, msg)
        logf.write(line + '\n')
        logf.flush()
        try:
            print(line, flush=True)
        except Exception:
            pass

    # ---- 复用基线六轨，替换掉 Demucs 分离 ----
    stems_fixed = link_stems(key, song)
    assert len(stems_fixed) == 6, '基线六轨不齐: %s' % sorted(stems_fixed)

    def fake_separate(audio_path, out_dir, base, progress_fn, shifts=1):
        progress_fn('复用基线 htdemucs_6s 六轨（跳过 Demucs 分离）。')
        return dict(stems_fixed)

    ta.separate_stems = fake_separate

    # ---- 诊断：覆盖伴奏轨集合（例如去掉 bass）----
    if args.accomp:
        ta.ACCOMP_PRIORITY = tuple(x.strip() for x in args.accomp.split(',') if x.strip())
        ta.ACCOMP_STEMS = ta.ACCOMP_PRIORITY          # 旧名别名，保持一致
        progress('【诊断】ACCOMP_PRIORITY 覆盖为 %s' % (ta.ACCOMP_PRIORITY,))

    # ---- 诊断：只给"送进 ByteDance 的伴奏音频"乘一个增益（内容不变）----
    if args.gain != 1.0:
        def gain_btd(wav_path, model, progress_fn, label):
            import librosa
            import numpy as np
            from piano_transcription_inference import sample_rate
            y, _sr = librosa.load(wav_path, sr=sample_rate, mono=True)
            y = np.ascontiguousarray((y * args.gain).astype('float32'))
            progress_fn('AI 正在识别%s(和弦增强)… [诊断增益 x%.4f]' % (label, args.gain))
            d = model.transcribe(y, None)
            notes = []
            for ev in d.get('est_note_events', []):
                s, e = float(ev['onset_time']), float(ev['offset_time'])
                if e > s + 1e-4:
                    notes.append((s, e, int(ev['midi_note']), int(ev['velocity'])))
            notes.sort(key=lambda x: (x[0], x[2]))
            return notes
        ta._btd_track_notes = gain_btd
        progress('【诊断】伴奏识别增益 x%.4f（其余一切不变）' % args.gain)

    # ---- 对照组：把伴奏来源换回旧行为「按 RMS 选最响单轨」----
    if arm == 'A':
        # 调用方是 `_merge_accomp_stems(stems, progress, out_dir=...)`，
        # 所以 lambda 必须收下 out_dir，否则臂 A 会因为多了个关键字参数直接崩。
        ta._merge_accomp_stems = (lambda stems, progress_fn, out_dir=None:
                                  ta._pick_main_accomp(stems))
        progress('臂 A（旧行为）：伴奏 = _pick_main_accomp（最响单轨）')
    else:
        # ⚠️ `--code` 可能指向**改动前**的源码快照，那时还没有 ACCOMP_PRIORITY
        # （只有旧名 ACCOMP_STEMS）。这里必须兼容两种，否则拿旧快照跑臂 B
        # 会直接 AttributeError 崩在开跑之前（踩过一次）。
        _prio = getattr(ta, 'ACCOMP_PRIORITY', None) or ta.ACCOMP_STEMS
        progress('臂 B（新行为）：伴奏 = _merge_accomp_stems(%s)' % '>'.join(_prio))

    audio = find_audio(song)
    progress('原始音频: %s' % audio)
    ms_exe = ta.find_musescore()
    model_path = ta.find_model()
    ffmpeg = ta.find_ffmpeg()
    progress('MuseScore=%s  model=%s  ffmpeg=%s' % (ms_exe, os.path.basename(model_path or ''),
                                                  os.path.basename(ffmpeg or '')))

    # ---- 参照音频（与基线完全一致的 sim 参照）----
    ref = os.path.join(HERE, '_ref', '%s_ref.wav' % key)
    os.makedirs(os.path.dirname(ref), exist_ok=True)
    if not os.path.isfile(ref):
        r = ta.decode_to_wav(audio, ffmpeg, ref, progress)
        if r != ref:                      # flac/wav/ogg 直接就是原文件
            ref = r
    progress('sim 参照: %s' % ref)

    out_dir = os.path.join(HERE, 'out', '%s_%s%s' % (key, arm, suffix))
    os.makedirs(out_dir, exist_ok=True)

    # ---- 诊断臂：屏蔽回炉，让「分轨 + 伴奏选择」的结果活到最终产物 ----
    real_sim_fn = ta._melody_similarity
    if args.no_reheat:
        # 返回刚好低于回炉门槛(0.15)的成本：不触发回炉，但日志不误导
        ta._melody_similarity = lambda a, b: (0.14, 0.8694)
        progress('【诊断】已屏蔽回炉：最终产物 = 分轨编排结果（非出厂路径）')

    status, err = 'ok', None
    res = None
    try:
        res = ta.run_pipeline(audio, out_dir, model_path, ms_exe, ffmpeg, progress,
                              use_separation=True, simple_mode=False, use_mt3=True)
    except Exception as e:
        status, err = 'error', '%s: %s' % (type(e).__name__, e)
        progress('管线异常: %s' % err)
    finally:
        ta._melody_similarity = real_sim_fn      # 还原真函数，下面用真的算 sim

    row = {
        'key': key, 'arm': arm, 'no_reheat': bool(args.no_reheat),
        'status': status, 'error': err,
        'base_sim_recorded': base_sim,
        'seconds': round(time.time() - t_start, 1),
        'out_dir': out_dir,
        'code': CODE_PATH, 'code_sha256': CODE_SHA,
    }

    if res:
        cost, sim = None, None
        chk = ta._melody_similarity(ref, res['wav'])
        if chk:
            cost, sim = chk
        row['cost'] = cost
        row['sim'] = sim
        row['delta_vs_recorded'] = (sim - base_sim) if sim is not None else None
        row['pdf'] = int(bool(res.get('pdf')) and all(ta._valid_pdf(p) for p in res['pdf']))
        row['midi'] = int(os.path.isfile(res['midi']))
        row['wav'] = int(ta._valid_wav(res['wav']))
        try:
            from evaluate import midi_notes
            from fragmentation import fragment_ratio
            notes = midi_notes(res['midi'])
            row['n_notes'] = len(notes)
            row['frag'] = float(fragment_ratio(notes)[0])
        except Exception as e:
            row['n_notes'] = None
            row['frag'] = None
            progress('笔记统计失败: %s' % e)

    # 从日志里捞关键消息：伴奏来源 + 是否回炉（回炉会覆盖分轨结果）
    logf.flush()
    txt = open(log_path, encoding='utf-8').read()
    for line in txt.splitlines():
        if '已合并伴奏轨' in line or '伴奏轨只有' in line or '伴奏合并不可用' in line:
            row['accomp_msg'] = line.split('] ', 1)[-1]
            break
    if '回炉成功' in txt:
        row['reheat'] = 'adopted'          # 回炉触发且被采用 → 分轨结果被丢弃
    elif '回炉未改善' in txt:
        row['reheat'] = 'rejected'
    elif '旋律自检通过' in txt:
        row['reheat'] = 'none'
    elif '【诊断】已屏蔽回炉' in txt:
        row['reheat'] = 'bypassed'
    else:
        row['reheat'] = '?'

    logf.write('RESULT ' + json.dumps(row, ensure_ascii=False) + '\n')
    logf.close()

    rdir = os.path.join(HERE, 'results')
    os.makedirs(rdir, exist_ok=True)
    with open(os.path.join(rdir, '%s_%s%s.json' % (key, arm, suffix)), 'w',
              encoding='utf-8') as f:
        json.dump(row, f, ensure_ascii=False, indent=2)

    print('RESULT ' + json.dumps(row, ensure_ascii=False))
    return 0 if status == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
