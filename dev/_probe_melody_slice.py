# -*- coding: utf-8 -*-
"""把产物 MIDI 的一段时间轴按音名铺出来，看左右手到底在抢什么。

用法：
    python lang_dev/_probe_melody_slice.py "<a.mid>" [起点秒] [时长秒]
"""
import sys

import pretty_midi

SPLIT = 60
NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def name(p):
    return '%s%d' % (NAMES[p % 12], p // 12 - 1)


def main(path, t0, dur):
    pm = pretty_midi.PrettyMIDI(path)
    left, right = [], []
    for inst in pm.instruments:
        bag = right if inst.name.lower().startswith('r') else left
        for n in inst.notes:
            bag.append((float(n.start), float(n.end), int(n.pitch), int(n.velocity)))
    rows = []
    for tag, bag in (('L', left), ('R', right)):
        for s, e, p, v in bag:
            if t0 <= s < t0 + dur:
                rows.append((s, tag, p, v, e))
    rows.sort()
    print('时间轴 %.2f~%.2fs（★ = 该时刻两手同时有音）' % (t0, t0 + dur))
    print('%-8s %-4s %-6s %-5s %s' % ('起点', '手', '音高', '力度', '时值'))
    prev_t = None
    for s, tag, p, v, e in rows:
        star = ''
        for s2, tag2, p2, v2, e2 in rows:
            if tag2 != tag and abs(s2 - s) <= 0.03:
                star = ' ★ 与另一手同时'
                break
        mark = '' if p < SPLIT else ''
        side = '左' if tag == 'L' else '右'
        print('%-8.3f %-4s %-6s %-5d %.3f%s%s'
              % (s, side, '%s(%d)%s' % (name(p), p, mark), v, e - s,
                 '   ← 低于C4' if p < SPLIT else '', star))
        prev_t = s


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1],
         float(sys.argv[2]) if len(sys.argv) > 2 else 0.0,
         float(sys.argv[3]) if len(sys.argv) > 3 else 12.0)
