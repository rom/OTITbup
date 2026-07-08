"""Offsite copies: an encrypted, self-contained snapshot of the whole
backup (git repo + blob store + run store) shipped to an external server or
cloud object store, and pulled back for a restore.

This is the 3-2-1 "1 offsite" leg, hardened: the snapshot is a single
tarball encrypted at rest with Fernet BEFORE it leaves the appliance, so
the remote (an SFTP box, an S3 bucket, a mounted share) only ever holds
ciphertext and never the encryption key. Losing the remote credentials
does not expose any configuration; only the appliance-held offsite key can
decrypt a snapshot.

    offsite:
      key_file: /etc/otitbup/offsite.key   # Fernet key (keep OFF the remote)
      transport: s3                         # file | sftp | s3
      # --- file (a local dir, NFS/SMB mount, or removable media):
      dir: /mnt/offsite/otitbup
      # --- sftp (needs otitbup[sftp] / paramiko):
      host: backup.example.com
      port: 22
      username: otitbup
      ssh_key_file: /etc/otitbup/id_ed25519  # or password:
      path: /srv/otitbup
      # --- s3 (AWS S3 or any S3-compatible: MinIO, Backblaze B2, Wasabi):
      bucket: ot-backups
      prefix: otitbup/
      region: eu-central-1
      endpoint: https://s3.eu-central-1.amazonaws.com
      access_key: AKIA...                    # or OTITBUP_OFFSITE_S3_KEY
      secret_key: ...                        # or OTITBUP_OFFSITE_S3_SECRET

`otitbup offsite push` uploads a snapshot; `offsite list` shows what's
there; `offsite pull` fetches+decrypts+extracts one; `offsite restore`
pulls and produces a hash-verified restore bundle for a device — all
without any device writes.
"""
from __future__ import annotations

import logging
import os
import tarfile
import tempfile
from pathlib import Path

log = logging.getLogger("otitbup.offsite")

_SNAPSHOT_RE = r"otitbup-\d{8}-\d{6}\.tar\.gz\.enc"


class OffsiteError(Exception):
    pass


# ----------------------------------------------------------- encryption

def _load_key(cfg: dict) -> bytes:
    key = os.environ.get("OTITBUP_OFFSITE_KEY") or cfg.get("key")
    if not key and cfg.get("key_file"):
        key = Path(cfg["key_file"]).read_text().strip()
    if not key:
        raise OffsiteError(
            "no offsite key configured (offsite.key_file / offsite.key / "
            "OTITBUP_OFFSITE_KEY). Generate one with `otitbup offsite genkey`."
        )
    return key.encode() if isinstance(key, str) else key


def _fernet(cfg: dict):
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:                       # pragma: no cover
        raise OffsiteError(
            "offsite encryption needs the 'cryptography' package "
            "(pip install otitbup[crypto])"
        ) from exc
    return Fernet(_load_key(cfg))


def generate_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


# -------------------------------------------------------- snapshot build

def _archive_members(config) -> list[tuple[Path, str]]:
    base = Path(config.data_dir).parent
    return [
        (Path(config.data_dir), "data"),
        (base / "blobs", "blobs"),
        (base / "runstore.db", "runstore.db"),
    ]


def build_snapshot(config, dest: Path) -> Path:
    """Write a plaintext tar.gz of repo+blobs+runstore to `dest`."""
    with tarfile.open(dest, "w:gz") as tar:
        for path, arcname in _archive_members(config):
            if path.exists():
                tar.add(path, arcname=arcname)
    return dest


def snapshot_name(stamp: str) -> str:
    """Remote object name for a snapshot taken at `stamp` (YYYYmmdd-HHMMSS,
    passed in by the caller since scripts can't read the clock)."""
    return f"otitbup-{stamp}.tar.gz.enc"


# ------------------------------------------------------------ transports

class Transport:
    def put(self, local: Path, name: str) -> None: ...
    def get(self, name: str, local: Path) -> None: ...
    def list(self) -> list[str]: ...


class FileTransport(Transport):
    """A directory: local disk, an NFS/SMB mount, or removable media."""

    def __init__(self, cfg: dict):
        d = cfg.get("dir")
        if not d:
            raise OffsiteError("offsite.transport=file needs offsite.dir")
        self.dir = Path(d)

    def put(self, local: Path, name: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / (name + ".part")
        tmp.write_bytes(local.read_bytes())
        os.replace(tmp, self.dir / name)

    def get(self, name: str, local: Path) -> None:
        src = self.dir / name
        if not src.exists():
            raise OffsiteError(f"snapshot not found: {name}")
        local.write_bytes(src.read_bytes())

    def list(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        import re
        return sorted(
            p.name for p in self.dir.iterdir()
            if re.fullmatch(_SNAPSHOT_RE, p.name)
        )


class SFTPTransport(Transport):
    """An SSH/SFTP server (needs paramiko: pip install otitbup[sftp])."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.remote = cfg.get("path", ".").rstrip("/")

    def _client(self):
        try:
            import paramiko
        except ImportError as exc:                   # pragma: no cover
            raise OffsiteError(
                "offsite.transport=sftp needs paramiko "
                "(pip install otitbup[sftp])"
            ) from exc
        cfg = self.cfg
        transport = paramiko.Transport(
            (cfg["host"], int(cfg.get("port", 22))))
        pkey = None
        if cfg.get("ssh_key_file"):
            pkey = paramiko.Ed25519Key.from_private_key_file(
                cfg["ssh_key_file"]) if "ed25519" in str(
                cfg["ssh_key_file"]).lower() else \
                paramiko.RSAKey.from_private_key_file(cfg["ssh_key_file"])
        transport.connect(
            username=cfg.get("username"),
            password=cfg.get("password"), pkey=pkey)
        return transport, paramiko.SFTPClient.from_transport(transport)

    def put(self, local: Path, name: str) -> None:
        transport, sftp = self._client()
        try:
            self._mkdirs(sftp)
            sftp.put(str(local), f"{self.remote}/{name}")
        finally:
            sftp.close()
            transport.close()

    def get(self, name: str, local: Path) -> None:
        transport, sftp = self._client()
        try:
            sftp.get(f"{self.remote}/{name}", str(local))
        finally:
            sftp.close()
            transport.close()

    def list(self) -> list[str]:
        import re
        transport, sftp = self._client()
        try:
            names = sftp.listdir(self.remote or ".")
        except OSError:
            names = []
        finally:
            sftp.close()
            transport.close()
        return sorted(n for n in names if re.fullmatch(_SNAPSHOT_RE, n))

    def _mkdirs(self, sftp) -> None:
        parts = self.remote.strip("/").split("/") if self.remote else []
        path = "/" if self.remote.startswith("/") else ""
        for part in parts:
            path = f"{path}/{part}" if path not in ("", "/") else \
                (f"/{part}" if path == "/" else part)
            try:
                sftp.stat(path)
            except OSError:
                sftp.mkdir(path)


class S3Transport(Transport):
    """Any S3-compatible object store, signed with SigV4 over stdlib urllib
    (no boto3). Works with AWS S3, MinIO, Backblaze B2, Wasabi, Ceph RGW."""

    def __init__(self, cfg: dict):
        self.bucket = cfg.get("bucket")
        if not self.bucket:
            raise OffsiteError("offsite.transport=s3 needs offsite.bucket")
        self.prefix = cfg.get("prefix", "").lstrip("/")
        self.region = cfg.get("region", "us-east-1")
        self.endpoint = cfg.get(
            "endpoint", f"https://s3.{self.region}.amazonaws.com"
        ).rstrip("/")
        self.access_key = (os.environ.get("OTITBUP_OFFSITE_S3_KEY")
                           or cfg.get("access_key"))
        self.secret_key = (os.environ.get("OTITBUP_OFFSITE_S3_SECRET")
                           or cfg.get("secret_key"))
        if cfg.get("secret_key_file") and not self.secret_key:
            self.secret_key = Path(cfg["secret_key_file"]).read_text().strip()
        if not self.access_key or not self.secret_key:
            raise OffsiteError("offsite s3 needs access_key and secret_key")
        # Path-style addressing works for both AWS and self-hosted stores.
        self.verify_tls = cfg.get("verify_tls", True)

    def _key(self, name: str) -> str:
        return f"{self.prefix}{name}" if self.prefix else name

    def put(self, local: Path, name: str) -> None:
        self._request("PUT", self._key(name), body=local.read_bytes())

    def get(self, name: str, local: Path) -> None:
        data = self._request("GET", self._key(name))
        local.write_bytes(data)

    def list(self) -> list[str]:
        import re
        import xml.etree.ElementTree as ET
        query = {"list-type": "2"}
        if self.prefix:
            query["prefix"] = self.prefix
        body = self._request("GET", "", query=query)
        # Strip the default namespace so findall is simple.
        text = body.decode()
        text = re.sub(r'\sxmlns="[^"]+"', "", text, count=1)
        root = ET.fromstring(text)
        names = []
        for key in root.findall(".//Contents/Key"):
            base = key.text.rsplit("/", 1)[-1]
            if re.fullmatch(_SNAPSHOT_RE, base):
                names.append(base)
        return sorted(names)

    # -- SigV4 ---------------------------------------------------------
    def _request(self, method: str, key: str, body: bytes = b"",
                 query: dict | None = None) -> bytes:
        import datetime
        import hashlib
        import hmac
        import ssl
        import urllib.error
        import urllib.request
        from urllib.parse import quote, urlencode

        # A fixed timestamp cannot be used (SigV4 rejects skew), so this is
        # the one place we read the clock; callers pass no time in.
        now = datetime.datetime.now(datetime.UTC)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")

        host = self.endpoint.split("://", 1)[1]
        canonical_uri = "/" + self.bucket + (
            "/" + quote(key, safe="/") if key else ""
        )
        canonical_qs = urlencode(sorted((query or {}).items()))
        payload_hash = hashlib.sha256(body).hexdigest()
        canonical_headers = (
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amzdate}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = (
            f"{method}\n{canonical_uri}\n{canonical_qs}\n"
            f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )
        scope = f"{datestamp}/{self.region}/s3/aws4_request"
        string_to_sign = (
            "AWS4-HMAC-SHA256\n"
            f"{amzdate}\n{scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def _sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = _sign(("AWS4" + self.secret_key).encode(), datestamp)
        k_region = _sign(k_date, self.region)
        k_service = _sign(k_region, "s3")
        k_signing = _sign(k_service, "aws4_request")
        signature = hmac.new(
            k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
        authorization = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        url = f"{self.endpoint}{canonical_uri}"
        if canonical_qs:
            url += "?" + canonical_qs
        req = urllib.request.Request(url, data=body or None, method=method)
        req.add_header("Host", host)
        req.add_header("x-amz-content-sha256", payload_hash)
        req.add_header("x-amz-date", amzdate)
        req.add_header("Authorization", authorization)
        ctx = None
        if self.verify_tls is False:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise OffsiteError(
                f"s3 {method} {key or '(list)'} failed: "
                f"{exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise OffsiteError(f"s3 {method} failed: {exc.reason}") from exc


def make_transport(cfg: dict) -> Transport:
    kind = cfg.get("transport", "file")
    if kind == "file":
        return FileTransport(cfg)
    if kind == "sftp":
        return SFTPTransport(cfg)
    if kind == "s3":
        return S3Transport(cfg)
    raise OffsiteError(f"unknown offsite transport: {kind!r}")


# ------------------------------------------------------------- top-level

def push(config, stamp: str) -> str:
    """Build, encrypt and upload a snapshot. Returns the remote name.
    `stamp` (YYYYmmdd-HHMMSS) is supplied by the caller."""
    cfg = config.offsite or {}
    fernet = _fernet(cfg)
    transport = make_transport(cfg)
    name = snapshot_name(stamp)
    with tempfile.TemporaryDirectory() as tmp:
        plain = build_snapshot(config, Path(tmp) / "snap.tar.gz")
        enc = Path(tmp) / name
        enc.write_bytes(fernet.encrypt(plain.read_bytes()))
        size = enc.stat().st_size
        transport.put(enc, name)
    log.info("offsite snapshot uploaded: %s (%d bytes ciphertext)", name, size)
    return name


def list_snapshots(config) -> list[str]:
    return make_transport(config.offsite or {}).list()


def pull(config, name: str | None, out_dir: Path) -> tuple[str, Path]:
    """Fetch a snapshot (default: newest), decrypt, and extract it into
    `out_dir`. Returns (name, extracted_dir)."""
    cfg = config.offsite or {}
    fernet = _fernet(cfg)
    transport = make_transport(cfg)
    if name is None:
        names = transport.list()
        if not names:
            raise OffsiteError("no offsite snapshots found")
        name = names[-1]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        enc = Path(tmp) / name
        transport.get(name, enc)
        try:
            plain = fernet.decrypt(enc.read_bytes())
        except Exception as exc:
            raise OffsiteError(
                f"decrypt failed for {name} (wrong offsite key?)") from exc
        tar_path = Path(tmp) / "snap.tar.gz"
        tar_path.write_bytes(plain)
        with tarfile.open(tar_path, "r:gz") as tar:
            _safe_extractall(tar, out_dir)
    log.info("offsite snapshot %s extracted to %s", name, out_dir)
    return name, out_dir


def _safe_extractall(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract, refusing any member that would escape `dest` (path
    traversal / absolute paths) — the tarball came off an external store."""
    dest = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest)):
            raise OffsiteError(f"unsafe path in snapshot: {member.name}")
    tar.extractall(dest)  # noqa: S202 - members validated above


def stores_from_snapshot(extracted: Path, blob_key: str | bytes | None = None):
    """Open (GitStore, BlobStore) over an extracted snapshot so restore
    bundles can be produced from the offsite copy. If the source appliance
    encrypted its blob store at rest, pass its blob key so offloaded
    artifacts decrypt during the restore."""
    from .blobstore import BlobStore
    from .gitstore import GitStore
    return (GitStore(extracted / "data"),
            BlobStore(extracted / "blobs", key=blob_key))


def restore_from_offsite(config, device, out_dir: Path, name: str | None = None,
                         commit: str | None = None, blob_key=None):
    """Pull an offsite snapshot and export a hash-verified restore bundle
    for `device` from it — no device writes. Returns (snapshot_name, commit,
    hash_mismatches, bundle_dir)."""
    from .restore import export_bundle
    with tempfile.TemporaryDirectory() as tmp:
        snap_name, extracted = pull(config, name, Path(tmp) / "snap")
        store, blobstore = stores_from_snapshot(extracted, blob_key=blob_key)
        commit, mismatches = export_bundle(
            store, device, out_dir, commit=commit, blobstore=blobstore)
    return snap_name, commit, mismatches, Path(out_dir)
