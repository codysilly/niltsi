# Offline NOTAM reader adapter

This is a local-file adapter, not production acquisition or a feed loader. Neither
production index.html nor the synthetic preview runs it. Do not publish a capture
or its normalized output without reviewing its preserved metadata. The output
retains the entire original response, which may contain private transport metadata.

Source: [NMS OpenAPI specification](https://drive.google.com/file/d/1Jbf-YgCDZ-TMVoPQyS5LraGXw_ThtM7M/view),
version 1.0.18, revised 2026-02-12. The GeoJSON feature's nested fields are illustrated
by an example, rather than defined by a complete typed schema. The checked-in
fixture is a labeled subset of that example, not a real response capture.

Run from the repository root, using actual capture provenance:

```sh
python3 -B scripts/normalize_notam_snapshot.py /path/to/local-capture.json \
  --airport KMEM --location KMEM --retrieved-utc 'ACTUAL_RETRIEVAL_TIME'
```

The command prints JSON to stdout. It does not access the network, authenticate,
follow content URLs, refresh anything, or change a published snapshot. The caller
supplies the dashboard airport code and actual location filter; this adapter is
only for explicitly location-scoped captures. Airport and query codes must match;
only a US K-prefix alias is supported. It does not partition bulk results
or assert that the queried location establishes a notice's applicability. Timestamp
strings are preserved verbatim; no freshness or active/expired decisions are made.

A non-Success response, reported errors, or missing/non-array inline data.geojson
raises an error. The caller must retain its previous accepted snapshot; the adapter
does not manage that state. A successful empty array remains coverage UNKNOWN.

Each array entry survives, including duplicates and malformed records, with its
source index, original feature, and warnings. Non-string reader fields become null;
their raw values remain in original_feature. No translation replaces missing
condition text. Provider simpleText/formattedText representations remain separate,
labeled with their supplied type and field. Source type and cancelationDate are
retained without interpreting cancellation/replacement relationships. No source
scope value is translated into the reader's airport-wide grouping.

The reader displays and indexes each supplied text representation independently.
Offsets are zero-based UTF-16 offsets into the named representation. A notice is
counted once per runway even if multiple representations reference that runway.
The narrow additional prefix grammar recognizes a complete runway designation
immediately followed by `RWY END`, as illustrated in the specification. It does
not infer the reciprocal runway or recognize arbitrary bare numbers. Unsupported
wording remains accessible through All notices and Unmatched / scope unknown.

## Validation and remaining work

Run the network-blocked Python tests and JavaScript/browser checks documented in
preview/README.md. Focused unit tests are also available with:

```sh
python3 -B -m unittest discover -s tests -p 'test_notam_normalizer.py'
node --test tests/notam-reader.test.cjs
```

Browser tests exercise the adapter output and SCAN at desktop and narrow widths,
with external requests blocked. They must pass in a browser-capable environment
before merge. The implementation session could not launch Chromium because its
required process-singleton socket was denied (Operation not permitted), so no new
browser/layout pass is claimed.

Real response field types/nullability, full original-text semantics, complete
coverage, lifecycle relationships, pagination/truncation, and synchronization
boundaries remain unverified. This adapter does not establish any of these.
