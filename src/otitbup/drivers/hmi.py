"""HMI / SCADA backup drivers.

SCADA/HMI projects change more often than PLC logic and are a common gap.
Coverage here:

- ignition_gateway: Inductive Automation Ignition exposes a full gateway
  backup as a downloadable .gwbk over HTTP — the cleanest HMI backup
  available. This driver fetches it (stdlib HTTP, Basic auth).
- wincc / factorytalk_view / generic_scada: SCADA project trees are files
  on an engineering station or server. These are SFTP presets (fetch the
  project directory); set options.paths to your project location. WinCC
  and PCS7 projects can alternatively be dropped into a generic_file watch
  folder.
"""
from __future__ import annotations

from typing import Any

from ..models import Device
from .base import Artifact, Driver, DriverError
from .generic_sftp import GenericSFTPDriver


class IgnitionGatewayDriver(Driver):
    """Fetch an Ignition gateway backup (.gwbk).

        options:
          port: 8088                 # or 8043 for https
          scheme: http               # http | https
          verify_tls: true
          path: /system/gwbackup     # endpoint (default shown)

    Credentials: username/password of a gateway user allowed to back up.
    """

    name = "ignition_gateway"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        import ssl
        import urllib.error
        import urllib.request

        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        scheme = options.get("scheme", "http")
        port = int(options.get("port", 8043 if scheme == "https" else 8088))
        path = options.get("path", "/system/gwbackup")
        url = f"{scheme}://{device.address}:{port}{path}"

        handlers: list = [urllib.request.ProxyHandler({})]
        if secrets and secrets.get("username"):
            mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            mgr.add_password(None, url, str(secrets["username"]),
                             str(secrets.get("password", "")))
            handlers.append(urllib.request.HTTPBasicAuthHandler(mgr))
            handlers.append(urllib.request.HTTPDigestAuthHandler(mgr))
        if scheme == "https" and options.get("verify_tls") is False:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        opener = urllib.request.build_opener(*handlers)

        try:
            with opener.open(url, timeout=float(options.get("timeout", 60))) as r:
                data = r.read()
        except (urllib.error.URLError, OSError) as exc:
            raise DriverError(
                f"{device.qualified_name}: gateway backup fetch failed: {exc}"
            ) from exc
        if not data:
            raise DriverError(
                f"{device.qualified_name}: empty gateway backup"
            )
        return [Artifact(name="gateway-backup.gwbk", data=data, kind="project")]


class WinCCDriver(GenericSFTPDriver):
    """Siemens WinCC / PCS7 project tree via SFTP. Set options.paths to the
    project directory on the engineering station/server."""

    name = "wincc"


class FactoryTalkViewDriver(GenericSFTPDriver):
    """Rockwell FactoryTalk View project tree via SFTP. Set options.paths
    to the project directory."""

    name = "factorytalk_view"


class GenericSCADADriver(GenericSFTPDriver):
    """Vendor-neutral SCADA project fetch over SFTP. Set options.paths."""

    name = "generic_scada"


class WonderwareDriver(GenericSFTPDriver):
    """AVEVA/Wonderware InTouch/System Platform project (Galaxy) files via
    SFTP. Set options.paths to the project/aaPKG location."""

    name = "wonderware"


class IFixDriver(GenericSFTPDriver):
    """GE/Emerson iFIX SCADA project tree via SFTP. Set options.paths."""

    name = "ifix"


class CitectDriver(GenericSFTPDriver):
    """AVEVA Citect/Plant SCADA project tree via SFTP. Set options.paths."""

    name = "citect"


class WinccOADriver(GenericSFTPDriver):
    """Siemens WinCC OA (ETM PVSS) project tree via SFTP. Set options.paths."""

    name = "wincc_oa"


class KepwareDriver(GenericSFTPDriver):
    """PTC KEPServerEX / Kepware config. The server runs on Windows and its
    project is a .opf/.json file — fetch it via SFTP (or drop exports into a
    generic_file watch folder). Set options.paths to the project file."""

    name = "kepware"
