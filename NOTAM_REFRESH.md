# SPA-10: one-PC, offline-tested refresh engine (not activated)

The engine uses explicit injected dependencies. The separate `nms_http.py` adapter
now implements one bounded HTTPS call when invoked; import/construction do not
connect. `import_nms_history.py` defaults to a read-only review and writes only
with an explicit approved digest. Neither integration has been activated on
production. There are no environment credential reads, schedules, website writes,
notice commits or v0.9 changes. No new service, hosting, dependency or distributed infrastructure is
needed. `sqlite3` is part of the tested native Python runtime.

The **private accepted outbox** is the publication handoff. The existing website
and `data/notams.json` are never opened for writing by this collector. Automatic
Git/Pages publication is deliberately absent. Publication failure/preflight and
outbox transaction failure retain the previous accepted bytes and cursor; an
ambiguous disk/commit outcome requires manual reconciliation before any export.

## Supplied contract, used without provider clarification

- [OpenAPI 1.0.18, revised 2026-02-12](https://drive.google.com/file/d/1Jbf-YgCDZ-TMVoPQyS5LraGXw_ThtM7M/view).
- [Supplied NMS FAQ](https://drive.google.com/file/d/1RazUuGb3o5xEkEUXon8_XR70K-KtJ8_R/view).
- [Production request examples](https://drive.google.com/file/d/1mN1B95eNRX_eMVp4TAnSZ8rEQO1ih1vc/view).

The FAQ allows at most one delta/data pull per three minutes and at most one
initial/full-classification bulk pull per 24 hours. All primary pulls use the
three-minute account gate; bulk also uses the 24-hour gate. A failed auth consumes
its reserved pull allowance conservatively. These are elapsed UTC intervals, not
calendar days or an arbitrary project time floor.

Auth is POST `https://api-nms.aim.faa.gov/v1/auth/token`, Basic authentication,
form `grant_type=client_credentials`. The engine accepts an explicit in-memory
credential mapping; it never reads process environment or credential stores.
Credentials/auth responses/response headers are never written to the ledger.

Documented initial-load preparation is GET `/nmsapi/v1/notams/il?allowRedirect=false`.
The IL file is **compressed AIXM inside SOAP**, not GeoJSON. Classification-only
GeoJSON request preparation is also supported using `/nmsapi/v1/notams` with
`classification=<documented enum>`, `allowRedirect=false`, and header
`nmsResponseFormat: GEOJSON`. There is no invented ALL classification value.
The executable engine currently uses only the all-classification IL path; the
classification-only plan is not silently substituted for a full baseline.

Initial responses use `status` and `data.url`. Only documented same-host content
paths are accepted: `/v1/content/{token}` relative to `/nmsapi`, or the explicit
`/nmsapi/v1/content/{token}` spelling in the FAQ. The same bearer token is used.
No redirects, arbitrary absolute/signed cloud URLs, URL decoding to a second host,
pagination or retry loop is implemented. Content paths expire after five minutes;
the client checks that interval conservatively from the initial request start.
The content GET is a logged continuation of the reserved bulk pull, not another
initial-load allowance; it must finish within the documented content-path lifetime.

Deltas use `/nmsapi/v1/notams?lastUpdatedDate=<UTC seconds>` and GEOJSON. OpenAPI
describes modifications (including inactive notices) within the preceding **24
hours**. The five-day limit on Location-Series is unrelated and is not reused.
No location/classification filters are added to the delta query. No pagination,
record cap, max-page controls, unsupported lifecycle code or deletions are invented.

## Account storage, locking and failure behavior

`Store` opens an explicitly supplied absolute private directory and existing
`account.sqlite3` plus `account.sqlite3-journal`. Missing paths/history, unsupported
schema, failed ACL/permission checks, corrupt SQLite, missing counted history rows
or clock rollback fail before transport. There is **no create-on-open/reset path**.

Both database and persistent rollback journal must first be created by the
existing `capture_storage.platform_storage()` implementation under a separately
approved history import. Native Windows uses protected owner+SYSTEM DACLs on local
NTFS; chmod is not privacy evidence. POSIX uses the existing owner/mode and flush
checks. SQLite uses native platform locking, `journal_mode=PERSIST` and
`synchronous=EXTRA`; it retains the precreated protected journal. If hot-journal
recovery removes a required file, later validation refuses collection rather than
recreating it with weaker permissions. SQLite/VFS/device durability guarantees
still apply; this is not a promise to survive a dishonest disk cache.

One store is bound to one logical account, `scope=all-classifications` and KMEM
reader extraction. All clients sharing those credentials must use that **same
physical store**, or remain paused. A different path is not another allowance.
This code cannot constrain a separate client that ignores the gate. No network or
multi-PC coordination is claimed.

`BEGIN IMMEDIATE` serializes admission. The primary attempt commits before auth;
each auth/initial/content/delta stage commits its reservation before that I/O.
`active` persists across process exit. Concurrent callers are refused; a crashed
reservation is never assumed unused or expired automatically. Admission examines
all successful, failed, imported and uncertain attempts. Cooldowns anchor to the
latest reservation/I/O time, including late auth/content activity, rather than
the start of a slow run. Successful data completion and failed/uncertain run
completion extend the anchor conservatively. SQLite lock waits are bounded at
two seconds to let short reader/writer transactions finish; they are not provider
retries. Busy failures do not trigger transport retries.

Successful data response bytes are retained privately, including unrecognized
fields and duplicates, before decoding/acceptance. Auth responses and non-200
error bodies/headers are not retained. Request reprs hide URLs, bodies and headers;
exception messages crossing the engine boundary are fixed codes only.

Opaque source IDs and source `lastUpdated` times identify updates; they are not
interpreted as lifecycle status. Exact replay is idempotent. A changed record at
the same/older timestamp, contradictory location fields or a changed MEM
association is a conflict: retain the accepted snapshot and require recovery.
Raw responses preserve even duplicate/older records. Absence never deletes an
ID; cancellations, expired/future effective times and schedules are not filtered.

All raw features remain in the private account catalog. Only explicit source
`icaoLocation=KMEM` or `location=MEM/KMEM` contributes to the MEM reader outbox.
Conflicts are not resolved using runway text/FIR/geometry. Unresolved location
records remain private and are counted in outbox provenance. Normalization reuses
`normalize_notam_snapshot.normalize`; its local partition is not misrepresented
as an upstream location query. Existing records keep their original retrieval
timestamps. Source text, representations and unknown fields remain unmodified.
Outbox attribution is neutral, coverage UNKNOWN, availability unassessed.

The accepted catalog, exact outbox bytes and cursor commit together. Failed
transport/decoding/validation/publication preflight rolls back acceptance while
retaining the consumed attempt and received response. Failed persistence returns
`PERSISTENCE_UNCERTAIN` if the failure cannot itself be recorded safely; do not
export or keep polling after that diagnostic. SQLite atomicity does not make a
lost COMMIT acknowledgement distinguishable from a committed write. Inspect that
case manually; the public website remains untouched in either case.

The delta cursor is the prior request's start, floored to seconds with one second
of **client replay overlap** (not a provider boundary rule). It never jumps to
the greatest source timestamp, response completion time or publication time.
Failed attempts do not advance it. If the replay lower bound falls outside 24
hours, stop with `DELTA_GAP`; do not query a shorter interval or claim recovery.

## Contract details not implemented by guessing

1. The documents describe compressed content but do not specify a complete archive
   packaging/member schema or AIXM/SOAP-to-reader mapping. `decode_initial` is a
   required injected adapter, with no default decompressor or invented field map.
   Synthetic tests feed labeled JSON bytes to a mock; that is not claimed as a
   verified initial-load wire format.
2. No documented response field establishes the IL file's exact snapshot cutover
   relative to delta history. A reviewed decoder must return `DecodedInitial`
   with a justified `coverage_since` lower bound, within the delta lookback. Local
   download time or maximum record timestamp is not such evidence. Without it,
   baseline acceptance stops; no hidden timestamp assumption enables collection.
3. The HTTP adapter and explicit history importer described below have offline
   native-Windows tests. They still require runtime/import review and separate
   execution authorization. The adapter is not constructed or called by the engine
   implicitly; a caller must inject it and explicit in-memory credentials.
4. The exact completeness/retention and lifecycle semantics are not established
   by response structure. Neither 100 records nor an empty delta changes UNKNOWN.

These are concrete activation gates, not a requirement to contact the provider.
Resolve them from supplied material, separately approved sanitized examples or
an explicitly reviewed policy/adapter; do not invent a live experiment.

## Concrete activation runbook — future approval required; not executed here

1. Review this source-only draft and native-Windows evidence. Keep the existing
   static snapshot published. The importer is separate and requires its own approval;
   no collection launcher or schedule ships here.
2. Designate this PC as the sole collector and obtain confirmation that every
   legacy/manual caller sharing the account remains paused or uses this gate.
   Bind a stable logical account such as `mem-budget` to one fixed local NTFS
   directory outside all repositories. Never create a second store to bypass a
   cooldown, a lost ledger, a pending reservation or the consumed SPA-26 exception.
3. Under **separate accounting-import approval**, use the explicit review/digest
   workflow below. The importer creates the fresh directory and both files with
   `platform_storage`, then initializes `SCHEMA` exactly once. Import all known account attempts (including failed/uncertain
   and the prior one-time capture), their conservative reservation/last-I/O UTC,
   outcome and scope. `other-data` participates in the three-minute gate; do not
   mislabel every location query as a documented bulk pull. Unknown scope/history
   needs operator reconciliation, not an empty ledger. Preserve the source receipts.
4. The state row must contain schema/account/history_verified/history_complete_through,
   scope `all-classifications`, airport `KMEM`, active/recovery, baseline/watermark,
   catalog, published_b64 and exact attempt_count/call_count/response_count. Use the
   existing approved publication as the initial outbox bytes, but **not** as an IL
   baseline: the old location-filtered capture does not establish one. Initial
   baseline=false, watermark=null; preserve imported history. The synthetic test
   helper demonstrates the schema only and must never be used on production.
5. Validate every ACL, SQLite integrity/journal setting, imported row count and
   UTC clock. Obtain a reviewed real transport and initial decoder/cutover policy.
   Supply credentials only to that future local runtime through a hidden prompt
   or approved process secret mechanism. Do not put values in commands, files,
   logs, issue/PR text or database state. Do not reuse the archived inspector.
6. After explicit request authorization and eligibility review, the single-call
   integration shape is:

   ```python
   collect(Store(approved_private_path, account=approved_logical_account),
           mode="bulk", credentials=hidden_prompt_result,
           transport=reviewed_one_call_transport, clock=verified_utc_clock,
           decode_initial=reviewed_initial_decoder)
   ```

   These names are integration dependencies, not supplied live commands. Inspect
   the returned fixed diagnostic and private responses/outbox. No retries or
   schedule activation follows implicitly. Follow later authorized delta calls
   with `mode="delta"`, using the same store and gate.
7. `Store.snapshot()` returns only the accepted private handoff. Review selected
   fields/legal notices and SHA256 before any separate data-only publication.
   Never publish the database/journal/raw responses or automatically promote an
   outbox after a failed/uncertain operation. A public deployment bridge remains
   a separate explicit action; no hosting or new infrastructure is needed.
8. Restart recovery: accepted stores resume with existing cursor and cooldowns.
   Pending/crashed attempts freeze collection. Verify the prior process is gone,
   reconcile all attempted stages and actual uncertainty interval, retain them as
   accounted, and only under explicit operator approval close the pending marker.
   Do not delete rows, rewind last-I/O time, reset counters or infer unused calls.
   A gap/conflict/corrupt store requires review and, if justified and budget-eligible,
   a separately approved new baseline—not a shorter guessed delta or ledger reset.
9. Only after runtime/request-budget/outbox behavior is accepted may the owner
   separately authorize a single-PC schedule. No Task Scheduler entry, workflow,
   daemon, auto-Git action or recurring collection is created by this PR.

## Offline native-Windows verification

Use the existing native Python and installed Chrome/Playwright dependencies; no
downloads or paid services. Run the complete Python suite with socket/DNS auditing
blocked, and the complete JavaScript/browser suite with external page routes
blocked. `tests/test_nms_refresh.py` creates **only synthetic temporary account
stores**, injects synthetic credentials/data and mock transport, and exercises
real Windows ACLs, SQLite transactions, process exit/restart, two-process races,
elapsed cooldown boundaries, response replay, conflicts/gaps, failure rollback,
private outbox failure and credential-safe diagnostics. Child process tests also
block sockets. This does not validate an unimplemented live content decoder or
the production ledger/import, and does not authorize any provider request.


## HTTP adapter integration (not activated)

`nms_http.HTTPAdapter(timeout=30, max_bytes=64*1024*1024)` is a callable matching
`transport(Request, limit)`. Construction/import never creates a connection.
Only a subsequent explicit call does. Inject this callable into `collect`; it does
not read environment credentials, install a launcher or bypass engine accounting.
The initial decoder and justified baseline cutover are still required and are not
implemented in this integration.

The adapter validates the entire prepared stage/method/URL/query before opening a
connection. The exact production authority is required. Auth, IL (including its
documented classification suffix), classification-only GeoJSON, delta and content
requests have separate shapes. Pagination, extra query keys, header injection,
auth at the wrong endpoint, absolute cloud URLs, cross-host URLs and encoded
content-path traversal are refused. No environment proxy is used. Only explicit
auth/content-type/response-format/accept headers can pass validation.

It uses `ssl.create_default_context()` with hostname/certificate verification,
`http.client.HTTPSConnection`, a positive timeout at most 30 seconds, a bounded
response read loop and an independent local response-byte budget. These are client
resource bounds, not provider caps. Elapsed time is checked between operations;
socket timeouts bound blocking network operations. Normal OS DNS resolver timing
is not claimed as a hard cancellable wall-clock deadline. No worker thread is
left to issue an unaccounted late request, and no redirect/retry is performed.

All redirects are refused without following Location or reading their bodies.
Other non-200 responses return only status and empty headers/body to the engine.
Successful responses return exact bytes with no retained headers. Fixed adapter
codes distinguish invalid requests, timeout, TLS/network/protocol failure,
redirect refusal and excessive size. Upstream messages/headers/bodies, credentials
and tokens never enter those diagnostics. The merged engine records its existing
safe failure code and retains consumed accounting/snapshot bytes; it does not retry.
Real TLS exchange has not been tested; fake HTTPS connections assert the normal
verification configuration with actual socket/DNS access blocked.

## Explicit history manifest and dry-run/write usage

This is a separately approved future operator action. **Do not run a production
import as part of this implementation review.** No target directory is inferred.
An existing target (even an interrupted import) is always refused. Do not choose
another target to bypass history; inspect/reconcile the first artifact instead.

The following is a **synthetic schema example**, not production history. Replace
all evidence, identities, counts and timestamps from reviewed records; never copy
these invented example times into an actual account import. Paths must be absolute.
Hashes are lowercase SHA256 and must match exact existing file bytes.

```json
{
  "schema_version": 1,
  "account": {
    "logical_id": "SYNTHETIC-ACCOUNT",
    "identity_reference": "SYNTHETIC operator account-binding record (not OAuth credentials)",
    "identity_confirmed": true,
    "all_clients_quiesced": true
  },
  "history": {
    "inventory_complete": true,
    "complete_through_utc": "2026-01-02T11:30:00Z",
    "declaration_reference": "SYNTHETIC review of every account client/known attempt"
  },
  "attempts": [{
    "id": "SYNTHETIC-ATTEMPT",
    "kind": "other-data",
    "reserved_utc": "2026-01-02T11:00:00Z",
    "last_io_utc": "2026-01-02T11:01:00Z",
    "last_io_is_upper_bound": false,
    "outcome": "succeeded",
    "known_call_count": 2,
    "receipt": {"path": "C:/NmsReview/SYNTHETIC-receipt.json", "sha256": "<exact SHA256>"},
    "calls": [
      {"stage": "auth", "at_utc": "2026-01-02T11:00:00Z", "certainty": "observed"},
      {"stage": "other-data", "at_utc": "2026-01-02T11:01:00Z", "certainty": "reserved"}
    ]
  }],
  "uncertainties": [],
  "prior_snapshot": {
    "path": "C:/NmsReview/SYNTHETIC-prior-snapshot.json",
    "bytes": 123,
    "sha256": "<exact SHA256>",
    "approved_for_display": true,
    "origin_attempt_id": "SYNTHETIC-ATTEMPT"
  }
}
```

`kind` is bulk/delta/other-data; outcome is succeeded/failed/uncertain. Explicit
known call counts must match all listed calls, including retries/reservations;
repeated stages are imported with distinct ordinal keys, not deduplicated.
Recognized repository SPA-26 receipts are checked against their defined auth/GET
counters. Other receipt formats are preserved by hash/reference and require
operator semantic review; their field names are not guessed into accounting.
The importer never edits, moves or overwrites original receipts. It retains the
exact manifest privately and preserves the prior accepted display snapshot bytes.

`inventory_complete` attests that the operator enumerated all known attempts and
all uncertainties; it does not erase unknown outcomes. Missing identity/history,
unknown scopes, missing/invalid timestamps, contradictory time ranges, duplicate
attempt IDs, wrong counters, missing origin evidence and hash changes reject the
import with specific fixed diagnostics. Empty history cannot silently accompany
an existing account snapshot whose origin attempt has not been supplied.

For explicitly **bounded uncertain** attempts, set outcome=uncertain and
last_io_is_upper_bound=true, provide the known reservation and latest possible I/O
UTC from evidence, and add one uncertainty entry:
`{"attempt_id":"SYNTHETIC-ATTEMPT","code":"TIMING_UNCERTAIN"}`.
Codes also include OUTCOME_UNKNOWN and ADDITIONAL_CALLS_UNKNOWN. Unknown individual
call times may be represented explicitly by `at_utc:null, certainty:"unknown-time"`
only for such attempts. Their counters are retained, with UNVERIFIED_TIME in the
private call row?not a fabricated timestamp. The report identifies untimed calls.
These imports end HELD_UNCERTAINTY, history_verified=false, and cannot collect.
There is no uncertainty-resolution, reset or automatic release command here.
Unbounded uncertainty or an incomplete attempt inventory is rejected, not guessed.

After separate operator approval, PowerShell usage is:

```powershell
# Read-only: no target is created, no SQLite connection is opened.
py -B scripts/import_nms_history.py --manifest 'C:/NmsReview/reviewed-history.json' --target 'C:/NmsPrivate/approved-account'

# Only after reviewing the full report and separately authorizing this import:
py -B scripts/import_nms_history.py --manifest 'C:/NmsReview/reviewed-history.json' --target 'C:/NmsPrivate/approved-account' --write --approve '<approval_digest from the reviewed dry run>'
```

The digest binds manifest bytes, referenced snapshot/receipt hashes and the exact
target. The write invocation revalidates and prints its review before any write.
Changed input or target requires another review; it is never silently approved.
Reports show planned attempt/call counts, timestamps, uncertainty/hold status,
minimum cooldown boundaries and snapshot hash/size, never snapshot/receipt bodies.
Those boundaries are computed lower limits, not authorization for a provider call.
No credentials belong in this manifest or either command.

The writer requires a fresh directory outside repositories and creates protected
owner+SYSTEM files on native Windows NTFS (existing POSIX protections elsewhere).
SQLite uses the merged SCHEMA, PERSIST rollback journal and synchronous=EXTRA.
After counter/integrity/ACL/readback checks and a second input-hash validation,
readiness is atomically persisted through the reused private-file replacement.
`baseline=false`, `watermark=null`, and catalog empty are mandatory: the old MEM
publication is an accepted display snapshot, not an all-classification baseline.

An import-status.json PENDING marker gates the engine even if SQLite state has
already been populated. Early interruption leaves this marker and partial files
for inspection; there is no auto-cleanup/retry that might discard accounting.
Only a fully validated READY marker permits normal Store use, and its digest must
match the state row. A lost acknowledgement while publishing READY can mean the
import finished; IMPORT_FINALIZATION_UNCERTAIN requires inspection, never another
import over that path. Original receipts remain unchanged in either case.

Remaining activation steps are unchanged in substance: review this draft and the
actual history/evidence, authorize only the history import first, resolve any held
uncertainty without discarding counters, implement/review the initial archive/AIXM
decoder and justified cutover, then separately authorize a controlled collection
call with the HTTPAdapter injected. No initial decoder, scheduler, Git publisher,
provider call or new service is added by this integration.
