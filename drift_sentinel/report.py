"""Render check results as console text, Markdown or JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import __version__
from .compare import CLEAN, DRIFT, ERROR, DriftResult

_ORDER = {DRIFT: 0, ERROR: 1, CLEAN: 2}
_MAX_MD_DIFF = 20_000


def sort_results(results: list[DriftResult]) -> list[DriftResult]:
    return sorted(results, key=lambda r: _ORDER[r.status])  # stable: keeps inventory order


def counts(results: list[DriftResult]) -> dict[str, int]:
    return {s: sum(1 for r in results if r.status == s) for s in (DRIFT, ERROR, CLEAN)}


def headline(results: list[DriftResult]) -> str:
    c = counts(results)
    return f"{len(results)} device(s) checked: {c[DRIFT]} drifted, {c[CLEAN]} clean, {c[ERROR]} error(s)"


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_text(results: list[DriftResult], show_diff: bool = True) -> str:
    out = [f"drift-sentinel: {headline(results)}", ""]
    for r in sort_results(results):
        if r.status == DRIFT:
            out.append(f"DRIFT  {r.device}  (+{r.added} / -{r.removed})")
            if show_diff:
                out.extend(["", r.diff, ""])
        elif r.status == ERROR:
            out.append(f"ERROR  {r.device}  {r.error}")
        else:
            out.append(f"OK     {r.device}")
    return "\n".join(out).rstrip() + "\n"


def fenced_diff(diff: str, limit: int = _MAX_MD_DIFF) -> str:
    if len(diff) > limit:
        diff = diff[:limit].rsplit("\n", 1)[0] + "\n... (diff truncated)"
    fence = "````" if "```" in diff else "```"
    return f"{fence}diff\n{diff}\n{fence}"


def render_markdown(results: list[DriftResult], run_url: str | None = None) -> str:
    c = counts(results)
    lines = [
        "## Router config drift report",
        "",
        f"{timestamp()} · {len(results)} device(s) · **{c[DRIFT]} drifted** · {c[CLEAN]} clean · {c[ERROR]} error(s)",
        "",
        "| Device | Status | Details |",
        "|---|---|---|",
    ]
    for r in sort_results(results):
        if r.status == DRIFT:
            lines.append(f"| `{r.device}` | 🔴 drift | +{r.added} / −{r.removed} lines |")
        elif r.status == ERROR:
            lines.append(f"| `{r.device}` | ⚠️ error | {r.error.replace('|', '/')} |")
        else:
            lines.append(f"| `{r.device}` | 🟢 clean | matches approved baseline |")
    for r in sort_results(results):
        if r.status == DRIFT:
            lines += ["", f"<details><summary><b>{r.device}</b> diff</summary>", "", fenced_diff(r.diff), "", "</details>"]
    if run_url:
        lines += ["", f"[Workflow run]({run_url})"]
    return "\n".join(lines) + "\n"


def render_json(results: list[DriftResult]) -> str:
    payload = {
        "tool": "drift-sentinel",
        "version": __version__,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": counts(results),
        "devices": [r.to_dict() for r in results],
    }
    return json.dumps(payload, indent=2) + "\n"
