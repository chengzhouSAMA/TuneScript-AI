# -*- coding: utf-8 -*-
"""看扫码流程里服务端到底有没有下发 Set-Cookie —— 这是"扫进来就过期"的关键。

    python lang_dev/_probe_qr_cookie_header.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import netease_login as NL


def show(tag, r):
    print('--- %s ---' % tag)
    print('    HTTP', r.status_code)
    sc = r.headers.get('Set-Cookie')
    print('    Set-Cookie:', (sc[:160] + '…') if sc and len(sc) > 160 else sc)
    print('    r.cookies :', ['%s=%s' % (c.name, c.value[:12] + '…')
                              if len(c.value) > 12 else '%s=%s' % (c.name, c.value)
                              for c in r.cookies])
    try:
        j = r.json()
        print('    body code :', j.get('code'))
        print('    body 里有 cookie 字段:', 'cookie' in j)
    except Exception:
        print('    body 不是 JSON')


s = NL._reset_session()
cfg = dict(NL.DEFAULT_CONFIG)
cfg['requestId'] = '12345678'
import json
body = {'type': 1, 'header': json.dumps(cfg, separators=(',', ':'))}
ck = dict(NL.DEFAULT_COOKIES)
r1 = s.post(NL._eapi_url(NL.UNIKEY_API),
            data={'params': NL._eapi_params(NL.UNIKEY_API, body)},
            headers={'User-Agent': NL.UA_EAPI, 'Referer': NL.REFERER,
                     'Content-Type': 'application/x-www-form-urlencoded'},
            cookies=ck, timeout=20)
show('1) unikey', r1)
unikey = r1.json().get('unikey')
print('    unikey =', unikey)

body2 = {'key': unikey, 'type': 1, 'header': json.dumps(cfg, separators=(',', ':'))}
r2 = s.post(NL._eapi_url(NL.QRLOGIN_API),
            data={'params': NL._eapi_params(NL.QRLOGIN_API, body2)},
            headers={'User-Agent': NL.UA_EAPI, 'Referer': NL.REFERER,
                     'Content-Type': 'application/x-www-form-urlencoded'},
            cookies=ck, timeout=20)
show('2) poll（没人扫，预期 801）', r2)

print()
print('=== 结论怎么看 ===')
print('· 若 poll 的响应头里**有 Set-Cookie** → 登录成功那一次（803）也走响应头下发凭据，')
print('  我们之前只读 body 就会丢掉 cookie，进而"扫进来就过期"。')
print('· 若完全没有 Set-Cookie → 凭据应当出现在 body，那问题不在这一层。')
print()
print('=== 用修好后的 _eapi_post 再跑一次（应能看到 _cookie_from）===')
j = NL._eapi_post(NL.QRLOGIN_API, {'key': unikey, 'type': 1})
print('    code =', j.get('code'))
print('    _cookie_from =', j.get('_cookie_from'))
print('    _cookie_names =', j.get('_cookie_names'))
