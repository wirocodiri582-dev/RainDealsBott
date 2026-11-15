from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from config import MINI_APP_URL


def _store_rows():
    rows = []
    if MINI_APP_URL:
        rows.append([InlineKeyboardButton(
            text="🏬 Open the site",
            web_app=WebAppInfo(url=MINI_APP_URL),
            style="success",
        )])
    rows.append([InlineKeyboardButton(
        text="🤖 Buy via bot",
        callback_data="products_menu",
        style="success",
    )])
    return rows


def get_main_menu(unread_count=0) -> InlineKeyboardMarkup:
    from services.notification_inbox import badge
    return InlineKeyboardMarkup(
        inline_keyboard=_store_rows() + [
            [InlineKeyboardButton(text="🛒 Cart", callback_data="ct_cart", style="primary"), InlineKeyboardButton(text=badge(unread_count), callback_data="ct_inbox", style="primary")],
            [
                InlineKeyboardButton(
                    text="💳 Wallet",
                    callback_data="deposit_start",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="🎁 Freebies",
                    callback_data="freebies_menu",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="👤 Profile",
                    callback_data="my_profile",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎯 Referral Store",
                    callback_data="referrals_menu",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🆘 Support",
                    callback_data="support_ticket",
                    style="primary",
                ),
                InlineKeyboardButton(
                    text="📦 My Orders",
                    callback_data="orders_menu",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔑 Reseller API",
                    callback_data="api_key_menu",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧹 Clear Menu",
                    callback_data="clear_start_menu",
                    style="danger",
                )
            ]
        ]
    )


def get_admin_main_menu(unread_count=0) -> InlineKeyboardMarkup:
    from services.notification_inbox import badge
    return InlineKeyboardMarkup(
        inline_keyboard=_store_rows() + [
            [InlineKeyboardButton(text="🛒 Cart", callback_data="ct_cart", style="primary"), InlineKeyboardButton(text=badge(unread_count), callback_data="ct_inbox", style="primary")],
            [
                InlineKeyboardButton(
                    text="💳 Wallet",
                    callback_data="deposit_start",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="🎁 Freebies",
                    callback_data="freebies_menu",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="👤 Profile",
                    callback_data="my_profile",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎯 Referral Store",
                    callback_data="referrals_menu",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🆘 Support",
                    callback_data="support_ticket",
                    style="primary",
                ),
                InlineKeyboardButton(
                    text="📦 My Orders",
                    callback_data="orders_menu",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔑 Reseller API",
                    callback_data="api_key_menu",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🧹 Clear Menu",
                    callback_data="clear_start_menu",
                    style="danger",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 Admin",
                    callback_data="admin_panel",
                    style="danger",
                )
            ]
        ]
    )
