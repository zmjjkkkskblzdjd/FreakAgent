


GRANT SELECT, INSERT, UPDATE, DELETE ON sales_meta.kb_glossary TO 'meta_rw'@'%' IDENTIFIED BY '${META_RW_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE ON sales_meta.kb_fewshot TO 'meta_rw'@'%';
