import os
import csv
from io import StringIO
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


def is_whitelisted(game_path):
    """Check if a game path matches any whitelisted games"""
    logger.debug(f"Checking game against whitelist: {game_path}")
    path_parts = game_path.lower().split(os.path.sep)
    game_name = (
        path_parts[-2] if len(path_parts) > 2 else "Unknown"
    )  # Usually parent folder is game name

    for game in WHITELISTED_GAMES:
        game_words = game.lower().split()
        if all(word in " ".join(path_parts).lower() for word in game_words):
            # Check if this game is in the user's skip list
            if config_manager.is_blacklist_skipped(game):
                logger.info(
                    f"Whitelist match found but user has chosen to ignore: {game_name} matches {game}"
                )
                return False

            logger.info(f"Whitelist match found: {game_name} matches {game}")
            return True

    logger.debug(f"No whitelist match found for: {game_name}")
    return False


def get_all_blacklisted_games():
    """Return the list of all blacklisted games for UI display"""
    return list(WHITELISTED_GAMES)
