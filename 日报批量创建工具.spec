# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


def runtime_binaries():
    roots = [Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs"]
    exact_names = {
        "python3.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "concrt140.dll",
        "vccorlib140.dll",
    }
    prefixes = ("api-ms-win-crt", "api-ms-win-core")
    binaries = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("*.dll"):
            name = path.name.lower()
            if name in exact_names or any(name.startswith(prefix) for prefix in prefixes):
                normalized = str(path.resolve())
                if normalized not in seen:
                    binaries.append((normalized, "."))
                    seen.add(normalized)
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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
