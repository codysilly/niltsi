"""Local-only review parsing and input checks. No acquisition or configuration.

Extracted from the uncommitted NOTAM prototype; these helpers do not validate
FAA semantics, authenticity, completeness, or successful sanitization.
"""


import json
import re
from datetime import datetime, timezone


class FeedError(Exception):
    """Fixed offline review validation code; never raw source diagnostics."""


def at_path(value, path):
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise FeedError("SCHEMA_MISMATCH")
        value = value[part]
    return value


def utc(value):
    if not isinstance(value, str) or not re.search(r"(?:Z|[+-]\d{2}:\d{2})$", value):
        raise FeedError("INVALID_VALIDITY")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise FeedError("INVALID_VALIDITY") from None


def assert_public_source(value, sensitive=()):
    """Preserve original source intact or reject it; never silently redact it."""
    raw = json.dumps(value, ensure_ascii=False)
    if any(secret and secret in raw for secret in sensitive) or re.search(r"(?i)\bbearer\s+\S+", raw):
        raise FeedError("SENSITIVE_RESPONSE")
    def walk(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if re.sub(r"[^a-z]", "", str(key).lower()) in {"authorization", "accesstoken", "refreshtoken", "clientsecret", "clientid", "password", "credential", "credentials"}:
                    raise FeedError("SENSITIVE_RESPONSE")
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
