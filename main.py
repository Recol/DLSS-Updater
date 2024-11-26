import sys
import os
import time
from pathlib import Path
import ctypes
import asyncio
from asyncslot import asyncSlot, AsyncSlotRunner

from PyQt6.QtCore import Qt, QUrl, QThreadPool
from PyQt6.QtGui import QDesktopServices

from dlss_updater.config import config_manager, LauncherPathName
from dlss_updater.logger import setup_logger, add_qt_handler
from dlss_updater.lib.thread_manager import Worker
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTextBrowser, QWidget, QVBoxLayout, QSplitter, QPushButton,
    QFileDialog, QHBoxLayout, QLabel, QMenu
)

logger = setup_logger()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DLSS-Updater")
        self.setGeometry(100, 100, 600, 350)
        self.thread_pool = QThreadPool()
        # Main container
        main_container = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Header section with welcome, donate and contact
        header_layout = QHBoxLayout()
        welcome_label = QLabel("Welcome to the GUI :) -Deco")
        welcome_label.setStyleSheet("color: white; font-size: 16px;")

        donate_button = QPushButton("☕ Support Development")
        donate_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://buymeacoffee.com/decouk")))

        report_bug_button = QPushButton("🐛 Report a Bug")
        report_bug_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/Recol/DLSS-Updater/issues")))
        
        contact_button = QPushButton("📞 Contact")
        contact_menu = QMenu()
        twitter_action = contact_menu.addAction("Twitter")
        discord_action = contact_menu.addAction("Discord")
        twitter_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl("https://x.com/iDeco_UK")))
        discord_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl("https://discord.com/users/162568099839606784")))
        contact_button.setMenu(contact_menu)

        header_layout.addWidget(welcome_label)
        header_layout.addStretch()
        header_layout.addWidget(donate_button)
        header_layout.addWidget(report_bug_button)
        header_layout.addWidget(contact_button)

        # Custom folders info
        info_label = QLabel("Note: For custom game folders, use any launcher button (e.g. Battle.net) and select your folder location.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: white; background-color: #3C3C3C; padding: 10px; border-radius: 4px;")

        # Original logger splitter setup
        logger_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.can_continue = False
        self.button_list = []
        self.path_list = []
        
        # We hold the enum values for each storefront in a list with the same sorting order as the buttons.
        self.button_enum_list = [
            LauncherPathName.STEAM,
            LauncherPathName.EA,
            LauncherPathName.UBISOFT,
            LauncherPathName.EPIC,
            LauncherPathName.GOG,
            LauncherPathName.BATTLENET
        ]
        self.button_enum_dict = {}

        # Create the text browsers for each storefront
        self.steam_text_browser = QPushButton('Click to select Steam game locations.', self)
        self.ea_text_browser = QPushButton('Click to select EA game locations.', self)
        self.ubisoft_text_browser = QPushButton('Click to select Ubisoft game locations.', self)
        self.epic_text_browser = QPushButton('Click to select Epic game locations.', self)
        self.gog_text_browser = QPushButton('Click to select GOG game locations.', self)
        self.battlenet_text_browser = QPushButton('Click to select Battle.net game locations.', self)

        # Button to start update process
        self.start_update_button = QPushButton('Click to start updating.', self)
        self.start_update_button.clicked.connect(self.call_threaded_update)

        # We give a name to each button in order to distinguish them in the dictionary
        self.steam_text_browser.setObjectName('Steam')
        self.ea_text_browser.setObjectName('EA')
        self.ubisoft_text_browser.setObjectName('UBISOFT')
        self.epic_text_browser.setObjectName('EPIC')
        self.gog_text_browser.setObjectName('GOG')
        self.battlenet_text_browser.setObjectName('BATTLENET')

        # We add the buttons to an ordered list to ensure ease of accessibility for future updates and function calls.
        self.button_list.append(self.steam_text_browser)
        self.button_list.append(self.ea_text_browser)
        self.button_list.append(self.ubisoft_text_browser)
        self.button_list.append(self.epic_text_browser)
        self.button_list.append(self.gog_text_browser)
        self.button_list.append(self.battlenet_text_browser)

        # Add the buttons as key to link them with the corresponding enum
        self.button_enum_dict.update({
            self.steam_text_browser.objectName(): LauncherPathName.STEAM,
            self.ea_text_browser.objectName(): LauncherPathName.EA,
            self.ubisoft_text_browser.objectName(): LauncherPathName.UBISOFT,
            self.epic_text_browser.objectName(): LauncherPathName.EPIC,
            self.gog_text_browser.objectName(): LauncherPathName.GOG,
            self.battlenet_text_browser.objectName(): LauncherPathName.BATTLENET
        })

        # Layouts for the browse buttons
        browse_buttons_layout = QVBoxLayout()
        browse_buttons_layout.addWidget(self.steam_text_browser)
        browse_buttons_layout.addWidget(self.ea_text_browser)
        browse_buttons_layout.addWidget(self.ubisoft_text_browser)
        browse_buttons_layout.addWidget(self.epic_text_browser)
        browse_buttons_layout.addWidget(self.gog_text_browser)
        browse_buttons_layout.addWidget(self.battlenet_text_browser)
        browse_buttons_layout.addWidget(self.start_update_button)
        browse_buttons_container_widget = QWidget()
        browse_buttons_container_widget.setLayout(browse_buttons_layout)

        # Link buttons to browse functionality
        self.steam_text_browser.clicked.connect(self.browse_folder)
        self.ea_text_browser.clicked.connect(self.browse_folder)
        self.ubisoft_text_browser.clicked.connect(self.browse_folder)
        self.epic_text_browser.clicked.connect(self.browse_folder)
        self.gog_text_browser.clicked.connect(self.browse_folder)
        self.battlenet_text_browser.clicked.connect(self.browse_folder)

        # Create QTextBrowser widget
        self.text_browser = QTextBrowser(self)

        # Set up layout
        logger_splitter.addWidget(browse_buttons_container_widget)
        logger_splitter.addWidget(self.text_browser)
        
        # Add new layouts to main layout
        main_layout.addLayout(header_layout)
        main_layout.addWidget(info_label)
        main_layout.addWidget(logger_splitter)
        main_container.setLayout(main_layout)
        self.setCentralWidget(main_container)

        # Set up logging
        self.logger = logger
        add_qt_handler(self.logger, self.text_browser)
        self.apply_dark_theme()

    def call_threaded_update(self):
        worker = Worker(print_something)
        worker.signals.error.connect(self.logger.error)
        worker.signals.result.connect(self.logger.info)
        worker.signals.finished.connect(lambda: self.logger.debug("Thread finished"))
        self.thread_pool.start(worker)

    def get_current_settings(self):
        steam_path = config_manager.check_path_value(LauncherPathName.STEAM)
        ea_path = config_manager.check_path_value(LauncherPathName.EA)
        ubisoft_path = config_manager.check_path_value(LauncherPathName.UBISOFT)
        epic_path = config_manager.check_path_value(LauncherPathName.EPIC)
        gog_path = config_manager.check_path_value(LauncherPathName.GOG)
        battlenet_path = config_manager.check_path_value(LauncherPathName.BATTLENET)

        self.path_list = [steam_path, ea_path, ubisoft_path, epic_path, gog_path, battlenet_path]

        for i, button in enumerate(self.button_list):
            if self.path_list[i]:
                button.setText(self.path_list[i])

    def browse_folder(self):
        """Open a dialog to select a directory."""
        directory = QFileDialog.getExistingDirectory(self, "Select Folder")
        if directory:
            directory = directory.replace('/', '\\')
            self.sender().setText(directory)
            config_manager.update_launcher_path(self.button_enum_dict.get(self.sender().objectName()), directory)

    def apply_dark_theme(self):
        """Apply a dark theme using stylesheets."""
        dark_stylesheet = """
            QMainWindow {
                background-color: #2E2E2E; /* Dark background */
                color: #FFFFFF; /* White text */
            }
            QPushButton {
                background-color: #4D4D4D; /* Button background */
                color: #FFFFFF; /* Button text color */
                border: 1px solid #7F7F7F; /* Button border */
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #5A5A5A; /* Button hover effect */
            }
            QTextBrowser {
                background-color: #3C3C3C; /* Text browser background */
                color: #FFFFFF; /* Text color */
                border: 1px solid #7F7F7F; /* Text browser border */
            }
            QMenu {
                background-color: #3C3C3C;
                color: #FFFFFF;
                border: 1px solid #7F7F7F;
            }
            QMenu::item:selected {
                background-color: #5A5A5A;
            }
        """
        self.setStyleSheet(dark_stylesheet)


def check_update_completion():
    update_log_path = os.path.join(os.path.dirname(sys.executable), "update_log.txt")
    if os.path.exists(update_log_path):
        with open(update_log_path, "r") as f:
            logger.info(f"Update completed: {f.read()}")
        os.remove(update_log_path)


def check_update_error():
    error_log_path = os.path.join(
        os.path.dirname(sys.executable), "update_error_log.txt"
    )
    if os.path.exists(error_log_path):
        with open(error_log_path, "r") as f:
            logger.error(f"Update error occurred: {f.read()}")
        os.remove(error_log_path)


try:
    from dlss_updater import (
        update_dll,
        is_whitelisted,
        __version__,
        LATEST_DLL_PATHS,
        DLL_TYPE_MAP,
        # protect_warframe_dll,
    )
    from dlss_updater.scanner import find_all_dlss_dlls
    from dlss_updater.auto_updater import auto_update
except ImportError as e:
    logger.error(f"Error importing dlss_updater modules: {e}")
    logger.error("Current sys.path:")
    for path in sys.path:
        logger.error(path)
    logger.error("\nCurrent directory contents:")
    for item in os.listdir():
        logger.error(item)
    logger.error("\ndlss_updater directory contents:")
    try:
        for item in os.listdir("dlss_updater"):
            logger.error(item)
    except FileNotFoundError:
        logger.error("dlss_updater directory not found")
    sys.exit(1)

# Add the directory containing the executable to sys.path
if getattr(sys, "frozen", False):
    application_path = os.path.dirname(sys.executable)
    sys.path.insert(0, application_path)


def check_dependencies():
    try:
        from importlib.metadata import distributions

        required = {"pefile", "psutil"}
        installed = set()
        for dist in distributions():
            name = dist.metadata.get("Name")
            if name:
                installed.add(name.lower())
        missing = required - installed
        if missing:
            logger.info(f"Missing dependencies: {', '.join(missing)}")
            return False
        return True
    except ImportError:
        logger.error("Unable to check dependencies. Proceeding anyway.")
        return True


def run_as_admin():
    script = Path(sys.argv[0]).resolve()
    params = " ".join([str(script)] + sys.argv[1:])
    logger.info("Re-running script with admin privileges...")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def display_release_notes():
    release_notes_file = Path(__file__).parent / "release_notes.txt"
    logger.info(f"Looking for release notes at: {release_notes_file}")
    if release_notes_file.exists():
        with open(release_notes_file, "r") as file:
            logger.info("\nRelease Notes:")
            logger.info(file.read())
    else:
        logger.info("\nRelease Notes file not found.")


def extract_game_name(dll_path, launcher_name):
    parts = Path(dll_path).parts
    try:
        if "steamapps" in parts:
            return parts[parts.index("steamapps") + 2]
        elif "EA Games" in parts:
            return parts[parts.index("EA Games") + 1]
        elif "Ubisoft Game Launcher" in parts:
            return parts[parts.index("games") + 1]
        elif "Epic Games" in parts:
            return parts[parts.index("Epic Games") + 2]
        elif "GOG Galaxy" in parts:
            return parts[parts.index("Games") + 1]
        elif "Battle.net" in parts:
            return parts[parts.index("Battle.net") + 1]
        else:
            # If we can't determine the game name, use the parent directory name
            return parts[-2]
    except (ValueError, IndexError) as e:
        logger.error(
            f"Error extracting game name for {dll_path} in {launcher_name}: {e}"
        )
        return "Unknown Game"


# async def verify_warframe_protection(dll_path):
#     dll_path = Path(dll_path).resolve()
#     backup_path = (dll_path.parent / "DLSS_Updater_Backup" / dll_path.name).resolve()

#     if not os.path.exists(dll_path):
#         logger.warning(f"Warframe DLL not found: {dll_path}")
#         return False

#     if not os.path.exists(backup_path):
#         logger.warning(f"Backup DLL not found: {backup_path}")
#         return False

#     # Check if the DLL is read-only and system
#     attributes = ctypes.windll.kernel32.GetFileAttributesW(str(dll_path))
#     is_protected = (attributes & 1) != 0 and (attributes & 4) != 0

#     if is_protected:
#         logger.info(f"Warframe DLL protection verified: {dll_path}")
#         return True
#     else:
#         logger.warning(f"Warframe DLL is not properly protected: {dll_path}")
#         return False


async def update_dlss_versions():
    logger.info(f"DLSS Updater version {__version__}")
    logger.info("Starting DLL search...")

    # observer = Observer()
    # observer.start()
    try:
        logger.info("Checking for updates...")
        if auto_update is None:
            logger.info("No updates were found.")
        else:
            try:
                update_available = await asyncio.to_thread(auto_update)
                if update_available:
                    logger.info(
                        "The application will now close for the update. If the update does NOT automatically restart, please manually reboot it from the /update/ folder."
                    )
                    return  # Exit here to allow the update process to take over
            except Exception as e:
                logger.error(f"Error during update check: {e}")
                import traceback

                traceback.logger.info_exc()

        display_release_notes()

        logger.info("Searching for DLSS DLLs...")
        all_dll_paths = await find_all_dlss_dlls()
        logger.info("DLL search completed.")

        updated_games = []
        skipped_games = []
        successful_backups = []
        processed_dlls = set()  # Keep track of processed DLLs

        if any(all_dll_paths.values()):
            logger.info("\nFound DLLs in the following launchers:")
            update_tasks = []
            dll_paths_to_update = []
            for launcher, dll_paths in all_dll_paths.items():
                if dll_paths:
                    logger.info(f"{launcher}:")
                    for dll_path in dll_paths:
                        dll_path = (
                            Path(dll_path) if isinstance(dll_path, str) else dll_path
                        )
                        if str(dll_path) not in processed_dlls:
                            dll_type = DLL_TYPE_MAP.get(
                                dll_path.name.lower(), "Unknown DLL type"
                            )
                            logger.info(f" - {dll_type}: {dll_path}")

                            game_name = extract_game_name(str(dll_path), launcher)
                            if "warframe" in game_name.lower():
                                continue
                                # logger.info(
                                #     f"Applying Warframe-specific protection to: {dll_path}"
                                # )
                                # protected = await protect_warframe_dll(str(dll_path))
                                # if protected:
                                #     logger.info(f"Warframe DLL protected: {dll_path}")
                                #     verified = await verify_warframe_protection(
                                #         str(dll_path)
                                #     )
                                #     if verified:
                                #         logger.info(
                                #             f"Warframe DLL protection verified: {dll_path}"
                                #         )
                                #     else:
                                #         logger.warning(
                                #             f"Warframe DLL protection could not be verified: {dll_path}"
                                #         )
                                # else:
                                #     logger.warning(
                                #         f"Failed to protect Warframe DLL: {dll_path}"
                                #     )
                            elif await is_whitelisted(str(dll_path)):
                                skipped_games.append(
                                    (dll_path, launcher, "Whitelisted", dll_type)
                                )
                            else:
                                dll_name = dll_path.name.lower()
                                if dll_name in LATEST_DLL_PATHS:
                                    latest_dll_path = LATEST_DLL_PATHS[dll_name]
                                    update_tasks.append(
                                        update_dll(str(dll_path), latest_dll_path)
                                    )
                                    dll_paths_to_update.append(
                                        (str(dll_path), launcher, dll_type)
                                    )
                                else:
                                    skipped_games.append(
                                        (
                                            str(dll_path),
                                            launcher,
                                            "No update available",
                                            dll_type,
                                        )
                                    )
                            processed_dlls.add(str(dll_path))

            if update_tasks:
                logger.info("\nUpdating DLLs...")
                update_results = await asyncio.gather(*update_tasks)
                for (dll_path, launcher, dll_type), result in zip(
                        dll_paths_to_update, update_results
                ):
                    if isinstance(result, tuple) and len(result) >= 2:
                        update_success, backup_path = result[:2]
                        if update_success:
                            logger.info(
                                f"Successfully updated {dll_type} at {dll_path}."
                            )
                            updated_games.append((dll_path, launcher, dll_type))
                            if backup_path:
                                successful_backups.append((dll_path, backup_path))
                        else:
                            logger.info(f"Failed to update {dll_type} at {dll_path}.")
                            skipped_games.append(
                                (dll_path, launcher, "Update failed", dll_type)
                            )
                    else:
                        logger.error(
                            f"Unexpected result format for {dll_path}: {result}"
                        )
                        skipped_games.append(
                            (dll_path, launcher, "Unexpected result", dll_type)
                        )
                logger.info("DLL updates completed.")
            elif skipped_games:
                logger.info("All found DLLs were skipped.")
            else:
                logger.info("No DLLs were eligible for update.")
        else:
            logger.info("No DLLs found.")

        # Display summary
        logger.info("\nSummary:")
        logger.info("")
        if updated_games or skipped_games or successful_backups:
            if updated_games:
                logger.info("Games updated successfully:")
                for dll_path, launcher, dll_type in updated_games:
                    game_name = extract_game_name(dll_path, launcher)
                    logger.info(f" - {game_name} - {launcher} ({dll_type})")
            else:
                logger.info("No games were updated.")

            if successful_backups:
                logger.info("\nSuccessful backups:")
                for dll_path, backup_path in successful_backups:
                    game_name = extract_game_name(dll_path, "Unknown")
                    dll_type = DLL_TYPE_MAP.get(
                        Path(dll_path).name.lower(), "Unknown DLL type"
                    )
                    logger.info(f" - {game_name}: {backup_path} ({dll_type})")
            else:
                logger.info("\nNo backups were created.")

            if skipped_games:
                logger.info("\nGames skipped:")
                for dll_path, launcher, reason, dll_type in skipped_games:
                    game_name = extract_game_name(dll_path, launcher)
                    logger.info(
                        f" - {game_name} - {launcher} ({dll_type}) (Reason: {reason})"
                    )
        else:
            logger.info("No DLLs were found or processed.")

    # except KeyboardInterrupt:
    #     observer.stop()
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        import traceback

        logger.error(traceback.format_exc())

    finally:
        # observer.join()
        input("\nPress Enter to exit...")
        logger.info("Application exiting.")

def print_something():
    logger.info("test thingy")
    time.sleep(5)
    bad_function_call()

def main():
    if gui_mode:
        log_file = os.path.join(os.path.dirname(sys.executable), "dlss_updater.log")
        sys.stdout = sys.stderr = open(log_file, "w")
    # Run the application with AsyncSlotRunner
    main_ui = QApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    main_window.get_current_settings()
    sys.exit(main_ui.exec())


if __name__ == "__main__":
    check_update_completion()
    check_update_error()
    gui_mode = "--gui" in sys.argv
    logger.debug("Python executable: %s", sys.executable)
    logger.debug("sys.path: %s", sys.path)
    logger.info("DLSS Updater started")
    if not check_dependencies():
        sys.exit(1)
    if not is_admin():
        run_as_admin()
    else:
        main()