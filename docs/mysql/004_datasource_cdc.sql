







USE allfusion;

DROP PROCEDURE IF EXISTS af_add_column_if_missing;
DELIMITER $$
CREATE PROCEDURE af_add_column_if_missing(
    IN p_table VARCHAR(64), IN p_column VARCHAR(64), IN p_ddl TEXT)
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_table
           AND COLUMN_NAME = p_column
    ) THEN
        SET @ddl = CONCAT('ALTER TABLE `', p_table, '` ADD COLUMN `', p_column, '` ', p_ddl);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

CALL af_add_column_if_missing('datasource', 'cdc_username', 'VARCHAR(128) NULL');
CALL af_add_column_if_missing('datasource', 'cdc_password_cipher', 'BLOB NULL');
CALL af_add_column_if_missing('datasource', 'cdc_key_version', 'SMALLINT NOT NULL DEFAULT 1');


CALL af_add_column_if_missing('datasource_mirror', 'generation', 'INT NOT NULL DEFAULT 1');

DROP PROCEDURE IF EXISTS af_add_column_if_missing;


SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = 'allfusion' AND TABLE_NAME = 'datasource'
   AND COLUMN_NAME IN ('cdc_username', 'cdc_password_cipher', 'cdc_key_version');
