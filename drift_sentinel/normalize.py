"""Strip volatile noise from configs so only meaningful changes count as drift."""

from __future__ import annotations

import re
from typing import Iterable

# Lines that change between collections even when the configuration itself did not.
VOLATILE: dict[str, list[str]] = {
    "cisco_ios": [
        r"^Building configuration",
        r"^Current configuration\s*:",
        r"^! Last configuration change at",
        r"^! NVRAM config last updated at",
        r"^! No configuration change since last restart",
        r"^ntp clock-period",
        r"^!Time:",
    ],
    "cisco_xr": [
        r"^Building configuration",
        r"^!! IOS XR Configuration",
        r"^!! Last configuration change",
        r"^\w{3} \w{3} +\d+ \d\d:\d\d:\d\d(\.\d+)? \w+$",
    ],
    "cisco_nxos": [
        r"^!Command:",
        r"^!Running configuration last done at",
        r"^!Startup config saved at",
        r"^!Time:",
    ],
    "cisco_asa": [
        r"^: Saved",
        r"^: Written by",
        r"^: Serial Number",
        r"^: Hardware:",
        r"^Cryptochecksum:",
    ],
    "arista_eos": [
        r"^! Command:",
        r"^! device:",
        r"^! Time:",
        r"^! boot system",
    ],
    "juniper_junos": [
        r"^## Last commit:",
        r"^## Last changed:",
        r"^\{(master|primary|backup|secondary)(:\w+)?\}$",
    ],
}
VOLATILE["cisco_xe"] = VOLATILE["cisco_ios"]

# Platforms that use a bare "!" as a section separator / comment.
BANG_COMMENTS = {"cisco_ios", "cisco_xe", "cisco_xr", "cisco_nxos", "cisco_asa", "arista_eos"}


def compile_patterns(platform: str, ignore: Iterable[str] = ()) -> list[re.Pattern[str]]:
    patterns = [*VOLATILE.get(platform, []), *ignore]
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise ValueError(f"invalid ignore pattern {pattern!r}: {exc}") from exc
    return compiled


def normalize(text: str, platform: str = "generic", ignore: Iterable[str] = ()) -> list[str]:
    """Return the config as a list of significant lines.

    Drops blank lines, trailing whitespace, platform volatile lines and any line
    matching a user ``ignore`` regex (matched with ``re.search`` against the line
    with its indentation intact).
    """
    patterns = compile_patterns(platform, ignore)
    drop_bangs = platform in BANG_COMMENTS
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        if drop_bangs and line.strip() == "!":
            continue
        if any(p.search(line) for p in patterns):
            continue
        lines.append(line)
    return lines
