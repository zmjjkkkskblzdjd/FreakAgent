from __future__ import annotations

import os
from pathlib import Path

_ALLFUSION_ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv
    load_dotenv(_ALLFUSION_ROOT / ".env")
    load_dotenv(_ALLFUSION_ROOT / "qa_harness" / ".env")
    load_dotenv(_ALLFUSION_ROOT / "DataAnalyzeAgent" / "DataAnalyzeAgent" / ".env")
except ImportError:
    pass  

DEFAULT_DIR = os.getenv(
    "KB_SERVICE_DIR",
    str(_ALLFUSION_ROOT / "data" / "kb_db"),
)

QA_KB_COLLECTION = "qa_kb"
GLOSSARY_COLLECTION = "glossary"
FEWSHOT_COLLECTION = "fewshot"

MYSQL_HOST = os.getenv("KB_MYSQL_HOST", "192.168.1.136")
MYSQL_PORT = int(os.getenv("KB_MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("KB_MYSQL_DATABASE", "sales_meta")
MYSQL_USER = os.getenv("KB_MYSQL_USER", "meta_ro")
MYSQL_PASSWORD = os.getenv("KB_MYSQL_PASSWORD", "")

API_HOST = os.getenv("KB_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("KB_API_PORT", "8001"))
