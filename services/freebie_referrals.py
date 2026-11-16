"""Eligibility helpers for referral-gated freebies.

Only registered Telegram accounts attributed on their first /start and later
authenticated through Telegram's signed Mini App initData count as verified.
This verifies distinct Telegram accounts, not distinct human beings.
"""
from __future__ import annotations

from html import escape

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models.product import Product
from models.user import User


def verified_referral_count(db: Session, telegram_id: int) -> int:
    referrer_network = db.query(User.miniapp_ip_hash).filter(
        User.telegram_id == int(telegram_id)
    ).scalar()
    base = db.query(User).filter(
            User.referred_by == int(telegram_id),
            User.telegram_id != int(telegram_id),
            User.miniapp_verified_at.isnot(None),
            User.is_banned.is_(False),
        )
    # Count only one referral per observed network for each referrer. Older
    # verified referrals without a stored fingerprint remain individually
    # counted so this rollout doesn't erase existing progress.
    approved = base.filter(User.miniapp_referral_review_approved_at.isnot(None))
    approved_count = approved.count()
    not_approved = base.filter(User.miniapp_referral_review_approved_at.is_(None))
    if referrer_network:
        # Do not count a second Telegram account verified from the inviter's
        # own observed network unless an admin explicitly reviews it.
        not_approved = not_approved.filter(or_(
            User.miniapp_ip_hash.is_(None),
            User.miniapp_ip_hash != referrer_network,
        ))
    approved_networks = approved.filter(User.miniapp_ip_hash.isnot(None)).with_entities(
        User.miniapp_ip_hash
    )
    unique_networks = not_approved.filter(
        User.miniapp_ip_hash.isnot(None),
        ~User.miniapp_ip_hash.in_(approved_networks),
    ).with_entities(
        func.count(func.distinct(User.miniapp_ip_hash))
    ).scalar() or 0
    # Keep pre-rollout verified referrals that never received the one-time
    # verification prompt. New referrals without a trustworthy fingerprint
    # must be reviewed, not allowed to increase the reward counter.
    legacy_without_network = not_approved.filter(
        User.miniapp_ip_hash.is_(None),
        User.miniapp_verification_prompted_at.is_(None),
    ).count()
    return int(approved_count) + int(unique_networks) + int(legacy_without_network)


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
    if not referred_user.referred_by:
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
        from services.notification_outbox import enqueue_admin
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
                    text="✅ Approve as a legitimate referral",
                    callback_data=f"admin_referral_approve_{int(referred_user.telegram_id)}",
                    style="success",
                )
            ]]),
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


