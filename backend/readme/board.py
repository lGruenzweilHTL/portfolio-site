"""One slate board as a standalone SVG: the site's ground, grain and chalk type.

What the site does with CSS (styles.css, "The chalk material") is done here
inside the image, since an image may not load anything. The site's grain
tiles (site/tex/) are 10-20 KB of noise each and don't compress, so the
grain is drawn with feTurbulence instead, tuned to look like them: strokes
sit under a mask of streaky noise (grain-line), lettering is chalk-coloured
under a finer one and roughened with the filters from _icons.html. The
slate's speckle is noise too. Only the eraser smear is a real tile.

Everything that draws itself is timed by one Timeline per board, so a card
plays once, in order, when the image loads. Reduced motion shows the end
state at once.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from ..config import settings
from . import chalk
from .fonts import HAND, SANS, Fonts, measure

# styles.css tokens
SLATE = "#1b1f22"
SLATE_2 = "#22272a"
SLATE_DEEP = "#121416"
CHALK = "#eceae4"
DIM = "#b3b7b9"
MUTE = "#8d9295"
FAINT = "rgb(236 234 228 / .22)"
ACC = "#f1d26a"

FULL = 840  # GitHub's profile column is about 830 px wide
HALF = 412  # two of these side by side, each shown at 49 %


def esc(s: str) -> str:
    return escape(s, {'"': "&quot;"})


@lru_cache(maxsize=None)
def tile(name: str) -> str:
    """A texture from site/tex as a data URL (the eraser smears; the grain is drawn by filters)."""
    data = (Path(settings.site_dir) / "tex" / f"{name}.webp").read_bytes()
    return "data:image/webp;base64," + base64.b64encode(data).decode("ascii")


# The filters of backend/templates/_icons.html, unchanged.
FILTERS = """
<filter id="chalk-lg" x="-4%" y="-25%" width="108%" height="150%" color-interpolation-filters="sRGB">
<feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="4" result="warp"/>
<feDisplacementMap in="SourceGraphic" in2="warp" scale="2.4" xChannelSelector="R" yChannelSelector="G" result="wob"/>
<feTurbulence type="fractalNoise" baseFrequency="0.7 0.32" numOctaves="2" seed="8" result="fine"/>
<feGaussianBlur in="wob" stdDeviation="1.5" result="soft"/>
<feComposite in="soft" in2="fine" operator="arithmetic" k1="0" k2="1" k3="1.1" k4="-0.55" result="m"/>
<feComponentTransfer in="m" result="mask"><feFuncA type="linear" slope="3.2" intercept="-1.25"/></feComponentTransfer>
<feComposite in="wob" in2="mask" operator="in" result="ch"/>
<feGaussianBlur in="ch" stdDeviation="4" result="dust"/>
<feComponentTransfer in="dust" result="dust2"><feFuncA type="linear" slope=".16"/></feComponentTransfer>
<feMerge><feMergeNode in="dust2"/><feMergeNode in="ch"/></feMerge>
</filter>
<filter id="chalk-md" x="-4%" y="-25%" width="108%" height="150%" color-interpolation-filters="sRGB">
<feTurbulence type="fractalNoise" baseFrequency="0.045" numOctaves="2" seed="4" result="warp"/>
<feDisplacementMap in="SourceGraphic" in2="warp" scale="1.8" xChannelSelector="R" yChannelSelector="G" result="wob"/>
<feTurbulence type="fractalNoise" baseFrequency="0.8 0.4" numOctaves="2" seed="8" result="fine"/>
<feGaussianBlur in="wob" stdDeviation="1" result="soft"/>
<feComposite in="soft" in2="fine" operator="arithmetic" k1="0" k2="1" k3="1" k4="-0.5" result="m"/>
<feComponentTransfer in="m" result="mask"><feFuncA type="linear" slope="3.4" intercept="-1.2"/></feComponentTransfer>
<feComposite in="wob" in2="mask" operator="in"/>
</filter>
<filter id="grain-sm" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
<feTurbulence type="fractalNoise" baseFrequency=".25 .9" numOctaves="2" seed="3"/>
<feColorMatrix values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 6 0 0 0 -1.6"/>
</filter>
<filter id="grain-lg" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
<feTurbulence type="fractalNoise" baseFrequency=".16 .75" numOctaves="3" seed="7"/>
<feColorMatrix values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 5.5 0 0 0 -1.5"/>
</filter>
<filter id="grain-line" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
<feTurbulence type="fractalNoise" baseFrequency=".12 .8" numOctaves="3" seed="5"/>
<feColorMatrix values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 5 0 0 0 -1.45"/>
</filter>
<filter id="slate" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
<feTurbulence type="fractalNoise" baseFrequency=".85" numOctaves="2" seed="9" result="n"/>
<feColorMatrix in="n" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 .09 0 0 0 -.02"/>
</filter>
<filter id="wob-sm" x="-4%" y="-30%" width="108%" height="160%">
<feTurbulence type="fractalNoise" baseFrequency="0.06" numOctaves="2" seed="4" result="n"/>
<feDisplacementMap in="SourceGraphic" in2="n" scale="1.1" xChannelSelector="R" yChannelSelector="G"/>
</filter>
"""

CSS = f"""
.stk{{stroke:{CHALK};fill:{CHALK}}}
.rs{{fill:none;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1 1;animation:draw .4s cubic-bezier(.35,.1,.45,1) both}}
@keyframes draw{{from{{stroke-dashoffset:1}}to{{stroke-dashoffset:0}}}}
.dep{{stroke:none;animation:dep .15s both}}
@keyframes dep{{from{{opacity:0}}}}
.wipe{{transform-box:fill-box;transform-origin:0 50%;animation:wipe .4s linear both}}
@keyframes wipe{{from{{transform:scaleX(0)}}}}
.fade{{animation:fade .5s ease-out both}}
@keyframes fade{{from{{opacity:0}}}}
.sweep{{transform-box:fill-box;transform-origin:0 50%;animation:wipe 1.6s cubic-bezier(.4,0,.3,1) both}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
"""


class Board:
    """Collects the parts of one image and writes it out as SVG."""

    def __init__(self, width: int, height: int, title: str, *, seed: int = 1, smear: tuple[str, float, float, float, float, float] | None = None) -> None:
        self.w, self.h = width, height
        self.title = title
        self.fonts = Fonts()
        self.tl = chalk.Timeline()
        self.body: list[str] = []
        self.defs: list[str] = []
        self.smear = smear
        self._seed = seed * 7919
        self._ids = 0

    # ---------- helpers ----------

    def seed(self) -> int:
        self._seed = (self._seed * 48271) % 2147483647
        return self._seed

    def uid(self, prefix: str) -> str:
        self._ids += 1
        return f"{prefix}{self._ids}"

    def add(self, markup: str) -> None:
        self.body.append(markup)

    def strokes(self, markup: str, *, acc: bool = False) -> None:
        """Chalk strokes under the grain mask; acc colours them yellow."""
        cls = ' class="acc"' if acc else ""
        self.body.append(f'<g mask="url(#gl)"{cls}>{markup}</g>')

    # ---------- text ----------

    def hand(self, x: float, y: float, text: str, size: float, *, weight: int = 700, kind: str = "md",
             anchor: str = "start", rotate: float = 0, write: bool = True, fill: str | None = None) -> float:
        """Chalk lettering in Kalam. kind: lg, md, sm (grain + filter) or acc (yellow chalk).
        Returns the text's measured width."""
        width = measure(HAND, weight, size, text)
        family = self.fonts.use(HAND, weight, text)
        filt = {"lg": "chalk-lg", "md": "chalk-md", "sm": "wob-sm", "acc": "wob-sm"}[kind]
        grain = "glg" if kind == "lg" else "gs"
        paint = f'fill="{fill or (ACC if kind == "acc" else CHALK)}" mask="url(#{grain})" filter="url(#{filt})"'
        anchor_attr = f' text-anchor="{anchor}"' if anchor != "start" else ""
        text_el = (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{esc(family)}" font-weight="{weight}" '
                   f'font-size="{size}"{anchor_attr} {paint}>{esc(text)}</text>')
        if write:
            left = x - (width if anchor == "end" else width / 2 if anchor == "middle" else 0)
            at, d = self.tl.text(len(text))
            cid = self.uid("w")
            self.defs.append(
                f'<clipPath id="{cid}"><rect class="wipe" x="{left - size * .3:.1f}" y="{y - size * 1.25:.1f}" '
                f'width="{width + size * 1.5:.1f}" height="{size * 1.7:.1f}" '
                f'style="animation-delay:{at:.2f}s;animation-duration:{d:.2f}s"/></clipPath>'
            )
            text_el = f'<g clip-path="url(#{cid})">{text_el}</g>'
        if rotate:
            text_el = f'<g transform="rotate({rotate} {x:.1f} {y:.1f})">{text_el}</g>'
        self.body.append(text_el)
        return width

    def sans(self, x: float, y: float, text: str, size: float, *, weight: int = 400, fill: str = CHALK,
             anchor: str = "start", fade: bool = True, at: float | None = None) -> float:
        """Plain type in Tafel Sans. Returns its measured width."""
        family = self.fonts.use(SANS, weight, text)
        anchor_attr = f' text-anchor="{anchor}"' if anchor != "start" else ""
        anim = ""
        if fade:
            delay = self.tl.t if at is None else at
            anim = f' class="fade" style="animation-delay:{delay:.2f}s"'
        self.body.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{esc(family)}" font-weight="{weight}" '
            f'font-size="{size}" fill="{fill}"{anchor_attr}{anim}>{esc(text)}</text>'
        )
        return measure(SANS, weight, size, text)

    # ---------- output ----------

    def svg(self) -> str:
        w, h = self.w, self.h
        smear = ""
        if self.smear:
            name, x, y, sw, sh, op = self.smear
            smear = f'<image href="{tile(name)}" x="{x}" y="{y}" width="{sw}" height="{sh}" opacity="{op}" preserveAspectRatio="none"/>'
        defs = (
            f'<clipPath id="board"><rect width="{w}" height="{h}" rx="14"/></clipPath>'
            f'<mask id="gl" maskUnits="userSpaceOnUse" x="0" y="0" width="{w}" height="{h}"><rect width="{w}" height="{h}" fill="#fff" filter="url(#grain-line)"/></mask>'
            f'<mask id="glg" maskUnits="userSpaceOnUse" x="0" y="0" width="{w}" height="{h}"><rect width="{w}" height="{h}" fill="#fff" filter="url(#grain-lg)"/></mask>'
            f'<mask id="gsolid" maskUnits="userSpaceOnUse" x="0" y="0" width="{w}" height="{h}"><rect width="{w}" height="{h}" fill="#b3b3b3"/><rect width="{w}" height="{h}" fill="#fff" filter="url(#grain-sm)"/></mask>'
            f'<mask id="gs" maskUnits="userSpaceOnUse" x="0" y="0" width="{w}" height="{h}"><rect width="{w}" height="{h}" fill="#fff" filter="url(#grain-sm)"/></mask>'
            + FILTERS + "".join(self.defs)
        )
        style = self.fonts.css() + CSS + f".acc .stk{{stroke:{ACC};fill:{ACC}}}"
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{esc(self.title)}"><title>{esc(self.title)}</title>'
            f"<style>{style}</style><defs>{defs}</defs>"
            f'<g clip-path="url(#board)"><rect width="{w}" height="{h}" fill="{SLATE}"/>'
            f'<rect width="{w}" height="{h}" filter="url(#slate)"/>{smear}{"".join(self.body)}</g></svg>'
        )
