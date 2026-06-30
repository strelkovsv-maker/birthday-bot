"""Telegram bot — handles the approval flow.

Flow:
  1. `send_draft_for_approval` DMs the owner with the draft + 3 inline buttons
  2. User taps a button → `handle_callback` routes:
        ✅ approve → posts to group, marks resolved
        🔄 retry   → regenerates with prior drafts as anti-context, edits the DM
        ❌ skip    → marks resolved, no post

Callback data format: "<action>:<draft_id>" where action ∈ {approve, retry, skip}.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from src.config import Settings
from src.llm import WishGenerator
from src.sheets import SheetClient, TeamMember
from src.state import Draft, StateStore

logger = logging.getLogger(__name__)


# ----- Keyboard -----------------------------------------------------------

def _approval_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Опубликовать", callback_data=f"approve:{draft_id}"),
        InlineKeyboardButton("🔄 Новый вариант", callback_data=f"retry:{draft_id}"),
        InlineKeyboardButton("❌ Пропустить",   callback_data=f"skip:{draft_id}"),
    ]])


# Match "С днём/днем рождения" + optional trailing punctuation.
# Case-insensitive, ё/е tolerant, allows extra whitespace between words.
_GREETING_PATTERN = re.compile(
    r"С\s+дн[её]м\s+рождения\s*([!.…]*)",
    re.IGNORECASE,
)


def _inject_handle_into_greeting(wish: str, handle: str) -> str:
    """Inject the Telegram handle into the LLM-produced birthday greeting.

    The LLM is calibrated to end wishes with "С днём рождения!" (somewhere,
    usually at the very end). This function finds the LAST occurrence of
    that phrase in the wish and replaces it with "С днём рождения, @handle!"
    so the person gets a Telegram mention.

    Behavior:
      - With handle, greeting present → injects handle into greeting
      - With handle, no greeting → returns wish unchanged (rare; the prompt
        normally forces a greeting)
      - Without handle → returns wish unchanged (fallback per design)

    Case is normalized to "С днём рождения" in the output regardless of
    what the LLM wrote ("С Днём Рождения" → "С днём рождения, @h!").
    """
    if not handle:
        return wish

    matches = list(_GREETING_PATTERN.finditer(wish))
    if not matches:
        return wish

    last = matches[-1]
    return (
        wish[:last.start()]
        + f"С днём рождения, {handle}!"
        + wish[last.end():]
    )


def _format_owner_message(person: TeamMember, draft_text: str) -> str:
    """Format the DM the owner sees, in HTML for safe escaping.

    Shows the wish with the @handle already injected into the closing
    greeting — so the owner's preview matches exactly what will be posted.
    """
    name_html = html.escape(person.name)
    dept_html = html.escape(person.department) if person.department else ""
    final_text = _inject_handle_into_greeting(draft_text, person.telegram_handle)
    body_html = html.escape(final_text)
    header = f"🎂 Сегодня день рождения у <b>{name_html}</b>"
    if dept_html:
        header += f" ({dept_html})"
    return f"{header}\n\n<i>Черновик поздравления:</i>\n\n{body_html}"


def _format_group_post(person: TeamMember, draft_text: str) -> str:
    """Format the message posted to the group chat.

    Injects the @handle into the existing "С днём рождения!" greeting if
    a handle is available; otherwise returns the wish unchanged.
    """
    return _inject_handle_into_greeting(draft_text, person.telegram_handle)


def _draft_to_team_member(draft: Draft) -> TeamMember:
    """Reconstruct a minimal TeamMember from a stored Draft. Used by the
    callback handlers (approve/retry/skip) to format messages identically
    to the initial DM."""
    return TeamMember(
        name=draft.person_name,
        telegram_handle=draft.person_handle,
        birthday_month=0, birthday_day=0,  # not used for formatting
        department=draft.department,
        role=draft.role,
        notes=draft.notes,
    )


# ----- Sending drafts -----------------------------------------------------

class BirthdayBotHandlers:
    """Container for handler logic so we can inject dependencies."""

    def __init__(
        self,
        settings: Settings,
        state: StateStore,
        wish_generator: WishGenerator,
    ) -> None:
        self.settings = settings
        self.state = state
        self.wish_generator = wish_generator

    async def send_draft_for_approval(
        self,
        app: Application,
        person: TeamMember,
        draft_text: str,
    ) -> int:
        """Persist a new pending draft and DM the owner. Returns draft_id."""
        draft_id = self.state.create_draft(
            person_name=person.name,
            person_handle=person.telegram_handle,
            department=person.department,
            role=person.role,
            notes=person.notes,
            current_draft=draft_text,
        )
        text = _format_owner_message(person, draft_text)
        msg = await app.bot.send_message(
            chat_id=self.settings.owner_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=_approval_keyboard(draft_id),
        )
        self.state.attach_owner_message_id(draft_id, msg.message_id)
        logger.info("Draft #%d sent to owner for approval (%s)", draft_id, person.name)
        return draft_id

    # ----- Callback handler ----------------------------------------------

    async def handle_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        if not query or not query.data:
            return

        # Ignore callbacks from anyone but the owner — defensive
        if query.from_user and query.from_user.id != self.settings.owner_chat_id:
            await query.answer("Эту кнопку может нажимать только владелец бота.", show_alert=True)
            return

        try:
            action, draft_id_s = query.data.split(":", 1)
            draft_id = int(draft_id_s)
        except (ValueError, AttributeError):
            await query.answer("Bad callback data")
            return

        draft = self.state.get(draft_id)
        if not draft:
            await query.answer("Этот черновик уже не найден.", show_alert=True)
            return

        if draft.status != "pending":
            await query.answer(
                f"Черновик уже {draft.status}. Действие не применено.",
                show_alert=True,
            )
            return

        if action == "approve":
            await self._approve(query, context, draft)
        elif action == "retry":
            await self._retry(query, context, draft)
        elif action == "skip":
            await self._skip(query, context, draft)
        else:
            await query.answer(f"Unknown action: {action}")

    async def _approve(self, query, context, draft: Draft) -> None:
        await query.answer("Публикую…")

        person = _draft_to_team_member(draft)
        post_text = _format_group_post(person, draft.current_draft)

        if self.settings.dry_run:
            logger.warning("DRY_RUN: would have posted to group: %s", post_text)
            footer = "\n\n🟡 <i>DRY_RUN — в группу не отправлено</i>"
        else:
            await context.bot.send_message(
                chat_id=self.settings.group_chat_id,
                text=post_text,
            )
            footer = "\n\n✅ <i>Опубликовано в группе</i>"

        self.state.mark_resolved(draft.id, "approved")

        # Edit the DM to reflect the action and remove the buttons
        try:
            await query.edit_message_text(
                text=_format_owner_message(person, draft.current_draft) + footer,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.exception("Failed to edit owner message after approve")

    async def _retry(self, query, context, draft: Draft) -> None:
        await query.answer("Генерирую новый вариант…")

        prior = draft.prior_drafts + [draft.current_draft]
        try:
            new_text = self.wish_generator.generate(
                name=draft.person_name,
                department=draft.department,
                role=draft.role,
                notes=draft.notes,
                prior_drafts=prior,
            )
        except Exception:
            logger.exception("Retry generation failed for draft #%d", draft.id)
            await context.bot.send_message(
                chat_id=self.settings.owner_chat_id,
                text=f"⚠️ Не получилось сгенерировать новый вариант для "
                     f"{draft.person_name}. Попробуй ещё раз через минуту.",
            )
            return

        self.state.replace_draft_text(draft.id, new_text)

        # Edit the existing DM with the new draft + same buttons
        try:
            await query.edit_message_text(
                text=_format_owner_message(_draft_to_team_member(draft), new_text),
                parse_mode=ParseMode.HTML,
                reply_markup=_approval_keyboard(draft.id),
            )
        except Exception:
            logger.exception("Failed to edit owner message after retry")

    async def _skip(self, query, context, draft: Draft) -> None:
        await query.answer("Пропускаю.")
        self.state.mark_resolved(draft.id, "skipped")
        try:
            await query.edit_message_text(
                text=_format_owner_message(
                    _draft_to_team_member(draft), draft.current_draft
                ) + "\n\n❌ <i>Пропущено</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.exception("Failed to edit owner message after skip")


# ----- Owner-only utility commands ----------------------------------------

async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: manually trigger today's birthday check.

    Useful if the cron didn't fire (Railway hiccup, you set up the bot mid-day,
    etc.) or you want to test without changing the schedule.
    """
    daily_check_callable = context.application.bot_data.get("daily_check")
    settings: Settings = context.application.bot_data["settings"]

    if not update.effective_user or update.effective_user.id != settings.owner_chat_id:
        return  # silently ignore non-owner

    if not daily_check_callable:
        await update.message.reply_text("daily_check not configured — bug.")
        return

    await update.message.reply_text("Запускаю проверку дней рождений…")
    await daily_check_callable(context.application)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with the current chat's ID. Useful for setup."""
    if not update.effective_chat:
        return
    chat = update.effective_chat
    await update.effective_message.reply_text(
        f"chat.id: <code>{chat.id}</code>\n"
        f"chat.type: <code>{chat.type}</code>\n"
        f"chat.title: <code>{chat.title or '—'}</code>",
        parse_mode=ParseMode.HTML,
    )


def register_handlers(app: Application, handlers: BirthdayBotHandlers) -> None:
    app.add_handler(CallbackQueryHandler(handlers.handle_callback))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("id", cmd_id))
