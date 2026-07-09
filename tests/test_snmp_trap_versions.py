"""SNMP trap encoding in all three versions (v1, v2c, v3/USM)."""
import hmac as hmac_mod

import pytest

from otitbup.events import (
    Event,
    build_snmpv1_trap,
    build_snmpv2_trap,
    build_snmpv3_trap,
    build_trap,
    usm_localized_key,
)

EVENT = Event(type="backup.error", message="plc-01 failed", severity="error")
OID = "1.3.6.1.4.1.99999"


def _tlv_children(data: bytes):
    """Walk one level of BER TLVs -> [(tag, body)]."""
    out, pos = [], 0
    while pos < len(data):
        tag = data[pos]
        first = data[pos + 1]
        if first < 0x80:
            length, body_at = first, pos + 2
        else:
            n = first & 0x7F
            length = int.from_bytes(data[pos + 2:pos + 2 + n], "big")
            body_at = pos + 2 + n
        out.append((tag, data[body_at:body_at + length]))
        pos = body_at + length
    return out


def _message_parts(datagram: bytes):
    tag, body = _tlv_children(datagram)[0]
    assert tag == 0x30
    return _tlv_children(body)


def test_v1_trap_structure():
    dgram = build_snmpv1_trap("public", OID, EVENT, uptime_ticks=1234,
                              agent_addr="192.0.2.1")
    parts = _message_parts(dgram)
    assert parts[0] == (0x02, b"\x00")            # version 0 = SNMPv1
    assert parts[1] == (0x04, b"public")
    tag, pdu = parts[2]
    assert tag == 0xA4                            # Trap-PDU [4]
    fields = _tlv_children(pdu)
    assert fields[0][0] == 0x06                   # enterprise OID
    assert fields[1] == (0x40, bytes([192, 0, 2, 1]))   # agent-addr
    assert fields[2] == (0x02, b"\x06")           # generic: enterpriseSpecific
    assert fields[3][0] == 0x02                   # specific-trap = event id
    assert int.from_bytes(fields[3][1], "big") == 7     # backup.error
    assert fields[4][0] == 0x43                   # TimeTicks
    assert b"backup.error" in pdu


def test_v2c_trap_structure():
    dgram = build_snmpv2_trap("public", OID, EVENT, request_id=9,
                              uptime_ticks=100)
    parts = _message_parts(dgram)
    assert parts[0] == (0x02, b"\x01")            # version 1 = v2c
    assert parts[2][0] == 0xA7                    # SNMPv2-Trap-PDU
    assert b"backup.error" in dgram


def test_v3_noauth_trap_structure():
    dgram = build_snmpv3_trap(
        {"engine_id": "8000270b0102", "username": "otitbup",
         "auth_protocol": "none"},
        OID, EVENT, request_id=42, uptime_ticks=500)
    parts = _message_parts(dgram)
    assert parts[0] == (0x02, b"\x03")            # version 3
    header = _tlv_children(parts[1][1])
    assert header[2] == (0x04, b"\x00")           # flags: noAuthNoPriv
    assert header[3] == (0x02, b"\x03")           # security model: USM
    assert b"otitbup" in parts[2][1]              # USM user name
    assert b"backup.error" in dgram               # scoped PDU in clear


@pytest.mark.parametrize("proto,maclen", [("sha1", 12), ("sha256", 24),
                                          ("md5", 12)])
def test_v3_auth_trap_has_valid_hmac(proto, maclen):
    v3 = {"engine_id": "8000270b0102", "username": "u",
          "auth_protocol": proto, "auth_key": "maplesyrup-password"}
    dgram = build_snmpv3_trap(v3, OID, EVENT, request_id=7, uptime_ticks=1)
    parts = _message_parts(dgram)
    flags = _tlv_children(parts[1][1])[2][1]
    assert flags == b"\x01"                       # authNoPriv
    usm = _tlv_children(_tlv_children(parts[2][1])[0][1])
    mac = usm[4][1]
    assert len(mac) == maclen and any(mac)
    # Recompute: HMAC over the message with the auth field zeroed.
    hash_name = {"sha1": "sha1", "sha256": "sha256", "md5": "md5"}[proto]
    key = usm_localized_key("maplesyrup-password",
                            bytes.fromhex("8000270b0102"), hash_name)
    zeroed = dgram.replace(mac, b"\x00" * maclen)
    expect = hmac_mod.new(key, zeroed, hash_name).digest()[:maclen]
    assert expect == mac


def test_v3_priv_trap_encrypts_scoped_pdu():
    pytest.importorskip("cryptography")
    v3 = {"engine_id": "8000270b0102", "username": "u",
          "auth_protocol": "sha256", "auth_key": "auth-pass-123",
          "priv_protocol": "aes128", "priv_key": "priv-pass-123"}
    dgram = build_snmpv3_trap(v3, OID, EVENT, request_id=7, uptime_ticks=999)
    parts = _message_parts(dgram)
    flags = _tlv_children(parts[1][1])[2][1]
    assert flags == b"\x03"                       # authPriv
    assert b"backup.error" not in dgram           # payload is encrypted

    # Decrypt with the localized key and the IV from the message.
    import struct

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    try:
        from cryptography.hazmat.decrepit.ciphers.modes import CFB
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.modes import CFB
    usm = _tlv_children(_tlv_children(parts[2][1])[0][1])
    boots = int.from_bytes(usm[1][1], "big")
    etime = int.from_bytes(usm[2][1], "big")
    salt = usm[5][1]
    key = usm_localized_key("priv-pass-123", bytes.fromhex("8000270b0102"),
                            "sha256")[:16]
    iv = struct.pack(">II", boots, etime) + salt
    dec = Cipher(algorithms.AES(key), CFB(iv)).decryptor()
    plain = dec.update(parts[3][1]) + dec.finalize()
    assert b"backup.error" in plain


def test_v3_priv_salt_is_unique_per_trap():
    pytest.importorskip("cryptography")
    v3 = {"engine_id": "8000270b0102", "username": "u",
          "auth_protocol": "sha256", "auth_key": "auth-pass-123",
          "priv_protocol": "aes128", "priv_key": "priv-pass-123"}
    salts = set()
    # Same request_id/uptime every time (as after a process restart): the
    # privacy salt must still differ so the AES-CFB IV never repeats.
    for _ in range(50):
        dgram = build_snmpv3_trap(v3, OID, EVENT, request_id=1, uptime_ticks=1)
        parts = _message_parts(dgram)
        usm = _tlv_children(_tlv_children(parts[2][1])[0][1])
        salts.add(usm[5][1])
    assert len(salts) == 50


def test_v3_priv_without_auth_rejected():
    with pytest.raises(ValueError):
        build_snmpv3_trap(
            {"engine_id": "80", "auth_protocol": "none",
             "priv_protocol": "aes128", "priv_key": "x"},
            OID, EVENT, request_id=1, uptime_ticks=1)


def test_build_trap_dispatches_on_version(tmp_path):
    v1 = build_trap({"version": "v1"}, EVENT, 1, 1)
    v2 = build_trap({}, EVENT, 1, 1)              # default v2c
    keyfile = tmp_path / "auth.key"
    keyfile.write_text("file-passphrase\n")
    v3 = build_trap(
        {"version": "v3",
         "v3": {"engine_id": "8000270b0102", "username": "u",
                "auth_protocol": "sha1", "auth_key_file": str(keyfile)}},
        EVENT, 1, 1)
    assert _message_parts(v1)[0] == (0x02, b"\x00")
    assert _message_parts(v2)[0] == (0x02, b"\x01")
    assert _message_parts(v3)[0] == (0x02, b"\x03")
    with pytest.raises(ValueError):
        build_trap({"version": "v9"}, EVENT, 1, 1)
