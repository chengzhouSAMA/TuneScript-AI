# -*- coding: utf-8 -*-
"""新 UI 的配色、控件与页面基类。

与旧版 `transcriber_app.App` 的样式**完全独立**（旧版用的是默认样式名，
这里一律用 `N.` 前缀），所以两套 UI 切换时互不影响。

只依赖标准库的 tkinter / ttk —— 不引入新依赖，打包体积不变。
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# 配色
# ---------------------------------------------------------------------------
N_BG = '#eef1f7'
N_SIDEBAR = '#232838'
N_SIDEBAR_HOVER = '#2e3448'
N_SIDEBAR_TEXT = '#b9c0d4'
N_SIDEBAR_SEL_BG = '#2f3550'
N_SIDEBAR_SEL_FG = '#ffffff'
N_ACCENT = '#6c5ce7'
N_ACCENT_DARK = '#5a4bd1'
N_ACCENT_SOFT = '#efeaff'
N_CARD = '#ffffff'
N_TEXT = '#1f2937'
N_MUTED = '#6b7280'
N_BORDER = '#dfe3ec'
N_OK = '#0f9d58'
N_WARN = '#b45309'
N_ERR = '#dc2626'

UI_FONT = 'Microsoft YaHei UI'

AUDIO_TYPES = [
    ("音频文件", "*.wav *.flac *.ogg *.mp3 *.m4a *.aac *.wma *.ncm"),
    ("所有文件", "*.*"),
]
WAV_TYPES = [("WAV", "*.wav"), ("所有文件", "*.*")]


def setup_styles(root):
    """一次性配置 ttk 样式（只在新建主窗口时调一次）。"""
    st = ttk.Style(root)
    try:
        st.theme_use('clam')
    except tk.TclError:
        pass
    st.configure('N.TFrame', background=N_BG)
    st.configure('N.TLabel', background=N_BG, foreground=N_TEXT, font=(UI_FONT, 9))
    st.configure('NCard.TFrame', background=N_CARD)
    st.configure('NCard.TLabel', background=N_CARD, foreground=N_TEXT, font=(UI_FONT, 9))
    st.configure('NCardMuted.TLabel', background=N_CARD, foreground=N_MUTED,
                 font=(UI_FONT, 9))
    st.configure('NCard.TCheckbutton', background=N_CARD, foreground=N_TEXT,
                 font=(UI_FONT, 9))
    st.map('NCard.TCheckbutton', background=[('active', N_CARD)])
    st.configure('NCard.TRadiobutton', background=N_CARD, foreground=N_TEXT,
                 font=(UI_FONT, 9))
    st.map('NCard.TRadiobutton', background=[('active', N_CARD)])
    st.configure('NTitle.TLabel', background=N_BG, foreground=N_TEXT,
                 font=(UI_FONT, 17, 'bold'))
    st.configure('NSub.TLabel', background=N_BG, foreground=N_MUTED,
                 font=(UI_FONT, 9))
    st.configure('NSection.TLabel', background=N_CARD, foreground=N_ACCENT,
                 font=(UI_FONT, 11, 'bold'))
    st.configure('NSectionSub.TLabel', background=N_CARD, foreground=N_MUTED,
                 font=(UI_FONT, 9))
    st.configure('NPrimary.TButton', background=N_ACCENT, foreground='#ffffff',
                 borderwidth=0, relief='flat', padding=(18, 9),
                 font=(UI_FONT, 10, 'bold'))
    st.map('NPrimary.TButton',
           background=[('active', N_ACCENT_DARK), ('disabled', '#c4b5fd')],
           foreground=[('disabled', '#ffffff')])
    st.configure('NSecond.TButton', background=N_ACCENT_SOFT, foreground=N_ACCENT,
                 borderwidth=0, relief='flat', padding=(12, 6), font=(UI_FONT, 9))
    st.map('NSecond.TButton',
           background=[('active', '#e4dcff'), ('disabled', '#eef0f4')],
           foreground=[('disabled', '#9ca3af')])
    st.configure('NGhost.TButton', background=N_CARD, foreground=N_MUTED,
                 borderwidth=0, relief='flat', padding=(10, 6), font=(UI_FONT, 9))
    st.map('NGhost.TButton', background=[('active', N_ACCENT_SOFT)],
           foreground=[('active', N_ACCENT)])
    st.configure('N.TEntry', fieldbackground='#ffffff', bordercolor=N_BORDER,
                 lightcolor=N_BORDER, darkcolor=N_BORDER, padding=4,
                 font=(UI_FONT, 9))
    st.configure('N.TCombobox', padding=3, font=(UI_FONT, 9))
    st.configure('N.Treeview', background='#ffffff', fieldbackground='#ffffff',
                 foreground=N_TEXT, rowheight=22, font=(UI_FONT, 9), borderwidth=0)
    st.configure('N.Treeview.Heading', background='#f3f5fa', foreground=N_TEXT,
                 font=(UI_FONT, 9, 'bold'), relief='flat')
    st.map('N.Treeview', background=[('selected', N_ACCENT_SOFT)],
           foreground=[('selected', N_TEXT)])
    st.configure('N.Horizontal.TProgressbar', troughcolor='#e5e7eb',
                 background=N_ACCENT, bordercolor='#e5e7eb',
                 lightcolor=N_ACCENT, darkcolor=N_ACCENT)


def card(parent, pady=8):
    """一张白底卡片。返回 (卡片外框, 内容框)。"""
    outer = tk.Frame(parent, bg=N_CARD, highlightbackground=N_BORDER,
                     highlightthickness=1, bd=0)
    outer.pack(fill='x', pady=pady)
    inner = tk.Frame(outer, bg=N_CARD)
    inner.pack(fill='x', padx=14, pady=12)
    return outer, inner


def section(parent, title, sub=None):
    """卡片内的小标题（可带一行说明）。"""
    ttk.Label(parent, text=title, style='NSection.TLabel').pack(anchor='w')
    if sub:
        ttk.Label(parent, text=sub, style='NSectionSub.TLabel',
                  wraplength=760, justify='left').pack(anchor='w', pady=(2, 6))
    else:
        ttk.Label(parent, text='', style='NSectionSub.TLabel').pack(anchor='w')


def form(parent, pady=6):
    """卡片里的 grid 容器。

    tkinter 不允许同一个父容器混用 pack 与 grid —— 卡片里 `section()` 用的是 pack，
    所以所有 `field()` 行都必须放进这个 holder 里用 grid 排。
    """
    holder = tk.Frame(parent, bg=N_CARD)
    holder.pack(fill='x', pady=(pady, 0))
    holder.columnconfigure(1, weight=1)
    return holder


def field(parent, row, text, var, browse=None, width=52, kinds=None, state='normal'):
    """一行「标签 + 输入框 + 可选浏览按钮」。返回 Entry。"""
    ttk.Label(parent, text=text, style='NCard.TLabel').grid(
        row=row, column=0, sticky='w', pady=(6, 0))
    ent = ttk.Entry(parent, textvariable=var, width=width, state=state)
    ent.grid(row=row, column=1, padx=6, pady=(6, 0), sticky='ew')
    if browse:
        ttk.Button(parent, text='浏览…', style='NSecond.TButton',
                   command=lambda: _pick(var, browse, kinds)).grid(
            row=row, column=2, pady=(6, 0))
    parent.columnconfigure(1, weight=1)
    return ent


def _pick(var, kind, kinds):
    cur = var.get().strip()
    init = os.path.dirname(cur) if cur else None
    if kind == 'file':
        p = filedialog.askopenfilename(title='选择文件', filetypes=kinds or AUDIO_TYPES,
                                       initialdir=init or None)
    else:
        p = filedialog.askdirectory(title='选择目录', initialdir=init or None)
    if p:
        var.set(p)


def open_in_explorer(path):
    """在系统文件管理器里打开文件所在目录（或目录本身）。"""
    try:
        if not path:
            return False
        target = path if os.path.isdir(path) else os.path.dirname(path)
        if not os.path.isdir(target):
            return False
        if sys.platform.startswith('win'):
            os.startfile(target)                      # noqa: S606
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', target])
        else:
            subprocess.Popen(['xdg-open', target])
        return True
    except Exception:
        return False


def fmt_ms(ms):
    """毫秒 → mm:ss。"""
    try:
        s = int(ms) // 1000
    except Exception:
        return ''
    return '%d:%02d' % (s // 60, s % 60)


class Page(tk.Frame):
    """功能页基类：统一标题栏、日志面板、后台任务与忙碌态。

    子类在 `__init__` 里往 `self.body` 放卡片，用 `self.run(work)` 跑后台任务；
    `work(progress)` 在工作线程里执行，`progress(msg)` 可随时调用（线程安全）。
    """
    def __init__(self, master, app, title, subtitle=''):
        super().__init__(master, bg=N_BG)
        self.app = app
        self.q = queue.Queue()
        self.busy = False
        self.title = title
        self._build_head(title, subtitle)
        self._build_scroll()
        self._build_log()
        self.after(120, self._poll)

    # ---- 骨架 ----
    def _build_head(self, title, subtitle):
        head = tk.Frame(self, bg=N_BG)
        head.pack(fill='x', padx=16, pady=(14, 8))
        ttk.Label(head, text=title, style='NTitle.TLabel').pack(anchor='w')
        if subtitle:
            ttk.Label(head, text=subtitle, style='NSub.TLabel',
                      wraplength=820, justify='left').pack(anchor='w', pady=(2, 0))

    def _build_log(self):
        self._log_card = tk.Frame(self, bg=N_CARD, highlightbackground=N_BORDER,
                                  highlightthickness=1, bd=0)
        # 日志卡固定贴在底部，**不参与滚动** —— 滚的是上面的内容区
        self._log_card.pack(fill='x', side='bottom', padx=16, pady=(0, 12))
        outer = self._log_card
        bar = tk.Frame(outer, bg=N_CARD)
        bar.pack(fill='x', padx=10, pady=(8, 0))
        self.status = tk.StringVar(value='就绪。')
        ttk.Label(bar, textvariable=self.status, style='NCardMuted.TLabel').pack(side='left')
        ttk.Button(bar, text='清空', style='NGhost.TButton',
                   command=self.clear_log).pack(side='right')
        self._log_toggle = ttk.Button(bar, text='收起', style='NGhost.TButton',
                                      command=self.toggle_log)
        self._log_toggle.pack(side='right', padx=4)
        self.bar = ttk.Progressbar(outer, mode='indeterminate', length=600)
        self.bar.pack(fill='x', padx=10, pady=(4, 4))
        self._log_wrap = tk.Frame(outer, bg=N_CARD)
        self._log_wrap.pack(fill='both', padx=10, pady=(0, 10))
        wrap = self._log_wrap
        self.log_text = tk.Text(wrap, height=5, bg='#fbfcfe', fg=N_TEXT,
                                relief='flat', highlightthickness=1,
                                highlightbackground=N_BORDER, wrap='word',
                                font=('Consolas', 9))
        sb = ttk.Scrollbar(wrap, orient='vertical', command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.log_text.configure(state='disabled')

    # ---- 内容区滚动（滚轮 / 滚动条）----
    def _build_scroll(self):
        """内容区做成可滚动的：窗口不够高就滚动看，不用把窗口拉大。

        `self.body` 是子类往里面放卡片的那个容器，这里把它挂进 Canvas。
        日志卡不走这里（它固定在底部，见 `_build_log`）。
        """
        self._scroll_wrap = tk.Frame(self, bg=N_BG)
        self._scroll_wrap.pack(fill='both', expand=True, pady=(0, 8))
        self._vbar = ttk.Scrollbar(self._scroll_wrap, orient='vertical')
        self._canvas = tk.Canvas(self._scroll_wrap, bg=N_BG, highlightthickness=0, bd=0)
        self._vbar.configure(command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vbar.set)
        # 滚动条先 pack（右侧），Canvas 再吃掉剩余宽高
        self._vbar.pack(side='right', fill='y')
        self._canvas.pack(side='left', fill='both', expand=True)
        self.body = tk.Frame(self._canvas, bg=N_BG)
        self._body_win = self._canvas.create_window((12, 6), window=self.body,
                                                    anchor='nw')
        self.body.bind('<Configure>', self._on_body_configure)
        self._canvas.bind('<Configure>', self._on_canvas_configure)
        self._wheel_bound = False
        self._vbar_visible = True

    def _on_body_configure(self, _e=None):
        try:
            self._canvas.configure(scrollregion=self._canvas.bbox('all'))
        except Exception:
            pass
        self._sync_scrollbar()

    def _on_canvas_configure(self, e):
        # 让内容跟着窗口宽度走（横向不出现滚动条）
        try:
            self._canvas.itemconfigure(self._body_win, width=max(1, e.width - 24))
        except Exception:
            pass
        self._sync_scrollbar()

    def _sync_scrollbar(self):
        """内容装得下就把滚动条收起来，装不下再显示。"""
        try:
            need = self.body.winfo_reqheight() > self._canvas.winfo_height() + 2
        except Exception:
            return
        if need and not self._vbar_visible:
            self._vbar.pack(side='right', fill='y', before=self._canvas)
            self._vbar_visible = True
        elif not need and self._vbar_visible:
            self._vbar.pack_forget()
            self._vbar_visible = False

    # 自己管滚动的控件，别抢它们的滚轮
    _SELF_SCROLL = (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox, ttk.Spinbox)

    def bind_wheel(self, widget=None):
        """给页面里所有控件挂上滚轮事件。

        为什么要递归挂：Tk 的 `<MouseWheel>` 只发给指针底下的那个控件，
        在 `Page` 上挂一次是收不到"指针停在某个卡片/按钮上"的滚轮的。
        （子类建完控件后调用一次；`Shell` 建完页面也会替它们调一次。）
        """
        w = widget if widget is not None else self
        if not isinstance(w, self._SELF_SCROLL):
            try:
                w.bind('<MouseWheel>', self._on_wheel, add='+')
            except Exception:
                pass
        for c in w.winfo_children():
            self.bind_wheel(c)
        self._wheel_bound = True

    def _on_wheel(self, event):
        if not self.winfo_ismapped():
            return
        try:
            step = int(-event.delta / 120) or (-1 if event.delta > 0 else 1)
            self._canvas.yview_scroll(step, 'units')
        except Exception:
            pass

    def toggle_log(self):
        """收起/展开底部日志 —— 小屏幕上想多看内容区时用。"""
        if self._log_wrap.winfo_manager():
            self._log_wrap.pack_forget()
            self.bar.pack_forget()
            self._log_toggle.configure(text='展开日志')
        else:
            self.bar.pack(fill='x', padx=10, pady=(4, 4))
            self._log_wrap.pack(fill='both', padx=10, pady=(0, 10))
            self._log_toggle.configure(text='收起')

    # ---- 日志 / 状态 ----
    def log(self, msg):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', str(msg) + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def clear_log(self):
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')

    def set_status(self, msg):
        self.status.set(msg)

    def set_busy(self, on):
        self.busy = bool(on)
        if on:
            self.bar.start(12)
        else:
            self.bar.stop()
        for w in self._busy_widgets():
            try:
                w.configure(state='disabled' if on else 'normal')
            except Exception:
                pass

    _BUSY_ATTRS = ()

    def _busy_widgets(self):
        out = []
        for name in self._BUSY_ATTRS:
            w = getattr(self, name, None)
            if w is not None:
                out.append(w)
        return out

    # ---- 后台任务 ----
    def run(self, work, on_done=None, busy_msg='处理中…'):
        if self.busy:
            messagebox.showinfo('请稍候', '当前任务还没结束。')
            return
        self.set_busy(True)
        self.set_status(busy_msg)
        self._on_done = on_done
        self._work = work

        def _thread():
            try:
                res = work(lambda m: self.q.put(('log', m)))
                self.q.put(('done', res))
            except Exception as e:
                self.q.put(('error', '%s: %s' % (type(e).__name__, e)))

        threading.Thread(target=_thread, daemon=True).start()

    def async_call(self, work, on_main):
        """后台跑 work()，在主线程回调 on_main(结果)。

        与 `run()` 的区别：不占忙碌态、不弹错误框 —— 用来做"进页面就顺手查一下
        登录状态/环境"这类小事，失败也不能打断用户。
        """
        def _t():
            try:
                r = work()
            except Exception as e:
                self.q.put(('call', (on_main, '查询失败：%s: %s' % (type(e).__name__, e))))
                return
            self.q.put(('call', (on_main, r)))
        threading.Thread(target=_t, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'log':
                    self.log(payload)
                elif kind == 'call':
                    fn, arg = payload
                    try:
                        fn(arg)
                    except Exception as e:
                        self.log('回调出错：%s: %s' % (type(e).__name__, e))
                elif kind == 'done':
                    self.set_busy(False)
                    self.set_status('完成。')
                    cb = getattr(self, '_on_done', None)
                    self._on_done = None
                    if cb:
                        try:
                            cb(payload)
                        except Exception as e:
                            self.log('结果处理出错：%s: %s' % (type(e).__name__, e))
                elif kind == 'error':
                    self.set_busy(False)
                    self.set_status('失败。')
                    self.log('❌ ' + str(payload))
                    messagebox.showerror('出错', str(payload))
        except queue.Empty:
            pass
        self.after(120, self._poll)
