from PyQt6.QtCore import QThread, pyqtSignal

class ThreadManager(QThread):
    finished = pyqtSignal()
    result = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.assigned_function = None
        self.args = None
        self.kwargs = None
        self.is_running = False

    def assign_function(self, func, *args, **kwargs):
        """Assign a function and its arguments to be executed in the thread."""
        if self.is_running:
            return
        self.assigned_function = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        """Execute the assigned function in the thread."""
        if self.assigned_function is not None:
            self.is_running = True
            function_output = self.assigned_function(*self.args, **self.kwargs)
            if function_output is not None:
                self.result.emit(function_output)
            self.finished.emit()
            self.is_running = False
