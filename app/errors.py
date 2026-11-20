"""Typed domain exception hierarchy for PMGA Platform.

Every failure across ingestion, decision evaluation, BPMN compilation/parsing,
persistence, workflow orchestration, and deliverable rendering maps to a
structured exception with a deterministic machine-readable error_code, HTTP
status_code, and user-safe detail message.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class PMGAError(Exception):
    """Base domain exception for all PMGA platform errors."""

    error_code: str = "PMGA_INTERNAL_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        error_code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}

    def to_payload(self, *, request_id: str = "-", path: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "detail": self.message,
            "error_code": self.error_code,
            "error_type": self.__class__.__name__,
            "request_id": request_id,
            "path": path,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(PMGAError):
    error_code = "PMGA_VALIDATION_ERROR"
    status_code = 400


class NotFoundError(PMGAError):
    error_code = "PMGA_NOT_FOUND"
    status_code = 404


class UnsupportedMediaError(PMGAError):
    error_code = "PMGA_UNSUPPORTED_MEDIA"
    status_code = 415


class PayloadTooLargeError(PMGAError):
    error_code = "PMGA_PAYLOAD_TOO_LARGE"
    status_code = 413


class BPMNParseError(PMGAError):
    error_code = "PMGA_BPMN_PARSE_ERROR"
    status_code = 422


class DecisionModelError(PMGAError):
    error_code = "PMGA_DECISION_MODEL_ERROR"
    status_code = 500


class PersistenceError(PMGAError):
    error_code = "PMGA_PERSISTENCE_ERROR"
    status_code = 500


class WorkflowExecutionError(PMGAError):
    error_code = "PMGA_WORKFLOW_ERROR"
    status_code = 500


class DeliverableRenderError(PMGAError):
    error_code = "PMGA_DELIVERABLE_ERROR"
    status_code = 500


class LLMUnavailableError(PMGAError):
    error_code = "PMGA_LLM_UNAVAILABLE"
    status_code = 503
