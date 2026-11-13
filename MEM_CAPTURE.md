# One-time supervised MEM capture

This prepares the next NOTAM MVP step: acquire one location-scoped response, retain
it privately, and run the merged offline normalizer. It does not schedule a request,
publish a snapshot, modify the dashboard, or implement a recurring collector.

The coordinating conversation holds the durable manual request log in
[SPA-26](https://linear.app/spatchin/issue/SPA-26/one-time-mem-capture-manual-request-ledger).
The user approved one designated collector: the existing Cloud task holding
`/workspace/work/mem-budget`, with every other FAA NMS client paused. This manual
exception does not replace or reset the archived ledger or provide a distributed
lock. The script cannot verify Linear state or the identity of its executing task;
those checks belong to the supervisor before execution.

## Before execution

1. Wait until at least **2026-11-13T07:44:03.558Z** (November 13, 1:44:03.558 a.m.
   America/Chicago). Any later activity, uncertain exclusivity, or known cooldown
   can extend the deadline. Elapsed time alone is not permission to run.
2. Confirm all other clients remained paused. Recheck SPA-26 and the designated
   task. The coordinator must persist one reservation in SPA-26 and read it back
   before preparing the private reservation file. No reservation is issued by this
   document, by tests, or by creating the PR.
3. Record a fresh UUID, reservation time, expiration time, effective not-before
   time and one absolute private output directory outside the checkout in that
   reservation. Its parent directory must already exist. Do not copy the
   reservation to another task or change its directory to retry an attempt.
4. Credentials must already be supplied through environment configuration as
   `FAA_NMS_CLIENT_ID` and `FAA_NMS_CLIENT_SECRET`. Never paste values into chat,
   arguments, logs, source files or the reservation file.

Reservation JSON fields (the values below are deliberately non-executable):

```json
{
  "schema_version": 1,
  "ledger": "SPA-26",
  "state": "TEMPLATE_NOT_AUTHORIZED",
  "airport": "KMEM",
  "collector": "mem-budget",
  "auth_attempt_limit": 1,
  "data_attempt_limit": 1,
  "reservation_id": "SUPERVISOR-ISSUED-UUID",
  "not_before_utc": "EFFECTIVE-ELIGIBILITY-TIME",
  "reserved_utc": "TIME-SAVED-IN-LINEAR",
  "expires_utc": "SUPERVISOR-SET-EXPIRATION",
  "output_dir": "/private/path/reserved-for-this-attempt"
}
```

After these steps, run only in the designated task:

```sh
python3 -B scripts/capture_mem_notams.py --reservation /private/path/reservation.json
```

That checks local reservation shape/time only; it does not authenticate, reserve a
request in Linear, or verify task identity. Only after the supervisor's durable
reservation and readback, execute once with the additional `--execute` switch.
No CLI is run live during preparation or testing.

## Attempt accounting and output

The only network sequence is one token POST, then one GET of
`/nmsapi/v1/notams?location=KMEM` using `nmsResponseFormat: GEOJSON`. Both use the
fixed production host, normal HTTPS/proxy/CA settings, a bounded timeout, and no
redirects or retry loop. No classification, initial-load or follow-up content
request is supported. The token remains in memory; authentication bodies and
upstream error bodies are not saved or printed.

The output directory is created exclusively with mode 0700. A flushed receipt is
recorded before each attempt; capture/receipt files are mode 0600. Directory/file
flush failures stop execution. A second invocation using the reserved output path
is refused. This is a local replay guard, not verified shared durable storage:
missing/lost task files must never be interpreted as permission for another attempt.
The durable Linear reservation remains consumed after failures or uncertainty.

A successful inline response produces:

- `response.json`: original response bytes, private and unmodified.
- `normalized.private.json`: separate text representations, raw fields and original
  records from the existing reader adapter. It is **not** published to data/notams.json.
- `receipt.json`: reservation UUID, attempted counts, HTTP statuses, retrieval UTC,
  query context, validated-format request ID when available, hash, returned count
  and final state. This non-secret receipt is suitable for reporting to the coordinator.

A malformed JSON/envelope/content-pointer response stops after the single GET;
raw HTTP-200 bytes remain private for investigation. HTTP failures and redirects
are not retried. Never publish or commit a capture or normalized result without a
separate review. CAPTURED_UNVERIFIED is not a completeness or field-semantics claim.
After the attempt, update SPA-26 from the receipt; do not automatically clear a
reservation or run another capture.

## Offline validation

`tests/test_mem_capture.py` injects synthetic credentials and a mock transport. It
covers recorded counters before I/O, quarantine/expiry, exact endpoints, private
output, replay refusal, HTTP/auth/data errors, malformed/content-pointer responses,
redirect refusal, expiration after authentication and sanitized failures.
Run it with the repository's socket-blocked Python harness. It makes no live
requests and issues no reservation. No browser tests are needed for these CLI-only
changes. Live transport remains untested until the separately supervised capture.
