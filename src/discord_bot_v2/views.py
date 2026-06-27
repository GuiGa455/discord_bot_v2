"""Discord UI components for farm configuration and product registration."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from contextlib import suppress
from decimal import Decimal, InvalidOperation

import discord

from discord_bot_v2.database import Database, Product
from discord_bot_v2.reporting import (
    build_admin_embed,
    build_farm_embed,
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


async def refresh_panels(interaction: discord.Interaction, database: Database) -> None:
    """Refresh known admin and farm panel embeds after a state change."""
    guild = interaction.guild
    if guild is None:
        return
    for channel_id, message_id in database.list_admin_panels(guild.id):
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(
                    embed=build_admin_embed(database, guild.id),
                    view=ConfigPanel(database),
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
    for farm_channel in database.list_farm_channels(guild.id):
        if farm_channel.panel_message_id is None:
            continue
        channel = guild.get_channel(farm_channel.channel_id)
        if isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(farm_channel.panel_message_id)
                await message.edit(
                    embed=build_farm_embed(database, guild.id, farm_channel.member_id),
                    view=FarmPanel(database),
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue


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
            product = self.database.add_product(interaction.guild_id, str(self.name))
        except sqlite3.IntegrityError:
            await interaction.response.send_message(
                "Esse produto já está cadastrado.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Produto **{product.name}** adicionado.", ephemeral=True, delete_after=10
        )


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
        try:
            self.database.remove_product(interaction.guild_id, int(self.values[0]))
        except sqlite3.IntegrityError:
            await interaction.response.edit_message(
                content="Esse produto pertence a uma meta e não pode ser removido.",
                view=None,
            )
            return
        await interaction.response.defer(ephemeral=True)
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

    def __init__(self, database: Database, product: Product, member_id: int) -> None:
        super().__init__()
        self.database = database
        self.product = product
        self.member_id = member_id

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
        self.database.add_entry(
            guild_id=interaction.guild_id,
            member_id=self.member_id,
            actor_id=interaction.user.id,
            actor_was_admin=admin_registration,
            product=self.product,
            quantity=quantity,
        )
        await interaction.delete_original_response()
        await refresh_panels(interaction, self.database)


class ProductSelect(discord.ui.Select["ProductSelectView"]):
    def __init__(self, database: Database, products: list[Product], member_id: int) -> None:
        self.database = database
        self.products = {str(product.id): product for product in products}
        self.member_id = member_id
        super().__init__(
            placeholder="Qual produto foi coletado?",
            options=[
                discord.SelectOption(label=item.name, value=str(item.id)) for item in products
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            QuantityModal(self.database, self.products[self.values[0]], self.member_id)
        )
        await _delete_temporary_message(interaction)


class ProductSelectView(discord.ui.View):
    def __init__(self, database: Database, products: list[Product], member_id: int) -> None:
        super().__init__(timeout=120)
        self.add_item(ProductSelect(database, products, member_id))


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
        await refresh_panels(interaction, self.database)


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
            await self.builder_message.edit(
                content=(
                    f"✅ **{format(target, 'f')} {self.product.name}** adicionado à meta. "
                    "Selecione outro produto ou finalize."
                )
            )
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
        await refresh_panels(interaction, self.database)


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
        products = self.database.list_products(interaction.guild_id)
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
        products = self.database.list_products(interaction.guild_id)
        if not products:
            await interaction.response.send_message(
                "Nenhum produto foi configurado. Peça a um administrador para adicioná-los.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Selecione o produto:",
            view=ProductSelectView(self.database, products, farm_channel.member_id),
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


class ConfigPanel(discord.ui.View):
    def __init__(self, database: Database) -> None:
        super().__init__(timeout=None)
        self.database = database

    @discord.ui.button(
        label="Criar sala FARME",
        style=discord.ButtonStyle.primary,
        emoji="🔒",
        custom_id="fdm:config:create-channel",
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
        label="Adicionar produto",
        style=discord.ButtonStyle.green,
        emoji="➕",
        custom_id="fdm:config:add-product",
    )
    async def add_product(
        self, interaction: discord.Interaction, _: discord.ui.Button[ConfigPanel]
    ) -> None:
        if not await _require_admin(interaction):
            return
        await interaction.response.send_modal(ProductModal(self.database))

    @discord.ui.button(
        label="Remover produto",
        style=discord.ButtonStyle.red,
        emoji="➖",
        custom_id="fdm:config:remove-product",
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
        label="Saída de estoque",
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
        products = self.database.list_products(interaction.guild_id)
        if not products:
            await interaction.response.send_message(
                "A lista de produtos está vazia.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Selecione o produto retirado:",
            view=OutputProductView(self.database, products),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Consultar membro",
        style=discord.ButtonStyle.secondary,
        emoji="🔎",
        custom_id="fdm:config:member-report",
        row=1,
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
        row=1,
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
        if not self.database.close_active_goal(interaction.guild_id):
            await interaction.response.send_message(
                "Não existe uma meta ativa.", ephemeral=True, delete_after=10
            )
            return
        await interaction.response.defer()
        await refresh_panels(interaction, self.database)
