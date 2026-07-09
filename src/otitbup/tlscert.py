"""Self-signed TLS certificate generation for the web UI.

OT appliances rarely sit behind a PKI, so `otitbup certgen` produces a
self-signed EC P-256 pair the web UI can serve immediately. Operators
with an internal CA should use properly issued certificates instead —
the webui.tls config takes any PEM cert/key pair.
"""
from __future__ import annotations

import datetime
import ipaddress
from pathlib import Path


class TLSCertError(Exception):
    pass


def generate_self_signed(
    cert_path: str | Path,
    key_path: str | Path,
    hostnames: list[str] | None = None,
    ips: list[str] | None = None,
    days: int = 3650,
) -> None:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise TLSCertError(
            "certificate generation requires the 'cryptography' package "
            "(pip install 'otitbup[crypto]')"
        ) from exc

    hostnames = hostnames or ["localhost"]
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0])]
    )
    san: list = [x509.DNSName(name) for name in hostnames]
    for ip in ips or []:
        san.append(x509.IPAddress(ipaddress.ip_address(ip)))
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .sign(key, hashes.SHA256())
    )

    key_path = Path(key_path)
    key_path.touch(mode=0o600)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    Path(cert_path).write_bytes(
        certificate.public_bytes(serialization.Encoding.PEM)
    )
