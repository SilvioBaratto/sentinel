"""Typer CLI for Sentinel — install/uninstall/start/stop/status + hidden run."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import typer

from sentinel import fmt

app = typer.Typer(
    help="Sentinel — macOS resource governor",
    no_args_is_help=True,
)


# ── Status view (plain data; produced by _build_status_reporter) ─────────────


@dataclass
class _StatusView:
    pressure_level: int
    pressure_label: str
    swap_used_bytes: int
    disk_free_bytes: int
    recent_actions: list[dict] = field(default_factory=list)
    idle_candidates: list[str] = field(default_factory=list)
    audit_log_tail: list[str] = field(default_factory=list)


# ── Injectable factory seams (patched by tests) ───────────────────────────────


def _build_controller():
    from sentinel.config_store import JsonConfigStore  # noqa: PLC0415
    from sentinel.service.controller import LaunchctlServiceController  # noqa: PLC0415

    store = JsonConfigStore()
    config = store.load()
    return LaunchctlServiceController(config=config.service, paths=store.paths())


def _build_daemon():
    from sentinel.advisor.ollama import OllamaAdvisor  # noqa: PLC0415
    from sentinel.config_store import JsonConfigStore  # noqa: PLC0415
    from sentinel.detection import build_detection  # noqa: PLC0415
    from sentinel.docker.port_discovery import DockerPortDiscoverer  # noqa: PLC0415
    from sentinel.docker.wake_proxy import build_wake_proxy  # noqa: PLC0415
    from sentinel.execute import build_executor  # noqa: PLC0415
    from sentinel.pipeline import build_pipeline  # noqa: PLC0415
    from sentinel.service.daemon import build_daemon  # noqa: PLC0415

    store = JsonConfigStore()
    app_cfg = store.load()
    pipeline = build_pipeline(app_cfg.monitor)
    detection = build_detection(app_cfg.monitor)
    engine = build_executor(app_cfg.execute)
    advisor = OllamaAdvisor(app_cfg.advisor)
    port_disc = DockerPortDiscoverer()
    wake_mgr = build_wake_proxy(app_cfg.wake)
    return build_daemon(
        app_cfg.service,
        pipeline=pipeline,
        detect=detection.detect,
        advisor=advisor,
        engine=engine,
        port_discoverer=port_disc,
        wake_manager=wake_mgr,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )


def _build_status_reporter() -> _StatusView:
    from pathlib import Path  # noqa: PLC0415

    from sentinel.config_store import JsonConfigStore  # noqa: PLC0415
    from sentinel.detection import build_detection  # noqa: PLC0415
    from sentinel.pipeline import build_pipeline  # noqa: PLC0415
    from sentinel.service.status_provider import DefaultStatusProvider  # noqa: PLC0415

    store = JsonConfigStore()
    app_cfg = store.load()
    paths = store.paths()
    pipeline = build_pipeline(app_cfg.monitor)
    detection = build_detection(app_cfg.monitor)
    provider = DefaultStatusProvider(
        sampler=pipeline._sampler,
        detection=detection,
        snapshot_path=Path(paths.state_path),
        audit_log_path=Path(paths.audit_log_path),
    )
    report = provider.build()
    return _status_view_from_report(report, paths)


# ── CLI commands ──────────────────────────────────────────────────────────────


@app.command()
def install() -> None:
    """Install the Sentinel LaunchAgent."""
    ctrl = _build_controller()
    ctrl.install()
    typer.echo("Sentinel installed.")


@app.command()
def uninstall() -> None:
    """Uninstall the Sentinel LaunchAgent."""
    ctrl = _build_controller()
    ctrl.uninstall()
    typer.echo("Sentinel uninstalled.")


@app.command()
def start() -> None:
    """Start the Sentinel service."""
    ctrl = _build_controller()
    ctrl.start()
    typer.echo("Sentinel started.")


@app.command()
def stop() -> None:
    """Stop the Sentinel service."""
    ctrl = _build_controller()
    ctrl.stop()
    typer.echo("Sentinel stopped.")


@app.command()
def status() -> None:
    """Show current Sentinel status."""
    reporter = _build_status_reporter()
    _render_status(reporter)


@app.command(hidden=True)
def run() -> None:
    """Run the Sentinel daemon (launchd entrypoint)."""
    daemon = _build_daemon()
    daemon.run()


# ── Rendering ─────────────────────────────────────────────────────────────────


def _render_status(reporter: _StatusView) -> None:
    typer.echo(f"Pressure: {reporter.pressure_level} ({reporter.pressure_label})")
    typer.echo(f"Swap used: {fmt.format_bytes(reporter.swap_used_bytes)}")
    typer.echo(f"Disk free: {fmt.format_bytes(reporter.disk_free_bytes)}")
    typer.echo("")
    _render_actions(reporter.recent_actions)
    _render_candidates(reporter.idle_candidates)
    _render_audit(reporter.audit_log_tail)


def _render_actions(actions: list[dict]) -> None:
    typer.echo("Recent actions:")
    for action in actions:
        rev = "reversible" if action.get("reversible") else "permanent"
        typer.echo(f"  {action.get('description', '')} — {rev}")
    typer.echo("")


def _render_candidates(candidates: list[str]) -> None:
    typer.echo("Idle candidates:")
    for name in candidates:
        typer.echo(f"  {name}")
    typer.echo("")


def _render_audit(lines: list[str]) -> None:
    typer.echo("Audit log:")
    for line in lines:
        typer.echo(f"  {line}")


# ── Private composition helpers ───────────────────────────────────────────────


def _status_view_from_report(report: object, paths: object) -> _StatusView:
    pressure = getattr(report, "pressure", None)
    swap = getattr(report, "swap", None)
    disks = getattr(report, "disks", ())
    return _StatusView(
        pressure_level=int(pressure) if pressure is not None else 0,
        pressure_label=pressure.name if pressure is not None else "UNKNOWN",
        swap_used_bytes=int(getattr(swap, "used_bytes", 0)),
        disk_free_bytes=int(disks[0].free_bytes) if disks else 0,
        recent_actions=_map_actions(getattr(report, "recent_actions", ())),
        idle_candidates=_map_candidates(report),
        audit_log_tail=_read_audit_tail(paths),
    )


def _map_actions(actions: tuple) -> list[dict]:
    result = []
    for a in actions:
        result.append(
            {
                "description": getattr(a, "target", ""),
                "reversible": bool(getattr(a, "reversibility", False)),
                "bytes": int(getattr(a, "bytes_freed", 0)),
            }
        )
    return result


def _map_candidates(report: object) -> list[str]:
    names: list[str] = []
    for p in getattr(report, "idle_processes", ()):
        names.append(getattr(p.info, "name", str(p)))
    for c in getattr(report, "idle_containers", ()):
        names.append(getattr(c, "name", str(c)))
    return names


def _read_audit_tail(paths: object) -> list[str]:
    from pathlib import Path  # noqa: PLC0415

    path = Path(getattr(paths, "audit_log_path", ""))
    try:
        lines = [
            ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        return lines[-20:] if len(lines) > 20 else lines
    except OSError:
        return []


# Entry point for `python -m sentinel.cli ...` — the exact form the LaunchAgent
# plist invokes (see service/controller.py _program_args). Without this, `-m`
# imports the module but never calls app(), so `... run` silently no-ops and the
# daemon never starts. The console_scripts entry point (sentinel.cli:app) calls
# app() itself, so this guard is what makes the two launch paths equivalent.
if __name__ == "__main__":
    app()
