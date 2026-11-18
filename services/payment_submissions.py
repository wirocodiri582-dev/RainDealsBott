"""Bind one payment reference to its owner's invoice without replacing prior work."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError


def bind_reference(deposit_id, reference, user_id, network):
    from database import transaction
    from models.deposit import Deposit
    import config
    reference = reference.strip()
    if network in ('BEP20', 'POLYGON'):
        reference = reference.lower()
    elif network == 'BINANCE':
        # Binance Pay references are matched case-insensitively. Store one
        # canonical form so casing cannot create a second invoice claim.
        reference = reference.upper()
    try:
        with transaction() as db:
            deposit = db.query(Deposit).filter(Deposit.id == deposit_id).with_for_update().first()
            if not deposit or deposit.telegram_id != user_id or deposit.network != network:
                return 'not_found'
            if deposit.status == 'completed':
                return 'completed'
            if deposit.status in ('failed', 'cancelled'):
                return 'closed'
            if deposit.tx_hash:
                # A repeat is acknowledged; a different reference needs another invoice.
                return 'already_submitted' if deposit.tx_hash.casefold() == reference.casefold() else 'reference_locked'
            now = datetime.utcnow()
            late_reference = False
            invoice_expired = bool(deposit.invoice_expires_at and deposit.invoice_expires_at <= now)
            if deposit.status == 'expired' or invoice_expired:
                # A customer may submit a Binance-chain TXID after the invoice
                # card's one-hour display window. The verifier still checks the
                # Binance timestamp against the original invoice interval, so
                # this cannot claim a payment made after the invoice expired.
                late_hours = max(1, min(168, int(getattr(config, 'DEPOSIT_LATE_REFERENCE_HOURS', 72))))
                late_deadline = (deposit.invoice_expires_at + timedelta(hours=late_hours)
                                 if deposit.invoice_expires_at else None)
                late_reference = (
                    network in ('BEP20', 'POLYGON')
                    and deposit.created_at is not None
                    and late_deadline is not None
                    and now <= late_deadline
                    and deposit.status in ('waiting_hash', 'expired')
                )
                if not late_reference:
                    deposit.status = 'expired'
                    return 'expired'
            bound = db.query(Deposit).filter(Deposit.id == deposit_id, Deposit.tx_hash.is_(None),
                      Deposit.status == deposit.status).update({Deposit.tx_hash: reference, Deposit.status: 'pending',
                          Deposit.verification_started_at: now}, synchronize_session=False)
            if bound != 1:
                fresh = db.query(Deposit).filter(Deposit.id == deposit_id).populate_existing().with_for_update().first()
                if fresh.status == 'completed':
                    return 'completed'
                if fresh.status in ('failed', 'cancelled'):
                    return 'closed'
                return 'already_submitted' if fresh.tx_hash and fresh.tx_hash.casefold() == reference.casefold() else 'reference_locked'
        return 'ok'
    except IntegrityError:
        from database import SessionLocal
        with SessionLocal() as db:
            if db.query(Deposit.id).filter(Deposit.tx_hash == reference).first():
                return 'duplicate'
        raise


def credit_verified(db, original, amount, inr_amount=None, conversion_rate=None):
    """Final accounting only: callers must already have verified the actual payment."""
    from models.deposit import Deposit
    from models.user import User
    expected = (original.tx_hash, original.network, original.telegram_id)
    try:
        amount = Decimal(str(amount))
        if not amount.is_finite() or amount <= 0:
            db.rollback()
            return False
        extras = {name: Decimal(str(value)) for name, value in
                  (('inr_amount', inr_amount), ('conversion_rate', conversion_rate)) if value is not None}
        if any(not value.is_finite() or value <= 0 for value in extras.values()):
            db.rollback()
            return False
    except (InvalidOperation, ValueError):
        db.rollback()
        return False
    deposit = db.query(Deposit).filter(Deposit.id == original.id).populate_existing().with_for_update().first()
    if not deposit or (deposit.tx_hash, deposit.network, deposit.telegram_id) != expected:
        db.rollback()
        return False
    if deposit.status == 'completed':
        db.rollback()
        return True
    if deposit.status in ('failed', 'expired', 'cancelled') or not deposit.tx_hash:
        db.rollback()
        return False
    duplicate = db.query(Deposit.id).filter(Deposit.tx_hash == deposit.tx_hash,
                    Deposit.status == 'completed', Deposit.id != deposit.id).first()
    if duplicate:
        db.rollback()
        return False
    # Conditional transition is a second guard even on databases without FOR UPDATE.
    claimed = db.query(Deposit).filter(Deposit.id == deposit.id, Deposit.status == deposit.status,
                Deposit.tx_hash == expected[0]).update({Deposit.status: 'crediting'}, synchronize_session=False)
    if claimed != 1:
        deposit_id = deposit.id
        db.rollback()
        fresh = db.get(Deposit, deposit_id)
        return bool(fresh and fresh.status == 'completed' and (fresh.tx_hash, fresh.network, fresh.telegram_id) == expected)
    wallet = db.query(User).filter(User.telegram_id==deposit.telegram_id).populate_existing().with_for_update().first()
    if not wallet:
        db.rollback()
        return False
    before_balance = Decimal(str(wallet.balance or 0))
    # Increment at the database: two different deposits cannot lose a wallet update.
    credited = db.query(User).filter(User.telegram_id == deposit.telegram_id).update({
        User.balance: func.coalesce(User.balance, 0) + amount,
        User.total_deposited: func.coalesce(User.total_deposited, 0) + amount,
    }, synchronize_session=False)
    if credited != 1:
        db.rollback()
        return False
    db.refresh(wallet)
    from services.wallet_ledger import record_wallet
    ledger_method = 'Telegram Stars' if str(deposit.network or '').upper() == 'STARS' else deposit.network
    record_wallet(db,wallet,f'Deposit #{deposit.id} · {ledger_method}',before_balance)
    deposit.amount = deposit.received_amount = amount
    deposit.status = 'completed'
    for name, value in extras.items():
        setattr(deposit, name, value)

    # Queue all successful-deposit notices in the same transaction as the
    # wallet credit. This also covers deposits verified later by background
    # reconciliation, where no Telegram message handler is waiting.
    from html import escape
    import config
    from services.notification_outbox import enqueue, enqueue_admin
    display_name = str(wallet.username or "").strip().lstrip("@")
    admin_name = f"@{escape(display_name)}" if display_name else f"<code>{wallet.telegram_id}</code>"
    public_name = (f"@{escape(display_name[:1])}*****" if display_name
                   else f"{str(wallet.telegram_id)[:4]}***")
    network_label = {
        "BEP20": "USDT BEP20",
        "POLYGON": "USDT Polygon",
        "BINANCE": "Binance Pay",
        "UPI": "UPI",
        "STARS": "Telegram Stars",
    }.get(str(deposit.network or "").upper(), escape(str(deposit.network or "Unknown")))
    amount_text = f"${amount.quantize(Decimal('0.01')):.2f} USDT"
    stars_paid_line = (
        f"\n⭐ <b>Stars paid:</b> {int(deposit.stars_amount_paid)}"
        if str(deposit.network or "").upper() == "STARS" and deposit.stars_amount_paid
        else ""
    )
    payment_reference = (
        "Telegram confirmed"
        if str(deposit.network or "").upper() == "STARS"
        else escape(str(deposit.tx_hash or '')[:18]) + "…"
    )
    admin_body = (
        "💎 <b>New Deposit!</b>\n\n"
        f"🎉 <b>By:</b> {admin_name}\n"
        f"🆔 <b>Telegram ID:</b> <code>{wallet.telegram_id}</code>\n"
        f"💰 <b>Amount:</b> {amount_text}\n"
        f"{stars_paid_line}"
        f"💳 <b>Method:</b> {network_label}\n"
        f"🧾 <b>Deposit:</b> <code>#{deposit.id}</code>\n"
        f"🔑 <b>Reference:</b> <code>{payment_reference}</code>"
    )
    enqueue_admin(db, f"deposit-completed:{deposit.id}", admin_body)

    if getattr(config, "GROUP_NOTIFICATIONS", False) and getattr(config, "GROUP_ID", None):
        public_body = (
            "💎 <b>New Deposit!</b>\n\n"
            f"🎉 <b>By:</b> {public_name}\n"
            f"💰 <b>Amount:</b> {amount_text}\n"
            f"{stars_paid_line}"
            f"💳 <b>Method:</b> {network_label}"
        )
        enqueue(
            db,
            f"deposit-completed:{deposit.id}",
            config.GROUP_ID,
            public_body,
            bot_id=int(config.BOT_TOKEN.split(":", 1)[0]),
        )
    db.commit()
    return True
