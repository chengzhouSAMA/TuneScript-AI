# -*- coding: utf-8 -*-
"""诊断「音太碎 + 不识别间奏」：拿真实产物的 MIDI 和分轨量出来，不靠感觉。

- 「碎」：短音占比、碎片率（同音高相邻短音）、左右手每秒音数
- 「不识别间奏」：无人声区间里**右手**有多少时间是有音的、最长空档多长
  （无人声区间由 **人声轨音频本身** 判定，不用产物里的任何中间量，避免自证）

    python lang_dev/_diag_frag_interlude.py 回归验收/out/fanwut_B_r1on
    python lang_dev/_diag_frag_interlude.py 回归验收/out/fanwut_B_r1on 回归验收/out/fanwut_B_r1off
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ai_transcriber_dev'))

VOCAL = {
    'fanwut': ('回归验收/_stems/fanwut/反乌托邦 - 乌托邦P_vocals.wav', 0.0),
}


def vocal_regions(out_dir):
    """从**人声轨音频**判「有没有人在唱」：返回 (有人声区间, 无人声区间)。"""
    import numpy as np
    import soundfile as sf
    key = os.path.basename(out_dir.rstrip('/\\')).split('_')[0]
    src = os.path.join(ROOT, '回归验收', '_stems', key)
    voc = None
    if os.path.isdir(src):
        for f in os.listdir(src):
            if f.endswith('_vocals.wav'):
                voc = os.path.join(src, f)
                break
    if not voc or not os.path.isfile(voc):
        return None, None
    y, sr = sf.read(voc, dtype='float32', always_2d=True)
    y = y.mean(1)
    hop = int(sr * 0.05)
    n = len(y) // hop
    rms = np.array([float(np.sqrt(np.mean(y[i * hop:(i + 1) * hop] ** 2) + 1e-12))
                    for i in range(n)])
    db = 20 * np.log10(rms + 1e-12)
    voiced = db > (db.max() - 35.0)        # 相对峰值 -35dB 以上算"有人在唱"
    # 合并短空档（<0.4s）与短有声段（<0.4s）
    def _merge(mask, min_len):
        k = int(min_len / 0.05)
        out = mask.copy()
        i = 0
        while i < len(out):
            if not out[i]:
                j = i
                while j < len(out) and not out[j]:
                    j += 1
                if j - i < k and i > 0 and j < len(out):
                    out[i:j] = True
                i = j
            else:
                i += 1
        return out
    voiced = _merge(voiced, 0.4)
    # 只保留 > 1.5s 的无人声段才算"间奏/前奏"
    spans, i = [], 0
    while i < len(voiced):
        if not voiced[i]:
            j = i
            while j < len(voiced) and not voiced[j]:
                j += 1
            if (j - i) * 0.05 >= 1.5:
                spans.append((i * 0.05, j * 0.05))
            i = j
        else:
            i += 1
    dur = len(voiced) * 0.05
    gaps = []
    cur = 0.0
    for a, b in spans:
        if a > cur:
            gaps.append((cur, a))
        cur = b
    if cur < dur:
        gaps.append((cur, dur))
    return gaps, spans


def hand_stats(notes, spans):
    """spans 内：有音时间占比（区间并集）、最长空档、每秒音数。"""
    if not spans:
        return None
    total = sum(b - a for a, b in spans)
    cov = 0.0
    longest = 0.0
    for a, b in spans:
        seg = sorted((max(s, a), min(e, b)) for s, e, _p, _v in notes
                     if e > a and s < b and min(e, b) > max(s, a))
        merged = []
        for s, e in seg:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        cur = a
        for s, e in merged:
            if s > cur:
                longest = max(longest, s - cur)
            cur = max(cur, e)
        if cur < b:
            longest = max(longest, b - cur)
        cov += sum(e - s for s, e in merged)
    n = sum(1 for s, _e, _p, _v in notes if any(a <= s < b for a, b in spans))
    return {'sec': total, 'cover': cov / total if total else 0.0,
            'longest_gap': longest, 'per_sec': n / total if total else 0.0}


def collisions(notes):
    """同音高、时间重叠的「重复触发」对数 —— 听感上就是'抖/碎'。"""
    by = {}
    for s, e, p, _v in notes:
        by.setdefault(p, []).append((s, e))
    n = 0
    for p, segs in by.items():
        segs.sort()
        for i in range(1, len(segs)):
            if segs[i][0] < segs[i - 1][1] - 1e-6:
                n += 1
    return n


def report(out_dir):
    import pretty_midi
    if not os.path.isdir(out_dir):
        print('=== %s ===\n  !! 目录不存在，跳过' % out_dir)
        return
    mid = None
    for f in os.listdir(out_dir):
        if f.endswith('.mid'):
            mid = os.path.join(out_dir, f)
            break
    if not mid:
        print('  !! 没有 MIDI')
        return
    pm = pretty_midi.PrettyMIDI(mid)
    tracks = {}
    for inst in pm.instruments:
        nm = (inst.name or '').upper()
        hand = 'L' if nm.startswith('L') else ('R' if nm.startswith('R') else nm)
        tracks[hand] = [(n.start, n.end, n.pitch, n.velocity) for n in inst.notes]
    alln = [(s, e, p, v) for h in tracks.values() for (s, e, p, v) in h]
    durs = [e - a0 for a0, e, _p, _v in alln]
    short = sum(1 for d in durs if d < 0.15)
    vshort = sum(1 for d in durs if d < 0.08)
    try:
        from fragmentation import fragment_ratio
        fr = fragment_ratio([[s, e, p] for s, e, p, _v in alln])[0]
    except Exception:
        fr = None
    dur_total = max([e for _s, e, _p, _v in alln] + [0.0])

    print('=== %s ===' % out_dir)
    print('  音符总数 %d   总时长 %.1fs' % (len(alln), dur_total))
    print('  左右手：L=%d  R=%d' % (len(tracks.get('L', [])), len(tracks.get('R', []))))
    print('  ★ 碎：短音(<0.15s) %d 个 = %.1f%%   极短(<0.08s) %d 个 = %.1f%%'
          % (short, 100.0 * short / max(1, len(alln)),
             vshort, 100.0 * vshort / max(1, len(alln))))
    print('  ★ 同音高重叠(重复触发) L=%d  R=%d  合计 %d'
          % (collisions(tracks.get('L', [])), collisions(tracks.get('R', [])),
             collisions(alln)))
    if fr is not None:
        print('  ★ 碎片率 fragment_ratio = %.4f' % fr)
    for h in ('L', 'R'):
        n = tracks.get(h, [])
        if n and dur_total:
            print('    %s 密度 %.2f 音/秒' % (h, len(n) / dur_total))

    sung, gaps = vocal_regions(out_dir)
    if gaps is None:
        print('  (找不到人声轨，跳过间奏分析)')
        return
    print('  人声段 %d 段 / 无人声段 %d 段'
          % (len(sung), len(gaps)))
    for h in ('R', 'L'):
        st = hand_stats(tracks.get(h, []), gaps)
        if st:
            print('    ★ 无人声段里的 %s：%s 区间内**有音**占 %.1f%%，最长空档 %.2fs，%.2f 音/秒'
                  % (h, '%.0f' % st['sec'] + 's', 100.0 * st['cover'],
                     st['longest_gap'], st['per_sec']))
        st2 = hand_stats(tracks.get(h, []), sung)
        if st2:
            print('      有人声段里的 %s：有音占 %.1f%%，最长空档 %.2fs，%.2f 音/秒'
                  % (h, 100.0 * st2['cover'], st2['longest_gap'], st2['per_sec']))
    print('  最长的一段无人声：%.1f ~ %.1f s'
          % (max(gaps, key=lambda g: g[1] - g[0]) if gaps else (0, 0)))


if __name__ == '__main__':
    dirs = sys.argv[1:] or ['回归验收/out/fanwut_B_r1on']
    for d in dirs:
        report(d)
        print()
