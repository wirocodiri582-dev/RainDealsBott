"""Short-lived cache for settings managed in the shared Rain Deals Manager."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import threading
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
from sqlalchemy import text

from database import engine

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_cache: dict[str, str] = {}
_loaded_at = 0.0
_ttl_seconds = 2.0


def _read_all(force: bool = False) -> dict[str, str]:
    global _cache, _loaded_at
    now = time.monotonic()
    with _lock:
        if not force and _loaded_at and now - _loaded_at < _ttl_seconds:
            return dict(_cache)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT setting_key, setting_value FROM raindeals_runtime_settings"
            )).mappings().all()
        loaded = {str(row["setting_key"]): str(row["setting_value"] or "") for row in rows}
        with _lock:
            _cache = loaded
            _loaded_at = time.monotonic()
        return dict(loaded)
    except Exception:
        # Prefer the last known values during a brief database interruption.
        # A cold start without the table safely falls back to config defaults.
        with _lock:
            stale = dict(_cache)
            if not _loaded_at:
                _loaded_at = time.monotonic()
        logger.debug("Manager runtime settings unavailable; using cached defaults", exc_info=True)
        return stale


def get_setting(key: str, default: str | None = None) -> str | None:
    return _read_all().get(str(key), default)


def set_local_cache(values: dict[str, str]) -> None:
    """Update this process cache after a local migration or controlled write."""
    global _cache, _loaded_at
    with _lock:
        _cache = {str(k): str(v) for k, v in values.items()}
        _loaded_at = time.monotonic()


def store_usdt_inr_snapshot(rate: Decimal) -> None:
    """Persist the latest successful Manager-source result for the Manager UI."""
    global _cache, _loaded_at
    snapshot = {
        "usdt_inr_last_rate": format(rate, "f"),
        "usdt_inr_last_fetched_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    try:
        with engine.begin() as connection:
            for key, value in snapshot.items():
                connection.execute(text(
                    "INSERT INTO raindeals_runtime_settings "
                    "(setting_key,setting_value,updated_by,updated_at) "
                    "VALUES (:key,:value,0,UTC_TIMESTAMP(6)) "
                    "ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value),updated_by=0,updated_at=VALUES(updated_at)"
                ), {"key": key, "value": value})
        with _lock:
            _cache.update(snapshot)
            _loaded_at = time.monotonic()
    except Exception:
        logger.warning("Could not save latest Manager USDT price snapshot", exc_info=True)


def setting_bool(key: str, default: bool = True) -> bool:
    value = get_setting(key)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def payment_value(key: str, fallback: str = "") -> str:
    return str(get_setting(key, fallback) or "").strip()


def payment_method_order() -> list[str]:
    raw = get_setting("payment_order", "[\"BEP20\",\"POLYGON\",\"BINANCE\",\"UPI\",\"STARS\"]")
    try:
        result = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        result = []
    allowed = {"BEP20", "POLYGON", "BINANCE", "UPI", "STARS"}
    order = [str(item).upper() for item in result if str(item).upper() in allowed] if isinstance(result, list) else []
    return order + sorted(allowed - set(order))


def payment_method_enabled(network: str) -> bool:
    return setting_bool(f"payment_enabled_{str(network).lower()}", True)


def payment_limits(network: str) -> tuple[Decimal, Decimal]:
    key = str(network).lower()
    try:
        minimum = Decimal(payment_value(f"payment_min_{key}", "1"))
        maximum = Decimal(payment_value(f"payment_max_{key}", "10000"))
        if not minimum.is_finite() or not maximum.is_finite() or minimum < 0 or maximum < minimum:
            raise InvalidOperation
        return minimum, maximum
    except (InvalidOperation, ValueError):
        return Decimal("1"), Decimal("10000")


def invoice_lifetime_seconds(network: str, default: int = 3600) -> int:
    raw = payment_value(f"payment_expiry_{str(network).lower()}", str(default))
    try:
        return max(300, min(86400, int(raw)))
    except (TypeError, ValueError):
        return max(300, min(86400, int(default)))


def payment_reference_allowed(network: str, reference: str) -> bool:
    key = str(network).lower()
    value = str(reference or "").strip()
    defaults = {"upi": (8, 20), "binance": (8, 32)}
    minimum, maximum = defaults.get(key, (1, 255))
    try:
        minimum = int(payment_value(f"payment_reference_min_{key}", str(minimum)))
        maximum = int(payment_value(f"payment_reference_max_{key}", str(maximum)))
    except ValueError:
        return False
    prefix = payment_value(f"payment_reference_prefix_{key}", "")
    return minimum <= len(value) <= maximum and (not prefix or value.casefold().startswith(prefix.casefold()))


def validate_public_https_url(value: str) -> str:
    """Validate a public HTTPS URL and reject local/private destinations."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) > 2048:
        raise ValueError("Price source URL must be 2,048 characters or shorter.")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Enter a public HTTPS URL without embedded credentials.")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Local and metadata hosts are not allowed.")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)]
        except OSError as exc:
            raise ValueError("The price source host did not resolve.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("The price source must resolve only to public IP addresses.")
    return raw


def _number_from_json(value) -> Decimal | None:
    preferred = {"rate", "price", "usdtinr", "usdt_inr", "inr", "value", "lastprice"}
    if isinstance(value, dict):
        normalized_preferred = {key.replace("_", "") for key in preferred}
        for key, item in value.items():
            if str(key).replace("-", "").replace("_", "").lower() in normalized_preferred:
                found = _number_from_json(item)
                if found is not None:
                    return found
        # Support common response envelopes without treating arbitrary IDs,
        # timestamps, or counters elsewhere in the JSON as exchange rates.
        for key in ("data", "result", "ticker"):
            item = value.get(key)
            if item is not None:
                found = _number_from_json(item)
                if found is not None:
                    return found
    elif isinstance(value, list):
        # Array-shaped responses are accepted only when unambiguous.
        if len(value) == 1:
            return _number_from_json(value[0])
    elif isinstance(value, (str, int, float, Decimal)):
        try:
            number = Decimal(str(value).replace(",", "").strip())
            if number.is_finite() and Decimal("1") <= number <= Decimal("1000"):
                return number
        except (InvalidOperation, ValueError):
            pass
    return None


def _parse_rate_response(status_code: int, raw: str) -> Decimal | None:
    if status_code != 200:
        return None
    try:
        rate = _number_from_json(json.loads(raw))
        if rate is not None:
            return rate
    except (ValueError, json.JSONDecodeError):
        pass
    match = re.search(r"(?:₹\s*|\b)(\d{1,4}(?:\.\d{1,6})?)\s*(?:INR|₹)", raw, re.IGNORECASE)
    if not match:
        match = re.search(r"(?:rate|price|usdt\s*inr)\D{0,30}(\d{1,4}(?:\.\d{1,6})?)", raw, re.IGNORECASE)
    if match:
        try:
            value = Decimal(match.group(1))
            return value if value.is_finite() and Decimal("1") <= value <= Decimal("1000") else None
        except InvalidOperation:
            return None
    return None


def fetch_manager_usdt_inr_rate() -> Decimal | None:
    """Fetch the custom price endpoint currently saved by Manager, if set."""
    raw = get_setting("usdt_price_source_url", "")
    if not raw:
        return None
    try:
        url = validate_public_https_url(raw)
        response = requests.get(
            url,
            headers={"Accept": "application/json,text/plain,text/html;q=0.8"},
            timeout=(3, 6),
            allow_redirects=False,
            stream=True,
        )
        try:
            if response.status_code != 200:
                logger.warning("Manager USDT price source returned HTTP %s", response.status_code)
                return None
            chunks = bytearray()
            for chunk in response.iter_content(chunk_size=16_384):
                if not chunk:
                    continue
                if len(chunks) + len(chunk) > 500_000:
                    logger.warning("Manager USDT price source exceeded the 500 KB response limit")
                    return None
                chunks.extend(chunk)
            raw_body = bytes(chunks).decode(response.encoding or "utf-8", errors="replace")
            rate = _parse_rate_response(response.status_code, raw_body)
        finally:
            response.close()
        if rate is None:
            logger.warning("Manager USDT price source returned no valid INR rate (status=%s)", response.status_code)
            return None
        return rate
    except Exception as exc:
        logger.warning("Manager USDT price source fetch failed: %s", type(exc).__name__)
        return None
