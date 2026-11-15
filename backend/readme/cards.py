"""The images of the GitHub profile README, one function each.

Copy comes from the same places as the site's (content/i18n/en.yaml,
content/resume.yaml), numbers from data.py. Every card is a slate board of
FULL or HALF width; cards shown side by side share a height.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from ..content import get_content
from ..i18n import catalogs
from . import chalk
from .board import ACC, CHALK, DIM, FULL, HALF, MUTE, SLATE_DEEP, Board
from .data import ReadmeData
from .fonts import HAND, SANS, fit, measure

PAIR_H = 300  # height of the half cards


def _t(key: str) -> str:
    return catalogs()["en"].t(key)


def heading(b: Board, x: float, y: float, text: str, *, rule_to: float | None = None, size: float = 30) -> None:
    """A card's title in chalk with a quick stroke under it."""
    w = b.hand(x, y, text, size, kind="md")
    end = rule_to if rule_to is not None else x + w + 10
    r = random.Random(b.seed())
    b.strokes(chalk.stroke([(x - 4, y + 9 + r.random() * 2), (end, y + 6 + r.random() * 3)], b.seed(),
                           w=2.2, amp=1.2, step=14, cut=0.55, tl=b.tl))


# ---------- header ----------

def header(d: ReadmeData) -> str:
    name = get_content().resume["personal"]["name"]
    role, school, note = _t("hero.role"), _t("hero.school"), _t("hero.note")
    b = Board(FULL, 268, f"{name}. {role} {school} {note[:1].upper() + note[1:]}.", seed=11,
              smear=("smear1", 420, -40, 520, 300, 0.55))
    b.tl.speed = 0.8
    w = b.hand(44, 102, name, 70, kind="lg")
    r = random.Random(3)
    b.strokes(chalk.stroke([(30, 126 + r.random() * 2), (44 + w + 30, 119)], b.seed(), w=3.4, amp=1.4, step=14, cut=0.55, tl=b.tl))
    b.tl.pause(0.1)
    b.sans(46, 180, role, 26, weight=600)
    b.sans(46, 216, school, 17, fill=DIM)
    # the margin note, like on the site: yellow chalk with an arrow towards the name
    b.tl.pause(0.2)
    nx, ny = 616, 62
    b.hand(nx, ny, note.split(" a ")[0] + " a", 25, weight=400, kind="acc", rotate=-4)
    b.hand(nx + 4, ny + 32, note.split(" a ", 1)[1], 25, weight=400, kind="acc", rotate=-4)
    return b.svg()


# ---------- numbers ----------

def stats(d: ReadmeData) -> str:
    items = [
        (f"{d.contributions:,}", "contributions in the last year"),
        (f"{d.commits:,}", "commits"),
        (f"{d.repos}", "public repositories"),
        (f"{d.pull_requests}", "pull requests"),
    ]
    b = Board(HALF, PAIR_H, "Numbers: " + ", ".join(f"{n} {label}" for n, label in items), seed=21)
    heading(b, 30, 52, "numbers")
    # a 2 x 2 grid, its lines drawn in chalk between the cells
    x0, y0, cw, ch = 30, 78, 176, 104
    b.strokes(chalk.stroke([(x0 + cw, y0 + 12), (x0 + cw + 2, y0 + 2 * ch - 4)], b.seed(), w=1.8, amp=1, tl=b.tl, op=0.5))
    b.strokes(chalk.stroke([(x0 - 4, y0 + ch + 2), (x0 + 2 * cw + 6, y0 + ch)], b.seed(), w=1.8, amp=1, tl=b.tl, op=0.5))
    for i, (num, label) in enumerate(items):
        cx = x0 + (i % 2) * cw + (22 if i % 2 else 0)
        cy = y0 + (i // 2) * ch + (8 if i // 2 else 0)
        b.hand(cx, cy + 50, num, 44, kind="md")
        for k, ln in enumerate(_wrap(label, 14, cw - 34)):
            b.sans(cx + 1, cy + 72 + k * 17, ln, 14, fill=DIM)
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
    heading(b, 30, 52, "languages")
    top = 96
    gap = (PAIR_H - top - 22) / max(1, len(langs))
    bar_x, bar_max = 142, 196
    for i, (name, share) in enumerate(langs):
        y = top + i * gap
        b.hand(132, y + 6, name, 22, weight=700, kind="sm", anchor="end")
        length = max(6, bar_max * share / langs[0][1])
        r = random.Random(b.seed())
        pts = [(bar_x, y + r.random() * 2 - 1), (bar_x + length, y + r.random() * 2 - 1)]
        b.strokes(chalk.stroke(pts, b.seed(), w=9 if i == 0 else 7, amp=1.0, step=12, cut=0.9, tl=b.tl))
        b.sans(bar_x + length + 10, y + 5, f"{share:.0%}" if share >= 0.01 else "<1%", 14, fill=DIM)
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


def activity(d: ReadmeData) -> str:
    weeks = weekly(d.days)
    if len(weeks) > 1 and (d.days[-1][0] - weeks[-1][0]).days < 6:
        weeks = weeks[:-1]  # the week still running would read as a drop
    peak_i = max(range(len(weeks)), key=lambda i: weeks[i][1])
    peak = weeks[peak_i][1] or 1
    left, right, top, base = 40, FULL - 40, 92, 206
    h = base + 40
    b = Board(FULL, h, f"Contributions over the last year: {d.contributions:,}. Busiest week: {peak} "
              f"(from {weeks[peak_i][0]:%d %B %Y}).", seed=41)
    heading(b, 30, 48, "the last year")
    b.hand(FULL - 34, 46, f"{d.contributions:,} contributions", 23, weight=400, kind="acc", anchor="end", rotate=-2)
    step = (right - left) / max(1, len(weeks) - 1)
    pts = [(left + i * step, base - (base - top) * c / peak) for i, (_, c) in enumerate(weeks)]
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
    curve_tl = chalk.Timeline(t=b.tl.t, speed=0.25)
    b.strokes(chalk.stroke(pts, b.seed(), w=3, amp=0.7, step=10, cut=0.85, tl=curve_tl))
    # the busiest week, ringed in yellow with its number
    px, py = pts[peak_i]
    b.tl.t = curve_tl.t + 0.6
    b.strokes(chalk.ring(px, py, 15, 11, b.seed(), w=2, tl=b.tl), acc=True)
    label = f"busiest week: {peak}"
    anchor = "end" if px > FULL - 200 else "start"
    lx = px - 22 if anchor == "end" else px + 22
    b.hand(lx, max(top - 2, py - 8) + 4, label, 18, weight=400, kind="acc", anchor=anchor)
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
    heading(b, 30, 52, "lately")
    y = 104
    for repo in items:
        b.strokes(chalk.tick(30, y - 10, b.seed(), size=0.7, w=2, tl=b.tl))
        b.hand(54, y, fit(HAND, 700, 21, repo.name, 220), 21, kind="sm")
        b.sans(HALF - 30, y - 1, _ago(repo.pushed, now), 13, fill=MUTE, anchor="end")
        quip = QUIPS.get(repo.name.lower())
        if quip:
            b.hand(55, y + 21, fit(HAND, 400, 15, quip, HALF - 70), 15, weight=400, kind="acc")
            y += 48
        elif repo.description:
            b.sans(55, y + 20, fit(SANS, 400, 13.5, repo.description, HALF - 86), 13.5, fill=DIM)
            y += 48
        else:
            y += 34
    return b.svg()


# ---------- now building ----------

def now(d: ReadmeData) -> str:
    """Marshal and its risk gate, small: what I'm building at the moment."""
    proj = next(p for p in get_content().resume["projects"] if p["name"] == "Marshal")
    tagline = _t("hero.p_marshal")
    b = Board(HALF, PAIR_H, f"Now building: {proj['name']}, {tagline}. Every tool call gets a risk tier: "
              "safe calls run, confirm calls wait for my yes, forbidden calls never run.", seed=61)
    heading(b, 30, 52, "now building")
    b.hand(30, 112, proj["name"], 42, kind="lg")
    b.sans(32, 138, tagline, 14, fill=DIM)
    cy = 220
    s = b.seed
    b.strokes(chalk.box(28, cy - 18, 84, 36, s(), sw=2, over=5, j=1.6))
    b.hand(70, cy + 6, "tool call", 17, kind="sm", anchor="middle")
    b.strokes(chalk.arrow([(118, cy), (148, cy)], s(), w=2, head=8, tl=b.tl))
    b.strokes(chalk.stroke([(152, cy, 2), (182, cy - 30, 2), (212, cy, 2), (182, cy + 30, 2), (152, cy, 2)], s(), w=2, amp=0.8, step=8, tl=b.tl))
    b.hand(182, cy + 5, "risk", 15, kind="sm", anchor="middle")
    b.strokes(chalk.arrow([(214, cy - 4), (232, cy - 34), (258, cy - 44)], s(), w=2, head=8, tl=b.tl))
    b.strokes(chalk.arrow([(214, cy), (258, cy)], s(), w=2, head=8, tl=b.tl), acc=True)
    b.strokes(chalk.arrow([(214, cy + 4), (232, cy + 34), (258, cy + 44)], s(), w=2, head=8, tl=b.tl))
    b.hand(266, cy - 39, "runs", 17, kind="sm")
    b.strokes(chalk.tick(306, cy - 48, s(), size=0.6, w=2, tl=b.tl))
    b.hand(266, cy + 5, "waits for my yes", 17, weight=400, kind="acc")
    b.hand(266, cy + 49, "never runs", 17, kind="sm")
    b.strokes(chalk.line((352, cy + 32), (364, cy + 46), s(), w=2, amp=0.4, tl=b.tl)
              + chalk.line((364, cy + 32), (352, cy + 46), s(), w=2, amp=0.4, tl=b.tl))
    return b.svg()


# ---------- the way to the site ----------

def cta(d: ReadmeData) -> str:
    b = Board(FULL, 150, "Projects with write-ups and diagrams, the résumé and an assistant that answers "
              "questions about me are on gruenzweil.cc.", seed=71)
    bx, by, bw, bh = 34, 44, 236, 58
    b.add(f'<g class="fade" style="animation-delay:{b.tl.t:.2f}s" mask="url(#gsolid)">'
          + chalk.solid(bx, by, bw, bh, b.seed(), color=CHALK, dark=SLATE_DEEP) + "</g>")
    b.strokes(chalk.box(bx, by, bw, bh, b.seed(), sw=2.4, over=6, j=2.4, tl=b.tl))
    b.sans(bx + 26, by + 37, "gruenzweil.cc", 22, weight=600, fill=SLATE_DEEP, fade=False)
    b.add(f'<g transform="translate({bx + 194} {by + 29})">'
          f'<path d="M-12 0H8M1-7 8 0 1 7" fill="none" stroke="{SLATE_DEEP}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></g>')
    b.tl.pause(0.2)
    b.sans(304, 68, "Projects with write-ups and diagrams, the résumé,", 16, fill=DIM)
    b.sans(304, 92, "and an assistant that answers questions about me.", 16, fill=DIM)
    b.hand(FULL - 34, 130, "served from my homelab", 20, weight=400, kind="acc", anchor="end", rotate=-2)
    return b.svg()
