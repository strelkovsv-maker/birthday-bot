"""Helper: print your personal Telegram chat_id.

Usage:
  1. Set TELEGRAM_BOT_TOKEN in your .env (or as an env var)
  2. Run: python -m tools.get_my_chat_id
  3. Open Telegram, find your bot, send any message (e.g. /start)
  4. The script prints your chat_id, then exits

Put the printed value into OWNER_CHAT_ID in your .env.
"""
from __future__ import annotations

# Must be first — installs OS trust store before httpx/ssl get imported elsewhere.
from src import _ssl_setup  # noqa: F401

import asyncio
import os
import sys

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_chat:
        return

    user = update.effective_user
    chat = update.effective_chat

    print()
    print("=" * 50)
    print(f"  Your chat_id:  {chat.id}")
    print(f"  user.id:       {user.id}")
    print(f"  username:      @{user.username}" if user.username else "  username:      (not set)")
    print(f"  full name:     {user.full_name}")
    print(f"  chat type:     {chat.type}")
    print("=" * 50)
    print()
    print("Copy the chat_id above into OWNER_CHAT_ID in your .env file.")
    print("Press Ctrl+C to stop the script.")

    if update.effective_message:
        await update.effective_message.reply_text(
            f"Got it! Your chat_id is `{chat.id}`. "
            f"Put this in OWNER_CHAT_ID and stop the script (Ctrl+C).",
            parse_mode="Markdown",
        )


async def amain() -> None:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set.", file=sys.stderr)
        print("Set it in .env or as an environment variable, then re-run.", file=sys.stderr)
        sys.exit(1)

    app = Application.builder().token(token).build()
    # Match any message, but only in private chats — to avoid grabbing
    # chat_ids of groups you might also be in.
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, echo))

    print("Bot listening. Send any message to your bot in Telegram.")
    print("(Press Ctrl+C to stop.)")
    print()

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Event().wait()  # run until Ctrl+C
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("\nStopped.")
