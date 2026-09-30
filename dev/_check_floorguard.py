# -*- coding: utf-8 -*-
"""地板守卫的**阴性对照**：证明它真的会响，而不是永远绿。

（依据 break-ai-fix-loops：「让验证者证明自己会失败」—— 一个从没红过的检查，
没有任何证明力。装那道推送闸门时就是靠阴性对照才发现第一版是假闸门。）

做法：建一个临时 git 仓库，逐条把"作弊"写进去，看守卫是不是**因为该条规则**报错；
再给一个**干净对照**，确认它不会见谁都咬。

    python lang_dev/_check_floorguard.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, '_floor_guard.py')

OK, BAD = [], []


def check(name, cond, info=''):
    (OK if cond else BAD).append(name)
    print('  %s %-58s %s' % ('✓' if cond else '✗', name, info))


TMP = tempfile.mkdtemp(prefix='ts_floorguard_')


def run(args, cwd=TMP):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def git(args):
    return run(['git'] + args)


def setup():
    os.makedirs(TMP, exist_ok=True)
    git(['init', '-q', '-b', 'main'])
    git(['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit',
         '-q', '--allow-empty', '-m', 'base'])
    with open(os.path.join(TMP, 'CONSTRAINTS.md'), 'w', encoding='utf-8') as f:
        f.write('# Constraints\n\n## RATCHET\n\n'
                '| 指标 | 当前值 | 方向 |\n|---|---|---|\n'
                '| 自检通过数 | 99 | w |\n| 碎片率 | 0.008 | s |\n')
    with open(os.path.join(TMP, 'app.py'), 'w', encoding='utf-8') as f:
        f.write('def f():\n    return 1\n')
    git(['add', '-A'])
    git(['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', 'files'])
    git(['tag', 'base'])
    return 'base'


def guard(base='base'):
    return run([sys.executable, GUARD, '--base', base])


print('=== 0) 阴性对照：干净 diff 必须**不**报 ===')
setup()
with open(os.path.join(TMP, 'app.py'), 'w', encoding='utf-8') as f:
    f.write('def f():\n    return 2\n\n\ndef g(x):\n    return x + 1\n')
rc, out = guard()
check('干净改动 → 退出码 0', rc == 0, 'rc=%d' % rc)
check('干净改动 → 没命中任何规则', '命中 0 条' in out)

print('\n=== 逐条规则：写进去，必须报 ===')
CASES = [
    ('F1 新增抑制注释', 'app.py',
     'import os  # no' + 'qa: F401\n', 'F1'),
    ('F1b type: ignore', 'app.py',
     'x = f()  # type:' + ' ignore\n', 'F1'),
    ('F2 未完成的活', 'app.py',
     'def todo():\n    raise NotImplementedError\n', 'F2'),
    ('F2b 空 except 吞失败', 'app.py',
     'def h():\n    try:\n        f()\n    except Exception:\n        pass\n', 'F2'),
    ('F6 凭据进源码', 'app.py',
     'COOKIE = "MUSIC' + '_U=deadbeef"\n', 'F6'),
    ('F6b GitHub PAT', 'app.py',
     'TOKEN = "ghp' + '_' + 'A' * 40 + '"\n', 'F6'),
    ('F3 新增跳过', '_check_x.py',
     'import unittest\n\n\n@unittest.skip("later")\ndef test_a():\n    pass\n', 'F3'),
]
for name, path, body, rule in CASES:
    with open(os.path.join(TMP, 'app.py'), 'w', encoding='utf-8') as f:
        f.write('def f():\n    return 1\n')
    if os.path.isfile(os.path.join(TMP, '_check_x.py')):
        os.remove(os.path.join(TMP, '_check_x.py'))
    with open(os.path.join(TMP, path), 'w', encoding='utf-8') as f:
        f.write(body)
    rc, out = guard()
    hit = rule in out
    check(name, rc == 1 and hit, 'rc=%d%s' % (rc, '' if hit else ' 没命中 %s' % rule))

print('\n=== 删掉断言也要报 ===')
with open(os.path.join(TMP, 'app.py'), 'w', encoding='utf-8') as f:
    f.write('def f():\n    return 1\n')
os.remove(os.path.join(TMP, '_check_x.py'))
with open(os.path.join(TMP, '_check_x.py'), 'w', encoding='utf-8') as f:
    f.write('check("a", True)\ncheck("b", True)\n')
git(['add', '-A'])
git(['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', 'tests'])
git(['tag', 't2'])
with open(os.path.join(TMP, '_check_x.py'), 'w', encoding='utf-8') as f:
    f.write('check("a", True)\n')
rc, out = guard('t2')
check('删掉一条断言 → F3 报', rc == 1 and 'F3' in out, 'rc=%d' % rc)

print('\n=== 阈值被下调 ===')
# 先把 F3 的残留清掉（上一步把 _check_x.py 删成了一行），否则 rc 被 F3 带红，
# 分不清 F4 到底响没响 —— 检查之间必须互不串台。
with open(os.path.join(TMP, '_check_x.py'), 'w', encoding='utf-8') as f:
    f.write('check("a", True)\ncheck("b", True)\n')
git(['add', '-A'])
git(['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', 'restore tests'])
git(['tag', 't3'])
rc, out = guard('t3')
check('（先确认此刻干净：rc=0 且 F3/F4 都不报）',
      rc == 0 and 'F3' not in out and 'F4' not in out, 'rc=%d' % rc)
with open(os.path.join(TMP, 'CONSTRAINTS.md'), 'w', encoding='utf-8') as f:
    f.write('# Constraints\n\n## RATCHET\n\n'
            '| 指标 | 当前值 | 方向 |\n|---|---|---|\n'
            '| 自检通过数 | 90 | w |\n| 碎片率 | 0.008 | s |\n')
rc, out = guard('t3')
check('通过数 99→90（越大越好）→ F4 报', rc == 1 and 'F4' in out, 'rc=%d' % rc)
with open(os.path.join(TMP, 'CONSTRAINTS.md'), 'w', encoding='utf-8') as f:
    f.write('# Constraints\n\n## RATCHET\n\n'
            '| 指标 | 当前值 | 方向 |\n|---|---|---|\n'
            '| 自检通过数 | 99 | w |\n| 碎片率 | 0.05 | s |\n')
rc, out = guard('t3')
check('碎片率 0.008→0.05（越小越好）→ F4 报', rc == 1 and 'F4' in out, 'rc=%d' % rc)
with open(os.path.join(TMP, 'CONSTRAINTS.md'), 'w', encoding='utf-8') as f:
    f.write('# Constraints\n\n## RATCHET\n\n'
            '| 指标 | 当前值 | 方向 |\n|---|---|---|\n'
            '| 自检通过数 | 120 | w |\n| 碎片率 | 0.004 | s |\n')
rc, out = guard('t3')
check('两项都变好 → 整轮 rc=0 且 F4 不报',
      rc == 0 and 'F4' not in out, 'rc=%d' % rc)

print('\n=== 例外表变长要报（F5） ===')
BASE_MD = ('# Constraints\n\n## RATCHET\n\n'
           '| 指标 | 当前值 | 方向 |\n|---|---|---|\n'
           '| 自检通过数 | 120 | w |\n| 碎片率 | 0.004 | s |\n\n'
           '## 例外\n\n| ID | 规则 | 路径 | 原因 | 到期 |\n|---|---|---|---|---|\n'
           '| E1 | 某规则 | 某路径 | 历史原因 | 2026-12-31 |\n')
with open(os.path.join(TMP, 'CONSTRAINTS.md'), 'w', encoding='utf-8') as f:
    f.write(BASE_MD)
git(['add', '-A'])
git(['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', 'constraints'])
git(['tag', 't4'])
rc, out = guard('t4')
check('例外表没变 → F5 不报', rc == 0 and 'F5' not in out, 'rc=%d' % rc)
with open(os.path.join(TMP, 'CONSTRAINTS.md'), 'w', encoding='utf-8') as f:
    f.write(BASE_MD + '| E2 | 又一条 | 另一处 | 没写原因 | 2027-06-30 |\n')
rc, out = guard('t4')
check('新增一行例外 → F5 报', rc == 1 and 'F5' in out, 'rc=%d' % rc)
with open(os.path.join(TMP, 'CONSTRAINTS.md'), 'w', encoding='utf-8') as f:
    f.write('# Constraints\n\n## RATCHET\n\n'
            '| 指标 | 当前值 | 方向 |\n|---|---|---|\n'
            '| 自检通过数 | 120 | w |\n| 碎片率 | 0.004 | s |\n')
rc, out = guard('t4')
check('整张例外表被删掉 → 不报 F5（变少不算放松）',
      rc == 0 and 'F5' not in out, 'rc=%d' % rc)

print('\n=== 字符串不算违规，注释才算（否则守卫会咬自己的夹具） ===')
rc, out = guard('t4')
with open(os.path.join(TMP, 'app.py'), 'w', encoding='utf-8') as f:
    # 字符串字面量里提到抑制标记：不是真的在抑制，不该报
    f.write('DOC = "举个例子：x = 1  # no' + 'qa"\n')
rc, out = guard('t4')
check('字符串里提到抑制标记 → 不报', rc == 0 and 'F1' not in out, 'rc=%d' % rc)
with open(os.path.join(TMP, 'app.py'), 'w', encoding='utf-8') as f:
    # 真注释：就是在抑制，必须报
    f.write('import os  # no' + 'qa: F401\n')
rc, out = guard('t4')
check('代码里真的挂了抑制注释 → 要报', rc == 1 and 'F1' in out, 'rc=%d' % rc)

print('\n=== 改阈值 ≠ 删断言（收紧护栏不该被判成削弱测试） ===')
with open(os.path.join(TMP, '_check_x.py'), 'w', encoding='utf-8') as f:
    f.write('check("通过数在理智范围内（<=900）", True)\ncheck("另一条", True)\n')
git(['add', '-A'])
git(['-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', 'asserts'])
git(['tag', 't5'])
with open(os.path.join(TMP, '_check_x.py'), 'w', encoding='utf-8') as f:
    f.write('check("通过数在理智范围内（<=1100）", True)\ncheck("另一条", True)\n')
rc, out = guard('t5')
check('只改名字里的阈值（900→1100）→ **不报**', rc == 0 and 'F3' not in out, 'rc=%d' % rc)
with open(os.path.join(TMP, '_check_x.py'), 'w', encoding='utf-8') as f:
    f.write('check("通过数在理智范围内（<=1100）", True)\n')
rc, out = guard('t5')
check('整条断言消失 → 要报', rc == 1 and 'F3' in out, 'rc=%d' % rc)

os.chdir(os.path.dirname(TMP))
shutil.rmtree(TMP, ignore_errors=True)

print('\n=== 汇总：%d 项，%d 通过，%d 失败 ===' % (len(OK) + len(BAD), len(OK), len(BAD)))
if BAD:
    for b in BAD:
        print('  失败：%s' % b)
sys.exit(1 if BAD else 0)
