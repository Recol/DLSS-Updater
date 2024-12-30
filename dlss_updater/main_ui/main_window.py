from .. import __version__, resource_path
from ..utils import update_dlss_versions
import os
from PyQt6.QtCore import Qt, QUrl, QSize
from PyQt6.QtGui import QDesktopServices, QIcon
from dlss_updater.lib.threading_lib import ThreadManager
from dlss_updater.config import config_manager, LauncherPathName
from dlss_updater.logger import add_qt_handler
from PyQt6.QtWidgets import (
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


class MainWindow(QMainWindow):
    def __init__(self, logger=None):
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

        # Connect to browse functionality if not the update button
        if "Update" not in text:
            button.clicked.connect(self.browse_folder)

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

        self.apply_dark_theme()

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
        self.thread_manager.waitForDone()
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