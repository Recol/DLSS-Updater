import os

WHITELISTED_GAMES = {
    "Warframe",
    "3DMark",
    "Fortnite",
    "The First Descendant",
    "EVIL DEAD The Game",
    "Escape from Tarkov",
    "Escape from Tarkov Arena",
    "Back 4 Blood",
    "Squad",
    "Squad 44",
    "Chivalry 2",
    "Need For Speed Unbound",
    "StarshipTroopersExtermination",
    # Add more games as needed
}


def is_whitelisted(game_path):
    # Convert the path to lowercase for case-insensitive matching
    lower_path = game_path.lower()

    # Check for each whitelisted game
    for game in WHITELISTED_GAMES:
        # Convert the game name to lowercase and replace spaces with underscores
        # This allows for more flexible matching
        game_check = game.lower().replace(" ", "_")
        if game_check in lower_path:
            return True

    return False


# Experimental global block, not use though
# def has_easy_anti_cheat(dll_path):
#     eac_folders = ["EasyAntiCheat", "EAC"]
#     dll_dir = Path(dll_path).parent

#     logger.info(f"Checking for EAC in: {dll_path}")  # Debug logger.info

#     # Check up to 3 levels up from the DLL location
#     for _ in range(3):
#         for root, dirs, _ in os.walk(dll_dir):
#             if any(eac_folder in dirs for eac_folder in eac_folders):
#                 return True
#         dll_dir = dll_dir.parent
#         if dll_dir == dll_dir.parent:  # Reached the root directory
#             break

#     return False
