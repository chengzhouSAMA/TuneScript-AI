# -*- coding: utf-8 -*-
"""把"助手口吻"的称呼从**会上传到 GitHub 的文件**里清掉。

统一成「用户」（直接对使用者说话的文案里用「你」）。

为什么要有这个脚本而不是手改一遍：称呼是**会复发的** —— 新写的注释/文档很容易
又把旧称呼带回来。跑一遍 `--check` 就能在提交前发现问题。

    python lang_dev/_strip_persona.py --check     # 只报，不改（有命中退出码 1）
    python lang_dev/_strip_persona.py             # 就地改
    python lang_dev/_strip_persona.py --check --all   # 连不上传的文件一起看

⚠️ 只改**称呼**，不动任何逻辑；改完必须核对行数没变、`.py` 还能编译。
⚠️ 读写一律走**二进制** —— 文本模式读会把 CRLF 归一成 LF，写回时整个文件的
   行尾就被换掉了（_selfcheck.py 会当场报几千行"未登记"）。踩过一次，别改回去。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 会被推送到 GitHub 的文件（仓库里 = 根目录模块 + dev/）
PUBLISHED = [
    'transcriber_app.py', 'ui_app.py', 'ui_kit.py', 'netease.py', 'netease_login.py',
    'bilibili.py', 'lang_id.py', 'lang_id_qwen_runner.py', 'audio_crop.py',
    'lang_modes.py', 'lang_pipeline.py', 'ja_romaji.py', 'en_phoneme.py',
    'asr_refine.py', 'lyrics_fetch.py', 'lyrics_match.py', 'make_icon.py',
]
DEV_DIR = 'lang_dev'
# 词表（数据，不是文案）：出现即报，不自动改，要人工判断。前两项是"助手口吻"的
# 称呼，其余是角色化/玩梗的痕迹。留着字面量是因为它就是这个检查的定义。
SMELL = ('喵', '猫娘', 'Mocha', '人家', '咱', '小女子')

# 先长后短、先具体后笼统 —— 顺序不能乱。
REPL = [
    ('实测主人就是黑胶会员', '实测用户就是黑胶会员'),
    ('实测主人的黑胶会员', '实测用户的黑胶会员'),
    ('主人自己推送时', '你自己推送时'),
    ('主人自己推送', '你自己推送'),
    ('主人自己', '你自己'),
    ('把改动推到主人的 GitHub', '把改动推到你的 GitHub'),
    ('若确实是主人在操作', '若确实是你本人在操作'),
    ('主人原话', '用户原话'),
    ('主人要求', '用户要求'),
    ('主人指定', '用户指定'),
    ('主人选定', '用户选定'),
    ('主人反馈', '用户反馈'),
    ('主人的', '用户的'),
    ('主人是', '用户是'),
    ('主人', '用户'),          # 兜底
]


def targets(include_all=False):
    out = []
    for f in PUBLISHED:
        p = os.path.join(ROOT, f)
        if os.path.isfile(p):
            out.append(p)
    if os.path.isdir(os.path.join(ROOT, DEV_DIR)):
        for n in sorted(os.listdir(os.path.join(ROOT, DEV_DIR))):
            if n.endswith(('.py', '.md')):
                out.append(os.path.join(ROOT, DEV_DIR, n))
    if include_all:
        for d in ('回归验收', '备份'):
            base = os.path.join(ROOT, d)
            for dp, _dn, fn in os.walk(base):
                for n in fn:
                    if n.endswith(('.py', '.md')):
                        out.append(os.path.join(dp, n))
    # 本脚本自己带着"替换表"，必然会命中自己 → 排除，否则每次 --check 都是一堆假报
    me = os.path.abspath(__file__)
    return [p for p in out if os.path.abspath(p) != me]


def scan(path):
    """返回 [(行号, 原文, 命中的 REPL 规则)]。"""
    hits = []
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except OSError as e:
        # 读不了就明说，不静默吞掉 —— 沉默的 except 会让"扫过了"变成假象
        print('  跳过（读不了）：%s — %s' % (os.path.relpath(path, ROOT), e))
        return hits
    for i, line in enumerate(text.splitlines(), 1):
        if '主人' in line or any(s in line for s in SMELL):
            rule = next((o for o, _ in REPL if o in line), '(需人工看)')
            hits.append((i, line.strip(), rule))
    return hits


def fix(path):
    """就地替换。

    ⚠️ 必须走**二进制**：文本模式读会把 CRLF 归一成 LF，写回时就把整个文件的
    行尾从 CRLF 换成 LF —— diff 会变成"每一行都改了"，`_selfcheck.py` 直接爆。
    （第一版就是这么翻车的：transcriber_app.py 4160 行全部变成"未登记的新增"。）
    """
    with open(path, 'rb') as f:
        raw = f.read()
    src = raw.decode('utf-8')
    n_before = src.count('\n')
    out = src
    for old, new in REPL:
        out = out.replace(old, new)
    if out == src:
        return 0, n_before, n_before
    with open(path, 'wb') as f:
        f.write(out.encode('utf-8'))
    n_after = out.count('\n')
    assert n_before == n_after, '行数变了，必须人工核对：%s' % path
    return src.count('主人') - out.count('主人'), n_before, n_after


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只报不改')
    ap.add_argument('--all', action='store_true', help='连 回归验收/备份 一起看')
    a = ap.parse_args()

    files = targets(a.all)
    total = 0
    for p in files:
        hits = scan(p)
        if not hits:
            continue
        rel = os.path.relpath(p, ROOT)
        if a.check:
            print('%s：%d 处' % (rel, len(hits)))
            for ln, txt, rule in hits[:3]:
                print('    %d: %s' % (ln, txt[:90]))
            if len(hits) > 3:
                print('    …还有 %d 处' % (len(hits) - 3))
            total += len(hits)
        else:
            gone, nb, na = fix(p)
            print('%s：清掉 %d 处（行数 %d → %d）' % (rel, gone, nb, na))
            if nb != na:
                print('   ⚠️ 行数变了，必须人工核对！')
            total += gone
    print('\n合计：%d 处' % total if a.check else '\n合计清掉：%d 处' % total)
    return 1 if (a.check and total) else 0


if __name__ == '__main__':
    sys.exit(main())
