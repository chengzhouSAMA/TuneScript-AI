# -*- coding: utf-8 -*-
"""_check_gui.py — 不开窗口的前提下检查 GUI 接线是否正确。

GUI 改动最容易出的问题是**网格 row 冲突**和**回调形参没同步**，
两者都不会在 `ast.parse` 里报出来，所以单查一遍。

用法: python lang_dev/_check_gui.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "transcriber_app.py")


def main():
    s = open(SRC, encoding="utf-8").read()
    tree = ast.parse(s)
    bad = 0

    print("=== 1) 输入区网格 row 分配 ===")
    rows = re.findall(r"text='([^']+?)：',\s*style='Card\.TLabel'\)\.grid\(\s*row=(\d+)", s)
    seen = {}
    for name, r in rows:
        mark = "  <-- 冲突!" if r in seen else ""
        if r in seen:
            bad += 1
        seen[r] = name
        print("  row=%s  %s%s" % (r, name, mark))
    if not any(n == "网易云搜索" for n, _r in rows):
        print("  ! 没找到「网易云搜索」这一行"); bad += 1

    print("\n=== 2) 控件属性是否齐全 ===")
    for attr in ("self.netease_var", "self.netease_entry"):
        ok = attr in s
        print("  %s %s" % ("✓" if ok else "✗", attr))
        bad += (0 if ok else 1)

    print("\n=== 3) _set_running 是否同步禁用新控件 ===")
    m = re.search(r"def _set_running\(self, running\):(.*?)\n    def ", s, re.S)
    body = m.group(1) if m else ""
    for ctrl in ("audio_entry", "outdir_entry", "bvid_entry", "netease_entry"):
        ok = ctrl in body
        print("  %s %s" % ("✓" if ok else "✗", ctrl))
        bad += (0 if ok else 1)

    print("\n=== 4) _start / _worker 形参是否与 Thread 调用一致 ===")
    w = re.search(r"def _worker\(self,\s*([^)]*)\)", s)
    t = re.search(r"Thread\(target=self\._worker,\s*args=\(([^)]*)\)", s)
    wp = [x.strip() for x in (w.group(1).split(",") if w else [])]
    tp = [x.strip() for x in (t.group(1).split(",") if t else [])]
    print("  _worker 形参 : %s" % wp)
    print("  Thread 实参  : %s" % tp)
    ok = len(wp) == len(tp) and all(a == b for a, b in zip(wp, tp))
    print("  %s 数量与顺序一致" % ("✓" if ok else "✗"))
    bad += (0 if ok else 1)
    if "netease" not in wp:
        print("  ! _worker 没有 netease 形参"); bad += 1

    print("\n=== 5) 三条输入路径都在 _start 里做了校验 ===")
    st = re.search(r"def _start\(self\):(.*?)\n    def ", s, re.S)
    sb = st.group(1) if st else ""
    for k in ("audio", "bvid", "netease"):
        ok = k in sb
        print("  %s %s" % ("✓" if ok else "✗", k))
        bad += (0 if ok else 1)

    print("\n=== 6) CLI 参数 ===")
    for k in ("'--netease'", "'--netease-id'", "'--quality'", "args.netease_id"):
        ok = k in s
        print("  %s %s" % ("✓" if ok else "✗", k))
        bad += (0 if ok else 1)

    print("\n=== 7) netease 模块导入都是惰性的（不拖慢启动）===")
    lazy = bool(re.search(r"def _worker.*?from netease import fetch_song", s, re.S))
    top = bool(re.search(r"^from netease import|^import netease", s, re.M))
    print("  %s 函数内导入" % ("✓" if lazy else "✗"))
    print("  %s 顶层未导入" % ("✓" if not top else "✗"))
    bad += (0 if (lazy and not top) else 1)

    print("\n=== 汇总：%d 个问题 ===" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
