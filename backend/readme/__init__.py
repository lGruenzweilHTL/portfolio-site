"""The images of the GitHub profile README (github.com/lGruenzweilHTL), drawn in chalk.

The README itself lives in the profile repository and is mostly <img> tags
pointing here: /readme/<name>.svg (routes/readme.py). Each image is a
standalone SVG in the site's look (board.py, chalk.py, fonts.py) with live
numbers from GitHub (data.py). They are drawn on startup and then every
few hours by readme_loop(), into static/generated/readme/, and served from
there, so a request never waits on GitHub.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Callable

from ..config import settings
from . import cards
from .data import ReadmeData, collect

log = logging.getLogger(__name__)

# name -> drawing function; the name is the URL: /readme/<name>.svg
IMAGES: dict[str, Callable[[ReadmeData], str]] = {
    "header": cards.header,
    "stats": cards.stats,
    "languages": cards.languages,
    "activity": cards.activity,
    "recent": cards.recent,
    "now": cards.now,
    "cta": cards.cta,
}

INTERVAL_SECONDS = 6 * 3600


def out_dir() -> Path:
    return Path(settings.static_generated_dir) / "readme"


def render_all(data: ReadmeData | None = None) -> list[str]:
    """Draws every image and swaps it in atomically. Returns the names drawn.

    One image failing doesn't stop the others; its old file stays served.
    """
    data = data or collect()
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    drawn = []
    for name, draw in IMAGES.items():
        try:
            svg = draw(data)
        except Exception:  # noqa: BLE001 — keep the other images and the old file
            log.exception("readme: drawing %s failed", name)
            continue
        tmp = out / f".{name}.svg.tmp"
        tmp.write_text(svg, encoding="utf-8")
        os.replace(tmp, out / f"{name}.svg")
        drawn.append(name)
    log.info("readme: drew %d images%s", len(drawn), " (sample numbers)" if data.sample else "")
    return drawn


async def readme_loop() -> None:
    """Draw now, then every INTERVAL_SECONDS, until the task is cancelled."""
    while True:
        try:
            await asyncio.to_thread(render_all)
        except Exception:  # noqa: BLE001 — a bad run must not end the loop
            log.exception("readme: render failed")
        await asyncio.sleep(INTERVAL_SECONDS)
