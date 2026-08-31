"""Loads and validates /content (YAML) at startup.

Two files:
  - resume.yaml           : structured facts (personal, projects, skills, etc.)
  - chatbot_personality.yaml : chatbot voice + system prompt template

Validation is shallow by design — Pydantic isn't used here because the schema
is evolving and over-modeling it upfront is friction. We check the required
top-level keys exist and the types make sense; deeper validation happens at
the point of use (PDF render, chat prompt render).

Reload: call load_content() again. Used by /webhook/deploy after a git pull.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import settings

log = logging.getLogger(__name__)


# --- Required structure (cheap schema check) ---------------------------------

REQUIRED_RESUME_KEYS = {
    "personal",
    "education",
    "projects",
    "skills",
    "beyond_code",
    "elevator_pitch",
}

REQUIRED_PERSONAL_KEYS = {
    "name",
    "title",
    "location",
    "email_personal",
    "email_school",
    "github",
    "github_url",
    "status",
}

REQUIRED_SKILLS_KEYS = {"languages", "tools_and_infra"}


class ContentError(ValueError):
    """Raised when /content is missing required keys or has wrong types."""


@dataclass
class Content:
    resume: dict[str, Any]
    persona: dict[str, Any]
    system_prompt_template: str

    @property
    def chatbot_system_prompt(self) -> str:
        """Render the system prompt with the current resume data.

        Simple placeholder replacement; deliberately not Jinja2 to keep this
        file dependency-light and the template legible in plain text.
        """
        tpl = self.system_prompt_template
        out = tpl
        # Personal
        p = self.resume["personal"]
        for key, val in p.items():
            out = out.replace("{{ resume.personal." + key + " }}", str(val))

        # Elevator pitch
        out = out.replace("{{ resume.elevator_pitch }}", self.resume["elevator_pitch"].strip())

        # Education
        edu_lines = []
        for ed in self.resume["education"]:
            line = f"- {ed.get('school', '?')}, {ed.get('location', '?')}"
            if ed.get("notes"):
                line += f" — {ed['notes']}"
            edu_lines.append(line)
        # Find the {% for ed ... %} {% endfor %} block and substitute.
        out = _render_simple_for(out, "ed in resume.education", edu_lines)

        # Projects
        proj_lines = []
        for proj in self.resume["projects"]:
            line = f"- {proj.get('name', '?')} ({proj.get('status', '?')}): {proj.get('summary', '').strip()}"
            stack = proj.get("stack") or []
            if stack:
                line += f"\n    Stack: {', '.join(stack)}"
            if proj.get("role"):
                line += f"\n    Role: {proj['role']}"
            if proj.get("repo_url"):
                line += f"\n    Repo: {proj['repo_url']}"
            proj_lines.append(line)
        out = _render_simple_for(out, "p in resume.projects", proj_lines)

        # Skills: strong langs, then familiar langs
        lang_lines = [f"- {l} (strong)" for l in self.resume["skills"]["languages"].get("strong", [])]
        lang_lines += [f"- {l} (familiar)" for l in self.resume["skills"]["languages"].get("familiar", [])]
        out = _render_simple_for_lang(out, lang_lines)

        # Beyond code
        bc_lines = []
        for item in self.resume["beyond_code"]:
            body = (item.get("body") or "").strip().replace("\n", " ")
            bc_lines.append(f"- {item.get('topic', '?')}: {body}")
        out = _render_simple_for(out, "item in resume.beyond_code", bc_lines)

        # Persona
        out = out.replace("{{ persona.name }}", self.persona.get("name", "Lukas's assistant"))
        out = out.replace("{{ persona.voice }}", (self.persona.get("voice") or "").strip())
        rules = self.persona.get("rules") or []
        rule_lines = [f"- {r}" for r in rules]
        out = _render_simple_for(out, "rule in persona.rules", rule_lines)

        return out


def _render_simple_for(template: str, iter_expr: str, lines: list[str]) -> str:
    """Replace a single {% for X in Y %}{% endfor %} block with joined lines.

    Robust enough for our template: doesn't handle nested loops, filters, or
    whitespace control. If the loop body references iter_expr.split()[0] (the
    loop var), we leave the line as-is — our templates only consume static
    text plus the loop var in 'ed.school' / 'p.name' / etc., which the caller
    has already pre-substituted by formatting the lines themselves.
    """
    marker_open = f"{{% for {iter_expr} %}}"
    marker_close = "{% endfor %}"
    if marker_open not in template:
        return template
    pre, rest = template.split(marker_open, 1)
    body, post = rest.split(marker_close, 1)
    # Indent the lines to match the indentation of the {% for %} tag.
    indent = pre[len(pre.rstrip("\n")):]  # whitespace at start of for-line
    rendered = "\n".join(indent + line for line in lines)
    return pre + rendered + post


def _render_simple_for_lang(template: str, lines: list[str]) -> str:
    """The language template has two consecutive for-loops we generated; handle
    them in one pass to keep _render_simple_for simple.
    """
    # First loop: resume.skills.languages.strong
    marker_open = "{% for lang in resume.skills.languages.strong %}"
    marker_close = "{% endfor %}"
    if marker_open in template:
        pre, rest = template.split(marker_open, 1)
        body, post = rest.split(marker_close, 1)
        indent = pre[len(pre.rstrip("\n")):]
        strong_block = "\n".join(indent + l for l in lines if l.endswith("(strong)"))
        template = pre + strong_block + post
    # Second loop: resume.skills.languages.familiar
    marker_open = "{% for lang in resume.skills.languages.familiar %}"
    if marker_open in template:
        pre, rest = template.split(marker_open, 1)
        body, post = rest.split(marker_close, 1)
        indent = pre[len(pre.rstrip("\n")):]
        familiar_block = "\n".join(indent + l for l in lines if l.endswith("(familiar)"))
        template = pre + familiar_block + post
    return template


def _check_required(data: dict, required: set, where: str) -> None:
    missing = required - set(data.keys())
    if missing:
        raise ContentError(f"{where} missing required keys: {sorted(missing)}")


def load_content(content_dir: str | Path | None = None) -> Content:
    """Load resume.yaml + chatbot_personality.yaml, validate, return Content.

    Also populates the module-level `_content` singleton so get_content() can
    serve it. Raises ContentError on any problem. Caller decides whether to
    fail startup (we do) — the chatbot and PDF generator both assume content
    is loaded.
    """
    base = Path(content_dir or settings.content_dir)
    resume_path = base / "resume.yaml"
    persona_path = base / "chatbot_personality.yaml"

    if not resume_path.exists():
        raise ContentError(f"resume.yaml not found at {resume_path}")
    if not persona_path.exists():
        raise ContentError(f"chatbot_personality.yaml not found at {persona_path}")

    with resume_path.open("r", encoding="utf-8") as f:
        resume = yaml.safe_load(f)
    with persona_path.open("r", encoding="utf-8") as f:
        persona = yaml.safe_load(f)

    if not isinstance(resume, dict):
        raise ContentError(f"resume.yaml must be a mapping, got {type(resume).__name__}")
    if not isinstance(persona, dict):
        raise ContentError(f"chatbot_personality.yaml must be a mapping, got {type(persona).__name__}")

    _check_required(resume, REQUIRED_RESUME_KEYS, "resume.yaml")
    _check_required(resume.get("personal", {}), REQUIRED_PERSONAL_KEYS, "resume.yaml:personal")
    _check_required(resume.get("skills", {}), REQUIRED_SKILLS_KEYS, "resume.yaml:skills")

    if "persona" not in persona:
        raise ContentError("chatbot_personality.yaml missing 'persona' key")
    if "system_prompt_template" not in persona:
        raise ContentError("chatbot_personality.yaml missing 'system_prompt_template' key")

    log.info("Content loaded: %d projects, %d skills, %d beyond-code items",
             len(resume.get("projects", [])),
             len(resume.get("skills", {}).get("languages", {}).get("strong", [])) +
             len(resume.get("skills", {}).get("languages", {}).get("familiar", [])),
             len(resume.get("beyond_code", [])))

    content = Content(
        resume=resume,
        persona=persona["persona"],
        system_prompt_template=persona["system_prompt_template"],
    )
    # Populate the module-level singleton so get_content() can serve it.
    # Reload after /webhook/deploy also routes through this function.
    global _content
    _content = content
    return content


# Module-level singleton, populated at startup. Replaced by load_content() on
# /webhook/deploy after a git pull.
_content: Content | None = None


def get_content() -> Content:
    if _content is None:
        raise RuntimeError("Content not loaded; call load_content() at startup")
    return _content


def reload_content() -> Content:
    global _content
    _content = load_content()
    return _content
