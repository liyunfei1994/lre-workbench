"""Cancellable background work with UI results delivered through queued slots."""
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot, Qt
from task_control import CancellationToken, TaskCancelled, cancellation_scope, checkpoint


class AuxiliarySignals(QObject):
    finished = Signal(object)
    cancelled = Signal(object)
    error = Signal(str)


class CallableWorker(QRunnable):
    def __init__(self, task, kind="auxiliary"):
        super().__init__()
        self.task = task
        self.kind = kind
        self.cancel_token = CancellationToken()
        self.signals = AuxiliarySignals()

    def request_cancel(self):
        self.cancel_token.cancel()

    @Slot()
    def run(self):
        try:
            with cancellation_scope(self.cancel_token):
                result = self.task()
                # A request arriving during native sparse solve must prevent its
                # result from being published after that call returns.
                checkpoint()
                self.signals.finished.emit(result)
        except TaskCancelled:
            self.signals.cancelled.emit({"kind": self.kind, "records": []})
        except Exception:
            self.signals.error.emit(traceback.format_exc())


def start_auxiliary_task(main, task, kind, message, finished, error=None, cancelled=None):
    """Keep the worker alive and connect UI slots before starting the thread."""
    if getattr(main, "_busy", False):
        return None
    worker = CallableWorker(task, kind)
    worker.signals.finished.connect(finished, Qt.QueuedConnection)
    worker.signals.error.connect(error or main._on_worker_error, Qt.QueuedConnection)
    if cancelled is not None:
        worker.signals.cancelled.connect(cancelled, Qt.QueuedConnection)
    main._active_worker = worker
    main._task_kind = kind
    main._set_busy(True, message)
    connect = getattr(main, "_connect_active_worker", None)
    if callable(connect):
        connect()
    else:
        worker.signals.cancelled.connect(
            lambda payload: main._set_busy(False, "计算已取消"), Qt.QueuedConnection)
    main.thread_pool.start(worker)
    return worker
