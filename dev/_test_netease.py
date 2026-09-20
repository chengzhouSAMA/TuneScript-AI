# -*- coding: utf-8 -*-
"""_test_netease.py — netease.py 的边界测试（降级 / 试听 / 取不到 / 文件属性）。

用法: python lang_dev/_test_netease.py
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import netease as N                                        # noqa: E402

OUT = os.path.join(ROOT, "lang_dev", "_out_netease")


def main():
    print("cookie：%s\n" % ("已提供" if N.load_cookie() else "无（最高一般到 320k）"))

    print("=== A) 请求 lossless（服务端应降级，且如实报告）===")
    p, info = N.fetch_song(song_id=2103987239, out_dir=OUT, level="lossless")
    print("  ok=%s  path=%s" % (info["ok"], os.path.basename(p) if p else None))
    print("  resolve=%s" % json.dumps(info["resolve"], ensure_ascii=False))

    print("\n=== B) 只有 45 秒试听的歌（应拒绝，而不是拿片段去转谱）===")
    p, info = N.fetch_song(song_id=347230, out_dir=OUT, level="exhigh")
    print("  ok=%s" % info["ok"])
    print("  reason=%s" % (info["reason"] or "")[:180])

    print("\n=== C) 完全取不到的歌（应报失败、不抛异常）===")
    p, info = N.fetch_song(song_id=186016, out_dir=OUT, level="exhigh")
    print("  ok=%s  reason=%s" % (info["ok"], (info["reason"] or "")[:110]))
    print("  尝试过的档位：")
    for t in (info.get("tried") or []):
        print("    %s" % json.dumps(t, ensure_ascii=False))

    print("\n=== D) 已下载文件的实际属性 ===")
    try:
        import soundfile as sf
    except Exception as e:
        print("  soundfile 不可用：%s" % e)
        return 0
    for f in sorted(glob.glob(os.path.join(OUT, "*.mp3")) +
                    glob.glob(os.path.join(OUT, "*.flac"))):
        try:
            i = sf.info(f)
            print("  %-44s %6.1fs  %6d Hz  %dch  %5.1f MB  %s"
                  % (os.path.basename(f)[:42], i.duration, i.samplerate,
                     i.channels, os.path.getsize(f) / 1048576, i.format))
        except Exception as e:
            print("  %s 读取失败：%s" % (os.path.basename(f), str(e)[:60]))

    print("\n=== E) 失败路径不应留下半截文件 ===")
    leftovers = glob.glob(os.path.join(OUT, "*.part"))
    print("  .part 残留：%s" % (leftovers or "无 ✓"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
