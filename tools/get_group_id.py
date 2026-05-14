"""Helper: print the chat_id of any group your bot is in.

Usage:
  1. Add the bot to your team group (and make it admin so it can read messages)
  2. Set TELEGRAM_BOT_TOKEN in your .env
  3. Run: python -m tools.get_group_id
  4. In the group, send any message. The script prints the group's chat_id.
  5. Copy the printed value into GROUP_CHAT_ID in your .env

Note: by default Telegram bots only see messages addressed to them or commands
in groups (privacy mode is on). If sending a normal message doesn't trigger
output, try mentioning the bot or sending /id in the group instead.
"""
from __future__ import annotations

# Must be first — installs OS trust store before httpx/ssl get imported elsewhere.
from src import _ssl_setup  # noqa: F401

import asyncio
import os
import sys

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters


async def report_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return

    print()
    print("=" * 50)
    print(f"  chat.id:    {chat.id}")
    print(f"  chat.type:  {chat.type}")
    print(f"  chat.title: {chat.title or '—'}")
    print("=" * 50)
    print()

    if chat.type in ("group", "supergroup"):
        print("Copy the chat.id above into GROUP_CHAT_ID in your .env.")
    else:
        print("(That looks like a private chat — for the GROUP id you need a group chat.)")
    print("Press Ctrl+C to stop.")

    if update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"chat.id: `{chat.id}`\nchat.type: `{chat.type}`",
                parse_mode="Markdown",
            )
        except Exception:
            pass  # bot might not have permission to reply; printing is enough


async def amain() -> None:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set.", file=sys.stderr)
        sys.exit(1)

    app = Application.builder().token(token).build()
    # Listen for /id command (works even with privacy mode on) and any message
    app.add_handler(CommandHandler("id", report_chat))
    app.add_handler(MessageHandler(filters.ALL, report_chat))

    print("Bot listening.")
    print("In your group, send `/id` (or any message if bot is admin/privacy off).")
    print("(Press Ctrl+C to stop.)")
    print()

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("\nStopped.")
