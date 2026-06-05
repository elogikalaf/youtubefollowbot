from __future__ import annotations

from math import ceil

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("➕ Subscribe"), KeyboardButton("📺 My Subscriptions")],
            [KeyboardButton("🆕 Recent Uploads"), KeyboardButton("❌ Unsubscribe")],
            [KeyboardButton("ℹ️ Help")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def confirmation_keyboard(confirm_data: str, cancel_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=confirm_data),
                InlineKeyboardButton("❌ Cancel", callback_data=cancel_data),
            ]
        ]
    )


def pagination_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"subs_page:{page - 1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"subs_page:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("↩️ Menu", callback_data="menu:main")])
    return InlineKeyboardMarkup(buttons)


def subscription_row_keyboard(channel_id: str, label: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(f"❌ {label}", callback_data=f"unsub:{channel_id}")]
