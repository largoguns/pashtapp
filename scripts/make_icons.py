"""Genera los iconos PWA (requiere Pillow, sólo en desarrollo): python scripts/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
BG = (15, 23, 42)        # slate-900 (#0f172a), igual que theme_color
ACCENT = (16, 185, 129)  # emerald-500
INK = (2, 6, 23)


def font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def make(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)
    # Zona segura "maskable": el motivo ocupa el 60 % central.
    pad = int(size * 0.2)
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=int(size * 0.14), fill=ACCENT)
    f = font(int(size * 0.42))
    d.text((size / 2, size / 2 + size * 0.01), "P", font=f, fill=INK, anchor="mm")
    # Pequeña "moneda" en la esquina del motivo.
    r = int(size * 0.075)
    cx, cy = size - pad, pad
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(56, 189, 248), outline=BG, width=max(2, size // 96))
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for s in (192, 512):
        make(s).save(OUT / f"icon-{s}.png", optimize=True)
        print("ok", OUT / f"icon-{s}.png")
