# Niltsi v0.9

Static informational aviation weather and runway dashboard for KORD, KLAX, and
KJFK. Existing METAR, unofficial atis.info ATIS, FAA NASR, dark UI, and all 32 runway
ends remain. This is not an operational briefing or runway availability service.

## Observed production structure and remaining verification gate

The owner successfully authenticated and reported KORD 41, KLAX 17, KJFK 41
records. These are observations, not expected counts or completeness guarantees.
No additional live FAA requests were made while adapting this implementation.
No real response artifacts or credentials are stored in this repository.

The owner-observed envelope is `status: string`, `data.geojson: array`. Each
item has `type`, `geometry`, and `properties.coreNOTAMData`, containing:

- `notam`: `id`, `number`, `text`, `effectiveStart`, `effectiveEnd`, `issued`,
  `lastUpdated`, `type`, and potentially other fields.
- `notamEvent.scenario`.
- `notamTranslation`: entries may have `simpleText`, `formattedText`, or other fields.

`scripts/nms_adapter.py` supports this envelope. Every returned item is preserved
as `original_feature`, including unknown fields, geometry, event information,
translations, duplicates, and malformed notices. Missing identity/text gets an
explicit local placeholder and validation warning; the source is never silently
substituted with a translation. Source IDs, full text, original time values,
syntactically normalized UTC values where possible, issued/last-updated times,
retrieval time, query airport/feature/URL, item index/count, profile, and envelope
metadata are retained. The query airport is marked as an unverified association.
Source credentials or reflected known secret values cause whole-response rejection
rather than silent redaction of the original notices.

`tests/fixtures/nms_observed_shape.json` contains **synthetic values reflecting
these observed paths**, not a sanitized real response. The original synthetic
FeatureCollection contract remains in `tests/fixtures/legacy_contract.json` only
for regression tests. It is not the production contract.

**Coverage remains UNKNOWN/unverified.** The production adapter does not interpret
`notam.type`, `notamEvent.scenario`, or envelope `status` as lifecycle, runway
association, or success/completeness semantics. It assigns `lifecycle: UNKNOWN`,
`end_kind: unverified`, `classification: unresolved`, and no runway ends. Even a
changed `verified` flag alone cannot activate semantic interpretation for this
adapter. It never treats a missing next link, total, empty list, or observed record
count as proof of completeness. Undocumented links are preserved but not followed.
ISO timestamps are normalized syntactically; PERM/EST/other encodings stay in the
original source without guessed semantics. The classifier in `notam_rules.py`
is not applied to observed FAA records.

Before enabling actual runway states, validate against FAA documentation and real
FAA examples: lifecycle/cancellations/replacements, identity/version relationships,
validity/schedules and permanent/estimated ends, runway associations and conditional
closures, feature/location coverage, completeness, pagination/truncation, delta
query/cursor/reconciliation rules, and redistribution requirements. Implement and
test those behaviors explicitly. Structure and authentication alone are insufficient.

## FAA request limits and deliberate acquisition pause

Owner-supplied documented production limits are:

- At most **one initial/bulk pull per 24 hours**.
- At most **one delta pull every three minutes**.

No per-airport exception or undocumented batch/delta query is assumed. The code
conservatively treats each airport API request as one bulk pull against a single
global-per-ledger budget. It does not perform the former three-airport bulk loop.
There is no rate-limit bypass option. No higher allowance is presumed approved.

`scripts/nms_pull_budget.py` stores only non-secret deadlines in the ignored,
durable `.nms-state/pull-budget.json`. Atomic reservations and a directory lock
precede each API attempt. Failed API requests consume the reservation; API retries
are disabled, including for HTTP 429/5xx and network failures. Authentication may
retry independently; it is not a NOTAM data pull. Corrupt state, conflicting locks,
clock rollback, or inability to persist a reservation block network access.

Missing history starts a **24-hour bulk / three-minute delta quarantine**, since
prior account usage is unknown. That first attempt writes cooldown state and makes
no FAA request. Do not delete/reset the ledger or launch a second independent
ledger to bypass it. A crash can leave a lock, requiring operator review rather
than an automatic unsafe reset. The ledger coordinates callers on its persistent
filesystem only; it cannot observe requests made outside these scripts or on other
machines. All clients using the same FAA account must share one authoritative
budget and account for the owner's recent manual pulls before more live requests.

Delta reservations enforce three minutes, but **production delta requests remain
disabled entirely** until their query and reconciliation semantics are validated.
The updater defaults to metadata-only UNKNOWN output with `PULL_MODE_REQUIRED` and
makes no authentication/data requests. The five-minute Actions schedule now does
this same metadata maintenance; live acquisition and the duplicate inspector step
are disabled. No credentials are injected into that workflow while acquisition is
paused. Stateless CI runners are not treated as durable account-wide rate tracking.
Before enabling automated acquisition, provide shared durable coordination and a
validated delta protocol. Five-minute fresh data is therefore not available yet.

An explicit local, authorized request can select **one airport** with
`--bulk-airport KORD` (or KLAX/KJFK), subject to the durable budget. Do not run one
for every airport back-to-back. The updater and inspector share the same ledger.
The updater still never advances `last_success_utc` for an unverified response.
Prior verified evidence remains in `notams`; newer unverified observations are
kept separately in `observed_notams` when necessary, and all are inspectable.

## Credentials, inspector, and safe diagnostics

The supplied connection configuration remains:

- `POST https://api-nms.aim.faa.gov/v1/auth/token`, HTTP Basic client credentials,
  form body `grant_type=client_credentials`; expect a nonempty `access_token` string.
- `GET https://api-nms.aim.faa.gov/nmsapi/v1/notams?location=KORD&feature=RWY`,
  `Authorization: Bearer <token>`, `nmsResponseFormat: GEOJSON`.

Use `FAA_NMS_CLIENT_ID` and `FAA_NMS_CLIENT_SECRET` as process variables in the
PowerShell terminal already holding them for an authorized local inspection, or
as GitHub Actions repository secrets for a future approved CI acquisition setup.
Never place them in files, source code, workflow inputs, shell arguments, chat, or
raw-response artifacts. Tokens remain in memory; authorization redirects are
refused. No frontend credential or manually maintained token secret exists.

The following are instructions for a future explicitly authorized check, not a
request to perform more live verification now:

```powershell
Set-Location 'C:\Users\codys\Projects\niltsi'
py -B scripts/inspect_nms_contract.py --check-environment
# Only after account-wide budget coordination and the required cooldown:
py -B scripts/inspect_nms_contract.py --bulk-airport KORD
$LASTEXITCODE
```

The environment check prints presence only and makes no requests. Invoking the
inspector without an explicit bulk airport also makes no requests. A new ledger
quarantines the first attempted live check as explained above. An allowed check
prints filtered field/type structure for one response, holds the parsed notices
in memory only, and does not write a source artifact, publish, follow pagination,
or enable verified coverage. Only its non-secret request-budget ledger changes.
To publish an authorized local observation to the local JSON file instead, use
`py -B scripts/update_notams.py --bulk-airport KORD`; this consumes the same bulk
allowance and is not an additional free verification request.

Diagnostics distinguish missing process variables, token-endpoint HTTP 401/403,
API statuses, redirects, TLS/DNS/connection/timeouts, JSON parsing, unexpected
structure, and budget blocks. No raw responses, headers, token values, or exception
messages are printed. A rejection is not proof of mistyped credentials. Variables
in a separate terminal do not update an already-running Codex process.

For `Could not find platform independent libraries <prefix>`, test Python's library
configuration separately; this warning is not an FAA authentication result:

```powershell
py -c "import sys, json, ssl, urllib.request; print(sys.executable); print(sys.prefix); print('Standard-library imports OK')"
py -E -B scripts/inspect_nms_contract.py --check-environment
```

`-E` ignores Python-specific environment settings such as `PYTHONHOME` and
`PYTHONPATH`, preserving the FAA variables. Repair/select a complete interpreter
if imports fail. Never disable TLS validation to work around runtime problems.
See [Python Windows module paths](https://docs.python.org/3.12/using/windows.html#finding-modules).

## Frontend states and publication

Observed source notices and provenance are inspectable as escaped text, including
formatted translations; HTML from sources is not executed. Duplicate source IDs
are preserved using separate local record keys. Every runway remains visible
without wind data. Observed source semantics cannot become CLOSED, NOTAM
RESTRICTION, or NO CLOSURE IDENTIFIED; coverage stays UNKNOWN. Unknown source
validity or lifecycle cannot silently expire a notice or imply availability.

The frontend retains the four-state model for future verified interpretations:
CLOSED for an unambiguous current closure; NOTAM RESTRICTION for a supported
restriction; NO CLOSURE IDENTIFIED only with fresh, complete, verified coverage;
and UNKNOWN for degraded or unresolved coverage. It never claims operationally
OPEN. The 15-minute freshness threshold, browser failure warnings, METAR/ATIS
behavior, all 32 runway ends, dark theme, and v0.9 labels remain.

Existing METAR/ATIS/runway workflows retain their sources/schedules and bounded
publication retries. NOTAM metadata publication still uses atomic replacement,
per-airport attempt/success times, failure retention, and explicit Pages invocation.
The current authenticated Pages configuration was not available locally (the
unauthenticated API returned 404); no settings have been changed. The reusable
Pages workflow inspects existing settings and fails safely for unsupported setups:

- Existing default-branch-root branch-based publishing: request an explicit Pages
  build without changing mode. The [10-builds/hour soft limit](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)
  is below a five-minute publishing target; review a separately approved Actions-mode
  migration before relying on that cadence.
- Existing Actions mode: package only frontend files and public feeds; deploy via
  standard Pages actions. A serialized deployment checks out latest data after its
  slot is acquired. No site creation or source-setting change is automatic.

The provided `GITHUB_TOKEN` requires `contents: write`, `pages: write`, and
`id-token: write`; no PAT is required. Check branch rules and the `github-pages`
environment before any future approved deployment. `verify_only` skips publication
and deployment, but currently cannot perform live FAA acquisition. A red NOTAM run
is expected while acquisition/semantics remain unverified. Nothing has been
committed, pushed, merged, or deployed during local implementation.

## Offline tests

Python 3.12+ and Node.js 22; no test dependencies:

```sh
python -m unittest discover -s tests -v
node --test tests/*.test.js
node --check app.js
node --check notams.js
```

Synthetic fixtures cover the owner-observed envelope, preservation of every item,
translations/provenance/unknown fields, duplicate IDs, malformed notices, unknown
semantics, retained success timestamps, request limits across instances, lock/state
failure, exact cooldown boundaries, consumed failed requests, no API retries,
safe diagnostics, secret rejection, legacy classifier regression, escaped frontend
text, and all 32 rows without wind. No test makes live FAA requests. Live FAA
semantics, account-wide external traffic coordination, real Pages permissions,
and visual browser rendering remain verification gates.


## Local offline interpretation review

The separate `scripts/review_notams.py` CLI reads **local sanitized JSON files
only**. It does not import the updater/client, read credentials, make requests,
start a server, enable services, publish feeds, change verification flags, or
modify the dashboard. No additional packages or paid features are required.
The existing METAR/ATIS code and all 32 runway ends are preserved.

No previously captured real FAA NOTAM examples are present in this checkout.
`tests/fixtures/notam_review.json` contains 18 artificial test notices, prominently
marked **SYNTHETIC ONLY**, across all three airport contexts. Neither these examples
nor a user's later supplied examples are treated as verified FAA data.

Run the synthetic demonstration at a fixed, reproducible review instant:

```powershell
py -B scripts/review_notams.py tests/fixtures/notam_review.json --at 2026-11-11T12:00:00Z
```

For a standalone dark HTML interface, with expandable source/provenance and links
from every runway end to its candidate notices:

```powershell
py -B scripts/review_notams.py tests/fixtures/notam_review.json --at 2026-11-11T12:00:00Z --html .notam-review/demo.html
```

Open that file directly in a browser. No HTTP server, script execution, external
assets, analytics, or internet access is needed. Output is created exclusively as
a **new** file; choose another filename on subsequent runs. Source files, the root
webpage, and dashboard data paths are protected from output. `.notam-review/` is
ignored by Git. `--json` prints a full machine-readable review to stdout instead.
All report formats preserve the original input and unknown fields. The CLI quotes
untrusted strings as JSON to escape terminal controls; HTML additionally escapes
markup and applies a policy forbidding network resources and scripts.

The report shows source ID/number, original text, user-supplied airport context,
original/normalized UTC validity, schedule fields and text clues, proposed runway
ends, candidate classification, quoted evidence with original-text offsets,
runway-pair evidence, and the existing baseline classifier's result/reason. It
also preserves event/scenario fields, translations, provenance, duplicates, and
unrecognized fields without treating them as authoritative interpretations.
An input SHA256 and fixed `--at` time make a review reproducible.

Proposals distinguish full runway closures, directional restrictions, partial
closures between named taxiways, conditional restrictions, equipment outages,
misleading taxiway mentions, and ambiguous text. Syntactic ISO time windows are
start-inclusive/end-exclusive and shown as FUTURE, EXPIRED,
WITHIN_REPORTED_WINDOW, or UNKNOWN. A window is not proof of lifecycle/currentness.
Compact timestamps, permanent/estimated ends, unsupported schedules, negations,
multiple clauses, conflicting runway pairs, and cancellation/replacement meanings
remain unresolved. Reference hints can flag a related notice for review but never
delete it or establish that it has been cancelled/replaced.

`candidate_state_assuming_lifecycle_confirmed` is a **review proposal**, not a
coverage state. Full/explicit restrictions can have a candidate CLOSED or NOTAM
RESTRICTION label, but all 32 runway rows and all source records keep
`coverage_state: UNKNOWN` and `lifecycle: UNKNOWN`. Ambiguous/conditional/scheduled
or cancellation-related candidates remain UNKNOWN. Future/expired candidates do
not assert a current closure. No missing notice or empty input produces OPEN or
an availability inference. The review classifier is not used by live acquisition.

### Supplying already captured sanitized examples later

1. Use an **already saved** FAA JSON response; this procedure does not authorize
   any new API request. Work on a local copy outside the tracked repository.
2. Remove authentication responses, authorization headers, tokens, client IDs,
   client secrets, cookies, and private request metadata. Inspect the copy locally
   before supplying it. Preserve public NOTAM IDs, full text, validity, schedules,
   translations, references, and other public fields. Record what categories were
   removed in a sanitization note, never the removed values. Automatic input checks
   reject credential-looking keys but cannot certify that arbitrary JSON is safe.
3. Place the sanitized response in `.notam-review/inputs/kord.json` (or a private
   local directory). Do not paste raw credentials or unsanitized responses into
   chat, commit them, or upload them to a service.
4. Supply its captured airport/query context explicitly. For example:

   ```powershell
   py -B scripts/review_notams.py .notam-review/inputs/kord.json --airport KORD --kind sanitized-real-unverified --html .notam-review/kord-review.html
   ```

   Supported raw input is the observed `{status, data: {geojson: [...]}}` envelope.
   `--kind` records the user's declaration, not an authenticity check. `--airport`
   is a review context, not a validated affected-airport assertion. Use `--at` with
   an explicit UTC/offset instant to review the historical capture; omission uses
   the current UTC time.
5. For several airports and capture provenance, use the bundle structure in the
   synthetic fixture: `review_input_version: 1`,
   `data_kind: "sanitized-real-unverified"`, and `airports` keyed by ICAO. Each entry
   contains its unchanged sanitized `response` and a `provenance` object. Record
   actual capture UTC if known, query context, local source reference, and a
   sanitization note; use null for unknown values. Preserve additional metadata.
   Do not pass `--airport` for a multi-airport bundle. Do not relabel the bundled
   synthetic fixtures as real examples.
6. Compare each proposal/evidence with the original and authoritative FAA rules.
   Retain reviewer decisions separately as notes; the tool has no approval action
   that enables live coverage. A corrected synthetic rule is not real-data
   validation. Complex schedules, real lifecycle/reference behavior, airport and
   runway scope, and complete source coverage remain verification gaps.
