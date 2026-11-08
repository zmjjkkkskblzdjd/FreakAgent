# -*- coding: utf-8 -*-
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")   

CH_HTTP = os.getenv("QG_CH_HTTP", "http://192.168.1.136:8123/")
CH_DATABASE = os.getenv("QG_CH_DATABASE", "sales_dw")

CH_USER = os.getenv("QG_CH_USER", "")                    
CH_PASSWORD = os.getenv("QG_CH_PASSWORD", "")
CH_WRITER_USER = os.getenv("QG_CH_WRITER_USER", "")      
CH_WRITER_PASSWORD = os.getenv("QG_CH_WRITER_PASSWORD", "")

MYSQL_HOST = os.getenv("QG_MYSQL_HOST", "192.168.1.136")
MYSQL_PORT = int(os.getenv("QG_MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("QG_MYSQL_DATABASE", "sales_meta")
MYSQL_USER = os.getenv("QG_MYSQL_USER", "")
MYSQL_PASSWORD = os.getenv("QG_MYSQL_PASSWORD", "")

MYSQL_BIZ_HOST = os.getenv("QG_MYSQL_BIZ_HOST", "192.168.1.134")
MYSQL_BIZ_PORT = int(os.getenv("QG_MYSQL_BIZ_PORT", "3306"))
MYSQL_BIZ_DATABASE = os.getenv("QG_MYSQL_BIZ_DATABASE", "sales_biz")
MYSQL_BIZ_USER = os.getenv("QG_MYSQL_BIZ_USER", "")
MYSQL_BIZ_PASSWORD = os.getenv("QG_MYSQL_BIZ_PASSWORD", "")

MAX_WRITE_ROWS = int(os.getenv("QG_MAX_WRITE_ROWS", "1000"))      
WRITE_AUDIT_LOG = PROJECT_ROOT / "logs" / "write_audit.jsonl"
MUTATION_POLL_INTERVAL_SEC = float(os.getenv("QG_MUTATION_POLL_INTERVAL_SEC", "2"))
MUTATION_POLL_TIMEOUT_SEC = float(os.getenv("QG_MUTATION_POLL_TIMEOUT_SEC", "30"))

WRITE_JOURNAL_DB = Path(os.getenv(
    "QG_WRITE_JOURNAL_DB", str(PROJECT_ROOT / "data" / "write_journal.db")))
UNDO_WINDOW_SEC = int(os.getenv("QG_UNDO_WINDOW_SEC", "3600"))   
PREVIEW_SAMPLE_ROWS = int(os.getenv("QG_PREVIEW_SAMPLE_ROWS", "20"))

ALLOWED_DATABASES = {db.strip() for db in
                     os.getenv("QG_ALLOWED_DATABASES", CH_DATABASE).split(",") if db.strip()}

DETAIL_LIMIT = int(os.getenv("QG_DETAIL_LIMIT", "200"))     
AGG_LIMIT = int(os.getenv("QG_AGG_LIMIT", "5000"))           
MAX_LIMIT = int(os.getenv("QG_MAX_LIMIT", "50000"))          
DEFAULT_TIMEOUT_MS = int(os.getenv("QG_TIMEOUT_MS", "30000"))
MAX_TIMEOUT_MS = int(os.getenv("QG_MAX_TIMEOUT_MS", "60000"))
MAX_CONCURRENCY = int(os.getenv("QG_MAX_CONCURRENCY", "10"))
MAX_SQL_CHARS = int(os.getenv("QG_MAX_SQL_CHARS", "20000"))
MAX_MEMORY_BYTES = int(os.getenv("QG_MAX_MEMORY_BYTES", str(2 * 1024 ** 3)))

MAX_THREADS = int(os.getenv("QG_MAX_THREADS", "4"))

CATALOG_TTL_SEC = int(os.getenv("QG_CATALOG_TTL_SEC", "300"))

GATEWAY_SECRET = os.getenv("AF_GATEWAY_SECRET", "")
AUTH_SKEW_SEC = int(os.getenv("AF_AUTH_SKEW_SEC", "300"))
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in
                   os.getenv("QG_ALLOWED_ORIGINS", "").split(",") if o.strip()]

SOURCE_MAX_ROWS = int(os.getenv("AF_DEGRADE_MAX_ROWS", "200"))
SOURCE_TIMEOUT_MS = int(os.getenv("AF_DEGRADE_TIMEOUT_MS", "3000"))
SOURCE_MAX_CONCURRENCY = int(os.getenv("AF_DEGRADE_MAX_CONCURRENCY", "2"))
SOURCE_QUEUE_TIMEOUT_SEC = float(os.getenv("AF_DEGRADE_QUEUE_TIMEOUT_SEC", "1"))
BREAKER_FAILS = int(os.getenv("AF_BREAKER_FAILS", "3"))
BREAKER_OPEN_SEC = float(os.getenv("AF_BREAKER_OPEN_SEC", "30"))
BREAKER_MAX_OPEN_SEC = float(os.getenv("AF_BREAKER_MAX_OPEN_SEC", "300"))
FRESH_LAG_MS = int(os.getenv("AF_FRESH_LAG_MS", "5000"))

PREVIEW_TTL_SEC = int(os.getenv("AF_PREVIEW_TTL_SEC", "60"))
PREVIEW_RATE_PER_MIN = int(os.getenv("AF_PREVIEW_RATE_PER_MIN", "10"))
WRITE_LOCK_TIMEOUT_SEC = float(os.getenv("AF_WRITE_LOCK_TIMEOUT_SEC", "2"))

AUDIT_LOG = PROJECT_ROOT / "logs" / "audit.jsonl"

HOST = os.getenv("QG_HOST", "0.0.0.0")
PORT = int(os.getenv("QG_PORT", "8130"))
