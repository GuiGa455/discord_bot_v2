from datetime import UTC, datetime, timedelta
from decimal import Decimal

from discord_bot_v2.database import Database, GoalProgress, Product
from discord_bot_v2.reporting import (
    build_admin_embed,
    build_farm_embed,
    build_sales_embed,
    display_period,
    format_progress,
    format_totals,
    parse_period,
    progress_bar,
    sales_period,
)


def test_parse_and_display_brazilian_period() -> None:
    start, end = parse_period("01/06/2026", "30/06/2026")

    assert start.startswith("2026-06-01T03:00:00")
    assert end.startswith("2026-07-01T02:59:59")
    assert display_period(start, end) == "01/06/2026 a 30/06/2026"


def test_report_formatters() -> None:
    item = GoalProgress(Product(1, "Ferro"), Decimal("100"), Decimal("50"))

    assert progress_bar(Decimal("50"), Decimal("100")) == "●●●●●○○○○○"
    assert "50 / 100" in format_progress([item])
    assert "Ferro" in format_totals({"Ferro": Decimal("20")})
    assert "Nenhum" in format_totals({})


def test_build_panel_embeds(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    product = database.add_product(1, "Ferro")
    start = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    end = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    goal = database.create_goal(1, start, end)
    database.set_goal_item(goal.id, product, Decimal("10"))
    database.activate_goal(1, goal.id)
    database.save_farm_channel(1, 10, 100, 1000)
    database.add_entry(
        guild_id=1,
        member_id=10,
        actor_id=10,
        actor_was_admin=False,
        product=product,
        quantity=Decimal("10"),
    )

    farm_embed = build_farm_embed(database, 1, 10)
    admin_embed = build_admin_embed(database, 1)

    assert "✅" in farm_embed.fields[0].value
    assert "100.0%" in admin_embed.fields[3].value
    assert "Proporcional ao progresso" in admin_embed.fields[2].value


def test_sales_periods_and_report(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    product = database.add_product(1, "Ferro", Decimal("20"))
    database.add_entry(
        guild_id=1,
        member_id=10,
        actor_id=10,
        actor_was_admin=False,
        product=product,
        quantity=Decimal("5"),
    )
    database.register_sale(guild_id=1, actor_id=99, product=product, quantity=Decimal("2"))

    day_start, _, day_label = sales_period("day")
    month_start, _, month_label = sales_period("month")
    week_start, _, week_label = sales_period("week")
    embed = build_sales_embed(database, 1, "week")

    assert "Hoje" in day_label
    assert "Mês atual" in month_label
    assert "Semana atual" in week_label
    assert day_start >= month_start
    assert week_start
    assert "$40.00" in embed.fields[0].value
    assert "Ferro" in embed.fields[2].value
    assert "█" in embed.fields[3].value
