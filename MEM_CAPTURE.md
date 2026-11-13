# One-time supervised MEM capture

This prepares the next NOTAM MVP step: acquire one location-scoped response, retain
it privately, and run the merged offline normalizer. It does not schedule a request,
publish a snapshot, modify the dashboard, or implement a recurring collector.

The coordinating conversation holds the durable manual request log in
[SPA-26](https://linear.app/spatchin/issue/SPA-26/one-time-mem-capture-manual-request-ledger).
SPA-26 designates PC1 as the sole collector (logical ID `mem-budget`), with every
other FAA NMS client paused. The former Cloud designation is superseded. This manual
exception does not replace or reset the archived ledger or provide a distributed
lock. The script cannot verify Linear state or the identity of its executing task;
those checks belong to the supervisor before execution.

The owner explicitly waived the conservative fixed project quarantine on November
12, 2026. The hard-coded floor is removed; the recorded reservation's not-before,
reservation time, expiry, request accounting and one-auth/one-GET limits remain.
This waiver does not establish account-wide history or override a known service limit.

## Before execution

1. Check the current SPA-26 reservation window and any known provider cooldown.
   The former fixed 2026-11-13T07:44:03.558Z project deadline no longer applies.
   Reservation shape, not-before and expiration are still enforced.
2. Confirm all other clients remained paused. Recheck SPA-26 and the designated
   task. The coordinator must persist one reservation in SPA-26 and read it back
   before preparing the private reservation file. No reservation is issued by this
   document, by tests, or by creating the PR.
3. Record a fresh UUID, reservation time, expiration time, effective not-before
   time and one absolute private output directory outside the checkout in that
   reservation. Its parent directory must already exist. Do not copy the
   reservation to another task or change its directory to retry an attempt.
4. Credentials must already be supplied through environment configuration as
   `FAA_NMS_CLIENT_ID` and `FAA_NMS_CLIENT_SECRET`. Never paste values into chat,
   arguments, logs, source files or the reservation file.

Reservation JSON fields (the values below are deliberately non-executable):

```json
{
  "schema_version": 1,
  "ledger": "SPA-26",
  "state": "TEMPLATE_NOT_AUTHORIZED",
  "airport": "KMEM",
  "collector": "mem-budget",
  "auth_attempt_limit": 1,
  "data_attempt_limit": 1,
  "reservation_id": "SUPERVISOR-ISSUED-UUID",
  "not_before_utc": "EFFECTIVE-ELIGIBILITY-TIME",
  "reserved_utc": "TIME-SAVED-IN-LINEAR",
  "expires_utc": "SUPERVISOR-SET-EXPIRATION",
  "output_dir": "/private/path/reserved-for-this-attempt"
}
```

After these steps, run only in the designated task:

```sh
python3 -B scripts/capture_mem_notams.py --reservation /private/path/reservation.json
```

That checks local reservation shape/time only; it does not authenticate, reserve a
request in Linear, or verify task identity. Only after the supervisor's durable
reservation and readback, execute once with the additional `--execute` switch.
No CLI is run live during preparation or testing.

## Attempt accounting and output

The only network sequence is one token POST, then one GET of
`/nmsapi/v1/notams?location=KMEM` using `nmsResponseFormat: GEOJSON`. Both use the
fixed production host, normal HTTPS/proxy/CA settings, a bounded timeout, and no
redirects or retry loop. No classification, initial-load or follow-up content
request is supported. The token remains in memory; authentication bodies and
upstream error bodies are not saved or printed.

Storage is explicitly platform-specific, described below. Before authentication,
the collector exclusively creates the private attempt directory and persists,
replaces, flushes, and reads back **two zero-attempt receipts**. This exercises
initial publication and replacement before sending anything. Only then is the
one-auth counter persisted before the token POST; the one-data counter is
persisted before the GET. A failure at any stage stops execution. No failed flush
is skipped, no unsafe replacement fallback is attempted, and storage failures
are not automatically retried just to write a final error receipt.

A second invocation using the reserved output path is refused, including after
preflight failure. The directory is deliberately left in place. This is a local
replay guard, not verified shared durable storage:
missing/lost task files must never be interpreted as permission for another attempt.
The durable Linear reservation remains consumed after failures or uncertainty.

A successful inline response produces:

- `response.json`: original response bytes, private and unmodified.
- `normalized.private.json`: separate text representations, raw fields and original
  records from the existing reader adapter. It is **not** published to data/notams.json.
- `receipt.json`: reservation UUID, attempted counts, HTTP statuses, retrieval UTC,
  query context, validated-format request ID when available, hash, returned count
  and final state. This non-secret receipt is suitable for reporting to the coordinator.

A malformed JSON/envelope/content-pointer response stops after the single GET;
raw HTTP-200 bytes remain private for investigation. HTTP failures and redirects
are not retried. Never publish or commit a capture or normalized result without a
separate review. CAPTURED_UNVERIFIED is not a completeness or field-semantics claim.
After the attempt, update SPA-26 from the receipt; do not automatically clear a
reservation or run another capture.

## Supported storage platforms and privacy

`capture_storage.py` selects separate backends; it never uses POSIX mode bits as
proof of Windows privacy. No external Python packages or elevated privileges are
required. The supervisor must provide an existing, trusted local parent directory
outside the checkout. Do not select shared, synced, or externally mutable storage.
The output path cannot contain symlinks or Windows reparse points/junctions.

### Native Windows

The Windows backend is restricted to **local fixed NTFS volumes with persistent
ACL support**. UNC/device paths, remote shares, FAT/exFAT/ReFS and unsupported
storage operations fail closed. It requires native `FileRenameInfoEx` support;
there is no compatibility fallback or WSL path. Other Windows configurations
must pass the offline storage suite before being selected as collector.

- `CreateDirectoryW` and exclusive `CreateFileW(CREATE_NEW)` receive a security
  descriptor **at creation**, before payload bytes are written. It specifies the
  executing Windows account as owner and a protected, non-inherited DACL granting
  full control only to that account and SYSTEM. There are no explicit Everyone,
  Users, Authenticated Users or Administrators grants, and no inherited ACEs.
- Native security-descriptor queries verify owner, protection, ACE type/flags,
  exact trustee set and access masks. Directories, temporary receipt files,
  replaced receipts and captured/normalized files must all pass. `chmod` is not
  used. SYSTEM and privileged administrators can still bypass/take ownership;
  this does not protect against a compromised OS or another process under the
  same account. EFS/disk encryption is not enabled by this code.
- Private files use `FILE_FLAG_WRITE_THROUGH`; `WriteFile` byte counts and
  `FlushFileBuffers` results are checked. Initial receipt publication uses
  same-directory `MoveFileExW(MOVEFILE_WRITE_THROUGH)` without overwrite permission.
  Existing receipt replacement uses `SetFileInformationByHandle(FileRenameInfoEx)`
  with replace and native POSIX-sharing flags, so an already-open reader can
  continue reading the previous complete receipt while new opens see the new one.
  This is a Windows API sharing behavior, not Linux or WSL execution.
- The source is explicitly flushed before and after rename; the destination is
  reopened with write-through semantics, its DACL is verified, and its buffers
  are explicitly flushed again. Final receipt bytes are read back and compared.
  Unsupported rename, ACL, open, short-write, read-back or flush operations stop
  the capture. There is no delete-then-rename replacement window or retry loop.
- A failure may leave `receipt.next` or an earlier receipt. It must not be
  reported as successful persistence. `PRIVATE_STORAGE_FAILED_DO_NOT_RETRY` or
  `FAILURE_RECEIPT_PERSISTENCE_FAILED_DO_NOT_RETRY` means the coordinator must
  treat the reservation as consumed/uncertain even if the final receipt is absent.

See Microsoft's documentation for
[creation-time security attributes](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew),
[native rename sharing flags](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information),
[MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw),
and [FlushFileBuffers](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers).
The unsupported `REPLACEFILE_WRITE_THROUGH` flag is not used. File flush APIs
depend on the filesystem, device and hardware honoring their contracts; these
tests do not certify sudden-power-loss recovery or a disk's volatile cache.

### POSIX

The separate POSIX implementation requires exclusive 0700 directories / 0600
regular files, executing-UID ownership, no symlink path components and no file
hard links. It checks those properties, flushes file content with `fsync`, uses
same-directory atomic `os.replace`, and requires directory `fsync` (including the
parent after attempt-directory creation). Failure is fatal; platforms/filesystems
without these operations are not silently accepted.

This implementation was inspected and retains platform-specific regression
tests, but was **not executed on POSIX during native Windows verification**.
It must be validated on the actual intended POSIX filesystem before use.

## Offline validation

`tests/test_mem_capture.py` injects synthetic credentials and a mock transport. It
covers recorded counters before I/O, reservation start/expiry, exact endpoints, private
output, replay refusal, HTTP/auth/data errors, malformed/content-pointer responses,
redirect refusal, expiration after authentication and sanitized failures.
Run it with the repository's socket-blocked Python harness. It makes no live
requests and issues no reservation. No browser tests are needed for these CLI-only
changes. Live transport remains untested until the separately supervised capture.

### Native Windows verification of this fix

Actually tested on this PC: **Windows 11 25H2, build 26200.9974, x64**, using
**CPython 3.12.10 x64 (MSC v.1943)** on the local NTFS test volume. The complete
Python suite discovered 69 tests: **67 passed; 2 POSIX-only tests were skipped**.
No assertions were disabled for Windows: POSIX mode assertions remain on POSIX,
and Windows uses native owner/DACL assertions and a restricted-token access check.

The Windows tests exercised real ACL creation before any payload write, native
ACL queries, rejection of an actual Everyone grant and removed DACL protection,
denial of file-data read to an explicitly restricted token, exact receipt
replacement/content, concurrent readers observing only complete generations,
and refusal to reuse a failed attempt directory. Injected native ACL, flush,
initial publication and replacement failures verified zero authentication calls;
failure persisting the data counter verified one synthetic auth and zero GETs.
A final receipt failure preserved private original response bytes and earlier
attempt counts without retrying. All credentials, reservations, responses and
output files in tests were synthetic and confined to temporary directories.

Not tested here: POSIX execution, other Windows versions/filesystems, power cuts,
device-cache honesty, privileged/same-account attacks or real FAA transport.
Reparse-path refusal was fault-injected; no actual junction was created. These
limits must not be presented as passed live capture or distributed-ledger tests.
No production reservation was created and no ledger was accessed or reset.

From the checkout, run the full suite using a **native Windows** Python 3.12+
interpreter (no WSL). This PowerShell harness blocks real DNS, sockets and urllib
HTTP, while tests inject synthetic transports:

```powershell
@'
import socket, sys, unittest, urllib.request
def blocked(*args, **kwargs):
    raise AssertionError('NETWORK_BLOCKED_DURING_OFFLINE_TESTS')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
urllib.request.urlopen = blocked
urllib.request.OpenerDirector.open = blocked
result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.discover('tests'))
sys.exit(not result.wasSuccessful())
'@ | py -B -
```

Use the absolute path to the selected native interpreter instead of `py` if the
launcher is not configured. This command runs tests only, not the capture CLI.
