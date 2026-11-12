# Synthetic NOTAM reader preview

Branch: feature/notam-reader-preview, based on origin/main 1e03c6aa947b81991aae363cf1aa3986e501501d.

From the checkout root run `python3 -B -m http.server 8010 --bind 0.0.0.0`.
Open `/preview/` on that local server. The runtime-local address is
http://127.0.0.1:8010/preview/ . Use the environment's port forwarding if accessing
from another machine; no hosted deployment is created.

Only preview/index.html loads fixtures.js and controls.js. Production index.html
loads the reading module with unavailable data and never loads synthetic fixtures.
The preview hook supplies a separate eight-end MEM inventory and seven synthetic
notices. Published data snapshots remain untouched. Existing METAR/ATIS snapshots
are local files; the browser does not acquire provider data.

The reader displays raw source values. Offsets are half-open, zero-based UTF-16
indices into original text, matching JavaScript String.slice. It indexes explicit
RWY/RWYS designations and slash/comma/AND lists against inventory, without reciprocal
expansion or operational interpretation. Unsupported text remains in All notices
and Unmatched / scope unknown. Airport-wide grouping requires scope='airport-wide'
in this local input contract; no real provider field mapping is asserted.

Duplicate IDs remain distinct by snapshot position. Source metadata is preserved.
This preview is not a production transport, real-response adapter, completeness
validator, lifecycle resolver, or acquisition/publication implementation. No
production staleness threshold exists. The stale selector simulates a source
explicitly declaring its snapshot stale; it does not derive notice applicability.
Reader focus and disclosure state are retained during unchanged-snapshot rerenders.

Offline validation from the checkout root:

```sh
python3 -B - <<'PY'
import socket, sys, unittest
def blocked(*args, **kwargs):
    raise AssertionError('Network blocked during offline tests')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('tests'))
sys.exit(not result.wasSuccessful())
PY
node --check app.js
node --check scan.js
node --check notam-reader.js
CHROMIUM_PATH=/usr/bin/chromium node --test tests/*.test.cjs
```

Both browser suites reject external requests. Their Python servers serve only local
files. New reader screenshots are written to /tmp/spatchin-reader-review/1440.png
and /tmp/spatchin-reader-review/390.png. No provider calls or updater CLIs are used.

Review: select 18L and confirm its two explicit-reference notices; 36R has only the
pair notice. Open All, source-declared airport-wide, and unmatched groups. Search
notice wording with SCAN, then navigate a match into a collapsed disclosure. Open
source metadata, focus a summary, and wait through a 30-second rerender. Try the
stale, failed-refresh, and unavailable selectors. Check all eight rows remain.
