# handlers/start.py — FULLY FIXED + MAX SPEED + EXACT WALLET BALANCE

import asyncio
import logging
import time
import uuid
from types import SimpleNamespace
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from html import escape as html_escape

from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import or_

from database import SessionLocal, transaction, retry_on_write_conflict
from models.user import User
from models.deposit import Deposit
from models.order import Order
from services.session_resume import save_resume
from config import (
    ADMIN_IDS, TOS_LINK,
    LOYALTY_POINTS_PER_USD,
)

from keyboards.menu import get_main_menu, get_admin_main_menu, get_profile_menu
from services.featured_product import featured_product as load_featured_product
from utils.ui import show, answer_card, update_card, get_card_location

# Membership check
from middleware.membership import check_user_membership, get_join_keyboard

logger = logging.getLogger(__name__)

router = Router()
_bot_username: str | None = None
_bot_username_lock = asyncio.Lock()


async def _get_bot_username(bot) -> str:
    global _bot_username
    if not _bot_username:
        async with _bot_username_lock:
            if not _bot_username:
                bot_info = await bot.get_me()
                _bot_username = bot_info.username or ""
    return _bot_username


# ╔══════════════════════════════════════════════════════════════╗
# ║  HTML-SAFE HELPERS                                         ║
# ╚══════════════════════════════════════════════════════════════╝

def safe(text: str) -> str:
    return html_escape(str(text), quote=False)


def link(text: str, url: str) -> str:
    return f"<a href='{url}'>{safe(text)}</a>"


# ╔══════════════════════════════════════════════════════════════╗
# ║  PRE-COMPUTED STATIC STRINGS                                ║
# ╚══════════════════════════════════════════════════════════════╝

_DOUBLE_LINE = "════════════════════════════"
_SINGLE_LINE = "────────────────────────────"
_DASH_LINE = "━━━━━━━━━━━━━━━━━━━━━━"

_BOX_WELCOME = (
    "╔════════════════════════════╗\n"
    "║        🎉 WELCOME          ║\n"
    "╚════════════════════════════╝"
)

_NEW_USER_GUIDE = (
    f"{_DOUBLE_LINE}\n\n"
    f"🎯 <b>Your Account is Ready!</b>\n\n"
    f"<b>📋 Quick Start Guide:</b>\n\n"
    f"💰 <b>1. Deposit Funds</b>\n"
    f"   └ Add balance via crypto or UPI\n\n"
    f"🛍 <b>2. Browse Products</b>\n"
    f"   └ Explore our catalog\n\n"
    f"🛒 <b>3. Make a Purchase</b>\n"
    f"   └ Instant delivery for auto products\n\n"
    f"🤝 <b>4. Refer Friends</b>\n"
    f"   └ Earn commission on their purchases\n"
)

_NEW_REF_BONUS = (
    f"\n{_DOUBLE_LINE}\n\n"
    f"🎁 <b>You joined via a referral link!</b>\n"
    f"Your referrer will earn commission on your first\n"
    f"purchases. You too can share your own referral\n"
    f"link to earn rewards! 🤝"
)


# ╔══════════════════════════════════════════════════════════════╗
# ║  FAST HELPERS                                              ║
# ╚══════════════════════════════════════════════════════════════╝

def _time_greeting() -> tuple[str, str]:
    h = datetime.now().hour
    if h < 12:
        return ("🌅", "Good Morning")
    if h < 17:
        return ("☀️", "Good Afternoon")
    if h < 21:
        return ("🌆", "Good Evening")
    return ("🌙", "Good Night")


def _status(balance: float) -> str:
    if balance <= 0:
        return "Empty"
    if balance < 10:
        return "Low"
    if balance < 100:
        return "Active"
    return "Premium"


def _get_member_since(user) -> str:
    created = (
            getattr(user, 'created_at', None) or
            getattr(user, 'date_joined', None) or
            getattr(user, 'created', None) or
            getattr(user, 'joined_at', None) or
            getattr(user, 'registered_at', None)
    )
    if created is not None and hasattr(created, 'strftime'):
        return created.strftime("%d %b %Y")
    return "N/A"


def generate_ref_code() -> str:
    return uuid.uuid4().hex[:8].upper()


def _format_balance(raw_balance) -> str:
    """Display wallet values with at most three decimal places."""
    try:
        dec_val = Decimal(str(raw_balance or 0))
        rounded = dec_val.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        return format(rounded, "f").rstrip("0").rstrip(".") or "0"
    except Exception:
        return "0"


# ╔══════════════════════════════════════════════════════════════╗
# ║  AUDIT-STYLE PROFILE BUILDER                               ║
# ╚══════════════════════════════════════════════════════════════╝

def _build_audit_profile(user, telegram_id: int) -> str:
    """Build the branded marketplace profile card."""
    balance_raw = getattr(user, 'balance_display', getattr(user, 'balance', 0))
    balance_str = _format_balance(balance_raw)
    total_orders = int(getattr(user, 'total_orders', 0) or 0)
    rewards_str = _format_balance(getattr(user, 'referral_earnings_display', 0) or 0)
    full_name = str(getattr(user, 'full_name', '') or 'there')
    first_name = safe((full_name.strip().split() or ['there'])[0])
    username = str(getattr(user, 'username', '') or '').strip().lstrip('@')
    points = max(
        0,
        int(Decimal(str(getattr(user, 'total_spent', 0) or 0)) * Decimal(str(LOYALTY_POINTS_PER_USD))),
    )
    username_line = f"\n🔗 @{safe(username)}" if username else ""

    return (
        "👤 <b>Account Overview</b>\n\n"
        f"🌟 <b>{first_name}</b>{username_line}\n"
        f"🆔 <code>{int(telegram_id)}</code>\n\n"
        f"💳 Available Balance: <b>${balance_str}</b>\n"
        f"📦 Orders Placed: <b>{total_orders}</b>\n"
        f"💸 Total Spent: <b>${_format_balance(getattr(user, 'total_spent', 0))}</b>\n"
        f"💰 Total Deposited: <b>${_format_balance(getattr(user, 'total_deposited', 0))}</b>\n"
        f"🌟 Loyalty Points: <b>{points:,}</b>\n"
        f"🎁 Referral Earnings: <b>${rewards_str}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 Account Status: <b>Active</b>"
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║  MODERN /start BUILDER — TERMINAL STYLE                     ║
# ╚══════════════════════════════════════════════════════════════╝

def _build_start_welcome(
    user, full_name: str, telegram_id: int, username: str | None,
    bot_username: str,
) -> str:
    fallback_name = (str(full_name or "").strip().split() or ["there"])[0]
    greeting_name = str(username or "").strip().lstrip("@") or fallback_name
    first_name = safe(greeting_name)
    return (
        f"👋 <b>Hey Dear, {first_name}</b> 🌸\n\n"
        "<b>Welcome to Raindeals</b> 🛍\n\n"
        "Get instant access to premium AI tools with fully automatic delivery. ⚡\n\n"
        "🔘 <b>Please choose an option below to continue.</b> ⤵️"
    )


_START_LOADING_TEXT = (
    "🌧 <b>RainDeals</b>\n\n"
    "⏳ <b>Opening your store…</b>\n"
    "Checking access and preparing your account."
)


async def _delete_start_cards_quietly(bot, chat_id: int, message_ids, keep_id: int) -> None:
    """Remove stale start/invoice cards without delaying the visible menu."""
    ids = set()
    for item in message_ids:
        if not item or len(item) != 2:
            continue
        item_chat_id, message_id = item
        if (
            item_chat_id is not None
            and int(item_chat_id) == int(chat_id)
            and message_id
            and str(message_id).isdigit()
            and int(message_id) != int(keep_id)
        ):
            ids.add(int(message_id))
    if ids:
        await asyncio.gather(
            *(bot.delete_message(chat_id=chat_id, message_id=message_id) for message_id in ids),
            return_exceptions=True,
        )


async def _cleanup_admin_start_cards(bot, admin_id: int, chat_id: int, keep_id: int) -> None:
    """Look up legacy admin alerts off the /start response path."""
    try:
        from services.notification_outbox import private_admin_alert_cards
        cards = await asyncio.to_thread(private_admin_alert_cards, admin_id, bot.id)
        await _delete_start_cards_quietly(bot, chat_id, cards, keep_id)
    except Exception:
        logger.debug("Could not clean old admin alerts during /start", exc_info=True)


def _load_main_menu_data(
    telegram_id: int,
    full_name: str,
    username: str | None,
    bot_username: str,
) -> dict | None:
    """Load home-menu data without blocking aiogram's event loop."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return None
        now = datetime.utcnow()
        deposit = (
            db.query(Deposit)
            .filter(
                Deposit.telegram_id == telegram_id,
                Deposit.status.in_(["waiting_hash", "pending"]),
                or_(Deposit.tx_hash.isnot(None), Deposit.invoice_expires_at > now),
            )
            .order_by(Deposit.tx_hash.is_(None), Deposit.id.desc())
            .first()
        )
        deposit_action = None
        if deposit:
            deposit_action = {
                "kind": "status" if deposit.tx_hash is not None else "resume",
                "id": int(deposit.id),
                "code": deposit.invoice_code or deposit.order_id,
            }
        return {
            "text": _build_start_welcome(user, full_name, telegram_id, username, bot_username),
            "chat_id": user.home_menu_chat_id,
            "message_id": user.home_menu_message_id,
            "unread_notifications": 0,
            "deposit_action": deposit_action,
            "featured_product": load_featured_product(db),
        }
    finally:
        db.close()


def _save_home_menu_location(telegram_id: int, chat_id: int, message_id: int) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if user:
            user.home_menu_chat_id = int(chat_id)
            user.home_menu_message_id = int(message_id)
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _load_start_deposit_action(telegram_id: int) -> dict | None:
    """Return one resumable invoice/status shortcut for the /start card."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        deposit = (
            db.query(Deposit)
            .filter(
                Deposit.telegram_id == telegram_id,
                Deposit.status.in_(["waiting_hash", "pending"]),
                or_(
                    Deposit.tx_hash.isnot(None),
                    Deposit.invoice_expires_at > now,
                ),
            )
            # Submitted transactions take precedence; otherwise show the
            # newest unexpired invoice. One query replaces two serial reads.
            .order_by(Deposit.tx_hash.is_(None), Deposit.id.desc())
            .first()
        )
        if not deposit:
            return None
        if deposit.tx_hash is not None:
            return {
                "kind": "status",
                "id": deposit.id,
                "code": deposit.invoice_code or deposit.order_id,
            }
        return {
            "kind": "resume",
            "id": deposit.id,
            "code": deposit.invoice_code or deposit.order_id,
        }
    finally:
        db.close()


def _load_profile_data(telegram_id: int) -> tuple[str, int] | None:
    """Load profile data in a worker thread, keeping remote DB waits off-loop."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return None
        from services.notification_inbox import unread_count
        return _build_audit_profile(user, telegram_id), int(unread_count(db, telegram_id) or 0)
    finally:
        db.close()


# ╔══════════════════════════════════════════════════════════════╗
# ║  CALLBACK HANDLERS                                         ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data == "profile_back")
async def profile_back_cb(callback: CallbackQuery):
    """Back button from Profile → DELETES the profile message. Main menu stays."""
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "clear_start_menu")
async def clear_start_menu(callback: CallbackQuery):
    """Delete only the home-menu message containing the tapped button."""
    try:
        if callback.message:
            await callback.message.delete()
            await callback.answer("Menu cleared.")
        else:
            await callback.answer("This menu can no longer be cleared.", show_alert=True)
    except Exception:
        await callback.answer("Could not clear this menu. Please try again.", show_alert=True)


@router.callback_query(F.data == "user_bot_menu")
@router.callback_query(F.data == "user_view")
@router.callback_query(F.data == "main_menu")
async def main_menu_cb(callback: CallbackQuery, state: FSMContext):
    """Back to main menu — edits the current message."""
    if callback.data == "user_view" and callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.clear()
    save_resume((callback.from_user.id, callback.message.chat.id), None)
    await callback.answer()

    bot_username = ""
    menu_data = await asyncio.to_thread(
        _load_main_menu_data,
        callback.from_user.id,
        callback.from_user.full_name,
        callback.from_user.username,
        bot_username,
    )
    if menu_data is None:
        await answer_card(callback, "User record not found. Send /start to register again.")
        return

    unread = menu_data.get("unread_notifications",0)
    featured = menu_data.get("featured_product")
    keyboard = (
        get_admin_main_menu(unread, featured)
        if callback.from_user.id in ADMIN_IDS
        else get_main_menu(unread, featured)
    )
    deposit_action = menu_data.get("deposit_action")
    if deposit_action:
        label = (f"⏳ Payment checking · #{deposit_action['id']}"
                 if deposit_action["kind"] == "status"
                 else f"💳 Resume payment · {deposit_action['code']}")
        keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(
            text=label,
            callback_data=f"resume_deposit_{deposit_action['id']}",
            style="success",
        )])
    target_chat_id = int(menu_data.get("chat_id") or callback.message.chat.id)
    target_message_id = int(menu_data.get("message_id") or callback.message.message_id)
    menu_card = await update_card(
        callback.message,
        text=menu_data["text"],
        reply_markup=keyboard,
        parse_mode="HTML",
        delete_input=False,
        chat_id=target_chat_id,
        message_id=target_message_id,
        owner_id=callback.from_user.id,
        replace_media=bool(callback.message.photo and target_message_id == callback.message.message_id),
        disable_web_page_preview=True,
    )
    final_message_id = int(menu_card.message_id) if menu_card else target_message_id
    final_chat_id = int(menu_card.chat.id) if menu_card else target_chat_id
    await asyncio.to_thread(
        _save_home_menu_location,
        callback.from_user.id,
        final_chat_id,
        final_message_id,
    )
    if callback.message.message_id != final_message_id:
        try:
            await callback.message.delete()
        except Exception:
            pass


@router.callback_query(F.data == "shop_unavailable")
async def shop_unavailable(callback: CallbackQuery):
    await callback.answer(
        "The RainDeals Store is not configured yet. Use Products to shop in Telegram.",
        show_alert=True,
    )


@router.callback_query(F.data == "featured_product")
async def open_featured_product(callback: CallbackQuery):
    """Open the admin-selected home-menu product, including a freebie."""
    featured = await asyncio.to_thread(load_featured_product)
    if not featured:
        await callback.answer(
            "The featured product has not been selected by an admin yet.",
            show_alert=True,
        )
        return
    await callback.answer()
    from handlers.products import product_info
    await product_info(callback, linked_product_id=featured["id"], acknowledge_callback=False)


@router.callback_query(F.data == "language_menu")
async def language_menu(callback: CallbackQuery):
    await callback.answer()
    await show(
        callback,
        "🌐 <b>Language</b>\n\nEnglish is currently active. Other translated language packs are not configured yet.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅ Main menu", callback_data="main_menu")
        ]]),
    )


@router.callback_query(F.data == "my_profile")
async def my_profile(callback: CallbackQuery):
    """Profile → sends NEW audit-style message with Back button. Main menu stays."""
    await callback.answer()

    profile = await asyncio.to_thread(_load_profile_data, callback.from_user.id)
    if not profile:
        await answer_card(callback, "User record not found. Send /start to register again.")
        return
    text, unread = profile

    await answer_card(callback,
        text,
        reply_markup=get_profile_menu(unread),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


def _load_loyalty_summary(telegram_id: int) -> tuple[int, str] | None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return None
        spent = Decimal(str(user.total_spent or 0))
        points = max(0, int(spent * Decimal(str(LOYALTY_POINTS_PER_USD))))
        return points, _format_balance(spent)
    finally:
        db.close()


@router.callback_query(F.data == "loyalty_points")
async def loyalty_points(callback: CallbackQuery):
    await callback.answer()
    summary = await asyncio.to_thread(_load_loyalty_summary, callback.from_user.id)
    if not summary:
        await answer_card(callback, "Your account could not be loaded. Please send /start.")
        return
    points, spent = summary
    await show(
        callback,
        "🌟 <b>LOYALTY POINTS</b>\n\n"
        f"✨ Points earned: <b>{points:,}</b>\n"
        f"🛍 Eligible purchases: <b>${spent}</b>\n\n"
        "Points are calculated from completed purchase spend. Contact support for help with your rewards.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🆘 Support", callback_data="support_ticket")],
            [InlineKeyboardButton(text="⬅ Profile", callback_data="my_profile")],
        ]),
    )


@router.callback_query(F.data == "shop_policies")
async def shop_policies(callback: CallbackQuery):
    await callback.answer()
    policies_url = str(TOS_LINK or "").strip()
    configured = policies_url.startswith("https://") and "your-site.com" not in policies_url.lower()
    rows = []
    if configured:
        rows.append([InlineKeyboardButton(text="📜 Read shop policies", url=policies_url)])
        text = "📜 <b>SHOP POLICIES</b>\n\nOpen the link below to read the current shop terms and policies."
    else:
        text = "📜 <b>SHOP POLICIES</b>\n\nThe policy page is not configured yet. Contact support if you need help before ordering."
    rows.extend([
        [InlineKeyboardButton(text="🆘 Contact support", callback_data="support_ticket")],
        [InlineKeyboardButton(text="⬅ Profile", callback_data="my_profile")],
    ])
    await show(callback, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def _load_mail_reader_orders(telegram_id: int) -> list[tuple[int, str]]:
    db = SessionLocal()
    try:
        orders = (
            db.query(Order.id, Order.product_name)
            .filter(
                Order.telegram_id == int(telegram_id),
                Order.status == "completed",
                Order.delivered_account.isnot(None),
                Order.delivered_account != "",
            )
            .order_by(Order.id.desc())
            .limit(10)
            .all()
        )
        return [(int(order_id), str(name or "Product")) for order_id, name in orders]
    finally:
        db.close()


@router.callback_query(F.data == "profile_mail_reader")
async def profile_mail_reader(callback: CallbackQuery):
    await callback.answer()
    orders = await asyncio.to_thread(_load_mail_reader_orders, callback.from_user.id)
    rows = [
        [InlineKeyboardButton(
            text=f"📨 Order #{order_id} · {name[:36]}",
            callback_data=f"order_detail_{order_id}",
        )]
        for order_id, name in orders
    ]
    if not rows:
        text = (
            "📨 <b>LIVE OTP / MAIL READER</b>\n\n"
            "No completed orders with delivered account details were found. "
            "When an order includes a mail reader link, open that order here."
        )
    else:
        text = (
            "📨 <b>LIVE OTP / MAIL READER</b>\n\n"
            "Choose a delivered order to view its saved account details and mail reader link."
        )
    rows.extend([
        [InlineKeyboardButton(text="📦 All My Orders", callback_data="orders_menu")],
        [InlineKeyboardButton(text="⬅ Profile", callback_data="my_profile")],
    ])
    await show(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║  MEMBERSHIP RETRY CALLBACK                                 ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data == "check_membership_retry")
async def retry_membership_check(callback: CallbackQuery):
    """Run a fresh check after a user joins a required chat."""
    if not callback.message or callback.message.chat.type != "private":
        await callback.answer("Open the bot in a private chat to verify membership.", show_alert=True)
        return

    is_member = await check_user_membership(
        callback.bot,
        callback.from_user.id,
        force_refresh=True,
        with_details=True,
    )

    is_member, failures = is_member
    if is_member:
        await callback.message.delete()
        await answer_card(callback,
            "✅ <b>Verification Successful!</b>\n\n"
            "Send /start to begin using the bot.",
            parse_mode="HTML"
        )
        await callback.answer("✅ Verified! Send /start", show_alert=True)
    else:
        await callback.answer(
            "❌ Could not verify: " + ", ".join(failures) + ". If joined, ask an admin to add the bot as an admin in that chat, then retry.",
            show_alert=True
        )


# ╔══════════════════════════════════════════════════════════════╗
# ║  USER CREATION                                             ║
# ╚══════════════════════════════════════════════════════════════╝

@retry_on_write_conflict(max_attempts=3)
def _get_or_create_user(
    telegram_id: int,
    username: str,
    full_name: str,
    ref_payload: str,
    load_home_menu: bool = True,
) -> dict:
    with transaction() as db:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        is_new = False
        referral_bonus = False

        if not user:
            is_new = True
            referred_by = None

            if ref_payload:
                referral_code = ref_payload.removeprefix("ref_")
                referrer = (
                    db.query(User)
                    .filter(User.referral_code == referral_code)
                    .with_for_update()
                    .first()
                )
                if referrer is not None and referrer.telegram_id != telegram_id:
                    referred_by = referrer.telegram_id
                    referrer.total_referrals = (referrer.total_referrals or 0) + 1
                    referral_bonus = True

            user = User(
                telegram_id=telegram_id,
                username=username,
                full_name=full_name,
                balance=0,
                referral_code=generate_ref_code(),
                referred_by=referred_by,
                total_referrals=0,
                referral_earnings=0,
                total_orders=0,
                total_spent=0,
                total_deposited=0,
                is_banned=False,
            )
            db.add(user)
            db.flush()
        else:
            # Avoid issuing profile updates on every /start when Telegram
            # profile details have not changed.
            if user.username != username:
                user.username = username
            if user.full_name != full_name:
                user.full_name = full_name

        deposit_action = None
        featured = None
        if load_home_menu:
            now = datetime.utcnow()
            deposit = (
                db.query(Deposit)
                .filter(
                    Deposit.telegram_id == telegram_id,
                    Deposit.status.in_(["waiting_hash", "pending"]),
                    or_(Deposit.tx_hash.isnot(None), Deposit.invoice_expires_at > now),
                )
                .order_by(Deposit.tx_hash.is_(None), Deposit.id.desc())
                .first()
            )
            if deposit:
                deposit_action = {
                    "kind": "status" if deposit.tx_hash is not None else "resume",
                    "id": int(deposit.id),
                    "code": deposit.invoice_code or deposit.order_id,
                }
            featured = load_featured_product(db)

        return {
            "is_new": is_new,
            "referral_bonus": referral_bonus,
            "referral_code": user.referral_code,
            "referred_by": getattr(user, "referred_by", None),
            "featured_product": featured,
            "deposit_action": deposit_action,
            # Return the dashboard values from the same transaction. This
            # removes the second remote database query from every /start.
            "profile": {
                "balance": user.balance,
                "total_orders": user.total_orders,
                "total_referrals": user.total_referrals,
                "total_spent": user.total_spent,
                "referral_earnings": user.referral_earnings,
                "referral_code": user.referral_code,
                "home_menu_chat_id": user.home_menu_chat_id,
                "home_menu_message_id": user.home_menu_message_id,
            },
        }


# ╔══════════════════════════════════════════════════════════════╗
# ║  /start COMMAND — With Membership Check                     ║
# ╚══════════════════════════════════════════════════════════════╝

@router.message(CommandStart())
async def start_cmd(message: Message, command: CommandObject, state: FSMContext):
    # This bot's user flow is private-chat only. In particular, never expose
    # join-required prompts in order/stock notification groups.
    if message.chat.type != "private":
        return

    started_at = time.perf_counter()
    loader_ms = membership_ms = db_ms = username_ms = card_ms = menu_save_ms = 0.0
    telegram_id = message.from_user.id
    from handlers.restart import stop_restart
    await stop_restart((message.from_user.id, message.chat.id))
    previous_card = await state.get_data()
    previous_cards = [
        (previous_card.get('_card_chat_id') or message.chat.id, previous_card.get(key))
        for key in ('_card_message_id', 'invoice_card_message_id', 'invoice_photo_message_id')
    ]
    # Reset only transient prompts. Any unpaid/pending invoice remains in the
    # database and is reused/reported by the deposit flow after /start.
    await state.clear()
    save_resume((message.from_user.id, message.chat.id), None)

    # Put one lightweight loader on the user's current card before membership
    # or database checks. The same card is edited into the final screen below.
    loading_card = await update_card(
        message,
        text=_START_LOADING_TEXT,
        parse_mode="HTML",
        delete_input=True,
        owner_id=telegram_id,
        replace_media=bool(
            previous_card.get("invoice_photo_message_id")
            and str(previous_card.get("invoice_photo_message_id"))
            == str(previous_card.get("_card_message_id"))
        ),
        disable_web_page_preview=True,
    )
    loader_ms = (time.perf_counter() - started_at) * 1000
    loading_location = (
        (int(loading_card.chat.id), int(loading_card.message_id))
        if loading_card
        else get_card_location(telegram_id, message.chat.id)
    )
    if loading_location:
        asyncio.create_task(_delete_start_cards_quietly(
            message.bot,
            message.chat.id,
            previous_cards,
            int(loading_location[1]),
        ))
        if telegram_id in ADMIN_IDS:
            asyncio.create_task(_cleanup_admin_start_cards(
                message.bot,
                telegram_id,
                message.chat.id,
                int(loading_location[1]),
            ))

    start_payload = (command.args or "").strip() if command else ""
    deep_link_product_id = None
    vendor_request_id = None
    if start_payload.startswith("product_"):
        product_id_text = start_payload.removeprefix("product_")
        if product_id_text.isdigit():
            deep_link_product_id = int(product_id_text)
    elif start_payload.startswith("vendor_request_"):
        request_id_text = start_payload.removeprefix("vendor_request_")
        if request_id_text.isdigit():
            vendor_request_id = int(request_id_text)

    # ═══════════════════════════════════════════════════════
    # CHANNEL MEMBERSHIP CHECK (skip for admins)
    # ═══════════════════════════════════════════════════════
    if telegram_id not in ADMIN_IDS:
        membership_started_at = time.perf_counter()
        is_member = await check_user_membership(
            message.bot,
            telegram_id,
            fast=deep_link_product_id is not None,
        )
        membership_ms = (time.perf_counter() - membership_started_at) * 1000
        if not is_member:
            await update_card(
                message,
                "⚠️ <b>Access Restricted</b>\n\n"
                "You must join every required chat to use the bot.\n\n"
                "👇 Join below, then press <b>Try Again</b>",
                reply_markup=get_join_keyboard(),
                parse_mode="HTML",
                delete_input=False,
                chat_id=loading_location[0] if loading_location else message.chat.id,
                message_id=loading_location[1] if loading_location else None,
                owner_id=telegram_id,
            )
            return

    username = message.from_user.username
    full_name = message.from_user.full_name
    ref_payload = "" if deep_link_product_id is not None or vendor_request_id is not None else start_payload
    is_admin = telegram_id in ADMIN_IDS

    try:
        db_started_at = time.perf_counter()
        load_home_menu = (
            deep_link_product_id is None
            and vendor_request_id is None
            and not is_admin
        )
        user_task = asyncio.to_thread(
            _get_or_create_user,
            telegram_id,
            username,
            full_name,
            ref_payload,
            load_home_menu,
        )
        admin_dashboard = None
        if deep_link_product_id is None and is_admin:
            from handlers.admin import _load_admin_dashboard

            results = await asyncio.gather(
                user_task,
                asyncio.to_thread(_load_admin_dashboard, full_name, telegram_id),
                return_exceptions=True,
            )
            result, admin_dashboard = results
            if isinstance(result, BaseException):
                raise result
            if isinstance(admin_dashboard, BaseException):
                logger.warning("Could not load admin dashboard during /start", exc_info=admin_dashboard)
                admin_dashboard = None
            unread, deposit_action = 0, None
        elif deep_link_product_id is None:
            # The home menu uses values collected in the user transaction so
            # /start stays to one database session while still restoring an
            # active payment and the current featured item.
            result = await user_task
            unread, deposit_action = 0, result.get("deposit_action")
        else:
            result = await user_task
            unread, deposit_action = 0, None
        db_ms = (time.perf_counter() - db_started_at) * 1000
    except Exception:
        logger.exception("Failed to create/update user %s on /start", telegram_id)
        await update_card(
            message,
            "❌ <b>Startup Error</b>\n\nSomething went wrong starting your session.\nPlease try again with /start",
            parse_mode="HTML",
            delete_input=False,
            chat_id=loading_location[0] if loading_location else message.chat.id,
            message_id=loading_location[1] if loading_location else None,
            owner_id=telegram_id,
        )
        return

    user = SimpleNamespace(**result["profile"])
    saved_home = (
        (result["profile"].get("home_menu_chat_id"), result["profile"].get("home_menu_message_id")),
    )
    if loading_location:
        asyncio.create_task(_delete_start_cards_quietly(
            message.bot,
            message.chat.id,
            previous_cards + list(saved_home),
            int(loading_location[1]),
        ))
    if is_admin:
        from handlers.admin import _get_admin_panel_with_providers
        admin_text = admin_dashboard or (
            "👑 <b>Rain Deals Admin Panel</b>\n\n"
            "Welcome to the admin workspace. Choose an option below."
        )
        menu_card = await update_card(
            message,
            text=admin_text,
            reply_markup=_get_admin_panel_with_providers(),
            parse_mode="HTML",
            delete_input=False,
            chat_id=loading_location[0] if loading_location else result["profile"].get("home_menu_chat_id"),
            message_id=loading_location[1] if loading_location else result["profile"].get("home_menu_message_id"),
            owner_id=telegram_id,
            disable_web_page_preview=True,
        )
        if menu_card:
            await asyncio.to_thread(
                _save_home_menu_location,
                telegram_id,
                int(menu_card.chat.id),
                int(menu_card.message_id),
            )
        logger.info(
            "start_timing user_id=%s admin=true loader_ms=%.1f db_ms=%.1f total_ms=%.1f",
            telegram_id,
            loader_ms,
            db_ms,
            (time.perf_counter() - started_at) * 1000,
        )
        return
    if vendor_request_id is not None:
        from handlers.vendor import prepare_request_application

        form_text, form_markup = await prepare_request_application(state, vendor_request_id)
        menu_card = await update_card(
            message,
            text=form_text or "This provider request has closed. You can view current requests in the vendor channel.",
            reply_markup=form_markup,
            parse_mode="HTML",
            delete_input=False,
            chat_id=loading_location[0] if loading_location else result["profile"].get("home_menu_chat_id"),
            message_id=loading_location[1] if loading_location else result["profile"].get("home_menu_message_id"),
            owner_id=telegram_id,
            disable_web_page_preview=True,
        )
        if menu_card:
            await asyncio.to_thread(
                _save_home_menu_location,
                telegram_id,
                int(menu_card.chat.id),
                int(menu_card.message_id),
            )
        logger.info(
            "start_timing user_id=%s vendor_request=%s loader_ms=%.1f db_ms=%.1f total_ms=%.1f",
            telegram_id,
            vendor_request_id,
            loader_ms,
            db_ms,
            (time.perf_counter() - started_at) * 1000,
        )
        return
    if deep_link_product_id is not None:
        from handlers.products import product_info

        await product_info(message, deep_link_product_id)
        return

    user.username = username
    user.full_name = full_name
    text = _build_start_welcome(user, full_name, telegram_id, username, "")
    keyboard = (
        get_admin_main_menu(featured_product=result.get("featured_product"))
        if is_admin
        else get_main_menu(featured_product=result.get("featured_product"))
    )
    if deposit_action:
        label = (
            f"⏳ Payment checking · #{deposit_action['id']}"
            if deposit_action["kind"] == "status"
            else f"💳 Resume payment · {deposit_action['code']}"
        )
        keyboard.inline_keyboard.insert(0, [InlineKeyboardButton(
            text=label,
            callback_data=f"resume_deposit_{deposit_action['id']}",
            style="success",
        )])
    card_started_at = time.perf_counter()
    menu_card = await update_card(
        message,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML",
        delete_input=False,
        chat_id=loading_location[0] if loading_location else result["profile"].get("home_menu_chat_id"),
        message_id=loading_location[1] if loading_location else result["profile"].get("home_menu_message_id"),
        owner_id=telegram_id,
        disable_web_page_preview=True,
    )
    card_ms = (time.perf_counter() - card_started_at) * 1000
    if menu_card:
        menu_save_started_at = time.perf_counter()
        await asyncio.to_thread(
            _save_home_menu_location,
            telegram_id,
            int(menu_card.chat.id),
            int(menu_card.message_id),
        )
        menu_save_ms = (time.perf_counter() - menu_save_started_at) * 1000
    logger.info(
        "start_timing user_id=%s loader_ms=%.1f membership_ms=%.1f db_ms=%.1f "
        "card_ms=%.1f menu_save_ms=%.1f visible_ms=%.1f total_ms=%.1f",
        telegram_id,
        loader_ms,
        membership_ms,
        db_ms,
        card_ms,
        menu_save_ms,
        (time.perf_counter() - started_at - menu_save_ms / 1000) * 1000,
        (time.perf_counter() - started_at) * 1000,
    )
