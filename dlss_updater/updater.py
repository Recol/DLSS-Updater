import os
import shutil
import pefile
from dlss_updater.config import LATEST_DLL_VERSION, LATEST_DLL_PATH
from pathlib import Path
import stat
import psutil
import time
from packaging import version

def parse_version(version_string):
    # Replace commas with dots and remove any trailing zeros
    cleaned_version = '.'.join(version_string.replace(',', '.').split('.')[:3])
    return version.parse(cleaned_version)

def get_dll_version(dll_path):
    try:
        with open(dll_path, 'rb') as file:
            pe = pefile.PE(data=file.read())
            for fileinfo in pe.FileInfo:
                for entry in fileinfo:
                    if hasattr(entry, 'StringTable'):
                        for st in entry.StringTable:
                            for key, value in st.entries.items():
                                if key == b'FileVersion':
                                    return value.decode('utf-8').strip()
    except Exception as e:
        print(f"Error reading version from {dll_path}: {e}")
    return None

def remove_read_only(file_path):
    if not os.access(file_path, os.W_OK):
        print(f"Removing read-only attribute from {file_path}")
        os.chmod(file_path, stat.S_IWRITE)

def set_read_only(file_path):
    if os.access(file_path, os.W_OK):
        print(f"Setting read-only attribute for {file_path}")
        os.chmod(file_path, stat.S_IREAD)

def is_file_in_use(file_path):
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            for item in proc.open_files():
                if file_path == item.path:
                    print(f"File {file_path} is in use by process {proc.info['name']} (PID: {proc.info['pid']})")
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False

def normalize_path(path):
    return os.path.normpath(path)

def update_dll(dll_path, latest_dll_path):
    dll_path = Path(normalize_path(dll_path)).resolve()
    latest_dll_path = Path(normalize_path(latest_dll_path)).resolve()
    print(f"Updating DLL from {latest_dll_path} to {dll_path}...")

    try:
        existing_version = get_dll_version(dll_path)
        latest_version = get_dll_version(latest_dll_path)

        if existing_version and latest_version:
            existing_parsed = parse_version(existing_version)
            latest_parsed = parse_version(latest_version)
            
            print(f"Existing version: {existing_version}, Latest version: {latest_version}")
            
            if existing_parsed >= latest_parsed:
                print(f"{dll_path} is already up-to-date (version {existing_version}).")
                return False
            else:
                print(f"Update needed: {existing_version} -> {latest_version}")

        # Check if the target path exists
        if not dll_path.exists():
            print(f"Error: Target DLL path does not exist: {dll_path}")
            return False

        # Check if the latest DLL path exists
        if not latest_dll_path.exists():
            print(f"Error: Latest DLL path does not exist: {latest_dll_path}")
            return False

        # Ensure the target directory is writable
        if not os.access(dll_path.parent, os.W_OK):
            print(f"Error: No write permission to the directory: {dll_path.parent}")
            return False

        # Remove read-only attribute if set
        remove_read_only(dll_path)

        # Check if the file is in use and retry if necessary
        retry_count = 5
        retry_interval = 10  # seconds
        while is_file_in_use(dll_path) and retry_count > 0:
            print(f"File {dll_path} is in use. Retrying in {retry_interval} seconds...")
            time.sleep(retry_interval)
            retry_count -= 1

        if retry_count == 0 and is_file_in_use(dll_path):
            print(f"File {dll_path} is still in use after multiple attempts. Cannot update.")
            return False

        # Copy the latest DLL to the target path
        print(f"Copying {latest_dll_path} to {dll_path}")
        shutil.copyfile(latest_dll_path, dll_path)
        print(f"Updated {dll_path} from version {existing_version} to {latest_version}.")

        # Set the read-only attribute back
        set_read_only(dll_path)
        return True
    except OSError as e:
        print(f"OSError: {e}")
        print(f"Path: {dll_path}")
        print("Check if the file is open in another program or if there are permission issues.")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False