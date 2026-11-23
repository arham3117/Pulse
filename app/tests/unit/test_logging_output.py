import json
import logging

import pytest
import structlog

from pulse.core.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _restore_logging():
    """configure_logging() binds stdout (here: pytest's capture buffer) and caches loggers.
    Undo that afterwards so later tests don't write to a closed file."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    root.handlers, root.level = handlers, level
    logging.getLogger("uvicorn.access").disabled = False


def test_json_lines_have_required_fields_and_redact(capsys) -> None:
    configure_logging("worker", "INFO")
    structlog.contextvars.bind_contextvars(monitor_id="m-1")
    get_logger().info("check_completed", url="https://example.com/x?token=s3cret")
    get_logger().debug("hidden")  # below INFO
    logging.getLogger("uvicorn.error").warning("stdlib line https://h/?k=v")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert len(lines) == 2
    first, second = lines
    assert first["service"] == "worker" and first["level"] == "info"
    assert first["event"] == "check_completed" and first["monitor_id"] == "m-1"
    assert first["url"] == "https://example.com/x?<redacted>"
    assert "timestamp" in first
    assert second["service"] == "worker" and second["level"] == "warning"
    assert "k=v" not in second["event"]
