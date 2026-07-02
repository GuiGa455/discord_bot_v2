"""Date parsing and Discord report rendering for the FDM workflow."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import discord

from discord_bot_v2.database import Database, GoalProgress, distribution_rule_name

LOCAL_TIMEZONE = ZoneInfo("America/Sao_Paulo")


def parse_period(start_text: str, end_text: str) -> tuple[str, str]:
    """Convert inclusive Brazilian dates to UTC ISO timestamps."""
    start_date = datetime.strptime(start_text.strip(), "%d/%m/%Y").date()
    end_date = datetime.strptime(end_text.strip(), "%d/%m/%Y").date()
    if end_date < start_date:
        raise ValueError("A data final deve ser igual ou posterior à inicial")
    start = datetime.combine(start_date, time.min, LOCAL_TIMEZONE).astimezone(UTC)
    end = datetime.combine(end_date, time.max, LOCAL_TIMEZONE).astimezone(UTC)
    return start.isoformat(), end.isoformat()


def display_period(start_at: str, end_at: str) -> str:
    start = datetime.fromisoformat(start_at).astimezone(LOCAL_TIMEZONE)
    end = datetime.fromisoformat(end_at).astimezone(LOCAL_TIMEZONE)
    return f"{start:%d/%m/%Y} a {end:%d/%m/%Y}"


def sales_period(kind: str, now: datetime | None = None) -> tuple[str, str, str]:
    local_now = (now or datetime.now(LOCAL_TIMEZONE)).astimezone(LOCAL_TIMEZONE)
    if kind == "day":
        start_date = local_now.date()
        label = f"Hoje — {start_date:%d/%m/%Y}"
    elif kind == "month":
        start_date = local_now.date().replace(day=1)
        label = f"Mês atual — {start_date:%m/%Y}"
    else:
        start_date = local_now.date() - timedelta(days=local_now.weekday())
        label = f"Semana atual — desde {start_date:%d/%m/%Y}"
    start = datetime.combine(start_date, time.min, LOCAL_TIMEZONE).astimezone(UTC)
    end = local_now.astimezone(UTC)
    return start.isoformat(), end.isoformat(), label


def build_sales_embed(database: Database, guild_id: int, period: str) -> discord.Embed:
    start_at, end_at, label = sales_period(period)
    sales = database.list_sales(guild_id, start_at=start_at, end_at=end_at)
    embed = discord.Embed(
        title="Relatório de vendas", description=label, color=discord.Color.gold()
    )
    total = sum((sale.total for sale in sales), Decimal(0))
    embed.add_field(name="Total vendido", value=format_usd(total))
    embed.add_field(name="Número de vendas", value=str(len(sales)))
    by_product: dict[str, Decimal] = {}
    by_day: dict[str, Decimal] = {}
    for sale in sales:
        by_product[sale.product_name] = by_product.get(sale.product_name, Decimal(0)) + sale.total
        day = datetime.fromisoformat(sale.created_at).astimezone(LOCAL_TIMEZONE).strftime("%d/%m")
        by_day[day] = by_day.get(day, Decimal(0)) + sale.total
    products = "\n".join(
        f"• **{name}:** {format_usd(value)}" for name, value in sorted(by_product.items())
    ) or "Nenhuma venda no período."
    embed.add_field(name="Por produto", value=products[:1024], inline=False)
    if by_day:
        maximum = max(by_day.values())
        chart = "\n".join(
            f"`{day}` {'█' * max(1, int(value / maximum * 12))} {format_usd(value)}"
            for day, value in sorted(by_day.items())
        )
        embed.add_field(name="Vendas por dia", value=chart[:1024], inline=False)
    return embed


def format_totals(totals: dict[str, Decimal]) -> str:
    visible = [(name, quantity) for name, quantity in totals.items() if quantity != 0]
    if not visible:
        return "Nenhum produto registrado."
    content = "\n".join(
        f"• **{name}:** {format(quantity, 'f')}" for name, quantity in sorted(visible)
    )
    return content if len(content) <= 1024 else content[:1000] + "\n… resultado resumido"


def format_usd(value: Decimal) -> str:
    return f"${value:,.2f}"


def progress_bar(current: Decimal, target: Decimal, size: int = 10) -> str:
    ratio = min(max(current / target, Decimal(0)), Decimal(1))
    filled = int(ratio * size)
    return "●" * filled + "○" * (size - filled)


def format_progress(items: list[GoalProgress]) -> str:
    if not items:
        return "Nenhuma meta ativa."
    lines = []
    for item in items:
        achieved = item.current >= item.target
        marker = "✅" if achieved else "📦"
        ratio = min(max(item.current / item.target, Decimal(0)), Decimal(1))
        percentage = (ratio * 100).quantize(Decimal("0.1"))
        lines.append(
            f"{marker} **{item.product.name}** — **{format(percentage, 'f')}%** "
            f"`{progress_bar(item.current, item.target)}`\n"
            f"`{format(item.current, 'f')} / {format(item.target, 'f')}`"
        )
    content = "\n".join(lines)
    return content if len(content) <= 1024 else content[:1000] + "\n… progresso resumido"


def overall_progress(items: list[GoalProgress]) -> tuple[Decimal, bool]:
    """Calculate balanced progress so excess in one product cannot hide another."""
    if not items:
        return Decimal(0), False
    ratios = [min(item.current / item.target, Decimal(1)) for item in items]
    ratio = sum(ratios, Decimal(0)) / len(ratios)
    return ratio, all(item.current >= item.target for item in items)


def build_farm_embed(database: Database, guild_id: int, member_id: int) -> discord.Embed:
    embed = discord.Embed(
        title="Controle de coleta",
        description=(
            f"Canal privado de <@{member_id}>. Use os controles abaixo para registrar "
            "produtos e consultar seu histórico."
        ),
        color=discord.Color.green(),
    )
    goal = database.get_active_goal(guild_id)
    if goal:
        embed.add_field(
            name=f"Meta — {display_period(goal.start_at, goal.end_at)}",
            value=format_progress(database.goal_progress(guild_id, member_id)),
            inline=False,
        )
    else:
        embed.add_field(name="Meta", value="Nenhuma meta ativa.", inline=False)
    return embed


def build_admin_embed(database: Database, guild_id: int) -> discord.Embed:
    embed = discord.Embed(
        title="Painel administrativo",
        description="Estoque, relatórios, metas e canais privados da firma.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Total registrado (histórico)",
        value=format_totals(database.product_totals(guild_id)),
        inline=False,
    )
    embed.add_field(
        name="Estoque atual",
        value=format_totals(database.stock_totals(guild_id)),
        inline=False,
    )
    reserve_rate = database.get_reserve_rate(guild_id)
    distribution_rule = distribution_rule_name(database.get_distribution_rule(guild_id))
    week_start, week_end, _ = sales_period("week")
    weekly_sales = database.sales_total(guild_id, start_at=week_start, end_at=week_end)
    embed.add_field(
        name="Caixa da firma",
        value=(
            f"**Saldo:** {format_usd(database.cash_balance(guild_id))}\n"
            f"**Vendido nesta semana:** {format_usd(weekly_sales)}\n"
            f"**Reserva:** {format(reserve_rate * 100, 'f')}%\n"
            f"**Regra de divisão:** {distribution_rule}"
        ),
        inline=False,
    )
    goal = database.get_active_goal(guild_id)
    if goal is None:
        embed.add_field(name="Meta ativa", value="Nenhuma meta ativa.", inline=False)
        return embed

    progress_lines: list[str] = []
    for farm_channel in database.list_farm_channels(guild_id):
        items = database.goal_progress(guild_id, farm_channel.member_id)
        ratio, achieved = overall_progress(items)
        percentage = (ratio * 100).quantize(Decimal("0.1"))
        progress_lines.append(
            f"{'✅' if achieved else '⏳'} <@{farm_channel.member_id}> — "
            f"**{format(percentage, 'f')}%** "
            f"`{progress_bar(ratio, Decimal(1))}`"
        )
    summary = "\n".join(progress_lines) or "Nenhuma sala FARME criada."
    embed.add_field(
        name=f"Meta ativa — {display_period(goal.start_at, goal.end_at)}",
        value=summary[:1024],
        inline=False,
    )
    return embed
