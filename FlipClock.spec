# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

import nicegui

block_cipher = None

root = Path(SPECPATH)
nicegui_path = Path(nicegui.__file__).parent

a = Analysis(
    [str(root / "flip_clock.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(nicegui_path), "nicegui"),
        (str(root / "icons8-digital-clock.ico"), "."),
        (str(root / "icons8-digital-clock-100.png"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "altair",
        "pyarrow",
        "plotly",
        "matplotlib",
        "numpy",
        "IPython",
        "jedi",
        "parso",
        "pandas",
        "cv2",
        "pyecharts",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FlipClock",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(root / "icons8-digital-clock.ico")],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FlipClock",
)
