"""GET /resume.pdf and GET /resume-ats.pdf — serve the cached PDFs.

We don't render on request. Both files are generated:
  - at app startup (see main.py startup hook)
  - on POST /webhook/deploy (after a git pull)

If the file is somehow missing (e.g. WeasyPrint unavailable on first boot),
the endpoint returns 503 with a clear message rather than 500ing.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

from ..config import settings
from ..resume import regenerate_resume_pdfs

log = logging.getLogger(__name__)

router = APIRouter()

# Files are served out of the generated dir, which lives outside the /site
# static mount. We expose them at well-known URLs and keep the directory
# gitignored.
_PDF_DIR = Path(settings.static_generated_dir)
_DESIGNED = _PDF_DIR / "resume.pdf"
_ATS = _PDF_DIR / "resume-ats.pdf"


def _serve(path: Path, download_name: str) -> FileResponse:
    if not path.exists():
        # Best-effort: try to regenerate on the fly. If WeasyPrint is
        # broken, this raises and we return 503.
        try:
            regenerate_resume_pdfs()
        except Exception as e:
            log.exception("Resume regeneration failed")
            raise HTTPException(
                status_code=503,
                detail=f"Resume PDF is not generated and could not be generated: {e}",
            )
    if not path.exists():
        raise HTTPException(status_code=503, detail="Resume PDF not available")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            # 1 hour cache; deploy webhook re-renders so content is fresh on push.
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": f'inline; filename="{download_name}"',
        },
    )


@router.get("/resume.pdf", include_in_schema=False)
def resume_pdf() -> FileResponse:
    """Styled two-column variant. The default 'send to a human' link."""
    return _serve(_DESIGNED, "lukas-gruenzweil-resume.pdf")


@router.get("/resume-ats.pdf", include_in_schema=False)
def resume_ats_pdf() -> FileResponse:
    """Single-column ATS variant. The 'submit to a portal' link."""
    return _serve(_ATS, "lukas-gruenzweil-resume-ats.pdf")


@router.get("/resume-themed.pdf", include_in_schema=False)
def resume_themed_pdf() -> RedirectResponse:
    """The old dark variant is gone — /resume.pdf is now the styled one.

    Kept as a permanent redirect so existing bookmarks and any cached link to
    the old themed PDF don't 404.
    """
    return RedirectResponse("/resume.pdf", status_code=301)


@router.post("/admin/regenerate-resume", include_in_schema=False)
def admin_regenerate() -> PlainTextResponse:
    """Manual regeneration trigger. Used by the admin UI / deploy scripts."""
    try:
        designed, ats = regenerate_resume_pdfs()
    except Exception as e:
        log.exception("Manual resume regeneration failed")
        raise HTTPException(status_code=500, detail=str(e))
    return PlainTextResponse(f"regenerated: {designed.name}, {ats.name}")
