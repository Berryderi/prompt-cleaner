"""JSON-lines logging. Callers pass counts, timings and reasons, never dictated text."""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path

LOGGER_NAME = "prompt_cleaner"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info and record.exc_info[0] is not None:
            # type only: a traceback message could quote the user's text
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup(log_dir: Path) -> logging.Logger:
    """Attach a rotating JSONL file handler (idempotent) and return the logger."""
    logger = get_logger()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "cleaner.jsonl", maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


def event(name: str, level: int = logging.INFO, exc_info=None, **fields) -> None:
    get_logger().log(level, name, extra={"fields": fields}, exc_info=exc_info)
