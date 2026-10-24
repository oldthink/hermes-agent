"""patch-0011: mirrored child tool.complete rows may carry ``preview``.

``agent_callbacks._mirror_subagent_to_child`` stores the ``tool.start`` dict and re-emits it
as ``tool.complete``; ToolCompletePayload must therefore accept the same shape ToolStartPayload
already legalises. Run under HERMES_TEST_ISOLATION (registry raises only there).
"""

import os

import pytest

from tui_gateway.contracts import registry

pytestmark = pytest.mark.skipif(
    not os.environ.get("HERMES_TEST_ISOLATION"), reason="needs HERMES_TEST_ISOLATION"
)


def test_mirror_child_tool_complete_accepts_preview():
    assert registry.STRICT
    registry.check_payload(
        "tool.complete",
        {
            "tool_id": "submirror:child:1",
            "name": "terminal",
            "args": {},
            "preview": "/some/file.py",
        },
    )
    # The common mirror shape: args-only, no preview — must stay accepted.
    registry.check_payload(
        "tool.complete",
        {"tool_id": "submirror:child:2", "name": "terminal", "args": {}},
    )


def test_unknown_field_still_rejected():
    with pytest.raises(registry.ContractViolation, match="bogus_field"):
        registry.check_payload(
            "tool.complete", {"tool_id": "x", "name": "t", "bogus_field": "1"}
        )
