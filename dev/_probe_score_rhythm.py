# -*- coding: utf-8 -*-
"""诊断：大谱表 XML 里左右手在谱面上是否互相错位（「抢节奏」的谱面证据）。

不改源码。只读 MusicXML，看：
  ① 每只手落在非 16 分格上的音有多少（= 谱面被写成碎分音/连音，读不出来）；
  ② 左右手在"同一拍点"上是否真的对齐（差 1 个 division 就是错位）；
  ③ 一个拍点上有几种细分（细分越杂，越像两手互相抢）。

用法：
    python lang_dev/_probe_score_rhythm.py "<x.xml>"
"""
import sys
import xml.etree.ElementTree as ET
from collections import Counter


def q(tag):
    return tag.split('}')[-1]


def parse(path):
    root = ET.parse(path).getroot()
    out = []          # (part, measure_idx, staff, voice, onset_div, dur_div, pitch)
    for part in root:
        if q(part.tag) != 'part':
            continue
        pid = part.get('id', '?')
        div = None                      # divisions 只在第 1 小节出现，往后沿用
        for mi, meas in enumerate(part):
            if q(meas.tag) != 'measure':
                continue
            t = 0
            for el in meas:
                k = q(el.tag)
                if k == 'attributes':
                    d = el.find('divisions')
                    if d is not None:
                        div = int(d.text)
                elif k == 'note':
                    is_chord = el.find('chord') is not None
                    if is_chord:
                        t0 = t
                    else:
                        t0 = t
                    dur = el.find('duration')
                    dur = int(dur.text) if dur is not None else 0
                    rest = el.find('rest') is not None
                    staff = el.find('staff')
                    staff = int(staff.text) if staff is not None else 1
                    voice = el.find('voice')
                    voice = voice.text if voice is not None else '1'
                    p = el.find('pitch')
                    pitch = None
                    if p is not None and not rest:
                        step = (p.findtext('step') or '?')
                        octv = p.findtext('octave') or '?'
                        alt = p.findtext('alter') or ''
                        pitch = '%s%s%s' % (step, alt, octv)
                    if not is_chord:
                        pass
                    if not rest:
                        out.append((pid, mi, staff, voice, t0, dur, pitch, div))
                    if not is_chord:
                        t += dur
    return out


def main(path):
    notes = parse(path)
    if not notes:
        raise SystemExit('没解析出音符')
    divs = Counter(n[7] for n in notes)
    div = divs.most_common(1)[0][0]
    per_measure = div * 4          # 假设 4/4
    print('文件: %s' % path)
    print('divisions=%s（每四分音符）  音符 %d 个  声部 %s'
          % (dict(divs), len(notes), sorted({n[3] for n in notes})))

    # ① 落在 16 分格外的音
    g16 = max(1, per_measure // 16)
    off16 = [n for n in notes if n[4] % g16 != 0]
    g8 = max(1, per_measure // 8)
    off8 = [n for n in notes if n[4] % g8 != 0]
    print('① 非 16 分格起音 %d/%d (%.1f%%)；非 8 分格 %d/%d (%.1f%%)'
          % (len(off16), len(notes), 100.0 * len(off16) / len(notes),
             len(off8), len(notes), 100.0 * len(off8) / len(notes)))
    if off16:
        c = Counter(n[4] % g16 for n in off16)
        print('   偏移余数分布(相对 16 分格): %s' % c.most_common(8))

    # ② 左右手"应该对齐却没对齐"
    bymeas = {}
    for n in notes:
        bymeas.setdefault(n[1], []).append(n)
    close, exact, total = 0, 0, 0
    worst = Counter()
    for mi, ns in bymeas.items():
        for a in ns:
            if a[2] != 1:
                continue
            for b in ns:
                if b[2] != 2:
                    continue
                d = abs(a[4] - b[4])
                if d > 2 * g16:
                    continue                       # 不同拍点，不算
                total += 1
                if d == 0:
                    exact += 1
                else:
                    close += 1
                    worst[d] += 1
    if total:
        print('② 左右手相邻起音对 %d 组：完全对齐 %d (%.1f%%)，错开 %d (%.1f%%)'
              % (total, exact, 100.0 * exact / total, close, 100.0 * close / total))
        print('   错开的 division 差: %s' % worst.most_common(8))

    # ③ 细分复杂度
    sub = Counter()
    for mi, ns in bymeas.items():
        ons = sorted({n[4] for n in ns})
        sub[len(ons)] += 1
    print('③ 每小节独立起音点数分布: %s' % sub.most_common(10))
    print('   平均每小节 %.1f 个起音点' % (sum(k * v for k, v in sub.items()) / max(1, sum(sub.values()))))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
