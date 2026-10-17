"""Tell people about drift: chat webhooks and one GitHub issue per drifted device."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .compare import CLEAN, DRIFT, ERROR, DriftResult
from .report import counts, fenced_diff, headline, timestamp

USER_AGENT = "drift-sentinel"


class NotifyError(Exception):
    pass


def _post_json(url: str, payload: dict, headers: dict | None = None, method: str = "POST"):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body) if body else None


# --------------------------------------------------------------------------- webhooks


def webhook_message(results: list[DriftResult], run_url: str | None = None) -> str:
    c = counts(results)
    if c[DRIFT] or c[ERROR]:
        lines = [f":rotating_light: Router config drift check: {headline(results)}"]
    else:
        lines = [f":white_check_mark: Router config drift check: {headline(results)}"]
    for r in results:
        if r.status == DRIFT:
            lines.append(f"• {r.device}: drifted (+{r.added} / -{r.removed} lines)")
        elif r.status == ERROR:
            lines.append(f"• {r.device}: check failed ({r.error})")
    if run_url:
        lines.append(f"Details: {run_url}")
    return "\n".join(lines)


def send_webhook(url: str, fmt: str, results: list[DriftResult], run_url: str | None = None) -> None:
    text = webhook_message(results, run_url)
    payload = {"content": text[:2000]} if fmt == "discord" else {"text": text}
    try:
        _post_json(url, payload)
    except (urllib.error.URLError, OSError) as exc:
        raise NotifyError(f"webhook delivery failed: {exc}") from None


# --------------------------------------------------------------------------- GitHub issues

LABEL = "config-drift"
_MARKER_RE = re.compile(r'<!-- drift-sentinel device="([^"]+)" fingerprint="([0-9a-f]*)" -->')
_MAX_BODY_DIFF = 55_000  # GitHub caps issue bodies at 65,536 characters


class GitHubIssues:
    """Keeps exactly one open issue per drifted device in sync with the latest check."""

    def __init__(self, repo: str, token: str, api_url: str = "https://api.github.com", run_url: str | None = None):
        self.repo = repo
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.run_url = run_url

    def _request(self, method: str, path: str, payload: dict | None = None):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            return _post_json(f"{self.api_url}{path}", payload, headers=headers, method=method)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise NotifyError(f"GitHub API {method} {path} -> {exc.code}: {detail}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise NotifyError(f"GitHub API {method} {path} failed: {exc}") from None

    def ensure_label(self) -> None:
        try:
            self._request(
                "POST",
                f"/repos/{self.repo}/labels",
                {"name": LABEL, "color": "d93f0b", "description": "Router config drifted from approved baseline"},
            )
        except NotifyError as exc:
            if "-> 422" not in str(exc):  # 422 = label already exists
                raise

    def open_issues(self) -> dict[str, dict]:
        """Map device name -> open drift issue (with parsed fingerprint)."""
        found: dict[str, dict] = {}
        page = 1
        while True:
            query = urllib.parse.urlencode({"state": "open", "labels": LABEL, "per_page": 100, "page": page})
            batch = self._request("GET", f"/repos/{self.repo}/issues?{query}") or []
            for issue in batch:
                if "pull_request" in issue:
                    continue
                m = _MARKER_RE.search(issue.get("body") or "")
                if m and m.group(1) not in found:
                    found[m.group(1)] = {**issue, "fingerprint": m.group(2)}
            if len(batch) < 100:
                return found
            page += 1

    def issue_body(self, r: DriftResult) -> str:
        run = f"[workflow run]({self.run_url})" if self.run_url else "local run"
        baseline = f"`{r.approved_path}`" if r.approved_path else "the approved baseline"
        return "\n".join(
            [
                f'<!-- drift-sentinel device="{r.device}" fingerprint="{r.fingerprint}" -->',
                f"The running configuration of **`{r.device}`** no longer matches {baseline}.",
                "",
                f"**Last checked:** {timestamp()} ({run}) · **+{r.added} / −{r.removed}** lines",
                "",
                fenced_diff(r.diff, _MAX_BODY_DIFF),
                "",
                "### How to resolve",
                "",
                "- **Unauthorized or accidental change?** Revert it on the router. "
                "This issue closes itself after the next clean check.",
                f"- **Intended change?** Re-baseline it: run the *Approve running config* workflow for `{r.device}` "
                f"(or `drift-sentinel approve {r.device}` locally) and merge the pull request.",
                "",
                "<sub>Opened by drift-sentinel. Secrets in the diff are redacted.</sub>",
            ]
        )

    def sync(self, results: list[DriftResult]) -> list[str]:
        """Open, update or close issues. Returns a human-readable log of actions."""
        self.ensure_label()
        existing = self.open_issues()
        actions: list[str] = []
        for r in results:
            issue = existing.get(r.device)
            if r.status == DRIFT:
                if issue is None:
                    created = self._request(
                        "POST",
                        f"/repos/{self.repo}/issues",
                        {"title": f"Config drift: {r.device}", "body": self.issue_body(r), "labels": [LABEL]},
                    )
                    actions.append(f"opened issue #{created['number']} for {r.device}")
                elif issue["fingerprint"] != r.fingerprint:
                    n = issue["number"]
                    self._request("PATCH", f"/repos/{self.repo}/issues/{n}", {"body": self.issue_body(r)})
                    self._request(
                        "POST",
                        f"/repos/{self.repo}/issues/{n}/comments",
                        {"body": f"The drift on `{r.device}` changed ({timestamp()}). "
                         f"The description now shows the latest diff (+{r.added} / −{r.removed})."},
                    )
                    actions.append(f"updated issue #{n} for {r.device}")
                else:
                    actions.append(f"issue #{issue['number']} for {r.device} already up to date")
            elif r.status == CLEAN and issue is not None:
                n = issue["number"]
                self._request(
                    "POST",
                    f"/repos/{self.repo}/issues/{n}/comments",
                    {"body": f"`{r.device}` matches its approved baseline again ({timestamp()}). Closing."},
                )
                self._request(
                    "PATCH", f"/repos/{self.repo}/issues/{n}", {"state": "closed", "state_reason": "completed"}
                )
                actions.append(f"closed issue #{n} for {r.device}")
            # ERROR: leave any issue untouched; a failed check proves nothing either way.
        return actions
