# TuneScript AI — 音乐转谱器

把音频识别为**钢琴 MIDI + 五线谱 PDF + 钢琴演奏 WAV** 的开源转谱工具。

![Python](https://img.shields.io/badge/Python-3.9-blue) ![License](https://img.shields.io/badge/License-MIT-green)

## 功能

- **音频转五线谱**：WAV / FLAC / OGG / MP3 / M4A / NCM(网易云加密) → 钢琴五线谱 PDF + MIDI + 钢琴 WAV
- **人声/伴奏分离**：Demucs 深度学习四轨分离(人声/鼓/贝斯/其他)
- **人声+和弦为主**：只分析人声旋律(右手)和多音高和弦(左手)，贝斯/鼓不识别
- **和弦增强**：ByteDance 钢琴转录模型(CPU 接近实时，比 MT3 快约 6 倍)
- **AI 深度思考自检**：
  - 节拍对齐自检(自动修正 BPM)
  - 旋律保真验证(原曲 vs 钢琴 WAV 相似度，不合格自动回炉换管线)
  - 高音幻觉过滤(清除非钢琴声误识别的孤立高音)
- **NCM 解密**：内置网易云音乐新旧两种加密格式解密
- **五线谱 PDF 硬保证**：绝不允许"生成了曲子却没有五线谱"

## 技术栈

| 组件 | 用途 |
|------|------|
| [Demucs](https://github.com/facebookresearch/demucs) | 人声/其他 分离 |
| [Basic Pitch](https://github.com/spotify/basic-pitch) | 人声旋律识别 |
| [ByteDance Piano Transcription](https://github.com/bytedance/piano_transcription) | 多音高和弦识别 |
| MuseScore 4 | 五线谱 PDF 渲染 |
| ffmpeg | 音频解码 |

## 快速开始

```bash
pip install -r requirements.txt

# 命令行转谱(默认：分离模式)
python transcriber_app.py --cli --audio 歌曲.mp3 --outdir ./输出

# 和弦增强(推荐)
python transcriber_app.py --cli --audio 歌曲.mp3 --outdir ./输出 --mt3

# 简洁模式(不分轨)
python transcriber_app.py --cli --audio 歌曲.mp3 --outdir ./输出 --simple
```

### 运行要求

- Windows 10/11 + [MuseScore Studio 4](https://musescore.org/)(渲染 PDF/WAV 必需)
- Python 3.9
- ffmpeg(MP3/M4A 等解码)

### 模型权重

- **Basic Pitch**(nmp.onnx)：随包自动定位
- **ByteDance 和弦模型**(~165MB)：`piano_btd/note_F1=0.9677_pedal_F1=0.9186.pth`(勾选"AI 增强"时使用)
- Demucs 分离模型首次运行自动下载(~80MB)

## 使用模式

| 分轨 | 增强 | 行为 |
|------|------|------|
| ✅ | ✅ | 分离 + ByteDance 和弦增强(人声+和弦,推荐) |
| ❌ | ✅ | 整曲识别 |
| ✅ | ❌ | 分离 + Basic Pitch |
| ❌ | ❌ | 整体分析 |

任何组合失败都会自动回退，保证"有曲子就有谱"。

## 输出

- `*_piano.mid` 钢琴 MIDI
- `*_五线谱.pdf` 五线谱
- `*_钢琴.wav` 钢琴演奏音频
- `*_vocals/_drums/_bass/_other.wav` 分离音轨(分离模式时)

## License

MIT
