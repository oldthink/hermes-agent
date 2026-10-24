"""Alibaba Cloud DashScope provider profiles (intl + CN, plus the Model Studio
Token Plan flat-token tier with its own key/endpoints — one module per vendor).

Profile names match models.dev catalog keys exactly so model metadata lines up
and ``model.provider: alibaba-cn`` resolves at runtime.
"""

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

# Top-level ``reasoning_effort`` levels the Token Plan compatible-mode endpoint accepts for
# qwen3 text models (live-probed 2026-10-21: minimal/low/medium/high/xhigh/max all HTTP 200
# and honored — 12k -> 2.9k reasoning tokens on a 10k-token review brief at low; the nested
# OpenAI-style ``extra_body.reasoning.effort`` shape is ACCEPTED but silently IGNORED).
_TOKEN_PLAN_QWEN_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")


class AlibabaTokenPlanProfile(ProviderProfile):
    """Token Plan profile with a qwen-only reasoning adapter (local patch 0012).

    The vanilla profile shipped no reasoning projection, so Hermes' ``--reasoning`` /
    ``agent.reasoning_effort`` / ``reasoning_overrides`` pins never reached the wire for
    ANY model on this endpoint — qwen legs rode the server-default thinking depth
    (~12k reasoning tokens, ~7 min per review brief). The adapter maps an explicit
    reasoning config onto the top-level ``reasoning_effort`` field the endpoint honors.

    Deliberately qwen3-text-only: deepseek / glm / qwen-audio / wan ids on this same
    endpoint keep the exact vanilla wire shape (byte-identical payloads) until each is
    probed separately — leg B (deepseek-v4.1-flash-1017) must not change behavior.
    """

    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, model: str | None = None, **context
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        top_level: dict[str, Any] = {}
        # Gate 1: no user/system preference expressed -> omit everything (vanilla wire).
        if not isinstance(reasoning_config, dict):
            return extra_body, top_level
        # Gate 2: qwen3 text models only (qwen-audio-*/wan*/glm*/deepseek* stay vanilla).
        if not str(model or "").lower().startswith("qwen3"):
            return extra_body, top_level
        enabled = reasoning_config.get("enabled") is not False
        effort = str(reasoning_config.get("effort") or "").strip().lower()
        if not enabled or effort == "none":
            # DashScope-native off switch (live-probed honored: 0 reasoning tokens).
            extra_body["enable_thinking"] = False
            return extra_body, top_level
        if effort not in _TOKEN_PLAN_QWEN_EFFORTS:
            effort = "medium"  # defensive: unseen levels (ultra is clamped upstream) fold to medium
        top_level["reasoning_effort"] = effort
        return extra_body, top_level


alibaba = ProviderProfile(
    name="alibaba", aliases=("dashscope", "alibaba-cloud", "qwen-dashscope", "aliyun"), env_vars=("DASHSCOPE_API_KEY",),
    base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
)

alibaba_cn = ProviderProfile(
    name="alibaba-cn", aliases=("dashscope-cn", "alibaba-cloud-cn"),
    display_name="Alibaba Cloud DashScope (China)",
    description="Alibaba Cloud DashScope, mainland-China endpoint",
    env_vars=("DASHSCOPE_API_KEY", "DASHSCOPE_CN_BASE_URL"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

alibaba_token_plan = AlibabaTokenPlanProfile(
    name="alibaba-token-plan", aliases=("dashscope-token-plan",), display_name="Alibaba Cloud (Token Plan)",
    description="Alibaba Cloud Model Studio Token Plan (flat-token tier)",
    signup_url="https://help.aliyun.com/zh/model-studio/",
    env_vars=("ALIBABA_TOKEN_PLAN_API_KEY", "ALIBABA_TOKEN_PLAN_BASE_URL"),
    base_url="https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1", auth_type="api_key",
)

alibaba_token_plan_cn = ProviderProfile(
    name="alibaba-token-plan-cn", aliases=("dashscope-token-plan-cn",),
    display_name="Alibaba Cloud (Token Plan, China)",
    description="Alibaba Cloud Model Studio Token Plan, mainland-China endpoint",
    signup_url="https://help.aliyun.com/zh/model-studio/",
    env_vars=("ALIBABA_TOKEN_PLAN_CN_API_KEY", "ALIBABA_TOKEN_PLAN_API_KEY", "ALIBABA_TOKEN_PLAN_CN_BASE_URL"),
    base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", auth_type="api_key",
)

register_provider(alibaba)
register_provider(alibaba_cn)
register_provider(alibaba_token_plan)
register_provider(alibaba_token_plan_cn)
