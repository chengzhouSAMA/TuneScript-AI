# -*- coding: utf-8 -*-
"""开一个真窗口，隔一会儿把它滚到底，用来肉眼/截图验证滚轮滚动。

    python lang_dev/_demo_scroll.py [--page netease] [--geom 1180x620+20+20] [--delay 20]
"""
import argparse
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--page', default='netease')
    ap.add_argument('--geom', default='1180x620+20+20')
    ap.add_argument('--delay', type=float, default=20.0)
    ap.add_argument('--hold', type=float, default=120.0)
    args = ap.parse_args()

    os.environ['TS_UI_PAGE'] = args.page
    os.environ['TS_UI_GEOMETRY'] = args.geom
    import ui_app
    import transcriber_app as TA

    root = tk.Tk()
    sh = ui_app.Shell(root, TA.find_model(), TA.find_musescore(), TA.find_ffmpeg())
    pg = sh.pages[args.page]

    def report(tag):
        root.update_idletasks()
        box = pg._canvas.bbox('all')
        print('[%s] 内容高=%s 视口高=%s 滚动条可见=%s yview=%s'
              % (tag, box[3] if box else '?', pg._canvas.winfo_height(),
                 pg._vbar_visible, tuple(round(v, 3) for v in pg._canvas.yview())),
              flush=True)

    def first_scroll():
        report('滚动前')
        # 这一步就是滚轮处理函数最终调用的东西（_on_wheel → yview_scroll）
        pg._on_wheel(type('E', (), {'delta': -120})())
        root.update_idletasks()
        report('滚 1 格后')
        for _i in range(24):
            pg._on_wheel(type('E', (), {'delta': -120})())
        root.update_idletasks()
        report('滚到底后')
        print('窗口保持 %ds，可截图对比。' % int(args.hold), flush=True)

    root.after(int(args.delay * 1000), first_scroll)
    root.after(int((args.delay + args.hold) * 1000), root.destroy)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
