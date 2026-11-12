#!/usr/bin/env python3
"""Fetch validated AWC METARs, retry transient failures, preserve last good snapshot."""
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

AIRPORTS = {"KORD", "KLAX", "KJFK"}
URL = "https://aviationweather.gov/api/data/metar?ids=KORD,KLAX,KJFK&format=json"
DEST = Path("data/metars.json")
DELAYS = (15, 30, 60)  # seconds before retries 2, 3, and 4


def observation_time(item):
    value = item.get("obsTime", item.get("reportTime"))
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).timestamp()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    raise ValueError("Missing observation time")


def valid(item):
    if not isinstance(item, dict) or item.get("icaoId") not in AIRPORTS:
        return False
    if not item.get("rawOb"):
        return False
    try:
        observed = observation_time(item)
        speed = item.get("wspd")
        if speed is not None and float(speed) < 0:
            return False
        return observed <= time.time() + 300 and observed > time.time() - 7 * 86400
    except (ValueError, TypeError, OverflowError, OSError):
        return False


def fetch():
    req = urllib.request.Request(URL, headers={
        "User-Agent": "SpatchinDashboard/0.9 (GitHub Actions)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=35) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise ValueError("AWC response was not a JSON array")
    selected = {}
    for item in payload:
        if valid(item):
            code = item["icaoId"]
            if code not in selected or observation_time(item) > observation_time(selected[code]):
                selected[code] = item
    if not selected:
        raise ValueError("AWC response contained no valid requested METARs")
    return selected


def main():
    previous = {}
    if DEST.exists():
        try:
            old = json.loads(DEST.read_text(encoding="utf-8"))
            previous = {m["icaoId"]: m for m in old.get("metars", []) if valid(m)}
        except (ValueError, KeyError, TypeError) as exc:
            print(f"Previous snapshot could not be parsed: {exc}", file=sys.stderr)
    latest = None
    for attempt in range(1, len(DELAYS) + 2):
        try:
            latest = fetch()
            print(f"AWC succeeded on attempt {attempt}; {len(latest)} valid airports")
            break
        except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
            print(f"AWC attempt {attempt} failed: {exc}", file=sys.stderr, flush=True)
            if attempt <= len(DELAYS):
                delay = DELAYS[attempt - 1]
                print(f"Retrying in {delay} seconds", flush=True)
                time.sleep(delay)
    if latest is None:
        print("AWC unavailable after retries; keeping existing data/metars.json unchanged", file=sys.stderr)
        raise SystemExit(1)
    merged = dict(previous)
    for code, item in latest.items():
        if code not in merged or observation_time(item) >= observation_time(merged[code]):
            merged[code] = item
    DEST.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "updated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": URL,
        "metars": [merged[code] for code in sorted(merged)],
    }
    DEST.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(f"Published {len(merged)} validated observations; older valid reports retained if missing upstream")


if __name__ == "__main__":
    main()
