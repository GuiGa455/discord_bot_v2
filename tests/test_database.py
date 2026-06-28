from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from discord_bot_v2.database import Database


def test_database_stores_products_channels_and_entries(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()

    product = database.add_product(1, "  Minério   de ferro ")
    database.save_farm_channel(1, 10, 100)
    entry_id = database.add_entry(
        guild_id=1,
        member_id=10,
        actor_id=20,
        actor_was_admin=True,
        product=product,
        quantity=Decimal("12.50"),
    )

    assert database.list_products(1) == [product]
    assert database.get_farm_channel(1, 10).channel_id == 100
    assert database.get_farm_channel_by_channel(100).member_id == 10
    assert entry_id == 1


def test_database_removes_product_without_losing_entry_history(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    product = database.add_product(1, "Madeira")
    database.add_entry(
        guild_id=1,
        member_id=10,
        actor_id=10,
        actor_was_admin=False,
        product=product,
        quantity=Decimal("1.25"),
    )

    assert database.remove_product(1, product.id) is True
    assert database.list_products(1) == []


def test_stock_totals_and_outputs(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    product = database.add_product(1, "Cobre")
    database.add_entry(
        guild_id=1,
        member_id=10,
        actor_id=10,
        actor_was_admin=False,
        product=product,
        quantity=Decimal("10.5"),
    )

    output_id = database.add_output(
        guild_id=1,
        actor_id=99,
        product=product,
        quantity=Decimal("2.25"),
        reason="Venda",
    )

    assert output_id == 1
    assert database.stock_totals(1) == {"Cobre": Decimal("8.25")}
    with pytest.raises(ValueError, match="Estoque insuficiente"):
        database.add_output(
            guild_id=1,
            actor_id=99,
            product=product,
            quantity=Decimal("9"),
            reason="Venda",
        )


def test_product_totals_filter_member_and_period(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    product = database.add_product(1, "Diamante")
    database.add_entry(
        guild_id=1,
        member_id=10,
        actor_id=10,
        actor_was_admin=False,
        product=product,
        quantity=Decimal("3"),
    )
    now = datetime.now(UTC)

    assert database.product_totals(
        1,
        member_id=10,
        start_at=(now - timedelta(minutes=1)).isoformat(),
        end_at=(now + timedelta(minutes=1)).isoformat(),
    ) == {"Diamante": Decimal("3")}
    assert database.product_totals(1, member_id=20) == {}


def test_goal_progress_and_panel_locations(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    product = database.add_product(1, "Ferro")
    start = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    end = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    goal = database.create_goal(1, start, end)
    database.set_goal_item(goal.id, product, Decimal("100"))
    database.activate_goal(1, goal.id)
    database.save_farm_channel(1, 10, 100, 1000)
    database.save_admin_panel(1, 200, 2000)
    database.add_entry(
        guild_id=1,
        member_id=10,
        actor_id=10,
        actor_was_admin=False,
        product=product,
        quantity=Decimal("40"),
    )

    active = database.get_active_goal(1)
    progress = database.goal_progress(1, 10)

    assert active == goal.__class__(goal.id, 1, start, end, "active")
    assert progress[0].target == Decimal("100")
    assert progress[0].current == Decimal("40")
    assert database.list_farm_channels(1)[0].panel_message_id == 1000
    assert database.list_admin_panels(1) == [(200, 2000)]


def test_goal_requires_an_item(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    goal = database.create_goal(1, "2026-01-01", "2026-01-31")

    with pytest.raises(ValueError, match="pelo menos um produto"):
        database.activate_goal(1, goal.id)


def test_close_active_goal(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    product = database.add_product(1, "Cobre")
    goal = database.create_goal(1, "2026-01-01", "2026-01-31")
    database.set_goal_item(goal.id, product, Decimal("10"))
    database.activate_goal(1, goal.id)

    assert database.close_active_goal(1) is True
    assert database.get_active_goal(1) is None
    assert database.close_active_goal(1) is False


def test_update_goal_period(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    goal = database.create_goal(1, "2026-01-01", "2026-01-31")

    database.update_goal_period(goal.id, "2026-02-01", "2026-02-28")
    with database._connect() as connection:
        row = connection.execute(
            "SELECT start_at, end_at FROM goals WHERE id = ?", (goal.id,)
        ).fetchone()

    assert tuple(row) == ("2026-02-01", "2026-02-28")


def test_log_channels_and_deleted_farm_link(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    database.set_log_channels(1, 200, 300)
    database.save_farm_channel(1, 10, 100, 1000)

    channels = database.get_log_channels(1)
    removed = database.delete_farm_channel(100)

    assert channels.entry_channel_id == 200
    assert channels.output_channel_id == 300
    assert removed.member_id == 10
    assert database.list_farm_channels(1) == []
    assert database.delete_farm_channel(100) is None
