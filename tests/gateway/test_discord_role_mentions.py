"""A ping of a role the bot holds addresses it, on every ingress path that requires a mention.

Discord renders role pings as ``<@&ROLE_ID>``; ``_self_is_explicitly_mentioned()`` only parses
user mentions, so ``<@&role> Raster question...`` in a mention-required channel was dropped
with no log line at all (the operator cannot even tell it apart from a gateway that never got
the event). Patch-0013 makes held-role pings count and still keeps bot authors on literal
``<@thisbot>`` tokens.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from gateway.platforms.helpers import MessageDeduplicator
from plugins.platforms.discord.adapter import DiscordAdapter

BOT_ID = 99
OWN_ROLE = 7001      # managed role named after the bot; the bot holds it
SHARED_ROLE = 7002   # held by every bot in the guild (e.g. "Definitely Not Sentient")
FOREIGN_ROLE = 7003  # exists, but this bot is not a member
CHANNEL_ID = 7


def _adapter(*, extra=None) -> DiscordAdapter:
    adapter = object.__new__(DiscordAdapter)
    adapter.platform = SimpleNamespace(value="discord")
    adapter.config = SimpleNamespace(extra=extra or {})
    adapter._client = SimpleNamespace(user=SimpleNamespace(id=BOT_ID, bot=True))
    adapter._dedup = MessageDeduplicator()
    adapter._threads = set()
    adapter._get_parent_channel_id = Mock(return_value=None)
    adapter._discord_channel_keys = Mock(return_value={str(CHANNEL_ID)})
    adapter._discord_free_response_channels = Mock(return_value=set())
    adapter._handle_message = AsyncMock(return_value=True)
    return adapter


def _msg(content: str, *, held=(OWN_ROLE, SHARED_ROLE), guild=True, role_mentions=()):
    return SimpleNamespace(
        id=123,
        author=SimpleNamespace(id=42, bot=False),
        channel=SimpleNamespace(id=CHANNEL_ID, parent_id=None),
        content=content,
        mentions=[],
        role_mentions=[SimpleNamespace(id=rid) for rid in role_mentions],
        guild=(
            SimpleNamespace(me=SimpleNamespace(roles=[SimpleNamespace(id=r) for r in held]))
            if guild
            else None
        ),
        type=discord.MessageType.default,
    )


ROLE_PING = f"<@&{OWN_ROLE}> Raster with empty values question - can you check and fill them in?"
USER_PING = f"<@{BOT_ID}> same question"


# ---- detection -------------------------------------------------------------

def test_held_role_ping_is_a_mention(monkeypatch):
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    assert adapter._self_is_role_mentioned(_msg(ROLE_PING)) is True
    # ...and it is still not a *user* mention: the bot-author gates stay untouched.
    assert adapter._self_is_explicitly_mentioned(_msg(ROLE_PING)) is False


def test_foreign_role_ping_is_not_a_mention(monkeypatch):
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    assert _adapter()._self_is_role_mentioned(_msg(f"<@&{FOREIGN_ROLE}> hi")) is False


def test_dm_has_no_roles_so_a_role_ping_never_matches(monkeypatch):
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    assert _adapter()._self_is_role_mentioned(_msg(ROLE_PING, guild=False)) is False


def test_role_mentions_list_alone_is_enough(monkeypatch):
    """Edited/relayed payloads can carry the parsed list without the raw token."""
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    assert _adapter()._self_is_role_mentioned(
        _msg("Raster question", role_mentions=[OWN_ROLE])
    ) is True


def test_flag_off_disables_role_addressing(monkeypatch):
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    assert _adapter(extra={"role_mentions": "false"})._self_is_role_mentioned(_msg(ROLE_PING)) is False
    monkeypatch.setenv("DISCORD_ROLE_MENTIONS", "off")
    assert _adapter()._self_is_role_mentioned(_msg(ROLE_PING)) is False


def test_csv_pin_limits_which_held_roles_count(monkeypatch):
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter(extra={"role_mentions": str(OWN_ROLE)})
    assert adapter._self_is_role_mentioned(_msg(ROLE_PING)) is True
    # The guild-wide role is held too, but it was not listed: a group ping must not wake us.
    assert adapter._self_is_role_mentioned(_msg(f"<@&{SHARED_ROLE}> hi")) is False


# ---- prompt hygiene --------------------------------------------------------

def test_role_ping_is_stripped_from_the_prompt(monkeypatch):
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    assert adapter._strip_self_mention_tokens(ROLE_PING, _msg(ROLE_PING)) == (
        "Raster with empty values question - can you check and fill them in?"
    )


def test_user_mention_still_stripped(monkeypatch):
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    assert adapter._strip_self_mention_tokens(USER_PING, _msg(USER_PING)) == "same question"


def test_foreign_role_ping_is_left_alone_in_the_prompt(monkeypatch):
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    text = f"<@&{FOREIGN_ROLE}> ask them"
    assert adapter._strip_self_mention_tokens(text, _msg(text)) == text


def test_non_addressing_held_role_is_preserved_under_csv_pin_or_flag_off(monkeypatch):
    """When role_mentions pins only OWN_ROLE (or is disabled), a held SHARED_ROLE token
    in the same message did not address us and must stay intact in the prompt."""
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    csv_adapter = _adapter(extra={"role_mentions": str(OWN_ROLE)})
    both = f"<@&{OWN_ROLE}> tell <@&{SHARED_ROLE}> to check this"
    assert csv_adapter._strip_self_mention_tokens(both, _msg(both)) == (
        f"tell <@&{SHARED_ROLE}> to check this"
    )
    off_adapter = _adapter(extra={"role_mentions": "false"})
    user_and_role = f"<@{BOT_ID}> tell <@&{SHARED_ROLE}> to check this"
    assert off_adapter._strip_self_mention_tokens(user_and_role, _msg(user_and_role)) == (
        f"tell <@&{SHARED_ROLE}> to check this"
    )


# ---- ingress ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovered_ingress_admits_a_role_ping(monkeypatch):
    """The missed-message backfill runs its own mention gate; it must agree with on_message."""
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    adapter._discord_message_admission = Mock(return_value=(True, False))
    message = _msg(ROLE_PING)

    assert await adapter._dispatch_recovered_message(message) is True
    adapter._handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovered_ingress_still_drops_an_unaddressed_message(monkeypatch):
    monkeypatch.delenv("DISCORD_ROLE_MENTIONS", raising=False)
    adapter = _adapter()
    adapter._discord_message_admission = Mock(return_value=(True, False))

    assert await adapter._dispatch_recovered_message(_msg(f"<@&{FOREIGN_ROLE}> ask them")) is False
    adapter._handle_message.assert_not_awaited()