from PyQt6.QtCore import QThreadPool, QRunnable, QObject, pyqtSignal

class WorkerSignals(QObject):
    finished = pyqtSignal()
    result = pyqtSignal(object)
    error = pyqtSignal(object)


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
                function_output = self.assigned_function(*self.args, **self.kwargs)
                if function_output is not None:
                    self.signals.result.emit(function_output)
            except Exception as e:
                self.signals.error.emit(f'Exception during function execution: {e}')
            finally:
                self.signals.finished.emit()


class Worker(QRunnable):
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self):
        """Execute the assigned function and emit signals."""
        if self.func is not None:
            try:
                function_output = self.func(*self.args, **self.kwargs)
                if function_output is not None:
                    self.signals.result.emit(function_output)  # Emit result signal
            except Exception as e:
                self.signals.error.emit(f'Exception during function execution: {e}')  # Emit error signal
            finally:
                self.signals.finished.emit()  # Emit finished signal