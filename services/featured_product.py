"""Admin-controlled product displayed as the second tile on the home menu."""

from sqlalchemy import select

from database import SessionLocal
from models.bot_menu_setting import BotMenuSetting
from models.product import Product


FEATURED_PRODUCT_KEY = "featured_product_id"


def featured_product(db=None) -> dict | None:
    """Return the active featured product label and id, if one is configured."""
    owns_session = db is None
    db = db if db is not None else SessionLocal()
    try:
        value = db.execute(
            select(BotMenuSetting.setting_value).where(
                BotMenuSetting.setting_key == FEATURED_PRODUCT_KEY
            )
        ).scalar_one_or_none()
        if not value or not str(value).isdigit():
            return None
        row = db.execute(
            select(Product.id, Product.name, Product.icon)
            .where(Product.id == int(value), Product.is_active.is_(True))
        ).first()
        if not row:
            return None
        return {"id": int(row.id), "name": str(row.name), "icon": str(row.icon or "📦")}
    finally:
        if owns_session:
            db.close()


def candidates(page: int = 0, page_size: int = 10) -> tuple[list[dict], bool]:
    """Return active paid and free products for the admin chooser."""
    page = max(0, int(page))
    page_size = max(1, min(20, int(page_size)))
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Product.id, Product.name, Product.icon, Product.price)
            .where(Product.is_active.is_(True))
            .order_by(Product.id.desc())
            .offset(page * page_size)
            .limit(page_size + 1)
        ).all()
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        return ([
            {"id": int(row.id), "name": str(row.name), "icon": str(row.icon or "📦"),
             "free": float(row.price or 0) == 0}
            for row in rows
        ], has_more)
    finally:
        db.close()


def set_featured_product(product_id: int | None, admin_id: int) -> str | None:
    """Set or clear the featured item; return an error string on invalid input."""
    db = SessionLocal()
    try:
        row = None
        if product_id is not None:
            row = db.query(Product).filter(
                Product.id == int(product_id), Product.is_active.is_(True)
            ).first()
            if row is None:
                return "That product is unavailable or disabled."

        setting = db.get(BotMenuSetting, FEATURED_PRODUCT_KEY)
        if product_id is None:
            if setting:
                db.delete(setting)
        elif setting:
            setting.setting_value = str(int(product_id))
            setting.updated_by = int(admin_id)
        else:
            db.add(BotMenuSetting(
                setting_key=FEATURED_PRODUCT_KEY,
                setting_value=str(int(product_id)),
                updated_by=int(admin_id),
            ))
        db.commit()
        return None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
