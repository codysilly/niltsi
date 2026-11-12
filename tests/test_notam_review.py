import contextlib
import copy
import io
import json
from html.parser import HTMLParser
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from nms_adapter import FeedError
from notam_review import build_review, interpret
from review_notams import load_json, main, render_html, render_text

AT='2026-11-11T12:00:00Z'


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.bundle=json.loads((ROOT/'tests/fixtures/notam_review.json').read_text())
        self.inventory=json.loads((ROOT/'data/runways.json').read_text())
        self.feature=copy.deepcopy(self.bundle['airports']['KORD']['response']['data']['geojson'][0])

    def record(self,text=None,at=AT,**fields):
        source=copy.deepcopy(self.feature)
        notam=source['properties']['coreNOTAMData']['notam']
        if text is not None:
            notam['text']=text
        notam.update(fields)
        return interpret(source,'KORD',self.inventory['airports']['ORD'],at,0,{'origin':'synthetic'})

    def test_full_pair_and_single_designator_reciprocal(self):
        for text,ends in (('RWY 4L/22R CLSD',['04L','22R']),('RWY 9L CLSD',['09L','27R']),('RWY 9R/27L CLSD',['09R','27L'])):
            n=self.record(text)
            self.assertEqual(n['proposed_category'],'FULL_RUNWAY_CLOSURE')
            self.assertEqual(n['proposed_runway_ends'],ends)
            self.assertEqual(n['candidate_state_assuming_lifecycle_confirmed'],'CLOSED')
            self.assertEqual(n['coverage_state'],'UNKNOWN')
            self.assertEqual(n['lifecycle'],'UNKNOWN')

    def test_exact_evidence_offsets_and_case_whitespace(self):
        text='!ORD 11/111 ORD\n rWy\t4l/22r  cLsD'
        n=self.record(text)
        self.assertEqual(n['proposed_category'],'FULL_RUNWAY_CLOSURE')
        for evidence in n['evidence']:
            if 'quote' in evidence:
                self.assertEqual(text[evidence['start']:evidence['end']],evidence['quote'])
        self.assertEqual(n['original_text'],text)

    def test_inconsistent_pairs_unknown_numbers_and_multiple_clauses(self):
        for text in ('RWY 4L/22L CLSD','RWY 18 CLSD','RWY 4L CLSD. RWY 9R CLSD','APCH END OF RWY 4L CLSD','RWY 4L NOT CLSD'):
            n=self.record(text)
            self.assertEqual(n['proposed_category'],'AMBIGUOUS')
            self.assertEqual(n['candidate_state_assuming_lifecycle_confirmed'],'UNKNOWN')
            self.assertEqual(n['proposed_runway_ends'],[])

    def test_directional_restriction_only_explicit_end(self):
        n=self.record('RWY 4L CLSD TO LDG')
        self.assertEqual(n['proposed_category'],'DIRECTIONAL_RESTRICTION')
        self.assertEqual(n['proposed_runway_ends'],['04L'])
        self.assertEqual(n['candidate_state_assuming_lifecycle_confirmed'],'NOTAM RESTRICTION')

    def test_partial_not_whole_runway_closure(self):
        n=self.record('RWY 4L/22R CLSD BTN TWY A AND TWY B')
        self.assertEqual(n['proposed_category'],'PARTIAL_RESTRICTION')
        self.assertEqual(n['proposed_runway_ends'],['04L','22R'])
        self.assertEqual(n['candidate_state_assuming_lifecycle_confirmed'],'NOTAM RESTRICTION')
        self.assertIn('PARTIAL_EXTENT_REQUIRES_REVIEW',n['warnings'])

    def test_conditional_restrictions_remain_unknown(self):
        for text in ('RWY 4L CLSD EXC PPR','RWY 4L CLSD WHEN WET'):
            n=self.record(text)
            self.assertEqual(n['proposed_category'],'CONDITIONAL_RESTRICTION')
            self.assertEqual(n['candidate_state_assuming_lifecycle_confirmed'],'UNKNOWN')
            self.assertTrue(n['conditional_evidence'])

    def test_lighting_and_taxiway_not_full_closures(self):
        for text in ('RWY 4L PAPI OTS','RWY 4L HIRL CLSD','RWY 4L LIGHTS CLOSED'):
            n=self.record(text)
            self.assertEqual(n['proposed_category'],'EQUIPMENT_OUTAGE')
            self.assertEqual(n['proposed_runway_ends'],['04L'])
        for text in ('TWY A CLSD BTN RWY 4L AND TWY B','TWY A CLSD ADJ RWY 4L CLSD','ILS RWY 4L OTS','RWY 4L PAPI OTS. TWY B CLSD'):
            self.assertNotEqual(self.record(text)['proposed_category'],'FULL_RUNWAY_CLOSURE')

    def test_utc_offsets_start_inclusive_end_exclusive(self):
        fields={'effectiveStart':'2026-11-11T06:00:00-06:00','effectiveEnd':'2026-11-11T14:00:00+01:00'}
        n=self.record(at=AT,**fields)
        self.assertEqual(n['effective_start_utc'],AT)
        self.assertEqual(n['effective_end_utc'],'2026-11-11T13:00:00Z')
        self.assertEqual(n['validity_phase'],'WITHIN_REPORTED_WINDOW')
        self.assertEqual(self.record(at='2026-11-11T11:59:59Z',**fields)['validity_phase'],'FUTURE')
        expired=self.record(at='2026-11-11T13:00:00Z',**fields)
        self.assertEqual(expired['validity_phase'],'EXPIRED')
        self.assertEqual(expired['candidate_state_assuming_lifecycle_confirmed'],'UNKNOWN')

    def test_unknown_or_reversed_times_preserve_source_and_unknown(self):
        for end in ('PERM','2026-11-12T00:00:00Z EST','2611120000',None,'2026-11-10T00:00:00Z'):
            n=self.record(effectiveEnd=end)
            self.assertEqual(n['original_end'],end)
            self.assertEqual(n['validity_phase'],'UNKNOWN')
            self.assertEqual(n['candidate_state_assuming_lifecycle_confirmed'],'UNKNOWN')

    def test_structured_and_text_schedules_are_not_evaluated(self):
        n=self.record(schedule={'days':'DLY','hours':'0100-0300'})
        self.assertTrue(n['schedule_fields'])
        self.assertEqual(n['candidate_state_assuming_lifecycle_confirmed'],'UNKNOWN')
        for text in ('RWY 4L CLSD DLY 0100-0300','RWY 4L CLSD MON-FRI SR-SS'):
            n=self.record(text)
            self.assertTrue(n['schedule_text_evidence'])
            self.assertIn('SCHEDULE_UNINTERPRETED',n['warnings'])
            self.assertEqual(n['candidate_state_assuming_lifecycle_confirmed'],'UNKNOWN')

    def test_cancellation_and_replacement_not_assumed_resolved(self):
        for fields in ({'type':'NOTAMC'},{'type':'R'},{'references':{'replacedBy':'SYNTHETIC-OTHER'}},{'cancelledNotamId':'SYNTHETIC-OTHER'}):
            n=self.record(**fields)
            self.assertTrue(n['lifecycle_hints'])
            self.assertIn('CANCELLATION_OR_REPLACEMENT_UNCERTAIN',n['warnings'])
            self.assertEqual(n['candidate_state_assuming_lifecycle_confirmed'],'UNKNOWN')
            self.assertEqual(n['coverage_state'],'UNKNOWN')

    def test_linked_cancellation_hint_does_not_delete_original(self):
        features=self.bundle['airports']['KORD']['response']['data']['geojson']
        features[11]['properties']['coreNOTAMData']['notam']['cancelledNotamId']=features[0]['properties']['coreNOTAMData']['notam']['id']
        report=build_review(self.bundle,self.inventory,AT)
        n=report['notices'][0]
        self.assertEqual(len(report['notices']),18)
        self.assertIn('RELATED_CANCELLATION_OR_REPLACEMENT_UNCERTAIN',n['warnings'])
        self.assertEqual(n['candidate_state_assuming_lifecycle_confirmed'],'UNKNOWN')

    def test_originals_provenance_all_32_ends_and_duplicates_preserved(self):
        self.bundle['airports']['KORD']['response']['data']['geojson'].append(copy.deepcopy(self.feature))
        before=copy.deepcopy(self.bundle)
        report=build_review(self.bundle,self.inventory,AT)
        self.assertEqual(report['original_input'],before)
        self.assertEqual(self.bundle,before)
        self.assertEqual(len(report['runway_rows']),32)
        self.assertTrue(all(r['coverage_state']=='UNKNOWN' for r in report['runway_rows']))
        self.assertEqual(len(report['notices']),19)
        self.assertEqual(report['notices'][0]['original_feature'],self.feature)
        self.assertIn('unrecognizedSourceField',str(report['notices'][0]['original_feature']))
        self.assertIn('captured_utc',report['notices'][0]['provenance'])

    def test_no_notices_never_establishes_availability(self):
        self.bundle['airports']={}
        report=build_review(self.bundle,self.inventory,AT)
        self.assertEqual(report['notices'],[])
        self.assertEqual(len(report['runway_rows']),32)
        self.assertTrue(all(r['coverage_state']=='UNKNOWN' and not r['candidate_reviews'] for r in report['runway_rows']))

    def test_explicit_kind_and_airport_context_required(self):
        response=self.bundle['airports']['KORD']['response']
        for kwargs in ({},{'kind':'synthetic'},{'airport':'KORD'}):
            with self.assertRaises(FeedError):
                build_review(response,self.inventory,AT,**kwargs)
        report=build_review(response,self.inventory,AT,kind='sanitized-real-unverified',airport='KORD')
        self.assertEqual(report['data_kind'],'sanitized-real-unverified')
        with self.assertRaises(FeedError):
            build_review(self.bundle,self.inventory,AT,kind='sanitized-real-unverified')

    def test_html_escapes_sources_and_has_no_external_resources_or_scripts(self):
        report=build_review(self.bundle,self.inventory,AT)
        document=render_html(report)
        self.assertEqual(document.count('class="runway-row"'),32)
        self.assertIn('SYNTHETIC EXAMPLES ONLY - NOT REAL FAA NOTICES',document)
        self.assertIn('&lt;script&gt;',document)
        self.assertNotIn('<script>',document)
        self.assertIn("default-src 'none'",document)
        class Tags(HTMLParser):
            def handle_starttag(self,tag,attrs):
                if tag in ('script','img','iframe','link','form','object','embed'):
                    raise AssertionError('Unexpected resource tag')
                for name,value in attrs:
                    if name=='href' and not value.startswith('#'):
                        raise AssertionError('External link')
        Tags().feed(document)

    def test_terminal_controls_are_escaped_without_source_loss(self):
        self.bundle['airports']['KORD']['response']['data']['geojson'][0]['properties']['coreNOTAMData']['notam']['text']='RWY 4L CLSD\x1b[2J\u202e'
        report=build_review(self.bundle,self.inventory,AT)
        output=render_text(report)
        self.assertNotIn('\x1b',output)
        self.assertNotIn('\u202e',output)
        self.assertIn('\\u001b',output)
        self.assertIn('\\u202e',output)

    def test_local_input_secret_duplicate_key_and_url_rejected(self):
        for text in ('{"access_token":"SYNTHETIC-NOT-FOR-OUTPUT"}','{"x":1,"x":2}','{"x":NaN}'):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'input.json';path.write_text(text)
                with self.assertRaises(FeedError):
                    load_json(path)
        with self.assertRaises(FeedError):
            load_json('https://example.invalid/input.json')

    def test_offline_cli_does_not_network_or_change_source(self):
        path=ROOT/'tests/fixtures/notam_review.json'
        before=path.read_bytes()
        with patch('socket.socket',side_effect=AssertionError('No networking')), patch('urllib.request.urlopen',side_effect=AssertionError('No networking')), patch('sys.stdout',io.StringIO()) as out:
            self.assertEqual(main([str(path),'--at',AT,'--json']),0)
        report=json.loads(out.getvalue())
        self.assertEqual(report['coverage_state'],'UNKNOWN')
        self.assertEqual(len(report['runway_rows']),32)
        self.assertEqual(path.read_bytes(),before)

    def test_html_cli_new_file_only_and_dashboard_protected(self):
        path=ROOT/'tests/fixtures/notam_review.json'
        with tempfile.TemporaryDirectory() as directory, patch('sys.stdout',io.StringIO()), patch('sys.stderr',io.StringIO()):
            dest=Path(directory)/'review.html'
            args=[str(path),'--at',AT,'--html',str(dest)]
            self.assertEqual(main(args),0)
            before=dest.read_bytes()
            self.assertEqual(main(args),1)
            self.assertEqual(dest.read_bytes(),before)
            self.assertEqual(main([str(path),'--html',str(ROOT/'index.html')]),1)


if __name__=='__main__':
    unittest.main()
