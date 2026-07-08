import io
from contextlib import redirect_stderr, redirect_stdout

from otitbup import helptext
from otitbup.cli import main


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def test_help_lists_grouped_commands():
    code, out, _ = _run(["help"])
    assert code == 0
    # Groups and a spread of commands appear.
    assert "Backup & schedule:" in out
    assert "backup" in out and "restore" in out and "strategy" in out
    assert "explain <command>" in out


def test_explain_known_command():
    code, out, _ = _run(["explain", "backup"])
    assert code == 0
    assert "otitbup backup" in out
    assert "--force" in out          # detail mentions the flag
    assert "See also" in out


def test_explain_unknown_command_suggests():
    code, out, err = _run(["explain", "bakup"])
    assert code == 2
    assert "unknown command" in err
    assert "backup" in err           # close-match suggestion


def test_every_registered_command_is_explainable():
    # Re-running argument construction by parsing --help is heavy; instead
    # assert the known command set is covered by the registry.
    known = {
        "init", "validate", "list", "drivers", "backup", "daemon", "serve",
        "diff", "log", "search", "annotate", "maintenance", "baseline",
        "status", "verify", "policy", "retention", "strategy", "export",
        "restore", "net-restore", "dr-plan", "rehearse", "discover",
        "reconcile", "netbox", "report", "report-verify", "passwd",
        "certgen", "secrets", "help", "explain",
        "test", "gc", "verify-audit", "token", "anomalies", "desired",
        "federation",
    }
    missing = known - set(helptext.COMMANDS)
    assert not missing, f"commands missing from help registry: {missing}"


def test_registry_commands_are_real_subcommands():
    # Every command documented in the registry must actually run (exit
    # cleanly for --help), catching stale help entries.
    import subprocess
    import sys
    for name in helptext.COMMANDS:
        if name in ("help", "explain"):
            continue
        proc = subprocess.run(
            [sys.executable, "-m", "otitbup", name, "--help"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, f"{name} --help failed: {proc.stderr}"


def test_explain_every_command_renders():
    for name in helptext.COMMANDS:
        text, found = helptext.render_explain(name)
        assert found
        assert name in text and len(text) > 80
