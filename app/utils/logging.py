from __future__ import annotations

import logging
import sys

import structlog

# Never logged in clear, whoever passes them (SPEC §12). Transcripts and quotes are personal data.
_SENSITIVE_KEYS = {
    "password",
    "current_password",
    "new_password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "transcript",
    "trainee_quote",
}


def _redact_sensitive(
    logger: structlog.types.WrappedLogger, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***redacted***"
    return event_dict


def configure_logging(log_level: str) -> None:
    """JSON lines to stdout (journald on the server), with the request id bound per request."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_sensitive,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
