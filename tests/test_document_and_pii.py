import pytest
from app.services.document_service import DocumentService

def test_pii_detection_and_redaction():
    sample_text = (
        "Citizen Hans Mueller sent his birth certificate from hans.mueller@web.de. "
        "His contact phone is +49 89 1234567. He transferred administrative fees from IBAN DE89370400440532013000. "
        "The emergency server password was svc_admin=Secret123!"
    )

    report = DocumentService.scan_for_pii(sample_text)
    assert report["has_pii"] is True
    assert report["detected_count"] >= 3

    redacted = report["redacted_text"]
    assert "hans.mueller@web.de" not in redacted
    assert "[EMAIL_REDACTED]" in redacted
    assert "DE89370400440532013000" not in redacted
    assert "[IBAN_REDACTED]" in redacted
