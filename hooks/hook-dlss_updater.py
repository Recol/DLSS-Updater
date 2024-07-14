from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules('dlss_updater')
datas = collect_data_files('dlss_updater')