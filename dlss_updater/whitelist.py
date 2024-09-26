import os
import csv
from io import StringIO
from urllib.request import urlopen
from urllib.error import URLError
from dlss_updater.logger import setup_logger

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
    logger.debug(f"Checking whitelist for: {game_path}")
    path_parts = game_path.lower().split(os.path.sep)

    for game in WHITELISTED_GAMES:
        game_words = game.lower().split()
        logger.debug(f"Checking against whitelisted game: {game}")

        if all(word in " ".join(path_parts) for word in game_words):
            logger.info(f"Whitelist match found: {game} in {game_path}")
            return True

    logger.debug(f"No whitelist match found for: {game_path}")
    return False
