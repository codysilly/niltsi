"""Single-PC collector engine. Injected transport only; private accepted outbox only.

No HTTP implementation, environment reads, state initializer, scheduler, Git writer
or public-file writer. Existing production accounting is never opened implicitly.
"""
import base64
import copy
from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import uuid

from capture_storage import platform_storage
from normalize_notam_snapshot import normalize
from nms_refresh_contract import (RefreshError, Request, DecodedInitial, auth_request,
                                  initial_request, delta_request, content_request,
                                  instant, stamp)

SCHEMA = '''
CREATE TABLE state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), document TEXT NOT NULL);
CREATE TABLE attempts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, reserved TEXT NOT NULL,
                       last_io TEXT NOT NULL, status TEXT NOT NULL, error TEXT);
CREATE TABLE calls (attempt TEXT NOT NULL, stage TEXT NOT NULL, at TEXT NOT NULL,
                    PRIMARY KEY(attempt,stage));
CREATE TABLE responses (attempt TEXT NOT NULL, stage TEXT NOT NULL, at TEXT NOT NULL,
                        body BLOB NOT NULL, PRIMARY KEY(attempt,stage));
PRAGMA user_version=1;
'''
# Schema is documentation for the separately approved history importer, not an
# initializer. Tests provision synthetic stores explicitly in temporary directories.


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


class Store:
    def __init__(self, root, *, account, storage=None):
        self.root = Path(root)
        repository = Path(__file__).resolve().parents[1]
        resolved = self.root.resolve()
        if (not self.root.is_absolute() or resolved == repository or repository in resolved.parents
                or not isinstance(account, str) or not account):
            raise RefreshError('HISTORY_OR_STORAGE_UNAVAILABLE')
        self.account = account
        self.storage = storage or platform_storage()

    @contextmanager
    def connect(self):
        db = None
        try:
            # Missing files/history are never initialized or replaced. PERSIST keeps
            # the pre-created private rollback journal, rather than recreating ACLs.
            for path in (self.root, self.root/'account.sqlite3', self.root/'account.sqlite3-journal'):
                self.storage.validate_location(path)
                self.storage.verify_private(path)
            db = sqlite3.connect((self.root/'account.sqlite3').as_uri()+'?mode=rw',
                                 uri=True, timeout=2, isolation_level=None)
            if db.execute('PRAGMA journal_mode=PERSIST').fetchone()[0] != 'persist':
                raise ValueError()
            db.execute('PRAGMA synchronous=EXTRA')
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise ValueError()
            if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError()
            # Hot-journal recovery must not silently recreate a less-private file.
            for path in (self.root/'account.sqlite3', self.root/'account.sqlite3-journal'):
                self.storage.verify_private(path)
            yield db
        except RefreshError:
            raise
        except sqlite3.OperationalError as error:
            code = 'BUSY' if (getattr(error, 'sqlite_errorcode', 0) & 255) in (5, 6) else 'HISTORY_OR_STORAGE_UNAVAILABLE'
            raise RefreshError(code) from None
        except Exception:
            raise RefreshError('HISTORY_OR_STORAGE_UNAVAILABLE') from None
        finally:
            if db is not None:
                db.close()

    @contextmanager
    def transaction(self, db):
        for path in (self.root/'account.sqlite3', self.root/'account.sqlite3-journal'):
            self.storage.verify_private(path)
        db.execute('BEGIN IMMEDIATE')
        try:
            yield
            db.execute('COMMIT')
            for path in (self.root/'account.sqlite3', self.root/'account.sqlite3-journal'):
                self.storage.verify_private(path)
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise

    def load(self, db):
        try:
            state = json.loads(db.execute('SELECT document FROM state WHERE singleton=1').fetchone()[0])
            if (state['schema'] != 1 or state['account'] != self.account
                    or state['history_verified'] is not True or not isinstance(state['catalog'], dict)
                    or state['scope'] != 'all-classifications' or state['airport'] != 'KMEM'):
                raise ValueError()
            instant(state['history_complete_through'])
            if state['watermark'] is not None:
                instant(state['watermark'])
            base64.b64decode(state['published_b64'], validate=True)
            for table, counter in [('attempts', 'attempt_count'), ('calls', 'call_count'),
                                   ('responses', 'response_count')]:
                if type(state[counter]) is not int or db.execute('SELECT count(*) FROM '+table).fetchone()[0] != state[counter]:
                    raise ValueError()
            return state
        except Exception:
            raise RefreshError('HISTORY_OR_STORAGE_UNAVAILABLE') from None

    @staticmethod
    def save(db, state):
        db.execute('UPDATE state SET document=? WHERE singleton=1', (encode(state).decode(),))

    def snapshot(self):
        """The last accepted private publication handoff, not a live web writer."""
        with self.connect() as db:
            return base64.b64decode(self.load(db)['published_b64'], validate=True)

    def reserve(self, db, mode, now):
        if mode not in ('bulk', 'delta'):
            raise RefreshError('INVALID_REQUEST')
        with self.transaction(db):
            state = self.load(db)
            if state['active'] is not None or state['recovery'] is not None:
                raise RefreshError('RECOVERY_REQUIRED')
            if instant(state['history_complete_through']) > now:
                raise RefreshError('CLOCK_ROLLBACK')
            if mode == 'delta':
                if not state['baseline'] or state['watermark'] is None:
                    raise RefreshError('BASELINE_REQUIRED')
                delta_request(state['watermark'], now)
            rows = db.execute('SELECT kind,reserved,last_io FROM attempts').fetchall()
            for kind, reserved, last_io in rows:
                if kind not in ('bulk', 'delta', 'other-data'):
                    raise RefreshError('HISTORY_OR_STORAGE_UNAVAILABLE')
                at = max(instant(reserved), instant(last_io))
                if at > now:
                    raise RefreshError('CLOCK_ROLLBACK')
                if now-at < timedelta(minutes=3) or (mode == 'bulk' and kind == 'bulk' and now-at < timedelta(hours=24)):
                    raise RefreshError('BUDGET_WAIT')
            attempt = uuid.uuid4().hex
            at = stamp(now)
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,NULL)', (attempt, mode, at, at, 'RESERVED'))
            state['active'] = attempt
            state['attempt_count'] += 1
            self.save(db, state)
        # Commit completed before auth/data I/O. A restart now finds a durable
        # unresolved reservation; it never interprets a crashed attempt as unused.
        return attempt, state

    def call_reserved(self, db, attempt, stage, now):
        with self.transaction(db):
            state = self.load(db)
            if state['active'] != attempt:
                raise RefreshError('RECOVERY_REQUIRED')
            previous = db.execute('SELECT last_io FROM attempts WHERE id=?', (attempt,)).fetchone()
            if instant(previous[0]) > now:
                raise RefreshError('CLOCK_ROLLBACK')
            db.execute('INSERT INTO calls VALUES (?,?,?)', (attempt, stage, stamp(now)))
            db.execute('UPDATE attempts SET last_io=?,status=? WHERE id=?', (stamp(now), 'IO_RESERVED', attempt))
            state['call_count'] += 1
            self.save(db, state)

    def received(self, db, attempt, stage, body, now):
        with self.transaction(db):
            state = self.load(db)
            last = db.execute('SELECT last_io FROM attempts WHERE id=?', (attempt,)).fetchone()[0]
            if instant(last) > now:
                raise RefreshError('CLOCK_ROLLBACK')
            db.execute('INSERT INTO responses VALUES (?,?,?,?)', (attempt, stage, stamp(now), body))
            db.execute('UPDATE attempts SET last_io=? WHERE id=?', (stamp(now), attempt))
            state['response_count'] += 1
            self.save(db, state)

    def failed(self, db, attempt, code, now):
        with self.transaction(db):
            state = self.load(db)
            if state['active'] != attempt:
                raise RefreshError('RECOVERY_REQUIRED')
            # Prior accepted catalog, publication bytes and watermark stay intact.
            state['active'] = None
            if code in ('DELTA_GAP', 'CONFLICT', 'CLOCK_ROLLBACK', 'BASELINE_CUTOVER_REQUIRED'):
                state['recovery'] = code
            self.save(db, state)
            last = db.execute('SELECT last_io FROM attempts WHERE id=?', (attempt,)).fetchone()[0]
            accounted = stamp(max(instant(last), now))
            db.execute('UPDATE attempts SET status=?,error=?,last_io=? WHERE id=?',
                       ('FAILED_ACCOUNTED', code, accounted, attempt))


def payload_features(payload):
    if (not isinstance(payload, dict) or payload.get('status') != 'Success'
            or payload.get('errors') not in (None, []) or not isinstance(payload.get('data'), dict)
            or not isinstance(payload['data'].get('geojson'), list)):
        raise RefreshError('INVALID_RESPONSE')
    return payload['data']['geojson']


def source_record(feature):
    try:
        record = feature['properties']['coreNOTAMData']['notam']
        if not isinstance(record['id'], str) or not record['id']:
            raise ValueError()
        instant(record['lastUpdated'])
        return record
    except Exception:
        raise RefreshError('INVALID_RECORD') from None


def mem_association(record):
    # Explicit source fields only; never runway text, FIR, radius or lifecycle.
    icao, local = record.get('icaoLocation'), record.get('location')
    if icao == 'KMEM' and local not in (None, 'MEM', 'KMEM'):
        raise RefreshError('CONFLICT')
    if local in ('MEM', 'KMEM') and icao not in (None, 'KMEM'):
        raise RefreshError('CONFLICT')
    return icao == 'KMEM' or local in ('MEM', 'KMEM')


def updated_catalog(prior, features, received):
    catalog = copy.deepcopy(prior)
    for feature in features:
        record = source_record(feature)
        old = catalog.get(record['id'])
        if old is not None:
            previous = source_record(old['feature'])
            if feature == old['feature']:
                continue  # Replayed overlap is idempotent; raw duplicates stay in responses.
            if instant(record['lastUpdated']) <= instant(previous['lastUpdated']):
                raise RefreshError('CONFLICT')
            if mem_association(record) != mem_association(previous):
                raise RefreshError('CONFLICT')
        mem_association(record)
        catalog[record['id']] = {'feature': copy.deepcopy(feature), 'received_utc': received}
    # No absent-ID deletion or cancellation/expiry filtering.
    return catalog


def reader_snapshot(catalog, retrieved):
    selected = [entry for entry in catalog.values() if mem_association(source_record(entry['feature']))]
    # Reuse field/representation mapping only. This local partition is not falsely
    # described as an upstream location-filtered request in the output provenance.
    mapped = normalize({'status': 'Success', 'data': {'geojson': [e['feature'] for e in selected]}},
                       airport='KMEM', retrieved_utc=retrieved, query_context={'location': 'KMEM'})
    notices = mapped['airports']['KMEM']['notices']
    for notice, entry in zip(notices, selected):
        notice['retrieved_utc'] = entry['received_utc']
    unresolved = sum(not any(source_record(e['feature']).get(k) for k in ('icaoLocation', 'location'))
                     for e in catalog.values())
    return {'retrieved_utc': retrieved,
            'publication': {'source': 'NMS-API snapshot', 'automatic_updates': False,
                            'mode': 'private collector outbox; explicit publication required',
                            'association': 'source-reported location fields', 'unresolved_records': unresolved,
                            'source_index_basis': 'local MEM array; original batch order retained privately'},
            'airports': {'KMEM': {'completeness': 'UNKNOWN', 'warning':
                'Fixed snapshot; no automatic public updates. Complete airport coverage is UNKNOWN. '
                'Source locations and lifecycle are unverified; original notices are retained. '
                'Reported times do not establish current operational validity. Closures are not assessed.',
                'notices': notices}}}


def collect(store, *, mode, credentials, transport, clock, decode_initial=None,
            before_accept=None, max_bytes=64*1024*1024):
    """One all-classification pull with explicit dependencies, never a loop or retry.

    transport(Request, limit) -> (HTTP status, headers, bytes), exactly one call.
    decode_initial(bytes, stage) -> DecodedInitial; no default archive/AIXM guesses.
    before_accept(candidate_bytes) is a side-effect-free publication preflight.
    Accepted bytes and cursor commit together in SQLite. Public files are untouched.
    """
    if mode not in ('bulk', 'delta') or not callable(transport) or max_bytes < 1:
        raise RefreshError('INVALID_REQUEST')
    auth = auth_request(credentials)
    if mode == 'bulk' and not callable(decode_initial):
        raise RefreshError('DECODER_REQUIRED')
    attempt = None
    with store.connect() as db:
        try:
            attempt, prior = store.reserve(db, mode, clock())

            def send(request, token=None):
                now = clock()
                store.call_reserved(db, attempt, request.stage, now)
                headers = dict(request.headers)
                if token is not None:
                    headers['Authorization'] = 'Bearer '+token
                prepared = Request(request.stage, request.url, headers, request.body, request.method)
                try:
                    status, response_headers, body = transport(prepared, 65536 if request.stage == 'auth' else max_bytes)
                except Exception:
                    raise RefreshError('TRANSPORT_FAILED') from None
                if not isinstance(body, bytes) or len(body) > (65536 if request.stage == 'auth' else max_bytes):
                    raise RefreshError('RESPONSE_TOO_LARGE')
                if status != 200:
                    # No error bodies, auth response, headers, tokens or URLs persisted.
                    raise RefreshError('AUTH_FAILED' if request.stage == 'auth' else 'HTTP_FAILED')
                if request.stage != 'auth':
                    store.received(db, attempt, request.stage, body, clock())
                return body

            try:
                token = json.loads(send(auth))['access_token']
                if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                    raise ValueError()
            except RefreshError:
                raise
            except Exception:
                raise RefreshError('AUTH_FAILED') from None
            started = clock()
            if mode == 'bulk':
                request = initial_request()
                body = send(request, token)
                try:
                    envelope = json.loads(body)
                    if envelope['status'] != 'Success' or envelope.get('errors') not in (None, []):
                        raise ValueError()
                    path = envelope['data']['url']
                except Exception:
                    raise RefreshError('INVALID_RESPONSE') from None
                body = send(content_request(path, stamp(started), clock()), token)
                try:
                    decoded = decode_initial(body, request.stage)
                except Exception:
                    raise RefreshError('INVALID_RESPONSE') from None
                if not isinstance(decoded, DecodedInitial):
                    raise RefreshError('BASELINE_CUTOVER_REQUIRED')
                lower = instant(decoded.coverage_since)
                if lower > started or started-lower > timedelta(hours=24):
                    raise RefreshError('BASELINE_CUTOVER_REQUIRED')
                delta_request(decoded.coverage_since, clock())
                watermark, payload = decoded.coverage_since, decoded.payload
            else:
                request = delta_request(prior['watermark'], started)
                try:
                    payload = json.loads(send(request, token))
                except RefreshError:
                    raise
                except Exception:
                    raise RefreshError('INVALID_RESPONSE') from None
                # Re-query the previous request start, never max(record time) or
                # completion time. Whole-second flooring deliberately replays overlap.
                # One second of CLIENT replay overlap protects a whole-second
                # boundary regardless of inclusive/exclusive provider filtering.
                watermark = stamp(started.replace(microsecond=0)-timedelta(seconds=1))
            received = stamp(clock())
            features = payload_features(payload)
            catalog = updated_catalog(prior['catalog'], features, received)
            candidate = encode(reader_snapshot(catalog, received))
            with store.transaction(db):
                state = store.load(db)
                if state['active'] != attempt:
                    raise RefreshError('RECOVERY_REQUIRED')
                if before_accept is not None:
                    try:
                        before_accept(candidate)
                    except Exception:
                        raise RefreshError('PUBLICATION_FAILED') from None
                state.update(catalog=catalog, watermark=watermark, baseline=True, active=None,
                             published_b64=base64.b64encode(candidate).decode())
                store.save(db, state)
                db.execute('UPDATE attempts SET status=? WHERE id=?', ('ACCEPTED', attempt))
            return {'attempt': attempt, 'status': 'ACCEPTED_PRIVATE_OUTBOX',
                    'records_received': len(features), 'coverage': 'UNKNOWN'}
        except Exception as error:
            code = (str(error) if isinstance(error, RefreshError) else
                    'BUSY' if (getattr(error, 'sqlite_errorcode', 0) & 255) in (5, 6) else 'LOCAL_FAILURE')
            if attempt is not None:
                try:
                    store.failed(db, attempt, code, clock())
                except Exception:
                    # A crash/ambiguous commit is not treated as an unused allowance.
                    # No public-file promotion exists in this engine.
                    raise RefreshError('PERSISTENCE_UNCERTAIN') from None
            raise RefreshError(code) from None
