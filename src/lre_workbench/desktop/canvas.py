"""Code-native illustration: a schematic, never a computed engineering drawing."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class EngineCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 270)
        self.setSizePolicy(self.sizePolicy().Policy.Expanding, self.sizePolicy().Policy.Expanding)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#f7f9fb"))
        p.setPen(QPen(QColor("#e9eef2"), 1))
        for x in range(0, self.width(), 24):
            p.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 24):
            p.drawLine(0, y, self.width(), y)
        p.save()
        scale = min(self.width() / 400, self.height() / 400)
        p.translate(self.width() / 2, self.height() / 2 - 8)
        p.scale(scale, scale)
        p.setPen(QPen(QColor("#becbd4"), 1, Qt.DashLine))
        p.drawLine(0, -140, 0, 160)
        # Main chamber, throat and bell section.
        path = QPainterPath(QPointF(-46, -95))
        path.lineTo(46, -95)
        path.lineTo(46, -14)
        path.cubicTo(46, 12, 21, 25, 21, 45)
        path.cubicTo(22, 77, 58, 110, 100, 133)
        path.lineTo(-100, 133)
        path.cubicTo(-58, 110, -22, 77, -21, 45)
        path.cubicTo(-21, 25, -46, 12, -46, -14)
        path.closeSubpath()
        p.setBrush(QColor("#e8eef3"))
        p.setPen(QPen(QColor("#526d80"), 2))
        p.drawPath(path)
        p.setPen(QPen(QColor("#d99940"), 2))
        p.drawLine(-46, -95, 46, -95)
        p.drawLine(-21, 45, 21, 45)
        p.setPen(QPen(QColor("#7bacc2"), 3))
        p.drawPolyline([QPointF(-46, -66), QPointF(-92, -66), QPointF(-92, -120), QPointF(-126, -120)])
        p.setPen(QPen(QColor("#d7a872"), 3))
        p.drawPolyline([QPointF(46, -66), QPointF(92, -66), QPointF(92, -120), QPointF(126, -120)])
        for x, color in [(-122, "#7bacc2"), (122, "#d7a872")]:
            p.setBrush(QColor("#ffffff"))
            p.setPen(QPen(QColor(color), 2))
            p.drawEllipse(QPointF(x, -120), 15, 15)
            p.drawLine(x - 7, -126, x + 7, -114)
            p.drawLine(x - 7, -114, x + 7, -126)
        p.setPen(QColor("#718695"))
        p.setFont(QFont(p.font().family(), 9))
        p.drawText(QRectF(-150, -161, 70, 22), Qt.AlignCenter, "LOX")
        p.drawText(QRectF(80, -161, 70, 22), Qt.AlignCenter, "CH4")
        p.drawText(QRectF(-46, -46, 92, 24), Qt.AlignCenter, "CHAMBER")
        p.drawText(QRectF(-60, 95, 120, 24), Qt.AlignCenter, "NOZZLE")
        p.restore()
        p.setPen(QColor("#8b98a3"))
        p.setFont(QFont(p.font().family(), 9))
        p.drawText(QRectF(10, self.height() - 30, self.width() - 20, 20), Qt.AlignCenter, "发动机示意 · 非计算几何")
