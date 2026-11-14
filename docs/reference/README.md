# Owner-approved design originals

These are the original PNG bytes supplied by the owner and verified locally on
2026-11-14. Future sessions should use these repository paths; no attachment
download, image generation or artwork reconstruction is necessary.

| File | Dimensions | Bytes | SHA256 |
| --- | --- | --- | --- |
| `dashboard-reference.png` | 1448 × 1079 | 2,548,136 | `8380cbe3c991e1af1259294fffc2ab90bd538d6be74a1f798b47a5e3d9ca4c09` |
| `../../assets/branding/spatchin-logo-source.png` | 1254 × 1254 RGBA | 734,722 | `ad5e051cfbfc48cfd5faf7c7c4f5048adb683039c9a8319a60840fc485a00144` |

The reference's aviation values, airport selection, dates, counts and v0.10 label
are illustrative only. They are not application data. Spatchin remains v0.9.
The earlier mismatched download was not accepted or used as the specification.

`assets/branding/logo-{16,32,64,128,180}.png` were resized from the full square
original using native Windows System.Drawing with high-quality bicubic
interpolation on transparent RGBA canvases. No cropping, rotation, stretching,
recoloring or replacement artwork was applied. The 64/128 images supply the
contain-sized header, 16/32 are PNG favicons, and 180 is the touch icon. Browser
checks decode the actual image URLs, rather than merely checking HTTP status.
There was no existing favicon/build pipeline to replace.

`c070-airports.csv` is the owner's read-only export of the approved Google Sheet,
provided locally on the same date. `airport-catalog.js` preserves its 130 codes in
source order. This supplies no inferred names, ICAOs, runway inventories or feed
coverage. See [dashboard behavior](../../DASHBOARD.md) for source URL and limits.

## Running-app review

The reference's three columns, prominent identifiers, navy cards, compact header,
ATIS boxes, blue controls and thin borders are implemented. Deliberate differences:

- Required WIP and coverage warnings, full dates and gust components add height.
  Cards grow with actual raw text instead of clipping to the reference dimensions.
- The UTC dropdown shows the supported observation only. TAF/forecast are clearly
  unavailable. No sample configurations, frequencies or weather were copied.
- Airport names/diagram links are limited to verified existing metadata and links.
  No diagram URL or ICAO is invented for visual completeness.
- Each matching source representation remains distinct and complete; provenance
  details are disclosed separately. Expanded notices may require vertical scrolling.
- Synthetic multi-airport screenshots are explicitly labelled test fixtures. The
  separately reviewed public MEM snapshot remains historical with UNKNOWN coverage.

Automated screenshots and comparison are implementation evidence, not owner
visual acceptance or MVP completion.
