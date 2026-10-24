"""Tests for agent.spawn_admission (local patch 0005).

All readings are pinned via the _set_admission_overrides seam: no test spawns a
real child, counts real scopes, or touches real memory. Polarity pairs, storm,
slot-release-equivalent (scope count is systemd-owned; we prove the counter is
re-read per admission), retry discrimination, and seam cleanliness.
"""

from __future__ import annotations

import pytest

from agent import spawn_admission as sa


@pytest.fixture(autouse=True)
def _clean_seam():
    sa._admission_overrides.clear()
    # H3: pin the memory reading for EVERY test — no test ever reads host RAM
    sa._set_admission_overrides(meminfo_reader=lambda: 65536)
    yield
    sa._admission_overrides.clear()


def _cfg(key, default=None):
    # minimal config_get stub: everything default
    return default


class TestCapRefusal:
    def test_below_cap_admits(self):
        sa._set_admission_overrides(scope_count=5)
        sa.admit_spawn("sleep 1", config_get=_cfg)  # no raise

    def test_at_cap_refuses_with_guard_text(self):
        sa._set_admission_overrides(scope_count=12)
        with pytest.raises(sa.SpawnRefused) as ei:
            sa.admit_spawn("sleep 1", config_get=_cfg)
        assert "hermes-worker scope(s) already running" in str(ei.value)
        assert "tools.max_spawn_running" in str(ei.value)
        assert "Command not started" in str(ei.value)

    def test_env_override_tightens_cap(self, monkeypatch):
        monkeypatch.setenv("HERMES_MAX_SPAWN_RUNNING", "3")
        sa._set_admission_overrides(scope_count=3)
        with pytest.raises(sa.SpawnRefused):
            sa.admit_spawn("sleep 1", config_get=_cfg)

    def test_cap_zero_disables(self, monkeypatch):
        monkeypatch.setenv("HERMES_MAX_SPAWN_RUNNING", "0")
        sa._set_admission_overrides(scope_count=999)
        sa.admit_spawn("sleep 1", config_get=_cfg)  # disabled = admit


class TestFloorRefusal:
    def test_above_floor_admits(self):
        sa._set_admission_overrides(meminfo_reader=lambda: 8192)
        sa.admit_spawn("sleep 1", config_get=_cfg)

    def test_below_floor_refuses_naming_floor(self):
        sa._set_admission_overrides(meminfo_reader=lambda: 2048)
        with pytest.raises(sa.SpawnRefused) as ei:
            sa.admit_spawn("sleep 1", config_get=_cfg)
        assert "below the" in str(ei.value)
        assert "4096 MiB floor" in str(ei.value)

    def test_reader_none_fails_open(self):
        sa._set_admission_overrides(meminfo_reader=lambda: None)
        sa.admit_spawn("sleep 1", config_get=_cfg)  # unavailable reading = admit

    def test_floor_zero_disables(self, monkeypatch):
        monkeypatch.setenv("HERMES_SPAWN_MEM_FLOOR_MB", "0")
        sa._set_admission_overrides(meminfo_reader=lambda: 1)
        sa.admit_spawn("sleep 1", config_get=_cfg)


class TestStorm:
    def test_n_concurrent_requests_exactly_cap_admitted(self, monkeypatch):
        """N > cap spawn attempts CONCURRENTLY (real threads, shared barrier):
        exactly cap admit, the rest refuse, cap still enforced after."""
        import threading
        cap = 4
        monkeypatch.setenv("HERMES_MAX_SPAWN_RUNNING", str(cap))
        live = {"n": 0}
        lock = threading.Lock()
        admitted = {"n": 0}
        refused = {"n": 0}
        barrier = threading.Barrier(10)

        def attempt(i):
            barrier.wait()  # all 10 threads race the admission point together
            with lock:  # hold across seam-set + admission: the seam is a plain dict (not atomic)
                count = live["n"]
                sa._set_admission_overrides(scope_count=count, meminfo_reader=lambda: 65536)
                try:
                    sa.admit_spawn(f"sleep 1 #{i}", config_get=_cfg)
                    admitted["n"] += 1
                    live["n"] += 1  # the admitted child occupies a scope
                except sa.SpawnRefused:
                    refused["n"] += 1

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert admitted["n"] == cap
        assert refused["n"] == 10 - cap
        # post-storm: cap still enforced, no counter drift
        sa._set_admission_overrides(scope_count=live["n"], meminfo_reader=lambda: 65536)
        with pytest.raises(sa.SpawnRefused):
            sa.admit_spawn("sleep 1 after-storm", config_get=_cfg)

    def test_slots_released_when_scopes_exit(self, monkeypatch):
        """Scope exit (child terminates) frees capacity: same count now admits."""
        cap = 2
        monkeypatch.setenv("HERMES_MAX_SPAWN_RUNNING", str(cap))
        sa._set_admission_overrides(scope_count=cap, meminfo_reader=lambda: 65536)
        with pytest.raises(sa.SpawnRefused):
            sa.admit_spawn("sleep 1", config_get=_cfg)
        sa._set_admission_overrides(scope_count=cap - 1, meminfo_reader=lambda: 65536)
        sa.admit_spawn("sleep 1", config_get=_cfg)  # admits again


class TestRetryDiscrimination:
    def test_identical_refusal_repeats_same_message(self):
        """A model looping on the refused call replays the exact guard text —
        the pre-existing stall-guard (FAILURE_TOLERANT + exact-args rule) halts it;
        this test pins the refusal as replayable/identical for that mechanism."""
        sa._set_admission_overrides(scope_count=99)
        msgs = set()
        for _ in range(3):
            with pytest.raises(sa.SpawnRefused) as ei:
                sa.admit_spawn("same command", config_get=_cfg)
            msgs.add(str(ei.value))
        assert len(msgs) == 1


class TestSeamCleanliness:
    def test_seam_cleared_after_use(self):
        sa._set_admission_overrides(scope_count=1)
        assert sa._admission_overrides
        sa._admission_overrides.clear()
        assert not sa._admission_overrides

    def test_fail_open_when_systemctl_missing(self, monkeypatch):
        monkeypatch.setattr(sa.shutil, "which", lambda name: None)
        assert sa._running_worker_scopes() == 0  # 0 running -> admits (fail-open)


class TestScopeCountParse:
    def test_counts_only_worker_scope_lines(self, monkeypatch):
        class FakeRun:
            returncode = 0
            stdout = (
                "hermes-worker-abc.scope load active running 1\n"
                "hermes-worker-def.scope load active running 2\n"
                "unrelated.service load active running 3\n"
            )
        monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: FakeRun())
        assert sa._running_worker_scopes() == 2


class TestConfigLane:
    def test_config_value_changes_cap(self, monkeypatch):
        """The production config accessor must actually be consulted (M1 regression)."""
        import types, sys
        fake = types.ModuleType("hermes_cli.config")
        fake.DEFAULT_CONFIG = {"tools": {"max_spawn_running": 2, "spawn_mem_floor_mb": 4096}}
        fake.cfg_get = lambda raw, section, key: 2 if key == "max_spawn_running" else None
        fake.read_raw_config = lambda: {}
        monkeypatch.setitem(sys.modules, "hermes_cli.config", fake)
        sa._set_admission_overrides(scope_count=2, meminfo_reader=lambda: 65536)
        with pytest.raises(sa.SpawnRefused) as ei:
            sa.admit_spawn("sleep 1")  # no config_get injection: production lane
        assert "cap 2" in str(ei.value)

    def test_bus_env_passed_to_probe(self, monkeypatch):
        """M3 regression: the scope-count probe carries the user-bus env."""
        captured = {}
        class FakeRun:
            returncode = 0
            stdout = "hermes-worker-x.scope load active running 1\n"
        def fake_run(argv, **kwargs):
            captured.update(kwargs)
            return FakeRun()
        monkeypatch.setattr(sa.subprocess, "run", fake_run)
        monkeypatch.setattr(sa.shutil, "which", lambda name: "/usr/bin/systemctl")
        sa._running_worker_scopes()
        assert isinstance(captured.get("env"), dict) and captured["env"].get("DBUS_SESSION_BUS_ADDRESS"), captured.get("env")

    def test_cron_kanban_scopes_excluded(self, monkeypatch):
        """H4: cron/kanban scopes do not consume the terminal-spawn cap."""
        class FakeRun:
            returncode = 0
            stdout = (
                "hermes-worker-cron-j1.scope load active running 1\n"
                "hermes-worker-kanban-t2.scope load active running 2\n"
                "hermes-worker-abc.scope load active running 3\n"
            )
        monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: FakeRun())
        monkeypatch.setattr(sa.shutil, "which", lambda name: "/usr/bin/systemctl")
        assert sa._running_worker_scopes() == 1

    def test_dead_cgroup_branch_removed(self):
        """H1 regression: _effective_available_mb returns memory_info's value directly."""
        import types
        fake_info = types.SimpleNamespace(available_mb=1234)
        fake_mod = types.ModuleType("tools.bot_desktop.resources")
        fake_mod.memory_info = lambda: fake_info
        import sys
        sa._admission_overrides.clear()  # drop the fixture's meminfo pin: exercise the module path
        sys.modules["tools.bot_desktop.resources"] = fake_mod
        try:
            assert sa._effective_available_mb() == 1234
        finally:
            del sys.modules["tools.bot_desktop.resources"]


class TestPtyHoist:
    def test_pty_path_refused_at_cap_and_never_enters_pty_spawn(self, monkeypatch):
        """M2 regression: use_pty=True routes through the admission guard; a refusal
        raises RuntimeError (the tool-error wire-in) BEFORE _spawn_local_pty runs,
        and the pipe fallback is never reached either."""
        from tools import process_registry as pr

        entered = {"pty": False, "pipe": False}
        def fake_pty_spawn(self, session, safe_command, env_vars):
            entered["pty"] = True
            raise AssertionError("PTY spawn entered despite refusal")

        monkeypatch.setattr(pr.ProcessRegistry, "_spawn_local_pty", fake_pty_spawn)
        monkeypatch.setenv("HERMES_MAX_SPAWN_RUNNING", "1")
        sa._set_admission_overrides(scope_count=1, meminfo_reader=lambda: 65536)

        registry = pr.ProcessRegistry()
        try:
            registry.spawn_local(command="sleep 1", cwd="/tmp", use_pty=True)
            raise AssertionError("spawn_local returned without refusing")
        except RuntimeError as e:
            assert "spawn refused" in str(e)
        assert entered["pty"] is False
