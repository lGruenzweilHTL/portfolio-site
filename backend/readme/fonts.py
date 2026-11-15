"""The site's fonts for SVG images: exact text widths, and subsets to embed.

An image can't load web fonts, so every SVG carries the glyphs it uses as
base64 woff2 (`Fonts.css()`). The sources are the site's own files in
site/fonts/. Tafel Sans is a variable font; each weight in use is cut out of
it as a static instance first, so a subset stays a few kilobytes.

Widths come from the fonts' hmtx tables. Kerning is ignored, so measured
text can come out a pixel or two wider than the browser draws it; layouts
leave room for that.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

from ..config import settings


@dataclass(frozen=True)
class Face:
    """One font at one weight, as the SVG names it."""
    family: str
    weight: int
    files: tuple[str, ...]  # site/fonts/*.woff2; the first that has a glyph wins
    variable: bool = False


HAND = "Kalam"
SANS = "Tafel Sans"

FACES: dict[tuple[str, int], Face] = {
    (HAND, 400): Face(HAND, 400, ("kalam-latin-400.woff2", "kalam-latin-ext-400.woff2")),
    (HAND, 700): Face(HAND, 700, ("kalam-latin-700.woff2", "kalam-latin-ext-700.woff2")),
    **{
        (SANS, w): Face(SANS, w, ("tafel-sans-latin.woff2", "tafel-sans-latin-ext.woff2"), variable=True)
        for w in (400, 500, 600, 700)
    },
}

FALLBACK = {HAND: "'Segoe Print', 'Bradley Hand', cursive", SANS: "system-ui, sans-serif"}


def _fonts_dir() -> Path:
    return Path(settings.site_dir) / "fonts"


@lru_cache(maxsize=None)
def _font_bytes(face: Face, file: str) -> bytes:
    """The font file as plain TrueType, cut to face.weight if it is variable."""
    font = TTFont(_fonts_dir() / file)
    if face.variable and "fvar" in font:
        font = instancer.instantiateVariableFont(font, {"wght": face.weight})
    font.flavor = None
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def _load(face: Face, file: str) -> TTFont:
    """A fresh copy (subsetting changes a font in place)."""
    return TTFont(io.BytesIO(_font_bytes(face, file)))


@lru_cache(maxsize=None)
def _advances(face: Face) -> tuple[dict[int, float], float]:
    """Advance width per code point in em, plus a fallback for unknown ones."""
    widths: dict[int, float] = {}
    for file in reversed(face.files):  # earlier files override later ones
        font = _load(face, file)
        upem = font["head"].unitsPerEm
        hmtx = font["hmtx"]
        for cp, glyph in font.getBestCmap().items():
            widths[cp] = hmtx[glyph][0] / upem
    return widths, widths.get(ord("n"), 0.5)


def measure(family: str, weight: int, size: float, text: str) -> float:
    """Width of text in user units (px) at size px."""
    widths, fallback = _advances(FACES[(family, weight)])
    return sum(widths.get(ord(ch), fallback) for ch in text) * size


def fit(family: str, weight: int, size: float, text: str, max_width: float) -> str:
    """text, cut at a word and ended with an ellipsis if it is wider than max_width."""
    if measure(family, weight, size, text) <= max_width:
        return text
    words = text.split()
    while words and measure(family, weight, size, " ".join(words) + "…") > max_width:
        words.pop()
    return (" ".join(words).rstrip(",.;:") + "…") if words else "…"


@lru_cache(maxsize=256)
def _subset(face: Face, chars: str) -> str:
    """@font-face rules for face with only chars, as data URLs."""
    rules = []
    left = set(chars)
    for file in face.files:
        font = _load(face, file)
        cmap = font.getBestCmap()
        here = {c for c in left if ord(c) in cmap}
        if not here:
            continue
        left -= here
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["kern", "liga", "calt"]
        opts.name_IDs = []
        opts.notdef_outline = True
        cut = subset.Subsetter(opts)
        cut.populate(unicodes=[ord(c) for c in here])
        cut.subset(font)
        out = io.BytesIO()
        font.flavor = "woff2"
        font.save(out)
        data = base64.b64encode(out.getvalue()).decode("ascii")
        rules.append(
            f"@font-face{{font-family:'{face.family}';font-weight:{face.weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2')}}"
        )
    return "".join(rules)


class Fonts:
    """Collects the text an image draws per face, then embeds just those glyphs."""

    def __init__(self) -> None:
        self._used: dict[tuple[str, int], set[str]] = {}

    def use(self, family: str, weight: int, text: str) -> str:
        """Records text and returns the CSS font-family value to draw it with."""
        self._used.setdefault((family, weight), set()).update(text)
        return f"'{family}', {FALLBACK[family]}"

    def css(self) -> str:
        return "".join(
            _subset(FACES[key], "".join(sorted(chars | {" "})))
            for key, chars in sorted(self._used.items())
        )
