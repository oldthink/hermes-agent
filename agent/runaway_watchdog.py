"""Per-session runaway watchdog (local patch 0002 — this install only, not upstream).

Background-process completions, watch matches, /loop ticks and heartbeats are injected as
synthetic user-role prompts; each one CLAIMS a new agent turn when the session is idle. The
per-turn iteration cap resets on every such wake, so a notification chain can run unbounded
(session c71d14, 2026-10-09). This module bounds that chain per session: after
``max_synthetic_wakes`` claimed auto-turns (default 25) or ``max_silent_hours`` (default 4h,
monotonic clock) without a genuine user message, wake sites hold new claims. Held events
requeue under patch-0001 semantics and batch-deliver after the next user message; a genuine
user submit (or /stop + a message) releases the hold via ``note_user_message``.

Process-local registry keyed by session id: a restart resets all counters, and a session
served by two processes (e.g. gateway + desktop) keeps one counter per process — degraded
(split bound), never stuck. PER-SESSION scope by design: never touches the global estop,
cron, kanban, or discord.

Limits: ``agent:`` config keys ``runaway_max_synthetic_wakes`` / ``runaway_max_silent_hours``,
overridden by env ``HERMES_RUNAWAY_MAX_WAKES`` / ``HERMES_RUNAWAY_MAX_SILENT_HOURS`` (testing).
Any config/env error falls back to the defaults.

Counter semantics (amendment A3): ``should_allow`` is a PURE check — call sites do
check -> claim -> ``note_claim_success`` ONLY when the claim actually succeeded.
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("agent.runaway_watchdog")

_DEFAULT_MAX_WAKES = 25
_DEFAULT_MAX_SILENT_HOURS = 4.0
_CFG_CACHE_TTL_S = 5.0

_lock = threading.Lock()
# key -> {"synthetic_wakes": int, "last_user_monotonic": float, "held_notified": bool}
_states: dict = {}
_cfg_cache: tuple[float, int, float] = (0.0, _DEFAULT_MAX_WAKES, _DEFAULT_MAX_SILENT_HOURS)


def _limits() -> tuple:
    """(max_synthetic_wakes, max_silent_hours): config first (5s TTL cache), env overrides, defaults on error."""
    global _cfg_cache
    now = time.monotonic()
    cached_at, max_wakes, max_silent_hours = _cfg_cache
    if now - cached_at >= _CFG_CACHE_TTL_S:
        max_wakes = _DEFAULT_MAX_WAKES
        max_silent_hours = _DEFAULT_MAX_SILENT_HOURS
        try:
            from hermes_cli.config import cfg_get, load_config_readonly
            cfg = load_config_readonly()
            wakes = cfg_get(cfg, "agent", "runaway_max_synthetic_wakes")
            if isinstance(wakes, (int, float)) and not isinstance(wakes, bool) and wakes > 0:
                max_wakes = int(wakes)
            hours = cfg_get(cfg, "agent", "runaway_max_silent_hours")
            if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0:
                max_silent_hours = float(hours)
        except Exception:
            pass
        _cfg_cache = (now, max_wakes, max_silent_hours)
    try:
        env_wakes = os.environ.get("HERMES_RUNAWAY_MAX_WAKES", "").strip()
        if env_wakes:
            max_wakes = max(1, int(env_wakes))
    except (TypeError, ValueError):
        pass
    try:
        env_hours = os.environ.get("HERMES_RUNAWAY_MAX_SILENT_HOURS", "").strip()
        if env_hours:
            max_silent_hours = max(0.0, float(env_hours))
    except (TypeError, ValueError):
        pass
    return max_wakes, max_silent_hours


def _fresh_state() -> dict:
    return {"synthetic_wakes": 0, "last_user_monotonic": time.monotonic(), "held_notified": False}


def register(key: str) -> None:
    """Lazy-init a session's state (first registration starts the silent-hours clock)."""
    if not key:
        return
    with _lock:
        if key not in _states:
            _states[key] = _fresh_state()


def note_user_message(key: str) -> None:
    """A genuine user message arrived: reset the counter and clear the hold notice."""
    if not key:
        return
    with _lock:
        _states[key] = _fresh_state()


def should_allow(key: str, component: str) -> bool:
    """Pure check: True while the session is under both limits; False (held) once tripped.

    Does NOT increment — callers increment via ``note_claim_success`` only after their claim
    actually succeeds. The first trip of an episode logs a one-shot notice (``held_notified``
    latch, cleared by ``note_user_message``); later checks stay silent.
    """
    if not key:
        return True
    max_wakes, max_silent_hours = _limits()
    with _lock:
        state = _states.get(key)
        if state is None:
            state = _states[key] = _fresh_state()
        tripped = (
            state["synthetic_wakes"] >= max_wakes
            or (time.monotonic() - state["last_user_monotonic"]) > max_silent_hours * 3600.0
        )
        if not tripped:
            return True
        if not state["held_notified"]:
            state["held_notified"] = True
            logger.warning(
                "session held by runaway watchdog (%s): %d auto-turns without user input "
                "(limit %d turns / %.1f h silent) — send a message or /stop; "
                "held notifications redeliver after the next user message",
                component, state["synthetic_wakes"], max_wakes, max_silent_hours,
            )
        return False


def note_claim_success(key: str) -> None:
    """A synthetic wake actually claimed a turn: count it (call only on successful claims)."""
    if not key:
        return
    with _lock:
        state = _states.get(key)
        if state is None:
            state = _states[key] = _fresh_state()
        state["synthetic_wakes"] += 1


def synthetic_wake_count(key: str) -> int:
    """Current claimed auto-turn count for ``key`` — for the TUI hold status line (A4)."""
    if not key:
        return 0
    with _lock:
        state = _states.get(key)
        return int(state["synthetic_wakes"]) if state is not None else 0
