"""Composition root for the execution subsystem.

build_executor is the single place all real adapters (or injected fakes)
are wired together, mirroring build_detection from detection.py.

All heavy OS imports live inside _os_components() — a top-level import of
sentinel.execute never loads psutil or spawns a subprocess.
"""

from __future__ import annotations

import logging

from sentinel.config import ExecuteConfig, ExecutionMode
from sentinel.execute.engine import ExecutionEngine
from sentinel.notify.notifier import MacNotifier, NullNotifier


def build_executor(
    config: ExecuteConfig,
    *,
    components: dict | None = None,
    audit_log_path: str | None = None,
) -> ExecutionEngine:
    """Wire real adapters (or injected fakes) into an ExecutionEngine.

    ``audit_log_path`` is the caller-resolved fallback used when
    ``config.audit_log_path`` is unset.  The composition root passes the same
    path the status reader tails, so writer and reader cannot diverge.
    """
    if components is not None:
        return _from_components(config, components)
    return _from_os(config, audit_log_path)


# ── private wiring helpers ────────────────────────────────────────────────────


def _from_components(config: ExecuteConfig, components: dict) -> ExecutionEngine:
    notifier = components.get("notifier") or _select_notifier(config)
    return ExecutionEngine(
        killer=components["killer"],
        stopper=components["stopper"],
        cleaner=components["cleaner"],
        audit=components["audit"],
        notifier=notifier,
        mode=config.mode,
    )


def _from_os(
    config: ExecuteConfig, audit_log_path: str | None = None
) -> ExecutionEngine:
    killer, stopper, cleaner, audit = _os_components(config, audit_log_path)
    return ExecutionEngine(
        killer=killer,
        stopper=stopper,
        cleaner=cleaner,
        audit=audit,
        notifier=_select_notifier(config),
        mode=config.mode,
    )


def _select_notifier(config: ExecuteConfig) -> object:
    """MacNotifier for AUTO + enabled; NullNotifier for everything else."""
    if config.mode == ExecutionMode.AUTO and config.notify.enabled:
        return MacNotifier(config=config.notify)
    return NullNotifier()


def _os_components(config: ExecuteConfig, audit_log_path: str | None = None) -> tuple:
    """Construct real OS adapters; all heavy imports are deferred to this body."""
    import time  # noqa: PLC0415

    from sentinel.docker.allow_list import ContainerAllowList  # noqa: PLC0415
    from sentinel.execute.activity_guard import ProjectActivityGuard  # noqa: PLC0415
    from sentinel.execute.cleanup_rule import default_rules  # noqa: PLC0415
    from sentinel.execute.deny_list_path_guard import DenyListPathGuard  # noqa: PLC0415
    from sentinel.execute.disk_cleaner import RuleBasedDiskCleaner  # noqa: PLC0415
    from sentinel.execute.docker_stopper import DockerContainerStopper  # noqa: PLC0415
    from sentinel.execute.file_shims import FileManagerTrasher, OsRemoveDeleter  # noqa: PLC0415
    from sentinel.execute.os_shims import (  # noqa: PLC0415
        NSRunningAppQuitter,
        PosixAliveProbe,
        PosixProcessSignaler,
    )
    from sentinel.execute.verified_killer import VerifiedKiller  # noqa: PLC0415

    quitter = NSRunningAppQuitter()
    probe = PosixAliveProbe()
    signaler = PosixProcessSignaler()

    def _send_quit(pid: int) -> None:
        quitter.quit(pid)

    def _send_signal(pid: int, sig: int) -> None:
        signaler.signal(pid, sig)

    def _read_create_time(pid: int) -> float | None:
        import psutil  # noqa: PLC0415 — deferred

        try:
            return psutil.Process(pid).create_time()
        except Exception:
            return None

    killer = VerifiedKiller(
        config.kill,
        quit_sender=_send_quit,
        alive_checker=probe.is_alive,
        signal_sender=_send_signal,
        sleeper=time.sleep,
        create_time_reader=_read_create_time,
    )

    stopper = DockerContainerStopper(
        allow_list=ContainerAllowList(),
    )

    cleaner = RuleBasedDiskCleaner(
        rules=default_rules(config.cleanup),
        path_guard=DenyListPathGuard(config=config.cleanup),
        activity_guard=ProjectActivityGuard(config=config.cleanup),
        trasher=FileManagerTrasher(),
        deleter=OsRemoveDeleter(),
    )

    audit = _make_audit_logger(config, audit_log_path)

    return killer, stopper, cleaner, audit


def _make_audit_logger(config: ExecuteConfig, fallback_path: str | None = None):
    """Build the audit sink; a NullHandler only when no path is resolvable at all.

    Opening the file must never crash the process: the LaunchAgent runs under
    KeepAlive.Crashed, so a raising RotatingFileHandler constructor would turn
    an unwritable directory into a restart loop.
    """
    import os  # noqa: PLC0415

    from sentinel.execute.audit import RotatingAuditLogger  # noqa: PLC0415

    path = config.audit_log_path or fallback_path
    if not path:
        return RotatingAuditLogger(handler=logging.NullHandler())
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        return RotatingAuditLogger(
            log_path=path,
            max_bytes=config.rotate_max_bytes or 10 * 1024 * 1024,
            backups=config.rotate_backups or 5,
        )
    except OSError:
        return RotatingAuditLogger(handler=logging.NullHandler())
