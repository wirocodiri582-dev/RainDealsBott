# keyboards/deposit.py

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def _configured_methods():
    import config
    from services.runtime_settings import (
        payment_method_enabled, payment_method_order, payment_value,
    )
    from services.stars_payments import stars_rate

    values = {
        "BEP20": ("💎 USDT (BEP20)", "deposit_bep20", payment_value("payment_bep20_address", getattr(config, "BEP20_ADDRESS", ""))),
        "POLYGON": ("💎 USDT (Polygon)", "deposit_polygon", payment_value("payment_polygon_address", getattr(config, "POLYGON_ADDRESS", ""))),
        "BINANCE": ("🔴 Binance Pay", "deposit_binance", payment_value("payment_binance_pay_id", getattr(config, "BINANCE_PAY_ID", ""))),
        "UPI": ("💵 UPI (₹ INR)", "deposit_upi", payment_value("payment_upi_id", getattr(config, "UPI_ID", ""))),
        "STARS": ("⭐ Telegram Stars", "deposit_stars", str(stars_rate() or "")),
    }
    return [
        (key, *values[key])
        for key in payment_method_order()
        if key in values and payment_method_enabled(key) and str(values[key][2] or "").strip()
    ]


def get_deposit_menu() -> InlineKeyboardMarkup:
    """Deposit method selection keyboard with real Telegram button colors.

    Uses Bot API 9.4+ style parameter:
      - 'primary' (blue) — BEP20, UPI
      - 'success' (green) — Polygon, Redeem Promocode, My Deposits
      - 'danger' (red) — Binance Pay

    Deposit options grouped together, Main Menu separated below.
    """
    methods = _configured_methods()
    style = {"BEP20": "primary", "POLYGON": "success", "BINANCE": "danger", "UPI": "primary", "STARS": "primary"}
    buttons = [
        InlineKeyboardButton(text=label, callback_data=callback, style=style[key])
        for key, label, callback, _value in methods
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows += [
            [
                InlineKeyboardButton(
                    text="🏷️ Redeem Promocode",
                    callback_data="deposit_promo",
                    style="success"
                ),
            ],
            # ── History ──
            [
                InlineKeyboardButton(
                    text="📜 My Deposits",
                    callback_data="my_deposits",
                    style="success"
                ),
                InlineKeyboardButton(
                    text="💸 My Spends",
                    callback_data="my_spends",
                    style="primary"
                ),
            ],
            # ── Navigation ──
            [
                InlineKeyboardButton(
                    text="🏠 Main Menu",
                    callback_data="main_menu",
                ),
            ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
