import pytest

from drift_sentinel.normalize import normalize
from drift_sentinel.redaction import REDACTED, redact


def test_ios_volatile_lines_and_separators_are_dropped():
    raw = (
        "Building configuration...\r\n\r\n"
        "Current configuration : 1874 bytes\r\n"
        "!\r\n"
        "! Last configuration change at 01:58:12 UTC Sat Oct 17 2026 by netops\r\n"
        "hostname r1   \r\n"
        "router bgp 1\r\n"
        " !\r\n"
        " neighbor 1.1.1.1 remote-as 2\r\n"
        "ntp clock-period 17179738\r\n"
        "end\r\n"
    )
    assert normalize(raw, "cisco_ios") == ["hostname r1", "router bgp 1", " neighbor 1.1.1.1 remote-as 2", "end"]


def test_bangs_kept_on_platforms_without_bang_comments():
    assert normalize("!\nfoo\n", "generic") == ["!", "foo"]


def test_junos_commit_header_dropped():
    raw = "## Last commit: 2026-10-16 22:04:51 UTC by netops\nset system host-name r1\n{master:0}\n"
    assert normalize(raw, "juniper_junos") == ["set system host-name r1"]


def test_user_ignore_patterns_keep_indentation():
    raw = "interface Gi1\n description changes often\n ip address 10.0.0.1 255.255.255.0\n"
    assert normalize(raw, "cisco_ios", [r"^\s+description "]) == ["interface Gi1", " ip address 10.0.0.1 255.255.255.0"]


def test_invalid_ignore_pattern_raises():
    with pytest.raises(ValueError, match="invalid ignore pattern"):
        normalize("x", "cisco_ios", ["("])


@pytest.mark.parametrize(
    "line, expected",
    [
        ("enable secret 9 $9$abc$def", f"enable secret 9 {REDACTED}"),
        ("username admin privilege 15 secret 5 $1$x$y", f"username admin privilege 15 secret 5 {REDACTED}"),
        ("username bob password 0 plaintext", f"username bob password 0 {REDACTED}"),
        (" neighbor 10.0.0.2 password 7 05080F1C2243", f" neighbor 10.0.0.2 password 7 {REDACTED}"),
        (" ip ospf message-digest-key 1 md5 7 0822455D0A16", f" ip ospf message-digest-key 1 md5 7 {REDACTED}"),
        (" ip ospf message-digest-key 1 md5 MYKEY", f" ip ospf message-digest-key 1 md5 {REDACTED}"),
        (" standby 1 authentication md5 key-string 7 0822455D", f" standby 1 authentication md5 key-string 7 {REDACTED}"),
        (" key 7 0822455D0A16544541", f" key 7 {REDACTED}"),
        ("tacacs-server key cisco123", f"tacacs-server key {REDACTED}"),
        ("crypto isakmp key 0 s3cret address 192.0.2.1", f"crypto isakmp key 0 {REDACTED} address 192.0.2.1"),
        ("snmp-server community public RO 10", f"snmp-server community {REDACTED} RO 10"),
        ("  pre-shared-key local s3cret", f"  pre-shared-key local {REDACTED}"),
        ("username admin role network-admin secret sha512 $6$abc", f"username admin role network-admin secret sha512 {REDACTED}"),
        (
            'set system root-authentication encrypted-password "$6$abc$def"',
            f"set system root-authentication encrypted-password {REDACTED}",
        ),
        (
            'set protocols ospf area 0 interface ge-0/0/0 authentication md5 1 key "$9$abc"',
            f"set protocols ospf area 0 interface ge-0/0/0 authentication md5 1 key {REDACTED}",
        ),
        ("set snmp community s3cret authorization read-only", f"set snmp community {REDACTED} authorization read-only"),
    ],
)
def test_redact(line, expected):
    assert redact(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "service password-encryption",
        "security passwords min-length 8",
        " standby 1 authentication md5 key-chain HSRP",
        "key chain OSPF",
        "hostname r1",
    ],
)
def test_redact_leaves_harmless_lines_alone(line):
    assert redact(line) == line
