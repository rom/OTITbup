"""Additional OT/ICS vendor drivers built on the existing capture cores.

None of these controllers has a fully open engineering protocol — Yokogawa
FA-M3/STARDOM, Honeywell ControlEdge, Emerson ROC/FloBoss, Bachmann M1,
B&R Automation and Fanuc CNC are all programmed with proprietary vendor
tools. Rather than fake those protocols, each driver here rides on the
reachable transport the device does expose (an embedded web server, SSH/
SFTP, or DNP3) and is honest about capturing identity/config at that
level. The authoritative engineering project stays versioned via
generic_file; these drivers add the on-device fingerprint/backup that the
box actually serves.

Registered:
  - yokogawa_web    Yokogawa FA-M3 / STARDOM web export (generic_http)
  - honeywell_web   Honeywell ControlEdge PLC/RTU web export (generic_http)
  - fanuc_cnc       Fanuc CNC embedded web server (generic_http)
  - bachmann_m1     Bachmann M1 controller project over SFTP
  - br_automation   B&R Automation Runtime project over SFTP
  - emerson_roc     Emerson ROC / FloBoss RTU attributes over DNP3
"""
from __future__ import annotations

from .generic_dnp3 import GenericDNP3Driver
from .generic_http import GenericHTTPDriver
from .generic_sftp import GenericSFTPDriver


class YokogawaWebDriver(GenericHTTPDriver):
    """Yokogawa FA-M3 (e-RT3), STARDOM FCN/FCJ and related controllers.
    Logic is engineered in WideField3 / Logic Designer (proprietary) — keep
    those project exports in generic_file. This driver pulls what the
    controller's embedded web server exposes (status/parameter pages, and
    config archives where the firmware offers a download). Export paths vary
    by model/firmware, so set options.urls to your device's endpoints."""

    name = "yokogawa_web"


class HoneywellWebDriver(GenericHTTPDriver):
    """Honeywell ControlEdge PLC / RTU (and web-managed field devices).
    ControlEdge Builder projects are proprietary — version their exports
    with generic_file. This driver fetches what the ControlEdge web server
    serves (diagnostics/parameter pages, config downloads where offered);
    set options.urls to the endpoints of your firmware. HTTPS with
    verify_tls: false is common on these appliances."""

    name = "honeywell_web"


class FanucCNCDriver(GenericHTTPDriver):
    """Fanuc CNC (Series 0i/30i/31i/32i). The machine data protocol FOCAS
    is proprietary and requires Fanuc's FWLIB — it is NOT implemented here.
    Many Fanuc controls and their Ethernet boards do expose an embedded
    web server (iHMI / basic maintenance pages); this driver captures only
    what is reachable over HTTP. Point options.urls at the pages/downloads
    your control serves. For full parameter/program backups, export from
    the control and version them with generic_file."""

    name = "fanuc_cnc"


class BachmannM1Driver(GenericSFTPDriver):
    """Bachmann M1 controllers (MX/CX/MC series). The M1 runtime stores its
    application and configuration on the removable CFC (CompactFlash) card,
    reachable over SSH/SFTP where the controller has it enabled. Defaults
    fetch /cfc0 (the project/config volume); the mount point varies by
    firmware and hardware, so override options.paths if yours differs.
    SolutionCenter project files themselves belong in generic_file."""

    name = "bachmann_m1"
    default_paths = ["/cfc0"]


class BRAutomationDriver(GenericSFTPDriver):
    """B&R (B&R Automation / part of ABB) X20/X90/Automation PC targets.
    Automation Studio is the proprietary engineering tool; its project is
    the source of truth (generic_file). Where the target runs a Linux-based
    Automation Runtime that exposes SSH/SFTP, this driver fetches the
    deployed project/config files — set options.paths to the runtime's
    project directory for your target (there is no universal default)."""

    name = "br_automation"


class EmersonROCDriver(GenericDNP3Driver):
    """Emerson ROC800/ROC800L and FloBoss (100/107) flow computers/RTUs.
    The native ROC Plus protocol is proprietary and is NOT implemented; ROC
    configuration is engineered in ROCLINK 800 (version its exports with
    generic_file). Most ROC/FloBoss units can also serve DNP3 — this driver
    reads their DNP3 group-0 device attributes (vendor/product/serial/
    versions) for a firmware/identity fingerprint. Requires DNP3 enabled on
    the unit; set options.outstation / options.port to match."""

    name = "emerson_roc"
