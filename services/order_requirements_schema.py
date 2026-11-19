"""Idempotent schema setup for buyer information, shared invoices, and Stars."""

from sqlalchemy import text

from database import Base, engine
from models.order_requirement_submission import OrderRequirementSubmission
from models.product_engagement import ProductEngagement
from models.admin_audit_log import AdminAuditLog
from models.bot_resume_session import BotResumeSession
from models.order_workflow import CheckoutAttempt, OrderEvent, OrderWorkNote
from models.bot_operations import WarrantyRequest, NotificationJob, ProviderHealth
from models.api_key import ApiKey, ApiOrder, ApiRateLimitBucket
from models.vendor_offer import VendorOffer, VendorRequest
from models.bot_menu_setting import BotMenuSetting
from models.ticket import Ticket, TicketMessage


def _ensure_column(connection, table: str, column: str, definition: str) -> bool:
    exists = connection.execute(text(
        "SELECT 1 FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table AND COLUMN_NAME=:column"
    ), {"table": table, "column": column}).first()
    if not exists:
        try:
            connection.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}"))
            return True
        except Exception:
            # Two replicas may both observe the missing column. Ignore only
            # the duplicate-DDL race after verifying that the column exists.
            exists = connection.execute(text(
                "SELECT 1 FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table AND COLUMN_NAME=:column"
            ), {"table": table, "column": column}).first()
            if not exists:
                raise
    return False


def _ensure_unique_columns(connection, table, columns, name):
    found = connection.execute(text(
        'SELECT INDEX_NAME FROM information_schema.STATISTICS '
        'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table AND NON_UNIQUE=0 '
        'GROUP BY INDEX_NAME HAVING COUNT(*)=:count '
        'AND GROUP_CONCAT(COLUMN_NAME ORDER BY COLUMN_NAME)=:columns LIMIT 1'
    ), {'table': table, 'count': len(columns), 'columns': ','.join(sorted(columns))}).first()
    if not found:
        identifiers = ','.join(f'`{column}`' for column in columns)
        try:
            connection.execute(text(f'CREATE UNIQUE INDEX `{name}` ON `{table}` ({identifiers})'))
        except Exception:
            found = connection.execute(text(
                'SELECT INDEX_NAME FROM information_schema.STATISTICS '
                'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table AND NON_UNIQUE=0 '
                'GROUP BY INDEX_NAME HAVING COUNT(*)=:count '
                'AND GROUP_CONCAT(COLUMN_NAME ORDER BY COLUMN_NAME)=:columns LIMIT 1'
            ), {'table': table, 'count': len(columns), 'columns': ','.join(sorted(columns))}).first()
            if not found:
                raise


def _ensure_index(connection, table, columns, name):
    exists = connection.execute(text('SELECT 1 FROM information_schema.STATISTICS '
        'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table AND INDEX_NAME=:name LIMIT 1'),
        {'table': table, 'name': name}).first()
    if not exists:
        identifiers = ','.join(f'`{column}`' for column in columns)
        try:
            connection.execute(text(f'CREATE INDEX `{name}` ON `{table}` ({identifiers})'))
        except Exception:
            exists = connection.execute(text('SELECT 1 FROM information_schema.STATISTICS '
                'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table AND INDEX_NAME=:name LIMIT 1'),
                {'table': table, 'name': name}).first()
            if not exists:
                raise


def ensure_order_requirements_schema() -> None:
    from models.customer_tools import CartItem, CartCheckout, CustomerPreferences, CustomerJobCursor, ClaimMessage, CancellationRequest, WalletEntry
    required_tables = [
            OrderRequirementSubmission.__table__,
            ProductEngagement.__table__,
            AdminAuditLog.__table__,
            BotResumeSession.__table__,
            CheckoutAttempt.__table__, OrderEvent.__table__, OrderWorkNote.__table__,
            WarrantyRequest.__table__, NotificationJob.__table__, ProviderHealth.__table__,
            ApiKey.__table__, ApiOrder.__table__, ApiRateLimitBucket.__table__,
            CartItem.__table__, CartCheckout.__table__, CustomerPreferences.__table__, CustomerJobCursor.__table__,
            ClaimMessage.__table__, CancellationRequest.__table__, WalletEntry.__table__,
            VendorOffer.__table__,
            VendorRequest.__table__,
            BotMenuSetting.__table__,
            Ticket.__table__, TicketMessage.__table__,
        ]
    try:
        Base.metadata.create_all(bind=engine, tables=required_tables)
    except Exception:
        # Concurrent startup can race on CREATE TABLE IF NOT EXISTS checks.
        # Treat it as harmless only if every expected table is now present.
        from sqlalchemy import inspect
        inspector = inspect(engine)
        missing = [table.name for table in required_tables if not inspector.has_table(table.name)]
        if missing:
            raise
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS raindeals_runtime_settings ("
            "setting_key VARCHAR(64) NOT NULL PRIMARY KEY,"
            "setting_value MEDIUMTEXT NOT NULL,"
            "updated_by BIGINT NOT NULL DEFAULT 0,"
            "updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
            ")"
        ))
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS raindeals_deposit_reviews ("
            "id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
            "deposit_id BIGINT NOT NULL,admin_id BIGINT NOT NULL,"
            "action VARCHAR(24) NOT NULL,note TEXT NOT NULL,"
            "external_reference VARCHAR(255) NULL,"
            "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),"
            "KEY ix_deposit_review_id (deposit_id,id),"
            "KEY ix_deposit_review_created (created_at)"
            ")"
        ))
        import config
        runtime_defaults = {
            "payment_upi_id": str(getattr(config, "UPI_ID", "") or ""),
            "payment_bep20_address": str(getattr(config, "BEP20_ADDRESS", "") or ""),
            "payment_polygon_address": str(getattr(config, "POLYGON_ADDRESS", "") or ""),
            "payment_binance_pay_id": str(getattr(config, "BINANCE_PAY_ID", "") or ""),
            "payment_stars_usdt_per_star": str(getattr(config, "TELEGRAM_STARS_USDT_PER_STAR", "") or ""),
            "payment_order": '["BEP20","POLYGON","BINANCE","UPI","STARS"]',
            "payment_enabled_upi": "1", "payment_enabled_bep20": "1",
            "payment_enabled_polygon": "1", "payment_enabled_binance": "1", "payment_enabled_stars": "1",
            "payment_min_upi": "1", "payment_max_upi": "1000000",
            "payment_min_bep20": "0.01", "payment_max_bep20": "100000",
            "payment_min_polygon": "0.01", "payment_max_polygon": "100000",
            "payment_min_binance": "0.01", "payment_max_binance": "100000",
            "payment_min_stars": "1", "payment_max_stars": "100000",
            "payment_expiry_upi": "3600", "payment_expiry_bep20": "3600",
            "payment_expiry_polygon": "3600", "payment_expiry_binance": "3600", "payment_expiry_stars": "3600",
            "payment_reference_min_upi": "8", "payment_reference_max_upi": "20",
            "payment_reference_min_binance": "8", "payment_reference_max_binance": "32",
            "payment_reference_prefix_upi": "", "payment_reference_prefix_binance": "",
            "shop_enabled": "1", "miniapp_enabled": "1", "feature_cart": "1",
            "feature_freebies": "1", "feature_referrals": "1", "feature_vendor_offers": "1", "feature_deposits": "1",
            "shop_pause_message": "RainDeals is temporarily unavailable. Please try again later.",
            "miniapp_disabled_message": "The RainDeals shop is temporarily unavailable. Please use the bot or try again later.",
            "usdt_price_source_url": "",
        }
        for setting_key, setting_value in runtime_defaults.items():
            if setting_key == "payment_stars_usdt_per_star" and not setting_value:
                continue
            connection.execute(text(
                "INSERT IGNORE INTO raindeals_runtime_settings "
                "(setting_key,setting_value,updated_by,updated_at) "
                "VALUES (:setting_key,:setting_value,0,UTC_TIMESTAMP(6))"
            ), {"setting_key": setting_key, "setting_value": setting_value})
        if _ensure_column(connection,'bot_notification_jobs','inbox_read_at','DATETIME NULL'):
            connection.execute(text("UPDATE bot_notification_jobs SET inbox_read_at=UTC_TIMESTAMP() "
                                    "WHERE status IN ('sent','uncertain','resolved','failed')"))
        _ensure_index(connection,'bot_notification_jobs',['bot_id','chat_id','inbox_read_at','id'],'ix_notification_inbox')
        _ensure_column(connection, 'bot_notification_jobs', 'category', 'VARCHAR(24) NULL')
        _ensure_column(connection, 'orders', 'received_at', 'DATETIME NULL')
        _ensure_column(connection, 'orders', 'subscription_expires_at', 'DATETIME NULL')
        _ensure_column(connection, 'orders', 'term_days', 'INT NOT NULL DEFAULT 0')
        _ensure_column(connection, 'products', 'term_days', 'INT NOT NULL DEFAULT 0')
        _ensure_column(connection, "bot_notification_jobs", "reply_markup", "JSON NULL")
        _ensure_column(connection, "bot_warranty_requests", "request_type", "VARCHAR(16) NOT NULL DEFAULT 'warranty'")
        _ensure_column(connection, "bot_warranty_requests", "refund_amount", "DECIMAL(20,8) NULL")
        _ensure_column(connection, "bot_warranty_requests", "used_days", "INT NULL")
        _ensure_column(connection, "bot_warranty_requests", "remaining_days", "INT NULL")
        _ensure_column(connection, "bot_warranty_requests", "deactivated_at", "VARCHAR(200) NULL")
        for column, definition in {
            'issue_type': "VARCHAR(32) NOT NULL DEFAULT 'other'",
            'product_confirmed': 'INT NOT NULL DEFAULT 0', 'invoice_snapshot': 'JSON NULL',
            'delivery_snapshot': 'TEXT NULL', 'proof_image': 'MEDIUMBLOB NULL',
            'proof_mime': 'VARCHAR(32) NULL', 'bot_id': 'BIGINT NULL',
        }.items():
            _ensure_column(connection, 'bot_warranty_requests', column, definition)
        for table in ('products', 'orders'):
            _ensure_column(connection, table, 'aftercare_policy', 'VARCHAR(16) NULL')
            _ensure_column(connection, table, 'subscription_days', 'INT NOT NULL DEFAULT 0')
        _ensure_column(connection, 'orders', 'coverage_started_at', 'DATETIME NULL')
        _ensure_column(connection, 'orders', 'refund_amount', 'DECIMAL(20,8) NULL')
        _ensure_index(connection, 'bot_notification_jobs', ['bot_id','chat_id','status','error_code','next_attempt_at'], 'ix_notification_destination_backoff')
        _ensure_index(connection, 'bot_notification_jobs', ['bot_id','status','next_attempt_at','id'], 'ix_notification_delivery_queue')
        _ensure_column(connection, 'bot_vendor_offers', 'request_id', 'INT NULL')
        _ensure_index(connection, 'bot_vendor_offers', ['request_id'], 'ix_bot_vendor_offers_request_id')
        _ensure_unique_columns(connection, 'deposits', ['tx_hash'], 'uq_deposits_tx_hash')
        _ensure_unique_columns(connection, 'api_orders', ['api_key_id', 'client_order_id'], 'uq_api_order_client_id')
        _ensure_column(connection, 'api_orders', 'request_fingerprint', 'VARCHAR(64) NULL')
        _ensure_column(connection, "users", "home_menu_chat_id", "BIGINT NULL")
        _ensure_column(connection, "users", "home_menu_message_id", "BIGINT NULL")
        _ensure_column(connection, "users", "language_code", "VARCHAR(16) NOT NULL DEFAULT 'en'")
        _ensure_column(connection, "products", "required_info", "JSON NULL")
        _ensure_column(connection, "products", "freebie_referrals_required", "INT NOT NULL DEFAULT 0")
        _ensure_column(connection, "users", "miniapp_verified_at", "DATETIME NULL")
        _ensure_column(connection, "users", "miniapp_verification_prompted_at", "DATETIME NULL")
        _ensure_column(connection, "users", "miniapp_ip_hash", "VARCHAR(64) NULL")
        _ensure_column(connection, "users", "miniapp_referral_review_approved_at", "DATETIME NULL")
        _ensure_column(connection, "users", "miniapp_referral_review_rejected_at", "DATETIME NULL")
        _ensure_column(connection, "users", "miniapp_referral_reviewed_by", "BIGINT NULL")
        _ensure_index(connection, "users", ["referred_by", "miniapp_verified_at"], "ix_users_verified_referrals")
        _ensure_index(connection, "users", ["referred_by", "miniapp_ip_hash", "miniapp_verified_at"], "ix_users_referral_network_check")
        # Older support handlers wrote Telegram IDs into tickets.user_id even
        # though the column references users.id. Convert those legacy rows so
        # the support inbox, owner checks, and Manager Web can resolve them.
        connection.execute(text(
            "UPDATE tickets AS t JOIN users AS u ON u.telegram_id = t.user_id "
            "SET t.user_id = u.id WHERE t.user_id <> u.id"
        ))
        _ensure_column(connection, "products", "reusable_delivery", "TEXT NULL")
        _ensure_column(connection, "products", "reusable_stock", "INT NOT NULL DEFAULT 0")
        _ensure_column(connection, "products", "warranty_days", "INT NOT NULL DEFAULT 0")
        _ensure_column(connection, "orders", "warranty_days", "INT NOT NULL DEFAULT 0")
        _ensure_column(connection, "orders", "warranty_expires_at", "DATETIME NULL")
        _ensure_column(connection, "orders", "admin_notification_queued_at", "DATETIME NULL")
        fallback_exists = connection.execute(text(
            "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='products' AND COLUMN_NAME='manual_fallback'"
        )).first()
        _ensure_column(connection, "products", "manual_fallback", "TINYINT(1) NOT NULL DEFAULT 0")
        if not fallback_exists:
            connection.execute(text("UPDATE products SET manual_fallback=1 WHERE delivery_type IN ('manual','hybrid')"))
        # Normalize legacy own products that advertise automatic/hybrid
        # delivery without separate or reusable account inventory. Keep the
        # configured stock as manual sellable capacity and avoid prompting an
        # admin during startup. This runs after manual_fallback is guaranteed
        # to exist on older databases.
        connection.execute(text(
            "UPDATE products SET delivery_type='manual', manual_fallback=1, preorder=0 "
            "WHERE COALESCE(source,'own')='own' "
            "AND LOWER(COALESCE(delivery_type,'automatic')) IN ('automatic','hybrid') "
            "AND TRIM(COALESCE(file_content,''))='' "
            "AND (TRIM(COALESCE(reusable_delivery,''))='' OR COALESCE(reusable_stock,0)<=0)"
        ))
        _ensure_column(connection, "orders", "reserved_delivery", "TEXT NULL")
        _ensure_column(connection, "orders", "reusable_delivery_account", "TEXT NULL")
        _ensure_column(connection, "orders", "reusable_delivery_quantity", "INT NOT NULL DEFAULT 0")
        _ensure_column(connection, "orders", "automatic_quantity", "INT NOT NULL DEFAULT 0")
        _ensure_column(connection, "orders", "manual_pending_quantity", "INT NOT NULL DEFAULT 0")
        _ensure_column(connection, "orders", "required_info_snapshot", "JSON NULL")
        _ensure_column(connection, "orders", "stock_deducted_quantity", "INT NULL")
        _ensure_column(connection, "orders", "referral_commission_amount", "DECIMAL(20,8) NULL")
        _ensure_column(connection, "orders", "referral_commission_to_balance", "TINYINT(1) NULL")
        _ensure_column(connection, "orders", "reseller_provider_order_id", "VARCHAR(255) NULL")
        _ensure_index(connection, "orders", ["status", "refunded", "reseller_id", "reseller_provider_order_id"],
                      "ix_orders_supplier_reconcile")
        _ensure_column(connection, "orders", "checkout_key", "VARCHAR(64) NULL")
        _ensure_column(connection, "orders", "assigned_admin_id", "BIGINT NULL")
        _ensure_column(connection, "orders", "assigned_at", "DATETIME NULL")
        _ensure_column(connection, "orders", "fulfillment_priority", "VARCHAR(12) NOT NULL DEFAULT 'normal'")
        checkout_index = connection.execute(text(
            "SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='orders' AND COLUMN_NAME='checkout_key' AND NON_UNIQUE=0"
        )).first()
        if not checkout_index:
            try:
                connection.execute(text('CREATE UNIQUE INDEX uq_orders_checkout_key ON orders (checkout_key)'))
            except Exception:
                exists = connection.execute(text(
                    "SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='orders' AND INDEX_NAME='uq_orders_checkout_key' LIMIT 1"
                )).first()
                if not exists:
                    raise
        _ensure_column(connection, "deposits", "invoice_code", "VARCHAR(32) NULL")
        _ensure_column(connection, "deposits", "invoice_expires_at", "DATETIME NULL")
        _ensure_column(connection, "deposits", "invoice_usdt_amount", "DECIMAL(20,8) NULL")
        _ensure_column(connection, "deposits", "telegram_chat_id", "BIGINT NULL")
        _ensure_column(connection, "deposits", "telegram_message_id", "BIGINT NULL")
        _ensure_column(connection, "deposits", "verification_started_at", "DATETIME NULL")
        _ensure_column(connection, "deposits", "verification_lease_token", "VARCHAR(64) NULL")
        _ensure_column(connection, "deposits", "verification_lease_until", "DATETIME NULL")
        _ensure_column(connection, "deposits", "stars_amount_requested", "INT NULL")
        _ensure_column(connection, "deposits", "stars_amount_paid", "INT NULL")
        _ensure_column(connection, "deposits", "stars_usdt_per_star", "DECIMAL(20,8) NULL")
        _ensure_column(connection, "deposits", "stars_invoice_payload", "VARCHAR(128) NULL")
        _ensure_column(connection, "deposits", "telegram_payment_charge_id", "VARCHAR(255) NULL")
        _ensure_column(connection, "deposits", "stars_invoice_url", "VARCHAR(2048) NULL")
        _ensure_column(connection, "deposits", "stars_precheckout_at", "DATETIME NULL")
        _ensure_unique_columns(connection, "deposits", ["stars_invoice_payload"], "uq_deposits_stars_payload")
        _ensure_unique_columns(connection, "deposits", ["telegram_payment_charge_id"], "uq_deposits_telegram_charge")
        _ensure_index(connection, "deposits", ["verification_lease_until"], "ix_deposits_verification_lease_until")
        index_exists = connection.execute(text(
            "SELECT 1 FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='deposits' "
            "AND COLUMN_NAME='invoice_code' AND NON_UNIQUE=0"
        )).first()
        if not index_exists:
            connection.execute(text(
                "CREATE UNIQUE INDEX uq_deposits_invoice_code ON deposits (invoice_code)"
            ))
