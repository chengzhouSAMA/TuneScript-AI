# -*- coding: utf-8 -*-
"""网易云扫码登录联调：直接从服务端看每一步的原始返回。

    python lang_dev/_probe_qr_login.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import netease_login as NL


def show(tag, obj):
    print('--- %s ---' % tag)
    if isinstance(obj, dict):
        keep = {}
        for k, v in obj.items():
            if k in ('cookie',):
                keep[k] = '<%d 字符，不打印>' % len(str(v))
            else:
                keep[k] = v
        print('   ', json.dumps(keep, ensure_ascii=False)[:600])
    else:
        print('   ', obj)


print('=== 端点常量 ===')
print('   unikey  :', NL.UNIKEY_API, '->', NL._eapi_url(NL.UNIKEY_API))
print('   qrlogin :', NL.QRLOGIN_API, '->', NL._eapi_url(NL.QRLOGIN_API))

print('\n=== 1) 取 unikey（type=1）===')
j1 = NL._eapi_post(NL.UNIKEY_API, {'type': 1})
show('type=1 的原始返回', j1)

print('\n=== 2) 对照：type=3（有些端用这个）===')
j3 = NL._eapi_post(NL.UNIKEY_API, {'type': 3})
show('type=3 的原始返回', j3)

key = (j1 or {}).get('unikey') or (j3 or {}).get('unikey')
if not key:
    print('\n!! 两种 type 都没拿到 unikey，后面没法测')
    sys.exit(1)

print('\n=== 3) 用 unikey 连续轮询 6 次（每次 2 秒）===')
print('   unikey =', key)
print('   扫码链接 =', NL.qr_url(key))
for i in range(6):
    j = NL._eapi_post(NL.QRLOGIN_API, {'key': key, 'type': 1})
    show('第 %d 次（t=%.1fs）' % (i + 1, i * 2.0), j)
    if j.get('code') == 800:
        print('   → 服务端说 800（过期）。这就是界面显示的「二维码已过期」。')
        break
    time.sleep(2)
