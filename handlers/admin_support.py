from datetime import datetime
from html import escape

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from database import SessionLocal
from models.ticket import Ticket, TicketMessage
from models.user import User
from config import ADMIN_IDS, MAIN_BOT_USERNAME

router = Router()


def is_admin(user_id: int):
    return user_id in ADMIN_IDS


# =====================================================
# TICKET LIST
# =====================================================

@router.callback_query(
    F.data == "admin_tickets"
)
async def admin_tickets(
        callback: CallbackQuery
):

    if not is_admin(
            callback.from_user.id
    ):

        await callback.answer(
            "Access denied.",
            show_alert=True
        )
        return

    db = SessionLocal()

    try:

        tickets = (
            db.query(Ticket)
            .order_by(
                Ticket.id.desc()
            )
            .limit(50)
            .all()
        )

        keyboard = []

        for ticket in tickets:
            owner = db.get(User, ticket.user_id)

            icon = (
                "🟢"
                if ticket.status == "Open"
                else "🔴"
            )

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text=
                        f"{icon} "
                        f"#{ticket.id} "
                        f"{owner.telegram_id if owner else 'Unknown'}",
                        callback_data=
                        f"ticket_{ticket.id}"
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    text="⬅ Back",
                    callback_data="admin_panel"
                )
            ]
        )

        await callback.message.edit_text(
            "🎫 Support Tickets",
            reply_markup=
            InlineKeyboardMarkup(
                inline_keyboard=keyboard
            )
        )

        await callback.answer()

    finally:
        db.close()


# =====================================================
# OPEN TICKET
# =====================================================

@router.callback_query(F.data.regexp(r"^ticket_\d+$"))
async def open_ticket(
        callback: CallbackQuery
):

    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    ticket_id = int(callback.data.removeprefix("ticket_"))

    db = SessionLocal()

    try:

        ticket = (
            db.query(Ticket)
            .filter(
                Ticket.id == ticket_id
            )
            .first()
        )

        if not ticket:

            await callback.answer(
                "Ticket not found."
            )
            return

        owner = db.get(User, ticket.user_id)
        telegram_id = int(owner.telegram_id) if owner else 0
        full_name = owner.full_name if owner else "Unknown"
        username = f"@{owner.username}" if owner and owner.username else "No Username"
        conversation = db.query(TicketMessage).filter(
            TicketMessage.ticket_id == ticket.id
        ).order_by(TicketMessage.created_at.asc(), TicketMessage.id.asc()).limit(30).all()
        if conversation and conversation[0].sender_role == "customer" and conversation[0].message == ticket.message:
            conversation = conversation[1:]
        conversation_text = "\n\n".join(
            f"{'⚙️ Status' if entry.sender_role == 'system' else '💬 Support' if entry.sender_role == 'admin' else '📝 Customer'} · "
            f"{entry.created_at.strftime('%d %b %Y, %H:%M UTC') if entry.created_at else '—'}\n"
            f"{escape(entry.message or '')}"
            for entry in conversation
        ) or escape(ticket.admin_response or "No replies yet.")

        text = f"""
🎫 Ticket #{ticket.id}

👤 Name:
{escape(full_name)}

🆔 User ID:
<code>{telegram_id}</code>

🔗 Username:
{escape(username)}

📄 Status:
{escape(ticket.status)}

📂 Category / priority:
{escape(ticket.category or 'General')} · {escape(ticket.priority or 'Normal')}

💬 Message:

{escape(ticket.message or '')}

🗂 Conversation:
{conversation_text}
"""

        keyboard = []

        bot_username = str(MAIN_BOT_USERNAME or "").strip().lstrip("@")
        if not bot_username:
            try:
                bot_username = str((await callback.bot.get_me()).username or "").strip()
            except Exception:
                bot_username = ""
        if bot_username:
            keyboard.append([
                InlineKeyboardButton(
                    text="💬 Reply privately",
                    url=f"https://t.me/{bot_username}?start=support_reply_{ticket.id}",
                )
            ])

        if (
            username != "No Username"
            and username != "Unknown"
        ):

            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="💬 Open User",
                        url=
                        f"https://t.me/{owner.username}"
                    )
                ]
            )

        keyboard.extend(
            [
                [
                    InlineKeyboardButton(
                        text="❌ Close Ticket",
                        callback_data=
                        f"close_ticket_{ticket.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅ Back",
                        callback_data=
                        "admin_tickets"
                    )
                ]
            ]
        )

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=
            InlineKeyboardMarkup(
                inline_keyboard=keyboard
            )
        )

        await callback.answer()

    finally:
        db.close()


# =====================================================
# CLOSE TICKET
# =====================================================

@router.callback_query(F.data.regexp(r"^close_ticket_\d+$"))
async def close_ticket(
        callback: CallbackQuery
):

    if not is_admin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    ticket_id = int(callback.data.removeprefix("close_ticket_"))

    db = SessionLocal()

    try:

        ticket = (
            db.query(Ticket)
            .filter(
                Ticket.id == ticket_id
            )
            .first()
        )

        if not ticket:

            await callback.answer(
                "Ticket not found."
            )
            return

        old_status = ticket.status
        ticket.status = "Closed"
        ticket.updated_at = datetime.utcnow()
        db.add(TicketMessage(
            ticket_id=ticket.id,
            sender_id=int(callback.from_user.id),
            sender_role="system",
            message=f"Ticket status changed: {old_status} → Closed.",
        ))

        db.commit()

    finally:
        db.close()

    try:
        from services.notification_outbox import admin_destination, queue_message
        with SessionLocal() as db:
            ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
            owner = db.get(User, ticket.user_id) if ticket else None
            telegram_id = int(owner.telegram_id) if owner else None
        if telegram_id:
            await queue_message(
                callback.bot,
                f"ticket-status:{ticket_id}:Closed",
                telegram_id,
                f"🎫 Support ticket #{ticket_id} was closed. If you still need help, create a new ticket.",
            )
        if admin_destination():
            await queue_message(
                callback.bot,
                f"admin-ticket-status:{ticket_id}:Closed",
                admin_destination(),
                f"🎫 Ticket #{ticket_id} status changed: {old_status} → Closed by <code>{callback.from_user.id}</code>.",
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to notify about closed ticket %s", ticket_id)

    await admin_tickets(
        callback
    )
