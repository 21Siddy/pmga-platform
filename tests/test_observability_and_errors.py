"""Tests for structured logging, secret/PII log scrubbing, correlation IDs, and exception handling."""

from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient

from app.errors import DecisionModelError, PMGAError
from app.logging_config import (
    CorrelationAndRedactingFilter,
    RedactingJsonFormatter,
    RedactingTextFormatter,
    log_context,
    scrub_secrets_and_pii,
)
from app.main import app

client = TestClient(app)


def test_secret_and_pii_scrubbing_across_templates_args_and_json_logs():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(CorrelationAndRedactingFilter())
    handler.setFormatter(RedactingJsonFormatter())

    test_logger = logging.getLogger("pmga.test.security")
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False
    test_logger.addHandler(handler)

    secret_openai = "sk-proj-supersecretkey987654321"
    secret_gemini = "AIzaSyD1234567890SecretGeminiKey"
    email = "citizen.name@bayreuth.de"
    iban = "DE89 3704 0044 0532 0130 00"

    try:
        with log_context(request_id="req-abc-123", project_id="proj-99", turn_id="turn-7"):
            test_logger.info(
                "Called provider with key=%s email=%s iban=%s Bearer %s",
                secret_openai,
                email,
                iban,
                secret_gemini,
            )
            try:
                raise RuntimeError(f"Auth failed with password=MySecretPassword! and {secret_gemini}")
            except RuntimeError:
                test_logger.exception("Failure handling request")
    finally:
        test_logger.removeHandler(handler)

    lines = [json.loads(line) for line in stream.getvalue().strip().splitlines()]
    assert len(lines) == 2

    raw_output = stream.getvalue()
    for secret in (secret_openai, secret_gemini, email, iban, "MySecretPassword!"):
        assert secret not in raw_output

    assert lines[0]["request_id"] == "req-abc-123"
    assert lines[0]["project_id"] == "proj-99"
    assert lines[0]["turn_id"] == "turn-7"
    assert "[EMAIL_REDACTED]" in lines[0]["message"]
    assert "[IBAN_REDACTED]" in lines[0]["message"]
    assert "[SECRET_REDACTED]" in lines[1]["exception"]


def test_text_formatter_also_scrubs_secrets():
    fmt = RedactingTextFormatter("%(levelname)s %(message)s")
    assert "[API_KEY_REDACTED]" in scrub_secrets_and_pii("token sk-test1234567890abcdef")
    rec = logging.LogRecord("x", logging.WARNING, "", 1, "bad key AIzaSyAbCdEfGhIjKlMnOpQr", None, None)
    assert "AIzaSyAbCdEfGhIjKlMnOpQr" not in fmt.format(rec)


def test_http_request_id_header_and_structured_error_payloads():
    # Custom X-Request-ID is echoed back cleanly
    res = client.get("/api/v1/health", headers={"X-Request-ID": "trace-42"})
    assert res.status_code == 200
    assert res.headers["X-Request-ID"] == "trace-42"

    # Malicious header characters get sanitized
    res_san = client.get("/api/v1/health", headers={"X-Request-ID": "bad\nheader/../../"})
    assert "\n" not in res_san.headers["X-Request-ID"]

    # Domain error JSON structure
    res_404 = client.get("/api/v1/projects/does-not-exist/workspace", headers={"X-Request-ID": "err-trace-1"})
    assert res_404.status_code == 404
    body = res_404.json()
    assert body["error_code"] == "PMGA_NOT_FOUND"
    assert body["request_id"] == "err-trace-1"
    assert body["path"] == "/api/v1/projects/does-not-exist/workspace"

    # Verify PMGAError payload helper directly
    err = DecisionModelError("Bad table", details={"model": "01_entry_classifier"})
    p = err.to_payload(request_id="r1", path="/p")
    assert p["error_code"] == "PMGA_DECISION_MODEL_ERROR"
    assert p["details"]["model"] == "01_entry_classifier"
