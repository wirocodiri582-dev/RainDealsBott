"""Eligibility helpers for referral-gated freebies.

Only registered Telegram accounts attributed on their first /start and later
authenticated through Telegram's signed Mini App initData count as verified.
This verifies distinct Telegram accounts, not distinct human beings.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from database import SessionLocal
from models.product import Product
from models.user import User


def verified_referral_count(db: Session, telegram_id: int) -> int:
    return int(
        db.query(User.id)
        .filter(
            User.referred_by == int(telegram_id),
            User.telegram_id != int(telegram_id),
            User.miniapp_verified_at.isnot(None),
            User.is_banned.is_(False),
        )
        .count()
    )


def freebie_eligibility(db: Session, telegram_id: int, product: Product) -> dict:
    required = max(0, int(getattr(product, "freebie_referrals_required", 0) or 0))
    if required == 0:
        return {
            "required": 0,
            "verified_referrals": 0,
            "claimant_verified": True,
            "unlocked": True,
        }
    user = db.query(User).filter(User.telegram_id == int(telegram_id)).first()
    claimant_verified = bool(user and user.miniapp_verified_at)
    count = verified_referral_count(db, telegram_id)
    return {
        "required": required,
        "verified_referrals": count,
        "claimant_verified": claimant_verified,
        "unlocked": required == 0 or (claimant_verified and count >= required),
    }


def freebie_eligibility_snapshot(telegram_id: int, product_id: int) -> dict:
    with SessionLocal() as db:
        product = db.query(Product).filter(Product.id == int(product_id)).first()
        if not product:
            return {"required": 0, "verified_referrals": 0, "claimant_verified": False, "unlocked": False}
        return freebie_eligibility(db, telegram_id, product)


