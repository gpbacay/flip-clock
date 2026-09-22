# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['flip_clock.py'],
    pathex=[],
    binaries=[],
    datas=[('C:/Users/Gianne Bacay/Desktop/project test/COMMISSIONS/flip-clock/.venv-build/Lib/site-packages/nicegui', 'nicegui'), ('icons8-digital-clock-100.png', '.'), ('icons8-digital-clock.ico', '.')],
    hiddenimports=['clr', 'pythonnet', 'capture_privacy'],
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
    a.binaries,
    a.datas,
    [],
    name='FlipClock',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icons8-digital-clock.ico'],
)
