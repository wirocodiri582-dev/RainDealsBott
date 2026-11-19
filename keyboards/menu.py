"""RainDeals customer home and profile menus."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from config import ADDITIONAL_CHANNEL_LINK, MINI_APP_URL
from services.menu_i18n import LANGUAGES, language_label, menu_text, normalize_language


def _feature_enabled(name: str) -> bool:
    from services.runtime_settings import setting_bool
    return setting_bool(f"feature_{name}", True)


def _filter_customer_rows(rows):
    def allowed(button):
        callback = str(button.callback_data or "")
        if callback.startswith("freebies"):
            return _feature_enabled("freebies")
        if callback.startswith(("referrals", "referral_")):
            return _feature_enabled("referrals")
        if callback.startswith("vendor_"):
            return _feature_enabled("vendor_offers")
        if callback == "deposit_start":
            return _feature_enabled("deposits")
        return True
    return [[button for button in row if allowed(button)] for row in rows if any(allowed(button) for button in row)]


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


def _shop_button(language: str = "en") -> InlineKeyboardButton:
    shop_url = str(MINI_APP_URL or "").strip()
    if shop_url:
        return InlineKeyboardButton(
            text=f"🛍 {menu_text(language, 'store')}", web_app=WebAppInfo(url=shop_url), style="success"
        )
    return InlineKeyboardButton(
        text=f"🛍 {menu_text(language, 'store')}", callback_data="shop_unavailable", style="success"
    )


def _customer_rows(
    featured_product: dict | None = None,
    language: str = "en",
) -> list[list[InlineKeyboardButton]]:
    language = normalize_language(language)
    if featured_product:
        product_id = int(featured_product["id"])
        featured_name = str(featured_product.get("name") or "Featured product")
        featured_icon = str(featured_product.get("icon") or "⭐")
        featured_text = f"⭐ {featured_icon} {featured_name}"[:60]
        featured_callback = f"product_{product_id}"
    else:
        featured_text = f"⭐ {menu_text(language, 'featured')}"
        featured_callback = "featured_product"

    rows = [
        [InlineKeyboardButton(text=f"🛍 {menu_text(language, 'products')}", callback_data="products_menu", style="success")],
        [InlineKeyboardButton(text=featured_text, callback_data=featured_callback, style="primary")],
        [
            InlineKeyboardButton(text=f"📦 {menu_text(language, 'orders')}", callback_data="orders_menu", style="primary"),
            InlineKeyboardButton(text=f"🎁 {menu_text(language, 'freebies')}", callback_data="freebies_menu", style="success"),
        ],
        [
            InlineKeyboardButton(text=f"👤 {menu_text(language, 'profile')}", callback_data="my_profile", style="primary"),
            InlineKeyboardButton(text=f"💰 {menu_text(language, 'deposit')}", callback_data="deposit_start", style="success"),
        ],
        [
            InlineKeyboardButton(text=f"🔑 {menu_text(language, 'api')}", callback_data="api_key_menu", style="primary"),
            _shop_button(language),
        ],
        [
            InlineKeyboardButton(
                text=f"🌐 {menu_text(language, 'language')} · {language_label(language).split(' ', 1)[-1]}",
                callback_data="language_menu", style="primary"
            ),
            InlineKeyboardButton(text=f"🎯 {menu_text(language, 'referrals')}", callback_data="referrals_menu", style="primary"),
        ],
        [InlineKeyboardButton(text=f"🤝 {menu_text(language, 'supplier')}", callback_data="vendor_apply", style="primary")],
    ]
    channel = str(ADDITIONAL_CHANNEL_LINK or "https://t.me/rainworlddd").strip()
    rows.append([InlineKeyboardButton(text=f"📢 {menu_text(language, 'channel')}", url=channel)])
    return rows


def get_main_menu(
    unread_count: int = 0,
    featured_product: dict | None = None,
    language: str = "en",
) -> InlineKeyboardMarkup:
    """Build the single customer menu in the order shown in the reference."""
    return InlineKeyboardMarkup(inline_keyboard=_filter_customer_rows(_customer_rows(featured_product, language)))


def get_admin_main_menu(
    unread_count: int = 0,
    featured_product: dict | None = None,
    language: str = "en",
) -> InlineKeyboardMarkup:
    """Admin's user-facing menu mirrors the customer grid without admin toggles."""
    return InlineKeyboardMarkup(inline_keyboard=_filter_customer_rows(_customer_rows(featured_product, language)))


def get_language_menu(current_language: str = "en") -> InlineKeyboardMarkup:
    current_language = normalize_language(current_language)
    codes = list(LANGUAGES)
    rows = []
    for index in range(0, len(codes), 2):
        row = []
        for code in codes[index:index + 2]:
            label = language_label(code)
            if code == current_language:
                label += " ✓"
            row.append(InlineKeyboardButton(
                text=label,
                callback_data=f"menu_lang_{code}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅ Main Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_miniapp_verification_menu() -> InlineKeyboardMarkup:
    """One-time Mini App identity verification actions for a customer."""
    shop_url = str(MINI_APP_URL or "").strip()
    if shop_url:
        open_shop = InlineKeyboardButton(
            text="🛍 Open RainDeals Store to verify",
            web_app=WebAppInfo(url=shop_url),
            style="success",
        )
    else:
        open_shop = InlineKeyboardButton(
            text="🛍 Store is not configured",
            callback_data="shop_unavailable",
            style="success",
        )
    return InlineKeyboardMarkup(inline_keyboard=[
        [open_shop],
        [InlineKeyboardButton(
            text="✅ I opened the Store · Verify",
            callback_data="referral_verify_check",
            style="primary",
        )],
        [InlineKeyboardButton(
            text="⬅ Continue in bot",
            callback_data="referral_verify_continue",
            style="primary",
        )],
    ])


def get_profile_menu(unread_count: int = 0) -> InlineKeyboardMarkup:
    """Profile actions shown separately from the shop home menu."""
    notice_label = "🔔 Notifications"
    if int(unread_count or 0) > 0:
        notice_label += f" · {min(int(unread_count), 99)}"
    rows = [
        [InlineKeyboardButton(text="💰 Deposit Balance", callback_data="deposit_start", style="success")],
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
    ]
    if not _feature_enabled("deposits"):
        rows = [[button for button in row if button.callback_data != "deposit_start"] for row in rows]
    if not _feature_enabled("cart"):
        rows = [[button for button in row if button.callback_data != "ct_cart"] for row in rows]
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])
