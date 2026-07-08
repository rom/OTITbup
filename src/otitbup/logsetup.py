"""Structured logging with optional file rotation.

Configured under `logging` in the config:

    logging:
      level: info            # debug | info | warning | error
      format: text           # text | json
      file: /var/log/otitbup/otitbup.log   # optional; else stderr
      max_bytes: 10485760    # rotate at 10 MiB
      backups: 5

JSON logs suit shipping to a SIEM; text is the default for a console.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from typing import Any

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
           "warning": logging.WARNING, "error": logging.ERROR}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure(cfg: dict[str, Any] | None, verbose: bool = False) -> None:
    cfg = cfg or {}
    level = logging.DEBUG if verbose else _LEVELS.get(
        str(cfg.get("level", "info")).lower(), logging.INFO)

    if str(cfg.get("format", "text")).lower() == "json":
        formatter: logging.Formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s")

    if cfg.get("file"):
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            cfg["file"],
            maxBytes=int(cfg.get("max_bytes", 10 * 1024 * 1024)),
            backupCount=int(cfg.get("backups", 5)),
        )
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [handler]
