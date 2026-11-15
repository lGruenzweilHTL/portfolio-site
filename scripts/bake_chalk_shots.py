"""Bake chalk renditions of the project screenshots in site/img/.

A screenshot in full colour looks pasted onto the chalkboard. This turns it
into chalk: greyscale, inverted when the UI is light (so its background
becomes the board), the background level crushed to nothing, chalk colour
with the brightness as alpha, and a fine grain so the board shows through.
The page shows the rendition and keeps the original one hover, focus or tap
away (`<figure class="shot">`, see styles.css and chalk.js).

    python scripts/bake_chalk_shots.py            # every image in SHOTS
    python scripts/bake_chalk_shots.py marshal    # just one

Writes site/img/<name>.chalk.webp next to site/img/<name>.webp. Needs only
Pillow. Add an entry to SHOTS for a new screenshot and look at the result on
the page: a UI with large mid-grey panels (the Unity editor) needs `hp`
(outlines from local contrast) instead of plain levels, or the panels turn
into flat grey slabs.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "site" / "img"
CHALK = (0xEC, 0xEA, 0xE4)

# height: output height in px (about 1.5x the height the page shows it at).
# invert: None decides by the median brightness (light UI -> invert).
# black: level that becomes transparent, 0-1; None = the most common level
#   (the UI's background) plus a margin.
# gamma < 1 brightens the mid tones. lv: weight of the plain levels.
# hp: weight of the high-pass (detail brighter than its surroundings), with
#   blur radius hp_r and gain hp_gain; flat areas of any level vanish.
SHOTS: dict[str, dict] = {
    "marshal": dict(height=625, gamma=0.8),
    "forkcast": dict(height=300, gamma=0.8),
    "sceneforge": dict(height=300, lv=0.4, hp=1.0, hp_r=5, hp_gain=3.0),
    "space-defenders": dict(height=300, gamma=0.9),
    "uniconsole": dict(height=300, invert=False, lv=0.0, hp=1.0, hp_r=4, hp_gain=3.5),
}


def _lut(f):
    return [max(0, min(255, round(f(i / 255) * 255))) for i in range(256)]


def _blur_mirrored(img: Image.Image, r: float) -> Image.Image:
    """Gaussian blur with mirrored edges, so the border doesn't light up."""
    p = int(r * 3) + 1
    w, h = img.size
    big = Image.new(img.mode, (w + 2 * p, h + 2 * p))
    big.paste(img, (p, p))
    big.paste(ImageOps.mirror(img.crop((0, 0, p, h))), (0, p))
    big.paste(ImageOps.mirror(img.crop((w - p, 0, w, h))), (w + p, p))
    top = big.crop((0, p, w + 2 * p, 2 * p))
    bottom = big.crop((0, h, w + 2 * p, h + p))
    big.paste(ImageOps.flip(top), (0, 0))
    big.paste(ImageOps.flip(bottom), (0, h + p))
    return big.filter(ImageFilter.GaussianBlur(r)).crop((p, p, p + w, p + h))


def render(name: str, height: int, invert: bool | None = None, black: float | None = None,
           white: float = 1.0, gamma: float = 1.0, lv: float = 1.0,
           hp: float = 0.0, hp_r: float = 6, hp_gain: float = 3.0) -> Image.Image:
    im = Image.open(IMG / f"{name}.webp")
    if im.mode == "RGBA":
        im = Image.alpha_composite(Image.new("RGBA", im.size, (0, 0, 0, 255)), im)
    im = im.convert("RGB")
    width = round(im.width * height / im.height)
    lum = im.resize((width, height), Image.LANCZOS).convert("L")

    hist = lum.histogram()
    half, acc, median = sum(hist) / 2, 0, 0
    for i, n in enumerate(hist):
        acc += n
        if acc >= half:
            median = i
            break
    if invert is None:
        invert = median > 128
    if invert:
        lum = ImageOps.invert(lum)
        hist = lum.histogram()
    background = max(range(256), key=lambda i: hist[i])
    b = (background + 10) / 255 if black is None else black

    def levels(x: float) -> float:
        if x <= b:
            return 0.0
        return lv * min(1.0, (x - b) / max(1e-3, white - b)) ** gamma

    out = lum.point(_lut(levels))
    if hp > 0:
        detail = ImageChops.subtract(lum, _blur_mirrored(lum, hp_r))
        out = ImageChops.lighter(out, detail.point(_lut(lambda x: min(1.0, x * hp_gain) * hp)))

    # Grain: fine pits, and faint streaks along the direction of a chalk drag.
    # (Pillow's noise isn't seeded, so a re-bake changes the bytes, not the look.)
    pits = Image.effect_noise((width, height), 48).point(
        _lut(lambda x: 0.55 + 0.45 * min(1.0, max(0.0, (x - 0.25) / 0.5))))
    streaks = Image.effect_noise((max(1, width // 6), height), 40).resize((width, height), Image.BILINEAR).point(
        _lut(lambda x: 0.8 + 0.2 * min(1.0, max(0.0, (x - 0.3) / 0.4))))
    alpha = ImageChops.multiply(out, ImageChops.multiply(pits, streaks))

    res = Image.new("RGBA", (width, height), CHALK + (0,))
    res.putalpha(alpha)
    return res


def main(names: list[str]) -> None:
    for name in names or list(SHOTS):
        res = render(name, **SHOTS[name])
        dest = IMG / f"{name}.chalk.webp"
        res.save(dest, quality=78, alpha_quality=80, method=6)
        print(f"{dest.relative_to(ROOT)}  {res.width}x{res.height}  {dest.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1:])
