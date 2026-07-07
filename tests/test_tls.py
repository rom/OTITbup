"""TLS for the web UI: certgen + an HTTPS server round-trip."""
import ssl
import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from otitbup.config import load_config
from otitbup.gitstore import GitStore
from otitbup.webui import WebUI, _Handler

pytest.importorskip("cryptography")

from otitbup.tlscert import generate_self_signed  # noqa: E402


def test_certgen_writes_protected_key(tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    generate_self_signed(cert, key, hostnames=["backup.plant.local"],
                         ips=["10.0.0.5"])
    assert cert.read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
    assert key.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
    assert (key.stat().st_mode & 0o777) == 0o600


def test_webui_serves_https(config_file, tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    generate_self_signed(cert, key, hostnames=["localhost"])

    config = load_config(config_file)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    ui = WebUI(config, store)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, ui))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        client_ctx = ssl.create_default_context(cafile=str(cert))
        client_ctx.check_hostname = False
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=client_ctx),
        )
        url = f"https://127.0.0.1:{httpd.server_address[1]}/"
        with opener.open(url, timeout=5) as response:
            assert response.status == 200
            assert b"otitbup" in response.read()
    finally:
        httpd.shutdown()
