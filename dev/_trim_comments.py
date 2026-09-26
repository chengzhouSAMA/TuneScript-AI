# -*- coding: utf-8 -*-
"""_trim_comments.py — 把注释/docstring 收敛成"只讲功能"，其余字节不动。

背景：本项目里的注释写成了开发日志（实测数字、踩坑记录、与备忘/README 的交叉引用、
日期、emoji 标记）。这些内容属于文档，不属于代码注释；代码里只应留：

  · 这个模块/函数是干什么的、怎么调（参数 / 返回 / 用法）
  · 代码本身看不出来的行为（边界、单位、降级策略、坑）

本工具的做法（**保守**）：
  1. 用 `tokenize` 定位行注释 → 命中"叙事特征"的丢弃，其余保留；
  2. 用 `ast` 定位 docstring → 只留首行摘要 + 以功能关键词开头的段落
     （参数/返回/用法/注意/CLI/示例/Args/Returns/Raises）；
  3. 除注释与 docstring 之外**一个字节都不改**（代码、空行、缩进原样保留）。

用法:
    python lang_dev/_trim_comments.py            # dry-run，只报统计
    python lang_dev/_trim_comments.py --apply    # 真正写入（先自动备份到 备份/pre_trim_<ts>/）
"""
import argparse
import ast
import io
import os
import re
import shutil
import sys
import time
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = ["netease.py", "lang_id.py", "audio_crop.py", "lang_modes.py",
         "lang_pipeline.py", "ja_romaji.py", "asr_refine.py",
         "lyrics_fetch.py", "lyrics_match.py", "lang_id_qwen_runner.py"]

# 叙事特征：命中即认为是"开发日志"而非功能说明
NARRATIVE = re.compile(
    r"(实测|踩坑|备忘|附-\d|README|教训|2026[-/]|★|⚠|🔴|🟡|🥇|🥈|🥉|"
    r"一开始|初版|第一版|上一版|历史遗留|依据|裁决|证据|诚实|"
    r"项目里|用户|需求|动机|背景|为什么|结论|建议|验证过|"
    r"出过问题|曾经|当时|后来|改成|回退|弃用|试过|举例|"
    r"V0\.\d|附篇|§|t\d[- ][A-Za-z]|arm [A-Z]|_eval_|_probe_)")

# docstring 里允许保留的"功能段落"开头
FUNC_PARA = re.compile(
    r"^\s*(参数|返回|返回值|用法|注意|警告|CLI|示例|例：|Args|Returns|Raises|"
    r"Yields|Usage|Note|Examples|input|output)\b", re.I)

# 装饰性标记（★ ⚠️ 🔴 等）——不是功能说明
DECOR = re.compile(r"^[\s★⚠️🔴🟡🟢🥇🥈🥉✅❌·—\-]*(?=\S)")


def _clean_line(ln):
    """去掉行尾空格、行首装饰标记。"""
    ln = DECOR.sub("", ln.rstrip())
    return ln.rstrip()


def _trim_docstring(ds):
    """只留首行摘要 + 功能段落；段内的叙事行单独剔除。返回新 docstring 文本。"""
    if not ds:
        return ds
    lines = ds.split("\n")
    out = [_clean_line(lines[0])]
    para, cur = [], []
    for ln in lines[1:]:
        if ln.strip() == "":
            para.append(cur)
            cur = []
        else:
            cur.append(_clean_line(ln))
    para.append(cur)
    for p in para:
        p = [x for x in p if x]
        if not p:
            continue
        head = p[0].strip()
        if not FUNC_PARA.match(head):
            continue
        # 段内逐行剔除叙事（如整段引用某次实测、某次踩坑）
        kept = [x for x in p if not NARRATIVE.search(x)]
        if not kept:
            continue
        out.append("")
        out.extend(kept)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _comment_is_narrative(text):
    return bool(NARRATIVE.search(text))


def process(path, apply_):
    src = open(path, encoding="utf-8").read()
    lines = src.split("\n")

    # ---- 1) 行注释：找出要丢掉的注释行 ----
    drop_full = set()          # 整行注释 -> 删掉整行
    strip_tail = {}            # 行尾注释 -> 起始列
    n_c = n_c_keep = 0
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            continue
        n_c += 1
        text = tok.string
        row, col = tok.start
        if _comment_is_narrative(text):
            body = lines[row - 1]
            if body[:col].strip() == "":
                drop_full.add(row)
            else:
                strip_tail[row] = col
        else:
            n_c_keep += 1

    # ---- 2) 先按**原始行号**处理行注释（从下往上，避免行号漂移）----
    new_lines = list(lines)
    for row in sorted(drop_full, reverse=True):
        if 1 <= row <= len(new_lines):
            del new_lines[row - 1]
    for row, col in sorted(strip_tail.items(), reverse=True):
        idx = row - 1
        if 0 <= idx < len(new_lines):
            new_lines[idx] = new_lines[idx][:col].rstrip()

    # ---- 3) 再**重新解析**已改过的源码**去定位 docstring** ----
    # 踩坑：初版先替换 docstring 再用原始行号删注释 —— docstring 替换会改变行数，
    # 行号就漂了，结果删掉了不该删的代码行（`return None` 被删，语法直接坏掉）。
    src2 = "\n".join(new_lines)
    tree = ast.parse(src2)
    ds_nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.body:
                continue
            first = node.body[0]
            if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                continue
            ds_nodes.append((first.lineno, first.end_lineno, first.col_offset,
                             first.value.value))
    ds_before = sum(v.count("\n") + 1 for _a, _b, _c, v in ds_nodes)

    out_lines = list(new_lines)
    for (a, b, col, old) in sorted(ds_nodes, key=lambda x: -x[0]):
        new = _trim_docstring(old)
        if new.strip() == old.strip():
            continue
        indent = " " * col
        if "\n" not in new:
            # 只剩一行摘要 → 写成单行 docstring，别拆成三行
            out_lines[a - 1:b] = [indent + '"""' + new + '"""']
            continue
        seg = [indent + '"""' + new.split("\n")[0]]
        for ln in new.split("\n")[1:]:
            seg.append((indent + ln) if ln.strip() else "")
        seg.append(indent + '"""')
        out_lines[a - 1:b] = seg

    out = "\n".join(out_lines)
    # 注释被删后可能留下连续空行，压到最多一个
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    # 函数体开头是 docstring 且紧跟多个空行的情况，压掉多余空行
    out = re.sub(r'("""\n)\n{2,}', r"\1", out)

    ds_after = out.count('"""')  # 粗略
    return {"path": path, "lines_before": len(lines), "lines_after": out.count("\n") + 1,
            "comments": n_c, "comments_kept": n_c_keep,
            "comments_dropped": len(drop_full) + len(strip_tail),
            "docstring_lines_before": ds_before,
            "docstring_blocks_trimmed": sum(
                1 for (_a, _b, _c, v) in ds_nodes
                if _trim_docstring(v).strip() != v.strip()),
            "new_src": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    if a.apply:
        ts = time.strftime("%Y%m%d_%H%M%S")
        bak = os.path.join(ROOT, "备份", "pre_trim_%s" % ts)
        os.makedirs(bak, exist_ok=True)
        for f in FILES:
            shutil.copy2(os.path.join(ROOT, f), bak)
        print("备份 -> %s\n" % bak)

    tot = {"b": 0, "af": 0, "cd": 0, "ck": 0, "dt": 0}
    print("  %-26s %7s %7s %7s %7s %7s" % ("文件", "原行", "现行", "注释丢", "注释留", "doc改"))
    for f in FILES:
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p):
            print("  ! 缺 %s" % f)
            continue
        r = process(p, a.apply)
        assert ast.parse(r["new_src"]), "语法被破坏了：%s" % f
        tot["b"] += r["lines_before"]; tot["af"] += r["lines_after"]
        tot["cd"] += r["comments_dropped"]; tot["ck"] += r["comments_kept"]
        tot["dt"] += r["docstring_blocks_trimmed"]
        print("  %-26s %7d %7d %7d %7d %7d"
              % (f, r["lines_before"], r["lines_after"], r["comments_dropped"],
                 r["comments_kept"], r["docstring_blocks_trimmed"]))
        if a.apply:
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(r["new_src"])
    print("  %-26s %7d %7d %7d %7d %7d" % ("合计", tot["b"], tot["af"], tot["cd"], tot["ck"], tot["dt"]))
    print("\n%s" % ("已写入。请用 git diff / 逐文件复核。" if a.apply else "（dry-run，加 --apply 才写入）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
