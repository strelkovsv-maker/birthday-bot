"""Daily birthday check, scheduled via APScheduler."""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import timezone as pytz_timezone
from telegram.ext import Application

from src.config import Settings
from src.llm import WishGenerator
from src.sheets import SheetClient
from src.telegram_bot import BirthdayBotHandlers

logger = logging.getLogger(__name__)


def make_daily_check(
    settings: Settings,
    sheet_client: SheetClient,
    wish_generator: WishGenerator,
    handlers: BirthdayBotHandlers,
):
    """Build the daily-check coroutine bound to its dependencies."""

    async def daily_check(app: Application) -> None:
        tz = pytz_timezone(settings.timezone)
        now = datetime.now(tz)
        logger.info("Daily check running at %s (%s)", now.isoformat(), settings.timezone)

        try:
            members = sheet_client.read_team()
        except Exception:
            logger.exception("Failed to read team sheet — aborting daily check")
            await app.bot.send_message(
                chat_id=settings.owner_chat_id,
                text="⚠️ Не получилось прочитать таблицу команды. "
                     "Проверь GOOGLE_SHEET_ID и доступ сервисного аккаунта.",
            )
            return

        today = now.date()
        matches = sheet_client.filter_today(members, today)
        logger.info(
            "Today is %s; matches: %s",
            today, [m.name for m in matches] or "none",
        )

        if not matches:
            return

        for person in matches:
            try:
                wish = wish_generator.generate(
                    name=person.name,
                    department=person.department,
                    role=person.role,
                    notes=person.notes,
                )
            except Exception:
                logger.exception(
                    "LLM generation failed for %s — skipping this person", person.name,
                )
                await app.bot.send_message(
                    chat_id=settings.owner_chat_id,
                    text=f"⚠️ Не получилось сгенерировать поздравление для "
                         f"{person.name}. Можно попробовать позже командой /test.",
                )
                continue

            try:
                await handlers.send_draft_for_approval(app, person, wish)
            except Exception:
                logger.exception(
                    "Failed to DM owner with draft for %s", person.name,
                )

    return daily_check


def setup_scheduler(
    app: Application,
    settings: Settings,
    daily_check_coro,
) -> AsyncIOScheduler:
    """Wire up the daily cron trigger. Returns the scheduler so caller can shut it down."""
    tz = pytz_timezone(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        daily_check_coro,
        trigger="cron",
        hour=settings.send_hour,
        minute=settings.send_minute,
        args=[app],
        id="daily_birthday_check",
        replace_existing=True,
        misfire_grace_time=3600,  # if container started slightly late, still fire
    )
    scheduler.start()
    logger.info(
        "Scheduler started: daily check at %02d:%02d %s",
        settings.send_hour, settings.send_minute, settings.timezone,
    )
    return scheduler
