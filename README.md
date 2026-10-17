# drift-sentinel

[![CI](https://github.com/0xgitpushpray/drift-sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/0xgitpushpray/drift-sentinel/actions/workflows/ci.yml)
[![Nightly drift check](https://github.com/0xgitpushpray/drift-sentinel/actions/workflows/nightly-drift.yml/badge.svg)](https://github.com/0xgitpushpray/drift-sentinel/actions/workflows/nightly-drift.yml)

A nightly bot that tells you when a router's running config has drifted from its approved version.

Every night it logs into your routers, pulls the running config, strips out noise such as timestamps and byte counts, and diffs it against the approved baseline stored in this repo. When a router has drifted, it opens a GitHub issue with the diff. If the router is changed back, it closes the issue. It can also post to Slack or Discord.

```
                 nightly (GitHub Actions cron / systemd timer)
                                   │
  routers ──ssh──► running config ─┤
                                   ├─► normalize ─► diff ─► GitHub issue per router
  approved/*.cfg (git) ────────────┘                    ├─► Slack / Discord webhook
                                                        └─► Markdown + JSON report
```

Approved baselines live in git, so approving a change means reviewing and merging a pull request.

## What a drift alert looks like

The repo ships with three demo routers. `edge-rtr-01` was "temporarily" changed by a vendor:

```diff
--- approved/edge-rtr-01
+++ running/edge-rtr-01
@@ -12,6 +12,7 @@
 username netops privilege 15 secret 9 <redacted>
+username vendor-tmp privilege 15 secret 9 <redacted>
 interface Loopback0
@@ -27,15 +28,15 @@
 interface GigabitEthernet0/0/2
- description UNUSED
- no ip address
- shutdown
+ description temp - vendor access
+ ip address 192.0.2.1 255.255.255.0
+ no shutdown
 ...
- neighbor 203.0.113.1 password 7 <redacted>
+ neighbor 203.0.113.1 password 7 <redacted>
 ...
 ip access-list extended INTERNET-IN
  10 permit tcp any any established
+ 15 permit tcp any any eq 3389
 ...
-logging host 10.99.0.20
 ...
- transport input ssh
+ transport input ssh telnet
```

`core-rtr-01` and `branch-rtr-01` differ from their baselines only in `Building configuration...`, `! Last configuration change at ...`, `ntp clock-period` and the Junos commit header, so they are reported clean.

Open the [issues labelled `config-drift`](https://github.com/0xgitpushpray/drift-sentinel/issues?q=label%3Aconfig-drift) to see the bot's output from the demo.

## Try it locally

```bash
git clone https://github.com/0xgitpushpray/drift-sentinel.git
cd drift-sentinel
pip install .
drift-sentinel check
```

## Point it at your network

1. **Use this repo as your own.** Fork it, or copy it into a **private** repo if your configs are sensitive. Delete the demo devices in `inventory.yml`, the files in `approved/` and the `examples/` folder.

2. **List your routers** in `inventory.yml`:

   ```yaml
   defaults:
     platform: cisco_ios          # any netmiko device_type
     username_env: ROUTER_USERNAME
     password_env: ROUTER_PASSWORD

   devices:
     - name: dc1-core-01
       host: 10.0.0.1
     - name: dc1-spine-01
       host: 10.0.0.11
       platform: arista_eos
     - name: br-04-srx
       host: 10.40.0.1
       platform: juniper_junos
   ```

3. **Add credentials** as repository secrets (*Settings → Secrets and variables → Actions*): `ROUTER_USERNAME`, `ROUTER_PASSWORD`, and optionally `ROUTER_ENABLE` (used only when a device sets `enable_secret_env: ROUTER_ENABLE`).

4. **Give the workflow a way to reach the routers.** GitHub-hosted runners are on the public internet, and your management network usually isn't. [Register a self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners) on a jump host, then change `runs-on` in both workflows to `[self-hosted]`. If you would rather not use Actions at all, see [Running without GitHub Actions](#running-without-github-actions).

5. **Record the first baselines.** From a host that can reach the routers:

   ```bash
   pip install ".[ssh]"
   export ROUTER_USERNAME=... ROUTER_PASSWORD=...
   drift-sentinel approve --all
   git add approved/ && git commit -m "Initial approved baselines" && git push
   ```

   Read through these files before you commit them. Whatever you commit becomes the approved config.

The nightly workflow runs at 02:17 UTC. Change the `cron` line in [`.github/workflows/nightly-drift.yml`](.github/workflows/nightly-drift.yml) to move it, or start it by hand from the Actions tab.

## How you get told

| Channel | Setup | Behaviour |
|---|---|---|
| **GitHub issue** | none (uses the workflow's `GITHUB_TOKEN`) | One issue per drifted router, titled `Config drift: <name>` and labelled `config-drift`. If the diff changes, the issue body is updated and a comment is added. If the drift stays the same, nothing is posted. When the router matches its baseline again, the issue is closed. You get GitHub's normal email or app notifications. |
| **Slack** | secret `DRIFT_WEBHOOK_URL` = an [incoming webhook](https://api.slack.com/messaging/webhooks) | A summary listing the drifted or failed routers, with a link to the run. It is sent only when something is wrong (add `--notify-clean` for a nightly all-clear). |
| **Discord** | the same secret, plus the repository variable `DRIFT_WEBHOOK_FORMAT=discord` | Same as Slack. |
| **Job status** | add `--fail-on-drift` to the workflow | Drift turns the run red, so GitHub's failed-workflow email fires. |
| **Reports** | none | A Markdown summary on the run page, plus `drift-report.md` / `drift-report.json` kept as a 30-day artifact. |

If a router can't be reached, that is reported as an **error** and the run fails (exit code 2). Drift issues are left as they are, because a failed check says nothing about whether the config is clean.

## Approving a change

A drift is either a mistake to undo or a change someone forgot to approve.

- **Undo it:** fix the router. The next nightly run closes the issue.
- **Approve it:** go to *Actions → Approve running config → Run workflow* and enter the device name (or `all`). The workflow captures the current running config and opens a pull request that updates `approved/<name>.cfg`. Review and merge it, and the next nightly run closes the issue. You can do the same locally with `drift-sentinel approve <name>`, then commit.

> The approve workflow opens its PR with `GITHUB_TOKEN`. That needs *Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests"* turned on, which is already done for this repo. PRs opened this way do not trigger CI on their own.

## Cutting out noise

Baselines and running configs are normalized before they are compared. Blank lines, trailing whitespace and CRLF line endings are removed, along with known volatile lines for each platform:

| Platform | Ignored automatically |
|---|---|
| `cisco_ios`, `cisco_xe` | `Building configuration`, `Current configuration :`, `! Last configuration change`, `! NVRAM config last updated`, `ntp clock-period`, bare `!` separators |
| `cisco_xr` | `Building configuration`, `!! IOS XR Configuration`, `!! Last configuration change`, the timestamp line |
| `cisco_nxos` | `!Command:`, `!Running configuration last done at`, `!Time:` |
| `cisco_asa` | `: Saved`, `: Written by`, `Cryptochecksum:` |
| `arista_eos` | `! Command:`, `! device:`, `! Time:`, `! boot system` |
| `juniper_junos` | `## Last commit:`, `## Last changed:`, `{master:0}`-style prompts |

To ignore anything else, add regexes, either globally or for a single device. They are matched against each line with its indentation intact:

```yaml
settings:
  ignore:
    - '^ntp clock-period'
    - '^crypto pki certificate'
devices:
  - name: lab-rtr
    host: 10.9.9.9
    ignore: ['^\s+description ']
```

Junos is collected with `show configuration | display set`, so each change shows up as a one-line diff. To collect with a different command, set `command:` on the device.

## Secrets

Passwords, enable secrets, keys, key-strings, MD5 keys, SNMP communities and pre-shared keys are replaced with `<redacted>` in every diff, issue, webhook message and report. The real values are still compared, so a changed BGP password counts as drift. It appears as a removed line and an added line that look the same except for the change marker.

If you don't want secrets in git at all, set `settings.secrets: mask`. Both sides are then redacted *before* comparison, and `drift-sentinel approve` writes redacted baselines. The trade-off is that a changed secret value is no longer detected (adding or removing a secret line still is).

Redaction is pattern-based and best effort. Read your baselines before pushing them to a public repo.

## Inventory reference

```yaml
settings:
  approved_dir: approved   # where baselines live (default: approved)
  secrets: compare         # compare | mask
  context_lines: 3         # unified diff context
  ignore: []               # global regexes

defaults: {}               # any device key below, applied to every device

devices:
  - name: r1                     # required; letters, digits, . _ -
    host: 10.0.0.1               # required for ssh
    platform: cisco_ios          # netmiko device_type (default cisco_ios)
    collector: ssh               # ssh | file | command
    port: 22
    username_env: ROUTER_USERNAME
    password_env: ROUTER_PASSWORD
    enable_secret_env: null      # env var holding the enable secret
    key_file: null               # SSH private key (password becomes optional)
    command: null                # ssh: CLI command override; command: shell command
    path: null                   # file collector: path to the saved config
    approved: null               # baseline path (default <approved_dir>/<name>.cfg)
    ignore: []                   # per-device regexes
    timeout: 60
```

**Collectors**

- `ssh` logs in with [netmiko](https://github.com/ktbyers/netmiko) (`pip install "drift-sentinel[ssh]"`). Devices are contacted in parallel (`--workers`, default 8).
- `file` reads a config that something else has already saved. Use it if Oxidized, RANCID or NAPALM already backs up your configs.
- `command` runs a shell command and treats its stdout as the config. `{name}` and `{host}` are substituted and shell-quoted, e.g. `command: ssh backup@nms cat /configs/{name}`.

## CLI

```
drift-sentinel check   [-i inventory.yml] [-d DEVICE ...] [--report FILE.md] [--json FILE]
                       [--github-issues] [--webhook URL] [--webhook-format slack|discord]
                       [--notify-clean] [--fail-on-drift] [-q] [-w WORKERS]
drift-sentinel approve [-i inventory.yml] (DEVICE ... | --all)
```

| Exit code | Meaning |
|---|---|
| 0 | no drift (or drift found without `--fail-on-drift`) |
| 1 | drift found and `--fail-on-drift` was given |
| 2 | a device couldn't be checked, a baseline is missing, the inventory is invalid, or a notification failed |

Environment variables: `DRIFT_INVENTORY` (default inventory path), `DRIFT_WEBHOOK_URL`, `DRIFT_WEBHOOK_FORMAT`, `GITHUB_TOKEN` and `GITHUB_REPOSITORY` (for `--github-issues`, set automatically in Actions).

## Running without GitHub Actions

Any scheduler works. On a Linux jump host:

```bash
sudo mkdir -p /opt/drift-sentinel && sudo chown netops /opt/drift-sentinel
git clone https://github.com/<you>/drift-sentinel.git /opt/drift-sentinel
cd /opt/drift-sentinel && python3 -m venv .venv && .venv/bin/pip install ".[ssh]"
```

Then use the systemd units in [`deploy/systemd/`](deploy/systemd/), or a cron entry:

```cron
17 2 * * *  cd /opt/drift-sentinel && git pull -q && .venv/bin/drift-sentinel check -q --webhook "$DRIFT_WEBHOOK_URL"
```

To get GitHub issues from outside Actions, export `GITHUB_TOKEN` (a fine-grained token with *Issues: read & write*) and `GITHUB_REPOSITORY=<owner>/<repo>`, then add `--github-issues`.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The code lives in `drift_sentinel/`, one module per step: `inventory` → `collectors` → `normalize`/`redaction` → `compare` → `report`/`notify`, with `cli` tying them together.

## License

[MIT](LICENSE)
