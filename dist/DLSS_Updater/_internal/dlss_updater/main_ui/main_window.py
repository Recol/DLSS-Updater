from .. import __version__, resource_path
from ..utils import update_dlss_versions, extract_game_name, DLL_TYPE_MAP
import os
from PyQt6.QtCore import Qt, QUrl, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon
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
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
    QFrame,
    QProgressBar,
    QSizePolicy,
    QMessageBox,
    QCheckBox,
)
from dlss_updater.lib.threading_lib import ThreadManager
from pathlib import Path
from dlss_updater.config import config_manager, LauncherPathName
from dlss_updater.logger import add_qt_handler, LoggerWindow, setup_logger
from dlss_updater.whitelist import get_all_blacklisted_games
from dlss_updater.main_ui.animated_toggle import AnimatedToggle


class LoadingOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Make this widget transparent
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Create a semi-transparent background
        self.setStyleSheet("background-color: rgba(0, 0, 0, 120);")

        # Add a progress bar
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        container = QWidget()
        container_layout = QVBoxLayout(container)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)  # Changed to percentage range
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedSize(250, 20)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #5A5A5A;
                border-radius: 5px;
                background-color: #3C3C3C;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #2D6E88;
                border-radius: 3px;
            }
        """
        )

        self.label = QLabel("Processing: 0%")
        self.label.setStyleSheet(
            "color: white; background-color: transparent; font-size: 14px; padding: 10px;"
        )

        container_layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self.progress_bar, 0, Qt.AlignmentFlag.AlignCenter)

        # Center the container
        container.setStyleSheet(
            "background-color: #2E2E2E; border-radius: 10px; padding: 20px;"
        )
        container.setFixedSize(300, 100)

        layout.addWidget(container, 0, Qt.AlignmentFlag.AlignCenter)

        # Hide by default
        self.hide()

    def set_progress(self, percentage):
        """Update progress bar and label with percentage"""
        self.progress_bar.setValue(percentage)
        self.label.setText(f"Processing: {percentage}%")

    def reset_progress(self):
        """Reset progress to 0"""
        self.progress_bar.setValue(0)
        self.label.setText("Processing: 0%")

    def showEvent(self, event):
        # Position the overlay to cover the entire parent widget
        if self.parent():
            self.setGeometry(self.parent().rect())
        super().showEvent(event)

        # Manual fade in using timers but with smoother transition
        self.setWindowOpacity(0.0)
        for i in range(11):  # Keep original steps for stability
            opacity = i / 10.0
            QTimer.singleShot(i * 25, lambda op=opacity: self.setWindowOpacity(op))

    def set_message(self, message):
        self.label.setText(message)

    def hideWithAnimation(self):
        # Manual fade out using timers
        for i in range(11):  # Keep original steps for stability
            opacity = 1.0 - (i / 10.0)
            QTimer.singleShot(i * 25, lambda op=opacity: self.setWindowOpacity(op))

        # Hide when fully transparent
        QTimer.singleShot(300, self.hide)


class NotificationWidget(QWidget):
    """A notification widget with proper positioning and animations"""

    def __init__(self, message, parent=None):
        super().__init__(parent)
        # Setup widget
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background-color: transparent;")

        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Notification label
        self.label = QLabel(message)
        self.label.setStyleSheet(
            """
            background-color: #2D6E88; 
            color: white; 
            border-radius: 8px; 
            padding: 10px 20px;
            font: bold 12px;
        """
        )
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.label)

        # Size and position - now handled by the parent positioning method
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)

        # Keep original animation timing for stability
        self.setWindowOpacity(0.0)
        for i in range(11):
            opacity = i / 10.0
            QTimer.singleShot(i * 25, lambda op=opacity: self.setWindowOpacity(op))

        # Schedule fade out
        QTimer.singleShot(2000, self.start_fade_out)

    def start_fade_out(self):
        # Keep original animation timing for stability
        for i in range(11):
            opacity = 1.0 - (i / 10.0)
            QTimer.singleShot(i * 25, lambda op=opacity: self.setWindowOpacity(op))

        # Close when fully transparent
        QTimer.singleShot(300, self.close)


class MainWindow(QMainWindow):
    # Define signals at the class level
    resized = pyqtSignal()
    """Main application window for DLSS Updater"""

    def __init__(self, logger=None):
        super().__init__()
        self.thread_manager = ThreadManager(self)
        self.button_enum_dict = {}
        self.setWindowTitle("DLSS-Updater")
        self.setGeometry(100, 100, 700, 500)  # Starting size
        self.setMinimumSize(700, 500)  # Minimum allowed size

        # FIX: Limit maximum height more strictly to prevent excessive vertical space.
        # In theory no longer needed, but kept here just in case.
        # self.setMaximumSize(1200, 650)  # Reduced maximum height from 800 to 650

        # Control variables used to keep track of app state.
        self.logger_expanded = False
        self.original_width = None
        self.current_button_style = None

        # FIX: Keep track of active notifications for repositioning
        self.active_notifications = []

        # Load and set the window icon
        logo_path = resource_path(os.path.join("icons", "dlss_updater.png"))
        logo_icon = QIcon(logo_path)
        self.setWindowIcon(logo_icon)

        # Main container
        self.main_container = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)  # Consistent spacing
        main_layout.setContentsMargins(10, 10, 10, 10)  # Consistent margins
        header_layout = QHBoxLayout()

        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        # Header section with welcome, logo, version, and other buttons
        header_left = QHBoxLayout()
        welcome_label = QLabel("Welcome to the GUI :) -Deco")
        version_label = QLabel(f"v{__version__}")
        welcome_label.setStyleSheet(
            "color: white; font-size: 16px; background-color: transparent;"
        )
        version_label.setStyleSheet(
            "color: #888888; font-size: 12px; margin-left: 8px; background-color: transparent;"
        )
        header_left.addWidget(welcome_label)
        header_left.addStretch()
        header_left.addWidget(version_label)

        # Add the header layout to the main layout
        header_layout.addLayout(header_left)
        main_layout.addLayout(header_layout)

        # Add the DLSS Updater logo as a separate element
        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.logo_label)

        # Custom folders info
        info_label = QLabel(
            "Note: You can now use the custom folder buttons below to add up to 4 additional game folders."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(
            "color: white; background-color: #3C3C3C; padding: 10px; border-radius: 4px; border: none;"
        )
        main_layout.addWidget(info_label)

        # Donate, report a bug, contact, release notes, and view logs buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(5)  # Consistent spacing
        donate_button = self.create_styled_button(
            "☕ Support Development", "heart.png", "Support the development"
        )
        donate_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://buymeacoffee.com/decouk"))
        )

        report_bug_button = self.create_styled_button(
            "🐛 Report a Bug", "bug.png", "Report a bug"
        )
        report_bug_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://github.com/Recol/DLSS-Updater/issues")
            )
        )

        contact_button = self.create_styled_button(
            "📞 Contact", "contact.png", "Contact the developer"
        )
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

        release_notes_button = self.create_styled_button(
            "📝 Release Notes", "notes.png", "View release notes"
        )
        release_notes_button.clicked.connect(self.show_release_notes)

        view_logs_button = self.create_styled_button(
            "📋 View Logs", "logs.png", "View application logs"
        )
        view_logs_button.clicked.connect(self.toggle_logger_window)

        # Add Check for Updates button before the Blacklist Manager button
        check_updates_button = self.create_styled_button(
            "🔄 Check for Updates", "Check for application updates"
        )
        check_updates_button.clicked.connect(self.check_for_updates)

        # Add hover effect to the button
        self.add_button_hover_effect(check_updates_button)

        # Add it to the button layout before blacklist_button
        button_layout.addWidget(check_updates_button)

        # Add Blacklist Manager button
        blacklist_button = self.create_styled_button(
            "⚙ Manage Blacklist", "settings.png", "Manage blacklisted games"
        )
        blacklist_button.clicked.connect(self.show_blacklist_manager)
        self.preferences_button = self.create_styled_button(
            "⚙ Update Preferences",
            "settings.png",
            "Configure which technologies to update",
        )
        self.preferences_button.clicked.connect(self.show_update_preferences)
        # Add hover effect to buttons
        for btn in [
            donate_button,
            report_bug_button,
            contact_button,
            release_notes_button,
            view_logs_button,
            blacklist_button,
            self.preferences_button,
        ]:
            self.add_button_hover_effect(btn)

        button_layout.addWidget(donate_button)
        button_layout.addWidget(report_bug_button)
        button_layout.addWidget(contact_button)
        button_layout.addWidget(release_notes_button)
        button_layout.addWidget(view_logs_button)
        button_layout.addWidget(blacklist_button)
        button_layout.addWidget(self.preferences_button)
        main_layout.addLayout(button_layout)

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

        main_layout.addWidget(self.logger_splitter)
        self.main_container.setLayout(main_layout)
        self.main_container.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.MinimumExpanding
        )
        self.logger_splitter.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.MinimumExpanding
        )
        self.setCentralWidget(self.main_container)

        # Create the loading overlay
        self.loading_overlay = LoadingOverlay(self.main_container)
        self.loading_overlay.hide()

        # Set up logging
        self.logger = logger or setup_logger()
        add_qt_handler(self.logger, self.logger_window)
        self.logger_window.signals.error.connect(self.expand_logger_window)

        # Connect the update button to the threaded update function
        self.start_update_button.clicked.connect(self.call_threaded_update)

        # Connect resize event to handle notification repositioning
        self.resized.connect(self.reposition_notifications)

        self.apply_dark_theme()

    def resizeEvent(self, event):
        """Handle window resize events"""
        super().resizeEvent(event)
        # Emit the custom resize signal
        self.resized.emit()

        # Update overlay position if it's visible
        if hasattr(self, "loading_overlay") and self.loading_overlay.isVisible():
            self.loading_overlay.setGeometry(self.main_container.rect())

    def reposition_notifications(self):
        """Reposition all active notifications when window size changes"""
        for notification in self.active_notifications[:]:
            if notification.isVisible():
                self.position_notification(notification)
            else:
                # Be careful with list modification during iteration
                try:
                    self.active_notifications.remove(notification)
                except ValueError:
                    # Notification might have been removed already
                    pass

    def position_notification(self, notification):
        """Position a notification properly within the window bounds"""
        if notification and notification.isVisible() and notification.parent():
            notification.adjustSize()
            parent_rect = self.rect()
            notification_width = notification.width()
            notification_height = notification.height()

            # Calculate position - centered horizontally, near bottom vertically
            x = max(0, (parent_rect.width() - notification_width) // 2)
            y = max(0, parent_rect.height() - notification_height - 30)

            # Ensure notification stays within parent bounds
            x = min(x, parent_rect.width() - notification_width)
            y = min(y, parent_rect.height() - notification_height)

            notification.move(x, y)

    def add_button_hover_effect(self, button):
        """Add hover effect to button while preserving original styling"""
        # Store the button's original style
        original_style = button.styleSheet()

        # Check if this is a custom folder button (blue background) or regular button (gray background)
        is_custom = (
            "background-color: #2D6E88" in original_style
            or "background-color: #2D5A88" in original_style
        )

        # Create hover style by changing only the background color while preserving other styles
        if is_custom:
            original_bg = (
                "#2D6E88"
                if "background-color: #2D6E88" in original_style
                else "#2D5A88"
            )
            hover_bg = "#367FA3" if original_bg == "#2D6E88" else "#366BA3"
            hover_style = original_style.replace(
                f"background-color: {original_bg}", f"background-color: {hover_bg}"
            )
        else:
            hover_style = original_style.replace(
                "background-color: #4D4D4D", "background-color: #5A5A5A"
            )

        # Store original event handlers
        original_enter = button.enterEvent
        original_leave = button.leaveEvent

        # Define new event handlers
        def new_enter_event(event):
            self.current_button_style = button.styleSheet()
            button.setStyleSheet(hover_style)
            if original_enter:
                original_enter(event)

        def new_leave_event(event):
            if self.current_button_style:
                button.setStyleSheet(self.current_button_style)
            if original_leave:
                original_leave(event)

        # Override event handlers
        button.enterEvent = new_enter_event
        button.leaveEvent = new_leave_event

    def show_blacklist_manager(self):
        """Show dialog to manage blacklisted games"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Manage Blacklisted Games")
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(400)

        layout = QVBoxLayout()

        info_label = QLabel(
            "Select games to ignore in the blacklist. Selected games will be updated even if they're in the blacklist."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(
            "margin-bottom: 10px; background-color: transparent; border: none;"
        )
        layout.addWidget(info_label)

        # Create a list widget with animated toggles for blacklisted games
        game_list = QListWidget()
        game_list.setStyleSheet("background-color: #3C3C3C;")

        # Get all blacklisted games
        blacklisted_games = get_all_blacklisted_games()
        blacklisted_games = sorted(blacklisted_games)  # Sort alphabetically

        # Get currently skipped games
        skipped_games = config_manager.get_all_blacklist_skips()

        # Store toggle widgets to access them later
        toggle_widgets = []

        for game in blacklisted_games:
            item = QListWidgetItem()
            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(5, 2, 5, 2)

            game_label = QLabel(game)
            game_label.setStyleSheet("color: white; background: transparent;")

            # Create animated toggle
            toggle = AnimatedToggle()
            toggle.setChecked(game in skipped_games)
            toggle.game_name = game  # Store game name with the toggle
            toggle_widgets.append(toggle)

            item_layout.addWidget(game_label)
            item_layout.addStretch()
            item_layout.addWidget(toggle)

            item.setSizeHint(item_widget.sizeHint())
            game_list.addItem(item)
            game_list.setItemWidget(item, item_widget)

        layout.addWidget(game_list)

        # Add Select All and Deselect All buttons
        selection_layout = QHBoxLayout()
        select_all_button = QPushButton("Select All")
        select_all_button.clicked.connect(
            lambda: self.toggle_all_toggles(toggle_widgets, True)
        )

        deselect_all_button = QPushButton("Deselect All")
        deselect_all_button.clicked.connect(
            lambda: self.toggle_all_toggles(toggle_widgets, False)
        )

        # Add hover effect to buttons
        self.add_button_hover_effect(select_all_button)
        self.add_button_hover_effect(deselect_all_button)

        selection_layout.addWidget(select_all_button)
        selection_layout.addWidget(deselect_all_button)
        layout.addLayout(selection_layout)

        # Add OK and Cancel buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        dialog.setLayout(layout)

        # Process the result if accepted
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Clear existing skips
            config_manager.clear_all_blacklist_skips()

            # Add new skips
            for toggle in toggle_widgets:
                if toggle.isChecked():
                    config_manager.add_blacklist_skip(toggle.game_name)

            self.logger.info("Updated blacklist skip settings")

            # Show notification
            self.show_notification("Blacklist settings updated!")

    def toggle_all_toggles(self, toggles, state):
        """Toggle all toggle switches to the given state"""
        for toggle in toggles:
            toggle.setChecked(state)

    def safe_disconnect(self, signal, slot):
        """Safely disconnect a signal from a slot if connected"""
        try:
            if signal and slot:
                signal.disconnect(slot)
        except Exception:
            # Ignore any disconnect errors
            pass

    def show_notification(self, message, duration=2000):
        """Show a floating notification message properly positioned"""
        notification = NotificationWidget(message, self)

        # Add to active notifications list
        self.active_notifications.append(notification)

        # Position notification
        self.position_notification(notification)

        # Show notification
        notification.show()

        # Remove from active notifications after it's closed
        QTimer.singleShot(
            duration + 300, lambda: self._remove_notification(notification)
        )

    def _remove_notification(self, notification):
        """Safely remove a notification from the active list"""
        if notification in self.active_notifications:
            try:
                self.active_notifications.remove(notification)
            except ValueError:
                # Already removed
                pass

    def show_update_preferences(self):
        """Show dialog to configure update preferences"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Update Preferences")
        dialog.setMinimumWidth(400)

        layout = QVBoxLayout()

        info_label = QLabel("Select which technologies you want to update:")
        info_label.setWordWrap(True)
        info_label.setStyleSheet(
            "margin-bottom: 10px; background-color: transparent; border: none;"
        )
        layout.addWidget(info_label)

        # Create checkboxes
        self.dlss_checkbox = QCheckBox("DLSS (Deep Learning Super Sampling)")
        self.dlss_checkbox.setChecked(config_manager.get_update_preference("DLSS"))

        self.ds_checkbox = QCheckBox("DirectStorage")
        self.ds_checkbox.setChecked(
            config_manager.get_update_preference("DirectStorage")
        )

        self.xess_checkbox = QCheckBox("XeSS (Intel Xe Super Sampling)")
        self.xess_checkbox.setChecked(config_manager.get_update_preference("XeSS"))

        # Add FSR checkbox
        self.fsr_checkbox = QCheckBox("FSR (AMD FidelityFX Super Resolution)")
        self.fsr_checkbox.setChecked(config_manager.get_update_preference("FSR"))

        # Apply styling to checkboxes
        checkbox_style = """
            QCheckBox {
                color: white;
                background-color: transparent;
                padding: 5px;
                font-size: 14px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
            QCheckBox::indicator:unchecked {
                border: 1px solid #7F7F7F;
                background-color: #3C3C3C;
            }
            QCheckBox::indicator:checked {
                border: 1px solid #2D6E88;
                background-color: #2D6E88;
            }
        """
        self.dlss_checkbox.setStyleSheet(checkbox_style)
        self.ds_checkbox.setStyleSheet(checkbox_style)
        self.xess_checkbox.setStyleSheet(checkbox_style)
        self.fsr_checkbox.setStyleSheet(checkbox_style)

        # Add checkboxes to layout
        layout.addWidget(self.dlss_checkbox)
        layout.addWidget(self.ds_checkbox)
        layout.addWidget(self.xess_checkbox)
        layout.addWidget(self.fsr_checkbox)

        # Update help text to include FSR
        help_text = QTextBrowser()
        help_text.setMaximumHeight(150)
        help_text.setHtml(
            """
            <p><b>DLSS</b>: NVIDIA Deep Learning Super Sampling technology improves performance while maintaining high image quality. Updates DLLs:</p>
            <ul>
                <li>nvngx_dlss.dll</li>
                <li>nvngx_dlssg.dll</li>
                <li>nvngx_dlssd.dll</li>
                <li>sl.*.dll (Streamline components)</li>
            </ul>
            <p><b>DirectStorage</b>: Microsoft's DirectStorage API accelerates game loading times and texture streaming. Updates DLLs:</p>
            <ul>
                <li>dstorage.dll</li>
                <li>dstoragecore.dll</li>
            </ul>
            <p><b>XeSS</b>: Intel's Xe Super Sampling technology provides performance improvements similar to DLSS for all GPU brands.</p>
            <p><b>FSR</b>: AMD's FidelityFX Super Resolution technology improves performance while maintaining visual quality across all GPU brands. Note: Only FSR 3.1.1 and later can be updated.</p>
            """
        )
        help_text.setStyleSheet(
            "background-color: #3C3C3C; color: white; border: 1px solid #555;"
        )
        layout.addWidget(help_text)

        # Add note about requiring at least one selection
        note_label = QLabel(
            "Note: At least one technology must be selected for updates to function."
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet(
            "color: #AAAAAA; font-style: italic; margin-top: 10px;"
        )
        layout.addWidget(note_label)

        # Add buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(lambda: self.validate_preferences(dialog))
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        dialog.setLayout(layout)
        dialog.exec()

    def validate_preferences(self, dialog):
        """Validate that at least one preference is selected"""
        if not (
            self.dlss_checkbox.isChecked()
            or self.ds_checkbox.isChecked()
            or self.xess_checkbox.isChecked()
            or self.fsr_checkbox.isChecked()  # Add FSR checkbox check
        ):
            # Show warning if no technologies selected
            warning_dialog = QMessageBox(self)
            warning_dialog.setIcon(QMessageBox.Icon.Warning)
            warning_dialog.setWindowTitle("Warning")
            warning_dialog.setText("At least one technology must be selected.")
            warning_dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
            warning_dialog.exec()
        else:
            # Save preferences and close dialog
            config_manager.set_update_preference("DLSS", self.dlss_checkbox.isChecked())
            config_manager.set_update_preference(
                "DirectStorage", self.ds_checkbox.isChecked()
            )
            config_manager.set_update_preference("XeSS", self.xess_checkbox.isChecked())
            config_manager.set_update_preference("FSR", self.fsr_checkbox.isChecked())

            self.logger.info("Updated technology preferences")
            self.show_notification("Update preferences saved!")
            dialog.accept()

    def check_for_updates(self):
        """Check for application updates"""
        from dlss_updater.auto_updater import check_for_updates, get_releases_url
        from PyQt6.QtWidgets import QMessageBox

        # Show loading
        self.loading_overlay.set_message("Checking for updates...")
        self.loading_overlay.show()

        try:
            latest_version, update_available = check_for_updates()

            # Hide loading
            self.loading_overlay.hideWithAnimation()

            if latest_version and update_available:
                # Show update available dialog
                reply = QMessageBox.question(
                    self,
                    "Update Available",
                    f"A new version ({latest_version}) is available!\n"
                    f"Current version: {__version__}\n\n"
                    "Would you like to download it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )

                if reply == QMessageBox.StandardButton.Yes:
                    # Open GitHub releases page
                    QDesktopServices.openUrl(QUrl(get_releases_url()))
            elif latest_version:
                # No update available
                QMessageBox.information(
                    self,
                    "No Updates",
                    f"You are running the latest version ({__version__}).",
                    QMessageBox.StandardButton.Ok,
                )
            else:
                # Error checking for updates
                QMessageBox.warning(
                    self,
                    "Update Check Failed",
                    "Could not check for updates. Please check your internet connection.",
                    QMessageBox.StandardButton.Ok,
                )
        except Exception as e:
            self.logger.error(f"Error checking for updates: {e}")
            self.loading_overlay.hideWithAnimation()
            QMessageBox.critical(
                self,
                "Error",
                f"An error occurred while checking for updates: {str(e)}",
                QMessageBox.StandardButton.Ok,
            )

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

                # Simple fade-in effect with opacity
                dialog.setWindowOpacity(0.0)
                dialog.show()

                # Gradually increase opacity - keep original timing for stability
                for i in range(11):
                    opacity = i / 10.0
                    QTimer.singleShot(
                        i * 25, lambda op=opacity: dialog.setWindowOpacity(op)
                    )

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

                # Show notification
                self.show_notification(f"Reset path for {launcher_button.objectName()}")

    def expand_logger_window(self):
        """Increase app window size and expands the logger window. Used only for errors."""
        if self.logger_expanded:
            return

        self.original_width = self.width()
        target_width = min(int(self.width() * 1.4), self.maximumWidth())

        # Set the splitter sizes directly
        self.logger_splitter.setSizes([target_width // 2, target_width // 2])
        self.logger_expanded = True

    def toggle_logger_window(self):
        """Toggle logger window with proper sizing constraints"""
        try:
            if self.logger_expanded:
                # Collapse logger
                self.logger_splitter.setSizes([self.original_width, 0])
                self.logger_expanded = False
                return

            # Store original width before expanding
            self.original_width = self.width()
            target_width = min(int(self.width() * 1.4), self.maximumWidth())

            # Expand logger
            self.logger_splitter.setSizes([target_width // 2, target_width // 2])
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

        # Set alignment and size policy
        button.setStyleSheet(button.styleSheet() + "text-align: left;")
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        if tooltip:
            button.setToolTip(tooltip)

        # Connect to browse functionality if not the update button
        if "Update" not in text:
            if "Custom Folder" in text or any(
                launcher in text
                for launcher in [
                    "Steam",
                    "EA",
                    "Ubisoft",
                    "Epic",
                    "GOG",
                    "Battle.net",
                    "Xbox",
                ]
            ):
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

        # Add custom folder buttons
        self.custom1_text_browser = self.create_styled_button(
            "Custom Folder 1", "folder.png", "Select custom game location 1"
        )
        self.custom2_text_browser = self.create_styled_button(
            "Custom Folder 2", "folder.png", "Select custom game location 2"
        )
        self.custom3_text_browser = self.create_styled_button(
            "Custom Folder 3", "folder.png", "Select custom game location 3"
        )
        self.custom4_text_browser = self.create_styled_button(
            "Custom Folder 4", "folder.png", "Select custom game location 4"
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
        self.custom1_text_browser.setObjectName("CUSTOM1")
        self.custom2_text_browser.setObjectName("CUSTOM2")
        self.custom3_text_browser.setObjectName("CUSTOM3")
        self.custom4_text_browser.setObjectName("CUSTOM4")

        # Store buttons in list
        self.button_list = [
            self.steam_text_browser,
            self.ea_text_browser,
            self.ubisoft_text_browser,
            self.epic_text_browser,
            self.gog_text_browser,
            self.battlenet_text_browser,
            self.xbox_text_browser,
            self.custom1_text_browser,
            self.custom2_text_browser,
            self.custom3_text_browser,
            self.custom4_text_browser,
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
                "CUSTOM1": LauncherPathName.CUSTOM1,
                "CUSTOM2": LauncherPathName.CUSTOM2,
                "CUSTOM3": LauncherPathName.CUSTOM3,
                "CUSTOM4": LauncherPathName.CUSTOM4,
            }
        )

        # Create layout for buttons with reset buttons
        browse_buttons_layout = QVBoxLayout()
        browse_buttons_layout.setSpacing(5)  # Consistent spacing
        browse_buttons_layout.setContentsMargins(5, 5, 5, 5)  # Consistent margins

        # Create a separator for original launchers and custom folders
        def create_separator(text):
            # Create a horizontal line with label - fixed styling
            container = QWidget()
            separator_layout = QHBoxLayout(container)
            separator_layout.setContentsMargins(0, 10, 0, 5)

            # Create label with better styling
            label = QLabel(text)
            label.setStyleSheet(
                "color: white; background-color: transparent; border: none;"
            )
            label.setMaximumWidth(150)  # Limit label width

            # Create lines
            left_line = QFrame()
            left_line.setFrameShape(QFrame.Shape.HLine)
            left_line.setFrameShadow(QFrame.Shadow.Sunken)
            left_line.setStyleSheet("background-color: #5A5A5A; border: none;")

            right_line = QFrame()
            right_line.setFrameShape(QFrame.Shape.HLine)
            right_line.setFrameShadow(QFrame.Shadow.Sunken)
            right_line.setStyleSheet("background-color: #5A5A5A; border: none;")

            # Add everything to layout
            separator_layout.addWidget(left_line)
            separator_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignCenter)
            separator_layout.addWidget(right_line)

            return container

        # Add built-in launchers label
        browse_buttons_layout.addWidget(create_separator("Game Launchers"))

        # Add standard launcher buttons
        for button in self.button_list[:7]:  # First 7 are standard launchers
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

            # Add hover effect to reset button
            self.add_button_hover_effect(reset_button)

            button_row.addWidget(reset_button)
            browse_buttons_layout.addLayout(button_row)

        # Add custom folders label
        browse_buttons_layout.addWidget(create_separator("Custom Folders"))

        # Add custom folder buttons
        for button in self.button_list[7:]:  # Last 4 are custom folders
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

            # Add hover effect to reset button
            self.add_button_hover_effect(reset_button)

            button_row.addWidget(reset_button)
            browse_buttons_layout.addLayout(button_row)

        # Add update button separator and button
        browse_buttons_layout.addWidget(create_separator("Update"))
        browse_buttons_layout.addWidget(self.start_update_button)

        # Add hover effects to all buttons
        for button in self.button_list:
            self.add_button_hover_effect(button)

        # Add special hover effect to update button
        self.add_button_hover_effect(self.start_update_button)

        # Create a scrollable area for the buttons
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.MinimumExpanding
        )

        self.browse_buttons_container_widget = QWidget()
        self.browse_buttons_container_widget.setLayout(browse_buttons_layout)

        # Set size policy to ensure proper expansion
        self.browse_buttons_container_widget.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.MinimumExpanding
        )

        scroll_area.setWidget(self.browse_buttons_container_widget)
        self.browse_buttons_container_widget = scroll_area

    def update_dlss_with_progress(self):
        """Wrapper for update_dlss_versions that emits progress signals"""
        from dlss_updater.utils import ParallelProgressTracker

        # Store reference to the thread's signal emitter
        if hasattr(self.thread_manager.current_worker, "signals"):
            self.progress_tracker = ParallelProgressTracker(1, self.logger)
            self.progress_tracker.progress_updated.connect(
                self.thread_manager.current_worker.signals.progress.emit
            )

        return update_dlss_versions()

    def call_threaded_update(self):
        """Start the update process in a separate thread."""
        try:
            # Disable the button immediately to prevent multiple clicks
            self.start_update_button.setEnabled(False)
            self.logger.info("Starting update process in thread...")

            # Show loading overlay
            self.loading_overlay.reset_progress()
            self.loading_overlay.set_progress(0)
            self.loading_overlay.show()

            # Clear any previous signal connections
            if self.thread_manager.signals:
                try:
                    # Disconnect previous connections if they exist
                    self.thread_manager.signals.finished.disconnect()
                    self.thread_manager.signals.result.disconnect()
                    self.thread_manager.signals.error.disconnect()
                    self.thread_manager.signals.progress.disconnect()
                except TypeError:
                    # Ignore errors if signals were not connected
                    pass

            # Assign the update function with progress signal
            self.thread_manager.assign_function(
                update_dlss_versions, progress_signal=None  # Will be set by Worker
            )

            # Connect new signals
            self.thread_manager.signals.finished.connect(self.handle_update_finished)
            self.thread_manager.signals.result.connect(self.handle_update_result)
            self.thread_manager.signals.error.connect(self.handle_update_error)
            self.thread_manager.signals.progress.connect(self.update_progress)

            # Run the thread
            self.thread_manager.run()

        except Exception as e:
            self.logger.error(f"Error starting update thread: {e}")
            import traceback

            self.logger.error(traceback.format_exc())
            # Ensure button is re-enabled in case of an error
            self.start_update_button.setEnabled(True)
            self.loading_overlay.hideWithAnimation()

    def update_progress(self, progress):
        self.loading_overlay.set_progress(progress)

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

        # Hide loading overlay
        self.loading_overlay.hideWithAnimation()

        # Stop timer for progress messages
        if hasattr(self, "progress_timer") and self.progress_timer.isActive():
            self.progress_timer.stop()

        # Show error notification
        self.show_notification(f"Error: {value}", 5000)

    def handle_update_result(self, result):
        """
        Handle results from the update thread.
        @param result: Tuple containing (success, updated_games, skipped_games, successful_backups)
        """
        try:
            # Stop timer for progress messages
            if hasattr(self, "progress_timer") and self.progress_timer.isActive():
                self.progress_timer.stop()

            # Hide loading overlay with animation
            self.loading_overlay.hideWithAnimation()

            if isinstance(result, tuple) and len(result) == 4:
                success, updated_games, skipped_games, successful_backups = result
                if success:
                    self.logger.info("Update process completed successfully")

                    # Show success notification
                    if updated_games:
                        self.show_notification(
                            f"Update completed: {len(updated_games)} games updated",
                            3000,
                        )
                    else:
                        self.show_notification(
                            "Update completed: No games needed updates", 3000
                        )

                    self.show_update_summary(
                        (updated_games, skipped_games, successful_backups)
                    )
                else:
                    self.logger.error("Update process failed")
                    self.show_notification("Update process failed", 3000)
            else:
                self.logger.error(f"Unexpected result format: {result}")
                self.show_notification(
                    "Update process returned unexpected result", 3000
                )
        except Exception as e:
            self.logger.error(f"Error handling update result: {e}")
            import traceback

            self.logger.error(traceback.format_exc())
            self.show_notification(f"Error: {str(e)}", 3000)
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
        # Standard launchers
        steam_path = config_manager.check_path_value(LauncherPathName.STEAM)
        ea_path = config_manager.check_path_value(LauncherPathName.EA)
        ubisoft_path = config_manager.check_path_value(LauncherPathName.UBISOFT)
        epic_path = config_manager.check_path_value(LauncherPathName.EPIC)
        gog_path = config_manager.check_path_value(LauncherPathName.GOG)
        battlenet_path = config_manager.check_path_value(LauncherPathName.BATTLENET)
        xbox_path = config_manager.check_path_value(LauncherPathName.XBOX)

        # Custom paths
        custom1_path = config_manager.check_path_value(LauncherPathName.CUSTOM1)
        custom2_path = config_manager.check_path_value(LauncherPathName.CUSTOM2)
        custom3_path = config_manager.check_path_value(LauncherPathName.CUSTOM3)
        custom4_path = config_manager.check_path_value(LauncherPathName.CUSTOM4)

        self.path_list = [
            steam_path,
            ea_path,
            ubisoft_path,
            epic_path,
            gog_path,
            battlenet_path,
            xbox_path,
            custom1_path,
            custom2_path,
            custom3_path,
            custom4_path,
        ]

        for i, button in enumerate(self.button_list):
            if i < len(self.path_list) and self.path_list[i]:
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

            # Show notification
            self.show_notification(f"Folder path updated!")

    def show_update_summary(self, update_result):
        """Display the update summary in a message box."""
        updated_games, skipped_games, successful_backups = update_result

        # Create a custom dialog for better styling
        dialog = QDialog(self)
        dialog.setWindowTitle("DLSS Updater - Update Summary")
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(300)

        layout = QVBoxLayout(dialog)

        text_browser = QTextBrowser()
        text_browser.setStyleSheet(
            "background-color: #3C3C3C; color: white; border: 1px solid #555;"
        )

        summary_text = ""
        if updated_games:
            summary_text += "Games updated successfully:\n"
            for dll_path, launcher, dll_type in updated_games:
                game_name = extract_game_name(dll_path, launcher)
                summary_text += f" - {game_name} - {launcher} ({dll_type})\n"
        else:
            summary_text += "No games were updated.\n"

        if successful_backups:
            summary_text += "\nSuccessful backups:\n"
            for dll_path, backup_path in successful_backups:
                game_name = extract_game_name(dll_path, "Unknown")
                dll_type = DLL_TYPE_MAP.get(
                    Path(dll_path).name.lower(), "Unknown DLL type"
                )
                summary_text += f" - {game_name}: {backup_path} ({dll_type})\n"
        else:
            summary_text += "\nNo backups were created.\n"

        if skipped_games:
            summary_text += "\nGames skipped:\n"
            for dll_path, launcher, reason, dll_type in skipped_games:
                game_name = extract_game_name(dll_path, launcher)
                summary_text += (
                    f" - {game_name} - {launcher} ({dll_type}) (Reason: {reason})\n"
                )

        text_browser.setPlainText(summary_text)
        layout.addWidget(text_browser)

        # Add close button
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        button_box.accepted.connect(dialog.accept)
        layout.addWidget(button_box)

        # Simple fade-in effect with opacity
        dialog.setWindowOpacity(0.0)
        dialog.show()

        # Keep original animation timing for stability
        for i in range(11):
            opacity = i / 10.0
            QTimer.singleShot(i * 25, lambda op=opacity: dialog.setWindowOpacity(op))

        dialog.exec()

    def apply_dark_theme(self):
        """Apply a dark theme using stylesheets."""
        dark_stylesheet = """
            QMainWindow, QDialog {
                background-color: #2E2E2E; /* Dark background */
                color: #FFFFFF; /* White text */
            }
            QPushButton {
                background-color: #4D4D4D; /* Button background */
                color: #FFFFFF; /* Button text color */
                border: 1px solid #7F7F7F; /* Button border */
                padding: 5px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #5A5A5A; /* Button hover effect */
            }
            QPushButton:pressed {
                background-color: #444444; /* Button press effect */
            }
            QPushButton:disabled {
                background-color: #3D3D3D; /* Disabled button */
                color: #888888;
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
            QScrollArea {
                background-color: #2E2E2E;
                border: none;
            }
            QLabel {
                color: #FFFFFF;
                background-color: transparent;
                border: none;
            }
            QCheckBox {
                color: #FFFFFF;
                background-color: transparent;
                border: none;
            }
            QListWidget {
                background-color: #3C3C3C;
                color: #FFFFFF;
                border: 1px solid #7F7F7F;
            }
            QListWidget::item {
                background-color: #3C3C3C;
            }
            QListWidget::item:hover {
                background-color: #444444;
            }
            QFrame {
                background-color: transparent;
                border: none;
            }
            QSplitter::handle {
                background-color: #5A5A5A;
            }
            QMessageBox {
                background-color: #2E2E2E;
                color: #FFFFFF;
            }
            QMessageBox QLabel {
                color: #FFFFFF;
                background-color: transparent;
                border: none;
            }
            QDialogButtonBox {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                border: none;
                background: #3C3C3C;
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #5A5A5A;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QProgressBar {
                border: 1px solid #5A5A5A;
                border-radius: 5px;
                background-color: #3C3C3C;
                text-align: center;
                color: white;
            }
            QProgressBar::chunk {
                background-color: #2D6E88;
                width: 10px;
                margin: 0.5px;
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

        for button in self.button_list[:7]:  # Apply to launcher buttons
            button.setStyleSheet(button_style)

        # Apply special styling to custom folder buttons
        custom_button_style = """
                                QPushButton {
                                    background-color: #2D6E88;
                                    color: white;
                                    border: 1px solid #7F7F7F;
                                    border-radius: 4px;
                                    padding: 8px 16px;
                                    text-align: left;
                                    margin: 2px 0px;
                                }
                                QPushButton:hover {
                                    background-color: #367FA3;
                                    border-color: #999999;
                                }
                                QPushButton:pressed {
                                    background-color: #245D73;
                                }
                                QPushButton:disabled {
                                    background-color: #1D3D5A;
                                    color: #888888;
                                }
                            """

        for button in self.button_list[7:]:  # Apply to custom folder buttons
            button.setStyleSheet(custom_button_style)

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
