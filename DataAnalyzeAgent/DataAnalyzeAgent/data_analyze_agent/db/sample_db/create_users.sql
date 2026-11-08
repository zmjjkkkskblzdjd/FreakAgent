








CREATE USER IF NOT EXISTS 'dataro_read'@'localhost' IDENTIFIED BY '{read_password}';
CREATE USER IF NOT EXISTS 'dataro_read'@'127.0.0.1' IDENTIFIED BY '{read_password}';
GRANT SELECT ON shop_demo.* TO 'dataro_read'@'localhost';
GRANT SELECT ON shop_demo.* TO 'dataro_read'@'127.0.0.1';

CREATE USER IF NOT EXISTS 'dataro_write'@'localhost' IDENTIFIED BY '{write_password}';
CREATE USER IF NOT EXISTS 'dataro_write'@'127.0.0.1' IDENTIFIED BY '{write_password}';
GRANT SELECT, INSERT, UPDATE, DELETE ON shop_demo.* TO 'dataro_write'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON shop_demo.* TO 'dataro_write'@'127.0.0.1';

FLUSH PRIVILEGES;
