# SPA-10: one-PC, offline-tested refresh engine (not activated)

This change adds request preparation and a dependency-injected collector. It does
not make HTTP requests by itself, read environment credentials, initialize a
production ledger, install a schedule, write the website, commit notices, or
change v0.9. No new service, hosting, dependency or distributed infrastructure is
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
3. The production HTTP runtime is intentionally injected. It needs separate source
   review for TLS, explicit time/size limits, one call per prepared request, no
   redirects/retries, safe auth handling and exit-status reporting. No built-in
   client can accidentally authenticate from this PR.
4. The exact completeness/retention and lifecycle semantics are not established
   by response structure. Neither 100 records nor an empty delta changes UNKNOWN.

These are concrete activation gates, not a requirement to contact the provider.
Resolve them from supplied material, separately approved sanitized examples or
an explicitly reviewed policy/adapter; do not invent a live experiment.

## Concrete activation runbook — future approval required; not executed here

1. Review this source-only draft and native-Windows evidence. Keep the existing
   static snapshot published. No live command, schedule or initializer ships here.
2. Designate this PC as the sole collector and obtain confirmation that every
   legacy/manual caller sharing the account remains paused or uses this gate.
   Bind a stable logical account such as `mem-budget` to one fixed local NTFS
   directory outside all repositories. Never create a second store to bypass a
   cooldown, a lost ledger, a pending reservation or the consumed SPA-26 exception.
3. Under **separate accounting-import approval**, provision the private directory
   and both files with `platform_storage`; then initialize `SCHEMA` once using a
   reviewed importer. Import all known account attempts (including failed/uncertain
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
