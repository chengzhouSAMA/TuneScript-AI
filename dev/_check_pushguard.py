# -*- coding: utf-8 -*-
"""推送闸门的自测：没有人类钥匙就必须推不上去。

不是"读一下代码看起来对"，而是**真的建一个临时仓库 + 临时裸远端，真跑 `git push`**，
看它到底推得上去还是推不上去。

    python lang_dev/_check_pushguard.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push_guard as G                                          # noqa: E402

OK, BAD = [], []


def check(name, cond, info=''):
    (OK if cond else BAD).append(name)
    print('  %s %-54s %s' % ('✓' if cond else '✗', name, info))


TMP = tempfile.mkdtemp(prefix='ts_pushguard_')
_keep_key, _keep_audit = G.KEY_FILE, G.AUDIT
G.KEY_FILE = os.path.join(TMP, 'key')
G.AUDIT = os.path.join(TMP, 'audit.log')
GOOD = 'correct-horse-battery-staple'


Key_FILE = None  # 占位，下面 _env() 会用到


def _env(extra=None):
    """钩子是**子进程**，模块级 monkeypatch 传不过去 —— 必须用 TS_PUSH_KEY_FILE
    把钥匙路径显式告诉它（第一版没做，测试里"有钥匙"那步因此假失败过一次）。"""
    e = os.environ.copy()
    e.pop('TS_PUSH_KEY', None)
    e['TS_PUSH_KEY_FILE'] = G.KEY_FILE
    if extra:
        e.update(extra)
    return e


def _run(args, cwd, env=None):
    """subprocess 在 Windows 上默认用 gbk 解码，闸门输出的是中文 UTF-8 → 必须显式指定。"""
    p = subprocess.run(args, cwd=cwd, env=_env(env), capture_output=True,
                       text=True, encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def _git(args, cwd):
    return _run(['git'] + args, cwd)


print('=== 1) 判定逻辑 ===')
os.environ.pop('TS_PUSH_KEY', None)
check('文件不存在 + 没环境变量 → 拒绝', G.check('origin', 'x', '') == 1)
with open(G.KEY_FILE, 'w', encoding='utf-8') as f:
    f.write(GOOD + '\n')
os.environ.pop('TS_PUSH_KEY', None)
# ⚠️ 这条是回归护栏：第一版闸门"钥匙文件存在就放行"，等于任何进程（含 AI）都能推。
check('**只有钥匙文件、没给环境变量 → 仍然拒绝**（不许文件存在即授权）',
      G.check('origin', 'x', '') == 1)
os.environ['TS_PUSH_KEY'] = GOOD
check('环境变量给对 → 放行', G.check('origin', 'x', '') == 0)
os.environ['TS_PUSH_KEY'] = 'wrong'
check('环境变量给错 → 拒绝', G.check('origin', 'x', '') == 1)
os.environ['TS_PUSH_KEY'] = ' ' + GOOD + '\n'      # 前后空白要容忍
check('钥匙带空白 → 仍放行（strip 过）', G.check('origin', 'x', '') == 0)
os.environ['TS_PUSH_KEY'] = GOOD + 'x'
check('钥匙多一个字 → 拒绝', G.check('origin', 'x', '') == 1)
os.environ.pop('TS_PUSH_KEY', None)

with open(G.AUDIT, encoding='utf-8') as f:
    log = f.read()
check('每次都写了审计日志', log.count('\n') >= 6, '%d 行' % log.count('\n'))
check('审计里既有 ALLOW 也有 DENY', 'ALLOW' in log and 'DENY' in log)
check('审计区分了"没给环境变量"和"钥匙错"',
      'DENY-no-env' in log and 'DENY-bad-key' in log)

print('\n=== 2) 真推一次：装闸门后能不能推上去 ===')
work = os.path.join(TMP, 'work')
bare = os.path.join(TMP, 'remote.git')
_run(['git', 'init', '-q', '--bare', '-b', 'main', bare], TMP)
_run(['git', 'init', '-q', '-b', 'main', work], TMP)
with open(os.path.join(work, 'a.txt'), 'w', encoding='utf-8') as f:
    f.write('hello\n')
IDENT = ['-c', 'user.email=t@t', '-c', 'user.name=t']
_git(IDENT + ['add', '-A'], work)
_git(IDENT + ['commit', '-qm', 'init'], work)
# Windows 上传本地路径当远端：用正斜杠
_git(['remote', 'add', 'origin', bare.replace('\\', '/')], work)
rc, out = _git(['remote', '-v'], work)
check('临时远端挂上了', 'origin' in out, out.strip().splitlines()[0][:60] if out.strip() else '')

_cwd = os.getcwd()
os.chdir(work)
rc = G.install()
_hp = G.hook_path()
check('--install 写好了钩子', rc == 0 and _hp and os.path.isfile(_hp), _hp or '')
os.chdir(_cwd)

os.environ.pop('TS_PUSH_KEY', None)
rc, out = _run(['git', 'push', '-u', 'origin', 'main'], work)
check('没有钥匙：push 被拒绝', rc != 0, 'rc=%d' % rc)
check('拒绝时打出了人话说明（不是 git 的报错）',
      '人类钥匙闸门' in out or '推送被拦' in out,
      [l for l in out.splitlines() if '⛔' in l][:1])
rc2, out2 = _run(['git', 'ls-remote', 'origin'], work)
check('远端确实没有收到任何东西', out2.strip() == '', repr(out2.strip()[:50]))

rc, out = _run(['git', 'push', '-u', 'origin', 'main'], work,
               env={'TS_PUSH_KEY': GOOD})
check('有钥匙：push 成功', rc == 0, 'rc=%d %s' % (rc, out.strip()[-70:]))
rc3, out3 = _run(['git', 'ls-remote', 'origin'], work)
check('远端这次收到了 main', 'refs/heads/main' in out3, out3.strip()[:50])

print('\n=== 3) 绕过与卸载 ===')
rc, out = _run(['git', 'push', '--no-verify', '-u', 'origin', 'main'], work)
check('⚠️ --no-verify 确实能绕过本地钩子（已知边界，硬闸得靠 GitHub 侧）',
      rc == 0, 'rc=%d' % rc)

os.chdir(work)
ok_un = G.uninstall() == 0 and not os.path.isfile(_hp)
os.chdir(_cwd)
check('--uninstall 能摘掉', ok_un)

G.KEY_FILE, G.AUDIT = _keep_key, _keep_audit
shutil.rmtree(TMP, ignore_errors=True)

print('\n=== 汇总：%d 项，%d 通过，%d 失败 ===' % (len(OK) + len(BAD), len(OK), len(BAD)))
if BAD:
    for b in BAD:
        print('  失败：%s' % b)
sys.exit(1 if BAD else 0)
