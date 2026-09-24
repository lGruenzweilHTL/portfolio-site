"""Resume PDF generation.

Two output files, generated from /content via WeasyPrint:
  - /static/generated/resume.pdf        (styled, two-column, the default)
  - /static/generated/resume-ats.pdf    (single-column, ATS-friendly)

Both templates render the same content in the same order; only presentation
differs. The styled one is the human-facing version, the ATS one is what you
send through a recruiter's applicant tracking system.

Regenerated:
  - At app startup (covers first deploy)
  - On /webhook/deploy (after a git pull)
  - On demand via regenerate_resume_pdfs() (admin/tools)

Served by the routes/resume.py router as FileResponse, so once the files
exist there's zero per-request render cost.

WeasyPrint notes:
  - First import is slow (~1-2s) because it loads Cairo/Pango bindings.
  - We import lazily inside render() so a missing system lib doesn't take
    down the app at import time — the resume endpoint will 500 instead.
  - The icon font is vendored under templates/assets/devicon/ and subset by
    scripts/build_devicon_subset.py, so rendering needs no network access.
    Don't introduce CDN <link>s here; a CDN outage fails silently as blank
    gaps in the PDF.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings
from .content import get_content

log = logging.getLogger(__name__)

# Filesystem layout
_GENERATED_DIR = Path(settings.static_generated_dir)
_DESIGNED_OUT = _GENERATED_DIR / "resume.pdf"
_ATS_OUT = _GENERATED_DIR / "resume-ats.pdf"

# resume.yaml date fields, e.g. start: 2026-08 -> "08 / 2026", 2026 -> "2026".
_YEAR_MONTH_RE = re.compile(r"(\d{4})-(\d{1,2})")
# Values that mean "still going" rather than a real date.
_OPEN_ENDED = frozenset({"present", "now", "current", "heute"})


def fmt_date(value: object) -> str:
    """Render one resume date for display.

    Accepts "2026-08", a bare year (2026 or "2026"), or an open-ended marker
    ("present"/"now"/"current"/"heute"). Open-ended and empty values render as
    "" so callers can substitute their own "Present" wording; anything
    unrecognised is passed through untouched rather than dropped.
    """
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)

    text = str(value).strip()
    if not text or text.lower() in _OPEN_ENDED:
        return ""

    match = _YEAR_MONTH_RE.fullmatch(text)
    if match:
        year, month = match.group(1), int(match.group(2))
        return f"{month:02d} / {year}"
    return text

# WeasyPrint + Jinja are expensive imports; do them once, lazily.
_jinja_env: Environment | None = None
_weasyprint_html_cls = None
_render_lock = threading.Lock()


def _ensure_jinja() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(settings.templates_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )
        # `trim` and `replace` filters are built-in to Jinja2.
        _jinja_env.filters["fmt_date"] = fmt_date
    return _jinja_env


def _ensure_weasyprint():
    global _weasyprint_html_cls
    if _weasyprint_html_cls is None:
        from weasyprint import HTML  # heavy import
        _weasyprint_html_cls = HTML
    return _weasyprint_html_cls


def _render_one(template_name: str, out_path: Path) -> None:
    """Render one template to PDF. Caller holds _render_lock."""
    env = _ensure_jinja()
    HTML = _ensure_weasyprint()

    content = get_content()
    html_str = env.get_template(template_name).render(r=content.resume)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same dir, then atomic-rename so a partially
    # written PDF never appears at the served path.
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    HTML(string=html_str, base_url=str(Path(settings.templates_dir).resolve())).write_pdf(str(tmp))
    tmp.replace(out_path)
    log.info("Rendered %s (%d bytes)", out_path.name, out_path.stat().st_size)


def regenerate_resume_pdfs() -> tuple[Path, Path]:
    """Render both PDFs. Safe to call concurrently (serialised internally)."""
    with _render_lock:
        _render_one("resume.html", _DESIGNED_OUT)
        _render_one("resume_ats.html", _ATS_OUT)
    return _DESIGNED_OUT, _ATS_OUT


def ensure_resume_pdfs() -> tuple[Path, Path]:
    """Return existing PDFs, generating them if missing. Used at startup."""
    if not _DESIGNED_OUT.exists() or not _ATS_OUT.exists():
        log.info("Resume PDFs missing, generating (designed=%s ats=%s)",
                 _DESIGNED_OUT.exists(), _ATS_OUT.exists())
        return regenerate_resume_pdfs()
    return _DESIGNED_OUT, _ATS_OUT
