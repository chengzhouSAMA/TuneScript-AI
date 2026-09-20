# -*- coding: utf-8 -*-
"""Bilibili 音频下载(无 Cookie) —— 供 TuneScript AI 输入 BV 号直接扒谱。"""
import os
import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def fetch_audio(bvid, save_path=None, progress=lambda m: None, qn=64):
    """输入 BV 号，下载该视频的音频(DASH 音频流，无需 Cookie)。

    返回 (音频路径, 标题, 时长秒)。无 Cookie 也能拿到音频流(最高约
    192kbps AAC)——转谱足够。失败抛 RuntimeError。
    """
    # 1) 视频信息 → cid
    r = requests.get("https://api.bilibili.com/x/web-interface/view",
                     params={"bvid": bvid}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"查询视频失败: {d.get('message')}")
    info = d["data"]
    cid = info["cid"]
    title = info.get("title") or bvid
    duration = info.get("duration") or 0

    # 2) 播放地址(DASH, 无 Cookie) → 音频流 URL
    r2 = requests.get("https://api.bilibili.com/x/player/playurl",
                      params={"bvid": bvid, "cid": cid, "qn": qn, "fnval": 16},
                      headers=HEADERS, timeout=15)
    r2.raise_for_status()
    p = r2.json()
    if p.get("code") != 0:
        raise RuntimeError(f"获取播放地址失败: {p.get('message')}")
    dash = (p.get("data") or {}).get("dash") or {}
    audios = dash.get("audio") or []
    if not audios:
        raise RuntimeError("未拿到音频流(视频可能设了限制)")
    best = max(audios, key=lambda x: x.get("id", 0))
    url = best.get("baseUrl") or best.get("base_url")

    # 3) 流式下载音频
    if save_path is None:
        safe = "".join(c for c in title if c not in '\\/:*?"<>|').strip() or bvid
        save_path = safe + ".m4a"
    progress(f"正在下载音频: {title} …")
    with requests.get(url, headers=HEADERS, stream=True, timeout=120) as rs:
        rs.raise_for_status()
        total = int(rs.headers.get("content-length", 0))
        done = 0
        with open(save_path, "wb") as f:
            for chunk in rs.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if total and done % (2 << 20) < (1 << 16):
                        progress(f"下载中 {done // 1048576}/{total // 1048576} MB…")
    progress("音频下载完成。")
    return save_path, title, duration
