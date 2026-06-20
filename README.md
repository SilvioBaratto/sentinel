# sentinel

> A macOS resource governor that watches memory pressure, swap, and disk — then safely reclaims them.

[![CI](https://github.com/SilvioBaratto/sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/SilvioBaratto/sentinel/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](#requirements)

Sentinel is a lightweight background daemon for macOS. It continuously samples
kernel memory pressure, swap, and free disk, and when the machine gets squeezed
it reclaims resources the *safe* way: it gracefully quits idle GUI apps, stops
idle Docker containers, and (only when disk is low) trashes stale build
artifacts and caches. Everything is reversible-by-default, gated behind a
never-kill protection list, and written to an audit log.

When Sentinel stops a Docker stack to free RAM, it leaves a **wake proxy**
listening on that stack's published ports — the next connection transparently
restarts the stack and forwards the request. Containers sleep when idle and
wake on demand, with no change to how you reach them.

---

## Why

Developer machines die a slow death: a dozen Electron apps, three browsers with
80 tabs each, and a `docker compose` stack that hasn't served a request in two
hours — all holding RAM hostage until the system starts swapping and everything
crawls. Manually hunting and quitting things is tedious and easy to get wrong
(kill the wrong process and you lose work).

Sentinel automates that triage with conservative, auditable rules:

- **Event-driven, not aggressive** — it acts on real kernel pressure signals,
  with hysteresis so a brief spike doesn't trigger a kill spree.
- **Safe by construction** — terminals, VPNs, password managers, Docker
  infrastructure, and backup agents are on a never-kill list. Only *idle* apps
  on an explicit allow-list are candidates.
- **Reversible first** — disk cleanup moves files to the Trash, not `rm`.
- **Transparent wake** — stopping a container doesn't break access to it.

---

## Features

| Subsystem | What it does |
|-----------|--------------|
| **Monitoring** | Samples memory pressure (`sysctl kern.memorystatus_vm_pressure_level`), swap, disk, and memory on a fixed interval into a rolling history. |
| **Threshold engine** | Maps samples to a `SentinelState` (`NORMAL` / `WARN` / `CRITICAL` / `DISK_LOW`) with asymmetric hysteresis — fast to escalate, slow to de-escalate (cooldown). |
| **Idle detection** | Finds idle GUI processes (low CPU, long idle, not frontmost, HID-idle) and idle Docker containers (low CPU + no net/block I/O + no active exec session). |
| **Safe execution** | Graceful escalation: AppleScript Quit → `SIGTERM` → `SIGKILL`, each verified before the next stage. Editors get a longer grace window. |
| **Disk cleanup** | Rule-based reclaim of stale Downloads, build artifacts (`node_modules`, `.next`, `dist`, `__pycache__`, `DerivedData`), and caches — guarded by a deny-list and project-activity check. Trash by default. |
| **Wake proxy** | Per-port asyncio TCP proxy that restarts a stopped Docker stack on first connection (restart-once + health gate), then splices bytes to the live container. |
| **Advisor (optional)** | Ranks reclaim candidates via a local [Ollama](https://ollama.com) model; falls back to identity ordering on any failure. Disabled by default, no network egress. |
| **Service** | Installs as a launchd LaunchAgent; survives logout/login, runs at low I/O priority, restarts on crash. |
| **Audit** | Every action is recorded to a rotating JSONL audit log with reversibility and bytes freed. |

---

## How it works

```
                         ┌────────────────────────────────────────────────┐
                         │                SentinelDaemon                    │
                         │            (one tick per interval)               │
                         └────────────────────────────────────────────────┘
                                            │ tick()
        ┌───────────────────────────────────┼───────────────────────────────────┐
        ▼                                   ▼                                     ▼
┌───────────────┐   state      ┌───────────────────┐  candidates   ┌──────────────────────┐
│  Monitoring   │ ───────────► │     Detection     │ ────────────► │   Execution Engine   │
│  pipeline     │              │  idle processes   │               │  kill / stop / clean │
│               │              │  idle containers  │               │  (AUTO/CONFIRM/DRY)  │
│ pressure/swap │              └───────────────────┘               └──────────────────────┘
│ disk/memory   │                       ▲                                     │
│   → state     │                       │ rank (optional)                     │ stopped container
└───────────────┘              ┌────────┴────────┐                            ▼
                               │  Ollama advisor  │                  ┌──────────────────────┐
                               └─────────────────┘                  │     Wake proxy       │
                                                                    │ listen → restart →   │
                                                                    │ health → splice      │
                                                                    └──────────────────────┘
```

**State gates which actions run:**

| State | Trigger | Actions taken |
|-------|---------|---------------|
| `NORMAL` | All within bounds | None — no executor is even called. |
| `WARN` | Pressure level 2 | Kill idle processes + stop idle containers. |
| `CRITICAL` | Pressure level 4 | Kill idle processes + stop idle containers. |
| `DISK_LOW` | Free disk below floor (default 20 GiB) | The above **plus** disk cleanup (AUTO mode only). |

Each tick runs the full sense → decide → act chain synchronously; the daemon
sleeps in short slices between ticks so `SIGTERM` shuts it down promptly.

---

## Requirements

- **macOS** (uses `sysctl` memory-pressure, `osascript`, `launchd`, `FileManager` trash)
- **Python 3.11+**
- **Docker** (optional) — only needed for container detection and the wake proxy
- **Ollama** (optional) — only needed for the advisor

Runtime dependencies are minimal: [`psutil`](https://pypi.org/project/psutil/) and [`typer`](https://pypi.org/project/typer/).

---

## Installation

```bash
git clone https://github.com/SilvioBaratto/sentinel.git
cd sentinel
pip install -e .
```

This puts a `sentinel` command on your `PATH`. Then install and start the
background service:

```bash
sentinel install   # write the LaunchAgent plist
sentinel start     # load it into launchd
```

Sentinel now runs in the background and at every login.

---

## Usage

```bash
sentinel install      # Install the LaunchAgent
sentinel uninstall    # Remove the LaunchAgent
sentinel start        # Start the service
sentinel stop         # Stop the service
sentinel status       # Show current pressure, swap, disk, recent actions, audit tail
```

Example `sentinel status` output:

```
Pressure: 2 (WARN)
Swap used: 3.2 GiB
Disk free: 48.1 GiB

Recent actions:
  Slack — permanent
  myproject_web — reversible

Idle candidates:
  Google Chrome
  myproject_worker

Audit log:
  {"timestamp": ..., "kind": "kill_process", "target": "Slack", ...}
```

---

## Configuration

Config lives at `~/Library/Application Support/Sentinel/config.json` and is
loaded fail-safe — a missing or corrupt file falls back to defaults. Writes are
atomic. The same directory holds the audit log (`sentinel.audit.jsonl`) and the
runtime state snapshot (`state.json`).

Key tunables (all have safe defaults):

```jsonc
{
  "monitor": {
    "interval": 30.0,             // seconds between samples
    "disk_low_floor": 21474836480,// 20 GiB — DISK_LOW trigger
    "confirm_samples": 3,         // samples to confirm an escalation
    "confirm_samples_clear": 5,   // samples to confirm a de-escalation
    "cooldown": 300.0             // min seconds between de-escalations
  },
  "process": {
    "idle_cpu_percent": 1.0,      // below this counts as idle
    "idle_seconds": 7200.0,       // 2h idle before reapable
    "protected_names": ["Terminal", "1Password", "Mullvad VPN", "..."],
    "reap_allow_list": ["Google Chrome", "Slack", "Code", "..."]
  },
  "docker": {
    "idle_cpu_percent": 0.5,
    "idle_seconds": 7200.0,
    "always_up_prefixes": ["optimizer_"],
    "always_up_suffixes": ["_db"]
  },
  "execute": {
    "mode": "auto",               // "auto" | "confirm" | "dry_run"
    "cleanup": {
      "downloads_max_age_days": 30,
      "build_artifact_names": ["node_modules", ".next", "dist", "__pycache__", "DerivedData"],
      "deny_paths": ["/System", "/usr", "/Applications", "..."]
    }
  },
  "wake":    { "enabled": true, "health_timeout": 30.0 },
  "advisor": { "enabled": false, "model": "glm-5.2:cloud" }
}
```

### Execution modes

| Mode | Behavior |
|------|----------|
| `auto` | Execute actions immediately; audit + notify per result. |
| `dry_run` | Log "would …" for every candidate; never touch anything. |
| `confirm` | Queue planned actions for review; execute nothing automatically. |

> **Tip:** start in `dry_run` for a day to see what Sentinel *would* do before
> letting it act.

---

## Safety model

Sentinel is built to be conservative — when in doubt, it does nothing.

- **Never-kill list** — terminals (including `claude`, `ssh`, `tmux`), VPN
  clients, password managers, Docker Desktop/Colima, and backup agents
  (Time Machine, Backblaze, Arq, CCC) are always protected. Protection wins
  over every other rule.
- **Allow-list reaping** — only idle apps on an explicit allow-list (browsers,
  editors, chat apps) are ever candidates.
- **Graceful kills** — a process gets a polite Quit, then `SIGTERM`, then (only
  if it still won't die) `SIGKILL`, each stage verified. Editors get an extended
  grace window and never auto-`SIGKILL` by default.
- **Reversible disk cleanup** — files move to the Trash, not `rm`. Deny-listed
  paths (`/System`, `/Applications`, app-support dirs) are off-limits, and a
  project-activity guard skips anything with recent git activity.
- **Idle containers, not active ones** — a container with an active `exec`
  session, recent I/O, or a configured always-up prefix/suffix is left alone.
- **Audit everything** — every action, real or dry-run, lands in the JSONL
  audit log with its reversibility and bytes freed.

---

## Architecture

The codebase follows a strict ports-and-adapters (hexagonal) design built in
four cycles. Each subsystem has a single composition root (`build_*`) — the only
place real OS adapters or test fakes are wired together — and everything
downstream depends only on `Protocol`s. All heavy OS imports are deferred into
function bodies, so importing a module never spawns a subprocess or touches the
Docker daemon.

```
src/sentinel/
├── cli.py            # Typer CLI: install/uninstall/start/stop/status/run
├── config.py         # All thresholds & tunables (frozen dataclasses)
├── config_store.py   # Atomic JSON persistence + path resolution
├── pipeline.py       # Cycle 1: monitoring composition root
├── detection.py      # Cycle 2: idle-detection composition root
├── domain/           # Protocols + value objects (the shared vocabulary)
├── monitor/          # pressure / swap / disk / memory readers, rolling history
├── rules/            # threshold engine, hysteresis gate, state machine
├── process/          # process lister, classifier, frontmost & HID idle, idle detector
├── docker/           # stats/session readers, idle detector, wake proxy + manager
├── execute/          # verified killer, container stopper, disk cleaner, audit, engine
├── advisor/          # optional Ollama ranking advisor
├── notify/           # macOS notifications
└── service/          # launchd plist, launchctl controller, daemon run-loop, status
```

---

## Development

```bash
pip install -e ".[dev]"   # pytest, hypothesis, ruff, docker

ruff check src tests      # lint
pytest                    # full suite
pytest -m "not integration"   # what CI runs
```

The suite is property-based where it matters (Hypothesis) and uses injected
fakes via the composition roots — no real processes, containers, or files are
touched in unit tests. CI runs `ruff` + `pytest` on Python 3.12 against every
push and PR to `main`.

---

## License

[MIT](LICENSE) © 2026 Silvio Angelo Baratto Roldan
