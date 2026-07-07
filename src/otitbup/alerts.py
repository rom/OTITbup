"""Change alerts via webhook, syslog, and email (REQUIREMENTS.md section 9).

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
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from logging.handlers import SysLogHandler
from typing import Any

log = logging.getLogger("otitbup.alerts")


class AlertManager:
    def __init__(self, cfg: dict[str, Any] | None):
        self.cfg = cfg or {}

    def notify(self, subject: str, body: str) -> None:
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
