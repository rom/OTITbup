"""Ticketing hooks: open a ticket when noteworthy events fire.

Turns operational events (an unexpected change, a backup failure) into
tickets in ServiceNow, Jira, or any generic webhook — stdlib HTTP, no SDK.
Configured under `tickets`:

    tickets:
      backend: servicenow        # servicenow | jira | rt | generic
      url: https://example.service-now.com
      username: svc-otitbup      # basic auth (servicenow/generic/rt)
      password: ...
      on: [backup.error, change.unexpected]   # event types that open a ticket
      # servicenow: table: incident
      # jira: project: OT, issue_type: Task, token: <PAT>, email: <email>
      # rt: queue: OT, token: <RT-token>

Delivery failures are logged, never fatal. Only the configured `on` event
types create tickets, so routine events don't spam the queue.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("otitbup.tickets")

_DEFAULT_ON = ["backup.error", "change.unexpected"]


class TicketManager:
    def __init__(self, cfg: dict[str, Any] | None):
        self.cfg = cfg or {}
        self.backend = self.cfg.get("backend")
        self.on = set(self.cfg.get("on") or _DEFAULT_ON)

    def wants(self, event_type: str) -> bool:
        return bool(self.backend) and event_type in self.on

    def open_ticket(self, event_type: str, summary: str, detail: str) -> None:
        if not self.wants(event_type):
            return
        try:
            if self.backend == "servicenow":
                self._servicenow(event_type, summary, detail)
            elif self.backend == "jira":
                self._jira(event_type, summary, detail)
            elif self.backend == "rt":
                self._rt(event_type, summary, detail)
            else:
                self._generic(event_type, summary, detail)
        # A ticket sink must never be fatal (contract). Besides network
        # errors, a misconfiguration (e.g. `backend` set but `url` missing,
        # which raises KeyError from self.cfg['url']) must be logged, not
        # allowed to abort the backup run that emitted the event.
        except Exception as exc:
            log.warning("ticket creation failed (%s): %s", self.backend, exc)

    def _request(self, url: str, payload: dict, headers: dict) -> None:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", **headers},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(req, timeout=float(self.cfg.get("timeout", 15))).close()

    def _basic_header(self) -> dict:
        user = self.cfg.get("username", "")
        pw = self.cfg.get("password", "")
        token = base64.b64encode(f"{user}:{pw}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _servicenow(self, event_type, summary, detail) -> None:
        table = self.cfg.get("table", "incident")
        url = f"{self.cfg['url'].rstrip('/')}/api/now/table/{table}"
        self._request(url, {
            "short_description": summary,
            "description": detail,
            "category": "OT",
            "u_source": "otitbup",
        }, self._basic_header())

    def _jira(self, event_type, summary, detail) -> None:
        url = f"{self.cfg['url'].rstrip('/')}/rest/api/2/issue"
        email = self.cfg.get("email", self.cfg.get("username", ""))
        token = self.cfg.get("token", self.cfg.get("password", ""))
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._request(url, {
            "fields": {
                "project": {"key": self.cfg.get("project", "OT")},
                "summary": summary,
                "description": detail,
                "issuetype": {"name": self.cfg.get("issue_type", "Task")},
            }
        }, {"Authorization": f"Basic {auth}"})

    def _rt(self, event_type, summary, detail) -> None:
        # Request Tracker REST 2.0: POST a ticket as JSON with a token.
        url = f"{self.cfg['url'].rstrip('/')}/REST/2.0/ticket"
        headers = {}
        if self.cfg.get("token"):
            headers["Authorization"] = f"token {self.cfg['token']}"
        elif self.cfg.get("username"):
            headers = self._basic_header()
        self._request(url, {
            "Queue": self.cfg.get("queue", "General"),
            "Subject": summary,
            "Content": detail,
            "ContentType": "text/plain",
        }, headers)

    def _generic(self, event_type, summary, detail) -> None:
        headers = {}
        if self.cfg.get("username"):
            headers = self._basic_header()
        self._request(self.cfg["url"], {
            "event": event_type,
            "summary": summary,
            "detail": detail,
            "source": "otitbup",
        }, headers)
