import textwrap
import time
import xml.dom.minidom as minidom

import pytest

from otitbup import charts
from otitbup.config import load_config
from otitbup.drivers.base import Artifact
from otitbup.gitstore import GitStore
from otitbup.pdfcanvas import PDFCanvas
from otitbup.runstore import RunRecord, RunStore


# ------------------------------------------------------------- SVG charts

def _valid_svg(svg: str) -> bool:
    # Wrap and parse to confirm well-formed XML.
    minidom.parseString(svg if svg.startswith("<svg") else f"<r>{svg}</r>")
    return True


def test_donut_svg_valid_and_scales():
    svg = charts.donut(7, 10, "covered")
    assert _valid_svg(svg)
    assert "<svg" in svg and "70%" in svg  # 7/10 in the title
    # Zero total must not divide by zero.
    assert _valid_svg(charts.donut(0, 0, "none"))


def test_vbars_svg():
    svg = charts.vbars([("mon", 3), ("tue", 5), ("wed", 0)])
    assert _valid_svg(svg)
    assert svg.count("<rect") == 3   # one bar per data point
    assert "tue: 5" in svg           # bar title
    assert charts.vbars([]) == "<p class='muted'>no data</p>"


def test_stacked_hbar_and_legend():
    svg = charts.stacked_hbar([("ok", 8, charts.OK), ("fail", 2, charts.FAIL)])
    assert _valid_svg(svg)
    lg = charts.legend([("ok", charts.OK), ("fail", charts.FAIL)])
    assert "ok" in lg and "fail" in lg


def test_charts_escape_labels():
    svg = charts.vbars([("<script>", 1)])
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


# ----------------------------------------------------------------- PDF

def test_pdfcanvas_renders_valid_pdf():
    c = PDFCanvas()
    c.rect(10, 10, 100, 50, fill=(1, 0, 0), stroke=(0, 0, 0))
    c.text(20, 20, "hello", size=12, font="bold")
    c.line(0, 0, 100, 100)
    c.new_page()
    c.text(20, 20, "page 2")
    data = c.render()
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert b" re B" in data          # filled+stroked rect
    assert b"/Count 2" in data       # two pages


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
                  - {name: sw-02, driver: cisco_ios}
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    store.write_and_commit(config.all_devices()[0], [Artifact(
        name="show_running_config.txt", data=b"hostname sw-01\nip http server\n")])
    runstore = RunStore(tmp_path / "r.db")
    now = time.time()
    runstore.record_run(RunRecord("s/net/sw-01", now - 60, now - 59,
                                  True, True, "abc", "ok"))
    return config, store, runstore


def test_rich_pdf_has_vector_graphics(env):
    from otitbup.reportfmt import to_pdf
    config, store, runstore = env
    data = to_pdf(config, store, runstore)
    assert data.startswith(b"%PDF")
    # Rich content: filled rectangles (tiles/bars/banner) and colour ops.
    assert b" re f" in data or b" re B" in data
    assert b" rg" in data            # fill colour set
    assert b"compliance report" in data
    assert b"Device status" in data  # the chart heading
    assert b"Page 1 of" in data      # footer pagination


def test_pdf_status_counts_mutually_exclusive(env):
    from otitbup.reportfmt import _status_counts
    config, store, runstore = env
    c = _status_counts(config, runstore, time.time())
    assert c["healthy"] + c["stale"] + c["never"] + c["failing"] == c["total"]
