import textwrap

from otitbup import mdrender
from otitbup.config import load_config
from otitbup.gitstore import GitStore
from otitbup.webui import WebUI, _find_doc


def test_markdown_escapes_and_renders():
    html = mdrender.render(
        "# H1\n\nHi **bold** `x` [l](https://e.com)\n\n- a\n- b\n\n"
        "```\n<script>evil</script>\n```\n\n> quote\n"
    )
    assert "<h1>H1</h1>" in html
    assert "<strong>bold</strong>" in html
    assert '<a href="https://e.com">l</a>' in html
    assert "<ul>" in html
    assert "&lt;script&gt;evil&lt;/script&gt;" in html  # code escaped
    assert "<blockquote>quote</blockquote>" in html


def test_markdown_blocks_javascript_links():
    html = mdrender.render("[x](javascript:alert(1))")
    assert "javascript:" not in html
    assert "x" in html


def test_find_doc_locates_repo_docs():
    # USAGE.md and FAQ.md ship in the repo docs/ directory.
    assert _find_doc("usage") is not None
    assert _find_doc("faq") is not None
    assert _find_doc("nope") is None


def _ui(tmp_path):
    cfg = tmp_path / "otitbup.yml"
    cfg.write_text(textwrap.dedent("""
        data_dir: ./data
        sites:
          - name: s
            zones: [{name: z, devices: [{name: d, driver: cisco_ios}]}]
    """))
    config = load_config(cfg)
    store = GitStore(config.data_dir)
    store.ensure_repo()
    return WebUI(config, store)


def test_doc_page_renders_usage(tmp_path):
    ui = _ui(tmp_path)
    page = ui.doc_page("usage")
    assert page is not None
    assert b"Usage" in page or b"usage" in page
    assert b"&larr; Help" in page or b"Help" in page


def test_doc_page_unknown_returns_none(tmp_path):
    assert _ui(tmp_path).doc_page("does-not-exist") is None


def test_help_page_links_to_manuals(tmp_path):
    page = _ui(tmp_path).help_page().decode()
    assert "/help/usage" in page
    assert "/help/faq" in page
