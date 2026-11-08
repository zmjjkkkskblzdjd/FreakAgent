
CREATE USER IF NOT EXISTS da_readonly IDENTIFIED BY '${DA_READONLY_PASSWORD}';
GRANT SELECT ON sales_dw.* TO da_readonly;
GRANT SELECT ON system.tables TO da_readonly;
GRANT SELECT ON system.columns TO da_readonly;
CREATE USER IF NOT EXISTS da_writer IDENTIFIED BY '${DA_WRITER_PASSWORD}';
GRANT INSERT, ALTER UPDATE, ALTER DELETE ON sales_dw.* TO da_writer;
GRANT SELECT ON sales_dw.* TO da_writer;
GRANT SELECT ON system.mutations TO da_writer;
