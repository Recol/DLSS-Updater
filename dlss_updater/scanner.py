import os
from pathlib import Path
from .whitelist import is_whitelisted
import asyncio


def get_steam_install_path():
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"
        )
        value, _ = winreg.QueryValueEx(key, "InstallPath")
        return value
    except (FileNotFoundError, ImportError):
        return None


def get_steam_libraries(steam_path):
    library_folders_path = Path(steam_path) / "steamapps" / "libraryfolders.vdf"
    if not library_folders_path.exists():
        return [Path(steam_path) / "steamapps" / "common"]

    libraries = []
    with library_folders_path.open("r") as file:
        lines = file.readlines()
        for line in lines:
            if "path" in line:
                path = line.split('"')[3]
                libraries.append(Path(path) / "steamapps" / "common")
    return libraries


async def find_nvngx_dlss_dll(library_paths, launcher_name):
    dll_paths = []
    for library_path in library_paths:
        for root, _, files in os.walk(library_path):
            if "nvngx_dlss.dll" in files:
                dll_path = Path(root) / "nvngx_dlss.dll"
                if not is_whitelisted(str(dll_path)):
                    print(f"Found DLSS DLL in {launcher_name}: {dll_path}")
                    dll_paths.append(dll_path)
                else:
                    print(f"Skipped whitelisted game in {launcher_name}: {dll_path}")
            await asyncio.sleep(0)  # Yield control to allow other tasks to run
    return dll_paths


def get_user_input(prompt):
    user_input = input(prompt).strip()
    return None if user_input.lower() in ["n/a", ""] else user_input


async def get_ea_games():
    ea_path = get_user_input(
        "Please enter the path for EA games or press Enter to skip: "
    )
    if ea_path is None:
        return []
    ea_games_path = Path(ea_path)
    if not ea_games_path.exists():
        print("Invalid path for EA games.")
        return []
    return [ea_games_path]


def get_ubisoft_install_path():
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher"
        )
        value, _ = winreg.QueryValueEx(key, "InstallDir")
        return value
    except (FileNotFoundError, ImportError):
        return None


async def get_ubisoft_games(ubisoft_path):
    ubisoft_games_path = Path(ubisoft_path) / "games"
    if not ubisoft_games_path.exists():
        return []
    return [ubisoft_games_path]


async def get_epic_games():
    epic_path = get_user_input(
        "Please enter the path for Epic Games or press Enter to skip: "
    )
    if epic_path is None:
        return []
    epic_games_path = Path(epic_path)
    if not epic_games_path.exists():
        print("Invalid path for Epic Games.")
        return []
    return [epic_games_path]


async def get_gog_games():
    gog_path = get_user_input(
        "Please enter the path for GOG games or press Enter to skip: "
    )
    if gog_path is None:
        return []
    gog_games_path = Path(gog_path)
    if not gog_games_path.exists():
        print("Invalid path for GOG games.")
        return []
    return [gog_games_path]


async def get_battlenet_games():
    battlenet_path = get_user_input(
        "Please enter the path for Battle.net games or press Enter to skip: "
    )
    if battlenet_path is None:
        return []
    battlenet_games_path = Path(battlenet_path)
    if not battlenet_games_path.exists():
        print("Invalid path for Battle.net games.")
        return []
    return [battlenet_games_path]


async def find_all_dlss_dlls():
    all_dll_paths = {
        "Steam": [],
        "EA Launcher": [],
        "Ubisoft Launcher": [],
        "Epic Games Launcher": [],
        "GOG Launcher": [],
        "Battle.net Launcher": [],
    }

    steam_path = get_steam_install_path()
    if steam_path:
        steam_libraries = get_steam_libraries(steam_path)
        all_dll_paths["Steam"] = await find_nvngx_dlss_dll(steam_libraries, "Steam")

    ea_games = await get_ea_games()
    if ea_games:
        all_dll_paths["EA Launcher"] = await find_nvngx_dlss_dll(
            ea_games, "EA Launcher"
        )

    ubisoft_path = get_ubisoft_install_path()
    if ubisoft_path:
        ubisoft_games = await get_ubisoft_games(ubisoft_path)
        all_dll_paths["Ubisoft Launcher"] = await find_nvngx_dlss_dll(
            ubisoft_games, "Ubisoft Launcher"
        )

    epic_games = await get_epic_games()
    if epic_games:
        all_dll_paths["Epic Games Launcher"] = await find_nvngx_dlss_dll(
            epic_games, "Epic Games Launcher"
        )

    gog_games = await get_gog_games()
    if gog_games:
        all_dll_paths["GOG Launcher"] = await find_nvngx_dlss_dll(
            gog_games, "GOG Launcher"
        )

    battlenet_games = await get_battlenet_games()
    if battlenet_games:
        all_dll_paths["Battle.net Launcher"] = await find_nvngx_dlss_dll(
            battlenet_games, "Battle.net Launcher"
        )

    # Remove duplicates while preserving the first occurrence in its original category
    unique_dlls = set()
    for launcher in all_dll_paths:
        unique_launcher_dlls = []
        for dll in all_dll_paths[launcher]:
            if str(dll) not in unique_dlls:
                unique_dlls.add(str(dll))
                unique_launcher_dlls.append(dll)
        all_dll_paths[launcher] = unique_launcher_dlls

    # Categorize any uncategorized DLLs
    uncategorized = []
    for launcher, dlls in all_dll_paths.items():
        for dll in dlls:
            if (
                sum(
                    str(dll) in [str(d) for d in path_list]
                    for path_list in all_dll_paths.values()
                )
                > 1
            ):
                uncategorized.append(dll)

    if uncategorized:
        print("Found DLLs in multiple categories:")
        for dll in uncategorized:
            print(f" - {dll}")
            # Remove from all categories except the first one it appears in
            first_appearance = next(
                launcher
                for launcher, dlls in all_dll_paths.items()
                if str(dll) in [str(d) for d in dlls]
            )
            for launcher, dlls in all_dll_paths.items():
                if launcher != first_appearance:
                    all_dll_paths[launcher] = [d for d in dlls if str(d) != str(dll)]

    return all_dll_paths
