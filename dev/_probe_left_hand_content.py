# -*- coding: utf-8 -*-
"""诊断：左手在 80ms 窗口里到底塞了几个音、有几个是重复八度。

_simple_piano 的左手用 fix_hand(max_notes=4, mode="mix")，而分轨路径
fuse_to_piano 的左手是 fix_hand(max_notes=1, mode="accomp")（只留最低音贝斯骨干）。
本探针量的是：出厂路径的左手比"贝斯骨干"多出来多少内容、其中多少是
同音高重复 / 八度重复（听感与谱面上都表现为"手在打架"）。

用法：
    python lang_dev/_probe_left_hand_content.py "<a.mid>"
"""
import sys
from collections import Counter

import pretty_midi

WIN = 0.08


def window_octave_dups(hand, win=WIN):
    """每 win 秒里，比本窗最低音高一个八度（或同音高）的重复音有多少。

    这些就是"同一只手把自己的贝斯/旋律音又高八度写一遍"的音：
    谱面上是两只手在同一个节奏点上互抢，实际是同一条线的自我加倍。
    """
    hand = sorted(hand)
    drop, windows, total = 0, 0, len(hand)
    i = 0
    while i < len(hand):
        t0, j, cur = hand[i][0], i, []
        while j < len(hand) and hand[j][0] <= t0 + win:
            cur.append(hand[j])
            j += 1
        i = j
        if len(cur) < 2:
            continue
        windows += 1
        low = min(c[2] for c in cur)
        for c in cur:
            if c[2] != low and (c[2] - low) % 12 == 0:
                drop += 1
    return drop, windows, total


def main(path):
    pm = pretty_midi.PrettyMIDI(path)
    left, right = [], []
    for inst in pm.instruments:
        bag = right if inst.name.lower().startswith('r') else left
        for n in inst.notes:
            bag.append((float(n.start), float(n.end), int(n.pitch), int(n.velocity)))
    left.sort()
    dur = max(n[1] for n in left + right)

    for tag, hand in (('左手', left), ('右手', right)):
        d, w, t = window_octave_dups(hand)
        print('%s：%d 音，多音窗 %d 个，其中"高一个八度的自我加倍" %d 个（占该手 %.1f%%）'
              % (tag, t, w, d, 100.0 * d / max(1, t)))

    # 按 80ms 窗口聚簇（与 fix_hand 的 window 一致）
    keep, multi, dup_exact, dup_oct, distinct = 0, 0, 0, 0, 0
    i = 0
    extra_notes = 0
    while i < len(left):
        t0, j, cur = left[i][0], i, []
        while j < len(left) and left[j][0] <= t0 + WIN:
            cur.append(left[j])
            j += 1
        i = j
        if len(cur) == 1:
            keep += 1
            continue
        multi += 1
        low = min(cur, key=lambda x: x[2])[2]
        extra_notes += len(cur) - 1
        ps = Counter(c[2] for c in cur)
        for p, c in ps.items():
            if c > 1:
                dup_exact += c - 1
            elif abs(p - low) == 12:
                dup_oct += 1
            else:
                distinct += 1
    print('左手 %d 音 / %.1fs' % (len(left), dur))
    print('  单音窗口 %d 个；多音窗口 %d 个（占 %.1f%%）'
          % (keep, multi, 100.0 * multi / max(1, keep + multi)))
    print('  若改成"每窗只留最低音贝斯骨干"，会去掉 %d 个音（占左手 %.1f%%）'
          % (extra_notes, 100.0 * extra_notes / max(1, len(left))))
    print('  被去掉的音里：同音高重复 %d，低音上方一个八度 %d，其它音级 %d'
          % (dup_exact, dup_oct, distinct))

    # 左手与右手起音错位：本该同时的音差几毫秒
    offs = []
    for s, e, p, v in left:
        near = [r for r in right if 0 < abs(r[0] - s) <= 0.05]
        if near:
            offs.append(min(abs(r[0] - s) for r in near) * 1000)
    if offs:
        offs.sort()
        print('  左手音附近(≤50ms)有右手音 %d 处，错位中位 %.0fms、最大 %.0fms'
              % (len(offs), offs[len(offs) // 2], offs[-1]))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
