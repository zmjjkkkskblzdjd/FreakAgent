













CREATE DATABASE IF NOT EXISTS sales_meta
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE sales_meta;


CREATE TABLE IF NOT EXISTS kb_meta (
    meta_key   VARCHAR(64)  NOT NULL COMMENT '元信息键',
    meta_value TEXT         NULL     COMMENT '元信息值',
    PRIMARY KEY (meta_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='语义库元信息（kv 结构）';


CREATE TABLE IF NOT EXISTS kb_glossary (
    id             INT          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    name           VARCHAR(128) NOT NULL COMMENT '指标标准名（精确匹配第①级的 key）',
    aliases        TEXT         NULL COMMENT '别名，JSON 数组字符串（第②级的 key）',
    formula        TEXT         NOT NULL COMMENT 'ClickHouse 口径表达式',
    related_tables TEXT         NULL COMMENT '相关表，JSON 数组字符串',
    unit           VARCHAR(32)  NULL DEFAULT '' COMMENT '单位',
    note           TEXT         NULL COMMENT '口径备注/消歧说明',
    enabled        TINYINT      NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
    updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                   ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_kb_glossary_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指标字典（人工维护主存）';




CREATE TABLE IF NOT EXISTS kb_fewshot (
    id             INT          NOT NULL AUTO_INCREMENT COMMENT '例题 ID（回源主键）',
    user_id        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '归属用户 id（用户级隔离，空串=改造前旧行）',
    question       TEXT         NOT NULL COMMENT '例题问题',
    reference_sql  TEXT         NOT NULL COMMENT '参考 SQL（必须 ClickHouse 方言）',
    explanation    TEXT         NULL COMMENT '解读要点',
    related_tables TEXT         NULL COMMENT '相关表，JSON 数组字符串',
    tags           TEXT         NULL COMMENT '标签，JSON 数组字符串（槽位标注，如 metric:成交额/dim:channel/time:环比/shape:rank）',
    status         VARCHAR(16)  NOT NULL DEFAULT 'published' COMMENT 'candidate=机器提名待审 / published=已发表（可检索）',
    enabled        TINYINT      NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
    updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                   ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间',
    PRIMARY KEY (id),
    KEY idx_kb_fewshot_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='fewshot 例题（按用户归属，人工维护主存）';
