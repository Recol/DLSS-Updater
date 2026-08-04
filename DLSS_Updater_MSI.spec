# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Windows MSI builds (onedir mode)
# This spec produces a directory output that Briefcase wraps into an MSI installer.
# Build with: set PYTHON_GIL=0 && pyinstaller DLSS_Updater_MSI.spec --noconfirm
# Then run: briefcase package windows --no-input

import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

sys.path.insert(0, os.path.abspath(SPECPATH))
from build_support import flet_client_datas

block_cipher = None

# Collect all dlss_updater submodules and data
dlss_updater_imports = collect_submodules('dlss_updater')
dlss_updater_datas = collect_data_files('dlss_updater')

# Collect Flet framework data files.
# NOTE: contrary to the old comment here, these collect NO client runtime -
# the flet_desktop wheel is two .py files. The actual client is bundled by
# flet_client_datas() below; see build_support.py for why (issue #265).
flet_datas = collect_data_files('flet')
flet_desktop_datas = collect_data_files('flet_desktop')

# The Flet desktop client archive, placed where ensure_client_cached() looks
# for it so the app never downloads it at first launch. Fails the build if
# absent rather than silently shipping a binary that only breaks on a clean
# machine.
flet_client = flet_client_datas()

# certifi's CA bundle. Without it every HTTPS call in the frozen app (DLL
# manifest, update check, Steam API, and flet's own client fetch) depends on
# the user's Windows root store being complete and up to date - which on a
# fresh Windows 11 install it may not be. main.py points SSL_CERT_FILE here.
certifi_datas = collect_data_files('certifi')

# Note: msgspec is handled by custom hook in pyinstaller_hooks/hook-msgspec.py
# The hook properly collects the free-threaded Python C extension (.cp314t-*.pyd)

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('dlss_updater', 'dlss_updater'),
        ('release_notes.txt', '.'),
        ('dlss_updater/icons', 'icons'),
    ] + flet_datas + flet_desktop_datas + dlss_updater_datas + flet_client + certifi_datas,
    hiddenimports=[
        # Only truly dynamic imports that PyInstaller can't detect:
        'winloop',           # Conditionally imported in main.py with try/except
        'importlib.metadata',  # Imported inside function in utils.py
        'flet_desktop',      # Internal runtime used by flet (never directly imported)
        'tomli_w',           # Imported lazily by msgspec.toml.encode() for config.toml persistence
        'certifi',           # Imported early in main.py to pin SSL_CERT_FILE
    ] + dlss_updater_imports,
    hookspath=['pyinstaller_hooks'],  # Custom hooks directory for msgspec
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Linux-only modules to exclude on Windows
        'uvloop',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir mode: EXE only gets scripts, COLLECT bundles everything into a directory
exe = EXE(
    pyz,
    a.scripts,
    [('X gil=0', None, 'OPTION')],  # Force GIL disabled for free-threaded Python 3.14
    exclude_binaries=True,  # Required for onedir mode
    name='DLSS_Updater',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX disabled to avoid Windows Defender false positives
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='dlss_updater/icons/dlss_updater.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,  # UPX disabled for all collected binaries
    upx_exclude=[],
    name='DLSS_Updater',
)
