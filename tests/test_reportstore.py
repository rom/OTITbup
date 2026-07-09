from types import SimpleNamespace

import pytest

from otitbup import reportstore


def _config(tmp_path):
    # reportstore only reads config.data_dir; reports live next to it.
    data = tmp_path / "data"
    data.mkdir()
    return SimpleNamespace(data_dir=str(data))


def test_store_and_list_newest_first(tmp_path):
    config = _config(tmp_path)
    import datetime
    t1 = datetime.datetime(2026, 1, 1, 10, 0, 0, tzinfo=datetime.UTC)
    t2 = datetime.datetime(2026, 1, 2, 11, 30, 0, tzinfo=datetime.UTC)
    p1 = reportstore.store_report(config, "html", b"<h1>one</h1>", when=t1)
    p2 = reportstore.store_report(config, "csv", b"a,b\n", when=t2)

    assert p1.parent == reportstore.reports_dir(config)
    assert p1.name == "compliance-20260101-100000.html"
    assert p2.name == "compliance-20260102-113000.csv"

    files = reportstore.list_reports(config)
    assert [f.name for f in files] == [p2.name, p1.name]  # newest first
    assert files[0].fmt == "csv" and files[1].fmt == "html"
    assert files[0].size == len(b"a,b\n")
    assert all(not f.signed for f in files)


def test_signatures_excluded_but_flag_reports_signed(tmp_path):
    config = _config(tmp_path)
    path = reportstore.store_report(config, "html", b"x")
    (path.parent / (path.name + ".sig")).write_bytes(b"sig")
    (path.parent / (path.name + ".pubkey")).write_bytes(b"key")

    files = reportstore.list_reports(config)
    assert len(files) == 1                      # .sig/.pubkey not listed
    assert files[0].signed is True


def test_report_path_traversal_guarded(tmp_path):
    config = _config(tmp_path)
    reportstore.store_report(config, "html", b"ok")
    good = reportstore.list_reports(config)[0].name
    assert reportstore.report_path(config, good) is not None

    # Path traversal / unexpected names are rejected.
    assert reportstore.report_path(config, "../secrets.yml") is None
    assert reportstore.report_path(config, "compliance-1-2.html") is None
    assert reportstore.report_path(config, "evil.html") is None
    assert reportstore.report_path(config, good + ".sig") is None
    # A well-formed but non-existent name resolves to None too.
    assert reportstore.report_path(
        config, "compliance-20200101-000000.html") is None


@pytest.mark.parametrize("name,ctype", [
    ("compliance-20260101-100000.html", "text/html; charset=utf-8"),
    ("compliance-20260101-100000.csv", "text/csv; charset=utf-8"),
    ("compliance-20260101-100000.pdf", "application/pdf"),
    ("compliance-20260101-100000.html.sig", "application/octet-stream"),
])
def test_content_type(name, ctype):
    assert reportstore.content_type(name) == ctype
