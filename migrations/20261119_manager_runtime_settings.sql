CREATE TABLE IF NOT EXISTS raindeals_runtime_settings (
  setting_key VARCHAR(64) NOT NULL PRIMARY KEY,
  setting_value MEDIUMTEXT NOT NULL,
  updated_by BIGINT NOT NULL DEFAULT 0,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
);

CREATE TABLE IF NOT EXISTS raindeals_deposit_reviews (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  deposit_id BIGINT NOT NULL,
  admin_id BIGINT NOT NULL,
  action VARCHAR(24) NOT NULL,
  note TEXT NOT NULL,
  external_reference VARCHAR(255) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY ix_deposit_review_id (deposit_id, id),
  KEY ix_deposit_review_created (created_at)
);
