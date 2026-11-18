-- Run once only if the application DB user cannot add columns at startup.
-- Do not run this after ensure_order_requirements_schema has added the fields.
ALTER TABLE deposits
  ADD COLUMN stars_amount_requested INT NULL,
  ADD COLUMN stars_amount_paid INT NULL,
  ADD COLUMN stars_usdt_per_star DECIMAL(20,8) NULL,
  ADD COLUMN stars_invoice_payload VARCHAR(128) NULL,
  ADD COLUMN telegram_payment_charge_id VARCHAR(255) NULL,
  ADD COLUMN stars_invoice_url VARCHAR(2048) NULL,
  ADD COLUMN stars_precheckout_at DATETIME NULL;

CREATE UNIQUE INDEX uq_deposits_stars_payload
  ON deposits (stars_invoice_payload);

CREATE UNIQUE INDEX uq_deposits_telegram_charge
  ON deposits (telegram_payment_charge_id);
