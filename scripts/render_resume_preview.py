"""Render both résumé templates to HTML you can open in a browser.

WeasyPrint can't run on every dev machine (it needs system Pango/Cairo), so this
is the fast way to eyeball a template change: it runs the same Jinja environment
and the same /content/resume.yaml the PDF pipeline uses, and writes the result
next to the templates.

    python scripts/render_resume_preview.py

Output lands in backend/templates/_preview_*.html — inside the templates
directory on purpose, so the styled template's relative
`assets/devicon/devicon.min.css` reference resolves exactly as it does for the
PDF (WeasyPrint gets base_url=templates_dir). Open the files directly; there's
no server involved. They're gitignored.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Running this file directly puts scripts/ on sys.path, not the repo root, so the
# `backend` package wouldn't be importable without this.
sys.path.insert(0, str(REPO_ROOT))

from backend.content import load_content  # noqa: E402
from backend.resume import _ensure_jinja  # noqa: E402

TEMPLATES = REPO_ROOT / "backend" / "templates"
PREVIEWS = [
    ("resume.html", "_preview_resume.html"),
    ("resume_ats.html", "_preview_resume_ats.html"),
]


def main() -> int:
    content = load_content()
    env = _ensure_jinja()

    for template_name, output_name in PREVIEWS:
        html = env.get_template(template_name).render(r=content.resume)
        out_path = TEMPLATES / output_name
        out_path.write_text(html, encoding="utf-8")
        print(f"{template_name:18} -> {out_path}")

    print("\nOpen them in a browser to check the layout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
