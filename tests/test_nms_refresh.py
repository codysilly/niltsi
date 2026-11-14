"""Synthetic stores only. No production paths, environment credentials or networking."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
from capture_storage import platform_storage
from nms_refresh import Store, SCHEMA, collect, encode
from nms_refresh_contract import (RefreshError, DecodedInitial, auth_request,
                                  initial_request, delta_request, content_request)

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
CREDENTIALS = {'client_id': 'SYNTHETIC-CLIENT', 'client_secret': 'SYNTHETIC-SECRET'}
TOKEN = 'SYNTHETIC-ACCESS-TOKEN'


def feature(identifier='SYNTHETIC-ID', updated='2026-01-02T11:59:00Z', text='SYNTHETIC RWY 18L\r\nsecond\rline'):
    return {'type': 'Feature', 'geometry': None, 'unrecognized': {'retained': True},
            'properties': {'coreNOTAMData': {'notam': {
                'id': identifier, 'number': 'SYNTHETIC-01', 'text': text,
                'issued': '2026-01-01T00:00:00Z', 'effectiveStart': '2027-01-01T00:00:00Z',
                'effectiveEnd': 'PERM', 'lastUpdated': updated, 'location': 'MEM',
                'icaoLocation': 'KMEM', 'type': 'UNINTERPRETED', 'schedule': 'UNVERIFIED'},
                'notamEvent': {'scenario': 'UNVERIFIED'}, 'notamTranslation': [
                    {'type': 'SYNTHETIC', 'simpleText': 'SYNTHETIC RWY 36R\r\ntranslation',
                     'formattedText': 'SYNTHETIC <unsafe>\rrepresentation'}]}}}


def envelope(features):
    return {'status': 'Success', 'data': {'geojson': features}, 'unrecognized_envelope': 'retained privately'}


def synthetic_store(parent):
    """Only test code initializes a ledger, exclusively under TemporaryDirectory."""
    storage = platform_storage(); root = Path(parent)/'synthetic-account'
    storage.create_attempt(root)
    for name in ['account.sqlite3', 'account.sqlite3-journal']:
        storage.private_write(root/name, b'')
    previous = encode({'retrieved_utc': '2020-01-01T00:00:00Z',
                       'airports': {'KMEM': {'completeness': 'UNKNOWN', 'notices': []}}})
    state = {'schema': 1, 'account': 'SYNTHETIC-ACCOUNT', 'history_verified': True,
             'history_complete_through': (NOW-timedelta(days=1)).isoformat(),
             'scope': 'all-classifications', 'airport': 'KMEM', 'baseline': False,
             'watermark': None, 'active': None, 'recovery': None, 'catalog': {},
             'published_b64': base64.b64encode(previous).decode(),
             'attempt_count': 0, 'call_count': 0, 'response_count': 0}
    db = sqlite3.connect(root/'account.sqlite3', isolation_level=None)
    try:
        db.execute('PRAGMA journal_mode=PERSIST'); db.execute('PRAGMA synchronous=EXTRA')
        db.executescript(SCHEMA)
        db.execute('INSERT INTO state VALUES (1,?)', (encode(state).decode(),))
    finally:
        db.close()
    return Store(root, account='SYNTHETIC-ACCOUNT', storage=storage)


class Transport:
    def __init__(self, store, payload, clock):
        self.store, self.payload, self.clock = store, payload, clock
        self.requests = []
        self.fail_stage = None
        self.crash_stage = None
        self.slow_auth = None

    def __call__(self, request, limit):
        # Separate connection proves reservation/call accounting committed BEFORE I/O.
        with self.store.connect() as db:
            state = self.store.load(db)
            self_id = state['active']
            assert self_id is not None
            assert db.execute('SELECT count(*) FROM calls WHERE attempt=? AND stage=?',
                              (self_id, request.stage)).fetchone()[0] == 1
        self.requests.append(request)
        if request.stage == self.crash_stage:
            raise SystemExit('SYNTHETIC PROCESS INTERRUPTION')
        if request.stage == self.fail_stage:
            raise OSError('SYNTHETIC-SECRET arbitrary upstream detail')
        if request.stage == 'auth':
            if self.slow_auth:
                self.slow_auth()
            return 200, {}, encode({'access_token': TOKEN, 'client_id': CREDENTIALS['client_id']})
        if request.stage == 'initial-aixm-soap':
            return 200, {}, encode({'status': 'Success', 'data': {'url': '/nmsapi/v1/content/SYNTHETIC'}})
        return 200, {}, encode(self.payload)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = synthetic_store(self.temp.name); self.now = NOW

    def run_pull(self, mode='bulk', features=None, **kwargs):
        transport = Transport(self.store, envelope(features if features is not None else [feature()]), lambda:self.now)
        options = dict(mode=mode, credentials=CREDENTIALS, transport=transport,
                       clock=lambda:self.now, decode_initial=lambda body, stage:
                       DecodedInitial(json.loads(body), (self.now-timedelta(seconds=60)).isoformat()))
        options.update(kwargs)
        result = collect(self.store, **options)
        return result, transport

    def state(self):
        with self.store.connect() as db:
            return self.store.load(db)

    def seed_baseline(self):
        self.run_pull(); self.now += timedelta(minutes=3)

    def assert_code(self, code, call):
        with self.assertRaises(RefreshError) as error:
            call()
        self.assertEqual(str(error.exception), code)

    def test_documented_plans_and_content_origin_expiry_guards(self):
        initial = initial_request(); self.assertEqual(urlsplit(initial.url).path, '/nmsapi/v1/notams/il')
        self.assertEqual(parse_qs(urlsplit(initial.url).query), {'allowRedirect': ['false']})
        self.assertNotIn('nmsResponseFormat', initial.headers)
        geo = initial_request('DOMESTIC')
        self.assertEqual(geo.headers['nmsResponseFormat'], 'GEOJSON')
        self.assertEqual(parse_qs(urlsplit(geo.url).query), {'classification': ['DOMESTIC'], 'allowRedirect': ['false']})
        delta = delta_request('2026-01-02T11:59:00.999Z', NOW)
        self.assertEqual(parse_qs(urlsplit(delta.url).query), {'lastUpdatedDate': ['2026-01-02T11:59:00Z']})
        for path in ['/v1/content/SYNTHETIC', '/nmsapi/v1/content/SYNTHETIC%2Ftoken=']:
            self.assertTrue(content_request(path, NOW.isoformat(), NOW).url.startswith('https://api-nms.aim.faa.gov/nmsapi/v1/content/'))
        for path in ['https://evil.invalid/v1/content/test', '//evil.invalid/a', '/v1/content/../auth',
                     '/v1/content/token?key=secret', '/v1/content/token#fragment', '/v1/content/token\r\n']:
            self.assert_code('INVALID_CONTENT_PATH', lambda:content_request(path, NOW.isoformat(), NOW))
        self.assert_code('CONTENT_EXPIRED', lambda:content_request('/v1/content/test', NOW.isoformat(), NOW+timedelta(minutes=5)))
        self.assert_code('DELTA_GAP', lambda:delta_request((NOW-timedelta(hours=24,seconds=1)).isoformat(), NOW))
        self.assert_code('CLOCK_ROLLBACK', lambda:delta_request((NOW+timedelta(seconds=1)).isoformat(), NOW))
        self.assert_code('INVALID_REQUEST', lambda:initial_request('ALL'))

    def test_raw_fields_representations_and_acl_preserved_no_record_cap(self):
        features = [feature('SYNTHETIC-'+str(i)) for i in range(151)]
        result, transport = self.run_pull(features=features)
        self.assertEqual(result['records_received'], 151)
        candidate = json.loads(self.store.snapshot()); notices=candidate['airports']['KMEM']['notices']
        self.assertEqual(len(notices), 151)
        self.assertEqual([n['original_feature'] for n in notices], features)
        self.assertEqual(notices[0]['text'], features[0]['properties']['coreNOTAMData']['notam']['text'])
        self.assertEqual(len(notices[0]['formatted_texts']), 2)
        self.assertEqual(candidate['publication']['source'], 'NMS-API snapshot')
        self.assertEqual(candidate['airports']['KMEM']['completeness'], 'UNKNOWN')
        self.assertFalse(candidate['publication']['automatic_updates'])
        self.assertEqual([r.stage for r in transport.requests], ['auth','initial-aixm-soap','content'])
        with self.store.connect() as db:
            body=db.execute('SELECT body FROM responses WHERE stage="content"').fetchone()[0]
            self.assertEqual(json.loads(body), envelope(features))
        for path in [self.store.root,self.store.root/'account.sqlite3',self.store.root/'account.sqlite3-journal']:
            self.store.storage.verify_private(path)

    def test_delta_merges_without_deleting_absent_expired_or_cancelled_records(self):
        self.run_pull(features=[feature('A'),feature('B')]); prior=self.state()
        self.now += timedelta(minutes=3)
        changed=feature('A','2026-01-02T12:01:00Z','SYNTHETIC changed')
        changed['properties']['coreNOTAMData']['notam'].update(type='C',effectiveEnd='2000-01-01T00:00:00Z')
        _,transport=self.run_pull('delta',[changed])
        self.assertEqual(set(self.state()['catalog']), {'A','B'})
        notices=json.loads(self.store.snapshot())['airports']['KMEM']['notices']
        self.assertEqual(notices[0]['original_feature'],changed)
        self.assertEqual(notices[1]['retrieved_utc'],prior['catalog']['B']['received_utc'])
        self.assertTrue(all('status' not in notice for notice in notices))
        request=transport.requests[-1]
        self.assertEqual(parse_qs(urlsplit(request.url).query)['lastUpdatedDate'],[prior['watermark'].replace('+00:00','Z')])

    def test_failed_auth_consumes_bulk_allowance_and_does_not_disclose_bodies(self):
        previous=self.store.snapshot()
        def fail(request, limit):return 401, {}, (CREDENTIALS['client_secret']+TOKEN).encode()
        self.assert_code('AUTH_FAILED',lambda:self.run_pull(transport=fail))
        self.assertEqual(self.store.snapshot(),previous)
        self.now+=timedelta(hours=23)
        self.assert_code('BUDGET_WAIT',lambda:self.run_pull())
        for path in self.store.root.iterdir():
            if path.is_file():
                for secret in [CREDENTIALS['client_secret'],TOKEN]:self.assertNotIn(secret.encode(),path.read_bytes())

    def test_three_minute_boundary_and_restart_keeps_cooldown(self):
        self.run_pull(); self.now+=timedelta(seconds=179)
        self.store=Store(self.store.root,account='SYNTHETIC-ACCOUNT')
        self.assert_code('BUDGET_WAIT',lambda:self.run_pull('delta'))
        self.now+=timedelta(seconds=1)
        self.run_pull('delta',features=[])
        self.assertIsNotNone(self.state()['watermark'])

    def test_bulk_limit_uses_last_io_time_and_exact_24_hour_boundary(self):
        with self.store.connect() as db, self.store.transaction(db):
            state=self.store.load(db);state['attempt_count']=1
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,NULL)',('imported','bulk',
                (NOW-timedelta(hours=24,seconds=10)).isoformat(),(NOW-timedelta(hours=24)+timedelta(seconds=1)).isoformat(),'FAILED_ACCOUNTED'))
            self.store.save(db,state)
        self.assert_code('BUDGET_WAIT',lambda:self.run_pull())
        self.now+=timedelta(seconds=1);self.run_pull()

    def test_auth_delay_extends_cooldown_to_actual_data_attempt(self):
        transport=Transport(self.store,envelope([feature()]),lambda:self.now)
        transport.slow_auth=lambda:setattr(self,'now',self.now+timedelta(minutes=2))
        self.run_pull(transport=transport)
        self.now+=timedelta(minutes=2,seconds=59)
        self.assert_code('BUDGET_WAIT',lambda:self.run_pull('delta'))

    def test_failed_uncertain_delta_is_accounted_and_original_watermark_replayed(self):
        self.seed_baseline();before=self.state();old=self.store.snapshot()
        transport=Transport(self.store,envelope([]),lambda:self.now);transport.fail_stage='delta'
        self.assert_code('TRANSPORT_FAILED',lambda:self.run_pull('delta',transport=transport))
        self.assertEqual(self.store.snapshot(),old);self.assertEqual(self.state()['watermark'],before['watermark'])
        self.assert_code('BUDGET_WAIT',lambda:self.run_pull('delta'))
        self.now+=timedelta(minutes=3)
        _,next_transport=self.run_pull('delta',features=[])
        self.assertEqual(parse_qs(urlsplit(next_transport.requests[-1].url).query)['lastUpdatedDate'],[before['watermark'].replace('+00:00','Z')])

    def test_slow_data_completion_and_timeout_cooldowns_start_at_completion(self):
        for failure in [False, True]:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                self.store=synthetic_store(temp);self.now=NOW;self.seed_baseline()
                transport=Transport(self.store,envelope([]),lambda:self.now)
                def slow(request,limit):
                    result=transport(request,limit)
                    if request.stage=='delta':
                        self.now+=timedelta(minutes=2)
                        if failure:raise TimeoutError('SYNTHETIC timeout')
                    return result
                if failure:self.assert_code('TRANSPORT_FAILED',lambda:self.run_pull('delta',transport=slow))
                else:self.run_pull('delta',transport=slow)
                self.now+=timedelta(seconds=179)
                self.assert_code('BUDGET_WAIT',lambda:self.run_pull('delta'))
                self.now+=timedelta(seconds=1);self.run_pull('delta',features=[])

    def test_conflicting_update_and_scope_change_freeze_previous_publication(self):
        for change in ['same-time','older','scope-change']:
            with self.subTest(change=change):
                with tempfile.TemporaryDirectory() as temp:
                    self.store=synthetic_store(temp);self.now=NOW;self.seed_baseline();old=self.store.snapshot()
                    f=feature(text='SYNTHETIC conflicting')
                    if change=='older':f['properties']['coreNOTAMData']['notam']['lastUpdated']='2025-01-01T00:00:00Z'
                    if change=='scope-change':f['properties']['coreNOTAMData']['notam'].update(lastUpdated='2026-01-02T12:01:00Z',location='OTHER',icaoLocation='KOTHER')
                    self.assert_code('CONFLICT',lambda:self.run_pull('delta',[f]))
                    self.assertEqual(self.store.snapshot(),old)
                    self.now+=timedelta(days=2)
                    self.assert_code('RECOVERY_REQUIRED',lambda:self.run_pull())

    def test_delta_gap_missing_baseline_and_unverified_cutover_do_no_replacement(self):
        old=self.store.snapshot()
        self.assert_code('BASELINE_REQUIRED',lambda:self.run_pull('delta'))
        self.assert_code('DECODER_REQUIRED',lambda:self.run_pull(decode_initial=None))
        self.assertEqual(self.state()['attempt_count'],0)
        self.assert_code('BASELINE_CUTOVER_REQUIRED',lambda:self.run_pull(decode_initial=lambda *_:None))
        self.assertEqual(self.store.snapshot(),old)
        with tempfile.TemporaryDirectory() as temp:
            self.store=synthetic_store(temp);self.now=NOW;self.seed_baseline();old=self.store.snapshot()
            self.now+=timedelta(days=1)
            self.assert_code('DELTA_GAP',lambda:self.run_pull('delta'))
            self.assertEqual(self.store.snapshot(),old)

    def test_publication_preflight_failure_is_atomic_and_does_not_advance_success(self):
        self.seed_baseline();old=self.store.snapshot();watermark=self.state()['watermark']
        def fail(_):raise OSError('SYNTHETIC-SECRET publication failed')
        self.assert_code('PUBLICATION_FAILED',lambda:self.run_pull('delta',features=[],before_accept=fail))
        self.assertEqual(self.store.snapshot(),old);self.assertEqual(self.state()['watermark'],watermark)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM attempts ORDER BY rowid DESC LIMIT 1').fetchone()[0],'FAILED_ACCOUNTED')

    def test_outbox_sql_failure_rolls_back_catalog_snapshot_and_cursor(self):
        self.seed_baseline();old=self.store.snapshot();before=self.state();save=self.store.save;failed=False
        def fail_once(db,state):
            nonlocal failed
            if not failed and state['published_b64']!=before['published_b64']:
                failed=True;raise sqlite3.OperationalError('synthetic disk full')
            return save(db,state)
        with patch.object(self.store,'save',side_effect=fail_once):
            self.assert_code('LOCAL_FAILURE',lambda:self.run_pull('delta',features=[]))
        self.assertEqual(self.store.snapshot(),old);self.assertEqual(self.state()['watermark'],before['watermark'])

    def test_persistence_failure_before_reservation_stops_before_transport(self):
        with patch.object(self.store,'save',side_effect=OSError('synthetic flush error')):
            self.assert_code('LOCAL_FAILURE',lambda:self.run_pull(transport=lambda *_:self.fail('I/O before accounting')))
        self.assertEqual(self.state()['attempt_count'],0)

    def test_missing_corrupt_or_incomplete_history_never_initializes_or_requests(self):
        for kind in ['missing-db','missing-journal','corrupt-db','missing-history-row']:
            with tempfile.TemporaryDirectory() as temp:
                self.store=synthetic_store(temp)
                if kind=='missing-db':(self.store.root/'account.sqlite3').unlink()
                elif kind=='missing-journal':(self.store.root/'account.sqlite3-journal').unlink()
                elif kind=='corrupt-db':(self.store.root/'account.sqlite3').write_bytes(b'SYNTHETIC-CORRUPT')
                else:
                    self.run_pull()
                    with self.store.connect() as db:db.execute('DELETE FROM attempts')
                with self.subTest(kind=kind), self.assertRaises(RefreshError):
                    self.run_pull(transport=lambda *_:self.fail('I/O with corrupt history'))
                if kind=='missing-db':self.assertFalse((self.store.root/'account.sqlite3').exists())

    def test_clock_rollback_and_oversized_responses_keep_accepted_snapshot(self):
        self.seed_baseline();old=self.store.snapshot();self.now-=timedelta(minutes=4)
        self.assert_code('CLOCK_ROLLBACK',lambda:self.run_pull('delta'))
        self.now=NOW+timedelta(minutes=3)
        self.assert_code('RESPONSE_TOO_LARGE',lambda:self.run_pull('delta',max_bytes=1))
        self.assertEqual(self.store.snapshot(),old)

    def test_store_rejects_repository_paths_and_invalid_mode_before_io(self):
        self.assert_code('HISTORY_OR_STORAGE_UNAVAILABLE',lambda:Store(SCRIPTS.parent,account='SYNTHETIC'))
        self.assert_code('HISTORY_OR_STORAGE_UNAVAILABLE',lambda:Store(Path('relative'),account='SYNTHETIC'))
        with self.store.connect() as db:
            self.assert_code('INVALID_REQUEST',lambda:self.store.reserve(db,'unknown',NOW))
        self.assertEqual(self.state()['attempt_count'],0)

    def test_pending_attempt_restart_is_not_an_automatic_retry(self):
        transport=Transport(self.store,envelope([]),lambda:self.now);transport.crash_stage='auth'
        with self.assertRaises(SystemExit):self.run_pull(transport=transport)
        self.store=Store(self.store.root,account='SYNTHETIC-ACCOUNT')
        self.assert_code('RECOVERY_REQUIRED',lambda:self.run_pull())
        self.assertEqual(self.state()['attempt_count'],1)

    def test_actual_process_exit_after_auth_reservation_requires_recovery(self):
        script='''import sys,os\nsys.path.insert(0,sys.argv[2])\ndef block(event,args):\n if event.startswith("socket."):raise RuntimeError("Network blocked")\nsys.addaudithook(block)\nfrom nms_refresh import Store,collect\nfrom nms_refresh_contract import instant\ns=Store(sys.argv[1],account="SYNTHETIC-ACCOUNT")\ncollect(s,mode="bulk",credentials={"client_id":"SYNTHETIC","client_secret":"SYNTHETIC"},transport=lambda *_:os._exit(23),clock=lambda:instant("2026-01-02T12:00:00Z"),decode_initial=lambda *_:None)\n'''
        child=subprocess.run([sys.executable,'-B','-c',script,str(self.store.root),str(SCRIPTS)],capture_output=True,timeout=15)
        self.assertEqual(child.returncode,23);self.assertFalse(child.stdout);self.assertFalse(child.stderr)
        self.store=Store(self.store.root,account='SYNTHETIC-ACCOUNT')
        self.assert_code('RECOVERY_REQUIRED',lambda:self.run_pull())
        self.assertEqual(self.state()['attempt_count'],1);self.assertEqual(self.state()['call_count'],1)

    def test_concurrent_full_collectors_make_only_one_accounted_pull(self):
        barrier=Path(self.temp.name)/'synthetic-full-start'
        script='''import sys,time,json\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[2])\ndef block(event,args):\n if event.startswith("socket."):raise RuntimeError("Network blocked")\nsys.addaudithook(block)\nfrom nms_refresh import Store,collect\nfrom nms_refresh_contract import RefreshError,DecodedInitial,instant\ns=Store(sys.argv[1],account="SYNTHETIC-ACCOUNT")\nwhile not Path(sys.argv[3]).exists():time.sleep(.01)\ndef transport(request,limit):\n if request.stage=="auth":return 200,{},b'{"access_token":"SYNTHETIC"}'\n if request.stage=="initial-aixm-soap":return 200,{},b'{"status":"Success","data":{"url":"/v1/content/SYNTHETIC"}}'\n return 200,{},b'{"status":"Success","data":{"geojson":[]}}'\ntry:\n result=collect(s,mode="bulk",credentials={"client_id":"SYNTHETIC","client_secret":"SYNTHETIC"},transport=transport,clock=lambda:instant("2026-01-02T12:00:00Z"),decode_initial=lambda body,stage:DecodedInitial(json.loads(body),"2026-01-02T11:59:00Z"))\n print(result["status"])\nexcept RefreshError as e:print(str(e))\n'''
        children=[subprocess.Popen([sys.executable,'-B','-c',script,str(self.store.root),str(SCRIPTS),str(barrier)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
        barrier.write_bytes(b'SYNTHETIC');results=[]
        for child in children:
            out,err=child.communicate(timeout=15);self.assertEqual(child.returncode,0);self.assertFalse(err);results.append(out.decode().strip())
        self.assertEqual(results.count('ACCEPTED_PRIVATE_OUTBOX'),1)
        self.assertTrue(set(results)<= {'ACCEPTED_PRIVATE_OUTBOX','BUSY','RECOVERY_REQUIRED','BUDGET_WAIT'})
        self.assertEqual(self.state()['attempt_count'],1);self.assertEqual(self.state()['call_count'],3)

    def test_concurrent_process_reservations_have_exactly_one_winner(self):
        barrier=Path(self.temp.name)/'synthetic-start'
        script='''import sys,time\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[2])\ndef block(event,args):\n if event.startswith("socket."):raise RuntimeError("Network blocked")\nsys.addaudithook(block)\nfrom nms_refresh import Store\nfrom nms_refresh_contract import RefreshError,instant\ns=Store(sys.argv[1],account="SYNTHETIC-ACCOUNT")\nwhile not Path(sys.argv[3]).exists():time.sleep(.01)\ntry:\n with s.connect() as db:s.reserve(db,"bulk",instant("2026-01-02T12:00:00Z"))\n print("RESERVED")\nexcept RefreshError as e:print(str(e))\n'''
        children=[subprocess.Popen([sys.executable,'-B','-c',script,str(self.store.root),str(SCRIPTS),str(barrier)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
        barrier.write_bytes(b'SYNTHETIC')
        results=[]
        for child in children:
            out,err=child.communicate(timeout=15);self.assertEqual(child.returncode,0);self.assertFalse(err);results.append(out.decode().strip())
        self.assertEqual(results.count('RESERVED'),1)
        self.assertTrue(set(results)<= {'RESERVED','BUSY','RECOVERY_REQUIRED'})
        self.assertEqual(self.state()['attempt_count'],1)

    def test_request_repr_and_stored_history_never_contain_credentials_or_access_token(self):
        _,transport=self.run_pull()
        auth=auth_request(CREDENTIALS)
        self.assertNotIn(CREDENTIALS['client_secret'],repr(auth))
        for request in transport.requests:self.assertNotIn(TOKEN,repr(request))
        for item in self.store.root.iterdir():
            if item.is_file():
                data=item.read_bytes()
                for marker in [TOKEN,CREDENTIALS['client_id'],CREDENTIALS['client_secret']]:self.assertNotIn(marker.encode(),data)


if __name__=='__main__':unittest.main()
