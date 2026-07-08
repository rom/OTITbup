"""Minimal SNMPv2c client (GET), stdlib-only.

Enough to fingerprint a device from the standard system group — no pysnmp
dependency. Used by the snmp_fingerprint driver and by discovery
enrichment. Includes a small BER decoder for the SNMP application types.
"""
from __future__ import annotations

import socket
import struct

# Well-known system-group OIDs.
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

SYSTEM_GROUP = {
    SYS_DESCR: "sysDescr",
    SYS_OBJECT_ID: "sysObjectID",
    SYS_UPTIME: "sysUpTime",
    SYS_CONTACT: "sysContact",
    SYS_NAME: "sysName",
    SYS_LOCATION: "sysLocation",
}


class SNMPError(Exception):
    pass


# ------------------------------------------------------------- BER encode

def _ber_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _ber_len(len(value)) + value


def _enc_int(n: int) -> bytes:
    if n == 0:
        return _tlv(0x02, b"\x00")
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    if body[0] & 0x80:
        body = b"\x00" + body
    return _tlv(0x02, body)


def _enc_oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.split(".")]
    encoded = [40 * parts[0] + parts[1]] + parts[2:]
    body = b""
    for value in encoded:
        if value < 0x80:
            body += bytes([value])
            continue
        chunk = [value & 0x7F]
        value >>= 7
        while value:
            chunk.append((value & 0x7F) | 0x80)
            value >>= 7
        body += bytes(reversed(chunk))
    return _tlv(0x06, body)


# ------------------------------------------------------------- BER decode

def _read_len(data: bytes, pos: int) -> tuple[int, int]:
    first = data[pos]
    pos += 1
    if first < 0x80:
        return first, pos
    count = first & 0x7F
    length = int.from_bytes(data[pos:pos + count], "big")
    return length, pos + count


def _decode_oid(body: bytes) -> str:
    if not body:
        return ""
    first = body[0]
    arcs = [first // 40, first % 40]
    value = 0
    for byte in body[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            arcs.append(value)
            value = 0
    return ".".join(str(a) for a in arcs)


def _decode_value(tag: int, body: bytes):
    if tag == 0x02 or tag in (0x41, 0x42, 0x43, 0x46):  # INT/Counter/Gauge/Ticks
        return int.from_bytes(body, "big") if body else 0
    if tag == 0x04:                                       # OCTET STRING
        return body.decode(errors="replace")
    if tag == 0x06:                                       # OID
        return _decode_oid(body)
    if tag == 0x40:                                       # IpAddress
        return ".".join(str(b) for b in body)
    if tag == 0x05:                                       # NULL
        return None
    return body.hex()


def _parse_varbinds(data: bytes) -> dict[str, object]:
    """Walk the response for the varbind list and return {oid: value}."""
    # Response: SEQ { version, community, GetResponse[0xA2] { rid, es, ei,
    # SEQ { VarBind SEQ { OID, value } ... } } }
    def children(seq: bytes):
        pos = 0
        while pos < len(seq):
            tag = seq[pos]
            length, p = _read_len(seq, pos + 1)
            yield tag, seq[p:p + length]
            pos = p + length

    outer_tag = data[0]
    length, pos = _read_len(data, 1)
    body = data[pos:pos + length]
    if outer_tag != 0x30:
        raise SNMPError("not an SNMP message")
    parts = list(children(body))
    pdu = next((v for t, v in parts if t in (0xA2, 0xA0, 0xA5)), None)
    if pdu is None:
        raise SNMPError("no PDU in response")
    pdu_parts = list(children(pdu))
    if len(pdu_parts) >= 2 and pdu_parts[1][0] == 0x02:
        error_status = int.from_bytes(pdu_parts[1][1], "big")
        if error_status != 0:
            raise SNMPError(f"SNMP error-status {error_status}")
    varbind_list = next((v for t, v in pdu_parts if t == 0x30), b"")
    result: dict[str, object] = {}
    for _t, vb in children(varbind_list):
        items = list(children(vb))
        if len(items) >= 2 and items[0][0] == 0x06:
            oid = _decode_oid(items[0][1])
            result[oid] = _decode_value(items[1][0], items[1][1])
    return result


def build_get(community: str, oids: list[str], request_id: int) -> bytes:
    varbinds = b"".join(
        _tlv(0x30, _enc_oid(oid) + _tlv(0x05, b"")) for oid in oids
    )
    pdu = _tlv(
        0xA0,
        _enc_int(request_id) + _enc_int(0) + _enc_int(0)
        + _tlv(0x30, varbinds),
    )
    return _tlv(0x30, _enc_int(1) + _tlv(0x04, community.encode()) + pdu)


def snmp_get(
    host: str, oids: list[str], community: str = "public",
    port: int = 161, timeout: float = 3.0,
) -> dict[str, object]:
    request = build_get(community, oids, request_id=1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(request, (host, port))
        data, _ = sock.recvfrom(65535)
    except OSError as exc:
        raise SNMPError(f"SNMP request to {host}:{port} failed: {exc}") from exc
    finally:
        sock.close()
    return _parse_varbinds(data)
