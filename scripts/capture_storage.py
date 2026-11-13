"""Private one-attempt local storage. No network or credential handling.

POSIX mode/owner checks and directory fsync are deliberately separate from the
Windows ACL/write-through implementation. No failure is downgraded to a warning.
"""
import json
import os
from pathlib import Path
import stat


class StorageError(Exception):
    """Fixed code only; never include paths or private content in diagnostics."""


class PosixStorage:
    name = 'posix-owner-modes-fsync'

    def validate_location(self, path):
        path = Path(path)
        if not path.is_absolute() or not path.parent.is_dir():
            raise StorageError('PRIVATE_PARENT_REQUIRED')
        for ancestor in (path, *path.parents):
            if ancestor.is_symlink():
                raise StorageError('LINK_OR_REPARSE_PATH_REFUSED')

    def verify_private(self, path):
        info = Path(path).lstat()
        expected = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600
        if (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != expected
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
            raise StorageError('PRIVATE_ACCESS_CHECK_FAILED')

    def sync_directory(self, path):
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def create_attempt(self, path):
        self.validate_location(path)
        Path(path).mkdir(mode=0o700, parents=False, exist_ok=False)
        self.verify_private(path)
        self.sync_directory(Path(path).parent)

    def private_write(self, path, content):
        self.validate_location(path)
        self.verify_private(Path(path).parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        with open(os.open(path, flags, 0o600), 'wb') as stream:
            self.verify_private(path)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        self.sync_directory(Path(path).parent)

    def replace(self, source, target):
        if Path(source).parent != Path(target).parent:
            raise StorageError('SAME_DIRECTORY_REPLACEMENT_REQUIRED')
        self.verify_private(source)
        if Path(target).exists():
            self.verify_private(target)
        os.replace(source, target)
        self.sync_directory(Path(target).parent)
        self.verify_private(target)


def platform_storage():
    if os.name == 'nt':
        from capture_storage_windows import WindowsStorage
        return WindowsStorage()
    if os.name == 'posix':
        return PosixStorage()
    raise StorageError('UNSUPPORTED_STORAGE_PLATFORM')


def persist_receipt(storage, output, receipt):
    content = json.dumps(receipt, indent=2).encode('utf-8')
    temporary = Path(output) / 'receipt.next'
    destination = Path(output) / 'receipt.json'
    storage.verify_private(output)
    storage.private_write(temporary, content)
    storage.replace(temporary, destination)
    if destination.read_bytes() != content:
        raise StorageError('RECEIPT_READBACK_FAILED')
