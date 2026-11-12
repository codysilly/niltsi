# Unfinished NOTAM prototype: Codex Cloud handoff

This branch archives the original uncommitted `feature/notam-integration` work for
continued development. It is **not** an integration branch or a release candidate.
Do not merge it wholesale into main, rebase it onto main, deploy it, or dispatch
its workflows as part of this handoff.

## Base and preservation

- Old base: `126e3e575d1bbdbf6c32ccf3032a0e575aa8450e` (Update METAR observations).
- Original local branch: `feature/notam-integration`, left pointing at that base.
- Archive branch: `archive/notam-prototype-handoff`.
- Main comparison snapshot: `816236af76f3330a2aa6906d2815bebc0b76ed67`.
- Separately reviewed offline-only reuse commit:
  `e4dbae052540c248afd539cc5d5979a89b693454` on
  `feature/notam-offline-review`. Prefer that isolated work for integration;
  its later HTML presentation and deterministic browser fixture are not backported
  into this older prototype.

The archive preserves prototype source, safe configuration, documentation,
synthetic fixtures/tests, and proposed workflow/publication code for inspection.
Existing source files were not rewritten to resemble current main.

Excluded: `data/notams.json` (generated empty UNKNOWN scaffold), generated review
HTML, `.notam-review/`, `.test-output/`, request-budget state, caches, dependencies,
environment files, secrets, logs and private captures. Do not copy these from a
local development environment into Cloud or the public repository. No real FAA
response artifacts are bundled. A clean archive checkout has no NOTAM feed;
the frontend must treat it as unavailable/UNKNOWN. Offline tests construct their
own data and do not require that excluded feed.

## Cloud starting point and offline checks

Read this document and README before running anything. Use Python 3.12+ and
Node.js 22+. The suites use standard libraries and injected/mock transports.
Do not install dependencies, configure FAA credentials, or run acquisition CLIs
just to exercise them. Run from the repository root:

```sh
python -B -m unittest discover -s tests -v
node --test tests/notams.test.js
node --check app.js
node --check notams.js
```

For process-wide Python network blocking, use this harness instead of the first
command (temporary test files are confined to operating-system temp directories):

```sh
python -B - <<'PY'
import socket, sys, unittest
def blocked(*args, **kwargs):
    raise AssertionError('Network blocked during offline tests')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
result = unittest.TextTestRunner(verbosity=1).run(
    unittest.defaultTestLoader.discover('tests'))
sys.exit(not result.wasSuccessful())
PY
```

For Node, the VM dashboard fixture already rejects fetches; this wrapper also
blocks real HTTP/socket transports for the whole test process:

```sh
node <<'JS'
const blocked=()=>{throw Error('Network blocked during offline tests');};
for(const name of ['node:http','node:https']) {
  const m=require(name);m.request=blocked;m.get=blocked;
}
const net=require('node:net');
net.connect=blocked;net.createConnection=blocked;net.Socket.prototype.connect=blocked;
require('node:tls').connect=blocked;
require('node:dgram').createSocket=blocked;
global.fetch=blocked;
require('./tests/notams.test.js');
JS
```

Handoff verification: 88 Python tests and 16 Node tests passed under these guards.
Python compilation, JavaScript syntax, whitespace and local actionlint checks
passed. This verifies synthetic/offline behavior, not FAA semantics or deployment.

## Acquisition remains disabled

**FAA authentication and data requests require separate, explicit authorization.**
This archive/push does not authorize any request. Do not execute
`update_notams.py`, `inspect_nms_contract.py`, any other updater, or workflow
dispatch for live verification. Do not supply credentials to automated tests.
Never print or commit credentials, raw responses, authentication diagnostics,
request headers or environment dumps.

The prototype NOTAM workflow does not inject credentials and its default updater
path only maintains UNKNOWN metadata; live acquisition is disabled. That path
still writes generated metadata and returns failure while coverage is unverified,
so it is not an offline test command. An explicit `--bulk-airport` path contains
real acquisition code and must not be run without separate authorization.

The contract configuration is marked unverified. Owner-reported KORD/KLAX/KJFK
record counts and field paths are observations, not captured evidence or expected
completeness. All checked-in response examples are labeled synthetic. Source
notices retain UNKNOWN lifecycle/coverage; the legacy classifier and review
proposals must not be treated as confirmed runway status or operational OPEN.

The budget helper coordinates only callers sharing one durable filesystem ledger.
It cannot account for manual requests or other machines. Missing history starts
quarantine; never delete/reset state or create another ledger to bypass limits.
Account-wide coordination, documented limits and delta reconciliation remain
unverified requirements before future authorized acquisition.

## Conflicts and limitations relative to main

- This old dashboard uses Niltsi branding and ORD/LAX/JFK defaults. Main uses
  Spatchin, MEM-only fallback and an explicit KMEM mapping. NOTAM components are
  hard-coded to KORD/KLAX/KJFK; MEM NOTAM support/evidence is absent.
- This app reads/writes `niltsi-airports` only. Preserve main's
  `spatchin-airports` precedence, legacy migration/rollback retention and
  `spatchin-scan-v1` settings during any later integration.
- Prototype HTML/CSS omit SCAN. Main's toolbar, script includes, styles and tests
  must be retained. The proposed Pages artifact also omits `scan.js`.
- Prototype workflows predate Spatchin concurrency groups and the diagnostic
  METAR schedule offset. They add permissions, publication retries and explicit
  Pages invocation. None of these operational changes is approved for main by
  this archive. Keep main's versions, schedules, CNAME and live data intact.
- The workflow proposal has a five-minute metadata schedule while acquisition
  is paused. It is not evidence of a working five-minute live data service.
- Publication helpers/workflows are unverified against current Pages settings,
  branch rules and environment permissions. Never execute publication helpers
  locally merely to inspect this branch.
- Synthetic coverage includes provenance preservation, request-budget edge
  cases, error sanitization, escaped rendering and 32 legacy runway rows without
  wind. Browser layout, SCAN coexistence and MEM integration are not established
  by the prototype VM tests.
- Real lifecycle, cancellation/replacement, schedules, permanent/estimated ends,
  airport/runway association, pagination/completeness, delta reconciliation and
  redistribution requirements still require authoritative review and already
  captured sanitized evidence. Do not fabricate such evidence.

Continue with small, isolated changes based on current main, reusing only the
needed prototype components. Keep live acquisition and deployment out of an
offline interpretation/review change.
