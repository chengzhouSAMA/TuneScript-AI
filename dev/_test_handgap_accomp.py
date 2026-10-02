# -*- coding: utf-8 -*-
"""R1（右手/左手差一个八度）与 R2（无人声段加强伴奏）的单元自测。

只用合成音符，不碰模型、不碰音频。跑：
    python lang_dev/_test_handgap_accomp.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import transcriber_app as T

OK = []
BAD = []


def check(name, cond, info=''):
    (OK if cond else BAD).append(name)
    print('  %s %-52s %s' % ('✓' if cond else '✗', name, info))


def gap(l, r):
    return T._hand_gap_min(l, r)[0]


print('=== 1) _hand_gap_min：基本测量 ===')
# 右手 64，左手 60 → 差 4
check('重叠时差 4 半音', gap([(0, 1, 60, 80)], [(0, 1, 64, 80)]) == 4)
# 不重叠 → 无可比时刻
check('不重叠返回 None', gap([(0, 1, 60, 80)], [(2, 3, 64, 80)]) is None)
check('空输入返回 None', gap([], [(0, 1, 64, 80)]) is None)
# 左手为空 → None
check('左手为空返回 None', gap([], []) is None)

print('\n=== 2) _enforce_octave_gap：拉到 ≥12 半音 ===')
right = [(0.0, 1.0, 64, 80)]
left = [(0.0, 1.0, 60, 80)]
out, rout, st = T._enforce_octave_gap(left, right)
check('原本只差 4', st['gap_before'] == 4, 'before=%s' % st['gap_before'])
check('拉开后 ≥12', st['gap_after'] is not None and st['gap_after'] >= 12,
      'after=%s' % st['gap_after'])
check('下移了 1 个八度', st['moved'] == 1 and st['octaves'] == 1)
check('音高 = 48（60−12）', out[0][2] == 48, 'p=%s' % out[0][2])
check('右手没被动', rout == right)
check('起止时间没变', out[0][0] == 0.0 and out[0][1] == 1.0)
check('力度没变', out[0][3] == 80)
check('音符数没变', len(out) == len(left))

# 已经够远 → 一个音都不动
out2, rout2, st2 = T._enforce_octave_gap([(0.0, 1.0, 40, 80)], right)
check('已差 24 半音 → 不动', st2['moved'] == 0 and out2[0][2] == 40)

# 右手在该时刻没音 → 无从比较，不动
out3, rout3, st3 = T._enforce_octave_gap([(5.0, 6.0, 60, 80)], right)
check('右手无音 → 不动', st3['moved'] == 0 and out3[0][2] == 60)

# 触底：右手 64、左手 60，但 floor 抬到 60 → 只能改抬右手（第二趟）
os.environ['TS_HAND_GAP_FLOOR'] = '60'
out4, rout4, st4 = T._enforce_octave_gap([(0.0, 1.0, 60, 80)],
                                         [(0.0, 1.0, 64, 80)])
del os.environ['TS_HAND_GAP_FLOOR']
check('触底时记为 blocked 且不动左手',
      st4['blocked'] == 1 and out4[0][2] == 60,
      'p=%s blocked=%s' % (out4[0][2], st4['blocked']))
check('第二趟把右手抬到 ≥ 左手+12',
      rout4[0][2] >= 60 + 12 and st4['raised'] == 1,
      'right=%s raised=%s' % (rout4[0][2], st4['raised']))
check('第二趟之后间隔达标', st4['gap_after'] >= 12, 'after=%s' % st4['gap_after'])
check('抬右手也保持数量/时间/力度',
      len(rout4) == 1 and rout4[0][0] == 0.0 and rout4[0][1] == 1.0
      and rout4[0][3] == 80)
# floor 正常时不该触发 blocked（走第一趟就够）
out4b, rout4b, st4b = T._enforce_octave_gap([(0.0, 1.0, 60, 80)],
                                            [(0.0, 1.0, 64, 80)])
check('floor 正常时不误报 blocked',
      st4b['blocked'] == 0 and out4b[0][2] == 48 and rout4b == [(0.0, 1.0, 64, 80)])

# ★ 回归：降了八度但「没降够」也必须算 blocked，否则第二趟不会启动
#   （第一版 bug：k>0 就不记 blocked → monitoring/jiabin 残留 −1 半音）
out4c, rout4c, st4c = T._enforce_octave_gap([(0.0, 1.0, 45, 80)],
                                            [(0.0, 1.0, 32, 80)])
check('降了但没降够 → 仍记 blocked', st4c['blocked'] == 1 and st4c['moved'] == 1,
      'blocked=%s moved=%s left=%s' % (st4c['blocked'], st4c['moved'], out4c[0][2]))
check('这种情形第二趟也把它救回来',
      st4c['gap_after'] >= 12 and st4c['raised'] == 1,
      'after=%s right=%s' % (st4c['gap_after'], rout4c[0][2]))

# 多音 + 多窗：右手换音区，左手要跟着分窗处理
right5 = [(0.0, 1.0, 64, 80), (1.0, 2.0, 76, 80)]
left5 = [(0.0, 1.0, 62, 80), (1.0, 2.0, 74, 80)]
out5, rout5, st5 = T._enforce_octave_gap(left5, right5)
check('分窗各自满足 ≥12', st5['gap_after'] >= 12, 'after=%s' % st5['gap_after'])
check('两个音都被下移', st5['moved'] == 2)

# 只改音高，不改数量/时间/力度
check('数量守恒', len(out5) == len(left5))
check('时间与力度守恒',
      all(o[0] == i[0] and o[1] == i[1] and o[3] == i[3]
          for o, i in zip(out5, left5)))

print('\n=== 3) 开关 TS_HAND_GAP=0 时完全不动 ===')
os.environ['TS_HAND_GAP'] = '0'
out6, rout6, st6 = T._enforce_octave_gap(left, right)
check('关闭后原样返回', out6 == left and rout6 == right and st6['on'] is False)
del os.environ['TS_HAND_GAP']

print('\n=== 4) _accomp_boost_params：默认值 ===')
p = T._accomp_boost_params()
check('默认开启', p['on'] is True)
check('pad 放宽到 1.6s（旧 0.7）', p['pad_len'] == 1.6)
check('抽稀间隔 ×0.5', p['ratio'] == 0.5)
check('other 单独识别默认开', p['other'] is True)

print('\n=== 5) _build_accomp：无人声段的孤立高音必须活下来 ===')
# 造一个「无人声段」：10~20s。里面放一个孤立高音 84（合成器主音的形状）
other = [(12.0, 12.4, 84, 90), (12.5, 12.9, 60, 70), (13.0, 13.4, 64, 70)]
in_gap = lambda t: 10.0 <= t < 20.0
gaps = [(10.0, 20.0)]

legacy = T._accomp_legacy(other, in_gap, gaps, min_gap=0.8, halluc=True)
check('旧路径把孤立高音 84 删掉',
      not any(n[2] == 84 for n in legacy),
      '旧路径音数=%d' % len(legacy))

os.environ['TS_ACCOMP_BOOST'] = '1'
boost = T._build_accomp(other, in_gap, gaps, min_gap=0.8, halluc=True)
check('R2 路径保住孤立高音 84',
      any(n[2] == 84 for n in boost),
      'R2 音数=%d' % len(boost))
check('R2 音数 ≥ 旧路径', len(boost) >= len(legacy))
check('R2 输出按时间排序',
      all(boost[i][0] <= boost[i + 1][0] for i in range(len(boost) - 1)))

print('\n=== 6) _build_accomp：有人声段必须逐字节不变 ===')
# 整个人声段里没有 gap → R2 与旧路径必须完全一致
sung = [(1.0, 1.4, 60, 70), (1.5, 1.9, 64, 70), (2.0, 2.4, 67, 70),
        (2.5, 2.9, 72, 70), (3.2, 3.6, 84, 90)]
no_gap = lambda t: False
a = T._accomp_legacy(sung, no_gap, [], min_gap=0.8, halluc=True)
b = T._build_accomp(sung, no_gap, [], min_gap=0.8, halluc=True)
check('无 gap 时 R2 == 旧路径', a == b,
      '旧=%d R2=%d' % (len(a), len(b)))

print('\n=== 7) _build_accomp：extra（other 单独识别）只并入无人声段 ===')
extra = [(12.2, 12.6, 79, 88),      # 落在无人声段 → 应并入
         (1.2, 1.6, 79, 88)]        # 落在有人声段 → 必须被丢弃
c = T._build_accomp([(12.0, 12.4, 60, 70)], in_gap, gaps,
                    min_gap=0.8, halluc=True, extra=extra)
pitches_in_gap = [n[2] for n in c if in_gap(n[0])]
check('无人声段的 extra 被并入', 79 in pitches_in_gap)
in_sung = T._build_accomp([(1.0, 1.4, 60, 70)], no_gap, [],
                          min_gap=0.8, halluc=True, extra=extra)
check('有人声段的 extra 被挡掉', not any(n[2] == 79 for n in in_sung))

print('\n=== 8) _dedupe_near：同音高同起音去重 ===')
dup = [(1.00, 1.4, 60, 70), (1.02, 1.5, 60, 72), (1.30, 1.7, 60, 70)]
d = T._dedupe_near(dup)
check('几乎同时的同音高只留一个', len(d) == 2, 'len=%d' % len(d))
check('相隔较远的保留', any(abs(n[0] - 1.30) < 1e-6 for n in d))

print('\n=== 9) TS_ACCOMP_BOOST=0 回到旧路径 ===')
os.environ['TS_ACCOMP_BOOST'] = '0'
z = T._build_accomp(other, in_gap, gaps, min_gap=0.8, halluc=True)
check('关闭 R2 后 == 旧路径', z == legacy)
del os.environ['TS_ACCOMP_BOOST']

print('\n=== 10) _fill_hand_gaps：拿分轨素材补某只手的空档 ===')
# 纪律：**只加不改** —— 已有音符一个不删、不动、不改时值。
base = [(0.0, 1.0, 60, 80), (3.0, 4.0, 62, 80)]
extra2 = [(1.2, 1.5, 48, 90), (1.7, 1.9, 50, 40)]

f1, s1 = T._fill_hand_gaps(list(base), list(extra2))
check('识别出 1 处空档', s1['holes'] == 1, 'holes=%s' % s1['holes'])
check('空档长度 2.0s', s1['hole_sec'] == 2.0, 'hole_sec=%s' % s1['hole_sec'])
check('2 个音补进去', s1['added'] == 2, 'added=%s' % s1['added'])
check('原有音符原样保留', all(n in f1 for n in base))
check('总数 = 原 2 + 新 2', len(f1) == 4, 'len=%d' % len(f1))
check('结果按起音排序', f1 == sorted(f1, key=lambda n: (n[0], n[2])))
check('力度不低于地板 55', [n[3] for n in f1 if n[0] == 1.7] == [55],
      'v=%s' % [n[3] for n in f1 if n[0] == 1.7])
check('够响的力度不被改动', [n[3] for n in f1 if n[0] == 1.2] == [90])

# 空输入：一律原样返回
z1, z1s = T._fill_hand_gaps([], list(extra2))
check('手为空 → 原样返回', z1 == [] and z1s['added'] == 0)
z2, z2s = T._fill_hand_gaps(list(base), [])
check('素材为空 → 原样返回', z2 == base and z2s['added'] == 0)

# 音域边界：pitch_hi 是**开区间**（60 属于右手，不能补给左手）
f2, s2 = T._fill_hand_gaps([(0.0, 1.0, 60, 80), (3.0, 4.0, 60, 80)],
                           [(1.2, 1.5, 59, 80), (1.7, 2.0, 60, 80)],
                           pitch_lo=-1, pitch_hi=60)
check('59 补进左手 / 60 不补', s2['added'] == 1, 'added=%s' % s2['added'])
f3, s3 = T._fill_hand_gaps([(0.0, 1.0, 60, 80), (3.0, 4.0, 60, 80)],
                           [(1.2, 1.5, 59, 80), (1.7, 2.0, 60, 80)],
                           pitch_lo=60, pitch_hi=128)
check('同一素材右手只收 60', s3['added'] == 1 and
      any(n[2] == 60 for n in f3), 'added=%s' % s3['added'])

# 空档外的素材一律不许进来（补音不能越界扩到已占用的时刻）
f4, s4 = T._fill_hand_gaps([(0.0, 1.0, 60, 80), (3.0, 4.0, 60, 80)],
                           [(0.2, 0.5, 48, 80), (2.2, 2.5, 48, 80), (5.0, 5.5, 48, 80)])
check('只有空档内那个被采用', s4['added'] == 1, 'added=%s' % s4['added'])
check('采用的是空档内的音', any(abs(n[0] - 2.2) < 1e-9 for n in f4))

# 太短的素材丢弃（min_len）
f5, s5 = T._fill_hand_gaps([(0.0, 1.0, 60, 80), (3.0, 4.0, 60, 80)],
                           [(1.2, 1.23, 48, 80), (1.6, 1.9, 48, 80)], min_len=0.06)
check('0.03s 的丢弃 / 0.3s 的保留', s5['added'] == 1, 'added=%s' % s5['added'])

# 密度上限：max_per_sec=3 → 同音高之间至少 1/3 秒。
# 三个音彼此相隔 0.1s，只有第一个能进。
f6, s6 = T._fill_hand_gaps([(0.0, 1.0, 60, 80), (3.0, 4.0, 60, 80)],
                           [(1.2, 1.5, 48, 80), (1.3, 1.6, 50, 80), (1.4, 1.7, 52, 80)],
                           max_per_sec=3.0)
check('3 个/秒上限只放行 1 个', s6['added'] == 1, 'added=%s' % s6['added'])
# 放宽到 10 个/秒 → 三个都进
# 注：间隔恰好等于 1/rate 时会被浮点误差挡掉（1.3−1.2 = 0.09999999999999987），
# 所以这里用 0.15s 间隔；生产里 rate=3.0（间隔 0.333s）碰不到这个边界。
f7, s7 = T._fill_hand_gaps([(0.0, 1.0, 60, 80), (3.0, 4.0, 60, 80)],
                           [(1.2, 1.5, 48, 80), (1.35, 1.65, 50, 80), (1.5, 1.8, 52, 80)],
                           max_per_sec=10.0)
check('放宽后 3 个都进', s7['added'] == 3, 'added=%s' % s7['added'])

# 同一处空档里同音高只触发一次（防碎/防抖）
f8, s8 = T._fill_hand_gaps([(0.0, 1.0, 60, 80), (5.0, 6.0, 60, 80)],
                           [(1.0, 1.4, 48, 80), (2.0, 2.4, 48, 80), (3.0, 3.4, 50, 80)],
                           max_per_sec=10.0)
check('同音高在一处空档里只留一次', s8['added'] == 2, 'added=%s' % s8['added'])

# 空档太短不补（min_hole）
f9, s9 = T._fill_hand_gaps([(0.0, 1.0, 60, 80), (1.3, 2.0, 60, 80)],
                           [(1.05, 1.25, 48, 80)], min_hole=0.5)
check('0.3s 的空档不动', s9['added'] == 0 and s9['holes'] == 0)

# 已知边界：**只手尾之后的素材永远补不进来**。空档是用该手自己的起止算出来的，
# t_end 就是这只手最后一个音的结束时刻，所以"尾部空档"长度恒为 0。
# 实测（inhuman4，素材总长 199.6s）：左手最后音 196.8s、右手 196.2s，
# 尾部空档 2.9s / 3.5s —— 只是正常收尾，没有补的必要，故保持现状。
f10, s10 = T._fill_hand_gaps([(0.0, 0.5, 60, 80)],
                             [(i * 0.5, i * 0.5 + 0.4, 48 + (i % 5), 80) for i in range(10)])
check('手尾之后的素材不补（尾部空档恒为 0）', s10['added'] == 0 and s10['holes'] == 0)
# 内部空档照常补
f11, s11 = T._fill_hand_gaps([(0.0, 0.5, 60, 80), (4.0, 4.5, 60, 80)],
                             [(i * 0.5, i * 0.5 + 0.4, 48 + (i % 5), 80) for i in range(10)])
check('内部空档正常补', s11['added'] >= 1, 'added=%s' % s11['added'])
check('补进来的都在空档 (0.5, 4.0) 内',
      all(0.5 <= n[0] < 4.0 for n in f11 if n[2] != 60))

print('\n=== 11) ⚠️ 往 t11 候选池里加料，结果**不是**超集 ===')
# 实测（shiki 冻结分轨，gf0 vs gf1）：左手逐字节不变，右手却有 15 个 gf0 的音
# 在 gf1 里没了、另多了 22 个。原因不是"删音"，而是 t11 的接受是**竞争性**的：
# 规则 B 先按「每 0.10s 取最高音」把候选压成单音线，再按 instr_rate 限速接受。
# 同一组里塞进一个更高的 other 轨音，就会把原来那个低音代表**顶掉**——
# 于是产物里看到"少一个 61~67 的音、多一个 65~86 的音"。
# 被顶掉的正是"中低音区升八度搬上来的混音音"，顶上来的才是 other 轨真正的高音。
_mixA = [(1.00, 1.30, 52, 80)]                       # 52 → 升八度成 64
_mixB = _mixA + [(1.05, 1.35, 81, 80)]               # 同组里多一个 other 轨的高音
_rA, _sA = T._fill_right_hand([], list(_mixA), [])
_rB, _sB = T._fill_right_hand([], list(_mixB), [])
check('单独混音时取升八度后的 64', [n[2] for n in _rA] == [64],
      'p=%s' % [n[2] for n in _rA])
check('加一个高音后改取 81（同组最高）', [n[2] for n in _rB] == [81],
      'p=%s' % [n[2] for n in _rB])
check('所以产物不是超集（旧音被顶掉，不是被删）',
      not set(n[2] for n in _rA) <= set(n[2] for n in _rB))
check('两边都只产出 1 个音（限速与取最高音没变）',
      len(_rA) == 1 and len(_rB) == 1)

print('\n=== 12) 轨优先级：_accomp_weights（鼓不识别 / 贝斯最后） ===')
_p = T.ACCOMP_PRIORITY
check('默认优先级是 piano>guitar>other>bass',
      _p == ('piano', 'guitar', 'other', 'bass'), 'got=%s' % (_p,))
check('**鼓点不在优先级表里**（鼓永不识别）', 'drums' not in _p)

_w = T._accomp_weights(_p)
check('优先档权重 1.0', _w['piano'] == 1.0 and _w['guitar'] == 1.0
      and _w['other'] == 1.0, 'w=%s' % _w)
check('最后一档（贝斯）被压到 ACCOMP_LAST_W', _w['bass'] == T.ACCOMP_LAST_W,
      'bass=%.2f / LAST_W=%.2f' % (_w['bass'], T.ACCOMP_LAST_W))
check('贝斯权重严格低于优先档（"优先性最后"）', _w['bass'] < 1.0)
check('没有 drums 的权重', 'drums' not in _w)
check('两档时最后一档才是低档',
      T._accomp_weights(('piano', 'guitar'))['guitar'] == T.ACCOMP_LAST_W)
check('单档时它就是最后一档',
      T._accomp_weights(('piano',))['piano'] == T.ACCOMP_LAST_W)
check('空表不炸', T._accomp_weights(()) == {})

# 显式权重覆盖
os.environ['TS_ACCOMP_WEIGHTS'] = 'piano=1,guitar=0.5,bass=0.9'
import importlib as _il
_il.reload(T)
_w2 = T._accomp_weights(T.ACCOMP_PRIORITY)
check('TS_ACCOMP_WEIGHTS 逐档覆盖生效',
      _w2['guitar'] == 0.5 and _w2['bass'] == 0.9 and _w2['piano'] == 1.0,
      'w=%s' % _w2)
del os.environ['TS_ACCOMP_WEIGHTS']

print('\n=== 13) 旧开关名 TS_ACCOMP_STEMS / 旧常量别名仍然认 ===')
check('ACCOMP_STEMS 是 ACCOMP_PRIORITY 的别名（老脚本照常能覆盖）',
      tuple(T.ACCOMP_STEMS) == tuple(T.ACCOMP_PRIORITY))
os.environ['TS_ACCOMP_STEMS'] = 'piano,guitar'
_il.reload(T)
check('设 TS_ACCOMP_STEMS 仍能改优先级顺序',
      T.ACCOMP_PRIORITY == ('piano', 'guitar'), 'got=%s' % (T.ACCOMP_PRIORITY,))
del os.environ['TS_ACCOMP_STEMS']
os.environ['TS_ACCOMP_PRIORITY'] = 'other,piano,bass'
_il.reload(T)
check('新名 TS_ACCOMP_PRIORITY 优先于旧名',
      T.ACCOMP_PRIORITY == ('other', 'piano', 'bass'), 'got=%s' % (T.ACCOMP_PRIORITY,))
del os.environ['TS_ACCOMP_PRIORITY']
_il.reload(T)

print('\n=== 14) 加权合并实测：贝斯被压到 0.4、鼓点不进来、电平锚点不变 ===')
# 用两条单频正弦当"轨"，合成后量各分量的幅度，直接证明三点：
#   · 贝斯按 ACCOMP_LAST_W 衰减（不是等权相加）；
#   · 鼓点即使给了文件也不会被并进来（鼓点不识别）；
#   · 合并后整体 RMS 仍锚定在"最响的那条参与轨"上（电平纪律没被权重破坏）。
import tempfile
import numpy as np
import soundfile as sf

_SR, _SEC = 22050, 2.0
_t = np.arange(int(_SR * _SEC)) / _SR
_TMPD = tempfile.mkdtemp(prefix='ts_prio_')


def _tone(name, freq, amp):
    p = os.path.join(_TMPD, name)
    sf.write(p, (amp * np.sin(2 * np.pi * freq * _t)).astype('float32'), _SR,
             subtype='PCM_16')
    return p


def _amp(path, freq):
    y, sr = sf.read(path, dtype='float32', always_2d=True)
    y = y[:, 0]
    n = len(y)
    w = np.hanning(n)
    tt = np.arange(n) / sr
    return float(2.0 * np.abs(np.sum(y * w * np.exp(-2j * np.pi * freq * tt)))
                 / np.sum(w))


def _rms(path):
    y, _sr = sf.read(path, dtype='float32', always_2d=True)
    return float(np.sqrt(np.mean(y ** 2)))


_p = _tone('song_piano.wav', 440.0, 0.50)
_b = _tone('song_bass.wav', 110.0, 0.50)
_d = _tone('song_drums.wav', 60.0, 0.50)
_stems = {'piano': _p, 'bass': _b, 'drums': _d}

_keep_prio, _keep_w = T.ACCOMP_PRIORITY, T.ACCOMP_LAST_W
T.ACCOMP_PRIORITY = ('piano', 'bass')
T.ACCOMP_LAST_W = 0.40
_msgs = []
_lab, _out = T._merge_accomp_stems(_stems, _msgs.append, out_dir=_TMPD)
check('鼓点没参与合并（标签只有 piano+bass）', _lab == 'piano+bass', 'label=%s' % _lab)
check('鼓点频率（60Hz）在合并结果里几乎为零', _amp(_out, 60.0) < 0.01,
      'amp=%.4f' % _amp(_out, 60.0))
_a440, _a110 = _amp(_out, 440.0), _amp(_out, 110.0)
# 合成前 440 与 110 等幅，贝斯权重 0.4 ⇒ 输出幅度比应为 1/0.4 = 2.5
check('贝斯被压到 0.4（440/110 幅度比 ≈ 2.5）', abs(_a440 / _a110 - 2.5) < 0.08,
      'ratio=%.3f（440=%.3f / 110=%.3f）' % (_a440 / _a110, _a440, _a110))
check('电平锚点仍是最响参与轨（合并 RMS ≈ piano RMS）',
      abs(_rms(_out) / _rms(_p) - 1.0) < 0.02,
      'merged=%.4f / anchor=%.4f' % (_rms(_out), _rms(_p)))
check('日志里报出了优先级与权重',
      any(('优先级' in m and '权重' in m) for m in _msgs), _msgs[-1][:70])

# 电平锚点必须**与低优先档在不在场无关**。
# 造一条比优先档更响的贝斯：旧写法会把锚点换成贝斯、整体电平跟着变，
# 那样"内容变了"和"电平变了"就混成一个自变量了。
_loud_bass = _tone('song_bass_loud.wav', 110.0, 1.60)
_lab2, _out2 = T._merge_accomp_stems({'piano': _p, 'bass': _loud_bass},
                                     _msgs.append, out_dir=_TMPD)
check('贝斯比钢琴还响时，锚点仍取优先档 piano（不被低优先档顶替）',
      any(('最强轨 piano' in m) for m in _msgs), _msgs[-1][:70])
check('因此合并电平仍 ≈ piano 的 RMS（低优先档不改变整体电平）',
      abs(_rms(_out2) / _rms(_p) - 1.0) < 0.02,
      'merged=%.4f / piano=%.4f' % (_rms(_out2), _rms(_p)))
check('但贝斯内容确实进来了（110Hz 存在）', _amp(_out2, 110.0) > 0.05,
      'amp110=%.3f' % _amp(_out2, 110.0))

# 权重 0 = 回到 2026-09-19 的"贝斯不参与"口径
T.ACCOMP_LAST_W = 0.0
_lab0, _out0 = T._merge_accomp_stems(_stems, lambda m: None, out_dir=_TMPD)
check('LAST_W=0 时贝斯按不参与处理', _lab0 == 'piano', 'label=%s' % _lab0)
check('LAST_W=0 时直接返回那条轨的原始文件', os.path.abspath(_out0) == os.path.abspath(_p))

T.ACCOMP_PRIORITY, T.ACCOMP_LAST_W = _keep_prio, _keep_w

print('\n=== 15) _collapse_octave_doubling：左手去"本音+高八度"自我加倍 ===')


def _pitches(hand):
    return [n[2] for n in hand]


# 2026-09-30 琵琶曲取证：出厂路径 _simple_piano 的左手是
# fix_hand(max_notes=4, mode="mix")，而 max_span=14 刚好容得下一个八度，
# 于是"贝斯本音 + 它的高八度"两个音都被保留（实测 119 个 = 左手 20.0%），
# 谱面上表现为同一条线被写成两条互相错开的声部 = 「左右手抢节奏」。
check('空输入返回空', T._collapse_octave_doubling([]) == [])
_one = [(0.0, 0.5, 48, 80)]
check('单音原样返回', T._collapse_octave_doubling(_one) == _one)

_both = [(0.0, 0.5, 48, 80), (0.0, 0.5, 60, 70)]
_out = T._collapse_octave_doubling(_both)
check('本音+高八度同窗 → 只留本音', _pitches(_out) == [48], 'p=%s' % _pitches(_out))
check('保留的是那个音的时间戳', _out[0][0] == 0.0 and _out[0][1] == 0.5)
check('力度原样不动（只删音，不改属性）', _out[0][3] == 80)
check('差 24 半音同样删',
      _pitches(T._collapse_octave_doubling([(0.0, .5, 36, 80), (0.0, .5, 60, 60)])) == [36])
check('错开 10ms 仍算同窗 → 删',
      _pitches(T._collapse_octave_doubling([(0.0, .5, 48, 80), (0.010, .5, 60, 70)])) == [48])

# 负控：该保留的一个都不能动
_chord = [(0.0, 0.5, 48, 80), (0.0, 0.5, 52, 78), (0.0, 0.5, 55, 76)]
check('C3+E3+G3 三和弦原样保留',
      _pitches(T._collapse_octave_doubling(_chord)) == [48, 52, 55],
      'p=%s' % _pitches(T._collapse_octave_doubling(_chord)))
check('C3+B3(差 11) 保留',
      len(T._collapse_octave_doubling([(0.0, .5, 48, 80), (0.0, .5, 59, 70)])) == 2)
check('相隔 200ms 的八度不动（不是同窗）',
      _pitches(T._collapse_octave_doubling([(0.0, .5, 48, 80), (0.20, .5, 60, 70)])) == [48, 60])
check('同音高重复不在本函数职责内（原样返回）',
      len(T._collapse_octave_doubling([(0.0, .5, 48, 80), (0.010, .5, 48, 70)])) == 2)

# 窗口口径与 fix_hand 一致：以每个音起音为锚，只向后看 80ms
_sus = [(0.0, 2.0, 48, 80), (0.050, 1.5, 60, 70)]
check('长音铺底、八度音在 80ms 内起音 → 删',
      _pitches(T._collapse_octave_doubling(_sus)) == [48],
      'p=%s' % _pitches(T._collapse_octave_doubling(_sus)))
check('前音已结束时不算同窗',
      _pitches(T._collapse_octave_doubling([(0.0, .3, 48, 80), (1.0, 1.5, 60, 70)])) == [48, 60])
# ⚠️ 刻意不做"向后看"：低音与高八度相隔 >80ms 的交替（如 G1→G2 的舞曲贝斯）
#    是真实演奏型态，不是识别加倍，删了就是把音乐改坏。
_alt = [(0.0, 0.30, 31, 80), (0.093, 0.30, 43, 80)]
check('相隔 93ms 的根音/八度交替原样保留（真实贝斯型态）',
      _pitches(T._collapse_octave_doubling(_alt)) == [31, 43],
      'p=%s' % _pitches(T._collapse_octave_doubling(_alt)))

_many = [
    (0.0, 0.4, 36, 80), (0.0, 0.4, 48, 70), (0.0, 0.4, 60, 60),   # 三个八度叠一起
    (0.5, 0.9, 41, 80), (0.5, 0.9, 43, 75),                        # F2+G2，非八度 → 都留
    (1.0, 1.4, 43, 80), (1.0, 1.4, 55, 70),                        # G2+G3，差 12 → 删上
]
_pm = _pitches(T._collapse_octave_doubling(_many))
check('三个八度叠加只留最低的 36', 36 in _pm and 48 not in _pm and 60 not in _pm, 'p=%s' % _pm)
check('F2+G2 这种非八度和声保留', 41 in _pm and 43 in _pm)
check('G2+G3 删掉 55', 43 in _pm and 55 not in _pm, 'p=%s' % _pm)
check('音符总数 = 7 − 3', len(_pm) == 4, 'n=%d' % len(_pm))

# 接入点：真的走 _simple_piano，确认左手少了音、右手一个不变
_notes = []
for _k in range(10):
    _t = _k * 0.25
    _notes.append((_t, _t + 0.2, 43, 80))        # 贝斯本音 G2
    _notes.append((_t, _t + 0.2, 55, 70))        # 它的高八度 G3
    _notes.append((_t, _t + 0.2, 74, 90))        # 右手旋律 D5
_midi, _lu, _ru = T._simple_piano(_notes)
check('接入后左手已无 ≥60 的音', all(p < 60 for _, _, p, _ in _lu))
check('接入后左手不再是"本音+高八度"两条线',
      len({round(s, 2) for s, _, _, _ in _lu}) == len(_lu), 'L=%d 音' % len(_lu))
check('右手音符数不受影响', len(_ru) == 10, 'R=%d 音' % len(_ru))
check('右手音高不动', {p for _, _, p, _ in _ru} == {74})

# ---------------------------------------------------------------------------
# 分轨后勾选识别音轨（2026-10-01 新增）
# 设计：勾选 = **过滤传给识别的那份 stems 字典**，合并逻辑一行不动
# （_merge_accomp_stems 本来就按 ACCOMP_PRIORITY 逐档 stems.get(k)，缺轨自动跳过）。
# ---------------------------------------------------------------------------
_all_stems = {'drums': 'd.wav', 'bass': 'b.wav', 'other': 'o.wav',
              'vocals': 'v.wav', 'piano': 'p.wav', 'guitar': 'g.wav'}
check('PICKABLE_STEMS 就是 ACCOMP_PRIORITY（顺序即优先级，单一来源）',
      T.PICKABLE_STEMS == tuple(T.ACCOMP_PRIORITY), '%s' % (T.PICKABLE_STEMS,))
check('人声与鼓都不可勾选（R1 要人声进右手 / R3 鼓点不识别）',
      'vocals' not in T.PICKABLE_STEMS and 'drums' not in T.PICKABLE_STEMS)

check('keep=None → 原样返回同一个对象（cli 与旧 GUI 行为不变）',
      T.filter_stems(_all_stems, None) is _all_stems)
_pick1 = T.filter_stems(_all_stems, ('piano',))
check('只勾钢琴 → 只剩人声+钢琴',
      sorted(_pick1) == ['piano', 'vocals'], '%s' % sorted(_pick1))
check('勾选后鼓一定不在结果里（R3：鼓点不识别）', 'drums' not in _pick1)
check('勾选不修改入参字典（长度仍是 6）', len(_all_stems) == 6, '%d' % len(_all_stems))
_pick_empty = T.filter_stems(_all_stems, [])
check('一条伴奏都没勾时人声仍在（人声由管线强制保留）',
      sorted(_pick_empty) == ['vocals'], '%s' % sorted(_pick_empty))

_noop_logs = []
check('_apply_stem_pick(picker=None) 是空操作（不传 picker = 不勾选）',
      T._apply_stem_pick(_all_stems, None, _noop_logs.append) is _all_stems
      and not _noop_logs)
_pick_logs = []
_picked = T._apply_stem_pick(_all_stems, lambda s: ('guitar', 'bass'),
                             _pick_logs.append)
check('勾选后返回过滤字典', sorted(_picked) == ['bass', 'guitar', 'vocals'],
      '%s' % sorted(_picked))
check('勾选结果写进日志（保留项与跳过项都在）',
      any('人工勾选识别音轨' in m and 'bass' in m and 'piano' in m for m in _pick_logs),
      '%s' % _pick_logs)
_cancelled = False
try:
    T._apply_stem_pick(_all_stems, lambda s: None, _pick_logs.append)
except T.PipelineCancelled:
    _cancelled = True
check('对话框取消 → 抛 PipelineCancelled（不是静默按原样继续）', _cancelled)
check('PipelineCancelled 是异常类（能被专门分支接住）',
      issubclass(T.PipelineCancelled, Exception))

# ---------------------------------------------------------------------------
# 谱面置信度着色（2026-10-01 新增）—— 通道是 predict() 的 note_events amplitude，
# **不是** velocity（velocity 下游被 _soft_velocity 按左右手重映射成力度层次了）
# ---------------------------------------------------------------------------
import inspect as _inspect  # 本块专用；ruff 没选 E402，不需要抑制注释

T._NOTE_CONF.clear()
T.set_note_conf_color(True)
check('着色默认开（无覆盖时 conf_color_enabled 为真）', T.conf_color_enabled() is True)
T.set_note_conf_color(None)
os.environ.pop("TS_NOTE_CONF_COLOR", None)
check('TS_NOTE_CONF_COLOR 未设时默认开', T.conf_color_enabled() is True)
os.environ["TS_NOTE_CONF_COLOR"] = "0"
check('TS_NOTE_CONF_COLOR=0 时关闭', T.conf_color_enabled() is False)
os.environ.pop("TS_NOTE_CONF_COLOR", None)

T._conf_record(1.0, 60, 0.20)      # 低
T._conf_record(2.0, 64, 0.45)      # 中
T._conf_record(3.0, 67, 0.90)      # 高
check('低置信 → 红', T.note_conf_color(1.0, 60, 0.6, 0.35) == T.CONF_COLORS["low"])
check('中置信 → 橙', T.note_conf_color(2.0, 64, 0.6, 0.35) == T.CONF_COLORS["mid"])
check('高置信 → 不上色（保持黑，谱面照旧干净）',
      T.note_conf_color(3.0, 67, 0.6, 0.35) is None)
check('查不到来源 → 蓝（补音/合成/被改写，最可疑的一类）',
      T.note_conf_color(9.9, 40, 0.6, 0.35) == T.CONF_COLORS["none"])
check('±一个八度也能查回（R1 会把音搬八度）',
      T.note_conf_color(1.0, 72, 0.6, 0.35) == T.CONF_COLORS["low"])
check('同音高起音差 40ms 内算同一个音（碎音合并会微调起音）',
      T.note_conf_color(1.04, 60, 0.6, 0.35) == T.CONF_COLORS["low"])
check('起音差超出 60ms 不算（免得张冠李戴）',
      T.note_conf_color(1.5, 60, 0.6, 0.35) == T.CONF_COLORS["none"])
T._conf_record(5.0, 60, 1.7)
check('越界 amplitude 不入表（防脏数据）', T._conf_lookup(5.0, 60) is None)

T.set_note_conf_color(False)
check('关掉时 _score_colors 返回 None（一条颜色都不写）',
      T._score_colors([("G", 2, [(0.0, 1.0, 60, 80)])], lambda t: int(t * 8)) is None)
T.set_note_conf_color(True)
_cols = T._score_colors([("G", 2, [(1.0, 3.0, 60, 80)])], lambda t: int(t * 8))
check('_score_colors 覆盖整个时值的槽位（跨小节续段不会掉成蓝）',
      _cols is not None and all((s, 60) in _cols for s in (8, 10, 23))
      and (24, 60) not in _cols, '%s' % (sorted(_cols) if _cols else None))

# ---------------------------------------------------------------------------
# 整曲语种判断（2026-10-01 新增）：默认开，但只报告、不参与取舍
# ---------------------------------------------------------------------------
os.environ["TS_LANG_JUDGE"] = "0"
_judge_off = T.judge_language("不存在.wav", None, None)
check('TS_LANG_JUDGE=0 → 不判、不加载模型，reason 说明开关',
      _judge_off.get("ok") is False and _judge_off.get("reason") == "TS_LANG_JUDGE=0",
      str(_judge_off))
os.environ.pop("TS_LANG_JUDGE", None)
_judge_bad = T.judge_language("不存在.wav", None, None)
check('没有可判音频时不炸，返回 ok=False',
      _judge_bad.get("ok") is False and "没有可判" in (_judge_bad.get("reason") or ""),
      str(_judge_bad))
check('run_pipeline 有 lang_seg 形参（GUI 高级模式用它显式开分段）',
      "lang_seg" in _inspect.signature(T.run_pipeline).parameters)

# ---------------------------------------------------------------------------
# P1 记谱网格（2026-10-02）：divisions 参数化 —— 三连音终于在数学上表示得了
# （DIV=4 时 1/3 个四分 = 1.333 个单位，写不出来；DIV=12 时 16分=3、八分三连=4）
# ---------------------------------------------------------------------------
os.environ.pop("TS_DIVISIONS", None)
check('默认 divisions/四分 = 4（16 分网格 = 出厂行为）', T.score_grid_div() == 4)
os.environ["TS_DIVISIONS"] = "12"
check('TS_DIVISIONS=12 → 12（三连音与二分网格同时可写）', T.score_grid_div() == 12)
for _bad in ("abc", "0", "999", "-3"):
    os.environ["TS_DIVISIONS"] = _bad
    check('非法 TS_DIVISIONS=%r 回落 4（不炸）' % _bad, T.score_grid_div() == 4)
os.environ.pop("TS_DIVISIONS", None)
check('DIV=4 下没有任何时值会被判成三连音（⇒ 出厂产物一个 time-modification 都不输出）',
      not any(T.is_triplet_dur(_d, 4) for _d in range(1, 65)))
check('DIV=12 下干净三连音值恰为 {2,4,8,16}',
      [_d for _d in range(1, 25) if T.is_triplet_dur(_d, 12)] == [2, 4, 8, 16])
check('二分网格值不算三连音（6=八分、12=四分）',
      not T.is_triplet_dur(6, 12) and not T.is_triplet_dur(12, 12))
check('div 不是 3 的倍数时一律不判三连音',
      not any(T.is_triplet_dur(_d, 8) for _d in range(1, 33)))
import tempfile as _tf   # 本块专用
_keep = os.environ.get("TS_DIVISIONS")
os.environ["TS_DIVISIONS"] = "12"
_p1x = os.path.join(_tf.mkdtemp(prefix="p1chk_"), "t.xml")
T.write_grand_staff_xml([(0.5, 0.75, 48, 80)],
                        [(0.0, 1 / 6.0, 60, 80), (1 / 6.0, 2 / 6.0, 62, 80),
                         (2 / 6.0, 0.5, 64, 80)], _p1x, bpm=120.0)
_p1t = open(_p1x, encoding="utf-8").read()
check('DIV=12 真产物：divisions=12，且三个八分三连音各带一个 time-modification',
      "<divisions>12</divisions>" in _p1t and _p1t.count("<time-modification>") == 3,
      'tm=%d' % _p1t.count("<time-modification>"))
if _keep is None:
    os.environ.pop("TS_DIVISIONS", None)
else:
    os.environ["TS_DIVISIONS"] = _keep

# ---------------------------------------------------------------------------
# E1 + E2 演奏法检测（2026-10-02）—— 统一开关 TS_ARTIC，默认关
# ---------------------------------------------------------------------------
os.environ.pop("TS_ARTIC", None)
check('TS_ARTIC 默认关（不写任何演奏法记号，出厂谱面不变）', T.artic_enabled() is False)
os.environ["TS_ARTIC"] = "1"
check('TS_ARTIC=1 时开启', T.artic_enabled() is True)
os.environ.pop("TS_ARTIC", None)
T._BEND.clear()
T._bend_record_events([(0.5, 1.0, 60, 0.90, -0.40), (0.5, 1.0, 60, 0.10, 0.05)])
check('E1：同一 (起音,音高) 只留 |pitch_bend| 最大者', T.bend_max(0.5, 60) == 0.40,
      'bend=%s' % T.bend_max(0.5, 60))
check('E1：没记录的音返回 None（不是 0）', T.bend_max(9.9, 99) is None)
_art = T.detect_articulations([(0.0, 0.10, 60, 80), (0.5, 1.48, 62, 80),
                               (1.5, 1.60, 64, 80)])
check('E2：短促音（时长/间隔 0.20）→ 断奏', _art.get((0.0, 60)) == 'staccato', str(_art))
check('E2：几乎贴着下一音（0.98）→ 连奏', _art.get((0.5, 62)) == 'tenuto')
check('E2：最后一个起音不判（没有下一个音可比）', (1.5, 64) not in _art)
check('E2：间隔太近不判（同和弦/密集走句不该标成一片断奏）',
      T.detect_articulations([(0.0, 0.05, 60, 80), (0.10, 0.20, 62, 80)]) == {})
check('E2：中间地带（0.60~0.92）不标 —— precision 优先，宁可少标',
      T.detect_articulations([(0.0, 0.40, 60, 80), (0.5, 1.0, 62, 80)]).get((0.0, 60)) is None)

# ---------------------------------------------------------------------------
# E4 踏板记号（2026-10-02）：把 `_build_hand` 已经算出的 CC64 区间写成谱面记号
# ---------------------------------------------------------------------------
os.environ.pop("TS_ARTIC", None)
check('踏板默认关（跟随总开关 TS_ARTIC）', T.pedal_enabled() is False)
os.environ["TS_ARTIC"] = "1"
check('TS_ARTIC=1 时踏板开', T.pedal_enabled() is True)
os.environ["TS_ARTIC_PEDAL"] = "0"
check('TS_ARTIC_PEDAL=0 可单独关掉踏板（其余记号不受影响）',
      T.pedal_enabled() is False and T.artic_enabled() is True)
os.environ.pop("TS_ARTIC_PEDAL", None)
T._PEDAL[:] = [(0.0, 1.0), (2.0, 3.0)]
_pm = T._pedal_marks([(0.0, 1.0, 48, 80), (1.0, 2.0, 50, 80), (2.05, 3.0, 52, 80)],
                     lambda t: int(round(t * 8)))
check('E4：区间起/止挂到最近的音起音上（0.05s 内也算最近）',
      _pm == {(0, 48): 'pedal_start', (8, 50): 'pedal_stop', (16, 52): 'pedal_start'},
      str(_pm))
check('E4：挂不上（差 >0.30s）就不标 —— 宁可少标',
      T._pedal_marks([(9.0, 9.5, 60, 80)], lambda t: int(round(t * 8))) == {})
check('E4：用 <direction> 形式而不是 <notations><pedal>'
      '（2026-10-02 实测 MuseScore 只认前者）',
      '<direction-type><pedal type="start" line="yes"/>' in
      "\n".join(T._xml_pedal_dir('pedal_start', 1)))
T._PEDAL[:] = []
os.environ.pop("TS_ARTIC", None)

print('\n=== 汇总：%d 项，%d 通过，%d 失败 ===' % (len(OK) + len(BAD), len(OK), len(BAD)))
if BAD:
    for b in BAD:
        print('  失败：%s' % b)
sys.exit(1 if BAD else 0)
