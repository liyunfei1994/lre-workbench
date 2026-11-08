"""Qt-owned redraw timers for disposable Matplotlib charts.

Matplotlib's zero-delay singleShot callback can outlive a QWidget removed by
deleteLater. A child QTimer is cancelled with its canvas; the validity check
also covers an already queued callback. Other plotting exceptions keep their
normal error handling.
"""
from PySide6.QtCore import QTimer
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from shiboken6 import isValid


class SafeFigureCanvasQTAgg(FigureCanvasQTAgg):
    def __init__(self, figure=None):
        self._retired = False
        self._redraw_timer = None
        super().__init__(figure)

    def draw_idle(self):
        if self._retired or not isValid(self):
            return
        if self._draw_pending or self._is_drawing:
            return
        if self._redraw_timer is None:
            self._redraw_timer = QTimer(self)
            self._redraw_timer.setSingleShot(True)
            self._redraw_timer.timeout.connect(self._draw_idle)
        self._draw_pending = True
        self._redraw_timer.start(0)

    def _draw_idle(self):
        if self._retired or not isValid(self):
            self._draw_pending = False
            return
        super()._draw_idle()

    def retire(self):
        self._retired = True
        self._draw_pending = False
        if self._redraw_timer is not None and isValid(self._redraw_timer):
            self._redraw_timer.stop()


def retire_charts(widget):
    """Cancel redraws before deleting a card or an entire analysis panel."""
    canvases = widget.findChildren(SafeFigureCanvasQTAgg)
    if isinstance(widget, SafeFigureCanvasQTAgg):
        canvases.append(widget)
    for canvas in canvases:
        canvas.retire()
