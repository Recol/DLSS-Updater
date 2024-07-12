import os

WHITELISTED_GAMES = {
    "Warframe",
    "3DMark",
    # Add more games as needed
}

def is_whitelisted(game_path):
    # Extract the game name from the path
    path_parts = game_path.split(os.path.sep)
    for part in path_parts:
        if part in WHITELISTED_GAMES:
            return True
    return False