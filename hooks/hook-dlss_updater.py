from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules('dlss_updater')

# Add Pillow modules for WebP thumbnail generation
hiddenimports += [
    'PIL',
    'PIL.Image',
    'PIL.WebPImagePlugin',
    'PIL.JpegImagePlugin',
]

datas = collect_data_files('dlss_updater')