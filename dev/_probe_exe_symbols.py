# -*- coding: utf-8 -*-
"""对比三个 exe 的入口脚本里到底有哪些符号（复用 _verify_exe.py 的取码方式）。

为什么要单独写：_verify_exe.py 的 WANT_MAIN 是**固定清单**，装的是哪一版源码它
分辨不出来 —— 用旧源码打出来的 exe 同样能过 57 项。这里直接把入口脚本的
字符串集合抽出来，按符号判断"装的是哪一版"。

用法：python lang_dev/_probe_exe_symbols.py
"""
import marshal
import os
import sys

from PyInstaller.archive.readers import CArchiveReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WANT = [
    ('_collapse_octave_doubling', '09-30 19:05 那一轮（左手去八度加倍）'),
    ('TS_ARRANGEMENT', '过夜：编配模式开关'),
    ('_sparsify_harmony_playable', '过夜：可弹和声抽稀'),
    ('_arrangement_mode', '过夜：编配模式读取'),
    ('with_ties', '过夜：谱面延音线控制'),
]

TARGETS = [
    ('出货中 dist/TuneScript AI V0.5.1.exe', 'dist/TuneScript AI V0.5.1.exe'),
    ('过夜候选 dist/fidelity_candidate/…', 'dist/fidelity_candidate/TuneScript AI V0.5.1.exe'),
    ('过夜候选 dist/studio_candidate/…', 'dist/studio_candidate/TuneScript AI V0.5.1.exe'),
]


def collect_strings(code, out=None, depth=0):
    """递归收集 code 对象的 co_consts / co_names / co_varnames 里的字符串。"""
    if out is None:
        out = set()
    if depth > 12:
        return out
    for field in ('co_consts', 'co_names', 'co_varnames'):
        for v in getattr(code, field, ()):
            if isinstance(v, str):
                out.add(v)
            elif hasattr(v, 'co_consts'):
                collect_strings(v, out, depth + 1)
    return out


def entry_strings(path):
    # CArchiveReader 收的是**路径**，不是文件对象（这一版 PyInstaller 的签名）
    a = CArchiveReader(path)
    best, best_n = None, -1
    for n in a.toc:
        s = str(n)
        if s.startswith(('pyi_rth', 'pyiboot', 'pyimod')) or '\\' in s or '.' in s:
            continue
        try:
            c = marshal.loads(a.extract(n))
        except Exception:
            continue
        k = len(getattr(c, 'co_names', ()))
        if k > best_n:
            best, best_n = c, k
    if best is None:
        return None, 0
    return collect_strings(best), best_n


for label, rel in TARGETS:
    p = os.path.join(ROOT, rel)
    print('\n### %s' % label)
    if not os.path.isfile(p):
        print('   (缺)')
        continue
    try:
        strs, n = entry_strings(p)
    except Exception as e:
        print('   ! 解析失败: %s: %s' % (type(e).__name__, e))
        continue
    print('   入口脚本顶层名字 %d 个，字符串 %d 个' % (n, len(strs)))
    for sym, desc in WANT:
        print('   %s %-28s %s' % ('有' if sym in strs else '--', sym, desc))
