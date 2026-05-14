"""Helper: generate birthday wishes for specific people from the Google Sheet.

This is the end-to-end test that exercises the real sheet-read path
(service account → Google Sheets API → SheetClient → parser → TeamMember →
WishGenerator → LLM). Useful for verifying the deployed pipeline without
having to set someone's birthday to today.

Usage:
    # Look up by name (case-insensitive substring match)
    python -m tools.preview_from_sheet "Стрелков Сергей" "Мигачев Роман"

    # See variants (simulates tapping "🔄 Новый вариант" in production)
    python -m tools.preview_from_sheet --retries 1 "Стрелков Сергей"

    # Show today's matches (same logic the daily check uses)
    python -m tools.preview_from_sheet --today
"""
from __future__ import annotations

# Must be first — installs OS trust store before httpx/ssl get imported elsewhere.
from src import _ssl_setup  # noqa: F401

import argparse
import sys
from datetime import datetime

from pytz import timezone as pytz_timezone

from src.config import load_settings
from src.llm import WishGenerator
from src.sheets import SheetClient


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Preview wishes for specific people read straight from the Google Sheet."
    )
    p.add_argument(
        "names",
        nargs="*",
        help="Name(s) to look up in the sheet (case-insensitive substring match). "
             "Example: 'Стрелков' or 'Мигачев Роман'.",
    )
    p.add_argument(
        "--today",
        action="store_true",
        help="Instead of named lookup, show whoever has a birthday today "
             "(uses the same filter as the daily 10:00 job).",
    )
    p.add_argument(
        "--retries", type=int, default=0,
        help="Generate this many extra variants per person (simulates retry).",
    )
    return p.parse_args()


def _print_person_header(person) -> None:
    print("=" * 72)
    print(f"  Name:       {person.name}")
    print(f"  Birthday:   {person.birthday_str}")
    print(f"  Department: {person.department or '—'}")
    print(f"  Role:       {person.role or '—'}")
    print(f"  Notes:      {person.notes or '—'}")
    print(f"  Handle:     {person.telegram_handle or '—'}")
    print("=" * 72)


def main() -> None:
    args = parse_args()

    if not args.today and not args.names:
        print("ERROR: pass at least one name, or use --today.", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    settings = load_settings()

    print(f"Reading sheet {settings.google_sheet_id} (tab: {settings.google_sheet_tab})…")
    sc = SheetClient(
        service_account_info=settings.google_service_account_info,
        sheet_id=settings.google_sheet_id,
        tab=settings.google_sheet_tab,
    )
    members = sc.read_team()
    print(f"Loaded {len(members)} team members.\n")

    # Select targets
    if args.today:
        today = datetime.now(pytz_timezone(settings.timezone)).date()
        targets = SheetClient.filter_today(members, today)
        print(f"Today is {today} ({settings.timezone}). "
              f"{len(targets)} birthday(s): {[m.name for m in targets] or 'none'}\n")
    else:
        targets = []
        for query in args.names:
            q = query.strip().lower()
            matches = [m for m in members if q in m.name.lower()]
            if not matches:
                print(f"  ⚠  No match for {query!r} in sheet")
                continue
            if len(matches) > 1:
                print(f"  ℹ  {len(matches)} matches for {query!r}: "
                      f"{[m.name for m in matches]} — using the first")
            targets.append(matches[0])
        print()

    if not targets:
        print("Nothing to generate. Exiting.")
        return

    gen = WishGenerator(api_key=settings.anthropic_api_key, model=settings.anthropic_model)

    for person in targets:
        _print_person_header(person)
        prior: list[str] = []
        rounds = 1 + max(0, args.retries)
        for i in range(rounds):
            label = f"Variant {i+1}/{rounds}" + ("  (retry — anti-context applied)" if i > 0 else "")
            print(f"\n[{label}]")
            text = gen.generate(
                name=person.name,
                department=person.department,
                role=person.role,
                notes=person.notes,
                prior_drafts=prior,
            )
            print(text)
            prior.append(text)
        print()


if __name__ == "__main__":
    main()
