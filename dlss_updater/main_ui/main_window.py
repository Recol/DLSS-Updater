from .. import __version__, resource_path
from ..utils import update_dlss_versions
import os
from PyQt6.QtCore import Qt, QUrl, QSize
from PyQt6.QtGui import QDesktopServices, QIcon
from dlss_updater.lib.threading_lib import ThreadManager
from pathlib import Path
from dlss_updater.config import config_manager, LauncherPathName
from dlss_updater.logger import add_qt_handler, LoggerWindow, setup_logger
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QSplitter,
    QPushButton,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QDialog,
    QTextBrowser,
)


class MainWindow(QMainWindow):
    def __init__(self, logger=None):
        super().__init__()
        self.thread_manager = ThreadManager(self)
        self.button_enum_dict = {}
        self.setWindowTitle("DLSS-Updater")
        self.setGeometry(100, 100, 600, 350)
        self.logger_expanded = False
        self.original_width = None

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
        version_label.setStyleSheet(
            "color: #888888; font-size: 12px; margin-left: 8px;"
        )
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
        release_notes_button = QPushButton("📝 Release Notes")
        release_notes_button.clicked.connect(self.show_release_notes)

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

        logger_toggle_button = QPushButton("📋 View Logs")
        logger_toggle_button.clicked.connect(self.toggle_logger_window)

        header_layout.addWidget(welcome_label)
        header_layout.addStretch()
        header_layout.addWidget(donate_button)
        header_layout.addWidget(report_bug_button)
        header_layout.addWidget(contact_button)
        header_layout.addWidget(release_notes_button)
        header_layout.addWidget(logger_toggle_button)
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
        self.logger_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.can_continue = False
        self.button_list = []
        self.path_list = []

        # Launcher buttons setup
        self.setup_launcher_buttons()

        # Create QTextBrowser widget
        self.logger_window = LoggerWindow(self)

        # Set up splitter layout
        self.logger_splitter.addWidget(self.browse_buttons_container_widget)
        self.logger_splitter.addWidget(self.logger_window)
        # We want the logger_window to be collapsed by default
        self.logger_splitter.setSizes([1, 0])

        # Add new layouts to main layout
        main_layout.addLayout(header_layout)
        main_layout.addWidget(info_label)
        main_layout.addWidget(self.logger_splitter)
        main_container.setLayout(main_layout)
        self.setCentralWidget(main_container)

        # Set up logging
        self.logger = logger or setup_logger()
        add_qt_handler(self.logger, self.logger_window)
        self.logger_window.signals.error.connect(self.expand_logger_window)

        # Connect the update button to the threaded update function
        self.start_update_button.clicked.connect(self.call_threaded_update)

        self.apply_dark_theme()

    def show_release_notes(self):
        """Display release notes in a dialog"""
        release_notes_file = Path(resource_path("release_notes.txt"))
        if release_notes_file.exists():
            with open(release_notes_file, "r") as file:
                notes = file.read()
                dialog = QDialog(self)
                dialog.setWindowTitle("Release Notes")
                layout = QVBoxLayout()
                text_browser = QTextBrowser()
                text_browser.setPlainText(notes)
                text_browser.setStyleSheet("background-color: #3C3C3C; color: white;")
                layout.addWidget(text_browser)
                dialog.setLayout(layout)
                dialog.resize(500, 400)
                dialog.exec()

    def reset_path(self):
        """Reset the associated launcher path"""
        reset_button = self.sender()
        launcher_button = reset_button.property("reset_button")
        if launcher_button:
            launcher_enum = self.button_enum_dict.get(launcher_button.objectName())
            if launcher_enum:
                config_manager.reset_launcher_path(launcher_enum)
                launcher_button.setText(launcher_button.objectName())
                self.logger.info(f"Reset path for {launcher_button.objectName()}")

    def expand_logger_window(self):
        """Increase app window size and expands the logger window. Used only for errors."""
        if self.logger_expanded:
            return
        self.original_width = self.width()
        self.setFixedWidth(int(self.width() * 1.4))
        self.logger_splitter.setSizes([int(self.width()), int(self.width())])
        self.logger_expanded = True

    def toggle_logger_window(self):
        """Increase app window size and expands the logger window."""
        try:
            if self.logger_expanded:
                self.logger_splitter.setSizes([1, 0])
                self.setFixedWidth(self.original_width)
                self.logger_expanded = False
                return

            # Store original width before expanding
            self.original_width = self.width()

            # Expand window
            self.setFixedWidth(int(self.width() * 1.4))
            self.logger_splitter.setSizes([int(self.width()), int(self.width())])
            self.logger_expanded = True

        except Exception as e:
            self.logger.error(f"Error toggling logger window: {e}")

    def create_styled_button(
        self, text: str, icon_path: str, tooltip: str = ""
    ) -> QPushButton:
        """
        Creates styled buttons with the specific icon and tooltip.
        @param text: Text to be displayed.
        @param icon_path: Path to the icon.
        @param tooltip: Tooltip on hover. Optional.
        @return: QPushButton Created button.
        """
        button = QPushButton(f"  {text}", self)

        # Load and process icon
        icon = QIcon(resource_path(os.path.join("icons", icon_path)))
        button.setIcon(icon)
        button.setIconSize(QSize(24, 24))  # Consistent icon size

        # Set fixed height for uniformity
        button.setMinimumHeight(40)

        if tooltip:
            button.setToolTip(tooltip)

        # Connect to browse functionality if not the update button
        if "Update" not in text:
            button.clicked.connect(self.browse_folder)

        return button

    def setup_launcher_buttons(self):
        """Setups the launcher buttons."""
        # Create launcher buttons
        self.steam_text_browser = self.create_styled_button(
            "Steam Games", "steam.png", "Select Steam game locations"
        )
        self.ea_text_browser = self.create_styled_button(
            "EA Games", "ea.png", "Select EA game locations"
        )
        self.ubisoft_text_browser = self.create_styled_button(
            "Ubisoft Games", "ubisoft.png", "Select Ubisoft game locations"
        )
        self.epic_text_browser = self.create_styled_button(
            "Epic Games", "epic.png", "Select Epic game locations"
        )
        self.gog_text_browser = self.create_styled_button(
            "GOG Games", "gog.png", "Select GOG game locations"
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

        # Set object names for identification
        self.steam_text_browser.setObjectName("Steam")
        self.ea_text_browser.setObjectName("EA")
        self.ubisoft_text_browser.setObjectName("UBISOFT")
        self.epic_text_browser.setObjectName("EPIC")
        self.gog_text_browser.setObjectName("GOG")
        self.battlenet_text_browser.setObjectName("BATTLENET")
        self.xbox_text_browser.setObjectName("XBOX")

        # Store buttons in list
        self.button_list = [
            self.steam_text_browser,
            self.ea_text_browser,
            self.ubisoft_text_browser,
            self.epic_text_browser,
            self.gog_text_browser,
            self.battlenet_text_browser,
            self.xbox_text_browser,
        ]

        # Update button dictionary
        self.button_enum_dict.update(
            {
                "Steam": LauncherPathName.STEAM,
                "EA": LauncherPathName.EA,
                "UBISOFT": LauncherPathName.UBISOFT,
                "EPIC": LauncherPathName.EPIC,
                "GOG": LauncherPathName.GOG,
                "BATTLENET": LauncherPathName.BATTLENET,
                "XBOX": LauncherPathName.XBOX,
            }
        )

        # Create layout for buttons with reset buttons
        browse_buttons_layout = QVBoxLayout()
        for button in self.button_list:
            button_row = QHBoxLayout()
            button_row.addWidget(button, stretch=1)

            # Create reset button
            reset_button = QPushButton()
            reset_button.setIcon(
                QIcon(resource_path(os.path.join("icons", "reset.png")))
            )
            reset_button.setIconSize(QSize(16, 16))
            reset_button.setFixedSize(24, 24)
            reset_button.setToolTip("Reset path")
            reset_button.setProperty("reset_button", button)
            reset_button.clicked.connect(self.reset_path)
            reset_button.setStyleSheet(
                """
                QPushButton {
                    background-color: #4D4D4D;
                    border: 1px solid #7F7F7F;
                    border-radius: 4px;
                    padding: 2px;
                    margin: 2px;
                }
                QPushButton:hover {
                    background-color: #5A5A5A;
                }
                QPushButton:pressed {
                    background-color: #444444;
                }
            """
            )

            button_row.addWidget(reset_button)
            browse_buttons_layout.addLayout(button_row)

        browse_buttons_layout.addWidget(self.start_update_button)
        self.browse_buttons_container_widget = QWidget()
        self.browse_buttons_container_widget.setLayout(browse_buttons_layout)

    def call_threaded_update(self):
        """Start the update process in a separate thread."""
        try:
            # Disable the button immediately to prevent multiple clicks
            self.start_update_button.setEnabled(False)
            self.logger.info("Starting update process in thread...")

            # Clear any previous signal connections
            if self.thread_manager.signals:
                try:
                    # Disconnect previous connections if they exist
                    self.thread_manager.signals.finished.disconnect()
                    self.thread_manager.signals.result.disconnect()
                    self.thread_manager.signals.error.disconnect()
                except TypeError:
                    # Ignore errors if signals were not connected
                    pass

            # Assign the update function
            self.thread_manager.assign_function(update_dlss_versions)

            # Connect new signals
            self.thread_manager.signals.finished.connect(self.handle_update_finished)
            self.thread_manager.signals.result.connect(self.handle_update_result)
            self.thread_manager.signals.error.connect(self.handle_update_error)

            # Run the thread
            self.thread_manager.run()

        except Exception as e:
            self.logger.error(f"Error starting update thread: {e}")
            import traceback

            self.logger.error(traceback.format_exc())
            # Ensure button is re-enabled in case of an error
            self.start_update_button.setEnabled(True)

    def handle_update_error(self, error):
        """
        Handle errors from the update thread.
        @param error: The error from the update thread.
        """
        exctype, value, tb = error
        self.logger.error(f"Error: {exctype}")
        self.logger.error(f"Value: {value}")
        self.logger.error(f"Traceback: {tb}")
        self.start_update_button.setEnabled(True)

    def handle_update_result(self, result):
        """
        Handle results from the update thread.
        @param result: The result from the update thread.
        """
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
        """Handle completion of the update thread."""
        try:
            self.logger.debug("Update thread finished")
            self.start_update_button.setEnabled(True)
            # Clean up worker reference
            self._current_worker = None
        except Exception as e:
            self.logger.error(f"Error in update finished handler: {e}")

    def closeEvent(self, event):
        """Handle application close event."""
        try:
            if self.thread_manager and self.thread_manager.current_worker:
                self.thread_manager.waitForDone()
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        super().closeEvent(event)

    def get_current_settings(self):
        """Get the current settings from the settings file."""
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

        self.start_update_button.setStyleSheet(
            """
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
                """
        )
