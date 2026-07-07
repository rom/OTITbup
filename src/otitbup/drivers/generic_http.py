"""HTTP(S) config-export driver, stdlib-only.

For devices managed via a web interface rather than a CLI — the primary
target is Moxa NPort serial-to-ethernet converters (registered alias:
moxa_nport), whose web console exposes the device configuration as an
exportable file. Point `urls` at the export endpoint(s) of your model and
firmware:

    - name: nport-01
      driver: moxa_nport
      address: 10.10.0.40
      credentials: nport-01           # username/password for the web UI
      options:
        urls:
          - "http://{address}/ConfigExport"   # {address} is substituted
        # verify_tls: false           # for self-signed device certs (https)

Requests go DIRECTLY to the device (proxy environment variables are
ignored — backup traffic must not leave the OT network). Basic and Digest
authentication are attempted with the configured credentials.
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..models import Device
from .base import Artifact, Driver, DriverError


def _artifact_name(url: str, index: int) -> str:
    path = urllib.parse.urlparse(url).path.strip("/")
    base = path.rsplit("/", 1)[-1] if path else ""
    if not base:
        base = f"response_{index}"
    return base if "." in base else f"{base}.txt"


class GenericHTTPDriver(Driver):
    name = "generic_http"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        options = device.options
        urls = options.get("urls") or (
            [options["url"]] if options.get("url") else []
        )
        if not urls:
            raise DriverError(
                f"{device.qualified_name}: options.urls is required "
                "(the device's config export endpoint)"
            )
        timeout = float(options.get("timeout", 15))

        handlers: list[urllib.request.BaseHandler] = [
            # Never route device traffic through a proxy.
            urllib.request.ProxyHandler({}),
        ]
        if secrets and secrets.get("username"):
            password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            for url in urls:
                resolved = url.format(address=device.address or "")
                password_mgr.add_password(
                    None, resolved,
                    str(secrets["username"]), str(secrets.get("password", "")),
                )
            handlers.append(urllib.request.HTTPBasicAuthHandler(password_mgr))
            handlers.append(urllib.request.HTTPDigestAuthHandler(password_mgr))
        if options.get("verify_tls") is False:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*handlers)

        artifacts = []
        for index, url in enumerate(urls):
            resolved = url.format(address=device.address or "")
            try:
                with opener.open(resolved, timeout=timeout) as response:
                    data = response.read()
            except (urllib.error.URLError, OSError) as exc:
                raise DriverError(
                    f"{device.qualified_name}: fetch of {resolved} "
                    f"failed: {exc}"
                ) from exc
            artifacts.append(
                Artifact(
                    name=_artifact_name(resolved, index),
                    data=data,
                    kind="config",
                    meta={"url": resolved},
                )
            )
        return artifacts


class MoxaNPortDriver(GenericHTTPDriver):
    """Alias with a Moxa NPort-specific name; behavior is generic_http.
    NPort export paths differ per model/firmware — set options.urls to
    your device's configuration export endpoint."""

    name = "moxa_nport"


class SiemensSicamDriver(GenericHTTPDriver):
    """Siemens SICAM A8000 (CP-8000/CP-8021/CP-8022/CP-8050) RTUs.
    Engineering is proprietary (SICAM TOOLBOX II / Device Manager) — keep
    parameter-set exports versioned with generic_file. This driver pulls
    what the RTU's integrated web server exposes (diagnostics/parameter
    pages, config archives where the firmware offers them); set
    options.urls to the endpoints of your firmware."""

    name = "siemens_sicam"


class AbbRtu500Driver(GenericHTTPDriver):
    """ABB RTU500 series (RTU520, RTU540, RTU560). The RTU500 integrated
    web server exposes status/configuration pages and file downloads;
    set options.urls to your firmware's export endpoints (https with
    verify_tls: false is common on these). Full configurations are
    engineered in RTUtil500 — version its exports with generic_file.
    Registered as abb_rtu520 and abb_rtu560."""

    name = "abb_rtu500"
