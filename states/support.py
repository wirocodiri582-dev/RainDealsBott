"""
states/support.py

States for support ticket flow.
"""

from aiogram.fsm.state import State, StatesGroup


class SupportState(StatesGroup):
    waiting_message = State()
    tracking_ticket = State()
    customer_reply = State()
    admin_reply = State()
