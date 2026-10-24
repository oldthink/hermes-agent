"""Stale-wake age marker (local patch 0004).

A completion wake is a synthetic *user* message. It can sit in the queue behind a long turn and be
delivered long after the process exited. These tests pin: the exit time is stamped at all three event
producers; the formatter shows the age and, past ``terminal.stale_wake_after_seconds``, a STALE banner
INSIDE the unchanged ``[IMPORTANT: Background process`` frame; unknown exit time renders the legacy text
byte-for-byte; and — the round-1 blocker — a stale wake is still classified as a SYNTHETIC message by
every classifier that pins the prefix (compaction, anchor restoration, timeline, desktop regex).
"""
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools import process_registry_notifications as prn
from tools.process_registry import ProcessRegistry, ProcessSession

PREFIX = "[IMPORTANT: Background process "
# apps/desktop/src/components/assistant-ui/thread/content.ts:3 (PROCESS_NOTIFICATION_RE), ported to Python
DESKTOP_RE = re.compile(r"^\[IMPORTANT: Background process [\s\S]*\]$")


def _evt(**kw):
    base = {"type": "completion", "session_id": "proc_x", "command": "echo hi", "exit_code": 0,
            "output": "done", "completion_reason": "exited"}
    base.update(kw)
    return base


def _fmt(evt):
    text = prn.format_process_notification(evt)
    assert text is not None
    return text


@pytest.fixture()
def fixed_now(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(prn.time, "time", lambda: now)
    return now


@pytest.fixture(autouse=True)
def default_threshold(monkeypatch):
    # isolate from the real config: 300 s unless a test overrides it
    monkeypatch.setattr(ProcessRegistry, "_config_seconds", staticmethod(lambda key, fallback: fallback))


class TestFormatter:
    def test_fresh_event_shows_age_not_stale(self, fixed_now):
        text = _fmt(_evt(exited_at=fixed_now - 3))
        lines = text.split("\n")
        assert lines[0].startswith(PREFIX + "proc_x completed normally (exit code 0).")
        assert lines[1] == "Completed 3s ago."
        assert "STALE WAKE" not in text

    def test_58_minute_event_is_marked_stale_inside_the_frame(self, fixed_now):
        text = _fmt(_evt(exited_at=fixed_now - 58 * 60))
        assert text.startswith(PREFIX)
        assert text.endswith("]")
        assert "Completed 58m ago." in text
        assert "STALE WAKE — completed 58m ago; delivered after a long turn." in text
        assert text.index("STALE WAKE") < text.index("Command:")  # banner is the first body block

    @pytest.mark.parametrize("exited", [None, 0, 0.0, "", "garbage"])
    def test_unknown_exit_time_renders_the_legacy_text_byte_for_byte(self, exited):
        legacy = _fmt(_evt())
        assert _fmt(_evt(exited_at=exited)) == legacy
        # Frozen pre-patch literal (parent commit): a wholesale re-render must still be caught.
        assert legacy == ("[IMPORTANT: Background process proc_x completed normally (exit code 0).\n"
                          "Command: echo hi\nOutput:\ndone]")
        assert "Completed" not in legacy and "STALE" not in legacy

    def test_threshold_is_read_from_config_key(self, fixed_now, monkeypatch):
        seen = {}

        def fake(key, fallback):
            seen["key"], seen["fallback"] = key, fallback
            return 10.0

        monkeypatch.setattr(ProcessRegistry, "_config_seconds", staticmethod(fake))
        assert "STALE WAKE" in _fmt(_evt(exited_at=fixed_now - 20))
        assert "STALE WAKE" not in _fmt(_evt(exited_at=fixed_now - 5))
        assert seen == {"key": "stale_wake_after_seconds", "fallback": 300.0}

    def test_zero_threshold_disables_the_stale_marker_but_keeps_the_age(self, fixed_now, monkeypatch):
        monkeypatch.setattr(ProcessRegistry, "_config_seconds", staticmethod(lambda key, fallback: 0.0))
        text = _fmt(_evt(exited_at=fixed_now - 3600))
        assert "Completed 1h ago." in text and "STALE WAKE" not in text

    def test_real_config_helper_reads_the_declared_default(self, fixed_now, monkeypatch):
        """No monkeypatch on _config_seconds: the production reader must resolve the
        DEFAULT_CONFIG key this patch declared (300.0) — not the exception fallback."""
        monkeypatch.setattr(prn.time, "time", lambda: fixed_now)
        from hermes_cli.config_defaults import DEFAULT_CONFIG
        assert DEFAULT_CONFIG["terminal"]["stale_wake_after_seconds"] == 300.0
        assert prn._stale_wake_threshold_seconds() == 300.0

    def test_killed_process_says_ended_not_completed(self, fixed_now):
        text = _fmt(_evt(exited_at=fixed_now - 60, completion_reason="killed"))
        assert "Ended 1m ago." in text and "Completed" not in text

    def test_config_failure_falls_back_to_300(self, fixed_now, monkeypatch):
        def boom(key, fallback):
            raise RuntimeError("config unreadable")

        monkeypatch.setattr(ProcessRegistry, "_config_seconds", staticmethod(boom))
        assert "STALE WAKE" in _fmt(_evt(exited_at=fixed_now - 301))
        assert "STALE WAKE" not in _fmt(_evt(exited_at=fixed_now - 299))

    def test_future_exit_time_never_negative(self, fixed_now):
        assert "Completed 0s ago." in _fmt(_evt(exited_at=fixed_now + 50))


class TestProducers:
    def test_registry_publish_finished_carries_exited_at(self):
        reg = ProcessRegistry()
        s = ProcessSession(id="proc_p", command="x", task_id="t", started_at=time.time(), exited=True,
                           exit_code=0, output_buffer="o", notify_on_complete=True)
        reg._running[s.id] = s
        with patch.object(reg, "_write_checkpoint"):
            reg._move_to_finished(s)
        evt = reg.completion_queue.get_nowait()
        assert evt["exited_at"] > 0 and abs(evt["exited_at"] - s.exited_at) < 1e-6

    def test_registry_event_with_unknown_exit_time_is_zero(self):
        reg = ProcessRegistry()
        s = ProcessSession(id="proc_q", command="x", task_id="t", started_at=time.time(), exited=True,
                           exit_code=0, output_buffer="o", notify_on_complete=True)
        reg._running[s.id] = s
        with patch.object(reg, "_write_checkpoint"):
            reg._publish_finished(s, True)  # bypass _move_to_finished: exited_at never stamped
        evt = reg.completion_queue.get_nowait()
        assert evt["exited_at"] == 0.0
        assert "Completed" not in _fmt(evt)

    def test_gateway_builder_carries_exited_at(self):
        from types import SimpleNamespace
        from gateway.run_notifications import GatewayNotificationsMixin

        sess = SimpleNamespace(output_buffer="out", command="echo hi", exit_code=0, task_id="t",
                               started_at=1.0, exited_at=1234.5, completion_reason="exited")
        evt = GatewayNotificationsMixin._build_process_completion_event({}, sess, "proc_g")
        assert evt["exited_at"] == 1234.5

    def test_bot_mode_dm_event_carries_exited_at(self, monkeypatch, fixed_now):
        import threading
        import tools.process_registry as pr_module
        from tools import bot_mode_dm

        captured = {}
        proc = SimpleNamespace(_completion_event=threading.Event(), command="echo hi", exit_code=0,
                               completion_reason="exited", termination_source=None, output_buffer="done",
                               completion_output_chars=2000, task_id="t", started_at=1.0, exited_at=fixed_now - 60)
        proc._completion_event.set()
        monkeypatch.setattr(pr_module.process_registry, "get", lambda pid: proc)
        monkeypatch.setattr(pr_module.process_registry, "is_completion_consumed", lambda pid: False)
        import tools.process_registry_notifications as prn_module
        monkeypatch.setattr(prn_module, "process_completion_display_text", lambda evts: "display")

        class FakeDB:
            def append_message(self, session_id, role, content, **kw):
                captured["content"] = content

        import threading as _th
        done = _th.Event()
        _orig_append = FakeDB.append_message
        def _append(self, *a, **k):
            _orig_append(self, *a, **k)
            done.set()
        FakeDB.append_message = _append
        agent = SimpleNamespace(_session_db=FakeDB(), session_id="sess_dm")
        assert bot_mode_dm._persist_reply_when_done("proc_b", agent) is True
        assert done.wait(5), "daemon thread never persisted the reply (check _exit_snapshot attrs on the fake proc)"
        assert "Completed 1m ago." in (captured.get("content") or "")


class TestStaleWakeIsStillSynthetic:
    """The round-1 blocker: the classifiers pin the literal prefix; a stale wake must keep it."""

    @pytest.fixture()
    def stale_text(self, fixed_now):
        return _fmt(_evt(exited_at=fixed_now - 58 * 60))

    def test_desktop_regex_matches(self, stale_text):
        assert DESKTOP_RE.match(stale_text)

    def test_ported_regex_equals_the_desktop_typescript_literal(self):
        """Drift control: the ported pattern must equal content.ts PROCESS_NOTIFICATION_RE verbatim."""
        import subprocess
        ts_path = Path(__file__).resolve().parents[2] / "apps/desktop/src/components/assistant-ui/thread/content.ts"
        ts = subprocess.run(["grep", "-o", r"PROCESS_NOTIFICATION_RE = /[^/]*/", str(ts_path)],
                            capture_output=True, text=True).stdout.strip()
        assert ts == "PROCESS_NOTIFICATION_RE = /^\\[IMPORTANT: Background process [\\s\\S]*\\]$/"

    def test_compressor_treats_it_as_scaffolding(self, stale_text):
        from agent.context_compressor import _synthetic_user_row
        assert _synthetic_user_row(stale_text) is True

    def test_anchor_restoration_does_not_treat_it_as_human_intent(self, stale_text):
        from agent.conversation_compression import _is_real_user_message
        assert _is_real_user_message({"role": "user", "content": stale_text}) is False

    def test_timeline_classifies_it_as_synthetic_prompt(self, stale_text):
        from hermes_state_timeline import _SYNTHETIC_PROMPT
        assert _SYNTHETIC_PROMPT.match(stale_text)

    def test_control_a_swapped_prefix_WOULD_be_classified_real(self, stale_text):
        """Failure control: proves the classifier assertions above can fail. The rejected design
        (prefix swap to [STALE WAKE) is read as real user input / a plain prompt by every classifier."""
        swapped = stale_text.replace("[IMPORTANT: Background process ", "[STALE WAKE — background process ", 1)
        from agent.context_compressor import _synthetic_user_row
        from agent.conversation_compression import _is_real_user_message
        from hermes_state_timeline import _SYNTHETIC_PROMPT
        assert not DESKTOP_RE.match(swapped)
        assert _synthetic_user_row(swapped) is False
        assert _is_real_user_message({"role": "user", "content": swapped}) is True
        assert not _SYNTHETIC_PROMPT.match(swapped)
