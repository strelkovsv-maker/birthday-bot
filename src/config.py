"""Configuration loaded from environment variables.

All required secrets and tunables live here. Loaded once at startup; if anything
required is missing, we fail fast with a clear error rather than crashing
mid-flow on the first birthday.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env file if present (local dev). On Railway the env is already injected.
load_dotenv()

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    # Telegram
    telegram_bot_token: str
    owner_chat_id: int
    group_chat_id: int

    # Anthropic
    anthropic_api_key: str
    anthropic_model: str

    # Google Sheets
    google_sheet_id: str
    google_sheet_tab: str
    google_service_account_info: dict

    # Schedule
    timezone: str
    send_hour: int
    send_minute: int

    # Misc
    dry_run: bool
    db_path: str
    log_level: str


def _required(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Required environment variable {name} is missing or empty. "
            f"See .env.example for the full list."
        )
    return val


def _required_int(name: str) -> int:
    raw = _required(name)
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Env var {name} must be an integer, got: {raw!r}") from e


def _parse_send_time(raw: str) -> tuple[int, int]:
    try:
        hour_s, minute_s = raw.split(":")
        return int(hour_s), int(minute_s)
    except Exception as e:
        raise RuntimeError(
            f"SEND_TIME must be HH:MM (e.g. 10:00), got: {raw!r}"
        ) from e


def _parse_service_account(raw: str) -> dict:
    """Parse the service account JSON.

    Two acceptable formats:
      1. Inline JSON in the env var (recommended for Railway)
      2. A path to a JSON file on disk (useful for local dev)
    """
    raw = raw.strip()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is empty")

    # Heuristic: if it starts with `{` treat as inline JSON, else as a file path
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. "
                "Make sure you pasted the entire service account JSON, including braces."
            ) from e

    path = Path(raw).expanduser()
    if not path.exists():
        raise RuntimeError(
            f"GOOGLE_SERVICE_ACCOUNT_JSON looks like a file path but does not exist: {path}"
        )
    return json.loads(path.read_text())


def load_settings() -> Settings:
    send_hour, send_minute = _parse_send_time(os.environ.get("SEND_TIME", "10:00"))

    return Settings(
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        owner_chat_id=_required_int("OWNER_CHAT_ID"),
        group_chat_id=_required_int("GROUP_CHAT_ID"),
        anthropic_api_key=_required("ANTHROPIC_API_KEY"),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip(),
        google_sheet_id=_required("GOOGLE_SHEET_ID"),
        google_sheet_tab=os.environ.get("GOOGLE_SHEET_TAB", "Team").strip(),
        google_service_account_info=_parse_service_account(
            _required("GOOGLE_SERVICE_ACCOUNT_JSON")
        ),
        timezone=os.environ.get("TIMEZONE", "Europe/Moscow").strip(),
        send_hour=send_hour,
        send_minute=send_minute,
        dry_run=os.environ.get("DRY_RUN", "false").lower() in ("1", "true", "yes"),
        db_path=os.environ.get("DB_PATH", "./state.db").strip(),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper().strip(),
    )


def setup_logging(level: str) -> None:
    """Configure root logger. Keep noisy libraries quieter."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # Quiet down chatty third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    logging.getLogger("telegram").setLevel(logging.INFO)


# Singleton — load once at import time, fail fast if misconfigured.
# Tests/tools that don't need full config can import the loader directly.
try:
    settings: Optional[Settings] = load_settings()
    setup_logging(settings.log_level)
except Exception as e:
    settings = None
    # Don't crash module import if some tool only needs a subset; let callers
    # call load_settings() themselves and surface the error.
    logger.debug("Eager settings load failed: %s", e)
