"""Change alerts via webhook, syslog, and email (docs/REQUIREMENTS.md section 9).

Config:

    alerts:
      webhooks:
        - https://hooks.example.com/otitbup
      syslog:
        address: 127.0.0.1
        port: 514
      email:
        smtp_host: mail.example.com
        smtp_port: 25
        from: otitbup@plant.example.com
        to: [ot-team@example.com]

Alert delivery failures are logged, never fatal: a broken webhook must not
stop backups.
"""
from __future__ import annotations

import json
import logging
import smtplib
import time
import urllib.request
from datetime import UTC, datetime
from email.message import EmailMessage
from logging.handlers import SysLogHandler
from pathlib import Path
from typing import Any

log = logging.getLogger("otitbup.alerts")


class AlertManager:
    def __init__(self, cfg: dict[str, Any] | None, state_path=None):
        self.cfg = cfg or {}
        # Rate-limiting: suppress a repeat of the same subject within
        # min_interval seconds (default 1h; 0 disables). State persists so
        # a flapping device can't spam across separate cron runs.
        self.min_interval = float(self.cfg.get("min_interval", 3600))
        self.state_path = Path(state_path) if state_path else None
        self._last_sent: dict[str, float] = self._load_state()

    def _load_state(self) -> dict[str, float]:
        if self.state_path and self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_state(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self._last_sent))
        except OSError as exc:
            log.warning("could not persist alert state: %s", exc)

    def _suppressed(self, subject: str) -> bool:
        if self.min_interval <= 0:
            return False
        now = time.time()
        last = self._last_sent.get(subject)
        if last is not None and now - last < self.min_interval:
            return True
        self._last_sent[subject] = now
        # Forget entries older than the window so state doesn't grow.
        self._last_sent = {
            k: v for k, v in self._last_sent.items()
            if now - v < self.min_interval
        }
        self._save_state()
        return False

    def notify(self, subject: str, body: str) -> None:
        if self._suppressed(subject):
            log.info("alert suppressed (rate limit): %s", subject)
            return
        for url in self.cfg.get("webhooks") or []:
            self._webhook(url, subject, body)
        if self.cfg.get("syslog"):
            self._syslog(self.cfg["syslog"], subject)
        if self.cfg.get("email"):
            self._email(self.cfg["email"], subject, body)

    def _webhook(self, url: str, subject: str, body: str) -> None:
        payload = json.dumps({
            "subject": subject,
            "body": body,
            "timestamp": datetime.now(UTC).isoformat(),
        }).encode()
        request = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(request, timeout=10).close()
        except Exception as exc:
            log.warning("webhook %s failed: %s", url, exc)

    def _syslog(self, cfg: dict[str, Any], subject: str) -> None:
        try:
            handler = SysLogHandler(
                address=(cfg.get("address", "127.0.0.1"), int(cfg.get("port", 514)))
            )
            record = logging.LogRecord(
                "otitbup", logging.WARNING, "", 0, subject, None, None
            )
            handler.emit(record)
            handler.close()
        except Exception as exc:
            log.warning("syslog alert failed: %s", exc)

    def _email(self, cfg: dict[str, Any], subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = cfg.get("from", "otitbup@localhost")
        message["To"] = ", ".join(cfg.get("to") or [])
        message.set_content(body)
        try:
            with smtplib.SMTP(
                cfg.get("smtp_host", "localhost"),
                int(cfg.get("smtp_port", 25)),
                timeout=15,
            ) as smtp:
                smtp.send_message(message)
        except Exception as exc:
            log.warning("email alert failed: %s", exc)
