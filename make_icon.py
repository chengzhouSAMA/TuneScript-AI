# -*- coding: utf-8 -*-
"""生成 TuneScript AI 应用图标(简约音乐音符风格)。

用法：python make_icon.py
输出：assets/app_icon.png、assets/app_icon.ico
"""
import os
from PIL import Image, ImageDraw, ImageFont

SIZE = 512
RADIUS = 108
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

TOP = (108, 92, 231)      # #6C5CE7
BOTTOM = (79, 70, 229)    # #4F46E5
WHITE = (255, 255, 255, 255)
SHADOW = (0, 0, 0, 70)


def make_icon_png(size=SIZE):
    # 1) 圆角渐变底
    grad = Image.new("RGB", (size, size))
    for y in range(size):
        t = y / (size - 1)
        r = int(TOP[0] + (BOTTOM[0] - TOP[0]) * t)
        g = int(TOP[1] + (BOTTOM[1] - TOP[1]) * t)
        b = int(TOP[2] + (BOTTOM[2] - TOP[2]) * t)
        for x in range(size):
            grad.putpixel((x, y), (r, g, b))

    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * RADIUS / SIZE), fill=255)

    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(grad, (0, 0), mask)

    # 2) 白色音符 (U+266A)
    draw = ImageDraw.Draw(icon)
    font = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", int(size * 0.58))
    text = "\u266a"
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (size - w) // 2 - bbox[0]
    y = (size - h) // 2 - bbox[1]

    # 轻微投影，增加立体感
    draw.text((x + max(4, size // 64), y + max(4, size // 64)), text, font=font, fill=SHADOW)
    draw.text((x, y), text, font=font, fill=WHITE)

    return icon


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    master = make_icon_png(SIZE)
    png_path = os.path.join(OUT_DIR, "app_icon.png")
    ico_path = os.path.join(OUT_DIR, "app_icon.ico")
    master.save(png_path, "PNG")

    small_path = os.path.join(OUT_DIR, "app_icon_small.png")
    master.resize((64, 64), Image.LANCZOS).save(small_path, "PNG")

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s, _ in sizes],
    )
    print("icon generated:")
    print(" ", png_path)
    print(" ", small_path)
    print(" ", ico_path)


if __name__ == "__main__":
    main()
