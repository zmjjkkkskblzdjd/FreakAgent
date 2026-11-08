













USE allfusion;



CREATE TABLE IF NOT EXISTS datasource_mirror (
  datasource_id BIGINT NOT NULL,
  ch_database VARCHAR(64) NOT NULL,
  connector_name VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL COMMENT 'pending/snapshot/running/failed/stopped',
  snapshot_at DATETIME(3) NULL,
  last_event_at DATETIME(3) NULL,
  lag_ms BIGINT NULL,
  error_msg VARCHAR(500) NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (datasource_id),
  UNIQUE KEY uk_ch_database (ch_database),
  UNIQUE KEY uk_connector (connector_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;



CREATE TABLE IF NOT EXISTS datasource_mirror_table (
  datasource_id BIGINT NOT NULL,
  table_name VARCHAR(128) NOT NULL,
  phase VARCHAR(16) NOT NULL COMMENT 'pending/snapshot/cdc/failed/drift',
  rows_synced BIGINT NULL,
  last_event_at DATETIME(3) NULL,
  error_msg VARCHAR(500) NULL,
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (datasource_id, table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;



CREATE TABLE IF NOT EXISTS datasource_write_grant (
  id BIGINT NOT NULL AUTO_INCREMENT,
  datasource_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  table_name VARCHAR(128) NOT NULL,
  granted_by BIGINT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_ds_user_table (datasource_id, user_id, table_name),
  KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;



DROP PROCEDURE IF EXISTS af_add_column_if_missing;

DELIMITER $$
CREATE PROCEDURE af_add_column_if_missing(
  IN p_table VARCHAR(64),
  IN p_column VARCHAR(64),
  IN p_def VARCHAR(512)
)
BEGIN
  DECLARE v_cnt INT DEFAULT 0;
  SELECT COUNT(*) INTO v_cnt
    FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA = 'allfusion'
     AND TABLE_NAME = p_table
     AND COLUMN_NAME = p_column;
  IF v_cnt = 0 THEN
    SET @ddl = CONCAT('ALTER TABLE `', p_table, '` ADD COLUMN `', p_column, '` ', p_def);
    PREPARE stmt FROM @ddl;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END IF;
END$$
DELIMITER ;

CALL af_add_column_if_missing('user_session', 'session_type', 'VARCHAR(8) NOT NULL DEFAULT ''user''');
CALL af_add_column_if_missing('login_attempt', 'kind', 'VARCHAR(8) NOT NULL DEFAULT ''user''');
CALL af_add_column_if_missing('audit_log', 'user_agent', 'VARCHAR(255) NULL');
CALL af_add_column_if_missing('audit_log', 'before_json', 'TEXT NULL');
CALL af_add_column_if_missing('audit_log', 'after_json', 'TEXT NULL');
CALL af_add_column_if_missing('audit_log', 'request_id', 'VARCHAR(64) NULL');

DROP PROCEDURE IF EXISTS af_add_index_if_missing;

DELIMITER $$
CREATE PROCEDURE af_add_index_if_missing(
  IN p_table VARCHAR(64),
  IN p_index VARCHAR(64),
  IN p_cols VARCHAR(255)
)
BEGIN
  DECLARE v_cnt INT DEFAULT 0;
  SELECT COUNT(*) INTO v_cnt
    FROM information_schema.STATISTICS
   WHERE TABLE_SCHEMA = 'allfusion'
     AND TABLE_NAME = p_table
     AND INDEX_NAME = p_index;
  IF v_cnt = 0 THEN
    SET @ddl = CONCAT('CREATE INDEX `', p_index, '` ON `', p_table, '` (', p_cols, ')');
    PREPARE stmt FROM @ddl;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END IF;
END$$
DELIMITER ;

CALL af_add_index_if_missing('login_attempt', 'idx_login_kind_time',
                             '`kind`, `account`, `ip`, `created_at`');

DROP PROCEDURE IF EXISTS af_add_column_if_missing;
DROP PROCEDURE IF EXISTS af_add_index_if_missing;



SELECT TABLE_NAME FROM information_schema.TABLES
 WHERE TABLE_SCHEMA = 'allfusion'
   AND TABLE_NAME IN ('datasource_mirror', 'datasource_mirror_table', 'datasource_write_grant');


SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = 'allfusion'
   AND ((TABLE_NAME = 'user_session' AND COLUMN_NAME = 'session_type')
     OR (TABLE_NAME = 'login_attempt' AND COLUMN_NAME = 'kind')
     OR (TABLE_NAME = 'audit_log' AND COLUMN_NAME IN
         ('user_agent', 'before_json', 'after_json', 'request_id')));

SELECT TABLE_NAME, INDEX_NAME FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = 'allfusion' AND INDEX_NAME = 'idx_login_kind_time';
