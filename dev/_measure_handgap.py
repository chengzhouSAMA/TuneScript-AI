# -*- coding: utf-8 -*-
"""R1（一个八度）/ R2（无人声段加强伴奏）的实测对照。

直接喂 `回归验收/_stems/<key>/` 里已分离好的六轨给 `transcribe_stems_enhanced`，
跳过 Demucs 与渲染，只比「分轨 → 左右手」这一段 —— 也就是这次改动的全部范围。

两臂（同一份源码、同一份素材，只改环境变量）：
    OLD: TS_HAND_GAP=0  TS_ACCOMP_BOOST=0   ← 改动前的行为
    NEW: 默认（两者都开）                    ← 本次改动

用法：
    python lang_dev/_measure_handgap.py --songs fanwut,monitoring,shiki
"""
import argparse
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

STEM_KEYS = ['vocals', 'drums', 'bass', 'guitar', 'piano', 'other']

# key -> 歌名（= _stems 下的文件主名），与 回归验收/regress_one.py 保持一致
SONGS = {
    'jiabin': '嘉宾 (粤语版) - 张远',
    'shiki': '柿崎ユウタ - バカみたいに（像个笨蛋一样） - KomisI-w',
    'fanwut': '反乌托邦 - 乌托邦P',
    'monitoring': 'モニタリング - DECO27,初音ミク',
    'gouzhi': '勾指起誓 - 洛天依Official,ilem',
}


def stems_of(key):
    d = os.path.join(ROOT, '回归验收', '_stems', key)
    out = {}
    for k in STEM_KEYS:
        p = os.path.join(d, '%s_%s.wav' % (SONGS[key], k))
        if os.path.isfile(p):
            out[k] = p
    return out, d


def density(notes, spans):
    """spans 内每秒有多少个音（spans 为空返回 None）。"""
    if not spans:
        return None
    total = sum(e - s for s, e in spans)
    if total <= 0:
        return None
    n = sum(1 for s, _e, _p, _v in notes if any(a <= s < b for a, b in spans))
    return n / total


def complement(spans, t_end):
    """spans 的补集（= 有人声区间）。"""
    out = []
    cur = 0.0
    for s, e in sorted(spans):
        if s > cur:
            out.append((cur, s))
        cur = max(cur, e)
    if cur < t_end:
        out.append((cur, t_end))
    return out


def run_arm(ta, key, arm):
    """跑一臂，返回测量结果 dict。"""
    stems, _d = stems_of(key)
    if not stems.get('vocals'):
        return {'error': '缺少 vocals 轨'}

    if arm == 'OLD':
        os.environ['TS_HAND_GAP'] = '0'
        os.environ['TS_ACCOMP_BOOST'] = '0'
    else:
        os.environ.pop('TS_HAND_GAP', None)
        os.environ.pop('TS_ACCOMP_BOOST', None)

    cap = {}
    orig_fvg = ta._find_vocal_gaps
    orig_build = ta._build_accomp
    orig_gap = ta._enforce_octave_gap

    def spy_gap(l, r, **kw):
        lo, ro, st = orig_gap(l, r, **kw)
        cap['gapst'] = st
        cap['gap_in_left'] = len(l)
        cap['gap_in_right'] = len(r)
        cap['gap_out_left'] = lo
        cap['gap_out_right'] = ro
        return lo, ro, st

    def spy_fvg(v, o, **kw):
        r = orig_fvg(v, o, **kw)
        cap['gaps'] = r
        return r

    def spy_build(other_notes, in_gap, gaps, **kw):
        r = orig_build(other_notes, in_gap, gaps, **kw)
        cap['accomp'] = r
        cap['extra'] = kw.get('extra') or []
        cap['n_other_in'] = len(other_notes)
        return r

    ta._find_vocal_gaps = spy_fvg
    ta._build_accomp = spy_build
    ta._enforce_octave_gap = spy_gap
    logs = []
    t0 = time.time()
    try:
        mp = ta.find_model()
        with tempfile.TemporaryDirectory(prefix='handgap_') as td:
            res = ta.transcribe_stems_enhanced(stems, mp, logs.append, out_dir=td)
    except Exception as e:
        import traceback
        return {'error': '%s: %s' % (type(e).__name__, e),
                'tb': traceback.format_exc()[-500:]}
    finally:
        ta._find_vocal_gaps = orig_fvg
        ta._build_accomp = orig_build
        ta._enforce_octave_gap = orig_gap
        os.environ.pop('TS_HAND_GAP', None)
        os.environ.pop('TS_ACCOMP_BOOST', None)
    dt = time.time() - t0
    if res is None:
        return {'error': '返回 None（人声轨音符过少）'}
    _midi, left, right = res
    gaps = cap.get('gaps') or []
    accomp = cap.get('accomp') or []
    extra = cap.get('extra') or []

    t_end = max([e for _s, e, _p, _v in left + right] + [0.0])
    sung = complement(gaps, t_end)
    gap_notes = [n for n in accomp if any(a <= n[0] < b for a, b in gaps)]
    sung_notes = [n for n in accomp if not any(a <= n[0] < b for a, b in gaps)]

    r1gap = ta._hand_gap_min(left, right)
    return {
        'arm': arm,
        'seconds': round(dt, 1),
        'n_left': len(left), 'n_right': len(right),
        'gap_min': r1gap[0], 'gap_cells': r1gap[1],
        'left': left, 'right': right,
        # R1 违规格数：逐格统计「右手最低音 − 左手最高音 < 12」的格子
        'gap_viol': _viol_cells(left, right, ta),
        # R2：伴奏（_build_accomp 的产物）在无人声段 / 有人声段的密度
        'n_accomp': len(accomp),
        'accomp_gap_dens': _r(density(accomp, gaps)),
        'accomp_sung_dens': _r(density(accomp, sung)),
        'gap_sec': round(sum(e - s for s, e in gaps), 1),
        'sung_sec': round(sum(e - s for s, e in sung), 1),
        # 最终左手（过了 fuse_to_piano 的抽稀/去噪）在无人声段的密度
        'left_gap_dens': _r(density(left, gaps)),
        'left_sung_dens': _r(density(left, sung)),
        'extra_note': len(extra),
        'other_note': cap.get('n_other_in'),
        'n_gap': len(gaps),
        'gapst': cap.get('gapst'),
        'viol_detail': _viol_detail(left, right, ta),
    }


def _viol_detail(left, right, ta, grid=0.02, limit=12):
    """列出仍然违约的时间格（右手最低音 − 左手最高音 < limit）。"""
    if not left or not right:
        return []
    hi_t = max(max(e for _s, e, _p, _v in left),
               max(e for _s, e, _p, _v in right))
    n = int(hi_t / grid) + 2
    r_min = ta._right_min_cells(right, hi_t, grid)
    l_max = [None] * n
    for s, e, p, _v in left:
        for i in range(max(0, int(s / grid)), min(n - 1, int(e / grid)) + 1):
            if l_max[i] is None or p > l_max[i]:
                l_max[i] = p
    out = []
    for i in range(n):
        if r_min[i] is not None and l_max[i] is not None and r_min[i] - l_max[i] < limit:
            if len(out) < 40:
                out.append({'i': i, 't': round(i * grid, 2),
                            'right_min': int(r_min[i]), 'left_max': int(l_max[i]),
                            'gap': int(r_min[i] - l_max[i])})
    return out


def _viol_cells(left, right, ta, grid=0.02):
    """「右手最低音 − 左手最高音 < 12」的时间格数。"""
    if not left or not right:
        return 0
    hi_t = max(max(e for _s, e, _p, _v in left),
               max(e for _s, e, _p, _v in right))
    n = int(hi_t / grid) + 2
    r_min = ta._right_min_cells(right, hi_t, grid)
    l_max = [None] * n
    for s, e, p, _v in left:
        for i in range(max(0, int(s / grid)), min(n - 1, int(e / grid)) + 1):
            if l_max[i] is None or p > l_max[i]:
                l_max[i] = p
    k = 0
    for i in range(n):
        if r_min[i] is not None and l_max[i] is not None and r_min[i] - l_max[i] < 12:
            k += 1
    return k


def _r(x):
    return None if x is None else round(x, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--songs', default='fanwut,monitoring,shiki')
    ap.add_argument('--out', default=os.path.join(HERE, '_measure_handgap.json'))
    args = ap.parse_args()

    import transcriber_app as ta
    keys = [k.strip() for k in args.songs.split(',') if k.strip()]
    all_res = {}
    for key in keys:
        all_res[key] = {}
        for arm in ('OLD', 'NEW'):
            print('\n===== %s / %s =====' % (key, arm), flush=True)
            r = run_arm(ta, key, arm)
            all_res[key][arm] = r
            if 'error' in r:
                print('  错误：%s' % r['error'], flush=True)
                if r.get('tb'):
                    print(r['tb'], flush=True)
            else:
                print('  用时 %ss  左手 %d 音  右手 %d 音' %
                      (r['seconds'], r['n_left'], r['n_right']), flush=True)
                print('  R1 右手最低音−左手最高音 = %s 半音（<12 的格子 %d）'
                      % (r['gap_min'], r['gap_viol']), flush=True)
                print('  R2 伴奏密度 无人声段 %s /s  有人声段 %s /s  （无人声 %ss，有人声 %ss）'
                      % (r['accomp_gap_dens'], r['accomp_sung_dens'],
                         r['gap_sec'], r['sung_sec']), flush=True)
                print('  R2 最终左手密度 无人声段 %s /s  有人声段 %s /s'
                      % (r['left_gap_dens'], r['left_sung_dens']), flush=True)
                print('  other 单独识别并入 %d 个音（伴奏轨原 %s 音）'
                      % (r['extra_note'], r['other_note']), flush=True)

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(all_res, f, ensure_ascii=False, indent=2,
                  default=lambda o: int(o))
    print('\n结果已写入 %s' % args.out, flush=True)
    # ---- 汇总表 ----
    print('\n' + '=' * 78)
    print('%-12s %-4s %-9s %-9s %-11s %-11s %s' %
          ('歌曲', '臂', 'gap最小', '<12格子', '无人声密度', '有人声密度', 'other并入'))
    for key in keys:
        for arm in ('OLD', 'NEW'):
            r = all_res[key][arm]
            if 'error' in r:
                print('%-12s %-4s  错误: %s' % (key, arm, r['error'][:40]))
                continue
            print('%-12s %-4s %-9s %-9s %-11s %-11s %s' %
                  (key, arm, r['gap_min'], r['gap_viol'],
                   r['accomp_gap_dens'], r['accomp_sung_dens'], r['extra_note']))


if __name__ == '__main__':
    main()
