from services.screen_controller import managed_screen
from services.payment_submissions import bind_reference
from services.notification_outbox import queue_message
from services.input_forms import is_cancel
from services.session_policy import INVOICE_LIFETIME_SECONDS
# handlers/deposit.py

import re
import uuid
import secrets
import logging
import asyncio
import time
from html import escape
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import desc

from states.deposit_states import DepositState
from database import SessionLocal
from models.deposit import Deposit
from models.promocode import PromoCode
from models.user import User
from services.deposit_checker import verify_deposit
from keyboards.deposit import get_deposit_menu
import config
from config import BEP20_ADDRESS, POLYGON_ADDRESS, BINANCE_PAY_ID
from utils.ui import (
    track_card,
    show,
    update_card,
    answer_card,
    schedule_card_transition,
    schedule_delete_card,
)

logger = logging.getLogger(__name__)

router = Router()

# ╔══════════════════════════════════════════════════════════════╗
# ║                     CONFIGURATION                           ║
# ╚══════════════════════════════════════════════════════════════╝

NETWORK_CONFIG = {
    "BEP20": {
        "icon": "🟡",
        "label": "BEP20 (BSC)",
        "description": "Binance Smart Chain",
        "style": "primary",
        "currency": "USDT",
        "symbol": "$",
    },
    "POLYGON": {
        "icon": "🟣",
        "label": "Polygon (MATIC)",
        "description": "Polygon Network",
        "style": "success",
        "currency": "USDT",
        "symbol": "$",
    },
    "BINANCE": {
        "icon": "🔴",
        "label": "Binance Pay",
        "description": "Pay with any crypto",
        "style": "danger",
        "currency": "USDT",
        "symbol": "$",
    },
    "UPI": {
        "icon": "🟢",
        "label": "UPI",
        "description": "Indian Rupee (₹)",
        "style": "primary",
        "currency": "INR",
        "symbol": "₹",
    },
    "STARS": {
        "icon": "⭐",
        "label": "Telegram Stars",
        "description": "Pay securely with Telegram Stars",
        "style": "primary",
        "currency": "XTR",
        "symbol": "⭐",
    },
    "PROMO": {
        "icon": "🏷️",
        "label": "Promocode",
        "description": "Promo Code Redemption",
        "style": "primary",
        "currency": "USDT",
        "symbol": "$",
    },
}

NETWORK_ADDRESSES = {
    "BEP20": BEP20_ADDRESS,
    "POLYGON": POLYGON_ADDRESS,
}

NETWORK_CALLBACKS = {
    "deposit_bep20": "BEP20",
    "deposit_polygon": "POLYGON",
    "deposit_binance": "BINANCE",
    "deposit_upi": "UPI",
}

CRYPTO_NETWORKS = {"BEP20", "POLYGON"}
BINANCE_PAY_NETWORK = "BINANCE"

TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
UTR_RE = re.compile(r"^\d{12}$")
TXN_ID_RE = re.compile(r"^(?:FMPIB)?\d{8,15}$", re.IGNORECASE)
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9]{8,32}$")
PROMO_CODE_RE = re.compile(r"^[A-Za-z0-9]{4,20}$")

# Pagination settings for deposit history
DEPOSITS_PER_PAGE = 5


# ╔══════════════════════════════════════════════════════════════╗
# ║                   UI HELPERS                                ║
# ╚══════════════════════════════════════════════════════════════╝

def _divider(char: str = "━", length: int = 30) -> str:
    return char * length


def _border_box(title: str, emoji: str = "💰") -> str:
    return (
        f"╔{_divider('═', 28)}╗\n"
        f"║  {emoji} {title:<23}║\n"
        f"╚{_divider('═', 28)}╝"
    )


def _format_value(value, places: str) -> str:
    """Format a Decimal value without exposing accounting-only precision."""
    try:
        rounded = Decimal(str(value or 0)).quantize(Decimal(places), rounding=ROUND_HALF_UP)
        return format(rounded, "f").rstrip("0").rstrip(".") or "0"
    except Exception:
        return "0"


def _format_usdt(value) -> str:
    return f"${_format_value(value, '0.001')} USDT"


def _format_inr(value) -> str:
    return f"₹{_format_value(value, '0.01')} INR"


def _load_wallet_balance(telegram_id: int):
    db = SessionLocal()
    try:
        return db.query(User.balance).filter(User.telegram_id == int(telegram_id)).scalar()
    finally:
        db.close()


def _format_amount(amount, network: str) -> str:
    """Compatibility formatter for payment-start screens.

    Completed UPI history uses both INR and USDT separately; this helper is
    for the requested amount shown before a payment is verified.
    """
    if (network or "").upper() == "UPI":
        return _format_inr(amount)
    return _format_usdt(amount)


def _format_timestamp() -> str:
    return datetime.now().strftime("%d %b %Y, %I:%M:%S %p")


def _format_dt(dt_value) -> str:
    """Format a datetime object to readable string."""
    if dt_value is None:
        return "N/A"
    if isinstance(dt_value, datetime):
        return dt_value.strftime("%d %b %Y, %I:%M %p")
    try:
        return str(dt_value)[:19]
    except Exception:
        return "N/A"


def _get_network_info(network: str) -> dict:
    return NETWORK_CONFIG.get(network, {
        "icon": "💰",
        "label": network,
        "description": "",
        "style": "primary",
        "currency": "USDT",
        "symbol": "$",
    })


def _configured_payment_value(key: str, fallback: str = "") -> str:
    from services.runtime_settings import payment_value
    return payment_value(key, fallback)


def _payment_method_is_enabled(network: str) -> bool:
    from services.runtime_settings import payment_method_enabled, payment_value
    fallback = {
        "BEP20": getattr(config, "BEP20_ADDRESS", ""),
        "POLYGON": getattr(config, "POLYGON_ADDRESS", ""),
        "BINANCE": getattr(config, "BINANCE_PAY_ID", ""),
        "UPI": getattr(config, "UPI_ID", ""),
    }
    if network == "STARS":
        from services.stars_payments import stars_rate
        configured = stars_rate() is not None
    else:
        key = {
            "BEP20": "payment_bep20_address", "POLYGON": "payment_polygon_address",
            "BINANCE": "payment_binance_pay_id", "UPI": "payment_upi_id",
        }.get(network)
        configured = bool(payment_value(key, fallback.get(network, ""))) if key else False
    return payment_method_enabled(network) and configured


def _status_emoji(status: str) -> str:
    """Get emoji for deposit status."""
    status_map = {
        "completed": "✅",
        "pending": "⏳",
        "waiting_hash": "🔍",
        "failed": "❌",
        "expired": "⏰",
        "cancelled": "🚫",
        "review_required": "⚠️",
        "refunded": "↩️",
    }
    return status_map.get(status, "❓")


def _status_label(status: str) -> str:
    """Get human-readable label for deposit status."""
    status_map = {
        "completed": "Completed",
        "pending": "Pending",
        "waiting_hash": "Awaiting TXID",
        "failed": "Failed",
        "expired": "Expired",
        "cancelled": "Cancelled",
        "review_required": "Needs admin review",
        "refunded": "Refund recorded",
    }
    return status_map.get(status, status.capitalize())


# ╔══════════════════════════════════════════════════════════════╗
# ║           AUTO-CLOSE SUCCESS MESSAGE HELPER                ║
# ╚══════════════════════════════════════════════════════════════╝

def _schedule_auto_close_success_message(bot, chat_id: int, message_id: int, delay_seconds: int = 60):
    """Return a completed deposit card to its menu unless the user navigates first."""
    menu_text = (
        "🏠 <b>Main Menu</b>\n\n"
        "Welcome back! What would you like to do?\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Your deposit was confirmed successfully.</i>\n\n"
        "Use the buttons below to navigate:"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Shop Now", callback_data="products_menu")],
            [
                InlineKeyboardButton(text="💰 Make a Deposit", callback_data="deposit_start"),
                InlineKeyboardButton(text="📜 My Orders", callback_data="orders_menu"),
            ],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")],
        ]
    )
    return schedule_card_transition(
        bot,
        chat_id=chat_id,
        message_id=message_id,
        delay_seconds=delay_seconds,
        text=menu_text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║                STANDARD KEYBOARDS (NO SUPPORT BUTTONS)      ║
# ╚══════════════════════════════════════════════════════════════╝

def _cancel_kb() -> InlineKeyboardMarkup:
    """Cancel the active unpaid invoice or navigate to the main menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Switch payment method",
                    callback_data="deposit_change_method",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel Deposit",
                    callback_data="deposit_cancel",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Main Menu",
                    callback_data="main_menu",
                )
            ],
        ]
    )


def _back_to_menu_kb() -> InlineKeyboardMarkup:
    """Back to main menu button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 Main Menu",
                    callback_data="main_menu",
                )
            ]
        ]
    )


def _promo_cancel_kb() -> InlineKeyboardMarkup:
    """Cancel button for promo code input."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="deposit_start",
                )
            ]
        ]
    )


def _promo_retry_kb() -> InlineKeyboardMarkup:
    """Retry or cancel for promo code."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Try Another Code",
                    callback_data="deposit_promo",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Main Menu",
                    callback_data="main_menu",
                ),
            ],
        ]
    )


def _status_kb(status: str, network: str) -> InlineKeyboardMarkup:
    """Create contextual keyboard based on deposit result status. NO support buttons."""
    buttons = []

    if status == "success":
        buttons.append([
            InlineKeyboardButton(
                text="🛍 Shop Now",
                callback_data="products_menu",
            ),
        ])
    elif status == "pending":
        buttons.append([
            InlineKeyboardButton(
                text="📜 View My Orders",
                callback_data="orders_menu",
            ),
        ])
    else:  # failure
        buttons.append([
            InlineKeyboardButton(
                text="🔄 Try Again",
                callback_data="deposit_start",
            ),
        ])

    buttons.append([
        InlineKeyboardButton(
            text="🏠 Main Menu",
            callback_data="main_menu",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ╔══════════════════════════════════════════════════════════════╗
# ║            MY DEPOSITS — VIEW DEPOSIT HISTORY              ║
# ╚══════════════════════════════════════════════════════════════╝

def _get_user_deposits(telegram_id: int, page: int = 0) -> tuple:
    """Get paginated deposit history for a user. Returns (deposits_list, total_count)."""
    db = SessionLocal()
    try:
        query = (
            db.query(Deposit)
            .filter(Deposit.telegram_id == telegram_id)
            .order_by(desc(Deposit.id))
        )
        total = query.count()
        deposits = (
            query
            .offset(page * DEPOSITS_PER_PAGE)
            .limit(DEPOSITS_PER_PAGE)
            .all()
        )
        return deposits, total
    finally:
        db.close()


def _build_deposit_card(deposit, index: int) -> str:
    """Build a formatted card for a single deposit."""
    network_info = _get_network_info(deposit.network or "unknown")
    icon = network_info["icon"]
    status_emoji = _status_emoji(deposit.status)
    status_label = _status_label(deposit.status)

    created_str = _format_dt(deposit.created_at if hasattr(deposit, 'created_at') else None)

    tx_display = "N/A"
    if (deposit.network or "").upper() == "STARS" and deposit.telegram_payment_charge_id:
        tx_display = "Telegram payment confirmed"
    elif deposit.tx_hash:
        if deposit.tx_hash.startswith("PROMO_"):
            tx_display = f"🎫 {deposit.tx_hash[6:]}"
        elif len(deposit.tx_hash) > 20:
            tx_display = f"{deposit.tx_hash[:12]}...{deposit.tx_hash[-6:]}"
        else:
            tx_display = deposit.tx_hash

    amount_lines = []
    if (deposit.network or "").upper() == "STARS":
        if deposit.status == "waiting_hash":
            status_label = "Awaiting Stars payment"
        elif deposit.status == "pending" and deposit.telegram_payment_charge_id:
            status_label = "Payment received · wallet credit processing"
        if deposit.stars_amount_paid:
            amount_lines.append(f"├─ ⭐ <b>Paid:</b> {int(deposit.stars_amount_paid)} Stars")
        elif deposit.stars_amount_requested:
            amount_lines.append(f"├─ ⭐ <b>Invoice:</b> {int(deposit.stars_amount_requested)} Stars")
        amount_lines.append(f"├─ 💰 <b>Wallet credit:</b> {_format_usdt(deposit.received_amount or deposit.invoice_usdt_amount or deposit.amount)}")
    elif (deposit.network or "").upper() == "UPI" and deposit.status == "completed":
        # New records retain both values. Older UPI deposits only have the
        # converted USDT amount, so do not incorrectly label it as INR.
        if deposit.inr_amount is not None:
            amount_lines.append(f"├─ 🇮🇳 <b>Paid:</b> {_format_inr(deposit.inr_amount)}")
        amount_lines.append(f"├─ 💰 <b>Credited:</b> {_format_usdt(deposit.received_amount or deposit.amount)}")
        if deposit.conversion_rate is not None:
            amount_lines.append(f"├─ 💱 <b>Rate:</b> ₹{_format_value(deposit.conversion_rate, '0.001')} / USDT")
    elif (deposit.network or "").upper() == "UPI":
        amount_lines.append(f"├─ 🇮🇳 <b>Expected:</b> {_format_inr(deposit.amount)}")
    else:
        amount_lines.append(f"├─ 💰 <b>Amount:</b> {_format_usdt(deposit.received_amount or deposit.amount)}")

    card = (
        f"{status_emoji} <b>Deposit #{deposit.id}</b>\n"
        f"├─ {icon} <b>Network:</b> {network_info['label']}\n"
        + "\n".join(amount_lines) + "\n"
        + f"├─ 🔑 <b>Ref:</b> <code>{tx_display}</code>\n"
        + f"├─ 📅 <b>Date:</b> {created_str}\n"
        + f"└─ 📊 <b>Status:</b> {status_label}\n"
    )
    return card


@router.callback_query(F.data == "my_deposits")
async def my_deposits_handler(callback: CallbackQuery):
    """Show user's deposit history (page 0)."""
    await _show_deposits_page(callback, page=0)


@router.callback_query(F.data.startswith("deposits_page_"))
async def deposits_page_handler(callback: CallbackQuery):
    """Handle pagination for deposit history."""
    try:
        page = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        page = 0
    await _show_deposits_page(callback, page=page)


async def _show_deposits_page(callback: CallbackQuery, page: int = 0):
    """Build and show a page of deposit history."""
    await callback.answer()
    telegram_id = callback.from_user.id

    deposits, total = await asyncio.to_thread(_get_user_deposits, telegram_id, page)

    if total == 0:
        text = (
            f"📜 <b>MY DEPOSITS</b>\n\n"
            f"<b>No deposits found!</b>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"💡 You haven't made any deposits yet.\n"
            f"Start by selecting a payment method below.\n\n"
            f"{_divider('─', 28)}"
        )
        await show(
            callback,
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💰 Make a Deposit", callback_data="deposit_start")],
                    [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")],
                ]
            ),
        )
        return

    total_pages = (total + DEPOSITS_PER_PAGE - 1) // DEPOSITS_PER_PAGE
    start_idx = page * DEPOSITS_PER_PAGE + 1
    end_idx = min(start_idx + DEPOSITS_PER_PAGE - 1, total)

    cards = []
    for deposit in deposits:
        cards.append(_build_deposit_card(deposit, 0))

    text = (
        f"📜 <b>MY DEPOSITS</b>\n\n"
        f"<b>Showing {start_idx}-{end_idx} of {total} deposits</b>\n"
        f"{_divider('─', 28)}\n\n"
        + "\n".join(cards) +
        f"\n{_divider('─', 28)}\n"
        f"<i>Page {page + 1} of {total_pages}</i>"
    )

    # Build pagination buttons
    nav_buttons = []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"deposits_page_{page - 1}"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"deposits_page_{page + 1}"))
    if row:
        nav_buttons.append(row)

    nav_buttons.append([InlineKeyboardButton(text="💰 Make a Deposit", callback_data="deposit_start")])
    nav_buttons.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")])

    result_card = await show(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=nav_buttons),
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║              VALIDATION HELPERS                             ║
# ╚══════════════════════════════════════════════════════════════╝

def _valid_upi_reference(txid: str) -> bool:
    val = (txid or "").strip()
    format_ok = bool(
        UTR_RE.fullmatch(val)
        or TXN_ID_RE.fullmatch(val)
    )
    if not format_ok:
        return False
    from services.runtime_settings import payment_reference_allowed
    return payment_reference_allowed("UPI", val)

def _valid_order_id(value: str) -> bool:
    value = str(value or "").strip()
    if not ORDER_ID_RE.fullmatch(value):
        return False
    from services.runtime_settings import payment_reference_allowed
    return payment_reference_allowed("BINANCE", value)


def _valid_promo_code(value: str) -> bool:
    return bool(PROMO_CODE_RE.match(value))


# ╔══════════════════════════════════════════════════════════════╗
# ║              PROMOCODE HELPERS                              ║
# ╚══════════════════════════════════════════════════════════════╝

def _validate_and_get_promo(code: str):
    """Validate a promocode. Returns (is_valid, data_or_error)."""
    db = SessionLocal()
    try:
        promo = (
            db.query(PromoCode)
            .filter(PromoCode.code == code.upper())
            .first()
        )

        if not promo:
            return False, {"error": f"❌ Invalid promocode:\n<code>{code.upper()}</code>"}

        if not promo.is_active:
            return False, {"error": "❌ This promocode has been deactivated."}

        if not promo.can_use():
            return False, {"error": "❌ This promocode has reached its usage limit."}

        promo_amount = Decimal(str(promo.amount))
        return True, {"promo": promo, "promo_amount": promo_amount}

    finally:
        db.close()


def _check_promo_used(telegram_id: int, code: str) -> bool:
    """Check if user already redeemed this promocode."""
    db = SessionLocal()
    try:
        already_used = (
            db.query(Deposit)
            .filter(
                Deposit.telegram_id == telegram_id,
                Deposit.tx_hash.in_((f"PROMO_{code.upper()}", f"PROMO_{code.upper()}_{telegram_id}")),
                Deposit.status == "completed",
            )
            .first()
        )
        return already_used is not None
    finally:
        db.close()


def _apply_promo_to_user(telegram_id: int, promo_code: str, promo_amount: Decimal):
    from services.promotions import redeem
    try:
        return redeem(telegram_id, promo_code)
    except Exception:
        logger.exception('Promocode redemption could not be completed')
        return False, 'Could not redeem the promocode. Check your wallet before retrying.'


@router.callback_query(F.data == "deposit_change_method")
@router.callback_query(F.data == "deposit_start")
async def deposit_menu(callback: CallbackQuery, state: FSMContext):
    """Display the deposit method selection menu."""
    started = time.perf_counter()
    try:
        await callback.answer()
        verifying = await asyncio.to_thread(_submitted_invoice, callback.from_user.id)
        if verifying:
            await state.clear()
            await show(
                callback,
                _verification_pending_text(verifying),
                parse_mode="HTML",
                reply_markup=_verification_pending_keyboard(),
                replace_media=bool(callback.message.photo),
            )
            return
        active = await asyncio.to_thread(_active_invoice, callback.from_user.id)
        if active:
            await state.update_data(
                deposit_id=active["id"],
                invoice_code=active["code"],
                invoice_expires_at=active["expires_at"],
                amount_usdt=active["amount_usdt"],
                network=active["network"],
            )
        else:
            await state.clear()

        balance = await asyncio.to_thread(_load_wallet_balance, callback.from_user.id)
        from services.runtime_settings import setting_bool
        if not setting_bool("feature_deposits", True):
            await show(callback, "💳 <b>Deposits are temporarily unavailable.</b>\n\nPlease check back later or contact support.", parse_mode="HTML", reply_markup=_back_to_menu_kb(), state=state)
            return
        text = (
            "💎 <b>DEPOSIT MONEY</b>\n\n"
            f"💳 Your balance: <b>{_format_usdt(balance)}</b>\n"
            "Choose a payment method 👇"
        )

        await show(
            callback,
            text,
            parse_mode="HTML",
            reply_markup=get_deposit_menu(),
            state=state,
        )
    finally:
        logger.info(
            "button_timing button=deposit user_id=%s total_ms=%.1f",
            callback.from_user.id,
            (time.perf_counter() - started) * 1000,
        )


@router.callback_query(F.data == "deposit_stars")
async def start_stars_deposit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    from services.stars_payments import stars_rate, stars_limits

    rate = stars_rate()
    minimum, maximum = stars_limits()
    if rate is None:
        await show(
            callback,
            "⭐ <b>Telegram Stars is not configured yet</b>\n\n"
            "The administrator must set <code>TELEGRAM_STARS_USDT_PER_STAR</code> "
            "on the bot host before Stars top-ups can be accepted.",
            parse_mode="HTML",
            reply_markup=_back_to_menu_kb(),
        )
        return

    await state.set_state(DepositState.waiting_stars_amount)
    await show(
        callback,
        "⭐ <b>PAY WITH TELEGRAM STARS</b>\n\n"
        f"Wallet credit rate: <b>{_format_usdt(rate)} per Star</b>\n"
        "Enter the number of Stars to pay as a whole number. Telegram will "
        "confirm the payment automatically; do not send a transaction hash.\n\n"
        f"Minimum: <b>{minimum:,} Stars</b> · Maximum: <b>{maximum:,} Stars</b>\n"
        "Type <code>CANCEL</code> to go back.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="↩️ Back to payment methods", callback_data="deposit_start")
        ]]),
        state=state,
    )


@router.message(DepositState.waiting_stars_amount)
async def process_stars_amount(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if is_cancel(raw):
        await update_card(
            message, state, "❌ <b>Stars top-up cancelled.</b>",
            parse_mode="HTML", reply_markup=_back_to_menu_kb(),
        )
        await state.clear()
        return
    from services.stars_payments import stars_limits
    minimum, maximum = stars_limits()
    if not re.fullmatch(r"\d{1,6}", raw) or not minimum <= int(raw) <= maximum:
        await update_card(
            message, state,
            f"⚠️ Enter a whole number from <b>{minimum:,} to {maximum:,} Stars</b>. "
            "Type <code>CANCEL</code> to go back.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="↩️ Back to payment methods", callback_data="deposit_start")
            ]]),
        )
        return

    try:
        from services.stars_payments import (
            StarsPaymentError, create_stars_invoice, create_stars_invoice_link,
            save_stars_invoice_message,
        )
        stars_amount = int(raw)
        invoice = await asyncio.to_thread(create_stars_invoice, message.from_user.id, stars_amount)
        invoice["telegram_id"] = int(message.from_user.id)
        invoice_url = await create_stars_invoice_link(message.bot, invoice)
        expires = datetime.fromisoformat(invoice["expires_at"])
        remaining = max(0, int((expires - datetime.utcnow()).total_seconds()))
        mins, secs = divmod(remaining, 60)
        credit = _format_usdt(invoice["usdt_credit"])
        body = (
            f"⭐ <b>TELEGRAM STARS · DEPOSIT #{invoice['id']}</b>\n"
            f"🧾 Invoice: <code>{escape(invoice['invoice'])}</code>\n"
            f"⭐ Stars to pay: <b>{int(invoice['stars_amount'])}</b>\n"
            f"💰 Wallet credit: <b>{credit}</b>\n"
            f"⏳ Expires in: <b>{mins}:{secs:02d}</b>\n\n"
            "Tap the payment button and complete checkout in Telegram. "
            "Your wallet is credited only after Telegram confirms the payment."
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"⭐ Pay {int(invoice['stars_amount'])} Stars", url=invoice_url)],
            [InlineKeyboardButton(text="📜 My Deposits", callback_data="my_deposits")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")],
        ])
        card = await answer_card(message, body, state=state, parse_mode="HTML", reply_markup=markup)
        if card:
            await asyncio.to_thread(
                save_stars_invoice_message,
                invoice["id"], message.from_user.id, card.chat.id, card.message_id,
            )
        await state.clear()
    except Exception as exc:
        from services.stars_payments import StarsPaymentError
        if isinstance(exc, StarsPaymentError):
            user_message = str(exc)
        else:
            logger.exception("Could not create Stars invoice for user %s", message.from_user.id)
            user_message = "Could not prepare the Stars invoice. Your wallet was not charged. Please try again."
        await update_card(
            message, state, f"⚠️ {escape(user_message)}",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="↩️ Payment methods", callback_data="deposit_start")
            ]]),
        )


@router.pre_checkout_query()
async def validate_stars_precheckout(query):
    """Answer Telegram's checkout authorization promptly; never credit here."""
    try:
        from services.stars_payments import validate_precheckout
        ok, reason = await asyncio.wait_for(asyncio.to_thread(
            validate_precheckout,
            query.from_user.id,
            query.invoice_payload,
            query.currency,
            query.total_amount,
        ), timeout=7.0)
    except Exception:
        logger.exception("Stars pre-checkout handler failed for user %s", query.from_user.id)
        ok, reason = False, "Payment verification is temporarily unavailable. Please try again."
    if ok:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message=reason[:200])


@router.message(F.successful_payment)
async def complete_stars_payment(message: Message):
    """Credit only from Telegram's authoritative successful_payment update."""
    payment = message.successful_payment
    if not payment:
        return
    from services.stars_payments import record_successful_payment
    result = "retry"
    for attempt in range(5):
        result = await asyncio.to_thread(
            record_successful_payment,
            message.from_user.id,
            payment.invoice_payload,
            payment.currency,
            payment.total_amount,
            payment.telegram_payment_charge_id,
        )
        if result != "retry":
            break
        if attempt < 4:
            await asyncio.sleep(0.5 * (2 ** attempt))
    if result == "retry":
        logger.error(
            "Could not durably record Telegram Stars payment after retries; "
            "leaving update retryable user_id=%s",
            message.from_user.id,
        )
        # The Cloudflare Telegram bridge returns a retryable HTTP error when
        # dispatch raises. Never acknowledge a charge without durable storage.
        raise RuntimeError("Telegram Stars charge could not be durably recorded")
    if result == "already_credited":
        return
    if result == "credited":
        await message.answer(
            "✅ <b>Telegram Stars payment confirmed</b>\n\n"
            f"⭐ Paid: <b>{int(payment.total_amount)} Stars</b>\n"
            "💰 Your RainDeals wallet has been credited. Open Profile or Balance to see the updated amount.",
            parse_mode="HTML",
        )
        return
    if result == "credit_pending":
        await message.answer(
            "✅ Telegram confirmed your Stars payment. Wallet credit is being reconciled; "
            "please do not pay again. Check your deposit history shortly.",
        )
        return

    try:
        refunded = await message.bot.refund_star_payment(
            user_id=message.from_user.id,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
        )
    except Exception:
        refunded = False
        logger.exception("Could not refund unmatched Telegram Stars charge for user %s", message.from_user.id)
    if refunded:
        await message.answer(
            "⚠️ This Stars payment could not be matched to an active RainDeals invoice, "
            "so Telegram has been asked to refund it. Check your Stars balance shortly.",
        )
    else:
        await message.answer(
            "⚠️ Telegram received your Stars payment, but automatic matching needs admin review. "
            "Please do not pay again; support has been alerted.",
        )


# ╔══════════════════════════════════════════════════════════════╗
# ║           DIRECT PROMOCODE REDEMPTION FROM MENU             ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data == "deposit_promo")
async def deposit_promo_start(callback: CallbackQuery, state: FSMContext):
    """Start direct promocode redemption from deposit menu."""
    await callback.answer()

    await show(
        callback,
        (
            f"🏷️ <b>REDEEM PROMOCODE</b>\n\n"
            f"<b>Enter your promocode to get bonus credits!</b>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"💡 <b>How it works:</b>\n"
            f"• Enter a valid promo code\n"
            f"• Get bonus USDT added instantly\n"
            f"• Each code can be used only once per user\n\n"
            f"<i>Send your promocode or type CANCEL</i>"
        ),
        parse_mode="HTML",
        reply_markup=_promo_cancel_kb(),
        state=state,
    )

    await state.set_state(DepositState.waiting_promo_code)


@router.message(DepositState.waiting_promo_code)
async def process_promo_code(message: Message, state: FSMContext):
    """Process promocode from direct redemption flow."""
    text = (message.text or "").strip().upper()

    if is_cancel(message.text):
        await update_card(
            message, state,
            "❌ <b>Promocode Redemption Cancelled</b>",
            parse_mode="HTML",
            reply_markup=_back_to_menu_kb(),
        )
        await state.clear()
        return

    if not _valid_promo_code(text):
        await update_card(
            message, state,
            (
                f"⚠️ <b>Invalid Promocode Format</b>\n\n"
                f"{_divider('─', 28)}\n\n"
                f"❌ Promocodes should be 4-20 alphanumeric characters.\n\n"
                f"<i>Please check your code and try again, or type CANCEL.</i>"
            ),
            parse_mode="HTML",
            reply_markup=_promo_cancel_kb(),
        )
        return

    is_valid, promo_data = await asyncio.to_thread(_validate_and_get_promo, text)

    if not is_valid:
        await update_card(
            message, state,
            promo_data["error"],
            parse_mode="HTML",
            reply_markup=_promo_retry_kb(),
        )
        await state.clear()
        return

    if await asyncio.to_thread(_check_promo_used, message.from_user.id, text):
        await update_card(
            message, state,
            (
                f"⚠️ <b>Already Redeemed</b>\n\n"
                f"{_divider('─', 28)}\n\n"
                f"❌ You have already redeemed this promocode:\n"
                f"<code>{text}</code>\n\n"
                f"<i>Each promocode can only be used once per user.</i>"
            ),
            parse_mode="HTML",
            reply_markup=_promo_retry_kb(),
        )
        await state.clear()
        return

    success, result = await asyncio.to_thread(_apply_promo_to_user,
        message.from_user.id, text, promo_data["promo_amount"]
    )

    if not success:
        await update_card(
            message, state,
            result,
            parse_mode="HTML",
            reply_markup=_back_to_menu_kb(),
        )
        await state.clear()
        return

    # Get card message IDs from state for auto-close timer
    data_before_clear = await state.get_data()
    card_chat_id = data_before_clear.get("_card_chat_id", message.chat.id)
    card_message_id = data_before_clear.get("_card_message_id")

    await state.clear()

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  GROUP NOTIFICATION — PROMOCODE REDEMPTION                 ║
    # ╚══════════════════════════════════════════════════════════════╝
    if getattr(config, 'GROUP_NOTIFICATIONS', False) and hasattr(config, 'GROUP_ID'):
        try:
            now = datetime.now().strftime("%d-%b-%Y %I:%M %p IST")
            uid = str(message.from_user.id)
            masked_uid = f"{uid[:4]}***{uid[-3:]}" if len(uid) >= 7 else uid

            group_msg = (
                "<code>$ journalctl --wallet</code>\n"
                "<code>New wallet event detected.</code>\n"
                "<code>━━━━━━━━━━━━━━━━━━━━━━</code>\n"
                f"<code>ACTION      PROMO</code>\n"
                f"<code>USER        {masked_uid}</code>\n"
                f"<code>CODE        {result['code']}</code>\n"
                f"<code>AMOUNT      ${result['amount']:,.2f}</code>\n"
                f"<code>STATUS      REDEEMED</code>\n"
                f"<code>TIME        {now}</code>\n"
                "<code>━━━━━━━━━━━━━━━━━━━━━━</code>\n"
                "<code>Wallet synchronized.</code>"
            )
            await queue_message(message.bot, f"promo:{message.from_user.id}:{result['code']}",
                chat_id=config.GROUP_ID,
                text=group_msg,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Failed to send group promo notification")

    # Show success message
    promo_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Shop Now", callback_data="products_menu")],
            [InlineKeyboardButton(text="💰 Make a Deposit", callback_data="deposit_start")],
            [InlineKeyboardButton(text="📜 My Deposits", callback_data="my_deposits")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")],
        ]
    )

    if card_chat_id and card_message_id:
        await update_card(
            message, None,
            (
                f"🎉 <b>PROMOCODE REDEEMED!</b>\n\n"
                f"<b>Congratulations!</b> Your promo code has been applied.\n\n"
                f"{_divider('═', 28)}\n\n"
                f"🏷️ <b>Code:</b> <code>{result['code']}</code>\n"
                f"💰 <b>Bonus:</b> +${result['amount']:,.2f} USDT\n"
                f"💳 <b>New Balance:</b> ${result['balance']:,.2f} USDT\n\n"
                f"{_divider('═', 28)}\n\n"
                f"✅ <b>Status:</b> Balance Credited Instantly\n"
                f"🛍 Your bonus is ready to use!\n\n"
                f"<i>Thank you for using the promocode! 🙏</i>"
            ),
            chat_id=card_chat_id,
            message_id=card_message_id,
            parse_mode="HTML",
            reply_markup=promo_kb,
        )
    else:
        await update_card(
            message, None,
            (
                f"🎉 <b>PROMOCODE REDEEMED!</b>\n\n"
                f"<b>Congratulations!</b> Your promo code has been applied.\n\n"
                f"{_divider('═', 28)}\n\n"
                f"🏷️ <b>Code:</b> <code>{result['code']}</code>\n"
                f"💰 <b>Bonus:</b> +${result['amount']:,.2f} USDT\n"
                f"💳 <b>New Balance:</b> ${result['balance']:,.2f} USDT\n\n"
                f"{_divider('═', 28)}\n\n"
                f"✅ <b>Status:</b> Balance Credited Instantly\n"
                f"🛍 Your bonus is ready to use!\n\n"
                f"<i>Thank you for using the promocode! 🙏</i>"
            ),
            parse_mode="HTML",
            reply_markup=promo_kb,
        )

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  AUTO-CLOSE: After 60 sec, transition back to main menu    ║
    # ╚══════════════════════════════════════════════════════════════╝
    if card_chat_id and card_message_id:
        _schedule_auto_close_success_message(
            bot=message.bot,
            chat_id=card_chat_id,
            message_id=card_message_id,
            delay_seconds=60,
        )


# ╔══════════════════════════════════════════════════════════════╗
# ║              NETWORK SELECTION                              ║
# ╚══════════════════════════════════════════════════════════════╝

@router.callback_query(F.data.in_(NETWORK_CALLBACKS.keys()))
async def select_network(callback: CallbackQuery, state: FSMContext):
    """Select or switch the payment method for a single active invoice."""
    await callback.answer()
    network = NETWORK_CALLBACKS[callback.data]
    if not _payment_method_is_enabled(network):
        await show(callback, "⚠️ This payment method is currently unavailable. Choose another method.", reply_markup=get_deposit_menu(), replace_media=bool(callback.message.photo))
        return
    verifying = await asyncio.to_thread(_submitted_invoice, callback.from_user.id)
    if verifying:
        await state.clear()
        await show(
            callback,
            _verification_pending_text(verifying),
            parse_mode="HTML",
            reply_markup=_verification_pending_keyboard(),
            replace_media=bool(callback.message.photo),
        )
        return
    data = await state.get_data()
    deposit_id = data.get("deposit_id")
    expires_at = data.get("invoice_expires_at")
    if not deposit_id:
        active = await asyncio.to_thread(_active_invoice, callback.from_user.id)
        if active:
            deposit_id = active["id"]
            expires_at = active["expires_at"]
            data = {
                **data,
                "deposit_id": active["id"],
                "invoice_code": active["code"],
                "invoice_expires_at": active["expires_at"],
                "amount_usdt": active["amount_usdt"],
                "network": active["network"],
            }
    if deposit_id and expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
        except (TypeError, ValueError):
            expiry = datetime.min
        if expiry > datetime.utcnow():
            if not await asyncio.to_thread(_switch_invoice_network, int(deposit_id), network):
                await callback.message.answer("This invoice is no longer switchable. Start a new deposit.")
                await state.clear()
                return
            await state.update_data(
                deposit_id=int(deposit_id),
                invoice_code=data.get("invoice_code"),
                invoice_expires_at=expires_at,
                amount_usdt=data.get("amount_usdt"),
                network=network,
            )
            await state.set_state(DepositState.waiting_txid)
            await _show_invoice(callback, state, int(deposit_id), network, data)
            return
        await state.clear()

    try:
        invoice = await asyncio.to_thread(_create_invoice, callback.from_user.id, Decimal("0"), network)
    except Exception:
        logger.exception("Failed to create payment invoice")
        await show(callback, "❌ Could not create an invoice. Please try again.", reply_markup=_back_to_menu_kb())
        return
    if invoice.get("verification_pending"):
        await state.clear()
        await show(
            callback,
            _verification_pending_text(invoice),
            parse_mode="HTML",
            reply_markup=_verification_pending_keyboard(),
            replace_media=bool(callback.message.photo),
        )
        return
    invoice_data = {
        **data,
        "deposit_id": invoice["id"],
        "invoice_code": invoice["code"],
        "invoice_expires_at": invoice["expires_at"],
        "amount_usdt": invoice["amount_usdt"],
        "network": network,
    }
    await state.update_data(**invoice_data)
    await state.set_state(DepositState.waiting_txid)
    await _show_invoice(callback, state, int(invoice["id"]), network, invoice_data)


def _load_invoice_for_resume(telegram_id: int, deposit_id: int) -> dict | None:
    db = SessionLocal()
    try:
        deposit = db.query(Deposit).filter(
            Deposit.id == deposit_id,
            Deposit.telegram_id == telegram_id,
            Deposit.status.in_(["waiting_hash", "pending"]),
        ).first()
        if not deposit:
            return None
        return {
            "id": deposit.id,
            "code": deposit.invoice_code or deposit.order_id,
            "network": deposit.network or "",
            "expires_at": deposit.invoice_expires_at.isoformat() if deposit.invoice_expires_at else None,
            "amount_usdt": str(deposit.invoice_usdt_amount or 0),
            "tx_hash": deposit.tx_hash,
            "status": deposit.status,
            "telegram_chat_id": deposit.telegram_chat_id,
            "telegram_message_id": deposit.telegram_message_id,
        }
    finally:
        db.close()


@router.callback_query(F.data.startswith("resume_deposit_"))
async def resume_deposit(callback: CallbackQuery, state: FSMContext):
    """Resume the user's existing invoice or show its submitted payment status."""
    try:
        deposit_id = int(callback.data.rsplit("_", 1)[1])
    except (TypeError, ValueError):
        await callback.answer("Invalid invoice.", show_alert=True)
        return
    await callback.answer()

    invoice = await asyncio.to_thread(_load_invoice_for_resume, callback.from_user.id, deposit_id)
    if not invoice:
        await show(
            callback,
            "ℹ️ <b>This invoice is no longer active.</b>\n\nOpen Wallet to create a new invoice if you still need to deposit.",
            parse_mode="HTML",
            reply_markup=_back_to_menu_kb(),
        )
        return

    if invoice.get("tx_hash"):
        await show(
            callback,
            _verification_pending_text(invoice),
            parse_mode="HTML",
            reply_markup=_verification_pending_keyboard(),
            replace_media=bool(callback.message.photo),
        )
        return

    if not invoice.get("expires_at") or datetime.fromisoformat(invoice["expires_at"]) <= datetime.utcnow():
        await show(
            callback,
            "⏰ <b>This invoice has expired.</b>\n\nOpen Wallet to create a fresh invoice.",
            parse_mode="HTML",
            reply_markup=_back_to_menu_kb(),
            replace_media=bool(callback.message.photo),
        )
        return

    invoice_data = {
        "deposit_id": invoice["id"],
        "invoice_code": invoice["code"],
        "invoice_expires_at": invoice["expires_at"],
        "amount_usdt": invoice["amount_usdt"],
        "network": invoice["network"],
        "_card_chat_id": invoice.get("telegram_chat_id"),
        "_card_message_id": invoice.get("telegram_message_id"),
    }
    await state.update_data(**invoice_data)
    await state.set_state(DepositState.waiting_txid)
    await _show_invoice(callback, state, deposit_id, invoice["network"], invoice_data)


# ╔══════════════════════════════════════════════════════════════╗
# ║              AMOUNT PROCESSING                              ║
# ╚══════════════════════════════════════════════════════════════╝

def _create_invoice(telegram_id: int, amount_usdt: Decimal, network: str) -> dict:
    """Create one one-hour invoice shared by all payment methods."""
    db = SessionLocal()
    try:
        # Serialize invoice creation per user. Simultaneous taps then observe
        # and reuse the same active invoice rather than creating duplicates.
        user = (
            db.query(User)
            .filter(User.telegram_id == telegram_id)
            .with_for_update()
            .first()
        )
        if not user:
            raise RuntimeError("Cannot create a deposit invoice for an unknown user")

        verifying = _submitted_invoice_from_db(db, telegram_id)
        if verifying:
            verifying["verification_pending"] = True
            return verifying

        active = (db.query(Deposit)
                  .filter(Deposit.telegram_id == telegram_id,
                          Deposit.network != "STARS",
                          Deposit.status.in_(["waiting_hash", "pending"]),
                          Deposit.tx_hash.is_(None),
                          Deposit.invoice_expires_at > datetime.utcnow())
                  .order_by(Deposit.id.desc()).with_for_update().first())
        if active:
            # Reuse the common invoice without changing its original amount
            # or expiry. Only the selected verification network may change.
            if active.network != network:
                active.network = network
                db.commit()
            return {"id": active.id, "code": active.invoice_code or active.order_id,
                    "expires_at": active.invoice_expires_at.isoformat(),
                    "amount_usdt": str(active.invoice_usdt_amount)}
        code = None
        for _ in range(8):
            candidate = f"PAYOUT{secrets.randbelow(900000) + 100000}"
            if not db.query(Deposit.id).filter(Deposit.invoice_code == candidate).first():
                code = candidate
                break
        if not code:
            raise RuntimeError("Could not allocate a unique invoice code")
        from services.runtime_settings import invoice_lifetime_seconds
        lifetime = invoice_lifetime_seconds(network, INVOICE_LIFETIME_SECONDS)
        expires = datetime.utcnow() + timedelta(seconds=lifetime)
        deposit = Deposit(
            telegram_id=telegram_id,
            order_id=code,
            invoice_code=code,
            invoice_expires_at=expires,
            invoice_usdt_amount=amount_usdt,
            amount=Decimal("0") if network in {"UPI", "BINANCE"} else amount_usdt,
            network=network,
            status="waiting_hash",
        )
        db.add(deposit)
        db.commit()
        db.refresh(deposit)
        return {"id": deposit.id, "code": code, "expires_at": expires.isoformat(), "amount_usdt": str(amount_usdt)}
    finally:
        db.close()


def _active_invoice(telegram_id: int) -> dict | None:
    db = SessionLocal()
    try:
        deposit = (db.query(Deposit)
                   .filter(Deposit.telegram_id == telegram_id,
                           Deposit.network != "STARS",
                           Deposit.status.in_(["waiting_hash", "pending"]),
                           Deposit.tx_hash.is_(None),
                           Deposit.invoice_expires_at > datetime.utcnow())
                   .order_by(Deposit.id.desc()).first())
        if not deposit:
            return None
        return {"id": deposit.id, "code": deposit.invoice_code or deposit.order_id,
                "expires_at": deposit.invoice_expires_at.isoformat(),
                "amount_usdt": str(deposit.invoice_usdt_amount or 0),
                "network": deposit.network}
    finally:
        db.close()


def _submitted_invoice_from_db(db, telegram_id: int) -> dict | None:
    deposit = (
        db.query(Deposit)
        .filter(
            Deposit.telegram_id == telegram_id,
            Deposit.status.in_(["waiting_hash", "pending"]),
            Deposit.tx_hash.isnot(None),
        )
        .order_by(Deposit.id.desc())
        .with_for_update()
        .first()
    )
    if not deposit:
        return None
    return {
        "id": deposit.id,
        "code": deposit.invoice_code or deposit.order_id,
        "network": deposit.network or "",
        "tx_hash": deposit.tx_hash or "",
        "status": deposit.status,
    }


def _submitted_invoice(telegram_id: int) -> dict | None:
    db = SessionLocal()
    try:
        return _submitted_invoice_from_db(db, telegram_id)
    finally:
        db.close()


def _verification_pending_text(deposit: dict) -> str:
    reference = str(deposit.get("tx_hash") or "")
    if len(reference) > 28:
        reference = reference[:24] + "..."
    network = _get_network_info(str(deposit.get("network") or ""))
    return (
        "⏳ <b>PAYMENT VERIFICATION IN PROGRESS</b>\n\n"
        f"🆔 <b>Deposit:</b> <code>#{deposit.get('id')}</code>\n"
        f"🧾 <b>Invoice:</b> <code>{escape(str(deposit.get('code') or ''))}</code>\n"
        f"📡 <b>Network:</b> {network['icon']} {escape(network['label'])}\n"
        f"🔑 <b>Reference:</b> <code>{escape(reference or 'Submitted')}</code>\n\n"
        "Your reference is already saved. The bot will keep checking this payment; "
        "you do not need another invoice or to submit it again."
    )


def _verification_pending_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 My Deposits", callback_data="my_deposits")],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")],
    ])


def _switch_invoice_network(deposit_id: int, network: str) -> bool:
    db = SessionLocal()
    try:
        if network not in NETWORK_CALLBACKS.values():
            return False
        deposit = db.query(Deposit).filter(Deposit.id == deposit_id).with_for_update().first()
        if not deposit or deposit.status not in {"waiting_hash", "pending"} or deposit.tx_hash:
            return False
        if deposit.invoice_expires_at and deposit.invoice_expires_at <= datetime.utcnow():
            return False
        deposit.network = network
        deposit.amount = Decimal("0") if network in {"UPI", "BINANCE"} else Decimal(str(deposit.invoice_usdt_amount or 0))
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _cancel_invoice(deposit_id: int, telegram_id: int) -> str:
    """Expire an unpaid invoice; never cancel one with a submitted reference."""
    db = SessionLocal()
    try:
        deposit = (
            db.query(Deposit)
            .filter(Deposit.id == deposit_id, Deposit.telegram_id == telegram_id)
            .with_for_update()
            .first()
        )
        if not deposit:
            return "not_found"
        if deposit.tx_hash or deposit.status == "pending":
            return "submitted"
        if deposit.status != "waiting_hash":
            return "inactive"
        deposit.status = "cancelled"
        db.commit()
        return "cancelled"
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.callback_query(F.data == "deposit_cancel")
async def cancel_deposit(callback: CallbackQuery, state: FSMContext):
    """Cancel the current unpaid invoice and replace its QR card cleanly."""
    await callback.answer()
    data = await state.get_data()
    deposit_id = data.get("deposit_id")
    if not deposit_id:
        active = await asyncio.to_thread(_active_invoice, callback.from_user.id)
        deposit_id = active.get("id") if active else None

    if deposit_id:
        try:
            result = await asyncio.to_thread(
                _cancel_invoice, int(deposit_id), callback.from_user.id
            )
        except Exception:
            logger.exception("Could not cancel deposit invoice %s", deposit_id)
            result = "error"
    else:
        result = "not_found"

    await state.clear()
    if result == "cancelled":
        text = (
            "❌ <b>Deposit cancelled</b>\n\n"
            "This unpaid invoice is closed. No payment was verified."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 New Deposit", callback_data="deposit_start")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")],
        ])
    elif result == "submitted":
        text = (
            "⏳ <b>Payment reference already submitted</b>\n\n"
            "This invoice cannot be cancelled while verification is in progress. "
            "Check your deposit status or contact support if needed."
        )
        keyboard = _status_kb("pending", str(data.get("network") or "BEP20"))
    elif result == "error":
        text = "⚠️ Could not cancel this invoice. Please try again."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")]
        ])
    else:
        text = "ℹ️ <b>No active deposit to cancel.</b>"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 New Deposit", callback_data="deposit_start")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="main_menu")],
        ])

    result_card = await show(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
        replace_media=bool(callback.message.photo),
    )
    if result == "cancelled" and result_card:
        schedule_delete_card(
            result_card,
            owner_id=callback.from_user.id,
            delay_seconds=900,
        )


def _invoice_keyboard() -> InlineKeyboardMarkup:
    # Keep the controls from the original deposit card.
    return _cancel_kb()


def _invoice_payload(network: str, data: dict) -> str:
    """Build a compact text-only invoice. QR images are intentionally omitted."""
    code = escape(str(data.get("invoice_code", "")))
    deposit_id = escape(str(data.get("deposit_id", "")))
    expiry = datetime.fromisoformat(data["invoice_expires_at"])
    remaining = max(0, int((expiry - datetime.utcnow()).total_seconds()))
    mins, secs = divmod(remaining, 60)
    network_config = _get_network_info(network)

    if network == "UPI":
        upi_id = escape(_configured_payment_value("payment_upi_id", str(getattr(config, "UPI_ID", "") or "Not configured")))
        payment_info = (
            f"🟢 <b>UPI ID</b>\n<code>{upi_id}</code>\n\n"
            "Pay any amount; the verified amount is credited."
        )
        reference = "UTR / Transaction reference"
    elif network in CRYPTO_NETWORKS:
        setting_key = "payment_bep20_address" if network == "BEP20" else "payment_polygon_address"
        address = escape(_configured_payment_value(setting_key, str(NETWORK_ADDRESSES.get(network, "") or "Not configured")))
        payment_info = (
            f"{network_config['icon']} <b>USDT address · {network_config['label']}</b>\n"
            f"<code>{address}</code>\n\n"
            f"Send USDT on {network_config['label']} only. The verified amount is credited."
        )
        reference = "Transaction Hash (TXID)"
    else:
        pay_id = escape(_configured_payment_value("payment_binance_pay_id", str(BINANCE_PAY_ID or "Not configured")))
        payment_info = (
            f"🔴 <b>Binance Pay ID</b>\n<code>{pay_id}</code>\n\n"
            "Pay any amount through Binance Pay; the verified amount is credited."
        )
        reference = "Binance Pay Order ID"

    body = (
        f"💳 <b>DEPOSIT · #{deposit_id}</b>\n"
        f"🧾 Invoice: <code>{code}</code>\n"
        f"📡 Method: {network_config['icon']} {network_config['label']}\n"
        f"⏳ Expires in: <b>{mins}:{secs:02d}</b>\n\n"
        f"{payment_info}\n\n"
        f"📩 Submit: <b>{reference}</b>"
    )
    return body


@managed_screen
async def _show_invoice(target, state: FSMContext, deposit_id: int, network: str, data: dict):
    body = _invoice_payload(network, data)
    bot = target.bot
    chat_id = target.message.chat.id if isinstance(target, CallbackQuery) else target.chat.id
    previous_photo = data.get("invoice_photo_message_id")
    previous_card = data.get("_card_message_id")
    previous_chat = data.get("_card_chat_id") or chat_id

    # Remove the previous invoice view before recreating it, so a switched
    # payment method always appears as QR first and invoice/buttons below.
    if previous_card:
        try:
            await bot.delete_message(chat_id=int(previous_chat), message_id=int(previous_card))
        except Exception:
            pass
    if previous_photo:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(previous_photo))
        except Exception:
            pass

    if isinstance(target, CallbackQuery):
        invoice_message = await target.message.answer(
            body, parse_mode="HTML", reply_markup=_invoice_keyboard()
        )
    else:
        invoice_message = await target.answer(
            body, parse_mode="HTML", reply_markup=_invoice_keyboard()
        )
    track_card(target.from_user.id, invoice_message.chat.id, invoice_message.message_id)
    await state.update_data(
        _card_chat_id=invoice_message.chat.id,
        _card_message_id=invoice_message.message_id,
        invoice_card_message_id=invoice_message.message_id,
        invoice_photo_message_id=None,
    )
    await asyncio.to_thread(
        _save_deposit_card,
        deposit_id,
        invoice_message.chat.id,
        invoice_message.message_id,
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║           TXID / UTR / ORDER ID PROCESSING                  ║
# ╚══════════════════════════════════════════════════════════════╝

def _set_pending_if_waiting(deposit_id: int) -> None:
    db = SessionLocal()
    try:
        dep = db.query(Deposit).filter(Deposit.id == deposit_id).first()
        if dep and dep.status not in {"completed", "failed", "expired", "review_required", "refunded"}:
            dep.status = "pending"
            db.commit()
    finally:
        db.close()


def _save_deposit_card(deposit_id: int, chat_id: int, message_id: int, *, verifying: bool = False) -> None:
    db = SessionLocal()
    try:
        deposit = db.query(Deposit).filter(Deposit.id == deposit_id).first()
        if not deposit:
            return
        deposit.telegram_chat_id = int(chat_id)
        deposit.telegram_message_id = int(message_id)
        deposit.verification_started_at = datetime.utcnow() if verifying else None
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not persist Telegram card for deposit %s", deposit_id)
        raise
    finally:
        db.close()


@router.message(DepositState.waiting_txid)
async def process_txid(message: Message, state: FSMContext):
    """Validate and process the transaction ID/UTR/order ID."""
    txid = message.text.strip() if message.text else ""

    data = await state.get_data()
    deposit_id = data.get("deposit_id")
    network = data.get("network")
    card_chat_id = data.get("_card_chat_id")
    card_message_id = data.get("_card_message_id")
    network_config = _get_network_info(network)

    if not deposit_id:
        await state.clear()
        await update_card(
            message, None,
            "⚠️ <b>No Active Deposit</b>\n\nPlease start a new deposit.",
            chat_id=card_chat_id,
            message_id=card_message_id,
            parse_mode="HTML",
            reply_markup=_back_to_menu_kb(),
        )
        return

    # Format validation
    if network == "UPI":
        if not _valid_upi_reference(txid):
            await update_card(
                message, state,
                (
                    f"⚠️ <b>Invalid UTR/Transaction ID</b>\n\n"
                    f"{_divider('─', 28)}\n\n"
                    f"❌ Expected: 12-digit UTR or app reference\n"
                    f"   (e.g. 123456789012 or FMPIB6269486679)\n\n"
                    f"<i>Check your payment app and try again.</i>"
                ),
                parse_mode="HTML",
                reply_markup=_cancel_kb(),
            )
            return
    elif network == BINANCE_PAY_NETWORK:
        if not _valid_order_id(txid):
            await update_card(
                message, state,
                (
                    f"⚠️ <b>Invalid Order ID</b>\n\n"
                    f"{_divider('─', 28)}\n\n"
                    f"❌ Expected: Alphanumeric, 8-32 characters\n\n"
                    f"<i>Check your Binance Pay receipt and try again.</i>"
                ),
                parse_mode="HTML",
                reply_markup=_cancel_kb(),
            )
            return
    else:
        if not TX_HASH_RE.match(txid):
            await update_card(
                message, state,
                (
                    f"⚠️ <b>Invalid Transaction Hash</b>\n\n"
                    f"{_divider('─', 28)}\n\n"
                    f"❌ Expected: 0x + 64 hex characters (66 total)\n\n"
                    f"<i>Copy the full TXID from your wallet/explorer.</i>"
                ),
                parse_mode="HTML",
                reply_markup=_cancel_kb(),
            )
            return

    try:
        result = await asyncio.to_thread(bind_reference, deposit_id, txid, message.from_user.id, network)
    except SQLAlchemyError:
        logger.warning('Payment reference storage unavailable; retry using the same reference')
        await update_card(message, state, 'Could not confirm that your reference was saved. Retry the same reference; avoid creating another invoice.',
                          reply_markup=_verification_pending_keyboard())
        return

    if result in ('already_submitted', 'reference_locked', 'completed', 'closed'):
        await state.clear()
        notices = {
            'completed': '✅ This deposit is already verified. Open your wallet to see the balance.',
            'closed': 'This invoice is closed. Open My Deposits to review it.',
            'already_submitted': '⏳ Your reference is already saved. Automatic verification continues; no need to submit it again.',
            'reference_locked': 'This invoice already has a reference. Open My Deposits before submitting another payment.',
        }
        await update_card(message, None, notices[result], chat_id=card_chat_id, message_id=card_message_id,
                          reply_markup=_verification_pending_keyboard())
        return

    if result == "not_found":
        await update_card(
            message, state,
            "⚠️ <b>Deposit Not Found</b>\n\nPlease start a new deposit.",
            parse_mode="HTML",
            reply_markup=_back_to_menu_kb(),
        )
        return
    if result == "expired":
        await state.clear()
        await update_card(message, None,
            "⏰ <b>Invoice expired</b>\n\nThis one-hour invoice has expired. Start a new deposit to get a fresh invoice.",
            chat_id=card_chat_id, message_id=card_message_id,
            parse_mode="HTML", reply_markup=_status_kb("failure", network))
        return
    if result == "duplicate":
        await update_card(
            message, state,
            "⚠️ <b>Duplicate Detected</b>\n\n❌ This reference is already in use.",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return

    # Verification loading
    verifying_card = await update_card(
        message, state,
        (
            f"🔍 <b>Verifying Your Payment...</b>\n\n"
            f"{_divider('─', 24)}\n\n"
            f"🆔 Deposit: <code>#{deposit_id}</code>\n"
            f"📡 Network: {network_config['icon']} {network_config['label']}\n"
            f"🔑 Reference: <code>{txid[:20]}...</code>\n\n"
            f"⏳ <i>Checking with the network...</i>\n"
            f"<i>This may take a moment.</i>"
        ),
        parse_mode="HTML"
    )

    # Persist the current card before awaiting the payment network. If the
    # process stops, startup recovery can requeue the deposit and replace this
    # spinner on the same Telegram card.
    if verifying_card:
        try:
            await asyncio.to_thread(
                _save_deposit_card,
                int(deposit_id),
                verifying_card.chat.id,
                verifying_card.message_id,
                verifying=True,
            )
        except Exception:
            logger.exception("Could not save verification card for deposit %s", deposit_id)

    result_info: dict = {}
    try:
        success = await verify_deposit(deposit_id, result_info=result_info)
    except Exception:
        logger.exception("Immediate verification failed for deposit %s; leaving it for the checker", deposit_id)
        try:
            await asyncio.to_thread(_set_pending_if_waiting, int(deposit_id))
        except Exception:
            logger.exception("Could not queue deposit %s for automatic verification", deposit_id)
        await state.clear()
        await update_card(
            message,
            None,
            (
                "⏳ <b>VERIFICATION CONTINUES</b>\n\n"
                f"🆔 Deposit: <code>#{deposit_id}</code>\n"
                "Your reference is saved. Automatic verification will continue in the background.\n\n"
                "You do not need to submit it again."
            ),
            chat_id=card_chat_id,
            message_id=card_message_id,
            parse_mode="HTML",
            reply_markup=_status_kb("pending", network),
        )
        return
    await state.clear()

    # ═══════════════════════════════════════════════════════════════
    # SUCCESS
    # ═══════════════════════════════════════════════════════════════
    if success:
        credit_note = ""
        if result_info.get("reason") == "verified_partial_credit":
            try:
                credited = Decimal(str(result_info.get("received")))
                credit_note = f"📥 <b>Verified amount credited:</b> {_format_usdt(credited)}\n"
            except Exception:
                credit_note = "📥 <b>Verified payment amount credited to your wallet.</b>\n"
        await update_card(
            message, None,
            (
                f"✅ <b>DEPOSIT CONFIRMED</b>\n\n"
                f"🎉 <b>Your deposit has been confirmed!</b>\n\n"
                f"{_divider('─', 28)}\n\n"
                f"🆔 <b>Deposit:</b> <code>#{deposit_id}</code>\n"
                f"📡 <b>Network:</b> {network_config['icon']} {network_config['label']}\n"
                f"🔑 <b>Ref:</b> <code>{txid[:24]}...</code>\n"
                f"🕐 <b>Time:</b> {_format_timestamp()}\n\n"
                f"{_divider('═', 28)}\n\n"
                f"✅ <b>Status:</b> Balance Credited\n"
                f"{credit_note}"
                f"💰 Funds added to your account.\n\n"
                f"<i>Thank you for your deposit! 🙏</i>"
            ),
            chat_id=card_chat_id,
            message_id=card_message_id,
            parse_mode="HTML",
            reply_markup=_status_kb("success", network),
        )

        # ╔══════════════════════════════════════════════════════════════╗
        # ║  AUTO-CLOSE: After 60 sec, transition back to main menu    ║
        # ╚══════════════════════════════════════════════════════════════╝
        if card_chat_id and card_message_id:
            _schedule_auto_close_success_message(
                bot=message.bot,
                chat_id=card_chat_id,
                message_id=card_message_id,
                delay_seconds=60,
            )

        return

    # ═══════════════════════════════════════════════════════════════
    # FAILURE
    # ═══════════════════════════════════════════════════════════════
    if success is False:
        reason = result_info.get("reason")
        if reason == "attempt_limit":
            await update_card(
                message, None,
                (
                    f"⚠️ <b>VERIFICATION CANCELLED</b>\n\n"
                    f"We could not verify this payment after {result_info.get('attempts', 10)} checks. "
                    f"Automatic checking has stopped.\n\n"
                    f"🆔 Deposit: <code>#{deposit_id}</code>\n"
                    f"🔑 Ref: <code>{txid[:24]}...</code>\n\n"
                    f"The admin has been notified. If you paid successfully, contact support and include these details."
                ),
                chat_id=card_chat_id,
                message_id=card_message_id,
                parse_mode="HTML",
                reply_markup=_status_kb("failure", network),
            )
        elif reason == "underpaid":
            await update_card(
                message, None,
                (
                    f"⚠️ <b>AMOUNT MISMATCH</b>\n\n"
                    f"<b>You sent less than the required amount.</b>\n\n"
                    f"{_divider('─', 28)}\n\n"
                    f"📋 <b>Required:</b> {result_info.get('requested')}\n"
                    f"📥 <b>Received:</b> {result_info.get('received')}\n"
                    f"❌ <b>Shortfall:</b> {result_info.get('shortfall')}\n\n"
                    f"💡 Start a new deposit for the remaining amount.\n\n"
                    f"🆔 Deposit Ref: <code>#{deposit_id}</code>"
                ),
                chat_id=card_chat_id,
                message_id=card_message_id,
                parse_mode="HTML",
                reply_markup=_status_kb("failure", network),
            )
        elif reason == "duplicate":
            await update_card(
                message, None,
                (
                    f"⚠️ <b>Duplicate Payment</b>\n\n"
                    f"{_divider('─', 24)}\n\n"
                    f"❌ This reference has already been used.\n\n"
                    f"Contact support if you believe this is an error."
                ),
                chat_id=card_chat_id,
                message_id=card_message_id,
                parse_mode="HTML",
                reply_markup=_status_kb("failure", network),
            )
        else:
            await update_card(
                message, None,
                (
                    f"❌ <b>VERIFICATION FAILED</b>\n\n"
                    f"<b>We couldn't verify your payment.</b>\n\n"
                    f"{_divider('─', 28)}\n\n"
                    f"🆔 Deposit: <code>#{deposit_id}</code>\n"
                    f"🔑 Ref: <code>{txid[:24]}...</code>\n\n"
                    f"Possible reasons:\n"
                    f"• Transaction not found on network\n"
                    f"• Wrong network/address used\n"
                    f"• Transaction still pending (retry later)\n\n"
                    f"💡 Contact support with your deposit ID."
                ),
                chat_id=card_chat_id,
                message_id=card_message_id,
                parse_mode="HTML",
                reply_markup=_status_kb("failure", network),
            )
        return

    # ═══════════════════════════════════════════════════════════════
    # PENDING
    # ═══════════════════════════════════════════════════════════════
    await asyncio.to_thread(_set_pending_if_waiting, deposit_id)

    if result_info.get("reason") == "underpaid":
        pending_msg = (
            "⚠️ <b>PAYMENT RECEIVED · REVIEW NEEDED</b>\n\n"
            "We found your transfer, but its amount is below the invoice.\n\n"
            f"📥 <b>Received:</b> {result_info.get('received')} USDT\n"
            f"📋 <b>Invoice:</b> {result_info.get('requested')} USDT\n"
            f"➖ <b>Difference:</b> {result_info.get('shortfall')} USDT\n\n"
            "No balance was credited. This payment is held for review; contact support with this deposit ID.\n"
            f"🆔 <code>#{deposit_id}</code>"
        )
    elif network == "UPI":
        pending_msg = (
            f"⏳ <b>VERIFICATION PENDING</b>\n\n"
            f"<b>Your payment is being verified.</b>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"🏦 <b>Method:</b> UPI Bank Transfer\n"
            f"💳 <b>Reference:</b> <code>{txid}</code>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"⏱ <b>What happens next:</b>\n"
            f"• We check for your payment regularly\n"
            f"• Usually confirmed within 1-5 minutes\n"
            f"• You'll be notified automatically\n\n"
            f"💡 No need to resend your UTR."
        )
    elif network == BINANCE_PAY_NETWORK:
        pending_msg = (
            f"⏳ <b>VERIFICATION PENDING</b>\n\n"
            f"<b>Your Binance Pay order is being verified.</b>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"🔴 <b>Method:</b> Binance Pay\n"
            f"🧾 <b>Order ID:</b> <code>{txid}</code>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"⏱ <b>What happens next:</b>\n"
            f"• Binance confirms the order\n"
            f"• Usually within 1-5 minutes\n"
            f"• You'll be notified automatically"
        )
    else:
        pending_msg = (
            f"⏳ <b>VERIFICATION PENDING</b>\n\n"
            f"<b>Your transaction is being verified.</b>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"📡 <b>Network:</b> {network_config['icon']} {network_config['label']}\n"
            f"🔑 <b>TXID:</b> <code>{txid[:24]}...</code>\n\n"
            f"{_divider('─', 28)}\n\n"
            f"⏱ <b>What happens next:</b>\n"
            f"• Network confirmations in progress\n"
            f"• Usually takes 2-10 minutes\n"
            f"• You'll be notified automatically\n\n"
            f"💡 No need to resend your TXID."
        )

    await update_card(
        message, None,
        pending_msg,
        chat_id=card_chat_id,
        message_id=card_message_id,
        parse_mode="HTML",
        reply_markup=_status_kb("pending", network),
    )
