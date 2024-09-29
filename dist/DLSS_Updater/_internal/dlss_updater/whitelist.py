import os
import csv

# import shutil
import asyncio
from io import StringIO
from urllib.request import urlopen
from urllib.error import URLError

# from pathlib import Path
from dlss_updater.logger import setup_logger

# import win32file
# import win32con
# from watchdog.observers import Observer
# from watchdog.events import FileSystemEventHandler

logger = setup_logger()

WHITELIST_URL = (
    "https://raw.githubusercontent.com/Recol/DLSS-Updater-Whitelist/main/whitelist.csv"
)


# class WarframeDLLHandler(FileSystemEventHandler):
#     def __init__(self, dll_path, backup_path):
#         self.dll_path = dll_path
#         self.backup_path = backup_path

#     def on_modified(self, event):
#         if event.src_path == self.dll_path:
#             logger.info(f"Detected modification attempt on {self.dll_path}")
#             shutil.copy2(self.backup_path, self.dll_path)
#             win32file.SetFileAttributes(
#                 self.dll_path,
#                 win32con.FILE_ATTRIBUTE_READONLY | win32con.FILE_ATTRIBUTE_SYSTEM,
#             )
#             logger.info(f"Restored and protected {self.dll_path}")


async def fetch_whitelist():
    try:
        with urlopen(WHITELIST_URL) as response:
            csv_data = StringIO(response.read().decode("utf-8"))
        reader = csv.reader(csv_data)
        return set(row[0].strip() for row in reader if row and row[0].strip())
    except URLError as e:
        logger.error(f"Failed to fetch whitelist: {e}")
        return set()
    except csv.Error as e:
        logger.error(f"Failed to parse whitelist CSV: {e}")
        return set()


WHITELISTED_GAMES = asyncio.run(fetch_whitelist())


# def create_symlink(src, dst):
#     try:
#         win32file.CreateSymbolicLink(dst, src, 1 if os.path.isdir(src) else 0)
#         return True
#     except Exception as e:
#         logger.error(f"Failed to create symlink: {e}")
#         return False


# async def protect_warframe_dll(dll_path):
#     dll_path = Path(dll_path).resolve()
#     backup_dir = dll_path.parent / "DLSS_Updater_Backup"
#     backup_path = backup_dir / dll_path.name

#     async def _protect():
#         try:
#             logger.info(f"Attempting to protect Warframe DLL: {dll_path}")

#             # Create backup directory if it doesn't exist
#             backup_dir.mkdir(exist_ok=True)
#             logger.info(f"Backup directory created/verified: {backup_dir}")

#             # Backup original DLL if not already done
#             if not backup_path.exists():
#                 await asyncio.to_thread(shutil.copy2, dll_path, backup_path)
#                 logger.info(f"Backed up original DLL to: {backup_path}")

#             # Replace with our updated DLL (assuming it's in LATEST_DLL_PATHS)
#             latest_dll = LATEST_DLL_PATHS.get(dll_path.name.lower())
#             if latest_dll:
#                 await asyncio.to_thread(shutil.copy2, latest_dll, dll_path)
#                 logger.info(f"Replaced DLL with updated version: {dll_path}")

#             # Set file attributes
#             await asyncio.to_thread(
#                 win32file.SetFileAttributes,
#                 str(dll_path),
#                 win32con.FILE_ATTRIBUTE_READONLY | win32con.FILE_ATTRIBUTE_SYSTEM,
#             )
#             logger.info(f"Set DLL as read-only and system file: {dll_path}")

#             # Set up file watcher
#             event_handler = WarframeDLLHandler(str(dll_path), str(backup_path))
#             observer = Observer()
#             observer.schedule(event_handler, str(dll_path.parent), recursive=False)
#             observer.start()
#             logger.info(f"File watcher set up for: {dll_path}")

#             return True

#         except Exception as e:
#             logger.error(f"Failed to protect Warframe DLL: {e}")
#             return False

#     return await _protect()


async def is_whitelisted(game_path):
    logger.debug(f"Checking whitelist for: {game_path}")
    path_parts = game_path.lower().split(os.path.sep)

    for game in WHITELISTED_GAMES:
        game_words = game.lower().split()
        logger.debug(f"Checking against whitelisted game: {game}")

        if all(word in " ".join(path_parts) for word in game_words):
            logger.info(f"Whitelist match found: {game} in {game_path}")

            # # Special handling for Warframe
            # if "warframe" in game.lower():
            #     logger.info(f"Applying protection to Warframe DLSS DLL: {game_path}")
            #     protected = await protect_warframe_dll(game_path)
            #     if protected:
            #         logger.info(
            #             f"Successfully protected Warframe DLSS DLL: {game_path}"
            #         )
            #     else:
            #         logger.warning(f"Failed to protect Warframe DLSS DLL: {game_path}")
            #     return False  # Allow updating Warframe DLL

            return True  # Whitelist match for other games

    logger.debug(f"No whitelist match found for: {game_path}")
    return False
