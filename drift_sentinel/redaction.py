"""Best-effort redaction of passwords, keys and communities in config lines."""

from __future__ import annotations

import re

REDACTED = "<redacted>"

_VALUE = r'("[^"]*"|[^\s;]+)'

# Each rule captures (prefix, value); the value is replaced, the prefix kept.
_RULES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        # ip ospf message-digest-key 1 md5 7 XXXX / snmp ... auth md5 XXXX
        r"(\bmd5\s+(?:\d+\s+)?)(?!key)(?!\d+\s)" + _VALUE,
        # enable secret 5 X / username u secret 9 X / neighbor p password 7 X / secret sha512 X
        r"(\b(?:secret|password)\s+(?:(?:\d|sha256|sha512)\s+)?)" + _VALUE,
        # key 7 X / key-string 7 X / crypto isakmp key 0 X
        r"((?<!-)\bkey(?:-string)?\s+\d\s+)" + _VALUE,
        # tacacs-server key X / radius-server key X / crypto isakmp key X
        r"(\b(?:tacacs-server|radius-server|isakmp)\s+key\s+(?:\d\s+)?)" + _VALUE,
        r"(\bsnmp(?:-server)?\s+community\s+)" + _VALUE,
        r"(\bpre-shared-key\s+(?:(?:local|remote)\s+)?(?:\d\s+)?)" + _VALUE,
        r"(\bpriv\s+(?:aes-128\s+)?)" + _VALUE,
        # Junos: authentication-key "$9$..." / key "$9$..."
        r'(\b(?:authentication-key|key)\s+)("[^"]*")',
    )
]


def redact(line: str) -> str:
    for rule in _RULES:
        line = rule.sub(lambda m: m.group(1) + REDACTED, line)
    return line
