#!/usr/bin/env python3
"""Remove the black matte outside the existing rounded AnvilDroid tile."""
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw

root = Path(__file__).resolve().parents[1]
icons = root / 'crates/anvildroid-gui/icons'
source = Image.open(icons / 'icon-source.png').convert('RGBA')
width, height = source.size
if width != height:
    raise ValueError('App icon source must be square')
# Match the artwork's inset rounded tile; retain the original master intact.
scale = 4
padding = width * 10 / 256
radius = width * 48 / 256
mask = Image.new('L', (width * scale, height * scale))
ImageDraw.Draw(mask).rounded_rectangle(
    (round(padding * scale), round(padding * scale),
     round((width - padding) * scale) - 1, round((height - padding) * scale) - 1),
    radius=round(radius * scale), fill=255)
mask = mask.resize(source.size, Image.Resampling.LANCZOS)
source.putalpha(ImageChops.multiply(source.getchannel('A'), mask))
# The sidebar displays this at 50 CSS px. 128 px stays sharp at 2x scale
# without making WebKit decode the 1254 px master on every app launch.
source.resize((128, 128), Image.Resampling.LANCZOS).save(
    root / 'crates/anvildroid-gui/src-frontend/brand.png', optimize=True)
for size in (32, 48, 64, 128, 256):
    icon = source.resize((size, size), Image.Resampling.LANCZOS)
    icon.save(icons / f'icon-{size}.png')
    if size == 256:
        icon.save(icons / 'icon.png')
