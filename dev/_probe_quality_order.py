# -*- coding: utf-8 -*-
"""音质降级调查 + 取直链顺序回归。

背景：用户是黑胶会员，却只下到 320kbps。「注意：服务端把音质降级了」这句提示
不是根因 —— 根因是 `resolve()` 的候选计划里，**匿名明文接口排在最前面**：
它哪怕只给 320k 也算"成功"，函数立刻 return，根本走不到能认出会员的 eapi 那一步。

    python lang_dev/_probe_quality_order.py            # 离线：只验证顺序
    python lang_dev/_probe_quality_order.py --live     # 再真跑一次匿名取直链
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netease as N

OB, OBAD = [], []


def check(name, cond, info=''):
    (OB if cond else OBAD).append(name)
    print('  %s %-52s %s' % ('✓' if cond else '✗', name, info))


def order_of(cookie):
    """拦下两个探针，看 resolve() 实际按什么顺序试。"""
    calls = []
    op, oe = N._probe_plain, N._probe_eapi

    def fake_plain(song_id, br, cookie=None):
        calls.append(('plain', br, bool(cookie)))
        return {'source': 'plain', 'code': 200, 'url': None, 'br': 0, 'size': 0,
                'fmt': '', 'level': None, 'trial': None}

    def fake_eapi(song_id, level, cookie=None):
        calls.append(('eapi', level, bool(cookie)))
        return {'source': 'eapi', 'code': 200, 'url': None, 'br': 0, 'size': 0,
                'fmt': '', 'level': None, 'trial': None}

    N._probe_plain, N._probe_eapi = fake_plain, fake_eapi
    try:
        N.resolve(12345, level='hires', cookie=cookie)
    finally:
        N._probe_plain, N._probe_eapi = op, oe
    return calls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live', action='store_true')
    args = ap.parse_args()

    print('=== 1) 有 cookie 时：先问能认出会员的 eapi ===')
    calls = order_of('MUSIC_U=FAKE; __csrf=FAKE;')
    print('    实际顺序：', calls)
    check('第一个探针是 eapi（带 level）', calls and calls[0][0] == 'eapi',
          str(calls[0] if calls else None))
    check('eapi 那一路带上了 cookie', calls and calls[0][2] is True)
    check('明文接口排在 eapi 之后', all(c[0] == 'eapi' for c in calls[:5]),
          str([c[0] for c in calls[:5]]))

    print('\n=== 2) 没 cookie 时：顺序不变（仍是匿名快速通道优先）===')
    calls2 = order_of('')
    print('    实际顺序：', [c[0] for c in calls2][:6])
    check('无 cookie 时第一个仍是 plain', calls2 and calls2[0][0] == 'plain')
    check('迟早也会试 eapi', any(c[0] == 'eapi' for c in calls2))

    print('\n=== 3) 明文探针必须把 cookie 发出去 ===')
    sent = {}
    orig_get = N.requests.get

    class _R(object):
        def json(self):
            return {'data': [{'code': 200, 'url': None, 'br': 0}]}

    def fake_get(url, **kw):
        sent.update(kw)
        return _R()

    N.requests.get = fake_get
    try:
        N._probe_plain(1, 320000, 'MUSIC_U=abc; __csrf=def')
        check('带 cookie 调用时 cookies 非空', bool(sent.get('cookies')),
              str(sent.get('cookies')))
        check('MUSIC_U 确实在里面', (sent.get('cookies') or {}).get('MUSIC_U') == 'abc')
        sent.clear()
        N._probe_plain(1, 320000, None)
        check('没 cookie 时不硬塞 cookies', not sent.get('cookies'))
    finally:
        N.requests.get = orig_get

    if args.live:
        print('\n=== 4) 真实联网：匿名取直链（回归，确认没把免费路径弄坏）===')
        cands = N.search('反乌托邦', limit=3)
        if not cands:
            print('    搜不到，跳过')
        else:
            sid = cands[0]['id']
            print('    取 %s（id=%s）' % (cands[0]['name'], sid))
            for lv in ('exhigh', 'hires'):
                res = N.resolve(sid, level=lv, cookie='')
                print('    level=%-8s ok=%s 实际=%-8s %5s kbps %-5s 顺序=%s'
                      % (lv, res.get('ok'), res.get('level'),
                         int((res.get('br') or 0) / 1000), res.get('fmt'),
                         res.get('order')))
                print('      %s' % res.get('reason'))

    print('\n=== 汇总：%d 项，%d 通过，%d 失败 ==='
          % (len(OB) + len(OBAD), len(OB), len(OBAD)))
    return 1 if OBAD else 0


if __name__ == '__main__':
    sys.exit(main())
