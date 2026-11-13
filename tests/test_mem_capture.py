"""Network-blocked, synthetic-only tests for the one-time supervised capture."""
import json
from datetime import datetime, timezone
from pathlib import Path
import stat
import os
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from capture_mem_notams import (AUTH_URL, DATA_URL, CaptureError, NoRedirect,
                                capture, check_reservation)
from capture_storage import platform_storage

NOW = datetime(2026, 11, 13, 8, tzinfo=timezone.utc)
ENV = {'FAA_NMS_CLIENT_ID': 'synthetic-client', 'FAA_NMS_CLIENT_SECRET': 'synthetic-secret'}
PAYLOAD = Path(__file__).parent / 'fixtures/nms_reader_documentation.json'


class MemCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'capture'
        self.reservation = {
            'schema_version': 1, 'ledger': 'SPA-26', 'state': 'RESERVED',
            'airport': 'KMEM', 'collector': 'mem-budget',
            'auth_attempt_limit': 1, 'data_attempt_limit': 1,
            'reservation_id': '00000000-0000-4000-8000-000000000001',
            'not_before_utc': '2026-11-13T07:44:03.558Z',
            'reserved_utc': '2026-11-13T07:59:00Z',
            'expires_utc': '2026-11-13T08:10:00Z', 'output_dir': str(self.output)}
        self.calls = []
        self.body = PAYLOAD.read_bytes()

    def transport(self, request, limit):
        self.calls.append(request)
        receipt = json.loads((self.output / 'receipt.json').read_text())
        if request.full_url == AUTH_URL:
            self.assertEqual(receipt['auth_attempts'], 1)
            self.assertEqual(receipt['data_attempts'], 0)
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(request.data, b'grant_type=client_credentials')
            return 200, {}, b'{"access_token":"synthetic-access-token"}'
        self.assertEqual(request.full_url, DATA_URL)
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.get_header('Nmsresponseformat'), 'GEOJSON')
        self.assertEqual(receipt['data_attempts'], 1)
        return 200, {'x-request-id': '00000000-0000-4000-8000-000000000002'}, self.body

    def run_capture(self, send=None, clock=lambda: NOW):
        return capture(self.reservation, env=ENV, send=send or self.transport, clock=clock)

    def test_success_is_one_auth_and_one_get_with_private_raw_and_normalized_outputs(self):
        result = self.run_capture()
        self.assertEqual([r.full_url for r in self.calls], [AUTH_URL, DATA_URL])
        self.assertEqual(result['state'], 'CAPTURED_UNVERIFIED')
        self.assertEqual(result['record_count'], 1)
        self.assertEqual((self.output / 'response.json').read_bytes(), self.body)
        normalized = json.loads((self.output / 'normalized.private.json').read_text())
        self.assertEqual(normalized['query_context'], {'location': 'KMEM'})
        self.assertEqual(normalized['airports']['KMEM']['completeness'], 'UNKNOWN')
        storage = platform_storage()
        storage.verify_private(self.output)
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(self.output.stat().st_mode) & 0o077, 0)
        for path in self.output.iterdir():
            storage.verify_private(path)
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)
            self.assertNotIn('synthetic-access-token', path.read_text())
            self.assertNotIn('synthetic-secret', path.read_text())

    def test_quarantine_and_expiry_block_before_any_request_or_output(self):
        for now in (datetime(2026, 11, 12, 8, tzinfo=timezone.utc), datetime(2026, 11, 13, 9, tzinfo=timezone.utc)):
            with self.assertRaises(CaptureError):
                self.run_capture(clock=lambda: now)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.output.exists())

    def test_lowered_floor_or_wrong_scope_cannot_create_an_allowance(self):
        for changes in ({'not_before_utc': '2026-11-12T00:00:00Z'}, {'airport': 'KORD'},
                        {'collector': 'other-task'}, {'state': 'QUARANTINED'},
                        {'data_attempt_limit': 2}, {'reservation_id': 'invalid'}):
            with self.subTest(changes=changes), self.assertRaises(CaptureError):
                check_reservation(dict(self.reservation, **changes), NOW)
        self.assertFalse(self.output.exists())

    def test_missing_credentials_stop_before_consuming_local_attempt(self):
        with self.assertRaisesRegex(CaptureError, 'CREDENTIALS_MISSING'):
            capture(self.reservation, env={}, send=self.transport, clock=lambda: NOW)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [])

    def test_repeated_invocation_cannot_reuse_the_reserved_output(self):
        self.run_capture()
        with self.assertRaisesRegex(CaptureError, 'DIRECTORY_EXISTS'):
            self.run_capture()
        self.assertEqual(len(self.calls), 2)

    def test_auth_failure_has_no_get_and_does_not_save_auth_response(self):
        calls = []
        def failed(request, limit):
            calls.append(request.full_url)
            return 401, {}, b'synthetic-secret must not be logged'
        with self.assertRaisesRegex(CaptureError, 'AUTH_HTTP_FAILURE'):
            self.run_capture(send=failed)
        self.assertEqual(calls, [AUTH_URL])
        receipt = json.loads((self.output / 'receipt.json').read_text())
        self.assertEqual((receipt['auth_attempts'], receipt['data_attempts']), (1, 0))
        self.assertEqual(receipt['state'], 'STOPPED_DO_NOT_RETRY')
        self.assertNotIn('synthetic-secret', (self.output / 'receipt.json').read_text())
        self.assertFalse((self.output / 'response.json').exists())

    def test_data_failure_consumes_attempt_without_retry_or_saving_error_body(self):
        def failed(request, limit):
            if request.full_url == DATA_URL:
                self.calls.append(request)
                return 503, {}, b'private upstream diagnostics'
            return self.transport(request, limit)
        with self.assertRaisesRegex(CaptureError, 'DATA_HTTP_FAILURE'):
            self.run_capture(send=failed)
        self.assertEqual(len(self.calls), 2)
        receipt = json.loads((self.output / 'receipt.json').read_text())
        self.assertEqual(receipt['data_attempts'], 1)
        self.assertEqual(receipt['http_status'], 503)
        self.assertFalse((self.output / 'response.json').exists())

    def test_content_pointer_or_bad_json_stops_after_two_requests(self):
        for body in (b'{"status":"Success","data":{"url":"/v1/content/never-follow"}}', b'bad json'):
            with tempfile.TemporaryDirectory() as directory:
                self.reservation['output_dir'] = str(Path(directory) / 'capture')
                self.output = Path(self.reservation['output_dir'])
                self.body = body
                self.calls.clear()
                with self.assertRaisesRegex(CaptureError, 'NORMALIZATION_REJECTED'):
                    self.run_capture()
                self.assertEqual(len(self.calls), 2)
                self.assertEqual((self.output / 'response.json').read_bytes(), body)
                self.assertFalse((self.output / 'normalized.private.json').exists())

    def test_expired_reservation_after_auth_blocks_data(self):
        times = iter([NOW, datetime(2026, 11, 13, 9, tzinfo=timezone.utc)])
        with self.assertRaisesRegex(CaptureError, 'EXPIRED'):
            self.run_capture(clock=lambda: next(times))
        self.assertEqual(len(self.calls), 1)
        receipt = json.loads((self.output / 'receipt.json').read_text())
        self.assertEqual(receipt['data_attempts'], 0)

    def test_redirect_handler_never_follows_destination(self):
        with self.assertRaisesRegex(CaptureError, 'REDIRECT_BLOCKED'):
            NoRedirect().redirect_request(urllib.request.Request(DATA_URL), None, 302, '', {}, 'https://other.invalid/token')

    def test_arbitrary_transport_exception_is_sanitized_and_attempt_stays_consumed(self):
        def failed(request, limit):
            raise RuntimeError('synthetic-secret synthetic-access-token')
        with self.assertRaisesRegex(CaptureError, '^LOCAL_FAILURE_ATTEMPT_CONSUMED$'):
            self.run_capture(send=failed)
        receipt = (self.output / 'receipt.json').read_text()
        self.assertNotIn('synthetic-secret', receipt)
        self.assertEqual(json.loads(receipt)['auth_attempts'], 1)

    def test_output_inside_repository_is_refused(self):
        from capture_mem_notams import REPOSITORY
        self.reservation['output_dir'] = str(REPOSITORY / 'data' / 'capture')
        with self.assertRaisesRegex(CaptureError, 'OUTSIDE_REPOSITORY'):
            check_reservation(self.reservation, NOW)
