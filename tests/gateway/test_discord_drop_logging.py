"""Drop-reason logging for the Discord mention gate (patch-0014).

Every mention-gate drop used to return silently at the gateway's running level —
indistinguishable from an event that never arrived (the 2026-10-22 research-bot role-ping
forensics). These tests pin the `admission:` INFO contract: one line per drop decision per
dispatch attempt, metadata only, and nothing logged for admitted traffic.

One-log structure (why (b)-then-(c) can never double-log): the recovered path's gate (b)
in `_dispatch_recovered_message()` short-circuits BEFORE `_discord_message_admission()`
runs, so a message dropped at (b) never reaches (c); a message that passes (b) can be
dropped at (c) — one line either way. The LIVE path is exactly-once per message id because
the dedup claim precedes every live drop site. Recovered-path re-logging across on_ready
re-scans (bounded by missed_message_backfill.max_attempts) is correct behaviour, not a
defect — do not add per-id bookkeeping in a logging-only patch.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from gateway.platforms.helpers import MessageDeduplicator
from plugins.platforms.discord.adapter import DiscordAdapter

BOT_ID = 99
CHANNEL_ID = 7
OWN_ROLE = 7001
GUILD_ID = 1


def _adapter(*, threads=()) -> DiscordAdapter:
    adapter = object.__new__(DiscordAdapter)
    adapter.platform = SimpleNamespace(value="discord")
    adapter.config = SimpleNamespace(extra={})
    adapter._client = SimpleNamespace(user=SimpleNamespace(id=BOT_ID, bot=True))
    adapter._dedup = MessageDeduplicator()
    adapter._threads = set(threads)
    adapter._get_parent_channel_id = Mock(return_value=None)
    adapter._discord_channel_keys = Mock(return_value={str(CHANNEL_ID)})
    adapter._discord_free_response_channels = Mock(return_value=set())
    adapter._voice_text_channels = {}
    adapter._is_allowed_user = Mock(return_value=True)
    adapter._allowed_role_ids = set()
    adapter._handle_message = AsyncMock(return_value=True)
    return adapter


def _msg(content: str, *, channel_id=CHANNEL_ID, held=(OWN_ROLE,), guild=True,
         thread=False, mentions=(), role_mentions=None):
    # Real discord.py Member.roles ALWAYS includes the guild's default (@everyone) role
    # (id == guild.id) — include it by default so everyone-guard tests assert against
    # reality, not a fixture that omits the very role the guard excludes.
    held = tuple(held) + (GUILD_ID,)
    channel = (
        Mock(spec=discord.Thread) if thread else SimpleNamespace(id=channel_id, parent_id=None)
    )
    channel.id = channel_id
    channel.parent_id = None
    if role_mentions is None:
        import re
        role_mentions = [SimpleNamespace(id=int(r)) for r in re.findall(r"<@&(\d+)>", content)]
    return SimpleNamespace(
        id=123,
        author=SimpleNamespace(id=42, bot=False),
        channel=channel,
        content=content,
        mentions=list(mentions),
        role_mentions=role_mentions,
        guild=(
            SimpleNamespace(
                id=GUILD_ID,
                me=SimpleNamespace(roles=[SimpleNamespace(id=r) for r in held]),
            )
            if guild
            else None
        ),
        type=discord.MessageType.default,
    )


# ---- live path (a): require_mention drop logs -------------------------------

@pytest.mark.asyncio
async def test_require_mention_drop_logs_one_line(monkeypatch, caplog):
    monkeypatch.delenv("DISCORD_REQUIRE_MENTION", raising=False)
    monkeypatch.delenv("DISCORD_IGNORE_NO_MENTION", raising=False)
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    del adapter._handle_message  # run the REAL live-path handler, not the recovered-path mock
    adapter._discord_message_admission = Mock(return_value=(True, False))  # isolate the (a) drop
    message = _msg("hello there")  # no mention of anyone

    with caplog.at_level("INFO", logger="plugins.platforms.discord.adapter"):
        handled = await adapter._handle_message(message, role_authorized=False)

    assert handled is False
    lines = [r for r in caplog.records if "admission:" in r.message]
    assert len(lines) == 1, [r.message for r in caplog.records]
    rec = lines[0]
    assert rec.levelno == 20  # INFO — visible at the gateway's running level
    assert "reason=require_mention_not_addressed" in rec.message
    assert f"msg={message.id}" in rec.message
    assert f"chan={CHANNEL_ID}" in rec.message
    assert "author=42" in rec.message
    assert "hello" not in rec.message  # metadata only, never content


@pytest.mark.asyncio
async def test_admitted_role_ping_logs_nothing(monkeypatch, caplog):
    monkeypatch.delenv("DISCORD_REQUIRE_MENTION", raising=False)
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    del adapter._handle_message  # run the REAL handler — the AsyncMock default would make
    #                           # the silence assertion below vacuously true
    message = _msg(f"<@&{OWN_ROLE}> check this raster")

    with caplog.at_level("INFO", logger="plugins.platforms.discord.adapter"):
        # Admission must pass for a held-role ping before _handle_message runs its gates;
        # mock it True so we reach the require_mention block with mention_prefix set.
        handled = await adapter._handle_message(message, role_authorized=False)

    # The adapter may drop later for unrelated mocked-surface reasons, but the mention
    # gate must NOT have logged a drop: the message IS addressed.
    drops = [r for r in caplog.records if "admission: drop" in r.message
             and "require_mention_not_addressed" in r.message]
    assert drops == []


@pytest.mark.asyncio
async def test_bot_thread_exempt_no_log(monkeypatch, caplog):
    """The shared `_in_bot_thread` exemption skips the gate — pinned on the recovered
    path (same helper the live path consults at adapter.py:6305) with minimal surface."""
    monkeypatch.delenv("DISCORD_REQUIRE_MENTION", raising=False)
    monkeypatch.delenv("DISCORD_THREAD_REQUIRE_MENTION", raising=False)
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter(threads={str(CHANNEL_ID)})
    adapter._discord_message_admission = Mock(return_value=(True, False))
    message = _msg("plain message", thread=True)

    with caplog.at_level("INFO", logger="plugins.platforms.discord.adapter"):
        dispatched = await adapter._dispatch_recovered_message(message)

    assert dispatched is True
    assert [r for r in caplog.records if "admission:" in r.message] == []


# ---- recovered path (b): backfill drop logs ---------------------------------

@pytest.mark.asyncio
async def test_backfill_drop_logs_one_line(monkeypatch, caplog):
    monkeypatch.delenv("DISCORD_REQUIRE_MENTION", raising=False)
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    message = _msg("no address at all")

    with caplog.at_level("INFO", logger="plugins.platforms.discord.adapter"):
        dispatched = await adapter._dispatch_recovered_message(message)

    assert dispatched is False
    lines = [r for r in caplog.records if "admission:" in r.message]
    assert len(lines) == 1, [r.message for r in caplog.records]
    assert "reason=backfill_require_mention_not_addressed" in lines[0].message


@pytest.mark.asyncio
async def test_admission_mentions_others_drop_logs_one_line(monkeypatch, caplog):
    """Site (c) on the live path: a HUMAN third party is mentioned, we are not — one line.
    (Mentioning another BOT exits via the silent 1770 veto, documented out of scope.)
    On the RECOVERED path this site is unreachable for this reason class (gate (b)
    drops not-self-addressed messages before admission runs), which is exactly why the
    one-log-per-attempt rule holds structurally there."""
    monkeypatch.delenv("DISCORD_IGNORE_NO_MENTION", raising=False)
    adapter = _adapter()
    human = SimpleNamespace(id=880, bot=False)
    message = _msg(f"<@880> do a thing", mentions=[human])

    with caplog.at_level("INFO", logger="plugins.platforms.discord.adapter"):
        admitted, _ = adapter._discord_message_admission(message, claim=True)

    assert admitted is False
    lines = [r for r in caplog.records if "admission:" in r.message]
    assert len(lines) == 1, [r.message for r in caplog.records]
    assert "reason=mentions_others_not_self" in lines[0].message
    assert f"chan={CHANNEL_ID}" in lines[0].message
    assert "author=42" in lines[0].message


@pytest.mark.asyncio
async def test_backfill_passing_gate_b_then_admission_veto_never_two_lines(monkeypatch, caplog):
    """Passes (b) (held-role ping), dropped by admission's bot-author gate — a silent
    admission return OUTSIDE the mention-gate class. Must log ZERO admission: lines
    (never-two holds; the boundary is documented, not instrumented)."""
    monkeypatch.delenv("DISCORD_REQUIRE_MENTION", raising=False)
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    adapter._get_allow_bots = Mock(return_value="none")
    message = _msg(f"<@&{OWN_ROLE}> hi", held=(OWN_ROLE,))
    message.author = SimpleNamespace(id=555, bot=True)

    with caplog.at_level("INFO", logger="plugins.platforms.discord.adapter"):
        dispatched = await adapter._dispatch_recovered_message(message)

    assert dispatched is False
    assert [r for r in caplog.records if "admission:" in r.message] == []


# ---- everyone-role guard (leg-B round-1 fold) --------------------------------

def test_everyone_role_never_addresses(monkeypatch):
    """@everyone is always in guild.me.roles; a crafted <@&guild_id> token must not
    count as addressing the bot (group ping must not wake us)."""
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    message = _msg(f"<@&{GUILD_ID}> hello everyone")

    assert adapter._self_is_role_mentioned(message) is False


def test_everyone_role_excluded_from_held_set(monkeypatch):
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    message = _msg("x")

    held = adapter._self_role_ids(message)
    assert str(GUILD_ID) not in held
    assert str(OWN_ROLE) in held
