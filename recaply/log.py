"""
Structured logging: `log.info("event", key=value, ...)` prints one line,
as `key=value` pairs (or JSON with RECAPLY_JSON_LOGS=1). Anything that looks
like an API key is masked before it is written.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time

_SECRET = re.compile(r"(key=|api[_-]?key[\"':= ]+|Bearer |x-api-key[\"':= ]+|sk-|gsk_|AIza)[A-Za-z0-9_\-\.]{6,}",
                     re.IGNORECASE)


def redact(text: str) -> str:
    """Mask API keys and bearer tokens inside `text`."""
    return _SECRET.sub(lambda m: m.group(1) + "***", str(text))


class _Formatter(logging.Formatter):
    def __init__(self, as_json: bool):
        super().__init__()
        self.as_json = as_json

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {}) or {}
        if self.as_json:
            out = {"t": round(record.created, 3), "level": record.levelname.lower(),
                   "logger": record.name, "event": record.getMessage(), **fields}
            return redact(json.dumps(out, default=str))
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        kv = " ".join(f"{k}={v}" for k, v in fields.items())
        return redact(f"{ts} {record.levelname[0]} {record.name}: {record.getMessage()}" + (f" {kv}" if kv else ""))


class Logger:
    """Thin wrapper so call sites can pass fields as keyword arguments."""

    def __init__(self, name: str):
        self._log = logging.getLogger(name)

    def _emit(self, level: int, event: str, **fields):
        if self._log.isEnabledFor(level):
            self._log.log(level, event, extra={"fields": fields})

    def debug(self, event: str, **fields):
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields):
        self._emit(logging.INFO, event, **fields)

    def warning(self, event: str, **fields):
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, **fields):
        self._emit(logging.ERROR, event, **fields)


_configured = False


def get_logger(name: str) -> Logger:
    """A structured logger under the `recaply` namespace."""
    global _configured
    if not _configured:
        from recaply.settings import settings
        root = logging.getLogger("recaply")
        if not root.handlers:
            h = logging.StreamHandler(sys.stderr)
            h.setFormatter(_Formatter(settings.json_logs))
            root.addHandler(h)
            root.setLevel(logging.INFO)
            root.propagate = False
        _configured = True
    return Logger(name if name.startswith("recaply") else f"recaply.{name}")
