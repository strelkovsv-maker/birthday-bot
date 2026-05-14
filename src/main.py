"""Entry point. Starts the bot, registers handlers, schedules the daily job."""
from __future__ import annotations

# Must be first — installs OS trust store before httpx/ssl get imported elsewhere.
from src import _ssl_setup  # noqa: F401

import asyncio
import logging
import signal

from telegram.ext import Application

from src.config import load_settings, setup_logging
from src.llm import WishGenerator
from src.scheduler import make_daily_check, setup_scheduler
from src.sheets import SheetClient
from src.state import StateStore
from src.telegram_bot import BirthdayBotHandlers, register_handlers

logger = logging.getLogger(__name__)


async def amain() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    logger.info(
        "Starting birthday-bot — timezone=%s, send_time=%02d:%02d, dry_run=%s, model=%s",
        settings.timezone, settings.send_hour, settings.send_minute,
        settings.dry_run, settings.anthropic_model,
    )

    # --- Wire up dependencies ---------------------------------------------
    state = StateStore(settings.db_path)
    wish_gen = WishGenerator(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
    )
    sheet_client = SheetClient(
        service_account_info=settings.google_service_account_info,
        sheet_id=settings.google_sheet_id,
        tab=settings.google_sheet_tab,
    )

    # --- Build the Telegram app ------------------------------------------
    app = Application.builder().token(settings.telegram_bot_token).build()
    handlers = BirthdayBotHandlers(
        settings=settings, state=state, wish_generator=wish_gen,
    )
    register_handlers(app, handlers)

    daily_check = make_daily_check(settings, sheet_client, wish_gen, handlers)

    # Stash references for command handlers
    app.bot_data["settings"] = settings
    app.bot_data["daily_check"] = daily_check

    # --- Sanity-check pending drafts on startup --------------------------
    pending = state.pending()
    if pending:
        logger.warning(
            "Found %d pending draft(s) in state on startup — "
            "they're still waiting for your decision in DM history. "
            "Use /test to regenerate today's drafts if needed.",
            len(pending),
        )

    # --- Boot the scheduler + polling ------------------------------------
    scheduler = setup_scheduler(app, settings, daily_check)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("Bot up and polling. Send /test in DM to trigger a manual check.")

    # --- Wait until interrupted -------------------------------------------
    stop = asyncio.Event()

    def _request_stop(*_args) -> None:
        logger.info("Shutdown requested")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows / some environments don't support add_signal_handler
            pass

    try:
        await stop.wait()
    finally:
        logger.info("Stopping…")
        scheduler.shutdown(wait=False)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("Goodbye.")


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
