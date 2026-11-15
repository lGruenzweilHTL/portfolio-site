"""GET /readme/<name>.svg: the GitHub profile README's images (backend/readme/).

Served from the files readme_loop() draws; nothing is rendered on request.
GitHub shows README images through its camo proxy, which honours
Cache-Control: no-cache plus the ETag FileResponse sets, so a new drawing
shows up on the profile without a stale hour and unchanged ones cost a 304.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..readme import IMAGES, out_dir

router = APIRouter()


@router.api_route("/readme/{name}.svg", methods=["GET", "HEAD"], include_in_schema=False)
def readme_image(name: str) -> FileResponse:
    if name not in IMAGES:
        raise HTTPException(status_code=404, detail="Not found")
    path = out_dir() / f"{name}.svg"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Not drawn yet")
    return FileResponse(path, media_type="image/svg+xml", headers={"Cache-Control": "public, no-cache"})
