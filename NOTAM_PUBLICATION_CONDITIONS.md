# NOTAM snapshot publication review

Evidence checked November 13, 2026. This records the saved account documentation;
it does not publish notices or establish that no later account terms exist.

## Source documents and display conditions

The saved [October 7 account terms](https://drive.google.com/file/d/18oD3SBPwhzNjWfFs1vt_vYMNnvBZhbXL/view),
under Using our Services, permit redistribution but prohibit characterizing
redistributed information as FAA data. Preserve legal notices supplied with the
service. The limited contract exception concerns communications with FAA or its
support contractors; it is not a general public attribution exception.

Use a delivery-source label such as “NMS-API snapshot” or “NMS service response”.
Preserve original notice text and provider representations, including legal
notices; do not rewrite notice text to change attribution. The existing adapter's
“NMS service response; field semantics unverified” label is neutral. A separate
link to FAA NOTAM Search and FAA attribution for NASR runway data are distinct
from characterizing redistributed NOTAM content as FAA data.

No prescribed public disclaimer sentence or mandatory attribution line was found
in this saved edition. The terms incorporate service-specific WSDDs/additional
terms and provide for emailed updates; check any applicable newer account notice.

## Coverage and freshness

The [OpenAPI 1.0.18 specification, revised February 12, 2026](https://drive.google.com/file/d/1Jbf-YgCDZ-TMVoPQyS5LraGXw_ThtM7M/view)
documents no pagination parameter, total-count field, or 100-record cap for the
filtered NOTAM endpoint. Its GeoJSON array has no maxItems restriction. The
documented maximum of 100 concerns geospatial radius in nautical miles.
Absence of a documented cap does not prove complete coverage. Do not invent a
pagination request or treat bulk content retrieval as continuation of this query.

A fixed snapshot must show its original retrieval time, UNKNOWN coverage and
that it does not automatically update. A successful local reload is a check of
the published file, not a new provider retrieval. Reload failures must leave the
snapshot's warnings visible alongside the reload error. Neither effective dates
nor a recent file check establish current operational validity.

UNKNOWN disclosure does not satisfy a promise to provide every airport NOTAM.
Lifecycle, schedule and closure interpretation remain outside the reading MVP.

## Exact candidate review before publication

- Verify the candidate against its approved manifest, including size and hash.
- Preserve every selected notice and representation exactly, including CR/CRLF.
- Review displayed source labels and supplied legal notices against the terms.
- Exclude credentials, transport/authentication and local execution metadata.
- Verify retrieval time and collection warnings on successful load, failed reload
  and recovery, including desktop and narrow layouts.
- Review the actual candidate and screenshots with the owner before public upload.

Real responses, local paths, receipts, reservation files, private previews and
private reports do not belong in a source-only PR. Publication requires a
separately reviewed data change; no capture, scheduler, or provider request is
needed to review the source warning fix.
