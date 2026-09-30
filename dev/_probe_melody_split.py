# -*- coding: utf-8 -*-
"""诊断：产物 MIDI 里「一条旋律线被 C4 硬切劈成两手」到什么程度。

不改任何源码，只读 MIDI。用来给「左右手抢节奏」定量取证。

用法：
    python lang_dev/_probe_melody_split.py "<a.mid>" ["<b.mid>" ...]
"""
import sys
from collections import Counter

import pretty_midi

SPLIT = 60          # _simple_piano 的 split_pitch
WIN = 0.08          # 起音簇窗口，与 _split_melody_accomp / fix_hand 一致
GAP = 0.60          # 连成"同一条线"允许的最大时间间隔


def hands(path):
    pm = pretty_midi.PrettyMIDI(path)
    left, right = [], []
    for inst in pm.instruments:
        bag = right if inst.name.lower().startswith('r') else left
        for n in inst.notes:
            bag.append((float(n.start), float(n.end), int(n.pitch), int(n.velocity)))
    return sorted(left), sorted(right)


def clusters(notes, win=WIN):
    """并集按时间聚成起音簇（簇内最高音 = 旋律候选）。"""
    notes = sorted(notes)
    out, i = [], 0
    while i < len(notes):
        t0, j, cur = notes[i][0], i, []
        while j < len(notes) and notes[j][0] <= t0 + win:
            cur.append(notes[j])
            j += 1
        out.append(cur)
        i = j
    return out


def line_stats(cand):
    """把旋律候选串成线，统计"跨 C4 的线"占比与低音支的跳进。"""
    runs, cur = [], []
    for idx, (t, p) in enumerate(cand):
        if cur:
            pt, pp = cand[cur[-1]]
            if (t - pt) > GAP or abs(p - pp) > 12:
                runs.append(cur)
                cur = []
        cur.append(idx)
    if cur:
        runs.append(cur)
    cross, steps = [], Counter()
    for run in runs:
        ps = [cand[i][1] for i in run]
        if min(ps) < SPLIT <= max(ps):          # 真跨过 C4 的线
            cross.append(run)
            for a, b in zip(ps, ps[1:]):
                steps[b - a] += 1
    return runs, cross, steps


def report(path):
    print('=' * 78)
    print(path)
    left, right = hands(path)
    both = left + right
    if not both:
        print('  空 MIDI')
        return
    dur = max(n[1] for n in both)
    print('  时长 %.1fs   左手 %d 音(%.2f/s, %d~%d)   右手 %d 音(%.2f/s, %d~%d)'
          % (dur, len(left), len(left) / dur,
             min(p for _, _, p, _ in left), max(p for _, _, p, _ in left),
             len(right), len(right) / dur,
             min(p for _, _, p, _ in right), max(p for _, _, p, _ in right)))

    # ① 产物里有没有"同一时刻两手都响"→ 有就不是单纯的音高互补
    lo = max(min(p for _, _, p, _ in left), min(p for _, _, p, _ in right))
    print('  左手最高 %d / 右手最低 %d → %s'
          % (max(p for _, _, p, _ in left), min(p for _, _, p, _ in right),
             '重叠' if lo <= max(p for _, _, p, _ in left) else '不重叠(硬切特征)'))

    # ② 旋律候选线（并集里每个起音簇的最高音）
    cs = clusters(both)
    cand = []
    for c in cs:
        top = max(c, key=lambda x: x[2])
        cand.append((top[0], top[2]))
    low = [(t, p) for t, p in cand if p < SPLIT]
    print('  旋律候选 %d 个，其中 <C4 的 %d 个 (%.1f%%)'
          % (len(cand), len(low), 100.0 * len(low) / max(1, len(cand))))

    runs, cross, steps = line_stats(cand)
    n_in = sum(len(r) for r in cross)
    print('  串成 %d 条线；其中跨 C4 的 %d 条（共 %d 个音，占候选 %.1f%%）'
          % (len(runs), len(cross), n_in, 100.0 * n_in / max(1, len(cand))))
    if steps:
        near = sum(v for k, v in steps.items() if abs(k) <= 7)
        print('  跨 C4 线内的相邻音程：|≤7| 的 %d / %d (%.0f%%)  → %s'
              % (near, sum(steps.values()), 100.0 * near / sum(steps.values()),
                 '是小步级进(真是一条线)' if near * 2 > sum(steps.values()) else '是大跳(不是一条线)'))
        print('  最常见的音程：', steps.most_common(6))

    # ③ 相邻换手：左右手交替多快
    ev = sorted([(n[0], 'L') for n in left] + [(n[0], 'R') for n in right])
    sw = sum(1 for a, b in zip(ev, ev[1:]) if a[1] != b[1] and (b[0] - a[0]) <= 0.25)
    print('  ≤0.25s 内换手 %d 次 → 平均每 %.2fs 换一次' % (sw, dur / max(1, sw)))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for a in sys.argv[1:]:
        try:
            report(a)
        except Exception as e:
            print('  !! %s: %s' % (type(e).__name__, e))
