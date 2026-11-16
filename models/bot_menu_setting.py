"""Small persistent settings used by the Telegram home menu."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class BotMenuSetting(Base):
    __tablename__ = "bot_menu_settings"

    setting_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    setting_value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )
