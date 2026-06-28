"""Discord audit-log delivery for stock movements."""

from __future__ import annotations

from contextlib import suppress
from decimal import Decimal

import discord

from discord_bot_v2.database import Database, Product


async def _resolve_text_channel(
    guild: discord.Guild, channel_id: int
) -> discord.TextChannel | None:
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    try:
        fetched = await guild.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None
    return fetched if isinstance(fetched, discord.TextChannel) else None


async def _send_log(
    guild: discord.Guild,
    channel: discord.TextChannel,
    embed: discord.Embed,
    fallback: str,
) -> None:
    bot_member = guild.me
    can_embed = bool(bot_member and channel.permissions_for(bot_member).embed_links)
    with suppress(discord.Forbidden, discord.HTTPException):
        if can_embed:
            await channel.send(embed=embed)
        else:
            await channel.send(fallback)


async def send_entry_log(
    *,
    guild: discord.Guild,
    database: Database,
    member_id: int,
    actor_id: int,
    actor_was_admin: bool,
    product: Product,
    quantity: Decimal,
    entry_id: int,
) -> None:
    channels = database.get_log_channels(guild.id)
    if channels is None:
        return
    channel = await _resolve_text_channel(guild, channels.entry_channel_id)
    if channel is None:
        return
    embed = discord.Embed(title="📥 Entrada no estoque", color=discord.Color.green())
    embed.add_field(name="Pessoa", value=f"<@{member_id}>")
    embed.add_field(name="Produto", value=product.name)
    embed.add_field(name="Quantidade", value=format(quantity, "f"))
    embed.add_field(name="Registrado por", value=f"<@{actor_id}>")
    embed.add_field(
        name="Origem administrativa",
        value="Sim" if actor_was_admin else "Não",
    )
    embed.set_footer(text=f"Registro #{entry_id}")
    await _send_log(
        guild,
        channel,
        embed,
        (
            f"📥 Entrada #{entry_id}: {format(quantity, 'f')} {product.name} "
            f"para <@{member_id}>, registrada por <@{actor_id}>."
        ),
    )


async def send_output_log(
    *,
    guild: discord.Guild,
    database: Database,
    actor_id: int,
    product: Product,
    quantity: Decimal,
    reason: str,
    output_id: int,
) -> None:
    channels = database.get_log_channels(guild.id)
    if channels is None:
        return
    channel = await _resolve_text_channel(guild, channels.output_channel_id)
    if channel is None:
        return
    embed = discord.Embed(title="📤 Saída do estoque", color=discord.Color.red())
    embed.add_field(name="Produto", value=product.name)
    embed.add_field(name="Quantidade", value=format(quantity, "f"))
    embed.add_field(name="Registrado por", value=f"<@{actor_id}>")
    embed.add_field(name="Motivo", value=reason, inline=False)
    embed.set_footer(text=f"Movimentação #{output_id}")
    await _send_log(
        guild,
        channel,
        embed,
        (
            f"📤 Saída #{output_id}: {format(quantity, 'f')} {product.name}, "
            f"registrada por <@{actor_id}>. Motivo: {reason}"
        ),
    )


async def send_cash_log(
    *,
    guild: discord.Guild,
    database: Database,
    title: str,
    amount: Decimal,
    reason: str,
    actor_id: int,
    balance: Decimal,
    color: discord.Color,
) -> None:
    channel_id = database.get_cash_log_channel(guild.id)
    if channel_id is None:
        return
    channel = await _resolve_text_channel(guild, channel_id)
    if channel is None:
        return
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Valor", value=f"${amount:,.2f}")
    embed.add_field(name="Saldo atual", value=f"${balance:,.2f}")
    embed.add_field(name="Responsável", value=f"<@{actor_id}>")
    embed.add_field(name="Motivo", value=reason, inline=False)
    await _send_log(
        guild,
        channel,
        embed,
        f"{title}: ${amount:,.2f}. Saldo: ${balance:,.2f}. Motivo: {reason}",
    )
