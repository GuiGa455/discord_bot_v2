from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from discord_bot_v2.bot import DiscordBot, create_bot, run
from discord_bot_v2.config import Settings


def test_create_bot_applies_configured_intents() -> None:
    settings = Settings("token", frozenset({"guilds"}))

    bot = create_bot(settings)

    assert bot.intents.guilds is True
    assert bot.intents.message_content is False


@pytest.mark.asyncio
async def test_on_message_replies_to_greeting() -> None:
    bot = create_bot(Settings("token"))
    message = Mock()
    message.author = object()
    message.content = "!oi pessoal"
    message.channel.send = AsyncMock()

    await bot.on_message(message)

    message.channel.send.assert_awaited_once_with("Olá! 👋")


@pytest.mark.asyncio
async def test_slash_oi_replies_to_interaction() -> None:
    bot = create_bot(Settings("token"))
    interaction = Mock()
    interaction.response.send_message = AsyncMock()

    await bot.slash_oi(interaction)

    interaction.response.send_message.assert_awaited_once_with("Olá! 👋")


def test_run_starts_client_with_token() -> None:
    settings = Settings("secret")
    client = Mock(spec=DiscordBot)
    with (
        patch("discord_bot_v2.bot.Settings.from_env", return_value=settings),
        patch("discord_bot_v2.bot.configure_logging") as configure,
        patch("discord_bot_v2.bot.create_bot", return_value=client),
    ):
        run()

    configure.assert_called_once_with("INFO")
    client.run.assert_called_once_with("secret", log_handler=None)


def test_run_explains_invalid_token() -> None:
    settings = Settings("invalid")
    client = Mock(spec=DiscordBot)
    client.run.side_effect = discord.LoginFailure("invalid token")
    with (
        patch("discord_bot_v2.bot.Settings.from_env", return_value=settings),
        patch("discord_bot_v2.bot.configure_logging"),
        patch("discord_bot_v2.bot.create_bot", return_value=client),
        pytest.raises(RuntimeError, match="token"),
    ):
        run()
