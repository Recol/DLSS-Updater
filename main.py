import sys
import os
from pathlib import Path
import ctypes
from PyQt6.QtCore import Qt, QUrl, QSize
from PyQt6.QtGui import QDesktopServices, QIcon
from dlss_updater.lib.threading_lib import ThreadManager
from dlss_updater.config import config_manager, LauncherPathName
from dlss_updater.logger import setup_logger, add_qt_handler
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTextBrowser,
    QWidget,
    QVBoxLayout,
    QSplitter,
    QPushButton,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
)

logger = setup_logger()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.thread_manager = ThreadManager(self)
        self.button_enum_dict = {}
        self.setWindowTitle("DLSS-Updater")
        self.setGeometry(100, 100, 600, 350)

        # Main container
        main_container = QWidget()
        main_layout = QVBoxLayout()
        header_left = QHBoxLayout()

        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        # Header section with welcome, donate and contact
        header_layout = QHBoxLayout()
        welcome_label = QLabel("Welcome to the GUI :) -Deco")
        version_label = QLabel(f"v{__version__}")
        welcome_label.setStyleSheet("color: white; font-size: 16px;")
        version_label.setStyleSheet("color: #888888; font-size: 12px; margin-left: 8px;")
        header_left.addWidget(welcome_label)
        header_left.addWidget(version_label)
        header_left.addStretch()

        donate_button = QPushButton("☕ Support Development")
        donate_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://buymeacoffee.com/decouk"))
        )

        report_bug_button = QPushButton("🐛 Report a Bug")
        report_bug_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/Recol/DLSS-Updater/issues")
            )
        )

        contact_button = QPushButton("📞 Contact")
        contact_menu = QMenu()
        twitter_action = contact_menu.addAction("Twitter")
        discord_action = contact_menu.addAction("Discord")
        twitter_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://x.com/iDeco_UK"))
        )
        discord_action.triggered.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://discord.com/users/162568099839606784")
            )
        )
        contact_button.setMenu(contact_menu)

        header_layout.addWidget(welcome_label)
        header_layout.addStretch()
        header_layout.addWidget(donate_button)
        header_layout.addWidget(report_bug_button)
        header_layout.addWidget(contact_button)
        version_label = QLabel(f"v{__version__}")
        version_label.setStyleSheet("color: white; font-size: 12px;")
        header_layout.addWidget(version_label)

        # Custom folders info
        info_label = QLabel(
            "Note: For custom game folders, use any launcher button (e.g. Battle.net) and select your folder location."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(
            "color: white; background-color: #3C3C3C; padding: 10px; border-radius: 4px;"
        )

        # Original logger splitter setup
        logger_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.can_continue = False
        self.button_list = []
        self.path_list = []

        # Launcher buttons setup
        self.setup_launcher_buttons()

        # Layouts for the browse buttons
        browse_buttons_layout = QVBoxLayout()
        for button in self.button_list:
            browse_buttons_layout.addWidget(button)
        browse_buttons_layout.addWidget(self.start_update_button)
        browse_buttons_container_widget = QWidget()
        browse_buttons_container_widget.setLayout(browse_buttons_layout)

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

        # Connect the update button to the threaded update function
        self.start_update_button.clicked.connect(self.call_threaded_update)

    def create_styled_button(self, text, icon_path, tooltip=""):
        button = QPushButton(f"  {text}", self)
        
        # Load and process icon
        icon = QIcon(resource_path(os.path.join("icons", icon_path)))
        button.setIcon(icon)
        button.setIconSize(QSize(24, 24))  # Consistent icon size
        
        # Set fixed height for uniformity
        button.setMinimumHeight(40)
        
        if tooltip:
            button.setToolTip(tooltip)
        
        return button
    
    def setup_launcher_buttons(self):
        

        # We hold the enum values for each storefront
        self.steam_text_browser = self.create_styled_button(
        "Steam Games", "steam.jpg", "Select Steam game locations"
    )
        self.ea_text_browser = self.create_styled_button(
            "EA Games", "ea.jpg", "Select EA game locations"
        )
        self.ubisoft_text_browser = self.create_styled_button(
            "Ubisoft Games", "ubisoft.png", "Select Ubisoft game locations"
        )
        self.epic_text_browser = self.create_styled_button(
            "Epic Games", "epic.svg", "Select Epic game locations"
        )
        self.gog_text_browser = self.create_styled_button(
            "GOG Games", "gog.jpg", "Select GOG game locations"
        )
        self.battlenet_text_browser = self.create_styled_button(
            "Battle.net Games", "battlenet.png", "Select Battle.net game locations"
        )
        self.xbox_text_browser = self.create_styled_button(
            "Xbox Games", "xbox.png", "Select Xbox game locations"
        )

        # Update button with special styling
        self.start_update_button = self.create_styled_button(
            "Start Update", "update.png", "Start DLSS update process"
        )
        self.start_update_button.setStyleSheet("""
            QPushButton {
                background-color: #2D5A88;
                color: white;
                border: 1px solid #7F7F7F;
                border-radius: 4px;
                padding: 8px 16px;
                text-align: left;
                font-weight: bold;
                margin: 2px 0px;
            }
            QPushButton:hover {
                background-color: #366BA3;
                border-color: #999999;
            }
            QPushButton:pressed {
                background-color: #244B73;
            }
            QPushButton:disabled {
                background-color: #1D3D5A;
                color: #888888;
            }
        """)

        # Set object names
        self.steam_text_browser.setObjectName("Steam")
        self.ea_text_browser.setObjectName("EA")
        self.ubisoft_text_browser.setObjectName("UBISOFT")
        self.epic_text_browser.setObjectName("EPIC")
        self.gog_text_browser.setObjectName("GOG")
        self.battlenet_text_browser.setObjectName("BATTLENET")
        self.xbox_text_browser.setObjectName("XBOX")

        # Add buttons to list
        self.button_list = [
            self.steam_text_browser,
            self.ea_text_browser,
            self.ubisoft_text_browser,
            self.epic_text_browser,
            self.gog_text_browser,
            self.battlenet_text_browser,
            self.xbox_text_browser
        ]

        # Update button dictionary
        self.button_enum_dict.update({
            "Steam": LauncherPathName.STEAM,
            "EA": LauncherPathName.EA,
            "UBISOFT": LauncherPathName.UBISOFT,
            "EPIC": LauncherPathName.EPIC,
            "GOG": LauncherPathName.GOG,
            "BATTLENET": LauncherPathName.BATTLENET,
            "XBOX": LauncherPathName.XBOX,
        })

        # Apply consistent styling to all launcher buttons
        button_style = """
            QPushButton {
                background-color: #4D4D4D;
                color: white;
                border: 1px solid #7F7F7F;
                border-radius: 4px;
                padding: 8px 16px;
                text-align: left;
                margin: 2px 0px;
            }
            QPushButton:hover {
                background-color: #5A5A5A;
                border-color: #999999;
            }
            QPushButton:pressed {
                background-color: #444444;
            }
            QPushButton:disabled {
                background-color: #3D3D3D;
                color: #888888;
            }
        """
        
        for button in self.button_list:
            button.setStyleSheet(button_style)

    def call_threaded_update(self):
        """Start the update process in a separate thread"""
        self.start_update_button.setEnabled(False)
        self.logger.info("Starting update process in thread...")

        self.thread_manager.signals.finished.connect(self.handle_update_finished)
        self.thread_manager.signals.result.connect(self.handle_update_result)
        self.thread_manager.signals.error.connect(self.handle_update_error)

        self.thread_manager.assign_function(update_dlss_versions)
        self.thread_manager.run()

    def handle_update_error(self, error):
        """Handle errors from the update thread"""
        exctype, value, tb = error
        self.logger.error(f"Error: {exctype}")
        self.logger.error(f"Value: {value}")
        self.logger.error(f"Traceback: {tb}")
        self.start_update_button.setEnabled(True)

    def handle_update_result(self, result):
        """Handle results from the update thread"""
        try:
            if result:
                self.logger.info("Update process completed successfully")
            else:
                self.logger.error("Update process failed")
        except Exception as e:
            self.logger.error(f"Error handling update result: {e}")
        finally:
            self.start_update_button.setEnabled(True)

    def handle_update_finished(self):
        """Handle completion of the update thread"""
        try:
            self.logger.debug("Update thread finished")
            self.start_update_button.setEnabled(True)
            # Clean up worker reference
            self._current_worker = None
        except Exception as e:
            self.logger.error(f"Error in update finished handler: {e}")

    def closeEvent(self, event):
        """Handle application close event"""
        self.thread_pool.waitForDone()
        super().closeEvent(event)

    def get_current_settings(self):
        steam_path = config_manager.check_path_value(LauncherPathName.STEAM)
        ea_path = config_manager.check_path_value(LauncherPathName.EA)
        ubisoft_path = config_manager.check_path_value(LauncherPathName.UBISOFT)
        epic_path = config_manager.check_path_value(LauncherPathName.EPIC)
        gog_path = config_manager.check_path_value(LauncherPathName.GOG)
        battlenet_path = config_manager.check_path_value(LauncherPathName.BATTLENET)
        xbox_path = config_manager.check_path_value(LauncherPathName.XBOX)

        self.path_list = [
            steam_path,
            ea_path,
            ubisoft_path,
            epic_path,
            gog_path,
            battlenet_path,
            xbox_path,
        ]

        for i, button in enumerate(self.button_list):
            if self.path_list[i]:
                button.setText(self.path_list[i])

    def browse_folder(self):
        """Open a dialog to select a directory."""
        directory = QFileDialog.getExistingDirectory(self, "Select Folder")
        if directory:
            directory = directory.replace("/", "\\")
            self.sender().setText(directory)
            config_manager.update_launcher_path(
                self.button_enum_dict.get(self.sender().objectName()), directory
            )

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
        find_all_dlss_dlls,
        auto_update,
        resource_path,
    )
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


def update_dlss_versions():
  
    logger.info(f"DLSS Updater version {__version__}")
    logger.info("Starting DLL search...")
        # Wrap each major operation in its own try-except block
    try:
        logger.info("Checking for updates...")
        if auto_update is None:
            logger.info("No updates were found.")
        else:
            try:
                update_available = auto_update()
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

        try:
            all_dll_paths = find_all_dlss_dlls()
            logger.info("DLL search completed.")
        except Exception as e:
            logger.error(f"Error finding DLLs: {e}")
            return False

        updated_games = []
        skipped_games = []
        successful_backups = []
        processed_dlls = set()

        if any(all_dll_paths.values()):
            logger.info("\nFound DLLs in the following launchers:")
            # Process each launcher
            for launcher, dll_paths in all_dll_paths.items():
                if dll_paths:
                    logger.info(f"{launcher}:")
                    for dll_path in dll_paths:
                        try:
                            dll_path = (
                                Path(dll_path)
                                if isinstance(dll_path, str)
                                else dll_path
                            )
                            if str(dll_path) not in processed_dlls:
                                # Use process_single_dll function
                                result = process_single_dll(dll_path, launcher)
                                if result:
                                    success, backup_path, dll_type = result
                                    if success:
                                        logger.info(
                                            f"Successfully processed: {dll_path}"
                                        )
                                        updated_games.append(
                                            (str(dll_path), launcher, dll_type)
                                        )
                                        if backup_path:
                                            successful_backups.append(
                                                (str(dll_path), backup_path)
                                            )
                                    else:
                                        if (
                                            backup_path
                                        ):  # If we have a backup path but success is False, it was attempted
                                            skipped_games.append(
                                                (
                                                    str(dll_path),
                                                    launcher,
                                                    "Update failed",
                                                    dll_type,
                                                )
                                            )
                                        else:  # If no backup path, it was skipped for other reasons
                                            skipped_games.append(
                                                (
                                                    str(dll_path),
                                                    launcher,
                                                    "Skipped",
                                                    dll_type,
                                                )
                                            )
                                processed_dlls.add(str(dll_path))
                        except Exception as e:
                            logger.error(f"Error processing DLL {dll_path}: {e}")
                            import traceback

                            logger.error(traceback.format_exc())
                            continue

            # Display summary after processing
            if updated_games:
                logger.info("\nGames updated successfully:")
                for dll_path, launcher, dll_type in updated_games:
                    game_name = extract_game_name(dll_path, launcher)
                    logger.info(f" - {game_name} - {launcher} ({dll_type})")
            else:
                logger.info("\nNo games were updated.")

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

        return True

    except Exception as e:
        import traceback

        trace = traceback.format_exc()
        logger.error(f"Critical error in update process: {e}")
        logger.error(f"Traceback:\n{trace}")
        return False


def process_single_dll(dll_path, launcher):
    """Process a single DLL file"""
    try:
        dll_type = DLL_TYPE_MAP.get(dll_path.name.lower(), "Unknown DLL type")
        logger.info(f" - {dll_type}: {dll_path}")

        game_name = extract_game_name(str(dll_path), launcher)
        if "warframe" in game_name.lower():
            return None

        if is_whitelisted(str(dll_path)):
            return False, None, dll_type

        dll_name = dll_path.name.lower()
        if dll_name in LATEST_DLL_PATHS:
            latest_dll_path = LATEST_DLL_PATHS[dll_name]
            return update_dll(str(dll_path), latest_dll_path)

        return False, None, dll_type
    except Exception as e:
        logger.error(f"Error processing DLL {dll_path}: {e}")
        return False, None, "Error"


def display_update_summary(updated_games, skipped_games, successful_backups):
    """Display a summary of the update process"""
    logger.info("\nSummary:")
    if not (updated_games or skipped_games or successful_backups):
        logger.info("No DLLs were found or processed.")
        return

    if updated_games:
        logger.info("\nGames updated successfully:")
        for dll_path, launcher, dll_type in updated_games:
            game_name = extract_game_name(dll_path, launcher)
            logger.info(f" - {game_name} - {launcher} ({dll_type})")
    else:
        logger.info("\nNo games were updated.")

    if successful_backups:
        logger.info("\nSuccessful backups:")
        for dll_path, backup_path in successful_backups:
            game_name = extract_game_name(dll_path, "Unknown")
            dll_type = DLL_TYPE_MAP.get(Path(dll_path).name.lower(), "Unknown DLL type")
            logger.info(f" - {game_name}: {backup_path} ({dll_type})")
    else:
        logger.info("\nNo backups were created.")

    if skipped_games:
        logger.info("\nGames skipped:")
        for dll_path, launcher, reason, dll_type in skipped_games:
            game_name = extract_game_name(dll_path, launcher)
            logger.info(f" - {game_name} - {launcher} ({dll_type}) (Reason: {reason})")


def main():
    try:
        if gui_mode:
            log_file = os.path.join(os.path.dirname(sys.executable), "dlss_updater.log")
            sys.stdout = sys.stderr = open(log_file, "w")
        # Run the application with Qt GUI
        main_ui = QApplication(sys.argv)
        main_window = MainWindow()
        main_window.show()
        main_window.get_current_settings()
        sys.exit(main_ui.exec())
    except Exception as e:
        logger.error(f"Critical error starting application: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


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
