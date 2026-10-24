import os
import pytest
from gateway.run_turn_runner import _DesktopSessionEventBridge
from tui_gateway.contracts import registry

pytestmark = pytest.mark.skipif(
    not os.environ.get("HERMES_TEST_ISOLATION"), reason="contract STRICT gate")


def _probe() -> _DesktopSessionEventBridge:
    b = _DesktopSessionEventBridge("")      # empty sid -> __init__ skips thread start; all attrs set
    b.session_id = "probe"
    b._thread = object()                    # emit() early-return guard satisfied
    return b


def test_all_bridge_emits_validate_under_strict():
    assert registry.STRICT
    b = _probe()
    b.on_turn_start()
    b.on_text_delta("hi"); b.on_reasoning_delta("think")
    b.on_interim("note"); b.on_interim("note2", already_streamed=True)
    b.on_tool_gen("terminal")
    b.on_tool_start("c1", "terminal", {"command": "ls"})
    b.on_tool_start("c2", "mcp__github__create_issue", {"title": "x"})
    b.on_tool_start("c3", "connectors__gmail__send_email", "to=a@b.c")
    b.on_tool_start("c4", "tool_call", {"calls": [{"name": "mcp__github__create_issue", "arguments": {"title": "x"}}]})
    b.on_tool_complete("c1", "terminal", {"command": "ls"}, "ok")
    b.on_tool_complete("c2", "mcp__github__create_issue", {"title": "x"}, '{"ok": true}')
    b.on_tool_complete("c3", "connectors__gmail__stop", "to=a@b.c", {"raw": object()})
    b.on_turn_complete({"final_response": "done"})
    b.on_turn_complete({"failed": True, "error": "boom"})
    b.on_turn_complete({"interrupted": True})
    seen = [(it["event"], it["payload"]) for it in b._queue.queue]
    assert len(seen) == 16
    for event, payload in seen:
        registry.check_payload(event, payload)   # STRICT -> raises on any violation
    mcp_evt = next(p for e, p in seen if e == "tool.start" and p.get("name") == "mcp__github__create_issue")
    assert mcp_evt["labels"] and mcp_evt["labels"][0]["kind"] == "mcp"
    conn_evt = next(p for e, p in seen if e == "tool.start" and p.get("name") == "connectors__gmail__send_email")
    assert conn_evt["labels"] and conn_evt["labels"][0]["kind"] == "connector"
    bridged_evt = next(p for e, p in seen if e == "tool.start" and p.get("name") == "tool_call")
    assert bridged_evt["labels"] and bridged_evt["labels"][0]["kind"] == "mcp"   # inner-call expansion
    local_evt = next(p for e, p in seen if e == "tool.start" and p.get("name") == "terminal")
    assert "labels" not in local_evt                                            # native-path parity


def test_real_emit_raises_on_bad_payload_under_strict():
    b = _probe()
    with pytest.raises(registry.ContractViolation):
        b.emit("tool.start", {"tool_id": "x", "name": "t",
                              "labels": {"verb": "Running", "target": "y", " Past": "Running"}})


def _committed_turn_result() -> tuple[dict, dict]:
    """A foreign-gateway turn result whose suffix the agent already flushed to the DB:
    the user row and final reply both carry ``_db_persisted`` and ``_row_id``. Returns
    the result plus the final assistant dict (its uid is random — read it back)."""
    from agent.context_compressor import _DB_PERSISTED_MARKER
    from agent.message_metadata import stamp_message_uid

    user: dict = {"role": "user", "content": "check the logs"}
    assistant: dict = {"role": "assistant", "content": "all clear"}
    stamp_message_uid(user)
    stamp_message_uid(assistant)
    for index, message in enumerate((user, assistant)):
        message["_row_id"] = 4400 + index
        message[_DB_PERSISTED_MARKER] = True
    return {"final_response": "all clear", "messages": [user, assistant]}, assistant


def test_bridge_turn_complete_carries_persisted_turn_receipt_with_message_uid():
    """Patch-0010's bridged message.complete must carry the same durable identity the
    native gateway's frame does: without it the Desktop's streamed bubble cannot key its
    absorb, and the canonical DB row lands as a SECOND bubble (2026-10-22 duplicate bubble).
    A receipt whose final row body matches the final response names the final row id + uid;
    the foreign process cannot prove the pre-turn prefix, so ``complete`` stays False."""
    result, final_row = _committed_turn_result()
    b = _probe()
    b.on_turn_complete(result)

    (event, payload), *_ = [(it["event"], it["payload"]) for it in b._queue.queue]
    assert event == "message.complete"
    registry.check_payload(event, payload)  # STRICT: persisted_turn must validate
    assert payload["persisted_turn"] == {
        "row_ids": [4400, 4401],
        "complete": False,
        "final_assistant_row_id": 4401,
        "message_uid": final_row["message_uid"],
    }


def test_bridge_turn_complete_omits_receipt_without_committed_rows():
    """No committed rows → no receipt: absence is unproven, never a negative claim
    (mirrors tui_gateway.prompt_turn._persisted_turn_receipt)."""
    b = _probe()
    b.on_turn_complete({"final_response": "draft", "messages": [{"role": "assistant", "content": "draft"}]})

    (_, payload), *_ = [(it["event"], it["payload"]) for it in b._queue.queue]
    assert "persisted_turn" not in payload
