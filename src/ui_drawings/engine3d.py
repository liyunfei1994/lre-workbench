"""
ui_drawings.engine3d —— 整机三维视图（PyVista 交互 / matplotlib fallback）。
"""

from __future__ import annotations

from typing import Any, Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ._common import (
    MATPLOTLIB_AVAILABLE, PYVISTA_AVAILABLE, pv, QtInteractor, np,
    _num, _make_figure, _draw_revolved_surface, _style_3d, _set_3d_equal,
    AspectPixmapLabel, _update_label,
)


class EngineThreeDView(QWidget):
    """整机 3D 视图：优先 PyVistaQt 交互式，fallback 到 matplotlib PNG。"""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.use_pyvista = PYVISTA_AVAILABLE
        if self.use_pyvista:
            try:
                self.plotter = QtInteractor(self)
                layout.addWidget(self.plotter.interactor)
                self.plotter.set_background("white")
                self.plotter.add_text("等待几何计算...", font_size=10, color="gray")
                row = QHBoxLayout()
                self._info_label = QLabel(
                    "提示：鼠标左键旋转，中键平移，滚轮缩放；右键菜单可剖切/导出。"
                )
                self._info_label.setStyleSheet("color: #6b7280; padding: 4px 8px;")
                row.addWidget(self._info_label, 1)
                self._btn_export = QPushButton("导出 3D 图…")
                self._btn_export.clicked.connect(self._export_3d)
                row.addWidget(self._btn_export)
                layout.addLayout(row)
            except Exception:
                self.use_pyvista = False

        if not self.use_pyvista:
            self.label = AspectPixmapLabel("等待几何计算...")
            self.label.setMinimumHeight(300)
            layout.addWidget(self.label, 1)
            hint = QLabel(
                "提示：未检测到 pyvistaqt，已使用 matplotlib 渲染。"
                "安装 `pip install pyvistaqt pyvista` 可获得真实交互式 3D。"
            )
            hint.setStyleSheet("color: #b45309; padding: 4px 8px; font-size: 11px;")
            layout.addWidget(hint)

    def update_geometry(self, geom: Dict[str, Any]):
        if self.use_pyvista:
            self._update_pyvista(geom)
        else:
            self._update_matplotlib(geom)

    def cleanup(self):
        """退出前收尾 PyVista 渲染窗口,趁 GL 上下文仍有效让 VTK 正常清理,
        避免关闭时刷一堆 vtkWin32OpenGLRenderWindow wglMakeCurrent 报错。"""
        plotter = getattr(self, "plotter", None)
        if plotter is not None:
            try:
                plotter.close()
            except Exception:
                pass

    def _export_3d(self):
        """导出整机 3D：PNG 高清 或 SVG/PDF 矢量。"""
        plotter = getattr(self, "plotter", None)
        if plotter is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出整机 3D", "engine_3d.png",
            "PNG 高清图 (*.png);;矢量图 (*.svg *.pdf *.eps)")
        if not path:
            return
        try:
            if path.lower().endswith((".svg", ".pdf", ".eps", ".ps")):
                plotter.save_graphic(path)
            else:
                plotter.screenshot(path, scale=3)
            QMessageBox.information(self, "导出成功", f"已保存：\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    # ─── PyVista 路径 ──────────────────────────────────────
    def _update_pyvista(self, geom: Dict[str, Any]):
        try:
            self.plotter.clear()

            c = geom.get("chamber", {}) or {}
            n = geom.get("nozzle", {}) or {}
            inj = geom.get("injector", {}) or {}

            rc = _num(c.get("radius_m"), 0.20)
            l_ch = _num(c.get("length_m"), 0.40)
            xs = n.get("contour_x") or []
            rs = n.get("contour_r") or []
            l_conv = _num(n.get("convergent_length_m"), 0.17)
            rt = _num(n.get("throat_radius_m"), 0.12)
            re = _num(n.get("exit_radius_m"), 0.70)

            # ── 渲染环境：渐变背景 + 抗锯齿 + 多光源，营造金属质感 ──
            try:
                self.plotter.set_background("#d7e0ec", top="#fbfdff")
            except Exception:
                self.plotter.set_background("white")
            for _aa in ("ssaa", "fxaa"):
                try:
                    self.plotter.enable_anti_aliasing(_aa)
                    break
                except Exception:
                    continue
            try:
                self.plotter.enable_lightkit()      # 三点式打光，金属高光更自然
            except Exception:
                pass

            # 统一"金属"材质（Phong 高光，不依赖 PBR 环境贴图，跨版本稳定）
            metal = dict(smooth_shading=True, opacity=1.0, show_edges=False,
                         specular=0.6, specular_power=18, ambient=0.30, diffuse=0.62)

            # 1) 燃烧室筒（铜/青铜色，再生冷却室典型外观；不透明更逼真）
            chamber_mesh = pv.Cylinder(
                center=(l_ch / 2.0, 0, 0), direction=(1, 0, 0),
                radius=rc, height=l_ch, resolution=120, capping=False,
            )
            self.plotter.add_mesh(chamber_mesh, color="#c2703d",
                                  label="主燃烧室 Chamber", **metal)

            # 2) 喷管（实际 revolve from contour，钢/铬镍金属色）
            if xs and rs:
                xs_arr = np.asarray(xs, dtype=float) + l_ch  # 整体平移使 x=0 在喷注面
                rs_arr = np.asarray(rs, dtype=float)
                points = np.column_stack([xs_arr, rs_arr, np.zeros_like(xs_arr)])
                poly = pv.PolyData(points, lines=np.hstack([[len(points)],
                                                            np.arange(len(points))]))
                # extrude_rotate 默认绕 z 轴；这里指定绕 x 轴
                surf = poly.extrude_rotate(resolution=120, capping=False,
                                           rotation_axis=(1, 0, 0))
                self.plotter.add_mesh(surf, color="#9aa6b6",
                                      label="喷管 Nozzle", **metal)
            else:
                # 退化：圆锥
                nozzle_mesh = pv.Cone(
                    center=(l_ch + l_conv + (l_ch * 0.5), 0, 0),
                    direction=(-1, 0, 0), radius=re, height=l_ch * 1.2, resolution=120,
                )
                self.plotter.add_mesh(nozzle_mesh, color="#9aa6b6",
                                      label="喷管 Nozzle", **metal)

            # 3) 再生冷却通道肋（沿轮廓的纵向凸起）——火箭喷管标志性外观
            try:
                if xs and rs and len(xs) >= 2:
                    cx = np.asarray(xs, dtype=float) + l_ch
                    cr = np.asarray(rs, dtype=float)
                    if len(cx) > 60:                       # 下采样，避免肋过密
                        step = max(1, len(cx) // 60)
                        cx = cx[::step]; cr = cr[::step]
                    n_ribs = 60
                    pts_list, conn_list, off = [], [], 0
                    for k in range(n_ribs):
                        ang = 2.0 * np.pi * k / n_ribs
                        ca, sa = np.cos(ang), np.sin(ang)
                        # 略微外凸（×1.004），让肋浮在壁面之上而非嵌入
                        pts = np.column_stack([cx, cr * ca * 1.004, cr * sa * 1.004])
                        m = len(pts)
                        pts_list.append(pts)
                        conn_list.append(np.hstack([[m], np.arange(off, off + m)]))
                        off += m
                    ribs = pv.PolyData(np.vstack(pts_list), lines=np.hstack(conn_list))
                    ribs = ribs.tube(radius=max(rt * 0.03, 0.0025), n_sides=8)
                    self.plotter.add_mesh(ribs, color="#5c6675", smooth_shading=True,
                                          specular=0.5, specular_power=14, opacity=1.0)
            except Exception:
                pass

            # 4) 动力头（Raptor 风格）：穹顶喷注器 + 双预燃室 + 双涡轮泵 + 集成歧管
            try:
                self._add_raptor_powerhead(rc, l_ch, geom)
            except Exception:
                # 兜底：退化为一个简单实心喷注头，保证顶部始终封口
                try:
                    inj_d = _num(inj.get("face_diameter_m"), 2 * rc)
                    h_inj = max(0.03, l_ch * 0.14)
                    inj_head = pv.Cylinder(
                        center=(-h_inj / 2.0, 0, 0), direction=(1, 0, 0),
                        radius=inj_d / 2.0 * 1.03, height=h_inj,
                        resolution=120, capping=True,
                    )
                    self.plotter.add_mesh(inj_head, color="#cda33c", smooth_shading=True,
                                          specular=0.55, specular_power=16, opacity=1.0)
                except Exception:
                    pass

            # 5) 坐标轴和刻度尺
            self.plotter.add_axes(interactive=False, color="#374151")
            self.plotter.show_bounds(
                grid="back", location="outer",
                xtitle="x [m]", ytitle="y [m]", ztitle="z [m]",
                font_size=10, color="#374151",
            )

            # 6) 标注
            de_text = (
                f"L_total ≈ {(l_ch + l_conv + _num(n.get('nozzle_length_m'), 0)):.3f} m\n"
                f"D_chamber = {2*rc:.3f} m, D_throat = {2*rt:.3f} m, D_exit = {2*re:.3f} m"
            )
            self.plotter.add_text(de_text, position="upper_left", font_size=9,
                                  color="#1f2937")

            self.plotter.add_legend(face="rectangle", size=(0.16, 0.10))
            self.plotter.reset_camera()
            self.plotter.camera.azimuth = -45
            self.plotter.camera.elevation = 20
        except Exception as exc:
            try:
                self.plotter.add_text(f"PyVista 渲染失败：{exc}", font_size=10, color="red")
            except Exception:
                pass

    def _add_raptor_powerhead(self, rc, l_ch, geom):
        """在燃烧室顶部搭建 Raptor 风格动力头：
        穹顶主喷注器 + 双预燃室 + 双涡轮泵 + 集成歧管/热气导管。
        组件按燃烧室半径 rc 成比例布置（参考 geom 中预燃室/涡轮的真实尺寸比例，
        并夹紧到合理区间），整洁且不依赖精确 CAD。任一步失败由上层 try/except 兜底。
        """
        p = self.plotter
        L = max(l_ch, 2.0 * rc)

        def clamp(v, lo, hi):
            return lo if v < lo else (hi if v > hi else v)

        # 材质（钢 / 暗钢 / 黄铜），与主体金属风格统一
        steel = dict(smooth_shading=True, opacity=1.0, specular=0.62,
                     specular_power=20, ambient=0.30, diffuse=0.60)
        dark = dict(smooth_shading=True, opacity=1.0, specular=0.55,
                    specular_power=16, ambient=0.26, diffuse=0.58)
        brass = dict(smooth_shading=True, opacity=1.0, specular=0.55,
                     specular_power=18, ambient=0.30, diffuse=0.62)
        C_DOME, C_STEEL, C_DARK, C_BRASS = "#b9c2cf", "#aab3c0", "#5f6a78", "#cda33c"

        def cyl(x_c, ang, r_off, r, h, color, mat, cap=True):
            cy, cz = r_off * np.cos(ang), r_off * np.sin(ang)
            m = pv.Cylinder(center=(x_c, cy, cz), direction=(1, 0, 0),
                            radius=r, height=h, resolution=60, capping=cap)
            p.add_mesh(m, color=color, **mat)

        def duct(p0, p1, r, color, mat):
            line = pv.PolyData(np.asarray([p0, p1], dtype=float),
                               lines=np.asarray([2, 0, 1]))
            p.add_mesh(line.tube(radius=r, n_sides=16), color=color, **mat)

        def at(ang, r_off):
            return (r_off * np.cos(ang), r_off * np.sin(ang))

        # 0) 平喷注面（封住燃烧室顶，防止穿透）
        face = pv.Disc(center=(0, 0, 0), inner=0.0, outer=rc,
                       normal=(1, 0, 0), r_res=2, c_res=120)
        p.add_mesh(face, color=C_DOME, **steel)

        # 1) 穹顶主喷注器（torispherical dome）
        h_dome = 0.55 * rc
        phi = np.linspace(0.0, np.pi / 2.0, 28)
        dome_pts = np.column_stack([-h_dome * np.sin(phi), rc * np.cos(phi),
                                    np.zeros_like(phi)])
        dome_poly = pv.PolyData(dome_pts,
                                lines=np.hstack([[len(dome_pts)],
                                                 np.arange(len(dome_pts))]))
        dome = dome_poly.extrude_rotate(resolution=120, capping=False,
                                        rotation_axis=(1, 0, 0))
        p.add_mesh(dome, color=C_DOME, label="动力头 Powerhead", **steel)

        # 2) 推进剂/喷注歧管环（顶部外圈环带）
        collar = pv.Cylinder(center=(-0.02 * L, 0, 0), direction=(1, 0, 0),
                             radius=rc * 1.07, height=0.10 * L,
                             resolution=120, capping=False)
        p.add_mesh(collar, color=C_STEEL, **steel)

        r_off = rc * 0.95

        # 3) 双预燃室（±y）——参考 geom 真实尺寸比例，夹紧到整洁区间
        pb = geom.get("ox_preburner", {}) or {}
        d_pb = _num(pb.get("diameter_m"), 0.6 * rc)
        l_pb_real = _num(pb.get("length_m"), 1.4 * rc)
        r_pb = clamp(0.5 * d_pb, 0.24 * rc, 0.40 * rc)
        l_pb = clamp(l_pb_real, 1.1 * rc, 2.2 * rc)
        x_pb_bot = -h_dome * 0.5
        x_pb_cen = x_pb_bot - l_pb / 2.0
        x_pb_top = x_pb_bot - l_pb
        for ang in (0.0, np.pi):
            cyl(x_pb_cen, ang, r_off, r_pb, l_pb, C_DARK, dark)
            cyl(x_pb_top - 0.04 * rc, ang, r_off, r_pb * 1.04, 0.08 * rc, C_BRASS, brass)  # 顶盖法兰
            y0, z0 = at(ang, r_off)
            y1, z1 = at(ang, rc * 0.45)
            duct((x_pb_bot, y0, z0), (-h_dome * 0.15, y1, z1), r_pb * 0.5, C_STEEL, steel)  # 热气下导管

        # 4) 双涡轮泵（±z）——涡轮机匣（暗钢）+ 泵体（钢）+ 进口法兰（黄铜）叠置
        tb = geom.get("ox_turbine", {}) or {}
        d_rot = _num(tb.get("rotor_diameter_m"), 0.8 * rc)
        r_turb = clamp(0.5 * d_rot, 0.34 * rc, 0.48 * rc)
        for ang in (np.pi / 2.0, -np.pi / 2.0):
            x_turb = x_pb_bot - 0.45 * rc
            cyl(x_turb, ang, r_off, r_turb, 0.55 * rc, C_DARK, dark)            # 涡轮机匣
            x_pump = x_turb - 0.55 * rc
            cyl(x_pump, ang, r_off, r_turb * 0.82, 0.75 * rc, C_STEEL, steel)   # 泵体
            cyl(x_pump - 0.45 * rc, ang, r_off, r_turb * 0.5, 0.10 * rc, C_BRASS, brass)  # 进口法兰
            y0, z0 = at(ang, r_off)
            y1, z1 = at(ang, rc * 1.0)
            duct((x_turb + 0.28 * rc, y0, z0), (-0.02 * L, y1, z1), 0.16 * rc, C_STEEL, steel)

        # 5) 顶部中心 gimbal/进料凸台
        cyl(-h_dome - 0.06 * rc, 0.0, 0.0, 0.16 * rc, 0.12 * rc, C_BRASS, brass)

    # ─── Matplotlib fallback ───────────────────────────────
    def _update_matplotlib(self, geom: Dict[str, Any]):
        try:
            fig = _make_figure(8.6, 5.2)
            ax = fig.add_subplot(111, projection="3d")
            c = geom.get("chamber", {}) or {}
            n = geom.get("nozzle", {}) or {}
            rc = _num(c.get("radius_m"), 0.20)
            l_ch = _num(c.get("length_m"), 0.40)
            xs = n.get("contour_x") or []
            rs = n.get("contour_r") or []

            # 燃烧室筒
            theta = np.linspace(0, 2 * np.pi, 60)
            x_ch = np.linspace(0, l_ch, 12)
            X, T = np.meshgrid(x_ch, theta)
            Y = rc * np.cos(T); Z = rc * np.sin(T)
            ax.plot_surface(X, Y, Z, color="#fdba74", alpha=0.7, linewidth=0, shade=True)

            if xs and rs:
                xs_arr = np.asarray(xs) + l_ch
                _draw_revolved_surface(ax, xs_arr, rs, color="#a5b4fc", alpha=0.55)

            _style_3d(ax, "整机三维示意 / Engine 3D（matplotlib fallback）")
            _set_3d_equal(ax, 1.05)
            _update_label(self.label, fig)
        except Exception as exc:
            self.label.setText(f"整机 3D 渲染失败：{exc}")