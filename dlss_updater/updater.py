import os
import shutil
import pefile
from dlss_updater.config import LATEST_DLL_VERSION, LATEST_DLL_PATH
import tempfile
from pathlib import Path
import stat
import psutil
import time

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
                                    version = value.decode('utf-8')
                                    return version
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
        if existing_version and existing_version >= LATEST_DLL_VERSION:
            print(f"{dll_path} is already up-to-date (version {existing_version}).")
            return False

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

        # Backup to a temporary directory first
        temp_backup_path = Path(tempfile.gettempdir()) / (dll_path.name + ".bak")
        print(f"Creating temporary backup: {temp_backup_path}")
        shutil.copyfile(dll_path, temp_backup_path)
        
        # Move the temporary backup to the target directory
        final_backup_path = dll_path.with_suffix(".dll.bak")
        print(f"Final backup path: {final_backup_path}")

        # Remove read-only attribute from the backup file if set
        remove_read_only(final_backup_path)
        shutil.move(temp_backup_path, final_backup_path)
        
        print(f"Copying {latest_dll_path} to {dll_path}")
        shutil.copyfile(latest_dll_path, dll_path)
        print(f"Updated {dll_path} from version {existing_version} to {LATEST_DLL_VERSION}.")

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
