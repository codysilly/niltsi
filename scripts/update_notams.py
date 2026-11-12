#!/usr/bin/env python3
"""Publish sanitized NOTAM snapshots; never log upstream exceptions or credentials."""
import argparse
import base64
import copy
import json
import os
import socket
import ssl
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from nms_adapter import API_URL, FeedError, NmsAdapter
from notam_rules import classify
from nms_pull_budget import PullBudget

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/notams.json"
TOKEN_URL = "https://api-nms.aim.faa.gov/v1/auth/token"
AIRPORTS = ("KORD", "KLAX", "KJFK")
ERROR_CODES = {"SCHEMA_MISMATCH", "INVALID_VALIDITY", "CONTRACT_INVALID", "AIRPORT_MISMATCH", "UNKNOWN_LIFECYCLE", "SENSITIVE_RESPONSE", "PAGINATION_UNVERIFIED", "INVALID_PAGINATION", "UNSAFE_PAGINATION", "HTTP_FAILURE", "NETWORK_FAILURE", "INVALID_JSON", "RESPONSE_TOO_LARGE", "AUTH_RESPONSE_INVALID", "MISSING_CREDENTIALS", "INCOMPLETE_PAGINATION", "CONTRACT_UNVERIFIED", "INTERNAL_VALIDATION_FAILURE"}


ERROR_CODES.update({'PULL_MODE_REQUIRED','PULL_NOT_REQUESTED','PULL_BUDGET_REQUIRED','PULL_HISTORY_UNVERIFIED',
                    'BULK_PULL_COOLDOWN','DELTA_PULL_COOLDOWN','PULL_STATE_INVALID','PULL_STATE_LOCKED',
                    'PULL_CLOCK_INVALID','DELTA_PROTOCOL_UNVERIFIED'})


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(request, opener=None, sleep=time.sleep, attempts=3):
    opener = opener or urllib.request.build_opener(NoRedirect())
    for attempt in range(attempts):
        try:
            with opener.open(request, timeout=25) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise FeedError("RESPONSE_TOO_LARGE")
                try:
                    return json.loads(raw)
                except (ValueError, UnicodeError):
                    raise FeedError("INVALID_JSON") from None
        except urllib.error.HTTPError as exc:
            retry = exc.code == 429 or 500 <= exc.code < 600
            delay = min(30, 2 ** (attempt + 1))
            if exc.code == 429 and exc.headers:
                retry_after = exc.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = min(30, int(retry_after))
            exc.close()
            if not retry or attempt == attempts-1:
                raise FeedError("HTTP_FAILURE", http_status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            delay = 2 ** (attempt + 1)
            if attempt == attempts-1:
                reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                kind = "TLS_CERTIFICATE" if isinstance(reason, ssl.SSLCertVerificationError) else "TLS" if isinstance(reason, ssl.SSLError) else "TIMEOUT" if isinstance(reason, TimeoutError) else "DNS" if isinstance(reason, socket.gaierror) else "CONNECTION"
                raise FeedError("NETWORK_FAILURE", network_kind=kind) from None
        sleep(delay)


class Client:
    def __init__(self, client_id, secret, fetch=request_json, budget=None, pull_kind='bulk'):
        if not client_id or not secret or not client_id.strip() or not secret.strip():
            raise FeedError("MISSING_CREDENTIALS", stage="authentication")
        self.fetch = fetch
        self.budget, self.pull_kind = budget, pull_kind
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        try:
            payload = fetch(urllib.request.Request(TOKEN_URL, data=b"grant_type=client_credentials", headers={
                "Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
            }, method="POST"))
        except FeedError as exc:
            exc.stage = "authentication"
            raise
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str) or not payload["access_token"] or any(c.isspace() for c in payload["access_token"]):
            raise FeedError("AUTH_RESPONSE_INVALID", stage="authentication")
        self.token = payload["access_token"]
        self.sensitive = (client_id, secret, basic, self.token)

    def get(self, url):
        try:
            if self.pull_kind != 'bulk':
                raise FeedError('DELTA_PROTOCOL_UNVERIFIED')
            if self.budget is None:
                raise FeedError('PULL_BUDGET_REQUIRED')
            self.budget.reserve('bulk')
            return self.fetch(urllib.request.Request(url, headers={
                "Authorization": "Bearer " + self.token, "nmsResponseFormat": "GEOJSON", "Accept": "application/json",
            }), attempts=1)
        except FeedError as exc:
            exc.stage = "api"
            raise


def retrieve(client, adapter, airport, inventory, timestamp):
    expected = {"KORD":16, "KLAX":8, "KJFK":8}[airport]
    if not isinstance(inventory, list) or len(inventory) != expected:
        raise FeedError("INTERNAL_VALIDATION_FAILURE")
    ids = {end["id"] for end in inventory}
    if len(ids) != expected or any(len(end["pair"].split("/")) != 2 or end["id"] not in end["pair"].split("/") or not set(end["pair"].split("/")) <= ids for end in inventory):
        raise FeedError("INTERNAL_VALIDATION_FAILURE")
    url = API_URL + "?" + urllib.parse.urlencode({"location": airport, "feature": "RWY"})
    seen, records, total = set(), {}, None
    for _ in range(100):
        if url in seen:
            raise FeedError("INCOMPLETE_PAGINATION")
        seen.add(url)
        page, next_url, count = adapter.page(client.get(url), airport, timestamp, client.sensitive)
        if adapter.mode == 'observed-nms':
            # Preserve every returned item, including duplicates and malformed
            # notices. Neither pagination nor semantic classification is enabled.
            return page
        if total is not None and total != count:
            raise FeedError("INCOMPLETE_PAGINATION")
        total = count
        for record in page:
            if record["id"] in records:
                raise FeedError("INCOMPLETE_PAGINATION")
            records[record["id"]] = classify(record, inventory)
        if len(records) > total:
            raise FeedError("INCOMPLETE_PAGINATION")
        if next_url is None:
            if len(records) != total:
                raise FeedError("INCOMPLETE_PAGINATION")
            return list(records.values())
        url = next_url
    raise FeedError("INCOMPLETE_PAGINATION")


def initial_airport():
    return {"state": "UNKNOWN", "verified": False, "complete": False, "attempted_utc": None,
            "last_success_utc": None, "error": "CONTRACT_UNVERIFIED", "notams": []}


def update(previous, client, adapter, inventories, timestamp, client_error=None, selected_airport=None):
    result = {"schema_version": 1, "generated_utc": timestamp, "source": "FAA NMS production",
              "source_url": API_URL, "contract_profile": adapter.contract["profile"], "airports": {}}
    for airport in AIRPORTS:
        prior = previous.get("airports", {}).get(airport, initial_airport())
        entry = copy.deepcopy(prior)
        entry.update(state="UNKNOWN", verified=False, complete=False, attempted_utc=timestamp)
        try:
            if client_error:
                raise FeedError(client_error)
            if selected_airport is not None and airport != selected_airport:
                raise FeedError('PULL_NOT_REQUESTED')
            notices = retrieve(client, adapter, airport, inventories[airport[1:]], timestamp)
            # Parsed unverified data may be inspected, but never accepted as coverage.
            if not adapter.verified:
                entry["error"] = "CONTRACT_UNVERIFIED"
                if not prior.get("last_success_utc"):
                    entry["notams"] = notices
                else:
                    entry['observed_notams'] = notices
            else:
                entry.update(state="VERIFIED", verified=True, complete=True, error=None,
                             last_success_utc=timestamp, notams=notices)
        except FeedError as exc:
            entry["error"] = str(exc) if str(exc) in ERROR_CODES else "INTERNAL_VALIDATION_FAILURE"
        except Exception:
            entry["error"] = "INTERNAL_VALIDATION_FAILURE"
        result["airports"][airport] = entry
    return result


def atomic_publish(payload, destination=DEST, sensitive=()):
    raw = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if any(value and value in raw for value in sensitive):
        raise FeedError("SENSITIVE_RESPONSE")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    try:
        temporary.write_text(raw, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Publish UNKNOWN coverage; live bulk retrieval requires an explicit single airport and a durable pull budget. Delta protocol is unverified.')
    parser.add_argument('--bulk-airport', choices=AIRPORTS, help='Explicit bulk request for ONE airport; global 24h budget applies; no automatic retry')
    args = parser.parse_args(argv)
    timestamp = now()
    contract = json.loads((ROOT / "scripts/nms_contract.json").read_text(encoding="utf-8"))
    adapter = NmsAdapter(contract)
    inventories = json.loads((ROOT / "data/runways.json").read_text(encoding="utf-8"))["airports"]
    previous = json.loads(DEST.read_text(encoding="utf-8")) if DEST.exists() else {}
    client, error = None, 'PULL_MODE_REQUIRED'
    try:
        if args.bulk_airport:
            budget = PullBudget()
            budget.check('bulk')
            client = Client(os.environ.get("FAA_NMS_CLIENT_ID"), os.environ.get("FAA_NMS_CLIENT_SECRET"), budget=budget)
            error = None
    except FeedError as exc:
        error = str(exc)
    except Exception:
        error = "INTERNAL_VALIDATION_FAILURE"
    snapshot = update(previous, client, adapter, inventories, timestamp, error, args.bulk_airport)
    sensitive = client.sensitive if client else tuple(os.environ.get(k, "") for k in ("FAA_NMS_CLIENT_ID", "FAA_NMS_CLIENT_SECRET"))
    atomic_publish(snapshot, sensitive=sensitive)
    failed = any(not entry["verified"] for entry in snapshot["airports"].values())
    print("NOTAM snapshot published: " + ("UNKNOWN / degraded coverage" if failed else "verified retrieval"))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("NOTAM publication failed; previous snapshot retained. Details suppressed for credential safety.", file=sys.stderr)
        sys.exit(1)
