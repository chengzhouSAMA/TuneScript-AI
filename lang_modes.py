# -*- coding: utf-8 -*-
"""lang_modes.py — 语种 → 人声识别模式的预设表。

用法
-
from lang_modes import resolve, mode_names
p = resolve("ja")            # TS_LANG_MODE=off 时返回 default 的值
p = resolve("ja", force=True)  # 诊断/实验用：强制取语言专用预设

CLI: python lang_modes.py            # 打印全部预设与彼此的差异
python lang_modes.py --diff ja # 只打印 ja 与 default 的差异
"""
import os
import sys

ENV_LANG_MODE = "TS_LANG_MODE"       # "off"(默认) / "auto"（启用语言专用预设）

# 出厂默认预设：**这些数值必须与 transcriber_app.py 当前出厂行为逐值一致**。
# 任何修改都必须先跑全量 5 曲验收。
DEFAULT = {
    "bp_min_len_vocal": 60.0,
    "bp_min_len_song": 127.0,
    "bp_onset_threshold": 0.45,
    "fill_win": 0.06,
    "fill_gap": 0.12,
    "fill_rate": 2.0,
    "fill_dens": 2.2,
    "fill_instr_rate": 2.5,
}

# 语种专用预设。**只列与 default 不同的键**，其余继承 default。
PRESETS = {
    "default": {},

    # ---- 中文（含合成人声：洛天依/星尘/诗岸等）----
    "zh": {"phonetic": "none"},

    # ---- 粤语 ----
    # 与 zh 同值；保留该键是为了将来换更强的 LID 后端后能直接生效。
    "yue": {"phonetic": "none"},

    # ---- 日语 ----
    # 日语一字一音、同音反复极多，所以对齐单位是**摩拉**；
    # 现有 default 的 fill_gap=0.12 已低于真人快音节的 ~0.25s 间隔，故不再下调，
    # 只把 fill_win 从 0.06 收紧到 0.05。**未验收**，仅在 TS_LANG_MODE=auto 时生效。
    "ja": {
        "fill_win": 0.05,
        "phonetic": "mora",
        "evidence": "方向合理但未达显著（+0.0036 < ±0.01 噪声地板）；待全量 A/B 验收",
    },

    # ---- 英语 ----
    # 英语是重音计时，一个音节可跨多个音素，所以对齐单位是**元音核（音节）**，
    # 不是音素。音节更长、辅音簇更多，同音高碎音合并可以比日语激进
    # （fill_gap 0.12→0.16）。
    # 仍属**未验收假设**：只在 30 秒英文试听段上验证过音素/音节路径本身可用，
    # 没有做过整曲 A/B，所以默认不启用（见 DISABLED_BY_DEFAULT）。
    "en": {
        "fill_gap": 0.16,
        "phonetic": "syllable",
        "evidence": "假设·未验收（仅在 30s 英文试听段验证过音节路径，无整曲 A/B）",
    },
}

# 要显式启用需设 TS_LANG_MODE_ALLOW_EN=1（并自行承担未验收风险）。
DISABLED_BY_DEFAULT = {"en"}

ENV_ALLOW_UNVERIFIED = "TS_LANG_MODE_ALLOW_UNVERIFIED"

CODE_NAMES = {"zh": "中文(普通话)", "yue": "粤语", "ja": "日语", "en": "英语",
              "default": "出厂默认", "unknown": "未判定"}


def mode_names():
    return sorted(PRESETS.keys())


def phonetic_unit(code):
    """该语种的**音节对齐单位**：'mora'（日语摩拉）/ 'syllable'（英语元音核）/ 'none'。

    供 `asr_refine.phonetic_timeline()` 之类的调用方决定按哪种单位切分。
    """
    p = PRESETS.get((code or "").strip().lower()) or {}
    return p.get("phonetic", "none")


def enabled():
    """语言模式是否启用（默认 off → 管线行为等同改动前）。"""
    return os.environ.get(ENV_LANG_MODE, "off").strip().lower() in ("auto", "1", "on")


def allow_unverified():
    return os.environ.get(ENV_ALLOW_UNVERIFIED, "0") == "1"


def get_preset(code):
    """取某语种的完整预设（已与 default 合并）。不启用也返回，供诊断用。"""
    p = dict(DEFAULT)
    code = (code or "default").strip().lower()
    extra = PRESETS.get(code)
    if not extra:
        p["_code"] = code if code in PRESETS else "default"
        p["_evidence"] = "无专用预设，采用出厂默认"
        return p
    for k, v in extra.items():
        if not k.startswith("_"):
            p[k] = v
    p["_code"] = code
    p["_evidence"] = extra.get("evidence", "与出厂默认一致" if not extra else "—")
    return p


def resolve(code, force=False):
    """**管线唯一应调用的入口**：返回最终生效的预设。"""
    if force:
        return get_preset(code)
    if not enabled():
        p = get_preset("default")
        p["_reason"] = "%s 未启用（默认 off）" % ENV_LANG_MODE
        return p
    c = (code or "unknown").strip().lower()
    if c not in PRESETS or c == "default":
        p = get_preset("default")
        p["_reason"] = "无 %s 专用预设" % c
        return p
    if c in DISABLED_BY_DEFAULT and not allow_unverified():
        p = get_preset("default")
        p["_reason"] = "%s 预设证据不足（未验收），需 %s=1 才启用" % (c, ENV_ALLOW_UNVERIFIED)
        return p
    p = get_preset(c)
    p["_reason"] = "已启用 %s 预设" % c
    return p


def diff(code):
    """返回 (code, default) 的差异表 [(键, default值, 该语种值)]。"""
    a, b = get_preset("default"), get_preset(code)
    out = []
    for k in DEFAULT:
        if a[k] != b[k]:
            out.append((k, a[k], b[k]))
    return out


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="语种 → 人声识别模式预设表")
    ap.add_argument("--diff", default=None, help="只打印某语种与 default 的差异")
    a = ap.parse_args(argv)

    print("TS_LANG_MODE 当前 = %r  → 语言模式%s"
          % (os.environ.get(ENV_LANG_MODE, "off"), "已启用" if enabled() else "未启用(off)"))

    def show(code):
        print("\n[%s] %s" % (code, CODE_NAMES.get(code, code)))
        d = diff(code)
        if not d:
            print("  与出厂默认**逐值一致**（零行为变化）")
        else:
            for k, dv, v in d:
                print("  %-18s %-8s -> %-8s" % (k, dv, v))
        p = get_preset(code)
        if p.get("_evidence") and p["_evidence"] not in ("—",):
            print("  证据：%s" % p["_evidence"])

    if a.diff:
        show(a.diff)
    else:
        print("\n出厂默认值：")
        for k, v in DEFAULT.items():
            print("  %-20s %s" % (k, v))
        for c in sorted(PRESETS):
            show(c)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
