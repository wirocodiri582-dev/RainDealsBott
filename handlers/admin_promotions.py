"""Admin-only product promotion cards for the configured stock group."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func

import config
from config import ADMIN_IDS, MAIN_BOT_USERNAME
from database import SessionLocal, transaction
from models.order import Order
from models.product import Product
from utils.ui import show

router = Router()
logger = logging.getLogger(__name__)
PAGE_SIZE = 8
_bot_username = MAIN_BOT_USERNAME


def _admin_markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _load_product_page(page: int) -> tuple[list[dict], int]:
    from handlers.products import _real_stock

    page = max(0, int(page))
    with SessionLocal() as db:
        query = db.query(Product).filter(
            Product.is_active.is_(True),
            Product.price >= 0,
        )
        total = query.count()
        page = min(page, max(0, (total - 1) // PAGE_SIZE))
        products = query.order_by(Product.name.asc(), Product.id.asc()).offset(page * PAGE_SIZE).limit(PAGE_SIZE).all()
        rows = [{
            "id": int(product.id),
            "name": str(product.name or "Product"),
            "price": Decimal(str(product.price or 0)),
            "stock": max(0, int(_real_stock(product))),
        } for product in products]
        return rows, total


def _load_product(product_id: int) -> dict | None:
    from handlers.products import _real_stock

    with SessionLocal() as db:
        product = db.query(Product).filter(
            Product.id == int(product_id),
            Product.is_active.is_(True),
            Product.price >= 0,
        ).first()
        if not product:
            return None
        since = datetime.utcnow() - timedelta(hours=24)
        recent_orders = int(db.query(func.count(Order.id)).filter(
            Order.product_id == int(product.id),
            Order.created_at >= since,
            Order.refunded.is_(False),
            Order.status.in_(("completed", "pending_info", "pending_manual", "preorder", "processing")),
        ).scalar() or 0)
        top_recent = db.query(
            Order.product_id,
            func.count(Order.id).label("order_count"),
        ).filter(
            Order.created_at >= since,
            Order.refunded.is_(False),
            Order.status.in_(("completed", "pending_info", "pending_manual", "preorder", "processing")),
        ).group_by(Order.product_id).order_by(
            func.count(Order.id).desc(), Order.product_id.asc()
        ).first()
        warranty_days = max(0, int(product.warranty_days or 0))
        delivery_type = str(product.delivery_type or "manual").casefold()
        delivery = {
            "automatic": "Automatic delivery",
            "hybrid": "Automatic delivery with manual fallback",
            "manual": "Manual delivery",
        }.get(delivery_type, "Manual delivery")
        return {
            "id": int(product.id),
            "name": str(product.name or "Product"),
            "price": Decimal(str(product.price or 0)),
            "stock": max(0, int(_real_stock(product))),
            "recent_orders": recent_orders,
            "is_trending": bool(top_recent and int(top_recent.product_id) == int(product.id) and int(top_recent.order_count) > 0),
            "delivery": delivery,
            "warranty": f"{warranty_days}-day warranty" if warranty_days else "No warranty specified",
        }


def _format_price(value: Decimal) -> str:
    return f"{value:.2f}"


def _format_stock(value: int) -> str:
    return "Available" if value >= 999999 else f"{value} in stock"


async def _public_product_link(bot, product_id: int) -> str:
    global _bot_username
    if not _bot_username:
        me = await bot.get_me()
        _bot_username = me.username or ""
    if not _bot_username:
        raise RuntimeError("The bot username is unavailable.")
    return f"https://t.me/{_bot_username}?start=product_{int(product_id)}"


def _campaign_text(template: str, product: dict) -> str:
    name = escape(product["name"])
    price = _format_price(product["price"])
    stock = int(product["stock"])
    demand = int(product["recent_orders"])
    if template == "trend":
        demand_text = f"{demand}+ orders placed in the last 24 hours" if demand >= 5 else f"{demand} orders placed in the last 24 hours"
        warranty = escape(product["warranty"])
        headline = "🏆 <b>TRENDING #1 RIGHT NOW!</b>" if product["is_trending"] else "✨ <b>FEATURED ON RAINDEALS</b>"
        return (
            "🔥🟥🟥\n\n"
            f"{headline}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>{name}</b>\n\n"
            f"📈 <b>Live Demand:</b> {demand_text}\n"
            f"💵 <b>Price:</b> ${price} USDT\n"
            f"✅ <b>Availability:</b> {_format_stock(stock)}\n"
            f"💯 <b>Delivery:</b> {escape(product['delivery'])}\n"
            f"🛡 <b>Coverage:</b> {warranty}\n\n"
            "🔥 <i>See the product details and current availability.</i>"
        )
    return (
        "👀 <b>STILL INTERESTED IN THIS TOOL?</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>{name}</b>\n\n"
        f"🔔 <b>Availability:</b> {_format_stock(stock)}\n"
        f"💵 <b>Price:</b> ${price} USDT\n"
        f"⚡ <b>Delivery:</b> {escape(product['delivery'])}\n\n"
        "⏰ <i>Check the listing for the latest stock before ordering.</i> 🔥"
    )


def _product_keyboard(products: list[dict], page: int, total: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=f"{item['name'][:34]} · {_format_stock(item['stock'])}",
        callback_data=f"admin_promote_product_{item['id']}_{page}",
    )] for item in products]
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅ Previous", callback_data=f"admin_promote_page_{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=f"admin_promote_page_{page}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="Next ➡", callback_data=f"admin_promote_page_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅ Admin panel", callback_data="admin_panel")])
    return _admin_markup(rows)


async def _show_product_page(callback: CallbackQuery, page: int) -> None:
    rows, total = await asyncio.to_thread(_load_product_page, page)
    page = max(0, min(int(page), max(0, (total - 1) // PAGE_SIZE)))
    text = (
        "📣 <b>PRODUCT PROMOTION</b>\n\n"
        "Choose an active product, preview a prepared campaign, then publish it to the Stock Group.\n"
        "Demand, price, stock, delivery mode, and warranty are read from current records."
    )
    if not rows:
        text += "\n\nNo active products are available to promote."
    await show(callback, text, parse_mode="HTML", reply_markup=_product_keyboard(rows, page, total))


@router.callback_query(F.data == "admin_promote")
@router.callback_query(F.data.regexp(r"^admin_promote_page_\d{1,6}$"))
async def promote_products(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Admin access only.", show_alert=True)
        return
    if callback.data == "admin_promote":
        await state.clear()
    page = 0
    if callback.data.startswith("admin_promote_page_"):
        try:
            page = int(callback.data.rsplit("_", 1)[1])
        except ValueError:
            page = 0
    await callback.answer()
    await _show_product_page(callback, page)


@router.callback_query(F.data.regexp(r"^admin_promote_product_\d{1,10}_\d{1,6}$"))
async def choose_promotion_product(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Admin access only.", show_alert=True)
        return
    try:
        _, _, _, raw_product_id, raw_page = callback.data.split("_")
        product_id, page = int(raw_product_id), int(raw_page)
    except (ValueError, AttributeError):
        await callback.answer("Product selection expired.", show_alert=True)
        return
    await callback.answer()
    product = await asyncio.to_thread(_load_product, product_id)
    if not product:
        await show(callback, "⚠️ This product is no longer active.", reply_markup=_admin_markup([
            [InlineKeyboardButton(text="⬅ Product list", callback_data=f"admin_promote_page_{page}")],
        ]))
        return
    text = (
        f"📣 <b>Promote {escape(product['name'])}</b>\n\n"
        f"Current price: <b>${_format_price(product['price'])} USDT</b>\n"
        f"Current stock: <b>{_format_stock(product['stock'])}</b>\n\n"
        "Choose a prepared message. Live figures are refreshed when you publish."
    )
    markup = _admin_markup([
        [InlineKeyboardButton(text="🔥 Trending #1", callback_data=f"admin_promote_template_trend_{product_id}_{page}")],
        [InlineKeyboardButton(text="👀 Still interested", callback_data=f"admin_promote_template_watch_{product_id}_{page}")],
        [InlineKeyboardButton(text="⬅ Product list", callback_data=f"admin_promote_page_{page}")],
    ])
    await show(callback, text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data.regexp(r"^admin_promote_template_(trend|watch)_\d{1,10}_\d{1,6}$"))
async def preview_promotion(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Admin access only.", show_alert=True)
        return
    try:
        _, _, _, template, raw_product_id, raw_page = callback.data.split("_")
        product_id, page = int(raw_product_id), int(raw_page)
    except (ValueError, AttributeError):
        await callback.answer("Promotion preview expired.", show_alert=True)
        return
    await callback.answer()
    product = await asyncio.to_thread(_load_product, product_id)
    if not product:
        await show(callback, "⚠️ This product is no longer active.", reply_markup=_admin_markup([
            [InlineKeyboardButton(text="⬅ Product list", callback_data=f"admin_promote_page_{page}")],
        ]))
        return
    text = _campaign_text(template, product)
    markup = _admin_markup([
        [InlineKeyboardButton(text="📣 Publish to Stock Group", callback_data=f"admin_promote_send_{template}_{product_id}_{page}")],
        [InlineKeyboardButton(text="⬅ Choose message", callback_data=f"admin_promote_product_{product_id}_{page}")],
        [InlineKeyboardButton(text="⬅ Product list", callback_data=f"admin_promote_page_{page}")],
    ])
    await show(callback, text + "\n\n<i>Preview · not posted yet.</i>", parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data.regexp(r"^admin_promote_send_(trend|watch)_\d{1,10}_\d{1,6}$"))
async def publish_promotion(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Admin access only.", show_alert=True)
        return
    try:
        _, _, _, template, raw_product_id, raw_page = callback.data.split("_")
        product_id, page = int(raw_product_id), int(raw_page)
    except (ValueError, AttributeError):
        await callback.answer("Promotion expired. Open Promote products again.", show_alert=True)
        return

    destination = str(getattr(config, "STOCK_GROUP_ID", "") or
                      getattr(config, "STOCK_NOTIFICATION_CHANNEL_ID", "") or "").strip()
    if not destination:
        await callback.answer("Stock Group is not configured.", show_alert=True)
        return
    product = await asyncio.to_thread(_load_product, product_id)
    if not product:
        await callback.answer("This product is no longer active.", show_alert=True)
        return
    if int(product["stock"]) <= 0:
        await callback.answer("This product is out of stock; promotion was not sent.", show_alert=True)
        return

    await callback.answer("Posting to the Stock Group…")
    try:
        link = await _public_product_link(callback.bot, product_id)
        message = await callback.bot.send_message(
            chat_id=destination,
            text=_campaign_text(template, product),
            parse_mode="HTML",
            reply_markup=_admin_markup([[
                InlineKeyboardButton(text="🛍 View product", url=link)
            ]]),
        )
    except Exception as exc:
        logger.exception("Admin product promotion failed product_id=%s", product_id)
        await show(callback, f"⚠️ Could not post to Stock Group: {escape(str(exc)[:220])}", parse_mode="HTML",
                    reply_markup=_admin_markup([
                        [InlineKeyboardButton(text="⬅ Preview", callback_data=f"admin_promote_template_{template}_{product_id}_{page}")],
                        [InlineKeyboardButton(text="⬅ Admin panel", callback_data="admin_panel")],
                    ]))
        return

    try:
        with transaction() as db:
            from models.admin_audit_log import AdminAuditLog
            db.add(AdminAuditLog(
                admin_telegram_id=int(callback.from_user.id),
                action="publish_product_promotion",
                entity_type="product",
                entity_id=str(product_id),
                summary=f"Published {template} promotion to {destination}; Telegram message {message.message_id}.",
            ))
    except Exception:
        logger.exception("Promotion sent but audit record failed product_id=%s", product_id)

    await show(
        callback,
        f"✅ <b>Promotion posted</b>\n\n{escape(product['name'])} was sent to the Stock Group.",
        parse_mode="HTML",
        reply_markup=_admin_markup([
            [InlineKeyboardButton(text="📣 Promote another product", callback_data="admin_promote")],
            [InlineKeyboardButton(text="⬅ Admin panel", callback_data="admin_panel")],
        ]),
    )
