# -*- coding: utf-8 -*-
"""新 UI 的结构自检：能建起来、7 个入口都在、每页都能切换、不抛异常。

不需要网络 —— 页面里的联网自检都跑在后台线程，本脚本建完就销毁。

    python lang_dev/_check_newui.py            # 只自检
    python lang_dev/_check_newui.py --shot     # 自检并把窗口截图存到 lang_dev/_ui_shot.png
"""
import os
import sys
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

OK, BAD = [], []


def check(name, cond, info=''):
    (OK if cond else BAD).append(name)
    print('  %s %-46s %s' % ('✓' if cond else '✗', name, info))


def main():
    import ui_app
    import ui_kit as K

    print('=== 1) 建主窗口 ===')
    root = tk.Tk()
    root.withdraw()
    K.setup_styles(root)
    check('主题样式配置通过', True)

    print('\n=== 2) 外壳与导航 ===')
    shell = ui_app.Shell(root, model_path='X', ms_exe='X', ffmpeg='X')
    check('Shell 构造成功', True)
    keys = [k for k, _l, _c in ui_app.NAV]
    check('导航项数 = 6（音频裁剪界面已按用户要求删除）',
          len(ui_app.NAV) == 6, str(keys))
    check('导航里没有「音频裁剪」',
          'crop' not in keys and not any('裁剪' in l for _k, l, _c in ui_app.NAV))
    check('ui_app 里没有 CropPage 类', not hasattr(ui_app, 'CropPage'))
    check('每项都有独立页面实例',
          all(k in shell.pages for k in keys), ','.join(shell.pages))
    check('每项都有独立侧栏按钮',
          all(k in shell.buttons for k in keys))

    print('\n=== 3) 逐个切换（每页都要能 lift 且不报错）===')
    for k, label, _cls in ui_app.NAV:
        try:
            shell.show(k)
            root.update_idletasks()
            check('切到「%s」' % label, True)
        except Exception as e:
            check('切到「%s」' % label, False, '%s: %s' % (type(e).__name__, e))

    print('\n=== 4) 关键控件在不在 ===')
    tp = shell.pages['transcribe']
    check('转谱页有 开始转谱 按钮', hasattr(tp, 'start_btn'))
    check('转谱页有 音频/BV/网易云/输出目录 四个输入',
          all(hasattr(tp, v) for v in ('audio_var', 'bvid_var', 'ne_var', 'outdir_var')))
    check('转谱页有「分轨后手动勾选识别音轨」开关', hasattr(tp, 'pick_var'))
    check('转谱页有勾选对话框及其工作线程入口',
          hasattr(tp, '_ask_stems') and hasattr(tp, '_stem_dialog'))
    check('勾选清单里人声/鼓写明了不可改的原因（R1/R3）',
          '必须保留' in tp._STEM_LABEL.get('vocals', '')
          and '不识别' in tp._STEM_LABEL.get('drums', ''),
          tp._STEM_LABEL.get('vocals', '') + ' | ' + tp._STEM_LABEL.get('drums', ''))
    check('转谱页有「简单/高级模式」单选（高级＝分轨+语种分段）',
          hasattr(tp, 'mode_var') and tp.mode_var.get() in ('simple', 'advanced'))
    check('转谱页有谱面置信度着色开关', hasattr(tp, 'conf_var'))
    check('旧的 sep_var/simple_var 已并入模式单选（不再两处口径）',
          not hasattr(tp, 'sep_var') and not hasattr(tp, 'simple_var'))
    np_ = shell.pages['netease']
    check('网易云页有 搜索按钮 + 结果表 + 下载按钮',
          hasattr(np_, 'search_btn') and hasattr(np_, 'tv') and hasattr(np_, 'dl_btn'))
    check('网易云页有 登录/退出入口', hasattr(np_, 'refresh_account'))
    check('网易云页有 cookie 输入栏（Entry + 保存/载入）',
          hasattr(np_, 'cookie_bar') and hasattr(np_.cookie_bar, 'entry')
          and hasattr(np_.cookie_bar, 'save') and hasattr(np_.cookie_bar, 'load'))
    check('转谱页有跳去 cookie 的入口', hasattr(tp, 'ne_entry'))
    check('cookie 脱敏不回显完整值',
          ui_app._mask('MUSIC_U=0123456789abcdef; appver=1;') ==
          'MUSIC_U=012345…cdef（16 字符，共 2 个字段）',
          ui_app._mask('MUSIC_U=0123456789abcdef; appver=1;'))
    check('B站页有 BV 输入 + 获取按钮',
          hasattr(shell.pages['bilibili'], 'bv_var') and hasattr(shell.pages['bilibili'], 'go_btn'))
    lp = shell.pages['lyrics']
    check('歌词页有 候选表 + 预览 + 保存',
          hasattr(lp, 'tv') and hasattr(lp, 'text') and hasattr(lp, 'save_btn'))
    check('语种页有 后端选择 + 结果表',
          hasattr(shell.pages['langid'], 'backend_var') and hasattr(shell.pages['langid'], 'tv'))
    check('环境页有 检测文本 + 刷新', hasattr(shell.pages['env'], 'text_var'))

    print('\n=== 5) 每个功能都能从侧栏一步到达（独立入口）===')
    for k, label, _cls in ui_app.NAV:
        shell.show(k)
        root.update_idletasks()
        mgr = shell.pages[k].winfo_manager()
        others = [x for x in shell.pages if x != k
                  and shell.pages[x].winfo_manager()]
        check('「%s」是独立页面且只有它在显示' % label,
              mgr == 'place' and not others,
              'manager=%s 同时显示的其它页=%s' % (mgr, others))
    shell.show('transcribe')
    check('默认页 = 转谱', shell.pages['transcribe'].winfo_manager() == 'place')

    print('\n=== 6) 内容区滚轮滚动 ===')
    pg = shell.pages['transcribe']
    check('内容区挂在 Canvas 上（可滚动）', isinstance(getattr(pg, '_canvas', None), tk.Canvas))
    check('有垂直滚动条', hasattr(pg, '_vbar') and isinstance(pg._vbar, ttk.Scrollbar))
    check('已给页面内所有控件挂上滚轮', pg._wheel_bound is True)
    check('日志卡固定在底部、不参与滚动',
          pg._log_card.winfo_manager() == 'pack' and pg._log_card is not pg.body)

    # 滚轮方向：向下滚 = 内容上移（yview_scroll(1)）
    calls = []
    real_scroll = pg._canvas.yview_scroll
    pg._canvas.yview_scroll = lambda *a: calls.append(a)
    real_mapped = pg.winfo_ismapped
    pg.winfo_ismapped = lambda: True

    class _Ev(object):
        def __init__(self, d):
            self.delta = d

    pg._on_wheel(_Ev(-120))
    check('滚轮向下 → yview_scroll(1)', calls == [(1, 'units')], str(calls))
    calls.clear()
    pg._on_wheel(_Ev(120))
    check('滚轮向上 → yview_scroll(-1)', calls == [(-1, 'units')], str(calls))
    calls.clear()
    pg._on_wheel(_Ev(-360))
    check('连续滚 3 格 → yview_scroll(3)', calls == [(3, 'units')], str(calls))
    pg._canvas.yview_scroll = real_scroll
    pg.winfo_ismapped = real_mapped

    # 内容比视口高时滚动条要出现；装得下就收起来
    for _i in range(24):
        tk.Frame(pg.body, height=40, bg='#ffffff').pack(fill='x')
    pg.bind_wheel()
    root.deiconify()
    root.update()
    root.update_idletasks()
    pg._on_body_configure()
    root.update_idletasks()
    check('内容变高后滚动条出现', pg._vbar_visible is True)
    pg._canvas.configure(scrollregion=pg._canvas.bbox('all'))
    check('scrollregion 已包住全部内容',
          pg._canvas.bbox('all')[3] > pg._canvas.winfo_height(),
          '内容高 %s vs 视口 %s' % (pg._canvas.bbox('all')[3], pg._canvas.winfo_height()))
    root.withdraw()

    print('\n=== 7) 音质展示：不能把「请求的档位」当成「实际拿到的」===')
    # 用户真实那一份：黑胶会员、请求 hires、实际 lossless 808kbps flac
    real = {'level': 'hires',
            'resolve': {'level': 'lossless', 'br': 808000, 'fmt': 'flac',
                        'downgraded': True},
            'cookie': {'ok': True, 'nickname': '承州SAMA',
                       'vip_label': '会员档位未知'},
            'reason': '请求 hires -> 实际 lossless 808kbps flac（cookie 优先，命中 eapi:hires）'}
    rl = ui_app._quality_lines(real)
    rj = '\n'.join(rl)
    print('    ' + '\n    '.join(rl))
    check('无损被如实显示', '音质：无损（808 kbps flac）' in rj)
    check('没有把 hires 说成实际音质', '音质：Hi-Res' not in rj)
    check('会员已生效时不再喊"需要会员 cookie"',
          '需要黑胶会员' not in rj and '需要会员 cookie' not in rj)
    check('改成说清是逐曲授权',
          '会员已生效' in rj and '逐曲授权' in rj)
    check('账号行照实写昵称', '已登录 承州SAMA' in rj)

    # 未登录 + 被降级 → 这时才该提"需要会员"
    anon = {'level': 'hires',
            'resolve': {'level': 'exhigh', 'br': 320000, 'fmt': 'mp3',
                        'downgraded': True},
            'cookie': {'ok': False, 'reason': '没有 cookie'},
            'reason': '请求 hires -> 实际 exhigh 320kbps mp3（匿名优先，命中 plain:1900000）'}
    aj = '\n'.join(ui_app._quality_lines(anon))
    check('未登录被降级时才提示需要会员',
          '需要黑胶会员' in aj and '音质：320kbps' in aj)

    # 已登录但只有 320k → 说版权限制，别赖会员
    vip320 = {'level': 'hires',
              'resolve': {'level': 'exhigh', 'br': 320000, 'fmt': 'mp3'},
              'cookie': {'ok': True, 'nickname': '某人', 'vip_label': '黑胶 VIP'}}
    vj = '\n'.join(ui_app._quality_lines(vip320))
    check('已登录却只给 320k → 指向版权限制',
          '版权限制' in vj and '需要黑胶会员' not in vj)
    check('没降级时不乱报警',
          '⚠️' not in '\n'.join(ui_app._quality_lines(
              {'level': 'exhigh', 'resolve': {'level': 'exhigh', 'br': 320000,
                                              'fmt': 'mp3'},
               'cookie': {'ok': False, 'reason': 'x'}})))
    check('未登录时说明原因',
          '未登录' in '\n'.join(ui_app._quality_lines(
              {'level': 'exhigh', 'resolve': {'level': 'exhigh', 'br': 320000,
                                              'fmt': 'mp3'},
               'cookie': {'ok': False, 'reason': 'cookie 无效或已过期'}})))

    print('\n=== 8) 后台任务不能被同名属性盖掉（转谱就炸在这）===')
    import inspect
    import time as _time
    run_src = inspect.getsource(K.Page.run)
    check('Page.run 不再写 self._work（会盖掉子类 _work 方法）',
          'self._work' not in run_src)
    check('Page.run 的内部状态用 _task_ 前缀', 'self._task_done' in run_src)
    wk_src = inspect.getsource(ui_app.TranscribePage._work)
    check('_work 里不碰 Tk 变量（它在工作线程跑）',
          not any(v in wk_src for v in ('sep_var', 'simple_var', 'mt3_var')),
          'sep_var/simple_var/mt3_var 必须先在主线程读出来')
    check('_work 里也不碰 pick_var（同样必须先在主线程读出）',
          'pick_var' not in wk_src)
    pick_src = inspect.getsource(ui_app.TranscribePage._ask_stems)
    check('_ask_stems 走 ask_main（工作线程不直接碰 Tcl）',
          'ask_main' in pick_src and 'after' not in pick_src)
    dlg_src = inspect.getsource(ui_app.TranscribePage._stem_dialog)
    check('勾选对话框拦住「一条伴奏都没勾」',
          '至少勾一条伴奏轨' in dlg_src and 'disabled' in dlg_src,
          '一条都不留时管线会拿人声当伴奏')
    check('勾选对话框里人声与鼓固定禁用（R1/R3 不是可选项）',
          "cb.configure(state='disabled')" in dlg_src and "'vocals', 'drums'" in dlg_src)
    check('_work 里也不碰 mode_var/conf_var（同样先在主线程读）',
          'mode_var' not in wk_src and 'conf_var' not in wk_src)
    check('_work 把高级模式映射成 分轨+语种分段 两开关同开',
          'use_separation=adv' in wk_src and 'simple_mode=(not adv)' in wk_src
          and 'lang_seg=adv' in wk_src)
    ms_src = inspect.getsource(ui_app.TranscribePage._open_in_musescore)
    check('转谱页有「在 MuseScore 中打开」按钮', hasattr(tp, 'ms_btn'))
    check('该按钮走 MuseScore 命令行（Popen 传 XML 路径，不拼 shell 串）',
          'Popen([ms, tgt])' in ms_src and 'ms_exe' in ms_src
          and 'shell' not in ms_src)
    check('跑管线期间该按钮被禁用（MuseScore 占着文件会让渲染失败）',
          'ms_btn' in ui_app.TranscribePage._BUSY_ATTRS)
    check('按钮打开的是 XML 而不是 PDF（XML 才是可编辑/可播放的源）',
          "r.get('xml')" in inspect.getsource(ui_app.TranscribePage._done))

    # ask_main：工作线程提问 → 主线程回答，值必须回得来（勾选对话框就靠它）。
    # 这里不手工调 _poll（那会叠加出多条重复的轮询链），页面自己的 120ms 轮询就够。
    import threading as _th
    _got = {}

    def _worker_ask():
        _got['v'] = tp.ask_main(lambda: ['piano'])

    _wt = _th.Thread(target=_worker_ask, daemon=True)
    _wt.start()
    for _i in range(200):
        root.update()
        _time.sleep(0.01)
        if not _wt.is_alive():
            break
    _wt.join(timeout=2)
    check('ask_main：工作线程能从主线程拿回返回值', _got.get('v') == ['piano'], str(_got))
    check('ask_main 在主线程直接调用不死锁（自己等自己）',
          tp.ask_main(lambda: 'inline') == 'inline')

    # 真跑一遍：任务必须被执行到，且子类方法还在
    pg3 = shell.pages['transcribe']
    seen = []
    orig_work = pg3._work

    def _fake_work(a, b, c, d, opts, p):
        seen.append((a, b, c, d, opts))
        return 'ok'

    pg3._work = _fake_work
    try:
        pg3.run(lambda p: pg3._work('A', 'B', 'C', 'D', ('x',), p), None)
        for _i in range(100):
            root.update()
            _time.sleep(0.03)
            if not pg3.busy:
                break
        check('后台任务真的被执行（说明 _work 没被换成 1 参数的 lambda）',
              seen == [('A', 'B', 'C', 'D', ('x',))], str(seen))
        check('跑完后子类 _work 方法仍然是那个 6 参函数',
              pg3._work is _fake_work)
    finally:
        pg3._work = orig_work

    if '--shot' in sys.argv:
        root.deiconify()
        shell.show('netease')
        root.update()
        root.after(1200, root.quit)
        try:
            root.mainloop()
        except Exception:
            pass
        shot = os.path.join(HERE, '_ui_shot.png')
        try:
            import subprocess
            ps = ('Add-Type -AssemblyName System.Windows.Forms,System.Drawing;'
                  '$b=New-Object Drawing.Bitmap([Windows.Forms.Screen]::PrimaryScreen.Bounds.Width,'
                  '[Windows.Forms.Screen]::PrimaryScreen.Bounds.Height);'
                  '$g=[Drawing.Graphics]::FromImage($b);'
                  '$g.CopyFromScreen(0,0,0,0,$b.Size);'
                  '$b.Save("%s");' % shot.replace('\\', '\\\\'))
            subprocess.run(['powershell', '-NoProfile', '-Command', ps], timeout=60)
            check('截图已保存', os.path.isfile(shot), shot)
        except Exception as e:
            check('截图已保存', False, str(e)[:60])

    root.destroy()
    print('\n=== 汇总：%d 项，%d 通过，%d 失败 ===' % (len(OK) + len(BAD), len(OK), len(BAD)))
    if BAD:
        for b in BAD:
            print('  失败：%s' % b)
    return 1 if BAD else 0


if __name__ == '__main__':
    sys.exit(main())
