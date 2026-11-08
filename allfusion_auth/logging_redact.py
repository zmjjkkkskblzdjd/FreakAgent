from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from allfusion_auth.redact import redact_text

_LOGGERS = (
    "uvicorn", "uvicorn.error", "uvicorn.access",
    "fastapi", "httpx", "sqlalchemy.engine",
)


class RedactFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — 格式化失败时放行，不能吞日志
            return True
        clean = redact_text(message)
        if clean != message:
            record.msg = clean
            record.args = ()
        return True


def install_logging_redact() -> None:
    filt = RedactFilter()
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    for handler in root.handlers:
        if not any(isinstance(f, RedactFilter) for f in handler.filters):
            handler.addFilter(filt)
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactFilter) for f in logger.filters):
            logger.addFilter(filt)


def install_exception_handler(app: FastAPI) -> None:
    logger = logging.getLogger("allfusion.error")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:  # noqa: ANN001
        hint = redact_text(str(exc))[:500]
        logger.error("%s %s -> %s", request.method, request.url.path, hint)
        return JSONResponse(status_code=500, content={
            "detail": {"error": "internal_error", "hint": hint},
        })
