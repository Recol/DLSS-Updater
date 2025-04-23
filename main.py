from dlss_updater.utils import *
from dlss_updater.logger import setup_logger
from dlss_updater.main_ui.main_window import MainWindow
from dlss_updater.auto_updater import cleanup_old_update_files
from dlss_updater.dll_repository import initialize_dll_cache
from PyQt6.QtWidgets import QApplication
import os
import sys

# Global flag to track initialization state
_dll_cache_initialized = False

logger = setup_logger()

# Add the directory containing the executable to sys.path
if getattr(sys, "frozen", False):
    application_path = os.path.dirname(sys.executable)
    sys.path.insert(0, application_path)


def main():
    try:
        # Initialize DLL cache only once and only after admin rights are confirmed
        global _dll_cache_initialized
        if not _dll_cache_initialized:
            logger.debug("Starting DLL cache initialization from main.py")
            initialize_dll_cache()
            _dll_cache_initialized = True
            logger.debug("DLL cache initialization completed")

        if gui_mode:
            log_file = os.path.join(os.path.dirname(sys.executable), "dlss_updater.log")
            sys.stdout = sys.stderr = open(log_file, "w")
        # Run the application with Qt GUI
        main_ui = QApplication(sys.argv)
        main_window = MainWindow(logger)
        main_window.show()
        main_window.get_current_settings()
        sys.exit(main_ui.exec())
    except Exception as e:
        logger.error(f"Critical error starting application: {e}")
        import traceback

        logger.error(traceback.format_exc())
        input("Press Enter to exit...")  # Wait for user input before exiting


if __name__ == "__main__":
    # Clean up any files from previous updates
    cleanup_old_update_files()

    # Check for update completion or errors
    check_update_completion()
    check_update_error()

    gui_mode = "--gui" in sys.argv
    logger.debug("Python executable: %s", sys.executable)
    logger.debug("sys.path: %s", sys.path)
    logger.info("DLSS Updater started")

    if not check_dependencies():
        sys.exit(1)

    # Check for admin rights and request if needed - BEFORE initializing DLL cache
    if not is_admin():
        logger.info("Requesting administrator privileges...")
        run_as_admin()
        sys.exit(0)  # Exit after requesting admin privileges
    else:
        main()
