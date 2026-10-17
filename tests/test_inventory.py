import pytest

from drift_sentinel.inventory import InventoryError, load_inventory


def write(tmp_path, text):
    p = tmp_path / "inventory.yml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults_merge_and_paths_resolve(tmp_path):
    inv = load_inventory(
        write(
            tmp_path,
            """
settings:
  approved_dir: baselines
  ignore: ['^ntp clock-period']
defaults:
  platform: arista_eos
devices:
  - name: r1
    host: 10.0.0.1
  - name: r2
    platform: juniper_junos
    collector: file
    path: running/r2.cfg
    approved: custom/r2.conf
""",
        )
    )
    r1, r2 = inv.devices
    assert r1.platform == "arista_eos" and r1.collector == "ssh" and r1.port == 22
    assert r1.approved == tmp_path / "baselines" / "r1.cfg"
    assert r2.platform == "juniper_junos"
    assert r2.path == tmp_path / "running" / "r2.cfg"
    assert r2.approved == tmp_path / "custom" / "r2.conf"
    assert inv.settings.ignore == ["^ntp clock-period"]


@pytest.mark.parametrize(
    "body, message",
    [
        ("devices: []", "non-empty list"),
        ("devices:\n  - host: 1.1.1.1", "'name' is required"),
        ("devices:\n  - name: bad/name\n    host: x", "'name' is required"),
        ("devices:\n  - name: r1\n    host: x\n  - name: r1\n    host: y", "duplicate"),
        ("devices:\n  - name: r1", "needs 'host'"),
        ("devices:\n  - name: r1\n    collector: file", "needs 'path'"),
        ("devices:\n  - name: r1\n    collector: carrier-pigeon", "collector must be"),
        ("devices:\n  - name: r1\n    hostt: x", "unknown key"),
        ("settings:\n  secrets: shout\ndevices:\n  - name: r1\n    host: x", "settings.secrets"),
        ("nonsense: 1\ndevices:\n  - name: r1\n    host: x", "unknown top-level"),
        ("devices:\n  - name: r1\n    host: x\n    ignore: '^foo'", "list of regex"),
    ],
)
def test_validation_errors(tmp_path, body, message):
    with pytest.raises(InventoryError, match=message):
        load_inventory(write(tmp_path, body))


def test_missing_file(tmp_path):
    with pytest.raises(InventoryError, match="not found"):
        load_inventory(tmp_path / "nope.yml")


def test_select(tmp_path):
    inv = load_inventory(write(tmp_path, "devices:\n  - name: a\n    host: x\n  - name: b\n    host: y"))
    assert [d.name for d in inv.select(["b"])] == ["b"]
    assert [d.name for d in inv.select(None)] == ["a", "b"]
    with pytest.raises(InventoryError, match="unknown device"):
        inv.select(["c"])
