"""Owner-observed FAA envelope ingestion, separate from semantic interpretation.

Observed structure is not evidence of lifecycle, runway association, pagination,
or completeness. Legacy FeatureCollection support is for regression fixtures only.
"""
import copy
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlsplit

API_URL = "https://api-nms.aim.faa.gov/nmsapi/v1/notams"
SOURCE_URL = "https://notams.aim.faa.gov/notamSearch/"


class FeedError(Exception):
    """Only fixed codes, never upstream exception text, are safe to publish."""

    def __init__(self, code, *, http_status=None, network_kind=None, stage=None):
        super().__init__(code)
        # Metadata is separate from the public feed's fixed error code. The
        # inspector renders only explicitly allowlisted diagnostic categories.
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.network_kind = network_kind
        self.stage = stage


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


class NmsAdapter:
    def __init__(self, contract):
        self.contract = contract
        self.mode = contract.get("mode", "legacy-synthetic")
        self.verified = self.mode == "legacy-synthetic" and contract.get("verified") is True and bool(contract.get("verification_reference"))

    def page(self, payload, airport, retrieved, sensitive=()):
        if self.mode == "observed-nms":
            return self.observed_page(payload, airport, retrieved, sensitive)
        if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
            raise FeedError("SCHEMA_MISMATCH")
        features = at_path(payload, self.contract["features_path"])
        if not isinstance(features, list):
            raise FeedError("SCHEMA_MISMATCH")
        records = []
        for feature in features:
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise FeedError("SCHEMA_MISMATCH")
            f = {key: at_path(feature, path) for key, path in self.contract["fields"].items()}
            if set(f) != {"id", "airport", "text", "start", "end", "schedule", "status"}:
                raise FeedError("CONTRACT_INVALID")
            if f["airport"] != airport:
                raise FeedError("AIRPORT_MISMATCH")
            if any(not isinstance(f[k], str) or not f[k].strip() for k in ("id", "text", "start", "end", "status")):
                raise FeedError("SCHEMA_MISMATCH")
            if len(f["text"]) > 100000 or len(f["id"]) > 200:
                raise FeedError("SCHEMA_MISMATCH")
            if f["schedule"] is not None and not isinstance(f["schedule"], str):
                raise FeedError("SCHEMA_MISMATCH")
            # An unexpected status may represent cancellation/replacement semantics.
            # Do not silently treat it as an active notice.
            if f["status"] not in ("ACTIVE", "CANCELLED", "REPLACED"):
                raise FeedError("UNKNOWN_LIFECYCLE")
            start = utc(f["start"])
            end_kind = "permanent" if f["end"] == "PERM" else "estimated" if f["end"].endswith("EST") else "fixed"
            end = None if end_kind == "permanent" else utc(f["end"].removesuffix("EST").strip())
            if end and datetime.fromisoformat(end) <= datetime.fromisoformat(start):
                raise FeedError("INVALID_VALIDITY")
            record = {
                "id": f["id"], "airport": airport, "text": f["text"],
                "effective_start_utc": start, "effective_end_utc": end,
                "original_start": f["start"], "original_end": f["end"],
                "end_kind": end_kind, "schedule": f["schedule"], "lifecycle": f["status"],
                "source": "FAA NMS", "source_url": SOURCE_URL,
                "source_reference": f["id"], "retrieved_utc": retrieved,
            }
            # Preserve full text or reject the record; never publish a redacted NOTAM
            # as though it were authoritative full text. Block reflected credentials.
            public_strings = [v for v in record.values() if isinstance(v, str)]
            if any(secret and any(secret in value for value in public_strings) for secret in sensitive):
                raise FeedError("SENSITIVE_RESPONSE")
            if any(re.search(r"(?i)\b(?:bearer\s+\S+|client_secret\s*[:=]|access_token\s*[:=])", v) for v in public_strings):
                raise FeedError("SENSITIVE_RESPONSE")
            records.append(record)
        p = self.contract["pagination"]
        if p["mode"] != "next-total":
            raise FeedError("PAGINATION_UNVERIFIED")
        total, next_page = at_path(payload, p["total_path"]), at_path(payload, p["next_path"])
        if type(total) is not int or total < 0 or (next_page is not None and (not isinstance(next_page, str) or not next_page)):
            raise FeedError("INVALID_PAGINATION")
        if next_page is not None:
            next_page = urljoin(API_URL, next_page)
            parsed, base = urlsplit(next_page), urlsplit(API_URL)
            query = parse_qs(parsed.query)
            # Never forward a bearer token to arbitrary links or a changed airport.
            if (parsed.scheme, parsed.netloc, parsed.path) != (base.scheme, base.netloc, base.path) or parsed.fragment or query.get("location") != [airport] or query.get("feature") != ["RWY"]:
                raise FeedError("UNSAFE_PAGINATION")
            if any(re.search(r"(?i)token|secret|authorization|client", k) for k in query):
                raise FeedError("UNSAFE_PAGINATION")
        return records, next_page, total

    def observed_page(self, payload, airport, retrieved, sensitive=()):
        if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
            raise FeedError("SCHEMA_MISMATCH")
        features = at_path(payload, "data.geojson")
        if not isinstance(features, list):
            raise FeedError("SCHEMA_MISMATCH")
        assert_public_source(payload, sensitive)
        metadata = copy.deepcopy({k:v for k,v in payload.items() if k != "data"})
        metadata["data"] = copy.deepcopy({k:v for k,v in payload["data"].items() if k != "geojson"})
        records = []
        for index, feature in enumerate(features):
            issues = []
            try:
                core = at_path(feature, "properties.coreNOTAMData")
                notam = at_path(core, "notam")
                if not isinstance(notam, dict):
                    raise FeedError("SCHEMA_MISMATCH")
            except FeedError:
                notam = {}
                issues.append("CORE_NOTAM_STRUCTURE_UNRECOGNIZED")
            source_id = notam.get("id")
            if not isinstance(source_id, str) or not source_id:
                issues.append("SOURCE_ID_UNRECOGNIZED")
            text = notam.get("text")
            if not isinstance(text, str) or not text:
                issues.append("FULL_TEXT_UNAVAILABLE")
            # UTC conversion is syntactic only; PERM/EST and lifecycle meaning
            # are not inferred from the observed structure.
            def normalize_time(value):
                try:
                    return utc(value)
                except FeedError:
                    return None
            records.append({
                "id": source_id if isinstance(source_id, str) and source_id else f"LOCAL-UNVERIFIED-RECORD-{index}",
                "record_key": f"{airport}:{retrieved}:{index}",
                "airport": airport, "airport_association": "query-only-unverified",
                "text": text if isinstance(text, str) and text else "Full NOTAM text unavailable; inspect preserved source notice.",
                "effective_start_utc": normalize_time(notam.get("effectiveStart")),
                "effective_end_utc": normalize_time(notam.get("effectiveEnd")),
                "original_start": copy.deepcopy(notam.get("effectiveStart")),
                "original_end": copy.deepcopy(notam.get("effectiveEnd")),
                "issued_utc": normalize_time(notam.get("issued")),
                "last_updated_utc": normalize_time(notam.get("lastUpdated")),
                "end_kind": "unverified", "schedule": None, "lifecycle": "UNKNOWN",
                "runway_ends": [], "classification": "unresolved",
                "reason": "FAA_SEMANTICS_UNVERIFIED", "validation_issues": issues,
                "source": "FAA NMS production", "source_url": API_URL,
                "source_reference": source_id if isinstance(source_id, str) else None,
                "retrieved_utc": retrieved, "original_feature": copy.deepcopy(feature),
                "source_provenance": {
                    "query_airport": airport, "query_feature": "RWY",
                    "request_url": API_URL+"?location="+airport+"&feature=RWY",
                    "response_index": index, "response_record_count": len(features),
                    "response_metadata": metadata, "adapter_profile": self.contract["profile"],
                    "completeness": "UNKNOWN", "pagination": "UNKNOWN",
                },
            })
        # None is UNKNOWN, never proof that the full result set was returned.
        return records, None, None


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
