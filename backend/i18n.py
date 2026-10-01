"""UI translations for the main page (templates/index.html).

One catalog per language in content/i18n/<code>.yaml. en.yaml is the
reference: every other catalog should have exactly its keys. A key missing
from a translation falls back to the English string (and is logged), so a
new English key never breaks the other pages; an unknown key used by the
template raises, so typos surface in development.

Catalogs are nested YAML, flattened to dotted keys:

    hero:
      bio: "I build ..."        ->  t('hero.bio')

Conventions:
  - Keys ending in `_html` are trusted markup (links, <strong>, <code>) and are
    not escaped. Everything else is autoescaped by Jinja.
  - `t('key', name=...)` fills `{name}` placeholders.
  - The `js` subtree is handed to the page scripts as window.I18N (without the
    `js.` prefix), so main.js, chat.js and feedback.js speak the page language.
  - `meta` describes the language itself: native name for the switcher,
    hreflang, og:locale, and the locale used for dates in the console.

Adding a language = adding a catalog. Its page is served at /<code>/ and
picked up by the switcher, hreflang links and the sitemap; the route is
registered at startup, so the deploy restart makes it live.

Catalogs are re-read when a file's mtime changes, so copy edits show up
without a restart.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from markupsafe import Markup

from .config import settings

log = logging.getLogger(__name__)

DEFAULT_LANG = "en"


@dataclass(frozen=True)
class Language:
    code: str
    name: str        # native name, shown in the switcher
    hreflang: str
    og_locale: str
    date_locale: str
    path: str        # "/" for the default language, "/<code>/" otherwise


class Catalog:
    def __init__(self, lang: Language, strings: dict[str, str]) -> None:
        self.lang = lang
        self.strings = strings

    def t(self, key: str, **values: Any) -> str:
        if key not in self.strings:
            raise KeyError(f"unknown translation key {key!r}")
        text = self.strings[key]
        if values:
            text = text.format(**values)
        return Markup(text) if key.endswith("_html") else text

    def js(self) -> dict[str, str]:
        return {k[3:]: v for k, v in self.strings.items() if k.startswith("js.")}


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, full + "."))
        else:
            out[full] = "" if value is None else str(value)
    return out


def _catalog_dir() -> Path:
    return Path(settings.content_dir) / "i18n"


def _load(files: list[Path]) -> dict[str, Catalog]:
    raw: dict[str, dict[str, str]] = {}
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{f}: top level must be a mapping")
        raw[f.stem] = _flatten(data)
    if DEFAULT_LANG not in raw:
        raise ValueError(f"{_catalog_dir()}: {DEFAULT_LANG}.yaml is required")

    ref = raw[DEFAULT_LANG]
    # Default language first, the rest by code: the switcher's order.
    codes = [DEFAULT_LANG] + sorted(c for c in raw if c != DEFAULT_LANG)
    out: dict[str, Catalog] = {}
    for code in codes:
        strings = raw[code]
        missing = [k for k in ref if k not in strings]
        extra = [k for k in strings if k not in ref]
        if missing:
            log.warning("i18n %s: %d key(s) missing, using English: %s", code, len(missing), ", ".join(missing[:10]))
        if extra:
            log.warning("i18n %s: %d key(s) not in en.yaml: %s", code, len(extra), ", ".join(extra[:10]))
        merged = {**ref, **{k: v for k, v in strings.items() if k in ref}}
        lang = Language(
            code=code,
            name=strings.get("meta.name", code),
            hreflang=strings.get("meta.hreflang", code),
            og_locale=strings.get("meta.og_locale", ""),
            date_locale=strings.get("meta.date_locale", code),
            path="/" if code == DEFAULT_LANG else f"/{code}/",
        )
        out[code] = Catalog(lang, merged)
    return out


_lock = threading.Lock()
_cache: tuple[tuple, dict[str, Catalog]] | None = None


def catalogs() -> dict[str, Catalog]:
    """All catalogs, default language first. Reloaded when a file changes."""
    global _cache
    files = sorted(_catalog_dir().glob("*.yaml"))
    stamp = tuple((f.name, f.stat().st_mtime_ns) for f in files)
    with _lock:
        if _cache is None or _cache[0] != stamp:
            _cache = (stamp, _load(files))
        return _cache[1]


def languages() -> list[Language]:
    return [c.lang for c in catalogs().values()]
