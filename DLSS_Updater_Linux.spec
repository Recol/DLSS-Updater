# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Linux builds

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

dlss_updater_imports = collect_submodules('dlss_updater')
dlss_updater_datas = collect_data_files('dlss_updater')


flet_datas = collect_data_files('flet')
flet_desktop_datas = collect_data_files('flet_desktop')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('dlss_updater', 'dlss_updater'),
        ('release_notes.txt', '.'),
        ('dlss_updater/icons/*.png', 'icons/'),
    ] + flet_datas + flet_desktop_datas,
    hiddenimports=[
        'pefile',  # Still needed for DLL version parsing
        'psutil',
        'importlib.metadata',
        'packaging',
        'concurrent.futures',
        'flet',
        'flet_desktop',
        'msgspec',
        'aiohttp',
        'aiosqlite',
        'aiofiles',
        'uvloop',  # Linux event loop (optional but included)
    ] + dlss_updater_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows-only modules to exclude on Linux
        'winloop',
        'scandir_rs',
        'winreg',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DLSS_Updater',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,   # Strip symbols for smaller binary on Linux
    upx=True,     # UPX compression
    console=True,
    optimize=2,
    # Note: No icon specified - Linux doesn't use .ico files
    # Desktop integration should be handled via .desktop files
)
