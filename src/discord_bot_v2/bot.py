"""Discord client creation and application entry point."""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from discord_bot_v2.config import Settings
from discord_bot_v2.database import Database
from discord_bot_v2.logging_config import configure_logging
from discord_bot_v2.reporting import build_admin_embed, build_farm_embed
from discord_bot_v2.views import ConfigPanel, FarmPanel, refresh_guild_panels

LOGGER = logging.getLogger(__name__)


class DiscordBot(discord.Client):
    """Application client with explicitly scoped Discord events."""

    def __init__(self, *, intents: discord.Intents, database: Database) -> None:
        super().__init__(intents=intents)
        self.database = database
        self.tree = app_commands.CommandTree(self)
        self._guild_commands_synced = False
        self._farm_panels_restored = False
        self.tree.command(name="oi", description="Receba uma saudação do bot")(self.slash_oi)
        self.tree.command(
            name="configurar_bot_fdm",
            description="Publica o painel administrativo de coleta",
        )(self.configure_bot_fdm)
        self.tree.command(
            name="configurar_logs_fdm",
            description="Define os canais de log de entradas e saídas",
        )(self.configurar_logs_fdm)
        self.tree.command(
            name="configurar_log_caixa_fdm",
            description="Define o canal de auditoria do caixa",
        )(self.configurar_log_caixa_fdm)

    async def setup_hook(self) -> None:
        self.database.initialize()
        self.add_view(ConfigPanel(self.database))
        self.add_view(FarmPanel(self.database))
        synced_commands = await self.tree.sync()
        LOGGER.info(
            "Application commands synchronized",
            extra={"command_count": len(synced_commands)},
        )

    async def slash_oi(self, interaction: discord.Interaction) -> None:
        """Respond to the native /oi interaction."""
        await interaction.response.send_message("Olá! 👋")

    async def configurar_logs_fdm(
        self,
        interaction: discord.Interaction,
        canal_entradas: discord.TextChannel,
        canal_saidas: discord.TextChannel,
    ) -> None:
        """Persist the audit destinations for one guild."""
        if (
            not isinstance(interaction.user, discord.Member)
            or not interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                "Apenas administradores podem executar este comando.",
                ephemeral=True,
                delete_after=10,
            )
            return
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(
                "Este comando só pode ser usado dentro de um servidor.", ephemeral=True
            )
            return
        bot_member = interaction.guild.me
        inaccessible: list[str] = []
        if bot_member is None:
            inaccessible = [canal_entradas.mention, canal_saidas.mention]
        else:
            for channel in (canal_entradas, canal_saidas):
                permissions = channel.permissions_for(bot_member)
                if not permissions.view_channel or not permissions.send_messages:
                    inaccessible.append(channel.mention)
        if inaccessible:
            await interaction.response.send_message(
                "O bot precisa de **Ver canal** e **Enviar mensagens** em: "
                + ", ".join(inaccessible),
                ephemeral=True,
                delete_after=15,
            )
            return
        self.database.set_log_channels(interaction.guild_id, canal_entradas.id, canal_saidas.id)
        await interaction.response.send_message(
            f"Logs configurados: entradas em {canal_entradas.mention} e "
            f"saídas em {canal_saidas.mention}.",
            ephemeral=True,
            delete_after=15,
        )

    async def configurar_log_caixa_fdm(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
    ) -> None:
        """Persist the cash audit destination for one guild."""
        if (
            not isinstance(interaction.user, discord.Member)
            or not interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                "Apenas administradores podem executar este comando.",
                ephemeral=True,
                delete_after=10,
            )
            return
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(
                "Este comando só pode ser usado dentro de um servidor.", ephemeral=True
            )
            return
        bot_member = interaction.guild.me
        permissions = canal.permissions_for(bot_member) if bot_member else None
        if permissions is None or not permissions.view_channel or not permissions.send_messages:
            await interaction.response.send_message(
                "O bot precisa de **Ver canal** e **Enviar mensagens** nesse canal.",
                ephemeral=True,
                delete_after=15,
            )
            return
        self.database.set_cash_log_channel(interaction.guild_id, canal.id)
        await interaction.response.send_message(
            f"Log do caixa configurado em {canal.mention}.",
            ephemeral=True,
            delete_after=15,
        )

    async def configure_bot_fdm(self, interaction: discord.Interaction) -> None:
        """Publish the administrator panel in the current channel."""
        if (
            not isinstance(interaction.user, discord.Member)
            or not interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                "Apenas administradores podem executar este comando.", ephemeral=True
            )
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                "Este comando só pode ser usado dentro de um servidor.", ephemeral=True
            )
            return
        bot_member = interaction.guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "O bot precisa da permissão **Gerenciar Canais** para criar os canais privados.",
                ephemeral=True,
            )
            return
        category = discord.utils.find(
            lambda item: item.name.casefold() == "farme", interaction.guild.categories
        )
        if category is None:
            await interaction.response.send_message(
                "Crie uma categoria chamada **FARME** antes de configurar o bot.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_admin_embed(self.database, interaction.guild.id),
            view=ConfigPanel(self.database),
        )
        panel_message = await interaction.original_response()
        self.database.save_admin_panel(
            interaction.guild.id, panel_message.channel.id, panel_message.id
        )

    async def on_connect(self) -> None:
        LOGGER.info("Connected to Discord gateway")

    async def on_ready(self) -> None:
        LOGGER.info(
            "Bot ready",
            extra={
                "discord_user": str(self.user),
                "guild_count": len(self.guilds),
            },
        )
        if not self._guild_commands_synced:
            await self._sync_commands_to_guilds()
        if not self._farm_panels_restored:
            await self._restore_farm_panels()

    async def _restore_farm_panels(self) -> None:
        """Upgrade panels created before their message IDs were persisted."""
        for guild in self.guilds:
            for farm_channel in self.database.list_farm_channels(guild.id):
                channel = guild.get_channel(farm_channel.channel_id)
                if not isinstance(channel, discord.TextChannel):
                    continue
                message: discord.Message | None = None
                try:
                    if farm_channel.panel_message_id is not None:
                        message = await channel.fetch_message(farm_channel.panel_message_id)
                    else:
                        async for candidate in channel.history(limit=50, oldest_first=True):
                            if candidate.author == self.user and any(
                                embed.title == "Controle de coleta" for embed in candidate.embeds
                            ):
                                message = candidate
                                self.database.save_farm_channel(
                                    guild.id,
                                    farm_channel.member_id,
                                    channel.id,
                                    candidate.id,
                                )
                                break
                    if message is not None:
                        await message.edit(
                            embed=build_farm_embed(self.database, guild.id, farm_channel.member_id),
                            view=FarmPanel(self.database),
                        )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    LOGGER.warning(
                        "Could not restore farm panel",
                        extra={"guild_id": guild.id},
                    )
        self._farm_panels_restored = True

    async def _sync_commands_to_guilds(self) -> None:
        """Copy global commands to connected guilds for immediate development updates."""
        for guild in self.guilds:
            guild_ref = discord.Object(id=guild.id)
            self.tree.copy_global_to(guild=guild_ref)
            synced_commands = await self.tree.sync(guild=guild_ref)
            LOGGER.info(
                "Guild commands synchronized",
                extra={
                    "command_count": len(synced_commands),
                    "guild_id": guild.id,
                },
            )
        self._guild_commands_synced = True

    async def on_disconnect(self) -> None:
        LOGGER.warning("Disconnected from Discord gateway; reconnection will be attempted")

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """Stop counting a member in goals when their FARME channel is deleted."""
        removed = self.database.delete_farm_channel(channel.id)
        if removed is None:
            return
        LOGGER.info(
            "Farm channel link removed",
            extra={"guild_id": channel.guild.id},
        )
        await refresh_guild_panels(channel.guild, self.database)

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return
        LOGGER.debug(
            "Message received",
            extra={
                "has_content": bool(message.content),
                "guild_id": message.guild.id if message.guild else None,
            },
        )
        if message.content.startswith("!oi"):
            await message.channel.send("Olá! 👋")


def create_bot(settings: Settings) -> DiscordBot:
    """Build a client from validated settings, without connecting it."""
    intents = discord.Intents.none()
    intents.guilds = "guilds" in settings.intents
    intents.message_content = "message_content" in settings.intents
    return DiscordBot(intents=intents, database=Database(settings.database_path))


def run() -> None:
    """Load configuration and start the Discord client."""
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    try:
        create_bot(settings).run(settings.discord_token, log_handler=None)
    except discord.LoginFailure as exc:
        raise RuntimeError(
            "O Discord recusou o token. Confira DISCORD_TOKEN no arquivo .env"
        ) from exc
    except discord.GatewayNotFound as exc:
        raise RuntimeError("Não foi possível acessar o gateway do Discord") from exc
