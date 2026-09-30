# -*- coding: utf-8 -*-
"""仓库里有、工作区没有的文件 —— 那几个文件你看不见，改不了，也扫不到。

为什么需要它（踩过两次）：
  · `README.md` 只在 `_gh_repo/` 里，工作区没有 → 它**从来没被称呼扫描扫过**
    （最显眼的文件反而没人管）；
  · `requirements.txt` / `.gitignore` / `README.txt` 同样只在克隆里。
症状是"线上有一份内容，工作区里搜不到" —— 想改它只能直接进克隆改，
于是那份文件就永远脱离了工作区这一套自检。

    python lang_dev/_check_tree_sync.py          # 查（有缺失退出码 1）
    python lang_dev/_check_tree_sync.py --list   # 只列出来

⚠️ 仓库目录不存在时**跳过**（退出码 2），不算失败。
"""
import argparse
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = os.path.join(ROOT, '_gh_repo')


def _sha(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()

# 仓库路径 → 工作区路径的**显式例外**。默认 dev/x → lang_dev/x，其余同名。
# 只有真的对不上时才往这里加，并且写清原因。
REMAP = {
    'dev/regress_one.py': '回归验收/regress_one.py',   # 验收 harness，按用途放在 回归验收/
}


def workspace_path(repo_rel):
    if repo_rel in REMAP:
        return REMAP[repo_rel]
    if repo_rel.startswith('dev/'):
        return 'lang_dev/' + repo_rel[4:]
    return repo_rel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true', help='只列出来')
    ap.add_argument('--strict', action='store_true',
                    help='克隆落后也算失败（出货/提交前用这个）')
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(REPO, '.git')):
        print('跳过：没找到仓库 %s（推送用的克隆）' % REPO)
        return 2

    # ⚠️ 必须关掉 core.quotepath：默认 git 会把非 ASCII 文件名转义成
    #    `"\351\237\263..."`，于是中文名的文件全被当成"工作区缺"（假阳性）。
    p = subprocess.run(['git', '-c', 'core.quotepath=false', 'ls-files'],
                       cwd=REPO, capture_output=True,
                       text=True, encoding='utf-8', errors='replace')
    if p.returncode != 0:
        print('跳过：git ls-files 失败')
        return 2
    tracked = [x for x in p.stdout.splitlines() if x.strip()]

    missing = []
    drift = []
    for rel in tracked:
        ws = workspace_path(rel).replace('/', os.sep)
        wpath = os.path.join(ROOT, ws)
        rpath = os.path.join(REPO, rel.replace('/', os.sep))
        if not os.path.exists(wpath):
            missing.append((rel, ws))
        elif os.path.isfile(rpath) and _sha(wpath) != _sha(rpath):
            drift.append(rel)

    print('仓库受控文件 %d 个；工作区缺 %d 个；克隆落后 %d 个'
          % (len(tracked), len(missing), len(drift)))
    for rel, ws in missing:
        print('  ✗ %-32s 工作区应有：%s' % (rel, ws))
    for rel in drift:
        print('  ~ %-32s 工作区更新了，克隆还是旧的' % rel)

    fail = bool(missing)
    if missing and not a.list:
        print('\n这几个文件在克隆里、工作区没有 —— 意味着：工作区那一套自检扫不到它们。')
        print('补法：从 _gh_repo/ 复制到上面对应的路径，然后跑 lang_dev/_sync_repo.py。')
    if drift:
        if a.strict:
            print('\n克隆落后 %d 个文件 —— 出货前必须同步：'
                  'python lang_dev/_sync_repo.py' % len(drift))
            fail = True
        else:
            # 干活途中"工作区新、克隆旧"是正常状态，别报红（报红了就会被无视）
            print('\n（干活途中落后是正常的；提交/推送前跑 python lang_dev/_sync_repo.py）')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
