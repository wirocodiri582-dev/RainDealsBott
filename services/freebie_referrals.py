"""Eligibility helpers for referral-gated freebies.

Only registered Telegram accounts attributed on their first /start and later
authenticated through Telegram's signed Mini App initData count as verified.
This verifies distinct Telegram accounts, not distinct human beings.
"""
from __future__ import annotations

from html import escape

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database import SessionLocal
from models.product import Product
from models.user import User


def verified_referral_count(db: Session, telegram_id: int) -> int:
    # Aggregate the eligibility rules in one database round-trip. The previous
    # implementation issued separate queries for the referrer's network,
    # approved accounts, unique networks, and legacy accounts on every page
    # refresh.
    base_filters = (
        User.referred_by == int(telegram_id),
        User.telegram_id != int(telegram_id),
        User.miniapp_verified_at.isnot(None),
        User.is_banned.is_(False),
    )
    referrer_network = select(User.miniapp_ip_hash).where(
        User.telegram_id == int(telegram_id)
    ).scalar_subquery()

    approved_count = select(func.count(User.id)).where(
        *base_filters,
        User.miniapp_referral_review_approved_at.isnot(None),
        User.miniapp_referral_review_rejected_at.is_(None),
    ).scalar_subquery()

    unreviewed_filters = (
        *base_filters,
        User.miniapp_referral_review_approved_at.is_(None),
        User.miniapp_referral_review_rejected_at.is_(None),
    )
    singleton_networks = select(
        User.miniapp_ip_hash.label("network_hash")
    ).where(
        *base_filters,
        User.miniapp_ip_hash.isnot(None),
    ).group_by(
        User.miniapp_ip_hash
    ).having(
        func.count(User.id) == 1
    ).subquery()

    unique_network_count = select(
        func.count(func.distinct(User.miniapp_ip_hash))
    ).where(
        *unreviewed_filters,
        User.miniapp_ip_hash.isnot(None),
        User.miniapp_ip_hash.in_(select(singleton_networks.c.network_hash)),
        or_(
            referrer_network.is_(None),
            referrer_network == "",
            User.miniapp_ip_hash != referrer_network,
        ),
    ).scalar_subquery()

    legacy_without_network = select(func.count(User.id)).where(
        *unreviewed_filters,
        User.miniapp_ip_hash.is_(None),
        User.miniapp_verification_prompted_at.is_(None),
    ).scalar_subquery()

    result = db.execute(select(
        approved_count.label("approved_count"),
        unique_network_count.label("unique_network_count"),
        legacy_without_network.label("legacy_count"),
    )).one()
    return int(result.approved_count or 0) + int(result.unique_network_count or 0) + int(result.legacy_count or 0)


def referral_ip_fingerprint(request) -> str | None:
    """Return a keyed fingerprint of the server-observed client address.

    On Render, prefer Cloudflare's single-client ``CF-Connecting-IP`` header.
    For other hosts, forwarded addresses are read only when the direct peer
    matches an explicitly configured trusted proxy CIDR. If the host only
    exposes a private proxy address, the fingerprint is omitted and the
    referral is sent for review instead of trusting a caller-controlled IP.
    """
    import ipaddress
    import hmac
    import config
    import hashlib

    headers = getattr(request, "headers", {})
    cloudflare_ip = str(headers.get("cf-connecting-ip", "")).strip()
    host = None
    if cloudflare_ip:
        # Render routes public web traffic through Cloudflare, which overwrites
        # this single-address header before forwarding it to the service.
        try:
            host = str(ipaddress.ip_address(cloudflare_ip))
        except ValueError:
            return None

    # Only inspect forwarded addresses when the immediate connection comes
    # from a proxy explicitly trusted by the service configuration.
    trusted_networks = []
    for value in getattr(config, "API_TRUSTED_PROXY_CIDRS", ()):
        try:
            trusted_networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    if host is None:
        peer = getattr(getattr(request, "client", None), "host", None)
        if not peer:
            return None
        try:
            peer_ip = ipaddress.ip_address(str(peer).strip())
        except ValueError:
            return None
        if trusted_networks and any(peer_ip in network for network in trusted_networks):
            forwarded = str(headers.get("x-forwarded-for", ""))
            for candidate in reversed(forwarded.split(",")):
                try:
                    address = ipaddress.ip_address(candidate.strip())
                except ValueError:
                    continue
                if any(address in network for network in trusted_networks):
                    continue
                host = str(address)
                break
            else:
                return None
        else:
            host = str(peer_ip)

    if not host:
        return None
    try:
        address = ipaddress.ip_address(str(host).strip())
        if not address.is_global:
            return None
    except ValueError:
        return None
    if not config.BOT_TOKEN:
        return None
    payload = b"raindeals-referral-network-v1:" + address.packed
    return hmac.new(config.BOT_TOKEN.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def notify_verified_referral(db: Session, referred_user: User) -> dict:
    """Add a customer inbox update and an admin review alert when warranted."""
    # This is only meaningful after a new account's first signed Store
    # verification. Repeated /start or referral-link clicks never call this
    # path and must not create another customer notification.
    if not referred_user.referred_by or not referred_user.miniapp_verified_at:
        return {"counted": False, "duplicate_network": False, "count": 0}

    referrer = db.query(User).filter(
        User.telegram_id == int(referred_user.referred_by)
    ).first()
    if not referrer:
        return {"counted": False, "duplicate_network": False, "count": 0}

    duplicate_network = False
    fingerprint = getattr(referred_user, "miniapp_ip_hash", None)
    matches_referrer_network = bool(
        fingerprint and fingerprint == getattr(referrer, "miniapp_ip_hash", None)
    )
    if fingerprint:
        duplicate_network = matches_referrer_network or bool(db.query(User.id).filter(
            User.referred_by == int(referrer.telegram_id),
            User.telegram_id != int(referred_user.telegram_id),
            User.miniapp_verified_at.isnot(None),
            User.is_banned.is_(False),
            User.miniapp_ip_hash == fingerprint,
        ).first())

    count = verified_referral_count(db, referrer.telegram_id)
    referrer_verified = bool(referrer.miniapp_verified_at)
    offers = db.query(Product.name, Product.freebie_referrals_required).filter(
        Product.is_active.is_(True),
        Product.price == 0,
        Product.freebie_referrals_required > 0,
    ).order_by(Product.freebie_referrals_required.asc(), Product.id.asc()).all()
    unlocked = [str(name or "Freebie") for name, required in offers if int(required or 0) <= count]
    pending = [(str(name or "Freebie"), int(required or 0)) for name, required in offers if int(required or 0) > count]

    network_unavailable = (
        not fingerprint
        and getattr(referred_user, "miniapp_verification_prompted_at", None) is not None
    )
    if duplicate_network or network_unavailable:
        reason = (
            "the trusted client network could not be determined by the server"
            if network_unavailable
            else "its observed network matches your own verified Store account"
            if matches_referrer_network
            else "its observed network matches another referral on your link"
        )
        lines = [
            "⚠️ <b>Referral not counted for freebie progress</b>",
            "This account completed Mini Store verification, but " + reason + ".",
            "Status: <b>pending admin review</b>.",
            f"🎁 Current eligible progress: <b>{count}</b> verified referral(s).",
        ]
        if unlocked and referrer_verified:
            lines.append("✅ <b>You can claim:</b> " + ", ".join(escape(name) for name in unlocked[:8]))
            lines.append("Open <b>Freebies</b> in the RainDeals bot to claim an available offer.")
        elif unlocked:
            lines.append("🎁 Your referral count meets the offer requirement, but verify your own account in the RainDeals Store before claiming.")
        elif pending:
            next_required = pending[0][1]
            lines.append(f"Next unlock: <b>{count}/{next_required}</b> verified referrals.")
        lines.append(
            "Shared homes, workplaces, and VPN exits can use one network. If this is a legitimate referral, contact Support for a review."
        )
        body = "\n\n".join(lines)
        from services.notification_outbox import enqueue, enqueue_admin
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        admin_reason = (
            "The app could not determine a trusted client network. Configure API_TRUSTED_PROXY_CIDRS before counting new referrals."
            if network_unavailable
            else "The referral was verified but excluded from freebie progress because its keyed network fingerprint matches the inviter or another referral. No raw IP address was stored."
        )
        enqueue_admin(
            db,
            f"referral-network-review:{referred_user.telegram_id}",
            "⚠️ <b>Referral network match</b>\n\n"
            f"Referrer ID: <code>{int(referrer.telegram_id)}</code>\n"
            f"Referred ID: <code>{int(referred_user.telegram_id)}</code>\n"
            + admin_reason,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Approve referral",
                    callback_data=f"admin_referral_approve_{int(referred_user.telegram_id)}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="❌ Reject referral",
                    callback_data=f"admin_referral_reject_{int(referred_user.telegram_id)}",
                    style="danger",
                ),
            ]]),
        )
        # A second account on the same network can make an earlier
        # provisional referral ineligible too. Update those invitees and put
        # each unresolved account in the admin review queue once.
        if fingerprint and not matches_referrer_network:
            matching_pending = db.query(User).filter(
                User.referred_by == int(referrer.telegram_id),
                User.telegram_id != int(referred_user.telegram_id),
                User.miniapp_verified_at.isnot(None),
                User.is_banned.is_(False),
                User.miniapp_referral_review_approved_at.is_(None),
                User.miniapp_referral_review_rejected_at.is_(None),
                User.miniapp_ip_hash == fingerprint,
            ).all()
            for matching_user in matching_pending:
                matching_id = int(matching_user.telegram_id)
                enqueue(
                    db,
                    f"referral-network-review-update:{matching_id}",
                    matching_id,
                    "⚠️ <b>Your referral is no longer counted yet</b>\n\n"
                    "Another account completed Store verification from the same observed network. Both referrals are awaiting admin review, so your inviter's eligible count has been recalculated. You will receive a decision here.",
                )
                enqueue_admin(
                    db,
                    f"referral-network-review:{matching_id}",
                    "⚠️ <b>Referral network match · review required</b>\n\n"
                    f"Referrer ID: <code>{int(referrer.telegram_id)}</code>\n"
                    f"Referred ID: <code>{matching_id}</code>\n"
                    "This previously verified referral now shares a network fingerprint with another referral. No raw IP address was stored.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text="✅ Approve referral",
                            callback_data=f"admin_referral_approve_{matching_id}",
                            style="success",
                        ),
                        InlineKeyboardButton(
                            text="❌ Reject referral",
                            callback_data=f"admin_referral_reject_{matching_id}",
                            style="danger",
                        ),
                    ]]),
                )
        enqueue(
            db,
            f"referral-participant-review:{int(referred_user.telegram_id)}",
            int(referred_user.telegram_id),
            "⚠️ <b>Your referral is not counted yet</b>\n\n"
            "Store verification is complete, but the referral needs an admin review because the network check matched another account or could not be confirmed.\n\n"
            "You will receive an update after review. Contact RainDeals Support if you think this is a mistake.",
        )
    else:
        lines = [
            "✅ <b>Referral completed</b>",
            "Your friend joined with your invite and finished Store verification.",
            f"Your eligible progress is now <b>{count}</b> completed referral(s).",
        ]
        if unlocked and referrer_verified:
            lines.append("🎁 <b>Unlocked:</b> " + ", ".join(escape(name) for name in unlocked[:8]))
            lines.append("Open <b>Freebies</b> in the RainDeals Mini Store to claim an available offer.")
        elif unlocked:
            lines.append("🎁 Your referral count meets the offer requirement. Verify your own account in the RainDeals Store before claiming.")
        elif pending:
            next_required = pending[0][1]
            next_names = [escape(name) for name, required in pending if required == next_required]
            lines.append(
                f"Next freebie unlock: <b>{count}/{next_required}</b> referrals"
                + (f" for {', '.join(next_names[:4])}." if next_names else ".")
            )
            if not referrer.miniapp_verified_at:
                lines.append("Open the RainDeals Mini Store once yourself to verify your account before claiming.")
        body = "\n\n".join(lines)

    from services.notification_outbox import enqueue
    enqueue(
        db,
        f"referral-{'network-review' if duplicate_network or network_unavailable else 'completed'}:{int(referred_user.telegram_id)}",
        int(referrer.telegram_id),
        body,
    )
    return {
        "counted": not duplicate_network and not network_unavailable,
        "duplicate_network": duplicate_network,
        "network_unavailable": network_unavailable,
        "count": count,
    }


def notify_claimant_verification(db: Session, user: User) -> None:
    """Record whether this signed-in user can claim a referral-gated freebie."""
    offers = db.query(Product.name, Product.freebie_referrals_required).filter(
        Product.is_active.is_(True),
        Product.price == 0,
        Product.freebie_referrals_required > 0,
    ).order_by(Product.freebie_referrals_required.asc(), Product.id.asc()).all()
    if not offers:
        return
    count = verified_referral_count(db, int(user.telegram_id))
    unlocked = [str(name or "Freebie") for name, required in offers if int(required or 0) <= count]
    pending = [(str(name or "Freebie"), int(required or 0)) for name, required in offers if int(required or 0) > count]
    lines = ["✅ <b>RainDeals Store verification complete</b>"]
    if unlocked:
        lines.append("🎁 <b>Available to claim:</b> " + ", ".join(escape(name) for name in unlocked[:8]))
        lines.append("Open <b>Freebies</b> in the bot to check stock and claim.")
    elif pending:
        next_required = pending[0][1]
        lines.append(f"Your verified referral progress is <b>{count}/{next_required}</b>.")
        lines.append(f"Invite {max(0, next_required - count)} more eligible friend(s) to unlock the next offer.")
    else:
        return
    from services.notification_outbox import enqueue
    enqueue(
        db,
        f"referral-claimant-verification:{int(user.telegram_id)}",
        int(user.telegram_id),
        "\n\n".join(lines),
    )


def freebie_eligibility(db: Session, telegram_id: int, product: Product) -> dict:
    required = max(0, int(getattr(product, "freebie_referrals_required", 0) or 0))
    user = db.query(User).filter(User.telegram_id == int(telegram_id)).first()
    claimant_verified = bool(user and user.miniapp_verified_at)
    count = verified_referral_count(db, telegram_id) if required else 0
    from services.runtime_settings import payment_value
    try:
        daily_limit = max(0, min(1000, int(payment_value('freebie_claim_limit_daily', '0') or 0)))
    except ValueError:
        daily_limit = 0
    daily_claims = 0
    if daily_limit:
        from datetime import datetime, timedelta
        from models.order import Order
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        daily_claims = int(db.query(func.count(Order.id)).filter(
            Order.telegram_id == int(telegram_id),
            Order.amount <= 0,
            Order.refunded.is_(False),
            Order.created_at >= today,
            Order.created_at < today + timedelta(days=1),
        ).scalar() or 0)
    limit_reached = bool(daily_limit and daily_claims >= daily_limit)
    return {
        "required": required,
        "verified_referrals": count,
        "claimant_verified": claimant_verified,
        "daily_limit": daily_limit,
        "daily_claims": daily_claims,
        "limit_reached": limit_reached,
        "unlocked": not limit_reached and (required == 0 or (claimant_verified and count >= required)),
    }


def freebie_eligibility_snapshot(telegram_id: int, product_id: int) -> dict:
    with SessionLocal() as db:
        product = db.query(Product).filter(Product.id == int(product_id)).first()
        if not product:
            return {"required": 0, "verified_referrals": 0, "claimant_verified": False, "unlocked": False}
        return freebie_eligibility(db, telegram_id, product)


