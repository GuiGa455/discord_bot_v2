"""Environment-based application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

ALLOWED_INTENTS = frozenset({"guilds", "message_content"})
ALLOWED_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime settings."""

    discord_token: str
    intents: frozenset[str] = frozenset({"guilds", "message_content"})
    log_level: str = "INFO"
    database_path: str = "data/bot.db"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token or token == "seu_token_aqui":
            raise ValueError("DISCORD_TOKEN não configurado. Defina-o no ambiente ou .env")

        intents = frozenset(
            item.strip().lower()
            for item in os.getenv("DISCORD_INTENTS", "guilds,message_content").split(",")
            if item.strip()
        )
        unsupported = intents - ALLOWED_INTENTS
        if unsupported:
            raise ValueError(f"Discord intents não suportadas: {', '.join(sorted(unsupported))}")

        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in ALLOWED_LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL inválido: {log_level}")
        database_path = os.getenv("DATABASE_PATH", "data/bot.db").strip()
        if not database_path:
            raise ValueError("DATABASE_PATH não pode ser vazio")
        return cls(
            discord_token=token,
            intents=intents,
            log_level=log_level,
            database_path=database_path,
        )
