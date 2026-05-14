"""Helper: generate a sample birthday wish without sending anything.

Use this to sanity-check tone, Russian quality, and any prompt tweaks before
deploying. Doesn't touch Telegram or the sheet.

Usage:
    python -m tools.preview_wish

You'll be prompted for name / department / notes. Or pass them as flags:
    python -m tools.preview_wish --name "Анна Петрова" \\
                                  --department "Market Risk" \\
                                  --notes "PM, любит горный велосипед, недавно сдала FRM"
"""
from __future__ import annotations

# Must be first — installs OS trust store before httpx/ssl get imported elsewhere.
from src import _ssl_setup  # noqa: F401

import argparse
import os
import sys

from dotenv import load_dotenv

from src.llm import WishGenerator


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Preview a birthday wish locally.")
    p.add_argument("--name", help="Person's name (e.g. 'Стрелков Сергей')")
    p.add_argument("--department", default="",
                   help="Market Risk / Liquidity Risk / FI Risk / "
                        "Trading Infrastructure / Price Competence Center / "
                        "FMRM IT Core / Quantitative modeling")
    p.add_argument("--role", default="",
                   help="Job title in Russian (e.g. 'главный аналитик по управлению рисками торговой книги')")
    p.add_argument("--notes", default="",
                   help="Free-text personal notes (hobbies, recent achievements, etc.)")
    p.add_argument("--retries", type=int, default=0,
                   help="Generate this many extra variants in sequence "
                        "(simulates tapping 'Try again')")
    p.add_argument("--model", default=None,
                   help="Override ANTHROPIC_MODEL (default: claude-sonnet-4-6)")
    return p.parse_args()


def prompt_if_missing(args: argparse.Namespace) -> None:
    if not args.name:
        args.name = input("Имя: ").strip()
    if not args.department:
        args.department = input("Подразделение [пусто = пропустить]: ").strip()
    if not args.role:
        args.role = input("Должность [пусто = пропустить]: ").strip()
    if not args.notes:
        args.notes = input("Заметки [пусто = пропустить]: ").strip()


def main() -> None:
    load_dotenv()
    args = parse_args()
    prompt_if_missing(args)

    if not args.name:
        print("ERROR: --name is required (or enter it interactively).", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set in env or .env", file=sys.stderr)
        sys.exit(1)

    model = args.model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    gen = WishGenerator(api_key=api_key, model=model)

    print()
    print(f"Model: {model}")
    print(f"Person:     {args.name}")
    print(f"Department: {args.department or '—'}")
    print(f"Role:       {args.role or '—'}")
    print(f"Notes:      {args.notes or '—'}")
    print()

    prior: list[str] = []
    rounds = 1 + max(0, args.retries)
    for i in range(rounds):
        label = f"Variant {i+1}/{rounds}" + ("  (retry — different angle requested)" if i > 0 else "")
        print("=" * 70)
        print(label)
        print("=" * 70)
        text = gen.generate(
            name=args.name,
            department=args.department,
            role=args.role,
            notes=args.notes,
            prior_drafts=prior,
        )
        print(text)
        print()
        prior.append(text)


if __name__ == "__main__":
    main()
