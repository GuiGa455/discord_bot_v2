import json
import logging

from discord_bot_v2.logging_config import JsonFormatter, configure_logging


def test_json_formatter_produces_structured_event() -> None:
    record = logging.LogRecord("app", logging.INFO, "", 0, "ready", (), None)
    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app"
    assert payload["message"] == "ready"
    assert "timestamp" in payload


def test_configure_logging_sets_root_level() -> None:
    configure_logging("WARNING")

    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
