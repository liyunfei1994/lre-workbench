"""Give Windows an application identity independent of python/pythonw.

SetCurrentProcessExplicitAppUserModelID must run before creating Qt windows.
Window and shortcut property stores also identify the relaunch command, name
and icon. Qt supplies native icons at each screen DPI. Window properties are
cleared on close, as required by SHGetPropertyStoreForWindow.
"""
import ctypes
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import uuid

from version import APP_VERSION

APP_USER_MODEL_ID = f"LRE.Workbench.{APP_VERSION}"


class _GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_uint32)]


class _VariantData(ctypes.Union):
    _fields_ = [("pointer", ctypes.c_void_p),
                ("storage", ctypes.c_ubyte * (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 8))]


class _PropVariant(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_uint16), ("reserved", ctypes.c_uint16 * 3),
                ("data", _VariantData)]


def _guid(value):
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


_STORE_IID = _guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")
_APP_FMTID = _guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3")
_PROPERTY_NAMES = {2: "relaunch_command", 3: "icon_resource", 4: "display_name", 5: "app_id"}


def _check_hresult(result, operation):
    if result < 0:
        raise OSError(f"{operation} failed: 0x{result & 0xffffffff:08x}")


@contextmanager
def _com_apartment():
    ole = ctypes.windll.ole32
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_int32
    result = ole.CoInitializeEx(None, 0)
    # Qt may already own an STA. Use that apartment without balancing its init.
    if result != -2147417850:  # RPC_E_CHANGED_MODE
        _check_hresult(result, "CoInitializeEx")
    try:
        yield
    finally:
        if result >= 0:
            ole.CoUninitialize()


class _ComInterface:
    def __init__(self, pointer):
        self.pointer = pointer

    def _method(self, index, *arguments, result=ctypes.c_int32):
        table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[index])

    def release(self):
        self._method(2, result=ctypes.c_uint32)(self.pointer)


class _PropertyStore(_ComInterface):
    def set(self, pid, value):
        key = _PropertyKey(_APP_FMTID, pid)
        variant = _PropVariant()
        if value is not None:
            buffer = ctypes.create_unicode_buffer(value)
            variant.vt = 31  # VT_LPWSTR; SetValue copies this caller-owned buffer.
            variant.data.pointer = ctypes.cast(buffer, ctypes.c_void_p)
        result = self._method(6, ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant))(
            self.pointer, ctypes.byref(key), ctypes.byref(variant))
        _check_hresult(result, "IPropertyStore.SetValue")

    def get(self, pid):
        key = _PropertyKey(_APP_FMTID, pid)
        variant = _PropVariant()
        result = self._method(5, ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant))(
            self.pointer, ctypes.byref(key), ctypes.byref(variant))
        _check_hresult(result, "IPropertyStore.GetValue")
        try:
            if variant.vt == 0:
                return None
            if variant.vt != 31:
                raise TypeError(f"Unexpected taskbar property type: {variant.vt}")
            return ctypes.wstring_at(variant.data.pointer) if variant.data.pointer else ""
        finally:
            clear = ctypes.windll.ole32.PropVariantClear
            clear.argtypes = [ctypes.POINTER(_PropVariant)]
            clear.restype = ctypes.c_int32
            clear(ctypes.byref(variant))

    def commit(self):
        _check_hresult(self._method(7)(self.pointer), "IPropertyStore.Commit")


@contextmanager
def _property_store(*, hwnd=None, path=None, writable=False):
    with _com_apartment():
        pointer = ctypes.c_void_p()
        if hwnd is not None:
            function = ctypes.windll.shell32.SHGetPropertyStoreForWindow
            function.argtypes = [ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
            result = function(hwnd, ctypes.byref(_STORE_IID), ctypes.byref(pointer))
        else:
            function = ctypes.windll.shell32.SHGetPropertyStoreFromParsingName
            function.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32,
                                 ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
            result = function(str(Path(path).resolve()), None, 2 if writable else 0,
                              ctypes.byref(_STORE_IID), ctypes.byref(pointer))
        _check_hresult(result, "SHGetPropertyStore")
        store = _PropertyStore(pointer)
        try:
            yield store
        finally:
            store.release()


def _identity_values(root):
    from version import APP_NAME
    root = Path(root).resolve()
    script = root / "start_windows.vbs"
    icon = root / "assets" / "app.ico"
    if not script.is_file() or not icon.is_file():
        raise FileNotFoundError("Taskbar launcher or icon is missing")
    wscript = Path(os.environ["SystemRoot"]) / "System32" / "wscript.exe"
    # Set the relaunch fields first; setting AppUserModel.ID triggers Shell refresh.
    return {2: subprocess.list2cmdline([str(wscript), str(script)]),
            3: f"{icon},0", 4: APP_NAME, 5: APP_USER_MODEL_ID}


def configure_window_identity(hwnd, root):
    if sys.platform != "win32":
        return
    with _property_store(hwnd=hwnd) as store:
        for pid, value in _identity_values(root).items():
            store.set(pid, value)


def clear_window_identity(hwnd):
    if sys.platform != "win32":
        return
    # Window property values must be released before the native window closes.
    with _property_store(hwnd=hwnd) as store:
        for pid in _PROPERTY_NAMES:
            store.set(pid, None)


def configure_shortcut_identity(path, root):
    if sys.platform != "win32":
        return
    with _property_store(path=path, writable=True) as store:
        for pid, value in _identity_values(root).items():
            store.set(pid, value)
        store.commit()
    # Notify Explorer about this specific shortcut, without rebuilding its cache.
    notify = ctypes.windll.shell32.SHChangeNotify
    notify.argtypes = [ctypes.c_int32, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_void_p]
    notify.restype = None
    notify(0x2000, 0x1005, str(Path(path).resolve()), None)  # UPDATEITEM, PATHW | FLUSH


def create_workbench_shortcut(path, root):
    """Save a Unicode .lnk without WScript.Shell's system-code-page conversion."""
    if sys.platform != "win32":
        raise OSError("Windows shortcuts require Windows")
    from version import APP_NAME
    root, path = Path(root).resolve(), Path(path).resolve()
    if path.suffix.lower() != ".lnk" or not path.parent.is_dir():
        raise ValueError("The shortcut must be an .lnk in an existing directory")
    values = _identity_values(root)  # Also check the launcher and icon exist.
    clsid = _guid("00021401-0000-0000-C000-000000000046")  # ShellLink
    link_iid = _guid("000214F9-0000-0000-C000-000000000046")  # IShellLinkW
    persist_iid = _guid("0000010B-0000-0000-C000-000000000046")  # IPersistFile
    with _com_apartment():
        pointer = ctypes.c_void_p()
        create = ctypes.windll.ole32.CoCreateInstance
        create.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
                           ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_int32
        _check_hresult(create(ctypes.byref(clsid), None, 1, ctypes.byref(link_iid),
                              ctypes.byref(pointer)), "CoCreateInstance(IShellLinkW)")
        link = _ComInterface(pointer)
        try:
            # IShellLinkW vtable slots; all string parameters are UTF-16.
            for slot, value in (
                (20, str(Path(os.environ["SystemRoot"]) / "System32" / "wscript.exe")),
                (11, subprocess.list2cmdline([str(root / "start_windows.vbs")])),
                (9, str(root)),
                (7, f"{APP_NAME} {APP_VERSION}"),
            ):
                _check_hresult(link._method(slot, ctypes.c_wchar_p)(pointer, value),
                               f"IShellLinkW slot {slot}")
            _check_hresult(link._method(17, ctypes.c_wchar_p, ctypes.c_int32)(
                pointer, values[3].rsplit(",", 1)[0], 0), "IShellLinkW.SetIconLocation")
            persistent = ctypes.c_void_p()
            _check_hresult(link._method(0, ctypes.POINTER(_GUID),
                                        ctypes.POINTER(ctypes.c_void_p))(
                pointer, ctypes.byref(persist_iid), ctypes.byref(persistent)),
                "QueryInterface(IPersistFile)")
            persist = _ComInterface(persistent)
            try:
                result = persist._method(6, ctypes.c_wchar_p, ctypes.c_int32)(
                    persistent, str(path), 1)
                _check_hresult(result, "IPersistFile.Save")
                if result != 0:
                    raise OSError("IPersistFile.Save did not complete")
            finally:
                persist.release()
        finally:
            link.release()
    configure_shortcut_identity(path, root)
    return path


def read_taskbar_identity(*, hwnd=None, path=None):
    if sys.platform != "win32":
        return {}
    with _property_store(hwnd=hwnd, path=path) as store:
        return {name: store.get(pid) for pid, name in _PROPERTY_NAMES.items()}


def configure_process_identity():
    if sys.platform != "win32":
        return None
    function = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_long
    result = function(APP_USER_MODEL_ID)
    if result < 0:
        raise OSError(f"Windows AppUserModelID failed: 0x{result & 0xffffffff:08x}")
    return APP_USER_MODEL_ID


def current_process_identity():
    if sys.platform != "win32":
        return None
    function = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID
    function.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    function.restype = ctypes.c_long
    pointer = ctypes.c_void_p()
    result = function(ctypes.byref(pointer))
    if result < 0:
        raise OSError(f"GetCurrentProcessExplicitAppUserModelID failed: 0x{result & 0xffffffff:08x}")
    try:
        return ctypes.wstring_at(pointer)
    finally:
        free = ctypes.windll.ole32.CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        free(pointer)
