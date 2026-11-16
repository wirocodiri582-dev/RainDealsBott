"""Private vendor intake, admin review and audited payout controls."""
import asyncio
import logging
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton as Button, InlineKeyboardMarkup, Message, BufferedInputFile

from config import ADMIN_IDS
from services import vendor_offers
from services.input_forms import InputError, is_cancel, text_value
from utils.ui import answer_card, show

logger = logging.getLogger(__name__)
router = Router()


class VendorApplication(StatesGroup):
    product_name = State()
    description = State()
    delivery_instructions = State()
    supplied_data = State()
    payout_details = State()


class VendorAdmin(StatesGroup):
    request_product = State()
    request_details = State()
    request_delivery = State()
    payout_amount = State()
    external_reference = State()
    rejection_reason = State()


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "vendor_request_new")
async def vendor_request_new(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(VendorAdmin.request_product)
    await show(
        callback,
        "📣 <b>Ask providers for a product</b>\n\n"
        "This request will be posted to the configured vendor deal channel. Vendors submit their supply and payout preference privately for your review.\n\n"
        "Send the product name (3–120 characters). /cancel to stop.",
        parse_mode="HTML", state=state,
        reply_markup=kb([[Button(text="Cancel", callback_data="admin_panel")]]),
    )


@router.message(VendorAdmin.request_product)
async def vendor_request_product(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "Provider request cancelled.", reply_markup=kb([[Button(text="👑 Admin Panel", callback_data="admin_panel")]]))
        return
    try:
        product_name = text_value(message.text, 120, 3)
    except InputError as error:
        await answer_card(message, str(error))
        return
    await state.update_data(vendor_request_product=product_name)
    await state.set_state(VendorAdmin.request_details)
    await answer_card(message, "Describe exactly what you need from the vendor: plan, duration, region, quantity, account type, or other acceptance criteria (10–1,400 characters). /cancel to stop.")


@router.message(VendorAdmin.request_details)
async def vendor_request_details(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "Provider request cancelled.", reply_markup=kb([[Button(text="👑 Admin Panel", callback_data="admin_panel")]]))
        return
    try:
        details = text_value(message.text, 1400, 10)
    except InputError as error:
        await answer_card(message, str(error))
        return
    await state.update_data(vendor_request_details=details)
    await state.set_state(VendorAdmin.request_delivery)
    await answer_card(message, "Write the delivery content or steps the vendor must provide to the buyer (5–1,400 characters). /cancel to stop.")


@router.message(VendorAdmin.request_delivery)
async def vendor_request_delivery(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    data = await state.get_data()
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "Provider request cancelled.", reply_markup=kb([[Button(text="👑 Admin Panel", callback_data="admin_panel")]]))
        return
    try:
        delivery = text_value(message.text, 1400, 5)
        bot_info = await asyncio.wait_for(message.bot.get_me(), timeout=5)
        request_id = await asyncio.to_thread(
            vendor_offers.create_request,
            message.from_user.id,
            message.bot.id,
            data["vendor_request_product"],
            data["vendor_request_details"],
            delivery,
            bot_info.username or "",
        )
    except (InputError, vendor_offers.VendorConfigurationError) as error:
        await answer_card(message, f"⚠️ {escape(str(error))}", parse_mode="HTML", reply_markup=kb([
            [Button(text="Retry posting", callback_data="vendor_request_retry")],
            [Button(text="👑 Admin Panel", callback_data="admin_panel")],
        ]))
        return
    except Exception:
        logger.exception("Could not create vendor request admin_id=%s", message.from_user.id)
        await answer_card(message, "⚠️ The request could not be posted. Check database and vendor deal-channel configuration, then try again.")
        return
    await state.clear()
    await answer_card(
        message,
        f"✅ Provider request #{request_id} was queued for the vendor deal channel. You’ll get an admin notification when a vendor submits an offer.",
        reply_markup=kb([[Button(text="📦 Review offers", callback_data="vendor_queue_0")],
                         [Button(text="👑 Admin Panel", callback_data="admin_panel")]]),
    )


@router.callback_query(F.data == "vendor_request_retry")
async def vendor_request_retry(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("vendor_request_product") or not data.get("vendor_request_details"):
        await callback.answer("The form expired. Start a new provider request.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(VendorAdmin.request_delivery)
    await show(callback, "Send the buyer delivery content/steps again (5–1,400 characters). /cancel to stop.", state=state)


def _user_summary(offer):
    amount = offer.get("approved_amount")
    payout = f"${Decimal(amount):.8f} USDT" if amount is not None else "Pending admin review"
    status = {
        "submitted": "Waiting for admin review",
        "approved": "Approved · external payout pending",
        "paid": "Paid",
        "rejected": "Declined",
    }.get(offer["status"], offer["status"].title())
    return (
        f"🤝 <b>Vendor offer #{offer['id']}</b>\n"
        f"Product: {escape(offer['product_name'])}\n"
        f"Status: <b>{status}</b>\n"
        f"Payout: {payout} · {escape(offer['payout_method_label'])}"
    )


async def prepare_request_application(state: FSMContext, request_id: int):
    """Prepare the private vendor form reached from a request deep link."""
    request = await asyncio.to_thread(vendor_offers.get_request, request_id)
    if not request or request["status"] != "open":
        return None, kb([[Button(text="🤝 Become a Provider", callback_data="vendor_apply")],
                         [Button(text="🏠 Main Menu", callback_data="main_menu")]])
    await state.clear()
    await state.update_data(
        vendor_request_id=int(request["id"]),
        vendor_product_name=request["product_name"],
        vendor_requested_details=request["requested_details"],
        vendor_requested_delivery=request["delivery_requirements"],
    )
    await state.set_state(VendorApplication.description)
    text = (
        f"🤝 <b>Offer for request #{request['id']}</b>\n"
        f"Product: <b>{escape(request['product_name'])}</b>\n\n"
        f"<b>Requested</b>\n{escape(request['requested_details'][:1000])}\n\n"
        f"<b>Delivery requirements</b>\n{escape(request['delivery_requirements'][:1000])}\n\n"
        "Describe your available product, quantity, and condition (10–1,400 characters). /cancel to stop."
    )
    return text, kb([[Button(text="Cancel", callback_data="main_menu")]])


@router.callback_query(F.data == "vendor_apply")
async def vendor_apply(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await state.set_state(VendorApplication.product_name)
    await show(
        callback,
        "🤝 <b>Become a RainDeals Provider</b>\n\n"
        "Submit a product offer for private admin review. Supply data and payout details are encrypted before being stored.\n\n"
        "Send the product name (3–120 characters). /cancel to stop.",
        parse_mode="HTML",
        state=state,
        reply_markup=kb([[Button(text="📑 My offers", callback_data="vendor_my_offers")],
                         [Button(text="Cancel", callback_data="main_menu")]]),
    )


@router.callback_query(F.data == "vendor_my_offers")
async def vendor_my_offers(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    rows = await asyncio.to_thread(vendor_offers.for_user, callback.from_user.id)
    if not rows:
        await show(callback, "You have not submitted a vendor offer yet.", reply_markup=kb([
            [Button(text="🤝 Submit an offer", callback_data="vendor_apply")],
            [Button(text="🏠 Main Menu", callback_data="main_menu")],
        ]))
        return
    buttons = [[Button(text=f"#{item['id']} · {item['product_name'][:45]} · {item['status']}",
                       callback_data=f"vendor_user_offer_{item['id']}")] for item in rows]
    buttons.append([Button(text="🤝 Submit another offer", callback_data="vendor_apply")])
    buttons.append([Button(text="🏠 Main Menu", callback_data="main_menu")])
    await show(callback, "🤝 <b>Your provider offers</b>\nTap an offer to view its status.",
               parse_mode="HTML", reply_markup=kb(buttons))


@router.callback_query(F.data.regexp(r"^vendor_user_offer_\d+$"))
async def vendor_user_offer(callback: CallbackQuery):
    offer_id = int(callback.data.rsplit("_", 1)[1])
    offer = await asyncio.to_thread(vendor_offers.get_for_user, offer_id, callback.from_user.id)
    if not offer:
        await callback.answer("Offer not found.", show_alert=True)
        return
    await callback.answer()
    await show(callback, _user_summary(offer), parse_mode="HTML", reply_markup=kb([
        [Button(text="⬅ My offers", callback_data="vendor_my_offers")],
        [Button(text="🏠 Main Menu", callback_data="main_menu")],
    ]))


@router.message(VendorApplication.product_name)
async def vendor_product_name(message: Message, state: FSMContext):
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "Vendor submission cancelled.")
        return
    try:
        value = text_value(message.text, 120, 3)
    except InputError as error:
        await answer_card(message, str(error))
        return
    await state.update_data(vendor_product_name=value)
    await state.set_state(VendorApplication.description)
    await answer_card(message, "Describe what you are offering and its quantity/condition (10–1,400 characters). /cancel to stop.")


@router.message(VendorApplication.description)
async def vendor_description(message: Message, state: FSMContext):
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "Vendor submission cancelled.")
        return
    try:
        value = text_value(message.text, 1400, 10)
    except InputError as error:
        await answer_card(message, str(error))
        return
    await state.update_data(vendor_description=value)
    await state.set_state(VendorApplication.delivery_instructions)
    await answer_card(message, "Write the delivery instructions the buyer should receive (5–1,400 characters). /cancel to stop.")


@router.message(VendorApplication.delivery_instructions)
async def vendor_delivery_instructions(message: Message, state: FSMContext):
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "Vendor submission cancelled.")
        return
    try:
        value = text_value(message.text, 1400, 5)
    except InputError as error:
        await answer_card(message, str(error))
        return
    await state.update_data(vendor_delivery_instructions=value)
    await state.set_state(VendorApplication.supplied_data)
    await answer_card(
        message,
        "🔐 Send the supplied account/code data for admin review (5–8,000 characters).\n\n"
        "This is sent privately to the bot, encrypted in the database, and excluded from the deal-channel notice. /cancel to stop.",
    )


@router.message(VendorApplication.supplied_data)
async def vendor_supplied_data(message: Message, state: FSMContext):
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "Vendor submission cancelled.")
        return
    try:
        supplied = text_value(message.text, 8000, 5)
    except InputError as error:
        await answer_card(message, str(error))
        return
    await state.update_data(vendor_supplied_data=supplied)
    await state.set_state(None)
    await answer_card(
        message,
        "Choose how you prefer to receive an approved payout. External payments are sent manually by the admin; the bot records the payout reference.",
        reply_markup=kb([
            [Button(text="💰 RainDeals wallet", callback_data="vendor_method_internal")],
            [Button(text="🔴 Binance Pay", callback_data="vendor_method_binance_pay")],
            [Button(text="🟡 USDT BEP20", callback_data="vendor_method_usdt_bep20")],
            [Button(text="🟣 USDT Polygon", callback_data="vendor_method_usdt_polygon")],
            [Button(text="🌐 Other external method", callback_data="vendor_method_other")],
            [Button(text="Cancel", callback_data="main_menu")],
        ]),
    )


@router.callback_query(F.data.regexp(r"^vendor_method_(internal|binance_pay|usdt_bep20|usdt_polygon|other)$"))
async def vendor_choose_method(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("vendor_product_name") or not data.get("vendor_supplied_data"):
        await callback.answer("The offer form expired. Start again.", show_alert=True)
        return
    method = callback.data.removeprefix("vendor_method_")
    await callback.answer()
    await state.update_data(vendor_payout_method=method)
    if method == "internal":
        await _show_vendor_confirmation(callback, state, "")
        return
    await state.set_state(VendorApplication.payout_details)
    instructions = {
        "binance_pay": "Send your Binance Pay ID or registered contact.",
        "usdt_bep20": "Send your USDT BEP20 wallet address.",
        "usdt_polygon": "Send your USDT Polygon wallet address.",
        "other": "Send the external payment method and payout details.",
    }[method]
    await show(callback, instructions + "\n\nThis destination is encrypted and only shown to admins in private chat. /cancel to stop.",
               state=state, reply_markup=kb([[Button(text="Cancel", callback_data="main_menu")]]))


@router.message(VendorApplication.payout_details)
async def vendor_payout_details(message: Message, state: FSMContext):
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "Vendor submission cancelled.")
        return
    try:
        details = text_value(message.text, 350, 5)
    except InputError as error:
        await answer_card(message, str(error))
        return
    data = await state.get_data()
    if data.get("vendor_payout_method") in ("usdt_bep20", "usdt_polygon"):
        address = details.strip()
        if not (len(address) == 42 and address.startswith("0x") and all(ch in "0123456789abcdefABCDEF" for ch in address[2:])):
            await answer_card(message, "Send a valid 42-character 0x wallet address, or /cancel.")
            return
    await state.update_data(vendor_payout_details=details)
    await state.set_state(None)
    await _show_vendor_confirmation(message, state, details)


async def _show_vendor_confirmation(target, state: FSMContext, payout_details: str):
    data = await state.get_data()
    method = vendor_offers.PAYOUT_METHODS.get(data.get("vendor_payout_method"), "Unknown")
    text = (
        "🧾 <b>Review your vendor offer</b>\n\n"
        f"Product: <b>{escape(data['vendor_product_name'])}</b>\n"
        f"Offer: {escape(data['vendor_description'][:500])}\n"
        f"Delivery notes: {escape(data['vendor_delivery_instructions'][:500])}\n"
        f"Supply data: <b>provided privately</b>\n"
        f"Payout preference: <b>{escape(method)}</b>\n"
    )
    if data.get("vendor_requested_details"):
        text += f"\nRequested by RainDeals: {escape(data['vendor_requested_details'][:300])}\n"
    if payout_details:
        text += f"Payout destination: <code>{escape(payout_details[:180])}</code>\n"
    text += "\nThe admin will review the supply and set the payout amount. Submit this offer?"
    await answer_card(target, text, parse_mode="HTML", reply_markup=kb([
        [Button(text="✅ Submit offer", callback_data="vendor_submit")],
        [Button(text="❌ Cancel", callback_data="main_menu")],
    ]))


@router.callback_query(F.data == "vendor_submit")
async def vendor_submit(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    required = ("vendor_product_name", "vendor_description", "vendor_delivery_instructions", "vendor_supplied_data", "vendor_payout_method")
    if any(not data.get(key) for key in required):
        await callback.answer("The offer form expired. Start again.", show_alert=True)
        return
    await callback.answer()
    try:
        offer_id = await asyncio.to_thread(
            vendor_offers.submit,
            callback.from_user.id,
            callback.bot.id,
            data["vendor_product_name"],
            data["vendor_description"],
            data["vendor_delivery_instructions"],
            data["vendor_supplied_data"],
            data["vendor_payout_method"],
            data.get("vendor_payout_details"),
            data.get("vendor_request_id"),
        )
    except vendor_offers.VendorConfigurationError:
        logger.exception("Vendor submission encryption is not configured")
        await show(callback, "⚠️ Provider submissions are temporarily unavailable. Ask the admin to configure the vendor data encryption key.",
                   reply_markup=kb([[Button(text="🏠 Main Menu", callback_data="main_menu")]]))
        return
    except (ValueError, InputError) as error:
        await show(callback, escape(str(error)), parse_mode="HTML", reply_markup=kb([
            [Button(text="Try again", callback_data="vendor_apply")],
            [Button(text="🏠 Main Menu", callback_data="main_menu")],
        ]))
        return
    await state.clear()
    await show(callback, f"✅ <b>Offer #{offer_id} submitted</b>\n\nAdmins were notified. Your supply data and payout destination are encrypted and stay private for review.",
               parse_mode="HTML", reply_markup=kb([
                   [Button(text="📑 My offers", callback_data="vendor_my_offers")],
                   [Button(text="🏠 Main Menu", callback_data="main_menu")],
               ]))


@router.callback_query(F.data.regexp(r"^vendor_queue_\d+$"))
async def vendor_queue(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return
    page = max(0, int(callback.data.rsplit("_", 1)[1]))
    await callback.answer()
    total, offers = await asyncio.to_thread(vendor_offers.pending, page)
    rows = [[Button(text=f"#{item['id']} · {item['product_name'][:38]} · {item['status']}",
                    callback_data=f"vendor_review_{item['id']}")] for item in offers]
    nav = []
    if page:
        nav.append(Button(text="⬅ Previous", callback_data=f"vendor_queue_{page-1}"))
    if (page + 1) * 8 < total:
        nav.append(Button(text="Next ➡", callback_data=f"vendor_queue_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([Button(text="⬅ Admin Panel", callback_data="admin_panel")])
    await show(callback, f"📦 <b>Vendor offers</b> · {total} awaiting review or external payout", parse_mode="HTML", reply_markup=kb(rows))


async def _private_admin_only(callback: CallbackQuery) -> bool:
    if callback.message.chat.type == "private":
        return True
    await callback.answer("Open the admin panel in a private chat to review sensitive vendor data.", show_alert=True)
    try:
        await callback.bot.send_message(
            callback.from_user.id,
            "A vendor offer needs your review.",
            reply_markup=kb([[Button(text="Open vendor queue", callback_data="vendor_queue_0")]]),
        )
    except Exception:
        logger.warning("Could not redirect vendor review to admin private chat", exc_info=True)
    return False


@router.callback_query(F.data.regexp(r"^vendor_review_\d+$"))
async def vendor_review(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return
    if not await _private_admin_only(callback):
        return
    offer_id = int(callback.data.rsplit("_", 1)[1])
    await callback.answer()
    await state.clear()
    try:
        offer = await asyncio.to_thread(vendor_offers.get_for_admin, offer_id)
    except vendor_offers.VendorConfigurationError:
        await show(callback, "🔐 Vendor data could not be decrypted. Restore the original stable encryption key before reviewing this offer.",
                   reply_markup=kb([[Button(text="⬅ Vendor offers", callback_data="vendor_queue_0")]]))
        return
    if not offer:
        await show(callback, "Vendor offer not found.", reply_markup=kb([[Button(text="⬅ Vendor offers", callback_data="vendor_queue_0")]]))
        return
    status = {
        "submitted": "Awaiting review",
        "approved": "Approved · external payout pending",
        "paid": "Paid",
        "rejected": "Declined",
    }.get(offer["status"], offer["status"])
    text = (
        f"🤝 <b>Vendor offer #{offer_id}</b>\n"
        f"Status: <b>{escape(status)}</b>\n"
        f"Vendor Telegram ID: <code>{offer['telegram_id']}</code>\n"
        f"Product: <b>{escape(offer['product_name'])}</b>\n\n"
        f"<b>Offer</b>\n{escape(offer['description'][:1000])}\n\n"
        f"<b>Buyer delivery instructions</b>\n{escape(offer['delivery_instructions'][:1000])}\n\n"
        f"<b>Payout preference</b>\n{escape(offer['payout_method_label'])}\n"
        f"<b>Private payout destination</b>\n<code>{escape((offer['payout_details'] or 'RainDeals wallet')[:300])}</code>"
    )
    if offer.get("requested_details"):
        text += (
            f"\n<b>Admin request</b>\n{escape(offer['requested_details'][:800])}\n"
            f"<b>Requested delivery requirements</b>\n{escape(offer['requested_delivery_requirements'][:800])}\n"
        )
    if offer["approved_amount"] is not None:
        text += f"\nApproved amount: <b>${Decimal(offer['approved_amount']):.8f} USDT</b>"
    if offer.get("payout_reference"):
        text += f"\nPayout reference: <code>{escape(offer['payout_reference'])}</code>"
    rows = [[Button(text="🔐 View supplied account data", callback_data=f"vendor_supply_{offer_id}")]]
    if offer["status"] == "submitted":
        rows.append([Button(text="✅ Approve · set payout", callback_data=f"vendor_approve_{offer_id}")])
        rows.append([Button(text="❌ Decline offer", callback_data=f"vendor_reject_{offer_id}")])
    elif offer["status"] == "approved":
        rows.append([Button(text="💸 Confirm external payout sent", callback_data=f"vendor_external_paid_{offer_id}")])
    rows.append([Button(text="🔄 Refresh", callback_data=f"vendor_review_{offer_id}"), Button(text="⬅ Vendor offers", callback_data="vendor_queue_0")])
    await show(callback, text, parse_mode="HTML", reply_markup=kb(rows))


@router.callback_query(F.data.regexp(r"^vendor_supply_\d+$"))
async def vendor_supply(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return
    if not await _private_admin_only(callback):
        return
    offer_id = int(callback.data.rsplit("_", 1)[1])
    try:
        offer = await asyncio.to_thread(vendor_offers.get_for_admin, offer_id)
    except vendor_offers.VendorConfigurationError:
        await callback.answer("Restore the vendor encryption key first.", show_alert=True)
        return
    if not offer:
        await callback.answer("Offer not found.", show_alert=True)
        return
    await callback.answer()
    payload = f"RainDeals vendor offer #{offer_id}\nProduct: {offer['product_name']}\n\n{offer['supplied_data']}"
    if len(escape(offer['supplied_data'])) < 3000:
        await show(callback, f"🔐 <b>Private supply data · Offer #{offer_id}</b>\n\n<code>{escape(offer['supplied_data'])}</code>",
                   parse_mode="HTML", reply_markup=kb([[Button(text="⬅ Offer", callback_data=f"vendor_review_{offer_id}")]]))
    else:
        document = BufferedInputFile(payload.encode("utf-8"), filename=f"vendor-offer-{offer_id}-private.txt")
        await callback.message.answer_document(
            document,
            caption=f"🔐 Private supply data · Offer #{offer_id}. Keep this file within the review team.",
            reply_markup=kb([[Button(text="⬅ Offer", callback_data=f"vendor_review_{offer_id}")]]),
        )


@router.callback_query(F.data.regexp(r"^vendor_approve_\d+$"))
async def vendor_approve_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return
    if not await _private_admin_only(callback):
        return
    offer_id = int(callback.data.rsplit("_", 1)[1])
    await callback.answer()
    await state.clear()
    await state.set_state(VendorAdmin.payout_amount)
    await state.update_data(vendor_offer_id=offer_id)
    await show(callback, f"Enter the approved payout in USDT for offer #{offer_id}.\n"
               "Internal wallet payouts are credited once by the bot. External payouts are marked approved here; send them manually, then record the transfer reference. /cancel to stop.",
               state=state, reply_markup=kb([[Button(text="Cancel", callback_data=f"vendor_review_{offer_id}")]]))


@router.message(VendorAdmin.payout_amount)
async def vendor_approve_amount(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    data = await state.get_data()
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "Payout approval cancelled.", reply_markup=kb([[Button(text="⬅ Offer", callback_data=f"vendor_review_{data.get('vendor_offer_id')}")]]))
        return
    try:
        vendor_offers._amount(message.text)
        error = await asyncio.to_thread(vendor_offers.approve, data["vendor_offer_id"], message.from_user.id, message.text)
    except ValueError as exc:
        await answer_card(message, str(exc))
        return
    if error:
        await state.clear()
        await answer_card(message, error)
        return
    await state.clear()
    offer = await asyncio.to_thread(vendor_offers.get_for_admin, data["vendor_offer_id"])
    if offer and offer["status"] == "approved":
        text = "✅ Approved. Send the external payout, then use Confirm external payout sent and record its reference."
    else:
        text = "✅ Approved and credited to the vendor’s RainDeals wallet."
    await answer_card(message, text, reply_markup=kb([[Button(text="Open offer", callback_data=f"vendor_review_{data['vendor_offer_id']}")]]))


@router.callback_query(F.data.regexp(r"^vendor_reject_\d+$"))
async def vendor_reject_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return
    if not await _private_admin_only(callback):
        return
    offer_id = int(callback.data.rsplit("_", 1)[1])
    await callback.answer()
    await state.set_state(VendorAdmin.rejection_reason)
    await state.update_data(vendor_offer_id=offer_id)
    await show(callback, f"Send a short decline reason for offer #{offer_id}; /cancel to leave it pending.", state=state,
               reply_markup=kb([[Button(text="Cancel", callback_data=f"vendor_review_{offer_id}")]]))


@router.message(VendorAdmin.rejection_reason)
async def vendor_reject_input(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    data = await state.get_data()
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "Offer remains pending.", reply_markup=kb([[Button(text="⬅ Offer", callback_data=f"vendor_review_{data.get('vendor_offer_id')}")]]))
        return
    try:
        reason = text_value(message.text, 1000, 3)
        error = await asyncio.to_thread(vendor_offers.reject, data["vendor_offer_id"], message.from_user.id, reason)
    except InputError as exc:
        await answer_card(message, str(exc))
        return
    await state.clear()
    await answer_card(message, error or "Offer declined and vendor notified.", reply_markup=kb([
        [Button(text="📦 Vendor offers", callback_data="vendor_queue_0")],
        [Button(text="👑 Admin Panel", callback_data="admin_panel")],
    ]))


@router.callback_query(F.data.regexp(r"^vendor_external_paid_\d+$"))
async def vendor_external_paid_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access denied.", show_alert=True)
        return
    if not await _private_admin_only(callback):
        return
    offer_id = int(callback.data.rsplit("_", 1)[1])
    await callback.answer()
    await state.set_state(VendorAdmin.external_reference)
    await state.update_data(vendor_offer_id=offer_id)
    await show(callback, f"After sending the external payout, enter its transaction/payment reference for offer #{offer_id}. /cancel to stop.",
               state=state, reply_markup=kb([[Button(text="Cancel", callback_data=f"vendor_review_{offer_id}")]]))


@router.message(VendorAdmin.external_reference)
async def vendor_external_paid_input(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    data = await state.get_data()
    if is_cancel(message.text):
        await state.clear()
        await answer_card(message, "External payout remains awaiting confirmation.", reply_markup=kb([[Button(text="⬅ Offer", callback_data=f"vendor_review_{data.get('vendor_offer_id')}")]]))
        return
    try:
        reference = text_value(message.text, 255, 2)
        error = await asyncio.to_thread(vendor_offers.mark_external_paid, data["vendor_offer_id"], message.from_user.id, reference)
    except InputError as exc:
        await answer_card(message, str(exc))
        return
    await state.clear()
    await answer_card(message, error or "✅ External payout recorded; vendor notified.", reply_markup=kb([
        [Button(text="📦 Vendor offers", callback_data="vendor_queue_0")],
        [Button(text="👑 Admin Panel", callback_data="admin_panel")],
    ]))
