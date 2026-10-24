"""Unit tests for the Alibaba Token Plan provider profile's reasoning wiring (patch 0012).

Live-probed contract (2026-10-21, token-plan.ap-southeast-1.maas.aliyuncs.com):

* The compatible-mode endpoint HONORS a top-level ``reasoning_effort`` for qwen3 text
  models — every level minimal..max returns 200 and measurably throttles thinking
  (12,066 -> 2,935 reasoning tokens on a 10k-token review brief, 419.6 s -> 113.2 s).
* The nested OpenAI-style ``extra_body.reasoning.effort`` shape is ACCEPTED but
  silently IGNORED (389.1 s, 12,094 reasoning tokens — identical to bare).
* ``enable_thinking: false`` is the honored off switch (34.2 s, 0 reasoning tokens).
* The pre-adapter profile emitted NO reasoning field at all, so every Hermes reasoning
  pin (``--reasoning``, ``agent.reasoning_effort``, ``reasoning_overrides``) was a
  silent no-op on this endpoint.

These tests pin the adapter's wire shape so qwen legs keep shipping the honored
``reasoning_effort`` field, while deepseek / glm / qwen-audio / wan ids on the same
endpoint keep the exact vanilla payload (byte-identical, leg B must not drift).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def token_plan_profile():
    """Resolve the registered Token Plan profile through the real discovery path."""
    import model_tools  # noqa: F401  (registers plugin providers on import)
    import providers

    profile = providers.get_provider_profile("alibaba-token-plan")
    assert profile is not None, "alibaba-token-plan provider profile must be registered"
    return profile


class TestTokenPlanQwenReasoningWireShape:
    """``build_api_kwargs_extras`` maps explicit reasoning configs onto the honored fields."""

    def test_no_preference_omits_everything(self, token_plan_profile):
        """No reasoning_config -> vanilla wire (server-default thinking), matching the
        pre-adapter behavior for callers that express no preference."""
        extra_body, top_level = token_plan_profile.build_api_kwargs_extras(
            reasoning_config=None, model="qwen3.8-max"
        )
        assert extra_body == {}
        assert top_level == {}

    def test_low_effort_ships_top_level_field(self, token_plan_profile):
        """The probe's headline case: explicit low pin -> top-level reasoning_effort=low
        (3.7x faster, 4x fewer thinking tokens, identical verdict quality)."""
        extra_body, top_level = token_plan_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "low"}, model="qwen3.8-max"
        )
        assert extra_body == {}
        assert top_level == {"reasoning_effort": "low"}

    def test_all_ladder_levels_pass_through(self, token_plan_profile):
        for level in ("minimal", "low", "medium", "high", "xhigh", "max"):
            extra_body, top_level = token_plan_profile.build_api_kwargs_extras(
                reasoning_config={"enabled": True, "effort": level}, model="qwen3.8-max"
            )
            assert top_level == {"reasoning_effort": level}
            assert extra_body == {}

    def test_disabled_sends_dashscope_off_switch(self, token_plan_profile):
        """``enabled: False`` -> ``enable_thinking: false`` (probed: 0 reasoning tokens)."""
        extra_body, top_level = token_plan_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": False}, model="qwen3.8-max"
        )
        assert extra_body == {"enable_thinking": False}
        assert top_level == {}

    def test_effort_none_sends_off_switch(self, token_plan_profile):
        extra_body, top_level = token_plan_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "none"}, model="qwen3.8-max"
        )
        assert extra_body == {"enable_thinking": False}
        assert top_level == {}

    def test_unknown_level_folds_to_medium(self, token_plan_profile):
        """Defensive fold: an unclamped level (ultra is normally clamped upstream by
        ``clamp_reasoning_config``) must never ship verbatim to this endpoint."""
        extra_body, top_level = token_plan_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "ultra"}, model="qwen3.8-max"
        )
        assert top_level == {"reasoning_effort": "medium"}

    def test_enabled_without_effort_ships_medium(self, token_plan_profile):
        extra_body, top_level = token_plan_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True}, model="qwen3.8-max"
        )
        assert top_level == {"reasoning_effort": "medium"}
        assert extra_body == {}


class TestTokenPlanNonQwenModelsStayVanilla:
    """Leg B (deepseek) and every non-qwen id must keep the exact pre-adapter payload."""

    @pytest.mark.parametrize("model", [
        "deepseek-v4.1-flash-1017",  # dual-review leg B — byte-identical wire required
        "deepseek-v4-pro",
        "glm-5.3",
        "qwen-audio-3.0-tts-plus",   # qwen- prefix but NOT a qwen3 text model
        "wan2.7-image",
    ])
    def test_non_qwen3_ids_emit_nothing(self, token_plan_profile, model):
        extra_body, top_level = token_plan_profile.build_api_kwargs_extras(
            reasoning_config={"enabled": True, "effort": "low"}, model=model
        )
        assert extra_body == {}
        assert top_level == {}

    def test_unset_on_non_qwen_stays_vanilla(self, token_plan_profile):
        extra_body, top_level = token_plan_profile.build_api_kwargs_extras(
            reasoning_config=None, model="deepseek-v4.1-flash-1017"
        )
        assert extra_body == {}
        assert top_level == {}


class TestTokenPlanTransportIntegration:
    """The adapter must surface in the kwargs the chat-completions transport actually sends."""

    def test_transport_ships_reasoning_effort_for_qwen_leg(self, token_plan_profile):
        import providers
        from agent.transports.chat_completions import ChatCompletionsTransport

        transport = ChatCompletionsTransport()
        messages = [{"role": "user", "content": "Review this plan."}]
        kwargs = transport.build_kwargs(
            "qwen3.8-max",
            messages,
            tools=None,
            provider_profile=providers.get_provider_profile("alibaba-token-plan"),
            base_url="https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
            reasoning_config={"enabled": True, "effort": "low"},
            supports_reasoning=True,
            temperature=None,
        )
        assert kwargs.get("reasoning_effort") == "low"
        assert "extra_body" not in kwargs or "reasoning" not in kwargs.get("extra_body", {})

    def test_transport_bare_for_deepseek_leg_b(self, token_plan_profile):
        """Leg B regression guard: reasoning pin present but model is deepseek -> the
        payload carries NO reasoning field of any shape (pre-adapter byte-identity)."""
        import providers
        from agent.transports.chat_completions import ChatCompletionsTransport

        transport = ChatCompletionsTransport()
        messages = [{"role": "user", "content": "Review this plan."}]
        kwargs = transport.build_kwargs(
            "deepseek-v4.1-flash-1017",
            messages,
            tools=None,
            provider_profile=providers.get_provider_profile("alibaba-token-plan"),
            base_url="https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
            reasoning_config={"enabled": True, "effort": "high"},
            supports_reasoning=True,
            temperature=None,
        )
        assert "reasoning_effort" not in kwargs
        assert "extra_body" not in kwargs
