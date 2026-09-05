# TuneScript AI — 音乐转谱器

把音频识别为**钢琴 MIDI + 五线谱 PDF + 钢琴演奏 WAV** 的开源转谱工具(V0.4, 6 轨分离)。

## 功能
- 音频转五线谱：WAV/FLAC/OGG/MP3/M4A/NCM → 五线谱 PDF + MIDI + 钢琴 WAV
- **6 轨分离**(htdemucs_6s)：人声/鼓/贝斯/**吉他/钢琴**/其他，伴奏细分
- 人声+和弦为主：人声旋律(右手)+ 多音高和弦(左手)
- **和弦增强**：ByteDance 钢琴转录(CPU 接近实时)
- **AI 自检**：节拍对齐、旋律保真回炉、高音幻觉过滤
- **NCM 解密**、**五线谱 PDF 硬保证**

## 技术栈
Demucs(6轨) · Basic Pitch(人声) · ByteDance(和弦) · MuseScore 4 · ffmpeg

## 快速开始
```
pip install -r requirements.txt
python transcriber_app.py --cli --audio 歌曲.mp3 --outdir ./输出 --mt3
```
Windows 10/11 + MuseScore 4 + Python 3.9。

## 输出
- *_piano.mid / *_五线谱.pdf / *_钢琴.wav
- *_vocals/_drums/_bass/_guitar/_piano/_other.wav (6 轨)

## License
MIT
