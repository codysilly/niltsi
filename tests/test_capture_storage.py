"""Real local storage tests; all payloads/reservations/credentials are synthetic."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from capture_storage import PosixStorage, StorageError, persist_receipt
from test_mem_capture import ENV, NOW
from capture_mem_notams import capture, CaptureError


@unittest.skipUnless(os.name == 'nt', 'Native Windows ACL/persistence tests')
class WindowsStorageTests(unittest.TestCase):
    def setUp(self):
        from capture_storage_windows import WindowsStorage
        self.storage = WindowsStorage()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'attempt'
        self.reservation = {
            'schema_version':1, 'ledger':'SPA-26', 'state':'RESERVED',
            'airport':'KMEM', 'collector':'mem-budget', 'auth_attempt_limit':1, 'data_attempt_limit':1,
            'reservation_id':'00000000-0000-4000-8000-000000000001',
            'not_before_utc':'2026-11-13T07:44:03.558Z', 'reserved_utc':'2026-11-13T07:59:00Z',
            'expires_utc':'2026-11-13T08:10:00Z', 'output_dir':str(self.output)}
        self.calls = []

    def run_capture(self, send=None):
        def fake(request, limit):
            self.calls.append(request.full_url)
            return 200, {}, b'{"access_token":"synthetic-token"}'
        return capture(self.reservation, env=ENV, send=send or fake, clock=lambda:NOW, storage=self.storage)

    def set_dacl(self, path, *, broad=False, protected=True):
        # Actual SetFileSecurityW tampering: verify the backend detects insecure
        # native ACLs, rather than trusting its own creation flags or chmod bits.
        sddl = 'O:'+self.storage.user_sid+'D:'+('P' if protected else '')
        sddl += ''.join('(A;;FA;;;'+sid+')' for sid in sorted(self.storage.allowed_sids))
        if broad:
            sddl += '(A;;FR;;;WD)'
        function = self.storage.security.SetFileSecurityW
        function.argtypes, function.restype = [W.LPCWSTR,W.DWORD,W.LPVOID], W.BOOL
        with self.storage.security_attributes(sddl) as attributes:
            flags = 4 | (0x80000000 if protected else 0x20000000)
            self.assertTrue(function(str(path), flags, attributes.descriptor))

    def test_protected_owner_and_system_only_dacl_on_actual_directory_and_file(self):
        with patch('os.chmod', side_effect=AssertionError('Windows privacy must not use chmod')):
            self.storage.create_attempt(self.output)
            file = self.output/'private.json'
            self.storage.private_write(file,b'{"synthetic":true}')
        for path in (self.output,file):
            info = self.storage.security_info(path)
            self.assertEqual(info['owner'],self.storage.user_sid)
            self.assertTrue(info['protected'])
            self.assertEqual({e['sid'] for e in info['entries']},{self.storage.user_sid,'S-1-5-18'})
            self.assertTrue(all(e['mask']==0x1F01FF and e['flags']==0 for e in info['entries']))
            self.storage.verify_private(path)

    def test_acl_is_private_before_first_payload_write(self):
        self.storage.create_attempt(self.output)
        original = self.storage.kernel.WriteFile
        checked=[]
        def checked_write(handle,buffer,length,written,overlapped):
            info=self.storage.security_info(handle=handle)
            self.assertTrue(info['protected'])
            self.assertEqual({e['sid'] for e in info['entries']},self.storage.allowed_sids)
            checked.append(True)
            return original(handle,buffer,length,written,overlapped)
        with patch.object(self.storage.kernel,'WriteFile',side_effect=checked_write):
            self.storage.private_write(self.output/'private.bin',b'synthetic bytes')
        self.assertEqual(checked,[True])

    def test_real_broad_dacl_and_inheritance_are_rejected(self):
        self.storage.create_attempt(self.output)
        path=self.output/'private.bin'
        self.storage.private_write(path,b'synthetic bytes')
        self.set_dacl(path,broad=True)
        with self.assertRaisesRegex(StorageError,'PRIVATE_ACCESS_CHECK_FAILED'):
            self.storage.verify_private(path)
        self.set_dacl(path,protected=False)
        self.assertFalse(self.storage.security_info(path)['protected'])
        with self.assertRaisesRegex(StorageError,'PRIVATE_ACCESS_CHECK_FAILED'):
            self.storage.verify_private(path)

    def test_explicit_restricted_token_cannot_read_private_file(self):
        # AccessCheck is local: no logon, password, account lookup, or network.
        # A restricting Everyone SID must not gain FILE_READ_DATA through the DACL.
        from capture_storage_windows import SidAndAttributes
        self.storage.create_attempt(self.output)
        path=self.output/'private.bin'
        self.storage.private_write(path,b'synthetic bytes')
        api=self.storage.security
        def bind(name,args):
            fn=getattr(api,name);fn.argtypes=args;fn.restype=W.BOOL
            return fn
        restrict=bind('CreateRestrictedToken',[W.HANDLE,W.DWORD,W.DWORD,W.LPVOID,W.DWORD,W.LPVOID,W.DWORD,W.LPVOID,C.POINTER(W.HANDLE)])
        convert=bind('ConvertStringSidToSidW',[W.LPCWSTR,C.POINTER(W.LPVOID)])
        duplicate=bind('DuplicateToken',[W.HANDLE,C.c_int,C.POINTER(W.HANDLE)])
        class GenericMapping(C.Structure):
            _fields_=[('read',W.DWORD),('write',W.DWORD),('execute',W.DWORD),('all',W.DWORD)]
        access=bind('AccessCheck',[W.LPVOID,W.HANDLE,W.DWORD,C.POINTER(GenericMapping),W.LPVOID,C.POINTER(W.DWORD),C.POINTER(W.DWORD),C.POINTER(W.BOOL)])
        token,restricted,impersonated=W.HANDLE(),W.HANDLE(),W.HANDLE()
        sid=W.LPVOID()
        try:
            self.assertTrue(api.OpenProcessToken(self.storage.kernel.GetCurrentProcess(),0xA,C.byref(token)))
            self.assertTrue(convert('S-1-1-0',C.byref(sid)))
            restricting=SidAndAttributes(sid,0)
            self.assertTrue(restrict(token,0,0,None,0,None,1,C.byref(restricting),C.byref(restricted)))
            self.assertTrue(duplicate(restricted,2,C.byref(impersonated)))
            handle=self.storage._open_existing(path)
            try:
                size=W.DWORD()
                api.GetKernelObjectSecurity(handle,7,None,0,C.byref(size))
                descriptor=C.create_string_buffer(size.value)
                self.assertTrue(api.GetKernelObjectSecurity(handle,7,descriptor,size,C.byref(size)))
            finally:
                self.storage._close(handle)
            mapping=GenericMapping(0x120089,0x120116,0x1200A0,0x1F01FF)
            privileges=C.create_string_buffer(2048)
            length,granted,status=W.DWORD(2048),W.DWORD(),W.BOOL()
            self.assertTrue(access(descriptor,impersonated,1,C.byref(mapping),privileges,C.byref(length),C.byref(granted),C.byref(status)))
            self.assertFalse(status.value)
            self.assertEqual(granted.value,0)
        finally:
            for handle in (impersonated,restricted,token):
                if handle:
                    self.storage._close(handle)
            if sid:
                self.storage.kernel.LocalFree(sid)

    def test_native_receipt_replacement_preserves_private_acl_and_exact_content(self):
        self.storage.create_attempt(self.output)
        for count in range(4):
            receipt={'synthetic':True,'auth_attempts':min(count,1),'data_attempts':min(max(count-1,0),1)}
            persist_receipt(self.storage,self.output,receipt)
            self.assertEqual(json.loads((self.output/'receipt.json').read_text()),receipt)
            self.assertFalse((self.output/'receipt.next').exists())
            self.storage.verify_private(self.output/'receipt.json')

    def test_native_atomic_replacement_never_exposes_partial_or_missing_receipt(self):
        self.storage.create_attempt(self.output)
        persist_receipt(self.storage,self.output,{'generation':0,'padding':'x'*1024})
        stop=threading.Event();started=threading.Event();errors=[];observations=[]
        read=self.storage.kernel.ReadFile
        read.argtypes,read.restype=[W.HANDLE,W.LPVOID,W.DWORD,C.POINTER(W.DWORD),W.LPVOID],W.BOOL
        def reader():
            try:
                while not stop.is_set():
                    # Explicit FILE_SHARE_DELETE permits atomic replacement while
                    # a reader has the previous immutable file open.
                    handle=self.storage._open_existing(self.output/'receipt.json',0x80000000)
                    try:
                        buffer=C.create_string_buffer(8192);count=W.DWORD()
                        self.assertTrue(read(handle,buffer,len(buffer),C.byref(count),None))
                        value=json.loads(buffer.raw[:count.value])
                        self.assertEqual(value['padding'],'x'*1024)
                        observations.append(value['generation']);started.set()
                    finally:
                        self.storage._close(handle)
            except BaseException as error:
                errors.append(error);started.set()
        thread=threading.Thread(target=reader);thread.start()
        try:
            self.assertTrue(started.wait(5))
            for count in range(1,15):
                persist_receipt(self.storage,self.output,{'generation':count,'padding':'x'*1024})
        finally:
            stop.set();thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[])
        self.assertTrue(observations)
        self.assertTrue(all(0<=v<=14 for v in observations))
        self.assertEqual(json.loads((self.output/'receipt.json').read_text())['generation'],14)

    def test_creation_access_failure_stops_before_auth(self):
        def denied(*args):
            C.set_last_error(5)
            return 0
        with patch.object(self.storage.kernel,'CreateDirectoryW',side_effect=denied):
            with self.assertRaisesRegex(CaptureError,'PRIVATE_STORAGE_SETUP_FAILED'):
                self.run_capture()
        self.assertEqual(self.calls,[])

    def test_unknown_capture_error_message_is_not_written_or_returned(self):
        def failed(request,limit):
            self.calls.append(request.full_url)
            raise CaptureError('synthetic-secret synthetic-token')
        with self.assertRaisesRegex(CaptureError,'^LOCAL_FAILURE_ATTEMPT_CONSUMED$'):
            self.run_capture(send=failed)
        receipt=(self.output/'receipt.json').read_text()
        self.assertNotIn('synthetic-secret',receipt)
        self.assertNotIn('synthetic-token',receipt)
        self.assertEqual(len(self.calls),1)

    def test_file_flush_failure_is_not_skipped_or_retried(self):
        with patch.object(self.storage.kernel,'FlushFileBuffers',return_value=0) as flush:
            with self.assertRaisesRegex(CaptureError,'PRIVATE_STORAGE_FAILED_DO_NOT_RETRY'):
                self.run_capture()
        self.assertEqual(flush.call_count,1)
        self.assertEqual(self.calls,[])
        self.assertTrue(self.output.exists())
        with self.assertRaisesRegex(CaptureError,'DIRECTORY_EXISTS'):
            self.run_capture()

    def test_replacement_failure_stops_before_auth(self):
        with patch.object(self.storage.kernel,'MoveFileExW',return_value=0) as move:
            with self.assertRaisesRegex(CaptureError,'PRIVATE_STORAGE_FAILED_DO_NOT_RETRY'):
                self.run_capture()
        self.assertEqual(move.call_count,1)
        self.assertEqual(self.calls,[])
        self.assertTrue((self.output/'receipt.next').exists())

    def test_existing_receipt_replacement_is_preflighted_before_auth(self):
        original=self.storage._publish_atomic
        calls=[]
        def fail_second(source,target):
            calls.append(True)
            if len(calls)==2:
                raise StorageError('TEST_FAILURE')
            original(source,target)
        with patch.object(self.storage,'_publish_atomic',side_effect=fail_second):
            with self.assertRaisesRegex(CaptureError,'PRIVATE_STORAGE_FAILED'):
                self.run_capture()
        self.assertEqual(self.calls,[])
        self.assertEqual(json.loads((self.output/'receipt.json').read_text())['auth_attempts'],0)

    def test_acl_tampering_after_creation_blocks_auth(self):
        create=self.storage.create_attempt
        def tampered(path):
            create(path);self.set_dacl(path,broad=True)
        with patch.object(self.storage,'create_attempt',side_effect=tampered):
            with self.assertRaisesRegex(CaptureError,'PRIVATE_STORAGE_FAILED'):
                self.run_capture()
        self.assertEqual(self.calls,[])

    def test_security_descriptor_query_failure_blocks_auth(self):
        with patch.object(self.storage.security,'GetKernelObjectSecurity',return_value=0):
            with self.assertRaisesRegex(CaptureError,'PRIVATE_STORAGE_SETUP_FAILED'):
                self.run_capture()
        self.assertEqual(self.calls,[])

    def test_post_capture_receipt_failure_keeps_private_raw_response_and_counters(self):
        body=(Path(__file__).parent/'fixtures/nms_reader_documentation.json').read_bytes()
        def send(request,limit):
            self.calls.append(request.full_url)
            return (200,{},b'{"access_token":"synthetic-token"}') if len(self.calls)==1 else (200,{},body)
        original=self.storage._publish_atomic
        def fail_final(source,target):
            if json.loads(Path(source).read_text())['state']=='CAPTURED_UNVERIFIED':
                raise StorageError('TEST_FAILURE')
            original(source,target)
        with patch.object(self.storage,'_publish_atomic',side_effect=fail_final):
            with self.assertRaisesRegex(CaptureError,'PRIVATE_STORAGE_FAILED'):
                self.run_capture(send=send)
        self.assertEqual(len(self.calls),2)
        self.assertEqual((self.output/'response.json').read_bytes(),body)
        self.storage.verify_private(self.output/'response.json')
        receipt=json.loads((self.output/'receipt.json').read_text())
        self.assertEqual((receipt['auth_attempts'],receipt['data_attempts']),(1,1))
        with self.assertRaisesRegex(CaptureError,'DIRECTORY_EXISTS'):
            self.run_capture(send=send)
        self.assertEqual(len(self.calls),2)

    def test_data_accounting_persistence_failure_prevents_get_after_one_auth(self):
        original=self.storage._publish_atomic
        def fail_data(source,target):
            if json.loads(Path(source).read_text())['data_attempts']==1:
                raise StorageError('TEST_FAILURE')
            original(source,target)
        with patch.object(self.storage,'_publish_atomic',side_effect=fail_data):
            with self.assertRaisesRegex(CaptureError,'PRIVATE_STORAGE_FAILED'):
                self.run_capture()
        self.assertEqual(len(self.calls),1)
        receipt=json.loads((self.output/'receipt.json').read_text())
        self.assertEqual((receipt['auth_attempts'],receipt['data_attempts']),(1,0))

    def test_final_destination_flush_failure_also_blocks_auth(self):
        original=self.storage._open_existing
        destination_opened=[]
        def opened(path,access=0x20000):
            handle=original(path,access)
            if Path(path).name=='receipt.json' and access & 0x40000000:
                destination_opened.append(handle)
            return handle
        flush=self.storage._flush_file
        def fail(handle):
            if handle in destination_opened:
                raise StorageError('WINDOWS_FILE_FLUSH_FAILED')
            flush(handle)
        with patch.object(self.storage,'_open_existing',side_effect=opened), patch.object(self.storage,'_flush_file',side_effect=fail):
            with self.assertRaisesRegex(CaptureError,'PRIVATE_STORAGE_FAILED'):
                self.run_capture()
        self.assertEqual(self.calls,[])
        self.assertTrue(destination_opened)

    def test_unc_and_injected_reparse_point_paths_are_refused(self):
        with self.assertRaisesRegex(StorageError,'LOCAL_PRIVATE_PARENT_REQUIRED'):
            self.storage.validate_location(Path(r'\\unreachable.invalid\share\attempt'))
        original=self.storage.kernel.GetFileAttributesW
        def attributes(path):
            return 0x410 if path==str(self.output.parent) else original(path)
        with patch.object(self.storage.kernel,'GetFileAttributesW',side_effect=attributes):
            with self.assertRaisesRegex(StorageError,'REPARSE'):
                self.storage.validate_location(self.output)


@unittest.skipUnless(os.name == 'posix', 'POSIX owner/mode/fsync tests; not Windows emulation')
class PosixStorageTests(unittest.TestCase):
    def test_private_modes_replace_and_replay(self):
        with tempfile.TemporaryDirectory() as root:
            storage=PosixStorage();path=Path(root)/'attempt';storage.create_attempt(path)
            persist_receipt(storage,path,{'auth_attempts':0})
            persist_receipt(storage,path,{'auth_attempts':1})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o700)
            self.assertEqual(stat.S_IMODE((path/'receipt.json').stat().st_mode),0o600)
            self.assertEqual(json.loads((path/'receipt.json').read_text()),{'auth_attempts':1})
            with self.assertRaises(FileExistsError):
                storage.create_attempt(path)

    def test_failed_directory_flush_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as root, patch('capture_storage.os.fsync',side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                PosixStorage().create_attempt(Path(root)/'attempt')
