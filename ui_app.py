# -*- coding: utf-8 -*-
"""新的多入口 UI：左侧导航 + 右侧功能页。

设计目标（用户要求）：**每个新增功能都有独立入口**，不用先"开始转谱"才能用到它。
导航共 6 项：

    转谱     本地/B站/网易云 → 五线谱 PDF + MIDI + WAV
    网易云   搜索 → 选曲 → 选音质下载；账号登录/退出（独立入口）
    B站      BV 号 → DASH 音频（独立入口）
    歌词     歌名 → 网易云/QQ 歌词 + LRC 时间轴（独立入口）
    语种     整曲语种识别（Silero / Qwen 外挂）（独立入口）
    环境     模型 / MuseScore / ffmpeg / 和弦增强 / 网易云登录状态

**只服务于扒谱的内部环节不进 UI**：日语罗马音摩拉、英语音标音节、语种分割扒谱、
强制对齐、ASR 重试、左右手八度与无人声段伴奏规则 —— 它们是转谱管线的内部实现。
音频裁剪也不进 UI（用户要求删除该界面）；`audio_crop.py` 仍在，命令行可用。

所有功能模块都是**惰性导入**（点开页面/点按钮才 import），所以新 UI 不会拖慢启动。
"""
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import ui_kit as K
from ui_kit import Page, card, field, fmt_ms, open_in_explorer, section

LEVELS = [("exhigh", '320kbps（默认，免登录一般也能拿到）'),
          ("higher", '192kbps'),
          ("standard", '128kbps'),
          ("lossless", '无损（需要会员 cookie）'),
          ("hires", 'Hi-Res（需要会员 cookie）')]
LEVEL_LABEL = {k: v.split('（')[0] for k, v in LEVELS}


def _artists(v):
    if isinstance(v, (list, tuple)):
        return ' / '.join(str(x) for x in v if x)
    return str(v or '')


def _quality_lines(info):
    """把 `netease.fetch_song()` 的 info 变成几行"人话"。

    ⚠️ 关键：`info['level']` 是**你请求的**档位，`info['resolve']['level']` 才是
    服务端**实际给的**。这两个混用会显示成「实际音质：Hi-Res」，而同一份日志的
    下一行又说"请求 hires -> 实际 exhigh 320kbps mp3" —— 自相矛盾，会让人以为
    拿到了 Hi-Res。所以这里一律以 `resolve` 为准，并把降级原因和账号状态一起说清。
    """
    out = []
    res = info.get('resolve') or {}
    want = info.get('level') or ''
    got = res.get('level') or ''
    br = int(res.get('br') or 0)
    fmt = res.get('fmt') or ''
    if got:
        line = '音质：%s' % LEVEL_LABEL.get(got, got)
        if br:
            line += '（%d kbps%s）' % (br // 1000, (' ' + fmt) if fmt else '')
        out.append(line)
        if want and got != want:
            out.append('⚠️ 请求的是 %s，服务端只给了 %s —— 无损 / Hi-Res 需要黑胶会员 cookie'
                       % (LEVEL_LABEL.get(want, want), LEVEL_LABEL.get(got, got)))
    ck = info.get('cookie') or {}
    if ck.get('ok'):
        out.append('账号：已登录 %s（%s）'
                   % (ck.get('nickname') or '?', ck.get('vip_label') or '会员状态未知'))
    else:
        out.append('账号：未登录 / cookie 未生效（%s）'
                   % (ck.get('reason') or '没有 cookie，免登录最高一般 320kbps'))
    if res.get('trial'):
        out.append('⚠️ 这是试听片段：%s' % res['trial'])
    return out


def _copy_to_clipboard(widget, text):
    """把文本放进剪贴板（扫不动二维码时，可以把链接发到手机打开）。"""
    try:
        if not text:
            messagebox.showinfo('还没有二维码', '二维码还没取到，稍等一下再点。')
            return
        widget.clipboard_clear()
        widget.clipboard_append(text)
        messagebox.showinfo('已复制', '扫码链接已复制，可以发到手机上打开：\n\n%s' % text)
    except Exception as e:
        messagebox.showerror('复制失败', str(e))


def _mask(cookie):
    """cookie 的脱敏摘要 —— **绝不回显完整 cookie**，但要够用户确认是不是那一串。"""
    if not cookie:
        return '（空）'
    parts = {}
    for seg in str(cookie).split(';'):
        if '=' in seg:
            k, v = seg.split('=', 1)
            parts[k.strip()] = v.strip()
    mu = parts.get('MUSIC_U') or ''
    if not mu:
        return '没有 MUSIC_U 字段（%d 个字段）' % len(parts)
    tail = ('…%s' % mu[-4:]) if len(mu) > 10 else ''
    return 'MUSIC_U=%s%s（%d 字符，共 %d 个字段）' % (mu[:6], tail, len(mu), len(parts))


class CookieBar:
    """可复用的「cookie 输入」一行：输入框 + 保存 / 载入当前 / 清空。

    写的是 `netease_login.save_cookie()` 那条路（= `netease_cookie.txt`，
    exe 同目录），和扫码登录、环境变量共用同一套读取逻辑。
    """

    def __init__(self, parent, page, on_saved=None):
        self.page = page
        self.on_saved = on_saved
        self.var = tk.StringVar()
        g = K.form(parent)
        ttk.Label(g, text='cookie 输入', style='NCard.TLabel').grid(
            row=0, column=0, sticky='w', pady=(4, 0))
        self.entry = ttk.Entry(g, textvariable=self.var, width=56)
        self.entry.grid(row=0, column=1, padx=6, pady=(4, 0), sticky='w')
        btns = tk.Frame(g, bg=K.N_CARD)
        btns.grid(row=0, column=2, pady=(4, 0))
        ttk.Button(btns, text='保存', style='NSecond.TButton',
                   command=self.save).pack(side='left')
        ttk.Button(btns, text='载入当前', style='NSecond.TButton',
                   command=self.load).pack(side='left', padx=4)
        ttk.Button(btns, text='清空', style='NGhost.TButton',
                   command=lambda: self.var.set('')).pack(side='left')
        ttk.Label(g, text='把 MUSIC_U=… 那一段（带不带引号都行、分号可留可去）粘进来点保存即可；'
                          '保存后写进 netease_cookie.txt。'
                          '优先级：环境变量 TS_NETEASE_COOKIE ＞ 这个文件 ＞ 打包时烤入的值。',
                  style='NCardMuted.TLabel', wraplength=740, justify='left').grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(4, 0))

    def save(self):
        txt = self.var.get().strip()
        if not txt:
            messagebox.showwarning('提示', '请先把 cookie 粘进来。')
            return
        try:
            import netease_login as NL
            path = NL.save_cookie(txt)
        except Exception as e:
            messagebox.showerror('保存失败', str(e))
            return
        try:
            import netease_login as NL
            src = NL.cookie_source()
        except Exception:
            src = '?'
        self.page.log('✅ cookie 已保存：%s' % path)
        self.page.log('   内容 %s；当前生效来源 = %s' % (_mask(txt), src))
        if src == 'env':
            self.page.log('   ⚠️ 环境变量 TS_NETEASE_COOKIE 优先级更高，'
                          '文件已写好但暂时不生效 —— 清掉环境变量或重启后才会用它。')
        self.var.set('')
        if self.on_saved:
            self.on_saved()

    def load(self):
        try:
            import netease_login as NL
            v = NL.load_cookie()
            src = NL.cookie_source()
        except Exception as e:
            messagebox.showerror('读取失败', str(e))
            return
        if not v:
            messagebox.showinfo('没有 cookie',
                                '当前既没有环境变量，也没有 netease_cookie.txt。')
            return
        self.var.set(v)
        self.page.log('已载入当前 cookie（来源 %s，%s）—— 可以直接改完再保存。'
                      % (src, _mask(v)))


def _table(parent, columns, height=6):
    """带滚动条的表格。columns = [(标题, 宽度, 对齐), ...]。"""
    wrap = tk.Frame(parent, bg=K.N_CARD)
    wrap.pack(fill='both', expand=True, pady=(6, 0))
    keys = ['c%d' % i for i in range(len(columns))]
    tv = ttk.Treeview(wrap, columns=keys, show='headings', height=height,
                      style='N.Treeview', selectmode='browse')
    for k, (title, width, anchor) in zip(keys, columns):
        tv.heading(k, text=title)
        tv.column(k, width=width, anchor=anchor, stretch=(width > 120))
    sb = ttk.Scrollbar(wrap, orient='vertical', command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    tv.pack(side='left', fill='both', expand=True)
    sb.pack(side='right', fill='y')
    return tv


# ---------------------------------------------------------------------------
# 网易云扫码登录（独立弹窗，供「网易云」页调用）
# ---------------------------------------------------------------------------
def qr_login_dialog(parent, on_success=None):
    """扫码登录窗口：二维码 + **过期自动换新** + 轮询状态日志。

    实测（`dev/_probe_qr_ttl.py`）：没人扫的时候服务端一直返回 801（等待扫码），
    只有真的放太久才会返回 800（过期）。所以这里**过期就自动换一张**，
    不再让用户"关掉重开"；同时把每次轮询的原始返回码写进小日志，
    真出问题能一眼看出是哪一步不对（而不只是"过期"三个字）。

    扫码这条路不通时，上一页的「cookie 输入」可以手工粘贴 —— 两条路等价。
    """
    try:
        import netease_login as NL
    except Exception as e:
        messagebox.showerror('错误', '登录模块不可用：%s' % e)
        return
    info = NL.account_info()
    if info.get('ok'):
        if not messagebox.askyesno(
                '已登录', '当前已登录 %s（%s）。\n要重新扫码换账号吗？'
                % (info.get('nickname'), info.get('vip_label'))):
            return
    win = tk.Toplevel(parent)
    win.title('网易云扫码登录')
    win.configure(bg=K.N_BG)
    win.transient(parent)
    win.resizable(False, False)

    cv = tk.Canvas(win, width=260, height=260, bg='white', highlightthickness=0)
    cv.pack(padx=18, pady=(18, 6))
    st = tk.StringVar(value='正在获取二维码…')
    ttk.Label(win, textvariable=st, style='N.TLabel', wraplength=300,
              justify='center').pack(padx=18)
    log = tk.Text(win, height=5, width=42, bg='#fbfcfe', fg=K.N_MUTED, relief='flat',
                  highlightthickness=1, highlightbackground=K.N_BORDER,
                  font=('Consolas', 8), wrap='word')
    log.pack(padx=18, pady=(8, 6))
    log.configure(state='disabled')
    bar = tk.Frame(win, bg=K.N_BG)
    bar.pack(padx=18, pady=(0, 16))

    state = {'unikey': None, 't0': 0.0, 'gen': 0}

    def add_log(line):
        try:
            log.configure(state='normal')
            log.insert('end', line + '\n')
            log.see('end')
            log.configure(state='disabled')
        except Exception:
            pass

    def draw(matrix):
        cv.delete('all')
        n = len(matrix)
        cell = max(1, 260 // n)
        off = (260 - cell * n) // 2
        for y, row in enumerate(matrix):
            for x, v in enumerate(row):
                if v:
                    cv.create_rectangle(off + x * cell, off + y * cell,
                                        off + (x + 1) * cell, off + (y + 1) * cell,
                                        fill='black', outline='')

    def new_qr():
        """取一张新二维码。gen 是代次，旧的轮询回来会被丢掉。"""
        state['gen'] += 1
        gen = state['gen']
        st.set('正在获取二维码…')
        cv.delete('all')

        def work():
            try:
                unikey, msg = NL.generate_qr_key()
            except Exception as e:
                unikey, msg = None, '获取二维码失败：%s: %s' % (type(e).__name__, e)
            try:
                win.after(0, lambda: apply(gen, unikey, msg))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def apply(gen, unikey, msg):
        if gen != state['gen'] or not win.winfo_exists():
            return
        if not unikey:
            st.set('%s\n（点「刷新二维码」重试；也可以回到上一页用 cookie 输入粘）' % msg)
            add_log('取 key 失败：%s' % msg)
            return
        state['unikey'] = unikey
        state['t0'] = time.time()
        try:
            draw(NL.qr_matrix(NL.qr_url(unikey)))
        except Exception as e:
            st.set('生成二维码图片失败：%s' % type(e).__name__)
            add_log('画二维码失败：%s' % e)
            return
        st.set('请用网易云音乐 App 扫码（没人扫就一直有效，真过期会自动换新的）')
        add_log('新二维码 %s' % unikey)
        win.after(1500, lambda: poll(gen, unikey))

    def poll(gen, unikey):
        if gen != state['gen'] or not win.winfo_exists():
            return
        try:
            code, cookie, msg = NL.poll_qr_key(unikey)
        except Exception as e:
            st.set('轮询失败（%s），2.5 秒后重试…' % type(e).__name__)
            add_log('轮询异常：%s' % e)
            win.after(2500, lambda: poll(gen, unikey))
            return
        el = time.time() - state['t0']
        if code == NL.ST_OK:
            if not cookie:
                # 803 却说没凭据：多半是响应头里的 Set-Cookie 没被收上来。
                # 说清楚、换一张重来，别让用户看到"什么都没发生"。
                st.set('扫码成功了，但没拿到登录凭据：%s\n正在重试…' % msg)
                add_log('code=803 但没有 cookie：%s' % msg)
                win.after(500, new_qr)
                return
            try:
                NL.save_cookie(cookie)
                st.set('登录成功，已保存到 netease_cookie.txt')
                add_log('code=803 登录成功；cookie %s' % _mask(cookie))
                if on_success:
                    on_success()
            except Exception as e:
                st.set('登录成功，但保存失败：%s' % e)
                add_log('code=803 但保存失败：%s' % e)
            win.after(1500, win.destroy)
            return
        if code == NL.ST_EXPIRED:
            add_log('%5.1fs code=800 过期 → 自动换新' % el)
            st.set('这张过期了，正在换一张…')
            win.after(300, new_qr)
            return
        if code == NL.ST_SCANNED:
            st.set('已扫码，请在手机上确认')
            add_log('%5.1fs code=802 已扫码待确认' % el)
        elif code == NL.ST_WAIT:
            st.set('等待扫码…（已 %d 秒）' % int(el))
            if int(el) % 20 < 2:                 # 20 秒打一行心跳，别刷屏
                add_log('%5.1fs code=801 等待扫码' % el)
        else:
            st.set(msg)
            add_log('%5.1fs code=%s %s' % (el, code, msg))
        win.after(2000, lambda: poll(gen, unikey))

    ttk.Button(bar, text='刷新二维码', style='NSecond.TButton',
               command=new_qr).pack(side='left')
    ttk.Button(bar, text='复制扫码链接', style='NSecond.TButton',
               command=lambda: _copy_to_clipboard(win, NL.qr_url(state['unikey'] or ''))
               ).pack(side='left', padx=6)
    ttk.Button(bar, text='关闭', style='NGhost.TButton',
               command=win.destroy).pack(side='left')

    new_qr()


# ---------------------------------------------------------------------------
# 1) 转谱
# ---------------------------------------------------------------------------
class TranscribePage(Page):
    _BUSY_ATTRS = ('start_btn', 'audio_entry', 'bvid_entry', 'ne_entry', 'outdir_entry')

    def __init__(self, master, app):
        super().__init__(master, app, '转谱',
                         '本地音频 / B站 BV 号 / 网易云搜索 → 五线谱 PDF + MIDI + 钢琴演奏 WAV')
        self.audio_var = tk.StringVar()
        self.bvid_var = tk.StringVar()
        self.ne_var = tk.StringVar()
        self.outdir_var = tk.StringVar()

        _o, c = card(self.body)
        section(c, '输入', '三种来源任选其一：填了 BV 号或网易云搜索词就不用再选本地文件。')
        g = K.form(c)
        self.audio_entry = field(g, 1, '音频文件', self.audio_var, 'file',
                                 kinds=K.AUDIO_TYPES)
        self.bvid_entry = field(g, 2, 'B站 BV 号', self.bvid_var)
        self.ne_entry = field(g, 3, '网易云搜索', self.ne_var)
        ttk.Button(g, text='cookie…', style='NSecond.TButton',
                   command=lambda: self.app.show('netease')).grid(row=3, column=2,
                                                                  pady=(6, 0))
        self.outdir_entry = field(g, 4, '输出目录', self.outdir_var, 'dir')

        _o2, c2 = card(self.body)
        section(c2, '选项', '默认值已经够用；不确定就别改。')
        self.sep_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(c2, style='NCard.TCheckbutton', variable=self.sep_var,
                        text='人声/伴奏分离分析（更准更干净，约多花几分钟；'
                             '分离出的音轨会一并保存）').pack(anchor='w', pady=2)
        self.mt3_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(c2, style='NCard.TCheckbutton', variable=self.mt3_var,
                        text='AI 智能识别增强（重点识别和弦，比 MT3 快约 6 倍）').pack(anchor='w', pady=2)
        self.simple_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(c2, style='NCard.TCheckbutton', variable=self.simple_var,
                        text='简洁模式（不分轨、经典流程，更快更稳定）').pack(anchor='w', pady=2)

        _o3, c3 = card(self.body, pady=(8, 0))
        row = tk.Frame(c3, bg=K.N_CARD)
        row.pack(anchor='w')
        self.start_btn = ttk.Button(row, text='开始转谱', style='NPrimary.TButton',
                                    command=self._start)
        self.start_btn.pack(side='left')
        self.open_btn = ttk.Button(row, text='打开输出目录', style='NSecond.TButton',
                                   state='disabled',
                                   command=lambda: open_in_explorer(self.outdir_var.get()))
        self.open_btn.pack(side='left', padx=8)

    def _start(self):
        audio = self.audio_var.get().strip()
        bvid = self.bvid_var.get().strip()
        query = self.ne_var.get().strip()
        outdir = self.outdir_var.get().strip()
        if not audio and not bvid and not query:
            messagebox.showwarning('提示', '请选择音频文件，或填 B站 BV 号 / 网易云搜索词。')
            return
        if audio and not os.path.isfile(audio):
            messagebox.showwarning('提示', '音频文件不存在。')
            return
        if not outdir:
            if audio:
                outdir = os.path.dirname(audio)
            else:
                outdir = filedialog.askdirectory(title='选择输出目录')
                if not outdir:
                    return
            self.outdir_var.set(outdir)
        if not os.path.isdir(outdir):
            messagebox.showwarning('提示', '输出目录不存在。')
            return
        if not self.app.model_path:
            messagebox.showerror('错误', '未找到 AI 识别模型（打包异常）。')
            return
        if not self.app.ms_exe:
            messagebox.showerror('错误', '未找到 MuseScore4.exe，请先安装 MuseScore 4。')
            return
        self.open_btn.configure(state='disabled')
        self.run(lambda p: self._work(audio, bvid, query, outdir, p),
                 self._done, '正在转谱…')

    def _work(self, audio, bvid, query, outdir, progress):
        from transcriber_app import run_pipeline
        if not audio and bvid:
            from bilibili import fetch_audio
            progress('正在按 BV 号获取 B站音频…')
            audio, _t, _d = fetch_audio(bvid,
                                        save_path=os.path.join(outdir, bvid + '.m4a'),
                                        progress=progress)
            progress('已获取：%s' % audio)
        elif not audio and query:
            from netease import fetch_song
            progress('正在搜索网易云音乐…')
            audio, info = fetch_song(query=query, out_dir=outdir,
                                     level=os.environ.get('TS_NETEASE_LEVEL', 'exhigh'),
                                     progress=progress)
            if not audio:
                raise RuntimeError('网易云下载失败：%s' % (info.get('reason') or '未知原因'))
            progress('已下载：%s' % audio)
        return run_pipeline(audio, outdir, self.app.model_path, self.app.ms_exe,
                            self.app.ffmpeg, progress,
                            use_separation=self.sep_var.get(),
                            simple_mode=self.simple_var.get(),
                            use_mt3=self.mt3_var.get())

    def _done(self, r):
        pdfs = r.get('pdf') or []
        if isinstance(pdfs, str):
            pdfs = [pdfs]
        lines = ['五线谱：%s' % p for p in pdfs]
        lines.append('钢琴演奏：%s' % r.get('wav'))
        lines.append('MIDI：%s' % r.get('midi'))
        for line in lines:
            self.log('   ' + line)
        names = {'vocals': '人声', 'drums': '鼓', 'bass': '贝斯', 'other': '其他',
                 'piano': '钢琴', 'guitar': '吉他'}
        for k, v in (r.get('stems') or {}).items():
            self.log('   分离音轨 %s：%s' % (names.get(k, k), v))
        self.log('✅ 完成。')
        self.open_btn.configure(state='normal')
        messagebox.showinfo('完成', '已生成：\n' + '\n'.join(lines))


# ---------------------------------------------------------------------------
# 2) 网易云（搜索下载 + 登录）—— 独立入口
# ---------------------------------------------------------------------------
class NeteasePage(Page):
    _BUSY_ATTRS = ('search_btn', 'dl_btn', 'kw_entry', 'outdir_entry')

    def __init__(self, master, app):
        super().__init__(master, app, '网易云音乐',
                         '搜索 → 选曲 → 选音质下载；账号登录与状态也在这里（独立入口）')
        self.kw_var = tk.StringVar()
        self.outdir_var = tk.StringVar()
        self.status_var = tk.StringVar(value='正在查询登录状态…')
        self.results = []

        _o, c = card(self.body)
        section(c, '账号', '免登录一般到 320kbps；欧美版权曲可能只给 30~45 秒试听。')
        ttk.Label(c, textvariable=self.status_var, style='NCard.TLabel',
                  wraplength=760, justify='left').pack(anchor='w', pady=(0, 6))
        # cookie 手动输入：扫码不好用（或想直接换号）时的正路
        self.cookie_bar = CookieBar(c, self, on_saved=self.refresh_account)
        row = tk.Frame(c, bg=K.N_CARD)
        row.pack(anchor='w', pady=(8, 0))
        ttk.Button(row, text='刷新状态', style='NSecond.TButton',
                   command=self.refresh_account).pack(side='left')
        ttk.Button(row, text='扫码登录…', style='NSecond.TButton',
                   command=lambda: qr_login_dialog(self, self.refresh_account)).pack(
            side='left', padx=6)
        ttk.Button(row, text='退出登录', style='NSecond.TButton',
                   command=self._logout).pack(side='left')

        _o2, c2 = card(self.body)
        section(c2, '搜索', '填歌名或「歌手 歌名」。')
        g2 = K.form(c2)
        self.kw_entry = field(g2, 1, '关键词', self.kw_var)
        r2 = tk.Frame(g2, bg=K.N_CARD)
        r2.grid(row=2, column=1, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Label(r2, text='条数：', style='NCard.TLabel').pack(side='left')
        self.limit_var = tk.StringVar(value='10')
        ttk.Combobox(r2, textvariable=self.limit_var, width=5, state='readonly',
                     style='N.TCombobox',
                     values=['5', '10', '20', '30']).pack(side='left', padx=(0, 10))
        self.search_btn = ttk.Button(r2, text='搜索', style='NPrimary.TButton',
                                     command=self._search)
        self.search_btn.pack(side='left')
        self.tv = _table(c2, [('', 34, 'center'), ('歌名', 250, 'w'),
                              ('歌手', 190, 'w'), ('专辑', 190, 'w'),
                              ('时长', 60, 'center')])
        self.tv.bind('<Double-1>', lambda e: self._download())

        _o3, c3 = card(self.body, pady=(8, 0))
        section(c3, '下载', '无损/Hi-Res 需要会员 cookie，否则服务端会静默降级。')
        r3 = tk.Frame(c3, bg=K.N_CARD)
        r3.pack(anchor='w')
        ttk.Label(r3, text='音质：', style='NCard.TLabel').pack(side='left')
        self.level_var = tk.StringVar(value='320kbps（默认，免登录一般也能拿到）')
        ttk.Combobox(r3, textvariable=self.level_var, width=36, state='readonly',
                     style='N.TCombobox',
                     values=[v for _k, v in LEVELS]).pack(side='left')
        self.outdir_entry = field(K.form(c3), 1, '输出目录', self.outdir_var, 'dir')
        r3b = tk.Frame(c3, bg=K.N_CARD)
        r3b.pack(anchor='w', pady=(8, 0))
        self.dl_btn = ttk.Button(r3b, text='下载所选', style='NPrimary.TButton',
                                 command=self._download)
        self.dl_btn.pack(side='left')
        ttk.Button(r3b, text='打开输出目录', style='NSecond.TButton',
                   command=lambda: open_in_explorer(self.outdir_var.get())).pack(
            side='left', padx=8)

        self.after(400, self.refresh_account)

    # ---- 账号 ----
    def refresh_account(self):
        def work():
            import netease_login as NL
            info = NL.account_info()
            src = NL.cookie_source() or '无'
            return (info, NL.quality_hint(), src)

        def done(r):
            if isinstance(r, str):          # 出错时 async_call 直接回字符串
                self.status_var.set(r)
                return
            info, hint, src = r
            self.status_var.set('cookie 来源：%s\n%s' % (src, hint))

        self.async_call(work, done)

    def _logout(self):
        try:
            import netease_login as NL
            ok = NL.logout()
        except Exception as e:
            messagebox.showerror('错误', '退出失败：%s' % e)
            return
        self.log('已退出登录。' if ok else '本来就没有本地 cookie 文件。')
        self.refresh_account()

    # ---- 搜索 ----
    def _search(self):
        kw = self.kw_var.get().strip()
        if not kw:
            messagebox.showwarning('提示', '请先填搜索关键词。')
            return
        try:
            limit = int(self.limit_var.get())
        except ValueError:
            limit = 10
        self.run(lambda p: self._do_search(kw, limit, p), self._show_results, '正在搜索…')

    def _do_search(self, kw, limit, progress):
        from netease import search
        progress('正在搜索网易云：%s' % kw)
        return search(kw, limit=limit)

    def _show_results(self, rows):
        self.results = rows or []
        self.tv.delete(*self.tv.get_children())
        for i, s in enumerate(self.results):
            self.tv.insert('', 'end', iid=str(i), values=(
                i + 1, s.get('name') or s.get('title') or '',
                _artists(s.get('artists')), s.get('album') or '',
                fmt_ms(s.get('duration_ms') or 0)))
        self.log('搜到 %d 条。双击某行即可下载。' % len(self.results))
        if not self.results:
            messagebox.showinfo('没有结果', '没搜到，换个关键词试试。')

    # ---- 下载 ----
    def _picked(self):
        sel = self.tv.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在结果里选一首（双击也行）。')
            return None
        return self.results[int(sel[0])]

    def _download(self):
        song = self._picked()
        if not song:
            return
        outdir = self.outdir_var.get().strip() or filedialog.askdirectory(
            title='选择下载目录')
        if not outdir:
            return
        self.outdir_var.set(outdir)
        level = 'exhigh'
        for k, v in LEVELS:
            if v == self.level_var.get():
                level = k
        self.run(lambda p: self._do_download(song, outdir, level, p),
                 self._download_done, '正在下载…')

    def _do_download(self, song, outdir, level, progress):
        from netease import fetch_song
        progress('正在取直链并下载（%s）…' % LEVEL_LABEL.get(level, level))
        path, info = fetch_song(song_id=song.get('id'), out_dir=outdir,
                                level=level, progress=progress)
        if not path:
            raise RuntimeError('下载失败：%s' % (info.get('reason') or '未知原因'))
        return path, info

    def _download_done(self, r):
        path, info = r
        self.log('✅ 已下载：%s' % os.path.normpath(path))
        for line in _quality_lines(info):
            self.log('   ' + line)
        if info.get('reason'):
            self.log('   说明：%s' % info['reason'])
        messagebox.showinfo('完成', '已下载：\n%s\n\n%s'
                            % (os.path.normpath(path),
                               '\n'.join(_quality_lines(info))))


# ---------------------------------------------------------------------------
# 3) B站音频 —— 独立入口
# ---------------------------------------------------------------------------
class BilibiliPage(Page):
    _BUSY_ATTRS = ('go_btn', 'bv_entry', 'outdir_entry')

    def __init__(self, master, app):
        super().__init__(master, app, 'B站音频',
                         '按 BV 号抓取音频流（独立入口，不转谱也可以只下载）')
        self.bv_var = tk.StringVar()
        self.outdir_var = tk.StringVar()
        self.result_var = tk.StringVar(value='（还没有结果）')

        _o, c = card(self.body)
        section(c, '参数', 'BV 号形如 BV1xx411c7mD，从视频链接里取。')
        g = K.form(c)
        self.bv_entry = field(g, 1, 'BV 号', self.bv_var)
        self.outdir_entry = field(g, 2, '保存目录', self.outdir_var, 'dir')
        r = tk.Frame(c, bg=K.N_CARD)
        r.pack(anchor='w', pady=(8, 0))
        self.go_btn = ttk.Button(r, text='获取音频', style='NPrimary.TButton',
                                 command=self._go)
        self.go_btn.pack(side='left')
        ttk.Button(r, text='打开保存目录', style='NSecond.TButton',
                   command=lambda: open_in_explorer(self.outdir_var.get())).pack(
            side='left', padx=8)

        _o2, c2 = card(self.body)
        section(c2, '结果')
        ttk.Label(c2, textvariable=self.result_var, style='NCardMuted.TLabel',
                  wraplength=760, justify='left').pack(anchor='w')

    def _go(self):
        bv = self.bv_var.get().strip()
        outdir = self.outdir_var.get().strip()
        if not bv:
            messagebox.showwarning('提示', '请填 BV 号。')
            return
        if not outdir:
            outdir = filedialog.askdirectory(title='选择保存目录')
            if not outdir:
                return
            self.outdir_var.set(outdir)
        self.run(lambda p: self._work(bv, outdir, p), self._done, '正在获取 B站音频…')

    def _work(self, bv, outdir, progress):
        from bilibili import fetch_audio
        path, title, dur = fetch_audio(bv, save_path=os.path.join(outdir, bv + '.m4a'),
                                       progress=progress)
        return path, title, dur

    def _done(self, r):
        path, title, dur = r
        txt = '标题：%s\n时长：%s\n文件：%s' % (title or '（未知）',
                                              ('%.1f 秒' % dur) if dur else '（未知）', path)
        self.result_var.set(txt)
        self.log('✅ 已保存：%s' % path)


# ---------------------------------------------------------------------------
# 4) 歌词 —— 独立入口
# ---------------------------------------------------------------------------
class LyricsPage(Page):
    _BUSY_ATTRS = ('go_btn', 'kw_entry', 'save_btn')

    def __init__(self, master, app):
        super().__init__(master, app, '歌词',
                         '搜歌取词（网易云 / QQ，无需 cookie）；带 LRC 时间轴，可另存 .lrc')
        self.kw_var = tk.StringVar()
        self.src_var = tk.StringVar(value='两个都查')
        self.limit_var = tk.StringVar(value='6')
        self.cands = []

        _o, c = card(self.body)
        section(c, '搜索', '可以直接填歌名；也可以从音频文件名自动解析。')
        g = K.form(c)
        self.kw_entry = field(g, 1, '关键词', self.kw_var)
        r = tk.Frame(g, bg=K.N_CARD)
        r.grid(row=2, column=1, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Label(r, text='来源：', style='NCard.TLabel').pack(side='left')
        ttk.Combobox(r, textvariable=self.src_var, width=12, state='readonly',
                     style='N.TCombobox',
                     values=['两个都查', '只查网易云', '只查QQ']).pack(side='left')
        ttk.Label(r, text='  条数：', style='NCard.TLabel').pack(side='left')
        ttk.Combobox(r, textvariable=self.limit_var, width=5, state='readonly',
                     style='N.TCombobox',
                     values=['4', '6', '10']).pack(side='left', padx=(0, 10))
        self.go_btn = ttk.Button(r, text='搜索歌词', style='NPrimary.TButton',
                                 command=self._go)
        self.go_btn.pack(side='left')
        ttk.Button(r, text='从音频文件名解析…', style='NSecond.TButton',
                   command=self._from_audio).pack(side='left', padx=6)

        _o2, c2 = card(self.body)
        section(c2, '候选', '双击查看；选中后点右下角保存。')
        self.tv = _table(c2, [('来源', 80, 'center'), ('歌名', 260, 'w'),
                              ('歌手', 190, 'w'), ('有效行', 70, 'center'),
                              ('首句时间', 90, 'center')], height=5)
        self.tv.bind('<<TreeviewSelect>>', lambda e: self._preview())
        self.tv.bind('<Double-1>', lambda e: self._preview())

        _o3, c3 = card(self.body, pady=(8, 0))
        section(c3, '预览')
        self.text = tk.Text(c3, height=8, bg='#fbfcfe', fg=K.N_TEXT, relief='flat',
                            highlightthickness=1, highlightbackground=K.N_BORDER,
                            wrap='word', font=(K.UI_FONT, 9))
        sb = ttk.Scrollbar(c3, orient='vertical', command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.save_btn = ttk.Button(c3, text='保存为 .lrc', style='NSecond.TButton',
                                   command=self._save)

    def _from_audio(self):
        p = filedialog.askopenfilename(title='选择音频文件', filetypes=K.AUDIO_TYPES)
        if not p:
            return
        try:
            from lyrics_fetch import parse_song_meta
            meta = parse_song_meta(p)
            q = ' '.join(x for x in (meta.get('artists'), meta.get('title')) if x)
        except Exception:
            q = os.path.splitext(os.path.basename(p))[0]
        self.kw_var.set(q or os.path.splitext(os.path.basename(p))[0])
        self.log('从文件名解析出搜索词：%s' % self.kw_var.get())

    def _go(self):
        kw = self.kw_var.get().strip()
        if not kw:
            messagebox.showwarning('提示', '请填关键词。')
            return
        prov = {'只查网易云': ('netease',), '只查QQ': ('qq',)}.get(
            self.src_var.get(), ('netease', 'qq'))
        try:
            limit = int(self.limit_var.get())
        except ValueError:
            limit = 6
        self.run(lambda p: self._work(kw, limit, prov, p), self._show, '正在取歌词…')

    def _work(self, kw, limit, prov, progress):
        from lyrics_fetch import search_and_fetch
        progress('正在搜索并取回歌词（来源 %s）…' % '/'.join(prov))
        return search_and_fetch(kw, limit=limit, providers=prov, fetch_lyric=True)

    def _show(self, cands):
        self.cands = cands or []
        self.tv.delete(*self.tv.get_children())
        for i, h in enumerate(self.cands):
            self.tv.insert('', 'end', iid=str(i), values=(
                h.get('provider'), h.get('title') or '',
                _artists(h.get('artists')), h.get('n_ts_lines') or h.get('n_lines') or 0,
                ('%.2f s' % h['offset_hint']) if h.get('offset_hint') else '—'))
        self.log('取到 %d 条带歌词的候选。' % len(self.cands))
        if not self.cands:
            messagebox.showinfo('没有结果', '没取到歌词，换个关键词或来源试试。')

    def _cur(self):
        sel = self.tv.selection()
        return self.cands[int(sel[0])] if sel else None

    def _preview(self):
        h = self._cur()
        if not h:
            return
        lines = h.get('lines') or []
        self.text.delete('1.0', 'end')
        self.text.insert('end', '\n'.join(lines[:40]))
        if len(lines) > 40:
            self.text.insert('end', '\n…（共 %d 行）' % len(lines))

    def _save(self):
        h = self._cur()
        if not h:
            messagebox.showwarning('提示', '请先选一条候选。')
            return
        dst = filedialog.asksaveasfilename(
            title='保存歌词', defaultextension='.lrc',
            initialfile='%s - %s.lrc' % (_artists(h.get('artists')),
                                         h.get('title') or 'lyrics'),
            filetypes=[('LRC 歌词', '*.lrc'), ('文本', '*.txt')])
        if not dst:
            return
        txt = h.get('lrc') or '\n'.join(h.get('lines') or [])
        with open(dst, 'w', encoding='utf-8') as f:
            f.write(txt)
        self.log('✅ 已保存：%s' % dst)


# ---------------------------------------------------------------------------
# 5) 语种识别 —— 独立入口
# ---------------------------------------------------------------------------
class LangIdPage(Page):
    _BUSY_ATTRS = ('go_btn', 'src_entry')

    def __init__(self, master, app):
        super().__init__(master, app, '语种识别',
                         '整曲/逐窗判断演唱语言（Silero 内置；Qwen 走外挂，更准但更慢）')
        self.src_var = tk.StringVar()
        self.backend_var = tk.StringVar(value='auto')
        self.win_var = tk.StringVar(value='4')
        self.hop_var = tk.StringVar(value='2')
        self.verdict_var = tk.StringVar(value='（还没有结果）')

        _o, c = card(self.body)
        section(c, '参数', '建议传「人声轨」而不是整曲混音；纯器乐会给出不确定结论。')
        g = K.form(c)
        self.src_entry = field(g, 1, '音频文件', self.src_var, 'file', kinds=K.AUDIO_TYPES)
        r = tk.Frame(g, bg=K.N_CARD)
        r.grid(row=2, column=1, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Label(r, text='后端：', style='NCard.TLabel').pack(side='left')
        ttk.Combobox(r, textvariable=self.backend_var, width=12, state='readonly',
                     style='N.TCombobox',
                     values=['auto', 'silero_onnx', 'qwen3asr']).pack(side='left')
        ttk.Label(r, text='　窗口(秒)：', style='NCard.TLabel').pack(side='left')
        ttk.Entry(r, textvariable=self.win_var, width=6).pack(side='left')
        ttk.Label(r, text='　步长(秒)：', style='NCard.TLabel').pack(side='left')
        ttk.Entry(r, textvariable=self.hop_var, width=6).pack(side='left')
        self.go_btn = ttk.Button(r, text='开始识别', style='NPrimary.TButton',
                                 command=self._go)
        self.go_btn.pack(side='left', padx=8)

        _o2, c2 = card(self.body)
        section(c2, '结果')
        ttk.Label(c2, textvariable=self.verdict_var, style='NCard.TLabel',
                  wraplength=760, justify='left').pack(anchor='w')
        self.tv = _table(c2, [('#', 50, 'center'), ('起', 80, 'center'),
                              ('止', 80, 'center'), ('语种', 160, 'w'),
                              ('置信度', 80, 'center')], height=7)

    def _go(self):
        src = self.src_var.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showwarning('提示', '请选择存在的音频文件。')
            return
        try:
            win = float(self.win_var.get())
            hop = float(self.hop_var.get())
        except ValueError:
            messagebox.showwarning('提示', '窗口/步长必须是数字。')
            return
        backend = self.backend_var.get()
        self.run(lambda p: self._work(src, backend, win, hop, p), self._done,
                 '正在识别语种…')

    def _work(self, src, backend, win, hop, progress):
        import lang_id as LI
        progress('正在加载语种检测器（后端 %s）…' % backend)
        det = LI.LanguageDetector(backend=None if backend == 'auto' else backend)
        if not det.available():
            raise RuntimeError('语种检测器不可用：%s' % (det.reason or '未知原因'))
        progress('正在滑窗识别（窗口 %.1fs / 步长 %.1fs）…' % (win, hop))
        wins = det.classify_windows(src, win=win, hop=hop)
        song = det.detect_song(src, win=win, hop=hop)
        return wins, song

    def _done(self, r):
        wins, song = r
        if not song.get('ok'):
            self.verdict_var.set('整曲判定：不可信 —— %s' % (song.get('reason') or '未通过门控'))
        else:
            self.verdict_var.set(
                '整曲判定：%s（%s）　置信度 %s　有效窗 %s / %s'
                % (song.get('name') or '?', song.get('code') or '?',
                   ('%.2f' % song['prob']) if song.get('prob') is not None else '—',
                   song.get('n_voiced'), song.get('n_windows')))
        self.tv.delete(*self.tv.get_children())
        for i, w in enumerate(wins or []):
            self.tv.insert('', 'end', values=(
                i + 1,
                ('%.1f' % w['t0']) if w.get('t0') is not None else '—',
                ('%.1f' % w['t1']) if w.get('t1') is not None else '—',
                '%s（%s）%s' % (w.get('name') or '?', w.get('code') or '?',
                               '' if w.get('ok', True) else ' · 未过门控'),
                ('%.2f' % w['prob']) if w.get('prob') is not None else '—'))
        self.log('✅ 识别完成：%d 个窗。' % len(wins or []))


# ---------------------------------------------------------------------------
# 6) 环境
# ---------------------------------------------------------------------------
class EnvPage(Page):
    def __init__(self, master, app):
        super().__init__(master, app, '环境',
                         '识别模型 / MuseScore / ffmpeg / 和弦增强 / 网易云登录状态')
        self.text_var = tk.StringVar(value='正在检测…')

        _o, c = card(self.body)
        section(c, '依赖状态', '缺 MuseScore 就只能出 MIDI，出不了五线谱 PDF。')
        ttk.Label(c, textvariable=self.text_var, style='NCard.TLabel',
                  wraplength=820, justify='left').pack(anchor='w', pady=(0, 6))
        r = tk.Frame(c, bg=K.N_CARD)
        r.pack(anchor='w')
        ttk.Button(r, text='重新检测', style='NSecond.TButton',
                   command=self.refresh).pack(side='left')

        _o2, c2 = card(self.body)
        section(c2, '说明', '这些开关默认就是对的；只有排查问题时才需要动。')
        tips = [
            'TS_UI=classic  —— 换回旧版单窗口界面',
            'TS_LANG_SEG=1  —— 转谱时按语种分段识别（默认关）',
            'TS_HAND_GAP=0  —— 关掉「右手人声与左手伴奏差一个八度」',
            'TS_ACCOMP_BOOST=0 —— 关掉「无人声段加强伴奏识别」',
            'TS_NETEASE_COOKIE —— 直接给环境变量比放文件优先级更高',
        ]
        for t in tips:
            ttk.Label(c2, text=t, style='NCardMuted.TLabel',
                      font=('Consolas', 9)).pack(anchor='w', pady=1)
        self.after(300, self.refresh)

    def refresh(self):
        def work():
            from transcriber_app import find_btd_checkpoint, find_ffmpeg, find_model, find_musescore
            lines = []
            lines.append('AI 识别模型：%s' % ('已就绪' if find_model() else '未找到（打包异常）'))
            ms = find_musescore()
            lines.append('MuseScore：%s' % (ms if ms else '未找到 —— 请安装 MuseScore 4'))
            ff = find_ffmpeg()
            lines.append('ffmpeg：%s' % (ff if ff else '未找到 —— 仅支持 WAV/FLAC/OGG'))
            lines.append('和弦增强(ByteDance)：%s'
                         % ('可用' if find_btd_checkpoint() else '未找到（和弦轨退回快速引擎）'))
            try:
                import lang_id as LI
                lines.append('语种识别后端：auto → %s（可用：%s）'
                             % (LI.auto_backend_name(), ', '.join(LI.list_backends())))
            except Exception as e:
                lines.append('语种识别：不可用（%s）' % type(e).__name__)
            try:
                import netease_login as NL
                info = NL.account_info()
                src = NL.cookie_source() or '无'
                if info.get('ok'):
                    lines.append('网易云：已登录 %s（cookie 来源 %s）'
                                 % (info.get('nickname'), src))
                else:
                    lines.append('网易云：未登录（cookie 来源 %s）—— %s'
                                 % (src, info.get('reason') or '免登录最高 320kbps'))
            except Exception as e:
                lines.append('网易云：模块不可用（%s）' % type(e).__name__)
            return '\n'.join(lines)

        def done(r):
            self.text_var.set(r if isinstance(r, str) else str(r))

        self.async_call(work, done)


# ---------------------------------------------------------------------------
# 外壳
# ---------------------------------------------------------------------------
NAV = [
    ('transcribe', '转谱', TranscribePage),
    ('netease', '网易云', NeteasePage),
    ('bilibili', 'B站音频', BilibiliPage),
    ('lyrics', '歌词', LyricsPage),
    ('langid', '语种识别', LangIdPage),
    ('env', '环境', EnvPage),
]


class Shell:
    """左侧导航 + 右侧功能页。每个功能一个独立入口。"""

    def __init__(self, root, model_path=None, ms_exe=None, ffmpeg=None):
        self.root = root
        self.model_path = model_path
        self.ms_exe = ms_exe
        self.ffmpeg = ffmpeg

        root.title('TuneScript AI · 工具箱')
        # 默认 1120x740 居中；`TS_UI_GEOMETRY=1120x740+20+20` 可钉死位置（截图/录屏用）
        root.geometry(os.environ.get('TS_UI_GEOMETRY', '').strip() or '1120x740')
        root.minsize(940, 620)
        root.configure(bg=K.N_BG)
        K.setup_styles(root)

        self.side = tk.Frame(root, bg=K.N_SIDEBAR, width=172)
        self.side.pack(side='left', fill='y')
        self.side.pack_propagate(False)

        logo = tk.Frame(self.side, bg=K.N_SIDEBAR)
        logo.pack(fill='x', pady=(18, 10), padx=14)
        tk.Label(logo, text='TuneScript AI', bg=K.N_SIDEBAR, fg='#ffffff',
                 font=(K.UI_FONT, 13, 'bold'), anchor='w').pack(anchor='w')
        tk.Label(logo, text='音乐转谱工具箱', bg=K.N_SIDEBAR, fg=K.N_SIDEBAR_TEXT,
                 font=(K.UI_FONT, 8), anchor='w').pack(anchor='w')

        self.container = tk.Frame(root, bg=K.N_BG)
        self.container.pack(side='left', fill='both', expand=True)

        self.pages = {}
        self.buttons = {}
        for key, label, cls in NAV:
            # 页面**不预先 place** —— 只 place 当前这一个。
            # （六个页面叠在同一个位置靠 lift() 抢顶层，实测在窗口真正 map 之后
            #   会退回创建顺序，显示的不是被选中的那页；而且六页同时建也白费时间。）
            page = cls(self.container, self)
            self.pages[key] = page
            page.bind_wheel()          # 控件都建完了，这时挂滚轮最省事
            b = tk.Button(self.side, text='  ' + label, anchor='w', bd=0,
                          relief='flat', bg=K.N_SIDEBAR, fg=K.N_SIDEBAR_TEXT,
                          activebackground=K.N_SIDEBAR_HOVER,
                          activeforeground='#ffffff', cursor='hand2',
                          font=(K.UI_FONT, 10), padx=10, pady=9,
                          command=lambda k=key: self.show(k))
            b.pack(fill='x', padx=8, pady=1)
            self.buttons[key] = b

        tk.Label(self.side, text='V0.5.1', bg=K.N_SIDEBAR, fg='#5b6279',
                 font=(K.UI_FONT, 8)).pack(side='bottom', pady=10)

        try:
            ico = os.path.join(self._bundle_dir(), 'assets', 'app_icon.ico')
            if os.path.isfile(ico):
                root.iconbitmap(ico)
        except Exception:
            pass

        # 初始页：默认「转谱」；`TS_UI_PAGE=netease` 可直接开在某页（截图/录屏/自检用）
        want = os.environ.get('TS_UI_PAGE', '').strip().lower()
        keys = [k for k, _l, _c in NAV]
        self.show(want if want in keys else 'transcribe')

    @staticmethod
    def _bundle_dir():
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        return os.path.dirname(os.path.abspath(__file__))

    def show(self, key):
        """切到某一页：只 place 选中的那一页，其余全部 place_forget。"""
        for k, p in self.pages.items():
            try:
                if k == key:
                    p.place(relx=0, rely=0, relwidth=1, relheight=1)
                else:
                    p.place_forget()
            except Exception:
                pass
        for k, b in self.buttons.items():
            on = (k == key)
            b.configure(bg=K.N_SIDEBAR_SEL_BG if on else K.N_SIDEBAR,
                        fg=K.N_SIDEBAR_SEL_FG if on else K.N_SIDEBAR_TEXT,
                        font=(K.UI_FONT, 10, 'bold' if on else 'normal'))
        self.pages[key].lift()


def run(root=None):
    """打开新 UI。`root` 省略时自己建一个 Tk。"""
    import transcriber_app as TA
    own = root is None
    if own:
        root = tk.Tk()
    Shell(root, model_path=TA.find_model(), ms_exe=TA.find_musescore(),
          ffmpeg=TA.find_ffmpeg())
    if own:
        root.mainloop()
    return root
