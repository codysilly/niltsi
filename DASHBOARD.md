# Dashboard reader and design

Spatchin remains v0.9 and is not ready for actual use. This reader indexes literal
runway references; it performs no closure, reciprocal, lifecycle or schedule interpretation.
All approved aviation snapshots remain unchanged.

## Notice presentation

Each runway has an independent accessible toggle and a following four-column
detail row. Source records with duplicate IDs remain distinct. Repeated references
within a record count once per runway; provider representations remain labelled
separately and never replace missing original text. SCAN reveals collapsed rows
and preserves CR, LF, CRLF, Unicode and escaped markup.

The airport-wide table contains every retrieved record, including unmatched
notices. Scope indexes refer to that same table instead of duplicating full text.
Inline rows contain only their literal matches. Preserved JSON metadata is excluded
from SCAN to avoid searching another serialized copy of every source string.

Sorting is ascending: explicit four-digit source year first (records without one
follow), then domestic, international, other, missing identifier families. Domestic
month and serial are numeric. International series, source suffix year and serial
use deterministic natural-numeric comparison, without expanding two-digit years.
Other identifiers use natural numeric tokens; ties preserve source order. Dates do
not supply missing years. Neither sorting nor matching changes original records.

Unavailable collections, unknown coverage and no literal matches have different
labels. Empty collections do not establish zero coverage. A display of explicitly
verified zero requires all of: an empty notices array, `completeness: "VERIFIED"`,
`zero_verified: true` and a nonempty `verification_reference`. These are explicit
local publication assertions, **not inferred FAA fields**. No current production
snapshot supplies this evidence. Elapsed snapshot age is shown without inventing
a freshness threshold. Existing collection warnings survive failed reloads.

## Airport and time controls

`airport-catalog.js` contains the owner's 130-code export of approved C070
Sheet1!A1:A130, supplied locally on 2026-11-14, from
https://docs.google.com/spreadsheets/d/17J10F734gVQ5XfTDkPzEoYQZPZMvZ1CUmb8Ghw05fmA/edit.
The export is the selection authority, not verified airport metadata. For example,
DJT is retained verbatim, not corrected by guessing. Only the existing four airport
ICAO/name mappings and published feeds are available. Other selected airports show
unavailable metadata/data; no K-prefix mapping or provider lookup is attempted.
Existing saved selections remain usable; new additions must be catalog members.
Maximum 30 total, MEM is removable, and an intentional empty selection persists.

The per-card UTC dropdown contains the actual published observation with its UTC
date, or observation unavailable. Forecast is disabled. Each card retains a valid
selection through rerender; a new observation replaces an obsolete observed-time
option. There is no forecast timeline, TAF feed, or forecast computation. Existing
METAR calculations/65-minute freshness and ATIS explicit-clause extraction remain.

## Remaining acquisition dependencies

Selected-airport raw NOTAM population still needs the SPA-10 architecture's initial
decoder, justified baseline/cutover, reviewed history/accounting and an authorized
collection-to-publication path. PR #22 is separate. No collector, transport,
scheduler, accounting or deployment changes are part of this dashboard work.
