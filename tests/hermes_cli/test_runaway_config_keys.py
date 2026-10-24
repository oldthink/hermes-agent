"""Local patch 0002 amendment: the watchdog's config keys are registered in DEFAULT_CONFIG.

Before the amendment ``hermes config get agent.runaway_max_synthetic_wakes`` printed a false
"not a recognized config key" warning: patch 0002 read the keys via ``cfg_get`` but never
registered them, so ``_validate_config_key``'s nested walk failed (populated ``agent``
section, missing sub-key). These tests pin registration, validator acceptance, and default
parity with agent.runaway_watchdog — one deliberate default set (25 wakes / 4 silent hours,
post-2026-10-10 incident sizing); do not change casually and never out of sync.
"""

from agent import runaway_watchdog
from hermes_cli.config import _validate_config_key
from hermes_cli.config_defaults import DEFAULT_CONFIG

KEYS = ("agent.runaway_max_synthetic_wakes", "agent.runaway_max_silent_hours")


def test_runaway_keys_registered_with_deliberate_defaults():
    agent_cfg = DEFAULT_CONFIG["agent"]
    assert agent_cfg["runaway_max_synthetic_wakes"] == 25
    assert agent_cfg["runaway_max_silent_hours"] == 4.0


def test_validator_accepts_runaway_keys():
    for key in KEYS:
        is_known, suggestion = _validate_config_key(key)
        assert is_known, f"{key} still flagged unknown (suggestion={suggestion!r})"


def test_unknown_agent_sibling_still_unknown():
    # Control: registering two keys must not open the agent section to typos.
    is_known, _ = _validate_config_key("agent.runaway_max_bogus_sibling")
    assert not is_known


def test_defaults_match_watchdog_module():
    # Single-source-of-truth parity: DEFAULT_CONFIG mirrors the runtime fallbacks.
    assert DEFAULT_CONFIG["agent"]["runaway_max_synthetic_wakes"] == runaway_watchdog._DEFAULT_MAX_WAKES
    assert DEFAULT_CONFIG["agent"]["runaway_max_silent_hours"] == runaway_watchdog._DEFAULT_MAX_SILENT_HOURS
