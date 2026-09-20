# TuneScript AI — 音乐转谱器

输入音频（或 B 站 BV 号、或网易云歌名），输出钢琴五线谱 PDF + MIDI + 钢琴 WAV + 分离音轨。

目标是"把整首歌改编成一份能弹的钢琴谱"，不是"给钢琴曲扒谱"。
所以伴奏不怕杂，怕漏——左手是把非人声轨合起来用的，不挑单一轨。

## 环境

- Windows 10/11
- Python 3.9
- MuseScore Studio 4（渲染 PDF 和钢琴音轨，必装）
- ffmpeg（MP3/M4A 解码，发行版里带了）

## 安装

```
pip install -r requirements.txt
```

第一次跑会自动下 Demucs 分离模型（约 80MB），之后可以离线用。

## 用法

```
# 默认：六轨分离
python transcriber_app.py --cli --audio 歌曲.mp3 --outdir ./输出

# 简洁模式：不分轨，快一些也稳一些
python transcriber_app.py --cli --audio 歌曲.mp3 --outdir ./输出 --simple

# 直接给 B 站 BV 号
python transcriber_app.py --cli --bvid BV1xxxx --outdir ./输出

# 网易云：搜歌名/歌手，自己下载再转谱
python transcriber_app.py --cli --netease "バカみたいに 柿崎ユウタ" --outdir ./输出
python transcriber_app.py --cli --netease-id 2103987239 --outdir ./输出
```

也有 GUI，双击 `TuneScript AI V0.5.1.exe`，输入框里填 BV 号或网易云关键词都行。

## 输出

```
歌曲_piano.mid        钢琴 MIDI
歌曲_五线谱.pdf        五线谱
歌曲_钢琴.wav          钢琴音色
歌曲_大谱表.xml        MusicXML
歌曲_vocals / _drums / _bass / _guitar / _piano / _other.wav   六条分离音轨
```

## 流程

```
解码 → Demucs htdemucs_6s 六轨分离 → 语种分割 → 逐段扒谱 → 融合编排 → 自检回炉 → 渲染
```

人声轨走 Basic Pitch 出右手旋律，伴奏轨合并后走 ByteDance 和弦识别出左手和声。
碎音合并、左右手分离（左手降到 C4 以下）、力度分层、延音踏板都在 `fuse_to_piano` 里。

渲染这块比较小心：MuseScore 有时崩溃退出但其实已经把文件写好了，所以产物一律按魔数
（`%PDF` / `MThd` / `RIFF`）判断，不看退出码。PDF 渲染失败会自动降级重试。

## V0.5.1 加了什么

### 语种识别

两套可切换，默认用小的那套：

- **Silero lang95**：95 语种，17MB ONNX，一首歌 0.7 秒；随 exe 内置，不需要额外依赖
- **Qwen3-ASR-0.6B**：30 语种 + 22 种中文方言，1.75GB，一首歌 42~181 秒；可选外挂

`TS_LANG_BACKEND=auto` 会优先用 Qwen，找不到就退回 Silero。

Qwen 不能装进主环境——`qwen-asr` 的源码用了 PEP 604（`X | Y`），Python 3.9 直接报 TypeError，
它的依赖 accelerate 也要求 3.10+。所以它跑在独立解释器里，主程序用 subprocess 调，
模型权重挂 exe 旁边（塞进 exe 的话每次启动都要解压约 4GB）。

### 语种分段扒谱

分轨之后、扒谱之前先按语种把切人声切开，每一段用自己语种的预设识别，最后拼回全局时间轴。

切分时利用窗口重叠（`hop = win/2`），对每个时间点按「窗口概率 × 该窗响度」表决，
唱得响的段落话语权更大；分片真重叠了再按区间 RMS 取更响的那一段。

有个坑记一下：静音段不能继承语种。有首歌人声轨前 21.5 秒是 −84 dBFS 的数字静音，
早期实现把它"前向填充"成上一个语种，结果整段中文被吞成日语，三段只切出一个交界。

### 日语罗马音摩拉

日语一字一音、同音反复多，一个摩拉差不多就是一个音符，所以按摩拉切比按词切有用：

```
さよなら 少しだけ違っただけの愛情表現 メランコリー 普段通り独りきり段取り
→ 假名
さよなら すこしだけちがっただけのあいじょうひょうげん めらんこりー …
→ 罗马音（45 摩拉）
sa/yo/na/ra  su/ko/shi/da/ke  chi/ga/t/ta/da/ke/no  a/i/jo/u/hyo/u/ge/n  me/ra/n/ko/ri/i …
```

促音（違った → `chi/ga/t/ta`）、长音（コー → `ko/ri/i`）、拗音（じょ → `jo` 算一个摩拉）、
拨音（ん 单独一个）都按标准韵律处理。

摩拉轴可以当"期望音符起点网格"用，哪里缺音符看得比较清楚。

### 识别不出来的段，切细了重试

质量差的段落对半切、强制指定语种重试，逐个子窗取最优。
质量判据是纯统计的：假名占比、重复度、长度。实测效果比如日语歌前奏
（自动模式输出中文乱码 `想当小丑吗？就就那么。`）强制日语后出 `じゃじゃじゃま、じゅうじゅうどま。`
—— 内容还是不对，但至少变回日语摩拉了，音节数能用了。

### 联网取歌词

网易云和 QQ 音乐都能取，不用 cookie。实测可用的接口：

```
网易云搜索  music.163.com/api/search/get/web                        s, type=1
网易云歌词  music.163.com/api/song/lyric                            id, lv=1, kv=1, tv=-1
QQ搜索      c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg           key
QQ歌词      c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg       songmid, nobase64=1
```

网易云的毫秒分隔符是冒号，`[00:09:88]さよなら`，不是 `[00:09.88]`；
只按点解析的话时间戳会全部丢掉，不报错。

### 比对

拿首轮识别结果跟歌词比，用来做三件事：

1. 确认是不是同一首歌——搜索会混进同歌手的别的曲子，靠相似度挑出来
2. 判断某段时间到底有没有歌词——官方 LRC 第一句在 9.88s 的话，0~9.88s 就是没在唱，
   那里识别出来的东西都是幻觉（这也是一开始"前奏识别不出来"的真相）
3. 判断跑偏——窗内有歌词但相似度很低，标成 hallucination

日语比对要注意：识别输出是一串假名，歌词是汉字假名混排，直接比字符会误判，
所以两边都先用 pykakasi 读成假名再比。

### 歌词作 context + 强制对齐

把歌词当 context 喂回 Qwen，同时开强制对齐：

| | 与官方歌词的平均相似度 |
|---|---|
| 基线（自动语种） | 0.356 |
| 强制日语，不给 context | 0.382 |
| 歌词 context + 强制对齐 | 0.725 |

对齐结果跟网易云 LRC 的时间对得上：`さよなら` 官方 9.88s / 我们 10.16s（差 0.28s），
`少しだけ…` 官方 10.72s / 我们 10.80s（差 0.08s）。

调试的时候踩了个坑，顺手验证了一下：**context 必须包含窗起点正在唱的那一行**。
因为窗经常从词中间开始（LRC 9.88s，窗从 10.0s 起，正好切在 `さよなら` 中间）。
同一个窗只换 context 首行，结果是 `さよなら` 0.938 / `少しだけ違った…` 0.709 / 不给 0.376。
一开始我按"严格落在窗内"取，正好把锚点行切掉了，分数反而更差。

### 网易云搜索下载

可以直接搜歌名下载后再转谱，不用自己去别的网站扒音频。做法参考了
[Netease_url](https://github.com/Suxiaoqinx/Netease_url)（MIT）的思路，
按实测重写了一份精简版，只留转谱要用的部分。核心是两条接口：

```
明文  music.163.com/api/song/enhance/player/url          id, ids, br
eapi  interface3.music.163.com/eapi/song/enhance/player/url/v1   level, encodeType
```

eapi 要加密参数（AES-128-ECB + md5 摘要，公开算法），明文那条更简单，优先走明文，
失败再走 eapi。实测的结果：

| 歌 | 明文 api | eapi standard | eapi exhigh | eapi lossless |
|---|---|---|---|---|
| バカみたいに | 320k | 128k | 320k | 降级成 320k |
| 光年之外 | 320k | 128k | 320k | 降级成 320k |
| 海阔天空 | 失败 | 128k，只有 45 秒 | 同 | 同 |
| 晴天 | 失败 | 404 | 404 | 404 |

结论：**不登录就能拿 320kbps**，对 Demucs 和 Basic Pitch 够用了。
FLAC 要黑胶会员，服务端会静默降级——所以返回值里带的是**实际拿到的**音质，
请求 `lossless` 拿到 320k 会直接告诉你。想看无损就把 cookie 放到
`TS_NETEASE_COOKIE` 环境变量或 `netease_cookie.txt`（已在 .gitignore 里）。

两个坑：

- **试听片段**。有些歌免登录只给前 45 秒，接口会返回 `freeTrialInfo`。
  不检测的话会把 45 秒当成整首歌去转谱，所以默认直接拒绝
  （要强来得设 `TS_NETEASE_ALLOW_TRIAL=1`）。
- **`--quality lossless` 不一定真是无损**，理由同上。

用 320k 源跑完整流程测过：六轨分离 → 识别 → 回炉后 sim **0.89**，
和手上已有的同曲素材（0.8889）一致，说明下载源没问题。

## 环境变量

下面这些都默认关闭，不设就是原来的行为。

```
TS_LANG_SEG              0      改成 1 才启用语种分段扒谱
TS_LANG_SEG_WIN/_HOP     30/15  LID 窗长/窗移（秒）
TS_LANG_SEG_SKIP_DB      -60     低于这个 dBFS 的分段跳过识别
TS_LANG_SEG_KEEP         0       改 1 保留裁剪出来的分片
TS_LANG_BACKEND          auto    auto / silero_onnx / qwen3asr
TS_LANG_ID               1       改 0 完全关掉 LID
TS_LANG_CANDIDATES       (空)    候选语种白名单，比如 zh,ja,en,yue
TS_LANG_MODE             off     改成 auto 才启用语种专用识别预设
TS_ASR_RETRY             0       改 1 启用低质量段再切割重试
TS_QWEN_PYTHON/_RUNNER/_MODEL     Qwen 外挂路径，一般不用手动设
TS_NETEASE_COOKIE        (空)    网易云 cookie，填了才可能拿无损
TS_NETEASE_LEVEL         exhigh  下载音质默认值（standard/higher/exhigh/lossless/hires）
TS_NETEASE_ALLOW_TRIAL   0       改 1 允许下载只有几十秒的试听片段
```

## 文件说明

```
transcriber_app.py      主程序，分离→识别→融合→渲染，带 GUI
bilibili.py             B 站 BV → DASH 音频流
netease.py              网易云搜索 + 下载（明文 api / eapi，含试听片段检测）

lang_id.py              语种识别，后端可插拔，找不到模型就降级不报错
audio_crop.py           裁剪 / 人声分段 / 语种分段
lang_modes.py           语种 → 音符提取预设，默认值和现在出厂值一样
lang_pipeline.py        语种分割 → 逐段扒谱 → 拼回时间轴
ja_romaji.py            日语 → 假名 → 罗马音摩拉
asr_refine.py           低质量段切细重试，摩拉轴，字和音符对表
lyrics_fetch.py         联网取歌词
lyrics_match.py         识别结果和歌词比对
lang_id_qwen_runner.py  Qwen 的独立进程 runner

音乐转谱器*.spec         PyInstaller 打包配方
dev/                    开发时用的评测和验证脚本
```

`dev/` 里是一些能复跑的脚本：`_selfcheck.py`（34 项自检）、`_check_gui.py`（GUI 接线）、
`_eval_lid*.py`（两套 LID 的准确率和耗时对照）、`_test_crop.py`（分段边界）、
`_test_netease.py`（下载降级/试听/取不到）、`_repro_context.py`（可复现性）、
`_verify_exe.py` / `_smoke_exe.py`（打包产物检查）等。

## 一些数字

- 全曲还原度（原曲 vs 钢琴渲染的 chroma+DTW）：嘉宾 0.932 / 柿崎 0.889 / 反乌托邦 0.863。
  混音跟钢琴音色比，天花板大概 0.935，所以别指望更高。
- 语种识别在 5 首真实曲上：Silero 和 Qwen 都是 4/5 严格正确、5/5（粤语算中文）。
- 语种分段边界，合成中日混唱，10s 窗 / 5s 窗移：1/1 和 2/2 命中。
- 摩拉对音符的命中率整体 83.9%，而前奏那段只有 10%——这个指标能定位到"哪几个字没弹出来"。
- 网易云下载：免登录 320kbps，一首 130 秒的歌 5.2MB、0.3 秒下完；转谱结果 sim 0.89。
- exe：V0.5 529.5MB → V0.5.1 546.7MB。

## 已知问题

- 语种分段只接在分轨路径上，而出厂产物基本走的是"回炉"（整曲混音）那条路，
  所以**语种模式对成品听感的收益还没验证出来**，默认关着。
- 粤语和普通话分不开，Silero 和 Qwen 都判成中文。
- 没有歌词的段落不该做音节化，那里出来的都是幻觉，比对会把它标成 `no_lyric`。
- Qwen 的语种判断是转写的副产物，转写崩了语种也跟着崩。
- 窗不是越长越好。Qwen 用 60 秒窗准确率反而掉——长窗让它更"自信"，但更不准。
- 歌词接口和网易云接口都是第三方站点，随时可能变；失败只报错，不会中断转谱。
- 网易云有些歌免登录只能拿 45 秒试听（会明确拒绝），有些直接 404。
  无损要会员 cookie，没 cookie 时服务端会静默降级成 320k。
- zh 和 ja 的识别预设目前是同一组参数，真要分开还得做全量 A/B。

## 致谢

Demucs、Basic Pitch、ByteDance Piano Transcription、Qwen3-ASR、pykakasi、MuseScore，
以及 [Netease_url](https://github.com/Suxiaoqinx/Netease_url)（网易云解析的接口思路参考）。

## License

MIT
