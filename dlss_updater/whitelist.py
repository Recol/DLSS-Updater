import os
import csv
from io import StringIO
import asyncio
from urllib.request import urlopen
from urllib.error import URLError
from dlss_updater.logger import setup_logger
from dlss_updater.config import config_manager

logger = setup_logger()

WHITELIST_URL = (
    "https://raw.githubusercontent.com/Recol/DLSS-Updater-Whitelist/main/whitelist.csv"
)


def fetch_whitelist():
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


WHITELISTED_GAMES = fetch_whitelist()


async def is_whitelisted(game_path):
    """
    Check if a game path matches any whitelisted games.
    Uses launcher pattern detection to find the actual game name.
    """
    logger.debug(f"Checking game against whitelist: {game_path}")

    # Extract path components
    path_parts = [p for p in game_path.split(os.path.sep) if p]

    # Skip if the path is too short
    if len(path_parts) < 3:
        return False

    # Look for known launcher patterns to identify the game directory
    game_dir = None

    # Epic Games pattern: <drive>:\Epic Games\<GameName>\...
    if "Epic Games" in path_parts:
        epic_index = path_parts.index("Epic Games")
        if epic_index + 1 < len(path_parts):
            game_dir = path_parts[epic_index + 1]

    # Steam pattern: <drive>:\<SteamLibrary>\steamapps\common\<GameName>\...
    elif "steamapps" in path_parts and "common" in path_parts:
        common_index = path_parts.index("common")
        if common_index + 1 < len(path_parts):
            game_dir = path_parts[common_index + 1]

    # EA Games pattern: <drive>:\EA Games\<GameName>\...
    elif "EA Games" in path_parts:
        ea_index = path_parts.index("EA Games")
        if ea_index + 1 < len(path_parts):
            game_dir = path_parts[ea_index + 1]

    # GOG pattern: <drive>:\GOG Games\<GameName>\... or <drive>:\GOG Galaxy\Games\<GameName>\...
    elif "GOG Games" in path_parts:
        gog_index = path_parts.index("GOG Games")
        if gog_index + 1 < len(path_parts):
            game_dir = path_parts[gog_index + 1]
    elif "GOG Galaxy" in path_parts and "Games" in path_parts:
        games_index = path_parts.index("Games")
        if games_index + 1 < len(path_parts):
            game_dir = path_parts[games_index + 1]

    # Ubisoft pattern: <drive>:\Ubisoft\Ubisoft Game Launcher\games\<GameName>\...
    elif "Ubisoft Game Launcher" in path_parts and "games" in path_parts:
        games_index = path_parts.index("games")
        if games_index + 1 < len(path_parts):
            game_dir = path_parts[games_index + 1]

    # Battle.net pattern: <drive>:\Battle.net\<GameName>\...
    elif "Battle.net" in path_parts:
        battlenet_index = path_parts.index("Battle.net")
        if battlenet_index + 1 < len(path_parts):
            game_dir = path_parts[battlenet_index + 1]

    # Xbox pattern: <drive>:\Xbox\<GameName>\...
    elif "Xbox" in path_parts:
        xbox_index = path_parts.index("Xbox")
        if xbox_index + 1 < len(path_parts):
            game_dir = path_parts[xbox_index + 1]

    # Fallback: Just use the parent directory if we couldn't identify the game
    if not game_dir:
        logger.debug(
            "Could not identify game from path patterns, using parent directory"
        )
        game_dir = path_parts[-2]

    logger.debug(f"Identified game directory: {game_dir}")

    # Check for skip list first
    for game in WHITELISTED_GAMES:
        if config_manager.is_blacklist_skipped(game):
            if game.lower() == game_dir.lower():
                logger.info(
                    f"Game '{game_dir}' is in whitelist but also in skip list - allowing update"
                )
                return False

    # Now check against whitelist
    for game in WHITELISTED_GAMES:
        # Skip if in skip list (already handled)
        if config_manager.is_blacklist_skipped(game):
            continue

        # Simple direct name comparison
        if game.lower() == game_dir.lower():
            logger.info(f"Whitelist match found: {game_dir}")
            return True

    logger.debug(f"No whitelist match found for: {game_dir}")
    return False


def get_all_blacklisted_games():
    """Return the list of all blacklisted games for UI display"""
    return list(WHITELISTED_GAMES)

async def check_whitelist_batch(game_paths):
    """Check multiple game paths against whitelist in parallel"""
    tasks = []
    for path in game_paths:
        task = asyncio.create_task(is_whitelisted(path))
        tasks.append((path, task))
    
    results = {}
    for path, task in tasks:
        try:
            results[path] = await task
        except Exception as e:
            logger.error(f"Error checking whitelist for {path}: {e}")
            results[path] = False
    
    return results