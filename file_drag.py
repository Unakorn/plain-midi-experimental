"""Copy one existing file through Windows' native OLE drag-and-drop loop.

Call ``drag_file(path)`` on the GUI thread from a mouse-motion handler while
the left button is held. The call returns when the user drops or cancels.
Windows supplies the IDataObject, including CF_HDROP, format enumeration,
and storage-medium ownership; this module only implements IDropSource.

Native API references:
https://learn.microsoft.com/windows/win32/api/ole2/nf-ole2-dodragdrop
https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-ishellitem-bindtohandler
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as ct
import os
from pathlib import Path
import uuid


HRESULT = ct.c_int32
DWORD = ct.c_uint32
ULONG = ct.c_uint32
BOOL = ct.c_int32
VOID_P = ct.c_void_p
CALLBACK = getattr(ct, "WINFUNCTYPE", ct.CFUNCTYPE)
S_OK = 0
E_NOINTERFACE = -2147467262
E_POINTER = -2147467261
DRAGDROP_S_DROP = 0x00040100
DRAGDROP_S_CANCEL = 0x00040101
DRAGDROP_S_USEDEFAULTCURSORS = 0x00040102
DROPEFFECT_COPY = 1
MK_LBUTTON = 1


class _GUID(ct.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", ct.c_uint16),
                ("Data3", ct.c_uint16), ("Data4", ct.c_ubyte * 8)]

    @classmethod
    def from_string(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


_IID_IUNKNOWN = _GUID.from_string("00000000-0000-0000-C000-000000000046")
_IID_IDROPSOURCE = _GUID.from_string("00000121-0000-0000-C000-000000000046")
_IID_IDATAOBJECT = _GUID.from_string("0000010e-0000-0000-C000-000000000046")
_IID_ISHELLITEM = _GUID.from_string("43826d1e-e718-42ee-bc55-a1e261c37bfe")
_BHID_DATAOBJECT = _GUID.from_string("b8c0bd9f-ed24-455c-83e6-d5390c4fe8c4")


def _check_hresult(result, operation):
    if result < 0:
        raise OSError(f"{operation} failed (Windows HRESULT 0x{result & 0xffffffff:08X}).")


def _method(interface, slot, result_type, *argument_types):
    """Bind one native COM vtable method, including its explicit this pointer."""
    vtable = ct.cast(interface, ct.POINTER(ct.POINTER(VOID_P))).contents
    return CALLBACK(result_type, VOID_P, *argument_types)(vtable[slot])


def _release(interface):
    if interface:
        _method(interface, 2, ULONG)(interface)


class _WindowsAPI:
    def __init__(self):
        self.ole32 = ct.WinDLL("ole32", use_last_error=True)
        self.shell32 = ct.WinDLL("shell32", use_last_error=True)
        self.ole32.OleInitialize.argtypes = [VOID_P]
        self.ole32.OleInitialize.restype = HRESULT
        self.ole32.OleUninitialize.argtypes = []
        self.ole32.OleUninitialize.restype = None
        self.ole32.DoDragDrop.argtypes = [VOID_P, VOID_P, DWORD, ct.POINTER(DWORD)]
        self.ole32.DoDragDrop.restype = HRESULT
        self.shell32.SHCreateItemFromParsingName.argtypes = [
            ct.c_wchar_p, VOID_P, ct.POINTER(_GUID), ct.POINTER(VOID_P)]
        self.shell32.SHCreateItemFromParsingName.restype = HRESULT


_API = None


def _native():
    global _API
    if os.name != "nt":
        raise NotImplementedError("File drag-out currently requires Windows. Use Open files to export the MIDI.")
    if _API is None:
        _API = _WindowsAPI()
    return _API


@contextmanager
def _ole_apartment(api):
    # Both S_OK and S_FALSE need a matching OleUninitialize on this thread.
    result = api.ole32.OleInitialize(None)
    _check_hresult(result, "Initializing Windows file dragging on the GUI thread")
    try:
        yield
    finally:
        api.ole32.OleUninitialize()


@contextmanager
def _shell_data_object(api, path):
    item, data = VOID_P(), VOID_P()
    try:
        _check_hresult(api.shell32.SHCreateItemFromParsingName(
            str(path), None, ct.byref(_IID_ISHELLITEM), ct.byref(item)),
            "Opening the MIDI as a Windows Shell item")
        bind = _method(item, 3, HRESULT, VOID_P, ct.POINTER(_GUID),
                       ct.POINTER(_GUID), ct.POINTER(VOID_P))
        _check_hresult(bind(item, None, ct.byref(_BHID_DATAOBJECT),
                            ct.byref(_IID_IDATAOBJECT), ct.byref(data)),
                       "Preparing the Windows file data object")
        yield data
    finally:
        _release(data)
        _release(item)


_QUERY_INTERFACE = CALLBACK(HRESULT, VOID_P, ct.POINTER(_GUID), ct.POINTER(VOID_P))
_ADD_REF = CALLBACK(ULONG, VOID_P)
_RELEASE = CALLBACK(ULONG, VOID_P)
_QUERY_CONTINUE = CALLBACK(HRESULT, VOID_P, BOOL, DWORD)
_GIVE_FEEDBACK = CALLBACK(HRESULT, VOID_P, DWORD)


class _DropSourceVTable(ct.Structure):
    _fields_ = [("QueryInterface", _QUERY_INTERFACE), ("AddRef", _ADD_REF),
                ("Release", _RELEASE), ("QueryContinueDrag", _QUERY_CONTINUE),
                ("GiveFeedback", _GIVE_FEEDBACK)]


class _DropSourceInterface(ct.Structure):
    _fields_ = [("lpVtbl", ct.POINTER(_DropSourceVTable))]


# Keep Python callbacks alive for every outstanding native COM reference.
_LIVE_SOURCES = {}


class _DropSource:
    def __init__(self):
        self.references = 1
        self.vtable = _DropSourceVTable(
            _QUERY_INTERFACE(self.query_interface), _ADD_REF(self.add_ref),
            _RELEASE(self.release), _QUERY_CONTINUE(self.query_continue),
            _GIVE_FEEDBACK(self.give_feedback))
        self.interface = _DropSourceInterface(ct.pointer(self.vtable))
        self.pointer = ct.cast(ct.pointer(self.interface), VOID_P)
        _LIVE_SOURCES[self.pointer.value] = self

    def query_interface(self, this, iid, output):
        if not output:
            return E_POINTER
        output[0] = None
        if not iid:
            return E_POINTER
        if bytes(iid.contents) not in (bytes(_IID_IUNKNOWN), bytes(_IID_IDROPSOURCE)):
            return E_NOINTERFACE
        output[0] = self.pointer.value
        self.add_ref(this)
        return S_OK

    def add_ref(self, this):
        self.references += 1
        return self.references

    def release(self, this):
        self.references -= 1
        if self.references == 0:
            _LIVE_SOURCES.pop(self.pointer.value, None)
        return self.references

    @staticmethod
    def query_continue(this, escape_pressed, key_state):
        if escape_pressed:
            return DRAGDROP_S_CANCEL
        if not key_state & MK_LBUTTON:
            return DRAGDROP_S_DROP
        return S_OK

    @staticmethod
    def give_feedback(this, effect):
        return DRAGDROP_S_USEDEFAULTCURSORS


def drag_file(path: str | Path) -> bool:
    """Drag one saved file as a copy; return True for a drop, False for cancel.

    This synchronous call must run on the GUI thread while the left mouse
    button is held. The source file remains in place. Drop targets decide
    whether they can import the file; an unsupported target produces False.
    Raises OSError on native failure and NotImplementedError outside Windows.
    """
    api = _native()
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("Drag-out requires a saved file, not a folder.")
    with _ole_apartment(api), _shell_data_object(api, path) as data:
        source = _DropSource()
        effect = DWORD(0)
        try:
            result = api.ole32.DoDragDrop(data, source.pointer, DROPEFFECT_COPY,
                                        ct.byref(effect))
            _check_hresult(result, "Dragging the MIDI file")
            return result == DRAGDROP_S_DROP and bool(effect.value & DROPEFFECT_COPY)
        finally:
            source.release(source.pointer)
