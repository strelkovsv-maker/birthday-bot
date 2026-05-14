"""Google Sheets reader.

Expected sheet schema (case-insensitive header matching):

    | Name | Telegram Handle | Birthday | Department | Role | Notes |

`Birthday` accepts several formats — see _parse_birthday().
`Department` is the bot-side category (Market Risk / Liquidity Risk / etc.).
`Role` is the person's actual job title in Russian — passed to the LLM
as a separate field so wishes can riff on the specific position.
`Notes` is free-text personal context (hobbies, recent achievements, etc.).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

# Required scopes for read-only Sheets access via service account
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


@dataclass
class TeamMember:
    name: str
    telegram_handle: str  # always normalized to start with "@" if non-empty
    birthday_month: int
    birthday_day: int
    department: str
    role: str
    notes: str

    @property
    def birthday_str(self) -> str:
        return f"{self.birthday_month:02d}-{self.birthday_day:02d}"


def _normalize_handle(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    return raw if raw.startswith("@") else f"@{raw}"


def _parse_birthday(raw: str) -> Optional[tuple[int, int]]:
    """Parse a birthday cell into (month, day). Returns None if unparseable.

    IMPORTANT — ENFORCED FORMAT: month always comes FIRST, then day.
    This avoids the Google Sheets locale trap where "05-10" can be auto-
    interpreted as October 5 (DD-MM) in some regions and as May 10 (MM-DD)
    in others. We force one convention: MM-DD always.

    Accepted formats:
      - YYYY-MM-DD   (1990-05-10)         ← recommended, unambiguous
      - YYYY/MM/DD   (1990/05/10)
      - MM-DD-YYYY   (05-10-1990)
      - MM/DD/YYYY   (05/10/1990)
      - MM-DD        (05-10)              ← year-less, month first
      - MM/DD        (05/10)
      - MM.DD        (05.10)

    Year is ignored — only month + day are used for matching.
    If the FIRST number is > 12, it's clearly a day-first format and we
    log a clear warning so the user knows to fix the cell.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    # Try formats WITH year — month always comes first or via ISO year-first
    formats_with_year = [
        "%Y-%m-%d", "%Y/%m/%d",  # ISO: year-month-day (unambiguous)
        "%m-%d-%Y", "%m/%d/%Y",  # US-style: month-day-year
    ]
    for fmt in formats_with_year:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.month, dt.day
        except ValueError:
            continue

    # Year-less month-day: STRICTLY assume MM-DD
    m = re.match(r"^(\d{1,2})[.\-/](\d{1,2})$", raw)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        if month > 12:
            logger.warning(
                "Birthday %r looks like day-first (first number > 12). "
                "This bot expects MM-DD format (month first). "
                "If you meant day=%d month=%d, please rewrite as %02d-%02d.",
                raw, month, day, day, month,
            )
            return None
        if day < 1 or day > 31:
            logger.warning("Birthday %r has invalid day=%d", raw, day)
            return None
        if month < 1:
            return None
        return month, day

    return None


def _norm_header(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


# Synonyms accepted for each column (case/whitespace-insensitive)
HEADER_SYNONYMS = {
    "name": {"name", "имя", "fullname", "фио"},
    "handle": {"telegramhandle", "handle", "telegram", "tg", "username", "ник", "никнейм"},
    "birthday": {"birthday", "birthdate", "dob", "деньрождения", "датарождения", "др"},
    "department": {"department", "dept", "подразделение", "team"},
    "role": {"role", "position", "должность", "роль", "title"},
    "notes": {"notes", "note", "comment", "comments", "заметки", "комментарий"},
}


def _resolve_columns(header_row: list[str]) -> dict[str, int]:
    """Map our canonical column names to the indexes in the actual sheet.

    Raises if a required column is missing.
    """
    normalized = [_norm_header(h) for h in header_row]
    resolved: dict[str, int] = {}
    for canonical, synonyms in HEADER_SYNONYMS.items():
        for idx, h in enumerate(normalized):
            if h in synonyms:
                resolved[canonical] = idx
                break

    missing = [k for k in ("name", "birthday") if k not in resolved]
    if missing:
        raise RuntimeError(
            f"Sheet is missing required column(s): {missing}. "
            f"Headers found: {header_row}. "
            f"See README for accepted column names."
        )
    return resolved


class SheetClient:
    def __init__(self, service_account_info: dict, sheet_id: str, tab: str) -> None:
        creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._sheet_id = sheet_id
        self._tab = tab

    def read_team(self) -> List[TeamMember]:
        """Read all rows from the configured tab; return parsed TeamMember objects.

        Rows with unparseable birthdays are skipped with a warning, not fatal —
        we don't want one bad row to silently drop everyone else.
        """
        sh = self._gc.open_by_key(self._sheet_id)
        ws = sh.worksheet(self._tab)
        rows = ws.get_all_values()
        if not rows:
            logger.warning("Sheet %s/%s is empty", self._sheet_id, self._tab)
            return []

        header, *data_rows = rows
        cols = _resolve_columns(header)

        members: list[TeamMember] = []
        for i, row in enumerate(data_rows, start=2):  # start=2: row 1 is header
            def cell(key: str) -> str:
                idx = cols.get(key)
                if idx is None or idx >= len(row):
                    return ""
                return (row[idx] or "").strip()

            name = cell("name")
            if not name:
                continue  # silently skip blank rows

            bday = _parse_birthday(cell("birthday"))
            if bday is None:
                logger.warning(
                    "Sheet row %d (%s): unparseable birthday %r — skipping",
                    i, name, cell("birthday"),
                )
                continue

            month, day = bday
            members.append(
                TeamMember(
                    name=name,
                    telegram_handle=_normalize_handle(cell("handle")),
                    birthday_month=month,
                    birthday_day=day,
                    department=cell("department"),
                    role=cell("role"),
                    notes=cell("notes"),
                )
            )

        logger.info("Loaded %d team members from sheet", len(members))
        return members

    @staticmethod
    def filter_today(members: List[TeamMember], today: date) -> List[TeamMember]:
        """Return members whose birthday matches today's month+day."""
        return [
            m for m in members
            if m.birthday_month == today.month and m.birthday_day == today.day
        ]
