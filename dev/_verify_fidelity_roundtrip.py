# -*- coding: utf-8 -*-
"""外部验证：真实 ffmpeg 解码 + MuseScore 导入/导出，不调用生产代码反解。

python lang_dev/_verify_fidelity_roundtrip.py --outdir 回归验收/_work/fidelity
可选 --source <旧版 transcriber_app.py> 对照旧版本；--frozen 检查三首冻结 MIDI。
不会写入或运行分离模型处理冻结音轨。
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pretty_midi
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import transcriber_app


def decode_roundtrip(app, outdir):
    """ALAC 是无损容器，故原 PCM 可以作为逐采样的独立真值。"""
    sr = 44100
    t = np.arange(sr) / sr
    audio = np.stack((.3 * np.sin(2 * np.pi * 440 * t)
                      + .1 * np.sin(2 * np.pi * 14000 * t),
                      .3 * np.sin(2 * np.pi * 660 * t)), axis=1)
    source = outdir / 'stereo_reference.wav'
    encoded = outdir / 'stereo_lossless.m4a'
    decoded = outdir / 'stereo_decoded.wav'
    sf.write(str(source), audio, sr, subtype='PCM_24')
    ffmpeg = app.find_ffmpeg()
    subprocess.run([ffmpeg, '-y', '-i', str(source), '-c:a', 'alac', str(encoded)],
                   check=True, capture_output=True, timeout=60)
    app.decode_to_wav(str(encoded), ffmpeg, str(decoded), lambda _msg: None)
    reference, _ = sf.read(str(source), always_2d=True)
    result, rate = sf.read(str(decoded), always_2d=True)
    same_shape = result.shape == reference.shape and rate == sr
    error = float(np.max(np.abs(reference - result))) if same_shape else None
    return {'sample_rate': rate, 'channels': result.shape[1],
            'max_sample_error': error,
            'passed': same_shape and error < 1e-6}


def compare_notes(expected, actual, tolerance):
    """按音高独立配对，报告起音匹配率及自然结束的误差，不用 chroma 代替音符。"""
    used = set()
    matches, onset_errors, offset_errors = 0, [], []
    for start, end, pitch, _vel in sorted(expected):
        candidates = [(abs(n.start - start), i, n) for i, n in enumerate(actual)
                      if i not in used and n.pitch == pitch]
        if not candidates:
            continue
        delta, idx, found = min(candidates, key=lambda item: (item[0], item[1]))
        if delta > tolerance:
            continue
        used.add(idx)
        matches += 1
        onset_errors.append(delta)
        offset_errors.append(abs(end - found.end))
    return {'expected': len(expected), 'exported': len(actual), 'onset_matches': matches,
            'onset_recall': matches / max(1, len(expected)),
            'onset_precision': matches / max(1, len(actual)),
            'mean_onset_error_sec': float(np.mean(onset_errors)) if matches else None,
            'mean_offset_error_sec': float(np.mean(offset_errors)) if matches else None}


def score_roundtrip(app, outdir):
    bpm = 90
    q = 60 / bpm
    left = [(0, 4 * q, 48, 70), (4 * q, 8 * q, 45, 70)]
    right = [(k * q / 4, (k + 1) * q / 4, 60, 90) for k in range(16)]
    right.append((1.5 * q, 6 * q, 76, 90))
    xml, midi, pdf = [outdir / ('score_roundtrip.' + ext) for ext in ('xml', 'mid', 'pdf')]
    app.write_grand_staff_xml(left, right, str(xml), bpm=bpm)
    ms = app.find_musescore()
    app._render_once(ms, str(xml), str(midi), timeout=120)
    app._render_once(ms, str(xml), str(pdf), timeout=120)
    parsed = pretty_midi.PrettyMIDI(str(midi))
    notes = [n for inst in parsed.instruments for n in inst.notes]
    result = compare_notes(left + right, notes, tolerance=.025)
    result['tempo'] = [float(t) for t in parsed.get_tempo_changes()[1]]
    result['pdf_valid'] = app._valid_pdf(str(pdf))
    # MuseScore 自带演奏释放，因此 offset 单独报告，不要求每个 note-off 零误差。
    result['passed'] = (result['onset_recall'] == 1 and result['onset_precision'] == 1
                        and result['pdf_valid']
                        and result['mean_offset_error_sec'] < q / 4
                        and abs(result['tempo'][0] - bpm) < .01)
    return result


def frozen_scores(app, outdir):
    """用 MuseScore 回导三首完整冻结 MIDI 的新谱，核对起音/时值。

    只读既有 MIDI：这是导出回归，不声称测到了模型识别准确率。
    不用 music21.stripTies()：和弦各音的 tie 不一致时它不能逐音合并，
    会把持续音的记谱片段误统计为额外起音。
    """
    reports = {}
    for song in ('fanwut', 'shiki', 'jiabin'):
        paths = sorted((ROOT / '转谱验证' / song).glob('*_piano.mid'))
        if not paths:
            raise FileNotFoundError('missing frozen MIDI: ' + song)
        midi = pretty_midi.PrettyMIDI(str(paths[0]))
        hands = [[(n.start, n.end, n.pitch, n.velocity) for n in inst.notes]
                 for inst in midi.instruments]
        # 固定 BPM 只验证序列化；不借测试去改全曲实际速度。
        bpm = 120
        xml = outdir / (song + '_roundtrip.xml')
        app.write_grand_staff_xml(hands[0], hands[1] if len(hands) > 1 else [], str(xml), bpm=bpm)
        exported = outdir / (song + '_roundtrip.mid')
        app._render_once(app.find_musescore(), str(xml), str(exported), timeout=180)
        parsed = pretty_midi.PrettyMIDI(str(exported))
        restored = [n for inst in parsed.instruments for n in inst.notes]
        expected = [n for hand in hands[:2] for n in hand]
        metrics = compare_notes(expected, restored, tolerance=.063)
        metrics['source_sha256'] = hashlib.sha256(paths[0].read_bytes()).hexdigest()
        metrics['passed'] = (metrics['onset_recall'] == 1 and metrics['onset_precision'] == 1
                             and metrics['mean_offset_error_sec'] < .125)
        reports[song] = metrics
    return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--outdir', required=True)
    parser.add_argument('--source')
    parser.add_argument('--frozen', action='store_true')
    args = parser.parse_args()
    app = transcriber_app
    if args.source:
        spec = importlib.util.spec_from_file_location('fidelity_baseline', args.source)
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        # 模块快照的位置不是 ffmpeg 的位置；工具与两臂源码解耦。
        app.find_ffmpeg = transcriber_app.find_ffmpeg
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    report = {'source': os.path.abspath(app.__file__),
              'source_sha256': hashlib.sha256(Path(app.__file__).read_bytes()).hexdigest()}
    for name, check in [('decode', decode_roundtrip), ('score', score_roundtrip)]:
        try:
            report[name] = check(app, outdir)
        except Exception as exc:
            report[name] = {'passed': False, 'error': type(exc).__name__ + ': ' + str(exc)}
    if args.frozen:
        report['frozen_score_roundtrip'] = frozen_scores(app, outdir)
    report['passed'] = all(report[k]['passed'] for k in ('decode', 'score'))
    if args.frozen:
        report['passed'] = report['passed'] and all(
            r['passed'] for r in report['frozen_score_roundtrip'].values())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    (outdir / 'report.json').write_text(text, encoding='utf-8')
    print(text)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
