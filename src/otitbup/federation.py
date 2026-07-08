"""Site collectors / Purdue-model federation (central roll-up).

Large OT estates are segmented: each plant (or Purdue level 3.5 DMZ) runs
its own OTITbup *collector* appliance that backs up the devices it can
reach, and a central appliance rolls those up for a single pane of glass.

The data plane is deliberately the boring, proven one: each collector
already pushes its git repo to a shared remote (`git.push` / `git.remote`),
so the backup *bytes* federate over plain git with no new protocol. This
module is the *control/visibility* plane on top of that: the central
appliance polls each collector's read-only JSON API (`GET /api/status`,
served by `otitbup serve`) over HTTPS with a scoped API token and
aggregates health into one view.

Config:

    federation:
      role: central                 # informational
      collectors:
        - name: plant-a
          url: https://plant-a.ot.example:8443
          token: otb_...            # a viewer-scoped token on the collector
          verify_tls: true          # or a CA bundle path
        - name: plant-b
          url: https://plant-b.ot.example:8443
          token_file: /etc/otitbup/plant-b.token

Everything is stdlib (urllib); a collector that is unreachable degrades to
an error row rather than failing the whole roll-up.
"""
from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("otitbup.federation")

_KEYS = ("devices", "covered", "never_backed_up", "stale", "failing",
         "blob_bytes")


@dataclass
class CollectorHealth:
    name: str
    url: str
    ok: bool
    totals: dict = field(default_factory=dict)
    error: str | None = None


def _token(entry: dict) -> str | None:
    if entry.get("token"):
        return str(entry["token"])
    if entry.get("token_file"):
        return Path(entry["token_file"]).read_text().strip()
    return None


def _ssl_context(verify) -> ssl.SSLContext | None:
    if verify is False:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if isinstance(verify, str):            # CA bundle path
        return ssl.create_default_context(cafile=verify)
    return None                            # default system trust


def poll_collector(entry: dict, timeout: float = 10.0) -> CollectorHealth:
    """Fetch one collector's /api/status. Never raises — a failure becomes
    an error row so one down site can't blind the whole roll-up."""
    name = entry.get("name", entry.get("url", "?"))
    url = str(entry.get("url", "")).rstrip("/")
    if not url:
        return CollectorHealth(name, url, False, error="no url configured")
    req = urllib.request.Request(url + "/api/status")
    token = _token(entry)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    ctx = _ssl_context(entry.get("verify_tls", True))
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        totals = {k: data.get("totals", {}).get(k, 0) for k in _KEYS}
        return CollectorHealth(name, url, True, totals=totals)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        log.warning("collector %s poll failed: %s", name, exc)
        return CollectorHealth(name, url, False, error=str(exc))


def poll_all(federation_cfg: dict, timeout: float = 10.0) -> list[CollectorHealth]:
    return [poll_collector(e, timeout=timeout)
            for e in (federation_cfg.get("collectors") or [])]


def aggregate(healths: list[CollectorHealth]) -> dict:
    """Sum totals across reachable collectors; report how many are down."""
    totals = {k: 0 for k in _KEYS}
    reachable = 0
    for h in healths:
        if not h.ok:
            continue
        reachable += 1
        for k in _KEYS:
            totals[k] += int(h.totals.get(k, 0))
    return {
        "collectors": len(healths),
        "reachable": reachable,
        "unreachable": len(healths) - reachable,
        "totals": totals,
    }
