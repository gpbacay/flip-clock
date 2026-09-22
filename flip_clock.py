#!/usr/bin/env python3
"""
Flip Clock with Time Triggers
=============================

A desktop app built with NiceGUI (https://nicegui.io/) that:
  1. Displays a flip-clock style digital clock (HH:MM:SS).
  2. Includes configurable time triggers for mouse clicks and keyboard keys.
  3. Includes a settable alarm clock.

Dependencies:
    pip install nicegui pyautogui keyboard pywebview

Features:
    - Flip-clock display (HH:MM:SS + AM/PM)
    - Time triggers (mouse / keyboard)
    - Alarm clock (set in Settings)
    - Minimize / close toolbar + Ctrl+Esc to quit
    - Optional Windows capture exclusion (hide from screen share / screenshots)

Run:
    python flip_clock.py
"""

from __future__ import annotations

import datetime
import multiprocessing as mp
import queue
import sys
import threading
import time
from pathlib import Path

from nicegui import app, ui

import capture_privacy

WINDOW_TITLE = capture_privacy.WINDOW_TITLE
DEFAULT_HIDE_FROM_CAPTURE = capture_privacy.DEFAULT_HIDE_FROM_CAPTURE

# ---- Optional automation libraries -----------------------------------
try:
    import pyautogui

    pyautogui.FAILSAFE = True
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False

try:
    import keyboard as kb_lib

    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            return Path(bundle)
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
ICON_PATH = APP_DIR / "icons8-digital-clock-100.png"
ICON_ICO_PATH = APP_DIR / "icons8-digital-clock.ico"
APP_PORT = 8080


def _get_hide_from_capture() -> bool:
    return capture_privacy.read_hide_from_capture()


def _set_hide_from_capture(enabled: bool) -> None:
    capture_privacy.write_hide_from_capture(enabled)


def _native_window():
    return getattr(getattr(app, "native", None), "main_window", None)


def _exit_fullscreen() -> None:
    try:
        window = _native_window()
        if window is not None:
            window.toggle_fullscreen()
            return
    except Exception:
        pass
    ui.notify("Could not exit fullscreen.", type="warning")


def _minimize_window() -> None:
    try:
        window = _native_window()
        if window is not None:
            # Leave fullscreen first so minimize is visible on the taskbar.
            try:
                if getattr(window, "fullscreen", False):
                    window.toggle_fullscreen()
            except Exception:
                pass
            window.minimize()
            return
    except Exception:
        pass
    ui.notify("Could not minimize window.", type="warning")


def _close_app() -> None:
    """Close Flip Clock (toolbar or Ctrl+Esc)."""
    try:
        engine.stop()
    except Exception:
        pass
    try:
        alarm_engine.dismiss()
    except Exception:
        pass
    try:
        window = _native_window()
        if window is not None:
            window.destroy()
            return
    except Exception:
        pass
    try:
        app.shutdown()
    except Exception:
        pass


KEY_ALIASES = {
    "spacebar": "space",
    "space bar": "space",
    "esc": "escape",
    "return": "enter",
    "del": "delete",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "altgr": "alt gr",
    "shift": "shift",
    "win": "windows",
    "cmd": "windows",
    "super": "windows",
}

COMMON_KEYS = [
    "space",
    "enter",
    "tab",
    "alt+tab",
    "alt",
    "ctrl",
    "shift",
    "escape",
    "backspace",
    "delete",
    "up",
    "down",
    "left",
    "right",
    "f5",
    "f6",
    "a",
    "w",
    "s",
    "d",
]


def _ensure_ico_icon() -> Path | None:
    """Windows native mode needs .ico; generate from PNG when possible."""
    if ICON_ICO_PATH.exists():
        return ICON_ICO_PATH
    if not ICON_PATH.exists():
        return None
    try:
        from PIL import Image

        Image.open(ICON_PATH).save(ICON_ICO_PATH, format="ICO", sizes=[(32, 32), (64, 64), (128, 128)])
        return ICON_ICO_PATH
    except Exception:
        return ICON_PATH

FLIP_CLOCK_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Oswald:wght@500;700&display=swap');

body, .nicegui-content {
    background: #0a0a0b !important;
    overflow: hidden;
    margin: 0 !important;
    padding: 0 !important;
    width: 100vw !important;
    height: 100vh !important;
    min-height: 100vh !important;
}

.clock-shell {
    width: 100vw;
    height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    justify-content: stretch;
    padding: 0;
    margin: 0;
    position: relative;
    background: radial-gradient(ellipse at 50% 35%, #1e1e22 0%, #101012 55%, #080809 100%);
}

.clock-hover-zone {
    position: absolute;
    top: 0;
    right: 0;
    z-index: 10;
    width: 11rem;
    height: 3.75rem;
    display: flex;
    align-items: flex-start;
    justify-content: flex-end;
    padding: 0.55rem 0.55rem 0 0;
    box-sizing: border-box;
}

.clock-toolbar {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.22s ease;
}

.clock-hover-zone:hover .clock-toolbar,
.clock-toolbar:focus-within {
    opacity: 1;
    pointer-events: auto;
}

.menu-btn {
    background: rgba(255, 255, 255, 0.06) !important;
    color: #ffffff !important;
    border-radius: 50% !important;
    width: 2rem !important;
    height: 2rem !important;
    min-height: 2rem !important;
    min-width: 2rem !important;
    padding: 0 !important;
    transition: background 0.2s, opacity 0.2s !important;
}
.menu-btn .q-icon {
    color: #ffffff !important;
    font-size: 1.15rem !important;
}
.menu-btn:hover {
    background: rgba(255, 255, 255, 0.14) !important;
    opacity: 1;
}

/* Window-style minimize: short bar near the bottom of the control */
.menu-btn-minimize .q-icon {
    display: none !important;
}
.menu-btn-minimize .q-btn__content {
    min-width: 0 !important;
    min-height: 0 !important;
}
.menu-btn-minimize .q-btn__content::after {
    content: "";
    display: block;
    width: 0.72rem;
    height: 0.15rem;
    margin-top: 0.42rem;
    border-radius: 1px;
    background: #ffffff;
}

.clock-face {
    flex: 1;
    width: 100%;
    height: 100%;
    background: transparent;
    border-radius: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    box-shadow: none;
}

.digits-row {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 100%;
    gap: clamp(3px, 0.65vw, 10px);
    padding: 0;
    --flip-h: min(78vh, calc(100vw / 10.2 / 0.62));
}

.flip-unit {
    position: relative;
    height: var(--flip-h);
    width: calc(var(--flip-h) * 0.62);
    --flip-w: calc(var(--flip-h) * 0.62);
    --flip-fs: calc(var(--flip-h) * 0.58);
    font-family: 'Oswald', 'Segoe UI', sans-serif;
    font-weight: 700;
    font-size: var(--flip-fs);
    line-height: 1;
    color: #f2f2f2;
    flex-shrink: 0;
}

.flip-card {
    position: relative;
    width: 100%;
    height: 100%;
    border-radius: clamp(4px, 0.5vw, 8px);
    overflow: hidden;
    background: #141414;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.06),
        0 4px 14px rgba(0, 0, 0, 0.45);
    perspective: calc(var(--flip-h) * 2.5);
    transform-style: preserve-3d;
}

.flip-pane {
    position: absolute;
    left: 0;
    width: 100%;
    height: calc(var(--flip-h) / 2);
    overflow: hidden;
    transform-style: preserve-3d;
    backface-visibility: hidden;
    -webkit-backface-visibility: hidden;
}

.flip-pane .digit-inner {
    position: absolute;
    left: 0;
    width: 100%;
    height: var(--flip-h);
    margin: 0 !important;
    padding: 0 !important;
    min-height: 0 !important;
    font-family: inherit !important;
    font-size: var(--flip-fs) !important;
    font-weight: inherit !important;
    line-height: var(--flip-h) !important;
    color: inherit !important;
    text-align: center !important;
    display: block !important;
    box-sizing: border-box !important;
    white-space: nowrap !important;
    overflow: hidden !important;
}

.flip-top .digit-inner,
.flip-top-flip .digit-inner {
    top: 0;
    background: linear-gradient(180deg, #1a1a1a 0%, #141414 100%);
}

.flip-bottom .digit-inner,
.flip-bottom-flip .digit-inner {
    bottom: -1px;
    background: linear-gradient(180deg, #101010 0%, #0c0c0c 100%);
}

.flip-top,
.flip-top-flip {
    top: 0;
    transform-origin: 50% 100%;
    border-bottom: 1px solid #000;
}

.flip-bottom,
.flip-bottom-flip {
    bottom: 0;
    transform-origin: 50% 0%;
}

.flip-top { z-index: 1; }
.flip-bottom { z-index: 0; }

.flip-top-flip {
    z-index: 3;
    visibility: hidden;
    transform: rotateX(0deg);
    backface-visibility: hidden;
    -webkit-backface-visibility: hidden;
}

.flip-bottom-flip {
    z-index: 2;
    visibility: hidden;
    transform: rotateX(90deg);
    backface-visibility: hidden;
    -webkit-backface-visibility: hidden;
}

.flip-top-flip.play {
    visibility: visible;
    animation: flipTopDown 0.28s cubic-bezier(0.37, 0.01, 0.94, 0.35) forwards;
}

.flip-bottom-flip.play {
    visibility: visible;
    animation: flipBottomUp 0.28s cubic-bezier(0.16, 0.84, 0.44, 1) forwards;
}

@keyframes flipTopDown {
    0% {
        transform: rotateX(0deg);
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
    }
    100% {
        transform: rotateX(-90deg);
        box-shadow: 0 10px 16px rgba(0, 0, 0, 0.55);
    }
}

@keyframes flipBottomUp {
    0% {
        transform: rotateX(90deg);
        box-shadow: 0 -2px 6px rgba(0, 0, 0, 0.35);
    }
    100% {
        transform: rotateX(0deg);
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
    }
}

.flip-unit.ampm-char {
    width: calc(var(--flip-h) * 0.48);
    --flip-w: calc(var(--flip-h) * 0.48);
    --flip-fs: calc(var(--flip-h) * 0.44);
}

.ampm-spacer {
    width: clamp(4px, 0.5vw, 12px);
    flex-shrink: 0;
}

.hinge {
    position: absolute;
    top: 50%;
    left: 0;
    right: 0;
    height: max(2px, calc(var(--flip-h) * 0.02));
    margin-top: calc(max(2px, calc(var(--flip-h) * 0.02)) / -2);
    background: #000;
    box-shadow: 0 1px 0 rgba(60, 60, 60, 0.5);
    z-index: 5;
    pointer-events: none;
}

.rivet {
    position: absolute;
    top: 50%;
    width: max(4px, calc(var(--flip-h) * 0.04));
    height: max(6px, calc(var(--flip-h) * 0.06));
    margin-top: calc(max(6px, calc(var(--flip-h) * 0.06)) / -2);
    border-radius: 50%;
    background: #0a0a0a;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.8);
    z-index: 6;
}
.rivet.left { left: max(3px, calc(var(--flip-h) * 0.03)); }
.rivet.right { right: max(3px, calc(var(--flip-h) * 0.03)); }

.colon {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: calc(var(--flip-h, 5.2rem) * 0.16);
    padding: 0;
    height: var(--flip-h, 5.2rem);
    flex-shrink: 0;
}

.colon span {
    width: clamp(5px, 0.55vw, 10px);
    height: clamp(5px, 0.55vw, 10px);
    border-radius: 50%;
    background: #f0f0f0;
    box-shadow: 0 0 8px rgba(255, 255, 255, 0.4);
    animation: colonPulse 2s ease-in-out infinite;
}

.colon span:nth-child(2) { animation-delay: 1s; }

@keyframes colonPulse {
    0%, 100% { opacity: 1; }
    50%      { opacity: 0.35; }
}

.settings-card {
    width: min(600px, 94vw) !important;
    max-height: min(88vh, 720px) !important;
    height: auto !important;
    display: flex !important;
    flex-direction: column !important;
    overflow: hidden !important;
    background: #1a1a1e !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 14px !important;
    padding: 1rem 0.35rem 1rem 1.25rem !important;
    box-sizing: border-box !important;
}
.q-dialog__inner--minimized > .settings-card,
.q-dialog__inner > .q-card.settings-card {
    max-height: min(88vh, 720px) !important;
}

.triggers-backdrop {
    backdrop-filter: blur(6px);
    background: rgba(0, 0, 0, 0.55) !important;
}

.settings-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 0.15rem;
    padding-right: 0.9rem;
    flex-shrink: 0;
}

.settings-subtitle {
    padding-right: 0.9rem;
    flex-shrink: 0;
}

.settings-close-btn {
    color: #cfcfcf !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    width: 2rem !important;
    height: 2rem !important;
    min-height: 2rem !important;
    min-width: 2rem !important;
    padding: 0 !important;
}
.settings-close-btn::before,
.settings-close-btn .q-focus-helper {
    display: none !important;
    opacity: 0 !important;
}
.settings-close-btn .q-icon {
    font-size: 1.25rem !important;
    color: inherit !important;
}
.settings-close-btn:hover {
    color: #ffffff !important;
    background: rgba(255, 255, 255, 0.08) !important;
}

.settings-scroll {
    flex: 1 1 auto;
    min-height: 0;
    width: 100%;
    overflow-x: hidden;
    overflow-y: auto;
    padding-right: 0.9rem;
    overscroll-behavior: contain;
    box-sizing: border-box;
}
.settings-scroll::-webkit-scrollbar {
    width: 8px;
}
.settings-scroll::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.18);
    border-radius: 8px;
}
.settings-scroll::-webkit-scrollbar-track {
    background: transparent;
}

.triggers-section {
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 10px;
    padding: 0.85rem 1rem;
    margin-top: 0.75rem;
}

.log-box textarea {
    font-family: 'Consolas', 'Courier New', monospace !important;
    font-size: 0.78rem !important;
    background: #0d0d0f !important;
    color: #8bc34a !important;
    border-radius: 8px !important;
    min-height: 110px !important;
    max-height: 160px !important;
}

.settings-accordion {
    display: block !important;
    width: 100% !important;
    max-width: 100% !important;
    align-self: stretch !important;
    box-sizing: border-box !important;
    margin-top: 0.55rem;
    border-radius: 10px !important;
    background: rgba(255, 255, 255, 0.03) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    overflow: visible !important;
    flex-shrink: 0;
}
.settings-accordion + .settings-accordion {
    margin-top: 0.45rem;
}
.settings-accordion .q-expansion-item__container {
    width: 100% !important;
    max-width: 100% !important;
    display: block !important;
    overflow: visible !important;
}
.settings-accordion .q-item {
    width: 100% !important;
    min-height: 2.6rem;
    padding: 0.45rem 0.85rem !important;
}
.settings-accordion .q-item__section--main {
    width: 100% !important;
    flex: 1 1 auto !important;
}
.settings-accordion .q-item__label {
    color: #f0f0f0 !important;
    font-weight: 600 !important;
    font-size: 0.92rem !important;
}
.settings-accordion .q-expansion-item__content {
    width: 100% !important;
    max-width: 100% !important;
    box-sizing: border-box !important;
    padding: 0 0.85rem 0.9rem !important;
    max-height: min(42vh, 360px);
    overflow-x: hidden !important;
    overflow-y: auto !important;
    overscroll-behavior: contain;
}
.settings-accordion .q-expansion-item__content::-webkit-scrollbar {
    width: 7px;
}
.settings-accordion .q-expansion-item__content::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.16);
    border-radius: 7px;
}
.settings-accordion .q-focus-helper {
    display: none !important;
}

.alarm-banner {
    position: absolute;
    left: 50%;
    bottom: 1.75rem;
    transform: translateX(-50%);
    z-index: 20;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
    padding: 1rem 1.35rem;
    min-width: min(360px, 88vw);
    background: rgba(22, 22, 26, 0.94);
    border: 1px solid rgba(255, 200, 80, 0.35);
    border-radius: 14px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.55);
    animation: alarmPulse 1.2s ease-in-out infinite;
}
.alarm-banner-title {
    color: #ffd27a;
    font-family: 'Oswald', 'Segoe UI', sans-serif;
    font-size: 1.35rem;
    letter-spacing: 0.04em;
}
.alarm-banner-sub {
    color: #c8c8c8;
    font-size: 0.85rem;
}
@keyframes alarmPulse {
    0%, 100% { box-shadow: 0 12px 40px rgba(0, 0, 0, 0.55), 0 0 0 0 rgba(255, 180, 60, 0.25); }
    50% { box-shadow: 0 12px 40px rgba(0, 0, 0, 0.55), 0 0 0 10px rgba(255, 180, 60, 0); }
}
"""


# =========================================================================
#  TIME TRIGGER ENGINE
# =========================================================================
def _normalize_key(key: str) -> str:
    cleaned = key.strip().lower()
    return KEY_ALIASES.get(cleaned, cleaned)


def _parse_key_combo(key: str) -> list[str]:
    """Split a combo like 'alt + tab' into normalized parts."""
    raw = key.strip().lower().replace(" ", "")
    if not raw:
        return []
    parts: list[str] = []
    for part in raw.split("+"):
        if not part:
            continue
        normalized = _normalize_key(part)
        if normalized and normalized not in parts:
            parts.append(normalized)
    return parts


def _format_key_combo(parts: list[str]) -> str:
    return "+".join(parts)


def _press_key(key: str) -> None:
    """Send a single key or key combo (e.g. alt+tab)."""
    parts = _parse_key_combo(key)
    if not parts:
        raise ValueError("No key specified")

    combo = _format_key_combo(parts)

    if HAS_KEYBOARD:
        kb_lib.press_and_release(combo)
        return

    if HAS_PYAUTOGUI:
        py_parts = ["esc" if part == "escape" else part for part in parts]
        if len(py_parts) == 1:
            pyautogui.press(py_parts[0])
        else:
            pyautogui.hotkey(*py_parts)
        return

    raise RuntimeError("Install keyboard or pyautogui to send key presses.")


class TimeTriggerEngine:
    """Runs the configured click/keypress loop in a background thread."""

    def __init__(self, log_callback=None):
        self._thread = None
        self._stop_event = threading.Event()
        self.running = False
        self.log = log_callback or (lambda msg: None)

    def start(self, config):
        if self.running:
            return
        self._stop_event.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, args=(config,), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self.running = False

    def _run(self, config):
        mode = config["mode"]
        interval = config["interval"]
        repeats = config["repeats"]
        x, y = config.get("x"), config.get("y")
        button = config.get("button", "left")
        key = config.get("key", "space")

        count = 0
        label = "mouse click" if mode == "mouse" else f"key '{key}'"
        self.log(f"Time trigger started ({label}) every {interval}s")

        while not self._stop_event.is_set():
            if repeats and count >= repeats:
                break

            try:
                if mode == "mouse":
                    if HAS_PYAUTOGUI:
                        pyautogui.click(x=x, y=y, button=button)
                        self.log(f"Clicked ({button}) at ({x}, {y})  [#{count + 1}]")
                    else:
                        self.log("ERROR: pyautogui not installed. Run: pip install pyautogui")
                        break
                else:
                    _press_key(key)
                    self.log(f"Pressed {_format_key_combo(_parse_key_combo(key))}  [#{count + 1}]")
            except Exception as e:
                self.log(f"ERROR during trigger: {e}")
                break

            count += 1
            slept = 0.0
            step = 0.05
            while slept < interval:
                if self._stop_event.is_set():
                    break
                time.sleep(min(step, interval - slept))
                slept += step

        self.running = False
        self.log("Time trigger stopped.")


# =========================================================================
#  ALARM ENGINE
# =========================================================================
class AlarmEngine:
    """Tracks a single wall-clock alarm with dismiss / snooze."""

    def __init__(self):
        self.config = capture_privacy.read_alarm_settings()
        self.ringing = False
        self._fired_key: str | None = None
        self._snooze_until: datetime.datetime | None = None
        self._sound_stop = threading.Event()
        self._sound_thread: threading.Thread | None = None
        self._on_ring = None  # callback set by UI

    def reload(self) -> None:
        self.config = capture_privacy.read_alarm_settings()

    def save(self, config: dict) -> None:
        self.config = config
        capture_privacy.write_alarm_settings(config)
        # Allow re-arm for a newly saved time.
        self._fired_key = None

    def set_ring_handler(self, handler) -> None:
        self._on_ring = handler

    def check(self, now: datetime.datetime) -> None:
        if self.ringing:
            return
        if self._snooze_until is not None:
            if now >= self._snooze_until:
                self._snooze_until = None
                self._start_ringing(now, snoozed=True)
            return
        if not self.config.get("enabled"):
            return
        if now.hour == self._target_hour24() and now.minute == int(self.config["minute"]):
            key = now.strftime("%Y-%m-%d %H:%M")
            if key != self._fired_key:
                self._fired_key = key
                self._start_ringing(now, snoozed=False)

    def _target_hour24(self) -> int:
        hour = int(self.config.get("hour", 7))
        ampm = str(self.config.get("ampm", "AM")).upper()
        if ampm == "AM":
            return 0 if hour == 12 else hour
        return 12 if hour == 12 else hour + 12

    def _start_ringing(self, now: datetime.datetime, *, snoozed: bool) -> None:
        self.ringing = True
        if self.config.get("sound", True):
            self._start_sound()
        if self._on_ring:
            try:
                self._on_ring(snoozed=snoozed, when=now)
            except Exception:
                pass

    def dismiss(self) -> None:
        self.ringing = False
        self._snooze_until = None
        self._stop_sound()

    def snooze(self, minutes: int | None = None) -> None:
        mins = minutes if minutes is not None else int(self.config.get("snooze_minutes", 5))
        mins = max(1, min(60, mins))
        self.ringing = False
        self._stop_sound()
        self._snooze_until = datetime.datetime.now() + datetime.timedelta(minutes=mins)

    def _start_sound(self) -> None:
        self._stop_sound()
        self._sound_stop.clear()

        def _beep_loop():
            while not self._sound_stop.is_set():
                try:
                    if sys.platform == "win32":
                        import winsound

                        winsound.Beep(880, 280)
                        if self._sound_stop.wait(0.18):
                            break
                        winsound.Beep(660, 220)
                    else:
                        print("\a", end="", flush=True)
                except Exception:
                    break
                if self._sound_stop.wait(0.55):
                    break

        self._sound_thread = threading.Thread(target=_beep_loop, daemon=True, name="alarm-sound")
        self._sound_thread.start()

    def _stop_sound(self) -> None:
        self._sound_stop.set()


# =========================================================================
#  FLIP DIGIT WIDGET
# =========================================================================
class FlipDigit:
    """Single animated flip-card digit with classic top/bottom flap animation."""

    _HALF_MS = 0.28

    def __init__(self, *, ampm: bool = False):
        self._value = ""
        self._animating = False
        self._pending: str | None = None
        unit_classes = "flip-unit ampm-char" if ampm else "flip-unit"
        with ui.element("div").classes(unit_classes) as self.root:
            with ui.element("div").classes("flip-card"):
                with ui.element("div").classes("flip-pane flip-top"):
                    self._top_digit = ui.label("0").classes("digit-inner")
                with ui.element("div").classes("flip-pane flip-bottom"):
                    self._bottom_digit = ui.label("0").classes("digit-inner")
                with ui.element("div").classes("flip-pane flip-top-flip") as self._top_flap:
                    self._top_flap_digit = ui.label("0").classes("digit-inner")
                with ui.element("div").classes("flip-pane flip-bottom-flip") as self._bottom_flap:
                    self._bottom_flap_digit = ui.label("0").classes("digit-inner")
                ui.element("div").classes("hinge")
                ui.element("div").classes("rivet left")
                ui.element("div").classes("rivet right")
        self._set_all("0")

    def _set_all(self, digit: str):
        self._top_digit.set_text(digit)
        self._bottom_digit.set_text(digit)
        self._top_flap_digit.set_text(digit)
        self._bottom_flap_digit.set_text(digit)

    def _reset_flaps(self):
        self._top_flap.classes(remove="play")
        self._bottom_flap.classes(remove="play")

    def set_digit(self, digit: str, *, animate: bool = True):
        if digit == self._value and not self._animating:
            return
        if self._animating:
            # Keep latest target so rapid second changes never skip a digit.
            self._pending = digit
            return

        old = self._value
        if animate and old:
            self._animating = True
            self._pending = None
            self._reset_flaps()

            # Static top already shows the new digit (revealed as top flap leaves).
            # Static bottom keeps the old digit until the bottom flap fully lands
            # — updating early causes a mid-hinge flash/glitch.
            self._top_digit.set_text(digit)
            self._bottom_digit.set_text(old)
            self._top_flap_digit.set_text(old)
            self._bottom_flap_digit.set_text(digit)
            self._value = digit

            ui.timer(0.02, lambda: self._top_flap.classes(add="play"), once=True)
            ui.timer(self._HALF_MS, self._start_bottom_flip, once=True)
            ui.timer(self._HALF_MS * 2 + 0.02, lambda d=digit: self._finish_flip(d), once=True)
        else:
            self._set_all(digit)
            self._value = digit

    def _start_bottom_flip(self):
        # Drop the finished top flap so it cannot flash over the rising bottom flap.
        self._top_flap.classes(remove="play")
        self._bottom_flap.classes(add="play")

    def _finish_flip(self, digit: str):
        self._set_all(digit)
        self._reset_flaps()
        self._animating = False
        pending = self._pending
        self._pending = None
        if pending is not None and pending != self._value:
            self.set_digit(pending, animate=True)

# =========================================================================
#  APPLICATION STATE
# =========================================================================
log_queue: queue.Queue[str] = queue.Queue()
action_queue: queue.Queue[str] = queue.Queue()
engine = TimeTriggerEngine(log_callback=lambda msg: log_queue.put(msg))
alarm_engine = AlarmEngine()
_trigger_toggle: callable | None = None


def enqueue_log(msg: str):
    log_queue.put(msg)


def _expansion(title: str, icon: str, *, value: bool = False):
    """Dark-themed accordion section used in Settings (one open at a time)."""
    return (
        ui.expansion(title, icon=icon, value=value)
        .classes("w-full settings-accordion")
        .style("width: 100%; max-width: 100%; display: block;")
        .props(
            'dense dark expand-separator group="settings-acc" '
            "header-class='text-weight-medium'"
        )
    )


def _build_time_triggers_panel(dialog_close):
    with ui.element("div").classes("settings-header"):
        ui.label("Settings").classes("text-h6")
        ui.button(icon="close", on_click=dialog_close).props(
            "flat round dense unelevated no-caps"
        ).classes("settings-close-btn").tooltip("Close")

    ui.label("Organize options below. Use Ctrl+Esc anytime to quit Flip Clock.").classes(
        "text-caption text-grey q-mb-xs settings-subtitle"
    )

    with ui.element("div").classes("settings-scroll"):
        _build_settings_sections()


def _build_settings_sections():
    # ---- Privacy --------------------------------------------------------
    with _expansion("Privacy", "visibility_off", value=True):
        hide_switch = ui.switch(
            "Hide from screen share, screenshots & taskbar",
            value=_get_hide_from_capture(),
        ).props("dense color=primary")
        ui.label(
            "When on, this window is excluded from most screen captures/share sessions "
            "and removed from the main taskbar. A Flip Clock icon stays in the notification "
            "area (▲ hidden icons). You still see the clock on your display."
        ).classes("text-caption text-grey q-mt-xs")

        def on_hide_toggle(e):
            enabled = bool(e.value)
            _set_hide_from_capture(enabled)
            if sys.platform != "win32":
                ui.notify("Capture exclusion is only available on Windows.", type="warning")
            else:
                ui.notify(
                    "Hidden from screen share; tray icon in ▲ menu (applies within ~1s)."
                    if enabled
                    else "Visible in screen share & taskbar (applies within ~1s).",
                    type="positive",
                )

        hide_switch.on_value_change(on_hide_toggle)

    # ---- Alarm ----------------------------------------------------------
    alarm_cfg = alarm_engine.config
    with _expansion("Alarm Clock", "alarm"):
        alarm_enable = ui.switch("Enable alarm", value=bool(alarm_cfg.get("enabled"))).props(
            "dense color=orange"
        )
        with ui.row().classes("w-full q-gutter-sm q-mt-sm items-end"):
            alarm_hour = ui.number(
                "Hour",
                value=int(alarm_cfg.get("hour", 7)),
                min=1,
                max=12,
                step=1,
                format="%.0f",
            ).classes("col")
            alarm_minute = ui.number(
                "Minute",
                value=int(alarm_cfg.get("minute", 0)),
                min=0,
                max=59,
                step=1,
                format="%.0f",
            ).classes("col")
            alarm_ampm = ui.select(
                ["AM", "PM"], value=alarm_cfg.get("ampm", "AM"), label="AM/PM"
            ).classes("col")
        with ui.row().classes("w-full q-gutter-sm q-mt-sm items-end"):
            alarm_label = ui.input("Label", value=str(alarm_cfg.get("label") or "Alarm")).classes(
                "col"
            )
            alarm_snooze = ui.number(
                "Snooze (min)",
                value=int(alarm_cfg.get("snooze_minutes", 5)),
                min=1,
                max=60,
                step=1,
                format="%.0f",
            ).classes("col")
        alarm_sound = ui.switch(
            "Play sound when ringing", value=bool(alarm_cfg.get("sound", True))
        ).props("dense color=orange")
        ui.label("Alarm fires at the set time while Flip Clock is open.").classes(
            "text-caption text-grey q-mt-xs"
        )
        alarm_status = ui.label("").classes("text-caption text-grey q-mt-xs")

        def _refresh_alarm_status():
            if not alarm_enable.value:
                alarm_status.set_text("Alarm is off.")
                return
            try:
                h = int(alarm_hour.value or 7)
                m = int(alarm_minute.value or 0)
            except (TypeError, ValueError):
                h, m = 7, 0
            label = (alarm_label.value or "Alarm").strip() or "Alarm"
            alarm_status.set_text(f"Armed: {label} at {h:02d}:{m:02d} {alarm_ampm.value}")

        def _persist_alarm():
            try:
                hour = max(1, min(12, int(alarm_hour.value or 7)))
            except (TypeError, ValueError):
                hour = 7
                alarm_hour.value = hour
            try:
                minute = max(0, min(59, int(alarm_minute.value or 0)))
            except (TypeError, ValueError):
                minute = 0
                alarm_minute.value = minute
            try:
                snooze = max(1, min(60, int(alarm_snooze.value or 5)))
            except (TypeError, ValueError):
                snooze = 5
                alarm_snooze.value = snooze
            cfg = {
                "enabled": bool(alarm_enable.value),
                "hour": hour,
                "minute": minute,
                "ampm": alarm_ampm.value or "AM",
                "label": (alarm_label.value or "Alarm").strip() or "Alarm",
                "sound": bool(alarm_sound.value),
                "snooze_minutes": snooze,
            }
            alarm_engine.save(cfg)
            _refresh_alarm_status()

        for ctrl in (
            alarm_enable,
            alarm_hour,
            alarm_minute,
            alarm_ampm,
            alarm_label,
            alarm_snooze,
            alarm_sound,
        ):
            ctrl.on_value_change(lambda _e: _persist_alarm())
        _refresh_alarm_status()

        with ui.row().classes("w-full q-mt-sm q-gutter-sm"):
            ui.button(
                "Save Alarm",
                icon="save",
                on_click=lambda: (_persist_alarm(), ui.notify("Alarm saved.", type="positive")),
            ).props("outline dense color=orange").classes("col")

    # ---- Time Triggers --------------------------------------------------
    with _expansion("Time Triggers", "timer", value=False):
        ui.label("Repeat mouse clicks or keyboard presses on a timer.").classes(
            "text-caption text-grey q-mb-sm"
        )

        ui.label("Trigger type").classes("text-subtitle2 q-mb-xs")
        mode = ui.radio(["Mouse Click", "Keyboard Key"], value="Mouse Click").props("inline dense")

        with ui.element("div").classes("q-mt-sm") as mouse_section:
            ui.label("Mouse target").classes("text-subtitle2 q-mb-xs")
            with ui.row().classes("w-full q-gutter-sm items-center"):
                x_input = ui.number("X", value=500, format="%.0f").classes("col")
                y_input = ui.number("Y", value=500, format="%.0f").classes("col")
                pick_btn = ui.button("Pick Location", icon="my_location").props("outline dense")
            with ui.row().classes("w-full q-mt-sm"):
                button_select = ui.select(
                    ["left", "right", "middle"], value="left", label="Button"
                ).classes("col")

        with ui.element("div").classes("q-mt-sm") as key_section:
            ui.label("Keyboard target").classes("text-subtitle2 q-mb-xs")
            with ui.row().classes("w-full q-mt-sm items-center q-gutter-sm"):
                mod_alt = ui.checkbox("Alt")
                mod_ctrl = ui.checkbox("Ctrl")
                mod_shift = ui.checkbox("Shift")
            with ui.row().classes("w-full q-mt-sm items-end q-gutter-sm"):
                key_select = ui.select(COMMON_KEYS, value="alt+tab", label="Key").classes("col")
                key_input = (
                    ui.input("Or type combo", value="")
                    .props("placeholder='alt+tab'")
                    .classes("col")
                )
            ui.label("Use modifiers + key, pick a preset, or type combos like alt+tab.").classes(
                "text-caption text-grey q-mt-xs"
            )

        with ui.element("div").classes("q-mt-sm"):
            ui.label("Schedule").classes("text-subtitle2 q-mb-xs")
            with ui.row().classes("w-full q-gutter-sm q-mt-sm"):
                interval_input = ui.number(
                    "Interval (sec)", value=1.0, min=0.05, step=0.1
                ).classes("col")
                repeats_input = ui.number(
                    "Repeats (0 = ∞)", value=0, min=0, step=1, format="%.0f"
                ).classes("col")
                delay_input = ui.number(
                    "Start delay (sec)", value=3, min=0, step=1, format="%.0f"
                ).classes("col")

        def refresh_mode():
            is_mouse = mode.value == "Mouse Click"
            mouse_section.set_visibility(is_mouse)
            key_section.set_visibility(not is_mouse)

        mode.on_value_change(refresh_mode)
        refresh_mode()

        with ui.row().classes("w-full q-mt-md q-gutter-sm"):
            start_btn = ui.button("Start", icon="play_arrow", color="green").classes("col")
            stop_btn = ui.button("Stop", icon="stop", color="red").classes("col")
            stop_btn.disable()

        hotkey_note = (
            "Global hotkey F6 toggles start/stop · Ctrl+Esc quits the app"
            if HAS_KEYBOARD
            else "Install 'keyboard' for F6 / Ctrl+Esc hotkeys"
        )
        ui.label(hotkey_note).classes("text-caption text-grey q-mt-xs")

        ui.label("Status Log").classes("text-subtitle2 q-mt-md q-mb-xs")
        log_area = ui.textarea().props("readonly outlined").classes("w-full log-box")
        log_area.value = ""

        def append_log(msg: str):
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            log_area.value = (log_area.value + f"[{stamp}] {msg}\n").lstrip()
            if log_area.value.count("\n") > 80:
                log_area.value = "\n".join(log_area.value.splitlines()[-80:])
            log_area.update()

        def drain_log_queue():
            while True:
                try:
                    append_log(log_queue.get_nowait())
                except queue.Empty:
                    break

        ui.timer(0.15, drain_log_queue)

        if not HAS_PYAUTOGUI:
            append_log("WARNING: pyautogui not found. pip install pyautogui")
        if not HAS_KEYBOARD:
            append_log("NOTE: install 'keyboard' for reliable key triggers and hotkeys")

        def pick_location():
            if not HAS_PYAUTOGUI:
                ui.notify("Install pyautogui first: pip install pyautogui", type="negative")
                return
            append_log("Move mouse to target... capturing in 3 seconds.")
            start_btn.disable()

            def countdown():
                for i in (3, 2, 1):
                    enqueue_log(f"Capturing in {i}...")
                    time.sleep(1)
                pos = pyautogui.position()
                x_input.value = pos.x
                y_input.value = pos.y
                x_input.update()
                y_input.update()
                enqueue_log(f"Captured location: ({pos.x}, {pos.y})")
                start_btn.enable()

            threading.Thread(target=countdown, daemon=True).start()

        pick_btn.on_click(pick_location)

        def _resolved_key() -> str:
            custom = (key_input.value or "").strip()
            if custom:
                return _format_key_combo(_parse_key_combo(custom))

            selected = (key_select.value or "space").strip()
            if "+" in selected:
                return _format_key_combo(_parse_key_combo(selected))

            parts: list[str] = []
            if mod_ctrl.value:
                parts.append("ctrl")
            if mod_alt.value:
                parts.append("alt")
            if mod_shift.value:
                parts.append("shift")

            main = _normalize_key(selected)
            if main not in parts:
                parts.append(main)
            return _format_key_combo(parts) if parts else "space"

        def gather_config():
            try:
                interval = max(0.05, float(interval_input.value or 1))
            except (TypeError, ValueError):
                ui.notify("Interval must be a number.", type="negative")
                return None
            try:
                repeats = int(repeats_input.value or 0)
            except (TypeError, ValueError):
                ui.notify("Repeats must be an integer.", type="negative")
                return None
            try:
                delay = max(0, float(delay_input.value or 0))
            except (TypeError, ValueError):
                delay = 0

            is_mouse = mode.value == "Mouse Click"
            cfg = {
                "mode": "mouse" if is_mouse else "keyboard",
                "interval": interval,
                "repeats": repeats,
                "delay": delay,
            }

            if is_mouse:
                if not HAS_PYAUTOGUI:
                    ui.notify("Mouse triggers require pyautogui.", type="negative")
                    return None
                try:
                    cfg["x"] = int(x_input.value)
                    cfg["y"] = int(y_input.value)
                except (TypeError, ValueError):
                    ui.notify("X and Y must be integers.", type="negative")
                    return None
                cfg["button"] = button_select.value
            else:
                key = _resolved_key()
                if not key:
                    ui.notify("Please choose or enter a key to press.", type="negative")
                    return None
                if not HAS_KEYBOARD and not HAS_PYAUTOGUI:
                    ui.notify("Install keyboard or pyautogui for key triggers.", type="negative")
                    return None
                cfg["key"] = key

            return cfg

        def start_clicked():
            cfg = gather_config()
            if cfg is None:
                return

            def delayed_start():
                for i in range(int(cfg["delay"]), 0, -1):
                    enqueue_log(f"Starting in {i}...")
                    time.sleep(1)
                engine.start(cfg)

            start_btn.disable()
            stop_btn.enable()
            threading.Thread(target=delayed_start, daemon=True).start()

        def stop_clicked():
            engine.stop()
            start_btn.enable()
            stop_btn.disable()

        def toggle_from_hotkey():
            if engine.running:
                stop_clicked()
            else:
                start_clicked()

        global _trigger_toggle
        _trigger_toggle = toggle_from_hotkey

        start_btn.on_click(start_clicked)
        stop_btn.on_click(stop_clicked)

# =========================================================================
#  UI
# =========================================================================
@ui.page("/")
def main_page():
    ui.dark_mode(True)
    ui.add_head_html(
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    )
    ui.add_css(FLIP_CLOCK_CSS)

    digits: dict[str, FlipDigit] = {}

    with ui.element("div").classes("clock-shell"):
        with ui.element("div").classes("clock-hover-zone"):
            with ui.element("div").classes("clock-toolbar"):
                exit_fs_btn = (
                    ui.button(icon="fullscreen_exit")
                    .props("flat round dense no-caps")
                    .classes("menu-btn")
                    .tooltip("Exit fullscreen")
                )
                minimize_btn = (
                    ui.button(icon="minimize")
                    .props("flat round dense no-caps")
                    .classes("menu-btn menu-btn-minimize")
                    .tooltip("Minimize")
                )
                close_btn = (
                    ui.button(icon="close")
                    .props("flat round dense no-caps")
                    .classes("menu-btn")
                    .tooltip("Close (Ctrl+Esc)")
                )
                settings_btn = (
                    ui.button(icon="settings")
                    .props("flat round dense no-caps")
                    .classes("menu-btn")
                    .tooltip("Settings")
                )

        with ui.element("div").classes("clock-face"):
            with ui.element("div").classes("digits-row"):
                digits["h1"] = FlipDigit()
                digits["h2"] = FlipDigit()
                with ui.element("div").classes("colon"):
                    ui.element("span")
                    ui.element("span")
                digits["m1"] = FlipDigit()
                digits["m2"] = FlipDigit()
                with ui.element("div").classes("colon"):
                    ui.element("span")
                    ui.element("span")
                digits["s1"] = FlipDigit()
                digits["s2"] = FlipDigit()
                ui.element("div").classes("ampm-spacer")
                digits["ap1"] = FlipDigit(ampm=True)
                digits["ap2"] = FlipDigit(ampm=True)

        with ui.element("div").classes("alarm-banner") as alarm_banner:
            alarm_banner.set_visibility(False)
            alarm_banner_title = ui.label("Alarm").classes("alarm-banner-title")
            alarm_banner_sub = ui.label("").classes("alarm-banner-sub")
            with ui.row().classes("q-gutter-sm"):
                snooze_btn = ui.button("Snooze", icon="snooze", color="orange").props(
                    "unelevated dense"
                )
                dismiss_btn = ui.button("Dismiss", icon="alarm_off", color="grey-8").props(
                    "unelevated dense"
                )

    def show_alarm_banner(*, snoozed: bool, when: datetime.datetime):
        label = str(alarm_engine.config.get("label") or "Alarm")
        alarm_banner_title.set_text(label)
        prefix = "Snooze ended · " if snoozed else ""
        alarm_banner_sub.set_text(f"{prefix}{when.strftime('%I:%M %p')}")
        alarm_banner.set_visibility(True)

    def hide_alarm_banner():
        alarm_banner.set_visibility(False)

    def on_dismiss():
        alarm_engine.dismiss()
        hide_alarm_banner()

    def on_snooze():
        mins = int(alarm_engine.config.get("snooze_minutes", 5))
        alarm_engine.snooze(mins)
        hide_alarm_banner()
        ui.notify(f"Snoozed for {mins} min", type="info")

    dismiss_btn.on_click(on_dismiss)
    snooze_btn.on_click(on_snooze)
    alarm_engine.set_ring_handler(show_alarm_banner)

    def tick():
        now = datetime.datetime.now()
        h = now.strftime("%I")
        m = now.strftime("%M")
        s = now.strftime("%S")
        ampm = now.strftime("%p")
        digits["h1"].set_digit(h[0])
        digits["h2"].set_digit(h[1])
        digits["m1"].set_digit(m[0])
        digits["m2"].set_digit(m[1])
        digits["s1"].set_digit(s[0])
        digits["s2"].set_digit(s[1])
        digits["ap1"].set_digit(ampm[0])
        digits["ap2"].set_digit(ampm[1])
        alarm_engine.check(now)

    ui.timer(0.2, tick)

    with ui.dialog() as triggers_dialog, ui.card().classes("settings-card"):
        triggers_dialog.props("backdrop-filter")
        _build_time_triggers_panel(triggers_dialog.close)

    settings_btn.on_click(triggers_dialog.open)
    exit_fs_btn.on_click(_exit_fullscreen)
    minimize_btn.on_click(_minimize_window)
    close_btn.on_click(_close_app)

    def process_actions():
        while True:
            try:
                action = action_queue.get_nowait()
            except queue.Empty:
                break
            if action == "toggle" and _trigger_toggle is not None:
                _trigger_toggle()
            elif action == "close":
                _close_app()

    ui.timer(0.1, process_actions)


@app.on_shutdown
def _shutdown():
    engine.stop()
    alarm_engine.dismiss()
    if HAS_KEYBOARD:
        try:
            kb_lib.unhook_all_hotkeys()
        except Exception:
            pass


def _register_hotkey():
    if not HAS_KEYBOARD:
        return

    def on_f6():
        action_queue.put("toggle")

    def on_ctrl_esc():
        action_queue.put("close")

    try:
        kb_lib.add_hotkey("F6", on_f6)
    except Exception:
        pass
    try:
        kb_lib.add_hotkey("ctrl+esc", on_ctrl_esc)
    except Exception:
        pass


def _ensure_webview2_user_data() -> Path:
    """WebView2 needs a writable profile dir; PyInstaller _MEIPASS is read-only.

    Use a per-process folder. Sharing one UserDataFolder across overlapping runs
    (or parent/child races) commonly yields E_ABORT and a white hung window.
    """
    import os
    import tempfile

    root = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "FlipClock" / "WebView2"
    base = root / f"session-{os.getpid()}"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        base = Path(tempfile.gettempdir()) / f"FlipClockWebView2-{os.getpid()}"
        base.mkdir(parents=True, exist_ok=True)
    # Force overwrite so a stale env from a previous launch cannot pin a locked folder.
    os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(base)
    return base


def run_app() -> None:
    global APP_PORT

    # Required for PyInstaller + native pywebview subprocess on Windows.
    mp.freeze_support()
    webview_data = _ensure_webview2_user_data()
    capture_privacy.install_nicegui_hook()
    app.native.window_args["resizable"] = True
    # MSHTML (legacy IE) renders a blank page with NiceGUI; Edge WebView2 is required.
    app.native.start_args["gui"] = "edgechromium"
    # pywebview's default private_mode temp dir can vanish before WebView2 inits (E_ABORT / white screen).
    app.native.start_args["storage_path"] = str(webview_data)
    app.native.start_args["private_mode"] = False

    from nicegui.native.native_mode import find_open_port

    APP_PORT = find_open_port(8080, 8999)
    _register_hotkey()

    favicon = _ensure_ico_icon()
    favicon_arg = str(favicon) if favicon else None

    ui.run(
        title=WINDOW_TITLE,
        favicon=favicon_arg,
        native=True,
        port=APP_PORT,
        fullscreen=True,
        dark=True,
        reload=False,
        show=False,
    )


if __name__ == "__main__":
    run_app()
