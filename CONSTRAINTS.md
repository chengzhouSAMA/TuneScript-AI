# Constraints

本项目的"够好到能出货"的定义。**改动前先读这个文件；不要为了让自己通过而削弱它。**

最后复核：2026-09-25。数字全部来自本机实测，每条都写明产出它的命令。

跑法（唯一的入口）：

```
python lang_dev/check.py --stage fast    # 每次编辑后      （实测 0.9 秒）
python lang_dev/check.py --stage task    # 认为做完了      （实测 12.2 秒）
python lang_dev/check.py --stage full    # 出货前（要 exe） （分钟级）
```

---

## 地板（永远生效，不需要装任何东西）

- **不许新增抑制注释来让检查变绿**：`# noqa`、`# type: ignore`、`pylint: disable`、
  `nosemgrep`、`gitleaks:allow`、`istanbul ignore`、`Stryker disable`。要修的是代码。
- **不许把失败变成沉默**：新增的空 `except: pass`、`raise NotImplementedError` 占位。
- **不许削弱测试**：新增 `@unittest.skip`、删掉 `check(...)` 断言。
  确实要跳过，就在提交信息里写清原因。
- **不许让凭据进源码**：cookie、token、私钥。命中只报位置，**不回显值**。
- **不许下调本文件的 RATCHET 数字**。收紧可以悄悄做，放松必须响。
- **不许 `git push --no-verify`**；推送由使用者本人带钥匙执行。
- **本文件不许在同一个提交里既改功能又放宽。**

上面这些由 `lang_dev/_floor_guard.py` 按 **diff** 检查（含未跟踪的新文件），
它自己的有效性由 `lang_dev/_check_floorguard.py` 的**阴性对照**证明
（19 项：每一条作弊都要能把它弄红，干净改动必须不能，规则书本身不能被咬）。

---

## 带数字、有机器的约束

| 维度 | 规则 | 检查命令 | 何时跑 |
|---|---|---|---|
| 地板 | 0 条命中 | `python lang_dev/_floor_guard.py` | 每次编辑 |
| 称呼口径 | 已发布文件 0 处角色化称呼 | `python lang_dev/_strip_persona.py --check` | 每次编辑 |
| 语法 | 所有 `.py` 过 `ast.parse` | `python lang_dev/check.py --stage fast` | 每次编辑 |
| 单元自检 | 89/89 | `python lang_dev/_test_handgap_accomp.py` | 每次编辑 |
| GUI 接线 | 0 问题 | `python lang_dev/_check_gui.py` | 每次编辑 |
| 地板守卫自身 | 19/19 | `python lang_dev/_check_floorguard.py` | 任务结束 |
| 新版 UI | 54/54 | `python lang_dev/_check_newui.py` | 任务结束 |
| 推送闸门 | 18/18 | `python lang_dev/_check_pushguard.py` | 任务结束 |
| 归档与冻结基线 | 99/99，且 `_stems/` 无新写入 | `python lang_dev/_selfcheck.py` | 任务结束 |
| exe 内容层 | 0 问题 | `python lang_dev/_verify_exe.py` | 出货 |
| exe 冒烟 | OFF 11/11、ON 12/12 | `python lang_dev/_smoke_exe.py --arm both` | 出货 |
| 备份纪律 | 每轮一份源码快照 | 人看 | 改源码时 |
| exe 打包 | 每版都出 exe（注释/文档轮可免，须写明理由） | 人看 | 出货 |

**两条纪律**（都吃过亏）：

1. **成本决定位置**：几秒内跑不完的检查不要塞进编辑循环。
   `_selfcheck.py` 实测 28.3 秒（它要加载 LID 模型），所以它属于 `task` 不属于 `fast`。
2. **至少留一条外部意见**：本项目里"外部"= **原曲音频本身**（chroma+DTW 相似度、
   PDF/MIDI 的魔数、冻结分轨）。全是自己写的断言等于自己批自己作业。

---

## RATCHET（记下今天的数，只许更好）

| 指标 | 当前值 | w/s |
|---|---|---|
| 单元自检通过数 | 89 | w |
| 归档自检通过数 | 99 | w |
| 地板守卫阴性对照 | 19 | w |
| 推送闸门自检 | 18 | w |
| 全曲 sim fanwut | 0.8961 | w |
| 全曲 sim shiki | 0.8884 | w |
| 全曲 sim jiabin | 0.9410 | w |
| 碎片率 fanwut | 0.0081 | s |
| 无人声段右手最长空档 inhuman（秒） | 2.38 | s |
| 无角色化称呼 | 0 | s |

方向：`w` = 越大越好，`s` = 越小越好。**只用实测跑出来的数更新这张表**，
产出命令见上面的表（sim/碎片率/空档来自 `回归验收/regress_one.py` 与
`lang_dev/_diag_frag_interlude.py`）。

> 为什么用"记下今天的数"而不是拍一个目标：本项目 62% 的时候拍 80% 只会永远红。
> 现在的做法是**拒绝变差**，变好就更新。

---

## 例外

| ID | 规则 | 路径 | 原因 | 到期 |
|---|---|---|---|---|
| E1 | `_floor_guard.py` 不查 `备份/` 与 `回归验收/` | 那两个目录 | 历史快照与验收产物，不是要出货的代码 | 2026-12-31 |

新增例外要在**代码评审里被看见**，并写清到期时间。守卫会把"悄悄新增例外行"报成 F5。

---

## 不适用

这个是桌面应用，没有 URL 可打：**Lighthouse / axe / 覆盖率门槛 / 依赖扫描不适用**，
不要为了凑数发明跑不起来的检查。（依据 constraint-driven-development：
没有 URL 的维度应该**删掉**，而不是编一个永远绿的检查。）

---

## 调试与修复循环（依据 break-ai-fix-loops）

改 bug 或修红检查时，三条硬规矩：

1. **一次验收声明最多三次尝试**。换了说法、重开会话、换个模型都不重置预算。
2. **修之前先复现**；复现不了就报 `INCONCLUSIVE`，不要靠猜去改。
3. **指纹重复 = 立刻换思路**：补丁变了但可观测状态没变、聚焦测试过了但真实路径还错 →
   停下编辑，换一个因果机制，先取一条能区分新旧假设的观测。

**证明要配得上声明**：改的是 CLI，就得按用户的方式跑一遍入口
（`transcriber_app.py --cli ...`）；改的是 exe 内容，就得从 exe 里把代码对象挖出来看
（`_verify_exe.py` 的内容层）。单元测试、类型检查、构建成功都**只是旁证**。

**给新写的检查配阴性对照**：先把已知坏状态喂给它，确认它**会红**。
装推送闸门那轮就是靠这个才发现第一版是假闸门（钥匙文件存在就放行）。
