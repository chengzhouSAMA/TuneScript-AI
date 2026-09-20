# -*- coding: utf-8 -*-
"""_pick_en_song.py — 挑一首**可下载的英文歌**，作为英语测试素材。

项目此前没有英语测试曲（备忘里写着"项目至今没有一首英语测试曲"），
这里用新做的 netease 下载逐个试，看哪首能拿到 320k。

用法: python lang_dev/_pick_en_song.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import netease as N                                        # noqa: E402

CANDIDATES = [
    "Shape of You Ed Sheeran",
    "Hello Adele",
    "Counting Stars OneRepublic",
    "Faded Alan Walker",
    "Hotel California Eagles",
    "Rolling in the Deep Adele",
    "Viva La Vida Coldplay",
    "Someone Like You Adele",
]


def main():
    ok = []
    for kw in CANDIDATES:
        cs = N.search(kw, limit=3)
        if not cs:
            print("  %-28s 搜索无结果" % kw)
            continue
        c = cs[0]
        r = N.resolve(c["id"], level="exhigh")
        flag = "OK " if r.get("ok") else "-- "
        print("  %s %-28s -> %-26s %s" % (flag, kw[:28],
                                          "%s - %s" % (c["name"][:18], "/".join(c["artists"])[:12]),
                                          ("%s %dk %s" % (r.get("level"), int((r.get("br") or 0) / 1000),
                                                          r.get("fmt"))) if r.get("ok")
                                          else (r.get("reason") or "")[:40]))
        if r.get("ok"):
            ok.append((c, r))
    print("\n可下载 %d 首" % len(ok))
    if ok:
        c, r = ok[0]
        print("建议测试曲：%s - %s（id=%s，%.1f MB）"
              % (c["name"], "/".join(c["artists"]), c["id"], (r.get("size") or 0) / 1048576))
        with open(os.path.join(ROOT, "lang_dev", "_en_song_pick.txt"), "w",
                  encoding="utf-8") as f:
            f.write("%s\t%s\t%s\n" % (c["id"], c["name"], "/".join(c["artists"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
