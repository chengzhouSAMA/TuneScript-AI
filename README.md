# TuneScript AI — 音乐转谱器

把音频识别为**钢琴 MIDI + 五线谱 PDF + 钢琴演奏 WAV** 的开源转谱工具。

![Python](https://img.shields.io/badge/Python-3.9-blue) ![License](https://img.shields.io/badge/License-MIT-green)

## 功能

- **音频转五线谱**：WAV / FLAC / OGG / MP3 / M4A / NCM(网易云加密) → 钢琴五线谱 PDF + MIDI + 钢琴 WAV
- **人声/伴奏分离**：Demucs 深度学习四轨分离(人声/鼓/贝斯/其他)
- **逐轨识别**：Basic Pitch(ONNX,CPU)
- **NCM 解密**：内置网易云音乐新旧两种加密格式解密
- **谱面优化**：左右手分配、和弦识别、延音踏板、碎音合并、前奏/尾奏参考谱拼接
- **五线谱 PDF 硬保证**：绝不允许"生成了曲子却没有五线谱"

## 技术栈

| 组件 | 用途 |
|------|------|
| [Demucs](https://github.com/facebookresearch/demucs) | 人声/鼓/贝斯/其他 四轨分离 |
| [Basic Pitch](https://github.com/spotify/basic-pitch) | 音符识别(ONNX,CPU) |
| MuseScore 4 | 五线谱 PDF 渲染 |
| ffmpeg | 音频解码 |

## 快速开始

```bash
# 依赖
pip install -r requirements.txt

# 命令行转谱(默认：人声/伴奏分离模式)
python transcriber_app.py --cli --audio 歌曲.mp3 --outdir ./输出

# 简洁模式(不分轨,更快更稳定)
python transcriber_app.py --cli --audio 歌曲.mp3 --outdir ./输出 --simple
```

### 运行要求

- Windows 10/11 + 已安装 [MuseScore Studio 4](https://musescore.org/)(渲染 PDF/WAV 必需)
- Python 3.9
- ffmpeg(MP3/M4A 等解码,或直接使用 WAV/FLAC/OGG)

### 模型权重

- **Basic Pitch**(nmp.onnx)：随包自动定位
- Demucs 分离模型首次运行自动下载(~80MB)

## 使用模式

| 分轨 | 行为 |
|------|------|
| ✅(默认) | 人声/伴奏分离 → 逐轨识别 → 融合成可弹钢琴谱 |
| ❌(简洁模式) | 不分轨,整体识别 + 按音高切左右手,更快更稳定 |

任何模式失败都会自动回退,保证"有曲子就有谱"。

## 输出

- `*_piano.mid` 钢琴 MIDI
- `*_五线谱.pdf` 五线谱
- `*_钢琴.wav` 钢琴演奏音频
- `*_vocals/_drums/_bass/_other.wav` 分离音轨(分离模式时)

## 说明

- 本仓库仅包含源码。exe 发行版体积大(>500MB),可通过 Releases 提供。
- 源码依赖安装：`pip install basic-pitch demucs torch onnxruntime librosa pretty_midi soundfile mido pycryptodome`

## License

MIT
