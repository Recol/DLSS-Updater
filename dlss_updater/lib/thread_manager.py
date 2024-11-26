from PyQt6.QtCore import QThread, pyqtSignal

class ThreadManager(QThread):
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.assigned_function = None
        self.args = None
        self.kwargs = None

    def assign_function(self, func, *args, **kwargs):
        """Assign a function and its arguments to be executed in the thread."""
        self.assigned_function = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        """Execute the assigned function in the thread."""
        if self.assigned_function is not None:
            self.assigned_function(*self.args, **self.kwargs)
            self.finished.emit()
