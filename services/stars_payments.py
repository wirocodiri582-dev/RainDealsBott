"""Telegram Stars wallet top-ups with server-verified, idempotent settlement."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models.deposit import Deposit
from models.user import User

logger = logging.getLogger(__name__)

MIN_STARS_AMOUNT = 1
MAX_STARS_AMOUNT = 100_000
_USDT_QUANTUM = Decimal("0.00000001")


class StarsPaymentError(ValueError):
    """A safe, user-displayable error for Stars invoice creation."""


def stars_rate() -> Decimal | None:
    """Return the configured USDT credit per Star, or None if disabled."""
    import config

    raw = str(getattr(config, "TELEGRAM_STARS_USDT_PER_STAR", "") or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value <= 0 or value > Decimal("1000000"):
        return None
    # The schema stores the rate to eight decimal places. Use that same
    # precision for the quote so the saved audit rate always reproduces it.
    value = value.quantize(_USDT_QUANTUM, rounding=ROUND_DOWN)
    return value if value > 0 else None


def stars_configured() -> bool:
    return stars_rate() is not None


async def create_stars_invoice_link(bot, invoice: dict) -> str:
    """Return a Telegram invoice link for bot or Mini App checkout."""
    existing = str(invoice.get("invoice_url") or "").strip()
    if existing:
        return existing
    from aiogram.types import LabeledPrice

    stars = int(invoice["stars_amount"])
    credit = Decimal(str(invoice["usdt_credit"]))
    url = await bot.create_invoice_link(
        title="RainDeals wallet top-up",
        description=f"Add {credit:.8f} USDT wallet credit using {stars} Telegram Stars.",
        payload=str(invoice["payload"]),
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="RainDeals wallet credit", amount=stars)],
    )
    # Concurrent Mini App requests may each create a URL for the same payload.
    # Store and return one canonical URL; an unreturned duplicate is harmless.
    import asyncio
    return await asyncio.to_thread(
        save_stars_invoice_url, int(invoice["id"]), int(invoice["telegram_id"]), str(url)
    )


def quote_usdt(stars: int, rate: Decimal | None = None) -> Decimal:
    try:
        count = int(stars)
    except (TypeError, ValueError):
        raise StarsPaymentError("Enter a whole number of Telegram Stars.")
    if count < MIN_STARS_AMOUNT or count > MAX_STARS_AMOUNT:
        raise StarsPaymentError(
            f"Enter an amount from {MIN_STARS_AMOUNT} to {MAX_STARS_AMOUNT:,} Stars."
        )
    rate = rate if rate is not None else stars_rate()
    if rate is None:
        raise StarsPaymentError("Telegram Stars top-ups are not configured yet. Please contact support.")
    credited = (Decimal(count) * rate).quantize(_USDT_QUANTUM, rounding=ROUND_DOWN)
    if credited <= 0:
        raise StarsPaymentError("This Stars amount is below the minimum wallet credit.")
    return credited


def _rate_for_existing(row: Deposit) -> Decimal:
    try:
        rate = Decimal(str(row.stars_usdt_per_star or 0))
    except (InvalidOperation, ValueError):
        rate = Decimal("0")
    if not rate.is_finite() or rate <= 0:
        raise StarsPaymentError("The saved Stars conversion is unavailable. Contact support before paying.")
    return rate


def create_stars_invoice(telegram_id: int, stars_amount: int) -> dict:
    """Create or reuse one unexpired Stars invoice for this user and amount."""
    count = int(stars_amount)
    rate = stars_rate()
    credited = quote_usdt(count, rate)
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        user = (db.query(User).filter(User.telegram_id == int(telegram_id))
                .with_for_update().first())
        if not user:
            raise StarsPaymentError("Your RainDeals account was not found. Send /start and try again.")

        submitted = (db.query(Deposit).filter(
            Deposit.telegram_id == int(telegram_id),
            Deposit.status.in_(["waiting_hash", "pending"]),
            Deposit.tx_hash.isnot(None),
        ).order_by(Deposit.id.desc()).with_for_update().first())
        if submitted:
            raise StarsPaymentError("A deposit is already being verified. Check My Deposits before starting another.")

        active = (db.query(Deposit).filter(
            Deposit.telegram_id == int(telegram_id),
            Deposit.network == "STARS",
            Deposit.status == "waiting_hash",
            Deposit.tx_hash.is_(None),
            Deposit.invoice_expires_at > now,
        ).order_by(Deposit.id.desc()).with_for_update().first())
        if active:
            if int(active.stars_amount_requested or 0) != count:
                raise StarsPaymentError(
                    "You already have an active Stars invoice for a different amount. Pay it or wait for it to expire."
                )
            saved_rate = _rate_for_existing(active)
            return {
                "id": int(active.id), "invoice": str(active.invoice_code or active.order_id or ""),
                "telegram_id": int(active.telegram_id),
                "payload": str(active.stars_invoice_payload or ""),
                "stars_amount": count, "usdt_credit": str(active.invoice_usdt_amount or 0),
                "rate": str(saved_rate), "expires_at": active.invoice_expires_at.isoformat(),
                "invoice_url": active.stars_invoice_url,
                "telegram_message_id": active.telegram_message_id,
                "reused": True,
            }

        # Close expired unpaid Stars invoices. Telegram's pre-checkout is also
        # validated against expiry; a payment already pre-checked may finish.
        expired = (db.query(Deposit).filter(
            Deposit.telegram_id == int(telegram_id), Deposit.network == "STARS",
            Deposit.status == "waiting_hash", Deposit.tx_hash.is_(None),
            Deposit.invoice_expires_at <= now,
        ).with_for_update().all())
        for row in expired:
            row.status = "expired"

        from services.session_policy import INVOICE_LIFETIME_SECONDS
        invoice_code = f"STARS{secrets.token_hex(5).upper()}"
        payload = "rdstars_" + secrets.token_urlsafe(32)
        expires = now + timedelta(seconds=int(INVOICE_LIFETIME_SECONDS))
        row = Deposit(
            telegram_id=int(telegram_id), order_id=invoice_code,
            invoice_code=invoice_code, invoice_expires_at=expires,
            invoice_usdt_amount=credited, amount=credited,
            network="STARS", status="waiting_hash",
            stars_amount_requested=count, stars_usdt_per_star=rate,
            stars_invoice_payload=payload,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "id": int(row.id), "telegram_id": int(telegram_id),
            "invoice": invoice_code, "payload": payload,
            "stars_amount": count, "usdt_credit": str(credited), "rate": str(rate),
            "expires_at": expires.isoformat(), "invoice_url": None,
            "telegram_message_id": None, "reused": False,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def save_stars_invoice_url(deposit_id: int, telegram_id: int, invoice_url: str) -> str:
    """Persist one canonical Mini App invoice link for the deposit."""
    db = SessionLocal()
    try:
        row = (db.query(Deposit).filter(
            Deposit.id == int(deposit_id), Deposit.telegram_id == int(telegram_id),
            Deposit.network == "STARS",
        ).with_for_update().first())
        if not row or not row.stars_invoice_payload:
            raise StarsPaymentError("The Stars invoice could not be found. Refresh and try again.")
        if row.stars_invoice_url:
            return str(row.stars_invoice_url)
        if row.status != "waiting_hash" or not row.invoice_expires_at or row.invoice_expires_at <= datetime.utcnow():
            raise StarsPaymentError("This Stars invoice has expired. Create a new deposit to continue.")
        row.stars_invoice_url = str(invoice_url)[:2048]
        db.commit()
        return str(row.stars_invoice_url)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def save_stars_invoice_message(deposit_id: int, telegram_id: int, chat_id: int, message_id: int) -> None:
    db = SessionLocal()
    try:
        row = db.query(Deposit).filter(
            Deposit.id == int(deposit_id), Deposit.telegram_id == int(telegram_id),
            Deposit.network == "STARS", Deposit.status == "waiting_hash",
        ).with_for_update().first()
        if row:
            row.telegram_chat_id = int(chat_id)
            row.telegram_message_id = int(message_id)
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def cancel_stars_invoice(deposit_id: int, telegram_id: int) -> None:
    db = SessionLocal()
    try:
        row = db.query(Deposit).filter(
            Deposit.id == int(deposit_id), Deposit.telegram_id == int(telegram_id),
            Deposit.network == "STARS", Deposit.status == "waiting_hash",
            Deposit.tx_hash.is_(None),
        ).with_for_update().first()
        if row:
            row.status = "cancelled"
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def validate_precheckout(telegram_id: int, payload: str, currency: str, total_amount: int) -> tuple[bool, str]:
    """Validate and durably record Telegram's pre-checkout before approving it."""
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        row = db.query(Deposit).filter(
            Deposit.stars_invoice_payload == str(payload), Deposit.network == "STARS",
        ).with_for_update().first()
        if not row or row.telegram_id != int(telegram_id):
            return False, "This Stars invoice does not belong to your account."
        if str(currency).upper() != "XTR":
            return False, "This invoice has an invalid Telegram Stars currency."
        if int(total_amount) != int(row.stars_amount_requested or 0):
            return False, "The Stars amount does not match this invoice."
        if row.status != "waiting_hash" or row.tx_hash:
            return False, "This Stars invoice is no longer active. Create a new deposit."
        if not row.invoice_expires_at or row.invoice_expires_at <= now:
            row.status = "expired"
            db.commit()
            return False, "This Stars invoice expired. Create a new deposit."
        row.stars_precheckout_at = now
        db.commit()
        return True, ""
    except Exception:
        db.rollback()
        logger.exception("Stars pre-checkout validation failed")
        return False, "Payment verification is temporarily unavailable. Please try again."
    finally:
        db.close()


def _queue_stars_review(reason: str, telegram_id: int, payload: str, charge_id: str) -> None:
    try:
        from services.notification_outbox import enqueue_admin_once
        fingerprint = hashlib.sha256(str(payload).encode("utf-8")).hexdigest()[:16]
        safe_charge = str(charge_id)[:12]
        enqueue_admin_once(
            f"stars-payment-review:{fingerprint}:{safe_charge}",
            "⚠️ <b>Telegram Stars payment needs review</b>\n\n"
            f"👤 Telegram ID: <code>{int(telegram_id)}</code>\n"
            f"🧾 Invoice payload fingerprint: <code>{fingerprint}</code>\n"
            f"🔑 Charge: <code>{safe_charge}…</code>\n"
            f"Reason: {str(reason)[:180]}",
        )
    except Exception:
        logger.exception("Could not queue Stars payment review alert")


def record_successful_payment(
    telegram_id: int, payload: str, currency: str, total_amount: int, charge_id: str,
) -> str:
    """Bind a successful Stars charge and credit once; return a handling result."""
    charge = str(charge_id or "").strip()
    if not charge or len(charge) > 255:
        _queue_stars_review("missing or oversized Telegram charge ID", telegram_id, payload, charge)
        return "refund"
    if str(currency).upper() != "XTR" or int(total_amount or 0) < 1:
        _queue_stars_review("currency or amount was invalid", telegram_id, payload, charge)
        return "refund"

    db = SessionLocal()
    deposit_id = None
    expected_credit = None
    try:
        row = (db.query(Deposit).filter(
            Deposit.stars_invoice_payload == str(payload), Deposit.network == "STARS",
        ).with_for_update().first())
        if not row:
            db.rollback()
            _queue_stars_review("unknown invoice payload", telegram_id, payload, charge)
            return "refund"
        deposit_id = int(row.id)
        if row.telegram_id != int(telegram_id):
            db.rollback()
            _queue_stars_review("Telegram payer did not own the invoice", telegram_id, payload, charge)
            return "refund"
        if int(row.stars_amount_requested or 0) != int(total_amount):
            db.rollback()
            _queue_stars_review("paid Stars amount did not match the invoice", telegram_id, payload, charge)
            return "refund"

        already_used = db.query(Deposit).filter(
            Deposit.telegram_payment_charge_id == charge,
        ).with_for_update().first()
        if already_used and int(already_used.id) != deposit_id:
            db.rollback()
            _queue_stars_review("charge ID was already attached to another deposit", telegram_id, payload, charge)
            return "refund"
        if row.telegram_payment_charge_id and row.telegram_payment_charge_id != charge:
            db.rollback()
            _queue_stars_review("invoice received a second distinct charge", telegram_id, payload, charge)
            return "refund"
        if row.status == "completed":
            same = row.telegram_payment_charge_id == charge
            db.rollback()
            if not same:
                _queue_stars_review("a second Stars charge arrived for a completed invoice", telegram_id, payload, charge)
            return "already_credited" if same else "refund"

        charge_reference = "TG_STARS:" + hashlib.sha256(charge.encode("utf-8")).hexdigest()
        bound_same_charge = (
            row.telegram_payment_charge_id == charge and row.tx_hash == charge_reference
        )
        # A valid pre-checkout can finish just as the one-hour invoice window
        # closes. Allow that already-approved checkout to settle for 15 minutes.
        approved_at = row.stars_precheckout_at
        was_prechecked = bool(
            approved_at and datetime.utcnow() - approved_at <= timedelta(minutes=15)
        )
        if (row.status != "waiting_hash" and not bound_same_charge
                and not (row.status == "expired" and was_prechecked)):
            db.rollback()
            _queue_stars_review("invoice was closed before a successful payment update", telegram_id, payload, charge)
            return "refund"

        expected_credit = Decimal(str(row.invoice_usdt_amount or 0))
        if expected_credit <= 0 or not expected_credit.is_finite():
            db.rollback()
            _queue_stars_review("saved wallet credit quote was invalid", telegram_id, payload, charge)
            return "refund"
        if row.tx_hash and row.tx_hash != charge_reference:
            db.rollback()
            _queue_stars_review("invoice already has a different payment reference", telegram_id, payload, charge)
            return "refund"
        row.tx_hash = charge_reference
        row.telegram_payment_charge_id = charge
        row.stars_amount_paid = int(total_amount)
        row.status = "pending"
        db.commit()
    except IntegrityError:
        db.rollback()
        with SessionLocal() as check:
            existing = check.query(Deposit).filter(
                Deposit.telegram_payment_charge_id == charge,
            ).first()
            if not existing or int(existing.id) != int(deposit_id or 0):
                _queue_stars_review("duplicate Telegram charge ID", telegram_id, payload, charge)
                return "refund"
            deposit_id = int(existing.id)
            expected_credit = Decimal(str(existing.invoice_usdt_amount or 0))
    except Exception:
        db.rollback()
        logger.exception("Could not bind Telegram Stars payment for user %s", telegram_id)
        _queue_stars_review("database error while recording successful payment", telegram_id, payload, charge)
        # No charge binding may have been committed. Ask the transport to
        # retry this update instead of acknowledging a payment we cannot yet
        # reconcile from the database.
        return "retry"
    finally:
        db.close()

    # This accounting path is the same atomic wallet + ledger + notification
    # path used by verified crypto/UPI deposits. The durable charge binding
    # above lets the periodic reconciler safely retry if this commit is delayed.
    try:
        with SessionLocal() as credit_db:
            row = credit_db.query(Deposit).filter(Deposit.id == deposit_id).first()
            if not row:
                return "credit_pending"
            if row.status == "completed" and row.telegram_payment_charge_id == charge:
                return "already_credited"
            from services.payment_submissions import credit_verified
            if credit_verified(credit_db, row, expected_credit):
                return "credited"
    except Exception:
        logger.exception("Telegram Stars wallet credit is pending for deposit %s", deposit_id)
    return "credit_pending"


def reconcile_pending_stars(limit: int = 30) -> int:
    """Retry wallet credit for successful Stars updates durably recorded earlier."""
    with SessionLocal() as db:
        rows = (db.query(Deposit.id).filter(
            Deposit.network == "STARS", Deposit.status == "pending",
            Deposit.telegram_payment_charge_id.isnot(None),
            Deposit.tx_hash.like("TG_STARS:%"),
        ).order_by(Deposit.id.asc()).limit(max(1, min(int(limit), 200))).all())
        ids = [int(row[0]) for row in rows]

    completed = 0
    for deposit_id in ids:
        try:
            with SessionLocal() as db:
                row = db.query(Deposit).filter(Deposit.id == deposit_id).first()
                if not row or row.status != "pending":
                    continue
                amount = Decimal(str(row.invoice_usdt_amount or 0))
                from services.payment_submissions import credit_verified
                if credit_verified(db, row, amount):
                    completed += 1
        except Exception:
            logger.exception("Stars wallet-credit reconciliation failed for deposit %s", deposit_id)
    return completed
