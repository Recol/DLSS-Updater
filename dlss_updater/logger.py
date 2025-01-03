import logging
import sys
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal, Qt
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
        # Set document max size to prevent memory issues with very large logs
        self.document().setMaximumBlockCount(5000)  # Limit to last 5000 lines
        # Set text browser properties
        self.setReadOnly(True)
        self.setOpenExternalLinks(False)
        # Enable smooth scrolling
        self.verticalScrollBar().setSingleStep(2)


class QLogger(logging.Handler, QObject):
    """Logger handler for the Qt GUI"""
    # Define the signal as a class attribute
    logMessage = pyqtSignal(str, str)

    def __init__(self, text_browser):
        logging.Handler.__init__(self)
        QObject.__init__(self)  # Initialize QObject
        
        self.text_browser = text_browser
        self.colors_dict = {
            "DEBUG": "white",
            "INFO": "green", 
            "WARNING": "yellow",
            "ERROR": "red",
        }
        # Connect the signal to the slot method
        self.logMessage.connect(self.write_log, Qt.ConnectionType.QueuedConnection)

    def emit(self, record):
        """
        Logs the record to the text browser object.
        @param record: LogRecord object to log.
        """
        msg = self.format(record)
        # Emit the signal with levelname and formatted message
        self.logMessage.emit(record.levelname, msg)

    def write_log(self, levelname, msg):
        """Write the log message to the text browser in the main thread."""
        color = self.colors_dict[levelname]
        formatted_msg = f'<font color="{color}">{msg}</font>'
        self.text_browser.signals_to_emit[levelname].emit()
        self.text_browser.append(formatted_msg)
        # Scroll to bottom
        scrollbar = self.text_browser.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


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
    # Remove any existing QLogger handlers
    for handler in logger_to_extend.handlers[:]:
        if isinstance(handler, QLogger):
            logger_to_extend.removeHandler(handler)

    # Create a new QLogger handler
    text_browser_handler = QLogger(text_browser)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    text_browser_handler.setFormatter(formatter)
    logger_to_extend.addHandler(text_browser_handler)


# Usage example
if __name__ == "__main__":
    logger = setup_logger()
    logger.info("This is a test log message")
    logger.info("This is a test logger.info message with an argument: %s", "test arg")