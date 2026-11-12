#!/usr/bin/env python3
"""Print response STRUCTURE ONLY. No response values, text, IDs, or credentials.

Run in a credential-scoped Actions step or the terminal holding process variables.
This does not verify semantics,
publish a feed, or alter nms_contract.json. It helps locate an unexpected envelope.
An explicit single-airport bulk request uses the shared durable pull budget;
only its non-secret ledger is written. No response artifact is written.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

from nms_adapter import API_URL, FeedError, NmsAdapter
from nms_pull_budget import PullBudget
from pathlib import Path
from update_notams import Client


def structure(value, sensitive=(), depth=0):
    if depth > 12:
        return "depth-limit"
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
                continue
            if re.search(r"(?i)token|secret|authorization|password|credential", key) or any(s and s in key for s in sensitive):
                continue
            result[key] = structure(child, sensitive, depth+1)
        return result
    if isinstance(value, list):
        return {"kind": "array", "count": len(value), "first_item_shape": structure(value[0], sensitive, depth+1) if value else None}
    return "null" if value is None else "boolean" if isinstance(value,bool) else "number" if isinstance(value,(int,float)) else "string" if isinstance(value,str) else "unsupported"


def diagnostic(exc, stage):
    """Never interpolate exception messages, URLs, response bodies, or headers."""
    stage = exc.stage if isinstance(exc, FeedError) and exc.stage in ("authentication", "api") else stage
    stage = stage if stage in ("authentication", "api", "structure") else "inspection"
    if isinstance(exc, FeedError):
        code = str(exc)
        if code == "HTTP_FAILURE":
            status = exc.http_status
            label = f"HTTP {status}" if status is not None else "HTTP status unavailable"
            if stage == "authentication" and status in (401, 403):
                return f"authentication failure ({label}): token request rejected; check production access and authentication configuration. Credential validity is not established by this diagnostic."
            if stage == "api" and status in (401, 403):
                return f"API authorization failure ({label}): check token permissions, expiry, and production API access."
            if status is not None and 300 <= status < 400:
                return f"{stage} {label}: redirect refused to protect authorization credentials."
            return f"{stage} HTTP failure ({label}); response body omitted."
        if code == "NETWORK_FAILURE":
            kinds = {"TLS_CERTIFICATE":"TLS certificate verification failure; check the Python CA trust configuration. Do not disable certificate validation.",
                     "TLS":"TLS handshake failure.", "DNS":"DNS resolution failure.",
                     "TIMEOUT":"network timeout; request failed.", "CONNECTION":"network/connection failure."}
            return f"{stage}: " + kinds.get(exc.network_kind, "network/TLS failure.")
        messages = {
            "MISSING_CREDENTIALS":"required process environment variables are missing or blank; no request was made.",
            "INVALID_JSON":"JSON parsing failure; response body omitted.",
            "AUTH_RESPONSE_INVALID":"unexpected token response structure: expected a nonempty access_token string without whitespace; values omitted.",
            "RESPONSE_TOO_LARGE":"response exceeded the 8 MiB safety limit.",
            "SENSITIVE_RESPONSE":"sanitization blocked the structural report; no response values were printed.",
            "SCHEMA_MISMATCH":"unexpected response structure; expected status:string and data.geojson:array.",
            "PULL_MODE_REQUIRED":"no airport selected; no live request allowed. Select one --bulk-airport only when a bulk request is authorized.",
            "PULL_BUDGET_REQUIRED":"a durable FAA pull-budget ledger is required before any data request.",
            "PULL_HISTORY_UNVERIFIED":"previous account traffic is unknown; a 24-hour bulk / 3-minute delta quarantine has been persisted. No request made.",
            "BULK_PULL_COOLDOWN":"bulk pull blocked by the global-per-ledger 24-hour cooldown. Do not reset the ledger.",
            "DELTA_PULL_COOLDOWN":"delta pull blocked by the global-per-ledger 3-minute cooldown.",
            "DELTA_PROTOCOL_UNVERIFIED":"delta query and reconciliation semantics are unverified; no delta request permitted.",
            "PULL_STATE_INVALID":"pull-budget history is corrupt or unreadable; request blocked.",
            "PULL_STATE_LOCKED":"another request holds the budget lock; request blocked.",
            "PULL_CLOCK_INVALID":"clock/history inconsistency; request blocked.",
        }
        if code in messages:
            return f"{stage}: {messages[code]}"
    return f"{stage}: unexpected local processing failure; exception details omitted. Check the Python runtime/imports and run offline tests."


def environment_check():
    missing = []
    for name in ("FAA_NMS_CLIENT_ID", "FAA_NMS_CLIENT_SECRET"):
        present = bool(os.environ.get(name, "").strip())
        print(name + (": present" if present else ": MISSING or blank"))
        if not present:
            missing.append(name)
    return missing


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only FAA NMS inspection. Prints structure and safe diagnostics, never response values. Does not publish or verify coverage.")
    parser.add_argument("--check-environment", action="store_true", help="Check process-variable presence and runtime imports only; make no network requests")
    parser.add_argument('--bulk-airport', choices=('KORD','KLAX','KJFK'), help='Explicit read-only bulk request for ONE airport; requires the durable 24h pull budget')
    args = parser.parse_args(argv)
    missing = environment_check()
    if args.check_environment:
        print("Python runtime: " + str(sys.version_info.major) + "." + str(sys.version_info.minor) + "; required standard-library imports succeeded.")
        return 1 if missing else 0
    if missing:
        print("authentication: MISSING_CREDENTIALS; no requests made. Run in the same terminal process that holds both variables (or a child launched from it).", file=sys.stderr)
        return 1
    if not args.bulk_airport:
        print(diagnostic(FeedError('PULL_MODE_REQUIRED'),'api'),file=sys.stderr)
        return 1
    try:
        budget = PullBudget()
        budget.check('bulk')
        client = Client(os.environ.get("FAA_NMS_CLIENT_ID"), os.environ.get("FAA_NMS_CLIENT_SECRET"),budget=budget)
    except Exception as exc:
        print("NMS inspection: " + diagnostic(exc, "authentication"), file=sys.stderr)
        return 1
    print("Authentication: token response accepted; API access, schema, and coverage remain unverified.")
    failed = False
    for airport in (args.bulk_airport,):
        stage = "api"
        try:
            payload = client.get(API_URL+"?"+urllib.parse.urlencode({"location":airport,"feature":"RWY"}))
            stage = "structure"
            report = json.dumps(structure(payload, client.sensitive), sort_keys=True)
            if any(s and s in report for s in client.sensitive):
                raise FeedError("SENSITIVE_RESPONSE")
            print(airport+" response structure (values omitted): "+report)
            contract = json.loads((Path(__file__).with_name('nms_contract.json')).read_text(encoding='utf-8'))
            records, _, _ = NmsAdapter(contract).page(payload,airport,'2000-01-01T00:00:00Z',client.sensitive)
            print(airport+f": observed envelope parsed; {len(records)} records retained in memory only. Coverage remains UNKNOWN/unverified. Fields, pagination, validity, lifecycle, and runway semantics still require review. First response only; pagination is not followed.")
        except Exception as exc:
            failed = True
            print(airport+": " + diagnostic(exc, stage), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
