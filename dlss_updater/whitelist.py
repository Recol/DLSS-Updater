import os
from pathlib import Path

WHITELISTED_GAMES = {
    "Warframe",
    "3DMark",
    "Fortnite",
    "The First Descendant",
    "EVIL DEAD The Game",
    "Escape From Tarkov",
    "Back 4 Blood",
    "Squad",
    "Squad 44",
    "Chivalry 2",
    # Add more games as needed
}


def is_whitelisted(game_path):
    # Extract the game name from the path
    path_parts = game_path.split(os.path.sep)
    for part in path_parts:
        if part in WHITELISTED_GAMES:
            return True
    return False

# Experimental global block, not use though
# def has_easy_anti_cheat(dll_path):
#     eac_folders = ["EasyAntiCheat", "EAC"]
#     dll_dir = Path(dll_path).parent

#     print(f"Checking for EAC in: {dll_path}")  # Debug print

#     # Check up to 3 levels up from the DLL location
#     for _ in range(3):
#         for root, dirs, _ in os.walk(dll_dir):
#             if any(eac_folder in dirs for eac_folder in eac_folders):
#                 return True
#         dll_dir = dll_dir.parent
#         if dll_dir == dll_dir.parent:  # Reached the root directory
#             break

#     return False
