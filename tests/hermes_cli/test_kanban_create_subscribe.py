"""``kanban create --subscribe``: explicit opt-in notification subscription + session stamping.

A desktop session dispatching via the CLI used to get zero completion wakes: bare
``hermes kanban create`` never wrote a ``kanban_notify_subs`` row and never stamped
``tasks.session_id`` (only the kanban_create TOOL path auto-subscribed). The flag is
explicit opt-in, routes DIRECTLY through ``add_notify_sub`` (never the config-gated
``_maybe_auto_subscribe``), stamps the session id ONLY on the subscribe success path,
and fails loudly (exit 2) when no notification target resolves.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn
from hermes_cli.kanban_parser import build_parser


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # The CLI must not see a stale ambient session identity from the outer test runner.
    for var in ("HERMES_SESSION_ID", "HERMES_SESSION_KEY", "HERMES_SESSION_PLATFORM",
                "HERMES_SESSION_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
    kb.init_db()
    return home


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    build_parser(parser.add_subparsers())
    return parser.parse_args(argv)


def _gateway_env(monkeypatch, platform="telegram", chat_id="chat-1"):
    """Simulate a gateway session: ContextVars absent, plain env mirror set."""
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", platform)
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", chat_id)


def _persisted_session(home, session_id="sess-cli-1") -> str:
    """A session row in this profile's state.db, so ``_persisted_session_id`` validates it."""
    from hermes_state import SessionDB

    db = SessionDB(db_path=home / "state.db")
    try:
        db.create_session(session_id, "cli")
    finally:
        db.close()
    return session_id


# ---------------------------------------------------------------------------
# (a) --subscribe with a valid target: 1 sub row + validated session_id stamped
# ---------------------------------------------------------------------------


def test_subscribe_with_target_writes_one_sub_and_stamps_session(kanban_home, monkeypatch, capsys):
    _gateway_env(monkeypatch)
    monkeypatch.setenv("HERMES_SESSION_ID", _persisted_session(kanban_home))

    rc = kc.kanban_command(_parse(["kanban", "create", "sub me",
                                   "--assignee", "worker1", "--subscribe"]))
    out, err = capsys.readouterr()
    assert rc == 0, (rc, out, err)
    assert "no notification target" not in err

    task_id = next(line.split()[1] for line in out.splitlines() if line.startswith("Created "))
    assert task_id.startswith("t_")

    with kbc.connect() as conn:
        task = kb.get_task(conn, task_id)
        assert task.session_id == "sess-cli-1", "validated HERMES_SESSION_ID must be stamped"
        subs = kbn.list_notify_subs(conn, task_id)
    assert len(subs) == 1, subs
    assert subs[0]["platform"] == "telegram"
    assert subs[0]["chat_id"] == "chat-1"
    assert subs[0]["delivery_mode"] == "notify+wake"


def test_subscribe_stamps_only_a_persisted_session_id(kanban_home, monkeypatch, capsys):
    """An env HERMES_SESSION_ID with no state.db row is NOT stamped (bare env reads are
    forbidden), but the subscription itself still lands — the two are independent."""
    _gateway_env(monkeypatch)
    monkeypatch.setenv("HERMES_SESSION_ID", "never-persisted")

    rc = kc.kanban_command(_parse(["kanban", "create", "ghost session",
                                   "--assignee", "worker1", "--subscribe"]))
    out, err = capsys.readouterr()
    assert rc == 0, (rc, out, err)
    task_id = next(line.split()[1] for line in out.splitlines() if line.startswith("Created "))
    with kbc.connect() as conn:
        assert kb.get_task(conn, task_id).session_id is None
        assert len(kbn.list_notify_subs(conn, task_id)) == 1


# ---------------------------------------------------------------------------
# (b) --subscribe with no resolvable target (stateless CLI / cron): exit 2
# ---------------------------------------------------------------------------


def test_subscribe_without_target_exits_two(kanban_home, monkeypatch, capsys):
    monkeypatch.delenv("HERMES_SESSION_KEY", raising=False)

    rc = kc.kanban_command(_parse(["kanban", "create", "stateless",
                                   "--assignee", "worker1", "--subscribe"]))
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "no notification target" in err
    # The card itself WAS created (the error names it) — never a silent no-op AND
    # never a lost create. Recover the id from the DB by title.
    with kbc.connect() as conn:
        row = conn.execute("SELECT id FROM tasks WHERE title = ?", ("stateless",)).fetchone()
        assert row, "task must exist even when the subscribe target fails to resolve"
        task_id = row["id"]
        assert kbn.list_notify_subs(conn, task_id) == []
        assert kb.get_task(conn, task_id).session_id is None


# ---------------------------------------------------------------------------
# (c) bare create without --subscribe: exit 0, stderr warning, 0 subs, NULL session
# ---------------------------------------------------------------------------


def test_bare_create_warns_and_writes_no_subs(kanban_home, monkeypatch, capsys):
    _gateway_env(monkeypatch)
    monkeypatch.setenv("HERMES_SESSION_ID", _persisted_session(kanban_home))

    rc = kc.kanban_command(_parse(["kanban", "create", "bare create",
                                   "--assignee", "worker1"]))
    out, err = capsys.readouterr()
    assert rc == 0, (rc, out, err)
    assert "--subscribe" in err
    assert "NOT be woken" in err
    task_id = next(line.split()[1] for line in out.splitlines() if line.startswith("Created "))
    with kbc.connect() as conn:
        task = kb.get_task(conn, task_id)
        assert task.session_id is None, "without the flag session_id stays NULL"
        assert kbn.list_notify_subs(conn, task_id) == [], "0 subs written"


def test_bare_create_json_emits_no_warning_and_no_subs(kanban_home, monkeypatch, capsys):
    """--json keeps stdout parseable; the warning is stderr-only even there."""
    _gateway_env(monkeypatch)
    monkeypatch.setenv("HERMES_SESSION_ID", _persisted_session(kanban_home))

    rc = kc.kanban_command(_parse(["kanban", "create", "json create",
                                   "--assignee", "worker1", "--json"]))
    out, err = capsys.readouterr()
    assert rc == 0, (rc, out, err)
    payload = json.loads(out)
    assert payload["session_id"] is None
    assert "subscribe" not in out
    with kbc.connect() as conn:
        assert kbn.list_notify_subs(conn, payload["id"]) == []


# ---------------------------------------------------------------------------
# (d) help surfaces tell the truth
# ---------------------------------------------------------------------------


def test_slash_help_no_false_auto_subscribe_claim():
    help_text = kc.run_slash("help")
    assert "auto-subscribes" not in help_text
    assert "--subscribe" in help_text


def test_parser_help_document_the_flag(capsys):
    parser = argparse.ArgumentParser(prog="hermes")
    build_parser(parser.add_subparsers())
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["kanban", "create", "-h"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--subscribe" in out
    assert "auto-subscribe" not in out
