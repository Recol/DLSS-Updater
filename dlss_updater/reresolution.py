"""
Re-resolution Engine for Steam App ID Updates

When a user adds a Steam API key after already scanning games,
this module re-resolves app IDs for games that used FTS5 or had
no app ID, potentially fixing many wrong/missing game images.

The re-resolution process:
1. Fetches owned games via Steam API (single call)
2. For each game needing re-resolution:
   - Try API lookup (instant, dict lookup)
   - Try store search (rate-limited, 1.5s delay)
3. Batch updates DB, clears old images, resets fetch_failed flags
"""

import asyncio
from typing import Any

from dlss_updater.logger import setup_logger

logger = setup_logger()


async def reresolution_needed() -> int:
    """Count how many games would benefit from re-resolution.

    Returns the number of games with FTS5/NULL resolution source
    or no steam_app_id that could potentially be improved.
    """
    from dlss_updater.database import db_manager

    games = await db_manager.get_games_needing_reresolution()
    return len(games)


async def run_reresolution(
    progress_callback: Any | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Re-resolve app IDs for games that used FTS5 or had no app ID.

    This is the main entry point for re-resolution. Called when a user
    adds their Steam API key after already having scanned games.

    Steps:
    1. Fetch owned games from Steam API (single call)
    2. For each game needing re-resolution:
       a. Try Steam API lookup (instant dict lookup)
       b. Try Store Search (rate-limited, ~1.5s delay)
       c. Skip if neither works (keep existing resolution)
    3. For games where app_id changed:
       a. Delete old cached image files
       b. Update game record with new app_id + source
       c. Clear fetch_failed flags for new app_ids

    Args:
        progress_callback: Optional async callable(current, total, message)
            for UI progress updates.

    Returns:
        Dict with counts: resolved, unchanged, failed, total
    """
    from dlss_updater.database import db_manager
    from dlss_updater.config import config_manager
    from dlss_updater.steam_integration import (
        steam_integration,
        find_app_id_via_api,
        find_app_id_via_store_search,
    )

    api_key = config_manager.get_steam_api_key()
    steam_id = config_manager.get_steam_id()

    empty_result = {"resolved": 0, "unchanged": 0, "failed": 0, "total": 0}

    if not api_key or not steam_id:
        logger.warning("Re-resolution skipped: no Steam API credentials configured")
        return empty_result

    # Step 1: Get games needing re-resolution (or all games if forced)
    if force:
        games = await db_manager.get_all_games_for_reresolution()
    else:
        games = await db_manager.get_games_needing_reresolution()
    total = len(games)

    if total == 0:
        logger.info("Re-resolution: no games need re-resolution")
        return empty_result

    if progress_callback:
        await _safe_callback(progress_callback, 0, total, f"Re-resolving {total} games...")

    # Step 2: Pre-fetch owned games from Steam API
    try:
        owned_games = await steam_integration.get_owned_games(api_key, steam_id)
    except Exception as e:
        logger.error(f"Failed to fetch owned games for re-resolution: {e}")
        return {"resolved": 0, "unchanged": 0, "failed": total, "total": total}

    if not owned_games:
        logger.warning(
            "GetOwnedGames returned 0 games. "
            "User's Steam profile game details may be private."
        )

    # Step 3: Re-resolve each game
    updates: list[dict] = []  # {game_id, steam_app_id, resolution_source}
    old_app_ids_to_clear: list[int] = []
    new_app_ids: list[int] = []

    resolved = 0
    unchanged = 0

    for i, game in enumerate(games):
        new_app_id = None
        new_source = None

        # Tier 2: Steam API lookup (instant, dict lookup)
        new_app_id = await find_app_id_via_api(game.name)
        if new_app_id:
            new_source = "api"

        # Tier 3: Store search (rate-limited)
        if new_app_id is None:
            new_app_id = await find_app_id_via_store_search(game.name)
            if new_app_id:
                new_source = "store_search"
            # Rate limit every store search request (hit or miss)
            await asyncio.sleep(1.5)

        if new_app_id and new_app_id != game.steam_app_id:
            # App ID changed or newly resolved
            updates.append(
                {
                    "game_id": game.id,
                    "steam_app_id": new_app_id,
                    "resolution_source": new_source,
                }
            )
            if game.steam_app_id:
                old_app_ids_to_clear.append(game.steam_app_id)
            new_app_ids.append(new_app_id)
            resolved += 1
            logger.debug(
                f"Re-resolved '{game.name}': {game.steam_app_id} -> {new_app_id} ({new_source})"
            )
        elif new_app_id and new_app_id == game.steam_app_id and new_source:
            # Same app ID but better source - update source only
            updates.append(
                {
                    "game_id": game.id,
                    "steam_app_id": new_app_id,
                    "resolution_source": new_source,
                }
            )
            unchanged += 1
        else:
            unchanged += 1

        # Progress update every 5 games
        if progress_callback and (i + 1) % 5 == 0:
            await _safe_callback(
                progress_callback, i + 1, total, f"Re-resolved {i + 1}/{total} games..."
            )

    # Step 4: Batch database updates
    if updates:
        await db_manager.batch_update_game_app_ids(updates)
        logger.info(f"Batch updated {len(updates)} game app IDs")

        # Clear old cached images for replaced app IDs
        if old_app_ids_to_clear:
            await db_manager.delete_cached_images_for_app_ids(old_app_ids_to_clear)
            logger.info(f"Cleared {len(old_app_ids_to_clear)} old cached images")

        # Clear fetch_failed flags for new app IDs (enable fresh fetches)
        if new_app_ids:
            await db_manager.clear_fetch_failed_for_app_ids(new_app_ids)
            logger.info(f"Cleared fetch_failed for {len(new_app_ids)} new app IDs")

    if progress_callback:
        await _safe_callback(
            progress_callback, total, total, f"Re-resolution complete: {resolved} improved"
        )

    result = {
        "resolved": resolved,
        "unchanged": unchanged,
        "failed": total - resolved - unchanged,
        "total": total,
    }
    logger.info(f"Re-resolution results: {result}")
    return result


async def _safe_callback(callback: Any, *args: Any) -> None:
    """Safely invoke a progress callback, handling both sync and async."""
    try:
        result = callback(*args)
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        logger.debug(f"Progress callback error: {e}")
