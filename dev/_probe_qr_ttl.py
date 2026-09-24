# -*- coding: utf-8 -*-
"""长轮询：看 unikey 到底多久才真的过期（默认最多 6 分钟）。

不动手机就扫，能测出"服务端自己说 800"的时间窗 —— 用来区分
「一开就说过期」（我们的 bug）和「放太久真过期」（服务端的正常行为）。

    python lang_dev/_probe_qr_ttl.py            # 最长 6 分钟
    python lang_dev/_probe_qr_ttl.py --minutes 3
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netease_login as NL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--minutes', type=float, default=6.0)
    ap.add_argument('--interval', type=float, default=2.0)
    args = ap.parse_args()

    j = NL._eapi_post(NL.UNIKEY_API, {'type': 1})
    key = j.get('unikey')
    print('unikey =', key, flush=True)
    if not key:
        print('!! 没拿到 unikey：', json.dumps(j, ensure_ascii=False)[:300])
        return 1
    print('扫码链接 =', NL.qr_url(key), flush=True)

    t0 = time.time()
    seen = {}
    first_expire = None
    while time.time() - t0 < args.minutes * 60:
        r = NL._eapi_post(NL.QRLOGIN_API, {'key': key, 'type': 1})
        code = r.get('code')
        seen[code] = seen.get(code, 0) + 1
        t = time.time() - t0
        if code != 801:
            print('  [%6.1fs] code=%s  %s' % (t, code,
                                              json.dumps(r, ensure_ascii=False)[:200]),
                  flush=True)
        if code == 800 and first_expire is None:
            first_expire = t
            print('  → 首次收到 800，t=%.1fs' % t, flush=True)
            break
        if code == 803:
            print('  → 登录成功（有人扫了）', flush=True)
            break
        if int(t) % 30 == 0 and code == 801:
            print('  [%6.1fs] 仍在等待扫码(801)' % t, flush=True)
        time.sleep(args.interval)

    print('\n=== 统计 ===')
    print('  出现过的 code：', seen)
    print('  首次 800 的时间：', ('%.1fs' % first_expire) if first_expire else '本次没出现')
    return 0


if __name__ == '__main__':
    sys.exit(main())
