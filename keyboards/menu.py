"""RainDeals customer home and profile menus."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from config import ADDITIONAL_CHANNEL_LINK, MINI_APP_URL


def get_start_choice_menu() -> InlineKeyboardMarkup:
    """Legacy two-choice keyboard kept for any older links still using it."""
    shop_url = str(MINI_APP_URL or "").strip()
    if shop_url:
        shop = InlineKeyboardButton(
            text="🛍 Open the shop", web_app=WebAppInfo(url=shop_url), style="success"
        )
    else:
        shop = InlineKeyboardButton(
            text="🛍 Open the shop", callback_data="shop_unavailable", style="success"
        )
    rows = [[shop]]
    rows.append([InlineKeyboardButton(
        text="🤖 Continue in bot", callback_data="user_bot_menu", style="primary"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _shop_button() -> InlineKeyboardButton:
    shop_url = str(MINI_APP_URL or "").strip()
    if shop_url:
        return InlineKeyboardButton(
            text="🛍 Store", web_app=WebAppInfo(url=shop_url), style="success"
        )
    return InlineKeyboardButton(
        text="🛍 Store", callback_data="shop_unavailable", style="success"
    )


def _customer_rows(featured_product: dict | None = None) -> list[list[InlineKeyboardButton]]:
    if featured_product:
        product_id = int(featured_product["id"])
        featured_name = str(featured_product.get("name") or "Featured product")
        featured_icon = str(featured_product.get("icon") or "⭐")
        featured_text = f"⭐ {featured_icon} {featured_name}"[:60]
        featured_callback = f"product_{product_id}"
    else:
        featured_text = "⭐ Featured product"
        featured_callback = "featured_product"

    rows = [
        [InlineKeyboardButton(text="🛍 Products", callback_data="products_menu", style="success")],
        [InlineKeyboardButton(text=featured_text, callback_data=featured_callback, style="primary")],
        [InlineKeyboardButton(text="📦 My Orders", callback_data="orders_menu", style="primary")],
        [InlineKeyboardButton(text="🎁 Freebies", callback_data="freebies_menu", style="success")],
        [
            InlineKeyboardButton(text="👤 Profile", callback_data="my_profile", style="primary"),
            InlineKeyboardButton(text="💰 Deposit", callback_data="deposit_start", style="success"),
        ],
        [
            InlineKeyboardButton(text="🔑 Bot API", callback_data="api_key_menu", style="primary"),
            InlineKeyboardButton(text="🌐 Language · English", callback_data="language_menu", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🎯 Refer & Earn", callback_data="referrals_menu", style="primary"),
            InlineKeyboardButton(text="🤝 Become a Supplier", callback_data="vendor_apply", style="primary"),
        ],
    ]
    channel = str(ADDITIONAL_CHANNEL_LINK or "https://t.me/rainworlddd").strip()
    rows.append([InlineKeyboardButton(text="📢 Join our channel", url=channel)])
    rows.append([_shop_button()])
    return rows


def get_main_menu(unread_count: int = 0, featured_product: dict | None = None) -> InlineKeyboardMarkup:
    """Build the single customer menu in the order shown in the reference."""
    return InlineKeyboardMarkup(inline_keyboard=_customer_rows(featured_product))


def get_admin_main_menu(
    unread_count: int = 0,
    featured_product: dict | None = None,
) -> InlineKeyboardMarkup:
    """Same customer layout with the admin switch placed above the Store row."""
    rows = _customer_rows(featured_product)
    rows.insert(-2, [InlineKeyboardButton(
        text="👑 Admin View", callback_data="admin_panel", style="danger"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_profile_menu(unread_count: int = 0) -> InlineKeyboardMarkup:
    """Profile actions shown separately from the shop home menu."""
    notice_label = "🔔 Notifications"
    if int(unread_count or 0) > 0:
        notice_label += f" · {min(int(unread_count), 99)}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Deposit Balance", callback_data="deposit_start", style="success")],
        [InlineKeyboardButton(text="📨 Live OTP / Mail Reader", callback_data="profile_mail_reader", style="primary")],
        [
            InlineKeyboardButton(text="✅ My Orders", callback_data="orders_menu", style="primary"),
            InlineKeyboardButton(text="🆘 Support", callback_data="support_ticket", style="primary"),
        ],
        [
            InlineKeyboardButton(text="🌟 Loyalty Points", callback_data="loyalty_points", style="primary"),
            InlineKeyboardButton(text="📜 Shop Policies", callback_data="shop_policies", style="primary"),
        ],
        [InlineKeyboardButton(text="🔔 Price Drop & Stock Alert", callback_data="restock_alerts_menu", style="primary")],
        [
            InlineKeyboardButton(text="🛒 Cart", callback_data="ct_cart", style="primary"),
            InlineKeyboardButton(text=notice_label, callback_data="ct_inbox", style="primary"),
        ],
        [InlineKeyboardButton(text="⬅ Main Menu", callback_data="main_menu", style="primary")],
    ])
