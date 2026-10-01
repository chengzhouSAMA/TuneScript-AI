# TuneScript AI —— 交接文档（给下一个接手这个项目的 AI）

写于 2026-10-01。**读完这份再动手。**

这份记的是"光看代码看不出来"的东西：管线的两条路怎么分叉、哪些方案已经试过并且被回退了、
验收怎么跑、哪些结论验过哪些没验过、以及当前的真实状态。

配套文档（按需要读）：

| 文件 | 内容 |
|---|---|
| `CONSTRAINTS.md` | 质量地板：7 条 Floor + 编号约束 + 机器可解析的 RATCHET 表 |
| `lang_dev/_README.md` | 开发日志，149 节。**§147/§148/§149 是最近三轮**，含大量实测数字 |
| `README.md` | 面向用户的产品说明（含「已知问题」一节） |
| `lang_dev/check.py` | 验收流水线本体，判据的唯一权威 |

---

## 0. 三十秒版

- **干什么的**：音频 → 钢琴谱。产出 MIDI + 五线谱 PDF + 钢琴音色 WAV，全程本地 CPU 推理，不联网（除了下载音源/歌词）。
- **主入口**：`transcriber_app.py`（4400 行，GUI 与 `--cli` 同一个文件）。
- **出货形态**：一个 ~550 MB 的单文件 exe（PyInstaller）。
- **最重要的一条纪律**：任何影响转谱行为的改动，收尾必须重打 exe，并让 `check.py --stage full` 全绿。
- **最容易踩的坑**：这个项目有**两条互斥的管线**（分轨 / 回炉），而出厂产物大多数走的是**回炉**那条。
  大部分功能（R1/R2/间奏补音/语种分段）都长在分轨路径上 —— 你改了分轨路径，用户可能根本看不到。

---

## 1. 环境：**克隆下来跑不起来**，先看这条

仓库只受控 **87 个文件**（25 个根目录文件 + `dev/` 61 个）。下列全部**不在仓库里**，必须从原工作区拿到：

| 缺什么 | 在哪 | 大小 | 缺了会怎样 |
|---|---|---|---|
| `ffmpeg.exe` | 仓库**根目录** | 80 MB | 一切解码失败（任何非 WAV/FLAC/OGG 的输入都进不来） |
| `dist/mt3/mt3.pth` | `dist/mt3/` | 175 MB | MT3 转录不可用，退回常规流程 |
| `dist/piano_btd/*.pth` | `dist/piano_btd/` | 164 MB | 和弦识别不可用 |
| `lang_id_models/` | 根目录 | 3.5 GB | 语种识别不可用（会自动退回，不报错） |
| `lang_id_venv314/` | 根目录 | venv | Qwen 后端不可用（自动退回 Silero） |
| MuseScore 4 | `C:\Program Files\MuseScore 4\bin\MuseScore4.exe` | 外部安装 | 出不了 PDF |

**两套 Python，别搞混**：

- **主环境 Python 3.9**（Windows 10/11）。依赖见 `requirements.txt`。
- **副环境 Python 3.14.5**（`lang_id_venv314/`）。只给 Qwen 用，主程序用 `subprocess` 调它。
  为什么必须两个：`qwen-asr` 的源码用了 PEP 604（`X | Y`），3.9 运行时报 TypeError；
  它的依赖 `accelerate>=1.10` 也要求 ≥3.10。**不要试图合并成一个环境。**

`python` 这个进程名在本机是 `python3.9.exe`（排查进程时别按 `python.exe` 找）。

---

## 2. 管线：两条路 ★ 理解一切的地基

### 路 A：分轨路径（`transcribe_stems`）

```
音频 → Demucs htdemucs_6s 六轨
      ├─ vocals  → Basic Pitch      → 右手旋律
      └─ 伴奏轨   → ByteDance 钢琴   → 左手和声        → fuse_to_piano()
      语种分段（TS_LANG_SEG=1）就接在这里 ↑
```

### 路 B：回炉 / 简洁模式（`_simple_piano`）

```
整曲混音 → Basic Pitch → 按音高 split_pitch=60(C4) 硬切左右手 → _simple_piano()
```

### 出厂产物走哪条？

`transcriber_app.py` 里：先跑路 A 出一版，算 `_melody_similarity`（chroma+DTW）；
**成本 > 0.15 就回炉**（换路 B 重跑）。回炉一旦被采纳，**路 A 的结果整体丢弃**
（后面靠 R2b/R2c 把分轨素材部分补回来，但**补回来不是超集** —— 见代码里的注释）。

⇒ **用户看到的多半是路 B 的产物。** 判断某份产物来自哪条路的指纹：

- 路 B：左手 ≤59、右手 ≥60，**音域零重叠、切点正好在 60**（硬切特征）
- 路 A：有 R1 的八度间隔保证，且常带 `splice`（参考谱拼接小节）

---

## 3. 硬性规则（用户原话，不可协商）

| 规则 | 内容 | 实现位置 |
|---|---|---|
| **R1** | 人声必须在**右手**，且与伴奏至少相差**一个八度** | `_enforce_octave_gap()`（两遍：压左手 / 抬右手）+ `_hand_gap_min()` 度量 |
| **R2** | 无人声段（前奏/间奏/尾奏）要**加强伴奏识别**，包括 `other` 里的电子音 | `_build_accomp()` / `_accomp_legacy()`（回退路径）/ `_filter_high_hallucination` / `_sparsify_harmony_playable` |
| **R3** | **鼓不识别，贝斯优先性最后** | `ACCOMP_PRIORITY = piano>guitar>other>bass`、`_accomp_weights()`、`ACCOMP_LAST_W=0.40` |
| **左手** | 每时刻最多 1 音（只留最低音贝斯骨干），伴奏不杂不乱 | 分轨路径 `fuse_to_piano` 的 `fix_hand(max_notes=1/2, mode="accomp")` |

**不要自己改写这些规则的语义。** 改之前先看 `lang_dev/_README.md` 对应章节的实测数字。

---

## 4. 已经试过并被回退的方案 ★ 最省时间的一节

动手前先看这里，别重复别人已经付过学费的事。

| 试过的方案 | 结果 | 取证在哪 |
|---|---|---|
| **右手改 `mode="melody"`（t3-A）** | 合成竞争测试里有效，但在**出厂回炉路径**上对 5 首真实曲目近乎惰性：shiki 的产物**逐字节相同**；还把 t6「人声接入」打回滚（at 覆盖 76.54%→70.35%）。**已弃用** | `transcriber_app.py` 的 `_simple_piano()` 注释块（长注释，专门留给后人） |
| **回炉后丢掉分轨素材** | 这是已修的问题：R2b/R2c 把分轨伴奏补回左手空档、并进右手候选池。⚠️ 但**产物不是超集** —— 规则 B 是竞争性接受，塞进一个更高的 `other` 音会把原来那个顶掉 | `transcriber_app.py` 的 R2b 注释块 + `_test_handgap_accomp.py` 第 11 节 |
| **给 t11 候选池"加料"** | 同上，加料 ≠ 超集。改这块必须逐音对，不能只看总数 | 同上 |
| **`no_lyric` 当作"无歌词段不做音节化"的门控** | **还没落地** —— 目前只被统计，没有代码读它。想做可以，但要先补门控 | `lang_dev/_README.md` §149.7 |
| **语种预设当成已经生效** | 四个语种的**识别参数其实是同一套**，改了预设只换填充/对齐单位。先给真参数，再谈收益 | `lang_dev/_README.md` §149.5 |

另外两条硬教训（都写在 `lang_dev/_README.md` 里）：

- **`_melody_similarity` 对"内容增加"是非单调的** —— 回炉会把分轨结果整份丢掉。
  拿 sim 单独判优劣会误判（历史上判过 PASS 的退化）。
- **不动行为就不该重打 exe**；动了行为**必须**重打。

---

## 5. 验收：怎么知道你没弄坏

### 主命令

```powershell
$env:PYTHONIOENCODING = 'utf-8'      # ⚠️ 不设这个会有 9 项假红（GBK 编码报错）
$env:PYTHONUTF8 = '1'
python -X utf8 lang_dev\check.py --stage fast     # 7 项，改完随手跑
python -X utf8 lang_dev\check.py --stage task     # 13 项，提交前
python -X utf8 lang_dev\check.py --stage full     # 16 项，出货前（含 exe 验证 + 冒烟）
```

**`full` 全绿才算过。** 各档内容：

| 档 | 项 |
|---|---|
| fast | syntax（全部 .py 过 ast）、ruff、tree_sync、persona（已发布文件无角色化称呼）、unit（111 项）、fidelity（解码与制谱保真）、gui 接线 |
| task | floor（地板守卫 F1~F6）、floor_negctl（21 项阴性对照）、newui（54）、pushguard（18）、cli_contract（23）、selfcheck（104） |
| full | verify_exe（**61 项**，exe 内容层）、tree_sync_strict、smoke_exe（两臂真跑管线） |

### exe 专项

```powershell
python -X utf8 lang_dev\_verify_exe.py                    # 归档层 + 模块层 + 内容层，须 0 问题
python -X utf8 lang_dev\_verify_exe.py "<另一个exe>"        # 可指定路径对比
python -X utf8 lang_dev\_smoke_exe.py --arm both          # 真跑管线并校验产物 magic
```

⚠️ **`_verify_exe.py` 的 `WANT_MAIN` 是固定清单** —— 新增任何功能后，**必须把新符号加进去**，
否则旧版 exe 也能过，验证器就分辨不出装的是哪一版源码（这个坑 2026-10-01 刚踩过）。

### 冻结分轨回归（量产品质量）

```powershell
python 回归验收\regress_one.py --song jiabin --arm B        # 复用冻结六轨，跳过 Demucs
python 回归验收\regress_one.py --song jiabin --arm B --code <改动前的 transcriber_app.py 副本>
```

固定素材在 `回归验收/_stems/<key>/`，`--code` 可以把源码复制成私有快照再导入（防并发编辑污染）。
出 `sim` / `cost` / `n_notes` / `frag`（碎片率）四个数，结果落在 `回归验收/results/*.json`。

⚠️ **sim 是代理指标，不是真理。** 判优劣要看 sim + frag + 音数 + 人耳，并且**至少两首曲**。
⚠️ 单曲样本量偏薄的真实例子：studio 编配在 shiki 上两档指标**完全相同**（等于没生效），
jiabin 也几乎不变，只有 fanwut 明显。**别拿一首歌的结论定默认档。**

### 地板守卫

`lang_dev/_floor_guard.py` 拦六类"偷偷降低标准"：删/弱化断言、空 except、加 skip、放宽阈值等。
它自带 21 项阴性对照（`_check_floorguard.py`），改它必须两边都跑。

⚠️ **2026-10-01 的真实事故**：`_selfcheck.py` 里一条**按 diff 判**的断言，因为基线前移而
静默失效，被整条替换掉了 —— F3 报「删掉了一条断言」。**替换断言 = 拉低棘轮**，
不是"换个检查对象"。修法是：名字原样保留（F3 按断言文本前 60 字判身份，**改名等于删除**），
判据改成"当前源码里还在不在"。

⚠️ **F6 会把"文档里写下的模式名"当成凭据**（2026-10-01 实测）。`_floor_guard.py:40` 写的是
"F6 不设限 —— 密钥写进文档一样是泄露"，**没有白名单**，`CONSTRAINTS.md` 那条例外也管不到它。
本文件 §10 那句「提交前扫一遍：〈模式名〉...」就被判成 `MUSIC_U` cookie —— **误报，但守卫没错**。
⇒ 地板守卫在改了 `HANDOFF.md` 之后会持续报这一条；要引用模式名，别写成能被正则命中的形态。

---

## 6. 交付纪律（违反任何一条都算没做完）

### ① 每一版都要打进 exe

用户原话：「以后每一版都需要打进 exe」。四步缺一不可：

```powershell
# 1. 先确认 exe 没在跑（绝不静默杀掉正在跑的实例！会被用户抓到）
# 2. 备份当前 exe
Copy-Item 'dist\TuneScript AI V0.5.1.exe' 'dist\_backup_TuneScript AI V0.5.1.exe' -Force
# 3. 重打（约 16 分钟，放后台跑，别占着一次工具调用干等）
python -m PyInstaller --noconfirm --clean "音乐转谱器_V0.5.1.spec"
# 4. 验证 + 报出大小与 sha256
python -X utf8 lang_dev\_verify_exe.py
python -X utf8 lang_dev\_smoke_exe.py --arm both
```

- ⚠️ 打之前确认**构建目录（仓库根）没有 `netease_cookie.txt`** —— 有的话 spec 会把它烤进要分发的 exe。
  构建日志里会有 `[spec] 构建目录没有 netease_cookie.txt，本次不烤入 cookie`，看见这句才安全。
- 只改注释/文档的轮次可以不打，但**必须在报告里写明"本轮无需重打，原因是…"**，不许默不作声跳过。

### ② 推送被闸门挡住（设计如此，不要绕过）

`_gh_repo/.git/hooks/pre-push` 装着 `dev/push_guard.py`，**默认拒绝一切 push**，
只有 `TS_PUSH_KEY` 环境变量与钥匙文件对上才放行。这是用户明确要求的。

⇒ **AI 只能提交，不能推送。** 收尾时把命令交给用户自己执行：

```powershell
cd "<工作区>\_gh_repo"
$env:TS_PUSH_KEY = (Get-Content "$env:USERPROFILE\.tunescript_push_key")
git push
Remove-Item Env:\TS_PUSH_KEY
```

### ③ 工作区是唯一源，仓库靠工具同步

- 源码在工作区；git 在 `_gh_repo/`（克隆）。
- **同步用 `python lang_dev/_sync_repo.py`**，它按 `git ls-files` 自动推。**不要手写复制列表**
  —— 历史上错过两次（漏了 `en_phoneme.py` / `bilibili.py` / `CONSTRAINTS.md`）。
- 出货前跑 `_check_tree_sync.py --strict`（克隆不能落后于工作区）。
- 新增文件要手动 `git add`（`_sync_repo.py` 只同步已跟踪文件）。

---

## 7. 已知边界与坑

### 语种 / LID —— 完整取证在 `lang_dev/_README.md` §149

十条，逐条对着代码核过。摘要：

- **语种分段只接在分轨路径，而出厂产物走回炉 ⇒ 收益未验证，默认关着**（不是"没做完"）。
- **粤语分不开不是接线问题**：映射表早就备好（`lang_id.py:78`、`:239-240`），
  是 Silero/Qwen 都不吐 `cantonese`；而且 yue 预设与 zh 同值 ⇒ 判出来也没差别。
- **四个语种的识别参数是同一套**（一个 `bp_*` 键都没覆盖）。
- **Qwen 的语种是转写的副产物**，转写崩了整条分割退化；它**故意不实现 `predict()`**，别顺手补。
- **窗不是越长越好**：`W60/H30` 的准确率掉到 3/5（W30/H15 是 4/5）。
- ja/en 预设**都未验收**；en 只在 30 秒试听段验证过。
- `no_lyric` 只被统计、没被用作门控。

### 第三方接口（歌词 / 网易云）

随时可能变；**失败只报错、不中断转谱**。网易云三条：
不带 cookie 一律按未登录处理（黑胶会员也只给 320k）；有 cookie 时**必须先问 eapi**，
否则明文接口"成功但只给 320k"就返回（这就是"我明明是会员"那个 bug 的成因）；
降级是**静默**的，靠 `downgraded` 字段才看得见。免登录试听片段默认拒绝。

### Windows / 编码陷阱（条条真踩过）

- **PowerShell 会把中文 UTF-8 变成 U+FFFD**（不可逆）。要显示含中文的文件内容，用
  `lang_dev/_show_exe_log.py` 之类的脚本，或直接用 read 工具，**不要** `Get-Content | ...` 再回写。
- **不要用 `python -c "..."` 写含中文/引号的代码** —— 会被 shell 吃掉引号，
  报出莫名其妙的 `SyntaxError`。**一律写成 `.py` 文件再跑。**
- `pwsh` 不一定在 PATH 上；`.ps1` 文件在 Windows PowerShell 5.1 里**按 ANSI 读**，
  含中文的脚本会解析失败 —— 改用 Python 脚本。
- **CRLF/LF 混用**会让 `difflib` 和 `git`（Myers）对同一处给出不同对齐，
  于是 `_selfcheck` 会把上下文行报成"被删的行"。登记在 `known_del` 里并写明成因。

### 其它

- 改 `transcriber_app.py` 时注意 **`ruff.toml` 的行长是 110**，且**禁止 `# noqa`**
  （要抑制请写进 `ruff.toml` 的 per-file-ignores 并写明原因）。
- `lang_modes.py:18-19` 硬规矩：`DEFAULT` 的数值**必须与当前出厂行为逐值一致**，改前跑全量 5 曲验收。
- `备份/pre_*` 是改动前快照链，`_selfcheck` 拿最新的那个当 diff 基线。**建新基线时想清楚**。

---

## 8. 当前状态（2026-10-01）

| | 值 |
|---|---|
| 出货 exe | `dist/TuneScript AI V0.5.1.exe`，**547.78 MB** |
| sha256 | `ED5DF2FA137D6E6F5B17EB535DCF14D8DBC3E882FABE29EDD330119887EC7BDC` |
| 上一版备份 | `dist/_backup_TuneScript AI V0.5.1.exe`（547.8 MB，`D3DEDF56…`，更早一版）；本轮快照 `dist/_backup_pre_stempick_TuneScript AI V0.5.1.exe`（550.24 MB，`16E5E2D7…4FFD`） |
| 桌面快捷方式 | `C:\Users\35968\Desktop\TuneScript AI V0.5.1.lnk` |
| 验收 | fast 7/7、task 13 项 **12 绿**、full 16 项 **15 绿** —— 唯一红是地板守卫 F6 误报（见 §5）；`_verify_exe` **70 项 0 问题**；冒烟 OFF 11/11 + ON 12/12 |
| 远端 main | `232d38c` |
| 本地 | 领先 3 个提交（`5e7c757` / `260291e` / 分轨勾选那轮 待推）；工作区干净 |

**残留物**（用户知道的，别自作主张清理）：

- 两个 TuneScript 进程自 01:44 起一直跑着（PID 14028 / 38796），启动自
  `dist/fidelity_candidate/`。**不要静默杀掉正在跑的实例。**
  （2026-10-01 09:2x 复核：这两个进程**已经不在**了，进程列表为空 —— 那时才敢重打 exe。）
- `dist/` 里有 **2277 MB 的 exe**：出货 + 备份 + 两个候选
  （`studio_candidate/` 是出货版的逐字节副本，可删；`fidelity_candidate/` 被上面两个进程占着）。
- `promo_video/` 是宣传片工作区（2.4 GB 媒体 + pip 依赖），**已 gitignore**，不是出货源码。
- `回归验收/out/`、`备份/`、`转谱验证/` 都是本地素材，不入库。

---

## 9. 文件地图

### 出货代码（这 25 个文件会被同步进仓库）

| 文件 | 说明 |
|---|---|
| `transcriber_app.py` | **主程序**（4400 行）：管线 + GUI + `--cli`。改动先读第 2、4 节 |
| `ui_app.py` / `ui_kit.py` | 新版多入口 UI + tkinter/ttk 主题 |
| `audio_crop.py` | 音频裁剪 + 语种分段（`segment_by_language`） |
| `lang_id.py` | LID 适配层：Silero ONNX 后端 + Qwen sidecar 后端 |
| `lang_id_qwen_runner.py` | Qwen sidecar 的入口（跑在 3.14 环境里） |
| `lang_modes.py` | 语种预设表（`DEFAULT` / `PRESETS` / `DISABLED_BY_DEFAULT`） |
| `lang_pipeline.py` | 「语种分割 → 逐段扒谱 → 拼回全局时间轴」封装 |
| `asr_refine.py` | 音节/摩拉切分、强制对齐、音节网格 |
| `ja_romaji.py` / `en_phoneme.py` | 日语罗马音（pykakasi）/ 英语音素（cmudict） |
| `lyrics_fetch.py` / `lyrics_match.py` | 联网取歌词 + 逐窗比对（产出 `no_lyric`/`hallucination` 判定） |
| `netease.py` / `netease_login.py` | 网易云 weapi 加密、扫码登录、音质降级记录 |
| `bilibili.py` | B 站音频下载 |
| `make_icon.py` | 生成图标 |
| `音乐转谱器_V0.5.1.spec` | **出货用的 spec**（另两个是历史版本） |

### 工具（`dev/` ← 工作区 `lang_dev/`）

`check.py`（验收流水线）、`_verify_exe.py`、`_smoke_exe.py`、`_selfcheck.py`、
`_floor_guard.py` + `_check_floorguard.py`、`_test_handgap_accomp.py`（111 项单元自检）、
`_test_fidelity.py`、`_check_gui.py` / `_check_newui.py` / `_check_cli_contract.py` /
`_check_pushguard.py` / `_check_tree_sync.py`、`_sync_repo.py`、`push_guard.py`、
`_strip_persona.py`、`_README.md`（开发日志 149 节）。

### 历史包袱（不是出货链路，别照它们改）

| 目录/文件 | 是什么 |
|---|---|
| `ai_transcriber_dev/` | 早期训练实验（`paddle` / `modelscope` / `huggingface_hub` 那些 import 全在这里） |
| `amt_test/` | 旧方案（`nussl`） |
| `TuneScript-AI/` | V0.1 时期快照，`.git` 已丢 |
| `_vfy_*.py`（根目录 30+ 个） | 历史验证脚本，散落 |
| `项目备忘.md` / `项目备忘2.0.md` | 用户早期的项目笔记（105 KB），已被 `lang_dev/_README.md` 取代 |
| `promo_video/` | 宣传片工作区 |
| `回归验收/` `转谱验证/` `备份/` | 回归素材 / 冻结基线 / 改动前快照 |

---

## 10. 公开仓库注意事项

仓库是**公开的**：<https://github.com/chengzhouSAMA/TuneScript-AI>

- **绝对不要把凭据写进任何会入库的文件** —— cookie / token / key 一律走环境变量，
  或者落在 `.gitignore` 覆盖的文件里（`netease_cookie.txt` 就是这种）。
  提交前扫一遍：`MUSIC_U=` / `ghp_` / `TS_PUSH_KEY`。
- 历史上曾有凭据被提交进仓库；用户**已知晓**并选择不重写 git 历史。
  **不要去"顺手"引用或复述具体位置**，也不要把任何凭据写进新文件或日志。
- 入库的只有 87 个文件。模型、venv、`ffmpeg.exe`、音源、回归产物、宣传素材**都不入库**。
- `README.md` 是公开门面，写面向用户的说明；**开发过程与技术细节写在 `lang_dev/_README.md`**。

---

## 11. 如果用户没给具体任务，三个候选方向

1. **把语种分段的收益验证出来** —— 前置是把它接进回炉路径（现在只接分轨），
   否则 A/B 一定测不出差异。见 §149.1、§149.9。
2. **给语种真参数** —— 现在四个语种共用出厂识别参数，`PRESETS` 只改填充与对齐单位。
   这是上一条的前置（§149.5）。
3. **补齐回炉路径的左手质量** —— `_simple_piano` 的左手现在是 studio 档
   `mode="accomp"` 最多 3 音，而不是分轨路径的"最低音贝斯骨干"。先量再改。

**三件都不要在单曲上定结论。** 这个项目最贵的教训就是：shiki 上"完全没差别"的改动，
在 fanwut 上是 +0.013。
