from PyQt6.QtCore import QThreadPool, QRunnable, pyqtSignal, QObject


class WorkerSignals(QObject):
    """Signals available from a running worker thread."""

    finished = pyqtSignal()
    result = pyqtSignal(object)
    error = pyqtSignal(tuple)
    progress = pyqtSignal(int)


class Worker(QRunnable):
    """
    Worker thread for running background tasks.

    Inherits from QRunnable for better thread pool management.
    """

    def __init__(self, fn, progress_callback=None, *args, **kwargs):
        super().__init__()

        # Store constructor arguments (re-used for processing)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

        # Create signals for communication
        self.signals = WorkerSignals()

        # Store progress callback if provided
        self.progress_callback = progress_callback
        if self.progress_callback:
            self.signals.progress.connect(self.progress_callback)

        # Add an option to stop the worker if needed
        self.is_running = True

    def run(self):
        """
        Initialise the runner function with passed args, kwargs.

        Automatic handling of different function signatures and return values.
        """
        try:
            # Pass the signals to the function if it accepts them
            if "progress_signal" in self.kwargs:
                self.kwargs["progress_signal"] = self.signals.progress

            # Retrieve args/kwargs here; and fire processing using them
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            # If function raised an exception, capture it
            import traceback

            # Package the exception details
            exctype = type(e)
            value = str(e)
            tb = traceback.format_exc()

            # If still running, emit the error signal
            if self.is_running:
                self.signals.error.emit((exctype, value, tb))
        else:
            # If function successfully completed, emit result
            if self.is_running:
                if result is not None:
                    self.signals.result.emit(result)
        finally:
            # Always emit finished signal
            if self.is_running:
                self.signals.finished.emit()

    def stop(self):
        """Set the running state to False to prevent further signal emissions."""
        self.is_running = False


class ThreadManager:
    """
    Manages thread pool and worker creation for background tasks.

    Provides a simplified interface for running functions in a thread pool.
    """

    def __init__(self, parent=None):
        # Create a thread pool
        self.thread_pool = QThreadPool()

        # Set up maximum thread count (adjust as needed)
        self.thread_pool.setMaxThreadCount(8)

        # Store the current worker
        self.current_worker = None

        # Store the parent (optional)
        self.parent = parent

        # Signals to be connected from the current worker
        self.signals = None

    def assign_function(self, func, *args, **kwargs):
        """
        Assign a function to be run in a background thread.

        Stops any existing worker before creating a new one.
        """
        # Stop any existing worker
        if self.current_worker:
            self.current_worker.stop()

        # Create a new worker
        worker = Worker(func, *args, **kwargs)

        # Store the current worker
        self.current_worker = worker

        # Update signals reference
        self.signals = worker.signals

    def run(self):
        """
        Run the currently assigned worker in the thread pool.

        Adds the worker to the thread pool for execution.
        """
        if self.current_worker:
            # Add worker to thread pool
            self.thread_pool.start(self.current_worker)

    def waitForDone(self):
        """
        Wait for all threads in the pool to complete.

        Useful for clean shutdown of the application.
        """
        # Stop current worker if exists
        if self.current_worker:
            self.current_worker.stop()

        # Wait for thread pool to finish
        self.thread_pool.waitForDone()
