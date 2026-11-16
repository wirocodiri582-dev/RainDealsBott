import asyncio
from datetime import datetime
from html import escape

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, and_

from database import SessionLocal
from models.ticket import Ticket, TicketMessage
from models.user import User
from config import ADMIN_IDS
from states.support import SupportState
from utils.ui import show, update_card, answer_card

router = Router()

# Store only the Telegram username, without @ or a t.me URL.
CONTACT_USERNAME = "Raindrop7707"


# =====================================================
# UTILITY FUNCTIONS
# =====================================================

def get_status_emoji(status: str) -> str:
    return {
        "Open": "🆕",
        "In Progress": "🔄",
        "Resolved": "✅",
        "Closed": "❌",
        "Reopened": "🔄"
    }.get(status, "📋")


def get_priority_emoji(priority: str) -> str:
    return {
        "Low": "🟢",
        "Medium": "🟡",
        "High": "🟠",
        "Urgent": "🔴"
    }.get(priority, "🟡")


# =====================================================
# CONTACT
# =====================================================

@router.callback_query(F.data == "contact_info")
async def contact(callback: CallbackQuery):
    """Contact admin with colored buttons."""
    await callback.answer()

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📞 Open Direct Chat",
                    url=f"https://t.me/{CONTACT_USERNAME}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎫 Create Support Ticket",
                    callback_data="support_create_ticket",
                    style="primary"  # 🔵 Blue
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 My Tickets",
                    callback_data="support_my_tickets",
                    style="primary"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ Main Menu",
                    callback_data="main_menu"
                )
            ]
        ]
    )

    await show(
        callback,
        "📞 <b>Contact Admin</b>\n\n"
        "A direct chat is available, but it is not connected to ticket history.\n\n"
        "For a tracked reply and a copy in RainDeals Admin Notify, create a support ticket instead.",
        reply_markup=markup
    )

# =====================================================
# SUPPORT MENU
# =====================================================

@router.callback_query(F.data == "support_ticket")
async def support_menu(callback: CallbackQuery, state: FSMContext):
    """Main support menu with colored buttons."""
    await state.clear()
    await callback.answer()

    db = SessionLocal()
    try:
        owner_id = db.query(User.id).filter(User.telegram_id == callback.from_user.id).scalar()
        open_tickets = (
            db.query(func.count(Ticket.id))
            .filter(
                and_(
                    Ticket.user_id == owner_id,
                    Ticket.status.in_(["Open", "In Progress"])
                )
            )
            .scalar()
        ) or 0

        resolved_tickets = (
            db.query(func.count(Ticket.id))
            .filter(
                and_(
                    Ticket.user_id == owner_id,
                    Ticket.status == "Resolved"
                )
            )
            .scalar()
        ) or 0

        total_tickets = (
            db.query(func.count(Ticket.id))
            .filter(Ticket.user_id == owner_id)
            .scalar()
        ) or 0

        markup = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🎫 Create ticket", callback_data="support_create_ticket", style="success"),
                InlineKeyboardButton(text=f"📋 My tickets ({total_tickets})", callback_data="support_my_tickets", style="primary"),
            ],
            [
                InlineKeyboardButton(text="🔍 Track by ID", callback_data="support_track_ticket", style="primary"),
                InlineKeyboardButton(text="❓ FAQ", callback_data="support_faq", style="primary"),
            ],
            [InlineKeyboardButton(text="📞 Direct contact", callback_data="contact_info")],
            [InlineKeyboardButton(text="⭐ Rate Support", callback_data="support_rate", style="primary")],
            [InlineKeyboardButton(text="⬅ Main Menu", callback_data="main_menu")],
        ])

        stats_text = ""
        if total_tickets > 0:
            resolution_rate = (resolved_tickets / total_tickets) * 100
            stats_text = (
                f"📊 <b>Your Support Stats</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🟡 Open: {open_tickets}  |  "
                f"🟢 Resolved: {resolved_tickets}  |  "
                f"📊 Total: {total_tickets}\n"
                f"✅ Resolution Rate: {resolution_rate:.0f}%\n\n"
            )

        await show(
            callback,
            f"🎫 <b>Support Center</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{stats_text}"
            "Choose a topic and send one clear message. You can follow up from the ticket screen; replies and status changes will appear in Notifications.\n\n"
            "For payments, include the deposit or order number. Never send passwords or recovery codes.",
            reply_markup=markup
        )
    finally:
        db.close()


# =====================================================
# TRACK SPECIFIC TICKET
# =====================================================

@router.callback_query(F.data == "support_track_ticket")
async def track_ticket_prompt(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SupportState.tracking_ticket)

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="support_ticket",
                    style="danger"  # 🔴 Red — cancel action
                )
            ]
        ]
    )

    await show(
        callback,
        "🔍 <b>Track Your Ticket</b>\n\n"
        "Please enter your ticket ID to check its status.\n\n"
        "<i>Example: 42</i>",
        reply_markup=markup,
        state=state
    )


@router.message(SupportState.tracking_ticket)
async def show_ticket_details(message: Message, state: FSMContext):
    await state.clear()

    try:
        ticket_id = int(message.text.strip())
    except ValueError:
        await answer_card(message, "❌ Invalid ticket ID. Please enter a number.")
        return

    db = SessionLocal()
    try:
        owner_id = db.query(User.id).filter(User.telegram_id == message.from_user.id).scalar()
        ticket = (
            db.query(Ticket)
            .filter(
                and_(
                    Ticket.id == ticket_id,
                    Ticket.user_id == owner_id
                )
            )
            .first()
        )

        if not ticket:
            await answer_card(message,
                "❌ <b>Ticket Not Found</b>\n\n"
                "This ticket doesn't exist or doesn't belong to you."
            )
            return

        status_emoji = get_status_emoji(ticket.status)
        priority_emoji = get_priority_emoji(ticket.priority or "Medium")
        created = ticket.created_at.strftime("%d %b %Y, %H:%M UTC") if ticket.created_at else "N/A"
        updated = ticket.updated_at.strftime("%d %b %Y, %H:%M UTC") if ticket.updated_at else "N/A"

        text = (
            f"🎫 <b>Ticket #{ticket.id} Details</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📂 <b>Category:</b> {escape(ticket.category or 'General')}\n"
            f"{priority_emoji} <b>Priority:</b> {escape(ticket.priority or 'Medium')}\n"
            f"📊 <b>Status:</b> {status_emoji} {escape(ticket.status)}\n"
            f"🕐 <b>Created:</b> {created}\n"
            f"🔄 <b>Updated:</b> {updated}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 <b>Your Message:</b>\n"
            f"{escape(ticket.message or '')}\n"
        )

        conversation = db.query(TicketMessage).filter(
            TicketMessage.ticket_id == ticket.id
        ).order_by(TicketMessage.created_at.asc(), TicketMessage.id.asc()).limit(20).all()
        for entry in conversation[1:]:
            text += (
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{'⚙️ <b>Status</b>' if entry.sender_role == 'system' else '💬 <b>Support</b>' if entry.sender_role == 'admin' else '📝 <b>You</b>'} · "
                f"{entry.created_at.strftime('%d %b %Y, %H:%M UTC')}\n"
                f"{escape(entry.message or '')}\n"
            )
        if ticket.admin_response and not conversation:
            text += f"━━━━━━━━━━━━━━━━━━━━━━\n💬 <b>Support:</b>\n{escape(ticket.admin_response)}\n"

        builder = InlineKeyboardBuilder()
        if ticket.status in ("Open", "In Progress", "Reopened"):
            builder.row(InlineKeyboardButton(
                text="💬 Add a message",
                callback_data=f"support_reply_{ticket.id}",
                style="success",
            ))
        builder.row(
            InlineKeyboardButton(
                text="📋 All My Tickets",
                callback_data="support_my_tickets",
                style="primary"
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="⬅ Support Menu",
                callback_data="support_ticket"
            )
        )

        await answer_card(message, text, reply_markup=builder.as_markup())

    finally:
        db.close()


# =====================================================
# FAQ
# =====================================================

@router.callback_query(F.data == "support_faq")
async def support_faq(callback: CallbackQuery):
    await callback.answer()

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 Payments & Deposits",
                    callback_data="faq_payments",
                    style="primary"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Orders & Delivery",
                    callback_data="faq_orders",
                    style="primary"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Referrals & Earnings",
                    callback_data="faq_referrals",
                    style="primary"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔒 Account & Security",
                    callback_data="faq_account",
                    style="primary"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎫 Support & Tickets",
                    callback_data="faq_support",
                    style="primary"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎫 Still Need Help? Create Ticket",
                    callback_data="support_create_ticket",
                    style="success"  # 🟢 Green — helpful action
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ Back",
                    callback_data="support_ticket"
                )
            ]
        ]
    )

    await show(
        callback,
        "❓ <b>Frequently Asked Questions</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select a category to find answers:\n\n"
        "💡 <i>Can't find your answer? Create a ticket!</i>",
        reply_markup=markup
    )


@router.callback_query(F.data.startswith("faq_"))
async def faq_category(callback: CallbackQuery):
    await callback.answer()

    faq_content = {
        "faq_payments": (
            "💰 <b>Payments & Deposits</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💳 <b>How to deposit?</b>\n"
            "• Go to Wallet → Deposit\n"
            "• Choose your payment method\n"
            "• Minimum deposit: $5.00\n\n"
            "⏱️ <b>Processing time?</b>\n"
            "• Crypto: 10-30 min\n"
            "• UPI/Pay: 5-15 min\n"
        ),
        "faq_orders": (
            "📦 <b>Orders & Delivery</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🛒 <b>How to order?</b>\n"
            "• Browse Products in shop\n"
            "• Complete payment\n\n"
            "⏱️ <b>Delivery time?</b>\n"
            "• Digital: Instant\n"
            "• Services: 24-48 hours\n"
        ),
        "faq_referrals": (
            "👥 <b>Referrals & Earnings</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔗 <b>How to refer?</b>\n"
            "• Go to Referrals menu\n"
            "• Share your referral link\n\n"
            "💰 <b>Earnings?</b>\n"
            "• 10% of referrals' activity\n"
            "• No earning limits\n"
        ),
        "faq_account": (
            "🔒 <b>Account & Security</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔑 <b>Lost access?</b>\n"
            "• Use /start to refresh\n\n"
            "🚫 <b>Banned?</b>\n"
            "• Open a ticket to appeal\n"
        ),
        "faq_support": (
            "🎫 <b>Support & Tickets</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🎫 <b>Create a ticket?</b>\n"
            "• Support → Create Ticket\n\n"
            "⏱️ <b>Response time?</b>\n"
            "• Under 2 hours\n"
        ),
    }

    content = faq_content.get(callback.data, "❓ FAQ not found.")

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎫 Create Ticket",
                    callback_data="support_create_ticket",
                    style="success"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ Back to FAQ",
                    callback_data="support_faq"
                ),
                InlineKeyboardButton(
                    text="🏠 Support Menu",
                    callback_data="support_ticket"
                )
            ]
        ]
    )

    await show(callback, content, reply_markup=markup)


# =====================================================
# RATE SUPPORT
# =====================================================

@router.callback_query(F.data == "support_rate")
async def rate_support(callback: CallbackQuery):
    await callback.answer()

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="😡 Terrible",
                    callback_data="rate_1",
                    style="danger"  # 🔴 Red
                )
            ],
            [
                InlineKeyboardButton(
                    text="😐 Poor",
                    callback_data="rate_2",
                    style="danger"
                )
            ],
            [
                InlineKeyboardButton(
                    text="😊 Good",
                    callback_data="rate_3",
                    style="primary"  # 🔵 Blue
                )
            ],
            [
                InlineKeyboardButton(
                    text="😄 Great",
                    callback_data="rate_4",
                    style="success"  # 🟢 Green
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌟 Excellent",
                    callback_data="rate_5",
                    style="success"  # 🟢 Green
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ Back",
                    callback_data="support_ticket"
                )
            ]
        ]
    )

    await show(
        callback,
        "⭐ <b>Rate Our Support</b>\n\n"
        "How would you rate your support experience?\n\n"
        "Your feedback helps us improve! 🙏",
        reply_markup=markup
    )


@router.callback_query(F.data.in_({"rate_1", "rate_2", "rate_3", "rate_4", "rate_5"}))
async def handle_rating(callback: CallbackQuery):
    await callback.answer()

    rating = int(callback.data.split("_")[1])
    rating_emoji = {1: "😡", 2: "😐", 3: "😊", 4: "😄", 5: "🌟"}
    rating_text = {1: "Terrible", 2: "Poor", 3: "Good", 4: "Great", 5: "Excellent"}

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 Support Menu",
                    callback_data="support_ticket",
                    style="primary"
                )
            ]
        ]
    )

    await show(
        callback,
        f"{rating_emoji[rating]} <b>Thanks for your feedback!</b>\n\n"
        f"You rated our support: <b>{rating_text[rating]}</b>\n\n"
        "We appreciate your time! 🙏",
        reply_markup=markup
    )

    try:
        from services.notification_outbox import admin_destination, queue_message
        destination = admin_destination()
        if destination:
            from html import escape
            import hashlib
            username = f"@{escape(callback.from_user.username)}" if callback.from_user.username else "No username"
            rating_key = hashlib.sha256(str(callback.id or callback.from_user.id).encode()).hexdigest()[:16]
            await queue_message(
                callback.bot,
                f"support-rating:{callback.from_user.id}:{rating_key}",
                destination,
                f"⭐ Support feedback · <b>{escape(rating_text[rating])}</b> {rating_emoji[rating]}\n"
                f"👤 {username} · Telegram ID <code>{callback.from_user.id}</code>",
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Could not send support rating to Admin Notify")


# =====================================================
# CREATE TICKET — WITH COLORED CATEGORIES
# =====================================================

@router.callback_query(F.data == "support_create_ticket")
async def ticket_categories(callback: CallbackQuery, state: FSMContext):
    """Show ticket categories with colored buttons."""
    await state.clear()
    await callback.answer()

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Order problem", callback_data="ticket_cat_order", style="primary"),
            InlineKeyboardButton(text="💳 Payment issue", callback_data="ticket_cat_payment", style="primary"),
        ],
        [
            InlineKeyboardButton(text="👤 Account help", callback_data="ticket_cat_account", style="primary"),
            InlineKeyboardButton(text="🔧 Bug report", callback_data="ticket_cat_bug", style="danger"),
        ],
        [
            InlineKeyboardButton(text="💡 Suggestion", callback_data="ticket_cat_suggestion", style="success"),
            InlineKeyboardButton(text="📋 Other", callback_data="ticket_cat_other", style="success"),
        ],
        [InlineKeyboardButton(text="⬅ Back to Support", callback_data="support_ticket")],
    ])

    await show(
        callback,
        "🎫 <b>Create Support Ticket</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📂 <b>Select your issue category:</b>\n\n"
        "🔴 <b>Urgent</b> — Bug Reports\n"
        "🔵 <b>Priority</b> — Orders, Payments, Account\n"
        "🟢 <b>General</b> — Suggestions, Questions\n\n"
        "<i>Select the most relevant category for faster help!</i>",
        reply_markup=markup
    )


CATEGORY_PRIORITY = {
    "ticket_cat_payment": "Medium",
    "ticket_cat_order": "High",
    "ticket_cat_account": "Medium",
    "ticket_cat_bug": "Urgent",
    "ticket_cat_suggestion": "Low",
    "ticket_cat_other": "Low",
}


@router.callback_query(F.data.startswith("ticket_cat_"))
async def select_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    category_map = {
        "ticket_cat_payment": "💰 Payment Issue",
        "ticket_cat_order": "📦 Order Problem",
        "ticket_cat_account": "👤 Account Help",
        "ticket_cat_bug": "🔧 Bug Report",
        "ticket_cat_suggestion": "💡 Suggestion",
        "ticket_cat_other": "📋 General Question",
    }

    category = category_map.get(callback.data, "📋 General Question")
    priority = CATEGORY_PRIORITY.get(callback.data, "Medium")
    priority_emoji = get_priority_emoji(priority)

    await state.update_data(ticket_category=category, ticket_priority=priority)
    await state.set_state(SupportState.waiting_message)

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="support_ticket",
                    style="danger"  # 🔴 Red
                )
            ]
        ]
    )

    await show(
        callback,
        f"🎫 <b>Create Ticket</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📂 <b>Category:</b> {category}\n"
        f"{priority_emoji} <b>Priority:</b> {priority}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Describe your issue:</b>\n\n"
        f"<i>Please include relevant details.</i>\n\n"
        f"✍️ <b>Send your message now:</b>",
        reply_markup=markup,
        state=state,
    )


def _save_ticket(user_id: int, text: str, category: str, priority: str) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == int(user_id)).with_for_update().first()
        if not user:
            raise ValueError("RainDeals account not found. Send /start and try again.")
        ticket = Ticket(
            # tickets.user_id references users.id; all handlers convert from
            # Telegram ID at the boundary instead of storing two ID types in
            # the same column.
            user_id=user.id,
            message=text,
            category=category,
            priority=priority,
            status="Open",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(ticket)
        db.flush()
        db.add(TicketMessage(
            ticket_id=ticket.id,
            sender_id=int(user_id),
            sender_role="customer",
            message=text,
        ))
        db.commit()
        db.refresh(ticket)
        return ticket.id
    finally:
        db.close()


@router.message(SupportState.waiting_message)
async def create_ticket(message: Message, state: FSMContext):
    data = await state.get_data()
    card_chat_id = data.get("_card_chat_id")
    card_message_id = data.get("_card_message_id")
    category = data.get("ticket_category", "📋 General Question")
    priority = data.get("ticket_priority", "Medium")
    priority_emoji = get_priority_emoji(priority)

    ticket_text = (message.text or "").strip()
    if len(ticket_text) < 20:
        await answer_card(message,
            "⚠️ <b>Message too short!</b>\n\n"
            "Please provide more details (minimum 20 characters)."
        )
        return

    if len(ticket_text) > 2000:
        await answer_card(message,
            "⚠️ <b>Message too long!</b>\n\n"
            "Please keep your message under 2000 characters."
        )
        return

    try:
        ticket_id = await asyncio.to_thread(
            _save_ticket, message.from_user.id, ticket_text, category, priority
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Could not save support ticket for user_id=%s", message.from_user.id)
        await answer_card(message, "⚠️ We could not save this ticket. Send /start and try again.")
        return

    await state.clear()

    await update_card(
        message, None,
        f"✅ <b>Ticket Created Successfully!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎫 <b>Ticket ID:</b> #{ticket_id}\n"
        f"📂 <b>Category:</b> {escape(category)}\n"
        f"{priority_emoji} <b>Priority:</b> {escape(priority)}\n"
        f"📊 <b>Status:</b> 🆕 Open\n\n"
        f"⏱️ Our team will review your ticket.\n"
        f"💡 Track your ticket in 📋 My Tickets",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📋 View My Tickets",
                        callback_data="support_my_tickets",
                        style="primary"
                    ),
                    InlineKeyboardButton(
                        text="🔍 Track This Ticket",
                        callback_data="support_track_ticket",
                        style="primary"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅ Main Menu",
                        callback_data="main_menu"
                    )
                ]
            ]
        ),
        chat_id=card_chat_id,
        message_id=card_message_id,
    )

    # Every ticket goes through the durable Admin Notify outbox. The private
    # deep link is the working reply path; text replies sent in the group are
    # never captured as admin messages.
    try:
        from config import MAIN_BOT_USERNAME
        from services.notification_outbox import admin_destination, queue_message
        destination = admin_destination()
        bot_username = str(MAIN_BOT_USERNAME or "").strip().lstrip("@")
        if not bot_username:
            bot_username = str((await message.bot.get_me()).username or "").strip()
        rows = [[
            InlineKeyboardButton(text="🔄 In Progress", callback_data=f"admin_ticket_progress_{ticket_id}"),
            InlineKeyboardButton(text="✅ Resolve", callback_data=f"admin_ticket_resolve_{ticket_id}"),
            InlineKeyboardButton(text="✖ Close", callback_data=f"admin_ticket_close_{ticket_id}"),
        ]]
        if bot_username:
            rows.append([InlineKeyboardButton(
                text="💬 Reply privately",
                url=f"https://t.me/{bot_username}?start=support_reply_{ticket_id}",
            )])
        rows.append([InlineKeyboardButton(
            text="👤 Open user", url=f"tg://user?id={message.from_user.id}"
        )])
        username = f"@{escape(message.from_user.username)}" if message.from_user.username else "No username"
        if destination:
            await queue_message(
                message.bot,
                f"admin-ticket:{ticket_id}",
                destination,
                f"🎫 <b>New support ticket #{ticket_id}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📂 <b>Category:</b> {escape(category)}\n"
                f"{priority_emoji} <b>Priority:</b> {escape(priority)}\n"
                f"👤 <b>User:</b> {username}\n"
                f"🆔 <b>Telegram ID:</b> <code>{message.from_user.id}</code>\n"
                f"🕐 <b>Time:</b> {datetime.utcnow().strftime('%d %b %Y, %H:%M UTC')}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 <b>Message:</b>\n\n"
                f"{escape(ticket_text[:1800])}{'…' if len(ticket_text) > 1800 else ''}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Admin Notify ticket enqueue failed for ticket_id=%s", ticket_id)


# =====================================================
# MY TICKETS — WITH COLORED BUTTONS
# =====================================================

@router.callback_query(F.data == "support_my_tickets")
async def my_tickets(callback: CallbackQuery):
    await callback.answer()

    db = SessionLocal()
    try:
        owner_id = db.query(User.id).filter(User.telegram_id == callback.from_user.id).scalar()
        tickets = (
            db.query(Ticket)
            .filter(Ticket.user_id == owner_id)
            .order_by(Ticket.updated_at.desc())
            .limit(15)
            .all()
        )

        if not tickets:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎫 Create Your First Ticket",
                            callback_data="support_create_ticket",
                            style="success"  # 🟢 Green
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅ Support Menu",
                            callback_data="support_ticket"
                        )
                    ]
                ]
            )

            await show(
                callback,
                "📋 <b>No Tickets Yet</b>\n\n"
                "You haven't created any support tickets.\n\n"
                "Need help? Create a ticket!",
                reply_markup=markup
            )
            return

        open_tickets = [t for t in tickets if t.status in ["Open", "In Progress"]]
        closed_tickets = [t for t in tickets if t.status in ["Resolved", "Closed"]]

        text = "📋 <b>Your Support Tickets</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"

        if open_tickets:
            text += "🟡 <b>Active Tickets:</b>\n"
            for ticket in open_tickets:
                status_emoji = get_status_emoji(ticket.status)
                priority_emoji = get_priority_emoji(ticket.priority or "Medium")
                created = ticket.created_at.strftime("%d/%m/%y")
                text += (
                    f"{status_emoji} <b>#{ticket.id}</b> {priority_emoji} "
                    f"<i>{created}</i> — {ticket.category or 'General'}\n"
                )
            text += "\n"

        if closed_tickets:
            text += "✅ <b>Resolved Tickets:</b>\n"
            for ticket in closed_tickets[:5]:
                status_emoji = get_status_emoji(ticket.status)
                created = ticket.created_at.strftime("%d/%m/%y")
                text += (
                    f"{status_emoji} <b>#{ticket.id}</b> <i>{created}</i>"
                    f" — {ticket.category or 'General'}\n"
                )
            text += "\n"

        text += (
            f"📊 <b>Summary:</b> "
            f"Active: {len(open_tickets)} | "
            f"Resolved: {len(closed_tickets)} | "
            f"Total: {len(tickets)}\n"
        )

        rows = []
        for ticket in tickets[:10]:
            label = f"{get_status_emoji(ticket.status)} #{ticket.id} · {ticket.category or 'General'}"
            rows.append([InlineKeyboardButton(
                text=label[:64], callback_data=f"support_view_{ticket.id}", style="primary"
            )])
        rows.extend([
            [
                InlineKeyboardButton(text="🎫 New Ticket", callback_data="support_create_ticket", style="success"),
                InlineKeyboardButton(text="🔍 Track by ID", callback_data="support_track_ticket", style="primary"),
            ],
            [InlineKeyboardButton(text="⬅ Support Menu", callback_data="support_ticket")],
        ])
        markup = InlineKeyboardMarkup(inline_keyboard=rows)

        await show(callback, text, reply_markup=markup)
    finally:
        db.close()


def _ticket_for_customer(telegram_id: int, ticket_id: int):
    db = SessionLocal()
    try:
        owner_id = db.query(User.id).filter(User.telegram_id == int(telegram_id)).scalar()
        if not owner_id:
            return None
        ticket = db.query(Ticket).filter(
            Ticket.id == int(ticket_id), Ticket.user_id == owner_id
        ).first()
        if not ticket:
            return None
        messages = db.query(TicketMessage).filter(
            TicketMessage.ticket_id == ticket.id
        ).order_by(TicketMessage.created_at.asc(), TicketMessage.id.asc()).limit(30).all()
        return {
            "id": int(ticket.id), "category": ticket.category or "General",
            "priority": ticket.priority or "Medium", "status": ticket.status,
            "message": ticket.message or "", "admin_response": ticket.admin_response or "",
            "created_at": ticket.created_at, "updated_at": ticket.updated_at,
            "messages": [(m.sender_role, m.message, m.created_at) for m in messages],
        }
    finally:
        db.close()


def _append_ticket_message(ticket_id: int, sender_id: int, role: str, text: str):
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == int(ticket_id)).with_for_update().first()
        if not ticket:
            return None, "Ticket not found."
        if ticket.status in ("Resolved", "Closed"):
            return None, "This ticket is closed. Create a new ticket if you still need help."
        sender = db.query(User).filter(User.telegram_id == int(sender_id)).first()
        if not sender:
            return None, "RainDeals account not found. Send /start and try again."
        if role == "customer" and ticket.user_id != sender.id:
            return None, "This ticket does not belong to your account."
        entry = TicketMessage(
            ticket_id=ticket.id, sender_id=int(sender_id), sender_role=role, message=text
        )
        db.add(entry)
        if role == "admin":
            ticket.admin_response = text
        ticket.updated_at = datetime.utcnow()
        db.flush()
        result = {"id": int(entry.id), "ticket_id": int(ticket.id),
                  "customer_telegram_id": int(db.get(User, ticket.user_id).telegram_id)}
        db.commit()
        return result, None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _admin_ticket_keyboard(bot_username: str, ticket_id: int):
    rows = [[
        InlineKeyboardButton(text="🔄 In Progress", callback_data=f"admin_ticket_progress_{ticket_id}"),
        InlineKeyboardButton(text="✅ Resolve", callback_data=f"admin_ticket_resolve_{ticket_id}"),
        InlineKeyboardButton(text="✖ Close", callback_data=f"admin_ticket_close_{ticket_id}"),
    ]]
    if bot_username:
        rows.append([InlineKeyboardButton(
            text="💬 Reply privately",
            url=f"https://t.me/{bot_username}?start=support_reply_{ticket_id}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("support_view_"))
async def view_support_ticket(callback: CallbackQuery):
    try:
        ticket_id = int(str(callback.data).removeprefix("support_view_"))
    except ValueError:
        await callback.answer("Ticket link is invalid.", show_alert=True)
        return
    ticket = await asyncio.to_thread(_ticket_for_customer, callback.from_user.id, ticket_id)
    if not ticket:
        await callback.answer("Ticket not found.", show_alert=True)
        return
    await callback.answer()
    created = ticket["created_at"].strftime("%d %b %Y, %H:%M UTC") if ticket["created_at"] else "—"
    updated = ticket["updated_at"].strftime("%d %b %Y, %H:%M UTC") if ticket["updated_at"] else "—"
    text = (
        f"🎫 <b>Ticket #{ticket_id}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📂 {escape(ticket['category'])} · {get_priority_emoji(ticket['priority'])} {escape(ticket['priority'])}\n"
        f"📊 {get_status_emoji(ticket['status'])} <b>{escape(ticket['status'])}</b>\n"
        f"🕐 Created {created} · Updated {updated}\n\n"
        f"📝 <b>Your request</b>\n{escape(ticket['message'])}"
    )
    for role, body, created_at in ticket["messages"][1:]:
        who = "⚙️ Status" if role == "system" else "💬 Support" if role == "admin" else "📝 You"
        stamp = created_at.strftime("%d %b %Y, %H:%M UTC") if created_at else ""
        text += f"\n\n━━━━━━━━━━━━━━━━━━━━━━\n{who} · {stamp}\n{escape(body)}"
    if ticket["admin_response"] and not ticket["messages"]:
        text += f"\n\n💬 <b>Support reply</b>\n{escape(ticket['admin_response'])}"
    rows = []
    if ticket["status"] not in ("Resolved", "Closed"):
        rows.append([InlineKeyboardButton(
            text="💬 Add a message", callback_data=f"support_reply_{ticket_id}", style="success"
        )])
    rows.extend([
        [InlineKeyboardButton(text="📋 My Tickets", callback_data="support_my_tickets")],
        [InlineKeyboardButton(text="⬅ Support Center", callback_data="support_ticket")],
    ])
    await show(callback, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("support_reply_"))
async def start_customer_ticket_reply(callback: CallbackQuery, state: FSMContext):
    try:
        ticket_id = int(str(callback.data).removeprefix("support_reply_"))
    except ValueError:
        await callback.answer("Ticket link is invalid.", show_alert=True)
        return
    ticket = await asyncio.to_thread(_ticket_for_customer, callback.from_user.id, ticket_id)
    if not ticket:
        await callback.answer("Ticket not found.", show_alert=True)
        return
    if ticket["status"] in ("Resolved", "Closed"):
        await callback.answer("This ticket is closed. Create a new ticket if you need more help.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(customer_reply_ticket_id=ticket_id)
    await state.set_state(SupportState.customer_reply)
    await show(
        callback,
        f"💬 <b>Reply to Ticket #{ticket_id}</b>\n\nSend one text update (up to 2,000 characters). It will be added to this ticket and sent to the RainDeals support team.\n\nSend /cancel to stop.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✖ Cancel", callback_data="support_reply_cancel")
        ]]),
        state=state,
    )


@router.callback_query(F.data == "support_reply_cancel")
async def cancel_ticket_reply(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Reply cancelled.")
    await show(callback, "Reply cancelled.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅ Support Center", callback_data="support_ticket")
    ]]))


async def _notify_admin_ticket_message(bot, ticket_id: int, message_id: int, sender_id: int, role: str, body: str):
    from config import MAIN_BOT_USERNAME
    from services.notification_outbox import admin_destination, queue_message
    destination = admin_destination()
    if not destination:
        return
    bot_username = str(MAIN_BOT_USERNAME or "").strip().lstrip("@")
    if not bot_username:
        bot_username = str((await bot.get_me()).username or "").strip()
    username = ""
    if role == "customer":
        with SessionLocal() as db:
            user = db.query(User.username).filter(User.telegram_id == int(sender_id)).scalar()
            if user:
                username = f"@{escape(user)}"
    await queue_message(
        bot,
        f"admin-ticket-message:{ticket_id}:{message_id}",
        destination,
        f"{'📝 Customer update' if role == 'customer' else '💬 Admin reply'} · <b>Ticket #{ticket_id}</b>\n"
        f"👤 {username or f'<code>{sender_id}</code>'}\n\n{escape(body[:1800])}{'…' if len(body) > 1800 else ''}",
        reply_markup=_admin_ticket_keyboard(bot_username, ticket_id) if role == "customer" else None,
    )


@router.message(SupportState.customer_reply)
async def save_customer_ticket_reply(message: Message, state: FSMContext):
    body = (message.text or "").strip()
    if not 5 <= len(body) <= 2000:
        await answer_card(message, "Enter a text update between 5 and 2,000 characters, or send /cancel.")
        return
    ticket_id = int((await state.get_data()).get("customer_reply_ticket_id") or 0)
    try:
        result, error = await asyncio.to_thread(
            _append_ticket_message, ticket_id, message.from_user.id, "customer", body
        )
    except Exception:
        result, error = None, "Could not save your message. Please try again."
    if error:
        await state.clear()
        await answer_card(message, escape(error))
        return
    await state.clear()
    await update_card(
        message,
        f"✅ <b>Update added to Ticket #{ticket_id}</b>\n\nSupport has been notified. Replies will appear in Notifications and in this ticket.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 Open ticket", callback_data=f"support_view_{ticket_id}"),
            InlineKeyboardButton(text="🎫 Support Center", callback_data="support_ticket"),
        ]]),
        parse_mode="HTML",
    )
    try:
        await _notify_admin_ticket_message(message.bot, ticket_id, result["id"], message.from_user.id, "customer", body)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Could not queue ticket reply to Admin Notify ticket_id=%s", ticket_id)


@router.message(SupportState.admin_reply)
async def save_admin_ticket_reply(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    body = (message.text or "").strip()
    if not 1 <= len(body) <= 4000:
        await answer_card(message, "Send a text reply up to 4,000 characters, or use /cancel.")
        return
    ticket_id = int((await state.get_data()).get("admin_reply_ticket_id") or 0)
    try:
        result, error = await asyncio.to_thread(
            _append_ticket_message, ticket_id, message.from_user.id, "admin", body
        )
    except Exception:
        result, error = None, "Could not save the reply. Please try again."
    if error:
        await state.clear()
        await answer_card(message, escape(error))
        return
    await state.clear()
    try:
        from services.notification_outbox import queue_message
        customer_notice = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 View support ticket", callback_data=f"support_view_{ticket_id}")
        ]])
        await queue_message(
            message.bot,
            f"ticket-reply:{ticket_id}:{result['id']}",
            result["customer_telegram_id"],
            f"💬 <b>Support replied to Ticket #{ticket_id}</b>\n\n{escape(body)}",
            reply_markup=customer_notice,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Could not notify customer about ticket reply ticket_id=%s", ticket_id)
    try:
        await _notify_admin_ticket_message(message.bot, ticket_id, result["id"], message.from_user.id, "admin", body)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Could not notify Admin Notify about ticket reply ticket_id=%s", ticket_id)
    await update_card(
        message,
        f"✅ Reply sent for Ticket #{ticket_id}. The customer will see it in RainDeals Notifications.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅ Admin Panel", callback_data="admin_panel")
        ]]),
        parse_mode="HTML",
    )


# =====================================================
# ADMIN COMMANDS — COLORED ACTIONS
# =====================================================

@router.callback_query(F.data.startswith("admin_ticket_"))
async def admin_ticket_action(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Admin access only.", show_alert=True)
        return

    parts = callback.data.split("_")
    if len(parts) != 4 or not parts[3].isdigit():
        await callback.answer("Invalid support action.", show_alert=True)
        return
    action = parts[2]
    ticket_id = int(parts[3])

    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()

        if not ticket:
            await callback.answer("❌ Ticket not found.", show_alert=True)
            return

        status_map = {
            "progress": "In Progress",
            "resolve": "Resolved",
            "close": "Closed",
        }

        if action in status_map:
            old_status = ticket.status
            new_status = status_map[action]
            if old_status == new_status:
                await callback.answer(f"Ticket is already {new_status}.", show_alert=True)
                return
            ticket.status = new_status
            ticket.updated_at = datetime.utcnow()
            user = db.get(User, ticket.user_id)
            customer_telegram_id = int(user.telegram_id) if user else None
            db.add(TicketMessage(
                ticket_id=ticket.id,
                sender_id=int(callback.from_user.id),
                sender_role="system",
                message=f"Ticket status changed: {old_status} → {new_status}.",
            ))
            db.commit()

            if customer_telegram_id:
                notify_text = (
                    f"🎫 <b>Support ticket #{ticket_id} updated</b>\n\n"
                    f"Status: <b>{escape(old_status)} → {escape(new_status)}</b>\n\n"
                    + ("✅ Your issue was resolved. If it still needs attention, reply from the ticket details."
                       if new_status == "Resolved" else
                       "🔄 Our team is working on your issue."
                       if new_status == "In Progress" else
                       "This ticket was closed.")
                )
                try:
                    from services.notification_outbox import queue_message
                    await queue_message(
                        callback.bot,
                        f"ticket-status:{ticket_id}:{new_status}",
                        customer_telegram_id,
                        notify_text,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="📋 View ticket", callback_data=f"support_view_{ticket_id}")
                        ]]),
                    )
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("Could not queue customer ticket status update ticket_id=%s", ticket_id)

            try:
                from services.notification_outbox import admin_destination, queue_message
                destination = admin_destination()
                if destination:
                    await queue_message(
                        callback.bot,
                        f"admin-ticket-status:{ticket_id}:{new_status}",
                        destination,
                        f"🎫 <b>Ticket #{ticket_id} status changed</b>\n"
                        f"{escape(old_status)} → <b>{escape(new_status)}</b>\n"
                        f"Changed by <code>{callback.from_user.id}</code>.",
                    )
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Could not queue Admin Notify ticket status ticket_id=%s", ticket_id)

            try:
                if callback.message:
                    old_text = callback.message.text or "Support ticket"
                    await callback.message.edit_text(
                        old_text + f"\n\n━━━━━━━━━━━━━━━━━━━━━━\n✅ Status: <b>{escape(new_status)}</b>",
                        reply_markup=None,
                        parse_mode="HTML",
                    )
            except Exception:
                pass
            await callback.answer(f"Ticket #{ticket_id}: {new_status}", show_alert=True)
        else:
            await callback.answer("Use the Reply privately link in Admin Notify to send a customer reply.", show_alert=True)

    finally:
        db.close()
