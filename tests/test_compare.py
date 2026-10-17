from drift_sentinel.compare import CLEAN, DRIFT, compare

APPROVED = """hostname r1
enable secret 9 $9$old
interface Gi1
 ip address 10.0.0.1 255.255.255.0
 shutdown
end
"""


def test_only_volatile_differences_are_clean():
    running = "Building configuration...\n! Last configuration change at x\n!\n" + APPROVED.replace("\n", "\r\n")
    assert compare("r1", APPROVED, running, "cisco_ios").status == CLEAN


def test_drift_is_detected_and_counted():
    running = APPROVED.replace(" shutdown", " no shutdown")
    result = compare("r1", APPROVED, running, "cisco_ios")
    assert result.status == DRIFT
    assert (result.added, result.removed) == (1, 1)
    assert "- shutdown" in result.diff and "+ no shutdown" in result.diff
    assert result.diff.startswith("--- approved/r1\n+++ running/r1")
    assert len(result.fingerprint) == 12


def test_secret_change_is_drift_but_redacted():
    running = APPROVED.replace("$9$old", "$9$new")
    result = compare("r1", APPROVED, running, "cisco_ios")
    assert result.status == DRIFT
    assert "$9$" not in result.diff
    assert "-enable secret 9 <redacted>" in result.diff


def test_mask_mode_ignores_secret_values():
    running = APPROVED.replace("$9$old", "$9$new")
    assert compare("r1", APPROVED, running, "cisco_ios", secrets="mask").status == CLEAN


def test_mask_mode_accepts_redacted_baseline():
    baseline = APPROVED.replace("$9$old", "<redacted>")
    assert compare("r1", baseline, APPROVED, "cisco_ios", secrets="mask").status == CLEAN


def test_ignore_patterns_apply_to_both_sides():
    running = APPROVED.replace("hostname r1", "hostname r1-renamed")
    assert compare("r1", APPROVED, running, "cisco_ios", ignore=["^hostname "]).status == CLEAN


def test_fingerprint_changes_with_diff():
    a = compare("r1", APPROVED, APPROVED.replace(" shutdown", " no shutdown"), "cisco_ios")
    b = compare("r1", APPROVED, APPROVED.replace("hostname r1", "hostname r2"), "cisco_ios")
    assert a.fingerprint != b.fingerprint
