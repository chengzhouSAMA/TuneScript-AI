# -*- coding: utf-8 -*-
"""地板守卫：拦"为了让检查变绿而偷偷降低标准"的那几手。

（做法取自 poka-yoke 与 constraint-driven-development：让错的事**自己喊出来**，
而不是写在文档里靠自觉。）

**只查 diff**，不查整个仓库 —— 判定基准是 `git diff <base>`（默认 origin/main，
含未提交改动）。返回码：0 干净 / 1 有违规 / 2 用不了。

    python lang_dev/_floor_guard.py                 # 查 origin/main..工作区
    python lang_dev/_floor_guard.py --base HEAD~1
    python lang_dev/_floor_guard.py --json

六条规则：

  F1 新增抑制注释      —— 把检查关掉而不是修代码
  F2 未完成的活        —— 抛 NotImplemented / 空 except 把失败变成沉默
  F3 测试被削弱        —— 新增 skip、或删掉断言
  F4 阈值被下调        —— RATCHET 表里的数字变差
  F5 悄悄开了例外      —— CONSTRAINTS.md 的例外表新增行
  F6 凭据进源码        —— cookie / token / 私钥

⚠️ 这个守卫自己也要能被证明"会失败" —— 见 `lang_dev/_check_floorguard.py`，
它拿合成 diff 逐条验证每条规则真的会响。
"""
import argparse
import json
import os
import re
import subprocess
import sys

# 被检查的仓库目录；由 --repo 覆盖。本项目的源码在工作区，git 在 _gh_repo/。
REPO = '.'

# 只有这些文件算"测试"：削弱它们 = 削弱判据
TEST_FILES = ('_check_', '_test_', '_selfcheck', '_verify_', '_smoke_')
# F1/F2/F3 只对**代码**生效：文档里为了讲清规则，必然会写出这些模式本身
# （规则书说"不许加抑制注释"，开发日志讲这次修了什么 —— 它们必然引用规则本身）。
# F6 不设限 —— 密钥写进文档一样是泄露。
CODE_EXT = ('.py', '.sh', '.js', '.mjs', '.ts', '.ps1', '.bat', '.cmd', '.yml', '.yaml')

SUPPRESS = [
    (r'#\s*noqa', 'noqa'),
    (r'#\s*type:\s*ignore', 'type: ignore'),
    (r'#\s*pylint:\s*disable', 'pylint: disable'),
    (r'istanbul\s+ignore', 'istanbul ignore'),
    (r'nosemgrep', 'nosemgrep'),
    (r'gitleaks:allow', 'gitleaks:allow'),
    (r'Stryker\s+disable', 'Stryker disable'),
    (r'@ts-ignore', 'ts-ignore'),
]
UNFINISHED = [
    (r'raise\s+NotImplementedError', 'NotImplementedError'),
    (r'throw\s+new\s+Error\(\s*[\'"]Not implemented', 'Not implemented'),
    (r'^\s*\.\.\.\s*$', '省略号占位'),
]
SKIP = [
    (r'@unittest\.skip', 'unittest.skip'),
    (r'@pytest\.mark\.skip', 'pytest.mark.skip'),
    (r'self\.skipTest\(', 'skipTest'),
    (r'\.skip\(\s*\)', '.skip()'),
]
SECRET = [
    (r'MUSIC_U\s*=', 'MUSIC_U cookie'),
    (r'ghp_[A-Za-z0-9]{20,}', 'GitHub PAT'),
    (r'-----BEGIN [A-Z ]*PRIVATE KEY-----', '私钥'),
    (r'\bAKIA[0-9A-Z]{16}\b', 'AWS key'),
    (r'\bsk-[A-Za-z0-9]{32,}\b', 'API key'),
]
# RATCHET 表：数字往下走（变差）就报。方向 w=越大越好 / s=越小越好
RATCHET_RE = re.compile(
    r'^\|\s*([^|]+?)\s*\|\s*([0-9.]+)\s*\|\s*([ws])\s*\|', re.M)


def _git(args):
    """在 REPO 里跑 git。默认就是当前目录。"""
    p = subprocess.run(['git'] + args, cwd=REPO, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, p.stdout


def _constraints_path():
    """CONSTRAINTS.md 取**被检查仓库的根**下的那份，不是脚本自己旁边那份。

    （否则守卫只能在自己的仓库里用 —— 测试要拿临时仓库验证"它真的会响"，
    取脚本路径就永远测不到真东西。）
    """
    import os
    rc, out = _git(['rev-parse', '--show-toplevel'])
    if rc == 0 and out.strip():
        return os.path.join(out.strip(), 'CONSTRAINTS.md')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'CONSTRAINTS.md')


def _parse_ratchet(text):
    return {m.group(1): (float(m.group(2)), m.group(3))
            for m in RATCHET_RE.finditer(text)}


def diff_lines(base):
    rc, out = _git(['diff', '--unified=0', base])
    if rc != 0:
        return None, 'git diff 失败（base=%s 存在吗？）' % base
    added, removed, files = [], [], []
    cur = None
    for line in out.splitlines():
        if line.startswith('+++ b/'):
            cur = line[6:]
            files.append(cur)
        elif line.startswith('+') and not line.startswith('+++'):
            added.append((cur, line[1:]))
        elif line.startswith('-') and not line.startswith('---'):
            removed.append((cur, line[1:]))
    # ⚠️ `git diff` **看不见未跟踪的新文件** —— 阴性对照抓出来的真漏洞：
    # 新建一个文件把抑制注释 / 密钥写进去，守卫会一声不吭。这里补上。
    rc2, others = _git(['ls-files', '--others', '--exclude-standard'])
    if rc2 == 0:
        for rel in [x.strip() for x in others.splitlines() if x.strip()]:
            files.append(rel)
            try:
                with open(rel, encoding='utf-8', errors='replace') as f:
                    for ln in f.read().splitlines():
                        added.append((rel, ln))
            except OSError:
                continue
    return (added, removed, files), None


def _strip_strings(line):
    """把行里的字符串字面量抠掉，只留代码与注释。

    为什么必须做：阴性对照的**夹具**就是 `'raise NotImplementedError'` 这种字符串，
    模式表本身也是 `(r'nosemgrep', 'nosemgrep')` 这种元组 —— 不抠掉的话，守卫会把
    自己（和它的测试）当成违规。抠掉字符串后，"字符串里提到 noqa"不报，
    而"赋值后面挂一条真抑制"照样报（注释没被抠掉）。

    F6（凭据）**不能**抠字符串 —— 密钥本来就是写在字符串里的。
    """
    return re.sub(r"'[^']*'|\"[^\"]*\"", '', line)


def _check_key(line):
    """把一条 `check(...)` 断言的"身份"抽出来。

    数字统一换成 `#`：阈值常常写在断言名字里（`累计新增在理智范围内（<=900）`），
    改成 1100 是**收紧护栏**、不是削弱测试，不该被判成"删掉断言"。
    只有整条断言凭空消失（身份在新增行里找不到）才算削弱。
    """
    m = re.search(r'check\s*\(', line)
    if not m:
        return None
    return re.sub(r'\d+', '#', line[m.start():]).strip()[:60]


def scan(added, removed):
    hits = []

    def add(rule, where, detail):
        hits.append({'rule': rule, 'file': where, 'detail': detail})

    # 只在**代码**文件里查模式：文档为了讲清规则必然写出这些模式本身。
    def is_code(f):
        return bool(f) and f.lower().endswith(CODE_EXT)

    for f, raw in added:
        if not is_code(f):
            continue
        line = _strip_strings(raw)
        for pat, name in SUPPRESS:
            if re.search(pat, line):
                add('F1 新增抑制注释', f, '%s: %s' % (name, raw.strip()[:70]))
        for pat, name in UNFINISHED:
            if re.search(pat, line):
                add('F2 未完成的活', f, '%s: %s' % (name, raw.strip()[:70]))

    # F6 凭据：**所有**文件、**原始行**（含字符串）都查
    for f, raw in added:
        if not f:
            continue
        for pat, name in SECRET:
            if re.search(pat, raw):
                # 只报位置，绝不回显命中内容 —— 值一旦进日志就等于泄露
                add('F6 凭据进源码', f, '疑似 %s（值已隐去）' % name)

    # F2b 空 except（新增行里 `except ...:` 紧跟 `pass`）
    for i in range(len(added) - 1):
        f, ra = added[i]
        g, rb = added[i + 1]
        a, b = _strip_strings(ra), _strip_strings(rb)
        if f and f == g and is_code(f) \
                and re.search(r'except[^:]*:\s*$', a) and b.strip() == 'pass':
            add('F2 未完成的活', f, '空 except 吞掉失败：%s' % ra.strip()[:60])

    # F3 测试被削弱
    added_keys = {_check_key(l) for f, l in added
                  if f and any(t in f for t in TEST_FILES)}
    for f, raw in added:
        if is_code(f) and any(t in f for t in TEST_FILES):
            line = _strip_strings(raw)
            for pat, _name in SKIP:
                if re.search(pat, line):
                    add('F3 测试被削弱', f, '新增跳过：%s' % raw.strip()[:70])
    for f, line in removed:
        if f and any(t in f for t in TEST_FILES) and re.search(r'\bcheck\s*\(', line):
            k = _check_key(line)
            if k not in added_keys:
                add('F3 测试被削弱', f, '删掉了一条断言：%s' % line.strip()[:70])

    return hits


def exception_rows(text):
    """例外表里的行（形如 `| E1 | 规则 | 路径 | ... |`）。"""
    return [l for l in text.splitlines()
            if re.search(r'\|\s*[WE]\d+\s*\|', l)]


def new_exceptions(base):
    """例外表**变长**了才报；新文件没有基线可比时直接跳过。"""
    rc, old = _git(['show', '%s:CONSTRAINTS.md' % base])
    if rc != 0:
        return []
    try:
        with open(_constraints_path(), encoding='utf-8') as f:
            new = f.read()
    except OSError:
        return []
    o, n = exception_rows(old), exception_rows(new)
    return n[len(o):] if len(n) > len(o) else []


def ratchet_regression(base):
    """对比 CONSTRAINTS.md 的 RATCHET 表在 base 与工作区之间的变化。"""
    rc, old = _git(['show', '%s:CONSTRAINTS.md' % base])
    if rc != 0:
        return []
    try:
        with open(_constraints_path(), encoding='utf-8') as f:
            new = f.read()
    except OSError:
        return []
    o, n = _parse_ratchet(old), _parse_ratchet(new)
    out = []
    for k, (nv, d) in n.items():
        if k not in o:
            continue
        ov = o[k][0]
        worse = (d == 'w' and nv < ov) or (d == 's' and nv > ov)
        if worse:
            out.append('%s：%s → %s（方向 %s，变差）' % (k, ov, nv, d))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--repo', default='.',
                    help='被检查的仓库目录（本项目的源码在工作区、git 在 _gh_repo/）')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    global REPO
    REPO = a.repo
    if not os.path.isdir(os.path.join(REPO, '.git')):
        print('不是 git 仓库：%s' % os.path.abspath(REPO))
        return 2

    dl, err = diff_lines(a.base)
    if err:
        print(err)
        return 2
    added, removed, files = dl
    hits = scan(added, removed)
    hits += [{'rule': 'F4 阈值被下调', 'file': 'CONSTRAINTS.md', 'detail': d}
             for d in ratchet_regression(a.base)]
    hits += [{'rule': 'F5 悄悄开了例外', 'file': 'CONSTRAINTS.md',
              'detail': r.strip()[:80]} for r in new_exceptions(a.base)]

    if a.json:
        print(json.dumps({'base': a.base, 'files': len(files), 'hits': hits},
                         ensure_ascii=False, indent=2))
    else:
        print('地板守卫：base=%s，改了 %d 个文件，+%d/-%d 行'
              % (a.base, len(files), len(added), len(removed)))
        if not hits:
            print('  ✓ 六条规则都没有命中')
        for h in hits:
            print('  ✗ [%s] %s  %s' % (h['rule'], h['file'], h['detail']))
        print('  命中 %d 条' % len(hits))
    return 1 if hits else 0


if __name__ == '__main__':
    sys.exit(main())
