# Native Windows dashboard validation

Validated 2026-11-14 on Windows 25H2 build 26200.9974, Windows PowerShell
5.1.26100.9818, native Python 3.12.10, Node 22.18.0 and installed Chrome
157.0.8103.51 through Playwright. No WSL or aviation-provider requests were used.

- Complete JavaScript/browser suite after the owner refinement: **73 passed,
  0 failed, 0 skipped**, serial execution, with
  `node --test --test-concurrency=1 --test-reporter=spec tests/*.test.cjs`.
  The local native Python executable supplied the existing tests' `python3`
  subprocesses through a temporary preload; external Node socket connections were
  blocked and browser contexts aborted all non-local page requests.
- Python behavior is unchanged by this refinement, so Python was not rerun.
  Earlier validation at `a7deefbfd86314b394e6cad31bc7cfb34c4d1c07` ran complete
  offline discovery: **92 tests, 90 passed, 2 POSIX-only skips**, with a Python
  audit hook rejecting socket connection and DNS events.
- New browser checks cover actual production-entry rendering at 1440px/390px,
  image decoding, approved original hashes, inline placement/colspan, independent
  keyboard toggles, focus/state retention, UTC rollover, full-text SCAN roundtrip,
  catalog addition/30-limit/removal/empty-selection persistence, and no page overflow.
- Public MEM reader check reconciles every source record and representation in
  the DOM: **100 notices and 131 separately labelled provider representations**.
  Retrieval is `2026-11-13T02:52:31.040326+00:00`; the updated screenshots show 36 hours
  since retrieval, UNKNOWN coverage, fixed snapshot and no automatic updates.
- Existing production-entry tests still pass for available, unavailable and
  rejected synthetic snapshots; failed reload retention and recovery remain tested.
- All approved `data/*.json` files have an empty Git diff from the branch base.

One full parallel run encountered a preview-page startup timeout (69/70 passed).
The full suite passed serially afterward without changing the assertion. Use
bounded serial browser execution on this PC; parallel startup reliability is a
remaining test-harness limitation. Earlier focused/browser suites also passed.

## Owner refinement on the existing PR #23 branch

Starting head: `a7deefbfd86314b394e6cad31bc7cfb34c4d1c07`. The existing layout,
verified original artwork, approved aviation snapshots and v0.9 are preserved.

- MEM's previously verified diagram URL was recovered from
  `a986cedf13bcde536987848b16bf91d2d1213eff:data/runways.json`:
  `https://aeronav.faa.gov/d-tpp/2611/00253AD.PDF`. Its existing chart dates,
  **2026-10-29 through 2026-11-26**, appear with the header link. This is a
  fixed chart, not a current-cycle check. The link opens a new tab with noopener.
  No verified links were found for ORD, LAX, JFK or catalog-only airports; those
  header links are omitted. No NASR ZIP is substituted and no provider was fetched.
- Expanded runway rows retain every notice and representation in a named,
  focusable scroll region, bounded by 60% of viewport height on desktop / 65%
  on mobile, with an additional rem cap. Sticky runway captions preserve context.
  No truncation, pagination, virtualization, line clamp or source-text changes.
- Tests use 32 synthetic long notices to check PageDown, scroll/focus retention
  through routine rerender, independent unrelated panels, and SCAN forward/backward
  navigation between the final two notices. Selected targets must be below 80% of
  the scroll range and simultaneously visible inside the panel and page viewport.
  Existing SCAN reveal/scrollIntoView behavior handles nested scrolling unchanged.
- Original CR/LF/CRLF, Unicode, markup-like strings and provider representations
  are compared exactly before/after SCAN, rerender and failed reload/recovery.
- One prominent per-airport summary holds retrieval, completeness, historical/
  no-automatic-update context, original collection warning, and reload errors.
  Source/legal text is preserved verbatim. Concise row labels link to that summary
  with aria-describedby; unavailable, no literal matches and explicitly verified
  zero remain distinct. Meaningful warnings remain visible outside disclosures.
- At 390px secondary text is at least 12px in the checked data labels, raw text
  is 14px, provenance controls are 13px, and primary buttons/selects are at least
  44px tall. Regression checks include no page-level horizontal overflow.
- Optional METAR polish was omitted to keep this pass bounded; existing wind,
  raw observation, UTC timestamp, freshness and source presentation remain.

Focused new/screenshot browser checks passed (6/6), followed by the complete
73-test JavaScript/browser run. Initial focused checks caught test expectations
for the old warning location and a partial-keyword expectation; those assertions
now check the consolidated visible summary and exact selected keywords. Native
keyboard scroll animation is allowed to settle before testing at-rest rerender
retention. There are no remaining failures in the serial run.

Screenshots are emitted outside tracked source through `SPATCHIN_SCREENSHOTS`
(or a temporary directory by default), by `tests/dashboard-design-browser.test.cjs`:

- `synthetic-1440.png`, `synthetic-390.png`: explicitly labelled synthetic
  multi-airport dashboards with 32 notices in an expanded bounded panel.
- `synthetic-scan-1440.png`, `synthetic-scan-390.png`: SCAN-selected LOWER_MATCH_B
  near the bottom of that long panel, reached through actual navigation.
- `public-historical-mem-desktop.png`, `public-historical-mem-mobile.png`:
  unchanged approved public snapshot, distinct from synthetic screenshots.

The updated six PNGs are outside source in
`C:\Users\codys\Projects\niltsi\.test-output\pr23-refinement-2026-11-14`.
The earlier screenshots and ZIP remain untouched in the separate original review
directory. The new review ZIP contains only these six PNGs; byte equivalence to
the originals is checked. No screenshot or captured notice is added to this commit.

These tests verify offline rendering and supported behavior, not current weather,
NOTAM applicability/completeness, owner visual acceptance, live acquisition or
deployment. TAF/forecast remain unavailable. Acquisition, history review,
baseline/cutover and collection/publication activation remain separate dependencies.
The FAA initial-content decoder task has **not started**; this UI refinement
implements and claims no decoder/acquisition progress. PR #22 is untouched.
