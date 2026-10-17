from drift_sentinel.compare import CLEAN, DRIFT, ERROR, DriftResult
from drift_sentinel.notify import LABEL, GitHubIssues, NotifyError, webhook_message


class FakeGitHub(GitHubIssues):
    """Records API calls and serves canned open issues."""

    def __init__(self, open_issues=()):
        super().__init__("me/repo", "token", run_url="https://example/run/1")
        self.issues = list(open_issues)
        self.calls = []

    def _request(self, method, path, payload=None):
        self.calls.append((method, path.split("?")[0], payload))
        if method == "POST" and path.endswith("/labels"):
            raise NotifyError("GitHub API POST /labels -> 422: already_exists")
        if method == "GET":
            return self.issues
        if method == "POST" and path.endswith("/issues"):
            return {"number": 42}
        return {}

    def writes(self):
        return [(m, p) for m, p, _ in self.calls if m in ("POST", "PATCH") and not p.endswith("/labels")]


def drift(name="r1", diff="--- a\n+++ b\n-x\n+y"):
    return DriftResult(name, DRIFT, diff=diff, added=1, removed=1, approved_path=f"approved/{name}.cfg")


def existing(name, fingerprint, number=7):
    marker = f'<!-- drift-sentinel device="{name}" fingerprint="{fingerprint}" -->'
    return {"number": number, "body": marker + "\nold body", "labels": [{"name": LABEL}]}


def test_opens_issue_for_new_drift():
    gh = FakeGitHub()
    actions = gh.sync([drift()])
    assert actions == ["opened issue #42 for r1"]
    method, path, payload = gh.calls[-1]
    assert (method, path) == ("POST", "/repos/me/repo/issues")
    assert payload["title"] == "Config drift: r1" and payload["labels"] == [LABEL]
    assert f'fingerprint="{drift().fingerprint}"' in payload["body"]
    assert "```diff" in payload["body"]


def test_same_drift_does_not_renotify():
    r = drift()
    gh = FakeGitHub([existing("r1", r.fingerprint)])
    assert gh.sync([r]) == ["issue #7 for r1 already up to date"]
    assert gh.writes() == []


def test_changed_drift_updates_issue_and_comments():
    gh = FakeGitHub([existing("r1", "000000000000")])
    assert gh.sync([drift()]) == ["updated issue #7 for r1"]
    assert gh.writes() == [("PATCH", "/repos/me/repo/issues/7"), ("POST", "/repos/me/repo/issues/7/comments")]


def test_clean_device_closes_issue():
    gh = FakeGitHub([existing("r1", "abc")])
    assert gh.sync([DriftResult("r1", CLEAN)]) == ["closed issue #7 for r1"]
    assert gh.calls[-1][2] == {"state": "closed", "state_reason": "completed"}


def test_error_leaves_issue_alone():
    gh = FakeGitHub([existing("r1", "abc")])
    assert gh.sync([DriftResult("r1", ERROR, error="timeout")]) == []
    assert gh.writes() == []


def test_pull_requests_and_foreign_issues_ignored():
    gh = FakeGitHub([{**existing("r1", "abc"), "pull_request": {}}, {"number": 9, "body": "unrelated"}])
    assert gh.open_issues() == {}


def test_webhook_message_lists_problems():
    msg = webhook_message([drift("r1"), DriftResult("r2", ERROR, error="timeout"), DriftResult("r3", CLEAN)])
    assert "r1: drifted (+1 / -1 lines)" in msg
    assert "r2: check failed (timeout)" in msg
    assert "r3" not in msg
