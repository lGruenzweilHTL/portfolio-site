"""Chalk strokes as SVG markup: the geometry of site/chalk.js, ported.

A stroke is a polyline that gets a slow sideways drift and a small hand
tremor, then is drawn twice (a full pass and a thinner one that lifts off
early) with a heavier deposit where the chalk lands. Points are (x, y) or
(x, y, 2) for a sharp corner. Every function takes a seed, so an image
comes out the same on every render.

`Timeline` hands out animation delays: strokes draw themselves one after
another with `stroke-dashoffset` (pathLength=1), which plays inside an
<img> too. With reduced motion the CSS in board.py turns it all off and
the strokes are simply there.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

Point = tuple[float, ...]


def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else str(v)


def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def poly_len(pts: list[Point]) -> float:
    return sum(dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def subdivide(pts: list[Point], step: float) -> list[Point]:
    out: list[Point] = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = max(1, round(dist(a, b) / step))
        for k in range(1, n + 1):
            corner = b[2] if k == n and len(b) > 2 else 0
            out.append((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n, corner))
    return out


def wobble(pts: list[Point], r: random.Random, amp: float) -> list[Point]:
    """A slow sideways drift plus a small tremor; the ends get nudged."""
    length = poly_len(pts) or 1
    f = max(0.6, length / 240)
    ph, ph2 = r.random() * 6.28, r.random() * 6.28
    acc = 0.0
    out = []
    for i, p in enumerate(pts):
        if i:
            acc += dist(pts[i - 1], p)
        a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        nx, ny = -(b[1] - a[1]), b[0] - a[0]
        nl = math.hypot(nx, ny) or 1
        nx, ny = nx / nl, ny / nl
        t = acc / length
        o = amp * (0.65 * math.sin(6.283 * f * t + ph) + 0.35 * math.sin(6.283 * f * 2.7 * t + ph2)) \
            + amp * 0.35 * (r.random() - 0.5)
        e = amp * 0.8 if i in (0, len(pts) - 1) else 0
        corner = p[2] if len(p) > 2 else 0
        out.append((p[0] + nx * o + (r.random() - 0.5) * e, p[1] + ny * o + (r.random() - 0.5) * e, corner))
    return out


def to_path(p: list[Point]) -> str:
    """Catmull-Rom through the points; corners (p[2] == 2) stay sharp."""
    if len(p) < 3:
        return f"M{_f(p[0][0])} {_f(p[0][1])}L{_f(p[-1][0])} {_f(p[-1][1])}"
    d = [f"M{_f(p[0][0])} {_f(p[0][1])}"]
    for i in range(len(p) - 1):
        p0 = p[i - 1] if i > 0 else p[i]
        p1, p2 = p[i], p[i + 1]
        p3 = p[i + 2] if i + 2 < len(p) else p2
        k1 = 0 if len(p1) > 2 and p1[2] == 2 else 1
        k2 = 0 if len(p2) > 2 and p2[2] == 2 else 1
        d.append(
            f"C{_f(p1[0] + k1 * (p2[0] - p0[0]) / 6)} {_f(p1[1] + k1 * (p2[1] - p0[1]) / 6)} "
            f"{_f(p2[0] - k2 * (p3[0] - p1[0]) / 6)} {_f(p2[1] - k2 * (p3[1] - p1[1]) / 6)} "
            f"{_f(p2[0])} {_f(p2[1])}"
        )
    return "".join(d)


@dataclass
class Timeline:
    """Start times for things that draw themselves, one after another."""
    t: float = 0.0
    speed: float = 1.0

    def take(self, length: float, overlap: float = 0.75) -> tuple[float, float]:
        """(delay, duration) in seconds for a stroke of this length."""
        d = min(0.55, max(0.1, length / 1300)) / self.speed
        at = self.t
        self.t += d * overlap
        return at, d

    def text(self, chars: int, overlap: float = 0.8) -> tuple[float, float]:
        d = min(0.6, 0.12 + chars * 0.025) / self.speed
        at = self.t
        self.t += d * overlap
        return at, d

    def pause(self, seconds: float) -> None:
        self.t += seconds


def _anim(at: float | None, d: float | None) -> str:
    if at is None:
        return ""
    return f' style="animation-delay:{at:.2f}s;animation-duration:{d:.2f}s"'


def stroke(
    pts: list[Point],
    seed: int,
    *,
    w: float = 3.0,
    amp: float | None = None,
    step: float = 9,
    cut: float | None = None,
    op: float = 0.92,
    tl: Timeline | None = None,
    color: str | None = None,
) -> str:
    """One chalk stroke along pts (user units)."""
    r = random.Random(seed)
    amp = min(2.2, w * 0.55) if amp is None else amp
    corners = [p for p in pts[1:-1] if len(p) > 2 and p[2] == 2]
    dense = subdivide(pts, step)
    p1 = wobble(dense, r, amp)
    at, d = tl.take(poly_len(dense)) if tl else (None, None)
    anim = _anim(at, d)
    col = f' stroke="{color}" fill="{color}"' if color else ""
    parts = [f'<g class="stk"{col}>']
    parts.append(f'<path class="rs" pathLength="1" d="{to_path(p1)}" stroke-width="{w:.2f}" opacity="{op}"{anim}/>')
    n = max(2, round(len(dense) * (cut if cut is not None else 0.7 + r.random() * 0.25)))
    p2 = [(p[0] + (r.random() - 0.5) * w * 0.5, p[1] + (r.random() - 0.5) * w * 0.5, p[2])
          for p in wobble(dense[:n], r, amp * 1.25)]
    if len(p2) > 1:
        parts.append(f'<path class="rs" pathLength="1" d="{to_path(p2)}" stroke-width="{w * 0.58:.2f}" opacity=".5"{anim}/>')
    dep_anim = f' style="animation-delay:{at:.2f}s"' if at is not None else ""
    parts.append(
        f'<ellipse class="dep" cx="{_f(p1[0][0])}" cy="{_f(p1[0][1])}" rx="{w * 0.78:.2f}" ry="{w * 0.62:.2f}" opacity=".55"{dep_anim}/>'
    )
    for c in corners:
        parts.append(f'<circle class="dep" cx="{_f(c[0])}" cy="{_f(c[1])}" r="{w * 0.55:.2f}" opacity=".38"{dep_anim}/>')
    parts.append("</g>")
    return "".join(parts)


def line(a: Point, b: Point, seed: int, **kw) -> str:
    return stroke([a, b], seed, **kw)


def arrow_head(a: Point, b: Point, seed: int, *, size: float = 12, w: float = 2.4, **kw) -> str:
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    out = []
    for s in (-1, 1):
        t = ang + math.pi + s * 0.48
        out.append(stroke([(b[0], b[1]), (b[0] + math.cos(t) * size, b[1] + math.sin(t) * size)],
                          seed + s, w=w, amp=0.5, cut=1, **kw))
    return "".join(out)


def arrow(pts: list[Point], seed: int, *, w: float = 2.4, head: float = 12, **kw) -> str:
    return stroke(pts, seed, w=w, **kw) + arrow_head(pts[-2], pts[-1], seed + 5, size=head, w=w, **kw)


def ellipse_pts(cx: float, cy: float, rx: float, ry: float, r: random.Random, turns: float = 1.1) -> list[Point]:
    a0 = -2.2 + r.random() * 0.6
    n = 48
    return [
        (cx + math.cos(a0 + 6.283 * turns * i / n) * rx * (1 + 0.05 * (i / n - 0.5)),
         cy + math.sin(a0 + 6.283 * turns * i / n) * ry * (1 + 0.05 * (i / n - 0.5)))
        for i in range(n + 1)
    ]


def ring(cx: float, cy: float, rx: float, ry: float, seed: int, *, w: float = 2.4, turns: float = 1.1, **kw) -> str:
    return stroke(ellipse_pts(cx, cy, rx, ry, random.Random(seed), turns), seed, w=w, amp=1.4, step=7, **kw)


def box(x: float, y: float, w: float, h: float, seed: int, *, sw: float = 2.4, over: float | None = None,
        j: float = 2.2, amp: float = 1.1, **kw) -> str:
    """Four separate strokes that overshoot at the corners."""
    r = random.Random(seed)
    ov = over if over is not None else min(9, max(4, min(w, h) * 0.12))

    def jit() -> float:
        return (r.random() - 0.5) * j

    sides = [
        [(x - ov * r.random(), y + jit()), (x + w + ov * (0.4 + r.random() * 0.6), y + jit())],
        [(x + w + jit(), y - ov * r.random()), (x + w + jit(), y + h + ov * (0.3 + r.random() * 0.7))],
        [(x + w + ov * r.random(), y + h + jit()), (x - ov * (0.3 + r.random() * 0.7), y + h + jit())],
        [(x + jit(), y + h + ov * r.random()), (x + jit(), y - ov * (0.3 + r.random() * 0.7))],
    ]
    return "".join(stroke(s, r.randrange(1 << 30), w=sw, amp=amp, **kw) for s in sides)


def tick(x: float, y: float, seed: int, *, size: float = 1.0, w: float = 2.4, **kw) -> str:
    """A check mark whose bottom corner sits at about (x + 6, y + 8)."""
    r = random.Random(seed)
    return stroke([(x + r.random() * 2, y + 1), (x + 6 * size, y + 8 * size, 2), (x + (21 + r.random() * 2) * size, y - (9 + r.random() * 2) * size)],
                  seed, w=w, amp=0.5, step=5, **kw)


def solid(x: float, y: float, w: float, h: float, seed: int, *, color: str, dark: str) -> str:
    """A block of chalk laid on with the side of the stick: solid, ragged edges."""
    r = random.Random(seed)

    def j(a: float) -> float:
        return (r.random() - 0.5) * a

    pts = []
    for px in range(0, int(w) + 1, 7):
        pts.append((x + px, y + 1 + j(2.6)))
    for py in range(0, int(h) + 1, 7):
        pts.append((x + w - 1 + j(2.6), y + py))
    for px in range(int(w), -1, -7):
        pts.append((x + px, y + h - 1 + j(2.6)))
    for py in range(int(h), -1, -7):
        pts.append((x + 1 + j(2.6), y + py))
    d = "M" + "L".join(f"{_f(p[0])} {_f(p[1])}" for p in pts) + "Z"
    out = [f'<path class="fill" d="{d}" fill="{color}"/>']
    for i in range(3):
        yy = y + (3 + r.random() * 3 if i == 0 else h - 4 - r.random() * 3 if i == 1 else 2 + r.random() * 2)
        out.append(
            f'<path d="M{_f(x + r.random() * w * 0.2)} {_f(yy)}L{_f(x + w * (0.6 + r.random() * 0.4))} {_f(yy + j(2))}" '
            f'stroke="{dark}" stroke-width="1.2" opacity=".25" fill="none"/>'
        )
    return "".join(out)
