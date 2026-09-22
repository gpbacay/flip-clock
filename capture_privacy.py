"""
Apply Windows capture-exclusion inside the pywebview process.

SetWindowDisplayAffinity only succeeds when called by the process that owns the
HWND. NiceGUI runs the native window in a subprocess, so this hook must run there.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011
SETTINGS_FILENAME = "flip_clock_settings.json"
DEFAULT_HIDE_FROM_CAPTURE = True
WINDOW_TITLE = "Flip Clock"

# Bound at import time in the window subprocess (before any parent patching).
from nicegui.native.native_mode import _open_window as _REAL_OPEN_WINDOW  # noqa: E402

_poller_started = False


def _writable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _settings_path() -> Path:
    return _writable_dir() / SETTINGS_FILENAME


def read_hide_from_capture() -> bool:
    try:
        path = _settings_path()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return bool(data.get("hide_from_capture", DEFAULT_HIDE_FROM_CAPTURE))
    except Exception:
        pass
    return DEFAULT_HIDE_FROM_CAPTURE


def write_hide_from_capture(enabled: bool) -> None:
    try:
        path = _settings_path()
        data: dict = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}
        data["hide_from_capture"] = bool(enabled)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _hwnd_from_native(native) -> int:
    if native is None:
        return 0
    handle = getattr(native, "Handle", None)
    if handle is None:
        return 0
    try:
        return int(handle.ToInt64())
    except Exception:
        try:
            return int(handle)
        except Exception:
            return 0


def _root_hwnd(hwnd: int) -> int:
    if sys.platform != "win32" or not hwnd:
        return hwnd
    try:
        import ctypes

        GA_ROOT = 2
        root = int(ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT) or 0)
        return root or hwnd
    except Exception:
        return hwnd


def resolve_window_hwnd(window=None) -> int:
    if sys.platform != "win32":
        return 0

    if window is not None:
        hwnd = _root_hwnd(_hwnd_from_native(getattr(window, "native", None)))
        if hwnd:
            return hwnd

    try:
        import webview

        for win in getattr(webview, "windows", []) or []:
            hwnd = _root_hwnd(_hwnd_from_native(getattr(win, "native", None)))
            if hwnd:
                return hwnd
    except Exception:
        pass

    try:
        import ctypes

        hwnd = int(ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE) or 0)
        return _root_hwnd(hwnd)
    except Exception:
        return 0


def set_capture_exclusion(exclude: bool, window=None) -> bool:
    """Must be called from the process that owns the Flip Clock window."""
    if sys.platform != "win32":
        return False

    hwnd = resolve_window_hwnd(window)
    if not hwnd:
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowDisplayAffinity.restype = wintypes.BOOL

        affinity = WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE
        if not user32.SetWindowDisplayAffinity(hwnd, affinity):
            return False

        current = wintypes.DWORD()
        if not user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(current)):
            return False
        expected = WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE
        return int(current.value) == expected
    except Exception:
        return False


def _start_affinity_poller(window) -> None:
    global _poller_started
    if _poller_started or sys.platform != "win32":
        return
    _poller_started = True

    def _loop():
        while True:
            try:
                set_capture_exclusion(read_hide_from_capture(), window=window)
            except Exception:
                pass
            time.sleep(1.0)

    threading.Thread(target=_loop, daemon=True, name="capture-privacy").start()


def _patch_webview_create_window() -> None:
    import webview

    create = webview.create_window
    if getattr(create, "_flip_clock_capture_patched", False):
        return

    def create_window(*args, **kwargs):
        window = create(*args, **kwargs)

        def _apply():
            set_capture_exclusion(read_hide_from_capture(), window=window)
            _start_affinity_poller(window)

        try:
            window.events.shown += _apply
        except Exception:
            pass
        try:
            window.events.loaded += _apply
        except Exception:
            pass

        # Immediate attempt in case events already fired.
        threading.Thread(target=_apply, daemon=True).start()
        return window

    create_window._flip_clock_capture_patched = True  # type: ignore[attr-defined]
    webview.create_window = create_window


def _open_window_hooked(*args, **kwargs):
    _patch_webview_create_window()
    return _REAL_OPEN_WINDOW(*args, **kwargs)


def install_nicegui_hook() -> None:
    """Patch NiceGUI so the window subprocess applies capture exclusion."""
    import nicegui.native.native_mode as native_mode

    native_mode._open_window = _open_window_hooked
