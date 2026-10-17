import json
import sys

import pytest

from drift_sentinel.cli import main

BASELINE = "hostname r1\ninterface Gi1\n shutdown\nend\n"


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Two file-collected routers: r1 matches its baseline, r2 has drifted."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("DRIFT_WEBHOOK_URL", raising=False)
    (tmp_path / "approved").mkdir()
    (tmp_path / "running").mkdir()
    for name in ("r1", "r2"):
        (tmp_path / "approved" / f"{name}.cfg").write_text(BASELINE)
    (tmp_path / "running" / "r1.cfg").write_text("Building configuration...\n!\n" + BASELINE)
    (tmp_path / "running" / "r2.cfg").write_text(BASELINE.replace(" shutdown", " no shutdown"))
    (tmp_path / "inventory.yml").write_text(
        "defaults:\n  collector: file\ndevices:\n"
        "  - name: r1\n    path: running/r1.cfg\n"
        "  - name: r2\n    path: running/r2.cfg\n"
    )
    return tmp_path


def test_check_reports_drift(lab, capsys):
    code = main(["check", "--report", "out/report.md", "--json", "out/report.json"])
    assert code == 0
    out = capsys.readouterr().out
    assert "1 drifted, 1 clean, 0 error(s)" in out
    assert "DRIFT  r2  (+1 / -1)" in out and "OK     r1" in out
    data = json.loads((lab / "out" / "report.json").read_text())
    assert data["summary"] == {"drift": 1, "error": 0, "clean": 1}
    assert "🔴 drift" in (lab / "out" / "report.md").read_text(encoding="utf-8")


def test_fail_on_drift_exit_code(lab):
    assert main(["check", "--fail-on-drift", "-q"]) == 1
    assert main(["check", "--fail-on-drift", "-q", "-d", "r1"]) == 0


def test_missing_baseline_is_an_error(lab, capsys):
    (lab / "approved" / "r1.cfg").unlink()
    assert main(["check", "-q"]) == 2
    assert "no approved baseline" in capsys.readouterr().out


def test_unreadable_running_config_is_an_error(lab):
    (lab / "running" / "r1.cfg").unlink()
    assert main(["check", "-q", "-d", "r1"]) == 2


def test_step_summary_written(lab, monkeypatch):
    summary = lab / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    main(["check", "-q"])
    assert "Router config drift report" in summary.read_text(encoding="utf-8")


def test_github_issues_without_token_fails(lab, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["check", "-q", "--github-issues"]) == 2


def test_approve_rebaselines_and_clears_drift(lab, capsys):
    assert main(["approve", "r2"]) == 0
    assert "updated" in capsys.readouterr().out
    assert main(["check", "--fail-on-drift", "-q"]) == 0
    assert main(["approve", "r2"]) == 0
    assert "unchanged" in capsys.readouterr().out


def test_approve_writes_normalized_config(lab):
    (lab / "approved" / "r1.cfg").unlink()
    main(["approve", "r1"])
    assert (lab / "approved" / "r1.cfg").read_text() == BASELINE


def test_approve_needs_devices_or_all(lab):
    assert main(["approve"]) == 2
    assert main(["approve", "--all"]) == 0


def test_command_collector(lab):
    py = sys.executable.replace("\\", "/")
    (lab / "show.py").write_text("import sys\nprint(open(f'running/{sys.argv[1]}.cfg').read())\n")
    (lab / "inventory.yml").write_text(
        "devices:\n"
        "  - name: r1\n    collector: command\n"
        f"    command: '\"{py}\" show.py {{name}}'\n"
    )
    assert main(["check", "--fail-on-drift", "-q"]) == 0


def test_bad_inventory(lab, capsys):
    (lab / "inventory.yml").write_text("devices: []\n")
    assert main(["check"]) == 2
    assert "error:" in capsys.readouterr().err
