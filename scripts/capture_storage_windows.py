"""Native Windows local-NTFS private storage, using documented Win32 APIs.

Protected owner+SYSTEM DACLs are supplied at creation, never chmod approximations.
File buffers are flushed; initial publication uses MoveFileExW WRITE_THROUGH and
replacement uses FileRenameInfoEx (replace + native POSIX sharing semantics).
This is a Win32 API, not WSL. The destination is reopened and
flushed again. This relies on
Windows/device flush semantics, not a claim to survive a dishonest disk cache.
"""
import ctypes as C
from ctypes import wintypes as W
from contextlib import contextmanager
from pathlib import Path

from capture_storage import StorageError

FILE_ALL_ACCESS = 0x1F01FF
READ_CONTROL = 0x20000
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ = 1
CREATE_NEW, OPEN_EXISTING = 1, 3
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_FLAG_WRITE_THROUGH = 0x80000000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
MOVEFILE_WRITE_THROUGH = 8
INVALID_HANDLE_VALUE = C.c_void_p(-1).value
OWNER_SECURITY_INFORMATION, DACL_SECURITY_INFORMATION = 1, 4
SE_DACL_PROTECTED = 0x1000


class SecurityAttributes(C.Structure):
    _fields_ = [('length', W.DWORD), ('descriptor', W.LPVOID), ('inherit', W.BOOL)]


class SidAndAttributes(C.Structure):
    _fields_ = [('sid', W.LPVOID), ('attributes', W.DWORD)]


class AclSizeInformation(C.Structure):
    _fields_ = [('count', W.DWORD), ('bytes_used', W.DWORD), ('bytes_free', W.DWORD)]


class AceHeader(C.Structure):
    _fields_ = [('type', W.BYTE), ('flags', W.BYTE), ('size', W.WORD)]


class FileRenameInfo(C.Structure):
    _fields_ = [('flags',W.DWORD), ('root',W.HANDLE), ('length',W.DWORD), ('name',W.WCHAR*1)]


class WindowsStorage:
    name = 'windows-protected-dacl-write-through'

    def __init__(self):
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.security = C.WinDLL('advapi32', use_last_error=True)
        self._bind()
        self.user_sid = self._current_user_sid()
        self.allowed_sids = {self.user_sid, 'S-1-5-18'}

    def _bind(self):
        def bind(dll, name, args, result=W.BOOL):
            function = getattr(dll, name)
            function.argtypes, function.restype = args, result
        bind(self.kernel, 'GetCurrentProcess', [], W.HANDLE)
        bind(self.kernel, 'CloseHandle', [W.HANDLE])
        bind(self.kernel, 'LocalFree', [W.LPVOID], W.LPVOID)
        bind(self.kernel, 'CreateDirectoryW', [W.LPCWSTR, C.POINTER(SecurityAttributes)])
        bind(self.kernel, 'CreateFileW', [W.LPCWSTR, W.DWORD, W.DWORD, C.POINTER(SecurityAttributes), W.DWORD, W.DWORD, W.HANDLE], W.HANDLE)
        bind(self.kernel, 'WriteFile', [W.HANDLE, W.LPVOID, W.DWORD, C.POINTER(W.DWORD), W.LPVOID])
        bind(self.kernel, 'FlushFileBuffers', [W.HANDLE])
        bind(self.kernel, 'MoveFileExW', [W.LPCWSTR, W.LPCWSTR, W.DWORD])
        bind(self.kernel, 'SetFileInformationByHandle', [W.HANDLE, C.c_int, W.LPVOID, W.DWORD])
        bind(self.kernel, 'GetFileAttributesW', [W.LPCWSTR], W.DWORD)
        bind(self.kernel, 'GetVolumePathNameW', [W.LPCWSTR, W.LPWSTR, W.DWORD])
        bind(self.kernel, 'GetDriveTypeW', [W.LPCWSTR], W.UINT)
        bind(self.kernel, 'GetVolumeInformationW', [W.LPCWSTR, W.LPWSTR, W.DWORD, W.LPVOID, W.LPVOID, C.POINTER(W.DWORD), W.LPWSTR, W.DWORD])
        bind(self.security, 'OpenProcessToken', [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)])
        bind(self.security, 'GetTokenInformation', [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)])
        bind(self.security, 'ConvertSidToStringSidW', [W.LPVOID, C.POINTER(W.LPWSTR)])
        bind(self.security, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', [W.LPCWSTR, W.DWORD, C.POINTER(W.LPVOID), W.LPVOID])
        bind(self.security, 'GetKernelObjectSecurity', [W.HANDLE, W.DWORD, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)])
        bind(self.security, 'GetSecurityDescriptorOwner', [W.LPVOID, C.POINTER(W.LPVOID), C.POINTER(W.BOOL)])
        bind(self.security, 'GetSecurityDescriptorDacl', [W.LPVOID, C.POINTER(W.BOOL), C.POINTER(W.LPVOID), C.POINTER(W.BOOL)])
        bind(self.security, 'GetSecurityDescriptorControl', [W.LPVOID, C.POINTER(W.WORD), C.POINTER(W.DWORD)])
        bind(self.security, 'GetAclInformation', [W.LPVOID, W.LPVOID, W.DWORD, C.c_int])
        bind(self.security, 'GetAce', [W.LPVOID, W.DWORD, C.POINTER(W.LPVOID)])

    @staticmethod
    def _require(result, code):
        if not result:
            raise StorageError(code)

    def _close(self, handle):
        self._require(self.kernel.CloseHandle(handle), 'WINDOWS_HANDLE_CLOSE_FAILED')

    def _sid_string(self, sid):
        value = W.LPWSTR()
        self._require(self.security.ConvertSidToStringSidW(sid, C.byref(value)), 'WINDOWS_SID_QUERY_FAILED')
        try:
            return value.value
        finally:
            self.kernel.LocalFree(C.cast(value, W.LPVOID))

    def _current_user_sid(self):
        token = W.HANDLE()
        self._require(self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, C.byref(token)), 'WINDOWS_IDENTITY_QUERY_FAILED')
        try:
            size = W.DWORD()
            self.security.GetTokenInformation(token, 1, None, 0, C.byref(size))
            if not size.value:
                raise StorageError('WINDOWS_IDENTITY_QUERY_FAILED')
            buffer = C.create_string_buffer(size.value)
            self._require(self.security.GetTokenInformation(token, 1, buffer, size, C.byref(size)), 'WINDOWS_IDENTITY_QUERY_FAILED')
            return self._sid_string(C.cast(buffer, C.POINTER(SidAndAttributes)).contents.sid)
        finally:
            self._close(token)

    @contextmanager
    def security_attributes(self, sddl=None):
        # SYSTEM is explicit for OS maintenance. No Users/Everyone/Administrators
        # grant, inherited ACE, or inheritance to unknown children is accepted.
        sddl = sddl or ('O:'+self.user_sid+'D:P'+''.join('(A;;FA;;;'+sid+')' for sid in sorted(self.allowed_sids)))
        descriptor = W.LPVOID()
        self._require(self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None), 'WINDOWS_SECURITY_DESCRIPTOR_FAILED')
        try:
            yield SecurityAttributes(C.sizeof(SecurityAttributes), descriptor, False)
        finally:
            self.kernel.LocalFree(descriptor)

    def validate_location(self, path):
        path = Path(path)
        if not path.is_absolute() or str(path).startswith(('\\\\', '//')) or not path.parent.is_dir():
            raise StorageError('LOCAL_PRIVATE_PARENT_REQUIRED')
        for ancestor in (path, *path.parents):
            attributes = self.kernel.GetFileAttributesW(str(ancestor))
            if attributes != 0xFFFFFFFF and attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise StorageError('LINK_OR_REPARSE_PATH_REFUSED')
            if attributes == 0xFFFFFFFF and ancestor != path:
                raise StorageError('WINDOWS_PATH_CHECK_FAILED')
        volume, filesystem, flags = C.create_unicode_buffer(261), C.create_unicode_buffer(261), W.DWORD()
        self._require(self.kernel.GetVolumePathNameW(str(path.parent), volume, len(volume)), 'WINDOWS_VOLUME_QUERY_FAILED')
        self._require(self.kernel.GetVolumeInformationW(volume.value, None, 0, None, None, C.byref(flags), filesystem, len(filesystem)), 'WINDOWS_VOLUME_QUERY_FAILED')
        if self.kernel.GetDriveTypeW(volume.value) != 3 or filesystem.value.upper() != 'NTFS' or not flags.value & 8:
            raise StorageError('LOCAL_NTFS_WITH_PERSISTENT_ACLS_REQUIRED')

    def _open_existing(self, path, access=READ_CONTROL):
        handle = self.kernel.CreateFileW(str(path), access, 7, None, OPEN_EXISTING,
                                        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_WRITE_THROUGH, None)
        if handle == INVALID_HANDLE_VALUE:
            raise StorageError('WINDOWS_FILE_OPEN_FAILED')
        return handle

    def security_info(self, path=None, *, handle=None):
        owned = handle is None
        if owned:
            handle = self._open_existing(path)
        try:
            size = W.DWORD()
            mask = OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
            self.security.GetKernelObjectSecurity(handle, mask, None, 0, C.byref(size))
            if not size.value:
                raise StorageError('WINDOWS_ACL_QUERY_FAILED')
            buffer = C.create_string_buffer(size.value)
            self._require(self.security.GetKernelObjectSecurity(handle, mask, buffer, size, C.byref(size)), 'WINDOWS_ACL_QUERY_FAILED')
            owner, dacl, defaulted, present = W.LPVOID(), W.LPVOID(), W.BOOL(), W.BOOL()
            control, revision = W.WORD(), W.DWORD()
            self._require(self.security.GetSecurityDescriptorOwner(buffer, C.byref(owner), C.byref(defaulted)), 'WINDOWS_ACL_QUERY_FAILED')
            self._require(self.security.GetSecurityDescriptorDacl(buffer, C.byref(present), C.byref(dacl), C.byref(defaulted)), 'WINDOWS_ACL_QUERY_FAILED')
            self._require(self.security.GetSecurityDescriptorControl(buffer, C.byref(control), C.byref(revision)), 'WINDOWS_ACL_QUERY_FAILED')
            if not present.value or not dacl.value:
                raise StorageError('PRIVATE_ACCESS_CHECK_FAILED')
            info = AclSizeInformation()
            self._require(self.security.GetAclInformation(dacl, C.byref(info), C.sizeof(info), 2), 'WINDOWS_ACL_QUERY_FAILED')
            entries = []
            for index in range(info.count):
                ace = W.LPVOID()
                self._require(self.security.GetAce(dacl, index, C.byref(ace)), 'WINDOWS_ACL_QUERY_FAILED')
                header = C.cast(ace, C.POINTER(AceHeader)).contents
                if header.type != 0 or header.size < 12:
                    raise StorageError('PRIVATE_ACCESS_CHECK_FAILED')
                entries.append({'sid':self._sid_string(ace.value+8), 'mask':W.DWORD.from_address(ace.value+4).value, 'flags':header.flags})
            return {'owner':self._sid_string(owner), 'protected':bool(control.value & SE_DACL_PROTECTED), 'entries':entries}
        finally:
            if owned:
                self._close(handle)

    def verify_private(self, path=None, *, handle=None):
        info = self.security_info(path, handle=handle)
        if (info['owner'] != self.user_sid or not info['protected']
                or len(info['entries']) != len(self.allowed_sids)
                or {e['sid'] for e in info['entries']} != self.allowed_sids
                or any(e['mask'] != FILE_ALL_ACCESS or e['flags'] for e in info['entries'])):
            raise StorageError('PRIVATE_ACCESS_CHECK_FAILED')

    def create_attempt(self, path):
        self.validate_location(path)
        with self.security_attributes() as attributes:
            if not self.kernel.CreateDirectoryW(str(path), C.byref(attributes)):
                if C.get_last_error() in (80, 183):
                    raise FileExistsError()
                raise StorageError('WINDOWS_PRIVATE_DIRECTORY_CREATE_FAILED')
        self.verify_private(path)

    def _flush_file(self, handle):
        self._require(self.kernel.FlushFileBuffers(handle), 'WINDOWS_FILE_FLUSH_FAILED')

    def private_write(self, path, content):
        self.validate_location(path)
        self.verify_private(Path(path).parent)
        with self.security_attributes() as attributes:
            handle = self.kernel.CreateFileW(str(path), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
                       C.byref(attributes), CREATE_NEW, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH | FILE_FLAG_OPEN_REPARSE_POINT, None)
        if handle == INVALID_HANDLE_VALUE:
            raise StorageError('WINDOWS_PRIVATE_FILE_CREATE_FAILED')
        try:
            self.verify_private(handle=handle)
            buffer, written = C.create_string_buffer(content), W.DWORD()
            self._require(self.kernel.WriteFile(handle, buffer, len(content), C.byref(written), None), 'WINDOWS_FILE_WRITE_FAILED')
            if written.value != len(content):
                raise StorageError('WINDOWS_SHORT_WRITE')
            self._flush_file(handle)
        finally:
            self._close(handle)

    def _publish_atomic(self, source, target):
        if Path(target).exists():
            # One native namespace replacement even while a previous receipt is
            # open for reading. No delete-then-rename window, retries, or fallback
            # to weaker sharing semantics if the OS/filesystem rejects this API.
            encoded = str(target).encode('utf-16-le')
            size = max(C.sizeof(FileRenameInfo), FileRenameInfo.name.offset+len(encoded)+2)
            buffer = C.create_string_buffer(size)
            info = C.cast(buffer,C.POINTER(FileRenameInfo)).contents
            info.flags, info.root, info.length = 3, None, len(encoded)
            C.memmove(C.addressof(buffer)+FileRenameInfo.name.offset,encoded,len(encoded))
            handle = self._open_existing(source, GENERIC_WRITE | READ_CONTROL | 0x10000)
            try:
                self.verify_private(handle=handle)
                self._flush_file(handle)
                self._require(self.kernel.SetFileInformationByHandle(handle,22,buffer,size), 'WINDOWS_RECEIPT_REPLACE_FAILED')
                self._flush_file(handle)
            finally:
                self._close(handle)
        else:
            self._require(self.kernel.MoveFileExW(str(source), str(target), MOVEFILE_WRITE_THROUGH), 'WINDOWS_RECEIPT_REPLACE_FAILED')

    def replace(self, source, target):
        if Path(source).parent != Path(target).parent:
            raise StorageError('SAME_DIRECTORY_REPLACEMENT_REQUIRED')
        self.validate_location(target)
        self.verify_private(source)
        if Path(target).exists():
            self.verify_private(target)
        self._publish_atomic(source, target)
        handle = self._open_existing(target, GENERIC_WRITE | READ_CONTROL)
        try:
            self.verify_private(handle=handle)
            self._flush_file(handle)
        finally:
            self._close(handle)
