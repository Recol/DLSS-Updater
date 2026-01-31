# PyInstaller hook for msgspec
# Required because msgspec uses a C extension with free-threaded Python naming
# (e.g., _core.cp314t-win_amd64.pyd) that PyInstaller doesn't auto-detect

import os
import glob
import sysconfig
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Collect submodules (but _core will be handled separately as binary)
hiddenimports = [m for m in collect_submodules('msgspec') if m != 'msgspec._core']

# Collect data files (py, pyi, typed marker files)
datas = collect_data_files('msgspec')

# Manually collect the C extension binary
binaries = []
try:
    import msgspec
    msgspec_dir = os.path.dirname(msgspec.__file__)
    ext_suffix = sysconfig.get_config_var('EXT_SUFFIX')

    # Find the _core extension with the correct suffix
    core_pattern = os.path.join(msgspec_dir, f'_core{ext_suffix}')
    core_files = glob.glob(core_pattern)

    if not core_files:
        # Fallback: find any _core extension
        core_files = glob.glob(os.path.join(msgspec_dir, '_core*.pyd'))
        if not core_files:
            core_files = glob.glob(os.path.join(msgspec_dir, '_core*.so'))

    if core_files:
        # Binary format: (source_path, destination_directory)
        binaries.append((core_files[0], 'msgspec'))
        print(f"[hook-msgspec] Found binary: {core_files[0]}")
    else:
        print(f"[hook-msgspec] WARNING: _core binary not found in {msgspec_dir}")

except ImportError:
    print("[hook-msgspec] WARNING: msgspec not installed")
