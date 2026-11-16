from datetime import datetime
from html import escape

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func

from database import SessionLocal
from models.user import User
from models.referral import Referral
from config import ADMIN_IDS, MINI_APP_URL
from utils.ui import show, answer_card

router = Router()


@router.callback_query(F.data.regexp(r"^admin_referral_approve_\d{1,15}$"))
async def approve_referral_review(callback: CallbackQuery):
    """Let an admin manually clear a shared-network false positive."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Admin access only.", show_alert=True)
        return
    try:
        referred_id = int(str(callback.data).removeprefix("admin_referral_approve_"))
    except ValueError:
        await callback.answer("Referral review link is invalid.", show_alert=True)
        return

    from services.freebie_referrals import verified_referral_count
    from services.notification_outbox import enqueue, enqueue_admin
    from models.product import Product

    db = SessionLocal()
    try:
        referred = db.query(User).filter(User.telegram_id == referred_id).with_for_update().first()
        if not referred or not referred.referred_by or not referred.miniapp_verified_at:
            await callback.answer("This referral is no longer eligible for review.", show_alert=True)
            return
        if referred.miniapp_referral_review_approved_at:
            await callback.answer("This referral was already approved.", show_alert=True)
            return

        referred.miniapp_referral_review_approved_at = datetime.utcnow()
        referred.miniapp_referral_reviewed_by = int(callback.from_user.id)
        referrer = db.query(User).filter(
            User.telegram_id == int(referred.referred_by)
        ).first()
        count = verified_referral_count(db, int(referred.referred_by))
        offers = db.query(Product.name, Product.freebie_referrals_required).filter(
            Product.is_active.is_(True),
            Product.price == 0,
            Product.freebie_referrals_required > 0,
        ).order_by(Product.freebie_referrals_required.asc(), Product.id.asc()).all()
        unlocked = [str(name or "Freebie") for name, required in offers if int(required or 0) <= count]
        pending = [(str(name or "Freebie"), int(required or 0)) for name, required in offers if int(required or 0) > count]
        lines = [
            f"✅ <b>Referral review approved</b> · Account <code>{referred_id}</code>",
            f"The referral now counts toward your freebie progress: <b>{count}</b> verified referral(s).",
        ]
        if unlocked and referrer and referrer.miniapp_verified_at:
            lines.append("🎁 <b>Available to claim:</b> " + ", ".join(escape(name) for name in unlocked[:8]))
            lines.append("Open <b>Freebies</b> in the RainDeals bot to check stock and claim.")
        elif unlocked:
            lines.append("🎁 Referral progress meets the offer requirement. The referrer must verify their own account in the RainDeals Store before claiming.")
        elif pending:
            lines.append(f"Next unlock: <b>{count}/{pending[0][1]}</b> verified referrals.")
        enqueue(
            db,
            f"referral-review:{referred_id}",
            int(referred.referred_by),
            "\n\n".join(lines),
        )
        enqueue_admin(
            db,
            f"referral-review-approved:{referred_id}",
            f"✅ Referral account <code>{referred_id}</code> approved by admin <code>{callback.from_user.id}</code>. "
            f"Current eligible count for referrer <code>{int(referred.referred_by)}</code>: <b>{count}</b>.",
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("Referral approved and progress updated.")


# =====================================================
# REFERRALS — COMPLETE SYSTEM
# =====================================================

def get_referral_emoji(count: int) -> str:
    """Get appropriate emoji based on referral count"""
    if count == 0:
        return "🌱"
    elif count <= 3:
        return "🌿"
    elif count <= 10:
        return "🪴"
    elif count <= 25:
        return "🌳"
    elif count <= 50:
        return "🏆"
    elif count <= 100:
        return "👑"
    else:
        return "🌟"


def get_referral_title(count: int) -> str:
    """Get title based on referral count"""
    if count == 0:
        return "🔰 Beginner"
    elif count <= 3:
        return "🌱 Sprout"
    elif count <= 10:
        return "🌿 Growing"
    elif count <= 25:
        return "🌳 Established"
    elif count <= 50:
        return "💎 Elite"
    elif count <= 100:
        return "👑 Royal"
    else:
        return "🌟 Legendary"


def get_progress_bar(percentage: float, length: int = 12) -> str:
    """Create a visual progress bar"""
    filled = int(length * percentage / 100)
    return f"{'▰' * filled}{'▱' * (length - filled)} {percentage:.1f}%"


@router.callback_query(F.data == "referrals_menu")
async def referrals(callback: CallbackQuery):
    db = SessionLocal()

    try:
        user = (
            db.query(User)
            .filter(User.telegram_id == callback.from_user.id)
            .first()
        )

        if not user:
            await callback.answer("❌ User not found.", show_alert=True)
            return

        total_referrals = user.total_referrals
        from services.freebie_referrals import verified_referral_count
        miniapp_verified_referrals = verified_referral_count(db, callback.from_user.id)
        # Use the display property (safe float conversion at the last moment)
        earnings = user.referral_earnings_display
        referral_code = user.referral_code

        # Calculate referral level progress
        if total_referrals >= 100:
            next_level = "MAX"
            progress = 100
            referrals_needed = 0
        elif total_referrals >= 50:
            next_level = "🌟 Legendary (100+)"
            progress = ((total_referrals - 50) / 50) * 100
            referrals_needed = 100 - total_referrals
        elif total_referrals >= 25:
            next_level = "👑 Royal (50+)"
            progress = ((total_referrals - 25) / 25) * 100
            referrals_needed = 50 - total_referrals
        elif total_referrals >= 10:
            next_level = "💎 Elite (25+)"
            progress = ((total_referrals - 10) / 15) * 100
            referrals_needed = 25 - total_referrals
        elif total_referrals >= 3:
            next_level = "🌳 Established (10+)"
            progress = ((total_referrals - 3) / 7) * 100
            referrals_needed = 10 - total_referrals
        else:
            next_level = "🌿 Growing (3+)"
            progress = (total_referrals / 3) * 100 if total_referrals > 0 else 0
            referrals_needed = 3 - total_referrals

        # Get recent referrals from the Referral table
        recent_referrals = (
            db.query(Referral)
            .filter(Referral.referrer_id == user.id)
            .order_by(Referral.created_at.desc())
            .limit(5)
            .all()
        )

        # Leaderboard position
        leaderboard_position = (
            db.query(func.count())
            .select_from(User)
            .filter(User.total_referrals > user.total_referrals)
            .scalar()
        ) + 1

        total_users = db.query(func.count(User.id)).scalar()
        percentile = (
            ((total_users - leaderboard_position) / total_users) * 100
            if total_users > 0
            else 0
        )

        bot = await callback.bot.get_me()
        referral_link = f"https://t.me/{bot.username}?start={referral_code}"

        emoji = get_referral_emoji(total_referrals)
        title = get_referral_title(total_referrals)

        text = (
            f"{emoji} <b>Referral Dashboard</b> {emoji}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Your Rank:</b> <code>{title}</code>\n"
            f"🏅 <b>Position:</b> #{leaderboard_position} "
            f"(Top {percentile:.1f}%)\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Statistics</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 <b>Total Referrals:</b> {total_referrals}\n"
            f"🛍 <b>Mini Store verified:</b> {miniapp_verified_referrals}\n"
            f"🔐 <b>Your Store verification:</b> {'✅ Complete' if user.miniapp_verified_at else '⏳ Needed to claim referral freebies'}\n"
            f"💰 <b>Total Earnings:</b> ${earnings:,.2f}\n"
        )

        if next_level != "MAX":
            level_name = next_level.split("(")[0].strip() if "(" in next_level else next_level
            text += (
                f"📈 <b>Progress to {level_name}:</b>\n"
                f"   {get_progress_bar(progress)}\n"
            )
        else:
            text += "📈 <b>Progress:</b> MAX LEVEL! 🎉\n"

        if referrals_needed > 0:
            text += f"\n✨ <b>{referrals_needed}</b> more referrals needed!\n"

        # Recent referrals
        if recent_referrals:
            text += (
                f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 <b>Recent Referrals</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
            )

            icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            for i, ref in enumerate(recent_referrals):
                referred_user = db.query(User).filter(User.id == ref.referred_id).first()
                if referred_user:
                    icon = icons[i] if i < 3 else "👤"
                    username = (
                        f"@{referred_user.username}"
                        if referred_user.username
                        else f"User#{referred_user.id}"
                    )
                    text += f"{icon} {username}\n"

        text += (
            f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 <b>Your Referral Link:</b>\n"
            f"<code>{referral_link}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💡 <i>Share your link to earn rewards!</i>\n"
            f"🎁 <i>Freebie referrals count after your friend opens the RainDeals Mini Store.</i>\n"
            f"🎁 <i>You earn 10% of your referrals' activity.</i>"
        )

        builder = InlineKeyboardBuilder()

        if not user.miniapp_verified_at:
            if MINI_APP_URL:
                builder.row(InlineKeyboardButton(
                    text="🛍 Verify in RainDeals Store",
                    web_app=WebAppInfo(url=MINI_APP_URL),
                    style="success",
                ))
            else:
                builder.row(InlineKeyboardButton(
                    text="🛍 Open Store from Main Menu",
                    callback_data="main_menu",
                    style="success",
                ))

        builder.row(
            InlineKeyboardButton(
                text="📤 Share Link",
                switch_inline_query=referral_link,
            ),
            InlineKeyboardButton(
                text="📋 Copy Link",
                callback_data="copy_referral",
            ),
        )

        builder.row(
            InlineKeyboardButton(
                text="🏆 Leaderboard",
                callback_data="referral_leaderboard",
            ),
            InlineKeyboardButton(
                text="ℹ️ How It Works",
                callback_data="referral_info",
            ),
        )

        builder.row(
            InlineKeyboardButton(
                text="📊 Detailed Stats",
                callback_data="referral_stats",
            ),
        )

        builder.row(
            InlineKeyboardButton(
                text="⬅ Back to Main Menu",
                callback_data="main_menu",
            ),
        )

        await show(callback, text, reply_markup=builder.as_markup())

    finally:
        db.close()

    await callback.answer()


@router.callback_query(F.data == "copy_referral")
async def copy_referral(callback: CallbackQuery):
    """Send referral link as a copyable message."""
    db = SessionLocal()
    try:
        user = (
            db.query(User)
            .filter(User.telegram_id == callback.from_user.id)
            .first()
        )
        if user:
            bot = await callback.bot.get_me()
            referral_link = f"https://t.me/{bot.username}?start={user.referral_code}"

            await answer_card(callback,
                f"📋 <b>Your Referral Link:</b>\n\n"
                f"<code>{referral_link}</code>\n\n"
                f"<i>Tap the link above to copy it!</i>"
            )
            await callback.answer("✅ Link sent! Tap to copy.", show_alert=True)
        else:
            await callback.answer("❌ User not found.", show_alert=True)
    finally:
        db.close()


@router.callback_query(F.data == "referral_leaderboard")
async def referral_leaderboard(callback: CallbackQuery):
    """Show top 10 referrers."""
    db = SessionLocal()
    try:
        top_referrers = (
            db.query(User)
            .filter(User.total_referrals > 0)
            .order_by(User.total_referrals.desc())
            .limit(10)
            .all()
        )

        current_user = (
            db.query(User)
            .filter(User.telegram_id == callback.from_user.id)
            .first()
        )

        text = (
            "🏆 <b>Referral Leaderboard</b> 🏆\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )

        medals = ["🥇", "🥈", "🥉"]

        for i, user in enumerate(top_referrers):
            medal = medals[i] if i < 3 else f"{i + 1}."
            username = (
                f"@{user.username}" if user.username else f"User {user.telegram_id}"
            )
            is_you = " 👈" if current_user and user.id == current_user.id else ""

            text += (
                f"{medal} <b>{username}</b>{is_you}\n"
                f"   └ 👥 {user.total_referrals} referrals | "
                f"💰 ${user.referral_earnings_display:,.2f}\n"
            )

        if current_user and current_user not in top_referrers:
            position = (
                db.query(func.count())
                .select_from(User)
                .filter(User.total_referrals > current_user.total_referrals)
                .scalar()
            ) + 1

            text += (
                f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📍 <b>Your Position:</b> #{position}\n"
                f"   👥 {current_user.total_referrals} referrals\n"
            )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="⬅ Back", callback_data="referrals_menu"),
            InlineKeyboardButton(text="🔄 Refresh", callback_data="referral_leaderboard"),
        )

        await show(callback, text, reply_markup=builder.as_markup())
    finally:
        db.close()
    await callback.answer()


@router.callback_query(F.data == "referral_info")
async def referral_info(callback: CallbackQuery):
    """Show how referrals work."""
    text = (
        "ℹ️ <b>How Referrals Work</b> ℹ️\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎯 <b>Share & Earn</b>\n"
        "Share your unique referral link with friends. "
        "When they join using your link, you become their referrer!\n\n"
        "💰 <b>Earning Rewards</b>\n"
        "• Earn <b>10%</b> of your referrals' activity\n"
        "• Rewards are credited automatically\n"
        "• No limit on how much you can earn!\n\n"
        "🌟 <b>Referral Levels</b>\n"
        "🌱 Sprout: 1-3 referrals\n"
        "🌿 Growing: 4-10 referrals\n"
        "🌳 Established: 11-25 referrals\n"
        "💎 Elite: 26-50 referrals\n"
        "👑 Royal: 51-100 referrals\n"
        "🌟 Legendary: 100+ referrals\n\n"
        "📊 <b>Leaderboard</b>\n"
        "Compete with others on the leaderboard! "
        "Top referrers get special recognition.\n\n"
        "💡 <b>Tips</b>\n"
        "• Share in relevant groups\n"
        "• Create helpful content\n"
        "• Be active in the community\n"
        "• Your link never expires!\n\n"
        "🚫 <b>Rules</b>\n"
        "• No spam or fake accounts\n"
        "• One account per person\n"
        "• Violations result in ban"
    )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅ Back to Referrals", callback_data="referrals_menu"),
        InlineKeyboardButton(text="🎯 Start Referring", callback_data="referrals_menu"),
    )

    await show(callback, text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "referral_stats")
async def referral_stats(callback: CallbackQuery):
    """Show detailed referral statistics and achievements."""
    db = SessionLocal()
    try:
        user = (
            db.query(User)
            .filter(User.telegram_id == callback.from_user.id)
            .first()
        )

        if not user:
            await callback.answer("❌ User not found.", show_alert=True)
            return

        avg_earning = (
            user.referral_earnings_display / user.total_referrals
            if user.total_referrals > 0
            else 0
        )

        text = (
            "📊 <b>Detailed Referral Statistics</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 <b>Total Referrals:</b> {user.total_referrals}\n"
            f"💰 <b>Total Earnings:</b> ${user.referral_earnings_display:,.2f}\n"
            f"📈 <b>Avg. Earning/Referral:</b> ${avg_earning:,.2f}\n"
            f"🔗 <b>Referral Code:</b> <code>{user.referral_code}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 <b>Achievements</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
        )

        achievements = []
        if user.total_referrals >= 1:
            achievements.append("✅ First Referral")
        if user.total_referrals >= 5:
            achievements.append("✅ 5 Referrals Milestone")
        if user.total_referrals >= 10:
            achievements.append("✅ 10 Referrals Club")
        if user.total_referrals >= 25:
            achievements.append("✅ Referral Master (25+)")
        if user.total_referrals >= 50:
            achievements.append("✅ Referral Expert (50+)")
        if user.total_referrals >= 100:
            achievements.append("🌟 Referral Legend (100+)")
        if user.referral_earnings_display >= 100:
            achievements.append("💵 $100 Earnings")

        if achievements:
            for a in achievements:
                text += f"• {a}\n"
        else:
            text += "• No achievements yet\n"

        text += "\n💡 <i>Keep referring to unlock more achievements!</i>"

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="⬅ Back", callback_data="referrals_menu"))

        await show(callback, text, reply_markup=builder.as_markup())
    finally:
        db.close()
    await callback.answer()
