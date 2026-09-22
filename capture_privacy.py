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
_last_taskbar_hidden: bool | None = None
_taskbar_lock = threading.Lock()
_tray_icon = None
_tray_lock = threading.Lock()
_tray_window = None
_tray_creating = False
# ShowInTaskbar / NotifyIcon during WebView2 init recreates the HWND and causes
# E_ABORT (white, hung window). Gate unsafe UI until after first successful load.
_webview_ready = threading.Event()
_PRIVACY_UI_DELAY_SEC = 2.5


def _writable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _settings_path() -> Path:
    return _writable_dir() / SETTINGS_FILENAME


DEFAULT_ALARM: dict = {
    "enabled": False,
    "hour": 7,
    "minute": 0,
    "ampm": "AM",
    "label": "Alarm",
    "sound": True,
    "snooze_minutes": 5,
}


def read_settings() -> dict:
    try:
        path = _settings_path()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def write_settings(updates: dict) -> None:
    """Merge *updates* into the settings file and persist."""
    try:
        path = _settings_path()
        data = read_settings()
        data.update(updates)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def read_hide_from_capture() -> bool:
    data = read_settings()
    return bool(data.get("hide_from_capture", DEFAULT_HIDE_FROM_CAPTURE))


def write_hide_from_capture(enabled: bool) -> None:
    write_settings({"hide_from_capture": bool(enabled)})


def read_alarm_settings() -> dict:
    data = read_settings()
    alarm = dict(DEFAULT_ALARM)
    raw = data.get("alarm")
    if isinstance(raw, dict):
        alarm.update({k: raw[k] for k in DEFAULT_ALARM if k in raw})
    # Clamp / normalize
    try:
        alarm["hour"] = max(1, min(12, int(alarm["hour"])))
    except (TypeError, ValueError):
        alarm["hour"] = 7
    try:
        alarm["minute"] = max(0, min(59, int(alarm["minute"])))
    except (TypeError, ValueError):
        alarm["minute"] = 0
    ampm = str(alarm.get("ampm", "AM")).upper()
    alarm["ampm"] = "PM" if ampm == "PM" else "AM"
    alarm["enabled"] = bool(alarm.get("enabled", False))
    alarm["sound"] = bool(alarm.get("sound", True))
    alarm["label"] = str(alarm.get("label") or "Alarm")[:80]
    try:
        alarm["snooze_minutes"] = max(1, min(60, int(alarm.get("snooze_minutes", 5))))
    except (TypeError, ValueError):
        alarm["snooze_minutes"] = 5
    return alarm


def write_alarm_settings(alarm: dict) -> None:
    write_settings({"alarm": alarm})


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


def _run_on_ui_thread(native, fn) -> bool:
    """Run *fn* on the WinForms UI thread when required (never block)."""
    try:
        needs_invoke = bool(getattr(native, "InvokeRequired", False))
        if not needs_invoke:
            fn()
            return True
        begin_invoke = getattr(native, "BeginInvoke", None)
        if begin_invoke is None:
            return False
        try:
            from System import Action  # type: ignore

            begin_invoke(Action(fn))
            return True
        except Exception:
            # pythonnet sometimes accepts a raw callable
            begin_invoke(fn)
            return True
    except Exception:
        return False


def _set_exstyle_taskbar(hwnd: int, hidden: bool) -> bool:
    """Toggle taskbar visibility via extended styles (no ShowWindow hide/show)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020

        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL

        style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        if hidden:
            style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        else:
            style = (style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        # Refresh frame without hiding the window (ShowWindow SW_HIDE freezes WebView2).
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        return True
    except Exception:
        return False


def set_taskbar_hidden(hidden: bool, window=None) -> bool:
    """Hide or show this app on the Windows taskbar (owning process only)."""
    if sys.platform != "win32":
        return False

    native = None
    try:
        if window is not None:
            native = getattr(window, "native", None)
        if native is None:
            import webview

            for win in getattr(webview, "windows", []) or []:
                native = getattr(win, "native", None)
                if native is not None:
                    break
        if native is not None and hasattr(native, "ShowInTaskbar"):
            applied = {"ok": False}

            def _apply():
                native.ShowInTaskbar = not hidden
                applied["ok"] = True

            if _run_on_ui_thread(native, _apply):
                # BeginInvoke is async; treat schedule success as ok.
                return True
    except Exception:
        pass

    hwnd = resolve_window_hwnd(window)
    if not hwnd:
        return False
    return _set_exstyle_taskbar(hwnd, hidden)


def _resolve_tray_icon_path() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "icons8-digital-clock.ico")
        candidates.append(Path(sys.executable).resolve().parent / "icons8-digital-clock.ico")
    candidates.append(Path(__file__).resolve().parent / "icons8-digital-clock.ico")
    for path in candidates:
        if path.is_file():
            return path
    return None


def _focus_window(window) -> None:
    try:
        if hasattr(window, "show"):
            window.show()
        if hasattr(window, "restore"):
            window.restore()
    except Exception:
        pass
    try:
        native = getattr(window, "native", None)
        if native is not None:
            if hasattr(native, "Show"):
                native.Show()
            if hasattr(native, "Activate"):
                native.Activate()
            if hasattr(native, "BringToFront"):
                native.BringToFront()
    except Exception:
        pass
    try:
        import ctypes

        hwnd = resolve_window_hwnd(window)
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _quit_app(window) -> None:
    global _tray_icon
    try:
        if _tray_icon is not None:
            _tray_icon.Visible = False
            _tray_icon.Dispose()
            _tray_icon = None
    except Exception:
        pass
    try:
        if window is not None and hasattr(window, "destroy"):
            window.destroy()
            return
    except Exception:
        pass
    try:
        import webview

        for win in getattr(webview, "windows", []) or []:
            try:
                win.destroy()
            except Exception:
                pass
    except Exception:
        pass


def ensure_tray_icon(window=None) -> bool:
    """Create a notification-area (system tray) icon in this process."""
    global _tray_icon, _tray_window, _tray_creating
    if sys.platform != "win32":
        return False

    with _tray_lock:
        if _tray_icon is not None:
            _tray_window = window or _tray_window
            return True
        if _tray_creating:
            return True

        if window is None:
            try:
                import webview

                windows = getattr(webview, "windows", []) or []
                window = windows[0] if windows else None
            except Exception:
                window = None
        if window is None:
            return False

        native = getattr(window, "native", None)
        if native is None:
            return False

        icon_path = _resolve_tray_icon_path()
        _tray_creating = True
        _tray_window = window

        def _create():
            global _tray_icon, _tray_window, _tray_creating
            try:
                if _tray_icon is not None:
                    return
                import clr  # type: ignore

                clr.AddReference("System.Windows.Forms")
                clr.AddReference("System.Drawing")
                from System.Drawing import Icon as DrawingIcon  # type: ignore
                from System.Windows.Forms import (  # type: ignore
                    ContextMenuStrip,
                    MouseButtons,
                    NotifyIcon,
                    ToolStripMenuItem,
                )

                ni = NotifyIcon()
                ni.Text = WINDOW_TITLE
                if icon_path is not None:
                    ni.Icon = DrawingIcon(str(icon_path))
                else:
                    form_icon = getattr(native, "Icon", None)
                    if form_icon is not None:
                        ni.Icon = form_icon

                menu = ContextMenuStrip()
                show_item = ToolStripMenuItem("Show Flip Clock")
                quit_item = ToolStripMenuItem("Quit")

                def on_show(_sender, _args):
                    _focus_window(window)

                def on_quit(_sender, _args):
                    _quit_app(window)

                show_item.Click += on_show
                quit_item.Click += on_quit
                menu.Items.Add(show_item)
                menu.Items.Add(quit_item)
                ni.ContextMenuStrip = menu

                def on_click(_sender, args):
                    try:
                        if args.Button == MouseButtons.Left:
                            _focus_window(window)
                    except Exception:
                        _focus_window(window)

                ni.MouseClick += on_click
                ni.Visible = True
                with _tray_lock:
                    _tray_icon = ni
                    _tray_window = window
                    _tray_creating = False
            except Exception:
                with _tray_lock:
                    _tray_creating = False

        if not _run_on_ui_thread(native, _create):
            try:
                _create()
            except Exception:
                _tray_creating = False
                return False
        return True


def set_tray_visible(visible: bool, window=None) -> bool:
    """Show/hide the notification-area icon (overflow / system tray)."""
    if sys.platform != "win32":
        return False
    if visible:
        ensure_tray_icon(window)
    with _tray_lock:
        if _tray_icon is None:
            # Still creating on UI thread, or failed — poller will retry.
            return not visible
        try:
            native = None
            win = window or _tray_window
            if win is not None:
                native = getattr(win, "native", None)

            def _apply():
                _tray_icon.Visible = bool(visible)

            if native is not None and _run_on_ui_thread(native, _apply):
                return True
            _apply()
            return True
        except Exception:
            return False


def apply_privacy(
    exclude: bool,
    window=None,
    *,
    force_taskbar: bool = False,
    ui_safe: bool | None = None,
) -> bool:
    """Apply capture exclusion; optionally taskbar hide + tray after WebView2 is ready.

    *ui_safe*:
      - True  → capture affinity only (safe during WebView2 init)
      - False → also toggle taskbar/tray (must be after WebView2 is ready)
      - None  → False only after `_webview_ready` is set, else True
    """
    global _last_taskbar_hidden

    if ui_safe is None:
        ui_safe = not _webview_ready.is_set()

    ok_capture = set_capture_exclusion(exclude, window=window)
    if ui_safe:
        return ok_capture

    ok_taskbar = True
    with _taskbar_lock:
        if force_taskbar or _last_taskbar_hidden is None or _last_taskbar_hidden != exclude:
            ok_taskbar = set_taskbar_hidden(exclude, window=window)
            _last_taskbar_hidden = exclude
    # When hidden from the main taskbar, keep a system-tray (overflow) presence.
    ok_tray = set_tray_visible(exclude, window=window)
    return ok_capture or ok_taskbar or ok_tray


def _start_affinity_poller(window) -> None:
    global _poller_started
    if _poller_started or sys.platform != "win32":
        return
    _poller_started = True

    def _loop():
        while True:
            try:
                # Wait until WebView2 has painted before any ShowInTaskbar/tray work.
                _webview_ready.wait(timeout=30)
                apply_privacy(read_hide_from_capture(), window=window)
            except Exception:
                pass
            time.sleep(1.0)

    threading.Thread(target=_loop, daemon=True, name="capture-privacy").start()


def _schedule_deferred_privacy(window) -> None:
    """Apply capture affinity now; defer taskbar/tray until WebView2 finishes init."""

    def _run():
        try:
            # Capture exclusion alone is HWND-safe and can run early.
            set_capture_exclusion(read_hide_from_capture(), window=window)
        except Exception:
            pass
        time.sleep(_PRIVACY_UI_DELAY_SEC)
        _webview_ready.set()
        try:
            apply_privacy(
                read_hide_from_capture(),
                window=window,
                force_taskbar=True,
                ui_safe=False,
            )
        except Exception:
            pass
        _start_affinity_poller(window)

    threading.Thread(target=_run, daemon=True, name="privacy-defer").start()


def _patch_webview_create_window() -> None:
    import webview

    create = webview.create_window
    if getattr(create, "_flip_clock_capture_patched", False):
        return

    def create_window(*args, **kwargs):
        window = create(*args, **kwargs)
        scheduled = {"done": False}

        def _on_loaded(*_a, **_k):
            if scheduled["done"]:
                try:
                    set_capture_exclusion(read_hide_from_capture(), window=window)
                except Exception:
                    pass
                return
            scheduled["done"] = True
            _schedule_deferred_privacy(window)

        # Only hook *loaded* — *shown* fires while WebView2 is still initializing.
        try:
            window.events.loaded += _on_loaded
        except Exception:
            pass

        return window

    create_window._flip_clock_capture_patched = True  # type: ignore[attr-defined]
    webview.create_window = create_window


def _ensure_webview2_user_data() -> None:
    """Ensure child process has a writable WebView2 profile directory."""
    if sys.platform != "win32":
        return
    try:
        import os
        import tempfile

        folder = os.environ.get("WEBVIEW2_USER_DATA_FOLDER")
        if folder:
            Path(folder).mkdir(parents=True, exist_ok=True)
            return
        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "FlipClock" / "WebView2"
        base.mkdir(parents=True, exist_ok=True)
        os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(base)
    except Exception:
        pass


def _open_window_hooked(*args, **kwargs):
    _ensure_webview2_user_data()
    # Ensure start_args storage_path survives into this process even if pickle dropped it.
    try:
        from nicegui import app as nicegui_app
        import os

        folder = os.environ.get("WEBVIEW2_USER_DATA_FOLDER")
        if folder:
            nicegui_app.native.start_args.setdefault("storage_path", folder)
            nicegui_app.native.start_args.setdefault("private_mode", False)
            nicegui_app.native.start_args.setdefault("gui", "edgechromium")
    except Exception:
        pass
    _patch_webview_create_window()
    return _REAL_OPEN_WINDOW(*args, **kwargs)


def install_nicegui_hook() -> None:
    """Patch NiceGUI so the window subprocess applies capture exclusion."""
    import nicegui.native.native_mode as native_mode

    native_mode._open_window = _open_window_hooked
