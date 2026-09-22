# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for FlipClock (NiceGUI + pywebview).

Onefile + multiprocessing is unreliable on Windows (child process often never
creates the WebView window). Onedir keeps native DLLs and spawn working.
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

import nicegui

nicegui_dir = os.path.dirname(nicegui.__file__)

datas = [
    (nicegui_dir, "nicegui"),
    ("icons8-digital-clock-100.png", "."),
    ("icons8-digital-clock.ico", "."),
]
binaries = []
hiddenimports = ["clr", "pythonnet", "capture_privacy", "multiprocessing"]

ng_datas, ng_binaries, ng_hidden = collect_all("nicegui")
datas += ng_datas
binaries += ng_binaries
hiddenimports += ng_hidden
hiddenimports += collect_submodules("webview")

a = Analysis(
    ["flip_clock.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FlipClock",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["icons8-digital-clock.ico"],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="FlipClock",
)
