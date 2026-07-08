import textwrap
import zipfile

import pytest

from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.reportfmt import render, to_csv, to_docx, to_pdf
from otitbup.runstore import RunRecord, RunStore

pytest.importorskip("cryptography")

from otitbup.signing import sign_file, verify_file  # noqa: E402


@pytest.fixture
def env(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: s
            zones:
              - name: net
                devices:
                  - {name: sw-01, driver: cisco_ios}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    store.write_and_commit(config.all_devices()[0], [Artifact(
        name="show_running_config.txt", data=b"hostname sw-01\nip http server\n")])
    runstore = RunStore(tmp_path / "r.db")
    import time
    runstore.record_run(RunRecord("s/net/sw-01", time.time() - 60,
                                  time.time() - 59, True, True, "abc", "ok"))
    return config, store, runstore, tmp_path


def test_csv_report(env):
    config, store, runstore, _ = env
    data = to_csv(config, store, runstore)
    text = data.decode()
    assert "s/net/sw-01" in text
    assert "no-http-server" in text        # policy finding row


def test_pdf_report_is_valid(env):
    config, store, runstore, _ = env
    data = to_pdf(config, store, runstore)
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert b"/Catalog" in data and b"xref" in data


def test_docx_report_is_valid_zip(env):
    config, store, runstore, tmp_path = env
    data = to_docx(config, store, runstore)
    out = tmp_path / "r.docx"
    out.write_bytes(data)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert "[Content_Types].xml" in names
        assert "word/document.xml" in names
        assert b"s/net/sw-01" in z.read("word/document.xml")


def test_render_dispatch(env):
    config, store, runstore, _ = env
    for fmt, magic in (("csv", b"device"), ("pdf", b"%PDF"),
                       ("docx", b"PK")):
        data, ct, ext = render(fmt, config, store, runstore)
        assert data.startswith(magic) or magic in data[:16]
        assert ext == fmt


def test_sign_and_verify_roundtrip(env, tmp_path):
    config, store, runstore, _ = env
    report = tmp_path / "r.pdf"
    report.write_bytes(to_pdf(config, store, runstore))
    key = tmp_path / "signing.key"
    sig = sign_file(report, key)
    assert sig.exists()
    assert (tmp_path / "r.pdf.pubkey").exists()
    assert key.stat().st_mode & 0o777 == 0o600
    assert verify_file(report, sig, tmp_path / "r.pdf.pubkey")

    # Tamper -> verification fails.
    report.write_bytes(report.read_bytes() + b"x")
    assert not verify_file(report, sig, tmp_path / "r.pdf.pubkey")
