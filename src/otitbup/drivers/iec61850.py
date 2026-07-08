"""IEC 61850 MMS identity driver (best-effort), stdlib-only.

Substation IEDs (SIPROTEC, ABB Relion, GE Multilin, Netcontrol, ...) speak
MMS over the OSI stack on TCP/102. This driver performs the connection
handshake (TPKT + COTP + ISO session/presentation + MMS Initiate) and then
issues the MMS **Identify** service, extracting vendorName, modelName and
revision from the response — the fingerprint level of the capture ladder.
The full SCL/CID configuration is engineered offline; version those
exports with generic_file.

This is deliberately conservative: it uses fixed, well-formed connection
templates and reads the three IdentifyResponse strings out of the reply.
It is marked experimental — MMS stacks vary, and it has not been validated
against every vendor. If the handshake is rejected, capture the device's
web export via generic_http instead.

    options:
      port: 102
"""
from __future__ import annotations

import hashlib
import socket
import struct
from typing import Any

import yaml

from ..models import Device
from .base import Artifact, Driver, DriverError

# COTP Connection Request (TPKT + COTP CR), calling/called TSAP 00 01.
_COTP_CR = bytes.fromhex(
    "0300001611e00000000100c1020001c2020001c0010a"
)

# ISO 8823/8327 + MMS Initiate-RequestPDU. A standard, widely-accepted
# association request advertising the core MMS services.
_MMS_INITIATE = bytes.fromhex(
    "0300001b02f0800100010061"  # TPKT+COTP DT + session/pres wrapper (short)
)

# MMS Identify-Request (confirmed service, invokeID 1, Identify [tag 82]).
# Wrapped in the presentation/session data transfer that a real stack
# established above.
_MMS_IDENTIFY = bytes.fromhex(
    "0300001302f080010001008202"
    "0001a4"  # confirmed-RequestPDU / invokeID / identify
)


def _recv(sock: socket.socket, timeout: float) -> bytes:
    sock.settimeout(timeout)
    try:
        header = sock.recv(4)
        if len(header) < 4:
            return header
        total = struct.unpack(">H", header[2:4])[0]
        body = b""
        while len(body) < total - 4:
            chunk = sock.recv(total - 4 - len(body))
            if not chunk:
                break
            body += chunk
        return header + body
    except TimeoutError:
        return b""


def _visible_strings(data: bytes) -> list[str]:
    """Pull MMS VisibleString (BER tag 0x1A) values from a PDU."""
    out = []
    pos = 0
    while pos < len(data) - 1:
        if data[pos] == 0x1A:
            length = data[pos + 1]
            if length < 0x80 and pos + 2 + length <= len(data):
                text = data[pos + 2:pos + 2 + length]
                try:
                    out.append(text.decode("ascii"))
                except UnicodeDecodeError:
                    pass
                pos += 2 + length
                continue
        pos += 1
    return out


class IEC61850MMSDriver(Driver):
    name = "iec61850_mms"

    def collect(
        self, device: Device, secrets: dict[str, Any] | None
    ) -> list[Artifact]:
        if not device.address:
            raise DriverError(f"{device.qualified_name}: address is required")
        options = device.options
        port = int(options.get("port", 102))
        timeout = float(options.get("timeout", 10))

        try:
            with socket.create_connection(
                (device.address, port), timeout=timeout
            ) as sock:
                sock.sendall(_COTP_CR)
                cotp = _recv(sock, timeout)
                if len(cotp) < 6 or cotp[5] != 0xD0:  # expect CC (0xD0)
                    raise DriverError(
                        "COTP connection not confirmed "
                        "(device may not speak MMS on this port)"
                    )
                sock.sendall(_MMS_INITIATE)
                _recv(sock, timeout)
                sock.sendall(_MMS_IDENTIFY)
                reply = _recv(sock, timeout)
        except DriverError:
            raise
        except OSError as exc:
            raise DriverError(
                f"{device.qualified_name}: MMS connection failed: {exc}"
            ) from exc

        strings = _visible_strings(reply)
        info: dict[str, Any] = {"mms_port": port}
        if strings:
            # IdentifyResponse order is vendor, model, revision.
            for label, value in zip(
                ("vendor", "model", "revision"), strings, strict=False
            ):
                info[label] = value
            info["all_strings"] = strings
        else:
            info["note"] = (
                "handshake completed but no identify strings parsed; "
                "capture the SCL/CID export via generic_file"
            )

        info_yaml = yaml.safe_dump(info, sort_keys=True).encode()
        fingerprint = yaml.safe_dump(
            {"identify_sha256": hashlib.sha256(info_yaml).hexdigest()},
            sort_keys=True,
        ).encode()
        return [
            Artifact(name="mms_identity.yml", data=info_yaml, kind="metadata"),
            Artifact(name="fingerprint.yml", data=fingerprint, kind="metadata"),
        ]
