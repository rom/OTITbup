"""Protocol gateways and web-managed serial-to-ethernet servers.

Many gateways (Modbus↔DNP3, fieldbus bridges, remote-access boxes) and
serial device servers have no CLI — they are configured through a web UI
that exposes a configuration export/backup. These drivers ride on the
generic_http machinery: set options.urls to your model/firmware's export
endpoint (paths differ per vendor and version). Credentials are the web
UI's username/password (Basic/Digest).

Registered:
  - moxa_mgate         Moxa MGate Modbus/protocol gateways
  - hms_anybus         HMS Anybus/Ixxat protocol gateways
  - prosoft_gateway    ProSoft Technology gateways
  - redlion_gateway    Red Lion / N-Tron protocol gateways
  - ewon_flexy         HMS eWON Flexy/Cosy remote-access gateways
  - kepware            KEPServerEX config export (drop .json/.opf via file)
  - digi_web           Digi web-managed serial/cellular gateways
  - lantronix_web      Lantronix web-managed device servers
  - advantech_eki_serial  Advantech EKI serial device servers (web)
  - generic_gateway    vendor-neutral web-managed gateway
"""
from __future__ import annotations

from .generic_http import GenericHTTPDriver


class _NamedHTTP(GenericHTTPDriver):
    """A generic_http driver under a vendor-specific name; behaviour is
    identical — configure options.urls for the device's export endpoint."""


class MoxaMGateDriver(_NamedHTTP):
    name = "moxa_mgate"


class HMSAnybusDriver(_NamedHTTP):
    name = "hms_anybus"


class ProSoftGatewayDriver(_NamedHTTP):
    name = "prosoft_gateway"


class RedLionGatewayDriver(_NamedHTTP):
    name = "redlion_gateway"


class EwonFlexyDriver(_NamedHTTP):
    name = "ewon_flexy"


class DigiWebDriver(_NamedHTTP):
    name = "digi_web"


class LantronixWebDriver(_NamedHTTP):
    name = "lantronix_web"


class AdvantechEKISerialDriver(_NamedHTTP):
    name = "advantech_eki_serial"


class GenericGatewayDriver(_NamedHTTP):
    name = "generic_gateway"
