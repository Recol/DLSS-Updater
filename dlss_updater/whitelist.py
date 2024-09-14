import os
from dlss_updater.logger import setup_logger

logger = setup_logger()

WHITELISTED_GAMES = {
    "Warframe",
    "3DMark",
    "Fortnite",
    "The First Descendant",
    "EVIL DEAD The Game",
    "Escape From Tarkov",
    "Escape from Tarkov Arena",
    "Planetside 2",
    "AFOP",
    "Back 4 Blood",
    "Squad",
    "Squad 44",
    "Chivalry 2",
    "Hunt Showdown",
    "Need For Speed Unbound",
    "StarshipTroopersExtermination",
    "Space Marine 2",
    # Add more games as needed
}


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
