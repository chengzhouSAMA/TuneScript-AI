# -*- coding: utf-8 -*-
"""`--cli` 退出码与输出契约的自测（依据 ai-native-cli 的 P0 规则）。

**真跑子进程**，不 mock：harness 只认退出码，所以必须验证"真的退了这个码"。

    python lang_dev/_check_cli_contract.py

覆盖：
  X1/X3  成功 0、用法错误 2（缺参数 / 未知参数 / 类型错）
  X9     失败**不许**退 0，也不许把错误写 stdout
  X2/X4  not-found 20、auth 10
  C1/C2  stdout 只放数据、日志与错误走 stderr
  E4/E5  错误是单行 JSON，含 error/code/message/suggestion 四个键
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
APP = os.path.join(ROOT, 'transcriber_app.py')
TMP = tempfile.mkdtemp(prefix='ts_cli_contract_')

OK, BAD = [], []


def check(name, cond, info=''):
    (OK if cond else BAD).append(name)
    print('  %s %-56s %s' % ('✓' if cond else '✗', name, info))


def run(*args):
    p = subprocess.run([PY, APP, '--cli'] + list(args), cwd=ROOT,
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=120)
    return p.returncode, p.stdout or '', p.stderr or ''


def err_json(stderr):
    """把 `[cli] ERROR {...}` 那一行抠出来解析。"""
    for line in stderr.splitlines():
        if line.startswith('[cli] ERROR '):
            try:
                return json.loads(line[len('[cli] ERROR '):])
            except ValueError:
                return None
    return None


print('=== X1/X3：成功 0、用法错误 2 ===')
rc, out, err = run('--help')
check('--help → 0（以前它没人处理，直接退 2）', rc == 0, 'rc=%d' % rc)
check('--help 列出退出码表', '退出码' in out and 'usage' in out)
check('--help 走 stdout（帮助是用户要的数据）', out.strip() != '' and err.strip() == '')

rc, out, err = run()
check('不给任何参数 → 2', rc == 2, 'rc=%d' % rc)
d = err_json(err)
check('MISSING_OUTDIR + 四个键齐全',
      bool(d) and d.get('code') == 'MISSING_OUTDIR'
      and set(d) == {'error', 'code', 'message', 'suggestion'}, str(d)[:80])

rc, out, err = run('--outdir', TMP)
check('给了 --outdir 但没给输入源 → 2', rc == 2, 'rc=%d' % rc)
check('MISSING_INPUT', (err_json(err) or {}).get('code') == 'MISSING_INPUT')

rc, out, err = run('--outdir', TMP, '--bogus-flag')
check('未知参数 → 2（G1）', rc == 2, 'rc=%d' % rc)

rc, out, err = run('--outdir', TMP, '--quality', 'bogus')
check('参数取值非法 → 2', rc == 2, 'rc=%d' % rc)

print('\n=== X2/X4：找不到 = 20 ===')
missing = os.path.join(TMP, '不存在 的歌.flac')
rc, out, err = run('--audio', missing, '--outdir', TMP)
check('音频文件不存在 → 20', rc == 20, 'rc=%d' % rc)
check('AUDIO_NOT_FOUND', (err_json(err) or {}).get('code') == 'AUDIO_NOT_FOUND')

print('\n=== C1/X9：失败时 stdout 必须为空 ===')
for name, args in (('缺参数', ()), ('缺输入源', ('--outdir', TMP)),
                   ('文件不存在', ('--audio', missing, '--outdir', TMP))):
    rc, out, err = run(*args)
    check('%-8s 失败时 stdout 为空' % name, out.strip() == '', repr(out[:40]))

print('\n=== E5：message 是人话、suggestion 可执行 ===')
d = err_json(err) or {}
check('message 非空', bool(d.get('message')), str(d.get('message'))[:50])
check('suggestion 非空', bool(d.get('suggestion')), str(d.get('suggestion'))[:50])

print('\n=== E8：退出码表与代码一致（防止有人偷偷改值） ===')
sys.path.insert(0, ROOT)
import transcriber_app as TA
check('EXIT_OK == 0', TA.EXIT_OK == 0)
check('EXIT_FAIL == 1', TA.EXIT_FAIL == 1)
check('EXIT_USAGE == 2（P0 X3）', TA.EXIT_USAGE == 2)
check('EXIT_AUTH == 10', TA.EXIT_AUTH == 10)
check('EXIT_NOT_FOUND == 20', TA.EXIT_NOT_FOUND == 20)
check('EXIT_CONFLICT == 30', TA.EXIT_CONFLICT == 30)
check('CLI_EXIT_TABLE 覆盖全部六个码',
      sorted(c for c, _n, _d in TA.CLI_EXIT_TABLE)
      == sorted([TA.EXIT_OK, TA.EXIT_FAIL, TA.EXIT_USAGE,
                 TA.EXIT_AUTH, TA.EXIT_NOT_FOUND, TA.EXIT_CONFLICT]))

print('\n=== 汇总：%d 项，%d 通过，%d 失败 ===' % (len(OK) + len(BAD), len(OK), len(BAD)))
if BAD:
    for b in BAD:
        print('  失败：%s' % b)
sys.exit(1 if BAD else 0)
