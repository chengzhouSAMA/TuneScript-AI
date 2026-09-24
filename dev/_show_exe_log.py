# -*- coding: utf-8 -*-
"""把 exe 的 `--cli` 日志筛出关键行进来看。

⚠️ 为什么需要它（踩过）：PowerShell 重定向 exe 的输出时，用的是**控制台代码页**
（本机 cp936）去解码 exe 写出的 UTF-8，中文会先被换成 U+FFFD 再写进文件 ——
**字节已经丢了，事后无法还原**。所以：

  · 想看到中文，跑之前先设 `[Console]::OutputEncoding = [Text.UTF8Encoding]::new()`；
  · 没设也没关系：这个脚本会把每行的**数字**抽出来打印。补音那几行关键信息
    （几个音 / 几处空档 / 多少秒 / 成本）全是数字，照样能判定跑没跑对。

    python lang_dev/_show_exe_log.py 回归验收/logs/exe_inhuman2.log
"""
import os
import re
import sys

KEYS = ('间奏', '回炉', '音域分离', '相似度', '补音', '回滚', '采用')


def load(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'utf-16', 'utf-8', 'gbk'):
        try:
            t = raw.decode(enc)
        except Exception:
            continue
        if 'cli' in t:
            return t, enc
    return raw.decode('utf-8', 'replace'), 'utf-8/replace'


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '回归验收/logs/exe_inhuman2.log'
    if not os.path.isfile(path):
        print('找不到日志：%s' % path)
        return 2
    t, enc = load(path)
    lines = [l for l in t.splitlines() if l.startswith('[cli]')]
    print('%s（按 %s 解码，%d 行 [cli]）' % (path, enc, len(lines)))
    hits = [l for l in lines if any(k in l for k in KEYS)]
    if hits:
        print('关键行：')
        for l in hits:
            print('  ' + l)
        return 0
    # 中文丢了（U+FFFD）→ 退化成"看数字"，数字足够判定补音跑没跑
    print('中文已被控制台代码页洗掉（U+FFFD），改看数字：')
    for i, l in enumerate(lines):
        nums = re.findall(r'[0-9]+(?:\.[0-9]+)?', l)
        if nums:
            print('  %2d  %s' % (i, ' '.join(nums)))
    print('\n提示：下次跑之前执行 —— ')
    print("  [Console]::OutputEncoding = [Text.UTF8Encoding]::new()")
    return 0 if lines else 1


if __name__ == '__main__':
    sys.exit(main())
