"""Resume PDF generation.

Two output files, generated from /content via WeasyPrint:
  - /static/generated/resume.pdf         (light, ATS-friendly)
  - /static/generated/resume-themed.pdf  (dark, matches site)

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
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings
from .content import get_content

log = logging.getLogger(__name__)

# Filesystem layout
_GENERATED_DIR = Path(settings.static_generated_dir)
_LIGHT_OUT = _GENERATED_DIR / "resume.pdf"
_DARK_OUT = _GENERATED_DIR / "resume-themed.pdf"

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
        _render_one("resume_light.html", _LIGHT_OUT)
        _render_one("resume_dark.html", _DARK_OUT)
    return _LIGHT_OUT, _DARK_OUT


def ensure_resume_pdfs() -> tuple[Path, Path]:
    """Return existing PDFs, generating them if missing. Used at startup."""
    if not _LIGHT_OUT.exists() or not _DARK_OUT.exists():
        log.info("Resume PDFs missing, generating (light=%s dark=%s)",
                 _LIGHT_OUT.exists(), _DARK_OUT.exists())
        return regenerate_resume_pdfs()
    return _LIGHT_OUT, _DARK_OUT
