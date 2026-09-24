# -*- coding: utf-8 -*-
"""对照实验：扫码登录到底该走 weapi 还是 eapi。

背景：NeteaseCloudMusicApi 的 `login_qr_key.js` / `login_qr_check.js` 用的是
`https://music.163.com/weapi/login/qrcode/unikey`（crypto: weapi），
而我们一直用 `interface3.music.163.com/eapi/...`（app 接口）。
两者的 unikey **不在同一个池子里** —— 二维码里写的是网页登录链接
`https://music.163.com/login?codekey=<key>`，手机 App 扫的其实是 **网页** 那条会话。
用 eapi 拿的 key，手机扫完服务端找不到 → 报"二维码不存在或已过期"。

    python lang_dev/_probe_weapi_qr.py
"""
import base64
import json
import os
import random
import string
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

MODULUS = ('00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e4176'
           '29ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce'
           '10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7')
NONCE = '0CoJUm6Qyw8W8jud'
PUBKEY = '010001'
IV = b'0102030405060708'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')


def _aes(text, key):
    from Crypto.Cipher import AES
    pad = 16 - len(text) % 16
    raw = text + bytes([pad]) * pad
    return base64.b64encode(AES.new(key, AES.MODE_CBC, IV).encrypt(raw))


def weapi(secret_raw, payload):
    text = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode()
    key = ''.join(random.choices(string.ascii_letters + string.digits, k=16)).encode()
    params = _aes(_aes(text, NONCE.encode()), key)
    rev = key[::-1].hex()
    enc = pow(int(rev, 16), int(PUBKEY, 16), int(MODULUS, 16))
    return {'params': params.decode(), 'encSecKey': '%0256x' % enc}


def post(path, payload, cookies=None):
    r = requests.post('https://music.163.com' + path,
                      data=weapi(None, payload),
                      headers={'User-Agent': UA, 'Referer': 'https://music.163.com/',
                               'Content-Type': 'application/x-www-form-urlencoded',
                               'Origin': 'https://music.163.com'},
                      cookies=cookies or {'os': 'pc', 'appver': '2.9.7'},
                      timeout=20)
    try:
        return r.json()
    except Exception:
        return {'_raw': r.text[:200], '_status': r.status_code}


def safe(o):
    if isinstance(o, dict):
        return {k: ('<%d 字符>' % len(str(v)) if k == 'cookie' else v)
                for k, v in o.items()}
    return o


print('=== 1) weapi 取 unikey ===')
j = post('/weapi/login/qrcode/unikey', {'type': 1})
print('   ', json.dumps(safe(j), ensure_ascii=False)[:400])
key = j.get('unikey')
if not key:
    print('!! weapi 没拿到 unikey，后面没法测')
    sys.exit(1)
print('    unikey =', key)
print('    扫码链接 = https://music.163.com/login?codekey=%s' % key)

print('\n=== 2) weapi 轮询 5 次（每次 2 秒）===')
for i in range(5):
    r = post('/weapi/login/qrcode/client/login', {'key': key, 'type': 1})
    print('    [%4.1fs] code=%-5s %s' % (i * 2.0, r.get('code'),
                                         (r.get('message') or '')[:60]))
    if r.get('code') == 800:
        print('    → 服务端说 800（这也太快了，说明 weapi 这条也不对）')
        break
    time.sleep(2)

print('\n=== 3) 对照：同一个 key 用 eapi 轮询会怎样 ===')
import netease_login as NL
e = NL._eapi_post(NL.QRLOGIN_API, {'key': key, 'type': 1})
print('    eapi 轮询 weapi 的 key ->', json.dumps(safe(e), ensure_ascii=False)[:200])
print('\n如果第 2 步一直是 801、而第 3 步报错/800 → 两者确实是两套会话，'
      '必须整条链路统一。')
