# Native Windows dashboard validation

Validated 2026-11-14 on Windows 25H2 build 26200.9974, Windows PowerShell
5.1.26100.9818, native Python 3.12.10, Node 22.18.0 and installed Chrome
157.0.8103.51 through Playwright. No WSL or aviation-provider requests were used.

- Complete JavaScript/browser suite: **71 passed, 0 failed, 0 skipped** with
  `node --test --test-concurrency=1 --test-reporter=spec tests/*.test.cjs`.
  The local native Python executable supplied the existing tests' `python3`
  subprocesses through a temporary preload; external Node socket connections were
  blocked and browser contexts aborted all non-local page requests.
- Complete offline Python discovery: **92 tests, 90 passed, 2 skipped**. The
  skips are POSIX-only filesystem checks; Windows checks ran natively. A Python
  audit hook rejected socket connection and DNS events.
- New browser checks cover actual production-entry rendering at 1440px/390px,
  image decoding, approved original hashes, inline placement/colspan, independent
  keyboard toggles, focus/state retention, UTC rollover, full-text SCAN roundtrip,
  catalog addition/30-limit/removal/empty-selection persistence, and no page overflow.
- Public MEM reader check reconciles every source record and representation in
  the DOM: **100 notices and 131 separately labelled provider representations**.
  Retrieval is `2026-11-13T02:52:31.040326+00:00`; the screenshots show 35 hours
  since retrieval, UNKNOWN coverage, fixed snapshot and no automatic updates.
- Existing production-entry tests still pass for available, unavailable and
  rejected synthetic snapshots; failed reload retention and recovery remain tested.
- All approved `data/*.json` files have an empty Git diff from the branch base.

One full parallel run encountered a preview-page startup timeout (69/70 passed).
The full suite passed serially afterward without changing the assertion. Use
bounded serial browser execution on this PC; parallel startup reliability is a
remaining test-harness limitation. Earlier focused/browser suites also passed.

Screenshots are emitted outside tracked source through `SPATCHIN_SCREENSHOTS`
(or a temporary directory by default), by `tests/dashboard-design-browser.test.cjs`:

- `synthetic-1440.png`, `synthetic-390.png`: explicitly labelled synthetic
  multi-airport demonstrations with an expanded inline notice.
- `synthetic-scan-1440.png`, `synthetic-scan-390.png`: actual SCAN highlights.
- `public-historical-mem-desktop.png`, `public-historical-mem-mobile.png`:
  unchanged approved public snapshot, distinct from synthetic screenshots.

These tests verify offline rendering and supported behavior, not current weather,
NOTAM applicability/completeness, owner visual acceptance, live acquisition or
deployment. TAF/forecast remain unavailable. Acquisition, history review,
baseline/cutover and collection/publication activation remain separate dependencies.
