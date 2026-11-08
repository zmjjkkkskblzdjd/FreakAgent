






ALTER TABLE app_user
  ADD COLUMN scopes VARCHAR(64) NOT NULL DEFAULT ''
  COMMENT '管理域，逗号分隔：db_admin（数据源/模型/镜像/用户）、kb_admin（知识库授权）';
