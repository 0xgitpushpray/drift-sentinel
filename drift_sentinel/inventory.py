"""Load and validate the YAML inventory of routers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

COLLECTORS = ("ssh", "file", "command")
SECRETS_MODES = ("compare", "mask")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class InventoryError(Exception):
    pass


@dataclass
class Device:
    name: str
    platform: str = "cisco_ios"
    collector: str = "ssh"
    host: str | None = None
    port: int = 22
    username_env: str = "ROUTER_USERNAME"
    password_env: str = "ROUTER_PASSWORD"
    enable_secret_env: str | None = None
    key_file: str | None = None
    command: str | None = None  # ssh: CLI command override; command collector: shell command
    path: Path | None = None  # file collector: where the running config lives
    approved: Path | None = None  # baseline file; defaults to <approved_dir>/<name>.cfg
    ignore: list[str] = field(default_factory=list)
    timeout: int = 60


@dataclass
class Settings:
    approved_dir: Path
    secrets: str = "compare"
    context_lines: int = 3
    ignore: list[str] = field(default_factory=list)


@dataclass
class Inventory:
    path: Path
    settings: Settings
    devices: list[Device]

    def select(self, names: list[str] | None) -> list[Device]:
        if not names:
            return list(self.devices)
        by_name = {d.name: d for d in self.devices}
        unknown = [n for n in names if n not in by_name]
        if unknown:
            raise InventoryError(f"unknown device(s): {', '.join(unknown)}")
        return [by_name[n] for n in names]


_DEVICE_KEYS = {f.name for f in fields(Device)}
_SETTINGS_KEYS = {f.name for f in fields(Settings)}


def _resolve(base: Path, value: str | Path) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else base / p


def _string_list(value, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InventoryError(f"{where}: 'ignore' must be a list of regex strings")
    return value


def load_inventory(path: str | Path) -> Inventory:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise InventoryError(f"inventory file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise InventoryError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise InventoryError(f"{path}: expected a mapping at the top level")
    extra = set(data) - {"settings", "defaults", "devices"}
    if extra:
        raise InventoryError(f"{path}: unknown top-level key(s): {', '.join(sorted(extra))}")

    base = path.resolve().parent
    raw_settings = data.get("settings") or {}
    bad = set(raw_settings) - _SETTINGS_KEYS
    if bad:
        raise InventoryError(f"settings: unknown key(s): {', '.join(sorted(bad))}")
    settings = Settings(
        approved_dir=_resolve(base, raw_settings.get("approved_dir", "approved")),
        secrets=raw_settings.get("secrets", "compare"),
        context_lines=int(raw_settings.get("context_lines", 3)),
        ignore=_string_list(raw_settings.get("ignore"), "settings"),
    )
    if settings.secrets not in SECRETS_MODES:
        raise InventoryError(f"settings.secrets must be one of {SECRETS_MODES}, got {settings.secrets!r}")

    defaults = data.get("defaults") or {}
    bad = set(defaults) - (_DEVICE_KEYS - {"name"})
    if bad:
        raise InventoryError(f"defaults: unknown key(s): {', '.join(sorted(bad))}")

    raw_devices = data.get("devices") or []
    if not isinstance(raw_devices, list) or not raw_devices:
        raise InventoryError(f"{path}: 'devices' must be a non-empty list")

    devices: list[Device] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_devices):
        if not isinstance(raw, dict):
            raise InventoryError(f"devices[{i}]: expected a mapping")
        merged = {**defaults, **raw}
        name = merged.get("name")
        where = f"device {name!r}" if name else f"devices[{i}]"
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise InventoryError(f"{where}: 'name' is required and may only contain letters, digits, '.', '_', '-'")
        if name in seen:
            raise InventoryError(f"{where}: duplicate device name")
        seen.add(name)
        bad = set(merged) - _DEVICE_KEYS
        if bad:
            raise InventoryError(f"{where}: unknown key(s): {', '.join(sorted(bad))}")

        merged["ignore"] = _string_list(merged.get("ignore"), where)
        if merged.get("path") is not None:
            merged["path"] = _resolve(base, merged["path"])
        merged["approved"] = (
            _resolve(base, merged["approved"]) if merged.get("approved") else settings.approved_dir / f"{name}.cfg"
        )
        device = Device(**merged)
        device.port = int(device.port)
        device.timeout = int(device.timeout)

        if device.collector not in COLLECTORS:
            raise InventoryError(f"{where}: collector must be one of {COLLECTORS}, got {device.collector!r}")
        if device.collector == "ssh" and not device.host:
            raise InventoryError(f"{where}: ssh collector needs 'host'")
        if device.collector == "file" and not device.path:
            raise InventoryError(f"{where}: file collector needs 'path'")
        if device.collector == "command" and not device.command:
            raise InventoryError(f"{where}: command collector needs 'command'")
        devices.append(device)

    return Inventory(path=path, settings=settings, devices=devices)
