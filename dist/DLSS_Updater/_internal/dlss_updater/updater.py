import os
import shutil
import pefile
from dlss_updater.config import LATEST_DLL_VERSIONS, LATEST_DLL_PATHS
from pathlib import Path
import stat
import time
import psutil
import asyncio
from packaging import version


def parse_version(version_string):
    # Replace commas with dots and remove any trailing zeros
    cleaned_version = ".".join(version_string.replace(",", ".").split(".")[:3])
    return version.parse(cleaned_version)


def get_dll_version(dll_path):
    try:
        with open(dll_path, "rb") as file:
            pe = pefile.PE(data=file.read())
            for fileinfo in pe.FileInfo:
                for entry in fileinfo:
                    if hasattr(entry, "StringTable"):
                        for st in entry.StringTable:
                            for key, value in st.entries.items():
                                if key == b"FileVersion":
                                    return value.decode("utf-8").strip()
    except Exception as e:
        print(f"Error reading version from {dll_path}: {e}")
    return None


def remove_read_only(file_path):
    if not os.access(file_path, os.W_OK):
        print(f"Removing read-only attribute from {file_path}")
        os.chmod(file_path, stat.S_IWRITE)


def restore_permissions(file_path, original_permissions):
    os.chmod(file_path, original_permissions)


def is_file_in_use(file_path, timeout=5):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with open(file_path, "rb"):
                return False  # File is not in use
        except PermissionError:
            for proc in psutil.process_iter(["pid", "name", "open_files"]):
                try:
                    for file in proc.open_files():
                        if file.path == file_path:
                            print(
                                f"File {file_path} is in use by process {proc.name()} (PID: {proc.pid})"
                            )
                            return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        time.sleep(0.1)  # Short sleep to prevent CPU overuse
    print(f"Timeout reached while checking if file {file_path} is in use")
    return True  # Assume file is NOT in use if we can't determine otherwise to prevent hanging conditions


def normalize_path(path):
    return os.path.normpath(path)


async def create_backup(dll_path):
    backup_path = dll_path.with_suffix(".dlsss")
    try:
        await asyncio.to_thread(shutil.copy2, dll_path, backup_path)
        print(f"Created backup: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"Failed to create backup for {dll_path}: {e}")
        return None

async def update_dll(dll_path, latest_dll_path):
    dll_path = Path(normalize_path(dll_path)).resolve()
    latest_dll_path = Path(normalize_path(latest_dll_path)).resolve()
    print(f"Checking DLL at {dll_path}...")

    original_permissions = os.stat(dll_path).st_mode

    try:
        existing_version = get_dll_version(dll_path)
        latest_version = get_dll_version(latest_dll_path)

        if existing_version and latest_version:
            existing_parsed = parse_version(existing_version)
            latest_parsed = parse_version(latest_version)

            print(
                f"Existing version: {existing_version}, Latest version: {latest_version}"
            )

            if existing_parsed < parse_version("2.0.0"):
                print(
                    f"Skipping update for {dll_path}: Version {existing_version} is less than 2.0.0 and cannot be updated."
                )
                return False, None

            if existing_parsed >= latest_parsed:
                print(f"{dll_path} is already up-to-date (version {existing_version}).")
                return False, None
            else:
                print(f"Update needed: {existing_version} -> {latest_version}")
                print("Preparing to update...")

        # Check if the target path exists
        if not dll_path.exists():
            print(f"Error: Target DLL path does not exist: {dll_path}")
            return False, None

        # Check if the latest DLL path exists
        if not latest_dll_path.exists():
            print(f"Error: Latest DLL path does not exist: {latest_dll_path}")
            return False, None

        # Ensure the target directory is writable
        if not os.access(dll_path.parent, os.W_OK):
            print(f"Error: No write permission to the directory: {dll_path.parent}")
            return False, None

        # Create backup
        print("Creating backup...")
        backup_path = await create_backup(dll_path)
        if not backup_path:
            print(f"Skipping update for {dll_path} due to backup failure.")
            return False, None

        print("Checking file permissions...")
        remove_read_only(dll_path)

        print("Checking if file is in use...")
        retry_count = 3
        while retry_count > 0:
            if not await asyncio.to_thread(is_file_in_use, str(dll_path)):
                break
            print(
                f"File is in use. Retrying in 2 seconds... (Attempts left: {retry_count})"
            )
            await asyncio.sleep(2)
            retry_count -= 1

        if retry_count == 0:
            print(
                f"File {dll_path} is still in use after multiple attempts. Cannot update."
            )
            restore_permissions(dll_path, original_permissions)
            return False, None

        print("Starting file copy...")
        await asyncio.to_thread(shutil.copyfile, latest_dll_path, dll_path)
        print("File copy completed.")

        # Restore original permissions
        restore_permissions(dll_path, original_permissions)

        print(
            f"Successfully updated {dll_path} from version {existing_version} to {latest_version}."
        )
        return True, backup_path
    except Exception as e:
        print(f"Error updating {dll_path}: {e}")
        restore_permissions(dll_path, original_permissions)
        return False, None