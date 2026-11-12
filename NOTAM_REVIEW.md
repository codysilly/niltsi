# Spatchin offline NOTAM interpretation review (SPA-9)

This local-only tool produces **unverified interpretation proposals** from local
JSON. It does not acquire data, read process credentials, update dashboard feeds,
enable coverage, or publish anything. Every notice and runway row retains
`coverage_state: UNKNOWN`; notice lifecycle remains UNKNOWN. Candidate CLOSED or
NOTAM RESTRICTION labels are hypothetical review proposals, never live states or
operational availability determinations. Missing notices never establish OPEN.

## Provenance and dependency boundary

Adapted from the uncommitted `feature/notam-integration` prototype based on
`126e3e575d1bbdbf6c32ccf3032a0e575aa8450e`. The prototype checkout is preserved.
Only these offline components are reused:

- `scripts/review_notams.py`: local JSON CLI, text/JSON and standalone HTML output.
- `scripts/notam_review.py`: proposals, quoted evidence offsets, validity windows,
  schedules/reference hints, source/provenance preservation and UNKNOWN coverage.
- `scripts/notam_rules.py`: conservative baseline text classifier, used only for
  review here; it does not establish real FAA semantics or availability.
- `scripts/notam_review_helpers.py`: only the exception, nested-path lookup,
  syntactic UTC normalization and credential-looking-input rejection extracted
  from the prototype adapter. No adapter, URLs, contract configuration, HTTP client,
  request-budget code, authentication or environment inspection is included.
- `tests/fixtures/notam_review.json`: 18 clearly labeled **SYNTHETIC ONLY** notices.
- `tests/test_notam_review.py`: focused offline review regression tests.

The fixture reflects owner-reported envelope paths, not a sanitized real FAA
response: `{status, data: {geojson: [...]}}`, with notices beneath
`properties.coreNOTAMData.notam`. Neither those paths, envelope status, synthetic
examples nor successful tests establish authenticity, lifecycle, coverage,
pagination, completeness, or FAA semantics. No real examples are bundled.

## Usage

Python 3.12+; standard library only. From the repository root:

```sh
python -B scripts/review_notams.py tests/fixtures/notam_review.json --at 2026-11-11T12:00:00Z
python -B scripts/review_notams.py tests/fixtures/notam_review.json --at 2026-11-11T12:00:00Z --json
python -B scripts/review_notams.py tests/fixtures/notam_review.json --at 2026-11-11T12:00:00Z --html /path/outside-checkout/synthetic-notam-review.html
```

Choose a **new** local HTML filename. Existing output is never overwritten; the
repository root and dashboard data directory are protected. Keep generated output
and any private inputs outside the checkout. HTML includes prominent synthetic
labels, input SHA256, fixed review time, original notices, provenance, unknown
fields, proposals and evidence. Source text is escaped; HTML has no scripts or
external resources and uses a restrictive content security policy.

The review scope is KORD/KLAX/KJFK and their 32 runway ends, using the existing
local `data/runways.json` read-only as reference inventory. This does not change
the dashboard's MEM default or KMEM mapping. MEM review and real MEM NOTAM evidence
are not included. Dashboard SCAN, saved airport preferences/storage migration,
versions, schedules, and published data are unchanged.

## Already captured examples

Do not make a new provider request as part of this workflow. Use only an already
captured local response after privately removing authentication material, tokens,
client IDs/secrets, cookies and private request metadata. Preserve public source
text, identity, validity, schedules, translations and provenance. Record capture
time/query context and what categories were removed, never removed values.
Automatic rejection of credential-looking keys is **not** sanitization certification.

For a single response, explicitly supply both airport context and declared kind:

```sh
python -B scripts/review_notams.py /private/sanitized.json --airport KORD --kind sanitized-real-unverified --at 2026-11-11T12:00:00Z
```

For multiple airports, use the fixture's `review_input_version: 1` bundle shape
with `data_kind: "sanitized-real-unverified"` and per-airport `response` and
`provenance`. That declaration records the user's assertion, not verification.
Never relabel synthetic examples as real evidence or commit private captures.

## Tests and limitations

```sh
python -B -m unittest discover -s tests -p test_notam_review.py -v
```

Tests cover scope/pair guards, partial/directional/equipment proposals, conditional
language, schedules, cancellation/replacement hints, UTC boundaries, duplicate
preservation, escaping, protected/new-only output, dependency isolation and UNKNOWN
coverage. All examples are synthetic. The CLI no-network regression blocks socket
creation and URL opening; the complete suite can also run under a process-wide
socket/DNS blocking harness.

PERM/EST and compact times, complex schedules, cancellation/replacement resolution,
airport/runway association, pagination/completeness, delta reconciliation and
redistribution requirements remain unverified. Real examples and authoritative
rules must be reviewed separately before any future live interpretation. There
is no approval switch in this tool that enables production coverage.
