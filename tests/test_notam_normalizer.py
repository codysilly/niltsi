import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from normalize_notam_snapshot import normalize

FIXTURE = Path(__file__).parent / "fixtures/nms_reader_documentation.json"


class NormalizerTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE.read_text())

    def run_adapter(self, payload=None):
        return normalize(self.payload if payload is None else payload, airport="KCLT",
                         retrieved_utc="capture time unverified", query_context={"location": "KCLT"})

    def test_documented_example_preserves_distinct_representations_and_raw_values(self):
        result = self.run_adapter()
        notice = result["airports"]["KCLT"]["notices"][0]
        self.assertEqual(notice["text"], "27 RWY END ID LGT U/S")
        self.assertEqual(notice["formatted_texts"][0]["text"], "!CLT 01/123 SAMPLE NOTAM")
        self.assertEqual(notice["id"], "NMS_ID_1234567812345678")
        self.assertEqual(result["original_response"], self.payload)
        self.assertEqual(notice["reported_relationships"]["cancelationDate"], "2025-09-18T21:11:00.000Z")
        self.assertNotIn("scope", notice)
        self.assertEqual(result["airports"]["KCLT"]["completeness"], "UNKNOWN")
        notice["original_feature"]["type"] = "changed"
        self.assertEqual(self.payload["data"]["geojson"][0]["type"], "Feature")

    def test_duplicate_ids_and_malformed_records_are_not_dropped(self):
        records = self.payload["data"]["geojson"]
        records.extend([copy.deepcopy(records[0]), None, {"properties": []}])
        notices = self.run_adapter()["airports"]["KCLT"]["notices"]
        self.assertEqual(len(notices), 4)
        self.assertEqual([n["source_index"] for n in notices], [0, 1, 2, 3])
        self.assertTrue(notices[2]["warnings"])
        self.assertIsNone(notices[2]["original_feature"])

    def test_missing_condition_is_not_replaced_with_translation(self):
        n = self.payload["data"]["geojson"][0]["properties"]["coreNOTAMData"]["notam"]
        n["text"] = {"bad": "shape"}
        n["effectiveEnd"] = "PERM"
        notice = self.run_adapter()["airports"]["KCLT"]["notices"][0]
        self.assertIsNone(notice["text"])
        self.assertEqual(notice["effectiveEnd"], "PERM")
        self.assertEqual(len(notice["formatted_texts"]), 1)
        self.assertTrue(any("text not supplied" in w for w in notice["warnings"]))

    def test_failed_and_partial_error_envelopes_are_rejected(self):
        for payload in (None, [], {"status": "Failure"}, {"status": "Success", "errors": [{"code": "1"}], "data": {"geojson": []}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize(payload, airport="KCLT", retrieved_utc="raw", query_context={"location": "KCLT"})

    def test_content_references_and_wrong_formats_are_not_fetched_or_accepted(self):
        for data in ({"url": "/v1/content/placeholder"}, {"aixm": ["<xml/>"]}, {"geojson": {}}, None):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.run_adapter({"status": "Success", "data": data})

    def test_empty_collection_is_valid_but_does_not_claim_complete_coverage(self):
        result = self.run_adapter({"status": "Success", "data": {"geojson": []}})
        self.assertEqual(result["airports"]["KCLT"]["notices"], [])
        self.assertIn("completeness unknown", result["airports"]["KCLT"]["warning"])

    def test_capture_provenance_is_required(self):
        for context in (None, {}, {"location": 123}):
            with self.assertRaises(ValueError):
                normalize(self.payload, airport="KCLT", retrieved_utc="raw", query_context=context)
        with self.assertRaises(ValueError):
            normalize(self.payload, airport="KCLT", retrieved_utc="", query_context={"location": "KCLT"})

    def test_mismatched_airport_grouping_is_rejected_and_us_alias_retains_query(self):
        with self.assertRaises(ValueError):
            normalize(self.payload, airport="KMEM", retrieved_utc="raw", query_context={"location": "KCLT"})
        result = normalize(self.payload, airport="KCLT", retrieved_utc="raw", query_context={"location": "CLT"})
        self.assertEqual(result["query_context"]["location"], "CLT")
