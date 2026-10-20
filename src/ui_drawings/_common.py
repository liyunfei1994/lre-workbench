"""
ui_drawings._common —— 共享绘图基础设施（从 ui_drawings.py 拆出，内容逐字保留）

包含：matplotlib/pyvista 守卫导入、自适应控件、工程图样式助手、3D 旋转助手、
中文导航工具条、保持宽高比画布、可折叠说明面板等。各部件子模块 `from ._common import *`。
"""

from __future__ import annotations

import io
import math
from typing import Any, Dict, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QFileDialog, QGraphicsView, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTabWidget,
    QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

try:
    import matplotlib
    matplotlib.use("QtAgg", force=True)
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.patches import (
        Circle, Rectangle, Polygon, FancyArrowPatch, FancyArrow, Wedge, Arc,
    )
    from matplotlib.lines import Line2D
    from matplotlib.backends.backend_qtagg import (
        NavigationToolbar2QT,
    )
    from qt_chart_canvas import SafeFigureCanvasQTAgg as FigureCanvasQTAgg
    MATPLOTLIB_AVAILABLE = True
except Exception:
    MATPLOTLIB_AVAILABLE = False
    np = None
    Figure = None
    FigureCanvasQTAgg = None
    NavigationToolbar2QT = None

try:
    import pyvista as pv
    from pyvistaqt import QtInteractor
    PYVISTA_AVAILABLE = True
except Exception:
    PYVISTA_AVAILABLE = False
    pv = None
    QtInteractor = None


class AutoFitGraphicsView(QGraphicsView):
    """QGraphicsView,初次显示自动 fit;支持滚轮缩放、拖动平移、双击复位。

    交互
    ────
        · 滚轮上下: 围绕鼠标位置缩放(出厂默认每格 ±15%)
        · 鼠标拖动: 平移视图(ScrollHandDrag)
        · 双击空白: 复位回 fit_scene(完整显示场景)
        · 窗口缩放: 仅在用户未手动缩放时自动 fit;一旦滚轮缩放过,保留当前视角

    设计说明
    ────────
        QGraphicsView.scale() 会乘到当前变换矩阵上。为了避免过度放大/缩小,
        把缩放因子裁剪到 0.1× ~ 12× 之间(经验值,覆盖 80 倍范围)。
    """

    _MIN_SCALE = 0.1
    _MAX_SCALE = 12.0
    _WHEEL_STEP = 1.15   # 每格滚轮缩放系数

    def __init__(self, scene=None, parent=None):
        super().__init__(scene, parent) if scene is not None else super().__init__(parent)
        self._user_zoomed = False
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # 缩放围绕鼠标位置 → 自然交互
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._user_zoomed:
            self.fit_scene()

    def fit_scene(self):
        sc = self.scene()
        if sc is not None and not sc.sceneRect().isNull():
            self.resetTransform()  # 清掉 user zoom,再 fit
            self.fitInView(sc.sceneRect(), Qt.KeepAspectRatio)
            self._user_zoomed = False

    def wheelEvent(self, event):
        angle = event.angleDelta().y()
        if angle == 0:
            return super().wheelEvent(event)
        factor = self._WHEEL_STEP if angle > 0 else 1.0 / self._WHEEL_STEP
        # 当前总缩放系数(取 X 方向,X/Y 一致)
        cur_scale = self.transform().m11()
        new_scale = cur_scale * factor
        if new_scale < self._MIN_SCALE or new_scale > self._MAX_SCALE:
            event.accept()
            return
        self.scale(factor, factor)
        self._user_zoomed = True
        event.accept()

    def mouseDoubleClickEvent(self, event):
        # 双击复位:重新 fit 整个场景
        self.fit_scene()
        super().mouseDoubleClickEvent(event)

def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default

_FIG_DPI = 150

def _make_figure(width: float = 8.4, height: float = 4.4) -> Figure:
    return Figure(figsize=(width, height), dpi=_FIG_DPI, constrained_layout=True)

def _fig_to_pixmap(fig: Figure) -> QPixmap:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=_FIG_DPI, facecolor="white")
    buf.seek(0)
    pix = QPixmap()
    pix.loadFromData(buf.getvalue(), "PNG")
    return pix

class AspectPixmapLabel(QLabel):
    """QLabel 子类:把 pixmap 自适应缩放到当前控件尺寸,同时保留宽高比。

    旧 QLabel.setPixmap 以原始像素显示,在 splitter 控件被拖动放大/缩小时,
    图要么被裁切要么留大片白边。此类:
      · set_source_pixmap(pix) 保存源图
      · resizeEvent 时按当前 size 重新平滑缩放(KeepAspectRatio + SmoothTransformation)
      · setSizePolicy(Ignored, Ignored) 让 QLabel.sizeHint 不再强迫布局保持原图尺寸
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._source_pixmap: QPixmap | None = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(80, 60)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setScaledContents(False)  # 自己控制缩放

    def set_source_pixmap(self, pix: QPixmap):
        self._source_pixmap = pix
        self._rescale()

    def clear_source(self):
        self._source_pixmap = None
        self.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self):
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        target = self.size()
        if target.width() < 8 or target.height() < 8:
            return
        scaled = self._source_pixmap.scaled(
            target, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        super().setPixmap(scaled)

def _update_label(label: QLabel, fig: Figure):
    """渲染 figure 到 label。若是 AspectPixmapLabel,保留源图供 resize 缩放。"""
    pix = _fig_to_pixmap(fig)
    if isinstance(label, AspectPixmapLabel):
        label.set_source_pixmap(pix)
    else:
        label.setPixmap(pix)
        label.setAlignment(Qt.AlignCenter)

DIM_COLOR = "#444444"

HATCH_INNER = "//"

HATCH_OUTER = "\\\\"

CHAMBER_FILL = "#fff7ed"

NOZZLE_FILL = "#eef2ff"

INNER_WALL = "#fcd34d"

OUTER_WALL = "#94a3b8"

INJECTOR_FILL = "#fde68a"

COOLING_FILL = "#bae6fd"

def _dim_arrow(ax, x1, y1, x2, y2, text, offset=0.06, fontsize=8,
               text_x_offset=0.0, text_y_offset=0.0):
    """工程化尺寸标注:双向箭头 + 延伸线 + 数值。

    V7.1:文字背景改为不透明白底,完全遮挡身后的轴刻度/网格,
    避免 Dc / Dt 在靠近 y 轴时跟 "0.0" tick 视觉重叠。
    """
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="<->", color=DIM_COLOR, lw=0.8),
                zorder=4)
    midx = 0.5 * (x1 + x2) + text_x_offset
    midy = 0.5 * (y1 + y2) + text_y_offset
    ax.text(midx, midy, text, fontsize=fontsize, ha="center", va="center",
            color=DIM_COLOR, zorder=5,
            bbox=dict(boxstyle="round,pad=0.22", fc="white",
                      ec="#e5e7eb", lw=0.4, alpha=1.0))

def _dim_extension(ax, x_ref, y_ref, x_to, y_to):
    """从工件外画一根细虚线到尺寸线。"""
    ax.plot([x_ref, x_to], [y_ref, y_to], color=DIM_COLOR, lw=0.5, ls=":")

def _engineering_style(ax, title: str, xlabel: str = "", ylabel: str = ""):
    ax.set_title(title, fontsize=11, fontweight="bold", color="#1f2937", pad=10)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(True, alpha=0.18, ls="--")
    ax.set_aspect("equal", adjustable="box")
    for sp in ax.spines.values():
        sp.set_color("#94a3b8")

def _draw_revolved_surface(ax, xs, rs, color="#cbd5e1", alpha=0.55):
    """从 (x, r) 轮廓 revolve 出一个 3D 表面。"""
    xs = np.asarray(xs); rs = np.asarray(rs)
    theta = np.linspace(0, 2 * np.pi, 60)
    X, T = np.meshgrid(xs, theta)
    R = np.tile(rs, (len(theta), 1))
    Y = R * np.cos(T)
    Z = R * np.sin(T)
    ax.plot_surface(X, Y, Z, color=color, alpha=alpha, linewidth=0, antialiased=True, shade=True)

def _style_3d(ax, title: str):
    ax.set_title(title, fontsize=11, fontweight="bold", color="#1f2937", pad=8)
    ax.set_xlabel("x / m", labelpad=4, fontsize=8)
    ax.set_ylabel("y / m", labelpad=4, fontsize=8)
    ax.set_zlabel("z / m", labelpad=4, fontsize=8)
    ax.view_init(elev=18, azim=-58)
    ax.tick_params(labelsize=7)
    try:
        ax.xaxis.pane.set_alpha(0.0)
        ax.yaxis.pane.set_alpha(0.0)
        ax.zaxis.pane.set_alpha(0.0)
        ax.xaxis._axinfo["grid"]["color"] = "#e5e7eb"
        ax.yaxis._axinfo["grid"]["color"] = "#e5e7eb"
        ax.zaxis._axinfo["grid"]["color"] = "#e5e7eb"
    except Exception:
        pass

def _set_3d_equal(ax, scale=1.0):
    try:
        xs, ys, zs = ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d()
        xmid = sum(xs)/2; ymid = sum(ys)/2; zmid = sum(zs)/2
        r = max(abs(xs[1]-xs[0]), abs(ys[1]-ys[0]), abs(zs[1]-zs[0])) / 2 * scale
        ax.set_xlim3d(xmid - r, xmid + r)
        ax.set_ylim3d(ymid - r, ymid + r)
        ax.set_zlim3d(zmid - r, zmid + r)
    except Exception:
        pass

def _pv_revolve(xs, rs, resolution: int = 160, capping: bool = False):
    """把 (x, r) 轮廓绕 x 轴旋转成 3D 表面 (PolyData)。"""
    xs = np.asarray(xs, dtype=float)
    rs = np.asarray(rs, dtype=float)
    pts = np.column_stack([xs, rs, np.zeros_like(xs)])
    poly = pv.PolyData(pts, lines=np.hstack([[len(pts)], np.arange(len(pts))]))
    return poly.extrude_rotate(resolution=resolution, capping=capping,
                               rotation_axis=(1, 0, 0))

def _pv_half(mesh):
    """沿 z=0 平面剖开,保留一半 (剖切视图);失败则返回原网格。"""
    try:
        return mesh.clip(normal=(0, 0, 1), origin=(0, 0, 0))
    except Exception:
        return mesh

if MATPLOTLIB_AVAILABLE:
    class ChineseNavToolbar(NavigationToolbar2QT):
        """精简版中文导航工具条。

        只保留真正有用的几项,并去掉两个会弹出 **英文对话框** 的按钮
        (子图边距设置、Figure options 曲线/坐标编辑器)——它们对看图没帮助、
        还容易误操作。图片保存统一走右上角的「导出本图…」(支持 300 DPI / 矢量)。
        """
        toolitems = (
            ("复位", "回到初始完整视图", "home", "home"),
            ("上一视图", "回到上一次的缩放/平移视角", "back", "back"),
            ("下一视图", "前进到下一次视角", "forward", "forward"),
            (None, None, None, None),
            ("平移", "按住左键拖动平移；右键拖动可缩放", "move", "pan"),
            ("框选放大", "拖出一个矩形框,放大该区域", "zoom_to_rect", "zoom"),
        )

        def __init__(self, canvas, parent=None):
            super().__init__(canvas, parent)
            self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.setIconSize(self.iconSize())

    class AspectCanvasWidget(QWidget):
        """承载 FigureCanvasQTAgg,并 **保持图纸宽高比** 居中显示(letterbox)。

        为什么需要它
        ────────────
        工程剖面图用了等比例坐标轴(set_aspect='equal'),如果直接让画布填满一个
        宽高比不匹配的区域,图就会被压成一条窄带、固定字号的尺寸标注全堆在一起。
        本容器按 figure 的设计宽高比给画布定尺寸、居中,空白处留白:
          · 画布仍是矢量重渲染 → 清晰;
          · 比例不变 → 标注不再重叠、不变形(效果等同原 PNG,但不糊)。
        """

        def __init__(self, canvas, aspect: float, parent=None):
            super().__init__(parent)
            self._canvas = canvas
            self._aspect = aspect if aspect and aspect > 0 else 1.6
            canvas.setParent(self)
            self.setMinimumHeight(300)
            self.setStyleSheet("background: white;")

        def _relayout(self):
            W, H = self.width(), self.height()
            if W <= 1 or H <= 1:
                return
            if W / H > self._aspect:        # 容器偏宽 → 以高为准
                h = H; w = int(round(h * self._aspect))
            else:                           # 容器偏高 → 以宽为准
                w = W; h = int(round(w / self._aspect))
            self._canvas.setGeometry((W - w) // 2, (H - h) // 2, w, h)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._relayout()

        def showEvent(self, event):
            super().showEvent(event)
            self._relayout()

    class _ScalingCanvas(FigureCanvasQTAgg):
        """缩放时同步调整 DPI、保持 figure 英寸尺寸不变(关键修复)。

        问题
        ────
        matplotlib 字号是固定的「点(pt)」。普通 FigureCanvasQTAgg 缩小时保持 DPI、
        缩小 figure 的英寸尺寸 → 固定点字号相对变大 → 缩小后尺寸标注重叠。

        解法
        ────
        反过来:保持 figure 英寸尺寸不变,缩小时降低 DPI →
        字号(pt→像素)与图形一起 **等比缩放** → 任意大小都不重叠,且始终矢量清晰。
        配合 AspectCanvasWidget(画布宽高比 == figure 宽高比),保证 X/Y 缩放一致。
        """

        def __init__(self, fig):
            try:
                w_in, _h_in = (float(v) for v in fig.get_size_inches())
            except Exception:
                w_in = 8.0
            self._ref_w_in = w_in if w_in > 0 else 8.0
            super().__init__(fig)

        def resizeEvent(self, event):
            try:
                ratio = float(getattr(self, "device_pixel_ratio", 1.0) or 1.0)
                w_px = event.size().width() * ratio
                if w_px > 0 and self._ref_w_in > 0:
                    # DPI 由宽度定;AspectCanvasWidget 保证高度同比 → 英寸尺寸不变
                    self.figure.set_dpi(max(20.0, w_px / self._ref_w_in))
            except Exception:
                pass
            super().resizeEvent(event)
else:  # pragma: no cover
    ChineseNavToolbar = None
    AspectCanvasWidget = None
    _ScalingCanvas = None


class CollapsibleBox(QWidget):
    """点击标题展开/收起的说明区(内含 QTextBrowser 富文本)。"""

    def __init__(self, title: str):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.RightArrow)
        self.toggle.setStyleSheet(
            "QToolButton{border:none; font-weight:bold; padding:6px 4px; color:#1f2937;}"
            "QToolButton:hover{color:#2563eb;}"
        )
        self.toggle.clicked.connect(self._on_toggle)
        v.addWidget(self.toggle)
        self.content = QTextBrowser()
        self.content.setOpenExternalLinks(True)
        self.content.setVisible(False)
        self.content.setMinimumHeight(240)
        v.addWidget(self.content)

    def _on_toggle(self, checked: bool):
        self.toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.content.setVisible(checked)

    def set_html(self, html: str):
        self.content.setHtml(html)

_EXPLAIN_CSS = (
    "<style>"
    "body{font-family:'Microsoft YaHei',sans-serif; font-size:9pt; color:#1f2937;}"
    "table{border-collapse:collapse; margin:4px 0;}"
    "th{background:#e2e8f0; padding:4px 8px; text-align:left;}"
    "td{padding:3px 8px; border-bottom:1px solid #e5e7eb;}"
    "code{background:#f1f5f9; color:#0f172a; padding:0 3px;}"
    ".src{color:#64748b; font-size:8.5pt;}"
    ".warn{background:#fef3c7; padding:6px 8px; border-left:3px solid #f59e0b;}"
    "</style>"
)

def _row(name, value, formula, meaning):
    return (f"<tr><td><b>{name}</b></td><td>{value}</td>"
            f"<td><code>{formula}</code></td><td>{meaning}</td></tr>")

__all__ = [
    "np", "Figure", "Circle", "Rectangle", "Polygon", "FancyArrowPatch",
    "FancyArrow", "Wedge", "Arc", "Line2D", "FigureCanvasQTAgg",
    "NavigationToolbar2QT", "MATPLOTLIB_AVAILABLE", "PYVISTA_AVAILABLE",
    "pv", "QtInteractor", "Qt", "QPixmap",
    "QButtonGroup", "QFileDialog", "QGraphicsView", "QHBoxLayout", "QLabel",
    "QMessageBox", "QPushButton", "QScrollArea", "QSizePolicy", "QStackedWidget",
    "QTabWidget", "QTextBrowser", "QToolButton", "QVBoxLayout", "QWidget",
    "AutoFitGraphicsView", "_num", "_FIG_DPI", "_make_figure", "_fig_to_pixmap",
    "AspectPixmapLabel", "_update_label",
    "DIM_COLOR", "HATCH_INNER", "HATCH_OUTER", "CHAMBER_FILL", "NOZZLE_FILL",
    "INNER_WALL", "OUTER_WALL", "INJECTOR_FILL", "COOLING_FILL",
    "_dim_arrow", "_dim_extension", "_engineering_style",
    "_draw_revolved_surface", "_style_3d", "_set_3d_equal",
    "_pv_revolve", "_pv_half",
    "ChineseNavToolbar", "AspectCanvasWidget", "_ScalingCanvas", "CollapsibleBox",
    "_EXPLAIN_CSS", "_row",
]
