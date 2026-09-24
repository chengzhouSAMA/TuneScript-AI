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

---
---

# 第七部分：两条编配硬规则（R1 一个八度 / R2 无人声段加强伴奏）

> 主人原话：**「人声必须在右手部分且与伴奏分离相差至少一个八度，在没有人声纯伴奏时
> 加强对伴奏的识别，包括 other 里的电子音等」**。这两条已写成 skill
> `tunescript-ai-piano-rules`，并落进代码、默认开启。

## 41. 改动前实测：两条规则都不满足

在 `回归验收/_stems/<key>/` 的六轨上直接跑 `transcribe_stems_enhanced`
（跳过 Demucs 与渲染，只看「分轨 → 左右手」这一段）：

| 曲 | 右手最低音−左手最高音 | <12 的格子 | 无人声段伴奏密度 | 有人声段 | 比值 |
|---|---|---|---|---|---|
| fanwut | **−14** | 946 | 0.44 /s | 1.02 /s | 0.43 |
| monitoring | **−25** | 120 | （全曲无空档） | 0.49 /s | — |
| shiki | **−10** | 133 | 0.17 /s | 0.62 /s | 0.27 |
| jiabin | **−34** | 3232 | 0.63 /s | 1.00 /s | 0.63 |
| gouzhi | **−16** | 656 | 0.52 /s | 1.08 /s | 0.48 |

两条都反着：**间隔是负的**（左手最高音比右手最低音还高 10~34 个半音，两手完全叠在一起），
**无人声段的伴奏比有人声段还稀**（比值 0.27~0.63，全部 <1）。

## 42. R1：`_enforce_octave_gap()` —— 为什么必须两趟

`_separate_hands(left, right_min=60)` 只把左手 ≥60 的音降八度，结果只是
**左手 ≤59 / 右手 ≥60**：边界处最小间隔 **1 个半音**。左手 59、右手 60 时，
谱面上就是挨着的 —— 这正是用户说的"糊在一起"。

新函数按时间格（20 ms）做两趟：

1. **压左手**：对每个左手音取「与它重叠的右手最低音」，整体下移八度直到
   `< 右手最低音 − 12`；低到 `floor`（默认 21 = A0，钢琴最低键）停手。
2. **抬右手**：只在第一趟触底的窗口才动人声那侧（升八度，上限 96）。

顺序不能反、也不能只做第一趟：左手压到钢琴最低键还差得远时，**唯一还能动的一侧是人声**。

### 42.1 ⚠️ 这里栽过的坑

**坑：`blocked` 只在 `k == 0` 时记。** 第一版写成
`if k: 记 moved  else: if p > limit: 记 blocked`。于是
「降了几个八度、最后卡在 floor 上仍不达标」的窗口既没记 blocked、也没记 moved ——
**第二趟永远不会启动**。monitoring/jiabin 因此残留 `−1` 半音、9/26 个违约格。
改成「只要最终 `np_ > limit` 就记 blocked」后，五首曲全部归零。

## 43. R2：`_build_accomp()` —— 只改无人声段

两条路径都汇总到一个入口：

```
_build_accomp(other_notes, in_gap, gaps, min_gap, halluc, extra)
```

- **有人声段**：`_sparsify_harmony(_suppress_pad_notes(_filter_high_hallucination(x)), min_gap)`
  —— 与改动前逐字节相同（用单元测试钉住）。
- **无人声段**：
  - **跳过** `_filter_high_hallucination()` —— 它删掉所有「≥G5 且孤立」的音，
    而合成器主音、尖锐 pluck 正是这个形状，**这是电子音的头号杀手**
  - `_suppress_pad_notes(max_len=1.6)`（旧 0.7）→ 留得住 synth pad
  - `_sparsify_harmony(min_gap = 原值 × 0.5)` → 密度翻倍
  - 取消「再抽稀到 1.5 s」与「力度 ×0.85」
  - 并入 `extra` = **`other` 轨单独再跑一次识别**的结果

最后一条是「other 加权」的落地方式：`other` 本来就并进了伴奏合并轨，但那条轨走的是
**音频层合并 → 钢琴模型 → 钢琴向过滤器**，`other` 拿不到任何优待。单独过一次识别，
等于给它一次独立的机会。

### 43.1 ⚠️ `extra` 必须先抽稀再并（第一次就翻在这）

第一版写的是 `_dedupe_near(b + [n for n in extra if in_gap(n[0])])` ——
**`extra` 是原始识别输出，密度 20+ 音/秒**。结果 fanwut 无人声段密度冲到
**5.72 /s，是有人声段的 5.5 倍**，左手被灌成一堵墙。加一层
`_sparsify_harmony(ex, min_gap=原值×0.5)` 后回到 2.78 /s。

## 44. 改动后实测（同一批素材、同一份源码，只改环境变量）

| 曲 | 臂 | 间隔最小值 | <12 格子 | 无人声密度 /s | 有人声密度 /s | 比值 | other 并入 |
|---|---|---|---|---|---|---|---|
| fanwut | 旧 | −14 | 946 | 0.44 | 1.02 | 0.43 | 0 |
| fanwut | **新** | **12** | **0** | **2.78** | 1.03 | **2.70** | 786 |
| monitoring | 旧 | −25 | 120 | — | 0.49 | — | 0 |
| monitoring | **新** | **12** | **0** | — | 0.49 | — | 683 |
| shiki | 旧 | −10 | 133 | 0.17 | 0.62 | 0.27 | 0 |
| shiki | **新** | **12** | **0** | **1.04** | 0.62 | **1.68** | 732 |
| jiabin | 旧 | −34 | 3232 | 0.63 | 1.00 | 0.63 | 0 |
| jiabin | **新** | **12** | **0** | **3.32** | 1.01 | **3.29** | 2863 |
| gouzhi | 旧 | −16 | 656 | 0.52 | 1.08 | 0.48 | 0 |
| gouzhi | **新** | **12** | **0** | **3.64** | 1.09 | **3.34** | 1755 |

- **R1：五首曲的间隔最小值全部 = 12 半音，违约格 0/0/0/0/0**（旧值 −10 ~ −34）。
- **R2：四首有空档的曲，比值从 0.27~0.63 全部升到 1.68~3.34**（判据 ≥1.0）。
  monitoring 全曲没有无人声空档 → R2 按设计**零作用**（伴奏密度旧新完全一致 0.49），
  这本身就是一条"不该动的地方没动"的证据。
- **有人声段密度旧新一致**（1.02/1.03、0.49/0.49、0.62/0.62、1.00/1.01、1.08/1.09），
  逐项吻合 ⇒ 改动确实只落在无人声段。

脚本：`lang_dev/_measure_handgap.py`（结果 JSON 在 `lang_dev/_measure_handgap.json`）。
注意它的口径是「**分轨 → 左右手**」这一段，不含回炉与渲染。

## 45. 开关（R1/R2 与以往不同：默认**开**）

以往新能力一律默认关（出厂行为不变）。这两条是**用户明确要求的编配行为**，
所以默认开；要回到改动前设 0：

```
TS_HAND_GAP              1       人声在右手且与伴奏差一个八度
TS_HAND_GAP_SEMI         12      间隔下限（半音）
TS_HAND_GAP_FLOOR        21      左手最低音（21 = A0）
TS_ACCOMP_BOOST          1       无人声段加强伴奏识别
TS_ACCOMP_GAP_PAD        1.6     无人声段长音抑制上限（旧值 0.7）
TS_ACCOMP_GAP_RATIO      0.5     无人声段抽稀间隔倍率（0.5 = 密度翻倍）
TS_ACCOMP_OTHER          1       单独识别 other 轨并只并入无人声段
```

`TS_ACCOMP_BOOST=0` 走 `_accomp_legacy()`，**逐字节还原**改动前的伴奏整理 ——
这条回退通道由单元测试守着（`_test_handgap_accomp.py` 第 9 节）。

## 46. 验证

| 层 | 脚本 | 结果 |
|---|---|---|
| 单元 | `lang_dev/_test_handgap_accomp.py` | **40/40**（含「只改音高：数量/时间/力度守恒」「关闭后逐字节还原」） |
| 项目自检 | `lang_dev/_selfcheck.py` | **81/81**（新增 §[12] R1 6 项、§[13] R2 6 项） |
| GUI 自检 | `lang_dev/_check_gui.py` | 0 问题 |
| 真素材 | `lang_dev/_measure_handgap.py` | 5 曲 × 2 臂，见 §44 |
| 整曲 E2E | `回归验收/regress_one.py --song fanwut --arm B` | 见 §46.1 |

### 46.1 整曲 E2E（fanwut，同一 harness，只改两个环境变量）

| 臂 | sim | DTW 成本 | n_notes | frag | 产物 | 用时 |
|---|---|---|---|---|---|---|
| 旧（`TS_HAND_GAP=0 TS_ACCOMP_BOOST=0`） | 0.8653 | 0.1446 | 1712 | 0.0152 | pdf/midi/wav 齐全 | 160.9 s |
| **新（默认）** | **0.8806** | **0.1272** | 1564 | 0.0576 | 齐全 | 163.9 s |
| （历史记录基线） | 0.8631 | — | — | — | — | — |

- **sim 上升 +0.0153**（相对历史记录 +0.0174，旧臂只 +0.0022）。
- 新臂多出一行日志：`音域分离：左手 8 个音下移八度（共 8 个），右手 0 个音升八度
  （共 0 个），右手最低音 − 左手最高音 0 → 12 半音（目标 ≥12）。`
- 最终保证那一趟只在真动了音高时才多渲染一次，**用时 +3.0 s**。

#### ⚠️ n_notes / frag 的变化**不是丢音**（已逐轨查证）

同一产物直接逐轨数 MIDI：

```
r1off  track=L 927 音（27..59）  track=R 866 音（55..86）  合计 1793
r1on   track=L 927 音（27..59）  track=R 866 音（55..86）  合计 1793
```

**两臂逐轨音符数与音域完全相同。** 差异全在统计函数：

- `ai_transcriber_dev/evaluate.py` 的 `midi_notes()` 用 `active[msg.note] = t` ——
  **以音高为键的 dict**，同音高的重叠音互相覆盖，于是"数出来"变少。R1 把左手下移八度后
  更容易与别的左手音撞成同音重叠，这个口径下的数字就缩水。
- `fragmentation.fragment_ratio()` 数的是「短音 + 紧接着有同音高音」，同样对同音重复敏感。

⇒ **这两个数字不能用来判断 R1 的好坏**。能判断的是 sim、逐轨音符数（两臂相同）与重叠后的谱面。

**试过但回退了**：R1 之后补一次 `_fix_same_pitch_overlap()`（同音重叠截尾）。
逐轨音符数仍不变，但 `frag` 反而升到 0.1167、sim 降到 0.8741 ——
该函数把"重叠"变成"首尾相接"，而 `fragment_ratio` 恰好把零间隙的同音对也算成碎片。
且既有的 `_separate_hands()` 本来就在 `_fix_same_pitch_overlap()` **之后**执行、
同样产生同音重叠，出厂一直如此。故不在 R1 后额外加这一步。

**本轮没做**：另外四首曲的整曲 E2E 未跑（只跑了 fanwut）。

### 46.2 ⚠️ 回炉路径上 R2 不生效（重要限制）

fanwut 的初次结果 sim 0.82 < 阈值 → **触发回炉**，最终产物改由 `_simple_piano()`
从**整曲混音**生成 —— 这条路径**不经过 `_build_accomp()`**，所以 **R2 在回炉曲上完全不生效**，
只有 R1 生效（R1 也挂进了 `_simple_piano()`）。

⇒ 对「一定会回炉」的曲目，R2 目前只在**分轨路径**（`transcribe_stems_enhanced` /
`transcribe_stems`）上起作用。要让回炉曲也吃到 R2，得在 `_simple_piano()` 一侧另做
无人声段的伴奏加强 —— 那是独立的一轮，**本轮未做**。

## 47. 文件清单

| 文件 | 改动 |
|---|---|
| `transcriber_app.py` | `_right_min_cells` / `_left_max_cells` / `_hand_gap_min` / `_enforce_octave_gap`；`_accomp_boost_params` / `_dedupe_near` / `_accomp_legacy` / `_build_accomp`；三处 R1 调用点；两条 `transcribe_stems*` 改用 `_build_accomp` 并加 other 单独识别；主转录函数末尾加 R1 最终保证 |
| `lang_dev/_test_handgap_accomp.py` | 新增（单元） |
| `lang_dev/_measure_handgap.py` | 新增（真素材对照） |
| `lang_dev/_selfcheck.py` | 新增 §[12]/§[13]；审计上限随本轮放大并登记旧伴奏整理为"已知旧实现" |
| `README.md` | 新增「默认开启的两条编配规则」一节 |

备份：`备份/pre_handgap_accomp_20260920/transcriber_app.py`（sha `EB4A7CDB…`，3586 行）。

## 48. 出货 exe（本轮）

> 用户原话：**「以后每一版都需要打进 exe」**。这条已写进 skill
> `tunescript-ai-piano-rules` 的「交付纪律第一条」，以后每轮照办。

| | 值 |
|---|---|
| 文件 | `dist/TuneScript AI V0.5.1.exe` |
| 大小 | 574,268,683 B（547.7 MB） |
| sha256 | `4F7FE0FDAB6605AB688DA2685EA6C1086A9E68CFC98BBDAA67F64CD4D16993D2` |
| 上一版备份 | `dist/_backup_TuneScript AI V0.5.1.exe`（sha `F1533D16…`，cookie 路径修复版） |
| 归档校验 | `lang_dev/_verify_exe.py` —— **33 项，0 问题** |
| 冒烟 | `lang_dev/_smoke_exe.py --arm both` —— OFF `magic 11/11`、ON `magic 12/12`，退出码均 0 |
| 烤入 cookie | 无（构建目录里没有 `netease_cookie.txt`，spec 已明确提示） |

**exe 内实测出现 R2 的日志**（这是"新代码真的在 exe 里"的端到端证据，
不是"模块在不在"那种静态判断）：

```
[cli] AI 正在识别其他轨(电子音)(乐曲越长越久，请耐心等待)…
[cli] 无人声段伴奏加强：other 轨单独识别 69 个音，只并入纯伴奏段。
```

### 48.1 ⚠️ `_verify_exe.py` 自己修掉的两个「看不到新代码」的坑

1. **入口脚本不在 PYZ 里。** 它是 CArchive 的一个条目，**键名就是脚本名**
   （本工程 = `transcriber_app`，内容是 marshal 后的 code）。原来写
   `_code_of("__main__")` → 永远返回 None，于是"主程序内容层"的检查一直是空的。
2. **`MUST_MOD` 里那条 `("__main__", …)` 是假阳性。** `has()` 用的是**子串**匹配，
   而 PYZ 里有 `numpy.f2py.__main__` —— 它替真正的入口脚本"通过"了检查。
   已删掉该条，改由新的「脚本层」检查完成：逐个试解 marshal，
   取**顶层名字最多**的那个（= 入口脚本），再从它的 code 常量/名字里核对
   `_enforce_octave_gap` / `_hand_gap_min` / `_build_accomp` / `_accomp_legacy`。

这两条又是"模块在 ≠ 内容是新的"的实例 —— **校验脚本本身也可能在骗人**。

---
---

# 第八部分：新版多入口 UI

> 主人原话：**「换一个新 ui 要求有增加的所有功能（不包括只用于扒谱的），且有独立入口，
> 比如网易云搜索」**。中途追加：**「删除音频裁剪界面」**。

## 49. 范围：什么进 UI、什么不进

判据是"**这个功能能不能脱离扒谱单独用**"。

| 进 UI（独立入口） | 为什么 |
|---|---|
| **转谱** | 主功能 |
| **网易云**（搜索→选曲→选音质下载 + 登录/退出/账号状态） | 能独立当"下载器"用 |
| **B站音频**（BV 号 → 音频） | 能独立当"下载器"用 |
| **歌词**（网易云/QQ 取词 + LRC 时间轴 + 另存） | 能独立当"取词工具"用 |
| **语种识别**（Silero / Qwen 外挂，整曲 + 逐窗） | 能独立当"这是什么语言"用 |
| **环境**（模型/MuseScore/ffmpeg/和弦增强/登录状态） | 排障入口 |

| 不进 UI | 为什么 |
|---|---|
| 日语罗马音摩拉、英语音标音节 | 只是扒谱时"把唱词切成可对齐单元"的内部步骤 |
| 语种分割扒谱、歌词比对、强制对齐、ASR 重试 | 转谱管线的内部环节，脱离扒谱没有意义 |
| 左右手八度分离（R1）、无人声段伴奏加强（R2） | 编配规则，是出厂行为不是可点功能 |
| **音频裁剪** | 用户明确要求删除该界面。`audio_crop.py` 仍在，命令行可用 |

## 50. 结构

新 UI 全部是**新文件**，`transcriber_app.py` 只改了 `main()` 里的分发（+23 行）：

```
ui_kit.py    配色、ttk 样式、卡片/表单/表格/日志面板、Page 基类（后台任务+忙碌态）
ui_app.py    Shell（左侧导航 + 右侧页面）+ 6 个功能页 + 扫码登录弹窗
```

- **样式名一律加 `N.` 前缀**，与旧版 `App` 用的默认样式名完全隔离 —— 两套 UI 互不影响。
- **功能模块全部惰性 import**（点开页面/点按钮才 import），所以启动不变慢。
- **只有被选中的那一页会被 `place`**，其余 `place_forget`。

## 51. ⚠️ 三个真踩到的坑

### 51.1 `pack` 与 `grid` 不能在同一容器混用

`card()` 里的 `section()` 用 `pack`，`field()` 用 `grid` → 直接
`TclError: cannot use geometry manager grid inside … which already has slaves managed by pack`。
加了 `ui_kit.form()` 造一个专门的 grid holder，所有表单行都放进去。

### 51.2 ★ 六页叠在一起靠 `lift()` 抢顶层 —— **显示的不是选中的那一页**

第一版把 6 个页面都 `place` 在同一个位置，切换时 `page.lift()`。
**单元自检（不显示窗口）全部通过**，但真跑起来截图一看：不管点哪个侧栏项，
显示的都是**「语种识别」**那一页。

原因：`lift()` 在窗口**真正 map 之前**调用会被丢掉，mainloop 一启动，
叠放顺序退回创建顺序（而 Tk 里最终露出来的是倒数第二个 `place` 的页面，不是最后一个）。
`winfo_children()` 查出来的顺序是对的 —— 所以**只有真窗口截图能发现**。

改法：**不叠放**。切换时只 `place` 选中的那页，其余 `place_forget()`。
顺带好处：不再一次性建 6 页的全部控件，启动更快。

**教训**：tkinter 的层叠/几何问题，结构自检（`withdraw()` 的窗口）**测不出来**，
必须真开窗截图 + OCR 才看得见。

### 51.3 截图别只看一次

`& "exe"` 在 PowerShell 里对 GUI 子系统程序**不会等待**，job 立刻"完成"，
但进程其实还在跑 —— 一开始误判成"exe 启动就退出"。
另外我为了收尾跑 `Stop-Process *TuneScript*`，**把同一时刻正在跑的冒烟测试 exe 也杀了**，
导致 OFF 臂报 `rc=4294967295(-1)`。重跑才是真结果（见 §52）。

## 52. 验证（都跑过）

| 层 | 怎么做 | 结果 |
|---|---|---|
| 结构 | `lang_dev/_check_newui.py` | **28/28**：7 张卡 → 6 项导航、每页控件齐、逐个切换后**只有一页在显示**、默认页=转谱 |
| 视觉 | 真开窗 + 截图 + OCR | 侧栏 6 项 + 内容区「转谱」标题/副标题/输入行/选项/开始按钮全部正确渲染 |
| 打包 | `_verify_exe.py` | **36/36**：模块层多了 `ui_kit`/`ui_app`，脚本层多了 `ui_app`（惰性 import 必须显式进 hiddenimports） |
| 冻结态运行 | 直接跑 exe + 前置窗口 + OCR | exe 里同样是新 UI，默认页「转谱」（截图 `lang_dev/_ui_exe.png`） |
| 回归 | `_selfcheck.py` / `_check_gui.py` / `_smoke_exe.py --arm both` | 81/81、0 问题、OFF 11/11 + ON 12/12 |

## 53. 开关与回退

- `TS_UI=classic`（或 `--ui classic`）→ 回到旧版单窗口界面。**旧版 `App` 一行没动**，
  这是唯一的回退通道。
- `TS_UI_GEOMETRY=1120x740+20+20` → 钉死窗口位置大小（截图/录屏用）。
- 新 UI 起不来时 `main()` 会捕获异常、拆掉半成品窗口、**自动退回旧 UI**，不会变砖。

## 54. 出货 exe（本轮）

| | 值 |
|---|---|
| 文件 | `dist/TuneScript AI V0.5.1.exe` |
| 大小 | 574,358,928 B（547.8 MB，比上版 +90 KB） |
| sha256 | `490D269AD3276A6FEA5BEFFCC536F3D2EE993532BA11B76795B7206002B38F90` |
| 上一版备份 | `dist/_backup_TuneScript AI V0.5.1.exe`（sha `4F7FE0FD…`） |
| 归档校验 | 36 项，0 问题 |
| 冒烟 | OFF `magic 11/11`、ON `magic 12/12`，rc 均 0 |

## 55. 文件清单

| 文件 | 改动 |
|---|---|
| `ui_kit.py` | **新增**：配色/样式/卡片/表单/表格/日志/`Page` 基类/`async_call` |
| `ui_app.py` | **新增**：`Shell` + `TranscribePage` / `NeteasePage` / `BilibiliPage` / `LyricsPage` / `LangIdPage` / `EnvPage` + 扫码登录弹窗 |
| `transcriber_app.py` | `main()` 里加 UI 分发（`TS_UI`/`--ui`）+ 新 UI 失败自动回退；**`App` 本身未改** |
| `音乐转谱器_V0.5.1.spec` | `hiddenimports` 加 `ui_kit`、`ui_app` |
| `lang_dev/_check_newui.py` | **新增**：结构自检 28 项 |
| `lang_dev/_verify_exe.py` | 模块层加两个新模块；脚本层加 `ui_app` |
| `README.md` | 新增「界面」一节 + 两个环境变量 + 文件说明 |

---
---

# 第九部分：cookie 输入栏 + 扫码「一直说过期」

> 主人原话：**「为网易云搜索模式添加一个输入 cookie 的输入栏」**、
> **「且扫码登陆时一直显示二维码过期」**。

## 56. 先查「一直显示二维码过期」：服务端实测二维码有效 **301 秒**

不猜，直接把每一步的原始返回打出来（`lang_dev/_probe_qr_login.py`）：

```
unikey  : /api/login/qrcode/unikey         -> https://interface3.music.163.com/eapi/login/qrcode/unikey
qrlogin : /api/login/qrcode/client/login   -> https://interface3.music.163.com/eapi/login/qrcode/client/login

取 unikey(type=1) → {"code": 200, "unikey": "9fbf0f82-…"}
连续轮询 6 次     → {"code": 801, "message": "等待扫码"} × 6
```

再跑一次长轮询（`lang_dev/_probe_qr_ttl.py --minutes 6`）：

```
[   0.2s] 仍在等待扫码(801)
...
[ 240.6s] 仍在等待扫码(801)
[ 301.4s] code=800  {"code": 800, "message": "二维码不存在或已过期"}
出现过的 code： {801: 138, 800: 1}
首次 800 的时间：301.4s
```

**结论：接口和加密都是对的，二维码压根不是"一开就过期"，而是安安稳稳有效 ~5 分钟**
（138 次轮询全是 801）。所以主人看到的"一直过期"，成因是——
**超过 5 分钟才扫到**（翻手机、开 App、找扫一扫），而旧代码一遇到 800 就
`st.set('二维码已过期，请关掉重开')` 然后 `return` **停止轮询**，界面就永远停在
"已过期"上了。看代码像是"过期"，其实是**它不再刷新了**。

### 56.1 改法：过期就自动换一张 + 过程可见

- `ui_app.qr_login_dialog` 重写：800 → **自动取新二维码**（`gen` 代次号保证旧轮询的
  回调不会覆盖新图），不再让用户"关掉重开"。
- 加「刷新二维码」按钮，随时手动换。
- 状态栏带**已等待秒数**，能直观看到 5 分钟的窗口。
- 加一块小日志，把 `code=801/802/803/800` 原样写出来 ——
  下次真出问题，一眼能看出卡在哪一步，而不是只看到"过期"两个字。
- 加「复制扫码链接」：扫不动时可以把 `https://music.163.com/login?codekey=…`
  发到手机打开。
- 旧版界面（`App._netease_login`）也做了同样的最小修复：800 → `win.after(300, fetch)`
  自动重取，而不是停摆。

## 57. cookie 输入栏

新增 `ui_app.CookieBar`（可复用的一行），放在**「网易云」页的账号卡片**里：

```
cookie 输入  [ MUSIC_U=…                    ]  [保存] [载入当前] [清空]
把 MUSIC_U=… 那一段（带不带引号都行、分号可留可去）粘进来点保存即可；
保存后写进 netease_cookie.txt。
优先级：环境变量 TS_NETEASE_COOKIE ＞ 这个文件 ＞ 打包时烤入的值。
```

- 「保存」走 `netease_login.save_cookie()` —— 和扫码登录写的是**同一个文件**，
  读取逻辑完全共用（环境变量 → exe 同目录 `netease_cookie.txt` → 烤入值）。
- 保存后如果发现**生效来源是 `env`**，会明确提示"环境变量优先级更高，
  文件已写好但暂时不生效"，不让用户以为存了没用。
- 「载入当前」把当前生效的 cookie 填进输入框，方便改完再存。
- **日志只打脱敏摘要**（`MUSIC_U=1eb9ce…5d70（63 字符，共 2 个字段）`），
  **绝不回显完整 cookie**。
- 「转谱」页的**网易云搜索**那一行右侧加了一个「cookie…」按钮，一步跳到本页
  —— 那里也是"网易云搜索模式"，不给第二套重复 UI。

## 58. 验证

| 项 | 结果 |
|---|---|
| `lang_dev/_check_newui.py` | **31/31**（新增：cookie 输入栏存在、脱敏格式正确） |
| `lang_dev/_selfcheck.py` | **81/81** |
| `lang_dev/_check_gui.py` | 0 问题 |
| `lang_dev/_verify_exe.py` | **39/39** —— 内容层新增 4 项：`CookieBar` / `TS_UI_PAGE` / `TS_UI_GEOMETRY` / `_mask` |
| 冻结态实跑 + 截图 OCR | 网易云页确实出现「cookie 输入」行与提示文字（截图 `lang_dev/_ui_cookie.png`） |

新增的两个**调试/验证用**环境变量（真开窗截图靠它们才能直接开在指定页）：

```
TS_UI_PAGE=netease        直接开在「网易云」页
TS_UI_GEOMETRY=1120x740+10+10   钉死窗口位置
```

## 59. 出货 exe（本轮）

| | 值 |
|---|---|
| 文件 | `dist/TuneScript AI V0.5.1.exe` |
| 大小 | 574,362,767 B（547.8 MB） |
| sha256 | `727BAAB2234CFEE935795DD5B59463371F93F138E67E428D85EF2073BD2C7067` |
| 上一版备份 | `dist/_backup_TuneScript AI V0.5.1.exe`（sha `490D269A…`） |
| 归档校验 | 39 项，0 问题 |

## 60. 本轮改动文件

| 文件 | 改动 |
|---|---|
| `ui_app.py` | 新增 `CookieBar` / `_mask` / `_copy_to_clipboard`；`qr_login_dialog` 重写（自动换码 + 状态日志 + 刷新/复制按钮）；`NeteasePage` 挂 cookie 栏；`TranscribePage` 加「cookie…」跳转；`Shell` 支持 `TS_UI_PAGE` |
| `transcriber_app.py` | 旧版扫码弹窗：`ST_EXPIRED` 从"关掉重开"改成自动重取 |
| `lang_dev/_probe_qr_login.py` | **新增**：单步联调，打印原始返回 |
| `lang_dev/_probe_qr_ttl.py` | **新增**：长轮询测二维码真实有效期 |
| `lang_dev/_check_newui.py` | +3 项 cookie 相关断言 |
| `lang_dev/_verify_exe.py` | 内容层加 4 项 UI 关键件 |
| `lang_dev/_selfcheck.py` | 登记本轮删除行为"已知旧实现" |
| `README.md` | cookie 一节补「也可以直接在界面里填」 |

---
---

# 第十部分：内容区可以滚轮滚动了

> 主人给的剪贴板截图：窗口最大化后，网易云页的**下载区被切在屏幕外面看不到**
> （截图里能看到"无损/Hi-Res 需要会员 cookie…"那行紧贴着任务栏），
> 要求：**「把界面做一个可以使用滚轮滚动查看的样子」**。

## 61. 问题

新 UI 的每一页都是"标题 + 内容卡片 + 底部日志卡"，内容一多就顶出窗口下沿。
窗口拉大能缓解，但**最大化之后还是会被日志卡挤掉**（日志卡固定占 5~7 行文本的高度）。

## 62. 做法：内容区进 Canvas，日志卡留在底部

`ui_kit.Page` 里把 `self.body` 挂进 `tk.Canvas`：

```
Page
├── head（标题/副标题，固定）
├── _scroll_wrap            ← 可滚动区（pack expand）
│   ├── _vbar  ttk.Scrollbar
│   └── _canvas ──(_body_win)── body   ← 子类往 body 里塞卡片
└── _log_card（状态/进度条/日志，固定贴底，**不参与滚动**）
```

- `_on_canvas_configure`：把 body 的宽度设成"画布宽 − 24"，**横向永不出现滚动条**。
- `_on_body_configure`：刷新 `scrollregion`。
- `_sync_scrollbar`：**装得下就把滚动条收起来**，装不下才显示（B站/环境两页就看不到它）。

另外加了一个「收起」按钮，把底部日志折起来给小屏腾地方；日志默认高度从 7 行降到 5 行。

## 63. ⚠️ 坑：Tk 的 `<MouseWheel>` 只发给"指针正底下"的那个控件

在 `Page` 上挂一次是没用的 —— 指针停在某张卡片、某个输入框上时，事件归那个控件，
`Page` 收不到。所以 `Page.bind_wheel()` **递归遍历整棵控件树**逐个 `bind('<MouseWheel>', …, add='+')`：

```python
_SELF_SCROLL = (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox, ttk.Spinbox)
# 这几类自己会滚，跳过，别抢它们的滚轮
```

因为子类是在 `super().__init__()` **之后**才建控件的，`bind_wheel()` 不能在基类里调
—— 由 `Shell` 建完页面后统一替它们调一次。

## 64. 实测（真窗口，不是单元模拟）

`lang_dev/_demo_scroll.py` 开真窗口、窗口内调用滚轮处理函数，打印 `yview`：

```
[滚动前]   内容高=812 视口高=367 滚动条可见=True yview=(0.0,   0.455)
[滚 1 格后] 内容高=812 视口高=367 滚动条可见=True yview=(0.045, 0.5)
[滚到底后] 内容高=812 视口高=367 滚动条可见=True yview=(0.545, 1.0)
```

- 内容 812 px、视口 367 px → **超出 445 px**，滚动条自动出现；
- 滚一格 = `1/22` 内容高 = 0.045（`yview_scroll(1,'units')`）；
- 滚到底 `yview=(0.545, 1.0)` —— 也就是**一开始有 54.5% 的内容在折叠线下面**，
  先前就是这部分被切没了。

**截图对照**（`lang_dev/_scroll_before.png` / `_scroll_after.png`，同一窗口）：

| | 看到什么 |
|---|---|
| 滚动前 | 网易云音乐 / 账号卡片 / cookie 来源：无 / cookie 输入 / 退出登录 —— **下载区完全看不到** |
| 滚动后 | 账号卡片滚上去了，**「下载」卡片出现**：音质 320kbps、下载所选、打开输出目录 |

各页内容高度（默认 740 高窗口，视口 459）：转谱 494、网易云 795、B站 326、
歌词 641、语种 486、环境 352 ⇒ 四页需要滚动，B站/环境两页不需要（滚动条自动隐藏）。

## 65. 验证

| 项 | 结果 |
|---|---|
| `lang_dev/_check_newui.py` | **40/40**（新增 §6：Canvas/滚动条/已挂滚轮/日志卡不随滚动；滚轮方向与格数；内容变高后滚动条出现；scrollregion 包住全部内容） |
| `lang_dev/_selfcheck.py` / `_check_gui.py` | 81/81 / 0 问题 |
| `lang_dev/_verify_exe.py` | **43/43** —— 内容层新增 3 项：`bind_wheel` / `_build_scroll` / `_sync_scrollbar`（从 exe 的 PYZ 里解出 ui_kit 的 code 核对） |
| 冻结态 | exe 同样渲染新界面（`lang_dev/_ui_exe_scroll.png`） |

**没做到的**：这个会话里键鼠注入工具要批准、而批准被禁用，所以我**没法真的用滚轮滚一下**。
替代证据是"真窗口内调用同一个处理函数 + 打印 yview"（§64）以及 exe 里确实编进了这三个符号。

## 66. 出货 exe（本轮）

| | 值 |
|---|---|
| 文件 | `dist/TuneScript AI V0.5.1.exe` |
| 大小 | 574,363,916 B（547.8 MB） |
| sha256 | `5FE8D7AAEF4437B6B6CCB26D3727A90C751123E9CE3357FD697AF55C3F15CF5E` |
| 上一版备份 | `dist/_backup_TuneScript AI V0.5.1.exe`（sha `727BAAB2…`） |
| 归档校验 | 43 项，0 问题 |

## 67. 本轮改动文件

| 文件 | 改动 |
|---|---|
| `ui_kit.py` | `Page` 内容区改 Canvas 滚动；`bind_wheel` / `_on_wheel` / `_sync_scrollbar` / `_build_scroll`；日志卡加「收起」、默认高度 7→5 行 |
| `ui_app.py` | `Shell` 建完页面统一 `bind_wheel()`；`CookieBar` 输入框改左对齐定宽（最大化时不再被拉成一整行） |
| `lang_dev/_check_newui.py` | +9 项滚动断言（含滚轮方向与格数） |
| `lang_dev/_verify_exe.py` | ui_kit 内容层 3 项 |
| `lang_dev/_demo_scroll.py` | **新增**：真窗口滚动演示，打印 yview |

---
---

# 第十一部分：扫码「扫进去就过期」的真正根因

> 主人原话：**「网易云扫码还是不行，扫进去就过期」**。

## 68. 先排除掉的猜测

| 猜测 | 怎么验的 | 结果 |
|---|---|---|
| 二维码一开就过期 | `_probe_qr_ttl.py` 长轮询 | **否**：没人扫时连 138 次 801，第 **301.4 s** 才 800 |
| 接口/加密写错了 | `_probe_qr_login.py` 打原始返回 | **否**：unikey 200、轮询 801 都正常 |
| 该用 weapi 而不是 eapi | `_probe_weapi_qr.py` 用 NeteaseCloudMusicApi 同款 weapi 重跑 | **否**：两条路都能拿 key、都能轮询（同一个 key 用 eapi 轮询也返回 801），说明 key 是同一个池子 |

## 69. ★ 根因：登录成功的 cookie 只在 **响应头 Set-Cookie** 里，而旧代码只读 body

`_probe_qr_cookie_header.py` 打出来的：

```
--- 1) unikey ---
    Set-Cookie: NMTID=00OSUC3LlZZ4UMDHEMlndAhOfHjy5MAAAGg04ogOQ; Max-Age=315360000; …
    body code : 200
    body 里有 cookie 字段: False
--- 2) poll（没人扫，预期 801）---
    Set-Cookie: None
    body code : 801
    body 里有 cookie 字段: False
```

**unikey 那一步服务端就下发了 `NMTID`（网易云的匿名设备标识），而旧代码把它扔了** ——
`_eapi_post` 只 `return r.json()`，响应头里的 cookie 全丢。后果是一串连锁反应：

1. 取 key 时服务端发了 `NMTID=A`，丢掉；
2. 之后每次轮询都是**不带 NMTID 的新连接**，在服务端看来每次都是"另一个设备"；
3. 手机扫码确认后，凭据要通过 `Set-Cookie` 回给**当初那个会话**；
   而我们既不认这个会话（没带 NMTID），又只读 body ——
   **`code=803` 明明成功了却拿不到 cookie**；
4. 代码见 `code==803 and cookie` 不成立 → `return` 继续轮询；
5. 而已经被"消费"掉的 key 下一次轮询返回 **800**；
6. 800 被映射成「二维码已过期」→ **界面上就是你看到的「扫进去就过期」**。

对照：NeteaseCloudMusicApi 的 `login_qr_check.js` 专门写了
`cookie: result.cookie.join(';')` —— 就是把响应头的 cookie 手动塞回 body，
因为**正文里没有**。我们缺的就是这一步。

## 70. 改法（`netease_login.py`）

1. **整个扫码流程共用一个 `requests.Session`**（`_session()`）：
   unikey 那一步拿到的 `NMTID` 自动带到后面每一次轮询。
2. **`_eapi_post` 把 cookie 合并进返回值**：本次响应的 `Set-Cookie` 优先，
   其次是 Session 里累积的；并记下 `_cookie_from` / `_cookie_names` 便于排查。
3. **`generate_qr_key()` 每次先 `_reset_session()`** —— 开一轮干净会话，
   不会把上一次登录的旧 `MUSIC_U` 混进来。
4. **`logout()` 也重置会话**。
5. **`poll_qr_key()` 在 `803 但没 cookie` 时说清楚原因**，不再让上层无声重试；
   `ui_app` 的窗口会把这句话显示出来并自动换一张重来。

实测（真实联调，`_probe_qr_cookie_header.py` 末段）：

```
unikey = 1456e67a-…  poll code = 801
cookie 来源 = header(0)/jar(1)      ← NMTID 现在跟着走了
cookie 字段 = ['NMTID']
session 里的 cookie = ['NMTID']
```

## 71. 验证

| 项 | 结果 |
|---|---|
| `lang_dev/_selfcheck.py` | **87/87**（§[11] 新增 6 项：Set-Cookie 合并、NMTID 带上、来源标注、803 拿到 cookie、803 无凭据要报错、每轮干净会话） |
| `lang_dev/_check_newui.py` / `_check_gui.py` | 40/40 / 0 问题 |
| `lang_dev/_verify_exe.py` | **45/45**（内容层加 `_reset_session`、`_cookie_from`） |

## 72. ⚠️ 两个如实交代

- **我没法自己"扫一下"验证**：手机端要多一台设备和一个账号。上面能给的证据是
  「旧代码必然拿不到 cookie」这条链（`Set-Cookie: NMTID=…` + `body 里有 cookie 字段: False`
  是服务端亲口说的），以及修好后的联调输出。**如果还是不行，窗口里那块小日志现在会把
  每次轮询的 `code` 和"有没有拿到凭据"都写出来，把那一行发我。**
- **重打 exe 时我误杀了一个正在运行的实例**（PID 18768，有窗口，多半是主人自己刚在试扫码）。
  原因是构建要删 `dist\TuneScript AI V0.5.1.exe`，被占用会直接
  `PermissionError: [WinError 5]` 而**构建静默失败**（第一次就是这么失败的：跑完两个
  ERROR 行就结束，exe 的 sha 跟备份一模一样）。以后重打前先确认没有实例在跑。

## 73. 出货 exe（本轮）

| | 值 |
|---|---|
| 文件 | `dist/TuneScript AI V0.5.1.exe` |
| 大小 | 574,365,345 B（547.8 MB） |
| sha256 | `43CD14DA8E7BA0F15EA09F84DA05848F15F6406DCB944E18ABA59B1E7556DAD6` |
| 上一版备份 | `dist/_backup_TuneScript AI V0.5.1.exe`（sha `5FE8D7AA…`） |
| 归档校验 | 45 项，0 问题 |

## 74. 本轮改动文件

| 文件 | 改动 |
|---|---|
| `netease_login.py` | `_session` / `_reset_session`；`_eapi_post` 合并 Set-Cookie 并标注来源；`generate_qr_key` 开干净会话；`logout` 重置会话；`poll_qr_key` 803 无凭据时明确报错 |
| `ui_app.py` | 扫码窗口：803 但无凭据时显示原因并自动换码，不再无声重试 |
| `lang_dev/_probe_weapi_qr.py` | **新增**：weapi/eapi 对照实验 |
| `lang_dev/_probe_qr_cookie_header.py` | **新增**：直接看 Set-Cookie（根因证据） |
| `lang_dev/_selfcheck.py` | §[11] 新增 6 项扫码凭据检查 |
| `lang_dev/_verify_exe.py` | 内容层加 2 项 |
| `README.md` | 扫码一节补「凭据在 Set-Cookie 里」的说明 |


