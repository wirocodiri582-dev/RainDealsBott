from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    category: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        default="General",
    )

    priority: Mapped[Optional[str]] = mapped_column(  # ← ADD THIS
        String(20),
        nullable=True,
        default="Medium",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="Open",
    )

    admin_response: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # Relationship
    user = relationship("User", backref="tickets")

    def __repr__(self) -> str:
        return (
            f"<Ticket id={self.id} "
            f"user_id={self.user_id} "
            f"status={self.status}>"
        )


class TicketMessage(Base):
    """Append-only support conversation messages for customer/admin replies."""

    __tablename__ = "ticket_messages"
    __table_args__ = (
        Index("ix_ticket_messages_ticket_created", "ticket_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticket_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False
    )
    sender_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_role: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
