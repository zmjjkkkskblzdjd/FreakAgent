






CREATE TABLE IF NOT EXISTS user_model_pref (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  purpose VARCHAR(32) NOT NULL DEFAULT 'chat',
  provider_id BIGINT NOT NULL,
  model VARCHAR(128) NOT NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_user_pref (tenant_id, user_id, purpose)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
