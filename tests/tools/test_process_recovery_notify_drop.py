"""t_695b4b52 — silent notify drops when the registry's owner process dies.

Covers the three fixes:
  1. ``save_completed_result`` never raises out of ``_move_to_finished``
     (deliberately-undeliverable receipt = failure control; the wake must
     still be queued).
  2. A checkpointed notify-on-complete child whose PID is already gone at
     recovery time gets a receipt + completion wake (previously: silently
     pruned — THE reported bug).
  3. A recovered ALIVE child gets its exit observed by the detached sweeper
     even with no gateway watcher loop (serve/desktop backend shape).
  4. The serve-backend lifespan runs checkpoint recovery (unit-level: the
     helper exists and the recovery thread is spawned).
"""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.process_registry import ProcessRegistry, ProcessSession


@pytest.fixture()
def registry():
    return ProcessRegistry()


def _checkpoint_entry(session_id, pid, *, notify=True, started=None, command="sleep 5"):
    return {
        "session_id": session_id,
        "command": command,
        "pid": pid,
        "pid_scope": "host",
        "host_start_time": None,
        "systemd_unit": "",
        "cwd": "/tmp",
        "started_at": started if started is not None else time.time(),
        "task_id": "t1",
        "owner_task_id": "t1",
        "session_key": "chat-1",
        "notify_on_complete": notify,
        "watcher_interval": 0,
        "parent_session_id": "owner-session",
        "persist_on_release": True,
    }


# ── 1. Receipt failure must never take the wake with it ─────────────────────


def test_undeliverable_receipt_still_queues_wake(registry, tmp_path, monkeypatch):
    """Failure control: an unwritable process-results dir (non-OSError path
    included) logs and keeps delivery alive; the completion event is queued."""
    def _boom(session):
        raise ValueError("deliberately undeliverable receipt (failure control)")

    # process_registry imported the symbol into its own namespace at module load.
    monkeypatch.setattr("tools.process_registry.save_completed_result", _boom)

    s = ProcessSession(
        id="proc_undeliverable01", command="echo x", task_id="t1",
        started_at=time.time(), notify_on_complete=True,
    )
    s.exited = True
    s.exit_code = 0
    registry._running[s.id] = s
    with patch.object(registry, "_write_checkpoint"):
        registry._move_to_finished(s)

    assert registry.completion_queue.qsize() == 1
    evt = registry.completion_queue.get_nowait()
    assert evt["type"] == "completion"
    assert evt["session_id"] == "proc_undeliverable01"


def test_save_completed_result_swallows_non_oserror(monkeypatch, tmp_path):
    """Direct unit: ValueError inside save_completed_result's write path is
    logged, not raised (the module's own try now catches Exception)."""
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from tools import process_registry_results as receipts

    token = set_hermes_home_override(tmp_path)
    try:
        s = ProcessSession(
            id="proc_receiptraise001", command="echo x", task_id="t1",
            started_at=time.time(), exited=True, exit_code=3,
        )
        # The module calls utils.atomic_json_write by its imported name.
        with patch("tools.process_registry_results.atomic_json_write",
                   side_effect=ValueError("surrogate!")):
            receipts.save_completed_result(s)  # must not raise
        assert not (tmp_path / "logs" / "process-results" / f"{s.id}.json").exists()
    finally:
        reset_hermes_home_override(token)


# ── 2. Recovery-time unobserved exit: receipt + wake ────────────────────────


def test_recovery_publishes_unobserved_exit_for_notify_child(registry, tmp_path, monkeypatch):
    """The reported bug: child dies while its owner process is gone; the
    successor's recovery sees a dead PID and used to prune silently. Now it
    writes the receipt and queues the completion."""
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override

    token = set_hermes_home_override(tmp_path)
    try:
        checkpoint = tmp_path / "processes.json"
        # A PID that is provably dead: spawn and reap a real short-lived process.
        import subprocess, sys, os
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        entry = _checkpoint_entry("proc_deadnotify0001", dead.pid)
        checkpoint.write_text(json.dumps([entry]), encoding="utf-8")

        with patch("tools.process_registry.CHECKPOINT_PATH", checkpoint):
            recovered = registry.recover_from_checkpoint()

        assert recovered == 0  # not adopted as running — it is dead
        receipt = tmp_path / "logs" / "process-results" / "proc_deadnotify0001.json"
        assert receipt.exists(), "unobserved exit must leave a durable receipt"
        record = json.loads(receipt.read_text(encoding="utf-8"))
        assert record["notify_on_complete"] is True
        assert record["termination_source"] == "recovery_unobserved"
        assert registry.completion_queue.qsize() == 1
        evt = registry.completion_queue.get_nowait()
        assert evt["type"] == "completion"
        assert evt["session_id"] == "proc_deadnotify0001"
        assert evt["exit_code"] is None
        assert "unobserved" in evt["note"].lower()
    finally:
        reset_hermes_home_override(token)


def test_recovery_silent_child_stays_silent(registry, tmp_path):
    """notify_on_complete absent → no receipt, no wake (a silent server/watcher
    must not fabricate completions)."""
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override

    token = set_hermes_home_override(tmp_path)
    try:
        import subprocess, sys
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        checkpoint = tmp_path / "processes.json"
        entry = _checkpoint_entry("proc_deadsilent0001", dead.pid, notify=False)
        checkpoint.write_text(json.dumps([entry]), encoding="utf-8")

        with patch("tools.process_registry.CHECKPOINT_PATH", checkpoint):
            registry.recover_from_checkpoint()

        assert not (tmp_path / "logs" / "process-results" / "proc_deadsilent0001.json").exists()
        assert registry.completion_queue.empty()
    finally:
        reset_hermes_home_override(token)


def test_recovery_skips_when_receipt_already_exists(registry, tmp_path):
    """An exit observed before the owner died already has its receipt; recovery
    must not fabricate an unknown-code duplicate wake."""
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override

    token = set_hermes_home_override(tmp_path)
    try:
        import subprocess, sys
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        session_id = "proc_observedexit001"
        results = tmp_path / "logs" / "process-results"
        results.mkdir(parents=True, exist_ok=True)
        (results / f"{session_id}.json").write_text("{}", encoding="utf-8")

        checkpoint = tmp_path / "processes.json"
        entry = _checkpoint_entry(session_id, dead.pid)
        checkpoint.write_text(json.dumps([entry]), encoding="utf-8")

        with patch("tools.process_registry.CHECKPOINT_PATH", checkpoint):
            registry.recover_from_checkpoint()

        assert registry.completion_queue.empty()
        assert json.loads((results / f"{session_id}.json").read_text()) == {}
    finally:
        reset_hermes_home_override(token)


# ── 3. Alive recovered child: the sweeper observes its exit ─────────────────


def test_detached_sweeper_publishes_exit_without_gateway_watcher(registry, tmp_path):
    """Serve-backend shape: recovered ALIVE child, no watcher loop anywhere.
    The detached sweeper must notice the PID's death and publish receipt+wake."""
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override

    token = set_hermes_home_override(tmp_path)
    try:
        import subprocess, sys
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        checkpoint = tmp_path / "processes.json"
        entry = _checkpoint_entry("proc_aliverecover001", child.pid)
        checkpoint.write_text(json.dumps([entry]), encoding="utf-8")

        with patch("tools.process_registry.CHECKPOINT_PATH", checkpoint):
            recovered = registry.recover_from_checkpoint()
        assert recovered == 1
        assert registry._detached_sweeper_thread is not None

        child.kill()
        child.wait()
        # Drive the sweeper synchronously instead of sleeping a full cadence.
        registry._sweep_detached_once()

        receipt = tmp_path / "logs" / "process-results" / "proc_aliverecover001.json"
        assert receipt.exists()
        assert registry.completion_queue.qsize() == 1
        evt = registry.completion_queue.get_nowait()
        assert evt["session_id"] == "proc_aliverecover001"
        assert evt["exit_code"] is None
        assert evt["termination_source"] == "recovery_unobserved"
    finally:
        reset_hermes_home_override(token)


def test_detached_sweeper_leaves_running_child_alone(registry, tmp_path):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override

    token = set_hermes_home_override(tmp_path)
    try:
        import subprocess, sys
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            checkpoint = tmp_path / "processes.json"
            entry = _checkpoint_entry("proc_stillrunning001", child.pid)
            checkpoint.write_text(json.dumps([entry]), encoding="utf-8")
            with patch("tools.process_registry.CHECKPOINT_PATH", checkpoint):
                registry.recover_from_checkpoint()
            registry._sweep_detached_once()
            assert registry.completion_queue.empty()
            assert registry.get("proc_stillrunning001") is not None
        finally:
            child.kill()
            child.wait()
    finally:
        reset_hermes_home_override(token)


# ── 4. Serve-backend lifespan runs recovery ─────────────────────────────────


def test_serve_lifespan_declares_checkpoint_recovery():
    """The recovery call is wired in web_server._lifespan (source-level guard:
    the exact regression — serve backends never recovering — must not return)."""
    import hermes_cli.web_server as ws
    import inspect

    src = inspect.getsource(ws._lifespan)
    assert "recover_from_checkpoint" in src, (
        "serve-backend lifespan must recover the process checkpoint; without it "
        "every background child of a desktop session is orphaned across a "
        "backend respawn (silent notify drop)")
    assert callable(ws._recover_served_profile_process_checkpoints)
