"""The résumé templates pick and shorten projects the way resume.yaml says.

Rendered to HTML only (Jinja), so no WeasyPrint libraries are needed.
"""
from __future__ import annotations

import pytest

from backend import resume
from backend.content import load_content


def _project(**over) -> dict:
    proj = {"name": "Demo", "cv": True, "role": "Author", "summary": "Long site summary.",
            "highlights": ["Site highlight"]}
    proj.update(over)
    return proj


def _render(template: str, projects: list[dict]) -> str:
    r = dict(load_content().resume, projects=projects)
    return resume._ensure_jinja().get_template(template).render(r=r)


@pytest.mark.parametrize("template", ["resume.html", "resume_ats.html"])
def test_only_cv_projects_appear(template):
    html = _render(template, [_project(name="Shown"), _project(name="Hidden", cv=False)])
    assert "Shown" in html and "Hidden" not in html


@pytest.mark.parametrize("template", ["resume.html", "resume_ats.html"])
def test_cv_overrides_replace_the_site_copy(template):
    html = _render(template, [_project(cv_summary="Short CV summary.", cv_highlights=[])])
    assert "Short CV summary." in html
    assert "Long site summary." not in html and "Site highlight" not in html


@pytest.mark.parametrize("template", ["resume.html", "resume_ats.html"])
def test_without_overrides_the_site_copy_is_used(template):
    html = _render(template, [_project()])
    assert "Long site summary." in html and "Site highlight" in html
