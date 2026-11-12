import contextlib
import io
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import inspect_nms_contract as inspector
from nms_adapter import API_URL, FeedError
from update_notams import Client, request_json

CANARY = 'DO-NOT-PRINT-SYNTHETIC-CREDENTIAL'
ENV = {'FAA_NMS_CLIENT_ID': CANARY+'-id', 'FAA_NMS_CLIENT_SECRET': CANARY+'-secret'}


class DiagnosticTests(unittest.TestCase):
    def run_inspector(self, client=None, environment=None, args=('--bulk-airport','KORD')):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, ENV if environment is None else environment, clear=True), patch.object(inspector,'PullBudget'), patch.object(inspector, 'Client') as factory, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if isinstance(client, Exception):
                factory.side_effect = client
            else:
                factory.return_value = client
            result = inspector.main(list(args))
        output = out.getvalue()+err.getvalue()
        self.assertNotIn(CANARY, output)
        return result, output, factory

    def test_missing_environment_no_network(self):
        result, output, factory = self.run_inspector(environment={})
        self.assertEqual(result, 1)
        factory.assert_not_called()
        self.assertIn('FAA_NMS_CLIENT_ID: MISSING', output)
        self.assertIn('no requests made', output)

    def test_check_environment_present_no_network(self):
        result, output, factory = self.run_inspector(args=['--check-environment'])
        self.assertEqual(result, 0)
        factory.assert_not_called()
        self.assertIn('imports succeeded', output)

    def test_authentication_http_statuses_safe(self):
        for status in (401,403,429,503):
            result, output, _ = self.run_inspector(FeedError('HTTP_FAILURE',http_status=status,stage='authentication'))
            self.assertEqual(result, 1)
            self.assertIn(f'HTTP {status}',output)
            self.assertIn('authentication',output)

    def test_api_failure_is_limited_to_selected_airport(self):
        class Fake:
            sensitive=(CANARY,)
            calls=0
            def get(self,url):
                self.calls+=1
                raise FeedError('HTTP_FAILURE',http_status=403,stage='api')
        client=Fake()
        result,output,_=self.run_inspector(client)
        self.assertEqual(result,1)
        self.assertEqual(client.calls,1)
        for airport in ('KORD',):
            self.assertIn(airport+': API authorization failure (HTTP 403)',output)

    def test_unknown_exception_text_never_printed(self):
        result, output, _ = self.run_inspector(RuntimeError(CANARY))
        self.assertEqual(result,1)
        self.assertIn('unexpected local processing failure',output)

    def test_token_response_structure_rejected(self):
        for payload in ({}, [], {'access_token':None}, {'access_token':''}, {'access_token':'has whitespace'}):
            with self.assertRaises(FeedError) as caught:
                Client('test-id','test-secret',fetch=lambda req:payload)
            self.assertEqual(caught.exception.stage,'authentication')
            self.assertIn('unexpected token response structure',inspector.diagnostic(caught.exception,'authentication'))

    def test_network_type_categories_and_no_raw_reason(self):
        for error,kind,expected in (
            (ssl.SSLCertVerificationError(1,CANARY),'TLS_CERTIFICATE','TLS certificate'),
            (ssl.SSLError(1,CANARY),'TLS','TLS handshake'),
            (socket.gaierror(1,CANARY),'DNS','DNS resolution'),
            (TimeoutError(CANARY),'TIMEOUT','timeout'),
            (ConnectionError(CANARY),'CONNECTION','connection failure'),
        ):
            class Opener:
                def open(self,req,timeout):
                    raise urllib.error.URLError(error)
            with self.assertRaises(FeedError) as caught:
                request_json(urllib.request.Request(API_URL),Opener(),lambda _:None)
            self.assertEqual(caught.exception.network_kind,kind)
            message=inspector.diagnostic(caught.exception,'authentication')
            self.assertIn(expected,message)
            self.assertNotIn(CANARY,message)

    def test_http_status_survives_client_wrapping(self):
        class Opener:
            def open(self,req,timeout):
                raise urllib.error.HTTPError(API_URL,401,CANARY,{'Authorization':CANARY},io.BytesIO(CANARY.encode()))
        with self.assertRaises(FeedError) as caught:
            Client('test-id','test-secret',fetch=lambda req:request_json(req,Opener()))
        self.assertEqual(str(caught.exception),'HTTP_FAILURE')
        self.assertEqual(caught.exception.http_status,401)
        self.assertEqual(caught.exception.stage,'authentication')
        self.assertNotIn(CANARY,inspector.diagnostic(caught.exception,'authentication'))

    def test_invalid_json_at_both_stages(self):
        class Opener:
            def open(self,req,timeout):
                return io.BytesIO(('<html>'+CANARY+'</html>').encode())
        with self.assertRaises(FeedError) as caught:
            request_json(urllib.request.Request(API_URL),Opener())
        for stage in ('authentication','api'):
            message=inspector.diagnostic(caught.exception,stage)
            self.assertIn(stage+': JSON parsing failure',message)
            self.assertNotIn(CANARY,message)

    def test_unexpected_envelope_is_structure_only_and_fails(self):
        class Fake:
            sensitive=(CANARY,)
            def get(self,url):
                return {'unexpected': [{'text': CANARY}]}
        result,output,_=self.run_inspector(Fake())
        self.assertEqual(result,1)
        self.assertIn('unexpected response structure',output)
        self.assertIn('first_item_shape',output)

    def test_success_does_not_claim_verified_and_writes_nothing(self):
        class Fake:
            sensitive=(CANARY,)
            def get(self,url):
                return {'status':'SYNTHETIC','data':{'geojson':[]}}
        before=(Path(__file__).resolve().parents[1]/'scripts/nms_contract.json').read_bytes()
        result,output,_=self.run_inspector(Fake())
        self.assertEqual(result,0)
        self.assertIn('coverage remain unverified',output)
        self.assertIn('First response only',output)
        self.assertEqual(before,(Path(__file__).resolve().parents[1]/'scripts/nms_contract.json').read_bytes())


if __name__=='__main__':
    unittest.main()
