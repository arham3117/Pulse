"""structlog JSON logging to stdout.

Every line carries ``service``, ``level``, ``event`` and a UTC ``timestamp``. Request- or
monitor-scoped fields (``request_id``, ``monitor_id``) are added with structlog's
contextvars so they follow the async task that set them.

URLs can carry tokens in query strings (``?api_key=...``), so a processor strips query
strings and userinfo from every string value before the line is rendered.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import structlog

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def redact_url(url: str) -> str:
    """Drop query string, fragment and userinfo from a URL: ``https://u:p@h/x?t=1`` → ``https://h/x?<redacted>``."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    query = "<redacted>" if parts.query else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def _redact_value(value: Any) -> Any:
    if isinstance(value, str) and ("http://" in value.lower() or "https://" in value.lower()):
        return _URL_RE.sub(lambda m: redact_url(m.group(0)), value)
    return value


def redact_urls_processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {k: _redact_value(v) for k, v in event_dict.items()}


def configure_logging(service: str, level: str = "INFO") -> None:
    """Configure structlog (and stdlib logging, for uvicorn/sqlalchemy) to emit JSON lines."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_urls_processor,
    ]
    structlog.configure(
        processors=[*shared, structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=service)

    # Route stdlib loggers (uvicorn, botocore, sqlalchemy) through the same JSON renderer.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=[
            *shared,
            structlog.stdlib.add_logger_name,
            lambda _l, _m, ed: {**ed, "service": service},
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in ("botocore", "aiobotocore", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLevelName(level)))
    # uvicorn's own access log is replaced by our middleware's JSON access log.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(**initial: Any) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(**initial)
