from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from discord_bot_v2.audit import (
    send_cash_log,
    send_entry_log,
    send_output_log,
    send_stock_input_log,
)
from discord_bot_v2.database import Database, Product


@pytest.mark.asyncio
async def test_sends_entry_and_output_to_configured_channels(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    database.set_log_channels(1, 200, 300)
    database.set_cash_log_channel(1, 400)
    entry_channel = Mock(spec=discord.TextChannel)
    entry_channel.send = AsyncMock()
    entry_channel.permissions_for.return_value.embed_links = True
    output_channel = Mock(spec=discord.TextChannel)
    output_channel.send = AsyncMock()
    output_channel.permissions_for.return_value.embed_links = False
    cash_channel = Mock(spec=discord.TextChannel)
    cash_channel.send = AsyncMock()
    cash_channel.permissions_for.return_value.embed_links = True
    guild = Mock(spec=discord.Guild)
    guild.id = 1
    guild.get_channel.side_effect = lambda channel_id: {
        200: entry_channel,
        300: output_channel,
        400: cash_channel,
    }.get(channel_id)
    product = Product(1, "Ferro")

    await send_entry_log(
        guild=guild,
        database=database,
        member_id=10,
        actor_id=20,
        actor_was_admin=True,
        product=product,
        quantity=Decimal("5.5"),
        entry_id=1,
    )
    await send_output_log(
        guild=guild,
        database=database,
        actor_id=20,
        product=product,
        quantity=Decimal("2"),
        reason="Venda para cliente",
        output_id=1,
    )
    await send_stock_input_log(
        guild=guild,
        database=database,
        actor_id=20,
        product=product,
        quantity=Decimal("3"),
        input_id=2,
    )
    await send_cash_log(
        guild=guild,
        database=database,
        title="Entrada no caixa",
        amount=Decimal("100"),
        reason="Venda",
        actor_id=20,
        balance=Decimal("100"),
        color=discord.Color.green(),
    )

    assert entry_channel.send.await_args.kwargs["embed"].title == "📥 Entrada no estoque"
    assert entry_channel.send.await_count == 2
    assert entry_channel.send.await_args.kwargs["embed"].footer.text == "Movimentação #2"
    assert "Saída #1" in output_channel.send.await_args.args[0]
    assert cash_channel.send.await_args.kwargs["embed"].title == "Entrada no caixa"


@pytest.mark.asyncio
async def test_skips_logs_when_channels_are_not_configured(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    guild = Mock(spec=discord.Guild)
    guild.id = 1

    await send_entry_log(
        guild=guild,
        database=database,
        member_id=10,
        actor_id=20,
        actor_was_admin=False,
        product=Product(1, "Ferro"),
        quantity=Decimal("1"),
        entry_id=1,
    )

    guild.get_channel.assert_not_called()
