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
    fake = {'level': 'hires',
            'resolve': {'level': 'exhigh', 'br': 320000, 'fmt': 'mp3',
                        'downgraded': True},
            'cookie': {'ok': True, 'nickname': '某人', 'vip_label': '黑胶 VIP'},
            'reason': '请求 hires -> 实际 exhigh 320kbps mp3'}
    joined = '\n'.join(ui_app._quality_lines(fake))
    check('显示的是服务端实际给的档位（320kbps）', '音质：320kbps（320 kbps mp3）' in joined,
          joined.split('\n')[0])
    check('没有把 hires 说成"实际音质"', '音质：Hi-Res' not in joined)
    check('明确写出被降级 + 原因', '只给了 320kbps' in joined and '需要黑胶会员' in joined)
    check('带上账号状态', '已登录' in joined and '黑胶 VIP' in joined)
    fake2 = {'level': 'exhigh',
             'resolve': {'level': 'exhigh', 'br': 320000, 'fmt': 'mp3'},
             'cookie': {'ok': False, 'reason': 'cookie 无效或已过期'}}
    j2 = '\n'.join(ui_app._quality_lines(fake2))
    check('没降级时不乱报警', '⚠️ 请求的是' not in j2, j2.replace('\n', ' | '))
    check('未登录时说明原因', '未登录' in j2 and 'cookie 无效或已过期' in j2)

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
