






CREATE TABLE IF NOT EXISTS kb_grant (
  kb_id      VARCHAR(64) NOT NULL,
  user_id    BIGINT      NOT NULL,
  tenant_id  BIGINT      NOT NULL,
  perm       VARCHAR(8)  NOT NULL,
  granted_by BIGINT      NOT NULL,
  created_at DATETIME    NOT NULL,
  PRIMARY KEY (kb_id, user_id),
  KEY idx_kb_grant_user (tenant_id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
