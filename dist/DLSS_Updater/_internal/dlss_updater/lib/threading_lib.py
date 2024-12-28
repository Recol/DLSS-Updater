from PyQt6.QtCore import QThreadPool, QRunnable, QObject, pyqtSignal


class WorkerSignals(QObject):
    finished = pyqtSignal()
    result = pyqtSignal(object)
    error = pyqtSignal(tuple)
    progress = pyqtSignal(int)


class ThreadManager(QThreadPool):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.assigned_function = None
        self.args = None
        self.kwargs = None
        self.signals = WorkerSignals()

    def assign_function(self, func, *args, **kwargs):
        """Assign a function and its arguments to be executed in the thread."""
        self.assigned_function = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        """Execute the assigned function in the thread."""
        if self.assigned_function is not None:
            try:
                result = self.assigned_function(*self.args, **self.kwargs)
                if result is not None:
                    self.signals.result.emit(result)
            except Exception as e:
                import traceback

                exctype = type(e)
                value = str(e)
                tb = traceback.format_exc()
                self.signals.error.emit((exctype, value, tb))
            finally:
                try:
                    self.signals.finished.emit()
                except RuntimeError:
                    pass  # Handle case where Qt is shutting down
