"""Private submissions from people who want to supply RainDeals products."""
from sqlalchemy import BigInteger, Column, DateTime, Integer, Numeric, String, Text, func, Index

from database import Base


class VendorOffer(Base):
    __tablename__ = "bot_vendor_offers"
    __table_args__ = (
        Index("ix_vendor_offer_queue", "status", "id"),
        Index("ix_vendor_offer_owner", "telegram_id", "id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(Integer, nullable=True, index=True)
    telegram_id = Column(BigInteger, nullable=False)
    bot_id = Column(BigInteger, nullable=False)
    product_name = Column(String(120), nullable=False)
    offer_description = Column(Text, nullable=False)
    delivery_instructions = Column(Text, nullable=False)
    # Supply credentials and payout destination are encrypted by the service.
    supply_ciphertext = Column(Text, nullable=False)
    payout_method = Column(String(24), nullable=False)
    payout_ciphertext = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="submitted", server_default="submitted")
    approved_amount = Column(Numeric(20, 8), nullable=True)
    admin_id = Column(BigInteger, nullable=True)
    admin_note = Column(Text, nullable=True)
    payout_reference = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    paid_at = Column(DateTime, nullable=True)


class VendorRequest(Base):
    """Admin-posted product needs that vendors can answer from Telegram."""
    __tablename__ = "bot_vendor_requests"
    __table_args__ = (Index("ix_vendor_request_status", "status", "id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(BigInteger, nullable=False)
    bot_id = Column(BigInteger, nullable=False)
    product_name = Column(String(120), nullable=False)
    requested_details = Column(Text, nullable=False)
    delivery_requirements = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="open", server_default="open")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
