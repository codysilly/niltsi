import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from nms_adapter import FeedError, NmsAdapter
from nms_pull_budget import PullBudget, INTERVALS
from update_notams import Client, request_json, retrieve, update
import update_notams

STAMP='2026-11-11T00:05:00Z'


class ObservedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.contract=json.loads((ROOT/'scripts/nms_contract.json').read_text())
        self.payload=json.loads((ROOT/'tests/fixtures/nms_observed_shape.json').read_text())
        self.adapter=NmsAdapter(self.contract)

    def page(self):
        return self.adapter.page(self.payload,'KORD',STAMP)

    def test_original_notam_event_translations_and_unknown_fields_preserved(self):
        records,next_page,total=self.page()
        n=records[0]
        self.assertEqual(n['original_feature'],self.payload['data']['geojson'][0])
        self.assertEqual(n['id'],'SYNTHETIC-SOURCE-ID-001')
        self.assertEqual(n['text'],'SYNTHETIC ONLY RWY 4L/22R CLSD')
        self.assertEqual(n['issued_utc'],'2026-11-09T20:00:00Z')
        self.assertEqual(n['last_updated_utc'],'2026-11-09T21:00:00Z')
        self.assertEqual(n['source_provenance']['query_airport'],'KORD')
        self.assertEqual(n['source_provenance']['response_metadata']['status'],self.payload['status'])
        self.assertEqual(n['lifecycle'],'UNKNOWN')
        self.assertEqual(n['runway_ends'],[])
        self.assertEqual(n['classification'],'unresolved')
        self.assertIsNone(next_page)
        self.assertIsNone(total)

    def test_verified_flag_alone_cannot_enable_semantics(self):
        self.contract.update(verified=True,verification_reference='structure-only')
        self.assertFalse(NmsAdapter(self.contract).verified)

    def test_duplicates_and_malformed_notices_retained(self):
        self.payload['data']['geojson']*=2
        self.payload['data']['geojson'].extend([{'unknown':'SYNTHETIC_UNRECOGNIZED'},None])
        records,_,_=self.page()
        self.assertEqual(len(records),4)
        self.assertEqual(records[0]['id'],records[1]['id'])
        self.assertEqual(len({r['record_key'] for r in records}),4)
        self.assertEqual(records[2]['original_feature'],{'unknown':'SYNTHETIC_UNRECOGNIZED'})
        self.assertIsNone(records[3]['original_feature'])

    def test_reported_counts_are_not_hardcoded_or_treated_as_complete(self):
        for airport,count in (('KORD',41),('KLAX',17),('KJFK',41)):
            self.payload['data']['geojson']=[copy.deepcopy(self.payload['data']['geojson'][0]) for _ in range(count)]
            records,next_page,total=self.adapter.page(self.payload,airport,STAMP)
            self.assertEqual(len(records),count)
            self.assertEqual(len({n['record_key'] for n in records}),count)
            self.assertIsNone(total)
            self.assertIsNone(next_page)
            self.assertTrue(all(n['source_provenance']['completeness']=='UNKNOWN' for n in records))

    def test_arbitrary_status_type_and_scenario_do_not_infer_lifecycle(self):
        for status in ('success','failure','cancelled','anything'):
            self.payload['status']=status
            self.payload['data']['geojson'][0]['properties']['coreNOTAMData']['notam']['type']='CANCELLED'
            self.assertEqual(self.page()[0][0]['lifecycle'],'UNKNOWN')

    def test_unknown_time_encodings_preserved_without_losing_notice(self):
        n=self.payload['data']['geojson'][0]['properties']['coreNOTAMData']['notam']
        for end in ('PERM','EST',None,42,'2611110000'):
            n['effectiveEnd']=end
            record=self.page()[0][0]
            self.assertEqual(record['original_end'],end)
            self.assertIsNone(record['effective_end_utc'])
            self.assertEqual(record['end_kind'],'unverified')

    def test_missing_text_not_replaced_with_formatted_translation(self):
        del self.payload['data']['geojson'][0]['properties']['coreNOTAMData']['notam']['text']
        record=self.page()[0][0]
        self.assertIn('FULL_TEXT_UNAVAILABLE',record['validation_issues'])
        self.assertIn('unavailable',record['text'])
        self.assertIn('formattedText',json.dumps(record['original_feature']))

    def test_no_counts_or_links_assumed_complete_or_followed(self):
        self.payload['pagination']={'total':1,'next':'https://evil.example/'}
        self.payload['data']['total']=1
        fake=Mock(sensitive=())
        fake.get.return_value=self.payload
        inventories=json.loads((ROOT/'data/runways.json').read_text())['airports']
        records=retrieve(fake,self.adapter,'KORD',inventories['ORD'],STAMP)
        self.assertEqual(len(records),1)
        fake.get.assert_called_once()
        result=update({},fake,self.adapter,inventories,STAMP,selected_airport='KORD')
        e=result['airports']['KORD']
        self.assertFalse(e['complete'])
        self.assertFalse(e['verified'])
        self.assertIsNone(e['last_success_utc'])
        self.assertEqual(e['state'],'UNKNOWN')
        self.assertEqual(result['airports']['KLAX']['error'],'PULL_NOT_REQUESTED')

    def test_empty_response_still_unknown(self):
        self.payload['data']['geojson']=[]
        self.assertEqual(self.page(),([],None,None))

    def test_malformed_envelope_fails_closed(self):
        for payload in ({},{'status':200,'data':{'geojson':[]}}, {'status':'SYNTHETIC','data':{'geojson':None}}):
            with self.assertRaises(FeedError):
                self.adapter.page(payload,'KORD',STAMP)

    def test_nested_secret_or_reflected_credential_rejects_entire_response(self):
        self.payload['data']['geojson'][0]['properties']['extra']={'access_token':'not-public'}
        with self.assertRaisesRegex(FeedError,'SENSITIVE_RESPONSE'):
            self.page()
        del self.payload['data']['geojson'][0]['properties']['extra']
        with self.assertRaisesRegex(FeedError,'SENSITIVE_RESPONSE'):
            self.adapter.page(self.payload,'KORD',STAMP,('SYNTHETIC-SOURCE-ID-001',))


class PullBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'budget.json'
        self.time=1000000
        self.budget=PullBudget(self.path,lambda:self.time)

    def mature(self):
        with self.assertRaisesRegex(FeedError,'PULL_HISTORY_UNVERIFIED'):
            self.budget.check('bulk')
        self.time+=INTERVALS['bulk']

    def test_new_history_quarantined_and_persisted_across_process_objects(self):
        with self.assertRaisesRegex(FeedError,'PULL_HISTORY_UNVERIFIED'):
            self.budget.reserve('bulk')
        with self.assertRaisesRegex(FeedError,'BULK_PULL_COOLDOWN'):
            PullBudget(self.path,lambda:self.time).reserve('bulk')

    def test_global_bulk_boundary_not_per_airport(self):
        self.mature()
        self.budget.reserve('bulk')
        self.time+=INTERVALS['bulk']-1
        with self.assertRaisesRegex(FeedError,'BULK_PULL_COOLDOWN'):
            self.budget.reserve('bulk')
        self.time+=1
        self.budget.reserve('bulk')

    def test_delta_three_minute_boundary(self):
        self.mature()
        self.budget.reserve('delta')
        self.time+=179
        with self.assertRaisesRegex(FeedError,'DELTA_PULL_COOLDOWN'):
            self.budget.reserve('delta')
        self.time+=1
        self.budget.reserve('delta')

    def test_corrupt_state_and_concurrent_lock_block_requests(self):
        self.path.write_text('invalid')
        with self.assertRaisesRegex(FeedError,'PULL_STATE_INVALID'):
            self.budget.check('bulk')
        lock=self.path.with_suffix('.lock');lock.mkdir()
        with self.assertRaisesRegex(FeedError,'PULL_STATE_LOCKED'):
            self.budget.reserve('bulk')
        lock.rmdir()

    def test_clock_rollback_blocks_requests(self):
        self.mature()
        self.budget.reserve('bulk')
        self.time-=1
        with self.assertRaisesRegex(FeedError,'PULL_CLOCK_INVALID'):
            self.budget.reserve('bulk')

    def test_reservation_write_failure_prevents_network(self):
        self.mature()
        fetch=Mock(return_value={'access_token':'synthetic-token'})
        client=Client('synthetic-id','synthetic-secret',fetch,budget=self.budget)
        with patch('nms_pull_budget.os.replace',side_effect=OSError('write failure')):
            with self.assertRaises(OSError):
                client.get('https://api-nms.aim.faa.gov/nmsapi/v1/notams?location=KORD&feature=RWY')
        self.assertEqual(fetch.call_count,1) # authentication only

    def test_failed_data_request_consumes_bulk_budget_and_does_not_retry(self):
        self.mature()
        calls=[]
        def fetch(req,**kwargs):
            calls.append(kwargs)
            if len(calls)==1:
                return {'access_token':'synthetic-token'}
            raise FeedError('HTTP_FAILURE',http_status=503)
        client=Client('synthetic-id','synthetic-secret',fetch,budget=self.budget)
        with self.assertRaisesRegex(FeedError,'HTTP_FAILURE'):
            client.get('https://api-nms.aim.faa.gov/nmsapi/v1/notams')
        self.assertEqual(calls[1],{'attempts':1})
        with self.assertRaisesRegex(FeedError,'BULK_PULL_COOLDOWN'):
            client.get('https://api-nms.aim.faa.gov/nmsapi/v1/notams')
        self.assertEqual(len(calls),2)

    def test_no_budget_or_delta_protocol_means_no_data_call(self):
        for kwargs in ({},{'budget':self.budget,'pull_kind':'delta'}):
            fetch=Mock(return_value={'access_token':'synthetic-token'})
            client=Client('synthetic-id','synthetic-secret',fetch,**kwargs)
            with self.assertRaises(FeedError):
                client.get('https://api-nms.aim.faa.gov/nmsapi/v1/notams')
            self.assertEqual(fetch.call_count,1)

    def test_default_updater_does_not_authenticate_or_pull(self):
        with patch('update_notams.Client') as client, patch('update_notams.atomic_publish') as publish, patch('sys.stdout',io.StringIO()):
            self.assertEqual(update_notams.main([]),1)
        client.assert_not_called()
        self.assertTrue(all(e['error']=='PULL_MODE_REQUIRED' for e in publish.call_args.args[0]['airports'].values()))

    def test_inspector_unknown_history_blocks_even_authentication(self):
        import inspect_nms_contract as inspector
        with patch.dict('os.environ',{'FAA_NMS_CLIENT_ID':'synthetic-id','FAA_NMS_CLIENT_SECRET':'synthetic-secret'},clear=True), patch.object(inspector,'PullBudget',return_value=self.budget), patch.object(inspector,'Client') as client, patch('sys.stdout',io.StringIO()), patch('sys.stderr',io.StringIO()) as err:
            self.assertEqual(inspector.main(['--bulk-airport','KORD']),1)
            self.assertIn('previous account traffic is unknown',err.getvalue())
        client.assert_not_called()
        self.assertTrue(self.path.exists())

    def test_data_transport_one_attempt_even_on_503(self):
        opener=Mock()
        opener.open.side_effect=urllib.error.HTTPError('omitted',503,'omitted',{},None)
        with self.assertRaises(FeedError):
            request_json(Mock(),opener,sleep=lambda _:self.fail('No API retry allowed'),attempts=1)
        opener.open.assert_called_once()


if __name__=='__main__':
    unittest.main()
