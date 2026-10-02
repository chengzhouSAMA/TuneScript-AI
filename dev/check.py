# -*- coding: utf-8 -*-
"""自检模式：一个入口，三档预算。判据与数字都写在仓库根的 CONSTRAINTS.md。

    python lang_dev/check.py --stage fast    # 每次编辑后      （实测 0.9 秒）
    python lang_dev/check.py --stage task    # 认为做完了      （实测 12.2 秒）
    python lang_dev/check.py --stage full    # 出货前（要 exe） （分钟级）
    python lang_dev/check.py --list

退出码：0 全绿 / 1 有失败 / 2 用法或环境不对。

为什么分档（依据 constraint-driven-development「成本决定位置」）：
跑不完几秒的检查塞进编辑循环，最后一定是被人关掉 —— 被关掉的闸门比没有闸门更糟，
因为标准看起来还在。所以 `_selfcheck.py`（冷盘单跑实测 28.3 秒，要加载 LID 模型）
只放在 task 档，绝不进 fast。
"""
import argparse
import ast
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable

# 项目里源码在工作区、git 在 _gh_repo/ —— 地板守卫要对着真正有 git 的那个跑。
REPO_DIR = os.path.join(ROOT, '_gh_repo')

# name -> (stage, 说明, 命令, 预算秒)
CHECKS = [
    ('syntax', 'fast', '所有 .py 过 ast.parse', None, 5),
    ('ruff', 'fast', '静态检查（只挑真会出错的规则）',
     [PY, '-m', 'ruff', 'check', '.', '回归验收/regress_one.py'], 20),
    ('tree_sync', 'fast', '仓库里有、工作区没有的文件',
     [PY, 'lang_dev/_check_tree_sync.py'], 15),
    ('persona', 'fast', '已发布文件无角色化称呼（含词表自检）',
     [PY, 'lang_dev/_strip_persona.py', '--check', '--selftest'], 10),
    ('unit', 'fast', '核心编排单元自检 159 项',
     [PY, 'lang_dev/_test_handgap_accomp.py'], 20),
    ('fidelity', 'fast', '音符解码与谱面保真（含阴性对照）',
     [PY, 'lang_dev/_test_fidelity.py'], 20),
    ('gui', 'fast', 'GUI 接线 0 问题',
     [PY, 'lang_dev/_check_gui.py'], 10),
    ('floor', 'task', '地板守卫（F1~F6）',
     [PY, 'lang_dev/_floor_guard.py', '--repo', REPO_DIR], 30),
    ('floor_negctl', 'task', '地板守卫的阴性对照 21 项',
     [PY, 'lang_dev/_check_floorguard.py'], 60),
    ('newui', 'task', '新版 UI 72 项',
     [PY, 'lang_dev/_check_newui.py'], 30),
    ('pushguard', 'task', '推送闸门 18 项',
     [PY, 'lang_dev/_check_pushguard.py'], 30),
    ('cli_contract', 'task', '--cli 退出码与输出契约 23 项',
     [PY, 'lang_dev/_check_cli_contract.py'], 120),
    ('selfcheck', 'task', '归档/冻结基线 104 项',
     [PY, 'lang_dev/_selfcheck.py'], 120),
    ('verify_exe', 'full', '出货 exe 的内容层 79 项',
     [PY, 'lang_dev/_verify_exe.py'], 300),
    ('tree_sync_strict', 'full', '克隆不落后于工作区（出货前必查）',
     [PY, 'lang_dev/_check_tree_sync.py', '--strict'], 30),
    ('smoke_exe', 'full', '出货 exe 冒烟（两臂）',
     [PY, 'lang_dev/_smoke_exe.py', '--arm', 'both'], 1800),
]
STAGES = ['fast', 'task', 'full']
SKIP_DIRS = {'lang_id_venv', 'lang_id_venv314', '__pycache__', '_gh_repo',
             '备份', '回归验收', '_work', 'build', 'dist', 'promo_video', '.git'}


def syntax_ok():
    """把工作区里会被出货的 .py 都解析一遍。返回 (ok, 说明)。"""
    bad, n = [], 0
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if not f.endswith('.py'):
                continue
            p = os.path.join(dp, f)
            n += 1
            try:
                with open(p, encoding='utf-8') as fh:
                    ast.parse(fh.read())
            except SyntaxError as e:
                bad.append('%s:%s %s' % (os.path.relpath(p, ROOT), e.lineno, e.msg))
            except OSError as e:
                # 读不了要报出来，不能静默跳过 —— 否则"153 个文件都过了"是假象
                bad.append('%s 读不了（%s）' % (os.path.relpath(p, ROOT),
                                            type(e).__name__))
    if bad:
        return False, '；'.join(bad[:3])
    return True, '%d 个文件' % n


def run_one(name, cmd):
    if cmd is None:                      # 进程内检查
        return syntax_ok()
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    out = (p.stdout or '') + (p.stderr or '')
    tail = [l.strip() for l in out.splitlines() if l.strip()]
    # 从输出里挑一行最有信息量的当摘要
    best = ''
    for l in reversed(tail):
        if any(k in l for k in ('汇总', '命中', '0 问题', '问题')):
            best = l
            break
    if not best and tail:
        best = tail[-1]
    if p.returncode == 2 and '不是 git 仓库' in out:
        return None, '跳过：没找到 git 仓库'
    return p.returncode == 0, best[:88]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', default='fast', choices=STAGES)
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--only', default='', help='只跑某一项（按名字）')
    a = ap.parse_args()

    if a.list:
        for n, st, desc, _c, bud in CHECKS:
            print('%-13s %-5s %-28s 预算 %ds' % (n, st, desc, bud))
        return 0

    upto = STAGES.index(a.stage)
    todo = [c for c in CHECKS if STAGES.index(c[1]) <= upto]
    if a.only:
        todo = [c for c in todo if c[0] == a.only]
        if not todo:
            print('没有这项：%s' % a.only)
            return 2

    print('自检模式 · %s 档 · %d 项（判据见 CONSTRAINTS.md）' % (a.stage, len(todo)))
    t_all = time.time()
    fails = []
    for name, _st, desc, cmd, budget in todo:
        t0 = time.time()
        ok, summary = run_one(name, cmd)
        el = time.time() - t0
        mark = '✓' if ok else ('-' if ok is None else '✗')
        over = '  超预算!' if el > budget else ''
        print('  %s %-13s %6.1fs  %-28s %s%s' % (mark, name, el, desc, summary, over))
        if ok is False:
            fails.append(name)

    total = time.time() - t_all
    print('-' * 72)
    print('合计 %.1fs，失败 %d 项%s' % (total, len(fails),
                                   ('：' + '、'.join(fails)) if fails else ''))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
