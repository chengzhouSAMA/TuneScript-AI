# -*- coding: utf-8 -*-
"""lang_modes.py — 语种 → 人声识别模式的预设表。

设计原则（对齐项目纪律）
------------------------
1. **`default` 预设与出厂现状逐值一致** —— 也就是说，在语言模式未启用或语种
   判为 unknown 时，管线行为**逐字节等同改动前**。这是本项目反复强调的红线。
2. **每个差异都标注证据强度**：`evidence` 字段写清该值来自哪次实测；没有实测支撑的
   一律写 "假设·未验收"。**未验收的差异绝不能在默认路径上生效。**
3. **纯数据 + 纯函数**，不 import 任何重依赖，可被 GUI / CLI / 测试独立引用。
4. **一键回退**：`TS_LANG_MODE=off`（默认）时 `resolve()` 永远返回 `default`。

绑定的真实参数（均已在 `transcriber_app.py` 中存在，2026-09-20 核对）
--------------------------------------------------------------------
| 预设键              | 落点                                            | 现状默认 |
|---------------------|-------------------------------------------------|----------|
| `bp_min_len_vocal`  | `transcribe_notes(..., min_len=)` 人声轨         | 60 ms    |
| `bp_min_len_song`   | `transcribe_notes(..., min_len=)` 整曲/回炉      | 127 ms   |
| `bp_onset_threshold`| Basic Pitch `onset_threshold`                    | 0.45     |
| `fill_win`          | `_melody_line(window=)`  → `TS_RIGHT_FILL_WIN`   | 0.06 s   |
| `fill_gap`          | `_dejitter_melody(onset_gap=)` → `TS_RIGHT_FILL_GAP` | 0.12 s |
| `fill_rate`         | `_fill_right_hand(max_per_sec=)` → `TS_FILL_RATE`| 2.0 音/秒 |
| `fill_dens`         | `_fill_right_hand(dens_target=)` → `TS_FILL_DENS`| 2.2 音/秒 |
| `fill_instr_rate`   | `_fill_right_hand(instr_rate=)` → `TS_FILL_INSTR_RATE` | 2.5 音/秒 |

用法
----
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
# `evidence` 必须可追溯到项目内的一次实测或一条代码注释；写不出就写 "假设·未验收"。
PRESETS = {
    "default": {},

    # ---- 中文（含合成人声：洛天依/星尘/诗岸等）----
    # 现状：LID 在 5 首固定素材上把中文全部判对（fanwut/gouzhi/jiabin 均 zh，见
    # lang_dev/_eval_lid_result.json）。zh 预设**故意保持与 default 完全一致**：
    # 中文是本项目的既有主场景，出厂参数本就为它调过，没有实测依据去改。
    "zh": {},

    # ---- 粤语 ----
    # LID 实测：粤语被稳定判为 zh（40 窗中仅 4 权重给 yue），即**模型分不开粤语与普通话**。
    # 因此 yue 预设与 zh 同值；保留该键是为了将来换更强的 LID 后端（Qwen3-ASR 等）后能直接生效。
    "yue": {},

    # ---- 日语 ----
    # 依据（代码内既有注释，transcriber_app.py L939/L1088/L1508）：
    #   「日语等多音节语言一字一音、同音反复极多，绝不能合并成一条长音」。
    # 现状 default 的 fill_gap=0.12 已经低于真人快音节的 ~0.25s 间隔，故**不再下调**；
    # 这里只把 fill_win 从 0.06 收紧到 0.05：
    #   证据：附-14.5 实测「onset_gap 0.22→0.12 术力口 +0.0036」——**在 ±0.01 噪声地板内**，
    #   属"方向合理但未达显著"。**未验收**，故仅在 TS_LANG_MODE=auto 时生效。
    "ja": {
        "fill_win": 0.05,
        "evidence": "方向合理但未达显著（附-14.5，+0.0036 < ±0.01）；待全量 A/B 验收",
    },

    # ---- 英语 ----
    # 英语音节更长、辅音簇多，碎音合并可以更激进一点（fill_gap 0.12→0.16）。
    # **纯假设·未验收**：本项目至今没有一首英语测试曲，没有任何实测依据。
    # 因此**默认不启用**该预设（见 DISABLED_BY_DEFAULT）。
    "en": {
        "fill_gap": 0.16,
        "evidence": "假设·未验收（项目无英语测试曲，无任何实测依据）",
    },
}

# 证据不足、**默认不生效**的语种：即使 TS_LANG_MODE=auto，`resolve()` 也返回 default。
# 要显式启用需设 TS_LANG_MODE_ALLOW_EN=1（并自行承担未验收风险）。
DISABLED_BY_DEFAULT = {"en"}

ENV_ALLOW_UNVERIFIED = "TS_LANG_MODE_ALLOW_UNVERIFIED"

CODE_NAMES = {"zh": "中文(普通话)", "yue": "粤语", "ja": "日语", "en": "英语",
              "default": "出厂默认", "unknown": "未判定"}


def mode_names():
    return sorted(PRESETS.keys())


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
    """**管线唯一应调用的入口**：返回最终生效的预设。

    - `TS_LANG_MODE` 非 auto（默认）→ 永远返回 default（零行为变化）
    - 语种为 unknown / 无专用预设 / 在 DISABLED_BY_DEFAULT 且未显式放行 → default
    - `force=True`：诊断/实验用，绕过所有开关（**不要在产品路径上使用**）
    """
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
