# -*- coding: utf-8 -*-
"""音符解码与制谱保真回归；不加载识别模型，也不修改冻结音轨。

python lang_dev/_test_fidelity.py
"""
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mido
import transcriber_app as T


def score_events(xml):
    """独立按 MusicXML 游标解析，合并 tie，返回 (staff, start, end, pitch)。

    单位为四分音符。检查所有小节时值及延音线配对，不能用生产代码反解。
    """
    root = ET.fromstring(xml)
    result, active = [], {}
    divisions = 1
    for bar, measure in enumerate(root.findall('./part/measure')):
        divisions = int(measure.findtext('attributes/divisions', str(divisions)))
        cursor, previous, furthest = 0, 0, 0
        for node in measure:
            if node.tag in ('backup', 'forward'):
                duration = int(node.findtext('duration'))
                cursor += duration * (-1 if node.tag == 'backup' else 1)
                if cursor < 0:
                    raise AssertionError('MusicXML cursor before measure start')
            if node.tag != 'note':
                continue
            duration = int(node.findtext('duration'))
            start = previous if node.find('chord') is not None else cursor
            if node.find('chord') is None:
                previous = start
                cursor += duration
            furthest = max(furthest, start + duration, cursor)
            if node.find('rest') is not None:
                continue
            p = node.find('pitch')
            pitch = (int(p.findtext('octave')) + 1) * 12
            pitch += dict(C=0, D=2, E=4, F=5, G=7, A=9, B=11)[p.findtext('step')]
            pitch += int(p.findtext('alter', '0'))
            staff = int(node.findtext('staff', '1'))
            key = (staff, node.findtext('voice', '1'), pitch)
            s, e = bar * 4 + start / divisions, bar * 4 + (start + duration) / divisions
            ties = {t.get('type') for t in node.findall('tie')}
            if 'stop' in ties:
                if key not in active or abs(active[key][2] - s) > 1e-8:
                    raise AssertionError('orphan or non-contiguous tie')
                event = active.pop(key)
                event[2] = e
            else:
                if key in active:
                    raise AssertionError('unclosed tie before reattack')
                event = [staff, s, e, pitch]
            if 'start' in ties:
                active[key] = event
            else:
                result.append(tuple(event))
        if furthest != divisions * 4 or cursor != divisions * 4:
            raise AssertionError('measure does not contain exactly four beats')
    if active:
        raise AssertionError('unclosed ties at end of score')
    return sorted(result)


class ScoreFidelity(unittest.TestCase):
    def xml(self, notes, bpm=120, **kwargs):
        return T.build_score_xml([('G', 2, notes)], bpm=bpm, **kwargs)

    def test_hands_start_together(self):
        xml = T.build_score_xml([('F', 4, [(0, .5, 48, 80)]),
                                 ('G', 2, [(0, .5, 72, 90)])])
        self.assertEqual(score_events(xml), [(1, 0, 1, 48), (2, 0, 1, 72)])

    def test_sixteenth_notes_do_not_collapse(self):
        notes = [(k * .125, (k + 1) * .125, 60, 80) for k in range(16)]
        got = score_events(self.xml(notes))
        self.assertEqual(got, [(1, k / 4, (k + 1) / 4, 60) for k in range(16)])

    def test_overlapping_sustain_keeps_its_duration(self):
        notes = [(0, 1.5, 60, 80), (.5, 1, 64, 80)]
        self.assertEqual(score_events(self.xml(notes)), [(1, 0, 3, 60), (1, 1, 2, 64)])

    def test_unequal_chord_durations(self):
        notes = [(0, .5, 60, 80), (0, 1.5, 64, 80), (0, 1, 67, 80)]
        self.assertEqual(score_events(self.xml(notes)),
                         [(1, 0, 1, 60), (1, 0, 2, 67), (1, 0, 3, 64)])

    def test_cross_bar_sustain_has_contiguous_ties(self):
        notes = [(1.5, 5, 60, 80), (1.75, 2.25, 64, 80), (3, 3.5, 67, 80)]
        self.assertEqual(score_events(self.xml(notes)),
                         [(1, 3, 10, 60), (1, 3.5, 4.5, 64), (1, 6, 7, 67)])

    def test_same_pitch_reattack_remains_separate(self):
        notes = [(0, .5, 60, 80), (.5, 1, 60, 80), (0, 1.5, 64, 80)]
        self.assertEqual(score_events(self.xml(notes)),
                         [(1, 0, 1, 60), (1, 0, 3, 64), (1, 1, 2, 60)])

    def test_quantized_tail_is_not_dropped(self):
        xml = self.xml([(1.999, 2.001, 60, 80)])
        self.assertEqual(score_events(xml), [(1, 4, 4.25, 60)])

    def test_tempo_is_written(self):
        root = ET.fromstring(self.xml([(0, .5, 60, 80)], bpm=90))
        tempos = [float(s.get('tempo')) for s in root.findall('.//sound')
                  if s.get('tempo') is not None]
        self.assertEqual(tempos, [90])

    def test_no_ties_fallback(self):
        root = ET.fromstring(self.xml([(0, 3, 60, 80), (.5, 1, 64, 80)],
                                     with_ties=False))
        self.assertEqual(root.findall('.//tie') + root.findall('.//tied'), [])

    def test_empty_score_and_requested_bars(self):
        xml = self.xml([], n_bars=3)
        self.assertEqual(score_events(xml), [])
        self.assertEqual(len(ET.fromstring(xml).findall('./part/measure')), 3)

    def test_all_midi_pitches_roundtrip(self):
        notes = [(k * .5, k * .5 + .5, k, 80) for k in range(128)]
        self.assertEqual([e[3] for e in score_events(self.xml(notes))], list(range(128)))

    def test_reference_splice_preserves_sixteenth_slots(self):
        xml = T.build_score_xml(
            [('F', 4, []), ('G', 2, [])],
            splice={0: {1: [(0, 48, 16, False, False)],
                        2: [(1, 72, 1, False, False)]}})
        self.assertEqual(score_events(xml), [(1, 0, 4, 48), (2, .25, .5, 72)])

    def test_validator_rejects_bad_backup(self):
        xml = T.build_score_xml([('F', 4, []), ('G', 2, [])])
        root = ET.fromstring(xml)
        for measure in root.findall('./part/measure'):
            for backup in measure.findall('backup'):
                measure.remove(backup)
        with self.assertRaises(AssertionError):
            score_events(ET.tostring(root, encoding='unicode'))

    def test_validator_rejects_orphan_tie(self):
        root = ET.fromstring(self.xml([(0, .5, 60, 80)]))
        ET.SubElement(root.find('.//note'), 'tie', type='stop')
        with self.assertRaises(AssertionError):
            score_events(ET.tostring(root, encoding='unicode'))


class MidiFidelity(unittest.TestCase):
    def midi(self, tracks, ticks=480):
        midi = mido.MidiFile(ticks_per_beat=ticks)
        midi.tracks.extend(mido.MidiTrack(t) for t in tracks)
        return midi

    def test_zero_velocity_note_on_is_note_off(self):
        midi = self.midi([[mido.Message('note_on', note=60, velocity=91),
                           mido.Message('note_on', note=60, velocity=0, time=480)]])
        self.assertEqual(T._mido_to_notes(midi), [(0, .5, 60, 91)])

    def test_tempo_map_applies_across_tracks(self):
        midi = self.midi([
            [mido.MetaMessage('set_tempo', tempo=500000),
             mido.MetaMessage('set_tempo', tempo=1000000, time=480)],
            [mido.Message('note_on', note=60, velocity=75),
             mido.Message('note_off', note=60, time=960)]])
        self.assertEqual(T._mido_to_notes(midi), [(0, 1.5, 60, 75)])

    def test_channels_do_not_close_each_others_notes(self):
        midi = self.midi([[
            mido.Message('note_on', channel=0, note=60, velocity=70),
            mido.Message('note_on', channel=1, note=60, velocity=90, time=240),
            mido.Message('note_off', channel=0, note=60, time=240),
            mido.Message('note_off', channel=1, note=60, time=240)]])
        self.assertEqual(T._mido_to_notes(midi), [(0, .5, 60, 70), (.25, .75, 60, 90)])

    def test_same_pitch_overlap_uses_fifo(self):
        midi = self.midi([[
            mido.Message('note_on', note=60, velocity=70),
            mido.Message('note_on', note=60, velocity=90, time=240),
            mido.Message('note_off', note=60, time=240),
            mido.Message('note_off', note=60, time=240)]])
        self.assertEqual(T._mido_to_notes(midi), [(0, .5, 60, 70), (.25, .75, 60, 90)])

    def test_unclosed_note_stops_at_file_end(self):
        midi = self.midi([[mido.Message('note_on', note=60, velocity=90),
                           mido.MetaMessage('end_of_track', time=480)]])
        self.assertEqual(T._mido_to_notes(midi), [(0, .5, 60, 90)])

    def test_default_tempo_and_resolution(self):
        midi = self.midi([[mido.Message('note_on', note=60, velocity=90),
                           mido.Message('note_off', note=60, time=960)]], ticks=960)
        self.assertEqual(T._mido_to_notes(midi, tempo_bpm=60), [(0, 1, 60, 90)])

    def test_percussion_channel_is_not_pitched_music(self):
        midi = self.midi([[mido.Message('note_on', channel=9, note=36, velocity=90),
                           mido.Message('note_off', channel=9, note=36, time=480)]])
        self.assertEqual(T._mido_to_notes(midi), [])

    def test_empty_midi(self):
        self.assertEqual(T._mido_to_notes(self.midi([])), [])


class AudioFidelity(unittest.TestCase):
    def test_decode_preserves_channels_and_sample_rate(self):
        import soundfile as sf
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'decoded.wav')
            sf.write(out, np.zeros((100, 2)), 44100, subtype='FLOAT')
            with patch.object(T.subprocess, 'run',
                              return_value=SimpleNamespace(returncode=0, stderr='')) as run:
                T.decode_to_wav('test.mp3', 'ffmpeg', out, lambda _msg: None)
            cmd = run.call_args[0][0]
            self.assertNotIn('-ac', cmd)
            self.assertNotIn('-ar', cmd)
            self.assertIn('pcm_f32le', cmd)

    def test_decoder_failure_does_not_accept_stale_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'stale.wav')
            with open(out, 'wb') as f:
                f.write(b'old output')
            with patch.object(T.subprocess, 'run',
                              return_value=SimpleNamespace(returncode=1, stderr='bad input')):
                with self.assertRaises(RuntimeError):
                    T.decode_to_wav('bad.mp3', 'ffmpeg', out, lambda _msg: None)

    def test_native_audio_is_not_transcoded(self):
        with patch.object(T.subprocess, 'run') as run:
            self.assertEqual(T.decode_to_wav('native.flac', None, 'unused.wav',
                                             lambda _msg: None), 'native.flac')
            run.assert_not_called()

    def test_antiphase_stereo_separation_is_finite(self):
        import numpy as np
        import torch
        import demucs.apply

        tone = np.sin(np.arange(2048) * .1).astype('float32')
        audio = np.stack((tone, -tone))
        model = SimpleNamespace(samplerate=44100, sources=['vocals'], cpu=lambda: None)
        observed = []

        def apply(_model, wav, **_kwargs):
            self.assertTrue(bool(torch.isfinite(wav).all()), 'normalization produced NaN/Inf')
            observed.append(True)
            return wav[:, None, :, :]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(demucs.apply, 'apply_model', apply), patch.dict(T._SEP_CACHE, model=model):
                with patch('librosa.load', return_value=(audio, 44100)):
                    result = T.separate_stems('unused.wav', tmp, 'test', lambda _msg: None)
        self.assertTrue(observed, 'separation failed before reaching model')
        self.assertIsNotNone(result)

    def test_silence_does_not_reach_separation_model(self):
        import numpy as np
        import demucs.apply
        from unittest.mock import Mock

        model = SimpleNamespace(samplerate=44100, sources=['vocals'], cpu=lambda: None)
        apply = Mock()
        with patch.object(demucs.apply, 'apply_model', apply), patch.dict(T._SEP_CACHE, model=model):
            with patch('librosa.load', return_value=(np.zeros((2, 2048)), 44100)):
                result = T.separate_stems('unused.wav', '.', 'test', lambda _msg: None)
        self.assertIsNone(result)
        apply.assert_not_called()


class ArrangementFidelity(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get('TS_ARRANGEMENT')

    def tearDown(self):
        if self.old is None:
            os.environ.pop('TS_ARRANGEMENT', None)
        else:
            os.environ['TS_ARRANGEMENT'] = self.old

    def test_studio_is_default_and_classic_is_explicit(self):
        os.environ.pop('TS_ARRANGEMENT', None)
        self.assertEqual(T._arrangement_mode(), 'studio')
        os.environ['TS_ARRANGEMENT'] = 'classic'
        self.assertEqual(T._arrangement_mode(), 'classic')

    def test_playable_harmony_keeps_root_and_one_color_note(self):
        notes = [
            (0.0, 0.4, 48, 80), (0.01, 0.5, 52, 70),
            (0.02, 0.45, 55, 75), (0.03, 0.4, 60, 90),
        ]
        got = T._sparsify_harmony_playable(notes, min_gap=0.55)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][2], 48)
        self.assertNotIn(60, [n[2] for n in got])

    def test_studio_left_hand_is_two_note_chord_not_single_bass(self):
        melody = [(0.0, 1.0, 76, 100)]
        harmony = [(0.0, 1.0, 48, 90), (0.0, 1.0, 52, 80),
                   (0.0, 1.0, 60, 75)]
        _, left_studio, _ = T.fuse_to_piano(
            melody, harmony, arrangement='studio')
        _, left_classic, _ = T.fuse_to_piano(
            melody, harmony, arrangement='classic')
        self.assertEqual(len(left_studio), 2)
        self.assertEqual(len(left_classic), 1)
        self.assertTrue(max(n[2] for n in left_studio) -
                        min(n[2] for n in left_studio) <= 14)

    def test_studio_releases_overlapping_accompaniment_at_new_onset(self):
        harmony = [(0.0, 2.0, 48, 90), (0.5, 1.0, 52, 80)]
        _, left_studio, _ = T.fuse_to_piano([], harmony, arrangement='studio')
        _, left_classic, _ = T.fuse_to_piano([], harmony, arrangement='classic')
        studio = {n[2]: n for n in left_studio}
        classic = {n[2]: n for n in left_classic}
        self.assertLessEqual(studio[48][1], 0.51)
        self.assertGreater(classic[48][1], 1.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
