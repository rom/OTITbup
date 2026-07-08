"""HMI/SCADA, substation IED and RTU drivers built on the existing cores.

None of these products has a fully open engineering protocol — the HMI/
SCADA platforms are configured with proprietary Windows engineering suites,
protection relays with vendor tools (DIGSI, PCM600, EnerVista, acSELerator,
...), and the RTUs with their own configurators. Rather than fake any of
those, each driver here rides on the reachable, open transport the device
does expose and is honest in its docstring about what that captures:

  - SCADA/HMI projects are files on an engineering station/server, fetched
    over SFTP (GenericSFTPDriver). Set options.paths to the project
    location; the authoritative project export still belongs in
    generic_file.
  - Protection relays that speak IEC 61850 are fingerprinted with the MMS
    Identify handshake (IEC61850MMSDriver → vendor/model/revision). Relays
    without open MMS are captured at the HTTP level (GenericHTTPDriver)
    where they run an embedded web server.
  - RTUs are fingerprinted over DNP3 group-0 device attributes
    (GenericDNP3Driver) or their embedded web server (GenericHTTPDriver).

Registered:
  HMI/SCADA (SFTP project fetch): aveva_edge, zenon, movicon,
    factorytalk_se, vtscada, clearscada / geo_scada,
    siemens_wincc_unified, reliance_scada
  Substation IEDs: siemens_siprotec, abb_relion, schneider_micom,
    nr_electric, nari_relay (IEC 61850 MMS identity); sel_relay,
    ge_multilin (HTTP config/identity)
  RTUs: kingfisher_rtu, motorola_ace (DNP3 attributes); satec_rtu (HTTP)
"""
from __future__ import annotations

from .generic_dnp3 import GenericDNP3Driver
from .generic_http import GenericHTTPDriver
from .generic_sftp import GenericSFTPDriver
from .iec61850 import IEC61850MMSDriver

# --------------------------------------------------------- HMI / SCADA (SFTP)


class AvevaEdgeDriver(GenericSFTPDriver):
    """AVEVA Edge (formerly InduSoft Web Studio / Wonderware InTouch Edge).
    Applications are a project folder (.APP + screens/scripts) on the
    engineering station or runtime host; the IWS/Edge engineering tool is
    proprietary. This driver fetches that project directory over SFTP — set
    options.paths to your application folder (there is no universal
    default). Keep the authoritative export in generic_file."""

    name = "aveva_edge"


class ZenonDriver(GenericSFTPDriver):
    """COPA-DATA zenon (Engineering Studio / Service Grid). zenon projects
    live in a workspace/project tree on the engineering station; the zenon
    Editor and its SQL project store are proprietary. This driver fetches
    the project workspace over SFTP — set options.paths to your project
    directory. The definitive export belongs in generic_file."""

    name = "zenon"


class MoviconDriver(GenericSFTPDriver):
    """Progea / Emerson Movicon 11 and Movicon.NExT. Movicon stores its
    application as XML/project files on disk; the Movicon IDE is
    proprietary. This driver fetches the project directory over SFTP — set
    options.paths to your .movprj / project folder location."""

    name = "movicon"


class FactoryTalkSEDriver(GenericSFTPDriver):
    """Rockwell FactoryTalk View SE (Site Edition), distinct from the
    machine-level FactoryTalk View ME driver (factorytalk_view). SE HMI
    projects and the FactoryTalk Directory live as files on the
    distributed HMI/application servers. This driver fetches the SE HMI
    project directory over SFTP — set options.paths to the project
    location on the server. Studio 5000 / FT View Studio remain the
    engineering source of truth (generic_file)."""

    name = "factorytalk_se"


class VTScadaDriver(GenericSFTPDriver):
    """Trihedral VTScada. A VTScada application is a versioned set of files
    (.SRC / layers) under the application directory; the VTScada IDE is
    proprietary. This driver fetches the application directory over SFTP —
    set options.paths to your VTScada application folder."""

    name = "vtscada"


class ClearScadaDriver(GenericSFTPDriver):
    """AVEVA / Schneider ClearSCADA, now Geo SCADA Expert. The configuration
    lives in the server database and exported files (.sde / database dirs)
    on the ClearSCADA/Geo SCADA server; ViewX/ClearSCADA is proprietary.
    This driver fetches the configured server files/exports over SFTP — set
    options.paths to the database/export location. Registered as both
    clearscada and geo_scada."""

    name = "clearscada"


class SiemensWinccUnifiedDriver(GenericSFTPDriver):
    """Siemens WinCC Unified (TIA Portal-based, distinct from classic WinCC
    and WinCC OA). Unified runtime projects/loaded configuration reside as
    files on the Unified PC or Comfort/Unified panel; TIA Portal is the
    proprietary engineering tool. This driver fetches the runtime project
    files over SFTP — set options.paths to the Unified runtime/project
    location. The TIA project archive belongs in generic_file."""

    name = "siemens_wincc_unified"


class RelianceScadaDriver(GenericSFTPDriver):
    """Reliance SCADA (GEOVAP). Reliance projects are a project folder on
    the engineering/runtime host; the Reliance Design environment is
    proprietary. This driver fetches the project directory over SFTP — set
    options.paths to your Reliance project location."""

    name = "reliance_scada"


# --------------------------------------------- Substation IEDs (IEC 61850 MMS)


class SiemensSiprotecDriver(IEC61850MMSDriver):
    """Siemens SIPROTEC 4 / SIPROTEC 5 protection relays. Settings are
    engineered in DIGSI (proprietary) — version DIGSI/SCD/CID exports with
    generic_file. SIPROTEC IEDs speak IEC 61850 MMS on TCP/102, so this
    driver rides the iec61850_mms identity handshake to capture
    vendor/model/revision as a firmware fingerprint. Experimental, same
    caveats as iec61850_mms; if the MMS handshake is refused, capture the
    web/HTTP export instead."""

    name = "siemens_siprotec"


class AbbRelionDriver(IEC61850MMSDriver):
    """ABB Relion protection relays (REF/RET/REL 6xx and 5xx series).
    Relion IEDs are engineered in PCM600 (proprietary) — keep SCD/CID
    exports in generic_file. Relion supports IEC 61850 MMS on TCP/102;
    this driver reuses the iec61850_mms Identify handshake for a
    vendor/model/revision fingerprint (experimental)."""

    name = "abb_relion"


class SchneiderMicomDriver(IEC61850MMSDriver):
    """Schneider Electric MiCOM (P-series) and Easergy (P3/P5) protection
    relays. Engineered with MiCOM S1 Studio / Easergy Studio (proprietary)
    — version setting exports with generic_file. Where the relay has
    IEC 61850 enabled it answers MMS on TCP/102; this driver reuses the
    iec61850_mms Identify handshake for an identity fingerprint. Relays
    without 61850 should be captured over HTTP instead (experimental)."""

    name = "schneider_micom"


class NrElectricDriver(IEC61850MMSDriver):
    """NR Electric PCS-9xx protection & control IEDs. Engineered with NR's
    proprietary tooling (PCS-Explorer) — keep SCD/CID exports in
    generic_file. PCS IEDs are IEC 61850 devices; this driver reuses the
    iec61850_mms Identify handshake for a vendor/model/revision
    fingerprint (experimental)."""

    name = "nr_electric"


class NariRelayDriver(IEC61850MMSDriver):
    """NARI (NARI-Relays) RCS/PCS protection & control IEDs. Engineered
    with NARI's proprietary tooling — version SCD/CID exports with
    generic_file. These are IEC 61850 devices; this driver reuses the
    iec61850_mms Identify handshake for an identity fingerprint
    (experimental)."""

    name = "nari_relay"


# ------------------------------------------ Substation IEDs (HTTP identity)


class SelRelayDriver(GenericHTTPDriver):
    """Schweitzer Engineering Laboratories (SEL) protection relays and the
    SEL RTAC. SEL settings are retrieved with SEL-specific tooling —
    acSELerator QuickSet, the SEL ASCII terminal protocol (ID/STA/SHO), or
    SEL FTP — none of which is implemented here; the SSH/terminal path is
    covered by the sel_terminal profile. This driver captures only what a
    SEL device exposes over an embedded web server (the RTAC and web-
    enabled relays); set options.urls to your device's export/status
    endpoints. Authoritative settings exports belong in generic_file."""

    name = "sel_relay"


class GeMultilinDriver(GenericHTTPDriver):
    """GE (GE Vernova / Multilin) UR (B30/T60/L90/...) and SR (750/760,
    369, ...) protection relays. Settings are engineered in EnerVista
    UR/SR Setup (proprietary); the relays speak Modbus and DNP3 for data,
    and UR devices with the Ethernet option can serve web/status pages.
    Neither EnerVista nor the Modbus register map is reimplemented here —
    this driver captures what the relay's embedded web server exposes; set
    options.urls to your firmware's endpoints. Keep setting-file exports in
    generic_file (UR relays with IEC 61850 can also use iec61850_mms)."""

    name = "ge_multilin"


# ------------------------------------------------------ RTUs (DNP3 / HTTP)


class KingfisherRtuDriver(GenericDNP3Driver):
    """Servelec Technologies / Schneider Electric Kingfisher RTUs. Logic is
    engineered in the Kingfisher toolkit (proprietary, not implemented) —
    version its exports with generic_file. Kingfisher RTUs speak DNP3; this
    driver reuses generic_dnp3 to read their group-0 device attributes
    (vendor/product/serial/versions) for a firmware/identity fingerprint.
    Requires DNP3 enabled; set options.outstation / options.port to
    match."""

    name = "kingfisher_rtu"


class MotorolaAceDriver(GenericDNP3Driver):
    """Motorola (Motorola Solutions) ACE3600 RTUs. The native MDLC protocol
    and the STS programming tool are proprietary and are NOT implemented —
    keep STS exports in generic_file. Where the ACE3600 is configured with
    a DNP3 port, this driver reuses generic_dnp3 to read its group-0 device
    attributes for an identity fingerprint. Requires DNP3 enabled on the
    unit; set options.outstation / options.port to match."""

    name = "motorola_ace"


class SatecRtuDriver(GenericHTTPDriver):
    """SATEC power meters / RTUs (PM series, EM720/EM920, expertmeter).
    Configuration is done with PAS (proprietary) and the device serves data
    over Modbus/DNP3; neither is reimplemented here. Most SATEC units run an
    embedded web server — this driver captures what it exposes (status/
    parameter pages, config downloads where offered). Set options.urls to
    your firmware's endpoints (HTTPS with verify_tls: false is common)."""

    name = "satec_rtu"
