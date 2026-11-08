







CREATE DATABASE IF NOT EXISTS allfusion
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE allfusion;



CREATE TABLE IF NOT EXISTS tenant (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(64) NOT NULL,
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_user (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NOT NULL,
  email VARCHAR(150) NOT NULL,
  username VARCHAR(64) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role VARCHAR(16) NOT NULL DEFAULT 'member',
  status TINYINT NOT NULL DEFAULT 1,
  email_verified_at DATETIME(3) NULL,
  last_login_at DATETIME(3) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_user_email (tenant_id, email),
  KEY idx_user_tenant (tenant_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_session (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  token_hash CHAR(64) NOT NULL,
  ip VARCHAR(45) NULL,
  user_agent VARCHAR(255) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  last_seen_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  expires_at DATETIME(3) NOT NULL,
  revoked_at DATETIME(3) NULL,
  UNIQUE KEY uk_session_token (token_hash),
  KEY idx_session_user (tenant_id, user_id, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS login_attempt (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  account VARCHAR(150) NOT NULL,
  ip VARCHAR(45) NOT NULL,
  ok TINYINT NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_attempt (account, created_at),
  KEY idx_attempt_ip (ip, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_log (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NOT NULL,
  user_id BIGINT NULL,
  action VARCHAR(64) NOT NULL,
  target VARCHAR(128) NOT NULL DEFAULT '',
  ip VARCHAR(45) NULL,
  detail_json MEDIUMTEXT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_audit_tenant (tenant_id, created_at),
  KEY idx_audit_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;



CREATE TABLE IF NOT EXISTS chat_session (
  id CHAR(26) PRIMARY KEY,
  tenant_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  thread_id VARCHAR(128) NOT NULL,
  title VARCHAR(128) NOT NULL DEFAULT '新对话',
  pinned TINYINT NOT NULL DEFAULT 0,
  status TINYINT NOT NULL DEFAULT 0,
  message_count INT NOT NULL DEFAULT 0,
  last_preview VARCHAR(200) NOT NULL DEFAULT '',
  last_msg_at DATETIME(3) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_session_thread (thread_id),
  KEY idx_session_list (tenant_id, user_id, status, pinned, last_msg_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS thread_message (
  id CHAR(26) PRIMARY KEY,
  tenant_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  session_id CHAR(26) NOT NULL,
  thread_id VARCHAR(128) NOT NULL,
  seq INT NOT NULL,
  role VARCHAR(16) NOT NULL,
  status VARCHAR(16) NOT NULL,
  request_id VARCHAR(64) NOT NULL,
  parent_id CHAR(26) NULL,
  content_preview VARCHAR(512) NOT NULL DEFAULT '',
  content_zstd LONGBLOB NULL,
  content_encoding TINYINT NOT NULL DEFAULT 1,
  content_bytes INT NOT NULL DEFAULT 0,
  content_sha256 CHAR(64) NOT NULL DEFAULT '',
  token_count INT NOT NULL DEFAULT 0,
  model VARCHAR(128) NULL,
  trace_id CHAR(32) NULL,
  metadata_json MEDIUMTEXT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_msg_seq (tenant_id, session_id, seq),
  UNIQUE KEY uk_msg_req (tenant_id, request_id),
  KEY idx_msg_list (tenant_id, session_id, seq),
  KEY idx_msg_session_created (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS session_summary (
  session_id CHAR(26) PRIMARY KEY,
  tenant_id BIGINT NOT NULL,
  covers_until_seq INT NOT NULL DEFAULT 0,
  summary_md MEDIUMTEXT NOT NULL,
  token_count INT NOT NULL DEFAULT 0,
  model VARCHAR(128) NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS thread_registry (
  thread_id VARCHAR(128) PRIMARY KEY,
  tenant_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  session_id CHAR(26) NOT NULL,
  status VARCHAR(16) NOT NULL,
  last_active_at DATETIME(3) NOT NULL,
  KEY idx_registry_gc (status, last_active_at),
  KEY idx_registry_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;



CREATE TABLE IF NOT EXISTS model_provider (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NOT NULL,
  name VARCHAR(64) NOT NULL,
  provider_type VARCHAR(32) NOT NULL,
  base_url VARCHAR(512) NOT NULL,
  completions_path VARCHAR(256) NULL,
  api_key_cipher VARBINARY(2048) NULL,
  key_version SMALLINT NOT NULL DEFAULT 1,
  key_fingerprint CHAR(16) NOT NULL DEFAULT '',
  enabled TINYINT NOT NULL DEFAULT 1,
  created_by BIGINT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_provider_name (tenant_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS model_route (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NOT NULL,
  purpose VARCHAR(32) NOT NULL,
  provider_id BIGINT NOT NULL,
  model VARCHAR(128) NOT NULL,
  priority INT NOT NULL DEFAULT 100,
  weight INT NOT NULL DEFAULT 1,
  max_tokens INT NULL,
  temperature DECIMAL(3,2) NULL,
  fallback_order INT NOT NULL DEFAULT 0,
  enabled TINYINT NOT NULL DEFAULT 1,
  extra_json MEDIUMTEXT NULL,
  UNIQUE KEY uk_route (tenant_id, purpose, provider_id, model),
  KEY idx_route_pick (tenant_id, purpose, enabled, priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;



CREATE TABLE IF NOT EXISTS datasource (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NOT NULL,
  name VARCHAR(64) NOT NULL,
  db_type VARCHAR(16) NOT NULL,
  host VARCHAR(255) NOT NULL,
  port INT NOT NULL,
  database_name VARCHAR(128) NOT NULL,
  schema_name VARCHAR(128) NULL,
  username VARCHAR(128) NOT NULL,
  password_cipher VARBINARY(2048) NULL,
  key_version SMALLINT NOT NULL DEFAULT 1,
  options_json MEDIUMTEXT NULL,
  max_rows INT NOT NULL DEFAULT 1000,
  query_timeout_sec INT NOT NULL DEFAULT 30,
  enabled TINYINT NOT NULL DEFAULT 1,
  created_by BIGINT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_ds_name (tenant_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS datasource_table (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  datasource_id BIGINT NOT NULL,
  tenant_id BIGINT NOT NULL,
  table_name VARCHAR(128) NOT NULL,
  table_comment VARCHAR(255) NULL,
  selected TINYINT NOT NULL DEFAULT 0,
  columns_json MEDIUMTEXT NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_ds_table (datasource_id, table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;



CREATE TABLE IF NOT EXISTS kb_owner (
  kb_id VARCHAR(64) PRIMARY KEY,
  tenant_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_kb_owner_tenant (tenant_id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
