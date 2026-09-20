# -*- coding: utf-8 -*-
"""_diff_source.py — 按字节逐行 diff 两个源码（本项目 CRLF，必须二进制安全）。

用法: python lang_dev/_diff_source.py <old.py> <new.py>
"""
import difflib
import sys


def lines(p):
    with open(p, "rb") as f:
        return f.read().decode("utf-8").splitlines(keepends=True)


def main():
    old_p, new_p = sys.argv[1], sys.argv[2]
    a, b = lines(old_p), lines(new_p)
    d = list(difflib.unified_diff(a, b, fromfile=old_p, tofile=new_p, n=3))
    n_add = sum(1 for l in d if l.startswith("+") and not l.startswith("+++"))
    n_del = sum(1 for l in d if l.startswith("-") and not l.startswith("---"))
    print("新增行 %d  删除行 %d  旧 %d 行  新 %d 行" % (n_add, n_del, len(a), len(b)))
    print("-" * 70)
    sys.stdout.write("".join(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
