"""Discord UI components for farm configuration and product registration."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import unicodedata
from collections.abc import Awaitable
from contextlib import suppress
from decimal import Decimal, InvalidOperation

import discord

from discord_bot_v2.audit import (
    send_cash_log,
    send_entry_log,
    send_output_log,
    send_stock_input_log,
)
from discord_bot_v2.database import (
    PROPORTIONAL_RULE,
    TIERED_BONUS_RULE,
    Database,
    FarmChannel,
    Product,
    distribution_rule_name,
)
from discord_bot_v2.finance import SettlementPreview, build_settlement_preview, money
from discord_bot_v2.reporting import (
    build_admin_embed,
    build_farm_embed,
    build_sales_embed,
    display_period,
    format_totals,
    parse_period,
)


def _is_administrator(interaction: discord.Interaction) -> bool:
    return (
        isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.administrator
    )


async def _require_admin(interaction: discord.Interaction) -> bool:
    if _is_administrator(interaction):
        return True
    await interaction.response.send_message(
        "Apenas administradores podem usar este controle.", ephemeral=True
    )
    return False


async def _delete_temporary_message(interaction: discord.Interaction) -> None:
    """Delete an ephemeral interaction message using the webhook-aware API."""
    with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
        await interaction.delete_original_response()
    if interaction.message is not None:
        with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
            await interaction.message.delete()


def _channel_name(member: discord.Member) -> str:
    normalized = unicodedata.normalize("NFKD", member.display_name)
    ascii_name = normalized.encode("ascii", "ignore").decode().lower()
    safe_name = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return f"farme-{safe_name or member.id}"[:100]


async def refresh_guild_panels(
    guild: discord.Guild,
    database: Database,
    *,
    farm_member_id: int | None = None,
    refresh_all_farms: bool = False,
) -> None:
    """Refresh panels concurrently, optionally limiting FARME updates to one member."""
    tasks: list[asyncio.Task[None]] = []
    admin_embed = build_admin_embed(database, guild.id)
    for channel_id, message_id in database.list_admin_panels(guild.id):
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            tasks.append(
                asyncio.create_task(_edit_admin_panel(channel, message_id, admin_embed, database))
            )
    farm_channels = database.list_farm_channels(guild.id)
    if not refresh_all_farms:
        farm_channels = [item for item in farm_channels if item.member_id == farm_member_id]
    for farm_channel in farm_channels:
        if farm_channel.panel_message_id is None:
            continue
        channel = guild.get_channel(farm_channel.channel_id)
        if isinstance(channel, discord.TextChannel):
            tasks.append(
                asyncio.create_task(
                    _edit_farm_panel(
                        channel,
                        farm_channel.panel_message_id,
                        build_farm_embed(database, guild.id, farm_channel.member_id),
                        database,
                    )
                )
            )
    if tasks:
        await asyncio.gather(*tasks)


async def _edit_admin_panel(
    channel: discord.TextChannel,
    message_id: int,
    embed: discord.Embed,
    database: Database,
) -> None:
    with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
        message = channel.get_partial_message(message_id)
        await message.edit(embed=embed, view=ConfigPanel(database))


async def _edit_farm_panel(
    channel: discord.TextChannel,
    message_id: int,
    embed: discord.Embed,
    database: Database,
) -> None:
    with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
        message = channel.get_partial_message(message_id)
        await message.edit(embed=embed, view=FarmPanel(database))


async def refresh_panels(
    interaction: discord.Interaction,
    database: Database,
    *,
    farm_member_id: int | None = None,
    refresh_all_farms: bool = False,
) -> None:
    guild = interaction.guild
    if guild is not None:
        await refresh_guild_panels(
            guild,
            database,
            farm_member_id=farm_member_id,
            refresh_all_farms=refresh_all_farms,
        )


class ProductModal(discord.ui.Modal, title="Adicionar produto"):
    name: discord.ui.TextInput[ProductModal] = discord.ui.TextInput(
        label="Nome do produto",
        placeholder="Ex.: Minério de ferro",
        min_length=1,
        max_length=100,
    )
    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        if len(self.database.list_products(interaction.guild_id)) >= 25:
            await interaction.response.send_message(
                "O limite atual é de 25 produtos por servidor.", ephemeral=True
            )
            return
        try:
            product = self.database.add_product(
                interaction.guild_id, str(self.name), kind="farm"
            )
        except (InvalidOperation, ValueError) as exc:
            await interaction.response.send_message(
                str(exc) or "Informe um preço válido.", ephemeral=True, delete_after=15
            )
            return
        except sqlite3.IntegrityError:
            await interaction.response.send_message(
                "Esse produto já está cadastrado.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Produto **{product.name}** adicionado.", ephemeral=True, delete_after=10
        )


class SaleProductModal(discord.ui.Modal, title="Cadastrar produto de venda"):
    name: discord.ui.TextInput[SaleProductModal] = discord.ui.TextInput(
        label="Nome do produto", placeholder="Ex.: Pistola", min_length=1, max_length=100
    )
    price: discord.ui.TextInput[SaleProductModal] = discord.ui.TextInput(
        label="Preço unitário", placeholder="Ex.: 5000.00", min_length=1, max_length=30
    )

    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        if len(self.database.list_products(interaction.guild_id)) >= 25:
            await interaction.response.send_message(
                "O limite atual é de 25 produtos por servidor.", ephemeral=True
            )
            return
        try:
            price = Decimal(str(self.price).strip().replace(",", "."))
            product = self.database.add_product(
                interaction.guild_id,
                str(self.name),
                sale_price=price,
                kind="sale",
            )
        except sqlite3.IntegrityError:
            await interaction.response.send_message(
                "Esse produto já está cadastrado.", ephemeral=True
            )
            return
        except (InvalidOperation, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True, delete_after=15)
            return
        await interaction.response.send_message(
            f"Produto de venda **{product.name}** cadastrado por **{money(price)}**.",
            ephemeral=True,
            delete_after=10,
        )


class ProductKindSelect(discord.ui.Select["ProductKindView"]):
    def __init__(self, database: Database) -> None:
        self.database = database
        super().__init__(
            placeholder="Qual será o tipo do produto?",
            options=[
                discord.SelectOption(
                    label="Produto de FARME",
                    value="farm",
                    description="Aparece nas metas e nas salas de coleta.",
                    emoji="📦",
                ),
                discord.SelectOption(
                    label="Produto de VENDA",
                    value="sale",
                    description="Possui preço e não aparece nas metas.",
                    emoji="🏷️",
                ),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction):
            return
        modal: discord.ui.Modal = (
            ProductModal(self.database)
            if self.values[0] == "farm"
            else SaleProductModal(self.database)
        )
        await interaction.response.send_modal(modal)
        await _delete_temporary_message(interaction)


class ProductKindView(discord.ui.View):
    def __init__(self, database: Database) -> None:
        super().__init__(timeout=120)
        self.add_item(ProductKindSelect(database))


class RemoveProductSelect(discord.ui.Select["RemoveProductView"]):
    def __init__(self, database: Database, products: list[Product]) -> None:
        self.database = database
        super().__init__(
            placeholder="Selecione um produto para remover",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=item.name, value=str(item.id)) for item in products
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        await interaction.response.defer(ephemeral=True)
        self.database.remove_product(interaction.guild_id, int(self.values[0]))
        await refresh_panels(interaction, self.database, refresh_all_farms=True)
        await _delete_temporary_message(interaction)


class RemoveProductView(discord.ui.View):
    def __init__(self, database: Database, products: list[Product]) -> None:
        super().__init__(timeout=120)
        self.add_item(RemoveProductSelect(database, products))


class QuantityModal(discord.ui.Modal, title="Registrar produto coletado"):
    quantity: discord.ui.TextInput[QuantityModal] = discord.ui.TextInput(
        label="Quantidade",
        placeholder="Ex.: 12,5",
        min_length=1,
        max_length=30,
    )

    def __init__(
        self,
        database: Database,
        product: Product,
        member_id: int,
        panel_message: discord.Message | None = None,
    ) -> None:
        super().__init__()
        self.database = database
        self.product = product
        self.member_id = member_id
        self.panel_message = panel_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        try:
            quantity = Decimal(str(self.quantity).strip().replace(",", "."))
            if not quantity.is_finite() or quantity <= 0:
                raise InvalidOperation
        except InvalidOperation:
            await interaction.response.send_message(
                "Informe uma quantidade numérica maior que zero.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        actor_is_admin = _is_administrator(interaction)
        target_is_admin = False
        if interaction.guild is not None:
            target_member = interaction.guild.get_member(self.member_id)
            if target_member is None:
                try:
                    target_member = await interaction.guild.fetch_member(self.member_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    target_member = None
            target_is_admin = bool(target_member and target_member.guild_permissions.administrator)
        admin_registration = actor_is_admin and not target_is_admin
        entry_id = self.database.add_entry(
            guild_id=interaction.guild_id,
            member_id=self.member_id,
            actor_id=interaction.user.id,
            actor_was_admin=admin_registration,
            product=self.product,
            quantity=quantity,
        )
        with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
            await interaction.delete_original_response()
        refresh: Awaitable[object]
        if self.panel_message is not None:
            refresh = asyncio.gather(
                self.panel_message.edit(
                    embed=build_farm_embed(self.database, interaction.guild_id, self.member_id),
                    view=FarmPanel(self.database),
                ),
                refresh_panels(interaction, self.database),
            )
        else:
            refresh = refresh_panels(interaction, self.database, farm_member_id=self.member_id)
        if interaction.guild is not None:
            await asyncio.gather(
                send_entry_log(
                    guild=interaction.guild,
                    database=self.database,
                    member_id=self.member_id,
                    actor_id=interaction.user.id,
                    actor_was_admin=admin_registration,
                    product=self.product,
                    quantity=quantity,
                    entry_id=entry_id,
                ),
                refresh,
            )
        else:
            await refresh


class ProductSelect(discord.ui.Select["ProductSelectView"]):
    def __init__(
        self,
        database: Database,
        products: list[Product],
        member_id: int,
        panel_message: discord.Message | None = None,
    ) -> None:
        self.database = database
        self.products = {str(product.id): product for product in products}
        self.member_id = member_id
        self.panel_message = panel_message
        super().__init__(
            placeholder="Qual produto foi coletado?",
            options=[
                discord.SelectOption(label=item.name, value=str(item.id)) for item in products
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            QuantityModal(
                self.database,
                self.products[self.values[0]],
                self.member_id,
                self.panel_message,
            )
        )
        await _delete_temporary_message(interaction)


class ProductSelectView(discord.ui.View):
    def __init__(
        self,
        database: Database,
        products: list[Product],
        member_id: int,
        panel_message: discord.Message | None = None,
    ) -> None:
        super().__init__(timeout=120)
        self.add_item(ProductSelect(database, products, member_id, panel_message))


class PeriodReportModal(discord.ui.Modal, title="Consultar registros"):
    start_date: discord.ui.TextInput[PeriodReportModal] = discord.ui.TextInput(
        label="Data inicial",
        placeholder="DD/MM/AAAA",
        required=False,
        max_length=10,
    )
    end_date: discord.ui.TextInput[PeriodReportModal] = discord.ui.TextInput(
        label="Data final",
        placeholder="DD/MM/AAAA",
        required=False,
        max_length=10,
    )

    def __init__(self, database: Database, member_id: int) -> None:
        super().__init__()
        self.database = database
        self.member_id = member_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        start_text = str(self.start_date).strip()
        end_text = str(self.end_date).strip()
        start_at: str | None = None
        end_at: str | None = None
        if start_text or end_text:
            if not start_text or not end_text:
                await interaction.response.send_message(
                    "Preencha as duas datas ou deixe ambas vazias para consultar o total.",
                    ephemeral=True,
                )
                return
            try:
                start_at, end_at = parse_period(start_text, end_text)
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
        totals = self.database.product_totals(
            interaction.guild_id,
            member_id=self.member_id,
            start_at=start_at,
            end_at=end_at,
        )
        embed = discord.Embed(
            title="Relatório de coleta",
            description=(
                f"Registros de <@{self.member_id}> "
                f"{'no período informado' if start_at else 'em todo o histórico'}."
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(name="Produtos", value=format_totals(totals), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True, delete_after=30)


class OutputQuantityModal(discord.ui.Modal, title="Registrar saída"):
    quantity: discord.ui.TextInput[OutputQuantityModal] = discord.ui.TextInput(
        label="Quantidade retirada",
        placeholder="Ex.: 12,5",
        min_length=1,
        max_length=30,
    )
    reason: discord.ui.TextInput[OutputQuantityModal] = discord.ui.TextInput(
        label="Motivo da retirada",
        placeholder="Ex.: Venda para cliente",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=500,
    )

    def __init__(self, database: Database, product: Product) -> None:
        super().__init__()
        self.database = database
        self.product = product

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        try:
            quantity = Decimal(str(self.quantity).strip().replace(",", "."))
            if not quantity.is_finite() or quantity <= 0:
                raise InvalidOperation
            output_id = self.database.add_output(
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                product=self.product,
                quantity=quantity,
                reason=str(self.reason),
            )
        except (InvalidOperation, ValueError) as exc:
            message = str(exc) or "Informe uma quantidade numérica maior que zero."
            await interaction.response.send_message(message, ephemeral=True)
            return
        await interaction.response.send_message(
            f"📤 Saída de **{format(quantity, 'f')} {self.product.name}** registrada. "
            f"Movimentação `#{output_id}`.",
            ephemeral=True,
            delete_after=10,
        )
        refresh = refresh_panels(interaction, self.database)
        if interaction.guild is not None:
            await asyncio.gather(
                send_output_log(
                    guild=interaction.guild,
                    database=self.database,
                    actor_id=interaction.user.id,
                    product=self.product,
                    quantity=quantity,
                    reason=str(self.reason),
                    output_id=output_id,
                ),
                refresh,
            )
        else:
            await refresh


class OutputProductSelect(discord.ui.Select["OutputProductView"]):
    def __init__(self, database: Database, products: list[Product]) -> None:
        self.database = database
        self.products = {str(product.id): product for product in products}
        super().__init__(
            placeholder="Produto que saiu do estoque",
            options=[
                discord.SelectOption(label=product.name, value=str(product.id))
                for product in products
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            OutputQuantityModal(self.database, self.products[self.values[0]])
        )
        await _delete_temporary_message(interaction)


class OutputProductView(discord.ui.View):
    def __init__(self, database: Database, products: list[Product]) -> None:
        super().__init__(timeout=120)
        self.add_item(OutputProductSelect(database, products))


class AdminMemberSelect(discord.ui.UserSelect["AdminMemberReportView"]):
    def __init__(self, database: Database) -> None:
        super().__init__(placeholder="Pessoa para consultar", min_values=1, max_values=1)
        self.database = database

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_modal(PeriodReportModal(self.database, self.values[0].id))
        await _delete_temporary_message(interaction)


class AdminMemberReportView(discord.ui.View):
    def __init__(self, database: Database) -> None:
        super().__init__(timeout=120)
        self.add_item(AdminMemberSelect(database))


class GoalTargetModal(discord.ui.Modal, title="Quantidade da meta"):
    target: discord.ui.TextInput[GoalTargetModal] = discord.ui.TextInput(
        label="Quantidade desejada",
        placeholder="Ex.: 100",
        min_length=1,
        max_length=30,
    )

    def __init__(
        self,
        database: Database,
        goal_id: int,
        product: Product,
        builder_message: discord.Message | None = None,
    ) -> None:
        super().__init__()
        self.database = database
        self.goal_id = goal_id
        self.product = product
        self.builder_message = builder_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction):
            return
        try:
            target = Decimal(str(self.target).strip().replace(",", "."))
            if not target.is_finite() or target <= 0:
                raise InvalidOperation
        except InvalidOperation:
            await interaction.response.send_message(
                "Informe uma quantidade numérica maior que zero.", ephemeral=True
            )
            return
        self.database.set_goal_item(self.goal_id, self.product, target)
        await interaction.response.defer(ephemeral=True)
        if self.builder_message is not None:
            try:
                await self.builder_message.edit(
                    content=(
                        f"✅ **{format(target, 'f')} {self.product.name}** adicionado à meta. "
                        "Selecione outro produto ou finalize."
                    )
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                if interaction.guild_id is not None:
                    await interaction.followup.send(
                        "O menu anterior expirou e foi recriado. Selecione outro produto "
                        "ou finalize a meta.",
                        view=GoalBuilderView(
                            self.database,
                            interaction.guild_id,
                            self.goal_id,
                            self.database.list_products(interaction.guild_id, kind="farm"),
                        ),
                        ephemeral=True,
                    )
        with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
            await interaction.delete_original_response()


class GoalProductSelect(discord.ui.Select["GoalBuilderView"]):
    def __init__(self, database: Database, goal_id: int, products: list[Product]) -> None:
        self.database = database
        self.goal_id = goal_id
        self.products = {str(product.id): product for product in products}
        super().__init__(
            placeholder="Adicionar produto à meta",
            options=[
                discord.SelectOption(label=product.name, value=str(product.id))
                for product in products
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            GoalTargetModal(
                self.database,
                self.goal_id,
                self.products[self.values[0]],
                interaction.message,
            )
        )


class GoalBuilderView(discord.ui.View):
    def __init__(
        self, database: Database, guild_id: int, goal_id: int, products: list[Product]
    ) -> None:
        super().__init__(timeout=600)
        self.database = database
        self.guild_id = guild_id
        self.goal_id = goal_id
        self.add_item(GoalProductSelect(database, goal_id, products))

    @discord.ui.button(label="Finalizar e ativar", style=discord.ButtonStyle.green, row=1)
    async def finish(
        self, interaction: discord.Interaction, _: discord.ui.Button[GoalBuilderView]
    ) -> None:
        if not await _require_admin(interaction):
            return
        try:
            self.database.activate_goal(self.guild_id, self.goal_id)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _delete_temporary_message(interaction)
        await refresh_panels(interaction, self.database, refresh_all_farms=True)


class GoalPeriodModal(discord.ui.Modal, title="Definir período da meta"):
    start_date: discord.ui.TextInput[GoalPeriodModal] = discord.ui.TextInput(
        label="Data inicial", placeholder="DD/MM/AAAA", min_length=10, max_length=10
    )
    end_date: discord.ui.TextInput[GoalPeriodModal] = discord.ui.TextInput(
        label="Data final", placeholder="DD/MM/AAAA", min_length=10, max_length=10
    )

    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        products = self.database.list_products(interaction.guild_id, kind="farm")
        if not products:
            await interaction.response.send_message(
                "Cadastre produtos antes de criar uma meta.", ephemeral=True
            )
            return
        try:
            start_at, end_at = parse_period(str(self.start_date), str(self.end_date))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        goal = self.database.create_goal(interaction.guild_id, start_at, end_at)
        await interaction.response.send_message(
            "Selecione cada produto, informe a meta e, ao terminar, clique em finalizar.",
            view=GoalBuilderView(self.database, interaction.guild_id, goal.id, products),
            ephemeral=True,
        )


class EditGoalModal(discord.ui.Modal, title="Editar meta ativa"):
    def __init__(self, database: Database, goal_id: int, start_at: str, end_at: str) -> None:
        super().__init__()
        self.database = database
        self.goal_id = goal_id
        start_text, end_text = display_period(start_at, end_at).split(" a ")
        self.start_date: discord.ui.TextInput[EditGoalModal] = discord.ui.TextInput(
            label="Data inicial",
            placeholder="DD/MM/AAAA",
            default=start_text,
            min_length=10,
            max_length=10,
        )
        self.end_date: discord.ui.TextInput[EditGoalModal] = discord.ui.TextInput(
            label="Data final",
            placeholder="DD/MM/AAAA",
            default=end_text,
            min_length=10,
            max_length=10,
        )
        self.add_item(self.start_date)
        self.add_item(self.end_date)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        try:
            start_at, end_at = parse_period(str(self.start_date), str(self.end_date))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True, delete_after=15)
            return
        self.database.update_goal_period(self.goal_id, start_at, end_at)
        products = self.database.list_products(interaction.guild_id, kind="farm")
        await interaction.response.send_message(
            "Período atualizado. Selecione os produtos que deseja alterar e finalize.",
            view=GoalBuilderView(self.database, interaction.guild_id, self.goal_id, products),
            ephemeral=True,
        )


class CashTransactionModal(discord.ui.Modal):
    def __init__(self, database: Database, kind: str) -> None:
        title = "Entrada no caixa" if kind == "income" else "Saída do caixa"
        super().__init__(title=title)
        self.database = database
        self.kind = kind
        self.amount: discord.ui.TextInput[CashTransactionModal] = discord.ui.TextInput(
            label="Valor em dólares",
            placeholder="Ex.: 250000.00",
            min_length=1,
            max_length=30,
        )
        self.reason: discord.ui.TextInput[CashTransactionModal] = discord.ui.TextInput(
            label="Motivo",
            placeholder="Ex.: Venda de produtos",
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=500,
        )
        self.add_item(self.amount)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        try:
            amount = Decimal(str(self.amount).strip().replace(",", "."))
            transaction_id = self.database.add_cash_transaction(
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                kind=self.kind,
                amount=amount,
                reason=str(self.reason),
            )
        except (InvalidOperation, ValueError) as exc:
            await interaction.response.send_message(
                str(exc) or "Informe um valor válido.", ephemeral=True, delete_after=15
            )
            return
        balance = self.database.cash_balance(interaction.guild_id)
        await interaction.response.send_message(
            f"Movimentação `#{transaction_id}` registrada. Saldo: **{money(balance)}**",
            ephemeral=True,
            delete_after=15,
        )
        refresh = refresh_panels(interaction, self.database)
        if interaction.guild is not None:
            await asyncio.gather(
                send_cash_log(
                    guild=interaction.guild,
                    database=self.database,
                    title=("💰 Entrada no caixa" if self.kind == "income" else "💸 Saída do caixa"),
                    amount=amount,
                    reason=str(self.reason),
                    actor_id=interaction.user.id,
                    balance=balance,
                    color=(discord.Color.green() if self.kind == "income" else discord.Color.red()),
                ),
                refresh,
            )
        else:
            await refresh


class ReserveRateModal(discord.ui.Modal, title="Configurar reserva da firma"):
    percentage: discord.ui.TextInput[ReserveRateModal] = discord.ui.TextInput(
        label="Percentual reservado",
        placeholder="Ex.: 30",
        min_length=1,
        max_length=6,
    )

    def __init__(self, database: Database, current_rate: Decimal) -> None:
        super().__init__()
        self.database = database
        self.percentage.default = format(current_rate * 100, "f")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        try:
            percentage = Decimal(str(self.percentage).strip().replace(",", "."))
            self.database.set_reserve_rate(interaction.guild_id, percentage / 100)
        except (InvalidOperation, ValueError) as exc:
            await interaction.response.send_message(
                str(exc) or "Informe um percentual válido.", ephemeral=True, delete_after=15
            )
            return
        await interaction.response.send_message(
            f"Reserva alterada para **{format(percentage, 'f')}%**.",
            ephemeral=True,
            delete_after=10,
        )
        await refresh_panels(interaction, self.database)


def settlement_embed(preview: SettlementPreview) -> discord.Embed:
    embed = discord.Embed(
        title="Prévia do fechamento da meta",
        description="Confira os valores antes de confirmar. Esta ação não poderá ser repetida.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Caixa atual", value=money(preview.cash_before))
    embed.add_field(
        name="Reserva da firma",
        value=f"{format(preview.reserve_rate * 100, 'f')}% — {money(preview.reserved_base)}",
    )
    embed.add_field(name="Base distribuível", value=money(preview.distributable))
    embed.add_field(
        name="Regra de distribuição",
        value=distribution_rule_name(preview.distribution_rule),
        inline=False,
    )
    lines = [
        f"<@{item.member_id}> — **{format(item.progress * 100, '.1f')}%** — "
        f"**{money(item.amount)}**"
        for item in preview.payouts
    ]
    embed.add_field(name="Pagamentos", value="\n".join(lines)[:1024], inline=False)
    embed.add_field(name="Total pago", value=money(preview.total_paid))
    embed.add_field(name="Permanecerá no caixa", value=money(preview.retained))
    return embed


class SettlementConfirmView(discord.ui.View):
    def __init__(self, database: Database, guild_id: int) -> None:
        super().__init__(timeout=300)
        self.database = database
        self.guild_id = guild_id

    @discord.ui.button(label="Confirmar divisão", style=discord.ButtonStyle.green, emoji="💵")
    async def confirm(
        self, interaction: discord.Interaction, _: discord.ui.Button[SettlementConfirmView]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild is None:
            return
        guild = interaction.guild
        try:
            preview = build_settlement_preview(self.database, self.guild_id)
            self.database.commit_goal_settlement(
                guild_id=self.guild_id,
                goal_id=preview.goal.id,
                actor_id=interaction.user.id,
                cash_before=preview.cash_before,
                reserve_rate=preview.reserve_rate,
                distributable=preview.distributable,
                distribution_rule=preview.distribution_rule,
                payouts=list(preview.payouts),
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True, delete_after=15)
            return
        await interaction.response.edit_message(
            content=(
                f"✅ Fechamento confirmado. **{money(preview.total_paid)}** pagos; "
                f"**{money(preview.retained)}** permaneceram no caixa."
            ),
            embed=None,
            view=None,
        )

        async def notify_payout(member_id: int, amount: Decimal, progress: Decimal) -> None:
            farm_channel = self.database.get_farm_channel(self.guild_id, member_id)
            if farm_channel is None:
                return
            channel = guild.get_channel(farm_channel.channel_id)
            if isinstance(channel, discord.TextChannel):
                with suppress(discord.Forbidden, discord.HTTPException):
                    await channel.send(
                        f"💵 **Fechamento da meta:** <@{member_id}> receberá "
                        f"**{money(amount)}** pelo progresso de "
                        f"**{format(progress * 100, '.1f')}%**."
                    )

        await asyncio.gather(
            *(
                notify_payout(item.member_id, item.amount, item.progress)
                for item in preview.payouts
            ),
            send_cash_log(
                guild=guild,
                database=self.database,
                title="💵 Fechamento da meta",
                amount=preview.total_paid,
                reason=(
                    f"Meta #{preview.goal.id}; reserva de "
                    f"{format(preview.reserve_rate * 100, 'f')}%"
                ),
                actor_id=interaction.user.id,
                balance=preview.retained,
                color=discord.Color.gold(),
            ),
            refresh_guild_panels(guild, self.database, refresh_all_farms=True),
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(
        self, interaction: discord.Interaction, _: discord.ui.Button[SettlementConfirmView]
    ) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await _delete_temporary_message(interaction)


class FarmPanel(discord.ui.View):
    def __init__(self, database: Database) -> None:
        super().__init__(timeout=None)
        self.database = database

    @discord.ui.button(
        label="Registrar coleta",
        style=discord.ButtonStyle.green,
        emoji="📦",
        custom_id="fdm:farm:register",
    )
    async def register(
        self, interaction: discord.Interaction, _: discord.ui.Button[FarmPanel]
    ) -> None:
        if interaction.channel_id is None or interaction.guild_id is None:
            return
        farm_channel = self.database.get_farm_channel_by_channel(interaction.channel_id)
        if farm_channel is None:
            await interaction.response.send_message(
                "Este canal não está vinculado a uma pessoa.", ephemeral=True
            )
            return
        if interaction.user.id != farm_channel.member_id and not _is_administrator(interaction):
            await interaction.response.send_message("Você não pode registrar aqui.", ephemeral=True)
            return
        products = self.database.list_products(interaction.guild_id, kind="farm")
        if not products:
            await interaction.response.send_message(
                "Nenhum produto foi configurado. Peça a um administrador para adicioná-los.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Selecione o produto:",
            view=ProductSelectView(
                self.database,
                products,
                farm_channel.member_id,
                interaction.message,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Consultar período",
        style=discord.ButtonStyle.secondary,
        emoji="📊",
        custom_id="fdm:farm:report",
    )
    async def report(
        self, interaction: discord.Interaction, _: discord.ui.Button[FarmPanel]
    ) -> None:
        if interaction.channel_id is None:
            return
        farm_channel = self.database.get_farm_channel_by_channel(interaction.channel_id)
        if farm_channel is None:
            await interaction.response.send_message(
                "Este canal não está vinculado a uma pessoa.", ephemeral=True
            )
            return
        if interaction.user.id != farm_channel.member_id and not _is_administrator(interaction):
            await interaction.response.send_message(
                "Você não pode consultar este histórico.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            PeriodReportModal(self.database, farm_channel.member_id)
        )


class MemberSelect(discord.ui.UserSelect["MemberSelectView"]):
    def __init__(self, database: Database) -> None:
        super().__init__(placeholder="Selecione a pessoa", min_values=1, max_values=1)
        self.database = database

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild is None:
            return
        selected = self.values[0]
        member = interaction.guild.get_member(selected.id)
        if member is None:
            try:
                member = await interaction.guild.fetch_member(selected.id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                await interaction.response.send_message(
                    "Não foi possível localizar esse membro no servidor.", ephemeral=True
                )
                return
        existing = self.database.get_farm_channel(interaction.guild.id, member.id)
        if existing and interaction.guild.get_channel(existing.channel_id):
            await interaction.response.send_message(
                f"Essa pessoa já possui o canal <#{existing.channel_id}>.", ephemeral=True
            )
            return
        category = discord.utils.find(
            lambda item: item.name.casefold() == "farme", interaction.guild.categories
        )
        if category is None:
            await interaction.response.send_message(
                "A categoria **FARME** não foi encontrada.", ephemeral=True
            )
            return
        bot_member = interaction.guild.me
        if bot_member is None:
            await interaction.response.send_message(
                "Não localizei o bot no servidor.", ephemeral=True
            )
            return
        overwrites: dict[
            discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite
        ] = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = await interaction.guild.create_text_channel(
            _channel_name(member),
            category=category,
            overwrites=overwrites,
            reason=f"Canal FARME criado por {interaction.user}",
        )
        panel_message = await channel.send(
            embed=build_farm_embed(self.database, interaction.guild.id, member.id),
            view=FarmPanel(self.database),
        )
        self.database.save_farm_channel(
            interaction.guild.id,
            member.id,
            channel.id,
            panel_message.id,
        )
        await _delete_temporary_message(interaction)
        if self.view and self.view.source_message:
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                await self.view.source_message.delete()


class MemberSelectView(discord.ui.View):
    def __init__(self, database: Database) -> None:
        super().__init__(timeout=120)
        self.source_message: discord.InteractionMessage | None = None
        self.add_item(MemberSelect(database))


class DeleteFarmRoomSelect(discord.ui.Select["DeleteFarmRoomView"]):
    def __init__(
        self,
        database: Database,
        guild: discord.Guild,
        farm_channels: list[FarmChannel],
        member_names: dict[int, str],
    ) -> None:
        self.database = database
        self.guild = guild
        options = []
        for farm_channel in farm_channels[:25]:
            member_name = member_names.get(
                farm_channel.member_id, f"Usuário {farm_channel.member_id}"
            )
            channel = guild.get_channel(farm_channel.channel_id)
            channel_name = getattr(channel, "name", str(farm_channel.channel_id))
            options.append(
                discord.SelectOption(
                    label=f"{member_name} — #{channel_name}"[:100],
                    value=str(farm_channel.member_id),
                    description=f"Pessoa: {member_name}"[:100],
                )
            )
        super().__init__(
            placeholder="Selecione a sala que será excluída",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction):
            return
        member_id = int(self.values[0])
        farm_channel = self.database.get_farm_channel(self.guild.id, member_id)
        if farm_channel is None:
            await interaction.response.send_message(
                "Essa sala não está mais cadastrada.", ephemeral=True, delete_after=10
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = self.guild.get_channel(farm_channel.channel_id)
        if channel is None:
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                fetched_channel = await self.guild.fetch_channel(farm_channel.channel_id)
                await fetched_channel.delete(reason=f"Sala FARME excluída por {interaction.user}")
        else:
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                await channel.delete(reason=f"Sala FARME excluída por {interaction.user}")
        self.database.delete_farm_channel(farm_channel.channel_id)
        await refresh_guild_panels(self.guild, self.database)
        await _delete_temporary_message(interaction)
        if self.view and self.view.source_message:
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                await self.view.source_message.delete()


class DeleteFarmRoomView(discord.ui.View):
    def __init__(
        self,
        database: Database,
        guild: discord.Guild,
        farm_channels: list[FarmChannel],
        member_names: dict[int, str] | None = None,
    ) -> None:
        super().__init__(timeout=120)
        self.source_message: discord.InteractionMessage | None = None
        self.add_item(DeleteFarmRoomSelect(database, guild, farm_channels, member_names or {}))


class DistributionRuleSelect(discord.ui.Select["DistributionRuleView"]):
    def __init__(self, database: Database, current_rule: str) -> None:
        self.database = database
        options = [
            discord.SelectOption(
                label="Proporcional ao progresso (regra atual)",
                value=PROPORTIONAL_RULE,
                description="Cada pessoa recebe a porcentagem exata alcançada.",
                default=current_rule == PROPORTIONAL_RULE,
            ),
            discord.SelectOption(
                label="Faixas + bônus aos 100%",
                value=TIERED_BONUS_RULE,
                description="Penalidades vão somente para quem cumpriu toda a meta.",
                default=current_rule == TIERED_BONUS_RULE,
            ),
        ]
        super().__init__(
            placeholder="Selecione a regra de distribuição",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        rule = self.values[0]
        self.database.set_distribution_rule(interaction.guild_id, rule)
        await interaction.response.edit_message(
            content=f"✅ Regra vigente: **{distribution_rule_name(rule)}**.",
            view=None,
        )
        await refresh_panels(interaction, self.database)


class DistributionRuleView(discord.ui.View):
    def __init__(self, database: Database, current_rule: str) -> None:
        super().__init__(timeout=120)
        self.add_item(DistributionRuleSelect(database, current_rule))


class ProductPriceModal(discord.ui.Modal, title="Definir preço de venda"):
    price: discord.ui.TextInput[ProductPriceModal] = discord.ui.TextInput(
        label="Preço unitário de venda",
        placeholder="Ex.: 125.50",
        required=True,
        max_length=30,
    )

    def __init__(self, database: Database, product: Product) -> None:
        super().__init__()
        self.database = database
        self.product = product
        if product.sale_price is not None:
            self.price.default = format(product.sale_price, "f")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        try:
            text = str(self.price).strip().replace(",", ".")
            price = Decimal(text)
            self.database.set_product_price(interaction.guild_id, self.product.id, price)
        except (InvalidOperation, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True, delete_after=15)
            return
        description = money(price)
        await interaction.response.send_message(
            f"**{self.product.name}** atualizado para **{description}**.",
            ephemeral=True,
            delete_after=10,
        )


class ProductPriceSelect(discord.ui.Select["ProductPriceView"]):
    def __init__(self, database: Database, products: list[Product]) -> None:
        self.database = database
        self.products = {str(product.id): product for product in products}
        super().__init__(
            placeholder="Selecione o produto",
            options=[
                discord.SelectOption(
                    label=product.name,
                    value=str(product.id),
                    description=(
                        f"Preço atual: {money(product.sale_price)}"
                        if product.sale_price is not None
                        else "Sem preço de venda"
                    ),
                )
                for product in products
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_modal(
            ProductPriceModal(self.database, self.products[self.values[0]])
        )
        await _delete_temporary_message(interaction)


class ProductPriceView(discord.ui.View):
    def __init__(self, database: Database, products: list[Product]) -> None:
        super().__init__(timeout=120)
        self.add_item(ProductPriceSelect(database, products))


class SaleQuantityModal(discord.ui.Modal, title="Registrar venda"):
    quantity: discord.ui.TextInput[SaleQuantityModal] = discord.ui.TextInput(
        label="Quantidade vendida", placeholder="Ex.: 12,5", min_length=1, max_length=30
    )

    def __init__(self, database: Database, product: Product) -> None:
        super().__init__()
        self.database = database
        self.product = product

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (
            not await _require_admin(interaction)
            or interaction.guild_id is None
            or interaction.guild is None
        ):
            return
        try:
            quantity = Decimal(str(self.quantity).strip().replace(",", "."))
            sale = self.database.register_sale(
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                product=self.product,
                quantity=quantity,
            )
        except (InvalidOperation, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True, delete_after=15)
            return
        balance = self.database.cash_balance(interaction.guild_id)
        await interaction.response.send_message(
            f"✅ Venda `#{sale.id}` registrada: **{format(quantity, 'f')} "
            f"{sale.product_name}** por **{money(sale.total)}**.",
            ephemeral=True,
            delete_after=15,
        )
        await asyncio.gather(
            send_output_log(
                guild=interaction.guild,
                database=self.database,
                actor_id=interaction.user.id,
                product=self.product,
                quantity=quantity,
                reason=f"Venda #{sale.id}",
                output_id=sale.stock_output_id,
            ),
            send_cash_log(
                guild=interaction.guild,
                database=self.database,
                title="💵 Venda registrada",
                amount=sale.total,
                reason=f"Venda #{sale.id}: {format(quantity, 'f')} {sale.product_name}",
                actor_id=interaction.user.id,
                balance=balance,
                color=discord.Color.green(),
            ),
            refresh_panels(interaction, self.database),
        )


class SaleProductSelect(discord.ui.Select["SaleProductView"]):
    def __init__(self, database: Database, products: list[Product]) -> None:
        self.database = database
        self.products = {str(product.id): product for product in products}
        super().__init__(
            placeholder="Selecione o produto vendido",
            options=[
                discord.SelectOption(
                    label=product.name,
                    value=str(product.id),
                    description=f"Preço unitário: {money(product.sale_price or Decimal(0))}",
                )
                for product in products
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_modal(
            SaleQuantityModal(self.database, self.products[self.values[0]])
        )
        await _delete_temporary_message(interaction)


class SaleProductView(discord.ui.View):
    def __init__(self, database: Database, products: list[Product]) -> None:
        super().__init__(timeout=120)
        self.add_item(SaleProductSelect(database, products))


class SaleStockInputModal(discord.ui.Modal, title="Entrada de estoque"):
    quantity: discord.ui.TextInput[SaleStockInputModal] = discord.ui.TextInput(
        label="Quantidade adicionada", placeholder="Ex.: 10", min_length=1, max_length=30
    )

    def __init__(self, database: Database, product: Product) -> None:
        super().__init__()
        self.database = database
        self.product = product

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        try:
            quantity = Decimal(str(self.quantity).strip().replace(",", "."))
            entry_id = self.database.add_stock_input(
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                product=self.product,
                quantity=quantity,
            )
        except (InvalidOperation, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True, delete_after=15)
            return
        await interaction.response.send_message(
            f"✅ Entrada `#{entry_id}`: **{format(quantity, 'f')} {self.product.name}**.",
            ephemeral=True,
            delete_after=10,
        )
        refresh = refresh_panels(interaction, self.database)
        if interaction.guild is not None:
            await asyncio.gather(
                send_stock_input_log(
                    guild=interaction.guild,
                    database=self.database,
                    actor_id=interaction.user.id,
                    product=self.product,
                    quantity=quantity,
                    input_id=entry_id,
                ),
                refresh,
            )
        else:
            await refresh


class SaleStockInputSelect(discord.ui.Select["SaleStockInputView"]):
    def __init__(self, database: Database, products: list[Product]) -> None:
        self.database = database
        self.products = {str(product.id): product for product in products}
        super().__init__(
            placeholder="Selecione o produto",
            options=[
                discord.SelectOption(label=product.name, value=str(product.id))
                for product in products
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_modal(
            SaleStockInputModal(self.database, self.products[self.values[0]])
        )
        await _delete_temporary_message(interaction)


class SaleStockInputView(discord.ui.View):
    def __init__(self, database: Database, products: list[Product]) -> None:
        super().__init__(timeout=120)
        self.add_item(SaleStockInputSelect(database, products))


class StockMovementSelect(discord.ui.Select["StockMovementView"]):
    def __init__(self, database: Database) -> None:
        self.database = database
        super().__init__(
            placeholder="Selecione o tipo de movimentação",
            options=[
                discord.SelectOption(label="Entrada de estoque", value="input", emoji="📥"),
                discord.SelectOption(label="Saída de estoque", value="output", emoji="📤"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        products = self.database.list_products(interaction.guild_id)
        if not products:
            await interaction.response.send_message(
                "A lista de produtos está vazia.", ephemeral=True, delete_after=10
            )
            return
        view: discord.ui.View = (
            SaleStockInputView(self.database, products)
            if self.values[0] == "input"
            else OutputProductView(self.database, products)
        )
        await interaction.response.edit_message(content="Selecione o produto:", view=view)


class StockMovementView(discord.ui.View):
    def __init__(self, database: Database) -> None:
        super().__init__(timeout=120)
        self.add_item(StockMovementSelect(database))


class SalesPeriodSelect(discord.ui.Select["SalesReportView"]):
    def __init__(self, database: Database) -> None:
        self.database = database
        super().__init__(
            placeholder="Selecione o período",
            options=[
                discord.SelectOption(label="Hoje", value="day"),
                discord.SelectOption(label="Semana atual", value="week"),
                discord.SelectOption(label="Mês atual", value="month"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        await interaction.response.edit_message(
            content=None,
            embed=build_sales_embed(self.database, interaction.guild_id, self.values[0]),
            view=self.view,
        )


class SalesReportView(discord.ui.View):
    def __init__(self, database: Database) -> None:
        super().__init__(timeout=300)
        self.add_item(SalesPeriodSelect(database))


class ResetRoleSelect(discord.ui.RoleSelect["DataToolsView"]):
    def __init__(self, database: Database) -> None:
        super().__init__(placeholder="Definir cargo autorizado para reset", max_values=1)
        self.database = database

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        role = self.values[0]
        self.database.set_reset_role(interaction.guild_id, role.id)
        await interaction.response.send_message(
            f"Cargo <@&{role.id}> autorizado para reset.", ephemeral=True, delete_after=10
        )


class ResetDatabaseModal(discord.ui.Modal, title="Resetar dados operacionais"):
    confirmation: discord.ui.TextInput[ResetDatabaseModal] = discord.ui.TextInput(
        label="Confirmação exigida", min_length=1, max_length=40
    )

    def __init__(self, database: Database, guild_id: int) -> None:
        super().__init__()
        self.database = database
        self.guild_id = guild_id
        self.confirmation.placeholder = f"Digite RESETAR {guild_id}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or not isinstance(
            interaction.user, discord.Member
        ):
            return
        role_id = self.database.get_reset_role(self.guild_id)
        has_role = role_id is not None and any(
            role.id == role_id for role in interaction.user.roles
        )
        if not has_role:
            await interaction.response.send_message(
                "Você não possui o cargo específico autorizado para reset.",
                ephemeral=True,
                delete_after=15,
            )
            return
        if str(self.confirmation).strip() != f"RESETAR {self.guild_id}":
            await interaction.response.send_message(
                "Confirmação incorreta. Nenhum dado foi apagado.", ephemeral=True, delete_after=15
            )
            return
        self.database.reset_operational_data(self.guild_id)
        await interaction.response.send_message(
            "✅ Coletas, estoque, metas, vendas e movimentações do caixa foram zerados. "
            "Produtos, preços, salas e configurações foram preservados.",
            ephemeral=True,
        )
        await refresh_panels(interaction, self.database, refresh_all_farms=True)


class DataToolsView(discord.ui.View):
    def __init__(self, database: Database) -> None:
        super().__init__(timeout=300)
        self.database = database
        self.add_item(ResetRoleSelect(database))

    @discord.ui.button(label="Resumo do banco", style=discord.ButtonStyle.secondary, row=1)
    async def summary(
        self, interaction: discord.Interaction, _: discord.ui.Button[DataToolsView]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        summary = self.database.database_summary(interaction.guild_id)
        labels = {
            "products": "Produtos",
            "farm_channels": "Salas FARME",
            "farm_entries": "Coletas",
            "stock_outputs": "Saídas de estoque",
            "stock_inputs": "Entradas de produtos de venda",
            "goals": "Metas",
            "sales": "Vendas",
            "cash_transactions": "Movimentações de caixa",
        }
        embed = discord.Embed(title="Resumo do banco de dados", color=discord.Color.blurple())
        embed.description = "\n".join(
            f"**{labels[key]}:** {value}" for key, value in summary.items()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Reset protegido", style=discord.ButtonStyle.danger, row=1)
    async def reset(
        self, interaction: discord.Interaction, _: discord.ui.Button[DataToolsView]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        if self.database.get_reset_role(interaction.guild_id) is None:
            await interaction.response.send_message(
                "Defina primeiro o cargo autorizado no seletor acima.",
                ephemeral=True,
                delete_after=15,
            )
            return
        await interaction.response.send_modal(
            ResetDatabaseModal(self.database, interaction.guild_id)
        )
        await _delete_temporary_message(interaction)


MENU_OPTIONS: dict[str, list[discord.SelectOption]] = {
    "rooms": [
        discord.SelectOption(label="Criar sala FARME", value="room_create", emoji="🔒"),
        discord.SelectOption(label="Excluir sala FARME", value="room_delete", emoji="🗑️"),
    ],
    "products": [
        discord.SelectOption(label="Cadastrar produto FARME", value="farm_product", emoji="📦"),
        discord.SelectOption(label="Cadastrar produto VENDA", value="sale_product", emoji="🏷️"),
        discord.SelectOption(label="Adicionar estoque de venda", value="sale_stock", emoji="📥"),
        discord.SelectOption(label="Alterar preços", value="prices", emoji="💲"),
        discord.SelectOption(label="Remover produto", value="remove_product", emoji="➖"),
        discord.SelectOption(label="Saída manual de estoque", value="stock_output", emoji="📤"),
    ],
    "goals": [
        discord.SelectOption(label="Criar meta", value="goal_create", emoji="🎯"),
        discord.SelectOption(label="Editar meta", value="goal_edit", emoji="✏️"),
        discord.SelectOption(label="Encerrar meta", value="goal_close", emoji="🏁"),
        discord.SelectOption(label="Regra de divisão", value="distribution_rule", emoji="⚖️"),
    ],
    "finance": [
        discord.SelectOption(label="Entrada no caixa", value="cash_income", emoji="💰"),
        discord.SelectOption(label="Saída do caixa", value="cash_expense", emoji="💸"),
        discord.SelectOption(label="Reserva da firma", value="reserve", emoji="🏦"),
        discord.SelectOption(label="Registrar venda", value="sale", emoji="🛒"),
        discord.SelectOption(label="Relatório de vendas", value="sales_report", emoji="📊"),
    ],
    "admin": [
        discord.SelectOption(label="Consultar membro", value="member_report", emoji="🔎"),
        discord.SelectOption(label="Atualizar painel", value="refresh", emoji="🔄"),
        discord.SelectOption(label="Dados e reset", value="data_tools", emoji="🗄️"),
    ],
}


class AdminCategorySelect(discord.ui.Select["AdminCategoryView"]):  # pragma: no cover
    def __init__(self, database: Database, category: str) -> None:
        self.database = database
        super().__init__(placeholder="Escolha uma ação", options=MENU_OPTIONS[category])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        action = self.values[0]
        guild_id = interaction.guild_id
        if action == "room_create":
            selection_view = MemberSelectView(self.database)
            await interaction.response.send_message(
                "Escolha a pessoa que terá acesso ao canal:",
                view=selection_view,
                ephemeral=True,
            )
            selection_view.source_message = await interaction.original_response()
            return
        if action == "room_delete":
            if interaction.guild is None:
                return
            farm_channels = self.database.list_farm_channels(guild_id)
            if not farm_channels:
                await interaction.response.send_message(
                    "Nenhuma sala FARME está cadastrada.", ephemeral=True, delete_after=10
                )
                return
            names: dict[int, str] = {}
            for item in farm_channels[:25]:
                member = interaction.guild.get_member(item.member_id)
                names[item.member_id] = (
                    member.display_name if member else f"Usuário {item.member_id}"
                )
            await interaction.response.send_message(
                "Escolha a sala FARME que deseja excluir:",
                view=DeleteFarmRoomView(
                    self.database, interaction.guild, farm_channels, names
                ),
                ephemeral=True,
            )
            return
        if action == "farm_product":
            await interaction.response.send_modal(ProductModal(self.database))
            return
        if action == "sale_product":
            await interaction.response.send_modal(SaleProductModal(self.database))
            return
        if action in {"sale_stock", "prices", "sale"}:
            products = self.database.list_products(guild_id, kind="sale")
            if not products:
                await interaction.response.send_message(
                    "Nenhum produto de venda foi cadastrado.", ephemeral=True, delete_after=12
                )
                return
            if action == "sale_stock":
                view: discord.ui.View = SaleStockInputView(self.database, products)
                text = "Selecione o produto que entrou no estoque:"
            elif action == "prices":
                view = ProductPriceView(self.database, products)
                text = "Selecione o produto cujo preço deseja alterar:"
            else:
                view = SaleProductView(self.database, products)
                text = "Selecione o produto vendido:"
            await interaction.response.send_message(text, view=view, ephemeral=True)
            return
        if action in {"remove_product", "stock_output"}:
            products = self.database.list_products(guild_id)
            if not products:
                await interaction.response.send_message(
                    "A lista de produtos está vazia.", ephemeral=True, delete_after=10
                )
                return
            view = (
                RemoveProductView(self.database, products)
                if action == "remove_product"
                else OutputProductView(self.database, products)
            )
            await interaction.response.send_message(
                "Selecione o produto:", view=view, ephemeral=True
            )
            return
        if action == "goal_create":
            await interaction.response.send_modal(GoalPeriodModal(self.database))
            return
        if action in {"goal_edit", "goal_close"}:
            goal = self.database.get_active_goal(guild_id)
            if goal is None:
                await interaction.response.send_message(
                    "Não existe uma meta ativa.", ephemeral=True, delete_after=10
                )
                return
            if action == "goal_edit":
                await interaction.response.send_modal(
                    EditGoalModal(self.database, goal.id, goal.start_at, goal.end_at)
                )
            else:
                try:
                    preview = build_settlement_preview(self.database, guild_id)
                except ValueError as exc:
                    await interaction.response.send_message(
                        str(exc), ephemeral=True, delete_after=15
                    )
                    return
                await interaction.response.send_message(
                    embed=settlement_embed(preview),
                    view=SettlementConfirmView(self.database, guild_id),
                    ephemeral=True,
                )
            return
        if action == "distribution_rule":
            current = self.database.get_distribution_rule(guild_id)
            await interaction.response.send_message(
                f"Regra vigente: **{distribution_rule_name(current)}**.",
                view=DistributionRuleView(self.database, current),
                ephemeral=True,
            )
            return
        if action in {"cash_income", "cash_expense"}:
            await interaction.response.send_modal(
                CashTransactionModal(
                    self.database, "income" if action == "cash_income" else "expense"
                )
            )
            return
        if action == "reserve":
            await interaction.response.send_modal(
                ReserveRateModal(self.database, self.database.get_reserve_rate(guild_id))
            )
            return
        if action == "sales_report":
            await interaction.response.send_message(
                embed=build_sales_embed(self.database, guild_id, "week"),
                view=SalesReportView(self.database),
                ephemeral=True,
            )
            return
        if action == "member_report":
            await interaction.response.send_message(
                "Escolha a pessoa:",
                view=AdminMemberReportView(self.database),
                ephemeral=True,
            )
            return
        if action == "refresh":
            await interaction.response.edit_message(
                embed=build_admin_embed(self.database, guild_id), view=ConfigPanel(self.database)
            )
            return
        await interaction.response.send_message(
            "Consulte os dados ou configure o reset protegido.",
            view=DataToolsView(self.database),
            ephemeral=True,
        )


class AdminCategoryView(discord.ui.View):  # pragma: no cover
    def __init__(self, database: Database, category: str) -> None:
        super().__init__(timeout=180)
        self.add_item(AdminCategorySelect(database, category))


class AdminCategoryButton(discord.ui.Button["ConfigPanel"]):  # pragma: no cover
    def __init__(self, database: Database, category: str, label: str, emoji: str) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"fdm:menu:{category}",
        )
        self.database = database
        self.category = category

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_message(
            f"Menu **{self.label}**:",
            view=AdminCategoryView(self.database, self.category),
            ephemeral=True,
        )


class ConfigPanel(discord.ui.View):
    def __init__(self, database: Database) -> None:
        super().__init__(timeout=None)
        self.database = database
        hidden_ids = {
            "fdm:config:add-sale-product",
            "fdm:config:add-sale-stock",
            "fdm:config:data-tools",
        }
        for item in list(self.children):
            if getattr(item, "custom_id", None) in hidden_ids:
                self.remove_item(item)

    @discord.ui.button(
        label="Criar sala FARME",
        style=discord.ButtonStyle.primary,
        emoji="🔒",
        custom_id="fdm:config:create-channel",
        row=0,
    )
    async def create_channel(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction):
            return
        selection_view = MemberSelectView(self.database)
        await interaction.response.send_message(
            "Escolha a pessoa que terá acesso ao canal:",
            view=selection_view,
            ephemeral=True,
        )
        selection_view.source_message = await interaction.original_response()

    @discord.ui.button(
        label="Excluir sala FARME",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        custom_id="fdm:config:delete-channel",
        row=0,
    )
    async def delete_channel(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild is None:
            return
        guild = interaction.guild
        farm_channels = self.database.list_farm_channels(guild.id)
        if not farm_channels:
            await interaction.response.send_message(
                "Nenhuma sala FARME está cadastrada.", ephemeral=True, delete_after=10
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def resolve_member_name(farm_channel: FarmChannel) -> tuple[int, str]:
            member = guild.get_member(farm_channel.member_id)
            if member is None:
                with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = await guild.fetch_member(farm_channel.member_id)
            name = member.display_name if member else f"Usuário {farm_channel.member_id}"
            return farm_channel.member_id, name

        resolved_names = await asyncio.gather(
            *(resolve_member_name(item) for item in farm_channels[:25])
        )
        selection_view = DeleteFarmRoomView(
            self.database,
            guild,
            farm_channels,
            dict(resolved_names),
        )
        await interaction.edit_original_response(
            content="Escolha a sala FARME que deseja excluir:",
            view=selection_view,
        )
        selection_view.source_message = await interaction.original_response()

    @discord.ui.button(
        label="Cadastrar produto",
        style=discord.ButtonStyle.green,
        emoji="➕",
        custom_id="fdm:config:add-product",
        row=1,
    )
    async def add_product(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_message(
            "Escolha como o produto será utilizado:",
            view=ProductKindView(self.database),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Produto VENDA",
        style=discord.ButtonStyle.green,
        emoji="🏷️",
        custom_id="fdm:config:add-sale-product",
        row=1,
    )
    async def add_sale_product(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_modal(SaleProductModal(self.database))

    @discord.ui.button(
        label="Estoque VENDA",
        style=discord.ButtonStyle.green,
        emoji="📥",
        custom_id="fdm:config:add-sale-stock",
        row=1,
    )
    async def add_sale_stock(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        products = self.database.list_products(interaction.guild_id, kind="sale")
        if not products:
            await interaction.response.send_message(
                "Nenhum produto de venda foi cadastrado.", ephemeral=True, delete_after=10
            )
            return
        await interaction.response.send_message(
            "Selecione o produto que entrou no estoque:",
            view=SaleStockInputView(self.database, products),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Remover produto",
        style=discord.ButtonStyle.red,
        emoji="➖",
        custom_id="fdm:config:remove-product",
        row=1,
    )
    async def remove_product(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        products = self.database.list_products(interaction.guild_id)
        if not products:
            await interaction.response.send_message(
                "A lista de produtos está vazia.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Escolha o produto que deseja remover:",
            view=RemoveProductView(self.database, products),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Entrada / saída estoque",
        style=discord.ButtonStyle.secondary,
        emoji="📤",
        custom_id="fdm:config:output",
        row=1,
    )
    async def output(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        await interaction.response.send_message(
            "Escolha o tipo de movimentação:",
            view=StockMovementView(self.database),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Consultar membro",
        style=discord.ButtonStyle.secondary,
        emoji="🔎",
        custom_id="fdm:config:member-report",
        row=0,
    )
    async def member_report(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_message(
            "Escolha a pessoa:",
            view=AdminMemberReportView(self.database),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Criar meta",
        style=discord.ButtonStyle.primary,
        emoji="🎯",
        custom_id="fdm:config:goal",
        row=2,
    )
    async def goal(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_modal(GoalPeriodModal(self.database))

    @discord.ui.button(
        label="Atualizar",
        style=discord.ButtonStyle.secondary,
        emoji="🔄",
        custom_id="fdm:config:refresh",
        row=0,
    )
    async def refresh(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        await interaction.response.edit_message(
            embed=build_admin_embed(self.database, interaction.guild_id), view=self
        )

    @discord.ui.button(
        label="Encerrar meta",
        style=discord.ButtonStyle.danger,
        emoji="🏁",
        custom_id="fdm:config:close-goal",
        row=2,
    )
    async def close_goal(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        try:
            preview = build_settlement_preview(self.database, interaction.guild_id)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True, delete_after=15)
            return
        await interaction.response.send_message(
            embed=settlement_embed(preview),
            view=SettlementConfirmView(self.database, interaction.guild_id),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Editar meta",
        style=discord.ButtonStyle.secondary,
        emoji="✏️",
        custom_id="fdm:config:edit-goal",
        row=2,
    )
    async def edit_goal(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        goal = self.database.get_active_goal(interaction.guild_id)
        if goal is None:
            await interaction.response.send_message(
                "Não existe uma meta ativa para editar.", ephemeral=True, delete_after=10
            )
            return
        await interaction.response.send_modal(
            EditGoalModal(
                self.database,
                goal.id,
                goal.start_at,
                goal.end_at,
            )
        )

    @discord.ui.button(
        label="Entrada no caixa",
        style=discord.ButtonStyle.green,
        emoji="💰",
        custom_id="fdm:config:cash-income",
        row=3,
    )
    async def cash_income(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_modal(CashTransactionModal(self.database, "income"))

    @discord.ui.button(
        label="Saída do caixa",
        style=discord.ButtonStyle.danger,
        emoji="💸",
        custom_id="fdm:config:cash-expense",
        row=3,
    )
    async def cash_expense(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_modal(CashTransactionModal(self.database, "expense"))

    @discord.ui.button(
        label="Reserva da firma",
        style=discord.ButtonStyle.secondary,
        emoji="🏦",
        custom_id="fdm:config:reserve-rate",
        row=3,
    )
    async def reserve_rate(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        await interaction.response.send_modal(
            ReserveRateModal(self.database, self.database.get_reserve_rate(interaction.guild_id))
        )

    @discord.ui.button(
        label="Regra de divisão",
        style=discord.ButtonStyle.secondary,
        emoji="⚖️",
        custom_id="fdm:config:distribution-rule",
        row=2,
    )
    async def distribution_rule(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        current_rule = self.database.get_distribution_rule(interaction.guild_id)
        await interaction.response.send_message(
            f"Regra vigente: **{distribution_rule_name(current_rule)}**.\n"
            "Escolha a regra aplicada ao próximo fechamento:",
            view=DistributionRuleView(self.database, current_rule),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Preços",
        style=discord.ButtonStyle.secondary,
        emoji="🏷️",
        custom_id="fdm:config:prices",
        row=4,
    )
    async def prices(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        products = self.database.list_products(interaction.guild_id, kind="sale")
        if not products:
            await interaction.response.send_message(
                "Cadastre um produto primeiro.", ephemeral=True, delete_after=10
            )
            return
        await interaction.response.send_message(
            "Selecione o produto cujo preço deseja alterar:",
            view=ProductPriceView(self.database, products),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Registrar venda",
        style=discord.ButtonStyle.green,
        emoji="🛒",
        custom_id="fdm:config:sale",
        row=4,
    )
    async def sale(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        products = [
            product
            for product in self.database.list_products(interaction.guild_id, kind="sale")
            if product.sale_price is not None
        ]
        if not products:
            await interaction.response.send_message(
                "Nenhum produto possui preço de venda. Use o botão **Preços**.",
                ephemeral=True,
                delete_after=15,
            )
            return
        await interaction.response.send_message(
            "Selecione o produto vendido:",
            view=SaleProductView(self.database, products),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Relatório de vendas",
        style=discord.ButtonStyle.primary,
        emoji="📊",
        custom_id="fdm:config:sales-report",
        row=4,
    )
    async def sales_report(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction) or interaction.guild_id is None:
            return
        await interaction.response.send_message(
            embed=build_sales_embed(self.database, interaction.guild_id, "week"),
            view=SalesReportView(self.database),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Dados / reset",
        style=discord.ButtonStyle.danger,
        emoji="🗄️",
        custom_id="fdm:config:data-tools",
        row=4,
    )
    async def data_tools(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_message(
            "Consulte o resumo ou configure o cargo necessário para um reset protegido.",
            view=DataToolsView(self.database),
            ephemeral=True,
        )


class _CompactConfigPanel(discord.ui.View):  # pragma: no cover
    """Compact persistent administrative menu grouped by business area."""

    def __init__(self, database: Database) -> None:
        super().__init__(timeout=None)
        self.add_item(AdminCategoryButton(database, "rooms", "Salas FARME", "🔐"))
        self.add_item(AdminCategoryButton(database, "products", "Produtos / Estoque", "📦"))
        self.add_item(AdminCategoryButton(database, "goals", "Metas", "🎯"))
        self.add_item(AdminCategoryButton(database, "finance", "Financeiro / Vendas", "💰"))
        self.add_item(AdminCategoryButton(database, "admin", "Administração", "⚙️"))
