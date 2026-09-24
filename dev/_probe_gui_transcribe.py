# -*- coding: utf-8 -*-
"""把「转谱」页的**真实 GUI 代码路径**跑一遍（不是 CLI）：点开始 → 等它跑完。

为什么要这个：CLI 走的是另一套 progress 回调，有些错误只在 GUI 的 lambda
回调用法下才暴露（例如 `TypeError: <lambda>() takes 1 positional argument
but 5 were given`）。这里直接驱动 `TranscribePage._start()`，
并且把弹窗换成记录，免得卡住。

    python lang_dev/_probe_gui_transcribe.py [--mt3] [--no-sep] [--simple]
"""
import argparse
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

ERRORS = []
INFOS = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audio', default=os.path.join(os.environ.get('TEMP', '.'), 'ts_ui_test.wav'))
    ap.add_argument('--outdir', default=os.path.join(os.environ.get('TEMP', '.'), 'ts_gui_out'))
    ap.add_argument('--mt3', action='store_true')
    ap.add_argument('--no-sep', action='store_true')
    ap.add_argument('--simple', action='store_true')
    ap.add_argument('--timeout', type=float, default=900.0)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    import ui_kit as K
    import ui_app
    import transcriber_app as TA

    # 弹窗换成记录，否则 messagebox 会阻塞住
    K.messagebox.showerror = lambda *a, **k: ERRORS.append(a)
    K.messagebox.showinfo = lambda *a, **k: INFOS.append(a)
    K.messagebox.showwarning = lambda *a, **k: ERRORS.append(a)
    K.messagebox.askyesno = lambda *a, **k: False

    root = tk.Tk()
    root.withdraw()
    shell = ui_app.Shell(root, model_path=TA.find_model(), ms_exe=TA.find_musescore(),
                         ffmpeg=TA.find_ffmpeg())
    pg = shell.pages['transcribe']
    pg.audio_var.set(args.audio)
    pg.outdir_var.set(args.outdir)
    pg.sep_var.set(not args.no_sep)
    pg.simple_var.set(args.simple)
    pg.mt3_var.set(args.mt3)

    print('音频   :', args.audio)
    print('选项   : sep=%s simple=%s mt3=%s'
          % (not args.no_sep, args.simple, args.mt3), flush=True)

    pg._start()
    t0 = time.time()
    while time.time() - t0 < args.timeout:
        root.update()
        time.sleep(0.2)
        if not pg.busy and (ERRORS or INFOS):
            break
        if not pg.busy and time.time() - t0 > 5:
            break
    root.update()

    print('\n=== 页内日志（尾部 25 行）===')
    txt = pg.log_text.get('1.0', 'end').strip().splitlines()
    for line in txt[-25:]:
        print('   ' + line)
    print('\n=== 弹窗 ===')
    print('   showinfo  :', [a[0] if a else '' for a in INFOS])
    print('   showerror :', [str(a[0]) if a else '' for a in ERRORS])
    if ERRORS:
        print('\n!! 有错误 —— 完整内容：')
        for a in ERRORS:
            print(' ' * 3 + str(a[1] if len(a) > 1 else a[0]))
    root.destroy()
    return 1 if ERRORS else 0


if __name__ == '__main__':
    sys.exit(main())
