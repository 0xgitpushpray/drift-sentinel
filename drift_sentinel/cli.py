"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__
from .collectors import CollectionError, collect
from .compare import DRIFT, ERROR, DriftResult, compare
from .inventory import Device, Inventory, InventoryError, load_inventory
from .normalize import normalize
from .notify import GitHubIssues, NotifyError, send_webhook
from .redaction import redact
from .report import counts, render_json, render_markdown, render_text

EXIT_OK, EXIT_DRIFT, EXIT_ERROR = 0, 1, 2


def _display_path(p: Path) -> str:
    try:
        return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def check_device(device: Device, inv: Inventory) -> DriftResult:
    approved_path = _display_path(device.approved)
    if not device.approved.exists():
        return DriftResult(
            device.name, ERROR, approved_path=approved_path,
            error=f"no approved baseline at {approved_path} (run: drift-sentinel approve {device.name})",
        )
    try:
        running = collect(device)
        result = compare(
            device.name,
            device.approved.read_text(encoding="utf-8", errors="replace"),
            running,
            platform=device.platform,
            ignore=[*inv.settings.ignore, *device.ignore],
            secrets=inv.settings.secrets,
            context_lines=inv.settings.context_lines,
        )
    except (CollectionError, ValueError, OSError) as exc:
        return DriftResult(device.name, ERROR, error=str(exc), approved_path=approved_path)
    result.approved_path = approved_path
    return result


def run_checks(devices: list[Device], inv: Inventory, workers: int) -> list[DriftResult]:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda d: check_device(d, inv), devices))


def _run_url() -> str | None:
    server, repo, run_id = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None


def _write(path: str, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def cmd_check(args: argparse.Namespace) -> int:
    inv = load_inventory(args.inventory)
    devices = inv.select(args.device)
    results = run_checks(devices, inv, args.workers)
    run_url = _run_url()
    c = counts(results)

    sys.stdout.write(render_text(results, show_diff=not args.quiet))
    markdown = render_markdown(results, run_url)
    if args.report:
        _write(args.report, markdown)
    if args.json:
        _write(args.json, render_json(results))
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(markdown)

    notify_failed = False
    if args.github_issues:
        repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
        if not repo or not token:
            print("error: --github-issues needs GITHUB_REPOSITORY and GITHUB_TOKEN", file=sys.stderr)
            notify_failed = True
        else:
            api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
            try:
                for action in GitHubIssues(repo, token, api, run_url).sync(results):
                    print(f"github: {action}")
            except NotifyError as exc:
                print(f"error: {exc}", file=sys.stderr)
                notify_failed = True

    if args.webhook and (c[DRIFT] or c[ERROR] or args.notify_clean):
        try:
            send_webhook(args.webhook, args.webhook_format, results, run_url)
            print(f"webhook: notified ({args.webhook_format})")
        except NotifyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            notify_failed = True

    if c[ERROR] or notify_failed:
        return EXIT_ERROR
    if c[DRIFT] and args.fail_on_drift:
        return EXIT_DRIFT
    return EXIT_OK


def cmd_approve(args: argparse.Namespace) -> int:
    inv = load_inventory(args.inventory)
    if args.all == bool(args.devices):
        print("error: name one or more devices, or pass --all", file=sys.stderr)
        return EXIT_ERROR
    devices = inv.select(None if args.all else args.devices)

    def capture(device: Device):
        try:
            return device, collect(device), None
        except CollectionError as exc:
            return device, None, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        captured = list(pool.map(capture, devices))

    failed = False
    for device, running, error in captured:
        if error:
            print(f"ERROR  {device.name}  {error}", file=sys.stderr)
            failed = True
            continue
        # Store the normalized form so baseline reviews aren't cluttered by timestamps.
        lines = normalize(running, device.platform)
        if inv.settings.secrets == "mask":
            lines = [redact(line) for line in lines]
        device.approved.parent.mkdir(parents=True, exist_ok=True)
        before = device.approved.read_text(encoding="utf-8") if device.approved.exists() else None
        after = "\n".join(lines) + "\n"
        device.approved.write_text(after, encoding="utf-8", newline="\n")
        state = "unchanged" if before == after else ("updated" if before is not None else "created")
        print(f"{state:9}  {_display_path(device.approved)}")
    return EXIT_ERROR if failed else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-i", "--inventory", default=os.environ.get("DRIFT_INVENTORY", "inventory.yml"),
        help="inventory file (default: $DRIFT_INVENTORY or inventory.yml)",
    )
    common.add_argument("-w", "--workers", type=int, default=8, help="devices to contact in parallel (default: 8)")

    parser = argparse.ArgumentParser(
        prog="drift-sentinel",
        description="Detect routers whose running config drifted from the approved baseline.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", parents=[common], help="compare running configs against approved baselines")
    check.add_argument("-d", "--device", action="append", help="only check this device (repeatable)")
    check.add_argument("--report", metavar="FILE", help="write a Markdown report")
    check.add_argument("--json", metavar="FILE", help="write a JSON report")
    check.add_argument("--github-issues", action="store_true", help="open/update/close one GitHub issue per drifted device")
    check.add_argument(
        "--webhook", default=os.environ.get("DRIFT_WEBHOOK_URL") or None,
        help="Slack/Discord incoming webhook URL (default: $DRIFT_WEBHOOK_URL)",
    )
    check.add_argument(
        "--webhook-format", choices=("slack", "discord"),
        default=os.environ.get("DRIFT_WEBHOOK_FORMAT") or "slack", help="payload format (default: slack)",
    )
    check.add_argument("--notify-clean", action="store_true", help="also send the webhook when everything is clean")
    check.add_argument("--fail-on-drift", action="store_true", help="exit 1 when drift is found")
    check.add_argument("-q", "--quiet", action="store_true", help="print the summary only, no diffs")
    check.set_defaults(func=cmd_check)

    approve = sub.add_parser("approve", parents=[common], help="save current running config as the approved baseline")
    approve.add_argument("devices", nargs="*", help="device names")
    approve.add_argument("--all", action="store_true", help="approve every device in the inventory")
    approve.set_defaults(func=cmd_approve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except InventoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
