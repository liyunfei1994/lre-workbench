"""
ui_drawings.panel —— 部件图纸面板（ComponentView / GeometryDrawingPanel）+ 3D 调度。
"""

from __future__ import annotations

from typing import Any, Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSizePolicy, QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from ._common import *  # noqa: F401,F403
from .engine3d import EngineThreeDView
from .nozzle import _fig_nozzle, _explain_nozzle, _build_nozzle_3d
from .chamber import _fig_chamber, _explain_chamber, _build_chamber_3d
from .injector import _fig_injector, _explain_injector
from .pumps import _fig_pumps, _explain_pumps
from .turbines import _fig_turbines, _explain_turbines
from .preburners import _fig_preburners, _explain_preburners


def build_component_3d(plotter, kind: str, geom: Dict[str, Any]):
    """在给定 plotter 上构建单部件 3D 模型。kind ∈ {'nozzle','chamber'}。"""
    plotter.clear()
    if kind == "nozzle":
        _build_nozzle_3d(plotter, geom)
    elif kind == "chamber":
        _build_chamber_3d(plotter, geom)
    plotter.set_background("white")
    plotter.add_axes(interactive=False, color="#374151")
    try:
        plotter.show_bounds(grid="back", location="outer",
                            xtitle="x [m]", ytitle="y [m]", ztitle="z [m]",
                            font_size=9, color="#374151")
    except Exception:
        pass
    plotter.reset_camera()
    try:
        plotter.camera.azimuth = -45
        plotter.camera.elevation = 18
    except Exception:
        pass


def export_contour_csv(geom: Dict[str, Any], kind: str, path: str,
                       mirror: bool = False) -> int:
    """导出喷管/燃烧室 *内壁* 坐标(供外部 CAD 旋转建模 / CFD 网格)。

    约定
    ────
      · 单位 米 [m];喉部位于 x = 0;r = 到轴线的半径(轴对称,绕 x 轴旋转即得三维)。
      · 喷管:完整燃气侧内壁(喷注面 → 喷管出口),列 x_m, r_m。
      · 燃烧室:喷注面 → 喉部,列 x_m, r_gas_m + 三层壁外缘半径(内壁/通道/外壳),
                便于在 CAD/CFD 里建再生冷却夹套(共轭传热)。

    参数
    ────
      path   : 输出文件;扩展名决定分隔符(.csv→逗号; .dat/.txt→空格)。
      mirror : 仅喷管有效。True 时输出 *闭合全轮廓*(上壁 +r 顺序 + 下壁 -r 逆序),
               用于二维平面网格或整截面 CAD。

    返回写入的坐标点数。
    """
    n = geom.get("nozzle", {}) or {}
    c = geom.get("chamber", {}) or {}
    xs = list(n.get("contour_x") or [])
    rs = list(n.get("contour_r") or [])
    if not xs or not rs:
        raise ValueError("缺少喷管轮廓数据,请先点击「计算几何」。")

    rc = float(c.get("radius_m") or 0.0)
    Lc = float(c.get("length_m") or 0.0)
    rt = float(n.get("throat_radius_m") or 0.0)
    re = float(n.get("exit_radius_m") or 0.0)
    eps = float(n.get("expansion_ratio") or 0.0)
    cr = float(n.get("contraction_ratio") or 0.0)
    Lconv = float(n.get("convergent_length_m") or 0.0)
    Lnoz = float(n.get("nozzle_length_m") or 0.0)
    bf = float(n.get("bell_fraction") or 0.80)
    ntype = str(n.get("nozzle_type") or "TOP")

    # 整段燃气侧内壁:收缩段起点前补一段燃烧室圆柱(到喷注面)
    x_inj = xs[0] - Lc
    full_x = [x_inj] + xs
    full_r = [rc] + rs

    # 注释行不用英文逗号(否则 Excel 把注释也按逗号拆列),改用 " | " / ";"。
    head = [
        "# FFSC 数字样机 — 内壁坐标导出 (engineering preliminary contour)",
        "# 单位 米 [m]; 喉部 x = 0; r = 到轴线半径 (轴对称; 绕 x 轴旋转成三维)",
        "# 用途 外部 CAD 旋转建模 / CFD 轴对称网格 / 后处理",
        f"# Dt = {2*rt:.6f} m | De = {2*re:.6f} m | Dc = {2*rc:.6f} m",
        f"# 喷管类型 = {ntype} (Thrust-Optimized Parabola) | bell 长度比 = {bf:.2f}",
        f"# 膨胀比 eps = {eps:.4f} | 收缩比 = {cr:.4f}",
        f"# L_chamber = {Lc:.6f} | L_conv = {Lconv:.6f} | L_nozzle = {Lnoz:.6f} | "
        f"L_total = {Lc + Lconv + Lnoz:.6f} (m)",
    ]

    ext = path.lower().rsplit(".", 1)[-1] if "." in path else "csv"
    delim = "," if ext == "csv" else " "

    lines = list(head)
    if kind == "chamber":
        ti = float(c.get("inner_wall_thickness_mm") or 0.0) / 1000.0
        tch = float(c.get("cooling_channel_height_mm") or 0.0) / 1000.0
        to = float(c.get("outer_wall_thickness_mm") or 0.0) / 1000.0
        lines.append(
            f"# 壁分层(径向外缘) 内壁 +{ti*1000:.2f}mm | 通道 +{tch*1000:.2f}mm | "
            f"外壳 +{to*1000:.2f}mm")
        cols = ["x_m", "r_gas_m", "r_inner_out_m", "r_channel_out_m", "r_outer_out_m"]
        lines.append("# " + delim.join(cols))
        count = 0
        for x, r in zip(full_x, full_r):
            if x > 1e-9:            # 燃烧室段只导到喉部
                break
            vals = [x, r, r + ti, r + ti + tch, r + ti + tch + to]
            lines.append(delim.join(f"{v:.6f}" for v in vals))
            count += 1
    else:
        pts = list(zip(full_x, full_r))
        if mirror:
            lines.append("# 闭合全轮廓 上壁(x +r) 顺序 + 下壁(x -r) 逆序")
            pts = pts + [(x, -r) for x, r in reversed(pts)]
        lines.append("# " + delim.join(["x_m", "r_m"]))
        for x, r in pts:
            lines.append(f"{x:.6f}{delim}{r:.6f}")
        count = len(pts)

    # .csv 用 utf-8-sig(带 BOM)→ Excel 正确识别中文;.dat/.txt 用纯 utf-8(利于 CFD 读取)
    encoding = "utf-8-sig" if ext == "csv" else "utf-8"
    with open(path, "w", encoding=encoding, newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return count


class ComponentView(QWidget):
    """单部件图纸视图：矢量 2D 画布 + 可选交互式 3D + 导出。

    相比旧版"PNG → QLabel 缩放":
      · 2D 用 FigureCanvasQTAgg,窗口放大时按当前像素 **重新矢量渲染**,永不模糊;
      · 导航工具栏(平移/缩放/复位)已移除——聚焦关键物理、精简界面;
      · 喷管/燃烧室额外提供 PyVista 交互式 3D(2D/3D 一键切换);
      · "导出本图"按钮:2D 存 PNG/PDF/SVG(300 DPI / 矢量),3D 存 PNG 或 SVG/PDF。
    """

    def __init__(self, kind: str, fig_fn, supports_3d: bool = False):
        super().__init__()
        self.kind = kind
        self.fig_fn = fig_fn
        self.supports_3d = bool(supports_3d and PYVISTA_AVAILABLE)
        self._fig = None
        self._canvas = None
        self._aspect_holder = None
        self._toolbar = None
        self._plotter = None
        self._geom: Dict[str, Any] | None = None
        self._3d_built = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 顶部控制条:2D/3D 切换 + 导出 ───────────────
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(6)
        if self.supports_3d:
            self.btn_2d = QToolButton(); self.btn_2d.setText("2D 剖面")
            self.btn_2d.setCheckable(True); self.btn_2d.setChecked(True)
            self.btn_3d = QToolButton(); self.btn_3d.setText("3D 模型")
            self.btn_3d.setCheckable(True)
            grp = QButtonGroup(self); grp.setExclusive(True)
            grp.addButton(self.btn_2d, 0); grp.addButton(self.btn_3d, 1)
            grp.idClicked.connect(self._on_mode)
            bar.addWidget(self.btn_2d); bar.addWidget(self.btn_3d)
        bar.addStretch(1)
        # 喷管/燃烧室: 导出内壁坐标(供外部 CAD/CFD)
        self.btn_coords = None
        if self.kind in ("nozzle", "chamber"):
            self.btn_coords = QPushButton("导出坐标 CSV…")
            self.btn_coords.setToolTip(
                "导出内壁坐标(轴对称, 喉部 x=0), 供外部 CAD 旋转建模 / CFD 网格")
            self.btn_coords.clicked.connect(self._export_coords)
            bar.addWidget(self.btn_coords)
        self.btn_export = QPushButton("导出本图…")
        self.btn_export.clicked.connect(self._export)
        bar.addWidget(self.btn_export)
        root.addLayout(bar)

        # ── 主显示区:堆叠 (2D / 3D) ────────────────────
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # 2D 页
        self.page2d = QWidget()
        self.l2d = QVBoxLayout(self.page2d)
        self.l2d.setContentsMargins(0, 0, 0, 0)
        self.l2d.setSpacing(0)
        self._placeholder = QLabel("等待几何计算...")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setMinimumHeight(300)
        self.l2d.addWidget(self._placeholder)
        self.stack.addWidget(self.page2d)

        # 3D 页(仅 nozzle/chamber 且 pyvista 可用)
        if self.supports_3d:
            self.page3d = QWidget()
            l3d = QVBoxLayout(self.page3d)
            l3d.setContentsMargins(0, 0, 0, 0)
            try:
                self._plotter = QtInteractor(self.page3d)
                self._plotter.set_background("white")
                l3d.addWidget(self._plotter.interactor)
                tip = QLabel("鼠标左键旋转 · 中键平移 · 滚轮缩放")
                tip.setStyleSheet("color:#6b7280; padding:2px 6px; font-size:11px;")
                l3d.addWidget(tip)
            except Exception:
                self._plotter = None
                l3d.addWidget(QLabel("PyVista 初始化失败,3D 不可用。"))
            self.stack.addWidget(self.page3d)

    # ── 数据更新 ───────────────────────────────────────
    def update_geometry(self, geom: Dict[str, Any]):
        self._geom = geom
        self._render_2d(geom)
        self._3d_built = False
        if self.supports_3d and self.stack.currentIndex() == 1:
            self._render_3d(geom)

    def _render_2d(self, geom: Dict[str, Any]):
        try:
            fig = self.fig_fn(geom)
        except Exception as exc:
            if self._placeholder is not None:
                self._placeholder.setText(f"{self.kind} 图纸生成失败：{exc}")
            return
        fig.set_facecolor("white")
        # 按 figure 设计尺寸取宽高比,容器据此 letterbox,避免等比例坐标被压扁
        try:
            w_in, h_in = fig.get_size_inches()
            aspect = float(w_in) / float(h_in)
        except Exception:
            aspect = 1.6
        # 拆掉旧的画布/工具条 / 占位符
        if self._placeholder is not None:
            self.l2d.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            self._placeholder = None
        if self._toolbar is not None:
            self.l2d.removeWidget(self._toolbar)
            self._toolbar.deleteLater()
            self._toolbar = None
        if self._aspect_holder is not None:
            self.l2d.removeWidget(self._aspect_holder)
            self._aspect_holder.deleteLater()
            self._aspect_holder = None
        # 新建矢量画布(保持宽高比 + 等比缩放字号)
        # 导航工具栏(复位/平移/框选放大/上一视图/下一视图)已移除——聚焦关键物理,精简界面。
        self._canvas = _ScalingCanvas(fig)
        self._aspect_holder = AspectCanvasWidget(self._canvas, aspect)
        self._toolbar = None
        self.l2d.addWidget(self._aspect_holder, 1)
        self._fig = fig

    def _on_mode(self, idx: int):
        self.stack.setCurrentIndex(idx)
        if idx == 1 and self.supports_3d and not self._3d_built and self._geom is not None:
            self._render_3d(self._geom)

    def _render_3d(self, geom: Dict[str, Any]):
        if self._plotter is None:
            return
        try:
            build_component_3d(self._plotter, self.kind, geom)
            self._3d_built = True
        except Exception as exc:
            try:
                self._plotter.clear()
                self._plotter.add_text(f"3D 渲染失败：{exc}", color="red", font_size=10)
            except Exception:
                pass

    def cleanup(self):
        """退出前收尾本部件的 PyVista 渲染窗口(避免 VTK 关闭报错)。"""
        if self._plotter is not None:
            try:
                self._plotter.close()
            except Exception:
                pass

    # ── 导出 ───────────────────────────────────────────
    def _export(self):
        is3d = self.supports_3d and self.stack.currentIndex() == 1 and self._plotter is not None
        if is3d:
            path, _ = QFileDialog.getSaveFileName(
                self, "导出 3D 图", f"{self.kind}_3d.png",
                "PNG 高清图 (*.png);;矢量图 (*.svg *.pdf *.eps)")
            if not path:
                return
            try:
                low = path.lower()
                if low.endswith((".svg", ".pdf", ".eps", ".ps")):
                    self._plotter.save_graphic(path)
                else:
                    self._plotter.screenshot(path, scale=3)
                QMessageBox.information(self, "导出成功", f"已保存：\n{path}")
            except Exception as exc:
                QMessageBox.warning(self, "导出失败", str(exc))
        else:
            if self._fig is None:
                QMessageBox.information(self, "导出", "请先点击「计算几何」生成图纸。")
                return
            path, _ = QFileDialog.getSaveFileName(
                self, "导出图片", f"{self.kind}.png",
                "PNG 高清图 (*.png);;PDF 矢量图 (*.pdf);;SVG 矢量图 (*.svg)")
            if not path:
                return
            try:
                self._fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
                QMessageBox.information(self, "导出成功", f"已保存：\n{path}")
            except Exception as exc:
                QMessageBox.warning(self, "导出失败", str(exc))

    def _export_coords(self):
        """导出内壁坐标 CSV(轴对称, 喉部 x=0), 供外部 CAD 旋转建模 / CFD 网格。"""
        if not self._geom:
            QMessageBox.information(self, "导出坐标", "请先点击「计算几何」生成几何。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出内壁坐标 CSV", f"{self.kind}_contour.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            count = export_contour_csv(self._geom, self.kind, path)
            QMessageBox.information(
                self, "导出成功",
                f"已写入 {count} 个坐标点（单位 m，喉部 x=0，轴对称）：\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))


def _explain_engine(geom: Dict[str, Any]) -> str:
    return (_EXPLAIN_CSS + "<body><p><b>整机三维 / Engine 3D</b>　喷管由轮廓 contour_x/contour_r "
            "真实旋转成型,燃烧室筒 + 喷注面板组合显示。鼠标左键旋转、滚轮缩放。</p>"
            "<p>各部件的尺寸是怎么估算的，切到对应标签页(喷管 / 主燃烧室 / 喷注器 / 泵 / 涡轮 / 预燃室)"
            "即可在本面板看到公式与依据。<b>喷管、主燃烧室标签页右上角可切到「3D 模型」</b>看单独的三维剖切。</p>"
            "<p class='src'>所有几何来自 <code>geometry.py: compute_geometry</code>，"
            "输入为循环求解结果 + 收缩比/L*/钟形比例等设计参数。</p></body>")

_EXPLAIN_FUNCS = {
    "engine": _explain_engine,
    "nozzle": _explain_nozzle,
    "chamber": _explain_chamber,
    "injector": _explain_injector,
    "pumps": _explain_pumps,
    "turbines": _explain_turbines,
    "preburners": _explain_preburners,
}

_TAB_KEYS = ["engine", "nozzle", "chamber", "injector", "pumps", "turbines", "preburners"]

class GeometryDrawingPanel(QWidget):
    """各组件几何图纸的多标签面板(矢量 2D + 交互式 3D + 估算说明 + 导出)。"""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._views: Dict[str, ComponentView] = {}
        self._engine_view: EngineThreeDView | None = None
        self._geom: Dict[str, Any] | None = None

        if not MATPLOTLIB_AVAILABLE:
            msg = QLabel(
                "未检测到 Matplotlib 或 NumPy。"
                "执行 `pip install matplotlib numpy` 后启用图纸显示。"
            )
            msg.setWordWrap(True)
            layout.addWidget(msg)
            return

        # 第 0 个 tab：整机 3D（PyVista 或 matplotlib fallback）
        self._engine_view = EngineThreeDView()
        self.tabs.addTab(self._engine_view, "整机三维 / Engine 3D")

        # 1..6：各部件。nozzle/chamber 支持 2D/3D 切换。顺序须与 GEOMETRY_TAB_FILTER_KEYS 一致。
        specs = [
            ("nozzle", "喷管 / Nozzle", _fig_nozzle, True),
            ("chamber", "主燃烧室 / Chamber", _fig_chamber, True),
            ("injector", "喷注器 / Injector", _fig_injector, False),
            ("pumps", "泵 / Pumps", _fig_pumps, False),
            ("turbines", "涡轮 / Turbines", _fig_turbines, False),
            ("preburners", "预燃室 / Preburners", _fig_preburners, False),
        ]
        for key, title, fn, threed in specs:
            view = ComponentView(key, fn, supports_3d=threed)
            self._views[key] = view
            self.tabs.addTab(view, title)

        # 底部：可折叠的"估算说明"面板,随当前标签页更新
        self.explain = CollapsibleBox("估算说明 / 公式与依据（点击展开，含实际数值）")
        layout.addWidget(self.explain)
        self.tabs.currentChanged.connect(self._refresh_explain)
        self._refresh_explain(self.tabs.currentIndex())

    def _refresh_explain(self, index: int):
        if not MATPLOTLIB_AVAILABLE or not hasattr(self, "explain"):
            return
        key = _TAB_KEYS[index] if 0 <= index < len(_TAB_KEYS) else "engine"
        fn = _EXPLAIN_FUNCS.get(key, _explain_engine)
        try:
            self.explain.set_html(fn(self._geom or {}))
        except Exception:
            self.explain.set_html("(说明生成失败)")

    def update_geometry(self, geom: Dict[str, Any]):
        if not MATPLOTLIB_AVAILABLE:
            return
        self._geom = geom

        if self._engine_view is not None:
            try:
                self._engine_view.update_geometry(geom)
            except Exception as exc:
                print(f"整机 3D 更新失败：{exc}")

        for key, view in self._views.items():
            try:
                view.update_geometry(geom)
            except Exception as exc:
                print(f"{key} 图纸更新失败：{exc}")

        # 说明面板代入最新数值
        self._refresh_explain(self.tabs.currentIndex())

    def cleanup(self):
        """退出前收尾所有 3D 渲染窗口(整机 + 喷管 + 燃烧室),避免 VTK 关闭报错刷屏。"""
        if self._engine_view is not None:
            try:
                self._engine_view.cleanup()
            except Exception:
                pass
        for view in self._views.values():
            try:
                view.cleanup()
            except Exception:
                pass