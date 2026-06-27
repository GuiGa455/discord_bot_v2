import sqlite3
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from discord_bot_v2.database import Database, FarmChannel, Product
from discord_bot_v2.views import (
    FarmPanel,
    GoalBuilderView,
    GoalPeriodModal,
    GoalTargetModal,
    OutputQuantityModal,
    PeriodReportModal,
    ProductModal,
    ProductSelect,
    ProductSelectView,
    QuantityModal,
)


@pytest.fixture
def database(tmp_path) -> Database:
    repository = Database(str(tmp_path / "bot.db"))
    repository.initialize()
    return repository


def interaction(guild_id: int = 1) -> Mock:
    item = Mock(spec=discord.Interaction)
    item.guild_id = guild_id
    item.guild = None
    item.channel_id = 100
    item.channel = Mock(spec=discord.TextChannel)
    item.channel.send = AsyncMock()
    item.user = Mock(id=10)
    item.response = Mock()
    item.response.send_message = AsyncMock()
    item.response.send_modal = AsyncMock()
    item.response.defer = AsyncMock()
    item.delete_original_response = AsyncMock()
    return item


@pytest.mark.asyncio
async def test_product_modal_adds_product(database: Database) -> None:
    modal = ProductModal(database)
    modal.name._value = "Minério"
    item = interaction()

    with patch("discord_bot_v2.views._require_admin", AsyncMock(return_value=True)):
        await modal.on_submit(item)

    assert database.list_products(1)[0].name == "Minério"
    item.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_product_modal_rejects_duplicate(database: Database) -> None:
    database.add_product(1, "Madeira")
    modal = ProductModal(database)
    modal.name._value = "madeira"
    item = interaction()

    with patch("discord_bot_v2.views._require_admin", AsyncMock(return_value=True)):
        await modal.on_submit(item)

    message = item.response.send_message.await_args.args[0]
    assert "já está" in message


@pytest.mark.asyncio
async def test_quantity_modal_stores_decimal_and_actor(database: Database) -> None:
    product = database.add_product(1, "Petróleo")
    modal = QuantityModal(database, product, member_id=20)
    modal.quantity._value = "12,50"
    item = interaction()

    with patch("discord_bot_v2.views._is_administrator", return_value=True):
        await modal.on_submit(item)

    connection = sqlite3.connect(database.path)
    try:
        row = connection.execute(
            "SELECT member_id, actor_id, actor_was_admin, quantity FROM farm_entries"
        ).fetchone()
    finally:
        connection.close()
    assert row == (20, 10, 1, "12.50")
    item.channel.send.assert_not_awaited()
    item.delete_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_quantity_modal_rejects_invalid_value(database: Database) -> None:
    modal = QuantityModal(database, Product(1, "Madeira"), member_id=20)
    modal.quantity._value = "zero"
    item = interaction()

    await modal.on_submit(item)

    assert "maior que zero" in item.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_farm_panel_offers_current_products(database: Database) -> None:
    product = database.add_product(1, "Madeira")
    database.save_farm_channel(1, 10, 100)
    panel = FarmPanel(database)
    item = interaction()

    with patch("discord_bot_v2.views._is_administrator", return_value=False):
        await panel.register.callback(item)

    sent_view = item.response.send_message.await_args.kwargs["view"]
    assert isinstance(sent_view, ProductSelectView)
    assert sent_view.children[0].options[0].label == product.name


@pytest.mark.asyncio
async def test_farm_panel_rejects_unlinked_channel(database: Database) -> None:
    panel = FarmPanel(database)
    item = interaction()

    await panel.register.callback(item)

    assert "não está vinculado" in item.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_farm_panel_rejects_other_member(database: Database) -> None:
    panel = FarmPanel(database)
    item = interaction()
    with (
        patch.object(
            database,
            "get_farm_channel_by_channel",
            return_value=FarmChannel(1, 999, 100),
        ),
        patch("discord_bot_v2.views._is_administrator", return_value=False),
    ):
        await panel.register.callback(item)

    assert "não pode" in item.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_period_report_modal_returns_member_totals(database: Database) -> None:
    product = database.add_product(1, "Ferro")
    database.add_entry(
        guild_id=1,
        member_id=10,
        actor_id=10,
        actor_was_admin=False,
        product=product,
        quantity=Decimal("7.5"),
    )
    modal = PeriodReportModal(database, member_id=10)
    modal.start_date._value = "01/01/2026"
    modal.end_date._value = "31/12/2026"
    item = interaction()

    await modal.on_submit(item)

    embed = item.response.send_message.await_args.kwargs["embed"]
    assert "7.5" in embed.fields[0].value


@pytest.mark.asyncio
async def test_output_modal_updates_stock(database: Database) -> None:
    product = database.add_product(1, "Cobre")
    database.add_entry(
        guild_id=1,
        member_id=10,
        actor_id=10,
        actor_was_admin=False,
        product=product,
        quantity=Decimal("10"),
    )
    modal = OutputQuantityModal(database, product)
    modal.quantity._value = "2,5"
    item = interaction()

    with patch("discord_bot_v2.views._require_admin", AsyncMock(return_value=True)):
        await modal.on_submit(item)

    assert database.stock_totals(1)["Cobre"] == Decimal("7.5")


@pytest.mark.asyncio
async def test_goal_period_and_target_modals_build_goal(database: Database) -> None:
    product = database.add_product(1, "Diamante")
    period_modal = GoalPeriodModal(database)
    period_modal.start_date._value = "01/01/2026"
    period_modal.end_date._value = "31/12/2026"
    item = interaction()

    with patch("discord_bot_v2.views._require_admin", AsyncMock(return_value=True)):
        await period_modal.on_submit(item)

    builder = item.response.send_message.await_args.kwargs["view"]
    assert isinstance(builder, GoalBuilderView)

    builder_message = Mock(spec=discord.Message)
    builder_message.edit = AsyncMock()
    target_modal = GoalTargetModal(database, builder.goal_id, product, builder_message)
    target_modal.target._value = "20"
    target_interaction = interaction()
    with patch("discord_bot_v2.views._require_admin", AsyncMock(return_value=True)):
        await target_modal.on_submit(target_interaction)

    database.activate_goal(1, builder.goal_id)
    assert database.goal_progress(1, 10)[0].target == Decimal("20")
    builder_message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_product_selector_deletes_temporary_menu(database: Database) -> None:
    product = database.add_product(1, "Ferro")
    select = ProductSelect(database, [product], member_id=10)
    select._values = [str(product.id)]
    item = interaction()
    item.message = Mock(spec=discord.Message)
    item.message.delete = AsyncMock()

    await select.callback(item)

    item.response.send_modal.assert_awaited_once()
    item.message.delete.assert_awaited_once()
    item.delete_original_response.assert_awaited_once()
