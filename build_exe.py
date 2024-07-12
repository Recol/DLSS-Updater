import os
import subprocess

# Get the absolute path to the directory containing this script
script_dir = os.path.abspath(os.path.dirname(__file__))

# Set the paths
main_py = os.path.join(script_dir, 'main.py')
output_dir = os.path.join(script_dir, 'dist')
latest_dll_path = os.path.join(script_dir, 'latest_dll', 'nvngx_dlss.dll')

# Ensure output directory exists
os.makedirs(output_dir, exist_ok=True)

print(f"Script directory: {script_dir}")
print(f"Main.py path: {main_py}")
print(f"Output directory: {output_dir}")
print(f"Latest DLL path: {latest_dll_path}")

# Prepare PyInstaller command
pyinstaller_args = [
    'pyinstaller',
    '--onefile',
    f'--add-data={latest_dll_path};latest_dll',
    '--name=DLSS_Updater',
    f'--distpath={output_dir}',
    '--clean',
    '--log-level=WARN',
    '--hidden-import=pefile',
    '--hidden-import=psutil',
    '--hidden-import=importlib.metadata',
    '--hidden-import=packaging',
    main_py
]

print("PyInstaller command:")
print(" ".join(pyinstaller_args))

print("Building executable with PyInstaller...")
try:
    result = subprocess.run(pyinstaller_args, check=True, capture_output=True, text=True)
    print(result.stdout)
except subprocess.CalledProcessError as e:
    print(f"Error occurred: {e}")
    print("Stdout:")
    print(e.stdout)
    print("Stderr:")
    print(e.stderr)

print("Build process completed.")
