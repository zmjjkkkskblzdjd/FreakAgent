from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

tenant = sa.Table(
    "tenant", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String(64), nullable=False),
    sa.Column("status", sa.SmallInteger, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

app_user = sa.Table(
    "app_user", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("email", sa.String(150), nullable=False),
    sa.Column("username", sa.String(64), nullable=False),
    sa.Column("password_hash", sa.String(255), nullable=False),
    sa.Column("role", sa.String(16), nullable=False),
    sa.Column("scopes", sa.String(64), nullable=False),
    sa.Column("status", sa.SmallInteger, nullable=False),
    sa.Column("email_verified_at", sa.DateTime),
    sa.Column("last_login_at", sa.DateTime),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

user_session = sa.Table(
    "user_session", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("user_id", sa.BigInteger, nullable=False),
    sa.Column("token_hash", sa.String(64), nullable=False),
    sa.Column("ip", sa.String(45)),
    sa.Column("user_agent", sa.String(255)),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("last_seen_at", sa.DateTime, nullable=False),
    sa.Column("expires_at", sa.DateTime, nullable=False),
    sa.Column("revoked_at", sa.DateTime),
)

login_attempt = sa.Table(
    "login_attempt", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.BigInteger),
    sa.Column("account", sa.String(150), nullable=False),
    sa.Column("ip", sa.String(45), nullable=False),
    sa.Column("ok", sa.SmallInteger, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

audit_log = sa.Table(
    "audit_log", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("user_id", sa.BigInteger),
    sa.Column("action", sa.String(64), nullable=False),
    sa.Column("target", sa.String(128), nullable=False),
    sa.Column("ip", sa.String(45)),
    sa.Column("detail_json", sa.Text),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

chat_session = sa.Table(
    "chat_session", metadata,
    sa.Column("id", sa.String(26), primary_key=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("user_id", sa.BigInteger, nullable=False),
    sa.Column("thread_id", sa.String(128), nullable=False),
    sa.Column("title", sa.String(128), nullable=False),
    sa.Column("pinned", sa.SmallInteger, nullable=False),
    sa.Column("status", sa.SmallInteger, nullable=False),
    sa.Column("message_count", sa.Integer, nullable=False),
    sa.Column("last_preview", sa.String(200), nullable=False),
    sa.Column("last_msg_at", sa.DateTime),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("updated_at", sa.DateTime, nullable=False),
)

thread_message = sa.Table(
    "thread_message", metadata,
    sa.Column("id", sa.String(26), primary_key=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("user_id", sa.BigInteger, nullable=False),
    sa.Column("session_id", sa.String(26), nullable=False),
    sa.Column("thread_id", sa.String(128), nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("role", sa.String(16), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("request_id", sa.String(64), nullable=False),
    sa.Column("parent_id", sa.String(26)),
    sa.Column("content_preview", sa.String(512), nullable=False),
    sa.Column("content_zstd", sa.LargeBinary),
    sa.Column("content_encoding", sa.SmallInteger, nullable=False),
    sa.Column("content_bytes", sa.Integer, nullable=False),
    sa.Column("content_sha256", sa.String(64), nullable=False),
    sa.Column("token_count", sa.Integer, nullable=False),
    sa.Column("model", sa.String(128)),
    sa.Column("trace_id", sa.String(32)),
    sa.Column("metadata_json", sa.Text),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

session_summary = sa.Table(
    "session_summary", metadata,
    sa.Column("session_id", sa.String(26), primary_key=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("covers_until_seq", sa.Integer, nullable=False),
    sa.Column("summary_md", sa.Text, nullable=False),
    sa.Column("token_count", sa.Integer, nullable=False),
    sa.Column("model", sa.String(128)),
    sa.Column("updated_at", sa.DateTime, nullable=False),
)

thread_registry = sa.Table(
    "thread_registry", metadata,
    sa.Column("thread_id", sa.String(128), primary_key=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("user_id", sa.BigInteger, nullable=False),
    sa.Column("session_id", sa.String(26), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("last_active_at", sa.DateTime, nullable=False),
)

model_provider = sa.Table(
    "model_provider", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("name", sa.String(64), nullable=False),
    sa.Column("provider_type", sa.String(32), nullable=False),
    sa.Column("base_url", sa.String(512), nullable=False),
    sa.Column("completions_path", sa.String(256)),
    sa.Column("api_key_cipher", sa.LargeBinary),
    sa.Column("key_version", sa.SmallInteger, nullable=False),
    sa.Column("key_fingerprint", sa.String(16), nullable=False),
    sa.Column("enabled", sa.SmallInteger, nullable=False),
    sa.Column("created_by", sa.BigInteger),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("updated_at", sa.DateTime, nullable=False),
    sa.Column("models_json", sa.Text),
    sa.Column("models_synced_at", sa.DateTime),
)

model_route = sa.Table(
    "model_route", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("purpose", sa.String(32), nullable=False),
    sa.Column("provider_id", sa.BigInteger, nullable=False),
    sa.Column("model", sa.String(128), nullable=False),
    sa.Column("priority", sa.Integer, nullable=False),
    sa.Column("weight", sa.Integer, nullable=False),
    sa.Column("max_tokens", sa.Integer),
    sa.Column("temperature", sa.Numeric(3, 2)),
    sa.Column("fallback_order", sa.Integer, nullable=False),
    sa.Column("enabled", sa.SmallInteger, nullable=False),
    sa.Column("extra_json", sa.Text),
)

user_model_pref = sa.Table(
    "user_model_pref", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("user_id", sa.BigInteger, nullable=False),
    sa.Column("purpose", sa.String(32), nullable=False),
    sa.Column("provider_id", sa.BigInteger, nullable=False),
    sa.Column("model", sa.String(128), nullable=False),
    sa.Column("updated_at", sa.DateTime, nullable=False),
)

datasource = sa.Table(
    "datasource", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("name", sa.String(64), nullable=False),
    sa.Column("db_type", sa.String(16), nullable=False),
    sa.Column("host", sa.String(255), nullable=False),
    sa.Column("port", sa.Integer, nullable=False),
    sa.Column("database_name", sa.String(128), nullable=False),
    sa.Column("schema_name", sa.String(128)),
    sa.Column("username", sa.String(128), nullable=False),
    sa.Column("password_cipher", sa.LargeBinary),
    sa.Column("key_version", sa.SmallInteger, nullable=False),
    sa.Column("options_json", sa.Text),
    sa.Column("max_rows", sa.Integer, nullable=False),
    sa.Column("query_timeout_sec", sa.Integer, nullable=False),
    sa.Column("enabled", sa.SmallInteger, nullable=False),
    sa.Column("created_by", sa.BigInteger),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("cdc_username", sa.String(128)),
    sa.Column("cdc_password_cipher", sa.LargeBinary),
    sa.Column("cdc_key_version", sa.SmallInteger, nullable=False),
)

datasource_table = sa.Table(
    "datasource_table", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("datasource_id", sa.BigInteger, nullable=False),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("table_name", sa.String(128), nullable=False),
    sa.Column("table_comment", sa.String(255)),
    sa.Column("selected", sa.SmallInteger, nullable=False),
    sa.Column("columns_json", sa.Text),
    sa.Column("updated_at", sa.DateTime, nullable=False),
)

kb_owner = sa.Table(
    "kb_owner", metadata,
    sa.Column("kb_id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("user_id", sa.BigInteger, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)

kb_grant = sa.Table(
    "kb_grant", metadata,
    sa.Column("kb_id", sa.String(64), primary_key=True),
    sa.Column("user_id", sa.BigInteger, primary_key=True),
    sa.Column("tenant_id", sa.BigInteger, nullable=False),
    sa.Column("perm", sa.String(8), nullable=False),
    sa.Column("granted_by", sa.BigInteger, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
)


datasource_mirror = sa.Table(
    "datasource_mirror", metadata,
    sa.Column("datasource_id", sa.BigInteger, primary_key=True),
    sa.Column("ch_database", sa.String(64), nullable=False),
    sa.Column("connector_name", sa.String(64), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("snapshot_at", sa.DateTime),
    sa.Column("last_event_at", sa.DateTime),
    sa.Column("lag_ms", sa.BigInteger),
    sa.Column("error_msg", sa.String(500)),
    sa.Column("updated_at", sa.DateTime, nullable=False),
    sa.Column("generation", sa.Integer, nullable=False, server_default=sa.text("1")),
)

datasource_mirror_table = sa.Table(
    "datasource_mirror_table", metadata,
    sa.Column("datasource_id", sa.BigInteger, primary_key=True),
    sa.Column("table_name", sa.String(128), primary_key=True),
    sa.Column("phase", sa.String(16), nullable=False),
    sa.Column("rows_synced", sa.BigInteger),
    sa.Column("last_event_at", sa.DateTime),
    sa.Column("error_msg", sa.String(500)),
    sa.Column("updated_at", sa.DateTime, nullable=False),
)

datasource_write_grant = sa.Table(
    "datasource_write_grant", metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("datasource_id", sa.BigInteger, nullable=False),
    sa.Column("user_id", sa.BigInteger, nullable=False),
    sa.Column("table_name", sa.String(128), nullable=False),
    sa.Column("granted_by", sa.BigInteger),
    sa.Column("created_at", sa.DateTime, nullable=False),
)
