"""Loads and validates /content (YAML) at startup.

Three files make up the content bundle:
  - resume.yaml                : structured facts (personal, projects, etc.)
  - chatbot_personality.yaml   : chatbot voice + system prompt template
  - services.yaml              : public/internal self-hosted service catalog

Validation is intentionally kept close to the data. The service catalog is
normalized into small dataclasses so templates never have to interpret raw YAML
or decide whether a link is public or home-network-only.

Reload: call load_content() again. Used by /webhook/deploy after a git pull.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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

VALID_VISIBILITIES = frozenset({"public", "internal"})
SERVICE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ContentError(ValueError):
    """Raised when /content is missing required keys or has wrong types."""


@dataclass(frozen=True)
class ServiceLink:
    """One clickable URL in a category or service."""

    label: str
    url: str
    visibility: str

    @property
    def visibility_label(self) -> str:
        return "Home network only" if self.visibility == "internal" else "Public"


@dataclass(frozen=True)
class Service:
    """A named service with one or more direct URLs."""

    id: str
    name: str
    description: str
    visibility: str
    urls: tuple[ServiceLink, ...]

    @property
    def visibility_label(self) -> str:
        return "Home network only" if self.visibility == "internal" else "Public"


@dataclass(frozen=True)
class ServiceCategory:
    """A service type/category, such as portfolio or media."""

    id: str
    name: str
    description: str
    visibility: str
    urls: tuple[ServiceLink, ...]
    services: tuple[Service, ...]

    @property
    def visibility_label(self) -> str:
        return "Home network only" if self.visibility == "internal" else "Public"


@dataclass
class Content:
    resume: dict[str, Any]
    persona: dict[str, Any]
    system_prompt_template: str
    services: tuple[ServiceCategory, ...]

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

        # Skills: languages then tools, both flat lists.
        lang_lines = [f"- {l}" for l in self.resume["skills"].get("languages", [])]
        out = _render_simple_for(out, "lang in resume.skills.languages", lang_lines)

        tool_lines = [f"- {t}" for t in self.resume["skills"].get("tools_and_infra", [])]
        out = _render_simple_for(out, "tool in resume.skills.tools_and_infra", tool_lines)

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


def _check_required(data: dict, required: set, where: str) -> None:
    missing = required - set(data.keys())
    if missing:
        raise ContentError(f"{where} missing required keys: {sorted(missing)}")


def _require_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContentError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def _required_string(mapping: dict[str, Any], key: str, where: str) -> str:
    if key not in mapping:
        raise ContentError(f"{where} missing required key: {key}")
    value = mapping[key]
    if not isinstance(value, str):
        raise ContentError(f"{where}.{key} must be a string")
    value = value.strip()
    if not value:
        raise ContentError(f"{where}.{key} must not be empty")
    return value


def _optional_string(mapping: dict[str, Any], key: str, where: str) -> str:
    if key not in mapping or mapping[key] is None:
        return ""
    value = mapping[key]
    if not isinstance(value, str):
        raise ContentError(f"{where}.{key} must be a string")
    return value.strip()


def _parse_visibility(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ContentError(f"{where} must be a string")
    visibility = value.strip().lower()
    if visibility not in VALID_VISIBILITIES:
        allowed = ", ".join(sorted(VALID_VISIBILITIES))
        raise ContentError(f"{where} must be one of: {allowed}")
    return visibility


def _parse_url(value: Any, where: str) -> str:
    """Validate a direct link without resolving or fetching its destination."""
    if not isinstance(value, str):
        raise ContentError(f"{where} must be a string")
    url = value.strip()
    if not url:
        raise ContentError(f"{where} must not be empty")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ContentError(f"{where} contains whitespace or control characters")
    if "\\" in url:
        raise ContentError(f"{where} contains a backslash")

    try:
        parsed = urlsplit(url)
    except ValueError as e:
        raise ContentError(f"{where} is not a valid URL: {e}") from e

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ContentError(f"{where} must use http:// or https://")
    if parsed.username is not None or parsed.password is not None:
        raise ContentError(f"{where} must not contain userinfo or credentials")
    try:
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as e:
        raise ContentError(f"{where} has an invalid port or host: {e}") from e
    if not hostname:
        raise ContentError(f"{where} must include a host")
    return url


def _parse_links(raw: Any, parent_visibility: str, where: str) -> tuple[ServiceLink, ...]:
    if not isinstance(raw, list):
        raise ContentError(f"{where} must be a list")

    links: list[ServiceLink] = []
    for index, item in enumerate(raw):
        item_where = f"{where}[{index}]"
        link = _require_mapping(item, item_where)
        label = _required_string(link, "label", item_where)
        url = _parse_url(link.get("url"), f"{item_where}.url")
        visibility = _parse_visibility(
            link.get("visibility", parent_visibility),
            f"{item_where}.visibility",
        )
        links.append(ServiceLink(label=label, url=url, visibility=visibility))
    return tuple(links)


def _parse_service(raw: Any, index: int, where: str) -> Service:
    service_where = f"{where}[{index}]"
    service = _require_mapping(raw, service_where)
    service_id = _required_string(service, "id", service_where)
    if not SERVICE_ID_RE.fullmatch(service_id):
        raise ContentError(
            f"{service_where}.id must be lowercase letters, numbers, or single hyphens"
        )
    name = _required_string(service, "name", service_where)
    visibility = _parse_visibility(service.get("visibility"), f"{service_where}.visibility")
    description = _optional_string(service, "description", service_where)
    urls = _parse_links(service.get("urls"), visibility, f"{service_where}.urls")
    if not urls:
        raise ContentError(f"{service_where}.urls must contain at least one link")
    return Service(
        id=service_id,
        name=name,
        description=description,
        visibility=visibility,
        urls=urls,
    )


def _parse_services(raw: Any, where: str) -> tuple[Service, ...]:
    if not isinstance(raw, list):
        raise ContentError(f"{where} must be a list")

    services: list[Service] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw):
        service = _parse_service(item, index, where)
        if service.id in seen_ids:
            raise ContentError(f"{where} contains duplicate service id: {service.id}")
        seen_ids.add(service.id)
        services.append(service)
    return tuple(services)


def _parse_categories(raw: Any) -> tuple[ServiceCategory, ...]:
    if not isinstance(raw, list):
        raise ContentError("services.yaml categories must be a list")

    categories: list[ServiceCategory] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw):
        category_where = f"services.yaml categories[{index}]"
        category = _require_mapping(item, category_where)
        category_id = _required_string(category, "id", category_where)
        if not SERVICE_ID_RE.fullmatch(category_id):
            raise ContentError(
                f"{category_where}.id must be lowercase letters, numbers, or single hyphens"
            )
        name = _required_string(category, "name", category_where)
        visibility = _parse_visibility(category.get("visibility"), f"{category_where}.visibility")
        description = _optional_string(category, "description", category_where)
        urls = _parse_links(category.get("urls", []), visibility, f"{category_where}.urls")
        services = _parse_services(category.get("services", []), f"{category_where}.services")
        if not urls and not services:
            raise ContentError(f"{category_where} must contain at least one url or service")

        if category_id in seen_ids:
            raise ContentError(f"services.yaml categories contains duplicate id: {category_id}")
        seen_ids.add(category_id)
        categories.append(
            ServiceCategory(
                id=category_id,
                name=name,
                description=description,
                visibility=visibility,
                urls=urls,
                services=services,
            )
        )
    return tuple(categories)


def load_services(content_dir: str | Path | None = None) -> tuple[ServiceCategory, ...]:
    """Load and normalize content/services.yaml."""
    base = Path(content_dir or settings.content_dir)
    services_path = base / "services.yaml"
    if not services_path.exists():
        raise ContentError(f"services.yaml not found at {services_path}")

    try:
        with services_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ContentError(f"services.yaml contains invalid YAML: {e}") from e

    root = _require_mapping(data, "services.yaml")
    if "categories" not in root:
        raise ContentError("services.yaml missing required key: categories")
    return _parse_categories(root["categories"])


def load_content(content_dir: str | Path | None = None) -> Content:
    """Load the content bundle, validate it, and populate the singleton.

    Raises ContentError on any problem. Caller decides whether to fail startup
    (we do) — the chatbot, PDF generator, and services page all assume content
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

    services = load_services(base)
    service_count = sum(1 + len(category.services) for category in services)
    link_count = sum(len(category.urls) + sum(len(service.urls) for service in category.services) for category in services)
    log.info(
        "Content loaded: %d projects, %d skills, %d beyond-code items, %d service categories, %d services, %d links",
        len(resume.get("projects", [])),
        len(resume.get("skills", {}).get("languages", []))
        + len(resume.get("skills", {}).get("tools_and_infra", [])),
        len(resume.get("beyond_code", [])),
        len(services),
        service_count,
        link_count,
    )

    content = Content(
        resume=resume,
        persona=persona["persona"],
        system_prompt_template=persona["system_prompt_template"],
        services=services,
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
