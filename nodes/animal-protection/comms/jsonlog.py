"""JSON-lines logs, one file per program in logs/ (Q5), bounded in size (Q4).

    logger = program_logger("hvac", ROOT / "logs")
    log(logger, logging.INFO, "asked a peer", target="incubator-1", elapsed_ms=812.4)

Each line is one JSON object: time (UTC), level, program, message and the keyword fields. Files rotate at
MAX_BYTES, keeping BACKUPS old files.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_BYTES = 5_000_000
BACKUPS = 3


class JsonFormatter(logging.Formatter):
    """Formats a record as one JSON line, with the `fields` passed through `log()`."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time_utc": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(), "program": record.name, "message": record.getMessage()}
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(fields)
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def program_logger(program: str, logs_dir: Path | None, *, echo: bool = False, level: int = logging.INFO) -> logging.Logger:
    """The JSON-lines logger of one program, writing logs_dir/<program>.jsonl (no file when logs_dir is None)."""
    logger = logging.getLogger(program)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    formatter = JsonFormatter()
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            logs_dir / f"{program}.jsonl", maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    if echo:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    return logger


def log(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Log `message` with structured `fields` (each becomes a key of the JSON line)."""
    logger.log(level, message, extra={"fields": fields})
