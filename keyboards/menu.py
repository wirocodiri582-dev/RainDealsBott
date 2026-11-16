"""Compact Telegram shop menus and the two-choice /start landing screen."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from config import CHANNEL_LINK, MINI_APP_URL


def get_start_choice_menu() -> InlineKeyboardMarkup:
    """Keep first-time/repeated /start focused on the two shop entry points."""
    shop_url = str(MINI_APP_URL or "").strip()
    rows = []
    if shop_url:
        rows.append([InlineKeyboardButton(
            text="🛍 Open the shop",
            web_app=WebAppInfo(url=shop_url),
            style="success",
        )])
    else:
        rows.append([InlineKeyboardButton(
            text="🛍 Open the shop",
            callback_data="shop_unavailable",
            style="success",
        )])
    rows.append([InlineKeyboardButton(
        text="🤖 Continue in bot",
        callback_data="user_bot_menu",
        style="primary",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _shop_button():
    shop_url = str(MINI_APP_URL or "").strip()
    if shop_url:
        return InlineKeyboardButton(
            text="🛍 Open shop",
            web_app=WebAppInfo(url=shop_url),
            style="success",
        )
    return InlineKeyboardButton(
        text="🛍 Open shop",
        callback_data="shop_unavailable",
        style="success",
    )


def _customer_rows(unread_count=0):
    from services.notification_inbox import badge

    rows = [
        [_shop_button()],
        [InlineKeyboardButton(text="🛍 Products", callback_data="products_menu", style="success")],
        [
            InlineKeyboardButton(text="🎁 Freebies", callback_data="freebies_menu", style="success"),
            InlineKeyboardButton(text="📦 My Orders", callback_data="orders_menu", style="primary"),
        ],
        [
            InlineKeyboardButton(text="💳 Deposit / Wallet", callback_data="deposit_start", style="success"),
            InlineKeyboardButton(text="👤 Profile", callback_data="my_profile", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🛒 Cart", callback_data="ct_cart", style="primary"),
            InlineKeyboardButton(text=badge(unread_count), callback_data="ct_inbox", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🎯 Refer & Earn", callback_data="referrals_menu", style="primary"),
            InlineKeyboardButton(text="🔑 Bot API", callback_data="api_key_menu", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🌐 Language · English", callback_data="language_menu", style="primary"),
            InlineKeyboardButton(text="🆘 Support", callback_data="support_ticket", style="primary"),
        ],
        [InlineKeyboardButton(text="🤝 Become a Provider", callback_data="vendor_apply", style="primary")],
    ]
    channel = str(CHANNEL_LINK or "").strip()
    if channel.startswith("https://t.me/"):
        rows.append([InlineKeyboardButton(text="📢 Join our channel", url=channel)])
    rows.append([InlineKeyboardButton(text="🧹 Clear menu", callback_data="clear_start_menu", style="danger")])
    return rows


def get_main_menu(unread_count=0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_customer_rows(unread_count))


def get_admin_main_menu(unread_count=0) -> InlineKeyboardMarkup:
    rows = _customer_rows(unread_count)
    rows.append([InlineKeyboardButton(
        text="👑 Admin View",
        callback_data="admin_panel",
        style="danger",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)
