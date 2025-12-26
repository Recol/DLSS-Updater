# -- mode: python ; coding: utf-8 --

import pefile
import psutil
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

dlss_updater_imports = collect_submodules('dlss_updater')
dlss_updater_datas = collect_data_files('dlss_updater')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('dlss_updater', 'dlss_updater'),
        ('release_notes.txt', '.'),
        ('dlss_updater/icons/*.png', 'icons/'),
    ],
    hiddenimports=[
        'pefile', 'psutil', 'importlib.metadata', 'packaging',
        'concurrent.futures', 'flet', 'msgspec', 'aiohttp',
        'aiosqlite', 'aiofiles', 'scandir_rs', 'winloop'
    ] + dlss_updater_imports,
    hookspath=['./hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    a.binaries,          # include binaries directly in the one-file exe
    a.zipfiles,
    a.datas,
    [],
    name='DLSS_Updater',
    version='version.txt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,            # UPX compression
    console=True,        # change to False if you don't want a console window
    optimize=2,
    icon='dlss_updater/icons/dlss_updater.ico',
)
