import logging
import threading
import time
from datetime import datetime, timezone
from html import escape

from database import SessionLocal, engine
from models.maintenance_setting import MaintenanceSetting

logger = logging.getLogger(__name__)
_cache_lock = threading.Lock()
_cached_value: bool | None = None
_cached_at = 0.0
_cache_ttl = 1.0


def ensure_maintenance_schema() -> None:
    try:
        MaintenanceSetting.__table__.create(bind=engine, checkfirst=True)
    except Exception:
        # Multiple replicas can race through checkfirst. Accept that race only
        # when the table is now present; otherwise fail startup visibly.
        from sqlalchemy import inspect
        if not inspect(engine).has_table(MaintenanceSetting.__tablename__):
            raise


def is_maintenance_enabled() -> bool:
    global _cached_value, _cached_at
    now = time.monotonic()
    with _cache_lock:
        if _cached_value is not None and now - _cached_at < _cache_ttl:
            return _cached_value
    db = SessionLocal()
    try:
        setting = db.query(MaintenanceSetting).filter(MaintenanceSetting.id == 1).first()
        value = bool(setting and setting.enabled)
        with _cache_lock:
            _cached_value, _cached_at = value, time.monotonic()
        return value
    except Exception:
        # A database outage must not silently disable an explicitly enabled
        # maintenance lock. Admins are exempt in the middleware.
        logger.exception("Could not read maintenance-mode setting; failing closed")
        with _cache_lock:
            _cached_value, _cached_at = True, time.monotonic()
        return True
    finally:
        db.close()


def is_customer_store_paused() -> bool:
    """Honor the manual lock, Manager shop switch, and an optional UTC window."""
    if is_maintenance_enabled():
        return True
    try:
        from services.runtime_settings import get_setting, setting_bool
        if not setting_bool("shop_enabled", True):
            return True
        start_raw = str(get_setting("maintenance_schedule_start", "") or "").strip()
        end_raw = str(get_setting("maintenance_schedule_end", "") or "").strip()
        if not start_raw or not end_raw:
            return False
        def parse_utc(raw):
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        start, end = parse_utc(start_raw), parse_utc(end_raw)
        return start <= now <= end
    except Exception:
        logger.exception("Could not evaluate Manager shop pause schedule; honoring manual maintenance state")
        return False


def customer_store_pause_message(default: str) -> str:
    try:
        from services.runtime_settings import get_setting
        custom = str(get_setting("shop_pause_message", "") or "").strip()
        return escape(custom[:1000]) if custom else default
    except Exception:
        return default


def set_maintenance_enabled(enabled: bool) -> None:
    global _cached_value, _cached_at
    db = SessionLocal()
    try:
        setting = db.query(MaintenanceSetting).filter(MaintenanceSetting.id == 1).first()
        if setting is None:
            setting = MaintenanceSetting(id=1, enabled=enabled)
            db.add(setting)
        else:
            setting.enabled = enabled
        db.commit()
        with _cache_lock:
            _cached_value, _cached_at = bool(enabled), time.monotonic()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
