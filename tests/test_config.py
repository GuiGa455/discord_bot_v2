import pytest

from discord_bot_v2.config import Settings


def test_settings_load_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("discord_bot_v2.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_INTENTS", "guilds")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings.from_env()

    assert settings.discord_token == "test-token"
    assert settings.intents == frozenset({"guilds"})
    assert settings.log_level == "DEBUG"
    assert settings.database_path == "data/bot.db"


def test_settings_require_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("discord_bot_v2.config.load_dotenv", lambda: None)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_INTENTS", "guilds")

    with pytest.raises(ValueError, match="DISCORD_TOKEN"):
        Settings.from_env()


def test_settings_reject_example_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("discord_bot_v2.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_TOKEN", "seu_token_aqui")

    with pytest.raises(ValueError, match="DISCORD_TOKEN"):
        Settings.from_env()


def test_settings_reject_unknown_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("discord_bot_v2.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_INTENTS", "guilds,unknown")

    with pytest.raises(ValueError, match="unknown"):
        Settings.from_env()


def test_settings_reject_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("discord_bot_v2.config.load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(ValueError, match="LOG_LEVEL"):
        Settings.from_env()
