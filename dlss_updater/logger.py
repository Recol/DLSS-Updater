import logging
import sys
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QTextBrowser

class QLoggerLevelSignal(QObject):
    """Signals for the Logger QTextBrowser derived class."""
    debug = pyqtSignal()
    info = pyqtSignal()
    warning = pyqtSignal()
    error = pyqtSignal()


class LoggerWindow(QTextBrowser):
    """A QTextBrowser subclass that have signals and a dict for ease of access to said signals."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = QLoggerLevelSignal()
        self.signals_to_emit = {
            "DEBUG": self.signals.debug,
            "INFO": self.signals.info,
            "WARNING": self.signals.warning,
            "ERROR": self.signals.error,
        }


class QLogger(logging.Handler):
    """Logger handler for the Qt GUI"""
    def __init__(self, text_browser):
        super().__init__()
        self.text_browser = text_browser
        self.colors_dict = {"DEBUG": "white", "INFO": "green", "WARNING": "yellow", "ERROR": "red"}

    def emit(self, record):
        """
        Logs the record to the text browser object.
        @param record: LogRecord object to log.
        """
        msg = self.format(record)
        color = self.colors_dict[record.levelname]
        self.text_browser.signals_to_emit[record.levelname].emit()
        self.text_browser.append(f'<font color="{color}">{msg}</font>')


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
    """
    Add a QTextBrowser handler to an existing logger instance.
    @param: logger_to_extend: logger instance to be extended.
    @param: text_browser: QTextBrowser instance to be added as a logger.
    """
    text_browser_handler = QLogger(text_browser)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    text_browser_handler.setFormatter(formatter)
    logger_to_extend.addHandler(text_browser_handler)


# Usage example
if __name__ == "__main__":
    logger = setup_logger()
    logger.info("This is a test log message")
    logger.info("This is a test logger.info message with an argument: %s", "test arg")
