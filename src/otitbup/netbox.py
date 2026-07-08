"""NetBox integration (CMDB / IPAM source of truth), stdlib-only.

Two directions, both read-only toward your git backups:

- reconcile: compare the otitbup inventory against NetBox devices — report
  devices in NetBox that otitbup does NOT back up (coverage gaps) and
  otitbup devices absent from NetBox (drift the other way).
- import: turn NetBox devices into an inventory-shaped YAML proposal
  (primary IP + a driver guessed from platform/role) for human review —
  the same review-before-use posture as discovery.

Config (also usable ad hoc via CLI flags):

    netbox:
      url: https://netbox.example.com
      token: <api-token>
      # verify_tls: true
      # filters: { status: active, role: network }   # NetBox query params
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


class NetBoxError(Exception):
    pass


@dataclass
class NetBoxDevice:
    name: str
    address: str | None
    role: str | None
    platform: str | None
    site: str | None
    manufacturer: str | None


@dataclass
class NetBoxReconciliation:
    not_backed_up: list[NetBoxDevice] = field(default_factory=list)
    not_in_netbox: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)


# Guess an otitbup driver from NetBox platform/manufacturer slugs.
_DRIVER_HINTS = {
    "ios": "cisco_ios", "iosxe": "cisco_ios", "nxos": "cisco_nxos",
    "asa": "cisco_asa", "junos": "juniper_junos", "eos": "arista_eos",
    "fortios": "fortinet_fortigate", "panos": "paloalto_panos",
    "routeros": "mikrotik_routeros", "comware": "hpe_comware",
    "procurve": "hpe_procurve", "vrp": "huawei_vrp", "exos": "extreme_exos",
    "scalance": "siemens_scalance", "ruggedcom": "ruggedcom_ros",
    "hirschmann": "hirschmann_hios", "moxa": "moxa_switch",
    "westermo": "westermo_weos",
}


class NetBoxClient:
    def __init__(
        self, url: str, token: str, verify_tls: bool = True,
        timeout: float = 15.0,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        handlers: list = [urllib.request.ProxyHandler({})]
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)

    def _get(self, path: str, params: dict) -> dict:
        query = urllib.parse.urlencode(params)
        url = f"{self.url}/api/{path}?{query}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Token {self.token}",
                          "Accept": "application/json"},
        )
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise NetBoxError(f"NetBox HTTP {exc.code} for {path}") from exc
        except OSError as exc:
            raise NetBoxError(f"NetBox unreachable: {exc}") from exc

    def devices(self, filters: dict | None = None) -> list[NetBoxDevice]:
        params = {"limit": 200, **(filters or {})}
        result: list[NetBoxDevice] = []
        path = "dcim/devices/"
        while True:
            page = self._get(path, params)
            for d in page.get("results", []):
                primary = d.get("primary_ip") or {}
                address = (primary.get("address") or "").split("/")[0] or None
                result.append(NetBoxDevice(
                    name=d.get("name") or f"device-{d.get('id')}",
                    address=address,
                    role=(d.get("role") or d.get("device_role") or {}).get("slug"),
                    platform=(d.get("platform") or {}).get("slug"),
                    site=(d.get("site") or {}).get("slug"),
                    manufacturer=((d.get("device_type") or {}).get(
                        "manufacturer") or {}).get("slug"),
                ))
            nxt = page.get("next")
            if not nxt:
                break
            # Follow pagination via absolute URL.
            params = dict(urllib.parse.parse_qsl(
                urllib.parse.urlparse(nxt).query))
        return result


def guess_driver(device: NetBoxDevice) -> str:
    for key in (device.platform, device.manufacturer):
        if key and key.lower() in _DRIVER_HINTS:
            return _DRIVER_HINTS[key.lower()]
        for hint, driver in _DRIVER_HINTS.items():
            if key and hint in key.lower():
                return driver
    return "generic_ssh"


def reconcile_netbox(config, client: NetBoxClient,
                     filters: dict | None = None) -> NetBoxReconciliation:
    inv_by_addr = {d.address: d for d in config.all_devices() if d.address}
    inv_addrs = set(inv_by_addr)
    nb_devices = client.devices(filters)
    nb_addrs = {d.address for d in nb_devices if d.address}

    result = NetBoxReconciliation()
    for d in nb_devices:
        if d.address and d.address in inv_addrs:
            result.matched.append(inv_by_addr[d.address].qualified_name)
        elif d.address:
            result.not_backed_up.append(d)
    for addr, device in sorted(inv_by_addr.items()):
        if addr not in nb_addrs:
            result.not_in_netbox.append(device.qualified_name)
    return result


def import_proposal(devices: list[NetBoxDevice], site: str, zone: str) -> str:
    lines = [
        "# otitbup NetBox import proposal — REVIEW BEFORE USE",
        "# Generated from NetBox. Verify each device, set credentials and",
        "# driver options, then merge the entries you approve.",
        "sites:", f"  - name: {site}", "    zones:",
        f"      - name: {zone}", "        devices:",
    ]
    for d in devices:
        if not d.address:
            continue
        safe = d.name.replace(" ", "-").replace("/", "-")
        lines += [
            f"          # netbox role={d.role} platform={d.platform}",
            f"          - name: {safe}",
            f"            driver: {guess_driver(d)}",
            f"            address: {d.address}",
            "            schedule: 1d",
            f"            credentials: {safe}",
        ]
    return "\n".join(lines) + "\n"
