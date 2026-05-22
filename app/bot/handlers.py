from __future__ import annotations

import logging
from math import ceil

from telegram import InlineKeyboardButton, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.bot.keyboards import confirmation_keyboard, main_menu_keyboard, pagination_keyboard
from app.services.subscriptions import (
    channel_subscription_count,
    count_user_subscriptions,
    create_subscription,
    delete_subscription,
    get_channel_name,
    list_user_subscriptions,
    save_channel,
    upsert_user,
)
from app.services.websub import ensure_global_websub_subscription, maybe_unsubscribe_global_websub
from app.services.youtube import extract_channel_info
from app.utils.youtube import looks_like_youtube_url

logger = logging.getLogger(__name__)
PAGE_SIZE = 6


def _allowed(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    allowed = context.application.bot_data["allowed_user_ids"]
    return user_id is not None and user_id in allowed


async def _deny(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("You are not allowed to use this bot.")


async def _ensure_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not _allowed(context, user.id if user else None):
        await _deny(update)
        return False
    return True


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("subscriptions", subscriptions_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("health", health_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.ALL, unauthorized_fallback), group=1)


async def unauthorized_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and not _allowed(context, update.effective_user.id):
        await _deny(update)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed(update, context):
        return
    text = (
        "Welcome.\n\n"
        "Send a YouTube link to subscribe to channel uploads. "
        "You will receive a Telegram notification whenever a new video appears."
    )
    await update.effective_message.reply_text(text, reply_markup=main_menu_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed(update, context):
        return
    text = (
        "Send a YouTube link, channel link, shorts link, @handle, or custom channel URL.\n\n"
        "Supported examples:\n"
        "https://www.youtube.com/watch?v=VIDEO_ID\n"
        "https://youtu.be/VIDEO_ID\n"
        "https://www.youtube.com/shorts/VIDEO_ID\n"
        "https://www.youtube.com/@handle\n\n"
        "Upload notifications will include the channel name, video title, and a clickable YouTube link."
    )
    await update.effective_message.reply_text(text, reply_markup=main_menu_keyboard())


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed(update, context):
        return
    await update.effective_message.reply_text("OK")


async def subscriptions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed(update, context):
        return
    await _show_subscriptions(update, context, page=0)


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed(update, context):
        return
    await _show_subscriptions(update, context, page=0, prompt_unsubscribe=True)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed(update, context):
        return
    text = (update.effective_message.text or "").strip()
    if text == "➕ Subscribe":
        context.user_data["awaiting_subscription_url"] = True
        await update.effective_message.reply_text(
            "Send the YouTube link you want to subscribe to.",
            reply_markup=main_menu_keyboard(),
        )
        return
    if text == "📺 My Subscriptions":
        await _show_subscriptions(update, context, page=0)
        return
    if text == "❌ Unsubscribe":
        await _show_subscriptions(update, context, page=0, prompt_unsubscribe=True)
        return
    if text == "ℹ️ Help":
        await help_command(update, context)
        return
    if context.user_data.get("awaiting_subscription_url") and looks_like_youtube_url(text):
        context.user_data.pop("awaiting_subscription_url", None)
        await _begin_subscription_flow(update, context, text)
        return
    if looks_like_youtube_url(text):
        await _begin_subscription_flow(update, context, text)
        return
    if context.user_data.get("awaiting_subscription_url"):
        await update.effective_message.reply_text("That does not look like a YouTube link.")


async def _begin_subscription_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    message = await update.effective_message.reply_text("Checking the link...")
    try:
        http_client = context.application.bot_data["http_client"]
        info = await extract_channel_info(url, http_client)
    except Exception:
        logger.exception("Failed to extract YouTube channel from %s", url)
        await message.edit_text(
            "I could not read that YouTube link.\n\n"
            "Please try a direct video link, channel link, or @handle link. "
            "If it keeps failing, YouTube may be blocking metadata access from this server."
        )
        return
    context.user_data["pending_subscription"] = {
        "channel_id": info.channel_id,
        "channel_name": info.channel_name,
        "source_url": info.source_url,
    }
    await message.edit_text(
        f"Subscribe to:\n{info.channel_name}",
        reply_markup=confirmation_keyboard("sub_confirm", "sub_cancel"),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if not await _ensure_allowed(update, context):
        await query.answer()
        return
    await query.answer()
    data = query.data or ""
    if data == "menu:main":
        await query.message.reply_text("Main menu", reply_markup=main_menu_keyboard())
        return
    if data == "sub_cancel":
        context.user_data.pop("pending_subscription", None)
        await query.message.edit_text("Subscription cancelled.", reply_markup=None)
        return
    if data == "sub_confirm":
        await _confirm_subscription(update, context)
        return
    if data.startswith("subs_page:"):
        page = int(data.split(":", 1)[1])
        await _show_subscriptions(update, context, page=page)
        return
    if data.startswith("unsub:"):
        channel_id = data.split(":", 1)[1]
        await _show_unsubscribe_confirm(update, context, channel_id)
        return
    if data.startswith("unsub_confirm:"):
        channel_id = data.split(":", 1)[1]
        await _perform_unsubscribe(update, context, channel_id)
        return
    if data.startswith("unsub_cancel:"):
        await query.message.edit_text("Unsubscribe cancelled.", reply_markup=None)


async def _confirm_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    pending = context.user_data.pop("pending_subscription", None)
    if not pending:
        await query.message.edit_text("That subscription request expired.", reply_markup=None)
        return
    user = update.effective_user
    session_factory = context.application.bot_data["session_factory"]
    http_client = context.application.bot_data["http_client"]
    settings = context.application.bot_data["settings"]
    async with session_factory() as session:
        await upsert_user(
            session,
            telegram_user_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        await save_channel(
            session,
            channel_id=pending["channel_id"],
            channel_name=pending["channel_name"],
            source_url=pending["source_url"],
        )
        created = await create_subscription(
            session,
            telegram_user_id=user.id,
            channel_id=pending["channel_id"],
        )
        await session.commit()

    if created:
        count = 0
        async with session_factory() as session:
            count = await count_user_subscriptions(session, telegram_user_id=user.id)
        async with session_factory() as session:
            channel_count = await channel_subscription_count(session, channel_id=pending["channel_id"])
        if channel_count == 1:
            await ensure_global_websub_subscription(
                session_factory,
                http_client,
                callback_url=settings.webhook_callback_url,
                secret=settings.webhook_secret,
                channel_id=pending["channel_id"],
                channel_name=pending["channel_name"],
                source_url=pending["source_url"],
            )
        await query.message.edit_text(
            f"Subscribed to {pending['channel_name']}.\n\nYou now follow {count} channel(s).",
            reply_markup=None,
        )
    else:
        await query.message.edit_text(
            f"You are already subscribed to {pending['channel_name']}.",
            reply_markup=None,
        )


async def _show_subscriptions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    page: int,
    prompt_unsubscribe: bool = False,
) -> None:
    session_factory = context.application.bot_data["session_factory"]
    user = update.effective_user
    async with session_factory() as session:
        total = await count_user_subscriptions(session, telegram_user_id=user.id)
        total_pages = max(1, ceil(total / PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        items = await list_user_subscriptions(
            session,
            telegram_user_id=user.id,
            offset=page * PAGE_SIZE,
            limit=PAGE_SIZE,
        )
    if not items:
        text = "You do not have any subscriptions yet."
        if update.callback_query:
            await update.callback_query.message.edit_text(text, reply_markup=None)
        else:
            await update.effective_message.reply_text(text, reply_markup=main_menu_keyboard())
        return
    lines = ["Your subscriptions:"]
    lines.extend(f"• {name}" for _, name in items)
    buttons = [
        [InlineKeyboardButton(f"❌ {channel_name}", callback_data=f"unsub:{channel_id}")]
        for channel_id, channel_name in items
    ]
    markup = pagination_keyboard(page, total_pages)
    markup.inline_keyboard = buttons + markup.inline_keyboard
    if update.callback_query:
        await update.callback_query.message.edit_text("\n".join(lines), reply_markup=markup)
    else:
        await update.effective_message.reply_text("\n".join(lines), reply_markup=markup)


async def _show_unsubscribe_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id: str) -> None:
    session_factory = context.application.bot_data["session_factory"]
    async with session_factory() as session:
        channel_name = await get_channel_name(session, channel_id=channel_id)
    if not channel_name:
        await update.callback_query.message.edit_text("That subscription no longer exists.", reply_markup=None)
        return
    await update.callback_query.message.edit_text(
        f"Unsubscribe from:\n{channel_name}",
        reply_markup=confirmation_keyboard(f"unsub_confirm:{channel_id}", f"unsub_cancel:{channel_id}"),
    )


async def _perform_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id: str) -> None:
    query = update.callback_query
    user = update.effective_user
    session_factory = context.application.bot_data["session_factory"]
    http_client = context.application.bot_data["http_client"]
    settings = context.application.bot_data["settings"]
    async with session_factory() as session:
        channel_name = await get_channel_name(session, channel_id=channel_id)
        deleted = await delete_subscription(session, telegram_user_id=user.id, channel_id=channel_id)
        await session.commit()
    if deleted:
        await maybe_unsubscribe_global_websub(
            session_factory,
            http_client,
            callback_url=settings.webhook_callback_url,
            secret=settings.webhook_secret,
            channel_id=channel_id,
        )
        await query.message.edit_text(
            f"Unsubscribed from {channel_name or 'the channel'}.",
            reply_markup=None,
        )
    else:
        await query.message.edit_text("That subscription was not found.", reply_markup=None)
