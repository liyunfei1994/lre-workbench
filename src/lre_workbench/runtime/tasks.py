"""Cooperative cancellation for numerical work in reusable worker threads.

Native library calls finish before the next checkpoint. No thread is forcibly
terminated, and cancellation cannot be mistaken for a failed physics result.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event


class TaskCancelled(BaseException):
    """A control-flow exit that deliberately bypasses physics fallback handlers."""


class CancellationToken:
    def __init__(self):
        self._event = Event()

    def cancel(self):
        self._event.set()

    @property
    def requested(self):
        return self._event.is_set()

    def checkpoint(self):
        if self.requested:
            raise TaskCancelled()


_current_token = ContextVar("lre_cancellation_token", default=None)


def checkpoint():
    """Check the active job; outside a job this leaves calculations unchanged."""
    token = _current_token.get()
    if token is not None:
        token.checkpoint()


@contextmanager
def cancellation_scope(token):
    """Install a token for this thread/task and always restore the previous one."""
    previous = _current_token.set(token)
    try:
        checkpoint()
        yield
    finally:
        _current_token.reset(previous)
