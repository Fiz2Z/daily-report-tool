# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


def runtime_binaries():
    root = Path(sys.base_prefix)
    names = [
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
    ]
    binaries = []
    for name in names:
        path = root / name
        if path.exists():
            binaries.append((str(path), "."))
    return binaries


a = Analysis(
    ['daily_report_gui.py'],
    pathex=[],
    binaries=runtime_binaries(),
    datas=[],
    hiddenimports=[],
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
    name='日报批量创建工具',
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
)
