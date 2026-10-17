"""Compare a running config against its approved baseline."""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass

from .normalize import normalize
from .redaction import redact

CLEAN = "clean"
DRIFT = "drift"
ERROR = "error"


@dataclass
class DriftResult:
    device: str
    status: str
    diff: str = ""  # unified diff, secrets redacted
    added: int = 0
    removed: int = 0
    error: str = ""
    approved_path: str = ""

    @property
    def fingerprint(self) -> str:
        """Stable id of this particular drift, used to avoid re-notifying."""
        return hashlib.sha256(self.diff.encode()).hexdigest()[:12] if self.diff else ""

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "status": self.status,
            "added": self.added,
            "removed": self.removed,
            "fingerprint": self.fingerprint,
            "approved_path": self.approved_path,
            "error": self.error,
            "diff": self.diff,
        }


def compare(
    device: str,
    approved: str,
    running: str,
    platform: str = "generic",
    ignore: tuple[str, ...] | list[str] = (),
    secrets: str = "compare",
    context_lines: int = 3,
) -> DriftResult:
    """Diff two configs after normalization.

    ``secrets="compare"`` treats a changed password as drift (it is still redacted in
    the output). ``secrets="mask"`` redacts both sides before comparing, for
    baselines that are stored with secrets already removed.
    """
    a = normalize(approved, platform, ignore)
    r = normalize(running, platform, ignore)
    if secrets == "mask":
        a = [redact(line) for line in a]
        r = [redact(line) for line in r]
    if a == r:
        return DriftResult(device, CLEAN)

    lines = list(
        difflib.unified_diff(
            a, r, fromfile=f"approved/{device}", tofile=f"running/{device}", n=context_lines, lineterm=""
        )
    )
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return DriftResult(device, DRIFT, "\n".join(redact(line) for line in lines), added, removed)
