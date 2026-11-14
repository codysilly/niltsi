"""No actual HTTP: injected connections only, including merged-engine integration."""
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from nms_http import HTTPAdapter, HTTPAdapterError
from nms_refresh import collect
from nms_refresh_contract import (Request, auth_request, initial_request, delta_request,
                                  content_request, DecodedInitial, RefreshError)
from test_nms_refresh import synthetic_store, envelope, feature, NOW, CREDENTIALS, TOKEN


def bearer(request):
    return Request(request.stage,request.url,{**request.headers,'Authorization':'Bearer '+TOKEN},request.body,request.method)


class Response:
    def __init__(self, body=b'{}', status=200, headers=None):
        self.body,self.status,self.headers=body,status,headers or {}
        self.offset=0;self.reads=[];self.closed=False;self.on_read=None
    def getheader(self,key):return self.headers.get(key)
    def read1(self,size):
        self.reads.append(size)
        if self.on_read:self.on_read()
        value=self.body[self.offset:self.offset+size];self.offset+=len(value);return value
    def close(self):self.closed=True


class Connection:
    def __init__(self,response,error=None):
        self.response,self.error=response,error;self.sock=self;self.requests=[];self.timeouts=[];self.closed=False
    def settimeout(self,value):self.timeouts.append(value)
    def request(self,*args,**kwargs):
        self.requests.append((args,kwargs))
        if self.error:raise self.error
    def getresponse(self):return self.response
    def close(self):self.closed=True


class Factory:
    def __init__(self,responses):self.responses=list(responses);self.calls=[];self.connections=[]
    def __call__(self,host,**kwargs):
        self.calls.append((host,kwargs));item=self.responses.pop(0)
        conn=Connection(item if not isinstance(item,Exception) else Response(),item if isinstance(item,Exception) else None)
        self.connections.append(conn);return conn


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.block=patch.object(socket,'create_connection',side_effect=AssertionError('Actual networking forbidden'))
        self.block.start();self.addCleanup(self.block.stop)

    def assert_code(self,code,call):
        with self.assertRaises(HTTPAdapterError) as error:call()
        self.assertEqual(str(error.exception),code)
        self.assertNotIn(TOKEN,repr(error.exception));self.assertNotIn(CREDENTIALS['client_secret'],str(error.exception))

    def test_import_and_construction_do_not_open_connections(self):
        factory=Factory([]);adapter=HTTPAdapter(connection_factory=factory)
        self.assertFalse(factory.calls)
        script='''import sys\nsys.path.insert(0,sys.argv[1])\ndef block(event,args):\n if event.startswith("socket."):raise RuntimeError("Network forbidden")\nsys.addaudithook(block)\nimport nms_http\nnms_http.HTTPAdapter()\nprint("NO NETWORK")\n'''
        child=subprocess.run([sys.executable,'-B','-c',script,str(Path(__file__).resolve().parents[1]/'scripts')],capture_output=True,timeout=15)
        self.assertEqual(child.returncode,0);self.assertEqual(child.stdout.strip(),b'NO NETWORK');self.assertFalse(child.stderr)

    def test_prepared_endpoints_tls_and_exact_bounded_bytes(self):
        plans=[auth_request(CREDENTIALS),bearer(initial_request()),bearer(initial_request('DOMESTIC')),
               bearer(delta_request('2026-01-02T11:59:00Z',NOW)),
               bearer(content_request('/nmsapi/v1/content/SYNTHETIC%2Ftoken=',NOW.isoformat(),NOW))]
        for plan in plans:
            response=Response(b'SYNTHETIC\r\nbytes',headers={'Content-Length':'16'})
            response.headers['Content-Length']=str(len(response.body))
            factory=Factory([response]);result=HTTPAdapter(connection_factory=factory)(plan,1024)
            self.assertEqual(result,(200,{},response.body));self.assertEqual(len(factory.calls),1)
            host,settings=factory.calls[0];self.assertEqual(host,'api-nms.aim.faa.gov');self.assertEqual(settings['port'],443)
            self.assertTrue(settings['context'].check_hostname);self.assertEqual(settings['context'].verify_mode,ssl.CERT_REQUIRED)
            self.assertTrue(all(0<t<=30 for t in factory.connections[0].timeouts));self.assertTrue(response.closed)
            self.assertTrue(factory.connections[0].closed);self.assertEqual(factory.connections[0].debuglevel,0)
            self.assertTrue(all(0<n<=1025 for n in response.reads))

    def test_allowlist_rejects_manipulated_targets_before_connection(self):
        bad=[
            Request('auth','https://evil.invalid/v1/auth/token'),
            Request('delta','http://api-nms.aim.faa.gov/nmsapi/v1/notams?lastUpdatedDate=x'),
            Request('delta','https://api-nms.aim.faa.gov.evil.invalid/nmsapi/v1/notams'),
            Request('delta','https://user@api-nms.aim.faa.gov/nmsapi/v1/notams'),
            bearer(Request('content','https://api-nms.aim.faa.gov/nmsapi/v1/content/%2e%2e%2fauth')),
            bearer(Request('content','https://api-nms.aim.faa.gov/nmsapi/v1/content/%252e%252e')),
            bearer(Request('content','https://api-nms.aim.faa.gov/nmsapi/v1/content/token?next=secret')),
            bearer(Request('delta','https://api-nms.aim.faa.gov/v1/auth/token')),
            bearer(Request('delta','https://api-nms.aim.faa.gov/nmsapi/v1/notams?lastUpdatedDate=2026-01-02T11:59:00Z&page=2')),
            bearer(Request('delta','https://api-nms.aim.faa.gov/nmsapi/v1/notams?lastUpdatedDate=x&lastUpdatedDate=y')),
            Request('content','https://api-nms.aim.faa.gov/nmsapi/v1/content/token',{'Authorization':'Bearer '+TOKEN+'\r\nEvil: header'}),
            Request('content','https://api-nms.aim.faa.gov/nmsapi/v1/content/token',{'Authorization':'Bearer '+TOKEN,'Host':'evil.invalid'}),
        ]
        for request in bad:
            with self.subTest(stage=request.stage):
                factory=Factory([]);self.assert_code('HTTP_INVALID_REQUEST',lambda:HTTPAdapter(connection_factory=factory)(request,100))
                self.assertFalse(factory.calls)

    def test_redirects_and_error_bodies_are_not_read_or_followed(self):
        for status in [301,302,303,307,308,401,403,429,500]:
            response=Response((TOKEN+CREDENTIALS['client_secret']).encode(),status,{'Location':'https://evil.invalid/'+TOKEN})
            factory=Factory([response]);adapter=HTTPAdapter(connection_factory=factory)
            if status<400:self.assert_code('HTTP_REDIRECT_REFUSED',lambda:adapter(bearer(initial_request()),100))
            else:self.assertEqual(adapter(bearer(initial_request()),100),(status,{},b''))
            self.assertFalse(response.reads);self.assertEqual(len(factory.calls),1);self.assertTrue(response.closed)

    def test_socket_tls_protocol_and_timeout_diagnostics_are_fixed(self):
        for error,code in [(TimeoutError(TOKEN),'HTTP_TIMEOUT'),(ssl.SSLError(TOKEN),'HTTP_TLS_FAILURE'),
                           (OSError(CREDENTIALS['client_secret']),'HTTP_NETWORK_FAILURE'),(ValueError(TOKEN),'HTTP_PROTOCOL_FAILURE')]:
            factory=Factory([error]);self.assert_code(code,lambda:HTTPAdapter(connection_factory=factory)(bearer(initial_request()),100))
            self.assertEqual(len(factory.calls),1);self.assertEqual(len(factory.connections[0].requests),1)

    def test_size_limits_and_elapsed_read_deadline(self):
        for response in [Response(b'x'*6),Response(b'',headers={'Content-Length':'999999'})]:
            self.assert_code('HTTP_RESPONSE_TOO_LARGE',lambda:HTTPAdapter(connection_factory=Factory([response]))(bearer(initial_request()),5))
        self.assert_code('HTTP_PROTOCOL_FAILURE',lambda:HTTPAdapter(connection_factory=Factory([Response(b'x',headers={'Content-Length':'2'})]))(bearer(initial_request()),5))
        now=[0.0];response=Response(b'x');response.on_read=lambda:now.__setitem__(0,31.0)
        self.assert_code('HTTP_TIMEOUT',lambda:HTTPAdapter(connection_factory=Factory([response]),monotonic=lambda:now[0])(bearer(initial_request()),5))

    def test_merged_engine_success_and_timeout_redirect_error_accounting(self):
        for failure in [None,TimeoutError(TOKEN),307,500]:
            with self.subTest(failure=type(failure).__name__),tempfile.TemporaryDirectory() as temp:
                store=synthetic_store(temp);old=store.snapshot()
                auth=Response(json.dumps({'access_token':TOKEN}).encode())
                initial=Response(b'{"status":"Success","data":{"url":"/v1/content/SYNTHETIC"}}')
                content=Response(json.dumps(envelope([feature()])).encode())
                second=initial if failure is None else failure if isinstance(failure,Exception) else Response(b'SECRET',failure)
                factory=Factory([auth,second,content]);adapter=HTTPAdapter(connection_factory=factory)
                def run():return collect(store,mode='bulk',credentials=CREDENTIALS,transport=adapter,clock=lambda:NOW,
                    decode_initial=lambda body,stage:DecodedInitial(json.loads(body),'2026-01-02T11:59:00Z'))
                if failure is None:
                    self.assertEqual(run()['status'],'ACCEPTED_PRIVATE_OUTBOX');self.assertNotEqual(store.snapshot(),old)
                else:
                    with self.assertRaises(RefreshError):run()
                    self.assertEqual(store.snapshot(),old)
                with store.connect() as db:
                    state=store.load(db);self.assertEqual(state['attempt_count'],1)
                    self.assertEqual(state['call_count'],3 if failure is None else 2)
                    if failure is not None:
                        self.assertEqual(db.execute('SELECT status FROM attempts').fetchone()[0],'FAILED_ACCOUNTED')
                self.assertEqual(len(factory.calls),3 if failure is None else 2)


if __name__=='__main__':unittest.main()
