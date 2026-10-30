"""Structured JSON logging: levels, request ID per request, file output, secret redaction."""
import contextvars
import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from . import config

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

SECRET_KEYS = {"api_key", "authorization", "x-api-key", "password", "token", "secret"}
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+)")


def _redact_text(text: str) -> str:
    redacted = SECRET_RE.sub("[REDACTED]", text)
    if config.AI_API_KEY:
        redacted = redacted.replace(config.AI_API_KEY, "[REDACTED]")
    return redacted


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": _redact_text(record.getMessage()),
        }
        for key, value in (getattr(record, "ctx", None) or {}).items():
            data[key] = "[REDACTED]" if key.lower() in SECRET_KEYS else value
        if record.exc_info:
            data["exception"] = _redact_text(self.formatException(record.exc_info))
        return json.dumps(data, default=str)


def log_event(logger: logging.Logger, level: int, message: str, **ctx) -> None:
    """Log an event name plus structured context fields."""
    logger.log(level, message, extra={"ctx": ctx})


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("meetinghub")
    if root.handlers:
        return
    root.setLevel(config.LOG_LEVEL)
    root.propagate = False
    fmt, flt = JsonFormatter(), RequestIdFilter()
    file_handler = RotatingFileHandler(config.LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    console = logging.StreamHandler()
    for handler in (file_handler, console):
        handler.setFormatter(fmt)
        handler.addFilter(flt)
        root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
