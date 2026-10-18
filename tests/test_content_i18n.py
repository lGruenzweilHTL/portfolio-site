"""content/*.yaml validates; every catalog has every English key; every key
the templates and page scripts use exists."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from backend.config import settings
from backend.content import load_content
from backend.i18n import DEFAULT_LANG, _flatten, catalogs

ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "content" / "i18n"


def flat(code: str) -> dict[str, str]:
    return _flatten(yaml.safe_load((CATALOG_DIR / f"{code}.yaml").read_text(encoding="utf-8")))


def test_content_validates():
    content = load_content()
    assert content.chatbot_system_prompt
    assert content.services


@pytest.mark.parametrize("path", sorted(CATALOG_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_catalog_has_every_english_key(path):
    ref = flat(DEFAULT_LANG)
    other = flat(path.stem)
    missing = sorted(set(ref) - set(other))
    extra = sorted(set(other) - set(ref))
    assert not missing, f"{path.name} is missing {missing}"
    assert not extra, f"{path.name} has keys en.yaml doesn't: {extra}"


def test_template_keys_exist_in_english():
    ref = flat(DEFAULT_LANG)
    used = set()
    for tpl in (ROOT / "backend" / "templates").glob("*.html"):
        used |= set(re.findall(r"\bt\('([a-z_][\w.]*)'", tpl.read_text(encoding="utf-8")))
    assert used, "no t('...') calls found"
    assert sorted(k for k in used if k not in ref) == []


def test_script_keys_exist_in_english_js_subtree():
    js = {k[3:] for k in flat(DEFAULT_LANG) if k.startswith("js.")}
    used = set()
    for script in ("main.js", "chat.js", "feedback.js"):
        used |= set(re.findall(r"\btr\('([a-z_][\w.]*)'", (ROOT / "site" / script).read_text(encoding="utf-8")))
    assert sorted(k for k in used if k not in js) == []


def test_catalogs_load():
    loaded = catalogs()
    assert DEFAULT_LANG in loaded and "de" in loaded
    assert loaded["de"].t("js.privacy_path") == "/de/privacy"
    for code, catalog in loaded.items():
        if code != "de":
            assert catalog.t("js.privacy_path") == "/privacy"
    assert settings.content_dir
