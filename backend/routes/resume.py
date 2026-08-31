"""GET /resume.pdf and GET /resume-themed.pdf — serve the cached PDFs.

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
from fastapi.responses import FileResponse, PlainTextResponse

from ..config import settings
from ..resume import regenerate_resume_pdfs

log = logging.getLogger(__name__)

router = APIRouter()

# Files are served out of the generated dir, which lives outside the /site
# static mount. We expose them at well-known URLs and keep the directory
# gitignored.
_PDF_DIR = Path(settings.static_generated_dir)
_LIGHT = _PDF_DIR / "resume.pdf"
_DARK = _PDF_DIR / "resume-themed.pdf"


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
    """ATS-friendly light variant. The default 'send to a recruiter' link."""
    return _serve(_LIGHT, "lukas-gruenzweil-resume.pdf")


@router.get("/resume-themed.pdf", include_in_schema=False)
def resume_themed_pdf() -> FileResponse:
    """Dark, branded variant matching the site."""
    return _serve(_DARK, "lukas-gruenzweil-resume-themed.pdf")


@router.post("/admin/regenerate-resume", include_in_schema=False)
def admin_regenerate() -> PlainTextResponse:
    """Manual regeneration trigger. Used by the admin UI / deploy scripts."""
    try:
        light, dark = regenerate_resume_pdfs()
    except Exception as e:
        log.exception("Manual resume regeneration failed")
        raise HTTPException(status_code=500, detail=str(e))
    return PlainTextResponse(f"regenerated: {light.name}, {dark.name}")
