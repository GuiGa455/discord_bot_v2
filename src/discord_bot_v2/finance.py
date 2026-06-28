"""Cash-box calculations and farm-goal payout previews."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from discord_bot_v2.database import CashPayout, Database, Goal
from discord_bot_v2.reporting import overall_progress

CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class SettlementPreview:
    goal: Goal
    cash_before: Decimal
    reserve_rate: Decimal
    reserved_base: Decimal
    distributable: Decimal
    payouts: tuple[CashPayout, ...]
    total_paid: Decimal
    retained: Decimal


def money(value: Decimal) -> str:
    return f"${value.quantize(CENT, rounding=ROUND_HALF_UP):,.2f}"


def build_settlement_preview(database: Database, guild_id: int) -> SettlementPreview:
    goal = database.get_active_goal(guild_id)
    if goal is None:
        raise ValueError("Não existe uma meta ativa")
    if database.goal_is_settled(goal.id):
        raise ValueError("Esta meta já teve o caixa distribuído")
    cash = database.cash_balance(guild_id).quantize(CENT, rounding=ROUND_HALF_UP)
    if cash <= 0:
        raise ValueError("O caixa não possui saldo para distribuir")
    reserve_rate = database.get_reserve_rate(guild_id)
    reserved = (cash * reserve_rate).quantize(CENT, rounding=ROUND_HALF_UP)
    distributable = cash - reserved

    participants: list[tuple[int, Decimal]] = []
    for farm_channel in database.list_farm_channels(guild_id):
        items = database.goal_progress(guild_id, farm_channel.member_id)
        if not items or not any(item.current > 0 for item in items):
            continue
        ratio, _ = overall_progress(items)
        participants.append((farm_channel.member_id, ratio))
    if not participants:
        raise ValueError("Nenhuma pessoa registrou coleta durante esta meta")

    base_share = (distributable / len(participants)).quantize(CENT, rounding=ROUND_HALF_UP)
    payouts = tuple(
        CashPayout(
            member_id=member_id,
            progress=ratio,
            base_share=base_share,
            amount=(base_share * ratio).quantize(CENT, rounding=ROUND_HALF_UP),
        )
        for member_id, ratio in participants
    )
    total_paid = sum((item.amount for item in payouts), Decimal(0))
    return SettlementPreview(
        goal=goal,
        cash_before=cash,
        reserve_rate=reserve_rate,
        reserved_base=reserved,
        distributable=distributable,
        payouts=payouts,
        total_paid=total_paid,
        retained=cash - total_paid,
    )
