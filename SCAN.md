# SCAN — Global Search & Highlight

SCAN adds a compact global search toolbar to the Spatchin dashboard (currently
branded Spatchin v0.9). Enter comma-separated literal keywords. Each term has a
persistent color; searches ignore case unless **Case sensitive** is checked.
**Highlight** disables highlighting without clearing saved terms. **Next** and
**Previous** wrap through matches in document order. Enter and Shift+Enter in
the search field also navigate. Navigation scrolls to the match and opens its
containing disclosures while keeping keyboard focus on the search controls.
The counter shows the selected match and total, including collapsed content.

The toolbar starts collapsed for first-time users. Its compact bar shows a
search icon, SCAN label, active keyword count, and expansion arrow. Click the
bar or focus it with Tab and press Enter or Space to expand/collapse. Hidden
search controls are removed from keyboard navigation. The active keyword count
ignores empty/duplicate terms using the current case preference, and is zero
when highlighting is off. Expand the toolbar to edit terms or navigate matches.
Collapsing keeps highlights, colors, terms, and the selected match intact;
dashboard controls continue working. Expanded/collapsed state is saved alongside
existing preferences. Old saved preferences default to collapsed. Match selection
survives toggling within the page; it resets on page refresh.

Preferences and assigned colors use the localStorage key `spatchin-scan-v1`,
independently of `niltsi-airports`. If storage is unavailable or corrupt, SCAN
continues with session defaults. No external requests or services are involved.

## Architecture and future NOTAM integration

`scan.js` works on text nodes and creates `<mark>` elements with DOM APIs. It
never interpolates search terms into HTML. Literal regular-expression escaping
prevents keywords from becoming executable patterns. Existing elements, event
listeners, and control values remain in place. Mutation observation reapplies
highlights after content changes, including the dashboard's existing refreshes;
SCAN disconnects observation during its own writes to avoid feedback loops.
The live UTC clock and SCAN toolbar are excluded to avoid needless rescans.

Future NOTAM and TAF panels inserted anywhere in the document body automatically
participate, without changing `app.js` or assuming panel-specific markup. Standard
`<details>` and `[hidden]` containers open on navigation; controls linked through
`aria-controls` get their `aria-expanded` state updated. The existing airport
`data-expand` controls are supported by a small compatibility adapter.
Custom disclosure components can handle the bubbling `scan:reveal` event from
the selected mark to reveal additional UI. Use `data-scan-ignore` on a subtree
that should not be searched. Inputs, editable content, script and style elements
are excluded. Keep NOTAM filtering in the future NOTAM integration; SCAN only
highlights and navigates, and never filters or rewrites source records.

The separate Windows NOTAM development tree is untouched. Reconcile the branches
later by retaining the SCAN toolbar/script includes and stylesheet additions;
NOTAM content should require no SCAN-specific rendering changes. No new TAF or
NOTAM data connection is included here.

## Matching rules and limits

Empty and duplicate terms are ignored (duplicates respect the case preference).
Overlapping terms use the longest match, with input order breaking ties; matches
are non-overlapping. Spaces inside a term are literal. Phrases match within a
text node, not across separate HTML elements. Shadow DOM, iframes, CSS-generated
text, form values, and virtualized content absent from the DOM are outside scope.
A custom disclosure using only CSS classes should handle `scan:reveal`.

## Validation

Run from the repository root:

```sh
node --check scan.js
node --test tests/scan.test.cjs
```

The browser suite uses the local Playwright installation, Python 3, and
Playwright's installed Chromium on macOS or Linux. Install the browser with
`npx playwright install chromium` if needed, or set `CHROMIUM_PATH` to use a
custom executable (for example, `/usr/bin/chromium`). It serves bundled
files on port 8766 and blocks all requests outside that local origin. Tests cover
literal/case/overlap matching, colors, persistence, disabled and unavailable
storage, navigation and expansion, dynamic content, DOM safety, existing airport
controls and local data refreshes, and a narrow toolbar viewport. It captures
`scan-desktop.png` and `scan-mobile.png` in a unique `spatchin-scan-*` directory
under the operating system's temporary directory.

The suite also covers default collapsed state, Enter/Space and Tab accessibility,
keyword counts, preserved highlights and selection, disclosure persistence, and
migration of saved preferences without an expanded-state field.
