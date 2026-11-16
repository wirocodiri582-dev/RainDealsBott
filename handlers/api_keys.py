"""Telegram UI for users to create and revoke their reseller API key."""

import asyncio
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from decimal import Decimal
from sqlalchemy import func

from services.api_keys import (
    ApiKeyConfigurationError,
    active_api_key_with_secret,
    create_api_key,
    revoke_api_key,
)
from database import SessionLocal
from models.api_key import ApiKey, ApiOrder
from models.order import Order
from models.user import User
from utils.ui import schedule_delete_card, show


router = Router()

API_ROOT = "https://raindeals-api.samarthp2727-rd.workers.dev"
API_BASE = f"{API_ROOT}/api/v1"
API_DOCS = f"{API_ROOT}/docs/"


def _api_account_card_data(telegram_id: int) -> tuple[str, Decimal, Decimal]:
    """Return owner name, shared wallet balance, and lifetime API order volume."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        owner = (user.full_name if user else "RainDeals reseller") or "RainDeals reseller"
        balance = Decimal(str(user.balance or 0)) if user else Decimal("0")
        total_volume = Decimal(str(
            db.query(func.coalesce(func.sum(Order.amount), 0))
            .select_from(ApiOrder)
            .join(ApiKey, ApiKey.id == ApiOrder.api_key_id)
            .join(Order, Order.id == ApiOrder.order_id)
            .filter(ApiKey.telegram_id == telegram_id, Order.refunded.is_(False))
            .scalar() or 0
        ))
        return owner, balance, total_volume


def _api_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Generate / Rotate API Key",
                    callback_data="api_key_generate",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛑 Revoke API Key",
                    callback_data="api_key_revoke",
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


def _api_endpoints() -> str:
    return (
        "🧭 <b>API endpoints</b>\n"
        "• <code>GET /api/v1/me</code> — balance and account\n"
        "• <code>GET /api/v1/products</code> — list products\n"
        "• <code>POST /api/v1/order</code> — place an order\n"
        "• <code>GET /api/v1/orders</code> — order history\n"
        "• <code>GET /api/v1/order/{id}</code> — order details\n"
    )


@router.callback_query(F.data == "api_key_menu")
async def api_key_menu(callback: CallbackQuery):
    await callback.answer()

    try:
        key, raw_key = await asyncio.to_thread(
            active_api_key_with_secret,
            callback.from_user.id,
        )
    except ApiKeyConfigurationError:
        await show(
            callback,
            "⚠️ <b>API Access Not Ready</b>\n\n"
            "The administrator must configure <code>API_KEY_PEPPER</code> first.",
            parse_mode="HTML",
            reply_markup=_api_menu(),
        )
        return

    owner, wallet_balance, total_volume = await asyncio.to_thread(
        _api_account_card_data, callback.from_user.id
    )

    if raw_key:
        status = (
            "🔑 <b>Your API Key</b>\n"
            f"<code>{escape(raw_key)}</code>\n"
            "<i>Tap the key to copy it.</i>"
        )
    elif key:
        status = f"🔑 Active key: <code>{escape(key.key_prefix)}...</code>\n⚠️ Generate a new key to display it here."
    else:
        status = "❌ No active API key"

    key_card = await show(
        callback,
        "🛍 <b>RAIN DEALS — RESELLER API</b>\n\n"
        f"👤 <b>Merchant:</b> {escape(owner)}\n"
        f"🆔 <b>Telegram ID:</b> <code>{callback.from_user.id}</code>\n\n"
        f"{status}\n\n"
        f"💳 <b>Unified Wallet Balance:</b> ${wallet_balance:.2f} USDT\n"
        f"📊 <b>Total API Volume:</b> ${total_volume:.2f} USDT\n\n"
        "💡 <b>Shared wallet:</b> API purchases use your RainDeals bot wallet, "
        "current reseller rates, and available stock.\n"
        "🛡 <b>Pricing & stock:</b> the API uses the product rates and stock "
        "configured by the admin; unavailable stock cannot be purchased.\n\n"
        f"🌐 <b>API Base URL:</b> <code>{API_BASE}</code>\n"
        f'📚 <b>Documentation:</b> <a href="{API_DOCS}">Open API guide</a>\n\n'
        "<b>Authentication header</b>\n"
        f"<code>X-API-Key: {escape(raw_key or (key.key_prefix + '…' if key else 'YOUR_API_KEY'))}</code>\n\n"
        f"{_api_endpoints()}\n"
        "Keep the key on your server. Never place it in browser JavaScript, "
        "HTML, screenshots, or public repositories. Rotating a key revokes the previous one.",
        parse_mode="HTML",
        reply_markup=_api_menu(),
    )
    if raw_key and key_card:
        schedule_delete_card(key_card, owner_id=callback.from_user.id, delay_seconds=900)


@router.callback_query(F.data == "api_key_generate")
async def api_key_generate(callback: CallbackQuery):
    await callback.answer()

    try:
        raw_key, _ = await asyncio.to_thread(
            create_api_key,
            callback.from_user.id,
        )
    except ApiKeyConfigurationError:
        await show(
            callback,
            "⚠️ <b>API Access Not Ready</b>\n\n"
            "The administrator must add <code>API_KEY_PEPPER</code> "
            "to the server environment first.",
            parse_mode="HTML",
            reply_markup=_api_menu(),
        )
        return

    owner, wallet_balance, total_volume = await asyncio.to_thread(
        _api_account_card_data, callback.from_user.id
    )

    key_card = await show(
        callback,
        "🛍 <b>RAIN DEALS — RESELLER API</b>\n"
        "✅ <b>New API key created</b>\n\n"
        f"👤 <b>Merchant:</b> {escape(owner)}\n"
        f"🆔 <b>Telegram ID:</b> <code>{callback.from_user.id}</code>\n"
        f"💳 <b>Unified Wallet Balance:</b> ${wallet_balance:.2f} USDT\n"
        f"📊 <b>Total API Volume:</b> ${total_volume:.2f} USDT\n\n"
        "🔑 <b>Your API Key</b> (tap to copy)\n"
        f"<code>{escape(raw_key)}</code>\n\n"
        "💡 API purchases use your RainDeals bot wallet and admin-configured product rates and stock.\n"
        f"🌐 <b>Base URL:</b> <code>{API_BASE}</code>\n"
        f'📚 <a href="{API_DOCS}">Documentation and integration guide</a>\n\n'
        "<b>Authentication</b>\n"
        f"<code>X-API-Key: {escape(raw_key)}</code>\n\n"
        f"{_api_endpoints()}\n"
        "🔒 Store this key on your server only; never put it in browser JavaScript, HTML, screenshots, or GitHub.",
        parse_mode="HTML",
        reply_markup=_api_menu(),
    )
    if key_card:
        schedule_delete_card(key_card, owner_id=callback.from_user.id, delay_seconds=900)


@router.callback_query(F.data == "api_key_revoke")
async def api_key_revoke(callback: CallbackQuery):
    await callback.answer()
    revoked = await asyncio.to_thread(revoke_api_key, callback.from_user.id)

    if revoked:
        text = (
            "🛑 <b>API Key Revoked</b>\n\n"
            "That key can no longer access your balance or products."
        )
    else:
        text = "ℹ️ <b>No Active API Key</b>\n\nThere was nothing to revoke."

    await show(callback, text, parse_mode="HTML", reply_markup=_api_menu())
