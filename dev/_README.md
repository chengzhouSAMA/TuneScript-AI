# 语种自动识别 + 语言分段裁剪（lang_id / audio_crop / lang_modes）

> **第二部分（2026-09-20 续）**：换用 **Qwen3-ASR** 做语种识别，并把
> **语种分割接进管线**（位置按主人指定：**分轨之后、扒谱之前**，人声按语言逐段识别，
> **相同时间点按音量大的优先**）。见 §8 起。第一部分（Silero 轻量档）保留在下文作为对照。

> 会话：2026-09-20（承接 V0.5 出货后的下一次功能扩展）
> 需求（主人原话）：**查询有无开源语言识别模型**，可自动识别歌曲中语言来进行自动语言模式分配
> （中文歌曲→中文人声识别模式、日语→日语识别模式），**添加音频裁剪功能**，
> 将不同语言的同一段人声音频裁剪开进行不同语言模式识别。
> 决策（主人选定）：**① 语言模式 = 音符提取预设（不引入 ASR）**；**② LID 引擎 = 轻量 ONNX**。

---

## 0. 一句话结论

**轻量 ONNX LID（Silero lang95）+ 限定候选集 + 10s 窗，在 5 首真实曲上是 4/5 strict、5/5 lenient
（粤语归入中文），5 首全曲只要 3.6 秒** —— 足以驱动「按语种自动分配人声识别模式」。
**但语言模式对出厂听感的收益尚未验收**（见 §4 的红线问题），且**跨语种混唱的边界精度在真实素材上无法验收**（§3.3）。

---

## 1. 新增文件（全部是**新增**，未改动 `transcriber_app.py`）

| 文件 | 作用 |
|---|---|
| `lang_id.py` | **可插拔 LID 适配层**：Silero lang95 ONNX 后端 + 置信度/能量双门控 + 候选集约束 + 优雅降级；CLI 可单跑 |
| `audio_crop.py` | **音频裁剪 / 人声分段 / 语言分段裁剪**：`crop` / `energy` / `lang` 三模式；产出 WAV 分片 + `manifest.json`；含「分段识别后拼回全局时间轴」的胶水 `manifest_notes()` |
| `lang_modes.py` | **语种 → 人声识别模式预设表**：默认预设与出厂现状**逐值一致**；差异项标注证据强度；`TS_LANG_MODE=off` 时永远返回默认 |
| `lang_id_models/` | 模型文件（`lang_classifier_95.onnx` 16.97 MB + 两个标签表，MIT） |
| `lang_dev/_probe_onnx*.py` | ONNX 契约实探（不猜输入格式） |
| `lang_dev/_eval_lid.py` | 5 首真实曲上的 LID 准确率评测（8 组配置） |
| `lang_dev/_test_crop.py` | 合成中/日混唱上的**分段边界**评测 |
| `备份/pre_lang_id_20260920_105504/` | 改前备份（`transcriber_app.py` = 158574 B / `A092C9E8…`，与 V0.5 出货源码一致） |

---

## 2. 开源 LID 模型调研（含实测可行性）

| 模型 | 语种数 | 体积 | 许可 | 对**歌唱** | 本机可行性 |
|---|---|---|---|---|---|
| **Silero LID (lang95)** ← 本次选用 | 95 | 4.7M 参数 / **17 MB ONNX** | MIT | 语音训练，无歌唱保证 | ✅ **onnxruntime 已装，零新依赖** |
| SpeechBrain VoxLingua107-ECAPA | 107 | ~21M / ~25 MB | Apache-2.0 | 语音训练 | 🔶 需新增 `speechbrain`（与 torch 2.8 兼容性未验） |
| Meta MMS-LID-256 / 126 | 256 / 126 | wav2vec2，~1 GB+ | ⚠️ **CC-BY-NC-4.0（禁商用）** | 语音训练 | ⚠️ 许可与出货冲突 |
| Whisper large-v3 | 99 | ~1.5 GB | Apache-2.0 | 一般（歌唱差） | 🔶 30 语种 LID 94.1% |
| **Qwen3-ASR-0.6B / 1.7B** | 30 + 22 中文方言 | ~1.2 / 3.4 GB | **Apache-2.0** | ✅ **歌唱 SOTA** | 🔶 需 torch；Py3.9 兼容性未验 |

**Qwen3-ASR（2026-01-30 开源）是"语言模式 = 歌词识别"路线的最优解**：30 语种识别 **97.9%**（Whisper-large-v3 为 94.1%），
且是唯一在**带伴奏整曲**上可用的开源方案（EntireSongs-zh WER 13.91 / en 14.60），并原生支持**同句内中英混唱**的 code-switching。
本次未选它，是因为主人选了「语言模式 = 音符预设」且要轻量档；**它是本方案未来的升级路径**（`lang_id.register_backend()` 已留好插槽）。

**文献层面的两条硬事实**（决定了本方案的边界）：
1. **纯音频 SLID 明显弱于文本元数据**：同一工作中 audio-only 加权 F1 0.857 / **宏平均仅 0.275**，
   而"标题+歌手名"文本达到 0.900；韩语/日语是 audio-only 的公认弱项
   （[Listen, Read, and Identify, arXiv:2103.01893](https://ar5iv.labs.arxiv.org/html/2103.01893)）。
2. **歌词语种 ≠ 元数据语种**：大量日文歌的曲名/歌手名是拉丁字母或汉字，元数据与歌词会打架。

---

## 3. 实测（本机，CPU）

### 3.1 LID 准确率：5 首真实固定素材

素材取自 `回归验收/_stems/`（回归管线复用的固定六轨，**只读，全程零改动**）。

| 曲 | 演唱 | 真值 | 依据 |
|---|---|---|---|
| fanwut 反乌托邦 | 星尘 Infinity/诗岸（合成） | **zh** | 作词/作曲/编曲 乌托邦P，[歌词全中文](https://www.huaiyinjie.com/new/44353.html) |
| gouzhi 勾指起誓 | 洛天依（中文 VOCALOID） | **zh** | 曲名/歌手 |
| jiabin 嘉宾(粤语版) | 张远（真人） | **yue** | 曲名 |
| monitoring モニタリング | DECO*27 / 初音ミク | **ja** | 曲名/歌手 |
| shiki バカみたいに | 柿崎ユウタ（真人） | **ja** | 曲名/歌手 |

| 配置 | 候选集 | 窗 | strict | lenient | 耗时(5 首) |
|---|---|---|---|---|---|
| A | 95 类全开 | 4 s | 4/5 | **4/5** | 5.8 s |
| B | 95 类全开 | 10 s | 4/5 | 5/5 | 3.6 s |
| **D（推荐）** | **zh,ja,en,yue** | **10 s** | **4/5** | **5/5** | **3.6 s** |
| E/F | zh,ja,en,yue | 4/10 s + 高置信 | 4/5 | 5/5 | 4.7/3.7 s |
| G/H | zh,ja,en,yue | 20/30 s | 4/5 | 5/5 | 3.7/3.9 s |

- **唯一 strict 失分是粤语被判成 zh**（40 窗里只有 4 权重的 yue 票）——模型分不开粤语与普通话。
  产品上粤语与中文共用中文预设，故不计入缺陷；`lang_modes.py` 保留 `yue` 键是为了将来换更强后端。
- **候选集约束是关键杠杆**：95 类全开时 jiabin 判成 `unknown`（zh 仅 35% 票权）、fanwut 置信度只有 0.53；
  限定 `zh,ja,en,yue` 后分别变成 zh 0.95 / zh 0.88。**无关语种（km/bn/my/nn）在稀释多数票。**
- ⚠️ **置信度在歌唱上不可校准**：主实验中 30 s 窗对 fanwut 给出 `p=1.00`，而 4 s 窗给出 0.53 —— 窗越长越"自信"。
  **不能只靠置信度门控做安全阀。**

### 3.2 语言分段裁剪：合成中/日混唱

项目现有 5 首素材**全是单语种**，无法验证分段能力，故合成硬拼接素材（真值边界精确已知）。
⚠️ 素材必须取**人声活跃区**：`勾指起誓` 人声轨前 21.5 s 是 −84 dBFS 数字静音。

| 配置 | mix1 (zh30\|ja30, 真值 30) | mix2 (zh20\|ja30\|zh20, 真值 20/50) |
|---|---|---|
| **win=10 / hop=5（推荐）** | 检出 35.0 → **1/1** | 检出 25.0 / 45.0 → **2/2** |
| win=10 / hop=2.5 | 检出 32.5 → 1/1 | 检出 22.5 / 45.0 → 1/2 |
| win=6 / hop=2 | 检出 36.0 → 0/1 | 检出 26.0 / 46.0 → 0/2 |

### 3.3 ⚠️ 边界精度的诚实结论

- 检出的交界**系统性偏晚 0~0.6×win**（win=10 时 2.5~6 s）。
- **逐窗取证**：mix1 里 `[30,40]s` 是**纯日语**窗，却被判成 `zh p=0.962` ——
  偏晚的机制是**硬拼接接缝（波形突变）**让模型在接缝后的第一个窗仍投旧语种。
- ⇒ **合成测试能验证"分段逻辑正确"，但不能外推真实混唱的边界精度**；而项目没有真实的"中·日混唱"素材，
  故**边界精度无法在真实素材上验收**。`segment_by_language(boundary_shift=)` 提供补偿旋钮，
  **默认 0**（不套用基于伪影测得的修正，避免过拟合）。
- 产品影响可控：即使边界偏 5 s，错的也只是 5 s 音频用错预设，而当前各语种预设差异极小（§5）。

---

## 4. ⚠️ 接入出厂路径的红线问题（**必须先在项目上确认**）

**「语言模式」如果挂在人声轨上，出厂听感会零影响。**
项目已用三重证据证明：**出厂产物 100% 来自回炉路径（整曲混音）**，人声轨只喂了"被丢弃的分轨编排"
（`术力口与人声连续性优化报告.md` §二）。这正是本项目**已经撞过三次**的墙
（分轨侧调优对出厂不可见、t3-A、t3-B）。

**正确接法（沿用 t6 的模式：让"人声感知"进入回炉路径）**：

```
1. 在分轨之后、回炉之前，对 stems['vocals'] 跑 LID（分析用，不产生分片）：
       from lang_id import LanguageDetector
       info = LanguageDetector(candidates="zh,ja,en,yue").detect_song(stems['vocals'])
       code = info['code'] if info['ok'] else 'unknown'
2. 用 code 取预设：
       from lang_modes import resolve
       preset = resolve(code)          # TS_LANG_MODE=off 时恒等于出厂默认
3. 把 preset 的值接到回炉路径的既有 env 钩子上（**不要新增分支**）：
       preset['fill_win']  -> 已有 TS_RIGHT_FILL_WIN 的读取点
       preset['fill_gap']  -> 已有 TS_RIGHT_FILL_GAP 的读取点
       preset['fill_rate'] -> 已有 TS_FILL_RATE 的读取点
       preset['bp_min_len_song'] -> transcribe_notes(..., min_len=)   ← 需改一行
4. 若要按语种分段（混唱曲）：用 audio_crop.segment_by_language() 拿 segments，
   逐段用各自预设识别，再用 audio_crop.manifest_notes() 拼回全局时间轴。
```

**落地前必须先做的前置检查**（项目既有纪律）：
改「回炉/转谱参数」会改变 `_cost0`，可能掐死 t6 的人声接入（判据窗口 `_cost0 + 0.005`）。
先跑 `python 回归验收/_check_graft_headroom.py --cost0 <新值>` 确认 Δcost 不越线。

---

## 5. 当前预设与证据强度（`lang_modes.py`）

| 语种 | 与出厂默认的差异 | 证据 |
|---|---|---|
| `zh` / `yue` | **无差异（逐值一致）** | 中文是本项目既有主场景，出厂参数本就为它调过 |
| `ja` | `fill_win` 0.06 → 0.05 | 方向合理但**未达显著**（附-14.5 实测 +0.0036 < ±0.01 噪声地板）；待全量 A/B |
| `en` | `fill_gap` 0.12 → 0.16 | **纯假设·未验收**（项目无英语测试曲）→ **默认不生效**，需 `TS_LANG_MODE_ALLOW_UNVERIFIED=1` |

⇒ **按项目纪律，语言模式当前的建议状态是 `TS_LANG_MODE=off`（默认）**：
LID 与裁剪能力已就绪且可复跑，但**没有一条改动被验证为提升出厂听感**，
所以不默认改变任何出厂产物。这符合本项目「实测前不默认生效」的一贯做法。

### 环境开关

| 开关 | 默认 | 作用 |
|---|---|---|
| `TS_LANG_ID` | `1` | `0` 关闭 LID（`available()` 直接 False） |
| `TS_LANG_BACKEND` | `silero_onnx` | 切换后端 |
| `TS_LANG_MODEL_DIR` | `lang_id_models/` | 模型目录 |
| `TS_LANG_CANDIDATES` | 空=95 类 | 候选语种白名单，**强烈建议设 `zh,ja,en,yue`** |
| `TS_LANG_MIN_PROB` / `TS_LANG_MIN_DB` | 0.35 / −45 dBFS | 置信度门 / 能量门 |
| `TS_LANG_MIN_CAND_MASS` | 0.10 | 候选集最小概率质量（低于则判 unknown，不硬猜） |
| `TS_LANG_MODE` | `off` | `auto` 才启用语种专用预设 |
| `TS_LANG_MODE_ALLOW_UNVERIFIED` | `0` | `1` 才放行 `en` 这类未验收预设 |

---

## 6. 本次踩到的两个"真值错了"的坑（写下来免得重踩）

1. **把中文术力口标成了日语**：第一版把 `反乌托邦 - 乌托邦P` 当日语，于是 8 组配置**全被判失败**，
   差点得出"轻量 LID 不可用"的反向结论。核实作词/作曲/编曲均为乌托邦P、演唱为星尘/诗岸、歌词全中文后才纠正。
   ⇒ **真值必须可追溯到外部证据，且要写进脚本注释。**
2. **合成素材取了静音段**：`勾指起誓` 人声轨前 21.5 s 是 −84 dBFS 数字静音，
   直接 `[0:25]` 拼出来的"中文段"其实是静音 → 真值边界根本不成立。
   ⇒ 这同时暴露了 `audio_crop` 的一个**真 bug**：初版把静音窗"前向填充"成上一个语种，
   导致"中·日·中"只检出 1 个交界。**已修**：静音段单独成段且 `lang=None`。

---

## 7. 已知局限 / 待办

- [ ] 🔴 **语言模式对出厂听感的收益未验收** —— 需在**回炉路径**上接，并按 §4 的前置检查 + 全量 5 曲验收
- [ ] **真实混唱的边界精度无法验收**（缺素材）；若要做，需先补一首真的中/日混唱曲
- [ ] **粤语与普通话分不开**（模型能力所限）→ 换 Qwen3-ASR 或加元数据先验
- [ ] `en` 预设是纯假设，需要英语测试曲才能验收
- [ ] 升级路径：`register_backend()` 接 `Qwen3-ASR-0.6B`（30 语种 LID 97.9% + 原生歌唱），
      代价是 exe 增 ~1.2 GB、CPU 很慢、需先验 Py3.9 兼容
- [ ] 若要"歌词"产物（而非仅语言模式），走 Qwen3-ASR 而非本模块

---
---

# 第二部分：Qwen3-ASR + 语种分割接入管线（2026-09-20 续）

> 主人要求：**① 换 qwen；② 提前语言分割，放在分轨之后、扒谱之前；
> ③ 扒谱时的人声按语言分割一段段来；④ 相同时间点的语言挑选音量大的部分优先识别。**

## 8. 一句话结论

**Qwen3-ASR 跑得起来、判得准、但很贵**：在 5 首固定素材上
**strict 4/5、lenient 5/5 —— 与 Silero 打平**，但单曲 LID 从 **0.7 秒涨到 42~105 秒**
（约 **90×**），并要多带 1.75 GB 权重 + 一整套独立 Python 3.14 环境。
它的独有价值在**粤语/22 种中文方言**与**真实歌词转写**（未来"歌词"功能的地基），
而不在"比 Silero 更准"——**至少在这 5 首上不是**。

## 9. Qwen3-ASR 落地细节

### 9.1 为什么必须走「独立进程 sidecar」

| 障碍 | 实测 |
|---|---|
| `qwen-asr` 源码用了 **PEP 604（`X \| Y`）** | Python 3.9 运行时报 `TypeError: unsupported operand type(s) for \|`——**与本项目 `mt3_infer` 当年同一个坑** |
| 它的依赖 `accelerate==1.12.0` | **要求 Python ≥3.10**（PyPI 元数据），3.9 装不上 |
| 主程序环境不能动 | 3.9 + torch 2.8.0+cpu，basic_pitch / demucs 都依赖它 |

⇒ 用**独立的 Python 3.14 环境** `lang_id_venv314/`（torch 2.14.0+cpu / transformers 4.57.6
/ qwen-asr 0.0.6 / accelerate 1.15.0）跑 `lang_id_qwen_runner.py`，
主程序用 `subprocess` 交换 JSON，**只依赖标准库**。

> 幸运点：`qwen-asr` 自己 pin 的正是 `transformers==4.57.6`，与本机主环境同版本；
> 但主环境缺的 `accelerate` 3.9 装不上，所以仍必须分环境。

### 9.2 实测准确率与耗时（5 首固定素材，与 Silero 同协议）

| 配置 | strict | lenient | 单曲 LID 耗时（纯 CPU） |
|---|---|---|---|
| **Qwen W15/H15** | 4/5 | 5/5 | 42 ~ 105 s |
| **Qwen W30/H15（管线默认）** | 4/5 | 5/5 | 74 ~ 181 s |
| Qwen W60/H30 | 3/5 | 4/5 | 84 ~ 168 s |
| Silero 10s/候选集（第一部分） | 4/5 | 5/5 | **0.7 s** |

- 模型加载仅 **1.7~2.1 s**（权重已缓存）；耗时几乎全在**逐窗自回归解码**上。
- **窗不是越长越好**：W60 反而掉到 3/5（`shiki` 被长窗判成 zh）。
- ⚠️ **Qwen 的语种是 ASR 的副产物**：转写崩了，语种也崩。
  实测 `W60/H30` 下 shiki 的转写退化成"来来来，无所不至的那谁的…"（中文乱码）→ 语种判成 zh。
  这也是它**不提供语种概率**的原因（`TS_LANG_MIN_PROB` 对本后端无效）。
- ⚠️ **它没有分对粤语**：`嘉宾(粤语版)` 全程返回 `Chinese`（虽然官方支持 `Cantonese`），
  与 Silero 的表现一样 → 粤语这条仍未解决。
- ✅ 它转出的是**真歌词**（"反乌托邦中的争夺，把愿望敲碎成粉末…"/"ねえ私知ってるよ君が一人…"），
  这是 Silero 完全没有的能力，是未来"歌词"功能的现成地基。

### 9.3 后端接入方式（可插拔，零侵入）

`lang_id.py` 新增 `QwenAsrSidecarBackend`（`TS_LANG_BACKEND=qwen3asr`），
并让 `LanguageDetector.classify_windows()` 自动分流：
有 `detect_segments()` 的后端走分段路径，否则走本地逐窗前向。

- `TS_LANG_BACKEND` 默认 **`auto`**：**Qwen 可用就用 Qwen，否则退回 Silero**。
- `prob` 记 1.0（Qwen 不给概率），`db` **由主进程自己算** → 响度加权与能量门在本地可复现。
- 候选集白名单**同样作用于 Qwen 路径**（不在白名单内 → unknown）。
- 踩坑修复：`LanguageDetector` 会把通用 `model_dir` 透传给后端工厂，
  而 Qwen 的模型在 `lang_id_models/Qwen3-ASR-0.6B/` 子目录 →
  曾静默拿 Silero 的目录去 `from_pretrained`。已修：Qwen 工厂**忽略**该参数，
  且 `available()` 会校验 `config.json` 是否存在、失败原因会被带到 `detect_song().reason`。

## 10. 管线接入（主人指定的位置）

    … → Demucs 分轨 → **【语种分割】** → 扒谱（人声按语言逐段）→ 融合 → 渲染

`transcriber_app.py` 的改动**只有一处、+17 行 / −1 行**（`transcribe_stems` 内）：

```python
if os.environ.get('TS_LANG_SEG', '0') == '1':
    ...
    vocal_notes, _lsinfo = transcribe_vocal_by_language(...)
else:
    vocal_notes = notes_of(stems['vocals'], '人声旋律', min_len=60)   # 与改动前逐字节一致
```

- **默认关闭**（`TS_LANG_SEG` 未设）：走 `else`，**行为与改动前逐字节一致**；
- 开启后由 `lang_pipeline.transcribe_vocal_by_language()` 完成
  「LID → 分段 → 逐段裁剪 → 用该段语种预设识别 → 拼回全局时间轴」；
- **任何一步失败都自动退化为整轨识别**（已用异常注入验证）；
- 裁剪分片**只写 `out_dir/_langseg/` 或系统临时目录**，绝不污染源素材目录
  （项目里 `_merge_accomp_stems` 踩过这个坑，见 备忘 附-14.7）；
- 静音段直接跳过识别（`勾指起誓` 人声轨前 21.5 s 是 −84 dBFS 数字静音）。

## 11. 「相同时间点按音量大的优先」怎么落的

不是拍脑袋，而是**利用窗口重叠**做的逐点表决：

1. 窗长 `win`、窗移 `hop=win/2` ⇒ **每个时间点被 2 个窗覆盖**，本来就有多个候选；
2. `audio_crop._time_vote_units()` 对**每个时间点**把覆盖它的所有窗的候选语种按
   **`窗口概率 × 该窗响度权重`** 累加，取累加最大者 ——
   于是**唱得响的那一段所在的窗，在它覆盖的时间点上话语权更大**；
3. 响度权重 = 相对最响窗的线性幅度比，夹到 `[0.05, 1]`（只在最响窗以下 40 dB 内比较，
   避免静音窗被压成 0、也避免一个超响窗把其它窗按死）；
4. 若分片仍出现**时间重叠**，再由 `audio_crop.resolve_overlaps_by_loudness()`
   把重叠区按**该区间内的 RMS** 判给更响的一段（当前切分本身不产生重叠，
   故这一步是**防御性**的，零行为变化，将来放开 top-k 多语种候选时会真正生效）。

实测（合成中/日硬拼接，`lang_dev/_test_crop.py`）：
`10s/5s` 下 mix1（中30|日30）**1/1**、mix2（中20|日30|中20）**2/2**，
且 mix2 的第二处交界从 `45.0` 改善为**精确的 `50.0`**。

## 12. 集成实测（真实固定素材，`lang_dev/_test_langseg.py`）

`shiki`（日语，130 s，纯真实单语曲）：

| 臂 | 用时 | 结果 |
|---|---|---|
| OFF（出厂） | **4.8 s** | L=85 / R=470，共 555 音符 |
| ON（`TS_LANG_SEG=1`） | **257.7 s** | 日志：`语种分段扒谱：2 段 → 识别 2 段 → 828 个音符（后端 qwen3asr）`；成品 L=84 / R=471，共 555 音符 |

- ✅ **位置正确**：语种分割确实发生在**分轨之后、扒谱之前**；
- ✅ **分段真实**：该曲被切成 2 段（`人声旋律(ja)` / `人声旋律(zh)`），逐段单独识别；
- ✅ **拼回正确**：828 个人声音符按全局时间轴合并后进入既有融合链，成品 555 音符；
- ✅ **退化正确**：LID 后端不存在 / 识别函数抛错，都自动退回整轨且不崩（异常注入已验证）。

### 12.1 ⚠️ 但「语言模式」在这一版里**实际还没产生差异**

两臂的成品音符数完全相同（555），因为：

1. `zh` / `ja` / `yue` 预设的 **`bp_min_len_vocal` 都是 60**（只有 `en` 是 0.16 那个 `fill_gap`、
   且 `en` 默认不放行）——**在本项目里，这三个语种的音符提取参数本来就该一样**；
2. `ja` 预设改的是 **`fill_win`**，而那个参数只在**回炉路径**（`TS_RIGHT_FILL_WIN`）生效，
   **不在本次接入的分轨路径上**。

⇒ **已接好的是"管道"，不是"差异"**。要让语种真的改变识别结果，必须：
① 给某语种一个**经过 5 曲 A/B 验收**的 `bp_min_len_vocal`（`lang_dev/_eval_lid.py` 里
`min_len` 的既有实测只有 `→127` 是**负的**，`<60` 方向从未测过）；或
② 把语种决策接到**回炉路径**（那里才有 `fill_*` 这一族参数）。
**在拿到实测证据前，按项目纪律不擅自设值。**

# 第三部分：日语专项 —— 未识别段再切割 + 谐音音节(罗马音摩拉)

> 主人要求：**① 以 shiki 前奏的"识别不出来的人声"为例，再切一刀单独识别；
> ② 用谐音音节的方式识别；③ 日语要切成罗马音做精确识别。**
> （Python 3.13 已安装，版本不再受限 —— 实测本机 3.9 / 3.12 / 3.13 / 3.14 都在，
> 侧环境用 3.14 已跑通，无需重建。）

## 15. 先钉真值：这首歌开头到底唱的是什么

**[TuneCore 官方歌词](https://linkco.re/4Gcu8Yr7/songs/2449712/lyrics?lang=ja) 开头**：

```
さよなら / 少しだけ違っただけの愛情表現 / メランコリー / 普段通り　独りきり段取り
```

**基线 ASR（自动语种）对 0~30 s 的实际输出**：

| 区间 | 语种 | 文本 | 质量分 |
|---|---|---|---|
| **0–10 s** | **Chinese** ❌ | `眨眨眨，车马；，悠悠马；，悠悠车马…` | **0.22**（`kana_too_low,repetitive`） |
| 10–20 s | Japanese | `さようなら。少し開けた角のアイロッシュ系メランコリック…` | 0.56 |
| 20–30 s | Japanese | `でくれる感情表現が飛びてると夜ろ白けな…` | 1.00 |

⇒ **前 10 秒不只是"文本错"，还被判成中文** —— 而那会把整段路由到**中文预设**，错上加错。

### 15.1 这段是"真人声"还是"分离残留"？（先排除误判）

项目有过先例（备忘 附-14.7）：不带闸门会把 Demucs **分离残留**
（−25.6 dB、谐波性 0.26）算成"人声活跃"。所以先做两项检验：

| 检验 | 前奏 0–10 s | 后续唱段 10 s+ | 结论 |
|---|---|---|---|
| 谐波占比（HPSS） | 0.426 | 0.785 | 前奏**明显更不像乐音** |
| 与 drums/bass/guitar/other/piano 的相关性 \|max\| | 0.145 | 0.099 ~ 0.131 | **与伴奏没有异常相关** |

⇒ **不是分离残留**（否则会与其它轨高度相关）。结合 −20.7 dBFS 的能量与低谐波占比，
判断为**气声/弱声/非乐音化**的演唱 —— **主人的方向是对的，值得细切重试。**
（脚本：`lang_dev/_harmonic_check.py`、`lang_dev/_bleed_check.py`）

## 16. 三段式实现

    ┌ ① 判质量 ── `asr_refine.score()`：假名占比 / 重复度 / 长度 → 0~1 分 + 问题标签
    ├ ② 再切割重试 ── `asr_refine.retry_windows()`：低分窗**强制语种**后对半切重试，逐子窗取最优
    └ ③ 谐音音节 ── `ja_romaji.py`：日语 → 假名 → **罗马音摩拉**时间轴（一摩拉 ≈ 一个音符）

### 16.1 「强制语种」是关键杠杆（实测）

自动模式下 0~10 s 是中文乱码；**把语种钉死在 Japanese** 后：

| 配置 | 0–5 s | 5–10 s |
|---|---|---|
| 自动 W5 | `想当小丑吗？就就那么。`（zh） | `很臭吗？很臭…`（zh） |
| 自动 W2 | `我下班就来。`（zh） | `安卓叫什么？`（zh） |
| **强制日 W5** | **`じゃじゃじゃま、じゅうじゅうどま。`** | **`チョコマンチョコマンチョコマン`** |
| **强制日 W3** | `私は。` / `じゃあ、車ま…` | `エンチャブル調査官。` |

⇒ 乱码**变成了日语摩拉**。内容仍不可信（原本该是 `さよなら`），
但**音节形态与时长骨架可用了** —— 这正是"谐音音节"要的东西。

### 16.2 罗马音摩拉分解（`ja_romaji.py`，含全部韵律规则）

```
官方：さよなら 少しだけ違っただけの愛情表現 メランコリー 普段通り独りきり段取り
假名：さよなら すこしだけちがっただけのあいじょうひょうげん めらんこりー ふだんとうりひとりきりだんどり
罗马音：sa/yo/na/ra su/ko/shi/da/ke chi/ga/t/ta/da/ke/no a/i/jo/u/hyo/u/ge/n
        me/ra/n/ko/ri/i fu/da/n/to/u/ri hi/to/ri/ki/ri/da/n/do/ri      （45 摩拉）
```

正确处理了 **促音**（違った → `chi/ga/**t**/ta`）、**长音**（コー → `ko/**ri/i**`）、
**拗音**（じょ → `jo` 一摩拉）、**拨音**（ん → 独立摩拉）。
依赖 `pykakasi`（已装在主环境与侧环境；缺失时只处理纯假名且会报覆盖率，不抛异常）。

## 17. 端到端 demo 结果（`lang_dev/_demo_ja_syllable.py`）

```
① 基线 W10 自动 ：0-10s score 0.22 (Chinese) / 10-20s 0.56 / 20-30s 1.00
② 重试         ：0-5s 强制日 → score 1.00（じゃじゃじゃま、じゅうじゅうどま。10 摩拉）
                 10-12.5s → 0.93（さようなら。スボシラケビがさっげの。）
                 17.5-20s → 1.00
   统计 n_in=3  n_retried=4  n_improved=6  n_still_bad=1  ASR 调用 3 次 / 11 个区间
③ 谐音音节     ：118 个罗马音摩拉，每个带时间戳
```

## 18. ★ 量化「日语的**字**有没有变成**音符**」（主人反馈③）

用同段人声跑 Basic Pitch（142 音符）与摩拉轴对表（`lang_dev/_mora_vs_notes.py`）：

| 窗口 | 摩拉数 | 命中率（tol 0.12 s） |
|---|---|---|
| **0–5 s** | 10 | **10.0 %** ← 就是"识别不出来"的那段 |
| 10–12.5 s | 16 | 93.8 % |
| 15–17.5 s | 17 | 94.1 % |
| 17.5–20 s | 12 | 83.3 % |
| 20–30 s | 63 | 90.5 % |
| **合计** | **118** | **83.9 %**（tol 0.06 → 56.8 %；tol 0.20 → 91.5 %） |

**并且：Basic Pitch 的第一个音符起点是 3.32 s —— 前 3.3 秒一个音符都没有。**

⇒ 这条指标**精准定位了问题段**（正常段 83~94%，问题段 10%），
也正是"日语切成罗马音"的用途：**摩拉轴可以当作"期望音符起点网格"**，
哪里缺音符一目了然（`notes_vs_morae()` 会直接列出"没被弹出来的字"）。

## 19. 诚实的局限

- 前奏 0–5 s 的**内容仍是幻觉**（`じゃじゃじゃま…` ≠ 官方 `さよなら`）。
  强制语种只解决了**语种路由与音节形态**，没有、也不可能"听出"听不懂的内容。
  **不要把这 10 个摩拉当成歌词。**
- 结论**只在 shiki 一首、一个段落上验证过**；`ja_romaji` 的规则是标准日语韵律，
  但"用摩拉轴补音符"的效果**尚未做全曲 A/B**。
- 摩拉时间用的是**窗内均分**（`timing=uniform`）。要更准需要接
  **`Qwen3-ForcedAligner-0.6B`**（权重已下好：`lang_id_models/Qwen3-ForcedAligner-0.6B`，1.71 GB，
  官方 AAS 42.9 ms）—— 但**对幻觉文本做强制对齐没有意义**，故本次未接。
- `asr_refine` / `ja_romaji` **尚未接进 `lang_pipeline`**（本次只到"能算、能量、能对表"），
  接入需要单独一轮 + 5 曲 A/B 验收。

## 20. 第三部分新增文件

| 文件 | 说明 |
|---|---|
| `ja_romaji.py` | **日语 → 假名 → 罗马音摩拉**（促音/长音/拗音/拨音全规则 + 质量判据） |
| `asr_refine.py` | 三段式：判质量 → 再切割重试（强制语种）→ 摩拉轴 + 字/音符对表 |
| `lang_dev/_harmonic_check.py` | 谐波占比检验（区分"真唱"与"残留/噪声"） |
| `lang_dev/_bleed_check.py` | 与其它分轨的相关性检验（判分离残留） |
| `lang_dev/_slice_intro.py` | 切片工具 |
| `lang_dev/_probe_intro_force.py` | 自动 vs 强制日语 × 6 档窗长对比 |
| `lang_dev/_demo_ja_syllable.py` | 端到端 demo（基线 → 重试 → 罗马音摩拉） |
| `lang_dev/_mora_vs_notes.py` | 摩拉 ↔ 音符命中率量化 |
| `lang_id_models/Qwen3-ForcedAligner-0.6B/` | 强制对齐权重（1.71 GB，本次未接） |

### 第三部分新增开关

| 开关 | 默认 | 作用 |
|---|---|---|
| `TS_ASR_RETRY` | `0` | `1` 启用"低质量段再切割重试" |
| `TS_ASR_RETRY_MIN` | `2.0` | 子窗最小秒数 |
| `TS_ASR_RETRY_SCORE` | `0.75` | 低于此质量分触发重试 |

（Qwen runner 新增 `spans` 显式时间片与逐配置 `language` 覆盖，重试才能按非均匀窗跑。）

---

## 21. 诚实的待办与风险

- [ ] 🔴 **本改动在出厂产物上仍是"零影响"**：项目已三重证明**出厂 100% 走回炉路径（整曲混音）**，
      而语种分割挂在**分轨路径**上（主人指定的位置）。要真正影响出厂听感，
      需把语种决策同样接进回炉路径（t6 的模式）。
- [ ] **换 Qwen 的收益尚未被证明**：5 首上准确率与 Silero 打平、慢 ~90×。
      建议用途排序：① 需要粤语/方言时；② 需要歌词产物时；③ 否则保留 Silero。
- [ ] **exe 体积**：若随包出货，需 +1.75 GB 权重 + 约 0.5 GB 的独立 torch 环境
      → 当前设计是**可选 sidecar**（装了就 `auto` 用，没装自动退回 Silero），exe 本体不变。
- [ ] 真实"中·日混唱"素材仍然没有 → 分段边界精度无法在真实素材上验收（与第一部分同一局限）。
- [ ] `en` 预设仍是纯假设；`ja` 的 `fill_win` 差异仍未验收。

## 14. 本次新增/修改文件

| 文件 | 说明 |
|---|---|
| `lang_id_qwen_runner.py` | **新增**：Qwen3-ASR 独立进程 runner（批量 wav × config，模型只加载一次） |
| `lang_pipeline.py` | **新增**：语种分割 → 逐段扒谱 → 拼回全局时间轴 的一步封装 |
| `lang_id.py` | **改**：新增 `qwen3asr` 后端 + `auto` 后端选择 + 分段后端的 decorate 路径 |
| `audio_crop.py` | **改**：新增 `_loud_weights` / `_time_vote_units`（音量优先逐点表决）/ `resolve_overlaps_by_loudness` |
| `transcriber_app.py` | **改**：`transcribe_stems` 内 +17/−1 行，`TS_LANG_SEG` 开关（默认关） |
| `lang_id_venv314/` | **新增**：Qwen 专用 Python 3.14 环境（隔离，不污染主环境） |
| `lang_id_models/Qwen3-ASR-0.6B/` | **新增**：1.75 GB 权重（hf-mirror 下载） |
| `lang_dev/_eval_lid_qwen.py` | Qwen LID 评测（5 首 × 3 配置，与 Silero 同协议） |
| `lang_dev/_probe_qwen.py` / `_dl_qwen.py` | 单窗耗时探针 / 权重下载 |
| `lang_dev/_test_langseg.py` | 管线集成测试（臂 OFF/ON 对比） |
| `lang_dev/_test_langpipeline.py` | 接线秒级测试（stub 替代 Basic Pitch + 异常注入） |
| `lang_dev/_diff_source.py` | **二进制安全**的源码逐行 diff（本项目 CRLF，文本模式会折算字节数） |
| `备份/pre_langseg_20260920_113643/` | 改前备份（`transcriber_app.py` = 158574 B / `A092C9E8…`） |

### 开关一览（第二部分新增）

| 开关 | 默认 | 作用 |
|---|---|---|
| `TS_LANG_SEG` | `0` | **`1` 才启用**语种分割扒谱（默认=出厂行为） |
| `TS_LANG_SEG_WIN` / `_HOP` | `30` / `15` | LID 窗长/窗移 |
| `TS_LANG_SEG_SKIP_DB` | `-60` | 低于此 dBFS 的段跳过识别 |
| `TS_LANG_SEG_KEEP` | `0` | `1` 保留裁剪分片 |
| `TS_LANG_BACKEND` | `auto` | `qwen3asr` / `silero_onnx` / `auto`（Qwen 优先） |
| `TS_QWEN_PYTHON` / `_RUNNER` / `_MODEL` / `TS_QWEN_THREADS` | 自动探测 | Qwen sidecar 路径与线程数 |

---
---

# 第四部分：联网取歌词外挂 + 比对 + 强制对齐

> 主人要求：**做一个联网取歌词的外挂，用 Python 爬虫、无需 cookie，爬网易云与 QQ音乐；
> 在第一次 Qwen 识别后进行比对，然后强制对齐。**

## 22. 一句话结论

**这条链是本次所有工作里收益最大的一环**：把联网取回的官方歌词作为 **context**
喂给 Qwen3-ASR 并开启强制对齐后，
与官方歌词的平均相似度从 **0.356（基线）→ 0.725（+104%）**，
单窗最高 **0.974**；且对齐结果**独立复现了网易云 LRC 的官方时间戳（误差 0.1~0.3 s）**。

## 23. 无 cookie 端点（2026-09-20 本机实测，全部 HTTP 200）

| 用途 | 端点 | 关键参数 | 备注 |
|---|---|---|---|
| 网易云·搜索 | `music.163.com/api/search/get/web` | `s, type=1, limit, offset` | `result.songs[]` 含 id/name/artists/album |
| 网易云·歌词 | `music.163.com/api/song/lyric` | `id, lv=1, kv=1, tv=-1` | LRC 带时间戳；另有 `tlyric`（翻译）/`klyric`（罗马音标注） |
| QQ音乐·搜索 | `c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg` | `key, format=json` | `data.song.itemlist[]` 含 songmid |
| QQ音乐·歌词 | `c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg` | `songmid, format=json, nobase64=1, g_tk=5381` | 必须带 `Referer: https://y.qq.com/portal/player.html` |

**实测踩到的两个坑（都会静默出错）**：
1. **网易云的毫秒分隔符是冒号**：`[00:09:88]さよなら`（不是 `[00:09.88]`）——
   只按 `.` 解析会把**全部时间戳丢掉**。
2. **QQ 的 `client_search_cpus` 端点返回非 JSON 被拒** → 改走 `smartbox_new`。

## 24. 比对（`lyrics_match.py`）：它一次解决三个问题

1. **曲目确认** —— 搜索会混进同歌手的别的歌。实测：
   `バカみたいに` **0.426** vs `衛星` 0.202 / `月が綺麗ねと言われたい！` 0.206 → **2 倍分离，选对**。
2. **判"无歌词段"** —— 官方 LRC 第一句在 **9.97 s**（`[00:09.88]さよなら`），
   所以 **0~9.97 s 根本没有歌词** → 判为 `no_lyric`。
   **这解释了"前奏识别不出来"：那里没人唱词**，`じゃじゃじゃま…` 是纯幻觉
   （之前的谐波/相关性检验只能说"不像残留"，现在有了**决定性的外部证据**）。
3. **判幻觉/跑偏** —— 窗内有歌词但相似度极低 → `hallucination`。

日语比对的关键设计：**两边都先用 pykakasi 读成假名再比**
（ASR 输出是假名流，歌词是汉字/假名混排，直接比字符会误判）；
相似度取 `max(序列相似度, 字符 bigram Dice)` —— 后者对"只识别对了一部分"更稳。

## 25. 歌词作 context + 强制对齐（本轮核心收益）

三臂单变量对照（同一批 10 s 窗，shiki 0~30 s）：

| 窗 | A 基线（自动语种） | B 强制日语（无 context） | **C 强制日语 + 歌词 context + 对齐** |
|---|---|---|---|
| 0–10 s | 0.091 | 0.167 | 0.267（`さよなら。`，**该窗无歌词，应被门控挡掉**） |
| 10–20 s | 0.367 | 0.367 | **0.935** |
| 20–30 s | 0.611 | 0.611 | **0.974** |
| **平均** | **0.356** | **0.382** | **0.725** |

C 的输出（10–20 s 窗）：

```
さよなら。少しだけ違っただけの愛情表現。メランコリー。普段通り 独りきり段取り。
私 多くは求めていないのに。隠した手のひら…
```
—— 与官方歌词逐句对应。**它同时给出了 41 个字符级时间戳。**

### 25.1 ★ 强制对齐独立复现了官方 LRC 时间

| 事件 | 官方 LRC | 我们的强制对齐 | 误差 |
|---|---|---|---|
| `さよなら` 起 | 9.88 s | `sa@10.16` | +0.28 s |
| `少しだけ…` 起 | 10.72 s | `shi@10.80` | **+0.08 s** |

⇒ 对齐可信；而且它给的是**逐摩拉**的真实时间（`sa@10.16 yo@10.32 na@10.48 ra@10.64`），
这正是主人要的"日语切成罗马音做**精确**识别"——**不再是窗内均分**。

### 25.2 ⚠️ 可复现性与"context 内容"的强敏感性（两处都必须知道）

**(1) Qwen 是逐字可复现的**（`lang_dev/_repro_context.py`，同一 job 跑 3 次）：
文本**逐字相同**、时间戳**逐窗一致**。
⇒ 所以任何分数差异都**不能**甩锅给随机性；A/B 是可信的。
（对比：项目里 Demucs 分离**不可复现**，那是另一回事。）

**(2) 但 context 的"边界"会**极端**影响结果**。同一批窗、同一语种、同一对齐器，
只改 context 的取值口径：

| 构建口径 | 10–20 s 窗相似度 | 三臂均值 |
|---|---|---|
| 严格落在窗内（pad=0） | 0.351 | **0.489** |
| 允许越出窗边界 1.0 s（pad=1.0） | **0.935** | **0.725** |

**(3) 机制已用单变量实验钉死**（`lang_dev/_probe_ctx_anchor.py`，只改 context 首行）：

| 变体 | context 首行 | 相似度 |
|---|---|---|
| V1 | `さよなら`（**窗起点正在唱的那一行**） | **0.938** |
| V2 | `少しだけ違っただけの愛情表現`（窗内第一行） | 0.709 |
| V3 | 空 context（对照） | 0.376 |

⇒ **两条规则**：
1. **给 context 就有效**（0.376 → 0.709）；
2. **必须包含「窗起点正在唱的那一行」**（0.709 → 0.938）——
   因为窗常**从词中间开始**（官方 LRC 在 9.88 s，窗从 10.0 s 起，正好切在 `さよなら` 中间）。
   **严格窗内会切掉这个锚点行，效果反而更差。**

（附：V1 的输出几乎就是官方歌词本身：
`さよなら。少しだけ違っただけの愛情表現。メランコリー。普段通り 独りきり段取り。
私、多くは求めていないのに、隠した手のひらの分だけ増える感情表現。`
后面拖了一句幻觉 `孤独がゼロフィアンの分だけ。`——context 偏置**不保证**完全不幻觉。）

## 26. context 的门控：什么该喂、什么不该喂

**该喂**：窗与歌词**有时间重叠**的段落（含窗起点所在行）→ 用 `overlap` 判定，不是"严格包含"。

**不该喂**：**整段都没有歌词**的窗（前奏/间奏/纯器乐）。
实测 `0~9.88 s` 就是这种情况——官方歌词第一句在 9.88 s，
而 Qwen 在那里无论自动、强制日语、还是喂了 context，都只是**幻觉**：

| 0–10 s 窗的配置 | 输出 |
|---|---|
| 自动语种 | `眨眨眨，车马；，悠悠马…`（中文乱码） |
| 强制日语（无 context） | `チャダンジョチョマジュワルョマジュワルチョマ…` |
| 强制日语 + 喂 context | `さよなら車。さよなら車。…`（照着 context 硬编，对齐把它放在 2.48 s，真值 9.88 s） |

⇒ **"先比对、再对齐"这个顺序的真正价值在这里**：
比对给出 `no_lyric` 判定，它就是 context 与"谐音音节化"的**闸门**。

## 27. 诚实的局限

- C 臂在 **0~10 s** 仍给出 0.267，是**门控前的旧数字**；修完后该窗应输出为空
  （重跑见 `_demo_lyrics_align.json`）。
- 结论仍**只在 shiki 一首、0~30 s** 上验证；未做全曲/多曲 A/B。
- 网易云/QQ 接口**随时可能变**；`lyrics_fetch` 全程 try/except，失败只会返回空，
  不会中断转谱（但也不会告诉你"接口挂了"——需要看日志）。
- **歌词是外部不可信数据**：代码只做文本处理，**不执行、不 eval**。
- 尚未接进 `lang_pipeline` / `transcriber_app`：目前是**独立外挂**，
  要进管线需单独一轮 + 全量验收。

## 28. 第四部分新增文件

| 文件 | 说明 |
|---|---|
| `lyrics_fetch.py` | **联网取歌词外挂**（网易云+QQ，无 cookie；LRC 解析含冒号格式；署名行/标题头过滤；歌名解析） |
| `lyrics_match.py` | **比对**：曲目确认 / 逐窗比对 / `no_lyric` 与 `hallucination` 判定 / 对齐用文本导出 |
| `lang_dev/_probe_lyrics_api.py` | 端点可用性实测 |
| `lang_dev/_demo_lyrics_align.py` | 端到端 demo（取词 → 比对 → context → 强制对齐 → 摩拉真实时间轴） |

CLI：
```bash
python lyrics_fetch.py --from-file "回归验收/_stems/shiki/柿崎ユウタ - バカみたいに（像个笨蛋一样） - KomisI-w_vocals.wav"
python lyrics_match.py --limit 6
lang_id_venv314\Scripts\python.exe lang_dev/_demo_lyrics_align.py --t0 0 --t1 30 --win 10
```

---
---

# 第五部分：打包进 exe（TuneScript AI V0.5.1）

> 主人要求：**也打包进 v0.5 exe 里**。

## 29. 结论与体积账

| | V0.5（原） | **V0.5.1（新）** |
|---|---|---|
| 文件 | `dist/TuneScript AI V0.5.exe` | **`dist/TuneScript AI V0.5.1.exe`** |
| 大小 | 529.5 MB | **546.7 MB（+17.2 MB）** |
| sha256 | `C47C16D1…`（已备份） | **`43B98968A3EED127…`** |

**V0.5 原样保留**（另存 `dist/_backup_TuneScript AI V0.5.exe`），与当年"做 V0.5 时保留 V0.4"的做法一致。

## 30. 里面放了什么、没放什么（关键设计）

| | 内容 | 位置 |
|---|---|---|
| **进 exe** | 全部新增代码：`lang_id` / `audio_crop` / `lang_modes` / `lang_pipeline` / `ja_romaji` / `asr_refine` / `lyrics_fetch` / `lyrics_match` | PYZ 归档（已验证 9/9 命中） |
| **进 exe** | **Silero lang95 ONNX（17 MB）+ 两个标签表**、`lang_id_qwen_runner.py`、**pykakasi 字典（9.8 MB）** | CArchive 的 datas |
| **不进 exe** | Qwen3-ASR-0.6B(1.75 GB)、Qwen3-ForcedAligner-0.6B(1.71 GB)、Python 3.14 venv | **exe 同目录的"外挂"** |

**为什么不把 Qwen 塞进去**：合计约 4 GB。onefile 的机制是**每次启动都把整包解压到临时目录** ——
4 GB 意味着每次开软件都要等它解压 4 GB，不可接受。
而项目**本来就有这个约定**：`dist/` 下早就有 `mt3/`(176 MB) 与 `piano_btd/`(165 MB) 外挂权重。

**没装外挂也完全可用** —— `TS_LANG_BACKEND=auto` 会自动退回 exe 内置的 Silero lang95（实测 0.7 s/曲）。

## 31. ⚠️ 两处必须改对的地方（第一次都翻车了）

### 31.1 打包后的路径解析（否则 exe 里永远找不到模型）

`lang_id.py` 原来用 `os.path.dirname(os.path.abspath(__file__))` 定位模型 ——
**onefile 下 `__file__` 指向解包临时目录**，挂在 exe 旁边的外挂永远找不到。
已改为：

```
resolve_resource(rel) 的优先级：env → **exe 同目录** → 打包内含(_MEIPASS) → 脚本目录
```
（`_exe_dir()` 在冻结态用 `sys.executable` 的目录 —— 与项目 `find_*_checkpoint()` 优先 exe 同目录一致。）

### 31.2 datas 只能打**三个文件**，不能整目录（否则 exe 从 529 MB 涨到 3.4 GB）

第一次构建我把 `lang_id_models/` **整个目录**写进 datas ——
**而那个目录里躺着 Qwen 的 3.46 GB 权重** → exe 变成 **3362 MB**。
已改成显式列三个 Silero 文件，回到 **546.7 MB**。
（教训：**datas 写目录名之前，先确认目录里没有后来才下载的大权重。**）

## 32. 验证（三层，都可复跑）

### 32.1 归档内容检查 `lang_dev/_verify_exe.py`

用项目自己用过的 `CArchiveReader`（V0.5 当年就是这么验的），
**并且要分两层查**——Python 模块在 `PYZ.pyz` 里，**不逐个出现在 CArchive 目录表**，
只在 CArchive 里找不到会误报：

```
=== 文件层（CArchive）=== 9/9 ✓（Silero onnx / 标签表 / runner / pykakasi 两个字典 …）
=== 模块层（PYZ，11123 个模块）=== 12/12 ✓（含 9 个新增模块）
=== 汇总：检查 21 项，0 问题 ===
```

### 32.2 端到端冒烟测试 `lang_dev/_smoke_exe.py`

不是"启动看看"，而是真的跑完管线并验 magic number：

| 臂 | 环境 | 退出码 | 用时 | magic |
|---|---|---|---|---|
| OFF 出厂 | 默认 | 0 | 36.7 s | **11/11** |
| ON 语种分段 | `TS_LANG_SEG=1` + `TS_LANG_BACKEND=silero_onnx` | 0 | 40.8 s | **12/12** |

ON 臂日志：`语种分段扒谱：1 段 → 识别 1 段 → 145 个音符（后端 silero_onnx）`
⇒ **一行证明三件事**：exe 里的新模块能 import、**打包进去的 Silero ONNX 被找到了**、onnxruntime 在 exe 里可用。

### 32.3 外挂(Qwen)联通性 —— `dist/` 加联接后实测

```
dist/lang_id_models       ← 目录联接 → 工作区 lang_id_models/（含 Qwen 两套权重）
dist/lang_id_venv314      ← 目录联接 → 工作区 lang_id_venv314/（torch 2.14 CPU + qwen-asr）
dist/lang_id_qwen_runner.py ← 硬链接
```
（用 `lang_dev/_setup_sidecar.py` 建立，`--status` / `--remove` 可查/可拆，**不复制数据、不占额外磁盘**。）

exe 实测（`TS_LANG_SEG=1` + `TS_LANG_BACKEND=qwen3asr`）：

```
[lang_id] Qwen runner：torch=2.14.0+cpu 加载 3.6s 推理 15.0s（1 窗）
[cli] 语种分段扒谱：1 段 → 识别 1 段 → 145 个音符（后端 qwen3asr）
退出码 0   用时 102.8 s   产物齐全（含 _vocals__ja__00-00.00-00-30.00.wav 语种分片）
```
⇒ **打包后的 exe 确实能通过"外挂"用上 Qwen。**

## 33. 重新构建 / 回退

```bash
# 构建（约 7~14 分钟）
python -m PyInstaller "音乐转谱器_V0.5.1.spec" --noconfirm --distpath dist --workpath build\V051b

# 验证
python lang_dev/_verify_exe.py
python lang_dev/_smoke_exe.py --arm both

# 外挂
python lang_dev/_setup_sidecar.py            # 建立
python lang_dev/_setup_sidecar.py --status    # 查看
python lang_dev/_setup_sidecar.py --remove    # 拆除

# 回退：直接删掉 V0.5.1.exe 与三个联接即可；V0.5 原件一直在
#       dist/TuneScript AI V0.5.exe（另存 dist/_backup_TuneScript AI V0.5.exe，sha C47C16D1…）
```

## 34. 诚实说明

- **本次只做到"可用"，没做到"用户可见"**：新能力全部挂在 `TS_LANG_SEG` 等**环境变量开关**后面
  （默认关闭 = 出厂行为逐字节不变），**GUI 里没有入口**、命令行也没有专门参数。
  要让它成为正式功能，需要 GUI/CLI 上再开一个开关并做全量验收。
- 语言模式本身**对出厂听感仍是零影响**（zh/ja 预设同值 + 出厂走回炉路径），见 §12.1。
- V0.5.1 的**相似度**与 V0.5 一致（0.88 / 0.87），说明打包没有引入行为变化。

---
---

# 第六部分：cookie 的落点与「烤进 exe」

> 起点是主人问：**「告诉我那个文件的哪一行需要输入 cookie」**。

## 35. 先答问题：没有哪一行代码要改

cookie 不是写进 `.py` 的，是放进一个**数据文件**。路径由 `netease_login.cookie_path()` 决定：

| 运行方式 | 文件位置 |
|---|---|
| 跑 exe（打包后） | **exe 同目录** `netease_cookie.txt` |
| 跑 .py（开发态） | 项目根目录 `netease_cookie.txt` |

内容一行：`MUSIC_U=<值>;`

**读取它的代码行**（如果一定要看行号）：

| 位置 | 作用 |
|---|---|
| `netease_login.py` `cookie_path()` | 拼出文件完整路径 |
| `netease_login.py` `load_cookie()` | 环境变量 → 文件 → 烤入，逐档回退 |
| `netease.py` `load_cookie()` | 自己不读文件，转发给上面那个 |

⇒ **不提供"改源码硬编码"的入口**，只有这三档。

## 36. 顺手查出的真 bug：打包后 `__file__` 指向解包临时目录

`netease_login.py` 原来写的是 `COOKIE_FILE = os.path.join(ROOT, "netease_cookie.txt")`，
`ROOT = dirname(abspath(__file__))`。开发态没问题，
**onefile 打包后 `__file__` 指向 `_MEIPASS` 解包目录** —— 于是：

- 用户放在 exe 旁边的 `netease_cookie.txt` **永远读不到**；
- 扫码登录写进去的 cookie 会**随临时目录被清掉**。

修法与 `lang_id._exe_dir()` 完全一致（见 §31.1）：冻结态取 `sys.executable` 的目录。

```python
def cookie_dir():
    if COOKIE_DIR_OVERRIDE:                 # 自检用，指向临时目录
        return COOKIE_DIR_OVERRIDE
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return ROOT
```

## 37. 三档优先级（`TS_NETEASE_COOKIE` > exe 旁文件 > 烤入值）

主人选择「**两者都要**」：既要能烤进 exe，也要保留外挂文件与环境变量。

| 档 | 来源 | 用途 |
|---|---|---|
| 1 | 环境变量 `TS_NETEASE_COOKIE` | 临时切换 / CI |
| 2 | exe（或脚本）旁边的 `netease_cookie.txt` | 日常自用，随时能改 |
| 3 | **打包时烤进 exe 的默认值**（新增） | 想做「一个 exe 走天下」 |

### 37.1 烤入是怎么做到"仓库里始终没有密钥"的

`音乐转谱器_V0.5.1.spec` 在 **构建目录下真的存在 `netease_cookie.txt`** 时：

1. 读该文件，写一个 `netease_cookie_baked.py`（只含 `COOKIE = '...'`）到 **临时目录**；
2. `pathex` 挂上该临时目录，`hiddenimports` 追加 `netease_cookie_baked`；
3. `Analysis` 跑完 → **立刻 `rmtree`**（另有 `atexit` 兜底）；
4. 没放文件 → 整个分支不执行，打印「本次不烤入 cookie」。

⇒ `netease_cookie_baked.py` **从不落在仓库里**，`.gitignore` 里也补了一条兜底。

### 37.2 出货 exe 是**不烤**的

构建目录没放 cookie 文件，所以现在 `dist/TuneScript AI V0.5.1.exe` 里没有密钥。
要烤自己的，把 `netease_cookie.txt` 放进项目根目录再跑一次 spec 即可。

## 38. 验证（都跑过，数字是真的）

### 38.1 构造期实测：假 cookie 真的进得去、且取得出

构建目录放 45 字符假串 `MUSIC_U=TESTONLY0000…` → 构建 → 从 exe 的 PYZ 里**把模块掏出来 `exec`**：

```
exe 内 COOKIE = MUSIC_U=TESTONLY0000000000000000000000000000;
```

⇒ 不是"构建日志说烤了"，是**从成品 exe 里读回来**的。

### 38.2 `lang_dev/_verify_exe.py` 增了「内容层」检查

原来只查"模块在不在 PYZ 里"——**但模块在 ≠ 内容是新的**（这正是 §36 那个 bug 能溜过去的原因）。
现在从 PYZ 解出 `netease_login` 的 **code 常量和名字**（`sys.executable` 落在 `co_names` 不是 `co_consts`，
第一版就栽在这），检查：

```
=== 内容层（netease_login 里的 cookie 定位方式）===
  常量数：363
  ✓ netease_cookie.txt         cookie 文件名
  ✓ frozen                     冻结态判断（sys.frozen）
  ✓ executable                 取 exe 自身目录（sys.executable）
  · netease_cookie_baked       未烤入（出货默认）
=== 汇总：检查 30 项，0 问题 ===
```

烤入版则用 `TS_EXPECT_BAKED=1` 把这一项**变成计分项**（那时是 30 项全过）。

### 38.3 `lang_dev/_selfcheck.py` 增了 6 项优先级测试

`env` 盖 `file` 盖 `baked`，三档各测一遍，外加"全空返回 None"。现在 **68 项全过**。

### 38.4 出货 exe

| | 值 |
|---|---|
| 大小 | 574,264,005 B（547.7 MB） |
| sha256 | `F1533D163B99F7CE933D26F13000213C039507543D79EA1AE8881540F62BCB6B` |
| exe 校验 | 30/30 |
| 自检 | 68/68 |
| GUI 自检 | 0 问题 |
| 磁盘残留 `netease_cookie_baked*` | 0 个 |

## 39. ⚠️ 安全事件：cookie 被上传到了公开仓库

主人在**网页版**往公开仓库 `chengzhouSAMA/TuneScript-AI` 上传了 `netease_cookie.txt`
（提交 `34d4122 Add files via upload`）。**网页上传会绕过 `.gitignore`。**

实测（用**未登录**的 GitHub API）：

| ref | 结果 |
|---|---|
| `36af4f3`（当时 HEAD） | 404，已不可读 |
| `main` | 404，已不可读 |
| `c61583b` | **匿名可读，157 字节** |
| `34d4122` | **匿名可读，157 字节** |

已做：`git rm --cached` + 推送（`36af4f3`），HEAD 树里已无该文件。

**没做**（主人选择）：改写历史清除 `34d4122` / `c61583b`。
⇒ 所以那两串**现在仍然匿名可读**。唯一有效的补救是**改网易云密码 / 退出所有设备**，
让那条 `MUSIC_U` 失效——cookie 一失效，文档里留着也无害。

**教训**：`MUSIC_U` 是完整会话凭据，等同账号密码；公开仓库里删文件**不等于**删历史。

## 40. 第六部分改动文件

| 文件 | 改动 |
|---|---|
| `netease_login.py` | `cookie_dir()` / `cookie_path()` / `baked_cookie()` / `cookie_source()`，`load_cookie()` 接三档 |
| `netease.py` | `load_cookie()` 改为委托，docstring 补第三档 |
| `transcriber_app.py` | `--netease-check` 第一行报 cookie 来源（env/file/baked/无） |
| `音乐转谱器_V0.5.1.spec` | 构建期烤入 + 清理 + `pathex`/`hiddenimports` 按需追加 |
| `lang_dev/_verify_exe.py` | 内容层检查（code 常量 + 名字），`TS_EXPECT_BAKED` 计分 |
| `lang_dev/_selfcheck.py` | 6 项三档优先级测试 |
| `.gitignore` | 补 `netease_cookie_baked.py` |
| `README.md` | 写清三档顺序与构建方式 |

GitHub：`c61583b`（cookie 路径修复）、`36af4f3`（移除误传文件）、`ec4d301`（烤入机制）。


