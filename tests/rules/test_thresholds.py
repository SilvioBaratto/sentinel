"""Tests for DefaultThresholdEngine behaviours not exercised by test_threshold_engine.py.

Covers:
- Asymmetric hysteresis: clearing is slower than elevating
- Disk-floor boundary: floor-1 → DISK_LOW, floor+1 → not
- CRITICAL pressure takes precedence over DISK_LOW
- Directional cooldown: escalation to a more-severe state during WARN cooldown is not blocked
"""

from __future__ import annotations

from sentinel.config import MonitorConfig
from sentinel.domain.value_objects import PressureLevel, SentinelState
from sentinel.rules.threshold import evaluate

from sentinel.domain.value_objects import DiskUsage

from tests.conftest import make_disk, make_sample

_GiB = 1024**3
_FLOOR_GiB = 20  # per MonitorConfig default


def _cfg(
    confirm: int = 2, confirm_clear: int = 4, cooldown: float = 0.0
) -> MonitorConfig:
    return MonitorConfig(
        confirm_samples=confirm,
        confirm_samples_clear=confirm_clear,
        cooldown=cooldown,
        disk_low_floor=_FLOOR_GiB * _GiB,
    )


def _samples(pressures: list[PressureLevel], disk_free_gib: float = 50.0):
    return [
        make_sample(
            timestamp=float(i),
            pressure=p,
            disks=(make_disk(free_gib=disk_free_gib),),
        )
        for i, p in enumerate(pressures)
    ]


# ---------------------------------------------------------------------------
# Asymmetric hysteresis — AC: "a single NORMAL sample during sustained WARN
# does NOT clear to NORMAL"
# ---------------------------------------------------------------------------


class TestAsymmetricHysteresis:
    def test_when_single_normal_in_warn_stream_then_state_stays_warn(self):
        """One NORMAL sample interrupting sustained WARN must not clear to NORMAL."""
        cfg = _cfg(confirm=2, confirm_clear=4)
        # Elevate to WARN with 2 confirm samples, then one NORMAL dip
        pressures = [
            PressureLevel.WARN,
            PressureLevel.WARN,  # ← WARN confirmed here
            PressureLevel.NORMAL,  # single dip — must NOT clear
        ]
        result = evaluate(_samples(pressures), cfg)
        assert result.proposed_state == SentinelState.WARN

    def test_when_confirm_samples_clear_normals_then_clears_to_normal(self):
        """confirm_samples_clear consecutive NORMAL samples DO eventually clear."""
        cfg = _cfg(confirm=2, confirm_clear=3, cooldown=0.0)
        # Elevate then clear with exactly confirm_samples_clear NORMAL samples
        pressures = (
            [PressureLevel.WARN] * 2  # confirm WARN
            + [PressureLevel.NORMAL] * 3  # confirm clear
        )
        result = evaluate(_samples(pressures), cfg)
        assert result.proposed_state == SentinelState.NORMAL

    def test_when_fewer_than_confirm_clear_normals_then_stays_warn(self):
        """confirm_samples_clear-1 NORMAL samples must NOT clear."""
        cfg = _cfg(confirm=2, confirm_clear=3, cooldown=0.0)
        pressures = (
            [PressureLevel.WARN] * 2
            + [PressureLevel.NORMAL] * 2  # one short of confirm_clear=3
        )
        result = evaluate(_samples(pressures), cfg)
        assert result.proposed_state == SentinelState.WARN


# ---------------------------------------------------------------------------
# Disk-floor boundary — AC: "disk free at floor-1 → DISK_LOW candidate;
# floor+1 → not"
# ---------------------------------------------------------------------------


class TestDiskFloor:
    def _disk_sample(self, free_bytes: int, n: int = 3):
        cfg = _cfg(confirm=n)
        disk = DiskUsage(mount="/", free_bytes=free_bytes, total_bytes=200 * _GiB)
        samples = [
            make_sample(
                timestamp=float(i),
                pressure=PressureLevel.NORMAL,
                disks=(disk,),
            )
            for i in range(n)
        ]
        return evaluate(samples, cfg)

    def test_when_disk_free_below_floor_then_disk_low(self):
        """free_bytes = floor - 1 → proposed_state == DISK_LOW."""
        floor = _FLOOR_GiB * _GiB
        result = self._disk_sample(free_bytes=floor - 1)
        assert result.proposed_state == SentinelState.DISK_LOW

    def test_when_disk_free_above_floor_then_not_disk_low(self):
        """free_bytes = floor + 1 → proposed_state == NORMAL (pressure is NORMAL)."""
        floor = _FLOOR_GiB * _GiB
        result = self._disk_sample(free_bytes=floor + 1)
        assert result.proposed_state == SentinelState.NORMAL

    def test_when_disk_free_exactly_at_floor_then_not_disk_low(self):
        """free_bytes == floor → condition is strictly less-than, so NOT DISK_LOW."""
        floor = _FLOOR_GiB * _GiB
        result = self._disk_sample(free_bytes=floor)
        assert result.proposed_state == SentinelState.NORMAL


# ---------------------------------------------------------------------------
# CRITICAL takes precedence over DISK_LOW — AC: "CRITICAL pressure takes
# precedence over DISK_LOW per documented ordering"
# ---------------------------------------------------------------------------


class TestCriticalPrecedenceOverDiskLow:
    def test_when_critical_pressure_and_disk_low_then_proposed_state_is_critical(self):
        """CRITICAL pressure overrides DISK_LOW: raw state must be CRITICAL, not DISK_LOW."""
        cfg = _cfg(confirm=2)
        floor = _FLOOR_GiB * _GiB
        samples = [
            make_sample(
                timestamp=float(i),
                pressure=PressureLevel.CRITICAL,
                disks=(
                    DiskUsage(mount="/", free_bytes=floor - 1, total_bytes=200 * _GiB),
                ),
            )
            for i in range(2)
        ]
        result = evaluate(samples, cfg)
        assert result.proposed_state == SentinelState.CRITICAL

    def test_when_warn_pressure_and_disk_low_then_proposed_state_is_warn(self):
        """WARN pressure overrides DISK_LOW (WARN severity > DISK_LOW)."""
        cfg = _cfg(confirm=2)
        floor = _FLOOR_GiB * _GiB
        samples = [
            make_sample(
                timestamp=float(i),
                pressure=PressureLevel.WARN,
                disks=(
                    DiskUsage(mount="/", free_bytes=floor - 1, total_bytes=200 * _GiB),
                ),
            )
            for i in range(2)
        ]
        result = evaluate(samples, cfg)
        assert result.proposed_state == SentinelState.WARN


# ---------------------------------------------------------------------------
# Directional cooldown — AC (from issue comment): "a CRITICAL candidate
# confirmed during a WARN cooldown still flips immediately"
# ---------------------------------------------------------------------------


class TestDirectionalCooldown:
    def test_when_critical_arrives_during_warn_cooldown_then_escalates_immediately(
        self,
    ):
        """Escalation to a strictly more-severe state must NOT be blocked by cooldown.

        Sequence: WARN confirmed → cooldown starts → CRITICAL samples arrive →
        CRITICAL must be confirmed without waiting for cooldown to expire.
        """
        cooldown = 300.0
        cfg = _cfg(confirm=2, confirm_clear=4, cooldown=cooldown)

        # Timestamps: WARN confirms at t=1, CRITICAL arrives at t=2 and t=3
        # (well within the 300 s cooldown)
        pressures = [
            PressureLevel.WARN,
            PressureLevel.WARN,  # WARN confirmed at t=1
            PressureLevel.CRITICAL,
            PressureLevel.CRITICAL,  # CRITICAL confirmed at t=3
        ]
        result = evaluate(_samples(pressures), cfg)
        assert result.proposed_state == SentinelState.CRITICAL
