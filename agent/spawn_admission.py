"""Spawn admission guard (local patch 0005 — this install only, not upstream).

Caps concurrent hermes-worker child scopes per user and refuses spawns below a
MemAvailable floor, BEFORE the registry Popen. The incident (2026-10-16 06:31,
spawn-oom-2026-10-16/REPORT.md): a resumed turn re-executed a scratch-venv python
battery — 310 processes / 35 concurrent x ~0.7 GB — because no layer bounded
concurrent process fan-out (patches 0001/0002/0004 bind wake chains, dispatch and
stale wakes; none cap fan-out).

Design (spawn-oom-remediation task 4, design note in
Documents/.hermes/notepads/spawn-oom-remediation/design-note-0005.md):

- The live count is the number of RUNNING ``hermes-worker-*`` user systemd scopes.
  Every terminal/process child already lands in its own scope
  (tools/process_registry.py::_build_systemd_scope_argv, --collect), so systemd is
  the cross-process, crash-recovery-free counter: a dead holder's scope disappears
  with it. No lockfile, no TTL, no holder-liveness code.
- The memory floor reuses tools/bot_desktop/resources.py::memory_info(), which
  already folds cgroup headroom into ``available_mb`` where a limit exists; on
  installs without a cgroup limit the reading is host MemAvailable.
- Refusal is a NORMAL tool error (failure-tolerant class) naming the guard and the
  remedy — never a hard-stop, never a silent drop.
- Seams for tests: ``_set_admission_overrides(scope_count=..., meminfo_reader=...)``
  pins the readings so no test ever touches real RAM or spawns real children.

Config: ``tools.max_spawn_running`` (default 12), ``tools.spawn_mem_floor_mb``
(default 4096); env overrides HERMES_MAX_SPAWN_RUNNING / HERMES_SPAWN_MEM_FLOOR_MB.
0 disables the respective check (fail-open on explicit operator choice only).
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
import subprocess
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SPAWN_RUNNING = 12
_DEFAULT_MEM_FLOOR_MB = 4096

# Test seam: pinned (scope_count, meminfo_reader). Never set in production code.
_admission_overrides: dict[str, Any] = {}


def _set_admission_overrides(scope_count: Optional[int] = None,
                             meminfo_reader: Optional[Callable[[], Optional[int]]] = None) -> None:
    """Test seam: pin the scope count and/or a meminfo reader (returns available MiB)."""
    _admission_overrides.clear()
    if scope_count is not None:
        _admission_overrides["scope_count"] = scope_count
    if meminfo_reader is not None:
        _admission_overrides["meminfo_reader"] = meminfo_reader


def _int_env(name: str) -> Optional[int]:
    raw = os.environ.get(name, "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("spawn admission: env %s=%r is not an integer; ignoring", name, raw)
        return None


def _resolve_limit(config_get: Callable[[str, Any], Any], env_name: str, config_key: str, default: int) -> int:
    """Config value -> env override -> default. 0 = disabled (explicit operator choice)."""
    try:
        val = config_get(config_key, None)
        if val is None:
            val = default
    except Exception:
        val = default
    env_val = _int_env(env_name)
    if env_val is not None:
        val = env_val
    if isinstance(val, bool):
        logger.warning("spawn admission: %s=%r is a bool; using default %d", env_name, val, default)
        return default
    try:
        ival = int(val)
    except (TypeError, ValueError):
        return default
    if ival < 0:
        logger.warning("spawn admission: %s=%r is negative; using default %d", env_name, val, default)
        return default
    return ival


def _running_worker_scopes() -> int:
    """Count RUNNING hermes-worker-* user scopes (machine-wide for this user)."""
    if "scope_count" in _admission_overrides:
        return int(_admission_overrides["scope_count"])
    binary = shutil.which("systemctl")
    if binary is None:
        return 0  # no systemd user manager visible: fail-open on the count
    probe_env = None
    try:
        from tools.process_registry import systemd_user_bus_env  # noqa: PLC0415
        probe_env = systemd_user_bus_env() or {}
    except Exception:
        # Import-time dep chain unavailable (e.g. minimal test runtime): derive the two
        # standard vars ourselves so a system-unit deployment still reaches the bus.
        _rd = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        _bus = Path(_rd) / "bus"
        try:
            if _bus.is_socket():
                probe_env = dict(os.environ)
                probe_env["XDG_RUNTIME_DIR"] = _rd
                probe_env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={_bus}"
        except OSError:
            pass
    try:
        out = subprocess.run(
            [binary, "--user", "list-units", "hermes-worker-*.scope", "--state=running",
             "--plain", "--no-legend", "--no-pager"],
            capture_output=True, text=True, timeout=10, check=False,
            env=probe_env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("spawn admission: scope count probe failed (%s); failing open", exc)
        return 0
    if out.returncode != 0:
        logger.warning("spawn admission: systemctl rc=%d; failing open", out.returncode)
        return 0
    # Terminal/process children only: cron-*/kanban-* worker scopes are dispatched by
    # their own schedulers with their own bounds and are NOT waitable by this tool's
    # operator — counting them would let a cron burst starve terminal spawns.
    return sum(1 for line in out.stdout.splitlines()
               if line.strip().startswith("hermes-worker-")
               and not line.strip().startswith(("hermes-worker-cron-", "hermes-worker-kanban-")))


def _effective_available_mb() -> Optional[int]:
    """Caller-effective available MiB: min(host MemAvailable, cgroup headroom) when limited.

    Uses tools.bot_desktop.resources.memory_info() (host-vs-cgroup aware). Returns
    None when the reading is unavailable (fail-open on the floor).
    """
    if "meminfo_reader" in _admission_overrides:
        return _admission_overrides["meminfo_reader"]()
    try:
        from tools.bot_desktop.resources import memory_info
    except Exception:
        return None
    try:
        info = memory_info()
    except Exception as exc:
        logger.warning("spawn admission: memory_info() failed (%s); failing open", exc)
        return None
    if info is None or getattr(info, "available_mb", None) is None:
        return None
    # resources.memory_info() already returns min(host MemAvailable, cgroup headroom)
    return int(info.available_mb)


class SpawnRefused(Exception):
    """Raised before Popen when admission fails. Message is user-facing tool text."""


def admit_spawn(command: str, *, config_get: Optional[Callable[[str, Any], Any]] = None) -> None:
    """Raise SpawnRefused when the spawn must not be admitted; return otherwise.

    Called at the TOP of the registry's local spawn paths (before any Popen), so a
    refusal costs nothing and consumes no slot. Fail-open policy: probe failures
    admit (the 45s swarm guard is the backstop, not this guard).
    """
    if config_get is None:
        def config_get(key: str, default: Any = None) -> Any:
            """House pattern (process_registry._config_value): hermes_cli.config
            cfg_get + read_raw_config + DEFAULT_CONFIG, with a hard fallback so a
            broken config file never crashes the spawn path."""
            try:
                from hermes_cli.config import DEFAULT_CONFIG, cfg_get, read_raw_config
                section, _, ck = key.partition(".")
                val = cfg_get(read_raw_config(), section, ck)
                if val is None:
                    return DEFAULT_CONFIG.get(section, {}).get(ck, default)
                return val
            except Exception:
                return default

    max_running = _resolve_limit(config_get, "HERMES_MAX_SPAWN_RUNNING",
                                 "tools.max_spawn_running", _DEFAULT_MAX_SPAWN_RUNNING)
    floor_mb = _resolve_limit(config_get, "HERMES_SPAWN_MEM_FLOOR_MB",
                              "tools.spawn_mem_floor_mb", _DEFAULT_MEM_FLOOR_MB)

    if max_running > 0:
        running = _running_worker_scopes()
        if running >= max_running:
            raise SpawnRefused(
                f"spawn refused: {running} hermes-worker scope(s) already running "
                f"(cap {max_running} via tools.max_spawn_running/HERMES_MAX_SPAWN_RUNNING) — "
                f"wait for children to exit, check `systemctl --user list-units 'hermes-worker-*'` "
                f"(count excludes cron-*/kanban-* workers), or raise the cap deliberately. "
                f"Command not started: {command[:120]}"
            )

    if floor_mb > 0:
        avail = _effective_available_mb()
        if avail is not None and avail < floor_mb:
            raise SpawnRefused(
                f"spawn refused: effective MemAvailable {avail} MiB is below the "
                f"{floor_mb} MiB floor (tools.spawn_mem_floor_mb/HERMES_SPAWN_MEM_FLOOR_MB) — "
                f"free memory before starting new children. Command not started: {command[:120]}"
            )
