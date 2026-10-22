from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
import os

from ordax_dev_agent.orchestrator_app_launcher import (
    VerifiedOrchestratorInstallation, validate_orchestrator_installation,
    launch_verified_orchestrator, _PinnedInstallation,
)
from ordax_dev_agent.orchestrator_app_bridge import ExpectedOrchestratorIdentity, OrchestratorBridgeUnavailable
from ordax_dev_agent.orchestrator_windows_pipe import WindowsApi, WindowsOrchestratorPipe


class InstallationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.expected = ExpectedOrchestratorIdentity('a' * 40, 'b' * 64, 'c' * 64)
        self.names = ('OrdaX Orchestrator Lab.exe', 'resources/app.asar', 'resources/runtime-build.json')
        for name in self.names:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(name.encode())
        self.receipt = VerifiedOrchestratorInstallation(self.root, tuple((n, hashlib.sha256(n.encode()).hexdigest()) for n in self.names), self.expected)

    def test_receipt_is_typed_complete_and_not_an_app_manifest(self):
        self.assertIs(validate_orchestrator_installation(self.receipt), self.receipt)
        for value in ({'root': str(self.root)}, replace(self.receipt, file_hashes=self.receipt.file_hashes[:2]), replace(self.receipt, expected=replace(self.expected, source_commit='main'))):
            with self.assertRaises(OrchestratorBridgeUnavailable):
                validate_orchestrator_installation(value)

    def test_traversal_aliases_ads_and_case_duplicates_are_rejected(self):
        for name in ('../escape', 'resources/../escape', '/absolute', 'resources\\escape', 'resources/app.asar:payload', 'resources/app.asar.', 'resources//alias', self.names[0].lower()):
            with self.subTest(name=name), self.assertRaises(OrchestratorBridgeUnavailable):
                validate_orchestrator_installation(replace(self.receipt, file_hashes=self.receipt.file_hashes + ((name, 'a' * 64),)))

    def test_absent_authority_never_reaches_file_hashing_or_process_creation(self):
        with patch('ordax_dev_agent.orchestrator_app_launcher.WindowsApi') as api, patch('subprocess.Popen') as spawn:
            with self.assertRaises(OrchestratorBridgeUnavailable):
                launch_verified_orchestrator(installation=self.receipt, authorize=lambda: None)
            api.assert_not_called()
            spawn.assert_not_called()

    def test_inventory_and_hash_mismatch_release_all_pins(self):
        class FakeApi:
            def __init__(self):
                self.opened, self.closed = [], []
                self.kernel = self
            def pin_file(self, path, directory=False):
                result = (path, directory)
                self.opened.append(result)
                return result
            def hash_handle(self, handle):
                return hashlib.sha256(handle[0].read_bytes()).hexdigest()
            def CloseHandle(self, handle):
                self.closed.append(handle)
        for tamper in ('extra', 'modified'):
            with self.subTest(tamper=tamper):
                api = FakeApi()
                if tamper == 'extra':
                    (self.root / 'injected.dll').write_bytes(b'injected')
                else:
                    (self.root / self.names[1]).write_bytes(b'tampered')
                with self.assertRaises(OrchestratorBridgeUnavailable):
                    _PinnedInstallation(self.receipt, api)
                self.assertCountEqual(api.opened, api.closed)
                (self.root / 'injected.dll').unlink(missing_ok=True)


@unittest.skipUnless(os.name == 'nt', 'Windows kernel pipe contracts')
class WindowsPrivatePipeTests(unittest.TestCase):
    def child(self, pipe, mode='echo'):
        source = r'''
import ctypes as c,json,sys,time
from ctypes import wintypes as w
from ordax_dev_agent.orchestrator_windows_pipe import WindowsApi
api=WindowsApi();k=api.kernel
h=k.CreateFileW(sys.argv[1],0xC0000000,0,None,3,0,None)
if h in (None,c.c_void_p(-1).value):sys.exit(3)
buf=c.create_string_buffer(65536);count=w.DWORD()
k.ReadFile(h,buf,len(buf),c.byref(count),None)
if sys.argv[2]=='stall':time.sleep(5)
else:
 frame=json.loads(buf.raw[:count.value]);data=json.dumps({'echo':frame},ensure_ascii=False).encode()+b'\n'
 k.WriteFile(h,c.create_string_buffer(data),len(data),c.byref(count),None)
k.CloseHandle(h)
'''
        child = subprocess.Popen([sys.executable, '-c', source, pipe.name, mode], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.dispose, child)
        return child

    @staticmethod
    def dispose(child):
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=10)

    def test_actual_private_pipe_roundtrip_preserves_unicode_and_exact_child_pid(self):
        pipe = WindowsOrchestratorPipe()
        self.addCleanup(pipe.close)
        child = self.child(pipe)
        pipe.bind_child(child)
        self.assertEqual(pipe({'project': 'ação'}), {'echo': {'project': 'ação'}})

    def test_other_process_is_rejected_before_any_bootstrap_byte(self):
        pipe = WindowsOrchestratorPipe()
        self.addCleanup(pipe.close)
        self.child(pipe)
        other = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(5)'])
        self.addCleanup(self.dispose, other)
        with self.assertRaisesRegex(OrchestratorBridgeUnavailable, 'host_pipe_peer_rejected'):
            pipe.bind_child(other)
        self.assertTrue(pipe._closed)

    def test_connect_and_reply_deadlines_cancel_kernel_io_without_retries(self):
        pipe = WindowsOrchestratorPipe(timeout_seconds=0.05)
        self.addCleanup(pipe.close)
        other = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(5)'])
        self.addCleanup(self.dispose, other)
        started = time.monotonic()
        with self.assertRaises(OrchestratorBridgeUnavailable):
            pipe.bind_child(other)
        self.assertLess(time.monotonic() - started, 2)
        second = WindowsOrchestratorPipe()
        self.addCleanup(second.close)
        child = self.child(second, 'stall')
        second.bind_child(child)
        second.timeout = 0.05
        started = time.monotonic()
        with self.assertRaises(OrchestratorBridgeUnavailable):
            second({'read': True})
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(second._closed)
        with self.assertRaises(OrchestratorBridgeUnavailable):
            second({'read': True})

    def test_kernel_pipe_dacl_has_only_the_current_user(self):
        import ctypes as c
        from ctypes import wintypes as w
        pipe = WindowsOrchestratorPipe()
        self.addCleanup(pipe.close)
        api = pipe.api
        get = api.advapi.GetSecurityInfo
        get.argtypes = [w.HANDLE, c.c_int, w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p, c.POINTER(c.c_void_p)]
        get.restype = w.DWORD
        convert = api.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(w.LPWSTR), c.c_void_p]
        convert.restype = w.BOOL
        descriptor, text = c.c_void_p(), w.LPWSTR()
        try:
            self.assertEqual(get(pipe.handle, 6, 4, None, None, None, None, c.byref(descriptor)), 0)
            self.assertTrue(convert(descriptor, 1, 4, c.byref(text), None))
            self.assertTrue(text.value.startswith('D:P'))
            self.assertEqual(text.value.count('(A;'), 1)
            self.assertNotIn(';;;WD)', text.value)
            self.assertNotIn(';;;AN)', text.value)
        finally:
            if text:
                api.kernel.LocalFree(c.cast(text, c.c_void_p))
            if descriptor:
                api.kernel.LocalFree(descriptor)

    def test_locked_installation_file_cannot_be_changed_or_replaced(self):
        api = WindowsApi()
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'app.asar'
            target.write_bytes(b'pinned')
            handle = api.pin_file(target)
            try:
                self.assertEqual(api.hash_handle(handle), hashlib.sha256(b'pinned').hexdigest())
                with self.assertRaises(PermissionError):
                    target.write_bytes(b'changed')
                other = Path(root) / 'replacement'
                other.write_bytes(b'changed')
                with self.assertRaises(PermissionError):
                    other.replace(target)
            finally:
                api.kernel.CloseHandle(handle)
            target.write_bytes(b'allowed after release')


if __name__ == '__main__':
    unittest.main()
