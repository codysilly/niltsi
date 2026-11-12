import base64
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from nms_adapter import API_URL, FeedError, NmsAdapter, utc
from notam_rules import classify
from update_notams import Client, NoRedirect, atomic_publish, initial_airport, request_json, retrieve, update
from inspect_nms_contract import structure
import update_notams

STAMP = '2026-11-11T00:05:00Z'
OLD = '2026-11-11T00:00:00Z'


class NotamTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads((ROOT / 'tests/fixtures/notams.json').read_text())
        self.contract = json.loads((ROOT / 'tests/fixtures/legacy_contract.json').read_text())
        self.adapter = NmsAdapter(self.contract)
        self.inventory = json.loads((ROOT / 'data/runways.json').read_text())['airports']

    def record(self, text=None, **fields):
        payload = copy.deepcopy(self.payload)
        if text is not None:
            payload['features'][0]['properties']['text'] = text
        payload['features'][0]['properties'].update(fields)
        record = self.adapter.page(payload, 'KORD', STAMP)[0][0]
        return classify(record, self.inventory['ORD'])

    def client(self, get):
        class FakeClient:
            sensitive = ('fixture-client-secret',)
        client = FakeClient()
        client.get = get
        return client

    def test_reciprocal_closure_and_padding(self):
        r = self.record()
        self.assertEqual(r['classification'], 'closure')
        self.assertEqual(r['runway_ends'], ['04L', '22R'])

    def test_whole_runway_single_designator_maps_pair(self):
        r = self.record('RWY 9L CLSD')
        self.assertEqual(r['runway_ends'], ['09L', '27R'])
        self.assertEqual(r['classification'], 'closure')

    def test_parallel_runways_not_confused(self):
        r = self.record('RWY 9R/27L CLSD')
        self.assertEqual(r['runway_ends'], ['09R', '27L'])

    def test_direction_specific_restriction(self):
        r = self.record('RWY 4L CLSD TO LDG')
        self.assertEqual(r['classification'], 'restriction')
        self.assertEqual(r['runway_ends'], ['04L'])

    def test_equipment_not_whole_runway_closure(self):
        self.assertEqual(self.record('RWY 4L PAPI OTS')['classification'], 'restriction')

    def test_conditional_partial_and_unsupported_closures_unresolved(self):
        for text in ('RWY 4L CLSD EXC PPR', 'RWY 4L CLSD WHEN WET', 'RWY 4L NOT CLSD',
                     'RWY 4L CLSD BTN TWY A AND B', 'RWY 4L CLSD DLY 0100-0200',
                     'RWY 4L CLSD TO ALL ACFT EXCEPT EMERG', 'RWY 4L CLSD FOR WORK'):
            with self.subTest(text=text):
                self.assertEqual(self.record(text)['classification'], 'unresolved')

    def test_taxiway_and_other_false_positives(self):
        for text in ('TWY A CLSD BTN RWY 4L AND TWY B', 'TWY A CLSD ADJ RWY 4L CLSD',
                     'ILS RWY 4L OTS', 'RWY 4L PAPI OTS. TWY B CLSD'):
            self.assertNotEqual(self.record(text)['classification'], 'closure')

    def test_multiple_clauses_do_not_hide_second_runway(self):
        r = self.record('RWY 4L PAPI OTS. RWY 9R CLSD')
        self.assertEqual(r['classification'], 'unresolved')
        self.assertEqual(r['runway_ends'], [])
        self.assertEqual(self.record('RWY 4L 4R CLSD')['runway_ends'], [])

    def test_unknown_runway_is_unresolved(self):
        r = self.record('RWY 18 CLSD')
        self.assertEqual(r['runway_ends'], [])

    def test_ambiguous_prefix_and_inconsistent_pairs(self):
        for text in ('APCH END OF RWY 4L CLSD', 'FIRST 1000FT OF RWY 4L CLSD', 'RWY 4L/22L CLSD'):
            self.assertEqual(self.record(text)['classification'],'unresolved')
        self.assertEqual(self.record('!ORD 11/001 ORD RWY 4L/22R CLSD')['classification'],'closure')

    def test_conditional_whole_runway_affects_both_ends_uncertainty(self):
        r=self.record('RWY 4L CLSD EXC PPR')
        self.assertEqual(r['classification'],'unresolved')
        self.assertEqual(r['runway_ends'],['04L','22R'])

    def test_invalid_inventory_fails_before_network(self):
        with self.assertRaises(FeedError):
            retrieve(self.client(lambda url:self.fail('Unexpected request')),self.adapter,'KORD',[],STAMP)

    def test_schedule_and_estimated_end_are_uncertain(self):
        self.assertEqual(self.record(schedule='DLY 0100-0200')['classification'], 'unresolved')
        self.assertEqual(self.record(effectiveEnd='2026-11-12T00:00:00Z EST')['classification'], 'unresolved')

    def test_permanent_and_utc_offset(self):
        r = self.record(effectiveStart='2026-11-10T02:00:00+02:00', effectiveEnd='PERM')
        self.assertEqual(r['effective_start_utc'], '2026-11-10T00:00:00Z')
        self.assertIsNone(r['effective_end_utc'])

    def test_invalid_times_rejected(self):
        for value in ('2026-11-10T00:00:00', 'invalid', None, 42):
            with self.assertRaises(FeedError):
                utc(value)
        with self.assertRaises(FeedError):
            self.record(effectiveEnd='2026-11-09T00:00:00Z')

    def test_missing_or_wrong_fields_fail_including_empty(self):
        for payload in ({}, {'type':'FeatureCollection','features':[]}, {'type':'FeatureCollection','features':'bad'}):
            with self.assertRaises(FeedError):
                self.adapter.page(payload, 'KORD', STAMP)
        with self.assertRaises(FeedError):
            self.adapter.page(self.payload, 'KLAX', STAMP)
        with self.assertRaises(FeedError):
            self.record(status='NEW_UNKNOWN_STATUS')

    def test_complete_empty_response(self):
        self.payload['features'] = []
        self.payload['pagination']['total'] = 0
        self.assertEqual(retrieve(self.client(lambda url:self.payload), self.adapter, 'KORD', self.inventory['ORD'], STAMP), [])

    def test_two_pages(self):
        second = copy.deepcopy(self.payload)
        self.payload['pagination'] = {'total':2, 'next':API_URL+'?location=KORD&feature=RWY&page=2'}
        second['pagination']['total'] = 2
        second['features'][0]['id'] = 'SYNTHETIC-ORD-002'
        pages = iter([self.payload, second])
        result = retrieve(self.client(lambda url:next(pages)), self.adapter, 'KORD', self.inventory['ORD'], STAMP)
        self.assertEqual(len(result), 2)

    def test_pagination_failure_retains_previous_success(self):
        self.payload['pagination']['next'] = API_URL+'?location=KORD&feature=RWY&page=2'
        def get(url):
            if 'page=2' in url:
                raise FeedError('HTTP_FAILURE')
            return self.payload
        previous = {'airports':{'KORD':dict(initial_airport(), last_success_utc=OLD, notams=[self.record()])}}
        result = update(previous, self.client(get), self.adapter, self.inventory, STAMP)
        self.assertEqual(result['airports']['KORD']['last_success_utc'], OLD)
        self.assertEqual(result['airports']['KORD']['notams'], previous['airports']['KORD']['notams'])
        self.assertFalse(result['airports']['KORD']['complete'])
        self.assertEqual(result['airports']['KORD']['error'], 'HTTP_FAILURE')

    def test_truncated_duplicate_loop_and_changed_total_fail(self):
        for mode in ('truncated','duplicate','loop','changed_total'):
            with self.subTest(mode=mode):
                first, second = copy.deepcopy(self.payload), copy.deepcopy(self.payload)
                first['pagination']['total'] = 2
                if mode != 'truncated':
                    first['pagination']['next'] = API_URL+'?location=KORD&feature=RWY&page=2'
                    second['pagination']['total'] = 2
                if mode == 'loop':
                    second['features'] = []
                    second['pagination']['next'] = first['pagination']['next']
                if mode == 'changed_total':
                    second['pagination']['total'] = 3
                pages = iter([first, second])
                with self.assertRaises(FeedError):
                    retrieve(self.client(lambda url:next(pages)),self.adapter,'KORD',self.inventory['ORD'],STAMP)

    def test_unsafe_pagination_rejected(self):
        for url in ('https://evil.example/?location=KORD&feature=RWY', API_URL+'?location=KLAX&feature=RWY', API_URL+'?location=KORD&feature=RWY&access_token=oops'):
            self.payload['pagination']['next'] = url
            with self.assertRaises(FeedError):
                self.adapter.page(self.payload,'KORD',STAMP)

    def test_default_contract_never_claims_verified_coverage(self):
        self.assertFalse(self.adapter.verified)
        result = update({}, self.client(lambda url:self.payload), self.adapter, self.inventory, STAMP)
        entry=result['airports']['KORD']
        self.assertEqual(entry['error'], 'CONTRACT_UNVERIFIED')
        self.assertIsNone(entry['last_success_utc'])
        self.assertFalse(entry['verified'])

    def test_unverified_retrieval_keeps_last_verified_evidence(self):
        old=self.record('RWY 9L CLSD')
        previous={'airports':{'KORD':dict(initial_airport(),last_success_utc=OLD,notams=[old])}}
        result=update(previous,self.client(lambda url:self.payload),self.adapter,self.inventory,STAMP)
        self.assertEqual(result['airports']['KORD']['last_success_utc'],OLD)
        self.assertEqual(result['airports']['KORD']['notams'],[old])

    def test_verified_test_contract_accepts_success_and_cancellations(self):
        self.contract.update(verified=True,verification_reference='SYNTHETIC TEST ONLY')
        self.payload['features'][0]['properties']['status']='CANCELLED'
        result=update({},self.client(lambda url:self.payload),NmsAdapter(self.contract),self.inventory,STAMP)
        self.assertTrue(result['airports']['KORD']['complete'])
        self.assertEqual(result['airports']['KORD']['last_success_utc'],STAMP)
        self.assertEqual(result['airports']['KORD']['notams'][0]['lifecycle'],'CANCELLED')
        self.assertFalse(result['airports']['KLAX']['complete'])

    def test_failure_codes_are_allowlisted(self):
        result=update({},None,self.adapter,self.inventory,STAMP,'Bearer secret-token')
        self.assertNotIn('secret-token', json.dumps(result))
        self.assertEqual(result['airports']['KORD']['error'],'INTERNAL_VALIDATION_FAILURE')

    def test_missing_credentials_main_publishes_unknown_and_returns_failure(self):
        output=io.StringIO()
        with patch('update_notams.Client',side_effect=FeedError('MISSING_CREDENTIALS')), patch('update_notams.PullBudget'), patch('update_notams.atomic_publish') as publish, patch('sys.stdout',output):
            code=update_notams.main(['--bulk-airport','KORD'])
        self.assertEqual(code,1)
        snapshot=publish.call_args.args[0]
        self.assertTrue(all(e['state']=='UNKNOWN' and e['error']=='MISSING_CREDENTIALS' for e in snapshot['airports'].values()))
        self.assertIn('UNKNOWN',output.getvalue())

    def test_allowlist_discards_extra_response_fields(self):
        self.payload['features'][0]['properties']['client_secret']='should-not-be-published'
        r=self.adapter.page(self.payload,'KORD',STAMP)[0]
        self.assertNotIn('should-not-be-published',json.dumps(r))

    def test_structure_inspection_never_includes_values_or_sensitive_keys(self):
        payload={'access_token':'synthetic-token','features':[{'id':'secret-id','text':'private-full-response','location':'KORD'}], 'secretFixture':'anything','nested':{'client_secret':'secret'}}
        report=json.dumps(structure(payload,('secretFixture','synthetic-token')))
        for value in ('synthetic-token','secret-id','private-full-response','KORD','secretFixture','client_secret','access_token'):
            self.assertNotIn(value,report)
        self.assertIn('features',report)
        self.assertIn('string',report)

    def test_reflected_credentials_rejected_without_redacting_notam(self):
        self.payload['features'][0]['properties']['text']='RWY 4L secret-fixture CLSD'
        with self.assertRaisesRegex(FeedError,'^SENSITIVE_RESPONSE$'):
            self.adapter.page(self.payload,'KORD',STAMP,('secret-fixture',))
        self.payload['features'][0]['properties']['text']='Bearer a-token'
        with self.assertRaisesRegex(FeedError,'^SENSITIVE_RESPONSE$'):
            self.adapter.page(self.payload,'KORD',STAMP)

    def test_atomic_publish_and_secret_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory)/'notams.json'
            atomic_publish({'old':True},dest)
            with self.assertRaises(FeedError):
                atomic_publish({'text':'secret-fixture'},dest,('secret-fixture',))
            self.assertEqual(json.loads(dest.read_text()),{'old':True})
            with patch('update_notams.os.replace',side_effect=OSError('failure')):
                with self.assertRaises(OSError):
                    atomic_publish({'new':True},dest)
            self.assertEqual(json.loads(dest.read_text()),{'old':True})
            self.assertFalse(dest.with_suffix('.tmp').exists())

    def test_production_auth_and_query_headers(self):
        requests=[]
        def fetch(req, **kwargs):
            requests.append(req)
            return {'access_token':'synthetic-token'}
        client=Client('synthetic-id','synthetic-secret',fetch,budget=Mock())
        client.get(API_URL+'?location=KORD&feature=RWY')
        self.assertEqual(requests[0].method,'POST')
        self.assertEqual(requests[0].data,b'grant_type=client_credentials')
        basic=base64.b64encode(b'synthetic-id:synthetic-secret').decode()
        self.assertEqual(requests[0].get_header('Authorization'),'Basic '+basic)
        self.assertEqual(requests[1].get_header('Authorization'),'Bearer synthetic-token')
        self.assertEqual(requests[1].get_header('Nmsresponseformat'),'GEOJSON')
        with self.assertRaisesRegex(FeedError,'MISSING_CREDENTIALS'):
            Client(None,None,fetch)

    def test_no_redirects_with_credentials(self):
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://evil.example'))

    def test_transient_retries_without_error_text(self):
        class Opener:
            count=0
            def open(self,req,timeout):
                self.count+=1
                if self.count<3:
                    raise urllib.error.HTTPError('secret-url',503,'Bearer secret',{},None)
                return io.BytesIO(b'{"ok":true}')
        opener=Opener()
        delays=[]
        result=request_json(urllib.request.Request(API_URL),opener,delays.append)
        self.assertTrue(result['ok'])
        self.assertEqual(delays,[2,4])

    def test_rate_limit_retry_after_is_bounded(self):
        class Opener:
            count=0
            def open(self,req,timeout):
                self.count+=1
                if self.count==1:
                    raise urllib.error.HTTPError('secret-url',429,'secret',{'Retry-After':'9999'},None)
                return io.BytesIO(b'{}')
        delays=[]
        request_json(urllib.request.Request(API_URL),Opener(),delays.append)
        self.assertEqual(delays,[30])

    def test_nontransient_failure_does_not_retry_or_expose_body(self):
        class Opener:
            def open(self,req,timeout):
                raise urllib.error.HTTPError('secret-url',401,'Bearer secret',{},io.BytesIO(b'client_secret=secret'))
        with self.assertRaisesRegex(FeedError,'^HTTP_FAILURE$'):
            request_json(urllib.request.Request(API_URL),Opener(),lambda delay:self.fail('Unexpected retry'))


if __name__=='__main__':
    unittest.main()
