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
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, func

import config
from config import BOT_TOKEN, MAIN_BOT_USERNAME
from database import SessionLocal
from models.deposit import Deposit
from models.order import Order
from models.product import Product
from models.user import User
from models.product_engagement import ProductEngagement
from models.customer_tools import MiniAppRecentProduct
from models.order_workflow import OrderEvent
from models.order_requirement_submission import OrderRequirementSubmission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/miniapp/api", tags=["RainDeals Mini App"])


def _auth_error():
    return HTTPException(status_code=401, detail={"error": "telegram_auth_required", "message": "Open this Mini App from the RainDeals Telegram bot."})


def _require_feature(name: str, message: str) -> None:
    from services.runtime_settings import setting_bool
    if not setting_bool(f"feature_{name}", True):
        raise HTTPException(status_code=503, detail={"error": "feature_unavailable", "message": message})


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
        return {"id": telegram_id, "telegram_user": telegram_user, "auth_date": auth_date}
    except (ValueError, TypeError, json.JSONDecodeError):
        raise _auth_error()


def current_user(
    request: Request,
    init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
) -> dict:
    auth = _verify_init_data(init_data or "")
    from services.runtime_settings import setting_bool, get_setting
    if not setting_bool("miniapp_enabled", True):
        message = get_setting("miniapp_disabled_message", "The RainDeals shop is temporarily unavailable. Please use the bot or try again later.")
        raise HTTPException(status_code=503, detail={"error": "miniapp_disabled", "message": message})
    with SessionLocal() as db:
        # Most Mini App requests only need a read. Avoid taking a database row
        # lock for every asset/API request; acquire it only for the one-time
        # verification write so concurrent first opens remain serialized.
        user = db.query(User).filter(User.telegram_id == auth["id"]).first()
        if not user or user.is_banned:
            raise HTTPException(status_code=403, detail={"error": "account_unavailable", "message": "Send /start to the RainDeals bot to activate your account."})
        newly_verified = False
        # Telegram signed initData proves the account identity. Record it only
        # once, along with a keyed network fingerprint for referral abuse
        # checks. No raw IP or forwarded header is persisted.
        if user.miniapp_verified_at is None:
            user = db.query(User).filter(
                User.telegram_id == auth["id"]
            ).with_for_update().first()
            if not user or user.is_banned:
                raise HTTPException(status_code=403, detail={"error": "account_unavailable", "message": "Send /start to the RainDeals bot to activate your account."})
            newly_verified = user.miniapp_verified_at is None
        if newly_verified:
            # Serialize first-time referrals from the same inviter while their
            # fingerprints are recorded. Without this parent-row lock, two
            # concurrent Mini App opens can miss each other's network hash.
            if user.referred_by:
                db.query(User.id).filter(
                    User.telegram_id == int(user.referred_by)
                ).with_for_update().first()
            if user.referred_by and user.miniapp_verification_prompted_at is None:
                user.miniapp_verification_prompted_at = datetime.utcnow()
            user.miniapp_verified_at = datetime.utcnow()
            from services.freebie_referrals import referral_ip_fingerprint
            user.miniapp_ip_hash = referral_ip_fingerprint(request)
            db.commit()
            try:
                from services.freebie_referrals import (
                    notify_claimant_verification,
                    notify_verified_referral,
                )
                with SessionLocal() as notification_db:
                    verified_user = notification_db.query(User).filter(
                        User.telegram_id == auth["id"]
                    ).first()
                    if verified_user:
                        if verified_user.referred_by or int(verified_user.total_referrals or 0) > 0:
                            notify_claimant_verification(notification_db, verified_user)
                        notify_verified_referral(notification_db, verified_user)
                        if verified_user.miniapp_ip_hash:
                            # If an inviter verifies after their invitees, run
                            # the same-network check again now that both sides
                            # have a server-observed fingerprint. This can
                            # withdraw an earlier provisional count and queue
                            # the proper user/admin review update.
                            matching_referrals = notification_db.query(User).filter(
                                User.referred_by == int(verified_user.telegram_id),
                                User.telegram_id != int(verified_user.telegram_id),
                                User.miniapp_verified_at.isnot(None),
                                User.is_banned.is_(False),
                                User.miniapp_referral_review_approved_at.is_(None),
                                User.miniapp_referral_review_rejected_at.is_(None),
                                User.miniapp_ip_hash == verified_user.miniapp_ip_hash,
                            ).all()
                            for matching_referral in matching_referrals:
                                notify_verified_referral(notification_db, matching_referral)
                        notification_db.commit()
            except Exception:
                logger.exception("Could not record Mini App referral verification for user_id=%s", auth["id"])
        bootstrap_profile = None
        if request.url.path.rstrip("/").endswith("/bootstrap"):
            bootstrap_profile = {
                "telegram_id": int(user.telegram_id), "name": user.full_name or "RainDeals customer",
                "username": user.username, "balance": user.balance,
                "spent": user.total_spent, "deposited": user.total_deposited,
                "orders": int(user.total_orders or 0),
                "referral_earnings": user.referral_earnings,
                "referral_code": str(user.referral_code or "").strip(),
            }
        return {
            "id": int(user.telegram_id),
            "telegram_user": auth["telegram_user"],
            "newly_verified": newly_verified,
            "referred": bool(user.referred_by),
            "bootstrap_profile": bootstrap_profile,
        }


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


def _serialize_product_rows(
    user_id: int,
    products: list[Product],
    *,
    claimant_verified: bool,
    verified_referrals: int = 0,
    include_details: bool = False,
) -> list[dict]:
    from handlers.products import (
        _get_bulk_price, _get_max_qty, _get_catalog_custom_prices, _money as shop_money,
        _real_stock,
        _product_purchase_configuration_errors,
    )
    from services.fulfillment_policy import manual_allowed as _manual_allowed
    from models.product_engagement import ProductEngagement
    from models.order import Order
    from services.runtime_settings import json_list_setting, payment_value
    with SessionLocal() as db:
        product_ids = [int(p.id) for p in products]
        engagements = db.query(ProductEngagement.product_id, ProductEngagement.is_favorite,
                                ProductEngagement.restock_enabled).filter(
            ProductEngagement.telegram_id == int(user_id),
            ProductEngagement.product_id.in_(product_ids or [-1]),
        ).all()
        favorite_ids = {int(row.product_id) for row in engagements if row.is_favorite}
        restock_ids = {int(row.product_id) for row in engagements if row.restock_enabled}
        try:
            daily_limit = max(0, min(1000, int(payment_value('freebie_claim_limit_daily', '0') or 0)))
        except ValueError:
            daily_limit = 0
        daily_claims = 0
        if daily_limit:
            from datetime import timedelta
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            daily_claims = int(db.query(func.count(Order.id)).filter(
                Order.telegram_id == int(user_id), Order.amount <= 0, Order.refunded.is_(False),
                Order.created_at >= today, Order.created_at < today + timedelta(days=1),
            ).scalar() or 0)
    promotions = {}
    now = datetime.now(timezone.utc)
    for promotion in json_list_setting('product_promotions_json'):
        if not isinstance(promotion, dict) or not promotion.get('active', True):
            continue
        try:
            start = datetime.fromisoformat(str(promotion.get('starts_at', '')).replace('Z', '+00:00'))
            end = datetime.fromisoformat(str(promotion.get('ends_at', '')).replace('Z', '+00:00'))
            start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
            end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end.astimezone(timezone.utc)
            if start <= now < end:
                promotions[int(promotion['product_id'])] = str(promotion.get('label') or '')[:80]
        except (ValueError, TypeError, KeyError):
            continue
    custom_prices = _get_catalog_custom_prices(user_id, [int(p.id) for p in products])
    output = []
    for product in products:
        try:
            if _product_purchase_configuration_errors(product):
                continue
            # Keep real/current capacity separate from an allowed preorder.
            available = max(0, int(_real_stock(product)))
            sellable = max(0, int(_get_max_qty(product)))
            is_preorder = bool(
                product.preorder
                and product.source != "reseller"
                and available <= 0
                and _manual_allowed(product)
            )
            custom_price = custom_prices.get(int(product.id))
            price = custom_price if custom_price is not None else shop_money(product.price)
            bulk = _get_bulk_price(product.bulk_pricing, 1) if custom_price is None and product.bulk_pricing else None
            if bulk is not None:
                price = shop_money(bulk)
            referral_requirement = (
                max(0, int(getattr(product, "freebie_referrals_required", 0) or 0))
                if Decimal(str(product.price or 0)) == 0 else 0
            )
            freebie_unlocked = (
                referral_requirement == 0
                or (claimant_verified and verified_referrals >= referral_requirement)
            )
            limit_reached = bool(daily_limit and daily_claims >= daily_limit and Decimal(str(product.price or 0)) == 0)
            if limit_reached:
                freebie_unlocked = False
            description = getattr(product, "_catalog_description", None)
            if description is None:
                description = str(product.description or "")
            output.append({
                "id": int(product.id), "name": str(product.name or "Product")[:255],
                "icon": str(product.icon or "📦")[:50],
                "description": description[:1200 if include_details else 350],
                "description_truncated": bool(not include_details and len(description) > 350),
                "category": (str(product.category or "General").strip() or "General")[:100],
                "price": _money(price), "currency": "USDT", "stock": available,
                "sellable_stock": sellable, "in_stock": available > 0,
                "purchasable": sellable > 0 and freebie_unlocked,
                "preorder": is_preorder,
                "delivery_type": str(product.delivery_type or "manual"),
                "required_info": _requirements(product),
                "freebie_referrals_required": referral_requirement,
                "verified_referrals": verified_referrals if referral_requirement else 0,
                "claimant_miniapp_verified": claimant_verified if referral_requirement else True,
                "freebie_unlocked": freebie_unlocked,
                "freebie_limit_reached": limit_reached,
                "freebie_daily_limit": daily_limit,
                "freebie_daily_claims": daily_claims,
                "is_favorite": int(product.id) in favorite_ids,
                "restock_subscribed": int(product.id) in restock_ids,
                "promotion": promotions.get(int(product.id), ""),
            })
        except Exception:
            logger.exception("Mini App product omitted due to invalid catalog configuration product_id=%s", product.id)
    return output


def _product_rows(user_id: int, freebies: bool = False, *, product_id: int | None = None, include_details: bool = False) -> list[dict]:
    with SessionLocal() as db:
        query = db.query(Product).filter(Product.is_active.is_(True))
        if product_id is not None:
            query = query.filter(Product.id == int(product_id))
        else:
            query = query.filter(Product.price == 0) if freebies else query.filter(Product.price > 0)
        products = query.order_by(Product.id.desc()).limit(1 if product_id is not None else 1000).all()
        claimant = db.query(User).filter(User.telegram_id == int(user_id)).first()
        claimant_verified = bool(claimant and claimant.miniapp_verified_at)
        has_gated_freebies = any(
            Decimal(str(p.price or 0)) == 0
            and int(getattr(p, "freebie_referrals_required", 0) or 0) > 0
            for p in products
        )
        verified_referrals = 0
        if has_gated_freebies:
            from services.freebie_referrals import verified_referral_count
            verified_referrals = verified_referral_count(db, user_id)
        return _serialize_product_rows(
            user_id, products, claimant_verified=claimant_verified,
            verified_referrals=verified_referrals, include_details=include_details,
        )


async def _cached_product_rows(user_id: int, freebies: bool) -> list[dict]:
    """Build the Mini App catalog from the bot's credential-free warm cache."""
    from handlers.products import _fetch_catalog_snapshot, _money as shop_money
    products = await _fetch_catalog_snapshot()
    return await asyncio.to_thread(_serialize_cached_product_rows, user_id, freebies, products, shop_money)


def _serialize_cached_product_rows(user_id: int, freebies: bool, snapshot: list[Product], shop_money) -> list[dict]:
    """Run SQL/custom-rate lookups and catalog serialization off the event loop."""
    wanted_free = bool(freebies)
    products = [
        product for product in reversed(snapshot)
        if (shop_money(product.price) == 0) == wanted_free
    ][:1000]
    with SessionLocal() as db:
        claimant = db.query(User).filter(User.telegram_id == int(user_id)).first()
        claimant_verified = bool(claimant and claimant.miniapp_verified_at)
        has_gated_freebies = any(
            Decimal(str(p.price or 0)) == 0
            and int(getattr(p, "freebie_referrals_required", 0) or 0) > 0
            for p in products
        )
        verified_referrals = 0
        if has_gated_freebies:
            from services.freebie_referrals import verified_referral_count
            verified_referrals = verified_referral_count(db, user_id)
        return _serialize_product_rows(
            user_id, products, claimant_verified=claimant_verified,
            verified_referrals=verified_referrals,
        )


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


class DepositCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network: str = Field(pattern=r"^(BEP20|POLYGON|BINANCE|UPI|STARS)$")
    stars_amount: int | None = Field(default=None, ge=1, le=100_000)


class DepositReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str = Field(min_length=8, max_length=255)


class CancellationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=1000)


class PromocodePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=32)


_DEPOSIT_LABELS = {
    "BEP20": ("🟡", "USDT · BEP20 (BSC)", "Transaction Hash (TXID)"),
    "POLYGON": ("🟣", "USDT · Polygon", "Transaction Hash (TXID)"),
    "BINANCE": ("🔴", "Binance Pay", "Binance Pay Order ID"),
    "UPI": ("🟢", "UPI · INR", "UTR / Transaction ID"),
    "STARS": ("⭐", "Telegram Stars", "Automatic Telegram confirmation"),
}


def _deposit_method_rows() -> list[dict]:
    from services.stars_payments import stars_rate
    from services.stars_payments import stars_limits
    from services.runtime_settings import payment_limits, payment_method_enabled, payment_method_order, payment_value

    stars_exchange_rate = stars_rate()
    destinations = {
        "BEP20": payment_value("payment_bep20_address", str(getattr(config, "BEP20_ADDRESS", "") or "")),
        "POLYGON": payment_value("payment_polygon_address", str(getattr(config, "POLYGON_ADDRESS", "") or "")),
        "BINANCE": payment_value("payment_binance_pay_id", str(getattr(config, "BINANCE_PAY_ID", "") or "")),
        "UPI": payment_value("payment_upi_id", str(getattr(config, "UPI_ID", "") or "")),
    }
    configured = {
        "BEP20": bool(destinations["BEP20"]),
        "POLYGON": bool(destinations["POLYGON"]),
        "BINANCE": bool(destinations["BINANCE"]),
        "UPI": bool(destinations["UPI"]),
        "STARS": bool(BOT_TOKEN and stars_exchange_rate is not None),
    }
    methods = []
    for key in payment_method_order():
        if key not in _DEPOSIT_LABELS:
            continue
        minimum, maximum = payment_limits(key)
        stars_min, stars_max = stars_limits() if key == "STARS" else (None, None)
        methods.append({
            "network": key, "icon": _DEPOSIT_LABELS[key][0], "label": _DEPOSIT_LABELS[key][1],
            "reference_label": _DEPOSIT_LABELS[key][2],
            "configured": configured[key] and payment_method_enabled(key),
            "minimum": str(minimum), "maximum": str(maximum),
            "stars_minimum": stars_min, "stars_maximum": stars_max,
            "usdt_per_star": str(stars_exchange_rate) if key == "STARS" and stars_exchange_rate else None,
        })
    return methods


def _deposit_view(row: Deposit) -> dict:
    from services.runtime_settings import payment_value
    network = str(row.network or "").upper()
    label = _DEPOSIT_LABELS.get(network, ("💳", network or "Payment", "Reference"))
    expiry = row.invoice_expires_at
    # Database invoice timestamps are naive UTC values; subtract UTC datetimes
    # directly so the remaining time is independent of the host timezone.
    if expiry:
        now = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.utcnow()
        remaining = max(0, int((expiry - now).total_seconds()))
    else:
        remaining = 0
    can_pay = bool(row.status == "waiting_hash" and not row.tx_hash and remaining > 0)
    payment = {"address": "", "instructions": ""}
    if can_pay:
        if network == "STARS":
            payment = {
                "invoice_url": str(row.stars_invoice_url or ""),
                "instructions": "Open Telegram's secure Stars checkout. Your wallet is credited after Telegram confirms payment.",
            }
        elif network == "BEP20":
            payment["address"] = payment_value("payment_bep20_address", str(getattr(config, "BEP20_ADDRESS", "") or ""))
            payment["instructions"] = "Send USDT on BNB Smart Chain (BEP20), then submit the transaction hash."
        elif network == "POLYGON":
            payment["address"] = payment_value("payment_polygon_address", str(getattr(config, "POLYGON_ADDRESS", "") or ""))
            payment["instructions"] = "Send USDT on Polygon, then submit the transaction hash."
        elif network == "BINANCE":
            payment["address"] = payment_value("payment_binance_pay_id", str(getattr(config, "BINANCE_PAY_ID", "") or ""))
            payment["instructions"] = "Pay using Binance Pay, then submit the Order ID from the receipt."
        elif network == "UPI":
            payment["address"] = payment_value("payment_upi_id", str(getattr(config, "UPI_ID", "") or ""))
            payment["instructions"] = "Pay using UPI, then submit the UTR or transaction reference."
    return {
        "id": int(row.id), "invoice": str(row.invoice_code or row.order_id or ""),
        "network": network, "network_label": label[1], "icon": label[0],
        "reference_label": label[2], "status": str(row.status or "unknown"),
        "amount": _money(row.amount), "received_amount": _money(row.received_amount),
        "stars_amount": int(row.stars_amount_requested or 0) if network == "STARS" else None,
        "stars_amount_paid": int(row.stars_amount_paid or 0) if network == "STARS" else None,
        "stars_wallet_credit": _money(row.received_amount or row.invoice_usdt_amount or 0) if network == "STARS" else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": expiry.isoformat() if expiry else None, "remaining_seconds": remaining,
        "payment": payment if can_pay else None,
        "reference_submitted": bool(row.tx_hash),
    }


def _owned_deposit(user_id: int, deposit_id: int) -> Deposit | None:
    with SessionLocal() as db:
        row = db.query(Deposit).filter(Deposit.id == deposit_id, Deposit.telegram_id == user_id).first()
        if not row:
            return None
        # Detach scalar fields needed by the API serializer before closing DB.
        db.expunge(row)
        return row


@router.get("/bootstrap")
def bootstrap(request: Request, auth=Depends(current_user)):
    from services import notification_inbox
    from services.runtime_settings import setting_bool
    user = auth.get("bootstrap_profile")
    if not user:
        raise HTTPException(status_code=403, detail="Could not load the RainDeals profile.")
    username = getattr(request.app.state, "main_bot_username", MAIN_BOT_USERNAME)
    referral_code = user["referral_code"]
    referral_enabled = setting_bool("feature_referrals", True)
    referral_link = f"https://t.me/{username}?start=ref_{referral_code}" if referral_enabled and username and referral_code else ""
    return {
        "profile": {"telegram_id": user["telegram_id"], "name": user["name"],
                    "username": user["username"], "balance": _money(user["balance"]),
                    "spent": _money(user["spent"]), "deposited": _money(user["deposited"]),
                    "orders": user["orders"],
                    # Referral aggregates are loaded only if the customer
                    # opens Profile; they are not needed to paint the shop.
                    "referrals": 0, "verified_referrals": 0,
                    "referral_earnings": _money(user["referral_earnings"]), "referral_link": referral_link},
        "unread_notifications": int(notification_inbox.count(auth["id"])),
        "main_bot_username": username,
        "deposit_methods": _deposit_method_rows() if setting_bool("feature_deposits", True) else [],
        "verification": {
            "just_completed": bool(auth.get("newly_verified")),
            "referred": bool(auth.get("referred")),
        },
        "features": {
            "cart": setting_bool("feature_cart", True),
            "freebies": setting_bool("feature_freebies", True),
            "referrals": setting_bool("feature_referrals", True),
            "vendor_offers": setting_bool("feature_vendor_offers", True),
            "deposits": setting_bool("feature_deposits", True),
        },
    }


@router.get("/profile")
def profile(request: Request, auth=Depends(current_user)):
    from services.freebie_referrals import verified_referral_count
    from services.runtime_settings import setting_bool
    referrals_enabled = setting_bool("feature_referrals", True)
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_id == auth["id"]).first()
        if not user:
            raise HTTPException(status_code=404, detail="RainDeals account not found.")
        username = getattr(request.app.state, "main_bot_username", MAIN_BOT_USERNAME)
        return {
            "telegram_id": int(user.telegram_id), "name": user.full_name or "RainDeals customer",
            "username": user.username, "balance": _money(user.balance),
            "spent": _money(user.total_spent), "deposited": _money(user.total_deposited),
            "orders": int(user.total_orders or 0),
            "referrals": int(db.query(func.count(User.id)).filter(
                User.referred_by == int(user.telegram_id)
            ).scalar() or 0) if referrals_enabled else 0,
            "verified_referrals": verified_referral_count(db, auth["id"]) if referrals_enabled else 0,
            "referral_earnings": _money(user.referral_earnings),
            "referral_link": (
                f"https://t.me/{username}?start=ref_{str(user.referral_code or '').strip()}"
                if referrals_enabled and username and user.referral_code else ""
            ),
        }


@router.get("/products")
async def products(freebies: bool = False, auth=Depends(current_user)):
    if freebies:
        _require_feature("freebies", "Freebies are temporarily unavailable.")
    try:
        rows = await _cached_product_rows(auth["id"], freebies)
    except Exception:
        logger.exception("Mini App cached catalog build failed; using direct catalog query")
        rows = await asyncio.to_thread(_product_rows, auth["id"], freebies)
    return {"products": rows, "categories": sorted({row["category"] for row in rows}, key=str.casefold)}


@router.get("/products/{product_id}")
def product_detail(product_id: int, auth=Depends(current_user)):
    rows = _product_rows(auth["id"], product_id=product_id, include_details=True)
    if not rows:
        raise HTTPException(status_code=404, detail="Product is unavailable.")
    if Decimal(str(rows[0].get("price") or 0)) == 0:
        _require_feature("freebies", "Freebies are temporarily unavailable.")
    _record_recent_view(auth["id"], product_id)
    rows[0]["recommendations"] = _recommendations(auth["id"], product_id)
    return {"product": rows[0]}


def _record_recent_view(user_id: int, product_id: int) -> None:
    from database import transaction
    with transaction() as db:
        if not db.query(Product.id).filter(Product.id == int(product_id), Product.is_active.is_(True)).first():
            return
        row = db.query(MiniAppRecentProduct).filter_by(
            telegram_id=int(user_id), product_id=int(product_id)
        ).with_for_update().first()
        if row:
            row.viewed_at = datetime.utcnow()
        else:
            db.add(MiniAppRecentProduct(telegram_id=int(user_id), product_id=int(product_id), viewed_at=datetime.utcnow()))
        # Keep only the most recent 30 product visits per account.
        old_ids = [item[0] for item in db.query(MiniAppRecentProduct.id).filter_by(
            telegram_id=int(user_id)
        ).order_by(MiniAppRecentProduct.viewed_at.desc(), MiniAppRecentProduct.id.desc()).offset(30).all()]
        if old_ids:
            db.query(MiniAppRecentProduct).filter(MiniAppRecentProduct.id.in_(old_ids)).delete(synchronize_session=False)


def _recommendations(user_id: int, product_id: int, limit: int = 6) -> list[dict]:
    with SessionLocal() as db:
        source = db.get(Product, int(product_id))
        if not source:
            return []
        purchased = db.query(Order.product_id, func.sum(Order.quantity).label('units')).filter(
            Order.status == 'completed', Order.refunded.is_(False), Order.product_id.isnot(None)
        ).group_by(Order.product_id).subquery()
        query = db.query(Product).outerjoin(purchased, purchased.c.product_id == Product.id).filter(
            Product.is_active.is_(True), Product.price > 0, Product.id != int(product_id)
        )
        if source.category:
            query = query.filter(Product.category == source.category)
        rows = query.order_by(purchased.c.units.desc(), Product.id.desc()).limit(limit * 2).all()
    return _serialize_product_rows(user_id, rows, claimant_verified=True)[:limit]


@router.get("/favorites")
def favorites(auth=Depends(current_user)):
    with SessionLocal() as db:
        ids = [int(row[0]) for row in db.query(ProductEngagement.product_id).filter_by(
            telegram_id=auth['id'], is_favorite=True
        ).order_by(ProductEngagement.created_at.desc()).limit(100).all()]
    rows = _product_rows(auth['id']) + _product_rows(auth['id'], freebies=True)
    products = {row['id']: row for row in rows}
    return {"products": [products[product_id] for product_id in ids if product_id in products]}


@router.post("/favorites/{product_id}")
def toggle_favorite(product_id: int, auth=Depends(current_user)):
    from database import transaction
    with transaction() as db:
        if not db.query(Product.id).filter(Product.id == product_id, Product.is_active.is_(True)).first():
            raise HTTPException(status_code=404, detail="Product is unavailable.")
        row = db.query(ProductEngagement).filter_by(
            telegram_id=auth['id'], product_id=product_id
        ).with_for_update().first()
        if not row:
            row = ProductEngagement(telegram_id=auth['id'], product_id=product_id, is_favorite=True)
            db.add(row)
            active = True
        else:
            row.is_favorite = not bool(row.is_favorite)
            active = bool(row.is_favorite)
            if not row.is_favorite and not row.restock_enabled:
                db.delete(row)
    return {"product_id": product_id, "is_favorite": active}


@router.get("/recent")
def recent_products(auth=Depends(current_user)):
    with SessionLocal() as db:
        ids = [int(row[0]) for row in db.query(MiniAppRecentProduct.product_id).filter_by(
            telegram_id=auth['id']
        ).order_by(MiniAppRecentProduct.viewed_at.desc()).limit(30).all()]
    rows = _product_rows(auth['id']) + _product_rows(auth['id'], freebies=True)
    products = {row['id']: row for row in rows}
    return {"products": [products[product_id] for product_id in ids if product_id in products]}


@router.get("/referrals")
def referral_activity(auth=Depends(current_user)):
    _require_feature("referrals", "Referrals are temporarily unavailable.")
    with SessionLocal() as db:
        inviter = db.query(User.miniapp_ip_hash).filter(User.telegram_id == int(auth['id'])).first()
        inviter_network = inviter[0] if inviter else None
        users = db.query(User).filter(User.referred_by == int(auth['id'])).order_by(
            User.id.desc()
        ).limit(100).all()
        network_counts = {}
        if users:
            network_counts = {str(network): int(count) for network, count in db.query(
                User.miniapp_ip_hash, func.count(User.id)
            ).filter(
                User.referred_by == int(auth['id']),
                User.miniapp_verified_at.isnot(None),
                User.miniapp_ip_hash.isnot(None),
            ).group_by(User.miniapp_ip_hash).all()}
        rows = []
        for user in users:
            if user.miniapp_referral_review_rejected_at:
                status = "rejected"
            elif user.miniapp_referral_review_approved_at or (
                user.miniapp_verified_at and (
                    not user.miniapp_ip_hash or
                    (user.miniapp_ip_hash != inviter_network and network_counts.get(str(user.miniapp_ip_hash), 0) == 1)
                )
            ):
                status = "verified"
            elif user.miniapp_verified_at:
                status = "review"
            else:
                status = "joined"
            rows.append({
                "username": ("@" + str(user.username)[:32]) if user.username else "RainDeals member",
                "status": status,
                "activity_at": (user.miniapp_referral_review_rejected_at or
                                user.miniapp_referral_review_approved_at or
                                user.miniapp_verified_at or
                                user.miniapp_verification_prompted_at).isoformat()
                               if (user.miniapp_referral_review_rejected_at or
                                   user.miniapp_referral_review_approved_at or
                                   user.miniapp_verified_at or
                                   user.miniapp_verification_prompted_at) else None,
            })
    return {"referrals": rows}


@router.post("/restock-alerts/{product_id}")
def subscribe_restock(product_id: int, auth=Depends(current_user)):
    from database import transaction
    from handlers.products import _real_stock
    with transaction() as db:
        product = db.query(Product).filter(Product.id == product_id, Product.is_active.is_(True)).first()
        if not product:
            raise HTTPException(status_code=404, detail="Product is unavailable.")
        if int(_real_stock(product)) > 0:
            return {"subscribed": False, "message": "This product is already in stock."}
        row = db.query(ProductEngagement).filter_by(telegram_id=auth['id'], product_id=product_id).with_for_update().first()
        if not row:
            row = ProductEngagement(telegram_id=auth['id'], product_id=product_id)
            db.add(row)
        row.restock_enabled = True
    return {"subscribed": True, "message": "We’ll notify you when stock returns."}


@router.delete("/restock-alerts/{product_id}")
def unsubscribe_restock(product_id: int, auth=Depends(current_user)):
    with SessionLocal() as db:
        row = db.query(ProductEngagement).filter_by(telegram_id=auth['id'], product_id=product_id).first()
        if row:
            row.restock_enabled = False
            if not row.is_favorite:
                db.delete(row)
        db.commit()
    return {"subscribed": False}


@router.get("/bundles")
def bundles(auth=Depends(current_user)):
    from services.runtime_settings import json_list_setting
    catalog = {row['id']: row for row in _product_rows(auth['id'])}
    result = []
    for row in json_list_setting('product_bundles_json'):
        if not isinstance(row, dict) or not row.get('active', True):
            continue
        items = [catalog[int(product_id)] for product_id in row.get('product_ids', [])
                 if str(product_id).isdigit() and int(product_id) in catalog]
        if len(items) < 2:
            continue
        result.append({"id": '-'.join(str(item['id']) for item in items),
                       "name": str(row.get('label') or row.get('name') or 'RainDeals bundle')[:80],
                       "description": str(row.get('description') or '')[:240],
                       "items": items, "total": _money(sum((Decimal(item['price']) for item in items), Decimal(0)))})
    return {"bundles": result[:50]}


@router.post("/bundles/{bundle_id}/cart")
def add_bundle_to_cart(bundle_id: str, auth=Depends(current_user)):
    _require_feature('cart', 'Cart is temporarily unavailable.')
    from services.runtime_settings import json_list_setting
    rows = json_list_setting('product_bundles_json')
    bundle = next((item for item in rows if isinstance(item, dict) and item.get('active', True)
                   and '-'.join(str(int(x)) for x in item.get('product_ids', []) if str(x).isdigit()) == bundle_id), None)
    if not bundle:
        raise HTTPException(status_code=404, detail='This bundle is no longer available.')
    from services import cart
    try:
        cart.add_bundle(auth['id'], bundle.get('product_ids', []))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _cart_snapshot(auth['id'])


@router.get("/deposit-methods")
def deposit_methods(auth=Depends(current_user)):
    from services.runtime_settings import setting_bool
    return {"methods": _deposit_method_rows() if setting_bool("feature_deposits", True) else []}


@router.post("/deposits")
async def create_deposit(payload: DepositCreate, request: Request, auth=Depends(current_user)):
    import asyncio
    from handlers.deposit import (
        _active_invoice, _create_invoice, _load_invoice_for_resume,
        _submitted_invoice, _switch_invoice_network,
    )
    from decimal import Decimal
    from services.runtime_settings import setting_bool

    if not setting_bool("feature_deposits", True):
        raise HTTPException(status_code=503, detail="Deposits are temporarily unavailable. Please try again later.")

    method = next((row for row in _deposit_method_rows() if row["network"] == payload.network), None)
    if not method or not method["configured"]:
        raise HTTPException(status_code=503, detail="This payment method is not configured. Choose another method or contact support.")
    if payload.network == "STARS":
        if payload.stars_amount is None:
            raise HTTPException(status_code=422, detail="Enter a whole number of Telegram Stars.")
        from services.stars_payments import stars_limits
        minimum, maximum = stars_limits()
        if not minimum <= int(payload.stars_amount) <= maximum:
            raise HTTPException(status_code=422, detail=f"Enter between {minimum:,} and {maximum:,} Stars.")
        bot = getattr(request.app.state, "main_bot", None)
        if bot is None:
            raise HTTPException(status_code=503, detail="Telegram Stars checkout is temporarily unavailable.")
        try:
            from services.stars_payments import (
                StarsPaymentError, create_stars_invoice, create_stars_invoice_link,
            )
            invoice = await asyncio.to_thread(
                create_stars_invoice, auth["id"], int(payload.stars_amount)
            )
            invoice["telegram_id"] = int(auth["id"])
            await create_stars_invoice_link(bot, invoice)
            owned = await asyncio.to_thread(_owned_deposit, auth["id"], int(invoice["id"]))
            if not owned:
                raise HTTPException(status_code=409, detail="The Stars invoice is no longer available. Refresh and try again.")
            return {"deposit": _deposit_view(owned)}
        except StarsPaymentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception:
            logger.exception("Mini App Stars invoice creation failed for telegram_id=%s", auth["id"])
            raise HTTPException(status_code=503, detail="Could not prepare the Stars checkout. Please try again.")
    submitted = await asyncio.to_thread(_submitted_invoice, auth["id"])
    if submitted:
        raise HTTPException(status_code=409, detail="A payment reference is already being verified. Check your deposit history before starting another.")
    try:
        active = await asyncio.to_thread(_active_invoice, auth["id"])
        if active:
            invoice_id = int(active["id"])
            if active.get("network") != payload.network:
                changed = await asyncio.to_thread(_switch_invoice_network, invoice_id, payload.network)
                if not changed:
                    raise HTTPException(status_code=409, detail="The current invoice can no longer switch methods. Check your deposit history.")
        else:
            created = await asyncio.to_thread(_create_invoice, auth["id"], Decimal("0"), payload.network)
            if created.get("verification_pending"):
                raise HTTPException(status_code=409, detail="A payment reference is already being verified. Check your deposit history before starting another.")
            invoice_id = int(created["id"])
            # _create_invoice safely reuses one active invoice. Apply the newly
            # selected method to that same invoice when another tab raced it.
            current = await asyncio.to_thread(_load_invoice_for_resume, auth["id"], invoice_id)
            if current and current.get("network") != payload.network:
                await asyncio.to_thread(_switch_invoice_network, invoice_id, payload.network)
        row = await asyncio.to_thread(_load_invoice_for_resume, auth["id"], invoice_id)
        if not row:
            raise HTTPException(status_code=409, detail="The invoice is no longer active. Refresh and try again.")
        owned = await asyncio.to_thread(_owned_deposit, auth["id"], invoice_id)
        return {"deposit": _deposit_view(owned)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Mini App deposit invoice creation failed for telegram_id=%s", auth["id"])
        raise HTTPException(status_code=503, detail="Could not prepare the payment invoice. Please try again.")


@router.get("/deposits")
def deposits(page: int = 0, auth=Depends(current_user)):
    page = max(0, min(int(page), 100000))
    with SessionLocal() as db:
        rows = (db.query(Deposit).filter(Deposit.telegram_id == auth["id"])
                .order_by(Deposit.id.desc()).offset(page * 10).limit(11).all())
        return {"deposits": [_deposit_view(row) for row in rows[:10]], "has_more": len(rows) > 10}


@router.get("/deposits/{deposit_id}")
def deposit_detail(deposit_id: int, auth=Depends(current_user)):
    row = _owned_deposit(auth["id"], deposit_id)
    if not row:
        raise HTTPException(status_code=404, detail="Deposit not found.")
    return {"deposit": _deposit_view(row)}


@router.post("/deposits/{deposit_id}/reference")
async def submit_deposit_reference(deposit_id: int, payload: DepositReference, auth=Depends(current_user)):
    import asyncio
    from handlers.deposit import _valid_order_id, _valid_upi_reference, TX_HASH_RE
    from services.runtime_settings import payment_reference_allowed
    from services.payment_submissions import bind_reference

    row = await asyncio.to_thread(_owned_deposit, auth["id"], deposit_id)
    if not row:
        raise HTTPException(status_code=404, detail="Deposit not found.")
    network = str(row.network or "").upper()
    if network == "STARS":
        raise HTTPException(status_code=409, detail="Telegram Stars payments are confirmed automatically. No payment reference is needed.")
    reference = payload.reference.strip()
    valid = (_valid_upi_reference(reference) if network == "UPI" else
             _valid_order_id(reference) if network == "BINANCE" else
             bool(TX_HASH_RE.fullmatch(reference)) and payment_reference_allowed(network, reference) if network in {"BEP20", "POLYGON"} else False)
    if not valid:
        reference_label = _DEPOSIT_LABELS.get(network, ("", "", "payment reference"))[2]
        raise HTTPException(status_code=422, detail=f"Enter a valid {reference_label}.")
    result = await asyncio.to_thread(bind_reference, deposit_id, reference, auth["id"], network)
    if result not in {"ok", "already_submitted"}:
        messages = {
            "duplicate": "This payment reference was already submitted.",
            "reference_locked": "This invoice already has a different reference attached.",
            "expired": "This invoice has expired. Check deposit history before creating another.",
            "closed": "This invoice is closed.",
            "not_found": "This invoice cannot accept a reference.",
            "completed": "This deposit is already complete.",
        }
        if result == "completed":
            return {"status": "completed", "message": "Payment already verified."}
        raise HTTPException(status_code=409, detail=messages.get(result, "Could not save this reference."))
    verify_info: dict = {}
    try:
        from services.deposit_checker import verify_deposit
        await verify_deposit(deposit_id, result_info=verify_info)
    except Exception:
        logger.exception("Mini App immediate deposit verification failed deposit_id=%s", deposit_id)
    latest = await asyncio.to_thread(_owned_deposit, auth["id"], deposit_id)
    status = str(latest.status if latest else "pending")
    return {"status": status, "message": "Payment verified." if status == "completed" else "Reference saved. Automatic verification will continue; you can check this deposit again here."}


@router.post("/deposits/{deposit_id}/correct-reference")
def correct_deposit_reference(deposit_id: int, auth=Depends(current_user)):
    from services.payment_submissions import request_reference_correction
    result = request_reference_correction(deposit_id, auth['id'])
    messages = {
        'not_found': (404, 'Deposit not found.'),
        'not_correctable': (409, 'This deposit reference cannot be changed. If it was already verified, contact RainDeals support.'),
        'verification_running': (409, 'Verification is in progress. Wait for its result before changing the reference.'),
        'expired': (409, 'This invoice has expired. Contact RainDeals support if you already paid.'),
    }
    if result in messages:
        status, message = messages[result]
        raise HTTPException(status_code=status, detail=message)
    return {'status': 'waiting_hash', 'message': 'The old reference was cleared. Submit the correct payment reference on this invoice.'}


@router.post("/promocodes/redeem")
def redeem_promocode(payload: PromocodePayload, auth=Depends(current_user)):
    from services.promotions import redeem
    ok, result = redeem(auth["id"], payload.code)
    if not ok:
        raise HTTPException(status_code=409, detail=str(result))
    return {"success": True, "code": result["code"], "amount": _money(result["amount"]), "balance": _money(result["balance"])}


@router.get("/cart")
def get_cart(auth=Depends(current_user)):
    _require_feature("cart", "Cart is temporarily unavailable.")
    return _cart_snapshot(auth["id"])


@router.post("/cart")
def change_cart(payload: CartChange, auth=Depends(current_user)):
    _require_feature("cart", "Cart is temporarily unavailable.")
    from services import cart
    try:
        cart.change(auth["id"], payload.product_id, payload.quantity)
        return _cart_snapshot(auth["id"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/cart")
def clear_cart(auth=Depends(current_user)):
    _require_feature("cart", "Cart is temporarily unavailable.")
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
    _require_feature("cart", "Cart checkout is temporarily unavailable.")
    import asyncio
    from services import cart
    try:
        await asyncio.to_thread(_validate_buyer_info, auth["id"], payload.buyer_info)
        token, lines, total = await asyncio.to_thread(cart.prepare, auth["id"])
        bot = getattr(request.app.state, "main_bot", None)
        if bot is None:
            raise HTTPException(status_code=503, detail="Checkout is temporarily unavailable. Try again in a moment.")
        result = await cart.checkout(auth["id"], token, bot.id, payload.buyer_info)
        # The bot's Telegram checkout handler sends its public order card itself.
        # Mini App checkout bypasses that handler, so enqueue the same durable
        # Admin Notify event here. Failure must not undo a completed purchase;
        # the background order scanner remains the retry/recovery path.
        checkout_result = result if isinstance(result, dict) else {}
        order_ids = {
            int(item["order_id"])
            for item in checkout_result.get("results", [])
            if isinstance(item, dict) and item.get("order_id")
        }
        if order_ids:
            from services.order_notifications import queue_order_notification
            for order_id in order_ids:
                try:
                    await asyncio.to_thread(queue_order_notification, order_id, bot.id)
                except Exception:
                    logger.exception("Mini App Admin Notify enqueue failed order_id=%s", order_id)
        return {"checkout": result, "total": _money(total), "items_reviewed": len(lines)}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        logger.exception("Mini App checkout failed for telegram_id=%s", auth["id"])
        raise HTTPException(status_code=503, detail="Checkout result is being checked. Open My Orders before trying again.")


@router.get("/orders")
def orders(status: str = "", q: str = "", page: int = 0, auth=Depends(current_user)):
    with SessionLocal() as db:
        query = db.query(Order).filter(Order.telegram_id == auth["id"])
        if status and status != 'all':
            query = query.filter(Order.status == status[:40])
        if q.strip():
            term = q.strip()[:80]
            if term.isdigit():
                query = query.filter((Order.id == int(term)) | Order.product_name.ilike(f'%{term}%'))
            else:
                query = query.filter(Order.product_name.ilike(f'%{term}%'))
        rows = query.order_by(desc(Order.id)).offset(max(0, min(page, 1000)) * 50).limit(51).all()
        return {"orders": [{"id": int(row.id), "product_name": row.product_name,
                            "quantity": int(row.quantity or 1), "amount": _money(row.amount),
                            "status": row.status, "created_at": row.created_at.isoformat() if row.created_at else None,
                            "refunded": bool(row.refunded)} for row in rows[:50]], "has_more": len(rows) > 50}


@router.get("/orders/{order_id}")
def order_detail(order_id: int, auth=Depends(current_user)):
    with SessionLocal() as db:
        row = db.query(Order).filter(Order.id == order_id, Order.telegram_id == auth["id"]).first()
        if not row:
            raise HTTPException(status_code=404, detail="Order not found.")
        from services.customer_tools import cancellable, cancellation_context
        cancellation = cancellation_context(auth['id'], order_id)
        submission = db.query(OrderRequirementSubmission).filter_by(
            order_id=int(order.id), telegram_id=int(auth['id'])
        ).first()
        timeline = db.query(OrderEvent).filter_by(order_id=int(row.id), customer_visible=True).order_by(
            OrderEvent.created_at.asc(), OrderEvent.id.asc()
        ).limit(100).all()
        return {"id": int(row.id), "product_name": row.product_name, "quantity": int(row.quantity or 1),
                "amount": _money(row.amount), "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "refunded": bool(row.refunded), "product_id": row.product_id,
                "delivered_details": str(row.delivered_account or ''),
                "submitted_details": submission.values if submission else {},
                "submitted_at": submission.submitted_at.isoformat() if submission and submission.submitted_at else None,
                "timeline": [{"kind": event.kind, "summary": event.summary,
                              "created_at": event.created_at.isoformat() if event.created_at else None}
                             for event in timeline],
                "can_cancel": bool(cancellable(row) and not cancellation),
                "cancellation": cancellation}


@router.post("/orders/{order_id}/cancel")
def request_order_cancellation(order_id: int, payload: CancellationPayload, auth=Depends(current_user)):
    from services.customer_tools import request_cancellation
    try:
        status = request_cancellation(auth['id'], order_id, payload.reason)
        return {"status": status, "message": "Your cancellation request was sent to RainDeals support."}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
