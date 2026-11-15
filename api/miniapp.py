"""Telegram Mini App API for the RainDeals customer storefront.

Every route authenticates Telegram.WebApp.initData server-side and scopes
records to the signed-in Telegram user. Never trust the UI's user ID.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc

from config import API_BASE_URL, BOT_TOKEN, MAIN_BOT_USERNAME
from database import SessionLocal
from models.api_key import ApiKey
from models.order import Order
from models.product import Product
from models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/miniapp/api", tags=["RainDeals Mini App"])


def _auth_error():
    return HTTPException(status_code=401, detail={"error": "telegram_auth_required", "message": "Open this Mini App from the RainDeals Telegram bot."})


def _verify_init_data(init_data: str) -> dict:
    if not BOT_TOKEN or not init_data or len(init_data) > 16_384:
        raise _auth_error()
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError("duplicate fields")
        received_hash = data.pop("hash", "")
        if len(received_hash) != 64:
            raise ValueError("missing hash")
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash.lower()):
            raise ValueError("invalid signature")
        auth_date = int(data.get("auth_date", "0"))
        now = int(time.time())
        if auth_date <= 0 or auth_date > now + 30 or now - auth_date > 86_400:
            raise ValueError("expired init data")
        telegram_user = json.loads(data.get("user", "{}"))
        if not isinstance(telegram_user, dict):
            raise ValueError("invalid user")
        telegram_id = int(telegram_user.get("id", 0))
        if telegram_id <= 0:
            raise ValueError("missing user")
        return {"id": telegram_id, "telegram_user": telegram_user}
    except (ValueError, TypeError, json.JSONDecodeError):
        raise _auth_error()


def current_user(init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")) -> dict:
    auth = _verify_init_data(init_data or "")
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_id == auth["id"]).first()
        if not user or user.is_banned:
            raise HTTPException(status_code=403, detail={"error": "account_unavailable", "message": "Send /start to the RainDeals bot to activate your account."})
        return {"id": int(user.telegram_id), "telegram_user": auth["telegram_user"]}


def _money(value) -> str:
    amount = Decimal(str(value or 0))
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _requirements(product: Product) -> list[dict[str, str]]:
    raw = product.required_info if isinstance(product.required_info, list) else []
    result = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "text").strip().lower()
        label = str(item.get("label") or kind.title()).strip()[:100]
        if kind in {"email", "phone", "text", "photo", "contact"} and label:
            result.append({"type": kind, "label": label})
    return result


def _product_rows(user_id: int, freebies: bool = False) -> list[dict]:
    from handlers.products import (
        _active_custom_price, _get_bulk_price, _get_max_qty, _money as shop_money,
        _real_stock,
        _product_purchase_configuration_errors,
    )
    from services.fulfillment_policy import manual_allowed as _manual_allowed
    with SessionLocal() as db:
        query = db.query(Product).filter(Product.is_active.is_(True))
        query = query.filter(Product.price == 0) if freebies else query.filter(Product.price > 0)
        products = query.order_by(Product.id.desc()).limit(1000).all()
        output = []
        for product in products:
            try:
                if _product_purchase_configuration_errors(product):
                    continue
                # Keep real/current capacity separate from an allowed
                # preorder. _get_max_qty intentionally returns a large
                # preorder ceiling, which must never be presented as stock.
                available = max(0, int(_real_stock(product)))
                sellable = max(0, int(_get_max_qty(product)))
                is_preorder = bool(
                    product.preorder
                    and product.source != "reseller"
                    and available <= 0
                    and _manual_allowed(product)
                )
                custom_price = _active_custom_price(db, user_id, product.id)
                price = custom_price if custom_price is not None else shop_money(product.price)
                bulk = _get_bulk_price(product.bulk_pricing, 1) if custom_price is None and product.bulk_pricing else None
                if bulk is not None:
                    price = shop_money(bulk)
                output.append({
                    "id": int(product.id), "name": str(product.name or "Product")[:255],
                    "icon": str(product.icon or "📦")[:50],
                    "description": str(product.description or "")[:1200],
                    "category": (str(product.category or "General").strip() or "General")[:100],
                    "price": _money(price), "currency": "USDT", "stock": available,
                    "sellable_stock": sellable, "in_stock": available > 0,
                    "purchasable": sellable > 0,
                    "preorder": is_preorder,
                    "delivery_type": str(product.delivery_type or "manual"),
                    "required_info": _requirements(product),
                })
            except Exception:
                logger.exception("Mini App product omitted due to invalid catalog configuration product_id=%s", product.id)
        return output


def _cart_snapshot(user_id: int) -> dict:
    from services import cart
    lines, busy = cart.view(user_id)
    return {"items": [{
        "product_id": int(line["product_id"]), "name": str(line["name"]),
        "quantity": int(line["quantity"]), "total": str(line["total"]),
        "error": line.get("error"), "required_info": line.get("required_info", []),
    } for line in lines], "checkout_busy": bool(busy)}


class CartChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0)
    quantity: int = Field(ge=0, le=1000)


class CheckoutPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    buyer_info: dict[str, dict[str, str]] = Field(default_factory=dict, max_length=20)

    @field_validator("buyer_info")
    @classmethod
    def bound_buyer_info(cls, value):
        total = 0
        for product_id, fields in value.items():
            if not str(product_id).isdigit() or len(fields) > 8:
                raise ValueError("Invalid product buyer-information payload.")
            for label, answer in fields.items():
                if not label.strip() or len(label) > 100 or len(answer) > 3000:
                    raise ValueError("Buyer-information values are too long.")
                total += len(label.encode("utf-8")) + len(answer.encode("utf-8"))
        if total > 12_000:
            raise ValueError("Buyer information exceeds the allowed size.")
        return value


@router.get("/bootstrap")
def bootstrap(request: Request, auth=Depends(current_user)):
    from services import notification_inbox
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_id == auth["id"]).first()
        key_count = db.query(ApiKey.id).filter(ApiKey.telegram_id == auth["id"], ApiKey.is_active.is_(True)).count()
        return {
            "profile": {"telegram_id": int(user.telegram_id), "name": user.full_name or "RainDeals customer",
                        "username": user.username, "balance": _money(user.balance),
                        "spent": _money(user.total_spent), "orders": int(user.total_orders or 0)},
            "unread_notifications": int(notification_inbox.count(auth["id"])),
            "active_api_keys": int(key_count), "api_base_url": API_BASE_URL,
            "main_bot_username": getattr(request.app.state, "main_bot_username", MAIN_BOT_USERNAME),
        }


@router.get("/products")
def products(freebies: bool = False, auth=Depends(current_user)):
    rows = _product_rows(auth["id"], freebies)
    return {"products": rows, "categories": sorted({row["category"] for row in rows}, key=str.casefold)}


@router.get("/cart")
def get_cart(auth=Depends(current_user)):
    return _cart_snapshot(auth["id"])


@router.post("/cart")
def change_cart(payload: CartChange, auth=Depends(current_user)):
    from services import cart
    try:
        cart.change(auth["id"], payload.product_id, payload.quantity)
        return _cart_snapshot(auth["id"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/cart")
def clear_cart(auth=Depends(current_user)):
    from services import cart
    try:
        cart.clear(auth["id"])
        return _cart_snapshot(auth["id"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def _validate_buyer_info(user_id: int, payload: dict[str, dict[str, str]]) -> None:
    from services.input_forms import InputError, api_requirement_values
    from services import cart
    lines, _ = cart.view(user_id)
    with SessionLocal() as db:
        for line in lines:
            product = db.get(Product, int(line["product_id"]))
            requirements = _requirements(product) if product else []
            values = payload.get(str(line["product_id"]), {})
            try:
                api_requirement_values(requirements, values)
            except InputError as exc:
                raise HTTPException(status_code=422, detail={"error": "buyer_info_invalid", "product_id": line["product_id"], "message": str(exc)})


@router.post("/checkout")
async def checkout(payload: CheckoutPayload, request: Request, auth=Depends(current_user)):
    import asyncio
    from services import cart
    try:
        await asyncio.to_thread(_validate_buyer_info, auth["id"], payload.buyer_info)
        token, lines, total = await asyncio.to_thread(cart.prepare, auth["id"])
        bot = getattr(request.app.state, "main_bot", None)
        if bot is None:
            raise HTTPException(status_code=503, detail="Checkout is temporarily unavailable. Try again in a moment.")
        result = await cart.checkout(auth["id"], token, bot.id, payload.buyer_info)
        return {"checkout": result, "total": _money(total), "items_reviewed": len(lines)}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        logger.exception("Mini App checkout failed for telegram_id=%s", auth["id"])
        raise HTTPException(status_code=503, detail="Checkout result is being checked. Open My Orders before trying again.")


@router.get("/orders")
def orders(auth=Depends(current_user)):
    with SessionLocal() as db:
        rows = db.query(Order).filter(Order.telegram_id == auth["id"]).order_by(desc(Order.id)).limit(50).all()
        return {"orders": [{"id": int(row.id), "product_name": row.product_name,
                            "quantity": int(row.quantity or 1), "amount": _money(row.amount),
                            "status": row.status, "created_at": row.created_at.isoformat() if row.created_at else None,
                            "refunded": bool(row.refunded)} for row in rows]}


@router.get("/orders/{order_id}")
def order_detail(order_id: int, auth=Depends(current_user)):
    with SessionLocal() as db:
        row = db.query(Order).filter(Order.id == order_id, Order.telegram_id == auth["id"]).first()
        if not row:
            raise HTTPException(status_code=404, detail="Order not found.")
        return {"id": int(row.id), "product_name": row.product_name, "quantity": int(row.quantity or 1),
                "amount": _money(row.amount), "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "refunded": bool(row.refunded)}


@router.get("/notifications")
def notifications(page: int = 0, auth=Depends(current_user)):
    from services.notification_inbox import listing
    return listing(auth["id"], page=max(0, min(page, 100000)))


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, auth=Depends(current_user)):
    from services.notification_inbox import read
    try:
        return read(auth["id"], notification_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Notification not found.")


@router.get("/wallet")
def wallet(page: int = 0, auth=Depends(current_user)):
    from services.customer_tools import wallet_statement
    balance, rows, more = wallet_statement(auth["id"], max(0, min(page, 100000)))
    return {"balance": _money(balance), "has_more": bool(more), "entries": [{
        "label": row["label"], "amount": _money(row["amount"]),
        "balance": _money(row["balance"]), "created_at": row["date"].isoformat() if row.get("date") else None,
    } for row in rows]}
