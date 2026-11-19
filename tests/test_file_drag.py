"""Native Windows contract tests for the MIDI drag source (no UI automation)."""
from __future__ import annotations

import ctypes as ct
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import file_drag as drag


class FORMATETC(ct.Structure):
    _fields_ = [("cfFormat", ct.c_uint16), ("ptd", ct.c_void_p),
                ("dwAspect", ct.c_uint32), ("lindex", ct.c_int32),
                ("tymed", ct.c_uint32)]


class STGMEDIUM(ct.Structure):
    _fields_ = [("tymed", ct.c_uint32), ("hGlobal", ct.c_void_p),
                ("pUnkForRelease", ct.c_void_p)]


@unittest.skipUnless(os.name == "nt", "Windows OLE integration")
class FileDragTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="plain-midi-drag-")
        self.path = Path(self.temporary.name) / "Eight bars — é 日本.mid"
        self.path.write_bytes(b"MThd\x00\x00\x00\x06\x00\x01\x00\x00\x01\xe0")
        self.api = drag._native()
        self.api.ole32.ReleaseStgMedium.argtypes = [ct.POINTER(STGMEDIUM)]
        self.api.ole32.ReleaseStgMedium.restype = None
        self.api.ole32.CoTaskMemFree.argtypes = [ct.c_void_p]
        self.api.ole32.CoTaskMemFree.restype = None
        self.api.shell32.DragQueryFileW.argtypes = [ct.c_void_p, ct.c_uint32,
                                                  ct.c_wchar_p, ct.c_uint32]
        self.api.shell32.DragQueryFileW.restype = ct.c_uint32

    def tearDown(self):
        self.temporary.cleanup()

    def extract(self, medium):
        self.assertEqual(medium.tymed, 1)  # TYMED_HGLOBAL
        query = self.api.shell32.DragQueryFileW
        self.assertEqual(query(medium.hGlobal, 0xffffffff, None, 0), 1)
        length = query(medium.hGlobal, 0, None, 0)
        result = ct.create_unicode_buffer(length + 1)
        self.assertEqual(query(medium.hGlobal, 0, result, length + 1), length)
        return Path(result.value)

    def test_native_data_object_exposes_one_unicode_file_and_repeated_getdata(self):
        """Exercise real Shell IDataObject, HGLOBAL and ReleaseStgMedium."""
        with drag._ole_apartment(self.api), drag._shell_data_object(self.api, self.path) as data:
            fmt = FORMATETC(15, None, 1, -1, 1)  # CF_HDROP / CONTENT / HGLOBAL
            query = drag._method(data, 5, drag.HRESULT, ct.POINTER(FORMATETC))
            self.assertEqual(query(data, ct.byref(fmt)), 0)
            get = drag._method(data, 3, drag.HRESULT, ct.POINTER(FORMATETC), ct.POINTER(STGMEDIUM))
            for _ in range(3):
                medium = STGMEDIUM()
                self.assertEqual(get(data, ct.byref(fmt), ct.byref(medium)), 0)
                try:
                    self.assertEqual(self.extract(medium), self.path)
                finally:
                    self.api.ole32.ReleaseStgMedium(ct.byref(medium))
        self.assertTrue(self.path.exists())

    def test_shell_format_enumerator_advertises_file_drop_and_clones(self):
        with drag._ole_apartment(self.api), drag._shell_data_object(self.api, self.path) as data:
            enumerator = ct.c_void_p()
            enum = drag._method(data, 8, drag.HRESULT, ct.c_uint32, ct.POINTER(ct.c_void_p))
            self.assertEqual(enum(data, 1, ct.byref(enumerator)), 0)
            clone = ct.c_void_p()
            try:
                self.assertEqual(drag._method(enumerator, 6, drag.HRESULT,
                                 ct.POINTER(ct.c_void_p))(enumerator, ct.byref(clone)), 0)
                next_item = drag._method(clone, 3, drag.HRESULT, ct.c_uint32,
                                        ct.POINTER(FORMATETC), ct.POINTER(ct.c_uint32))
                formats = []
                for _ in range(100):
                    fmt, fetched = FORMATETC(), ct.c_uint32()
                    result = next_item(clone, 1, ct.byref(fmt), ct.byref(fetched))
                    if fetched.value:
                        formats.append(fmt.cfFormat)
                        if fmt.ptd:
                            self.api.ole32.CoTaskMemFree(fmt.ptd)
                    if result == 1:
                        break
                    self.assertEqual(result, 0)
                else:
                    self.fail("The native format enumerator did not terminate.")
                self.assertIn(15, formats)
            finally:
                drag._release(clone)
                drag._release(enumerator)

    def test_source_interfaces_lifetime_escape_release_and_feedback(self):
        source = drag._DropSource()
        pointer = source.pointer
        query = drag._method(pointer, 0, drag.HRESULT, ct.POINTER(drag._GUID), ct.POINTER(ct.c_void_p))
        output = ct.c_void_p()
        try:
            for iid in (drag._IID_IUNKNOWN, drag._IID_IDROPSOURCE):
                self.assertEqual(query(pointer, ct.byref(iid), ct.byref(output)), 0)
                self.assertEqual(output.value, pointer.value)
                self.assertEqual(source.references, 2)
                drag._release(output)
            self.assertEqual(query(pointer, ct.byref(drag._IID_IDATAOBJECT), ct.byref(output)), drag.E_NOINTERFACE)
            self.assertIsNone(output.value)
            self.assertEqual(query(pointer, ct.byref(drag._IID_IDROPSOURCE), None), drag.E_POINTER)
            continuation = drag._method(pointer, 3, drag.HRESULT, drag.BOOL, drag.DWORD)
            self.assertEqual(continuation(pointer, 0, 1), drag.S_OK)
            self.assertEqual(continuation(pointer, 0, 0), drag.DRAGDROP_S_DROP)
            self.assertEqual(continuation(pointer, 1, 1), drag.DRAGDROP_S_CANCEL)
            self.assertEqual(continuation(pointer, 1, 0), drag.DRAGDROP_S_CANCEL)
            self.assertEqual(drag._method(pointer, 4, drag.HRESULT, drag.DWORD)(pointer, 1),
                             drag.DRAGDROP_S_USEDEFAULTCURSORS)
        finally:
            drag._release(pointer)
        self.assertNotIn(pointer.value, drag._LIVE_SOURCES)

    def test_copy_only_drag_result_and_cleanup(self):
        """Real data/source objects; substitute only the interactive drag loop."""
        original_live = set(drag._LIVE_SOURCES)
        for code, effect, expected in [(drag.DRAGDROP_S_DROP, 1, True),
                                       (drag.DRAGDROP_S_DROP, 0, False),
                                       (drag.DRAGDROP_S_CANCEL, 0, False)]:
            def loop(data, source, allowed, result):
                self.assertEqual(allowed, drag.DROPEFFECT_COPY)
                ct.cast(result, ct.POINTER(drag.DWORD))[0] = effect
                return code
            with patch.object(self.api.ole32, "DoDragDrop", side_effect=loop):
                self.assertEqual(drag.drag_file(self.path), expected)
            self.assertEqual(set(drag._LIVE_SOURCES), original_live)
        with patch.object(self.api.ole32, "DoDragDrop", return_value=-2147467259):
            with self.assertRaisesRegex(OSError, "80004005"):
                drag.drag_file(self.path)
        self.assertEqual(set(drag._LIVE_SOURCES), original_live)
        self.assertTrue(self.path.exists())

    def test_invalid_path_is_rejected_before_drag_loop(self):
        with patch.object(self.api.ole32, "DoDragDrop") as loop:
            with self.assertRaises(FileNotFoundError):
                drag.drag_file(self.path.with_name("missing.mid"))
            with self.assertRaises(ValueError):
                drag.drag_file(self.path.parent)
            loop.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
