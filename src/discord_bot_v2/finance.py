"""Cash-box calculations and farm-goal payout previews."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from discord_bot_v2.database import (
    TIERED_BONUS_RULE,
    CashPayout,
    Database,
    Goal,
)
from discord_bot_v2.reporting import overall_progress

CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class SettlementPreview:
    goal: Goal
    cash_before: Decimal
    reserve_rate: Decimal
    reserved_base: Decimal
    distributable: Decimal
    distribution_rule: str
    payouts: tuple[CashPayout, ...]
    total_paid: Decimal
    retained: Decimal


def money(value: Decimal) -> str:
    return f"${value.quantize(CENT, rounding=ROUND_HALF_UP):,.2f}"


def tier_factor(progress: Decimal) -> Decimal:
    """Return the payout factor for the configured progress bracket."""
    if progress >= Decimal("1"):
        return Decimal("1")
    if progress >= Decimal("0.95"):
        return Decimal("0.95")
    if progress >= Decimal("0.90"):
        return Decimal("0.85")
    if progress >= Decimal("0.80"):
        return Decimal("0.70")
    if progress >= Decimal("0.70"):
        return Decimal("0.55")
    if progress >= Decimal("0.60"):
        return Decimal("0.40")
    if progress >= Decimal("0.50"):
        return Decimal("0.25")
    return Decimal("0.10")


def _tiered_payouts(
    distributable: Decimal, participants: list[tuple[int, Decimal]]
) -> tuple[CashPayout, ...]:
    base_share = (distributable / len(participants)).quantize(CENT, rounding=ROUND_HALF_UP)
    provisional = [
        (base_share * tier_factor(progress)).quantize(CENT, rounding=ROUND_HALF_UP)
        for _, progress in participants
    ]
    achiever_count = sum(progress >= 1 for _, progress in participants)
    penalties = distributable - sum(provisional, Decimal(0))
    bonus = (
        (penalties / achiever_count).quantize(CENT, rounding=ROUND_DOWN)
        if achiever_count
        else Decimal(0)
    )
    return tuple(
        CashPayout(
            member_id=member_id,
            progress=progress,
            base_share=base_share,
            amount=amount + bonus if progress >= 1 else amount,
        )
        for (member_id, progress), amount in zip(participants, provisional, strict=True)
    )


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
    distribution_rule = database.get_distribution_rule(guild_id)

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
    if distribution_rule == TIERED_BONUS_RULE:
        payouts = _tiered_payouts(distributable, participants)
    else:
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
        distribution_rule=distribution_rule,
        payouts=payouts,
        total_paid=total_paid,
        retained=cash - total_paid,
    )
