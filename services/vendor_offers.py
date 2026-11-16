"""Encrypted vendor submissions and exactly-once wallet/external payout states."""
import base64
import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN


class VendorConfigurationError(RuntimeError):
    pass


PAYOUT_METHODS = {
    "internal": "RainDeals bot wallet",
    "binance_pay": "Binance Pay",
    "usdt_bep20": "USDT · BEP20",
    "usdt_polygon": "USDT · Polygon",
    "other": "Other external method",
}


def _ciphers():
    import config
    from cryptography.fernet import Fernet

    raw_keys = [
        getattr(config, "VENDOR_DATA_ENCRYPTION_KEY", ""),
        getattr(config, "SESSION_RESUME_ENCRYPTION_KEY", ""),
        getattr(config, "API_KEY_ENCRYPTION_KEY", ""),
    ]
    keys = []
    for secret in dict.fromkeys(str(value).strip() for value in raw_keys if value):
        seed = hashlib.sha256(("raindeals-vendor-data-v1:" + secret).encode("utf-8")).digest()
        keys.append(Fernet(base64.urlsafe_b64encode(seed)))
    if not keys:
        raise VendorConfigurationError(
            "Vendor submissions require VENDOR_DATA_ENCRYPTION_KEY or an existing stable encryption key."
        )
    return keys


def _encrypt(value):
    return _ciphers()[0].encrypt(str(value).encode("utf-8")).decode("ascii")


def _decrypt(value):
    from cryptography.fernet import InvalidToken

    for cipher in _ciphers():
        try:
            return cipher.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken:
            continue
    raise VendorConfigurationError("Vendor data cannot be decrypted. Check the stable encryption key configuration.")


def _amount(value):
    try:
        amount = Decimal(str(value).strip()).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Enter a valid payout amount, such as 12.50.")
    if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000"):
        raise ValueError("Payout must be greater than 0 and no more than 1,000,000 USDT.")
    return amount


def create_request(admin_id, bot_id, product_name, requested_details,
                   delivery_requirements, bot_username):
    """Publish an admin product request for providers through the outbox."""
    from database import transaction
    from models.vendor_offer import VendorRequest
    from services.input_forms import text_value
    from services.notification_outbox import enqueue, enqueue_admin
    import config
    from html import escape

    channel = str(getattr(config, "VENDOR_DEAL_CHANNEL_ID", "") or "").strip()
    username = str(bot_username or "").strip().lstrip("@")
    if not channel:
        raise VendorConfigurationError("Set VENDOR_DEAL_CHANNEL_ID to the dedicated vendor/deal channel.")
    if not username:
        raise VendorConfigurationError("The bot username is unavailable; try again shortly.")
    product_name = text_value(product_name, 120, 3)
    requested_details = text_value(requested_details, 1400, 10)
    delivery_requirements = text_value(delivery_requirements, 1400, 5)

    with transaction() as db:
        request = VendorRequest(
            admin_id=int(admin_id), bot_id=int(bot_id), product_name=product_name,
            requested_details=requested_details,
            delivery_requirements=delivery_requirements, status="open",
        )
        db.add(request)
        db.flush()
        deep_link = f"https://t.me/{username}?start=vendor_request_{request.id}"
        markup = {"inline_keyboard": [[{
            "text": "🤝 Provide this product",
            "url": deep_link,
        }]]}
        body = (
            f"📣 <b>RainDeals provider request #{request.id}</b>\n"
            f"Product: <b>{escape(product_name)}</b>\n\n"
            f"<b>What we need</b>\n{escape(requested_details)}\n\n"
            f"<b>Delivery requirements</b>\n{escape(delivery_requirements)}\n\n"
            "Submit your offer privately to RainDeals for review."
        )
        enqueue(db, f"vendor-request:{request.id}:opened", channel, body,
                int(bot_id), reply_markup=markup)
        enqueue_admin(
            db, f"vendor-request:{request.id}:posted",
            f"📣 Provider request #{request.id} posted to the vendor channel.\nProduct: <b>{escape(product_name)}</b>",
        )
        return int(request.id)


def get_request(request_id):
    from database import SessionLocal
    from models.vendor_offer import VendorRequest

    with SessionLocal() as db:
        row = db.query(VendorRequest).filter(VendorRequest.id == int(request_id)).first()
        if not row:
            return None
        return {
            "id": int(row.id), "product_name": row.product_name,
            "requested_details": row.requested_details,
            "delivery_requirements": row.delivery_requirements,
            "status": row.status,
        }


def submit(telegram_id, bot_id, product_name, description, delivery_instructions,
           supplied_data, payout_method, payout_details=None, request_id=None):
    from database import transaction
    from models.vendor_offer import VendorOffer, VendorRequest
    from services.input_forms import text_value
    from services.notification_outbox import enqueue_admin
    from html import escape

    product_name = text_value(product_name, 120, 3)
    description = text_value(description, 1400, 10)
    delivery_instructions = text_value(delivery_instructions, 1400, 5)
    supplied_data = text_value(supplied_data, 8000, 5)
    payout_method = str(payout_method or "").strip().lower()
    if payout_method not in PAYOUT_METHODS:
        raise ValueError("Choose one of the available payout methods.")
    payout_details = text_value(payout_details, 350, 5) if payout_method != "internal" else ""
    supply_ciphertext = _encrypt(supplied_data)
    payout_ciphertext = _encrypt(payout_details) if payout_details else None

    with transaction() as db:
        request = None
        if request_id is not None:
            request = db.query(VendorRequest).filter(
                VendorRequest.id == int(request_id)
            ).with_for_update().first()
            if not request or request.status != "open":
                raise ValueError("This provider request is closed. Open the vendor channel for current requests.")
            product_name = request.product_name
        offer = VendorOffer(
            request_id=int(request.id) if request else None,
            telegram_id=int(telegram_id),
            bot_id=int(bot_id),
            product_name=product_name,
            offer_description=description,
            delivery_instructions=delivery_instructions,
            supply_ciphertext=supply_ciphertext,
            payout_method=payout_method,
            payout_ciphertext=payout_ciphertext,
            status="submitted",
        )
        db.add(offer)
        db.flush()
        review_markup = {"inline_keyboard": [[{
            "text": f"Review vendor offer #{offer.id}",
            "callback_data": f"vendor_review_{offer.id}",
        }]]}
        enqueue_admin(
            db,
            f"vendor-offer:{offer.id}:submitted",
            f"🤝 <b>Vendor offer submitted</b>\nOffer #{offer.id}\n"
            f"Product: {escape(product_name)}\nVendor Telegram ID: <code>{int(telegram_id)}</code>",
            reply_markup=review_markup,
        )
        return int(offer.id)


def _row_data(offer, include_private=False):
    data = {
        "id": int(offer.id),
        "request_id": int(offer.request_id) if offer.request_id is not None else None,
        "telegram_id": int(offer.telegram_id),
        "product_name": offer.product_name,
        "description": offer.offer_description,
        "delivery_instructions": offer.delivery_instructions,
        "payout_method": offer.payout_method,
        "payout_method_label": PAYOUT_METHODS.get(offer.payout_method, offer.payout_method),
        "status": offer.status,
        "approved_amount": str(offer.approved_amount) if offer.approved_amount is not None else None,
        "payout_reference": offer.payout_reference,
        "admin_note": offer.admin_note,
        "created_at": offer.created_at,
        "paid_at": offer.paid_at,
    }
    if include_private:
        data["supplied_data"] = _decrypt(offer.supply_ciphertext)
        data["payout_details"] = _decrypt(offer.payout_ciphertext) if offer.payout_ciphertext else ""
    return data


def pending(page=0, page_size=8):
    from database import SessionLocal
    from models.vendor_offer import VendorOffer

    page = max(0, int(page))
    with SessionLocal() as db:
        query = db.query(VendorOffer).filter(VendorOffer.status.in_(("submitted", "approved")))
        total = query.count()
        rows = query.order_by(VendorOffer.id.desc()).offset(page * page_size).limit(page_size).all()
        return total, [_row_data(row) for row in rows]


def get_for_admin(offer_id):
    from database import SessionLocal
    from models.vendor_offer import VendorOffer, VendorRequest

    with SessionLocal() as db:
        offer = db.query(VendorOffer).filter(VendorOffer.id == int(offer_id)).first()
        if not offer:
            return None
        data = _row_data(offer, include_private=True)
        if offer.request_id:
            request = db.query(VendorRequest).filter(VendorRequest.id == offer.request_id).first()
            if request:
                data["requested_details"] = request.requested_details
                data["requested_delivery_requirements"] = request.delivery_requirements
        return data


def get_for_user(offer_id, telegram_id):
    from database import SessionLocal
    from models.vendor_offer import VendorOffer

    with SessionLocal() as db:
        offer = db.query(VendorOffer).filter(
            VendorOffer.id == int(offer_id), VendorOffer.telegram_id == int(telegram_id)
        ).first()
        return _row_data(offer) if offer else None


def for_user(telegram_id, limit=10):
    from database import SessionLocal
    from models.vendor_offer import VendorOffer

    with SessionLocal() as db:
        rows = db.query(VendorOffer).filter(
            VendorOffer.telegram_id == int(telegram_id)
        ).order_by(VendorOffer.id.desc()).limit(max(1, min(int(limit), 20))).all()
        return [_row_data(row) for row in rows]


def _notify_user(db, offer, suffix, body):
    from services.notification_outbox import enqueue
    enqueue(db, f"vendor-offer:{offer.id}:{suffix}", offer.telegram_id, body, offer.bot_id)


def reject(offer_id, admin_id, reason=""):
    from database import transaction
    from models.vendor_offer import VendorOffer

    with transaction() as db:
        offer = db.query(VendorOffer).filter(VendorOffer.id == int(offer_id)).with_for_update().first()
        if not offer or offer.status != "submitted":
            return "This offer is no longer awaiting review."
        offer.status = "rejected"
        offer.admin_id = int(admin_id)
        offer.admin_note = str(reason or "").strip()[:1000] or None
        body = f"Your vendor offer #{offer.id} was declined."
        if offer.admin_note:
            body += f"\nReason: {offer.admin_note}"
        _notify_user(db, offer, "rejected", body)
    return None


def approve(offer_id, admin_id, raw_amount):
    from database import transaction
    from models.user import User
    from models.vendor_offer import VendorOffer, VendorRequest
    from services.wallet_ledger import record_wallet

    amount = _amount(raw_amount)
    with transaction() as db:
        offer = db.query(VendorOffer).filter(VendorOffer.id == int(offer_id)).with_for_update().first()
        if not offer or offer.status != "submitted":
            return "This offer is no longer awaiting review."
        user = None
        if offer.payout_method == "internal":
            user = db.query(User).filter(User.telegram_id == offer.telegram_id).with_for_update().first()
            if not user:
                return "The vendor account could not be found; no payout was made."
        offer.approved_amount = amount
        offer.admin_id = int(admin_id)
        if offer.request_id:
            request = db.query(VendorRequest).filter(
                VendorRequest.id == offer.request_id
            ).with_for_update().first()
            if request and request.status == "open":
                request.status = "filled"
        if offer.payout_method == "internal":
            before = Decimal(str(user.balance or 0))
            user.balance = before + amount
            record_wallet(db, user, f"Vendor offer #{offer.id} payout", before)
            offer.status = "paid"
            offer.paid_at = datetime.utcnow()
            _notify_user(db, offer, "paid", f"✅ Vendor offer #{offer.id} was approved. ${amount:.8f} USDT was credited to your RainDeals wallet.")
        else:
            offer.status = "approved"
            _notify_user(
                db,
                offer,
                "approved",
                f"✅ Vendor offer #{offer.id} was approved for ${amount:.8f} USDT. External payout is being arranged by the admin team.",
            )
    return None


def mark_external_paid(offer_id, admin_id, reference):
    from database import transaction
    from models.vendor_offer import VendorOffer
    from services.input_forms import text_value

    reference = text_value(reference, 255, 2)
    with transaction() as db:
        offer = db.query(VendorOffer).filter(VendorOffer.id == int(offer_id)).with_for_update().first()
        if not offer or offer.status != "approved" or offer.payout_method == "internal":
            return "This external payout is not awaiting confirmation."
        offer.status = "paid"
        offer.admin_id = int(admin_id)
        offer.payout_reference = reference
        offer.paid_at = datetime.utcnow()
        _notify_user(
            db,
            offer,
            "paid",
            f"✅ External payout for vendor offer #{offer.id} was marked sent by RainDeals. Reference: {reference}",
        )
    return None
