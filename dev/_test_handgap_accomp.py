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

print('\n=== 汇总：%d 项，%d 通过，%d 失败 ===' % (len(OK) + len(BAD), len(OK), len(BAD)))
if BAD:
    for b in BAD:
        print('  失败：%s' % b)
sys.exit(1 if BAD else 0)
