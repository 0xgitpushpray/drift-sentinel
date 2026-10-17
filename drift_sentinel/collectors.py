"""Fetch the running configuration of a device."""

from __future__ import annotations

import os
import shlex
import subprocess

from .inventory import Device

DEFAULT_COMMANDS = {
    "cisco_ios": "show running-config",
    "cisco_xe": "show running-config",
    "cisco_xr": "show running-config",
    "cisco_nxos": "show running-config",
    "cisco_asa": "show running-config",
    "arista_eos": "show running-config",
    "juniper_junos": "show configuration | display set",
}


class CollectionError(Exception):
    pass


def collect(device: Device) -> str:
    if device.collector == "file":
        return _collect_file(device)
    if device.collector == "command":
        return _collect_command(device)
    return _collect_ssh(device)


def _collect_file(device: Device) -> str:
    try:
        return device.path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CollectionError(f"cannot read {device.path}: {exc.strerror or exc}") from None


def _collect_command(device: Device) -> str:
    cmd = device.command.format(name=shlex.quote(device.name), host=shlex.quote(device.host or ""))
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=device.timeout)
    except subprocess.TimeoutExpired:
        raise CollectionError(f"command timed out after {device.timeout}s") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [""]
        raise CollectionError(f"command exited {proc.returncode}: {detail[0]}")
    return proc.stdout


def _env(name: str | None, required: bool) -> str | None:
    if not name:
        return None
    value = os.environ.get(name)
    if required and not value:
        raise CollectionError(f"environment variable {name} is not set")
    return value or None


def _collect_ssh(device: Device) -> str:
    try:
        from netmiko import ConnectHandler
    except ImportError:
        raise CollectionError("netmiko is not installed; run: pip install 'drift-sentinel[ssh]'") from None

    params = {
        "device_type": device.platform,
        "host": device.host,
        "port": device.port,
        "username": _env(device.username_env, required=True),
        "conn_timeout": min(device.timeout, 30),
        "timeout": device.timeout,
    }
    if device.key_file:
        params.update(use_keys=True, key_file=os.path.expanduser(device.key_file))
        password = _env(device.password_env, required=False)
        if password:
            params["password"] = password
    else:
        params["password"] = _env(device.password_env, required=True)
    enable_secret = _env(device.enable_secret_env, required=True)
    if enable_secret:
        params["secret"] = enable_secret

    command = device.command or DEFAULT_COMMANDS.get(device.platform, "show running-config")
    try:
        with ConnectHandler(**params) as conn:
            if enable_secret:
                conn.enable()
            return conn.send_command(command, read_timeout=device.timeout)
    except Exception as exc:  # netmiko raises many unrelated exception types
        raise CollectionError(f"ssh to {device.host}:{device.port} failed: {type(exc).__name__}: {exc}") from None
