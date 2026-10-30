"""Host-owned observation validation. No freshness inference from tool prose."""

from __future__ import annotations

import json
from typing import Any


def receipt_freshness(registry: Any, receipt: dict[str, Any]) -> str:
    """Return CURRENT, CHANGED, UNAVAILABLE, or UNKNOWN for a complete receipt.

    Adapters must be registered executors with an explicit validation method.
    An unchanged historical receipt alone never authorizes external-data reuse.
    """
    if receipt["status"] != "SUCCEEDED":
        return "UNKNOWN"
    payload = json.loads(receipt["result_json"] or "null")
    observations = (
        payload.get("source_observations", []) if isinstance(payload, dict) else []
    )
    if not observations:
        return "UNKNOWN"
    executor = registry.get_executor(receipt["tool_name"])
    validate = getattr(executor, "validate_observation", None)
    if not callable(validate):
        return "UNKNOWN"
    if not isinstance(observations, list) or len(observations) > 8:
        return "UNAVAILABLE"
    states = [validate(item) for item in observations]
    if "CHANGED" in states:
        return "CHANGED"
    if "UNAVAILABLE" in states:
        return "UNAVAILABLE"
    if any(state != "CURRENT" for state in states):
        return "UNKNOWN"
    # A receipt truncated by journal persistence cannot support reuse either.
    text = payload.get("result_content", "")
    if any(item.get("truncated", True) for item in observations) or len(text) >= 10000:
        return "UNKNOWN"
    return "CURRENT"
