"""Explicit synthetic manifests/receipts/snapshots; no production store access."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
from capture_storage import platform_storage
from import_nms_history import review, import_history, HistoryImportError
from nms_refresh import Store, collect
from nms_refresh_contract import RefreshError, DecodedInitial
from test_nms_refresh import NOW, CREDENTIALS


def sha(value):return hashlib.sha256(value).hexdigest()


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.target=self.base/'synthetic-import'
        self.receipt=self.base/'synthetic-receipt.json';self.receipt.write_bytes(b'{"synthetic":true,"ledger":"SPA-26","auth_attempts":1,"data_attempts":1}')
        self.snapshot=self.base/'synthetic-snapshot.json'
        self.snapshot.write_text(json.dumps({'retrieved_utc':'2026-01-02T11:01:00Z',
            'publication':{'source':'NMS-API snapshot','automatic_updates':False},
            'airports':{'KMEM':{'completeness':'UNKNOWN','warning':'SYNTHETIC historical display only',
            'notices':[{'id':'SYNTHETIC','text':'SYNTHETIC LEGAL NOTICE\r\nsource preserved',
                        'formatted_texts':[{'type':'SYNTHETIC','field':'simpleText','text':'SYNTHETIC\rprovider text'}]}]}}}),encoding='utf-8')
        self.manifest={'schema_version':1,'account':{'logical_id':'SYNTHETIC-ACCOUNT','identity_reference':'SYNTHETIC identity attestation',
            'identity_confirmed':True,'all_clients_quiesced':True},'history':{'inventory_complete':True,
            'complete_through_utc':'2026-01-02T11:30:00Z','declaration_reference':'SYNTHETIC evidence review'},
            'attempts':[{'id':'SYNTHETIC-ATTEMPT','kind':'bulk','reserved_utc':'2026-01-02T11:00:00Z',
                'last_io_utc':'2026-01-02T11:01:00Z','last_io_is_upper_bound':False,'outcome':'succeeded','known_call_count':2,
                'receipt':{'path':str(self.receipt),'sha256':sha(self.receipt.read_bytes())},
                'calls':[{'stage':'auth','at_utc':'2026-01-02T11:00:00Z','certainty':'observed'},
                         {'stage':'initial-aixm-soap','at_utc':'2026-01-02T11:01:00Z','certainty':'reserved'}]}],
            'uncertainties':[],'prior_snapshot':{'path':str(self.snapshot),'bytes':len(self.snapshot.read_bytes()),
                'sha256':sha(self.snapshot.read_bytes()),'approved_for_display':True,'origin_attempt_id':'SYNTHETIC-ATTEMPT'}}
        self.path=self.base/'synthetic-manifest.json';self.save()

    def save(self):self.path.write_text(json.dumps(self.manifest,indent=2),encoding='utf-8')
    def report(self):return review(self.path,self.target,clock=lambda:NOW)[0]
    def execute(self,**kwargs):return import_history(self.path,self.target,approve=self.report()['approval_digest'],clock=lambda:NOW,**kwargs)
    def assert_code(self,code,call):
        with self.assertRaises(HistoryImportError) as e:call()
        self.assertEqual(str(e.exception),code);self.assertNotIn('SYNTHETIC',str(e.exception))

    def test_dry_run_has_no_writes_or_sqlite_connection_and_redacts_source_text(self):
        before={p.name:p.read_bytes() for p in self.base.iterdir()}
        with patch.object(sqlite3,'connect',side_effect=AssertionError('dry-run database I/O')):
            report=self.report()
        self.assertFalse(report['writes_performed']);self.assertFalse(self.target.exists())
        self.assertEqual(report['attempt_count'],1);self.assertEqual(report['known_call_count'],2)
        self.assertNotIn('LEGAL NOTICE',json.dumps(report))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.base.iterdir()})

    def test_import_exact_snapshot_counters_private_acl_and_no_baseline(self):
        receipt=self.receipt.read_bytes();snapshot=self.snapshot.read_bytes()
        result=self.execute();self.assertEqual(result['status'],'IMPORTED_READY_NO_BASELINE')
        store=Store(self.target,account='SYNTHETIC-ACCOUNT')
        self.assertEqual(store.snapshot(),snapshot);self.assertEqual(self.receipt.read_bytes(),receipt)
        self.assertEqual((self.target/'import-manifest.private.json').read_bytes(),self.path.read_bytes())
        for item in [self.target,*self.target.iterdir()]:store.storage.verify_private(item)
        with store.connect() as db:
            state=store.load(db);self.assertFalse(state['baseline']);self.assertIsNone(state['watermark'])
            self.assertEqual((state['attempt_count'],state['call_count'],state['response_count']),(1,2,0))
            self.assertEqual(db.execute('PRAGMA synchronous').fetchone()[0],3)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('SELECT reserved,last_io FROM attempts').fetchone(),('2026-01-02T11:00:00Z','2026-01-02T11:01:00Z'))
        with self.assertRaises(RefreshError) as e:
            collect(store,mode='delta',credentials=CREDENTIALS,transport=lambda *_:self.fail('Unexpected network'),clock=lambda:NOW)
        self.assertEqual(str(e.exception),'BASELINE_REQUIRED')
        with self.assertRaises(RefreshError) as e:
            collect(store,mode='bulk',credentials=CREDENTIALS,transport=lambda *_:self.fail('Unexpected network'),clock=lambda:NOW,decode_initial=lambda *_:None)
        self.assertEqual(str(e.exception),'BUDGET_WAIT')

    def test_existing_store_refusal_never_resets_counters_or_files(self):
        self.execute();before={p.name:p.read_bytes() for p in self.target.iterdir()}
        self.assert_code('IMPORT_TARGET_EXISTS',lambda:import_history(self.path,self.target,approve='anything',clock=lambda:NOW))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.target.iterdir()})

    def test_approval_binds_inputs_and_target_and_never_auto_updates(self):
        approval=self.report()['approval_digest']
        other=self.base/'other-target'
        self.assert_code('IMPORT_APPROVAL_MISMATCH',lambda:import_history(self.path,other,approve=approval,clock=lambda:NOW))
        self.assertFalse(other.exists())
        self.manifest['history']['declaration_reference']='SYNTHETIC changed declaration';self.save()
        self.assert_code('IMPORT_APPROVAL_MISMATCH',lambda:import_history(self.path,self.target,approve=approval,clock=lambda:NOW))
        self.assertFalse(self.target.exists())
        self.receipt.write_bytes(b'changed')
        self.assert_code('IMPORT_EVIDENCE_MISMATCH',self.report)

    def test_incomplete_identity_unknown_fields_and_contradictions_are_specific(self):
        original=copy.deepcopy(self.manifest)
        cases=[('IMPORT_IDENTITY_UNVERIFIED',lambda m:m['account'].update(identity_confirmed=False)),
               ('IMPORT_HISTORY_INCOMPLETE',lambda m:m['history'].update(inventory_complete=False)),
               ('IMPORT_MISSING_FIELD',lambda m:m['attempts'][0].pop('last_io_utc')),
               ('IMPORT_CONTRADICTION',lambda m:m['attempts'][0].update(last_io_utc='2026-01-02T10:00:00Z')),
               ('IMPORT_CONTRADICTION',lambda m:m['attempts'][0].update(last_io_is_upper_bound=True)),
               ('IMPORT_CONTRADICTION',lambda m:m['attempts'].append(copy.deepcopy(m['attempts'][0]))),
               ('IMPORT_CONTRADICTION',lambda m:m['prior_snapshot'].update(origin_attempt_id='unknown')),
               ('IMPORT_UNKNOWN_FIELD',lambda m:m.update(client_secret='SYNTHETIC-SECRET')),
               ('IMPORT_INVALID_TIME',lambda m:m['attempts'][0].update(reserved_utc='unknown'))]
        for code,change in cases:
            self.manifest=copy.deepcopy(original);change(self.manifest);self.save()
            with self.subTest(code=code):self.assert_code(code,self.report)
            self.assertFalse(self.target.exists())

    def test_uncertainty_is_preserved_held_and_never_declared_verified(self):
        attempt=self.manifest['attempts'][0];attempt.update(outcome='uncertain',last_io_is_upper_bound=True)
        self.manifest['uncertainties']=[{'attempt_id':attempt['id'],'code':'OUTCOME_UNKNOWN'}];self.save()
        report=self.report();self.assertTrue(report['activation_hold']);self.assertFalse(report['history_verified'])
        result=self.execute();self.assertEqual(result['status'],'IMPORTED_HELD')
        marker=json.loads((self.target/'import-status.json').read_bytes());self.assertEqual(marker['state'],'HELD_UNCERTAINTY')
        db=sqlite3.connect(self.target/'account.sqlite3',isolation_level=None)
        try:
            db.execute('PRAGMA journal_mode=PERSIST')
            state=json.loads(db.execute('SELECT document FROM state').fetchone()[0])
            self.assertFalse(state['history_verified']);self.assertEqual(state['attempt_count'],1)
            self.assertEqual(db.execute('SELECT last_io,status FROM attempts').fetchone(),('2026-01-02T11:01:00Z','IMPORTED_UNCERTAIN'))
        finally:db.close()
        with self.assertRaises(RefreshError):Store(self.target,account='SYNTHETIC-ACCOUNT').snapshot()

    def test_unbounded_or_unreferenced_uncertainty_is_rejected(self):
        self.manifest['attempts'][0]['outcome']='uncertain';self.save()
        self.assert_code('IMPORT_UNBOUNDED_UNCERTAINTY',self.report)
        self.manifest['attempts'][0]['last_io_is_upper_bound']=True;self.save()
        self.assert_code('IMPORT_UNBOUNDED_UNCERTAINTY',self.report)

    def test_known_receipt_counts_cannot_be_zeroed_and_unknown_times_are_not_fabricated(self):
        self.manifest['attempts'][0]['calls']=[];self.manifest['attempts'][0]['known_call_count']=0;self.save()
        self.assert_code('IMPORT_CONTRADICTION',self.report)
        attempt=self.manifest['attempts'][0]
        attempt.update(outcome='uncertain',last_io_is_upper_bound=True,known_call_count=2,
                       calls=[{'stage':'auth','at_utc':None,'certainty':'unknown-time'},
                              {'stage':'other-data','at_utc':None,'certainty':'unknown-time'}])
        self.manifest['uncertainties']=[{'attempt_id':attempt['id'],'code':'TIMING_UNCERTAIN'}];self.save()
        self.assertEqual(self.report()['known_calls_without_timestamps'],2)
        self.execute()
        db=sqlite3.connect(self.target/'account.sqlite3',isolation_level=None)
        try:
            db.execute('PRAGMA journal_mode=PERSIST')
            self.assertEqual(db.execute('SELECT at FROM calls').fetchall(),[('UNVERIFIED_TIME',),('UNVERIFIED_TIME',)])
            state=json.loads(db.execute('SELECT document FROM state').fetchone()[0])
            self.assertEqual(state['call_count'],2);self.assertFalse(state['history_verified'])
        finally:db.close()

    def test_interruption_at_every_pre_ready_checkpoint_blocks_engine_and_reimport(self):
        for point in ['pending','history-written','validated']:
            with self.subTest(point=point):
                self.target=self.base/('interrupted-'+point)
                def stop(stage):
                    if stage==point:raise RuntimeError('SYNTHETIC interrupted')
                self.assert_code('IMPORT_INTERRUPTED',lambda:self.execute(checkpoint=stop))
                self.assertEqual(json.loads((self.target/'import-status.json').read_bytes())['state'],'PENDING')
                with self.assertRaises(RefreshError):Store(self.target,account='SYNTHETIC-ACCOUNT').snapshot()
                self.assert_code('IMPORT_TARGET_EXISTS',lambda:import_history(self.path,self.target,approve='ignored',clock=lambda:NOW))

    def test_interrupted_native_process_preserves_evidence_and_blocks_engine(self):
        script='''import sys,os\nsys.path.insert(0,sys.argv[1])\ndef block(event,args):\n if event.startswith("socket."):raise RuntimeError("Network blocked")\nsys.addaudithook(block)\nfrom import_nms_history import review,import_history\nfrom nms_refresh_contract import instant\nclock=lambda:instant("2026-01-02T12:00:00Z")\nreport=review(sys.argv[2],sys.argv[3],clock=clock)[0]\ndef stop(stage):\n if stage=="history-written":os._exit(23)\nimport_history(sys.argv[2],sys.argv[3],approve=report["approval_digest"],clock=clock,checkpoint=stop)\n'''
        before=self.receipt.read_bytes();child=subprocess.run([sys.executable,'-B','-c',script,str(SCRIPTS),str(self.path),str(self.target)],capture_output=True,timeout=15)
        self.assertEqual(child.returncode,23);self.assertFalse(child.stdout);self.assertFalse(child.stderr)
        self.assertEqual(self.receipt.read_bytes(),before)
        with self.assertRaises(RefreshError):Store(self.target,account='SYNTHETIC-ACCOUNT').snapshot()

    def test_flush_or_finalization_failure_is_not_silently_skipped(self):
        storage=platform_storage()
        if hasattr(storage,'_flush_file'):
            with patch.object(storage,'_flush_file',side_effect=OSError('SYNTHETIC flush failed')):
                self.assert_code('IMPORT_INTERRUPTED',lambda:self.execute(storage=storage))
            self.assertTrue(self.target.exists())
        self.target=self.base/'finalization-failure'
        with patch.object(storage,'replace',side_effect=OSError('SYNTHETIC atomic publication failed')):
            self.assert_code('IMPORT_FINALIZATION_UNCERTAIN',lambda:self.execute(storage=storage))
        with self.assertRaises(RefreshError):Store(self.target,account='SYNTHETIC-ACCOUNT').snapshot()

    def test_sqlite_commit_failure_leaves_pending_blocked_store(self):
        original_connect=sqlite3.connect
        class FailingCommit:
            def __init__(self,*args,**kwargs):self.db=original_connect(*args,**kwargs)
            def __getattr__(self,key):return getattr(self.db,key)
            def execute(self,sql,*args):
                if sql=='COMMIT':raise sqlite3.OperationalError('SYNTHETIC commit/flush failure')
                return self.db.execute(sql,*args)
        with patch.object(sqlite3,'connect',side_effect=FailingCommit):
            self.assert_code('IMPORT_INTERRUPTED',self.execute)
        self.assertEqual(json.loads((self.target/'import-status.json').read_bytes())['state'],'PENDING')
        with self.assertRaises(RefreshError):Store(self.target,account='SYNTHETIC-ACCOUNT').snapshot()

    def test_lost_ready_acknowledgement_requires_inspection_not_overwrite(self):
        def lost(stage):
            if stage=='finalized':raise OSError('SYNTHETIC lost acknowledgement')
        self.assert_code('IMPORT_FINALIZATION_UNCERTAIN',lambda:self.execute(checkpoint=lost))
        self.assertEqual(Store(self.target,account='SYNTHETIC-ACCOUNT').snapshot(),self.snapshot.read_bytes())
        self.assert_code('IMPORT_TARGET_EXISTS',lambda:import_history(self.path,self.target,approve='ignored',clock=lambda:NOW))

    def test_explicit_cli_write_requires_review_digest_and_preserves_snapshot(self):
        approval=self.report()['approval_digest']
        script='''import sys,runpy\ndef block(event,args):\n if event.startswith("socket."):raise RuntimeError("Network blocked")\nsys.addaudithook(block)\nsys.path.insert(0,sys.argv[1])\nsys.argv=["import_nms_history.py","--manifest",sys.argv[2],"--target",sys.argv[3],"--write","--approve",sys.argv[4]]\nrunpy.run_module("import_nms_history",run_name="__main__")\n'''
        args=[sys.executable,'-B','-c',script,str(SCRIPTS),str(self.path),str(self.target)]
        refused=subprocess.run(args+['wrong'],capture_output=True,timeout=15)
        self.assertEqual(refused.returncode,1);self.assertIn(b'IMPORT_APPROVAL_MISMATCH',refused.stdout);self.assertFalse(self.target.exists())
        accepted=subprocess.run(args+[approval],capture_output=True,timeout=15)
        self.assertEqual(accepted.returncode,0);self.assertFalse(accepted.stderr)
        text=accepted.stdout.decode();first,end=json.JSONDecoder().raw_decode(text)
        self.assertFalse(first['writes_performed']);self.assertEqual(json.loads(text[end:])['status'],'IMPORTED_READY_NO_BASELINE')
        self.assertEqual(Store(self.target,account='SYNTHETIC-ACCOUNT').snapshot(),self.snapshot.read_bytes())

    def test_dry_run_cli_has_no_write_and_prints_review_report(self):
        script='''import sys,runpy\ndef block(event,args):\n if event.startswith("socket."):raise RuntimeError("Network blocked")\nsys.addaudithook(block)\nsys.path.insert(0,sys.argv[1])\nsys.argv=["import_nms_history.py","--manifest",sys.argv[2],"--target",sys.argv[3]]\nrunpy.run_module("import_nms_history",run_name="__main__")\n'''
        child=subprocess.run([sys.executable,'-B','-c',script,str(SCRIPTS),str(self.path),str(self.target)],capture_output=True,timeout=15)
        self.assertEqual(child.returncode,0);self.assertFalse(child.stderr)
        report=json.loads(child.stdout);self.assertFalse(report['writes_performed']);self.assertFalse(self.target.exists())
        self.assertNotIn(b'LEGAL NOTICE',child.stdout)


if __name__=='__main__':unittest.main()
