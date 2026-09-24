"""Application configuration loaded from environment / .env.

All settings flow through this module so the rest of the codebase doesn't
have to read os.environ directly. Tests can override with monkeypatch on
the module attributes.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Workspace root = parent of the backend/ directory.
BACKEND_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(WORKSPACE_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenRouter ---
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemma-4-26b-a4b-it:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- Turnstile ---
    turnstile_sitekey: str = "2x00000000000000000000AA"  # Always fail by default
    turnstile_secret: str = "2x0000000000000000000000000000000AA"
    turnstile_dev_bypass: bool = False  # NEVER true in production

    # --- Deploy webhook ---
    deploy_webhook_secret: str = ""
    # systemd unit name to restart on deploy. Empty string disables restart.
    deploy_systemd_unit: str = "backend"
    # Where the deploy handler writes the "please restart" trigger. The
    # matching systemd .path unit (deploy/backend-restart.path) watches for
    # this file appearing, and deploy/backend-restart.conf (tmpfiles.d)
    # creates the parent directory group-writable on every boot — the app
    # process (typically www-data) must be able to create the trigger, and
    # /run itself is root-owned 0755 so it cannot.
    deploy_restart_flag: str = "/run/backend-restart/trigger"

    # --- ntfy notifications ---
    # Set NTFY_ENABLED=1 to send. Disabled by default so dev never spams you.
    ntfy_enabled: bool = False
    ntfy_base_url: str = ""        # e.g. https://ntfy.yourdomain.com (no trailing /)
    ntfy_topic: str = ""           # single topic, e.g. "lukas-portfolio"
    ntfy_auth_token: str = ""      # optional Bearer token

    # --- Admin ---
    admin_dev_bypass: bool = False  # NEVER true in production
    admin_dev_user: str = "lukas@local"

    # --- Database ---
    database_path: str = str(WORKSPACE_ROOT / "resume.db")

    # --- Paths ---
    site_dir: str = str(WORKSPACE_ROOT / "site")
    content_dir: str = str(WORKSPACE_ROOT / "content")
    templates_dir: str = str(BACKEND_DIR / "templates")
    static_generated_dir: str = str(WORKSPACE_ROOT / "static" / "generated")
    files_dir: str = str(WORKSPACE_ROOT / "site" / "files")
    secure_files_dir: str = str(WORKSPACE_ROOT / "site" / "files" / "secure")

    # --- Public-facing URLs (for generating absolute links in admin responses) ---
    # Used to construct full share URLs for secure links, OG tags, etc.
    # Should be the public origin (no trailing slash), e.g. https://lukasgruenzweil.com
    base_url: str = "http://localhost:8000"

    # --- App ---
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "info"

    # --- Rate limits (slowapi format) ---
    chat_rate_limit: str = "10/minute"
    feedback_rate_limit: str = "5/minute"
    track_rate_limit: str = "120/minute"
    deploy_rate_limit: str = "10/minute"

    # --- Chat safety ---
    chat_max_tokens: int = 600
    chat_max_history_messages: int = 20
    chat_request_timeout_seconds: float = 30.0


settings = Settings()
