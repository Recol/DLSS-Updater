import logging
import sys
from pathlib import Path


class QLogger(logging.Handler):
    """Logger handler for the Qt GUI"""

    def __init__(self, text_browser):
        super().__init__()
        self.text_browser = text_browser

    def emit(self, record):
        msg = self.format(record)
        self.text_browser.append(msg)


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

        log_file_path = (
            Path(sys.executable).parent / log_file_name
            if getattr(sys, "frozen", False)
            else Path(__file__).parent / log_file_name
        )

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


def add_qt_handler(logger_to_extend, text_browser):
    """Add a QTextBrowser handler to an existing logger instance."""
    text_browser_handler = QLogger(text_browser)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    text_browser_handler.setFormatter(formatter)
    logger_to_extend.addHandler(text_browser_handler)


# Usage example
if __name__ == "__main__":
    logger = setup_logger()
    logger.info("This is a test log message")
    logger.info("This is a test logger.info message with an argument: %s", "test arg")
