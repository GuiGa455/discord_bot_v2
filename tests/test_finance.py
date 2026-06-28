from decimal import Decimal

import pytest

from discord_bot_v2.database import Database
from discord_bot_v2.finance import build_settlement_preview, money


def prepared_database(tmp_path) -> tuple[Database, int]:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    iron = database.add_product(1, "Ferro")
    copper = database.add_product(1, "Cobre")
    goal = database.create_goal(1, "2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00")
    database.set_goal_item(goal.id, iron, Decimal("100"))
    database.set_goal_item(goal.id, copper, Decimal("20"))
    database.activate_goal(1, goal.id)
    database.save_farm_channel(1, 10, 100)
    database.save_farm_channel(1, 20, 200)
    for member_id, iron_amount, copper_amount in (
        (10, Decimal("100"), Decimal("20")),
        (20, Decimal("50"), Decimal("10")),
    ):
        database.add_entry(
            guild_id=1,
            member_id=member_id,
            actor_id=member_id,
            actor_was_admin=False,
            product=iron,
            quantity=iron_amount,
        )
        database.add_entry(
            guild_id=1,
            member_id=member_id,
            actor_id=member_id,
            actor_was_admin=False,
            product=copper,
            quantity=copper_amount,
        )
    database.add_cash_transaction(
        guild_id=1,
        actor_id=99,
        kind="income",
        amount=Decimal("2000000"),
        reason="Receita da firma",
    )
    return database, goal.id


def test_preview_and_commit_goal_settlement(tmp_path) -> None:
    database, goal_id = prepared_database(tmp_path)

    preview = build_settlement_preview(database, 1)

    assert preview.reserved_base == Decimal("600000.00")
    assert preview.distributable == Decimal("1400000.00")
    assert [item.amount for item in preview.payouts] == [
        Decimal("700000.00"),
        Decimal("350000.00"),
    ]
    assert preview.retained == Decimal("950000.00")
    assert money(preview.total_paid) == "$1,050,000.00"

    database.commit_goal_settlement(
        guild_id=1,
        goal_id=goal_id,
        actor_id=99,
        cash_before=preview.cash_before,
        reserve_rate=preview.reserve_rate,
        distributable=preview.distributable,
        payouts=list(preview.payouts),
    )

    assert database.cash_balance(1) == Decimal("950000.00")
    assert database.get_active_goal(1) is None
    assert database.goal_is_settled(goal_id) is True
    with pytest.raises(ValueError, match="já teve"):
        database.commit_goal_settlement(
            guild_id=1,
            goal_id=goal_id,
            actor_id=99,
            cash_before=preview.cash_before,
            reserve_rate=preview.reserve_rate,
            distributable=preview.distributable,
            payouts=list(preview.payouts),
        )


def test_cash_expense_and_reserve_validation(tmp_path) -> None:
    database = Database(str(tmp_path / "bot.db"))
    database.initialize()
    database.set_reserve_rate(1, Decimal("0.25"))
    database.add_cash_transaction(
        guild_id=1,
        actor_id=99,
        kind="income",
        amount=Decimal("100"),
        reason="Venda",
    )
    database.add_cash_transaction(
        guild_id=1,
        actor_id=99,
        kind="expense",
        amount=Decimal("40"),
        reason="Compra",
    )

    assert database.get_reserve_rate(1) == Decimal("0.25")
    assert database.cash_balance(1) == Decimal("60")
    with pytest.raises(ValueError, match="Saldo insuficiente"):
        database.add_cash_transaction(
            guild_id=1,
            actor_id=99,
            kind="expense",
            amount=Decimal("61"),
            reason="Compra",
        )
