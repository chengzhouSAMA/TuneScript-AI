# -*- coding: utf-8 -*-
"""推送闸门：没有人类钥匙，任何自动化（含 AI）都不许 push 这个仓库。

git 在每次 push 前会跑 `.git/hooks/pre-push`，本脚本就是它的实现。
**默认拒绝** —— 钥匙对不上就 exit 1，推送中止。

钥匙放在**仓库外面**（不入库、不进快照）：

    ~/.tunescript_push_key        内容是一行口令
    或直接给环境变量 TS_PUSH_KEY

装/卸/生成：

    python dev/push_guard.py --keygen      # 生成钥匙文件（已存在则不动）
    python dev/push_guard.py --install     # 装进 <repo>/.git/hooks/pre-push
    python dev/push_guard.py --uninstall
    python dev/push_guard.py --status

你自己推送时（PowerShell）：

    $env:TS_PUSH_KEY = (Get-Content "$env:USERPROFILE\\.tunescript_push_key")
    git push
    Remove-Item Env:\\TS_PUSH_KEY

⚠️ **能力边界，别指望它挡住一切**：本地 hook 挡的是"顺手就推"的自动化流程。
**掌握文件写权限的东西可以直接删掉 hook、或者 `git push --no-verify`。**
真正的硬闸在 GitHub 侧（分支保护 / ruleset：禁止直接推 main、必须走 PR 并由人批准）。
两层一起上才是完整方案；只装这一层时必须知道它只是"防手滑"。
"""
import argparse
import hmac
import os
import subprocess
import sys
import time

KEY_FILE = (os.environ.get("TS_PUSH_KEY_FILE")
            or os.path.join(os.path.expanduser("~"), ".tunescript_push_key"))
AUDIT = os.path.join(os.path.expanduser("~"), ".tunescript_push_audit.log")
HOOK_MARK = "tunescript-push-guard"


def _read_key():
    """只认**环境变量**里的钥匙。

    ⚠️ 这里踩过一次坑，别改回去：第一版是"环境变量没有就读钥匙文件"，
    结果**只要钥匙文件存在，任何进程（包括 AI）都能推** —— 闸门等于没有。
    现在钥匙文件只当"正确答案"，必须由人在自己的 shell 里显式导出才放行。
    """
    k = os.environ.get("TS_PUSH_KEY")
    return k.strip() if k else None


def _audit(verdict, detail):
    try:
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                      verdict, detail))
    except OSError:
        pass


def check(remote="", url="", refs=""):
    """返回 0 = 放行，1 = 拒绝。给 hook 和自检共用。"""
    given = _read_key()
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            stored = f.read().strip()
    except OSError:
        stored = ""
    if not stored:
        _audit("DENY-no-keyfile", "%s %s" % (remote, url))
        sys.stderr.write(
            "\n⛔ 推送被拦：还没造钥匙。先跑 python dev/push_guard.py --keygen\n"
            "   钥匙文件应位于：%s\n" % KEY_FILE)
        return 1
    if not given:
        _audit("DENY-no-env", "%s %s" % (remote, url))
        sys.stderr.write(
            ("\n" + "=" * 68 + "\n"
             "⛔ 推送被「人类钥匙闸门」拦住：没有在本进程里提供钥匙。\n\n"
             "   注意：**光是钥匙文件存在不算授权** —— 否则任何进程（含 AI）\n"
             "   都能推，闸门就白装了。必须由人显式导出：\n\n"
             "   PowerShell:\n"
             "     $env:TS_PUSH_KEY = (Get-Content \"$env:USERPROFILE\\.tunescript_push_key\")\n"
             "     git push\n"
             "     Remove-Item Env:\\TS_PUSH_KEY\n\n"
             "这是**故意**的设计：不允许任何自动化（含 AI）把改动推到你的 GitHub。\n"
             "   首次使用先造钥匙：python dev/push_guard.py --keygen\n"
             + "=" * 68 + "\n"))
        return 1
    if hmac.compare_digest(given, stored):
        _audit("ALLOW", "%s %s" % (remote, url))
        return 0
    _audit("DENY-bad-key", "%s %s" % (remote, url))
    sys.stderr.write(
        "\n⛔ 推送被拦：钥匙不匹配（比对文件 %s）。\n"
        "   若确实是你本人在操作，检查 TS_PUSH_KEY 是不是多带了空格/换行。\n" % KEY_FILE)
    return 1


def git_dir():
    try:
        out = subprocess.check_output(["git", "rev-parse", "--git-dir"],
                                      stderr=subprocess.DEVNULL)
        return os.path.abspath(out.decode("utf-8", "replace").strip())
    except Exception:
        return None


def hook_path():
    gd = git_dir()
    return os.path.join(gd, "hooks", "pre-push") if gd else None


def install():
    hp = hook_path()
    if not hp:
        print("找不到 .git 目录 —— 在仓库里跑这个脚本。")
        return 2
    os.makedirs(os.path.dirname(hp), exist_ok=True)
    if os.path.isfile(hp):
        with open(hp, encoding="utf-8", errors="replace") as f:
            if HOOK_MARK not in f.read():
                print("⚠️ 已存在别的 pre-push 钩子，先备份成 %s.bak" % hp)
                os.replace(hp, hp + ".bak")
    here = os.path.dirname(os.path.abspath(__file__))
    rel = os.path.join(here, "push_guard.py")
    body = ('#!/bin/sh\n'
            '# %s —— 没有人类钥匙就不许 push（由 push_guard.py --install 写入，别手改）\n'
            'exec python "%s" --hook "$@"\n' % (HOOK_MARK, rel.replace("\\", "/")))
    with open(hp, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    try:
        os.chmod(hp, 0o755)
    except OSError:
        pass
    print("已装：%s" % hp)
    if not os.path.isfile(KEY_FILE):
        print("⚠️ 还没有钥匙文件，现在任何推送都会被拒。先跑：\n"
              "   python dev/push_guard.py --keygen")
    return 0


def uninstall():
    hp = hook_path()
    if hp and os.path.isfile(hp):
        # ⚠️ 必须先把文件读完并关掉再 os.remove —— 在 with 块里删会
        # 在 Windows 上稳定报 WinError 32（文件被自己占着）。
        with open(hp, encoding="utf-8", errors="replace") as f:
            mine = HOOK_MARK in f.read()
        if not mine:
            print("那个 pre-push 不是本闸门装的，没动它。")
            return 1
        for i in range(5):
            try:
                os.remove(hp)
                print("已卸：%s" % hp)
                return 0
            except PermissionError:
                time.sleep(0.3 * (i + 1))
            except OSError as e:
                print("删不掉 %s：%s" % (hp, e))
                return 1
        print("删不掉 %s（文件被占用），关掉正在跑的 git 再试。" % hp)
        return 1
    print("没有装。")
    return 0


def keygen():
    if os.path.isfile(KEY_FILE):
        print("钥匙已存在，不动它：%s" % KEY_FILE)
        return 0
    import secrets
    k = secrets.token_urlsafe(24)
    with open(KEY_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(k + "\n")
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    print("已生成钥匙：%s\n"
          "（钥匙不在仓库里，也不会被提交。你自己推送时：\n"
          "  PowerShell → $env:TS_PUSH_KEY = (Get-Content \"$env:USERPROFILE\\.tunescript_push_key\")\n"
          "               git push ; Remove-Item Env:\\TS_PUSH_KEY\n"
          "  也别忘了 git 的 --no-verify 能绕过本地钩子，硬闸在 GitHub 侧。）" % KEY_FILE)
    return 0


def status():
    hp = hook_path()
    installed = False
    if hp and os.path.isfile(hp):
        with open(hp, encoding="utf-8", errors="replace") as f:
            installed = HOOK_MARK in f.read()
    print("闸门已装：%s" % ("是" if installed else "否"))
    print("钩子路径：%s" % (hp or "(找不到 .git)"))
    print("钥匙文件：%s（%s）" % (KEY_FILE,
                                "存在" if os.path.isfile(KEY_FILE) else "**不存在**"))
    print("环境变量 TS_PUSH_KEY：%s" % ("已设置" if os.environ.get("TS_PUSH_KEY") else "未设置"))
    print("审计日志：%s" % AUDIT)
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--keygen", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--hook", action="store_true", help="git 钩子入口（内部用）")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    if a.keygen:
        return keygen()
    if a.install:
        return install()
    if a.uninstall:
        return uninstall()
    if a.status:
        return status()
    if a.hook:
        # git pre-push 的调用约定：$1=远端名 $2=远端URL，stdin=要推的 refs
        remote = a.args[0] if len(a.args) > 0 else ""
        url = a.args[1] if len(a.args) > 1 else ""
        try:
            refs = sys.stdin.read()
        except Exception:
            refs = ""
        return check(remote, url, refs)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
