"""Circuit-breaker observability contract for ``_record_task_failure``.

A breaker trip parks the card in ``blocked`` with a ``gave_up`` event; the
audit trail must also tell the truth about that state without changing who
may release it:

* the ``gave_up`` payload and the closed run's metadata carry
  ``will_retry: False`` (the post-decision view — the retry budget is spent);
* a ``status``-kind diagnostic row (``{"status": "blocked", "reason":
  "circuit_breaker"}``) lands immediately BEFORE ``gave_up`` and carries the
  run's ``retry_status`` verbatim, so ``_resume_status_from_events`` still
  reads ``gave_up`` as the newest lifecycle event and a review-phase card
  resumes to review after release;
* NEVER a ``blocked``-kind event row: ``_has_sticky_block`` reads exactly
  that kind, and a breaker trip stamped there would convert every counter
  hold into an operator-only sticky block and break retry-on-unblock;
* the release model is unchanged: raising ``max_retries``/``failure_limit``
  on a tripped card still releases it on the next ``recompute_ready``.

Plus the module-form worker argv contract: ``-P`` (``PYTHONSAFEPATH``) keeps
the spawn cwd off the child's ``sys.path`` so the pinned import root
(PYTHONPATH) wins when a worker spawns inside a hermes-agent checkout.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def conn(tmp_path: Path):
    db = kbc.connect(tmp_path / "kanban.db")
    try:
        yield db
    finally:
        db.close()


def _claimed(conn, title: str) -> str:
    tid = kb.create_task(conn, title=title, assignee="builder")
    assert kb.claim_task(conn, tid, claimer="host:test") is not None
    return tid


def _trip(conn, task_id: str, *, failure_limit: int = 1, **kwargs) -> bool:
    """Book one failure through the breaker via the spawn-path shape."""
    return kbd._record_task_failure(
        conn, task_id, "worker exploded",
        outcome="crashed", failure_limit=failure_limit,
        release_claim=True, end_run=True, **kwargs,
    )


# ---------------------------------------------------------------------------
# (a) trip emits status -> gave_up, payload carries will_retry: False
# ---------------------------------------------------------------------------


def test_breaker_trip_emits_status_row_before_gave_up_with_will_retry_false(conn) -> None:
    tid = _claimed(conn, "trip me")
    assert _trip(conn, tid, failure_limit=1) is True
    task = kb.get_task(conn, tid)
    assert task is not None and task.status == "blocked"

    events = kb.list_events(conn, tid)
    assert [e.kind for e in events[-2:]] == ["status", "gave_up"]
    status_row, gave_up = events[-2], events[-1]
    assert status_row.payload == {
        "status": "blocked",
        "reason": "circuit_breaker",
        "retry_status": "ready",
    }
    assert gave_up.payload is not None
    assert gave_up.payload["will_retry"] is False
    # The audit row shares the trip's run, not a synthetic one.
    assert status_row.run_id == gave_up.run_id

    run = kb.list_runs(conn, tid, include_active=False)[-1]
    assert run.outcome == "gave_up"
    assert (run.metadata or {}).get("will_retry") is False


def test_breaker_status_row_is_classified_diagnostic(conn) -> None:
    """The audit row renders as a diagnostic line, never a lifecycle flip:
    the notifier's classifier is the consumer that decides that rendering."""
    from gateway.kanban_watchers_notifier import diagnostic_event

    tid = _claimed(conn, "diagnostic row")
    assert _trip(conn, tid, failure_limit=1) is True
    status_row = kb.list_events(conn, tid)[-2]
    assert status_row.kind == "status"
    assert diagnostic_event(status_row) is True


# ---------------------------------------------------------------------------
# (b) no blocked-kind row; sticky only via force_trip's gave_up marker
# ---------------------------------------------------------------------------


def test_breaker_trip_never_emits_blocked_kind_and_stays_non_sticky(conn) -> None:
    tid = _claimed(conn, "non sticky")
    assert _trip(conn, tid, failure_limit=1) is True
    assert all(e.kind != "blocked" for e in kb.list_events(conn, tid))
    assert kb._has_sticky_block(conn, tid) is False
    # Counter hold still enforced while the limit stays at the tripped value.
    kb.recompute_ready(conn, failure_limit=1)
    held = kb.get_task(conn, tid)
    assert held is not None and held.status == "blocked"


def test_force_trip_is_sticky_via_gave_up_marker_only(conn) -> None:
    tid = _claimed(conn, "forced")
    assert kbd._record_task_failure(
        conn, tid, "credential revoked",
        outcome="crashed", force_trip=True, release_claim=True, end_run=True,
    ) is True
    events = kb.list_events(conn, tid)
    assert [e.kind for e in events[-2:]] == ["status", "gave_up"]
    assert (events[-1].payload or {}).get("sticky") is True
    assert all(e.kind != "blocked" for e in events)
    assert kb._has_sticky_block(conn, tid) is True
    # Sticky survives even a raised limit: operator-only release.
    kb.recompute_ready(conn, failure_limit=99)
    stuck = kb.get_task(conn, tid)
    assert stuck is not None and stuck.status == "blocked"


# ---------------------------------------------------------------------------
# (c) below-limit failure books the counter only
# ---------------------------------------------------------------------------


def test_below_limit_failure_emits_no_breaker_rows(conn) -> None:
    tid = _claimed(conn, "still retrying")
    assert _trip(conn, tid, failure_limit=3) is False
    kinds = [e.kind for e in kb.list_events(conn, tid)]
    assert "gave_up" not in kinds
    assert "status" not in kinds
    assert "blocked" not in kinds

    task = kb.get_task(conn, tid)
    assert task is not None
    assert task.status == "ready"  # spawn path restored the source phase
    assert task.consecutive_failures == 1
    run = kb.list_runs(conn, tid, include_active=False)[-1]
    assert run.outcome == "crashed"
    # Below the limit the card WILL be retried — no post-decision marker.
    assert "will_retry" not in (run.metadata or {})


# ---------------------------------------------------------------------------
# Release-path regression: recovery model unchanged by the audit rows
# ---------------------------------------------------------------------------


def test_raised_limits_still_release_a_tripped_card(conn) -> None:
    tid = _claimed(conn, "releasable")
    assert _trip(conn, tid, failure_limit=1) is True
    kb.recompute_ready(conn, failure_limit=1)
    held = kb.get_task(conn, tid)
    assert held is not None and held.status == "blocked"

    # Operator raises the dispatcher limit -> next tick releases to resume_status.
    kb.recompute_ready(conn, failure_limit=5)
    released = kb.get_task(conn, tid)
    assert released is not None and released.status == "ready"

    # Same release via the per-task override raised on the card itself.
    tid2 = _claimed(conn, "releasable via task override")
    assert _trip(conn, tid2, failure_limit=1) is True
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET max_retries = 5 WHERE id = ?", (tid2,))
    kb.recompute_ready(conn, failure_limit=1)
    released2 = kb.get_task(conn, tid2)
    assert released2 is not None and released2.status == "ready"


# ---------------------------------------------------------------------------
# Review-phase resume: the audit-row ordering is load-bearing
# ---------------------------------------------------------------------------


def test_review_phase_trip_resumes_to_review_and_pins_row_order(conn) -> None:
    tid = kb.create_task(conn, title="review trip", assignee="builder")
    implementation = kb.claim_task(conn, tid, claimer="host:builder")
    assert implementation is not None
    assert kb.request_review(
        conn, tid, summary="ready for review", reviewer="reviewer",
        expected_run_id=implementation.current_run_id,
    )
    assert kb.claim_review_task(conn, tid) is not None

    assert _trip(conn, tid, failure_limit=1) is True
    events = kb.list_events(conn, tid)
    status_row, gave_up = events[-2], events[-1]
    # Ordering pin: the status row lands BEFORE gave_up, so gave_up stays the
    # newest lifecycle event for _resume_status_from_events; belt-and-braces,
    # the status row also carries the run's retry_status verbatim.
    assert (status_row.kind, gave_up.kind) == ("status", "gave_up")
    assert status_row.id < gave_up.id
    assert status_row.payload is not None
    assert status_row.payload["retry_status"] == "review"
    assert gave_up.payload is not None
    assert gave_up.payload["retry_status"] == "review"
    assert gave_up.payload["will_retry"] is False
    assert kb._resume_status_from_events(conn, tid) == "review"

    assert kb.unblock_task(conn, tid) is True
    resumed = kb.get_task(conn, tid)
    assert resumed is not None and resumed.status == "review"


# ---------------------------------------------------------------------------
# Module-form worker argv: -P (PYTHONSAFEPATH) + import-root pin
# ---------------------------------------------------------------------------


def test_module_hermes_argv_sets_safe_path() -> None:
    from hermes_cli import kanban_db_worker_argv as kwa

    assert kwa._module_hermes_argv() == [sys.executable, "-P", "-m", "hermes_cli.main"]


def test_propagate_module_import_root_pins_pythonpath_despite_interpreter_flags() -> None:
    from hermes_cli import kanban_db_worker_argv as kwa

    root = str(Path(kwa.__file__).resolve().parents[1])

    env: dict[str, str] = {}
    kwa._propagate_module_import_root(kwa._module_hermes_argv(), env)
    assert env["PYTHONPATH"].split(os.pathsep)[0] == root

    # A resolved shim path owns its imports — never pinned.
    shim_env: dict[str, str] = {}
    kwa._propagate_module_import_root(["/opt/hermes/bin/hermes"], shim_env)
    assert "PYTHONPATH" not in shim_env


def test_safe_path_flag_keeps_spawn_cwd_off_module_import_path(tmp_path: Path) -> None:
    """-P is load-bearing, not cosmetic: without it ``python -m`` prepends the
    spawn cwd, so a workspace-local ``hermes_cli`` package shadows the pinned
    import root — the self-worktree dependency-activation failure mode."""
    repo_root = str(Path(kbd.__file__).resolve().parents[1])
    poison = tmp_path / "hermes_cli"
    poison.mkdir()
    (poison / "__init__.py").write_text('raise RuntimeError("poisoned hermes_cli")\n')
    env = {k: v for k, v in os.environ.items() if k != "HERMES_BIN"}
    env["PYTHONPATH"] = repo_root

    safe = subprocess.run(
        [sys.executable, "-P", "-m", "hermes_cli.main", "--version"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )
    assert safe.returncode == 0, safe.stderr[-400:]

    # Failure control: drop -P and the poison package wins.
    unsafe = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "--version"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )
    assert unsafe.returncode != 0
    assert "poisoned hermes_cli" in unsafe.stderr


# ---------------------------------------------------------------------------
# Dispatcher spawn invocation — _resolve_hermes_argv()
#
# Workers spawned by the dispatcher must use a `hermes` invocation that does
# not depend on PATH being set up correctly. cron jobs, systemd User= services,
# launchd jobs, and other detached processes routinely run with a stripped
# $PATH that doesn't include the venv's bin/, so a bare `["hermes", ...]`
# spawn fails with FileNotFoundError and the task gets stuck. The resolver
# prefers the interpreter-bound module form (exactly this install; a PATH
# shim could be attacker-planted or belong to another install, #111569) and
# only falls back to the PATH shim when ``hermes_cli`` is not importable.
# ---------------------------------------------------------------------------


def test_resolve_hermes_argv_prefers_module_form_over_path_shim(monkeypatch):
    """A `hermes` on PATH must not shadow the running install (#111569):
    the module argv wins whenever ``hermes_cli`` is importable; only an
    explicit ``$HERMES_BIN`` overrides it."""
    import shutil
    import sys
    from hermes_cli import kanban_db_dispatch as kbd
    from hermes_cli import kanban_db_worker_argv as kwa

    monkeypatch.delenv("HERMES_BIN", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/tmp/planted/hermes")
    monkeypatch.setattr(kwa, "_safe_which_no_cwd", lambda name: "/tmp/planted/hermes")
    assert kbd._resolve_hermes_argv() == [sys.executable, "-P", "-m", "hermes_cli.main"]

    monkeypatch.setenv("HERMES_BIN", "/opt/hermes/bin/hermes")
    assert kbd._resolve_hermes_argv() == ["/opt/hermes/bin/hermes"]




def test_resolve_hermes_argv_module_actually_runs():
    """The fallback module name must be importable + runnable.

    A unit test that pins the literal string is necessary but not
    sufficient — if `hermes_cli.main` ever loses `if __name__ == "__main__"`
    handling or its argparse setup, `python -m hermes_cli.main --version`
    would fail and so would every dispatcher spawn that hits the fallback.
    Run it as a real subprocess to catch that regression.
    """
    import subprocess
    from hermes_cli import kanban_db_dispatch as kbd
    import shutil
    import unittest.mock as mock

    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("HERMES_BIN", None)
        with mock.patch.object(shutil, "which", return_value=None):
            argv = kbd._resolve_hermes_argv()
    # ``-P`` (PYTHONSAFEPATH) keeps the cwd off the child's path, so the
    # module form only runs with its import root pinned — exactly what
    # ``_propagate_module_import_root`` does for every real spawn. Mirror
    # that env here: this test proves the module runs, not that a bare
    # cwd-relative import could ever satisfy it.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(Path(kbd.__file__).resolve().parents[1]), env.get("PYTHONPATH")) if p
    )
    r = subprocess.run(argv + ["--version"], capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 0, (
        f"`{' '.join(argv)} --version` failed (rc={r.returncode}); "
        f"stderr={r.stderr[:200]!r}"
    )


def test_default_spawn_pins_repo_root_on_module_worker_pythonpath(tmp_path, monkeypatch):
    """A module-form worker must carry the import context that selected it.

    ``_resolve_hermes_argv`` proves ``hermes_cli`` importable in the gateway,
    where a store-python shim has the repo root on ``sys.path`` in-process;
    the worker env scrub strips Hermes-owned PYTHONPATH entries, so the bare
    ``sys.executable -m hermes_cli.main`` child died on import and the board
    auto-blocked (#122299, #122487, #122500). The spawned env must put the
    running install's root first on PYTHONPATH — and never for a resolved shim
    path, which owns its own imports.
    """
    import os
    import sys
    from pathlib import Path

    from hermes_cli import kanban_db_dispatch as kbd

    root = str(Path(kbd.__file__).resolve().parents[1])
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("HERMES_BIN", raising=False)

    captured = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env", {})
            self.pid = 4242

    monkeypatch.setattr("subprocess.Popen", _FakePopen)

    task = kb.Task(
        id="t_import_root", title="x", body=None, assignee="coder", status="ready",
        priority=0, created_by=None, created_at=0, started_at=None, completed_at=None,
        workspace_kind="worktree", workspace_path=str(tmp_path / "ws"), claim_lock=None,
        claim_expires=None, tenant=None, branch_name=None,
    )

    monkeypatch.setattr(kbd, "_resolve_hermes_argv", lambda: [sys.executable, "-m", "hermes_cli.main"])
    kbd._default_spawn(task, str(tmp_path / "ws"))
    assert captured["env"]["PYTHONPATH"].split(os.pathsep)[0] == root

    monkeypatch.setattr(kbd, "_resolve_hermes_argv", lambda: ["/opt/hermes/bin/hermes"])
    kbd._default_spawn(task, str(tmp_path / "ws"))
    assert root not in captured["env"].get("PYTHONPATH", "").split(os.pathsep)
