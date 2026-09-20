# TuneScript AI — 音乐转谱器

把一首歌**自动改编成可弹的钢琴谱**：输入音频（或 B 站 BV 号），输出
**五线谱 PDF + 钢琴 MIDI + 钢琴演奏 WAV + 分离音轨**。

![Python](https://img.shields.io/badge/Python-3.9-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-lightgrey)
![Version](https://img.shields.io/badge/Version-V0.5.1-green)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 它解决什么问题

市面上的"扒谱"工具大多做的是**识别**：把钢琴曲转成谱。
但用户真正想要的是**改编** —— 把一首带人声、吉他、合成器、鼓的**完整歌曲**，
变成一份**双手能弹的钢琴谱**。

这两件事的差别很大：识别要求"纯"，改编要求"全"。
TuneScript AI 走的是后者：分离 → 逐轨理解 → 融合编排 → 渲染成谱。

> **核心保证：绝不允许"生成了曲子却没有五线谱"。**
> 先渲染 PDF 再渲染 WAV，PDF 失败自动降级（重试 → 无延音线版 → 分谱），
> 产物用**魔数校验**（`%PDF` / `MThd` / `RIFF`）而不是退出码判断。

---

## 工作流程

```
音频 (WAV/FLAC/MP3/M4A/NCM)  或  B站 BV 号
        │
        ├─ ffmpeg 解码 / NCM 解密 / B站 DASH 直取
        ▼
   Demucs htdemucs_6s   六轨分离
   (人声 / 鼓 / 贝斯 / 吉他 / 钢琴 / 其他)
        │
        ├───────────────► 保存 6 条音轨
        ▼
   【语种分割】            ← V0.5.1
   人声轨 → 语种识别 → 按语种切段（同时间点按音量优先）
        │
        ▼
   逐段扒谱
   · 人声轨  → Basic Pitch（右手主旋律，按语种预设）
   · 伴奏轨  → 合并后 ByteDance 和弦识别（左手和声）
        │
        ▼
   融合编排（fuse_to_piano）
   碎音合并 · legato · 左右手分离 · 力度分层 · 延音踏板
        │
        ▼
   旋律自检（原曲 vs 钢琴的 chroma+DTW）
   不合格自动"回炉"重试，取更优
        │
        ▼
   五线谱 PDF + MIDI + 钢琴 WAV
```

---

## 核心特性

| | |
|---|---|
| **六轨分离** | Demucs `htdemucs_6s`：人声/鼓/贝斯/吉他/钢琴/其他，伴奏细分 |
| **人声旋律** | Basic Pitch（ONNX，CPU），单音旋律 SOTA |
| **和弦识别** | ByteDance Piano Transcription（CPU 接近实时，F1 0.9677） |
| **钢琴改编** | 左右手分离、跨度限制（≤9 度）、每手最多 3 键、碎音合并、延音踏板 |
| **AI 自检** | 节拍对齐自检、旋律保真回炉、高音幻觉过滤、长音铺垫抑制 |
| **NCM 解密** | 内置网易云音乐新旧两种加密格式 |
| **B 站直取** | BV 号 → DASH 音频流（无需 Cookie），不下载视频 |
| **PDF 硬保证** | 产物魔数校验 + 多级降级 + 自动回炉 |

---

## V0.5.1 新增能力

这一版把"**按语种理解人声**"做进了管线，并接上了一条**联网取歌词 → 比对 → 强制对齐**的链路。

### 1. 语种识别（可插拔 LID）

| 引擎 | 语种数 | 体积 | 许可 | 说明 |
|---|---|---|---|---|
| **Silero lang95**（默认内置） | 95 | **17 MB ONNX** | MIT | 零新依赖，**0.7 秒/曲** |
| **Qwen3-ASR-0.6B**（可选外挂） | 30 + 22 中文方言 | 1.75 GB | Apache-2.0 | 歌唱场景更强，42~181 秒/曲 |

`TS_LANG_BACKEND=auto` 会**优先用 Qwen，找不到就自动退回 Silero** —— 不装外挂也完全可用。

### 2. 语种分段扒谱

分轨之后、扒谱之前先做语种分割，人声**按语种逐段识别**，再拼回全局时间轴。

- **相同时间点按音量优先**：利用 `hop = win/2` 的窗口重叠，对**每个时间点**按
  「窗口概率 × 该窗响度权重」表决 —— 唱得响的段落在语种判定上话语权更大；
  若分片仍重叠，再由 `resolve_overlaps_by_loudness()` 按该区间 RMS 判给更响的一段。
- **静音段绝不继承语种**（踩过坑：人声轨前 21.5 秒是 −84 dBFS 数字静音，
  早期实现把它"前向填充"成上一个语种，导致整段中文被吞并成日语）。
- **任何一步失败都自动退回整轨识别**，不打断转谱。

### 3. 日语罗马音摩拉（mora）

日语是**摩拉计时语言**：一字一音、同音反复极多，**一个摩拉 ≈ 一个音符**。
所以对日语最好的表示不是"词"，而是**罗马音摩拉序列**：

```
さよなら 少しだけ違っただけの愛情表現 メランコリー 普段通り独りきり段取り
  ↓ 假名
さよなら すこしだけちがっただけのあいじょうひょうげん めらんこりー …
  ↓ 罗马音（45 摩拉）
sa/yo/na/ra  su/ko/shi/da/ke  chi/ga/t/ta/da/ke/no  a/i/jo/u/hyo/u/ge/n  me/ra/n/ko/ri/i …
```

规则完整：**促音**（違った → `chi/ga/t/ta`）、**长音**（コー → `ko/ri/i`）、
**拗音**（じょ → `jo` 一摩拉）、**拨音**（ん 独立摩拉）。

用途：把摩拉轴当作"**期望音符起点网格**"，哪里缺音符一目了然。

### 4. 未识别段再切割重试

对质量差的段落对半切、**强制语种**重试，逐子窗取最优。
质量判据是纯统计的：假名占比 / 重复度 / 长度。

实测（日语歌前奏，自动模式输出中文乱码时）：

| 配置 | 0–5 秒的输出 |
|---|---|
| 自动语种 | `想当小丑吗？就就那么。`（中文） |
| **强制日语** | **`じゃじゃじゃま、じゅうじゅうどま。`**（日语摩拉） |

### 5. 联网取歌词外挂（无 Cookie）

支持**网易云**与 **QQ音乐**，实测可用端点：

| 用途 | 端点 |
|---|---|
| 网易云·搜索 | `music.163.com/api/search/get/web` (`s, type=1`) |
| 网易云·歌词 | `music.163.com/api/song/lyric` (`id, lv=1, kv=1, tv=-1`) |
| QQ音乐·搜索 | `c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg` (`key`) |
| QQ音乐·歌词 | `c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg` (`songmid, nobase64=1`) |

> ⚠️ 网易云的毫秒分隔符是**冒号**：`[00:09:88]さよなら`（不是 `[00:09.88]`）。
> 只按 `.` 解析会**静默丢掉全部时间戳**。

### 6. 比对：一次解决三个问题

拿首轮 ASR 结果与歌词比对：

1. **曲目确认** —— 搜索会混进同歌手的别的歌，用相似度挑出真身
   （实测目标曲 0.426 vs 噪声候选 0.202，2 倍分离）；
2. **判「无歌词段」** —— 官方 LRC 第一句若在 9.88 s，则 **0~9.88 s 根本没在唱词**
   （这正是"前奏识别不出来"的真相：那里没人唱词，输出全是幻觉）；
3. **判幻觉** —— 窗内有歌词但相似度极低 → 标记 `hallucination`。

日语比对的坑：ASR 输出是**假名流**，歌词是**汉字/假名混排** ——
所以两边都先用 [pykakasi](https://github.com/hexatomium/pykakasi) 读成假名再比。

### 7. 歌词作 context + 强制对齐

把歌词作为 **context（上下文偏置）** 喂回 Qwen3-ASR 并开启强制对齐：

| 配置 | 与官方歌词平均相似度 |
|---|---|
| 基线（自动语种） | 0.356 |
| 强制日语（无 context） | 0.382 |
| **歌词 context + 强制对齐** | **0.725（+104%）** |

并且**独立复现了官方 LRC 的时间戳**：

| 事件 | 官方 LRC | 强制对齐 | 误差 |
|---|---|---|---|
| `さよなら` 起 | 9.88 s | 10.16 s | +0.28 s |
| `少しだけ…` 起 | 10.72 s | 10.80 s | **+0.08 s** |

> **一条重要经验（单变量实测钉出来的）**：context **必须包含「窗起点正在唱的那一行」**。
> 因为窗常从**词中间**开始（官方 LRC 9.88 s，窗从 10.0 s 起，正好切在 `さよなら` 中间）。
> 同一窗，只改 context 首行：`さよなら` **0.938** ／ `少しだけ違った…` 0.709 ／ 空 context 0.376。
> 用"严格落在窗内"的取法反而会切掉锚点行，效果**更差**。

---

## 快速开始

### 环境要求

- **Windows 10 / 11**
- **Python 3.9**
- **已安装 [MuseScore Studio 4](https://musescore.org/)**（渲染五线谱 PDF 与钢琴 WAV 必需）
- ffmpeg（MP3/M4A 解码；发行版已内置 `ffmpeg.exe`）

### 安装

```bash
pip install -r requirements.txt
```

首次运行会自动下载 Demucs 分离模型（约 80 MB），之后可离线使用。

### 使用

```bash
# 六轨分离模式（默认，更准更干净）
python transcriber_app.py --cli --audio 歌曲.mp3 --outdir ./输出

# 简洁模式（不分轨，更快更稳定）
python transcriber_app.py --cli --audio 歌曲.mp3 --outdir ./输出 --simple

# 从 B 站 BV 号直接转谱（无需 Cookie）
python transcriber_app.py --cli --bvid BV1xxxx --outdir ./输出
```

也可以直接双击 `TuneScript AI V0.5.1.exe` 使用图形界面。

### 输出文件

```
输出/
├── 歌曲_piano.mid          钢琴 MIDI（双手）
├── 歌曲_五线谱.pdf          五线谱
├── 歌曲_钢琴.wav            钢琴演奏音频
├── 歌曲_大谱表.xml          MusicXML（大谱表）
└── 歌曲_vocals/_drums/_bass/_guitar/_piano/_other.wav   六条分离音轨
```

---

## 技术栈

| 环节 | 选型 | 备注 |
|---|---|---|
| 音源分离 | [Demucs](https://github.com/facebookresearch/demucs) `htdemucs_6s` | 实测优于 Spleeter / Open-Unmix / Stem-Separator-AMT |
| 人声旋律 | [Basic Pitch](https://github.com/spotify/basic-pitch) | ONNX，CPU 可跑 |
| 和弦识别 | [ByteDance Piano Transcription](https://github.com/qiuqiangkong/piano_transcription_inference) | F1 0.9677，CPU 接近实时 |
| 五线谱渲染 | MuseScore 4 | 命令行调用 |
| 语种识别 | Silero LID lang95 / [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) | 可插拔 |
| 日语读音 | [pykakasi](https://github.com/hexatomium/pykakasi) | 汉字→假名→罗马音 |

---

## 模块地图

```
transcriber_app.py          主程序：分离 → 识别 → 融合 → 渲染 + GUI
bilibili.py                 B 站 BV → DASH 音频流（无需 Cookie）

lang_id.py                  语种识别适配层（可插拔后端 + 优雅降级 + 候选集约束）
audio_crop.py               音频裁剪 / 人声分段 / 语种分段（音量优先表决 + 重叠解析）
lang_modes.py               语种 → 音符提取预设表（默认值 == 出厂行为）
lang_pipeline.py            语种分割 → 逐段扒谱 → 拼回全局时间轴
ja_romaji.py                日语 → 假名 → 罗马音摩拉（促音/长音/拗音/拨音）
asr_refine.py               低质量段再切割重试 + 摩拉时间轴 + 字/音符对表
lyrics_fetch.py             联网取歌词（网易云 + QQ音乐，无 Cookie）
lyrics_match.py             ASR ↔ 歌词比对（曲目确认 / 无歌词 / 幻觉）
lang_id_qwen_runner.py      Qwen3-ASR 独立进程 runner（sidecar）

音乐转谱器*.spec            PyInstaller 打包配方
dev/                        开发与验证工具（见下）
```

### 为什么 Qwen 走独立进程

`qwen-asr` 的源码用了 PEP 604（`X | Y`），**Python 3.9 运行时报 `TypeError`**；
它的依赖 `accelerate` 也要求 Python ≥3.10。而主程序必须留在 3.9
（basic_pitch / demucs 依赖它）。

所以 Qwen 跑在**独立解释器**里（如 `lang_id_venv314`），主程序用 `subprocess` 交换 JSON。
**可选外挂**：把 `lang_id_venv314/` 与 `lang_id_models/` 放在 exe 同目录即可；
不装则自动退回内置 Silero。**exe 本体不含 Qwen 权重**（否则每次启动都要解压约 4 GB）。

---

## 环境变量

**默认全部关闭 —— 不设置时出厂行为逐字节不变。**

| 开关 | 默认 | 作用 |
|---|---|---|
| `TS_LANG_SEG` | `0` | `1` 启用语种分段扒谱 |
| `TS_LANG_SEG_WIN` / `_HOP` | `30` / `15` | LID 窗长 / 窗移（秒） |
| `TS_LANG_SEG_SKIP_DB` | `-60` | 低于此 dBFS 的分段跳过识别 |
| `TS_LANG_SEG_KEEP` | `0` | `1` 保留裁剪分片 |
| `TS_LANG_BACKEND` | `auto` | `auto`（Qwen 优先）/ `silero_onnx` / `qwen3asr` |
| `TS_LANG_ID` | `1` | `0` 完全关闭 LID |
| `TS_LANG_CANDIDATES` | 空 = 95 类 | 候选语种白名单，如 `zh,ja,en,yue` |
| `TS_LANG_MODE` | `off` | `auto` 才启用语种专用识别预设 |
| `TS_ASR_RETRY` | `0` | `1` 启用低质量段再切割重试 |
| `TS_QWEN_PYTHON` / `_RUNNER` / `_MODEL` | 自动探测 | Qwen 外挂路径 |

未列出的其它调试开关见 `dev/_README.md`。

---

## dev/ —— 开发与验证工具

这个项目的一条硬经验：**"感觉变好了"不算数，必须量出来**。
`dev/` 下是可复跑的评测与回归工具：

| 脚本 | 作用 |
|---|---|
| `_selfcheck.py` | **33 项验收自检**（源码改动审计 / 开关语义 / 优雅降级 / 裁剪 / 胶水 …） |
| `_eval_lid.py` / `_eval_lid_qwen.py` | 两套 LID 引擎在真实素材上的准确率与耗时对照 |
| `_test_crop.py` | 语种分段边界检测（合成中/日混唱，真值边界已知） |
| `_test_langseg.py` / `_test_langpipeline.py` | 管线集成测试与接线测试（含异常注入） |
| `_probe_ctx_anchor.py` | context「锚点行」机制的单变量验证 |
| `_repro_context.py` | **可复现性检验**（同 job 跑 N 次比对） |
| `_harmonic_check.py` / `_bleed_check.py` | 判断某段是"真人声"还是"分离残留" |
| `_mora_vs_notes.py` | 量化「日语的**字**有没有变成**音符**」 |
| `_verify_exe.py` / `_smoke_exe.py` | 打包产物内容核查 + exe 端到端冒烟测试 |
| `_setup_sidecar.py` | 一键建立/拆除 Qwen 外挂联接 |

其它实测结论、踩坑记录与经验教训见 `dev/_README.md` 与 `项目备忘*.md`。

---

## 实测与可信度

- **全曲还原度（sim = 原曲 vs 钢琴渲染的 chroma+DTW）**：
  《嘉宾》**0.932** / 《柿崎ユウタ》**0.889** / 《反乌托邦》**0.863**
  （混音对钢琴渲染的**天然天花板约 0.935**）
- **语种识别**（5 首真实曲）：Silero 与 Qwen 均 **4/5 严格正确、5/5 含粤语**；
  耗时 Silero **0.7 s/曲** vs Qwen **42~181 s/曲**
- **语种分段边界**（合成中/日混唱）：10 s 窗 / 5 s 窗移下 **1/1** 与 **2/2** 命中
- **摩拉→音符命中率**：整体 **83.9%**；而问题段（前奏）只有 **10%** ——
  这条指标能精确定位"哪几个字没被弹出来"
- **歌词外挂收益**：相似度 **0.356 → 0.725**；对齐复现官方 LRC 误差 **0.08~0.28 s**
- **exe 体积**：V0.5 529.5 MB → **V0.5.1 546.7 MB**（+17.2 MB）

---

## 已知局限

诚实地写在前面，避免误用：

- **语种分段目前只接在"分轨路径"上**，而出厂产物主要来自"回炉路径（整曲混音）" ——
  所以**语种模式对出厂听感的收益尚未被证明**，默认关闭。
- **粤语与普通话分不开**（Silero 与 Qwen 都是），会被判成中文。
- **无歌词的段落不该做"音节化"**：那里输出的是幻觉，比对判据会把它标成 `no_lyric`。
- Qwen 的语种判定是 **ASR 的副产物**：转写崩了，语种也会崩。
- **窗不是越长越好**：Qwen 在 60 秒窗上准确率反而下降（长窗会让它更"自信"但更不准）。
- 歌词接口依赖第三方站点，**随时可能失效**；失败只会返回空、不中断转谱。
- 「按语种切换识别预设」的**参数值本身仍需全量 A/B 验收**（`zh`/`ja` 目前同值）。

---

## 常见问题

**Q：为什么必须装 MuseScore？**
A：五线谱 PDF 与钢琴音色 WAV 都由 MuseScore 4 命令行渲染。没有它就没有产物 ——
而"必须有谱"是这个项目的底线，所以它不在可选依赖里。

**Q：跑一首歌要多久？**
A：30 秒片段约 40 秒（CPU）；3 分钟整曲约 3~5 分钟。
启用 Qwen 语种识别会再增加 42~181 秒。

**Q：需要联网吗？**
A：只在首次下载分离模型、以及使用 B 站下载 / 联网取歌词时需要。识别与渲染全在本地。

**Q：GPU 有用吗？**
A：本项目全部在 CPU 上跑通；有 CUDA 环境会更快，但非必需。

---

## 致谢

Demucs、Basic Pitch、ByteDance Piano Transcription、Qwen3-ASR、pykakasi、MuseScore
等开源项目的作者们。

## License

MIT
