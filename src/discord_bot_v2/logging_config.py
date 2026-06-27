"""Centralized structured logging configuration."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Small dependency-free JSON formatter for structured logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        discord_user = getattr(record, "discord_user", None)
        if discord_user is not None:
            payload["discord_user"] = str(discord_user)
        guild_count = getattr(record, "guild_count", None)
        if guild_count is not None:
            payload["guild_count"] = guild_count
        command_count = getattr(record, "command_count", None)
        if command_count is not None:
            payload["command_count"] = command_count
        has_content = getattr(record, "has_content", None)
        if has_content is not None:
            payload["has_content"] = has_content
        guild_id = getattr(record, "guild_id", None)
        if guild_id is not None:
            payload["guild_id"] = guild_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Configure process-wide logs for machine ingestion."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
