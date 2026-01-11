import logging
import sys
from pathlib import Path


def _get_log_directory() -> Path:
    """
    Get the appropriate log directory based on platform and execution context.

    Returns:
        Path to the log directory (created if necessary).
    """
    if sys.platform == 'linux':
        # On Linux, use XDG-compliant location (works for Flatpak and native)
        log_dir = Path.home() / '.local' / 'share' / 'dlss-updater'
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir
    elif getattr(sys, "frozen", False):
        # Windows frozen: use executable directory
        return Path(sys.executable).parent
    else:
        # Development: use module directory
        return Path(__file__).parent


def setup_logger(log_file_name="dlss_updater.log"):
    """
    Setups the initial logger.
    param: log_file_name: filename to be used for the logfile.
    return: logger instance created.
    """
    logger = logging.getLogger("DLSSUpdater")

    # Check if the logger has already been configured
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        log_file_path = _get_log_directory() / log_file_name

        # Create handlers
        console_handler = logging.StreamHandler(sys.stdout)
        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")

        # Create formatter and add it to handlers
        log_format = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        console_handler.setFormatter(log_format)
        file_handler.setFormatter(log_format)

        # Add handlers to the logger
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

        # Prevent propagation to avoid duplicate logs
        logger.propagate = False

    return logger


# Usage example
if __name__ == "__main__":
    logger = setup_logger()
    logger.info("This is a test log message")
    logger.info("This is a test logger.info message with an argument: %s", "test arg")
