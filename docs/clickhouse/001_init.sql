






CREATE DATABASE IF NOT EXISTS allfusion_trace;

CREATE TABLE IF NOT EXISTS allfusion_trace.trace_span (
  ts DateTime64(3),
  trace_id UUID,
  span_id UInt64,
  parent_span_id UInt64,
  tenant_id UInt64,
  user_hash UInt64,
  session_id String,
  thread_id String,
  request_id String,
  service LowCardinality(String),
  node LowCardinality(String),
  kind LowCardinality(String),
  status LowCardinality(String),
  error_code String,
  model LowCardinality(String),
  prompt_tokens UInt32,
  completion_tokens UInt32,
  cost Decimal(12, 6),
  duration_ms UInt32,
  attrs String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (tenant_id, ts)


TTL toDateTime(ts) + INTERVAL 90 DAY;
