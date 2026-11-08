









CREATE TABLE IF NOT EXISTS sales_dw.kb_meta
(
    meta_key   String COMMENT '元信息键',
    meta_value String COMMENT '元信息值'
) ENGINE = MergeTree ORDER BY meta_key
  COMMENT '语义库元信息（MySQL sales_meta.kb_meta 全量同步副本；元数据表，分析查询勿用）';

CREATE TABLE IF NOT EXISTS sales_dw.kb_glossary
(
    name           String        COMMENT '指标标准名',
    aliases        Array(String) COMMENT '别名',
    formula        String        COMMENT 'ClickHouse 口径表达式',
    related_tables Array(String) COMMENT '相关表',
    unit           String        DEFAULT '' COMMENT '单位',
    note           String        DEFAULT '' COMMENT '口径备注',
    formula_desc   String        DEFAULT '' COMMENT 'LLM生成的自然语言口径描述，用于向量嵌入',
    enabled        UInt8         DEFAULT 1  COMMENT '1=启用 0=停用'
) ENGINE = MergeTree ORDER BY name
  COMMENT '指标字典（MySQL sales_meta 主存全量同步副本；元数据表，分析查询勿用）';

CREATE TABLE IF NOT EXISTS sales_dw.kb_fewshot
(
    id             UInt64        COMMENT '例题 ID（回源主键）',
    user_id        String        DEFAULT '' COMMENT '归属用户 id（用户级隔离；空串=改造前旧行）',
    question       String        COMMENT '例题问题',
    reference_sql  String        COMMENT '参考 SQL（ClickHouse 方言）',
    explanation    String        DEFAULT '' COMMENT '解读要点',
    related_tables Array(String) COMMENT '相关表',
    tags           Array(String) COMMENT '标签（槽位标注）',
    status         String        DEFAULT 'published' COMMENT 'candidate=待审 / published=可检索',
    enabled        UInt8         DEFAULT 1  COMMENT '1=启用 0=停用'
) ENGINE = MergeTree ORDER BY id
  COMMENT 'fewshot 例题（按用户归属；MySQL sales_meta 主存全量同步副本；元数据表，分析查询勿用）';
