"""Safe, structured diagnostics for boundary failures.

The helpers in this module deliberately avoid logging raw exception messages or
tracebacks.  Provider/transport exceptions can contain credentials, request
headers, prompts, or response bodies; callers instead get stable error types,
codes, and explicitly supplied non-secret identifiers.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict


_SENSITIVE_KEY_MARKERS = ("api_key", "authorization", "credential", "password", "secret", "token")
_MAX_FIELD_CHARS = 256


def safe_exception_metadata(exc: BaseException) -> Dict[str, Any]:
    """Return a non-secret, machine-readable description of an exception."""
    metadata: Dict[str, Any] = {"error_type": type(exc).__name__}
    if exc.__cause__ is not None:
        metadata["cause_type"] = type(exc.__cause__).__name__
    projector = getattr(exc, "to_safe_projection", None)
    if not callable(projector):
        return metadata

    try:
        projection = projector()
    except Exception:
        return metadata
    if not isinstance(projection, dict):
        return metadata

    for key in (
        "category",
        "code",
        "provider",
        "operation",
        "status_code",
        "retryable",
        "provider_error_type",
        "provider_error_code",
        "request_id",
        "retry_after",
    ):
        value = projection.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            metadata[key] = value
    return metadata


def log_boundary_failure(
    logger: logging.Logger,
    event: str,
    exc: BaseException,
    **context: Any,
) -> Dict[str, Any]:
    """Log a bounded JSON diagnostic without serializing raw exception text.

    The returned payload is useful for attaching the exact same safe metadata to
    a public error DTO.  Context must consist of identifiers or other explicitly
    non-secret scalar values; suspicious keys are dropped defensively.
    """
    payload: Dict[str, Any] = {"event": event, **safe_exception_metadata(exc)}
    for key, value in context.items():
        lowered = key.lower()
        if any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            text = str(value)
            payload[key] = text[:_MAX_FIELD_CHARS] if len(text) > _MAX_FIELD_CHARS else value
    logger.error("boundary_failure=%s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload
