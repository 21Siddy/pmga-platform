"""Structured contextual logging with correlation IDs and secret/PII scrubbing.

Features:
- Thread/async-safe request_id, project_id, and turn_id correlation via contextvars.
- Automatic redaction of API keys (Gemini AIza..., OpenAI/Anthropic sk-..., Bearer tokens)
  and sensitive citizen PII (emails, IBANs, German tax IDs, password assignments) across
  both log message templates and positional/dict log arguments + exception traces.
- Supports structured JSON logs (`LOG_FORMAT=json`) or clean human-readable key=val logs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

request_id_var: ContextVar[str] = ContextVar("pmga_request_id", default="-")
project_id_var: ContextVar[str] = ContextVar("pmga_project_id", default="-")
turn_id_var: ContextVar[str] = ContextVar("pmga_turn_id", default="-")

_SECRET_AND_PII_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE), "Bearer [SECRET_REDACTED]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{12,}\b"), "[API_KEY_REDACTED]"),
    (re.compile(r"\bsk-(?:proj-|ant-)?[0-9A-Za-z_\-]{12,}\b"), "[API_KEY_REDACTED]"),
    (
        re.compile(r"(?i)(api[_-]?key|x-llm-api-key|x-gemini-api-key|authorization)\s*[:=]\s*(['\"]?)[^\s,;'\"]+\2"),
        r"\1=[SECRET_REDACTED]",
    ),
    (re.compile(r"(?i)(password|passwort|pwd|kennwort)\s*[:=]\s*([^\s,;]+)"), r"\1=[SECRET_REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b"), "[EMAIL_REDACTED]"),
    (re.compile(r"\b[A-Z]{2}[0-9]{2}(?:[ ]?[0-9]{4}){4}(?:\s?[0-9]{1,4})?\b"), "[IBAN_REDACTED]"),
    (re.compile(r"\b\d{2}/\d{3}/\d{5}\b"), "[TAX_ID_REDACTED]"),
]


def scrub_secrets_and_pii(value: Any) -> Any:
    """Recursively scrub secrets and citizen PII from strings, dicts, lists, and tuples."""
    if isinstance(value, str):
        out = value
        for pattern, replacement in _SECRET_AND_PII_PATTERNS:
            out = pattern.sub(replacement, out)
        return out
    if isinstance(value, dict):
        return {k: scrub_secrets_and_pii(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_secrets_and_pii(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_secrets_and_pii(item) for item in value)
    return value


class CorrelationAndRedactingFilter(logging.Filter):
    """Injects correlation IDs and scrubs secrets/PII from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", None) or request_id_var.get()
        record.project_id = getattr(record, "project_id", None) or project_id_var.get()
        record.turn_id = getattr(record, "turn_id", None) or turn_id_var.get()

        # Scrub message and arguments before formatting so neither template nor args leak secrets/PII.
        if isinstance(record.msg, str):
            record.msg = scrub_secrets_and_pii(record.msg)
        if record.args:
            record.args = scrub_secrets_and_pii(record.args)
        return True


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return scrub_secrets_and_pii(formatted)


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "project_id": getattr(record, "project_id", "-"),
            "turn_id": getattr(record, "turn_id", "-"),
            "message": scrub_secrets_and_pii(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = scrub_secrets_and_pii(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


@contextmanager
def log_context(
    *,
    request_id: Optional[str] = None,
    project_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Iterator[None]:
    """Temporarily bind correlation IDs to contextvars for structured logging."""
    tokens = []
    if request_id is not None:
        tokens.append((request_id_var, request_id_var.set(request_id)))
    if project_id is not None:
        tokens.append((project_id_var, project_id_var.set(project_id)))
    if turn_id is not None:
        tokens.append((turn_id_var, turn_id_var.set(turn_id)))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


def configure_logging(level: Optional[str] = None, log_format: Optional[str] = None) -> None:
    """Idempotently configure PMGA root and application loggers."""
    lvl_name = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    lvl = getattr(logging, lvl_name, logging.INFO)
    fmt_mode = (log_format or os.getenv("LOG_FORMAT") or "text").lower()

    root = logging.getLogger()
    root.setLevel(lvl)

    # Remove previously attached PMGA handlers so repeated calls remain idempotent.
    for existing in list(root.handlers):
        if getattr(existing, "_pmga_managed", False):
            root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler._pmga_managed = True  # type: ignore[attr-defined]
    handler.setLevel(lvl)
    handler.addFilter(CorrelationAndRedactingFilter())

    if fmt_mode == "json":
        handler.setFormatter(RedactingJsonFormatter())
    else:
        handler.setFormatter(
            RedactingTextFormatter(
                fmt="%(asctime)s %(levelname)s [req=%(request_id)s proj=%(project_id)s turn=%(turn_id)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )

    root.addHandler(handler)
    # Keep noisy third-party HTTP debug spam under control unless explicitly requested.
    for noisy in ("httpcore", "httpx", "fontTools", "weasyprint"):
        logging.getLogger(noisy).setLevel(max(lvl, logging.WARNING))
