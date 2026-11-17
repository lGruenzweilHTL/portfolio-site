"""The images of the GitHub profile README, one function each.

Copy comes from the same places as the site's (content/i18n/en.yaml,
content/resume.yaml), numbers from data.py. Every card is a slate board of
FULL or HALF width; cards shown side by side share a height.

GitHub shows the boards at about their own width, next to README text set at
14 px that a README can't make larger, so the boards' type stays close to it:
14 px body, 22 px card titles, nothing but the name above 32 px.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

from ..content import get_content
from ..i18n import catalogs
from . import chalk
from .board import ACC, CHALK, DIM, FULL, HALF, MUTE, SLATE_DEEP, Board
from .data import ReadmeData
from .fonts import HAND, SANS, fit, measure

PAIR_H = 256  # height of the half cards


def _t(key: str) -> str:
    return catalogs()["en"].t(key)


def heading(b: Board, x: float, y: float, text: str, *, rule_to: float | None = None, size: float = 22) -> float:
    """A card's title in chalk with a quick stroke under it. Returns the title's width."""
    w = b.hand(x, y, text, size, kind="md")
    end = rule_to if rule_to is not None else x + w + 10
    r = random.Random(b.seed())
    b.strokes(chalk.stroke([(x - 4, y + 7 + r.random() * 2), (end, y + 5 + r.random() * 2)], b.seed(),
                           w=1.9, amp=1.1, step=14, cut=0.55, tl=b.tl))
    return w


# ---------- header ----------

def header(d: ReadmeData) -> str:
    name = get_content().resume["personal"]["name"]
    role, school, note = _t("hero.role"), _t("hero.school"), _t("hero.note")
    b = Board(FULL, 206, f"{name}. {role} {school} {note[:1].upper() + note[1:]}.", seed=11,
              smear=("smear1", 420, -40, 520, 300, 0.55))
    b.tl.speed = 0.8
    w = b.hand(40, 82, name, 52, kind="lg")
    r = random.Random(3)
    b.strokes(chalk.stroke([(28, 100 + r.random() * 2), (40 + w + 24, 95)], b.seed(), w=2.8, amp=1.3, step=14, cut=0.55, tl=b.tl))
    b.tl.pause(0.1)
    b.sans(42, 142, role, 19, weight=600)
    b.sans(42, 168, school, 14, fill=DIM)
    # the margin note, like on the site: yellow chalk with an arrow towards the name
    b.tl.pause(0.2)
    nx, ny = 640, 54
    b.hand(nx, ny, note.split(" a ")[0] + " a", 19, weight=400, kind="acc", rotate=-4)
    b.hand(nx + 3, ny + 25, note.split(" a ", 1)[1], 19, weight=400, kind="acc", rotate=-4)
    return b.svg()


# ---------- numbers ----------

def stats(d: ReadmeData) -> str:
    items = [
        (f"{d.contributions:,}", "public contributions in the last year"),
        (f"{d.commits:,}", "commits"),
        (f"{d.repos}", "public repositories"),
        (f"{d.pull_requests}", "pull requests"),
    ]
    b = Board(HALF, PAIR_H, "Numbers: " + ", ".join(f"{n} {label}" for n, label in items), seed=21)
    heading(b, 30, 44, "numbers")
    # a 2 x 2 grid, its lines drawn in chalk between the cells
    x0, y0, cw, ch = 30, 64, 176, 90
    b.strokes(chalk.stroke([(x0 + cw, y0 + 12), (x0 + cw + 2, y0 + 2 * ch - 4)], b.seed(), w=1.8, amp=1, tl=b.tl, op=0.5))
    b.strokes(chalk.stroke([(x0 - 4, y0 + ch + 2), (x0 + 2 * cw + 6, y0 + ch)], b.seed(), w=1.8, amp=1, tl=b.tl, op=0.5))
    for i, (num, label) in enumerate(items):
        cx = x0 + (i % 2) * cw + (22 if i % 2 else 0)
        cy = y0 + (i // 2) * ch + (8 if i // 2 else 0)
        b.hand(cx, cy + 42, num, 32, kind="md")
        for k, ln in enumerate(_wrap(label, 14, cw - 34)):
            b.sans(cx + 1, cy + 62 + k * 17, ln, 14, fill=DIM)
    return b.svg()


def _wrap(text: str, size: float, width: float, weight: int = 400) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for wd in words:
        nxt = f"{cur} {wd}".strip()
        if cur and measure(SANS, weight, size, nxt) > width:
            lines.append(cur)
            cur = wd
        else:
            cur = nxt
    if cur:
        lines.append(cur)
    return lines


# ---------- languages ----------

def languages(d: ReadmeData) -> str:
    langs = d.languages
    b = Board(HALF, PAIR_H, "Languages by code: " + ", ".join(f"{n} {s:.0%}" for n, s in langs), seed=31)
    heading(b, 30, 44, "languages")
    top = 84
    gap = (PAIR_H - top - 20) / max(1, len(langs))
    bar_x, bar_max = 126, 212
    for i, (name, share) in enumerate(langs):
        y = top + i * gap
        b.hand(116, y + 5, name, 17, weight=700, kind="sm", anchor="end")
        length = max(6, bar_max * share / langs[0][1])
        r = random.Random(b.seed())
        pts = [(bar_x, y + r.random() * 2 - 1), (bar_x + length, y + r.random() * 2 - 1)]
        b.strokes(chalk.stroke(pts, b.seed(), w=7 if i == 0 else 5.5, amp=0.9, step=12, cut=0.9, tl=b.tl))
        b.sans(bar_x + length + 10, y + 5, f"{share:.0%}" if share >= 0.01 else "<1%", 13, fill=DIM)
    return b.svg()


# ---------- activity ----------

def weekly(days: list[tuple[date, int]]) -> list[tuple[date, int]]:
    """Contributions per week (Sunday to Saturday), keyed by the week's first day."""
    out: list[tuple[date, int]] = []
    for day, count in days:
        if not out or (day - out[-1][0]).days >= 7:
            out.append((day, count))
        else:
            out[-1] = (out[-1][0], out[-1][1] + count)
    return out


def level(count: int, peak: int) -> float:
    """A week's height on the activity card, 0 to 1. Log scale, so one very busy
    week doesn't flatten every other week into the base line."""
    if peak <= 0:
        return 0.0
    return math.log1p(count) / math.log1p(peak)


def activity(d: ReadmeData) -> str:
    weeks = weekly(d.days)
    if len(weeks) > 1 and (d.days[-1][0] - weeks[-1][0]).days < 6:
        weeks = weeks[:-1]  # the week still running would read as a drop
    peak_i = max(range(len(weeks)), key=lambda i: weeks[i][1])
    peak = weeks[peak_i][1] or 1
    left, right, top, base = 40, FULL - 40, 80, 188
    h = base + 40
    b = Board(FULL, h, f"Public contributions per week over the last year, on a log scale: {d.contributions:,} in total. "
              f"Busiest week: {peak} (from {weeks[peak_i][0]:%d %B %Y}).", seed=41)
    hw = heading(b, 30, 44, "the last year")
    b.sans(30 + hw + 18, 43, "per week, log scale", 13, fill=MUTE)
    b.hand(FULL - 34, 42, f"{d.contributions:,} public contributions", 18, weight=400, kind="acc", anchor="end", rotate=-2)
    step = (right - left) / max(1, len(weeks) - 1)
    pts = [(left + i * step, base - (base - top) * level(c, peak)) for i, (_, c) in enumerate(weeks)]
    # hatching under the curve, like shading on a board
    cid = b.uid("area")
    area = "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + f"L{right} {base}L{left} {base}Z"
    b.defs.append(f'<clipPath id="{cid}"><path d="{area}"/></clipPath>')
    hatch = "".join(f"M{x:.1f} {base + 2}l{-(base - top + 6) * 0.6:.1f} {-(base - top + 6)}"
                    for x in range(left, right + 140, 9))
    at = b.tl.t + 0.2
    b.add(f'<g clip-path="url(#{cid})" mask="url(#gl)"><path class="fade" style="animation-delay:{at + 1:.2f}s;animation-duration:.8s" '
          f'd="{hatch}" stroke="{CHALK}" stroke-width="1.3" opacity=".2" fill="none"/></g>')
    # the base line, the month ticks, then the curve, drawn left to right
    b.strokes(chalk.stroke([(left - 8, base + 1), (right + 8, base)], b.seed(), w=2, amp=0.9, step=16, tl=b.tl, op=0.6))
    ticks = []
    for i, (day, _) in enumerate(weeks):
        first = day + timedelta(days=6)
        if first.day <= 7 and i > 0:  # the week holding the 1st of a month
            x = left + i * step
            ticks.append(f"M{x:.1f} {base - 3}v8")
            if x < right - 12:
                b.sans(x, base + 24, first.strftime("%b"), 12, fill=MUTE, anchor="middle", at=at)
    b.add(f'<g mask="url(#gl)"><path d="{"".join(ticks)}" stroke="{CHALK}" stroke-width="1.6" stroke-linecap="round" opacity=".5"/></g>')
    # faint lines at 1, 10, 100 ... so the log scale can be read off
    grid = []
    for v in (1, 10, 100, 1000):
        if v > peak:
            break
        y = base - (base - top) * level(v, peak)
        grid.append(f"M{left - 4} {y:.1f}H{right + 4}")
        b.sans(left - 10, y + 4, f"{v:,}", 11, fill=MUTE, anchor="end", at=at)
    if grid:
        b.add(f'<g mask="url(#gl)"><path class="fade" style="animation-delay:{at:.2f}s" d="{"".join(grid)}" '
              f'stroke="{CHALK}" stroke-width="1" stroke-dasharray="2 6" stroke-linecap="round" opacity=".35"/></g>')
    curve_tl = chalk.Timeline(t=b.tl.t, speed=0.25)
    b.strokes(chalk.stroke(pts, b.seed(), w=3, amp=0.7, step=10, cut=0.85, tl=curve_tl))
    # the busiest week, ringed in yellow with its number
    px, py = pts[peak_i]
    b.tl.t = curve_tl.t + 0.6
    b.strokes(chalk.ring(px, py, 13, 10, b.seed(), w=2, tl=b.tl), acc=True)
    label = f"busiest week: {peak}"
    anchor = "end" if px > FULL - 200 else "start"
    lx = px - 20 if anchor == "end" else px + 20
    b.hand(lx, max(top - 2, py - 8) + 4, label, 15, weight=400, kind="acc", anchor=anchor)
    return b.svg()


# ---------- lately ----------

def _ago(when: datetime, now: datetime) -> str:
    days = (now.date() - when.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


# A margin note in yellow chalk instead of the GitHub description, for repos that earned one.
QUIPS = {
    "naollm": "the most fun way to watch a robot walk into a wall",
    "lego-spike-serial": "a real protocol for hardware that never talks back",
}


def recent(d: ReadmeData, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    items = d.recent[:4]
    b = Board(HALF, PAIR_H, "Lately: " + "; ".join(f"{r.name}, pushed {_ago(r.pushed, now)}" for r in items), seed=51)
    heading(b, 30, 44, "lately")
    y = 88
    for repo in items:
        b.strokes(chalk.tick(30, y - 9, b.seed(), size=0.6, w=1.8, tl=b.tl))
        b.hand(52, y, fit(HAND, 700, 17, repo.name, 230), 17, kind="sm")
        b.sans(HALF - 30, y - 1, _ago(repo.pushed, now), 13, fill=MUTE, anchor="end")
        quip = QUIPS.get(repo.name.lower())
        if quip:
            b.hand(53, y + 19, fit(HAND, 400, 14, quip, HALF - 70), 14, weight=400, kind="acc")
            y += 42
        elif repo.description:
            b.sans(53, y + 19, fit(SANS, 400, 13, repo.description, HALF - 84), 13, fill=DIM)
            y += 42
        else:
            y += 30
    return b.svg()


# ---------- now building ----------

def now(d: ReadmeData) -> str:
    """Marshal and its risk gate, small: what I'm building at the moment."""
    proj = next(p for p in get_content().resume["projects"] if p["name"] == "Marshal")
    tagline = _t("hero.p_marshal")
    b = Board(HALF, PAIR_H, f"Now building: {proj['name']}, {tagline}. Every tool call gets a risk tier: "
              "safe calls run, confirm calls wait for my yes, forbidden calls never run.", seed=61)
    heading(b, 30, 44, "now building")
    b.hand(30, 92, proj["name"], 30, kind="lg")
    b.sans(31, 116, tagline, 14, fill=DIM)
    cy = 192
    s = b.seed
    b.strokes(chalk.box(28, cy - 18, 84, 36, s(), sw=2, over=5, j=1.6))
    b.hand(70, cy + 5, "tool call", 15, kind="sm", anchor="middle")
    b.strokes(chalk.arrow([(118, cy), (148, cy)], s(), w=2, head=8, tl=b.tl))
    b.strokes(chalk.stroke([(152, cy, 2), (182, cy - 30, 2), (212, cy, 2), (182, cy + 30, 2), (152, cy, 2)], s(), w=2, amp=0.8, step=8, tl=b.tl))
    b.hand(182, cy + 5, "risk", 14, kind="sm", anchor="middle")
    b.strokes(chalk.arrow([(214, cy - 4), (232, cy - 34), (258, cy - 44)], s(), w=2, head=8, tl=b.tl))
    b.strokes(chalk.arrow([(214, cy), (258, cy)], s(), w=2, head=8, tl=b.tl), acc=True)
    b.strokes(chalk.arrow([(214, cy + 4), (232, cy + 34), (258, cy + 44)], s(), w=2, head=8, tl=b.tl))
    b.hand(266, cy - 39, "runs", 15, kind="sm")
    b.strokes(chalk.tick(300, cy - 47, s(), size=0.55, w=2, tl=b.tl))
    b.hand(266, cy + 5, "waits for my yes", 15, weight=400, kind="acc")
    b.hand(266, cy + 49, "never runs", 15, kind="sm")
    b.strokes(chalk.line((344, cy + 34), (354, cy + 46), s(), w=2, amp=0.4, tl=b.tl)
              + chalk.line((354, cy + 34), (344, cy + 46), s(), w=2, amp=0.4, tl=b.tl))
    return b.svg()


# ---------- the way to the site ----------

def cta(d: ReadmeData) -> str:
    b = Board(FULL, 124, "Projects with write-ups and diagrams, the résumé and an assistant that answers "
              "questions about me are on gruenzweil.cc.", seed=71)
    bx, by, bw, bh = 34, 36, 200, 52
    b.add(f'<g class="fade" style="animation-delay:{b.tl.t:.2f}s" mask="url(#gsolid)">'
          + chalk.solid(bx, by, bw, bh, b.seed(), color=CHALK, dark=SLATE_DEEP) + "</g>")
    b.strokes(chalk.box(bx, by, bw, bh, b.seed(), sw=2.4, over=6, j=2.4, tl=b.tl))
    b.sans(bx + 22, by + 33, "gruenzweil.cc", 18, weight=600, fill=SLATE_DEEP, fade=False)
    b.add(f'<g transform="translate({bx + 166} {by + 26}) scale(.85)">'
          f'<path d="M-12 0H8M1-7 8 0 1 7" fill="none" stroke="{SLATE_DEEP}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></g>')
    b.tl.pause(0.2)
    b.sans(266, 58, "Projects with write-ups and diagrams, the résumé,", 14, fill=DIM)
    b.sans(266, 80, "and an assistant that answers questions about me.", 14, fill=DIM)
    return b.svg()
