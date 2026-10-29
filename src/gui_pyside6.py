"""
gui_pyside6.py

FFSC 全流量液氧甲烷火箭发动机数字样机 —— PySide6 单机版界面 V5.3.2

V5.3.2 改动（2026-05）：
─────────────────────────────────────────────────────────
1. 单位制优化：
   · 推力输入/显示改为 kN（内部仍以 N 计算）。
   · 压力输入/显示改为 MPa（内部仍以 Pa 计算）。
   · 再生冷却吸热量改为 MW 显示。
   · DISPLAY_UNIT_MAP 统一管理所有显示单位换算。
2. 参数标签修正：
   · "预燃室压比" → "预燃室/主燃室压力比（≈涡轮压比）"，消除歧义。
3. KEY_LABELS 补全：
   · 新增 cooling、pump、nozzle 缺失的中文标签，确保部件结果全部
     显示"中文名称 / English"格式，不再出现裸英文 key。
4. 字体大小可调：
   · 左侧面板顶部新增"字体大小"下拉框（小/中/大/特大）。
   · 所有 QSS 样式随选项实时更新。
5. 流程拓扑图重设计（Raptor 2 风格）：
   · 彩色填充区域区分流体类型。
   · 各节点显示实际计算压力（bar）和温度（K）。
   · 新增右侧图例，与 Raptor 2 示意图风格一致。

V5.3 历史改动：
─────────────────────────────────────────────────────────
1. 左侧参数区改为 QScrollArea，窗口可以正常缩小。
2. 右侧"总体性能"和"部件结果"改为分组卡片式显示。
3. 表格仍保留在状态点、几何估算、节流扫描中，用于查看完整明细。
4. GUI 仍直接调用 core.EngineInputs 与 cycle.FFSCCycleModel，不走 API。
"""

from __future__ import annotations

import csv
import json
import math
import sys
import traceback
from dataclasses import fields, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool, Slot, QPointF
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QPen, QTextCursor,
    QPainterPath, QPolygonF, QLinearGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsLineItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTabWidget,
    QTextEdit,
    QComboBox,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ui_metadata import (
    _MATERIAL_FIELD_META, DISPLAY_UNIT_MAP, CYCLE_TYPE_CHOICES, GG_LAYOUT_CHOICES, PROPELLANT_CHOICES,
    NONLINEAR_SOLVER_CHOICES, COMBUSTION_LOSS_CHOICES, THERMO_DATASET_CHOICES,
    TURBINE_BOUNDARY_CHOICES, TURBINE_MEANLINE_CHOICES, TURBINE_GEOMETRY_STATE_CHOICES,
    FFSC_ONLY_GROUPS, GG_ONLY_GROUPS,
    PARAM_GROUPS, OVERVIEW_GROUPS, OVERVIEW_GROUPS_GG, COMPONENT_ORDER,
    COMPONENT_LABELS, COMPONENT_GROUPS, KEY_LABELS, STATE_LABELS
)
from core import EngineInputs, CycleResults, preburner_temperature_constraint
from cycle import FFSCCycleModel
from gg_cycle import make_cycle_model
from geometry import compute_geometry, geometry_to_dict
from thermo import CANTERA_AVAILABLE, ROCKETCEA_AVAILABLE, cross_validate_cea_cantera
from sweep import sweep_ox_preburner_mr, sweep_fuel_preburner_mr, sweep_to_csv
from ui_drawings import AutoFitGraphicsView, GeometryDrawingPanel
from materials import MATERIALS, list_materials_for_part


def _material_choices_for(field_key: str):
    """返回 (TurbineMaterial 列表) 用于填充 QComboBox。"""
    part, ox_only = _MATERIAL_FIELD_META[field_key]
    candidates = list_materials_for_part(part)
    if ox_only:
        candidates = [m for m in candidates if m.oxygen_compatible]
    return candidates


# 涡轮部位 part_key → EngineInputs 字段名缩写
_TURB_PART_ABBR = {"rotor_blade": "rotor", "stator_vane": "stator",
                   "disk": "disk", "housing": "housing"}


def _material_field_for_row(part_key: str, side: str) -> str:
    """材料库某行 → 对应的 EngineInputs 材料字段名；不可内联选择则返回 ""。

    仅"有输入字段支撑"的行可在材料库表格里内联下拉选材料（改了能被 run_cycle 读到）：
    推力室内/外壁 + 8 个涡轮部位。泵/喷管/喷注器目前用 DEFAULT_*（无输入字段），不可选。
    """
    if part_key == "chamber_inner_wall":
        return "chamber_inner_wall_material"
    if part_key == "chamber_outer_wall":
        return "chamber_outer_wall_material"
    if side in ("ox", "fuel") and part_key in _TURB_PART_ABBR:
        return f"turb_{side}_{_TURB_PART_ABBR[part_key]}_material"
    return ""


def _material_tooltip(m) -> str:
    """生成材料下拉条目的悬浮说明：温度上限、密度、Co 含量、用途。"""
    ox_flag = "✓ 富氧兼容" if m.oxygen_compatible else "✗ 含 Co，禁用富氧"
    name_cn = getattr(m, "name_cn", "") or ""
    cn_line = f"中文名: {name_cn}\n" if name_cn else ""
    k_th = getattr(m, "thermal_conductivity_WmK", 0.0)
    k_line = f"热导率 k = {k_th:.0f} W/(m·K)\n" if k_th > 0 else ""
    return (
        f"内部 key: {m.key}\n"
        f"{cn_line}"
        f"工艺: {m.process}\n"
        f"密度 ρ = {m.density_kgm3:.0f} kg/m³\n"
        f"室温 σy = {m.sigma_yield_RT_MPa:.0f} MPa\n"
        f"高温 σy = {m.sigma_yield_hot_MPa:.0f} MPa @ {m.T_hot_ref_K:.0f} K\n"
        f"{k_line}"
        f"长期上限 T = {m.T_max_use_K:.0f} K\n"
        f"短时上限 T = {m.T_max_short_K:.0f} K\n"
        f"Co 含量 = {m.cobalt_fraction*100:.1f}%   {ox_flag}\n"
        f"说明: {m.notes}"
    )

# matplotlib 嵌入 Qt（可选依赖，缺失时节流扫描仅显示表格）
try:
    import matplotlib
    matplotlib.use("QtAgg")

    # ── 中文字体 & 负号修复 ──────────────────────────────────────────────
    # 按优先级依次尝试 Windows / Linux / macOS 常见中文字体
    matplotlib.rcParams["font.sans-serif"] = [
        "Noto Sans SC",      # Bundled OFL font used by LRE Workbench
        "Microsoft YaHei",   # Windows 微软雅黑（最优先）
        "SimHei",            # Windows 黑体
        "WenQuanYi Micro Hei",  # Linux 文泉驿
        "PingFang SC",       # macOS
        "Noto Sans CJK SC",  # Linux/通用
        "DejaVu Sans",       # 兜底（无中文但不报错）
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False  # 防止负号变方框

    import matplotlib.pyplot as _mpl_plt          # noqa: F401（触发 rc 初始化）
    from qt_chart_canvas import SafeFigureCanvasQTAgg as FigureCanvasQTAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import AutoMinorLocator
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


# ─── 显示单位换算（共享声明位于 ui_metadata）──────────────────
# key → (SI → 显示值 的换算系数, 显示单位字符串)
# 显示值 = SI值 × factor；反向：SI值 = 显示值 / factor
# 只列出需要换算的字段，其余字段保持原始 SI 单位。


def _to_display(key: str, si_val: float) -> str:
    """SI 值 → 显示字符串（应用 DISPLAY_UNIT_MAP 换算）。"""
    if key in DISPLAY_UNIT_MAP:
        factor, _ = DISPLAY_UNIT_MAP[key]
        disp = si_val * factor
        # 根据量级选择合适精度
        if abs(disp) >= 1000:
            return f"{disp:.1f}"
        elif abs(disp) >= 10:
            return f"{disp:.3f}"
        else:
            return f"{disp:.6g}"
    return str(si_val)


def _from_display(key: str, display_str: str) -> float:
    """显示字符串 → SI 值（应用 DISPLAY_UNIT_MAP 逆换算）。"""
    val = float(display_str)
    if key in DISPLAY_UNIT_MAP:
        factor, _ = DISPLAY_UNIT_MAP[key]
        return val / factor
    return val


GEOMETRY_OBJECT_LABELS = {
    "chamber": "主燃烧室",
    "nozzle": "喷管",
    "injector": "喷注器",
    "ox_pump": "液氧泵",
    "fuel_pump": "燃料泵",
    "ox_turbine": "富氧涡轮",
    "fuel_turbine": "富燃涡轮",
    "ox_preburner": "富氧预燃室",
    "fuel_preburner": "富燃预燃室",
    "overall_length_m": "发动机总体长度 [m]",
    "overall_max_diameter_m": "最大外径 [m]",
    "dry_mass_estimate_kg": "干重估算 [kg]",
}

# 几何估算页：上方 GeometryDrawingPanel 各 tab(按序)对应下方应显示的 geom 顶层键集合。
# None 表示显示全部;顺序必须与 GeometryDrawingPanel 中 addTab 的顺序一致。
GEOMETRY_TAB_FILTER_KEYS: List[Optional[Set[str]]] = [
    {"overall_length_m", "overall_max_diameter_m", "dry_mass_estimate_kg"},  # 0: 整机三维 / Engine 3D
    {"nozzle"},                                                              # 1: 喷管 / Nozzle
    {"chamber"},                                                             # 2: 主燃烧室 / Chamber
    {"injector"},                                                            # 3: 喷注器 / Injector
    {"ox_pump", "fuel_pump"},                                                # 4: 泵 / Pumps
    {"ox_turbine", "fuel_turbine"},                                          # 5: 涡轮 / Turbines
    {"ox_preburner", "fuel_preburner"},                                      # 6: 预燃室 / Preburners
]

GEOMETRY_KEY_LABELS = {
    "diameter_m": "直径 [m]",
    "radius_m": "半径 [m]",
    "length_m": "长度 [m]",
    "volume_m3": "体积 [m³]",
    "l_star_m": "特征长度 L* [m]",
    "wall_thickness_mm": "壁厚估算总厚 [mm]",
    "contraction_ratio": "收缩比",
    # ── V7.1 燃烧室新增字段 ────────────────────────
    "cylindrical_section_length_m": "圆柱段长度 [m]",
    "inner_wall_thickness_mm": "内壁厚度 (GRCop-84) [mm]",
    "cooling_channel_height_mm": "冷却通道径向高度 [mm]",
    "cooling_channel_width_mm":  "冷却通道周向宽度 [mm]",
    "outer_wall_thickness_mm": "外壳厚度 (Inconel-718) [mm]",
    "num_cooling_channels": "再生冷却通道数",
    "inner_surface_area_m2": "燃气侧内表面积 [m²]",
    "dome_radius_m": "喷注端 dome 半径 [m]",
    "chamber_to_convergent_blend_radius_m": "圆柱-收缩段过渡圆弧 R [m]",
    # ── 喷管字段 ─────────────────────────────────
    "throat_diameter_m": "喉部直径 [m]",
    "throat_radius_m": "喉部半径 [m]",
    "exit_diameter_m": "出口直径 [m]",
    "exit_radius_m": "出口半径 [m]",
    "expansion_ratio": "膨胀比",
    "nozzle_length_m": "喷管长度 [m]",
    "convergent_length_m": "收缩段长度 [m]",
    "total_length_m": "总长度 [m]",
    "convergent_half_angle_deg": "收缩段半角 [deg]",
    "initial_expansion_angle_deg": "初始膨胀角 [deg]",
    "exit_angle_deg": "出口角 [deg]",
    "throat_upstream_radius_m": "喉部上游圆弧半径 Ru [m]",
    "throat_downstream_radius_m": "喉部下游圆弧半径 Rd [m]",
    "contour_x": "喷管轮廓轴向坐标 [m]",
    "contour_r": "喷管轮廓半径坐标 [m]",
    # ── V7.1 喷管新增字段 ──────────────────────────
    "nozzle_type": "喷管类型",
    "bell_fraction": "钟形长度比 (% bell)",
    "throat_axial_position_m": "喉部轴向位置 (距喷注面) [m]",
    "num_contour_points": "轮廓离散点数",
    "name": "名称",
    "fluid": "工质",
    "face_diameter_m": "喷注面直径 [m]",
    "face_area_m2": "喷注面面积 [m²]",
    "num_elements": "喷注单元数",
    "element_spacing_mm": "单元等效占用尺度（非排布节距）[mm]",
    "d_ox_mm": "中心流道直径 [mm]",
    "d_post_mm": "中心管外径 [mm]",
    "d_outer_mm": "外环流道外径（外筒内径）[mm]",
    "delta_fuel_mm": "环缝径向宽度 [mm]",
    "post_wall_thickness_mm": "中心管壁厚假设 [mm]",
    "geometry_status": "几何记录状态",
    "geometry_warnings": "几何说明与告警",
    "impeller_diameter_m": "叶轮直径 [m]",
    "shaft_speed_rpm": "转速 [rpm]",
    "specific_speed": "比转速",
    "num_stages": "级数估算",
    "tip_speed_ms": "叶尖速度 [m/s]",
    "head_m": "扬程 [m]",
    "volume_flow_m3s": "体积流量 [m³/s]",
    "pump_type": "泵型估计",
    "rotor_diameter_m": "转子直径 [m]",
    "blade_height_mm": "叶高 [mm]",
    "u_over_c": "速比 U/C₀",
}



# 流路状态点顺序：用于绘制 P-T-ρ 曲线
_LOX_PATH = [
    ("lox_tank",            "储箱"),
    ("lox_pump_inlet",      "泵入口"),
    ("lox_after_pump",      "泵出口"),
    ("ox_preburner_outlet", "预燃室出口"),
    ("ox_turbine_exhaust",  "涡轮出口"),
    ("main_chamber",        "主燃室"),
]
_CH4_PATH = [
    ("fuel_tank",              "储箱"),
    ("fuel_pump_inlet",        "泵入口"),
    ("fuel_after_pump",        "泵出口"),
    ("fuel_after_cooling",     "冷却后"),
    ("fuel_preburner_outlet",  "预燃室出口"),
    ("fuel_turbine_exhaust",   "涡轮出口"),
    ("main_chamber",           "主燃室"),
]

SPECIES_LABELS = {
    "CH4": "CH4 甲烷",
    "O2": "O2 氧气",
    "H2O": "H2O 水蒸气",
    "CO2": "CO2 二氧化碳",
    "CO": "CO 一氧化碳",
    "H2": "H2 氢气",
    "OH": "OH 羟基",
    "O": "O 原子氧",
    "H": "H 原子氢",
    "HO2": "HO2 过氧羟基",
    "H2O2": "H2O2 过氧化氢",
}


COMPONENT_HIDE_IF_PRESENT = {
    "rho_kgm3": "rho_in_kgm3",
    "p_in_bar": "p_in_MPa",
    "p_out_bar": "p_out_MPa",
    "power_W": "power_MW",
    "turbine_power_W": "turbine_power_MW",
    "heat_load_W": "heat_load_MW",
    "delta_p_Pa": "delta_p_MPa",
    "dp_ox_Pa": "dp_ox_MPa",
    "dp_fuel_Pa": "dp_fuel_MPa",
    # V5.3.6 新增：预燃室喷注器压降（保留 MPa，隐藏 Pa/bar 重复）
    "injector_dp_Pa":         "injector_dp_MPa",
    "injector_dp_bar":        "injector_dp_MPa",
    "preburner_pressure_Pa":  "preburner_pressure_MPa",
}

COMPONENT_VALUE_SCALE = {
    "power_W": 1e-6,
    "turbine_power_W": 1e-6,
    "heat_load_W": 1e-6,
    "p_in_Pa": 1e-6,
    "p_out_Pa": 1e-6,
    "P_in_Pa": 1e-6,
    "P_out_Pa": 1e-6,
    "dp_ox_Pa": 1e-6,
    "dp_fuel_Pa": 1e-6,
    "p_exit_Pa": 1e-6,
    "exit_pressure_Pa": 1e-6,
    "p_amb_Pa": 1e-6,
    "thrust_N": 1e-3,
    "thrust_vac_N": 1e-3,
}

COMPONENT_LABEL_OVERRIDE = {
    "p_exit_Pa": "喷管出口压力 [MPa]",
    "exit_pressure_Pa": "喷管出口压力 [MPa]",
    "p_amb_Pa": "环境压力 [MPa]",
    "thrust_N": "推力 [kN]",
    "thrust_vac_N": "真空推力 [kN]",
}

COMPONENT_SPECIFIC_LABELS = {
    "ox_pump": {
        "p_in_MPa": "泵入口压力 [MPa]",
        "p_out_MPa": "泵出口压力 [MPa]",
        "p_in_bar": "泵入口压力 [bar]",
        "p_out_bar": "泵出口压力 [bar]",
        "T_in_K": "泵入口温度 [K]",
        "T_out_K": "泵出口温度 [K]",
        "eta": "泵效率 [-]",
    },
    "fuel_pump": {
        "p_in_MPa": "泵入口压力 [MPa]",
        "p_out_MPa": "泵出口压力 [MPa]",
        "p_in_bar": "泵入口压力 [bar]",
        "p_out_bar": "泵出口压力 [bar]",
        "T_in_K": "泵入口温度 [K]",
        "T_out_K": "泵出口温度 [K]",
        "eta": "泵效率 [-]",
    },
    "cooling_channel": {
        "T_in_K": "冷却通道入口温度 [K]",
        "T_out_K": "冷却通道出口温度 [K]",
        "delta_T_K": "冷却剂温升 [K]",
    },
    "ox_preburner_turbine": {
        "T_in_K": "涡轮入口温度 [K]",
        "T_exhaust_K": "涡轮出口温度 [K]",
    },
    "fuel_preburner_turbine": {
        "T_in_K": "涡轮入口温度 [K]",
        "T_exhaust_K": "涡轮出口温度 [K]",
    },
}


class WorkerSignals(QObject):
    progress = Signal(int, int, float, str)
    finished = Signal(object)
    cancelled = Signal(object)
    record_ready = Signal(object)
    error = Signal(str)


class CancellableWorker(QRunnable):
    def __init__(self):
        super().__init__()
        from task_control import CancellationToken
        self.cancel_token = CancellationToken()
        self.signals = WorkerSignals()

    def request_cancel(self):
        self.cancel_token.cancel()


class SolveWorker(CancellableWorker):
    def __init__(self, inputs: EngineInputs):
        super().__init__()
        self.inputs = inputs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        from task_control import TaskCancelled, cancellation_scope, checkpoint
        try:
            with cancellation_scope(self.cancel_token):
                # V20：按 inputs.cycle_type 选择循环模型（ffsc / gg）
                model = make_cycle_model(self.inputs)

                def on_progress(it: int, max_it: int, norm: float):
                    checkpoint()
                    self.signals.progress.emit(it, max_it, float(norm), "设计点求解")

                results = model.evaluate(progress_callback=on_progress)
                checkpoint()
                self.signals.finished.emit(results)
        except TaskCancelled:
            self.signals.cancelled.emit({"kind": "solve", "records": []})
        except Exception:
            self.signals.error.emit(traceback.format_exc())


class ThrottleWorker(CancellableWorker):
    def __init__(self, inputs: EngineInputs):
        super().__init__()
        self.inputs = inputs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        from task_control import TaskCancelled, cancellation_scope, checkpoint
        completed = []
        try:
            with cancellation_scope(self.cancel_token):
                # V20：按 inputs.cycle_type 选择循环模型（ffsc / gg）
                model = make_cycle_model(self.inputs)

                def on_progress(idx: int, n: int, desc: str):
                    checkpoint()
                    self.signals.progress.emit(idx, n, 0.0, desc)

                def on_record(record):
                    completed.append(record)
                    self.signals.record_ready.emit(record)

                results = model.throttle_sweep(
                    progress_callback=on_progress, record_callback=on_record)
                checkpoint()
                self.signals.finished.emit(results)
        except TaskCancelled:
            self.signals.cancelled.emit({"kind": "throttle", "records": completed})
        except Exception:
            self.signals.error.emit(traceback.format_exc())


class ThermoWorker(CancellableWorker):
    """在后台线程运行 cross_validate_cea_cantera()，避免阻塞 UI。"""

    def __init__(self, of_list, eps_list, pc_pa, T_fuel_K, T_ox_K, methane_thermo_dataset="gri30"):
        super().__init__()
        self.of_list   = of_list
        self.eps_list  = eps_list
        self.pc_pa     = pc_pa
        self.T_fuel_K  = T_fuel_K
        self.T_ox_K    = T_ox_K
        self.methane_thermo_dataset = methane_thermo_dataset
        self.signals   = WorkerSignals()

    @Slot()
    def run(self):
        from task_control import TaskCancelled, cancellation_scope, checkpoint
        results = []
        try:
            with cancellation_scope(self.cancel_token):
                n_total = len(self.of_list) * len(self.eps_list)
                for i, of in enumerate(self.of_list):
                    for j, eps in enumerate(self.eps_list):
                        checkpoint()
                        from thermo import cross_validate_cea_cantera as _xval
                        pts = _xval(
                            of_list=[of], eps_list=[eps],
                            pc_pa=self.pc_pa,
                            T_fuel_K=self.T_fuel_K,
                            T_ox_K=self.T_ox_K,
                            methane_thermo_dataset=self.methane_thermo_dataset,
                        )
                        results.extend(pts)
                        for record in pts:
                            self.signals.record_ready.emit(record)
                        done = i * len(self.eps_list) + j + 1
                        self.signals.progress.emit(
                            done, n_total, 0.0,
                            f"O/F={of:.1f}  ε={eps:.0f}"
                        )
                checkpoint()
                self.signals.finished.emit(results)
        except TaskCancelled:
            self.signals.cancelled.emit({"kind": "thermo", "records": results})
        except Exception:
            self.signals.error.emit(traceback.format_exc())


class OFSweepWorker(CancellableWorker):
    """
    后台线程跑预燃室 O/F 扫描（涡轮设计点扫描）。

    side ∈ {'ox', 'fuel'}：扫描哪一侧预燃室
    of_list                ：待扫描的 O/F 目标值列表
    """

    def __init__(self, inputs: EngineInputs, side: str, of_list: List[float]):
        super().__init__()
        self.inputs   = inputs
        self.side     = side
        self.of_list  = list(of_list)
        self.signals  = WorkerSignals()

    @Slot()
    def run(self):
        from task_control import TaskCancelled, cancellation_scope, checkpoint
        completed = []
        try:
            with cancellation_scope(self.cancel_token):
                def on_progress(idx: int, n: int, desc: str):
                    checkpoint()
                    self.signals.progress.emit(idx, n, 0.0, desc)

                def on_record(record):
                    completed.append(record)
                    self.signals.record_ready.emit(record)

                sweep_function = (sweep_ox_preburner_mr if self.side == "ox"
                                  else sweep_fuel_preburner_mr)
                records = sweep_function(
                    self.inputs, self.of_list,
                    progress_callback=on_progress, record_callback=on_record)
                checkpoint()
                self.signals.finished.emit({"side": self.side, "records": records,
                                            "inputs": asdict(self.inputs)})
        except TaskCancelled:
            self.signals.cancelled.emit({"kind": "of_sweep", "side": self.side,
                                         "records": completed, "inputs": asdict(self.inputs)})
        except Exception:
            self.signals.error.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FFSC LOX/LCH₄ 火箭发动机数字样机  V6.2.1")
        # 设置窗口图标(优先用项目根目录下的 app_icon.ico/png,缺省则用 Qt 内置)
        from PySide6.QtGui import QIcon
        from pathlib import Path
        icon_path = Path(__file__).parent / "app_icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            # 退路:Qt 自带的 SP_ComputerIcon — 上线前替换成自定义火箭图标
            self.setWindowIcon(self.style().standardIcon(self.style().StandardPixmap.SP_TitleBarMenuButton))
        self.resize(1400, 860)
        self.setMinimumSize(900, 560)

        self.thread_pool = QThreadPool.globalInstance()
        self.input_edits: Dict[str, QLineEdit] = {}
        self.last_results: CycleResults | None = None
        self.last_geometry: Dict[str, Any] | None = None
        self._last_wallfield_full = None   # V19：整程 WallField（marching 剖面来源）
        self.last_throttle: List[dict] | None = None
        self.last_of_sweep: Dict[str, Any] | None = None  # V9：O/F 扫描结果

        # 字体大小：界面全局基准字号，可通过左侧面板下拉框调整
        # V5.3.6：默认 13 → 14，配合总体性能页 KPI 大字（fs+6=20px）在 1440 宽屏更清晰
        self._font_size: int = 16

        # V20.1：构造期抑制"加载参考机型"弹窗（reset_defaults/预设应用也临时置位）
        self._ui_ready: bool = False
        self._suppress_preset_prompt: bool = False

        self._build_ui()
        self._build_menu()
        self._apply_style()
        self.statusBar().showMessage(
            f"热力学模式：{'Cantera / GRI-Mech 3.0（高保真）' if CANTERA_AVAILABLE else '经验 fallback（无 Cantera）'}"
        )
        self._ui_ready = True

    def _build_menu(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for text, slot in [
            ("运行设计点", self.run_cycle),
            ("节流扫描", self.run_throttle),
            ("涡轮设计点扫描", self.run_of_sweep_ox),
            ("热力学对比", self.run_thermo_compare),
            ("计算几何", self.compute_geometry_from_last),
            ("导出 JSON", self.export_json),
            ("导出 CFTurbo MD", self.export_cfturbo_md),
            ("恢复默认", self.reset_defaults),
        ]:
            act = QAction(text, self)
            act.triggered.connect(slot)
            toolbar.addAction(act)
            if text == "计算几何":
                toolbar.addSeparator()

    def _build_ui(self):
        splitter = QSplitter(Qt.Horizontal)
        left = self._build_left_panel()
        right = self._build_right_panel()
        left.setMinimumWidth(280)
        right.setMinimumWidth(580)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([360, 920])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        self.setStatusBar(QStatusBar())

    def _build_left_panel(self) -> QWidget:
        defaults = EngineInputs()
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(10, 10, 10, 10)
        outer_layout.setSpacing(8)

        title = QLabel("发动机设计输入")
        title.setObjectName("PanelTitle")
        outer_layout.addWidget(title)

        # ── 字体大小选择器 ─────────────────────────────────
        font_row = QHBoxLayout()
        font_label = QLabel("界面字体：")
        font_label.setObjectName("MutedLabel")
        self._font_combo = QComboBox()
        self._font_combo.addItems(["小 (12px)", "中 (14px)", "大 (16px)", "特大 (18px)"])
        self._font_combo.setCurrentIndex(2)   # 默认"大 (16px)"（部件结果卡片更易读）
        # V5.3.6 修复：原 currentIndexChanged 信号发出 int 而非 str，与 slot 签名不匹配，
        # 导致 size_map.get(text, 13) 总是返回默认值——字体永远不变。
        # 改用 currentTextChanged 信号，slot 接收正确的字符串。
        self._font_combo.currentTextChanged.connect(self._on_font_size_changed)
        self._font_combo.setToolTip("调整整个界面的基准字体大小（类似 ANSYS Fluent 界面字体选项）")
        font_row.addWidget(font_label)
        font_row.addWidget(self._font_combo)
        font_row.addStretch(1)
        outer_layout.addLayout(font_row)

        subtitle = QLabel("压力单位：MPa | 推力单位：kN | 参数区可滚动")
        subtitle.setObjectName("MutedLabel")
        outer_layout.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(2, 2, 6, 2)
        root.setSpacing(8)

        # V20：组名 → QGroupBox 映射（循环方式切换时控制 FFSC/GG 专属组显隐）
        self._param_group_boxes: Dict[str, QGroupBox] = {}
        for group_title, items in PARAM_GROUPS:
            box = QGroupBox(group_title)
            self._param_group_boxes[group_title] = box
            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignLeft)
            form.setFormAlignment(Qt.AlignTop)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(6)
            for key, label, unit in items:
                # V19 防护：同一 EngineInputs 字段只允许一个输入控件——重复注册会让
                # 后者覆盖 input_edits 里的前者，先出现的可见输入框被静默忽略。
                if key in self.input_edits:
                    raise RuntimeError(f"PARAM_GROUPS 重复注册输入字段：{key}")
                # ── V20：循环方式 / GG 轴系布局选择器（QComboBox，userData=内部 key）──
                if key in ("cycle_type", "gg_layout", "propellant_pair", "nonlinear_solver", "combustion_loss_model", "methane_thermo_dataset",
                           "turbine_boundary_basis", "turbine_meanline_mode", "ox_turbine_geometry_state_basis", "fuel_turbine_geometry_state_basis"):
                    combo = QComboBox()
                    combo.setObjectName("ParamCombo")
                    choices = {"cycle_type": CYCLE_TYPE_CHOICES,
                               "gg_layout": GG_LAYOUT_CHOICES,
                               "propellant_pair": PROPELLANT_CHOICES,
                               "nonlinear_solver": NONLINEAR_SOLVER_CHOICES,
                               "combustion_loss_model": COMBUSTION_LOSS_CHOICES,
                               "methane_thermo_dataset": THERMO_DATASET_CHOICES,
                               "turbine_boundary_basis": TURBINE_BOUNDARY_CHOICES,
                               "turbine_meanline_mode": TURBINE_MEANLINE_CHOICES,
                               "ox_turbine_geometry_state_basis": TURBINE_GEOMETRY_STATE_CHOICES,
                               "fuel_turbine_geometry_state_basis": TURBINE_GEOMETRY_STATE_CHOICES}[key]
                    default_val = getattr(defaults, key)
                    for i, (val, text) in enumerate(choices):
                        combo.addItem(text, userData=val)
                        if val == default_val:
                            combo.setCurrentIndex(i)
                    if key == "cycle_type":
                        combo.setToolTip(
                            "选择发动机循环方式：\n"
                            "· FFSC 全流量分级燃烧（Raptor 类）：双预燃室气-气喷注，默认\n"
                            "· GG 燃气发生器开式循环（TQ-12 类）：富燃 GG 驱动涡轮后直排\n"
                            "切换后左侧参数区自动显示对应循环的专属参数组。")
                        combo.currentIndexChanged.connect(self._on_cycle_type_changed)
                    elif key == "gg_layout":
                        combo.setToolTip(
                            "GG 轴系布局（仅 GG 循环生效）：\n"
                            "· 单轴：一台涡轮同轴驱动氧泵和燃料泵\n"
                            "· 双轴：GG 燃气并联分流至两台涡轮，各驱动一侧泵")
                    elif key == "nonlinear_solver":
                        combo.setToolTip("相同物理方程与残差验收判据；TRF 用于对照。完整收敛仍需所有启用的迭代层通过。")
                    elif key.startswith("turbine_") or key.endswith("_turbine_geometry_state_basis"):
                        combo.setToolTip("保存所选涡轮接口与初选假设；选择模型不代表已完成部件验证。")
                    else:
                        combo.setToolTip(
                            "液氧/煤油使用正十二烷替代工质，仅支持 GG 稳态循环与指定吸热量冷却。\n"
                            "切换工质不会自动替换数值；建议载入配套工程示例。\n"
                            "该模型不等同于真实 RP-1 或 Merlin 1D 的性能复现。")
                    self.input_edits[key] = combo
                    form.addRow(label, combo)
                    continue
                # ── V10：材料字段使用 QComboBox（按部位/富氧兼容性筛选）──
                if key in _MATERIAL_FIELD_META:
                    combo = QComboBox()
                    combo.setObjectName("ParamCombo")
                    default_key = getattr(defaults, key)
                    current_idx = 0
                    for i, m in enumerate(_material_choices_for(key)):
                        # 条目文本:中英双语;userData:内部 key
                        combo.addItem(m.display_name, userData=m.key)
                        combo.setItemData(i, _material_tooltip(m), Qt.ToolTipRole)
                        if m.key == default_key:
                            current_idx = i
                    combo.setCurrentIndex(current_idx)
                    # 整个控件的悬浮提示：解释这一栏是干什么的
                    part, ox_only = _MATERIAL_FIELD_META[key]
                    part_cn = {
                        "rotor_blade": "动叶（旋转件 — 离心应力 + 高温燃气）",
                        "stator_vane": "静叶 / 喷嘴（高温但无离心应力）",
                        "disk":        "涡轮盘（盘内应力 + 中等温度）",
                        "housing":     "壳体 / 歧管（压力容器 + 中温）",
                        "chamber_inner_wall": "主燃烧室内壁（燃气侧，高热流 → 铜合金）",
                        "chamber_outer_wall": "主燃烧室外壳（结构层，承高压冷却剂 → 镍基）",
                    }.get(part, part)
                    if part.startswith("chamber_"):
                        side_cn = "推力室壁（按部位筛选）"
                    else:
                        side_cn = "富氧侧（仅显示 ASTM G124 氧兼容材料）" if ox_only else "富燃侧（允许含 Co 高温合金）"
                    combo.setToolTip(
                        f"参数 {key}\n部位：{part_cn}\n侧别：{side_cn}\n"
                        f"鼠标悬停每个选项可查看材料温度上限、密度、Co 含量等详情。"
                    )
                    self.input_edits[key] = combo
                    form.addRow(label, combo)
                    continue

                # 将 SI 默认值转换为显示单位后填入输入框
                si_val = getattr(defaults, key)
                init_text = _to_display(key, si_val) if isinstance(si_val, float) else str(si_val)
                edit = QLineEdit(init_text)
                edit.setAlignment(Qt.AlignRight)
                edit.setObjectName("ParamEdit")
                # tooltip 说明 SI 原始值，便于调试
                tip_si = f"内部 SI 值：{si_val}"
                if key in DISPLAY_UNIT_MAP:
                    factor, dunit = DISPLAY_UNIT_MAP[key]
                    tip_si += f"（输入为 {dunit}，程序自动乘以 {1/factor:.0g} 转换为 SI）"
                edit.setToolTip(f"{key} [{unit}]\n{tip_si}")
                self.input_edits[key] = edit
                row_label = f"{label} [{unit}]" if unit else label
                form.addRow(row_label, edit)
            root.addWidget(box)

        # ── V20.1：参考机型预设（循环方式组内追加两行）─────────────
        # 下拉框按当前循环过滤机型；按钮把该机型工况一键写入全部输入框。
        _cycle_box = self._param_group_boxes.get("循环方式 / Engine cycle")
        if _cycle_box is not None:
            _cycle_form = _cycle_box.layout()
            self._preset_combo = QComboBox()
            self._preset_combo.setObjectName("ParamCombo")
            self._preset_combo.setToolTip(
                "参考机型工况库（engine_presets.py）：\n"
                "· FFSC → Raptor 3（对标默认参数）\n"
                "· GG → 天鹊 TQ-12A（蓝箭官网 84.6 t / 293 s 口径标定）\n"
                "选择机型后点下方按钮，把该机型工况一键填入左侧全部输入框。")
            _cycle_form.addRow("参考机型 / Ref. engine", self._preset_combo)
            self._preset_btn = QPushButton("应用参考机型工况")
            self._preset_btn.setObjectName("SecondaryButton")
            self._preset_btn.setToolTip("把上方所选参考机型的全套工况参数写入输入面板（覆盖当前数值）")
            self._preset_btn.clicked.connect(self._on_apply_preset_clicked)
            # 跨两列整行，但限宽 —— 参数区内容宽 ~780px 需水平滚动，
            # 若让按钮拉满整行，居中的文字会落在默认视口(~360px)之外"看似空白"。
            self._preset_btn.setMaximumWidth(330)
            _cycle_form.addRow(self._preset_btn)

        # V20：按当前循环方式初始化 FFSC/GG 专属参数组显隐 + 机型下拉候选
        self._on_cycle_type_changed()

        root.addStretch(1)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)

        self.chk_auto_geometry = QCheckBox("求解后自动计算几何参数")
        self.chk_auto_geometry.setChecked(True)
        outer_layout.addWidget(self.chk_auto_geometry)

        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("▶ 运行设计点")
        self.btn_run.setObjectName("PrimaryButton")
        self.btn_throttle = QPushButton("📊 节流扫描")
        self.btn_throttle.setObjectName("SecondaryButton")
        self.btn_run.clicked.connect(self.run_cycle)
        self.btn_throttle.clicked.connect(self.run_throttle)
        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(self.btn_throttle)
        outer_layout.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        outer_layout.addWidget(self.progress)
        self.progress_label = QLabel("就绪")
        self.progress_label.setObjectName("MutedLabel")
        outer_layout.addWidget(self.progress_label)
        return outer

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        root = QVBoxLayout(panel)
        root.setContentsMargins(10, 10, 10, 10)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("FFSC 数字样机计算结果")
        title.setObjectName("PanelTitle")
        subtitle = QLabel("循环闭合 · 预燃室混合比 · 涡轮泵功率平衡 · 几何估算")
        subtitle.setObjectName("MutedLabel")
        # V20：标题/副标题随结果的循环类型切换（FFSC / GG）
        self._result_title_label = title
        self._result_subtitle_label = subtitle
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        self.badge = QLabel("未求解")
        self.badge.setObjectName("BadgeNeutral")
        header.addLayout(title_box)
        header.addStretch(1)
        header.addWidget(self.badge)
        root.addLayout(header)

        # 用 addTab 返回的 index 建命名表,避免后续插入 tab 导致硬编码 index 错位
        self.tabs = QTabWidget()
        self._tab_idx: Dict[str, int] = {}
        self._tab_idx["overview"]   = self.tabs.addTab(self._build_overview_tab(),    "总体性能")
        self._tab_idx["components"] = self.tabs.addTab(self._build_components_tab(),  "部件结果")
        self._tab_idx["states"]     = self.tabs.addTab(self._build_states_tab(),      "状态点")
        self._tab_idx["topology"]   = self.tabs.addTab(self._build_topology_tab(),    "流程拓扑")
        self._tab_idx["geometry"]   = self.tabs.addTab(self._build_geometry_tab(),    "几何估算")
        # V19：推力室设计工作区（部件级详细设计）。原顶层「热结构云图」「再生冷却」
        # 两页作为子页迁入工作区（widget 仍由本类的 _build_*_tab 构建、populate 不变）；
        # 旧 _tab_idx 键指向工作区，兼容既有跳转。
        from ui_chamber_design import ChamberDesignPanel
        self.chamber_panel = ChamberDesignPanel(self)
        self._tab_idx["chamber_design"] = self.tabs.addTab(self.chamber_panel, "推力室设计")
        self._tab_idx["thermstr"] = self._tab_idx["chamber_design"]
        self._tab_idx["cooling"]  = self._tab_idx["chamber_design"]
        self._tab_idx["materials"]  = self.tabs.addTab(self._build_materials_tab(),   "材料库")
        # 发动机特性（方案B）：节流特性 + 高度特性合并为一个顶层页（内嵌两个子页）
        self._tab_idx["engchar"]    = self.tabs.addTab(self._build_engchar_tab(),      "发动机特性")
        self._tab_idx["throttle"]   = self._tab_idx["engchar"]   # 向后兼容旧引用（节流完成后切到此页）
        self._tab_idx["of_sweep"]   = self.tabs.addTab(self._build_of_sweep_tab(),    "涡轮设计点扫描")
        self._tab_idx["thermo"]     = self.tabs.addTab(self._build_thermo_tab(),      "热力学对比")
        self._tab_idx["log"]        = self.tabs.addTab(self._build_log_tab(),         "日志/警告")
        root.addWidget(self.tabs, 1)
        return panel

    def _build_overview_tab(self) -> QWidget:
        self.overview_scroll, self.overview_content, self.overview_layout = self._scroll_content()
        return self._wrap_tab_with_export(
            self.overview_scroll, "overview_content", "ffsc_overview.png")

    def _build_components_tab(self) -> QWidget:
        self.components_scroll, self.components_content, self.components_layout = self._scroll_content()
        return self._wrap_tab_with_export(
            self.components_scroll, "components_content", "ffsc_components.png")

    def _build_states_tab(self) -> QWidget:
        """
        V5.3.6 重构：
        ─────────────────────────────────────────────────────
        1. 上半部分拆成"液氧流路"与"燃料流路"两张独立卡片（水平并排），
           主燃烧室作为汇合点单独显示在两卡之间。
        2. 流路 P-T 曲线去掉密度子图，改为 2×2（LOX/CH4 × P/T），
           子图明显放大，更易读。
        3. 右侧新增"压降瀑布图"卡片，显式展示每段压降（含主喷注器、阀门、
           冷却套等通常被吞掉的压力损失），是 FFSC 数字样机的核心可视化。
        """
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        intro = QLabel(
            "状态点页面展示循环中各关键截面的压力、温度、流量、组分；"
            "上方左右两张卡片分别对应液氧流路和燃料流路，下方为状态点详情与组分；"
            "右侧上为流路 P-T 曲线，下为压降瀑布图，计算后自动刷新。"
        )
        intro.setObjectName("MutedLabel")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # ── 主水平分割：左=表格+详情  右=曲线+瀑布图 ──
        hsplit = QSplitter(Qt.Horizontal)
        hsplit.setChildrenCollapsible(False)

        # ─────────────────────────────────────────────
        # 左侧容器：上 = 两张流路卡片，下 = 详情+组分
        # ─────────────────────────────────────────────
        left_w = QWidget()
        left_vbox = QVBoxLayout(left_w)
        left_vbox.setContentsMargins(0, 0, 0, 0)
        left_vbox.setSpacing(0)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.setChildrenCollapsible(False)

        # ── 上：流路卡片（液氧 + 甲烷 + 主燃烧室）────────
        # V10：改用 QSplitter，用户可拖动两条分隔线调整三张卡片的宽度，
        # 解决"内容太宽只能下方横条滚动"的问题。
        paths_w = QSplitter(Qt.Horizontal)
        paths_w.setChildrenCollapsible(False)
        paths_w.setHandleWidth(6)

        # 液氧流路卡片
        self.lox_path_card, self.lox_path_table = self._make_path_card(
            title="液氧流路 LOX Path",
            subtitle="储箱 → 管路 → 液氧泵 → 富氧预燃室 → 富氧涡轮 → 主燃烧室",
            accent_color="#2563EB",
        )
        self.lox_path_card.setMinimumWidth(170)   # V10：220→170，可把左栏拖更窄、给右侧图表让宽
        paths_w.addWidget(self.lox_path_card)

        # 燃料流路卡片
        self.ch4_path_card, self.ch4_path_table = self._make_path_card(
            title="燃料流路 Fuel Path",
            subtitle="储箱 → 管路 → 燃料泵 → 再生冷却 → 富燃预燃室 → 富燃涡轮 → 主燃烧室",
            accent_color="#D97706",
        )
        self.ch4_path_card.setMinimumWidth(170)   # V10：220→170，同上
        paths_w.addWidget(self.ch4_path_card)

        # 主燃烧室卡片（汇合点）
        self.main_path_card, self.main_path_table = self._make_path_card(
            title="主燃烧室（汇合）",
            subtitle="两路热气混合燃烧",
            accent_color="#DC2626",
        )
        self.main_path_card.setMinimumWidth(120)   # V10：140→120，同上
        paths_w.addWidget(self.main_path_card)

        # 初始比例 5:5:3
        paths_w.setStretchFactor(0, 5)
        paths_w.setStretchFactor(1, 5)
        paths_w.setStretchFactor(2, 3)
        # 让 splitter 自身不随 vsplit 折叠
        paths_w.setMinimumHeight(160)

        vsplit.addWidget(paths_w)

        # ── 下：详情卡片 + 组分卡片（水平排列）──────────────
        bottom_w = QWidget()
        bottom = QHBoxLayout(bottom_w)
        bottom.setContentsMargins(0, 4, 0, 0)
        bottom.setSpacing(12)

        self.state_detail_card = QFrame()
        self.state_detail_card.setObjectName("ResultCard")
        detail_layout = QVBoxLayout(self.state_detail_card)
        detail_layout.setContentsMargins(14, 12, 14, 12)
        detail_layout.setSpacing(8)
        self.state_title = QLabel("状态点详情")
        self.state_title.setObjectName("CardTitle")
        detail_layout.addWidget(self.state_title)
        self.state_hint = QLabel("点击左上方任一流路卡片中的状态点行后，此处显示详细参数。")
        self.state_hint.setObjectName("MutedLabel")
        self.state_hint.setWordWrap(True)
        detail_layout.addWidget(self.state_hint)
        self.state_detail_table = QTableWidget(0, 3)
        self.state_detail_table.setHorizontalHeaderLabels(["参数", "数值", "单位"])
        self.state_detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.state_detail_table.horizontalHeader().setStretchLastSection(True)
        self.state_detail_table.verticalHeader().setDefaultSectionSize(26)
        self.state_detail_table.setAlternatingRowColors(True)
        self.state_detail_table.setEditTriggers(QTableWidget.NoEditTriggers)
        detail_layout.addWidget(self.state_detail_table)
        bottom.addWidget(self.state_detail_card, 1)

        species_box = QFrame()
        species_box.setObjectName("ResultCard")
        species_layout = QVBoxLayout(species_box)
        species_layout.setContentsMargins(14, 12, 14, 12)
        species_layout.setSpacing(8)
        species_title = QLabel("组分质量分数")
        species_title.setObjectName("CardTitle")
        species_layout.addWidget(species_title)
        self.species_note = QLabel("按质量分数从大到小排序；主表中只显示前 4 项摘要。")
        self.species_note.setObjectName("MutedLabel")
        self.species_note.setWordWrap(True)
        species_layout.addWidget(self.species_note)
        self.species_table = QTableWidget(0, 3)
        self.species_table.setHorizontalHeaderLabels(["物种", "质量分数", "百分比"])
        self.species_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.species_table.horizontalHeader().setStretchLastSection(True)
        self.species_table.verticalHeader().setDefaultSectionSize(26)
        self.species_table.setAlternatingRowColors(True)
        self.species_table.setEditTriggers(QTableWidget.NoEditTriggers)
        species_layout.addWidget(self.species_table)
        bottom.addWidget(species_box, 1)

        vsplit.addWidget(bottom_w)
        vsplit.setSizes([340, 360])
        left_vbox.addWidget(vsplit)
        hsplit.addWidget(left_w)

        # ─────────────────────────────────────────────
        # 右侧：上 = 流路 P-T 曲线（2×2），下 = 压降瀑布图
        # ─────────────────────────────────────────────
        if MATPLOTLIB_AVAILABLE:
            # V10：右侧改用垂直 QSplitter，用户可上下拖动调整 P-T 曲线与
            # 压降瀑布图的高度（此前是固定 QVBoxLayout，只能左右拖、不能上下拖）。
            right_vsplit = QSplitter(Qt.Vertical)
            right_vsplit.setChildrenCollapsible(False)
            right_vsplit.setHandleWidth(6)

            # 上：P-T 曲线卡片
            chart_w = QFrame()
            chart_w.setObjectName("ResultCard")
            chart_layout = QVBoxLayout(chart_w)
            chart_layout.setContentsMargins(8, 10, 10, 10)
            chart_layout.setSpacing(4)
            # 标题行：左标题 + 右"导出 PNG"按钮
            chart_hdr_row = QHBoxLayout()
            chart_hdr = QLabel("流路 P-T 曲线（沿流向）")
            chart_hdr.setObjectName("CardTitle")
            chart_hdr_row.addWidget(chart_hdr)
            chart_hdr_row.addStretch(1)
            btn_flow_png = QPushButton("📷 导出 PNG")
            btn_flow_png.setToolTip("把流路 P-T 曲线导出为 300 dpi 高清 PNG（或 SVG / PDF）")
            btn_flow_png.clicked.connect(
                lambda: self._export_figure_as_png(
                    self.states_flow_fig, "ffsc_flowpath_PT.png",
                    gate_table=self.lox_path_table))
            chart_hdr_row.addWidget(btn_flow_png)
            chart_layout.addLayout(chart_hdr_row)
            # V5.3.6：3×2 → 2×2，去掉密度子图。fig 高度 9 → 6，留空间给瀑布图
            self.states_flow_fig = Figure(figsize=(7, 6), facecolor="#FAFAFA")
            self.states_flow_canvas = FigureCanvasQTAgg(self.states_flow_fig)
            self.states_flow_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            chart_layout.addWidget(self.states_flow_canvas)
            self._init_states_flowpath_axes()
            right_vsplit.addWidget(chart_w)

            # 下：压降瀑布图卡片
            waterfall_w = QFrame()
            waterfall_w.setObjectName("ResultCard")
            wlayout = QVBoxLayout(waterfall_w)
            wlayout.setContentsMargins(8, 10, 10, 10)
            wlayout.setSpacing(4)
            # 标题行：左标题 + 右"导出 PNG"按钮
            w_hdr_row = QHBoxLayout()
            wlabel = QLabel("压降网络瀑布图（含喷注器/冷却套/阀门压降）")
            wlabel.setObjectName("CardTitle")
            w_hdr_row.addWidget(wlabel)
            w_hdr_row.addStretch(1)
            btn_waterfall_png = QPushButton("📷 导出 PNG")
            btn_waterfall_png.setToolTip("把压降瀑布图导出为 300 dpi 高清 PNG（或 SVG / PDF）")
            btn_waterfall_png.clicked.connect(
                lambda: self._export_figure_as_png(
                    self.waterfall_fig, "ffsc_pressure_waterfall.png",
                    gate_table=self.lox_path_table))
            w_hdr_row.addWidget(btn_waterfall_png)
            wlayout.addLayout(w_hdr_row)
            self.waterfall_fig = Figure(figsize=(7, 5), facecolor="#FAFAFA")
            self.waterfall_canvas = FigureCanvasQTAgg(self.waterfall_fig)
            self.waterfall_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            wlayout.addWidget(self.waterfall_canvas)
            self._init_waterfall_axes()
            right_vsplit.addWidget(waterfall_w)

            # 初始高度比例 3:2（与旧 stretch 一致），用户仍可拖动手柄上下调整
            right_vsplit.setStretchFactor(0, 3)
            right_vsplit.setStretchFactor(1, 2)
            right_vsplit.setSizes([540, 360])

            hsplit.addWidget(right_vsplit)
            hsplit.setSizes([720, 600])
        else:
            hsplit.setSizes([900, 0])

        outer.addWidget(hsplit, 1)

        self._selected_state_key = None
        self._state_keys_by_row: List[str] = []   # 保留以兼容旧导出逻辑
        return w

    def _make_path_card(self, title: str, subtitle: str, accent_color: str):
        """
        V5.3.6 新增：创建单条流路的卡片（含标题 + 状态点表）。
        Returns
        ─────
        card : QFrame 卡片容器
        table : QTableWidget 流路状态点表（5 列：状态点 / P / T / ṁ / ρ）
        """
        card = QFrame()
        card.setObjectName("ResultCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("CardTitle")
        title_lbl.setStyleSheet(f"color: {accent_color}; font-weight: 900;")
        layout.addWidget(title_lbl)

        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("MutedLabel")
        sub_lbl.setWordWrap(True)
        layout.addWidget(sub_lbl)

        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["状态点", "压力 [MPa]", "温度 [K]", "流量 [kg/s]", "密度 [kg/m³]"])
        # V10：改用 Interactive（用户可手动拖列宽）+ 第一列 ResizeToContents，
        # 配合表格自身的水平滚动条，窗口任何宽度下都能完整显示数据。
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(60)
        # 让超出窗口宽度的内容可以横向滚动，而不是被裁掉
        table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 给一个合理的默认列宽（用户仍可拖动）
        for col, w_px in [(1, 110), (2, 90), (3, 100), (4, 120)]:
            table.setColumnWidth(col, w_px)
        table.verticalHeader().setDefaultSectionSize(26)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.itemSelectionChanged.connect(
            lambda t=table: self._on_path_table_selection_changed(t)
        )
        layout.addWidget(table)
        return card, table

    def _build_topology_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.scene = QGraphicsScene()
        self.view = AutoFitGraphicsView(self.scene)
        self.view.setRenderHint(self.view.renderHints())
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        layout.addWidget(self.view)
        # 底部工具行：导出按钮 + 提示
        bottom = QHBoxLayout()
        self.btn_export_topology = QPushButton("📷 导出 PNG")
        self.btn_export_topology.setObjectName("SecondaryButton")
        self.btn_export_topology.setToolTip(
            "把当前流程拓扑图导出为高分辨率 PNG（也支持 SVG 矢量 / PDF）")
        self.btn_export_topology.clicked.connect(self._export_topology_png)
        bottom.addWidget(self.btn_export_topology)
        note = QLabel("提示：流程拓扑图会随窗口自动缩放；该图为系统级流路示意，不替代工程 P&ID。")
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        bottom.addWidget(note, 1)
        layout.addLayout(bottom)
        return w

    def _export_topology_png(self):
        """把当前流程拓扑图导出为 PNG / SVG / PDF。

        复用最近一次渲染用的 FFSCTopologyRenderer（_draw_topology 里保存的），
        重新 render() 出一张 matplotlib Figure 后 savefig：
          · PNG → 高 dpi 位图（清晰，适合插报告/PPT）；
          · SVG / PDF → 矢量，无限缩放不糊（适合论文/出版）。
        """
        renderer = getattr(self, "_topology_renderer", None)
        if renderer is None:
            QMessageBox.information(
                self, "暂无拓扑图",
                "请先运行设计点求解，生成流程拓扑图后再导出。")
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出流程拓扑图", "ffsc_topology.png",
            "PNG 图片 (*.png);;SVG 矢量图 (*.svg);;PDF (*.pdf)")
        if not path:
            return
        try:
            fig = renderer.render(dpi=150)
            # 位图(png)用 300 dpi 出印刷级清晰图；矢量(svg/pdf)dpi 不影响清晰度
            save_dpi = 300 if path.lower().endswith(".png") else 150
            fig.savefig(path, dpi=save_dpi, bbox_inches="tight",
                        facecolor=fig.get_facecolor())
            self._append_log(f"已导出流程拓扑图: {path}")
        except Exception as ex:
            QMessageBox.warning(self, "导出失败", f"无法导出拓扑图：\n{ex}")

    def _build_geometry_tab(self) -> QWidget:
        """几何估算 tab (V7.1 重构)。

        把图纸面板和参数卡片包到一个 *垂直* QSplitter 里:
          · 上半:GeometryDrawingPanel (TabWidget 内是 AspectPixmapLabel,
                  跟着 splitter 一起放大/缩小、保持宽高比)
          · 下半:参数卡片 + (隐藏) 平铺总表

        用户拖动中间分隔条即可把图纸区放大,解决"显示不全"的问题。
        """
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        # 让分隔条肉眼可见,提示用户可以拖动
        splitter.setStyleSheet(
            "QSplitter::handle:vertical { "
            "  background: #cbd5e1; border-top: 1px solid #94a3b8;"
            "  border-bottom: 1px solid #94a3b8; margin: 2px 0px; }"
            "QSplitter::handle:vertical:hover { background: #94a3b8; }"
        )

        # ── 上半:图纸面板 ─────────────────────────────
        self.geometry_drawing_panel = GeometryDrawingPanel()
        self.geometry_drawing_panel.setMinimumHeight(320)
        # 切换上方图纸 tab 时，下方参数卡片随之过滤为对应部件
        self.geometry_drawing_panel.tabs.currentChanged.connect(
            self._on_geometry_drawing_tab_changed
        )
        splitter.addWidget(self.geometry_drawing_panel)

        # ── 下半:参数卡片 + 提示 + 平铺总表 ────────────
        bottom_scroll = QScrollArea()
        bottom_scroll.setWidgetResizable(True)
        bottom_scroll.setFrameShape(QFrame.NoFrame)
        bottom_content = QWidget()
        layout = QVBoxLayout(bottom_content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        hint = QLabel(
            "图纸为数字样机早期方案示意:用于尺寸核查、构型展示和方案沟通,"
            "不替代详细 CAD/工程制图。\n"
            "提示:拖动上方灰色分隔条可放大/缩小图纸区域;鼠标滚轮在图上可缩放(部分图)。"
        )
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.geometry_cards_layout = QVBoxLayout()
        self.geometry_cards_layout.setSpacing(12)
        self.geometry_cards_layout.addStretch(1)
        layout.addLayout(self.geometry_cards_layout)

        self.geometry_table = QTableWidget(0, 4)
        self.geometry_table.setHorizontalHeaderLabels(["对象", "参数", "数值", "备注"])
        self.geometry_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.geometry_table.horizontalHeader().setStretchLastSection(True)
        self.geometry_table.setVisible(False)
        layout.addWidget(self.geometry_table)

        bottom_scroll.setWidget(bottom_content)
        splitter.addWidget(bottom_scroll)

        # 初始比例:图 ~ 60% / 参数 ~ 40%;两侧都不可折叠到 0
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([560, 380])
        return splitter

    # ─────────────────────────────────────────────────────────
    # V10 新增：材料库页签
    # ─────────────────────────────────────────────────────────
    def _build_materials_tab(self) -> QWidget:
        """
        材料库页签 —— V12 工程数据表版（对标 ANSYS Engineering Data / Granta MI）

        版式：上 = 全宽多列工程数据树（部件 → 部位，横向直接列出材料 + 密度 /
              高温强度 / 温度上限 / 氧兼容 / 工况校核，并以颜色标识状态）；
              下 = 选中行的详情卡片 + σy(T) 曲线 + 选材能力图。

        替代 V10 的"单列缩进清单"——把"列表"升级为"工程数据表"。
        """
        outer = QWidget()
        v = QVBoxLayout(outer)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        intro = QLabel(
            "材料库 · 工程数据表：每行 = 一个部位的选用材料及其密度 / 高温屈服强度 / "
            "温度上限 / 氧兼容 / 当前工况校核；颜色标识氧兼容与安全状态。"
            "点击任一行 → 下方查看该材料的 σy(T) 曲线与选材能力图。"
        )
        intro.setObjectName("MutedLabel")
        intro.setWordWrap(True)
        v.addWidget(intro)

        # ── 反向定指标：从当前工况反推《材料研发指标书》──────────────
        spec_row = QHBoxLayout()
        self.btn_material_spec = QPushButton("📋 生成材料研发指标书（反向定指标）")
        self.btn_material_spec.setObjectName("SecondaryButton")
        self.btn_material_spec.setToolTip(
            "由当前求解工况反推每个关键部件的『所需材料性能指标』，"
            "对现有选材逐条判定（满足/临界/需研发），并给出研发缺口与库内候选。")
        self.btn_material_spec.clicked.connect(self._on_generate_material_spec)
        spec_row.addWidget(self.btn_material_spec)

        self.btn_liner_compare = QPushButton("🔬 燃烧室铜合金对比（耦合冷却）")
        self.btn_liner_compare.setObjectName("SecondaryButton")
        self.btn_liner_compare.setToolTip(
            "同一冷却工况下对比 CuCrZr / GRCop-42 / GRCop-84 / NARloy-Z / Glidcop 的"
            "热面壁温 vs 各自温度上限——直接看谁扛得住当前热流。耦合再生冷却。")
        self.btn_liner_compare.clicked.connect(self._on_compare_liner)
        spec_row.addWidget(self.btn_liner_compare)
        spec_row.addStretch(1)
        v.addLayout(spec_row)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)

        # ── 上：工程数据树（多列）─────────────────────────
        self.materials_tree = QTreeWidget()
        self.materials_tree.setColumnCount(7)
        self.materials_tree.setHeaderLabels([
            "部件 / 部位", "材料 Material", "密度 [kg/m³]",
            "σy@工况 [MPa]", "T_max [K]", "富氧 O₂", "工况校核",
        ])
        self.materials_tree.setAlternatingRowColors(True)
        # 行内嵌入 QComboBox（材料可选），关闭等高行避免下拉被裁
        self.materials_tree.setUniformRowHeights(False)
        self.materials_tree.setMinimumHeight(240)
        self.materials_tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.materials_tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        self.materials_tree.setStyleSheet(
            "QTreeWidget { background:#FFFFFF; border:1px solid #E5E7EB;"
            " border-radius:8px; font-size:12px; }"
            "QTreeWidget::item { padding:4px 6px; }"
            "QTreeWidget::item:selected { background:#DBEAFE; color:#1E3A8A; }"
            "QHeaderView::section { background:#F3F4F6; padding:6px; border:none;"
            " border-right:1px solid #E5E7EB; font-weight:700; color:#374151; }"
        )
        hdr = self.materials_tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        for _c in range(2, 7):
            hdr.setSectionResizeMode(_c, QHeaderView.ResizeToContents)
        self.materials_tree.itemSelectionChanged.connect(self._on_material_selected)
        split.addWidget(self.materials_tree)

        # ── 下：材料 detail 卡片容器（含 scroll）─────────
        self.materials_detail_scroll = QScrollArea()
        self.materials_detail_scroll.setWidgetResizable(True)
        self.materials_detail_scroll.setFrameShape(QFrame.NoFrame)
        # 设较小最小高度：让竖直分隔条能把详情区压下去，从而把上方材料树拉高看全部行
        self.materials_detail_scroll.setMinimumHeight(140)
        detail_content = QWidget()
        self.materials_detail_layout = QVBoxLayout(detail_content)
        self.materials_detail_layout.setContentsMargins(10, 4, 10, 10)
        self.materials_detail_layout.setSpacing(10)
        # 占位
        placeholder = QLabel("点击上方任一部位行，查看该材料的详细参数、σy(T) 曲线与选材能力图。")
        placeholder.setObjectName("MutedLabel")
        placeholder.setWordWrap(True)
        self.materials_detail_layout.addWidget(placeholder)
        self.materials_detail_layout.addStretch(1)
        self.materials_detail_scroll.setWidget(detail_content)
        split.addWidget(self.materials_detail_scroll)

        # 树为主区(默认就展开足够高、能看到全部部位行)；详情区可被分隔条压缩到下限。
        # 材料树本身带竖直滚动条，行多时可直接滚动；分隔条也能把树拉到更高。
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([520, 300])

        v.addWidget(split, 1)
        return outer

    def _populate_materials_tab(self, results: CycleResults):
        """
        每次求解完成调用：刷新工程数据树，每行 = 一个部位的选用材料 + 关键指标
        + 当前工况校核（颜色状态）。每个叶子行存 meta，供下方详情/图表使用。
        """
        if not hasattr(self, "materials_tree"):
            return  # tab 还没建好

        self.materials_tree.clear()
        # 重建期间程序化 setCurrentIndex 会触发下拉信号 → 用此标志屏蔽，避免误触发重跑
        self._materials_tree_building = True
        inputs = results.inputs

        # 8 个条目：富氧/富燃 × 动叶/静叶/盘/壳
        items: List[Tuple[str, str, str, str, str]] = [
            # (group, sub, part_key, material_key, side)
            ("富氧涡轮 (O-rich)", "动叶 rotor_blade", "rotor_blade", inputs.turb_ox_rotor_material,   "ox"),
            ("富氧涡轮 (O-rich)", "静叶 stator_vane", "stator_vane", inputs.turb_ox_stator_material,  "ox"),
            ("富氧涡轮 (O-rich)", "轮盘 disk",        "disk",        inputs.turb_ox_disk_material,    "ox"),
            ("富氧涡轮 (O-rich)", "壳体 housing",     "housing",     inputs.turb_ox_housing_material, "ox"),
            ("富燃涡轮 (F-rich)", "动叶 rotor_blade", "rotor_blade", inputs.turb_fuel_rotor_material,   "fuel"),
            ("富燃涡轮 (F-rich)", "静叶 stator_vane", "stator_vane", inputs.turb_fuel_stator_material,  "fuel"),
            ("富燃涡轮 (F-rich)", "轮盘 disk",        "disk",        inputs.turb_fuel_disk_material,    "fuel"),
            ("富燃涡轮 (F-rich)", "壳体 housing",     "housing",     inputs.turb_fuel_housing_material, "fuel"),
        ]

        # 追加：涡轮泵（液氧泵 / 燃料泵）+ 燃烧室壁 / 喷管壁 / 喷注器（默认材料，来自 materials.py）
        try:
            from materials import (
                DEFAULT_CHAMBER_WALL_MATERIALS as _CW,
                DEFAULT_NOZZLE_WALL_MATERIALS as _NW,
                DEFAULT_INJECTOR_MATERIALS as _IJ,
                DEFAULT_OX_PUMP_MATERIALS as _OP,
                DEFAULT_FUEL_PUMP_MATERIALS as _FP,
            )
            items += [
                # 泵（涡轮泵转子组：叶轮/诱导轮离心应力主导，泵壳承压；工质为低温推进剂）
                ("液氧泵 (LOX pump)", "叶轮 impeller",  "pump_impeller", inputs.lox_pump_impeller_material, "ox"),
                ("液氧泵 (LOX pump)", "诱导轮 inducer", "pump_inducer",  _OP.get("pump_inducer",  "monelk500_impeller"),  "ox"),
                ("液氧泵 (LOX pump)", "泵壳 housing",   "pump_housing",  _OP.get("pump_housing",  "inconel718_pump_ox"), "ox"),
                ("燃料泵 (Fuel pump)", "叶轮 impeller",  "pump_impeller", inputs.fuel_pump_impeller_material, "fuel"),
                ("燃料泵 (Fuel pump)", "诱导轮 inducer", "pump_inducer",  _FP.get("pump_inducer",  "ti6al4v"), "fuel"),
                ("燃料泵 (Fuel pump)", "泵壳 housing",   "pump_housing",  _FP.get("pump_housing",  "ss316l"), "fuel"),
                ("燃烧室壁 (Chamber)", "内壁 inner（再生冷却铜）", "chamber_inner_wall", getattr(inputs, "chamber_inner_wall_material", None) or _CW.get("chamber_inner_wall", "grcop84"), ""),
                ("燃烧室壁 (Chamber)", "外壳 outer（结构层）",     "chamber_outer_wall", getattr(inputs, "chamber_outer_wall_material", None) or _CW.get("chamber_outer_wall", "inconel718_wall"), ""),
                ("喷管壁 (Nozzle)",    "扩张段内壁 inner",         "nozzle_inner_wall",  _NW.get("nozzle_inner_wall", "grcop84"), ""),
                ("喷管壁 (Nozzle)",    "扩张段外壳 outer",         "nozzle_outer_wall",  _NW.get("nozzle_outer_wall", "inconel718_wall"), ""),
                ("喷管壁 (Nozzle)",    "真空延伸段（再生/Raptor）", "nozzle_extension",   _NW.get("nozzle_extension", "inconel718_wall"), ""),
                ("喷管壁 (Nozzle)",    "真空延伸段（辐射/可选）",   "nozzle_extension_radiative", _NW.get("nozzle_extension_radiative", "niobium_c103"), ""),
                ("喷注器 (Injector)",  "面板 face（高热流）",       "injector_face",      _IJ.get("injector_face", "grcop84"), ""),
                ("喷注器 (Injector)",  "本体 body",                "injector_body",      _IJ.get("injector_body", "inconel718_injector"), ""),
            ]
        except Exception:
            pass

        try:
            from materials import get_material
        except ImportError:
            self._materials_tree_building = False
            return

        # ── 当前工况温度（从最近结果取，用于 σy@工况 与状态着色）──────
        def _op_temp(side: str, part_key: str) -> float:
            try:
                # 泵：工质为低温推进剂，取泵出口温度（远低于材料温度上限，约束在离心应力）
                if part_key.startswith("pump_"):
                    pump_comp = "ox_pump" if side == "ox" else "fuel_pump"
                    fallback = 75.0 if side == "ox" else 110.0
                    return float(results.components[pump_comp].outputs.get("T_out_K", fallback))
                # V20：GG 循环无 ox/fuel_preburner_turbine —— 回退到 GG 涡轮
                if side == "ox":
                    comp = (results.components.get("ox_preburner_turbine")
                            or results.components.get("gg_turbine_ox")
                            or results.components.get("gg_turbine"))
                    return float(comp.outputs.get("T_in_K", 750.0)) if comp else 750.0
                if side == "fuel":
                    comp = (results.components.get("fuel_preburner_turbine")
                            or results.components.get("gg_turbine_fuel")
                            or results.components.get("gg_turbine"))
                    return float(comp.outputs.get("T_in_K", 850.0)) if comp else 850.0
                if part_key == "chamber_inner_wall":
                    return float(results.components["cooling_channel"].outputs.get("T_wall_hot_K", 800.0))
            except Exception:
                pass
            return {
                "chamber_outer_wall": 500.0, "nozzle_inner_wall": 700.0,
                "nozzle_outer_wall": 450.0, "nozzle_extension": 600.0,
                "nozzle_extension_radiative": 1200.0,
                "injector_face": 700.0, "injector_body": 500.0,
                "pump_impeller": 90.0, "pump_inducer": 90.0, "pump_housing": 90.0,
            }.get(part_key, 600.0)

        C_GREEN, C_YELLOW, C_RED, C_GRAY = (
            QColor("#15803D"), QColor("#B45309"), QColor("#B91C1C"), QColor("#6B7280"))

        groups: Dict[str, QTreeWidgetItem] = {}
        first_child = None
        for group, sub, part_key, mat_key, side in items:
            # 组父节点（不可选，跨列标题）
            if group not in groups:
                parent = QTreeWidgetItem([group])
                fp = parent.font(0); fp.setBold(True); fp.setPointSize(fp.pointSize() + 1)
                parent.setFont(0, fp)
                parent.setForeground(0, QBrush(QColor("#1F2937")))
                parent.setBackground(0, QBrush(QColor("#EEF2F7")))
                parent.setFlags(Qt.ItemIsEnabled)
                parent.setFirstColumnSpanned(True)
                self.materials_tree.addTopLevelItem(parent)
                parent.setExpanded(True)
                groups[group] = parent
            parent = groups[group]

            try:
                mat = get_material(mat_key)
            except Exception:
                bad = QTreeWidgetItem(["  " + sub, f"⚠ 未知材料 {mat_key}", "—", "—", "—", "—", "—"])
                parent.addChild(bad)
                continue

            T_op  = _op_temp(side, part_key)
            sy_op = mat.sigma_yield_at(T_op)
            o2_ok = bool(mat.oxygen_compatible)

            # 工况校核：动叶 / 泵主叶轮用离心应力安全系数 SF，其它部位用温度余量。
            # 诱导轮不在此列：模型只反推主叶轮叶尖速度，诱导轮(泵入口防汽蚀)叶尖速度
            # 远低于主叶轮 OD，套用会虚高误报红；其真实约束是汽蚀(NPSH/Nss)另算。
            if part_key in ("rotor_blade", "pump_impeller"):
                audit = self._material_audit_for_current_results(
                    {"part_key": part_key, "side": side, "mat_key": mat_key}, mat)
                if audit:
                    SF = audit[4]
                    status_txt = f"SF={SF:.2f}"
                    status_col = C_GREEN if SF >= 1.5 else (C_YELLOW if SF >= 1.0 else C_RED)
                else:
                    status_txt, status_col = "—", C_GRAY
            else:
                margin = mat.T_max_use_K - T_op
                status_txt = f"温度余量 {margin:+.0f}K"
                status_col = C_GREEN if margin >= 100 else (C_YELLOW if margin >= 0 else C_RED)

            child = QTreeWidgetItem([
                "  " + sub,
                mat.display_name,
                f"{mat.density_kgm3:.0f}",
                f"{sy_op:.0f}",
                f"{mat.T_max_use_K:.0f}",
                "✓" if o2_ok else "✗",
                status_txt,
            ])
            for ci in (2, 3, 4, 5):
                child.setTextAlignment(ci, Qt.AlignCenter)
            child.setForeground(5, QBrush(C_GREEN if o2_ok else C_RED))
            f5 = child.font(5); f5.setBold(True); child.setFont(5, f5)
            child.setForeground(6, QBrush(status_col))
            f6 = child.font(6); f6.setBold(True); child.setFont(6, f6)
            child.setData(0, Qt.UserRole, {
                "group": group, "sub": sub, "part_key": part_key,
                "mat_key": mat_key, "side": side, "T_op": T_op,
            })
            parent.addChild(child)

            # ── 材料库内联可选：有输入字段支撑的行（推力室壁 + 涡轮各部位）嵌 QComboBox ──
            field_key = _material_field_for_row(part_key, side)
            if field_key and field_key in _MATERIAL_FIELD_META:
                combo = QComboBox()
                combo.setObjectName("TreeMatCombo")
                cur_i = 0
                for ii, mm in enumerate(_material_choices_for(field_key)):
                    combo.addItem(mm.display_name, userData=mm.key)
                    combo.setItemData(ii, _material_tooltip(mm), Qt.ToolTipRole)
                    if mm.key == mat_key:
                        cur_i = ii
                combo.setCurrentIndex(cur_i)
                combo.setToolTip("在此直接改选该部位材料 → 自动重跑设计点，"
                                 "再生冷却/热结构/校核随之更新")
                combo.currentIndexChanged.connect(
                    lambda _idx, fk=field_key, cb=combo, it=child:
                        self._on_tree_material_changed(fk, cb.currentData(), it))
                self.materials_tree.setItemWidget(child, 1, combo)

            if first_child is None:
                first_child = child

        self._materials_tree_building = False
        if first_child is not None:
            self.materials_tree.setCurrentItem(first_child)

    def _on_tree_material_changed(self, field_key: str, new_key: str, item):
        """材料库下拉改材料 —— **纯浏览**：只刷新该行的展示列 + 下方详情卡 / σy(T) 曲线。

        完全不影响运行：不写回输入表单、不改运行配置、不重算任何东西。下次点"运行设计点"
        仍按「发动机设计输入」里设的材料计算（材料库的预览在下次运行时被重建覆盖）。
        当前工况温度 T_op 与壁材无关（单点冷却用固定导热），随行存于 meta，换材料即时算对。
        """
        if getattr(self, "_materials_tree_building", False):
            return                       # 重建期间的程序化变更，忽略
        if not new_key or item is None:
            return
        try:
            from materials import get_material
            m = get_material(new_key)
        except Exception:
            return
        meta = item.data(0, Qt.UserRole) or {}
        T_op = float(meta.get("T_op", 600.0))
        part_key = meta.get("part_key", "")
        side = meta.get("side", "")
        C_GREEN, C_YELLOW, C_RED, C_GRAY = (
            QColor("#15803D"), QColor("#B45309"), QColor("#B91C1C"), QColor("#6B7280"))
        # 工况校核列：动叶/泵主叶轮用离心应力 SF，其它用温度余量（与 populate 同口径）
        if part_key in ("rotor_blade", "pump_impeller"):
            audit = self._material_audit_for_current_results(
                {"part_key": part_key, "side": side, "mat_key": new_key}, m)
            if audit:
                SF = audit[4]
                status_txt = f"SF={SF:.2f}"
                status_col = C_GREEN if SF >= 1.5 else (C_YELLOW if SF >= 1.0 else C_RED)
            else:
                status_txt, status_col = "—", C_GRAY
        else:
            margin = m.T_max_use_K - T_op
            status_txt = f"温度余量 {margin:+.0f}K"
            status_col = C_GREEN if margin >= 100 else (C_YELLOW if margin >= 0 else C_RED)
        o2_ok = bool(m.oxygen_compatible)
        # 就地更新该行各列（密度 / σy@工况 / T_max / 氧兼容 / 工况校核）
        item.setText(2, f"{m.density_kgm3:.0f}")
        item.setText(3, f"{m.sigma_yield_at(T_op):.0f}")
        item.setText(4, f"{m.T_max_use_K:.0f}")
        item.setText(5, "✓" if o2_ok else "✗")
        item.setText(6, status_txt)
        item.setForeground(5, QBrush(C_GREEN if o2_ok else C_RED))
        item.setForeground(6, QBrush(status_col))
        # 更新该行 meta 的材料 key，使下方详情卡 / σy(T) 曲线显示新材料
        meta["mat_key"] = new_key
        item.setData(0, Qt.UserRole, meta)
        if self.materials_tree.currentItem() is item:
            self._on_material_selected()          # 已选中：直接刷新详情卡
        else:
            self.materials_tree.setCurrentItem(item)  # 未选中：选中它即触发详情刷新

    def _on_generate_material_spec(self):
        """反向定指标：由当前工况生成《材料研发指标书》，弹窗展示并可导出。"""
        results = getattr(self, "_latest_results", None)
        if results is None:
            QMessageBox.information(
                self, "材料研发指标书",
                "请先在左侧『▶ 运行设计点』完成一次求解，得到工况后再生成指标书。")
            return
        try:
            import material_spec as ms
            specs = ms.generate_material_spec_sheet(results)
            report = ms.format_report(specs)
        except Exception as ex:
            QMessageBox.critical(self, "材料研发指标书", f"生成失败：{ex}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("关键部件 · 材料研发指标书（反向定指标）")
        dlg.resize(980, 700)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)

        view = QTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.NoWrap)
        view.setFont(QFont("Consolas", 10))
        view.setPlainText(report)
        lay.addWidget(view, 1)

        btns = QHBoxLayout()
        btn_md = QPushButton("导出 Markdown")
        btn_csv = QPushButton("导出 CSV")
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("PrimaryButton")
        btns.addStretch(1)
        btns.addWidget(btn_md)
        btns.addWidget(btn_csv)
        btns.addWidget(btn_close)
        lay.addLayout(btns)

        def _exp_md():
            path, _ = QFileDialog.getSaveFileName(
                dlg, "导出 Markdown", "material_spec_sheet.md", "Markdown (*.md)")
            if path:
                ms.export_markdown(specs, path)
                QMessageBox.information(dlg, "导出", f"已导出：\n{path}")

        def _exp_csv():
            path, _ = QFileDialog.getSaveFileName(
                dlg, "导出 CSV", "material_spec_sheet.csv", "CSV (*.csv)")
            if path:
                ms.export_csv(specs, path)
                QMessageBox.information(dlg, "导出", f"已导出：\n{path}")

        btn_md.clicked.connect(_exp_md)
        btn_csv.clicked.connect(_exp_csv)
        btn_close.clicked.connect(dlg.accept)
        dlg.exec()

    def _on_compare_liner(self):
        """燃烧室铜合金对比（耦合冷却）：弹窗展示对比柱状图 + 文本表。"""
        results = getattr(self, "_latest_results", None)
        if results is None:
            QMessageBox.information(
                self, "铜合金对比",
                "请先在左侧『▶ 运行设计点』完成一次求解，得到工况后再对比。")
            return
        try:
            import material_spec as ms
            # 比较只消费本次循环保存的热负荷；更换材料后的热场需完整循环重算。
            rows, op = ms.compare_chamber_liner_materials(results)
            text = ms.format_liner_comparison(rows, op)
        except Exception as ex:
            QMessageBox.critical(self, "铜合金对比", f"对比失败：{ex}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("燃烧室内壁材料比较（保存的热负荷）")
        dlg.resize(1000, 780)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)

        if MATPLOTLIB_AVAILABLE:
            try:
                fig = Figure(figsize=(10, 5.2), facecolor="#FAFAFA")
                canvas = FigureCanvasQTAgg(fig)
                canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                ms.plot_liner_comparison(fig, rows, op)
                lay.addWidget(canvas, 3)
            except Exception:
                pass

        view = QTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.NoWrap)
        view.setFont(QFont("Consolas", 10))
        view.setPlainText(text)
        lay.addWidget(view, 2)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("PrimaryButton")
        btn_close.clicked.connect(dlg.accept)
        row.addWidget(btn_close)
        lay.addLayout(row)
        dlg.exec()

    def _on_material_selected(self):
        items = self.materials_tree.selectedItems()
        if not items:
            return
        meta = items[0].data(0, Qt.UserRole)
        if not meta:
            return  # 组父节点（无 meta）不渲染
        self._render_material_detail(meta)

    def _render_material_detail(self, meta: dict):
        """渲染右侧材料 detail 卡片。"""
        # 清空旧内容
        while self.materials_detail_layout.count() > 0:
            child = self.materials_detail_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        try:
            from materials import get_material
        except ImportError:
            return
        try:
            mat = get_material(meta["mat_key"])
        except Exception as ex:
            err = QLabel(f"⚠ 加载材料失败：{ex}")
            self.materials_detail_layout.addWidget(err)
            self.materials_detail_layout.addStretch(1)
            return

        # ── 卡片 1：基本信息 ─────────────────────────────
        try:
            from materials import gb_grade as _gb
            _grade = _gb(mat.key) or "—"
        except Exception:
            _grade = "—"
        rows_basic: List[Tuple[str, str, str]] = [
            ("英文名称 / Name",   mat.name,                          ""),
            ("中文名称 / 中文学名", mat.name_cn or "—",                ""),
            ("国标/对标牌号",      _grade,                             ""),
            ("内部 key",          mat.key,                            ""),
            ("适用部位",          " / ".join(mat.parts),              ""),
            ("加工方式",          mat.process,                        ""),
        ]
        badges_basic: List[Tuple[str, str]] = []
        if mat.oxygen_compatible:
            badges_basic.append(("LOX/GOX 兼容 ✓", "ok"))
        else:
            badges_basic.append(("禁用富氧（含 Co）", "error"))

        card1 = self._make_card(
            f"{meta['group']} · {meta['sub']}",
            rows_basic,
            badges=badges_basic,
            subtitle=f"ASTM G124 钴含量 {mat.cobalt_fraction*100:.2f}%",
            notes=mat.notes,
            notes_title="用途说明",
        )
        self.materials_detail_layout.addWidget(card1)

        # ── 卡片 2：力学参数 ─────────────────────────────
        rows_mech: List[Tuple[str, str, str]] = [
            ("密度 ρ",                       f"{mat.density_kgm3:.0f}",       "kg/m³"),
            ("屈服强度（室温）",              f"{mat.sigma_yield_RT_MPa:.0f}", "MPa"),
            ("屈服强度（高温参考点）",        f"{mat.sigma_yield_hot_MPa:.0f}", "MPa"),
            ("高温参考温度",                  f"{mat.T_hot_ref_K:.0f}",         "K"),
            ("长期使用温度上限 T_max",        f"{mat.T_max_use_K:.0f}",         "K"),
            ("短时使用温度上限 T_max_short",  f"{mat.T_max_short_K:.0f}",       "K"),
        ]
        # 热导率(若已知);燃烧室内壁材料(GRCop-84 等)是关键差异化指标
        if mat.thermal_conductivity_WmK > 0:
            rows_mech.append(
                ("热导率 k (室温)",
                 f"{mat.thermal_conductivity_WmK:.0f}", "W/(m·K)")
            )
        # V12：补全弹性模量 / 线膨胀 / 泊松比 / 比热（数据库本就有，原卡片漏列）
        rows_mech += [
            ("弹性模量 E",      f"{mat.youngs_modulus_GPa:.0f}",  "GPa"),
            ("线膨胀系数 α",     f"{mat.cte_1e6_per_K:.1f}",       "1e-6/K"),
            ("泊松比 ν",         f"{mat.poisson_ratio:.2f}",       "—"),
            ("比热容 cp",        f"{mat.spec_heat_J_kgK:.0f}",     "J/(kg·K)"),
        ]
        card2 = self._make_card("力学/热物性 Mechanical & Thermal", rows_mech)
        self.materials_detail_layout.addWidget(card2)

        # ── 卡片 3：化学/相容性 ─────────────────────────
        rows_chem: List[Tuple[str, str, str]] = [
            ("钴 Co 质量分数",         f"{mat.cobalt_fraction*100:.2f}", "%"),
            ("ASTM G124 氧兼容",       "✓ 允许" if mat.oxygen_compatible else "✗ 禁用", ""),
        ]
        card3 = self._make_card("化学/相容性 Chemistry", rows_chem)
        self.materials_detail_layout.addWidget(card3)

        # ── 卡片 4：当前工况下的校核（动叶 / 泵主叶轮：离心应力主导）──────
        # 诱导轮不算 SF：模型只给主叶轮叶尖速度，套用会误报（详见 _populate_materials_tab 注释）。
        audit = None
        _is_pump_rot = meta["part_key"] == "pump_impeller"
        if meta["part_key"] == "rotor_blade" or _is_pump_rot:
            audit = self._material_audit_for_current_results(meta, mat)
            if audit is not None:
                T_K, U_ms, sy_T, sigma_c, SF, issues = audit
                # 安全系数徽章
                if SF >= 1.5:
                    bdg = [(f"SF={SF:.2f} ✓", "ok")]
                elif SF >= 1.0:
                    bdg = [(f"SF={SF:.2f} 偏低", "warn")]
                else:
                    bdg = [(f"SF={SF:.2f} <1.0", "error")]
                # 标签自适应：涡轮取入口温度/节圆速度，泵取出口温度/叶尖速度
                if _is_pump_rot:
                    _t_lbl, _u_lbl = "泵出口温度 T_out", "叶尖速度 u_tip"
                else:
                    _t_lbl, _u_lbl = "工况入口温度 T_in", "节圆速度 U_mean"
                rows_audit: List[Tuple[str, str, str]] = [
                    (_t_lbl,                     f"{T_K:.0f}",      "K"),
                    (_u_lbl,                     f"{U_ms:.0f}",     "m/s"),
                    ("插值屈服强度 σy(T)",        f"{sy_T:.0f}",    "MPa"),
                    ("离心应力 σ = ρ·U²",       f"{sigma_c:.0f}",  "MPa"),
                    ("安全系数 SF = σy/σ",      f"{SF:.2f}",       "—"),
                ]
                card4 = self._make_card(
                    "当前工况校核 Current operating point",
                    rows_audit,
                    badges=bdg,
                    subtitle=" · ".join(issues) if issues else "无报警",
                )
                self.materials_detail_layout.addWidget(card4)

        # ── 卡片 5 + 6：可视化（V12，对标工业材料库）──────────────
        # σy(T) 真图（含工况点 ★ + 离心应力线，间距即安全裕度）
        # + 选材能力图（同部位候选材料散点，按氧兼容着色，高亮当前）
        if MATPLOTLIB_AVAILABLE:
            T_ref = float(audit[0]) if audit else (
                90.0 if meta.get("part_key", "").startswith("pump_")
                else 750.0 if meta.get("side") == "ox"
                else 850.0 if meta.get("side") == "fuel" else 800.0
            )
            try:
                fig_sy = self._fig_sigma_yield_curve(mat, audit)
                self.materials_detail_layout.addWidget(self._make_chart_card(
                    "σy(T) 屈服强度–温度曲线",
                    fig_sy,
                    subtitle=("蓝线=屈服强度随温度衰减；橙/红虚线=长期/短时温度上限"
                              + ("；★=当前工况，红线=离心应力，二者间距即安全裕度" if audit else "")),
                ))
                fig_map = self._fig_material_capability_map(mat, meta.get("side", ""), T_ref)
                self.materials_detail_layout.addWidget(self._make_chart_card(
                    f"选材能力图 Material selection map（@ {T_ref:.0f} K）",
                    fig_map,
                    subtitle=("横轴=温度能力 T_max，纵轴=该温度下强度；右上角越优。"
                              "绿=富氧兼容，红=含 Co 禁富氧，★=当前选用。"),
                ))
            except Exception as ex:
                self.materials_detail_layout.addWidget(QLabel(f"⚠ 图表渲染失败：{ex}"))
        else:
            ts = [300.0, 600.0, 800.0, 1000.0, 1200.0]
            rows_sy = [(f"T = {T:.0f} K", f"σy = {mat.sigma_yield_at(T):.0f}", "MPa") for T in ts]
            self.materials_detail_layout.addWidget(
                self._make_card("σy(T) 屈服强度温度曲线", rows_sy))

        self.materials_detail_layout.addStretch(1)

    # ─────────────────────────────────────────────────────────
    # V12：材料库可视化（对标工业材料库 Granta / CES）
    # ─────────────────────────────────────────────────────────
    def _make_chart_card(self, title: str, fig, subtitle: str = "") -> QFrame:
        """把一张 matplotlib Figure 包成与 _make_card 同风格的卡片。"""
        card = QFrame()
        card.setObjectName("ResultCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        lay.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("MutedLabel")
            s.setWordWrap(True)
            s.setStyleSheet("color: #6B7280; font-size: 12px;")
            lay.addWidget(s)
        canvas = FigureCanvasQTAgg(fig)
        canvas.setMinimumHeight(260)
        lay.addWidget(canvas)
        try:
            fig.tight_layout()
            canvas.draw_idle()
        except Exception:
            pass
        return card

    def _fig_sigma_yield_curve(self, mat, audit):
        """σy–T 曲线：屈服强度随温度衰减 + 温度上限 + 工况点/离心应力（动叶）。"""
        fig = Figure(figsize=(5.4, 2.9), dpi=100)
        ax = fig.add_subplot(111)
        T_hi = max(1300.0, mat.T_max_short_K + 120.0)
        n = 120
        Ts = [250.0 + i * (T_hi - 250.0) / (n - 1) for i in range(n)]
        sy = [mat.sigma_yield_at(t) for t in Ts]
        ax.plot(Ts, sy, color="#2563eb", lw=2.2, zorder=3, label="σy(T)")
        # 数据锚点（室温 + 高温参考点）
        ax.scatter([300.0, mat.T_hot_ref_K],
                   [mat.sigma_yield_RT_MPa, mat.sigma_yield_hot_MPa],
                   color="#1e3a8a", s=26, zorder=5)
        # 温度上限
        ax.axvline(mat.T_max_use_K, color="#f59e0b", ls="--", lw=1.2,
                   label=f"长期上限 {mat.T_max_use_K:.0f}K")
        ax.axvline(mat.T_max_short_K, color="#ef4444", ls=":", lw=1.2,
                   label=f"短时上限 {mat.T_max_short_K:.0f}K")
        # 工况点 + 离心应力（动叶）
        if audit is not None:
            T_K, U_ms, sy_T, sigma_c, SF, _issues = audit
            ax.axhline(sigma_c, color="#dc2626", lw=1.4, alpha=0.85,
                       label=f"离心应力 {sigma_c:.0f}MPa")
            ax.plot([T_K, T_K], [sigma_c, sy_T], color="#16a34a", lw=1.4, ls="-")
            ax.scatter([T_K], [sy_T], color="#16a34a", marker="*", s=180,
                       zorder=6, label=f"工况 {T_K:.0f}K · SF={SF:.2f}")
        ax.set_xlabel("温度 T [K]", fontsize=9)
        ax.set_ylabel("屈服强度 σy [MPa]", fontsize=9)
        ax.set_ylim(bottom=0)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, loc="upper right", framealpha=0.9)
        ax.tick_params(labelsize=8)
        return fig

    def _fig_material_capability_map(self, mat_current, side: str, T_ref: float):
        """选材能力图：同部位候选材料散点（T_max × σy@T_ref），氧兼容着色，高亮当前。"""
        from materials import MATERIALS
        fig = Figure(figsize=(5.4, 3.4), dpi=100)
        ax = fig.add_subplot(111)
        # 候选集合：与当前材料共享部位的材料（聚焦同类选材）；不足则用全库
        cur_parts = set(mat_current.parts)
        cands = [m for m in MATERIALS.values() if set(m.parts) & cur_parts]
        if len(cands) < 3:
            cands = list(MATERIALS.values())
        for m in cands:
            x = m.T_max_use_K
            y = m.sigma_yield_at(T_ref)
            is_cur = (m.key == mat_current.key)
            col = "#16a34a" if m.oxygen_compatible else "#dc2626"
            if is_cur:
                ax.scatter([x], [y], c=col, s=240, marker="*",
                           edgecolors="#1e3a8a", linewidths=1.6, zorder=6)
                ax.annotate(m.name, (x, y), textcoords="offset points",
                            xytext=(8, 6), fontsize=8, fontweight="bold",
                            color="#1e3a8a")
            else:
                ax.scatter([x], [y], c=col, s=48, alpha=0.55, zorder=3)
                ax.annotate(m.name, (x, y), textcoords="offset points",
                            xytext=(5, 3), fontsize=6.2, color="#6b7280")
        ax.axvline(T_ref, color="#9ca3af", ls="--", lw=0.8, alpha=0.7)
        ax.set_xlabel("长期使用温度上限 T_max [K]", fontsize=9)
        ax.set_ylabel(f"屈服强度 σy @ {T_ref:.0f}K [MPa]", fontsize=9)
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=8)
        from matplotlib.lines import Line2D
        ax.legend(handles=[
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#16a34a",
                   markersize=8, label="富氧兼容"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#dc2626",
                   markersize=8, label="含 Co 禁富氧"),
            Line2D([0], [0], marker="*", color="w", markerfacecolor="#1e3a8a",
                   markersize=12, label="当前选用"),
        ], fontsize=7, loc="lower right", framealpha=0.9)
        return fig

    def _material_audit_for_current_results(self, meta: dict, mat):
        """
        从最近一次求解的 results.components 取出对应部件的工况温度与速度，
        重新做一次材料 audit 用于显示。返回 (T, U, sy_T, sigma_c, SF, issues) 或 None。

        · 涡轮动叶：取涡轮 T_in 与叶中速度 U_mean；
        · 泵主叶轮：取泵出口温度 T_out 与单级叶尖速度 u_tip
          （离心应力 σ_c≈ρ·u_tip² 与涡轮动叶同一判据）。
          诱导轮不走此路径——模型未单独反推其叶尖速度，套用主叶轮速度会误报。
        """
        if not hasattr(self, "_latest_results") or self._latest_results is None:
            return None

        part_key = meta.get("part_key", "")
        # 泵主叶轮：离心应力主导，读泵组件叶尖速度
        if part_key == "pump_impeller":
            comp = self._latest_results.components.get(
                "ox_pump" if meta["side"] == "ox" else "fuel_pump")
            if not comp:
                return None
            o = comp.outputs
            T_K  = o.get("T_out_K")
            U_ms = o.get("u_tip_est_ms")
        else:
            # 涡轮动叶：读对应预燃室涡轮
            comp_name = "ox_preburner_turbine" if meta["side"] == "ox" else "fuel_preburner_turbine"
            comp = self._latest_results.components.get(comp_name)
            if not comp:
                return None
            o = comp.outputs
            T_K  = o.get("T_in_K")
            U_ms = o.get("U_mean_actual_ms")

        if T_K is None or U_ms is None:
            return None
        try:
            from materials import material_audit_row
            row = material_audit_row(meta["mat_key"], T_K=T_K, U_ms=U_ms, side=meta["side"])
            return (
                T_K, U_ms,
                row["sigma_yield_T_MPa"], row["sigma_centrifugal_MPa"],
                row["safety_factor"], row["issues"],
            )
        except Exception:
            return None

    def _build_throttle_tab(self) -> QWidget:
        """节流扫描标签页：左侧数据表 + 右侧 2×2 专业图表（ANSYS/MATLAB 风格）。"""
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ── 左侧：数据表 ────────────────────────────────────────────────
        left = QFrame()
        left.setObjectName("ResultCard")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 6, 10)

        tbl_hdr = QHBoxLayout()
        tbl_title = QLabel("节流扫描数据表")
        tbl_title.setObjectName("CardTitle")
        tbl_hdr.addWidget(tbl_title)
        tbl_hdr.addStretch(1)
        btn_tbl_png = QPushButton("📷 导出 PNG")
        btn_tbl_png.setToolTip("把数据表导出为 300 dpi 高清 PNG（或 PDF）")
        btn_tbl_png.clicked.connect(self._export_throttle_table_png)
        tbl_hdr.addWidget(btn_tbl_png)
        left_layout.addLayout(tbl_hdr)

        self.throttle_table = QTableWidget(0, 10)
        self.throttle_table.setHorizontalHeaderLabels([
            "节流 %", "目标推力 kN", "实际推力 kN", "室压 MPa",
            "Isp s", "总流量 kg/s", "主燃室温 K",
            "富氧预燃室温 K", "富燃预燃室温 K", "收敛",
        ])
        self.throttle_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.throttle_table.horizontalHeader().setStretchLastSection(True)
        self.throttle_table.setAlternatingRowColors(True)
        self.throttle_table.setSelectionBehavior(QTableWidget.SelectRows)
        left_layout.addWidget(self.throttle_table)
        splitter.addWidget(left)

        # ── 右侧：matplotlib 图表面板 ────────────────────────────────────
        if MATPLOTLIB_AVAILABLE:
            right = QFrame()
            right.setObjectName("ResultCard")
            right_layout = QVBoxLayout(right)
            right_layout.setContentsMargins(6, 10, 10, 10)

            chart_hdr = QHBoxLayout()
            chart_title = QLabel("性能曲线")
            chart_title.setObjectName("CardTitle")
            chart_hdr.addWidget(chart_title)
            chart_hdr.addStretch(1)
            btn_chart_png = QPushButton("📷 导出 PNG")
            btn_chart_png.setToolTip("把性能曲线导出为 300 dpi 高清 PNG（或 SVG / PDF）")
            btn_chart_png.clicked.connect(self._export_throttle_charts_png)
            chart_hdr.addWidget(btn_chart_png)
            right_layout.addLayout(chart_hdr)

            # Figure：浅灰背景，符合 UI 整体色调
            self.throttle_fig = Figure(
                figsize=(11, 8), facecolor="#FAFAFA", layout="constrained")
            self.throttle_fig.get_layout_engine().set(
                w_pad=0.08, h_pad=0.09, wspace=0.10, hspace=0.12)
            self.throttle_canvas = FigureCanvasQTAgg(self.throttle_fig)
            # Keep four plots readable when the data table or window narrows
            # the chart pane. The viewport scrolls rather than clipping labels.
            self.throttle_canvas.setMinimumSize(640, 520)
            self.throttle_canvas.setSizePolicy(
                QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.throttle_chart_scroll = QScrollArea()
            self.throttle_chart_scroll.setWidgetResizable(True)
            self.throttle_chart_scroll.setFrameShape(QFrame.NoFrame)
            self.throttle_chart_scroll.setWidget(self.throttle_canvas)
            self.throttle_chart_scroll.setToolTip(
                "窗口较小时可滚动查看完整曲线，或拖动中间分隔条放大图表。")
            right_layout.addWidget(self.throttle_chart_scroll)

            self._init_throttle_axes()          # 绘制占位框架
            splitter.addWidget(right)
            splitter.setStretchFactor(0, 0)
            splitter.setStretchFactor(1, 1)
            splitter.setSizes([300, 800])
        else:
            warn = QLabel(
                "⚠  matplotlib 未安装，无法显示图表\n"
                "请在终端运行：pip install matplotlib")
            warn.setAlignment(Qt.AlignCenter)
            warn.setObjectName("MutedLabel")
            splitter.addWidget(warn)
            splitter.setSizes([600, 300])

        root.addWidget(splitter)
        return w

    # ── 导出 PNG 通用助手（任意标签页复用：节流扫描 / 热力学对比 …）──────
    #   · 图表：对现有 Figure 直接 savefig（PNG 300dpi / 矢量 150），所见即所得。
    #   · 数据表：QTableWidget 只能按屏幕分辨率截屏，达不到 300dpi；故把表内容
    #     重渲成 matplotlib 表格再 savefig —— 真高清、含全部行、风格统一。

    def _export_figure_as_png(self, fig, default_name: str, gate_table=None):
        """把任意 matplotlib Figure 导出为高清图（PNG 300dpi / SVG / PDF）。

        gate_table：若提供且其 rowCount()==0，提示先运行（避免导出占位图）。
        """
        if fig is None:
            QMessageBox.information(self, "暂无图表",
                                    "matplotlib 未安装或图表未初始化。")
            return
        if gate_table is not None and gate_table.rowCount() == 0:
            QMessageBox.information(self, "暂无数据",
                                    "请先运行计算，生成曲线后再导出。")
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出图表为图片", default_name,
            "PNG 图片 (*.png);;SVG 矢量图 (*.svg);;PDF (*.pdf)")
        if not path:
            return
        try:
            save_dpi = 300 if path.lower().endswith(".png") else 150
            fig.savefig(path, dpi=save_dpi, bbox_inches="tight",
                        facecolor=fig.get_facecolor())
            self._append_log(f"已导出图表 PNG: {path}")
        except Exception as ex:
            QMessageBox.warning(self, "导出失败", f"无法导出图表：\n{ex}")

    def _export_table_as_png(self, table, title_text: str, default_name: str):
        """把任意 QTableWidget 渲染成 matplotlib 表格导出为高清图（300dpi PNG / PDF）。"""
        if table is None or table.rowCount() == 0:
            QMessageBox.information(self, "暂无数据",
                                    "请先运行计算，生成数据后再导出。")
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出数据表为图片", default_name,
            "PNG 图片 (*.png);;PDF (*.pdf)")
        if not path:
            return
        try:
            ncols, nrows = table.columnCount(), table.rowCount()
            headers = ["#"] + [
                (table.horizontalHeaderItem(c).text() if table.horizontalHeaderItem(c) else "")
                for c in range(ncols)]
            cell_text = []
            for r in range(nrows):
                row = [str(r + 1)]
                for c in range(ncols):
                    it = table.item(r, c)
                    row.append(it.text() if it is not None else "")
                cell_text.append(row)

            ncol_total = ncols + 1
            fig_w = max(13.0, ncol_total * 1.45)
            fig_h = max(2.8, 0.9 + (nrows + 1) * 0.36)
            fig = Figure(figsize=(fig_w, fig_h), facecolor="white", dpi=100)
            ax = fig.add_subplot(111)
            ax.axis("off")
            ax.set_title(title_text, fontsize=13, fontweight="bold",
                         color="#1F2937", pad=14)
            tbl = ax.table(cellText=cell_text, colLabels=headers,
                           loc="center", cellLoc="center")
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(9)
            tbl.scale(1, 1.55)
            for (r, c), cell in tbl.get_celld().items():
                cell.set_edgecolor("#D6DEE8")
                cell.set_linewidth(0.6)
                if r == 0:                       # 表头
                    cell.set_facecolor("#1F4E79")
                    cell.set_text_props(color="white", fontweight="bold")
                else:                            # 斑马纹
                    cell.set_facecolor("#FFFFFF" if (r % 2) else "#EEF4FB")
            fig.tight_layout()
            save_dpi = 300 if path.lower().endswith(".png") else 150
            fig.savefig(path, dpi=save_dpi, bbox_inches="tight", facecolor="white")
            self._append_log(f"已导出数据表 PNG: {path}")
        except Exception as ex:
            QMessageBox.warning(self, "导出失败", f"无法导出数据表：\n{ex}")

    # ── 各标签页导出入口（薄包装）──────────────────────────────────────

    def _export_throttle_charts_png(self):
        self._export_figure_as_png(
            getattr(self, "throttle_fig", None), "ffsc_throttle_charts.png",
            gate_table=getattr(self, "throttle_table", None))

    def _export_throttle_table_png(self):
        self._export_table_as_png(
            getattr(self, "throttle_table", None),
            "FFSC 节流扫描数据表 / Throttle Sweep Data",
            "ffsc_throttle_table.png")

    def _export_thermo_charts_png(self):
        self._export_figure_as_png(
            getattr(self, "thermo_fig", None), "ffsc_thermo_compare_charts.png",
            gate_table=getattr(self, "thermo_table", None))

    def _export_thermo_table_png(self):
        self._export_table_as_png(
            getattr(self, "thermo_table", None),
            "FFSC 热力学对比 / NASA CEA × Cantera",
            "ffsc_thermo_compare_table.png")

    # ── 整页(卡片/键值布局)导出：QWidget → 高清 PNG ─────────────────────
    # 总体性能 / 部件结果 不是表格也不是 matplotlib 图,而是自定义卡片布局。
    # 用 QWidget.render() 把"滚动区内容控件"(全高,含需滚动才能看到的部分)
    # 渲染到高 DPI QImage —— 所见即所得、含全部内容、真高清。

    def _export_widget_as_png(self, widget, default_name: str):
        """把任意 QWidget(通常是滚动区的内容控件)渲染成高清 PNG。"""
        if getattr(self, "_latest_results", None) is None:
            QMessageBox.information(self, "暂无结果",
                                    "请先运行设计点求解，生成结果后再导出。")
            return
        if widget is None or widget.height() < 5:
            QMessageBox.information(self, "暂无内容", "本页暂无可导出的内容。")
            return
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出本页为图片", default_name, "PNG 图片 (*.png)")
        if not path:
            return
        try:
            from PySide6.QtGui import QImage, QColor
            wlog, hlog = max(1, widget.width()), max(1, widget.height())
            scale = 300.0 / 96.0                      # 目标 ~300 dpi
            budget_px = 60.0e6                         # 像素上限(超大页降档防爆内存)
            if wlog * scale * hlog * scale > budget_px:
                scale = max(1.0, (budget_px / (wlog * hlog)) ** 0.5)
            W, H = int(wlog * scale), int(hlog * scale)
            img = QImage(W, H, QImage.Format_ARGB32)
            img.setDevicePixelRatio(scale)
            img.fill(QColor("#FFFFFF"))
            widget.render(img)                         # 渲染整控件(含滚动外内容)
            eff_dpi = scale * 96.0
            dpm = int(round(eff_dpi / 0.0254))
            img.setDotsPerMeterX(dpm)
            img.setDotsPerMeterY(dpm)
            if not img.save(path, "PNG"):
                raise RuntimeError("QImage.save 返回 False")
            self._append_log(f"已导出本页 PNG: {path}（{W}×{H}px, ~{eff_dpi:.0f} dpi）")
        except Exception as ex:
            QMessageBox.warning(self, "导出失败", f"无法导出本页：\n{ex}")

    def _export_card_as_png(self, card, btn, default_name: str):
        """把单张部件卡片导出为高清 PNG（复用整页渲染逻辑）。
        渲染前临时隐藏卡片上的"导出"按钮本身，避免它被拍进图里。"""
        try:
            btn.hide()
        except Exception:
            pass
        try:
            self._export_widget_as_png(card, default_name)
        finally:
            try:
                btn.show()
            except Exception:
                pass

    def _wrap_tab_with_export(self, scroll_widget, content_attr: str, default_name: str):
        """把一个滚动页包成 [顶部导出按钮行 + 滚动区]。

        导出渲染的是 content_attr 指向的"内容控件"(滚动区全高内容),
        顶部按钮本身不在该控件内,故不会被截进图里。
        """
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 12, 0)
        bar.addStretch(1)
        btn = QPushButton("📷 导出本页 PNG")
        btn.setToolTip("把本页全部内容（含需滚动查看的部分）导出为高清 PNG")
        btn.clicked.connect(
            lambda: self._export_widget_as_png(getattr(self, content_attr, None), default_name))
        bar.addWidget(btn)
        v.addLayout(bar)
        v.addWidget(scroll_widget)
        return container

    # ── 节流扫描图表：初始化与刷新 ──────────────────────────────────────

    def _init_throttle_axes(self, message="点击「节流扫描」后自动绘制"):
        """绘制初始占位图（未跑数据时显示说明文字）。"""
        self.throttle_fig.clear()
        self.throttle_fig.patch.set_facecolor("#FAFAFA")

        gs = self.throttle_fig.add_gridspec(2, 2)
        self._ax_thrust = self.throttle_fig.add_subplot(gs[0, 0])
        self._ax_isp    = self.throttle_fig.add_subplot(gs[0, 1])
        self._ax_flow   = self.throttle_fig.add_subplot(gs[1, 0])
        self._ax_temp   = self.throttle_fig.add_subplot(gs[1, 1])
        # A sweep streams multiple updates. Create its secondary axis once;
        # clearing the primary axis does not remove axes created by twinx().
        self._ax_mdot = self._ax_flow.twinx()
        self._ax_mdot.set_visible(False)

        subtitles = [
            (self._ax_thrust, "推力跟随"),
            (self._ax_isp,    "比冲"),
            (self._ax_flow,   "室压与总流量"),
            (self._ax_temp,   "温度分布"),
        ]
        for ax, title in subtitles:
            self._style_ax_base(ax)
            ax.set_title(title, fontsize=10, fontweight="bold",
                         color="#111827", pad=7)
            ax.text(0.5, 0.5, message,
                    ha="center", va="center", transform=ax.transAxes,
                    color="#9CA3AF", fontsize=9, style="italic")
        self.throttle_canvas.draw()

    @staticmethod
    def _style_ax_base(ax, facecolor="#F8F9FA"):
        """统一应用专业风格到一个 Axes。"""
        ax.set_facecolor(facecolor)
        ax.grid(True, color="#E5E7EB", linewidth=0.75,
                linestyle="--", alpha=0.85)
        ax.tick_params(labelsize=8.5, colors="#4B5563",
                       length=3, width=0.7)
        for spine in ax.spines.values():
            spine.set_edgecolor("#D1D5DB")
            spine.set_linewidth(0.8)

    @staticmethod
    def _label_ax(ax, title, xlabel, ylabel, legend=True):
        """应用标题 / 轴标签 / 图例。"""
        ax.set_title(title, fontsize=10, fontweight="bold",
                     color="#111827", pad=7)
        ax.set_xlabel(xlabel, fontsize=8.5, color="#4B5563", labelpad=4)
        ax.set_ylabel(ylabel, fontsize=8.5, color="#4B5563", labelpad=4)
        if legend:
            ax.legend(fontsize=8.0, framealpha=0.92,
                      edgecolor="#E5E7EB", loc="best",
                      handlelength=2.0, borderpad=0.6)

    def _update_throttle_charts(self, sweep: List[dict]):
        """根据扫描结果刷新四张子图（仅使用已收敛点）。"""
        if not MATPLOTLIB_AVAILABLE:
            return

        # ── 采集数据（仅收敛点）────────────────────────────────────────
        x, f_act, f_tgt = [], [], []
        isp_list, pc_list, mdot_list = [], [], []
        t_ch, t_ox, t_fuel, t_gg = [], [], [], []
        is_gg = False

        for pt in sweep:
            if not pt.get("converged"):
                continue
            res = pt.get("results")
            s   = res.summary if res else {}
            x.append(pt.get("throttle_ratio", 0) * 100)
            f_act.append(s.get("thrust_N",         0) / 1e3)
            f_tgt.append(pt.get("thrust_target_N", 0) / 1e3)
            isp_list.append(s.get("Isp_s",               0))
            pc_list.append( pt.get("pc_Pa",              0) / 1e6)
            mdot_list.append(s.get("mdot_total_kg_s",    0))
            t_ch.append(  s.get("chamber_temperature_K",         0))
            t_ox.append(  s.get("ox_preburner_T_combustion_K",   0))
            t_fuel.append(s.get("fuel_preburner_T_combustion_K", 0))
            t_gg.append(s.get("gg_T_combustion_K", 0))
            is_gg = is_gg or "gg_T_combustion_K" in s

        if not x:
            self._init_throttle_axes(
                "暂无已收敛点" if sweep else "点击「节流扫描」后自动绘制")
            return

        # ── 调色盘（工业软件常用）──────────────────────────────────────
        C_BLUE   = "#1D4ED8"
        C_ORANGE = "#D97706"
        C_GREEN  = "#059669"
        C_RED    = "#DC2626"
        C_GRAY   = "#9CA3AF"

        # 标记样式
        mk = dict(marker="o", markersize=5,
                  markerfacecolor="white", markeredgewidth=1.6)

        XLABEL = "节流比 [%]"

        # ── 子图1：推力跟随 ────────────────────────────────────────────
        ax = self._ax_thrust
        ax.clear()
        self._style_ax_base(ax)
        ax.plot(x, f_act, color=C_BLUE,   lw=2.0, label="实际推力",  **mk)
        ax.plot(x, f_tgt, color=C_GRAY,   lw=1.5, ls="--",
                marker="x", markersize=5, markeredgewidth=1.5,
                label="目标推力")
        self._label_ax(ax, "推力跟随", XLABEL, "推力 [kN]")

        # ── 子图2：比冲 ───────────────────────────────────────────────
        ax = self._ax_isp
        ax.clear()
        self._style_ax_base(ax)
        ax.plot(x, isp_list, color=C_GREEN, lw=2.0, label="Isp", **mk)
        ax.fill_between(x, isp_list,
                        alpha=0.08, color=C_GREEN)        # 面积渐变
        self._label_ax(ax, "比冲",
                       XLABEL, "Isp [s]", legend=False)

        # ── 子图3：室压 & 质量流量（双Y轴）──────────────────────────────
        ax = self._ax_flow
        ax.clear()
        self._style_ax_base(ax)
        l1, = ax.plot(x, pc_list,   color=C_BLUE,   lw=2.0,
                      label="室压", **mk)
        ax2 = self._ax_mdot
        ax2.clear()
        ax2.set_visible(True)
        # clear() resets the axis-label side even on a twinned axis.
        ax2.yaxis.set_label_position("right")
        ax2.yaxis.tick_right()
        self._style_ax_base(ax2, facecolor="none")   # 透明，共享背景
        ax2.grid(False)
        l2, = ax2.plot(x, mdot_list, color=C_ORANGE, lw=2.0,
                       label="总流量", **mk)

        ax.set_title("室压与总流量",
                     fontsize=10, fontweight="bold", color="#111827", pad=7)
        ax.set_xlabel(XLABEL, fontsize=8.5, color="#4B5563", labelpad=4)
        ax.set_ylabel("室压 [MPa]",    fontsize=8.5, color=C_BLUE,   labelpad=4)
        ax2.set_ylabel("总流量 [kg/s]", fontsize=8.5, color=C_ORANGE, labelpad=4)
        ax.tick_params(axis="y", colors=C_BLUE,   labelsize=8.5)
        ax2.tick_params(axis="y", colors=C_ORANGE, labelsize=8.5)
        ax.tick_params(axis="x", labelsize=8.5, colors="#4B5563")
        ax.legend([l1, l2], [l1.get_label(), l2.get_label()],
                  fontsize=8.0, framealpha=0.92, edgecolor="#E5E7EB",
                  loc="upper left", handlelength=2.0)

        # ── 子图4：本次循环的燃烧部件温度 ───────────────────────────────
        ax = self._ax_temp
        ax.clear()
        self._style_ax_base(ax)
        ax.plot(x, t_ch,   color=C_RED,    lw=2.0, label="主燃烧室",      **mk)
        if is_gg:
            ax.plot(x, t_gg, color=C_ORANGE, lw=2.0, label="燃气发生器", **mk)
        else:
            ax.plot(x, t_ox,   color=C_BLUE,   lw=2.0, label="富氧预燃室",    **mk)
            ax.plot(x, t_fuel, color=C_ORANGE, lw=2.0, label="富燃预燃室",    **mk)
        self._label_ax(ax, "温度分布",
                       XLABEL, "温度 T [K]")

        self.throttle_fig.canvas.draw_idle()

    # ──────────────────────────────────────────────────────────────
    # 发动机特性页（方案B）：节流特性 ｜ 高度特性
    # ──────────────────────────────────────────────────────────────
    def _build_engchar_tab(self) -> QWidget:
        """发动机两大经典特性曲线合并页：内嵌「节流特性 | 高度特性」两个子页。

        · 节流特性：固定硬件下推力/室压/比冲/流量随节流变化（已有 throttle 视图）。
        · 高度特性：固定喷管下推力/比冲随飞行高度（环境压力）变化（新增）。
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.engchar_tabs = QTabWidget()
        self.engchar_tabs.addTab(self._build_throttle_tab(),     "节流特性")
        self.engchar_tabs.addTab(self._build_altitude_subtab(),  "高度特性")
        lay.addWidget(self.engchar_tabs)
        return w

    def _build_altitude_subtab(self) -> QWidget:
        """高度特性子页：左侧要点读数 + 右侧 推力/比冲–高度 曲线。"""
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ── 左：要点读数 ─────────────────────────────────────────────
        left = QFrame()
        left.setObjectName("ResultCard")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 10, 6, 10)
        title = QLabel("高度特性要点 / Altitude Characteristics")
        title.setObjectName("CardTitle")
        ll.addWidget(title)

        self.altitude_table = QTableWidget(0, 2)
        self.altitude_table.setHorizontalHeaderLabels(["参数", "数值"])
        self.altitude_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.altitude_table.verticalHeader().setVisible(False)
        self.altitude_table.setAlternatingRowColors(True)
        self.altitude_table.setSelectionBehavior(QTableWidget.SelectRows)
        ll.addWidget(self.altitude_table)

        note = QLabel(
            "固定喷管（设计点几何）下，满流喷管出口压力 Pe 与飞行高度无关，故\n"
            "F(z) = F_vac − P_a(z)·A_e 对环境压力严格线性：高度升高、P_a 下降，\n"
            "推力与比冲单调升至真空值。绿色竖线 = 完全膨胀高度（Pe = P_a）；其下方\n"
            "为过膨胀、上方为欠膨胀。设计点求解后自动刷新，无需单独运行。")
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        ll.addWidget(note)
        splitter.addWidget(left)

        # ── 右：图表 ─────────────────────────────────────────────────
        if MATPLOTLIB_AVAILABLE:
            right = QFrame()
            right.setObjectName("ResultCard")
            rl = QVBoxLayout(right)
            rl.setContentsMargins(6, 10, 10, 10)
            hdr = QHBoxLayout()
            ct = QLabel("高度特性曲线 / Altitude Performance")
            ct.setObjectName("CardTitle")
            hdr.addWidget(ct)
            hdr.addStretch(1)
            btn = QPushButton("📷 导出 PNG")
            btn.setToolTip("把高度特性曲线导出为 300 dpi 高清 PNG（或 SVG / PDF）")
            btn.clicked.connect(self._export_altitude_charts_png)
            hdr.addWidget(btn)
            rl.addLayout(hdr)

            self.altitude_fig = Figure(figsize=(8, 8), facecolor="#FAFAFA")
            self.altitude_canvas = FigureCanvasQTAgg(self.altitude_fig)
            self.altitude_canvas.setSizePolicy(
                QSizePolicy.Expanding, QSizePolicy.Expanding)
            rl.addWidget(self.altitude_canvas)
            self._init_altitude_axes()
            splitter.addWidget(right)
            splitter.setSizes([360, 740])
        else:
            warn = QLabel("⚠  matplotlib 未安装，无法显示图表\n请运行：pip install matplotlib")
            warn.setAlignment(Qt.AlignCenter)
            warn.setObjectName("MutedLabel")
            splitter.addWidget(warn)
            splitter.setSizes([600, 300])

        root.addWidget(splitter)
        return w

    def _init_altitude_axes(self):
        """高度特性占位图（未求解时显示说明文字）。"""
        self.altitude_fig.clear()
        self.altitude_fig.patch.set_facecolor("#FAFAFA")
        gs = self.altitude_fig.add_gridspec(
            2, 1, hspace=0.40, left=0.13, right=0.95, top=0.94, bottom=0.09)
        self._ax_alt_F   = self.altitude_fig.add_subplot(gs[0, 0])
        self._ax_alt_isp = self.altitude_fig.add_subplot(gs[1, 0])
        for ax, title in [
            (self._ax_alt_F,   "推力 — 高度特性 / Thrust vs Altitude"),
            (self._ax_alt_isp, "比冲 — 高度特性 / Specific Impulse vs Altitude"),
        ]:
            self._style_ax_base(ax)
            ax.set_title(title, fontsize=10, fontweight="bold", color="#111827", pad=7)
            ax.text(0.5, 0.5, "运行设计点求解后自动绘制",
                    ha="center", va="center", transform=ax.transAxes,
                    color="#9CA3AF", fontsize=9, style="italic")
        self.altitude_canvas.draw()

    def _update_altitude_charts(self, data: dict):
        """根据 altitude_sweep 结果刷新 推力/比冲–高度 两张子图。"""
        if not MATPLOTLIB_AVAILABLE or not hasattr(self, "altitude_fig"):
            return
        pts  = data.get("points", [])
        info = data.get("info", {})
        if not pts:
            return
        z   = [p["altitude_km"] for p in pts]
        F   = [p["thrust_kN"]   for p in pts]
        isp = [p["Isp_s"]       for p in pts]

        C_BLUE  = "#1D4ED8"; C_GREEN = "#059669"; C_GRAY = "#9CA3AF"
        C_RED   = "#DC2626"; C_AMBER = "#D97706"
        mk = dict(marker="o", markersize=3.2, markerfacecolor="white", markeredgewidth=1.1)

        F_vac   = info.get("F_vac_N", 0.0) / 1e3
        F_sl    = info.get("F_sl_N", 0.0) / 1e3
        isp_vac = info.get("Isp_vac_s", 0.0)
        isp_sl  = info.get("Isp_sl_s", 0.0)
        z_match = info.get("match_altitude_m", 0.0) / 1000.0
        z_max   = info.get("z_max_m", 80000.0) / 1000.0
        sep_sl  = bool(info.get("separated_at_sea_level", False))
        z_sep   = info.get("separation_altitude_m", 0.0) / 1000.0

        # ── 推力 – 高度 ──
        ax = self._ax_alt_F
        ax.clear(); self._style_ax_base(ax)
        ax.plot(z, F, color=C_BLUE, lw=2.0, label="推力 F(z)", **mk)
        ax.axhline(F_vac, color=C_GRAY, ls="--", lw=1.2,
                   label=f"真空推力 {F_vac:.0f} kN")
        ax.scatter([0], [F_sl], color=C_RED, zorder=5, s=30)
        ax.annotate(f"海平面 {F_sl:.0f} kN", xy=(0.0, F_sl),
                    xytext=(z_max * 0.16, F_sl + (F_vac - F_sl) * 0.34),
                    fontsize=8, color=C_RED, ha="left", va="center",
                    arrowprops=dict(arrowstyle="->", color=C_RED, lw=0.9))
        if 0.0 < z_match < z_max:
            ax.axvline(z_match, color=C_GREEN, ls=":", lw=1.3)
            ax.text(z_match + z_max * 0.012, F_sl + (F_vac - F_sl) * 0.05,
                    f"完全膨胀 Pe=Pa\n@ {z_match:.1f} km",
                    fontsize=7.5, color=C_GREEN, ha="left", va="bottom")
        if sep_sl and 0.0 < z_sep < z_max:
            ax.axvspan(0.0, z_sep, color=C_AMBER, alpha=0.10)
            ax.text(z_sep * 0.5, F_vac, "过膨胀分离区",
                    fontsize=7.5, color=C_AMBER, ha="center", va="top")
        frange = max(F_vac - F_sl, 1.0)
        ax.set_ylim(F_sl - frange * 0.12, F_vac + frange * 0.20)
        self._label_ax(ax, "推力 — 高度特性 / Thrust vs Altitude",
                       "高度 z [km]", "推力 F [kN]")

        # ── 比冲 – 高度 ──
        ax = self._ax_alt_isp
        ax.clear(); self._style_ax_base(ax)
        isp_lo = min(isp)
        isp_hi = max(max(isp), isp_vac)
        pad = max((isp_hi - isp_lo) * 0.20, 1.5)
        y0, y1 = isp_lo - pad, isp_hi + pad
        ax.plot(z, isp, color=C_GREEN, lw=2.0, label="比冲 Isp(z)", **mk)
        ax.fill_between(z, isp, y0, alpha=0.08, color=C_GREEN)
        ax.axhline(isp_vac, color=C_GRAY, ls="--", lw=1.2,
                   label=f"真空比冲 {isp_vac:.1f} s")
        ax.scatter([0], [isp_sl], color=C_RED, zorder=5, s=30,
                   label=f"海平面 {isp_sl:.1f} s")
        if 0.0 < z_match < z_max:
            ax.axvline(z_match, color=C_GREEN, ls=":", lw=1.3)
        ax.set_ylim(y0, y1)
        self._label_ax(ax, "比冲 — 高度特性 / Specific Impulse vs Altitude",
                       "高度 z [km]", "比冲 Isp [s]")

        self.altitude_fig.canvas.draw_idle()

    def _populate_altitude_readout(self, data: dict):
        """高度特性左侧要点读数表。"""
        if not hasattr(self, "altitude_table"):
            return
        info = data.get("info", {})
        pts  = data.get("points", [])
        F_vac = info.get("F_vac_N", 0.0) / 1e3
        F_sl  = info.get("F_sl_N", 0.0) / 1e3
        gain  = (F_vac - F_sl) / F_sl * 100.0 if F_sl else 0.0
        sl_state = pts[0]["expansion_state"] if pts else "—"
        rows = [
            ("真空推力 F_vac [kN]",       f"{F_vac:.1f}"),
            ("海平面推力 F_sl [kN]",      f"{F_sl:.1f}"),
            ("真空增益 ΔF [%]",           f"{gain:.1f}"),
            ("真空比冲 Isp_vac [s]",      f"{info.get('Isp_vac_s', 0.0):.1f}"),
            ("海平面比冲 Isp_sl [s]",     f"{info.get('Isp_sl_s', 0.0):.1f}"),
            ("出口面积 Ae [m²]",          f"{info.get('Ae_m2', 0.0):.4f}"),
            ("出口压力 Pe [kPa]",         f"{info.get('pe_Pa', 0.0) / 1e3:.2f}"),
            ("膨胀比 ε [-]",              f"{info.get('eps', 0.0):.1f}"),
            ("完全膨胀高度 Pe=Pa [km]",   f"{info.get('match_altitude_m', 0.0) / 1000.0:.2f}"),
            ("海平面膨胀状态",            sl_state),
            ("海平面流动分离",            "是" if info.get("separated_at_sea_level") else "否"),
            ("总流量 ṁ [kg/s]",          f"{info.get('mdot_kg_s', 0.0):.1f}"),
        ]
        self.altitude_table.setRowCount(0)
        for k, v in rows:
            self._add_table_row(self.altitude_table, [k, v])
        self.altitude_table.resizeColumnsToContents()
        self.altitude_table.horizontalHeader().setStretchLastSection(True)

    def _populate_altitude(self, results: CycleResults):
        """设计点求解完成后自动计算并刷新高度特性（复用设计点结果，零额外求解）。"""
        if not MATPLOTLIB_AVAILABLE or not hasattr(self, "altitude_fig"):
            return
        try:
            data = make_cycle_model(results.inputs).altitude_sweep(design_results=results)
        except Exception as ex:
            try:
                self._log_warn(f"高度特性计算失败：{ex}", source="Solver")
            except Exception:
                pass
            return
        self._update_altitude_charts(data)
        self._populate_altitude_readout(data)

    def _export_altitude_charts_png(self):
        self._export_figure_as_png(
            getattr(self, "altitude_fig", None),
            "ffsc_altitude_characteristic.png",
            gate_table=getattr(self, "altitude_table", None))

    # ──────────────────────────────────────────────────────────────
    # 日志面板（V5.4.4：ANSYS Fluent 风格 — 等级图标 / 颜色 / 过滤 / 状态条）
    # ──────────────────────────────────────────────────────────────
    _LOG_LEVEL_STYLE = {
        # level → (icon, color_hex, label_4char, qss_pill)
        "INFO":  ("ℹ", "#2563EB", "INFO", "PillNeutral"),
        "STEP":  ("▷", "#7C3AED", "STEP", "PillNeutral"),
        "OK":    ("✓", "#15803D", " OK ", "PillOk"),
        "WARN":  ("⚠", "#B45309", "WARN", "PillWarn"),
        "ERROR": ("✗", "#B91C1C", "ERR ", "PillError"),
    }
    _LOG_FILTER_BUTTONS = [
        ("ALL",   "全部"),
        ("INFO",  "ℹ 信息"),
        ("STEP",  "▷ 步骤"),
        ("OK",    "✓ 成功"),
        ("WARN",  "⚠ 警告"),
        ("ERROR", "✗ 错误"),
    ]

    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # 内部状态
        self._log_entries: List[Tuple[str, str, str, str]] = []   # (ts, level, source, message)
        self._log_filter: str = "ALL"
        self._log_warn_count: int = 0
        self._log_err_count: int = 0

        # ── 顶部状态条（三个 pill：当前操作 / 收敛标志 / 警告/错误计数）──
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        title_lbl = QLabel("当前状态")
        title_lbl.setObjectName("CardTitle")
        status_row.addWidget(title_lbl)

        self.log_status_op = QLabel("就绪 / Ready")
        self.log_status_op.setObjectName("PillNeutral")
        status_row.addWidget(self.log_status_op)

        self.log_status_conv = QLabel("· 未求解")
        self.log_status_conv.setObjectName("PillNeutral")
        status_row.addWidget(self.log_status_conv)

        self.log_status_warn = QLabel("⚠ 0 警告")
        self.log_status_warn.setObjectName("PillNeutral")
        status_row.addWidget(self.log_status_warn)

        self.log_status_err = QLabel("✗ 0 错误")
        self.log_status_err.setObjectName("PillNeutral")
        status_row.addWidget(self.log_status_err)

        status_row.addStretch(1)
        layout.addLayout(status_row)

        # ── 过滤按钮 + 工具按钮 ────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        filter_lbl = QLabel("过滤:")
        filter_lbl.setObjectName("MutedLabel")
        filter_row.addWidget(filter_lbl)

        self._log_filter_buttons: Dict[str, QPushButton] = {}
        for lvl, text in self._LOG_FILTER_BUTTONS:
            btn = QPushButton(text)
            btn.setObjectName("SecondaryButton")
            btn.setCheckable(True)
            btn.setChecked(lvl == "ALL")
            btn.clicked.connect(lambda _checked, l=lvl: self._set_log_filter(l))
            self._log_filter_buttons[lvl] = btn
            filter_row.addWidget(btn)

        filter_row.addStretch(1)

        self._btn_log_clear = QPushButton("🗑 清空")
        self._btn_log_clear.setObjectName("SecondaryButton")
        self._btn_log_clear.clicked.connect(self._clear_log)
        filter_row.addWidget(self._btn_log_clear)

        self._btn_log_save = QPushButton("💾 导出日志")
        self._btn_log_save.setObjectName("SecondaryButton")
        self._btn_log_save.clicked.connect(self._save_log)
        filter_row.addWidget(self._btn_log_save)

        layout.addLayout(filter_row)

        # ── 富文本日志主体 ─────────────────────────────────
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogText")
        layout.addWidget(self.log, 1)

        # 启动欢迎信息
        self._log_info("FFSC 数字样机已启动 — V6.2.1", source="System")
        self._log_info(f"热力学引擎: {'Cantera 已就绪' if CANTERA_AVAILABLE else '⚠ Cantera 不可用，使用经验回退'}",
                       source="System")
        self._log_info("点击「运行设计点」开始 Newton 迭代；可同时打开「节流扫描」做包线分析。",
                       source="Hint")
        return w

    # ─── 日志写入 API ─────────────────────────────────────
    def _log_entry(self, level: str, message: str, source: str = ""):
        ts = datetime.now().strftime("%H:%M:%S")
        # 多行消息按行拆分入库（每行单独着色），便于过滤
        for line in str(message).splitlines() or [""]:
            self._log_entries.append((ts, level, source, line))
        if level == "WARN":
            self._log_warn_count += 1
        elif level == "ERROR":
            self._log_err_count += 1
        self._refresh_log_display()
        self._refresh_log_status_badges()

    def _log_info(self, msg: str, source: str = ""):    self._log_entry("INFO", msg, source)
    def _log_step(self, msg: str, source: str = ""):    self._log_entry("STEP", msg, source)
    def _log_ok(self, msg: str, source: str = ""):      self._log_entry("OK", msg, source)
    def _log_warn(self, msg: str, source: str = ""):    self._log_entry("WARN", msg, source)
    def _log_error(self, msg: str, source: str = ""):   self._log_entry("ERROR", msg, source)

    def _set_log_filter(self, level: str):
        self._log_filter = level
        for k, btn in self._log_filter_buttons.items():
            btn.setChecked(k == level)
        self._refresh_log_display()

    def _refresh_log_display(self):
        # HTML 拼装：等宽字体 + 等级图标 + 颜色 + 时间戳
        parts = [
            "<div style=\"font-family:'Cascadia Mono',Consolas,'Microsoft YaHei',monospace;"
            "font-size:12px;line-height:1.55;\">"
        ]
        if not self._log_entries:
            parts.append(
                "<div style=\"color:#9CA3AF;font-style:italic;padding:8px;\">"
                "（暂无日志条目。点击「运行设计点」或「节流扫描」开始记录。）"
                "</div>"
            )
        for ts, level, source, msg in self._log_entries:
            if self._log_filter != "ALL" and self._log_filter != level:
                continue
            icon, color, tag, _ = self._LOG_LEVEL_STYLE.get(
                level, ("·", "#374151", "    ", "PillNeutral")
            )
            src_html = (
                f"<span style=\"color:#9CA3AF;\">[{self._html_escape(source)}]</span> "
                if source else ""
            )
            safe_msg = self._html_escape(msg)
            parts.append(
                "<div style=\"padding:1px 0;\">"
                f"<span style=\"color:#94A3B8;\">{ts}</span>&nbsp;&nbsp;"
                f"<span style=\"color:{color};font-weight:800;\">{icon} {tag}</span>&nbsp;"
                f"{src_html}<span style=\"color:#111827;\">{safe_msg}</span>"
                "</div>"
            )
        parts.append("</div>")
        self.log.setHtml("".join(parts))
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _refresh_log_status_badges(self):
        w = self._log_warn_count
        e = self._log_err_count
        self.log_status_warn.setText(f"⚠ {w} 警告")
        self.log_status_warn.setObjectName("PillWarn" if w > 0 else "PillNeutral")
        self.log_status_err.setText(f"✗ {e} 错误")
        self.log_status_err.setObjectName("PillError" if e > 0 else "PillNeutral")
        for pill in (self.log_status_warn, self.log_status_err):
            pill.style().unpolish(pill)
            pill.style().polish(pill)

    def _set_log_status(self, op: str, conv: str = None, conv_kind: str = "neutral"):
        """更新顶部状态条：当前操作 + 收敛标志。"""
        self.log_status_op.setText(op)
        if conv is not None:
            self.log_status_conv.setText(conv)
            obj = {
                "ok":      "PillOk",
                "warn":    "PillWarn",
                "error":   "PillError",
                "neutral": "PillNeutral",
            }.get(conv_kind, "PillNeutral")
            self.log_status_conv.setObjectName(obj)
            self.log_status_conv.style().unpolish(self.log_status_conv)
            self.log_status_conv.style().polish(self.log_status_conv)

    def _clear_log(self):
        self._log_entries.clear()
        self._log_warn_count = 0
        self._log_err_count = 0
        self._refresh_log_display()
        self._refresh_log_status_badges()
        self._log_info("日志已清空", source="User")

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出日志", "ffsc_log.txt", "Text (*.txt);;All files (*)"
        )
        if not path:
            return
        lines = []
        for ts, lvl, src, msg in self._log_entries:
            src_tag = f"[{src}] " if src else ""
            lines.append(f"[{ts}] [{lvl:5}] {src_tag}{msg}")
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        self._log_ok(f"日志已导出: {path}", source="User")

    @staticmethod
    def _html_escape(s: str) -> str:
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;")
        )

    # ──────────────────────────────────────────────────────────────
    # 涡轮设计点扫描标签页 (V9)：预燃室 O/F 扫描 → CFTurbo 输入
    # ──────────────────────────────────────────────────────────────

    # 表头:中文 + 符号双语,每列对应 CFTurbo Global Setup 的关键输入字段。
    # 移除 Isp(扫描 PB 时主燃室性能基本不变,无诊断价值)。
    # 新增 残差 列,让用户直接看到收敛程度,而不只是 ✓/✗。
    _OF_SWEEP_COLUMNS = [
        ("O/F 目标",                 "of_target",            "{:.2f}"),
        ("预燃室温度 T_PB [K]",      "T_PB_K",               "{:.1f}"),
        ("涡轮压比 π_t",              "pi_t",                 "{:.4f}"),
        ("入口压力 p_in [MPa]",      "p_in_MPa",             "{:.2f}"),
        ("出口压力 p_out [MPa]",     "p_out_MPa",            "{:.2f}"),
        ("流量 ṁ [kg/s]",            "mass_flow_kgs",        "{:.2f}"),
        ("定压比热 cp [J/kg·K]",      "cp_Jkgk",              "{:.0f}"),
        ("比热比 γ",                  "gamma",                "{:.3f}"),
        ("气体常数 R [J/kg·K]",      "R_specific_Jkgk",      "{:.1f}"),
        ("摩尔质量 MW [kg/kmol]",    "molar_mass_kg_kmol",   "{:.2f}"),
        ("等熵焓降 Δh_s [kJ/kg]",     "isentropic_dh_kJkg",   "{:.1f}"),
        ("涡轮功率 [MW]",            "turbine_power_MW",     "{:.2f}"),
        ("残差",                      "residual",             "{:.2e}"),
        ("收敛",                      "converged",            "{}"),
    ]

    def _build_of_sweep_tab(self) -> QWidget:
        """
        涡轮设计点扫描 Tab：
        ┌──────────────────────────────────────────────────────────┐
        │ 顶部：扫描配置（哪侧预燃室 + O/F 起止/步长 + 开始按钮）        │
        ├──────────────────────────────────────────────────────────┤
        │ 中部：扫描结果表（每行一个候选设计点）                       │
        ├──────────────────────────────────────────────────────────┤
        │ 底部：操作按钮（导出 CSV + 应用此设计点 + 提示文字）          │
        └──────────────────────────────────────────────────────────┘
        """
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # 顶部说明
        intro = QLabel(
            "扫描预燃室 O/F，比较出口温度、分流与循环匹配；运行上限由方案单独设定，不能由气体温度推定材料寿命。"
            "选中一行可直接获得 CFTurbo Global Setup 所需的 (T_in, p_in, p_out, π_t, ṁ, cp, γ, R, MW)。\n"
            "提示：T_PB 是使用当前入口焓与热化学假设得到的计算值；不是金属温度，也不是已验证的实测温度。"
        )
        intro.setObjectName("MutedLabel")
        intro.setWordWrap(True)
        root.addWidget(intro)

        # ── 顶部：配置区 ───────────────────────────────────────
        cfg = QFrame()
        cfg.setObjectName("ResultCard")
        cfg_layout = QGridLayout(cfg)
        cfg_layout.setContentsMargins(10, 10, 10, 10)
        cfg_layout.setHorizontalSpacing(8)
        cfg_layout.setVerticalSpacing(6)

        cfg_layout.addWidget(QLabel("扫描对象"), 0, 0)
        self.of_sweep_side = QComboBox()
        self.of_sweep_side.addItems([
            "富氧预燃室 (Oxidizer Preburner)",
            "富燃预燃室 (Fuel Preburner)",
        ])
        self.of_sweep_side.currentIndexChanged.connect(self._on_of_sweep_side_changed)
        cfg_layout.addWidget(self.of_sweep_side, 0, 1, 1, 3)

        cfg_layout.addWidget(QLabel("O/F 起始"), 1, 0)
        self.of_sweep_start = QLineEdit("60")
        cfg_layout.addWidget(self.of_sweep_start, 1, 1)
        cfg_layout.addWidget(QLabel("O/F 终止"), 1, 2)
        self.of_sweep_end = QLineEdit("110")
        cfg_layout.addWidget(self.of_sweep_end, 1, 3)
        cfg_layout.addWidget(QLabel("步长"), 1, 4)
        self.of_sweep_step = QLineEdit("5")
        cfg_layout.addWidget(self.of_sweep_step, 1, 5)

        self.btn_run_of_sweep = QPushButton("▶ 开始扫描")
        self.btn_run_of_sweep.setObjectName("PrimaryButton")
        self.btn_run_of_sweep.clicked.connect(self._on_run_of_sweep_clicked)
        cfg_layout.addWidget(self.btn_run_of_sweep, 1, 6)

        root.addWidget(cfg)

        # ── 中部：结果表 ───────────────────────────────────────
        table_card = QFrame()
        table_card.setObjectName("ResultCard")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(10, 10, 10, 10)

        title = QLabel("扫描结果 / Sweep Results")
        title.setObjectName("CardTitle")
        table_layout.addWidget(title)

        self.of_sweep_table = QTableWidget(0, len(self._OF_SWEEP_COLUMNS))
        self.of_sweep_table.setHorizontalHeaderLabels(
            [c[0] for c in self._OF_SWEEP_COLUMNS]
        )
        self.of_sweep_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.of_sweep_table.horizontalHeader().setStretchLastSection(True)
        self.of_sweep_table.setAlternatingRowColors(True)
        self.of_sweep_table.setSelectionBehavior(QTableWidget.SelectRows)
        table_layout.addWidget(self.of_sweep_table, 1)

        root.addWidget(table_card, 1)

        # ── 底部：操作区 ───────────────────────────────────────
        btn_row = QHBoxLayout()
        self.btn_of_sweep_export = QPushButton("📤 导出 CSV (CFTurbo 输入表)")
        self.btn_of_sweep_export.setObjectName("SecondaryButton")
        self.btn_of_sweep_export.clicked.connect(self._on_of_sweep_export)
        btn_row.addWidget(self.btn_of_sweep_export)

        # V10：把扫描结果表导出为 300 dpi 高清 PNG（与其它标签页一致，复用通用助手）
        self.btn_of_sweep_png = QPushButton("📷 导出 PNG (结果表)")
        self.btn_of_sweep_png.setObjectName("SecondaryButton")
        self.btn_of_sweep_png.setToolTip("把扫描结果表导出为 300 dpi 高清 PNG（或 PDF）")
        self.btn_of_sweep_png.clicked.connect(self._on_of_sweep_export_png)
        btn_row.addWidget(self.btn_of_sweep_png)

        self.btn_of_sweep_apply = QPushButton("✓ 应用选中行为设计点")
        self.btn_of_sweep_apply.setObjectName("SecondaryButton")
        self.btn_of_sweep_apply.clicked.connect(self._on_of_sweep_apply)
        btn_row.addWidget(self.btn_of_sweep_apply)
        btn_row.addStretch(1)

        self.of_sweep_hint = QLabel("点击「开始扫描」运行 O/F 扫描。")
        self.of_sweep_hint.setObjectName("MutedLabel")
        btn_row.addWidget(self.of_sweep_hint)

        root.addLayout(btn_row)
        return w

    def _on_of_sweep_side_changed(self, idx: int):
        """切换扫描对象时，给一个合理的默认 O/F 范围。"""
        if idx == 0:
            # 富氧侧：典型 60–110
            self.of_sweep_start.setText("60")
            self.of_sweep_end.setText("110")
            self.of_sweep_step.setText("5")
        else:
            # 富燃侧：典型 0.05–0.30
            self.of_sweep_start.setText("0.10")
            self.of_sweep_end.setText("0.25")
            self.of_sweep_step.setText("0.02")

    def _on_run_of_sweep_clicked(self):
        """触发 O/F 扫描（与工具栏按钮等价；当前 GUI 选项决定扫描哪一侧）"""
        try:
            inputs = self._read_inputs()
            start = float(self.of_sweep_start.text())
            end   = float(self.of_sweep_end.text())
            step  = float(self.of_sweep_step.text())
        except Exception as e:
            QMessageBox.warning(self, "输入错误", f"扫描参数解析失败：{e}")
            return

        if step <= 0 or end < start:
            QMessageBox.warning(self, "输入错误", "请确保 步长 > 0 且 终止 ≥ 起始。")
            return

        # 生成 O/F 列表（含 end）
        of_list: List[float] = []
        v = start
        # 防止浮点累计误差，限制最多 200 个点
        guard = 0
        while v <= end + 1e-9 and guard < 200:
            of_list.append(round(v, 6))
            v += step; guard += 1
        if not of_list:
            QMessageBox.warning(self, "输入错误", "扫描列表为空。")
            return

        side = "ox" if self.of_sweep_side.currentIndex() == 0 else "fuel"
        self._set_busy(True, f"开始 {('富氧' if side=='ox' else '富燃')}预燃室 O/F 扫描")
        worker = OFSweepWorker(inputs, side, of_list)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(self._on_of_sweep_finished)
        worker.signals.error.connect(self._on_worker_error)
        self._active_worker = worker
        self.thread_pool.start(worker)

    def run_of_sweep_ox(self):
        """工具栏快捷入口：直接按当前 GUI 配置触发扫描。"""
        self.tabs.setCurrentIndex(self._tab_idx["of_sweep"])
        self._on_run_of_sweep_clicked()

    @Slot(object)
    def _on_of_sweep_finished(self, payload: Dict[str, Any]):
        side    = payload.get("side", "ox")
        records = payload.get("records", [])
        self.last_of_sweep = payload
        self._set_busy(False, "涡轮设计点扫描完成")
        self.progress.setValue(100)
        self._populate_of_sweep_table(records)
        # 跳到本 Tab
        self.tabs.setCurrentIndex(self._tab_idx["of_sweep"])
        ok = sum(1 for r in records if r.get("converged"))
        self.of_sweep_hint.setText(
            f"完成：{len(records)} 个 O/F 点；收敛 {ok} 个。"
            f"选中一行 → 点击「应用选中行为设计点」可写回主面板。"
        )

    def _populate_of_sweep_table(self, records: List[dict]):
        """把扫描记录填进表格。未收敛行用浅红高亮，温度超限行用浅黄高亮。"""
        tbl = self.of_sweep_table
        tbl.setRowCount(0)

        T_OX_LIMIT = 800.0
        T_FUEL_LIMIT = 900.0

        for rec in records:
            row = tbl.rowCount()
            tbl.insertRow(row)
            for col, (header, key, fmt) in enumerate(self._OF_SWEEP_COLUMNS):
                val = rec.get(key, "")
                if key == "converged":
                    text = "✓" if val else "✗"
                elif isinstance(val, (int, float)) and not isinstance(val, bool):
                    try:
                        text = fmt.format(val) if fmt else str(val)
                    except Exception:
                        text = str(val)
                else:
                    text = str(val)
                item = QTableWidgetItem(text)
                # 所有单元格内容居中显示
                item.setTextAlignment(Qt.AlignCenter)
                # 高亮规则
                if not rec.get("converged", False):
                    item.setBackground(QBrush(QColor("#FEE2E2")))   # 未收敛 → 浅红
                else:
                    T_PB = rec.get("T_PB_K", 0.0)
                    side = rec.get("side", "ox")
                    limit = T_OX_LIMIT if side == "ox" else T_FUEL_LIMIT
                    if T_PB > limit:
                        item.setBackground(QBrush(QColor("#FEF3C7")))  # 超限 → 浅黄
                    elif side == "ox" and T_PB < limit - 50:
                        item.setBackground(QBrush(QColor("#DCFCE7")))  # 安全 → 浅绿
                tbl.setItem(row, col, item)
        tbl.resizeColumnsToContents()

    def _on_of_sweep_export(self):
        if not self.last_of_sweep or not self.last_of_sweep.get("records"):
            QMessageBox.information(self, "无扫描数据",
                                     "请先点击「开始扫描」生成结果。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 O/F 扫描结果",
            "of_sweep_cftbinputs.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        try:
            sweep_to_csv(self.last_of_sweep["records"], path)
            QMessageBox.information(self, "导出成功", f"已写入：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_of_sweep_export_png(self):
        """把扫描结果表导出为 300 dpi 高清 PNG / PDF（复用通用表格导出助手）。"""
        self._export_table_as_png(
            getattr(self, "of_sweep_table", None),
            "FFSC 涡轮设计点扫描结果 / Turbine Design-Point Sweep",
            "ffsc_turbine_of_sweep_table.png")

    def _on_of_sweep_apply(self):
        """把当前选中行的 O/F 写回左侧主面板对应输入框，方便复跑设计点。"""
        if not self.last_of_sweep or not self.last_of_sweep.get("records"):
            QMessageBox.information(self, "无扫描数据", "请先运行扫描。")
            return
        idx = self.of_sweep_table.currentRow()
        if idx < 0:
            QMessageBox.information(self, "未选中", "请先在表格中选中一行。")
            return
        rec = self.last_of_sweep["records"][idx]
        side = rec.get("side", "ox")
        of_val = float(rec.get("of_target", 0.0))
        key = "ox_preburner_mr_target" if side == "ox" else "fuel_preburner_mr_target"
        edit = self.input_edits.get(key)
        if edit is None:
            QMessageBox.warning(self, "应用失败",
                                 f"找不到主面板字段 {key}，请手动填入。")
            return
        # 直接用 SI 单位（O/F 无量纲，无需换算）
        edit.setText(f"{of_val:.4g}")
        QMessageBox.information(
            self, "已应用",
            f"已把 {side} 预燃室目标 O/F 设为 {of_val:.4g}，"
            f"点击工具栏「运行设计点」即可复算。"
        )

    # ──────────────────────────────────────────────────────────────
    # 热结构云图标签页(L2):推力室/喷管内壁 温度场 + 热应力场
    # ──────────────────────────────────────────────────────────────

    def _build_thermstr_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        intro = QLabel(
            "主燃烧室内壁（喷注面→喉部）热-结构分析。下方【载荷清单】给出作用在壁上的载荷；"
            "云图为沿程概览，喉部通道横截面的真二维场见下方两个按钮。先定载荷，不含疲劳/寿命。"
        )
        intro.setObjectName("MutedLabel")
        intro.setWordWrap(True)
        root.addWidget(intro)

        # 载荷清单卡片（先确定载荷——结构分析的输入边界条件）
        self.thermstr_loads = QLabel("运行设计点后生成主燃烧室壁载荷清单。")
        self.thermstr_loads.setObjectName("ResultCard")
        self.thermstr_loads.setWordWrap(True)
        self.thermstr_loads.setTextFormat(Qt.RichText)
        self.thermstr_loads.setStyleSheet(
            "QLabel#ResultCard{background:#ffffff;border:1px solid #e5e7eb;"
            "border-radius:8px;padding:10px 12px;}")
        root.addWidget(self.thermstr_loads)

        # 「喉部 2D 有限元」按钮（scikit-fem，按需运行——比 1D 解析更准的真二维场）
        btn_row = QHBoxLayout()
        self.fea_btn = QPushButton("▶ 运行喉部通道 2D 有限元 (scikit-fem)")
        self.fea_btn.setObjectName("PrimaryButton")
        self.fea_btn.setEnabled(False)
        self.fea_btn.setToolTip(
            "用上面确定的载荷做喉部通道横截面 2D 热-结构有限元，"
            "解真二维温度场+应力场（捕捉周向温变与通道角点应力集中），与 1D 解析对照。")
        self.fea_btn.clicked.connect(self._run_throat_fea)
        btn_row.addWidget(self.fea_btn)
        self.matcmp_btn = QPushButton("▶ 对比主流内衬材料 (2D 温度场云图)")
        self.matcmp_btn.setObjectName("PrimaryButton")
        self.matcmp_btn.setEnabled(False)
        self.matcmp_btn.setToolTip(
            "对所有候选铜合金内衬（GRCop-84/42、NARloy-Z、CuCrZr、Glidcop）各跑一遍"
            "喉部 2D FEA（同一冷却工况、材料无关边界），给出谁扛得住、谁扛不住。")
        self.matcmp_btn.clicked.connect(self._run_material_comparison_2d)
        btn_row.addWidget(self.matcmp_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        if MATPLOTLIB_AVAILABLE:
            card = QFrame(); card.setObjectName("ResultCard")
            lay = QVBoxLayout(card); lay.setContentsMargins(6, 10, 10, 10)
            self.thermstr_fig = Figure(figsize=(11, 8), facecolor="#FAFAFA")
            self.thermstr_canvas = FigureCanvasQTAgg(self.thermstr_fig)
            self.thermstr_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.thermstr_canvas.setMinimumHeight(720)
            lay.addWidget(self.thermstr_canvas)
            self.thermstr_note = QLabel("运行设计点后自动生成云图。")
            self.thermstr_note.setObjectName("MutedLabel")
            self.thermstr_note.setWordWrap(True)
            lay.addWidget(self.thermstr_note)
            root.addWidget(card, 1)
            ax = self.thermstr_fig.add_subplot(111)
            ax.axis("off")
            ax.text(0.5, 0.5, "运行设计点后自动生成温度/应力云图",
                    ha="center", va="center", transform=ax.transAxes,
                    color="#9CA3AF", fontsize=11, style="italic")
        else:
            warn = QLabel("⚠ matplotlib 未安装,无法显示云图")
            warn.setObjectName("MutedLabel")
            root.addWidget(warn)
        return w

    def _populate_thermstr(self, results: CycleResults):
        """Read saved thermal loads and add a separately identified structural estimate."""
        if not MATPLOTLIB_AVAILABLE or not hasattr(self, "thermstr_fig"):
            return
        self._last_wallfield = None
        self._last_geometry_for_fea = None
        for name in ("fea_btn", "matcmp_btn"):
            if hasattr(self, name):
                getattr(self, name).setEnabled(False)
        self.thermstr_fig.clear()
        self.thermstr_canvas.draw_idle()
        self._render_chamber_loads(None)
        try:
            from chamber_design import saved_cooling_result
            from thermal_structural import wall_field_from_profile, plot_wall_field
            prof = saved_cooling_result(results)["profile"]
            geom = self.last_geometry
            if not geom:
                from geometry import compute_geometry, geometry_to_dict
                inp = results.inputs
                geom = geometry_to_dict(compute_geometry(
                    results,
                    contraction_ratio=getattr(inp, "chamber_contraction_ratio", 2.5),
                    l_star=getattr(inp, "chamber_l_star_m", 1.0)))
            inner_key = getattr(results.inputs, "chamber_inner_wall_material", "grcop84")
            outer_key = getattr(results.inputs, "chamber_outer_wall_material", "inconel718_wall")
            wf = wall_field_from_profile(prof, results.summary, geom,
                                         inner_material_key=inner_key,
                                         outer_material_key=outer_key, domain="chamber")
            if wf is None:
                self.thermstr_note.setText("保存的冷却剖面数据不完整；请重算完整循环。")
                return
            plot_wall_field(self.thermstr_fig, wf)
            self.thermstr_canvas.draw_idle()
            self.thermstr_note.setText("  ·  ".join(wf.notes))
            self._render_chamber_loads(getattr(wf, "loads", None))
            self._last_wallfield = wf
            self._last_geometry_for_fea = geom
            for name in ("fea_btn", "matcmp_btn"):
                if hasattr(self, name):
                    getattr(self, name).setEnabled(True)
        except Exception as ex:
            import traceback
            self.thermstr_note.setText(f"保存的冷却载荷不可用：{ex}")
            self._log_info("热结构载荷读取失败\n" + traceback.format_exc(), source="ThermStr")

    def _render_chamber_loads(self, loads):
        """把主燃烧室壁载荷清单渲染成 HTML 表格(富文本 QLabel)。"""
        if not hasattr(self, "thermstr_loads"):
            return
        if loads is None:
            self.thermstr_loads.setText("当前记录未保存统一冷却载荷；需重算完整循环。")
            return
        head = (
            f"<div style='font-size:13px;font-weight:bold;color:#0f172a;margin-bottom:4px;'>"
            f"主燃烧室壁 载荷清单 / Chamber Wall Load Set</div>"
            f"<div style='font-size:11px;color:#6b7280;margin-bottom:6px;'>"
            f"分析域:{loads.domain}（喷注面→喉部）　·　热衬里 {loads.liner_material}"
            f"（{loads.liner_thickness_mm:.2f} mm）　·　结构夹套 {loads.jacket_material}"
            f"（{loads.jacket_thickness_mm:.1f} mm）</div>"
        )
        rows = (
            "<tr style='background:#f1f5f9;font-weight:bold;'>"
            "<td style='padding:4px 8px;'>载荷</td><td style='padding:4px 8px;'>作用对象</td>"
            "<td style='padding:4px 8px;'>量值</td><td style='padding:4px 8px;'>产生应力 / 裕度</td></tr>"
        )
        for name, target, mag, stress in loads.to_rows():
            rows += (
                f"<tr><td style='padding:3px 8px;'>{name}</td>"
                f"<td style='padding:3px 8px;color:#374151;'>{target}</td>"
                f"<td style='padding:3px 8px;'>{mag}</td>"
                f"<td style='padding:3px 8px;color:#b45309;'>{stress}</td></tr>"
            )
        self.thermstr_loads.setText(
            head + f"<table width='100%' style='font-size:12px;border-collapse:collapse;'>{rows}</table>")

    def _run_throat_fea(self):
        """Solve a snapshot in the background, then display its fields on the UI thread."""
        if getattr(self, "_busy", False):
            return
        wf = getattr(self, "_last_wallfield", None)
        geom = getattr(self, "_last_geometry_for_fea", None)
        if wf is None or geom is None:
            QMessageBox.information(self, "请先运行设计点", "请先「运行设计点」生成主燃烧室载荷，再运行 2D 有限元。")
            return
        try:
            import fea_chamber
        except Exception:
            QMessageBox.warning(self, "缺少依赖", "2D 有限元需要 scikit-fem。请执行: pip install scikit-fem")
            return
        from copy import deepcopy
        from auxiliary_worker import start_auxiliary_task
        wf, geom = deepcopy(wf), deepcopy(geom)
        inner_key = getattr(self.input_edits.get("chamber_inner_wall_material"), "currentData", lambda: "grcop84")()
        inner_key = inner_key if isinstance(inner_key, str) and inner_key in __import__("materials").MATERIALS else "grcop84"
        self.fea_btn.setText("⏳ 求解中…")
        start_auxiliary_task(
            self, lambda: {"result": fea_chamber.solve_from_wallfield(wf, geom, inner_material_key=inner_key), "wallfield": wf},
            "throat_fea", "喉部通道 2D 有限元求解中", self._on_throat_fea_finished,
            error=self._on_chamber_fea_error, cancelled=self._on_chamber_fea_cancelled)

    def _restore_chamber_fea_buttons(self):
        self.fea_btn.setText("▶ 运行喉部通道 2D 有限元 (scikit-fem)")
        self.matcmp_btn.setText("▶ 对比主流内衬材料 (2D 温度场云图)")

    @Slot(object)
    def _on_chamber_fea_cancelled(self, payload):
        self._restore_chamber_fea_buttons()

    @Slot(str)
    def _on_chamber_fea_error(self, tb):
        self._restore_chamber_fea_buttons()
        self._on_worker_error(tb)

    @Slot(object)
    def _on_throat_fea_finished(self, payload):
        self._set_busy(False, "喉部通道 2D 有限元完成")
        self._task_kind = None
        self._restore_chamber_fea_buttons()
        try:
            self._show_throat_fea_result(payload)
        except Exception:
            self._on_chamber_fea_error(traceback.format_exc())

    def _show_throat_fea_result(self, payload):
        import fea_chamber
        res, wf = payload["result"], payload["wallfield"]
        ti = wf.throat_index
        sig1d = wf.sigma_eq[ti] if wf.sigma_eq else 0.0
        dlg = QDialog(self)
        dlg.setWindowTitle("主燃烧室喉部 通道横截面 2D 有限元 (scikit-fem)")
        dlg.resize(1180, 820)
        lay = QVBoxLayout(dlg); lay.setContentsMargins(10, 10, 10, 10)
        if MATPLOTLIB_AVAILABLE and res.ok:
            fig = Figure(figsize=(12, 6), facecolor="#FAFAFA")
            canvas = FigureCanvasQTAgg(fig)
            canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            fea_chamber.plot_fea_fields(fig, res)
            lay.addWidget(canvas, 3)
        note = QLabel()
        note.setWordWrap(True); note.setTextFormat(Qt.RichText)
        cmp_line = (f"<b>与 1D 解析对照</b>:1D 喉部 σ_eq = {sig1d:.0f} MPa(只算穿衬里梯度+压差弯曲);"
                    f"2D 峰值 σ_vm = {res.vm_max_MPa:.0f} MPa(90 分位 {res.vm_p90_MPa:.0f})。"
                    f"2D 更高,因为它捕捉了**薄热衬里被冷背结构约束**的热应力——1D 假设薄壁、忽略此约束。")
        body = "<br>".join(f"· {n}" for n in res.notes)
        note.setText(f"<div style='font-size:12px;color:#374151;'>{body}<br><br>{cmp_line}</div>")
        lay.addWidget(note, 1)
        row = QHBoxLayout(); row.addStretch(1)
        b = QPushButton("关闭"); b.setObjectName("PrimaryButton"); b.clicked.connect(dlg.accept)
        row.addWidget(b); lay.addLayout(row)
        dlg.exec()

    def _run_material_comparison_2d(self):
        """Compare immutable material-boundary snapshots in a cancellable worker."""
        if getattr(self, "_busy", False):
            return
        wf = getattr(self, "_last_wallfield", None)
        geom = getattr(self, "_last_geometry_for_fea", None)
        if wf is None or geom is None:
            QMessageBox.information(self, "请先运行设计点", "请先「运行设计点」，再做材料对比。")
            return
        try:
            import fea_chamber
        except Exception:
            QMessageBox.warning(self, "缺少依赖", "2D 有限元需要 scikit-fem。请执行: pip install scikit-fem")
            return
        from copy import deepcopy
        from auxiliary_worker import start_auxiliary_task
        wf, geom = deepcopy(wf), deepcopy(geom)
        self.matcmp_btn.setText("⏳ 逐材料求解中…")
        start_auxiliary_task(
            self, lambda: fea_chamber.compare_liner_materials_2d(wf, geom),
            "material_fea", "候选内衬材料 2D 有限元对比中", self._on_material_comparison_finished,
            error=self._on_chamber_fea_error, cancelled=self._on_chamber_fea_cancelled)

    @Slot(object)
    def _on_material_comparison_finished(self, results):
        self._set_busy(False, "候选内衬材料 2D 对比完成")
        self._task_kind = None
        self._restore_chamber_fea_buttons()
        if not results:
            QMessageBox.information(self, "无结果", "未能生成材料对比（缺候选材料或几何）。")
            return
        try:
            self._show_material_comparison_result(results)
        except Exception:
            self._on_chamber_fea_error(traceback.format_exc())

    def _show_material_comparison_result(self, results):
        import fea_chamber
        dlg = QDialog(self)
        dlg.setWindowTitle("主流推力室内衬材料 喉部 2D 温度场对比（云图 + 性能参数）")
        dlg.resize(1320, 860)
        lay = QVBoxLayout(dlg); lay.setContentsMargins(10, 10, 10, 10)
        if MATPLOTLIB_AVAILABLE:
            fig = Figure(figsize=(13, 5.2), facecolor="#FAFAFA")
            canvas = FigureCanvasQTAgg(fig)
            canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            # 主图：所有材料的 2D 温度场云图并排（同色标）
            fea_chamber.plot_material_fields_grid(fig, results)
            lay.addWidget(canvas, 3)
        # 性能参数 + 判定表（含 k/T_max/σ_y，从材料库读）
        import materials as _m
        note = QLabel(); note.setWordWrap(True); note.setTextFormat(Qt.RichText)
        hdr = ("<tr style='background:#f1f5f9;font-weight:bold;'>"
               "<td style='padding:3px 8px;'>材料</td><td style='padding:3px 8px;'>k [W/m·K]</td>"
               "<td style='padding:3px 8px;'>σ_y热态 [MPa]</td><td style='padding:3px 8px;'>T_max [K]</td>"
               "<td style='padding:3px 8px;'>2D 热面壁温</td><td style='padding:3px 8px;'>min SF</td>"
               "<td style='padding:3px 8px;'>判定</td></tr>")
        body = ""
        for r in results:
            try:
                mat = _m.get_material(r.material_key)
                k = f"{mat.thermal_conductivity_WmK:.0f}"; sy = f"{mat.sigma_yield_hot_MPa:.0f}"
            except Exception:
                k = sy = "—"
            col = '#16a34a' if r.level == 'ok' else '#d97706' if r.level == 'marginal' else '#dc2626'
            body += (f"<tr><td style='padding:2px 8px;'>{r.material}</td>"
                     f"<td style='padding:2px 8px;'>{k}</td><td style='padding:2px 8px;'>{sy}</td>"
                     f"<td style='padding:2px 8px;'>{r.T_max_use_K:.0f}</td>"
                     f"<td style='padding:2px 8px;'>{r.T_max_K:.0f} K</td>"
                     f"<td style='padding:2px 8px;'>{r.min_SF:.2f}</td>"
                     f"<td style='padding:2px 8px;color:{col};'>{r.verdict_zh}</td></tr>")
        note.setText(
            "<div style='font-size:11.5px;color:#374151;'>"
            "<b>性能参数 + 判定（同一冷却工况，材料无关边界，公平对比）</b>"
            f"<table width='100%' style='border-collapse:collapse;'>{hdr}{body}</table>"
            "<b>性能参数来源/验证</b>：k(热导率)、σ_y(屈服)、E、α(膨胀)、ν、T_max、密度 均取自材料库，"
            "依据 NASA TM-2014-218360(GRCop-84/42)、TM-2010-216640(NARloy-Z)、"
            "Sandvik/ScienceDirect(CuCrZr/C18150)、ASM Alloy Digest(Glidcop)、Minneci et al. Int.Mater.Rev.2021 综述。"
            "铜合金基础热物性/力学性能有 NASA 试验与 SSME 飞行背书；高温退化曲线 σ_y(T) 为简化模型、"
            "增材态有散布；LCF 疲劳数据稀缺且多专有——基础参数可信，高温/疲劳余地需谨慎。"
            "<br><b>结论</b>：现有铜合金均‘扛不住’当前喉部工况时，优先在<b>冷却</b>下功夫"
            "（强化流道 / TBC / 膜或发汗冷却 / 提热导到 GRCop-42）把壁温压回许用区；材料研发是 10 年级长期并行方向。</div>")
        lay.addWidget(note, 2)
        row = QHBoxLayout(); row.addStretch(1)
        b = QPushButton("关闭"); b.setObjectName("PrimaryButton"); b.clicked.connect(dlg.accept)
        row.addWidget(b); lay.addLayout(row)
        dlg.exec()

    # ──────────────────────────────────────────────────────────────
    # 再生冷却标签页:沿程 q(x)/壁温/冷却剂/换热系数 剖面(直观感受冷却)
    # ──────────────────────────────────────────────────────────────

    def _build_cooling_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        intro = QLabel(
            "直接展示完整循环保存的统一冷却沿程：热流、壁温、冷却剂状态和压降。"
            "图中 x 为发动机轴向坐标，冷却剂流向以记录为准；端口压降直接取保存的入口、出口状态。\n"
            "历史记录或预算模式缺少沿程解时不补算曲线。修改几何、换热或损失参数后需重算完整循环。"
            "热结构页仅消费这些保存的热负荷，结构估计不代表结构验证。"
        )
        intro.setObjectName("MutedLabel")
        intro.setWordWrap(True)
        root.addWidget(intro)

        if MATPLOTLIB_AVAILABLE:
            card = QFrame(); card.setObjectName("ResultCard")
            lay = QVBoxLayout(card); lay.setContentsMargins(6, 10, 10, 10)
            self.cooling_fig = Figure(figsize=(11, 9), facecolor="#FAFAFA")
            self.cooling_canvas = FigureCanvasQTAgg(self.cooling_fig)
            self.cooling_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.cooling_canvas.setMinimumHeight(850)
            lay.addWidget(self.cooling_canvas)
            self.cooling_note = QLabel("运行设计点后自动生成再生冷却沿程剖面。")
            self.cooling_note.setObjectName("MutedLabel")
            self.cooling_note.setWordWrap(True)
            lay.addWidget(self.cooling_note)
            root.addWidget(card, 1)
            ax = self.cooling_fig.add_subplot(111)
            ax.axis("off")
            ax.text(0.5, 0.5, "运行设计点后自动生成再生冷却沿程剖面",
                    ha="center", va="center", transform=ax.transAxes,
                    color="#9CA3AF", fontsize=11, style="italic")
        else:
            warn = QLabel("⚠ matplotlib 未安装,无法显示冷却剖面")
            warn.setObjectName("MutedLabel")
            root.addWidget(warn)
        return w

    def _populate_cooling(self, results: CycleResults):
        """Display the cooling profile used by the cycle without recomputing physics."""
        if not MATPLOTLIB_AVAILABLE or not hasattr(self, "cooling_fig"):
            return
        self._last_wallfield_full = None
        self.cooling_fig.clear()
        self.cooling_canvas.draw_idle()
        try:
            from chamber_design import saved_cooling_result
            from thermal_structural import wall_field_from_profile, plot_cooling_profiles
            prof = saved_cooling_result(results)["profile"]
            geom = self.last_geometry
            if not geom:
                from geometry import compute_geometry, geometry_to_dict
                inp = results.inputs
                geom = geometry_to_dict(compute_geometry(
                    results,
                    contraction_ratio=getattr(inp, "chamber_contraction_ratio", 2.5),
                    l_star=getattr(inp, "chamber_l_star_m", 1.0)))
            inner_key = getattr(results.inputs, "chamber_inner_wall_material", "grcop84")
            outer_key = getattr(results.inputs, "chamber_outer_wall_material", "inconel718_wall")
            wf = wall_field_from_profile(prof, results.summary, geom,
                                         inner_material_key=inner_key,
                                         outer_material_key=outer_key, domain="full")
            if wf is None:
                self.cooling_note.setText("保存的冷却剖面数据不完整；请重算完整循环。")
                return
            plot_cooling_profiles(self.cooling_fig, wf)
            self.cooling_canvas.draw_idle()
            self.cooling_note.setText("  ·  ".join(wf.notes))
            self._last_wallfield_full = wf
        except Exception as ex:
            import traceback
            self.cooling_note.setText(f"保存的冷却剖面不可用：{ex}")
            self._log_info("再生冷却剖面读取失败\n" + traceback.format_exc(), source="Cooling")

    # ──────────────────────────────────────────────────────────────
    # 热力学对比标签页：NASA CEA × Cantera 交叉验证
    # ──────────────────────────────────────────────────────────────

    def _build_thermo_tab(self) -> QWidget:
        """
        热力学对比标签页布局：
        顶部  ── 参数控制行（O/F 范围、eps 列表、Pc、温度）+ 运行按钮
        中部  ── QSplitter：左侧数据表（35%）+ 右侧 2×2 图表（65%）
        """
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── 说明标题行 ───────────────────────────────────────────
        title_row = QHBoxLayout()
        title_lbl = QLabel("NASA CEA × Cantera 热力学交叉验证")
        title_lbl.setObjectName("CardTitle")
        avail_parts = []
        avail_parts.append("Cantera ✓" if CANTERA_AVAILABLE else "Cantera ✗（经验模型）")
        avail_parts.append("rocketcea ✓" if ROCKETCEA_AVAILABLE else "rocketcea ✗（未安装）")
        avail_lbl = QLabel("  |  ".join(avail_parts))
        avail_lbl.setObjectName("MutedLabel")
        title_row.addWidget(title_lbl)
        title_row.addStretch(1)
        title_row.addWidget(avail_lbl)
        root.addLayout(title_row)

        # ── 参数控制面板 ─────────────────────────────────────────
        ctrl_box = QGroupBox("扫描参数 / Scan Parameters")
        ctrl_lay = QHBoxLayout(ctrl_box)
        ctrl_lay.setSpacing(18)

        def _param(label_text, default_text, width=100):
            lbl = QLabel(label_text)
            lbl.setObjectName("KVKey")
            ed  = QLineEdit(default_text)
            ed.setFixedWidth(width)
            ed.setAlignment(Qt.AlignRight)
            ctrl_lay.addWidget(lbl)
            ctrl_lay.addWidget(ed)
            return ed

        self._thermo_of_min  = _param("O/F 起始",       "2.5",  60)
        self._thermo_of_max  = _param("O/F 结束",       "5.0",  60)
        self._thermo_of_n    = _param("点数",            "8",    50)
        ctrl_lay.addSpacing(10)
        self._thermo_eps     = _param("膨胀比 ε（逗号分隔）", "40,80,120", 120)
        ctrl_lay.addSpacing(10)
        self._thermo_pc      = _param("Pc MPa",          "30",   60)
        self._thermo_T_fuel  = _param("T_CH4 K",         "110",  60)
        self._thermo_T_ox    = _param("T_LOX K",         "90",   60)
        ctrl_lay.addStretch(1)

        self._thermo_run_btn = QPushButton("▶ 运行对比")
        self._thermo_run_btn.setObjectName("PrimaryButton")
        self._thermo_run_btn.clicked.connect(self.run_thermo_compare)
        ctrl_lay.addWidget(self._thermo_run_btn)
        root.addWidget(ctrl_box)

        # ── 进度标签 ─────────────────────────────────────────────
        self._thermo_status = QLabel("就绪  |  点击「运行对比」开始扫描")
        self._thermo_status.setObjectName("MutedLabel")
        root.addWidget(self._thermo_status)

        # ── 主体 Splitter ────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ── 左：数据表 ───────────────────────────────────────────
        left = QFrame()
        left.setObjectName("ResultCard")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(10, 10, 6, 10)
        tbl_hdr = QHBoxLayout()
        tbl_title = QLabel("对比数据表 / Comparison Table")
        tbl_title.setObjectName("CardTitle")
        tbl_hdr.addWidget(tbl_title)
        tbl_hdr.addStretch(1)
        btn_tbl_png = QPushButton("📷 导出 PNG")
        btn_tbl_png.setToolTip("把对比数据表导出为 300 dpi 高清 PNG（或 PDF）")
        btn_tbl_png.clicked.connect(self._export_thermo_table_png)
        tbl_hdr.addWidget(btn_tbl_png)
        left_lay.addLayout(tbl_hdr)

        self.thermo_table = QTableWidget(0, 11)
        self.thermo_table.setHorizontalHeaderLabels([
            "O/F", "ε",
            "Isp CT (s)", "Isp CEA (s)", "ΔIsp %",
            "C* CT m/s",  "C* CEA m/s",  "ΔC* %",
            "Tc CT (K)",  "Tc CEA (K)",  "ΔTc %",
        ])
        self.thermo_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.thermo_table.horizontalHeader().setStretchLastSection(True)
        self.thermo_table.setAlternatingRowColors(True)
        self.thermo_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.thermo_table.setEditTriggers(QTableWidget.NoEditTriggers)
        left_lay.addWidget(self.thermo_table)
        splitter.addWidget(left)

        # ── 右：matplotlib 图表 ───────────────────────────────────
        if MATPLOTLIB_AVAILABLE:
            right = QFrame()
            right.setObjectName("ResultCard")
            right_lay = QVBoxLayout(right)
            right_lay.setContentsMargins(6, 10, 10, 10)
            chart_hdr = QHBoxLayout()
            chart_title = QLabel("性能对比图 / Performance Comparison Charts")
            chart_title.setObjectName("CardTitle")
            chart_hdr.addWidget(chart_title)
            chart_hdr.addStretch(1)
            btn_chart_png = QPushButton("📷 导出 PNG")
            btn_chart_png.setToolTip("把性能对比图导出为 300 dpi 高清 PNG（或 SVG / PDF）")
            btn_chart_png.clicked.connect(self._export_thermo_charts_png)
            chart_hdr.addWidget(btn_chart_png)
            right_lay.addLayout(chart_hdr)

            self.thermo_fig = Figure(figsize=(11, 8), facecolor="#FAFAFA")
            self.thermo_canvas = FigureCanvasQTAgg(self.thermo_fig)
            self.thermo_canvas.setSizePolicy(
                QSizePolicy.Expanding, QSizePolicy.Expanding)
            right_lay.addWidget(self.thermo_canvas)

            self._init_thermo_axes()
            splitter.addWidget(right)
            splitter.setSizes([400, 720])
        else:
            warn = QLabel(
                "⚠  matplotlib 未安装，无法显示图表\n"
                "请运行：pip install matplotlib")
            warn.setAlignment(Qt.AlignCenter)
            warn.setObjectName("MutedLabel")
            splitter.addWidget(warn)
            splitter.setSizes([600, 300])

        root.addWidget(splitter, 1)
        return w

    def _init_thermo_axes(self):
        """初始化 2×2 占位图框架(未运行时显示)。委托给 cross_validate_charts 模块。"""
        if not MATPLOTLIB_AVAILABLE:
            return
        from cross_validate_charts import init_chart_placeholders
        init_chart_placeholders(self.thermo_fig)
        self.thermo_canvas.draw()

    def _update_thermo_charts(self, records: List[dict]):
        """用交叉验证结果刷新 2×2 图表。委托给 cross_validate_charts 模块。"""
        if not MATPLOTLIB_AVAILABLE or not records:
            return
        from cross_validate_charts import plot_cross_validate
        plot_cross_validate(self.thermo_fig, records)
        self.thermo_canvas.draw_idle()

    def run_thermo_compare(self):
        """读取参数控件，启动 ThermoWorker 后台线程。"""
        fuel_selector = self.input_edits.get("propellant_pair")
        if fuel_selector is not None and fuel_selector.currentData() == "lox_kerosene":
            QMessageBox.information(self, "热力学对比范围",
                                    "此独立对比工具仅支持液氧/甲烷；煤油替代工质请在 GG 系统仿真中计算。")
            return
        try:
            of_min   = float(self._thermo_of_min.text())
            of_max   = float(self._thermo_of_max.text())
            of_n     = max(2, int(self._thermo_of_n.text()))
            eps_list = [float(e.strip()) for e in self._thermo_eps.text().split(",") if e.strip()]
            pc_pa    = float(self._thermo_pc.text()) * 1e6
            T_fuel   = float(self._thermo_T_fuel.text())
            T_ox     = float(self._thermo_T_ox.text())
        except ValueError as e:
            QMessageBox.warning(self, "参数错误", f"请检查输入参数：{e}")
            return

        import math as _m
        of_list = [of_min + (of_max - of_min) * i / (of_n - 1) for i in range(of_n)]

        if not eps_list:
            eps_list = [40.0, 80.0, 120.0]

        self._thermo_run_btn.setEnabled(False)
        self._thermo_status.setText("正在计算，请稍候…")
        n = len(of_list) * len(eps_list)
        self.progress.setRange(0, n)
        self.progress.setValue(0)

        dataset_selector = self.input_edits.get("methane_thermo_dataset")
        dataset = dataset_selector.currentData() if dataset_selector is not None else "gri30"
        worker = ThermoWorker(of_list, eps_list, pc_pa, T_fuel, T_ox, dataset)
        worker.signals.progress.connect(self._on_thermo_progress)
        worker.signals.finished.connect(self._on_thermo_finished)
        worker.signals.error.connect(self._on_thermo_error)
        self._active_worker = worker
        self.thread_pool.start(worker)

        # 切换到热力学对比标签页（index = 6，即节流扫描之后）
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "热力学对比":
                self.tabs.setCurrentIndex(i)
                break

    def _on_thermo_progress(self, done: int, total: int, _norm: float, desc: str):
        self.progress.setValue(done)
        self._thermo_status.setText(f"计算中… {done}/{total}  ({desc})")

    def _on_thermo_finished(self, records: list):
        self._thermo_run_btn.setEnabled(True)
        self.progress.setValue(self.progress.maximum())
        ct_ok  = any(r.get("cantera") for r in records)
        cea_ok = any(r.get("cea")     for r in records)
        parts  = []
        if ct_ok:  parts.append("Cantera ✓")
        if cea_ok: parts.append("CEA ✓")
        if not (ct_ok or cea_ok):
            parts = ["⚠ 两种方法均无可用结果（请检查依赖库）"]
        self._thermo_status.setText(
            f"完成  |  {len(records)} 个点  |  " + "  ".join(parts)
        )

        # ── 填充数据表 ───────────────────────────────────────────
        self.thermo_table.setRowCount(0)
        for rec in records:
            ct  = rec.get("cantera") or {}
            cea = rec.get("cea")     or {}

            def _fv(d, k):
                v = d.get(k)
                return f"{v:.3f}" if isinstance(v, float) else "—"

            def _fd(k):
                v = rec.get(k)
                return f"{v:+.2f}" if isinstance(v, float) else "—"

            row_vals = [
                f"{rec['of']:.2f}",
                f"{rec['eps']:.0f}",
                _fv(ct,  "Isp_vac_s"), _fv(cea, "Isp_vac_s"), _fd("delta_Isp_vac_s_pct"),
                _fv(ct,  "Cstar_m_s"), _fv(cea, "Cstar_m_s"), _fd("delta_Cstar_m_s_pct"),
                _fv(ct,  "Tc_K"),      _fv(cea, "Tc_K"),       _fd("delta_Tc_K_pct"),
            ]
            row = self.thermo_table.rowCount()
            self.thermo_table.insertRow(row)
            for col, val in enumerate(row_vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                # 差值列：正（CEA高）蓝，负（CEA低）红
                if col in (4, 7, 10) and val != "—":
                    try:
                        dv = float(val)
                        if abs(dv) > 1.0:
                            item.setForeground(QBrush(
                                QColor("#1D4ED8") if dv > 0 else QColor("#DC2626")
                            ))
                    except ValueError:
                        pass
                self.thermo_table.setItem(row, col, item)

        self.thermo_table.resizeColumnsToContents()

        # ── 更新图表 ─────────────────────────────────────────────
        if MATPLOTLIB_AVAILABLE:
            self._update_thermo_charts(records)

        self._append_log(
            f"热力学对比完成：{len(records)} 个计算点，"
            f"Cantera={'可用' if ct_ok else '不可用'}，"
            f"rocketcea/CEA={'可用' if cea_ok else '不可用'}"
        )

    def _on_thermo_error(self, tb: str):
        self._thermo_run_btn.setEnabled(True)
        self._thermo_status.setText("❌ 计算出错，详情见日志")
        self._append_log("热力学对比出错：\n" + tb)
        QMessageBox.critical(self, "热力学对比出错", tb[:600])

    def _scroll_content(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll, content, layout

    def _make_kpi_card(self, title: str, value: str, unit: str = "", hint: str = "", state: str = "neutral") -> QFrame:
        card = QFrame()
        card.setObjectName(f"KpiCard_{state}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("KpiTitle")
        value_label = QLabel(f"{value} {unit}".strip())
        value_label.setObjectName("KpiValue")
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hint_label = QLabel(hint)
        hint_label.setObjectName("KpiHint")
        hint_label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(value_label)
        layout.addWidget(hint_label)
        return card

    def _make_status_strip(self, results: CycleResults) -> QFrame:
        s = results.summary
        residual = float(s.get("solver_residual_norm", 1.0) or 1.0)
        warning_count = len(results.warnings or []) + int(bool(s.get("solver_warning", 0.0)))
        mode = str(s.get("solve_mode_text", "目标预燃室混合比模式"))
        thermo = "Cantera" if float(s.get("thermo_source", 1.0) or 0.0) > 0.5 else "经验模型"
        from convergence import numerical_convergence
        numerical = numerical_convergence(results)
        status_text = numerical["label"]
        status_state = {"pass": "ok", "unknown": "warning", "fail": "error"}[numerical["status"]]
        items = [
            (status_text, status_state),
            (thermo, "neutral"),
            (mode, "neutral"),
            (f"残差 {self._fmt(residual)}", "neutral"),
            (f"警告 {warning_count}", "warning" if warning_count else "ok"),
        ]
        strip = QFrame()
        strip.setObjectName("StatusStrip")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        for text, state in items:
            badge = QLabel(text)
            badge.setObjectName({"ok":"PillOk", "warning":"PillWarn", "error":"PillErr"}.get(state, "PillNeutral"))
            layout.addWidget(badge)
        layout.addStretch(1)
        return strip

    def _make_kpi_row(self, results: CycleResults) -> QFrame:
        s = results.summary
        row = QFrame()
        row.setObjectName("KpiRow")
        layout = QGridLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        thrust_err = abs(float(s.get("thrust_error_percent", 999) or 999))
        oxT = float(s.get("ox_preburner_T_combustion_K", 0) or 0)
        fuelT = float(s.get("fuel_preburner_T_combustion_K", 0) or 0)
        # 目标命中、数值限值与限值依据分别表述；未设定/暂定不显示绿色验收。
        temp_mode = float(s.get("preburner_temp_target_mode", 0.0) or 0.0) >= 0.5
        def preburner_card(side, temperature):
            constraint = preburner_temperature_constraint(results.inputs, side)
            limit, status = constraint["limit_K"], constraint["status"]
            target = s.get(f"{side}_preburner_temp_target_K",
                           getattr(results.inputs, f"{side}_preburner_temp_target", None))
            target_hint = f"目标 {self._fmt(target)} K / " if temp_mode else ""
            if status == "unset":
                return target_hint + "上限未设定 · 尚未评估", "neutral"
            if status == "provisional":
                return target_hint + f"暂定上限 {self._fmt(limit)} K · 依据待核实", "warning"
            if status != "documented" or not constraint["basis"] or limit is None:
                return target_hint + "上限依据不完整 · 尚未评估", "neutral"
            reached = not temp_mode or (target is not None and abs(temperature - target) <= 5.0)
            state = "warning" if temperature > limit else "ok" if reached else "neutral"
            return target_hint + f"方案上限 {self._fmt(limit)} K · 已填写依据", state
        ox_pb_hint, ox_pb_state = preburner_card("ox", oxT)
        fuel_pb_hint, fuel_pb_state = preburner_card("fuel", fuelT)
        # 真空比冲 = 发动机内禀属性(主显);海平面比冲 = 测试结果(副显)
        isp_vac = s.get("Isp_vac_s")
        isp_sl  = s.get("Isp_s")
        # 真空推力(主显);海平面推力 = 目标推力同口径(副显)
        thrust_vac = s.get("thrust_vac_N")
        thrust_sl  = s.get("thrust_N")
        flow_model = float(s.get("nozzle_flow_model", 0.0) or 0.0)
        flow_txt = {0.0: "冻结流", 1.0: "平衡流", -1.0: "理想气体"}.get(flow_model, "冻结流")
        kpis = [
            ("真空推力", self._fmt_scaled(thrust_vac, "force"), "",
             f"海平面 {self._fmt_scaled(thrust_sl, 'force')}(误差 {self._fmt(s.get('thrust_error_percent'))}%)",
             "ok" if thrust_err < 0.1 else "warning"),
            ("真空比冲 Isp_vac", self._fmt(isp_vac), "s",
             f"海平面 {self._fmt(isp_sl)} s · {flow_txt}口径", "neutral"),
            ("总质量流量", self._fmt(s.get("mdot_total_kg_s")), "kg/s", "液氧 + 燃料", "neutral"),
            ("主燃烧室温度", self._fmt(s.get("chamber_temperature_K")), "K", "燃气平衡温度", "neutral"),
            ("富氧预燃室温度", self._fmt(oxT), "K", ox_pb_hint, ox_pb_state),
            ("富燃预燃室温度", self._fmt(fuelT), "K", fuel_pb_hint, fuel_pb_state),
        ]
        # V20：GG 循环——后两张预燃室温度卡替换为 GG 温度 + 涡轮排气温度
        if str(getattr(results.inputs, "cycle_type", "ffsc")) == "gg":
            ggT = float(s.get("gg_T_combustion_K", 0) or 0)
            gg_limit = float(getattr(results.inputs, "gg_temp_limit", 950.0))
            gg_tgt = float(getattr(results.inputs, "gg_temp_target", gg_limit))
            gg_temp_mode = float(getattr(results.inputs, "use_target_gg_temp", 1.0)) >= 0.5
            if gg_temp_mode:
                gg_hint = f"目标 {self._fmt(gg_tgt)} / 上限 {self._fmt(gg_limit)} K"
                gg_state = ("ok" if (ggT <= gg_limit and abs(ggT - gg_tgt) <= 5.0)
                            else ("warning" if ggT > gg_limit else "neutral"))
            else:
                gg_hint = f"上限 {self._fmt(gg_limit)} K"
                gg_state = "ok" if ggT <= gg_limit else "warning"
            pi_t = s.get("gg_turbine_pi_t")
            kpis[4] = ("燃气发生器温度", self._fmt(ggT), "K", gg_hint, gg_state)
            kpis[5] = ("GG 涡轮排气温度", self._fmt(s.get("gg_turbine_exhaust_T_K")), "K",
                       f"涡轮压比 π_t = {self._fmt(pi_t)}（开式大压比）", "neutral")
        for i, (title, value, unit, hint, state) in enumerate(kpis):
            layout.addWidget(self._make_kpi_card(title, value, unit, hint, state), i // 3, i % 3)
        return row

    @staticmethod
    def _fmt_scaled(v: Any, kind: str) -> str:
        if v is None:
            return "—"
        try:
            x = float(v)
        except Exception:
            return str(v)
        if kind == "force":
            return f"{x/1e6:.3f} MN"
        if kind == "pressure":
            return f"{x/1e6:.3f} MPa"
        if kind == "power":
            return f"{x/1e6:.3f} MW"
        return f"{x:.3g}"

    def _make_card(self, title: str, rows: List[Tuple[str, str, str]],
                   status: str = "",
                   badges: List[Tuple[str, str]] | None = None,
                   subtitle: str = "",
                   export_name: str = "",
                   notes: str = "", notes_title: str = "") -> QFrame:
        """
        创建部件结果卡片。

        Parameters
        ──────────
        title   : 卡片标题
        rows    : [(key_label, value_text, unit), ...]
        status  : 旧接口，单个状态文字（向后兼容；推荐改用 badges）
        badges  : V10 新接口 [(text, level), ...] level ∈ {"ok","warn","error","neutral"}
                  显示在标题右侧，多枚徽章并排。
        """
        card = QFrame()
        card.setObjectName("ResultCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        top = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("CardTitle")
        top.addWidget(label)
        top.addStretch(1)
        # V10：多枚徽章（OK / WARN / ERROR）
        if badges:
            level_to_name = {
                "ok":      "BadgeOk",
                "warn":    "BadgeWarning",
                "warning": "BadgeWarning",
                "error":   "BadgeError",
                "critical":"BadgeError",
                "neutral": "BadgeNeutral",
            }
            for text, level in badges:
                b = QLabel(text)
                b.setObjectName(level_to_name.get(level, "BadgeNeutral"))
                top.addWidget(b)
        elif status:
            # 旧路径兼容
            badge = QLabel(status)
            badge.setObjectName("BadgeNeutral")
            top.addWidget(badge)
        # V10：可选"单卡片导出 PNG"按钮（部件结果页用——每个部件单独导出）
        if export_name:
            btn_exp = QPushButton("📷 导出")
            btn_exp.setToolTip(f"把「{title}」这张卡片单独导出为高清 PNG")
            btn_exp.setCursor(Qt.PointingHandCursor)
            btn_exp.setStyleSheet(
                "QPushButton{font-size:11px; padding:2px 9px; color:#2563EB;"
                " background:#EFF4FF; border:1px solid #BFD3FF; border-radius:5px;}"
                "QPushButton:hover{background:#DCE8FF;}")
            btn_exp.clicked.connect(
                lambda _=False, c=card, b=btn_exp, nm=export_name:
                    self._export_card_as_png(c, b, nm))
            top.addWidget(btn_exp)
        layout.addLayout(top)
        # V10：可选 subtitle（如 "轴转速 24000 rpm · U/C₀ 0.52 · 推荐 1 级"）
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("MutedLabel")
            sub.setWordWrap(True)
            sub.setStyleSheet("color: #6B7280; font-size: 12px; padding-bottom: 4px;")
            layout.addWidget(sub)
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)
        for i, (name, value, unit) in enumerate(rows):
            r = i // 2
            c = (i % 2) * 2
            k = QLabel(name)
            k.setObjectName("KVKey")
            v = QLabel(f"{value} {unit}".strip())
            v.setObjectName("KVValue")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(k, r, c)
            grid.addWidget(v, r, c + 1)
        layout.addLayout(grid)
        # 长文本（用途说明等）：单独整行 + 自动换行 + 限宽，避免不换行 QLabel 把卡片撑太宽
        if notes:
            if notes_title:
                nk = QLabel(notes_title)
                nk.setObjectName("KVKey")
                layout.addWidget(nk)
            nv = QLabel(notes)
            nv.setObjectName("KVValue")
            nv.setWordWrap(True)
            nv.setMaximumWidth(640)
            nv.setTextInteractionFlags(Qt.TextSelectableByMouse)
            nv.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            layout.addWidget(nv)
        return card

    def _make_warning_card(self, title: str, warnings: List[str]) -> QFrame:
        """
        可行性警告专用卡片：每条警告独占一行并自动换行。

        与 _make_card 的两列网格不同——警告文本往往很长，放进不换行的
        KVValue 标签会把整个总体性能页撑宽，故这里用单列 + wordWrap。
        """
        card = QFrame()
        card.setObjectName("ResultCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        top = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("CardTitle")
        top.addWidget(label)
        top.addStretch(1)
        badge = QLabel(f"⚠ {len(warnings)} 条")
        badge.setObjectName("BadgeWarning")
        top.addWidget(badge)
        layout.addLayout(top)
        for i, w in enumerate(warnings):
            row = QHBoxLayout()
            row.setSpacing(12)
            k = QLabel(f"警告 {i + 1}")
            k.setObjectName("KVKey")
            k.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            v = QLabel(w)
            v.setObjectName("KVValue")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            row.addWidget(k, 0, Qt.AlignTop)
            row.addWidget(v, 1)
            layout.addLayout(row)
        return card

    def _clear_layout_keep_stretch(self, layout: QVBoxLayout):
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        layout.addStretch(1)

    # V10：字符串字段（材料 key 等），不参与数值转换
    _STRING_FIELDS = {
        "turb_ox_rotor_material",   "turb_ox_stator_material",
        "turb_ox_disk_material",    "turb_ox_housing_material",
        "turb_fuel_rotor_material", "turb_fuel_stator_material",
        "turb_fuel_disk_material",  "turb_fuel_housing_material",
        # V19：冷却通道沿程分布表（JSON 串，推力室设计工作区维护）
        "cooling_channel_table_json",
        "turb_ox_stage_work_fractions", "turb_fuel_stage_work_fractions",
    }
    # V10：整数字段
    _INT_FIELDS = {
        "throttle_steps",
        "lox_pump_n_stages", "fuel_pump_n_stages",
        "turb_ox_n_stages_user", "turb_fuel_n_stages_user",
    }

    def _read_inputs(self) -> EngineInputs:
        kwargs: Dict[str, Any] = {}
        for key, edit in self.input_edits.items():
            # V10：材料字段是 QComboBox，读取 currentData()（内部 key 字符串）
            if isinstance(edit, QComboBox):
                data = edit.currentData()
                if data is None or str(data).strip() == "":
                    raise ValueError(f"参数 {key} 未选择")
                kwargs[key] = str(data)
                continue
            raw = edit.text().strip()
            if key in self._STRING_FIELDS:
                kwargs[key] = raw          # 字符串字段允许为空（如分布表 JSON=""）
                continue
            if raw == "":
                raise ValueError(f"参数 {key} 不能为空")
            try:
                if key in self._INT_FIELDS:
                    kwargs[key] = int(float(raw))
                else:
                    # 将显示单位值转回 SI 单位（DISPLAY_UNIT_MAP 中的字段做逆换算）
                    kwargs[key] = _from_display(key, raw)
            except ValueError:
                raise ValueError(f"参数 {key} = {raw!r} 不是有效数字")
        return EngineInputs(**kwargs)

    def _current_cycle_type(self) -> str:
        """当前选择的循环方式 key（'ffsc' / 'gg'）。"""
        combo = self.input_edits.get("cycle_type")
        if isinstance(combo, QComboBox):
            data = combo.currentData()
            if data:
                return str(data)
        return "ffsc"

    def _on_cycle_type_changed(self, *_args):
        """循环方式切换：显隐 FFSC/GG 专属参数组 + 刷新参考机型 + 询问加载预设（V20/V20.1）。"""
        cycle = self._current_cycle_type()
        is_gg = (cycle == "gg")
        for title, box in getattr(self, "_param_group_boxes", {}).items():
            if title in FFSC_ONLY_GROUPS:
                box.setVisible(not is_gg)
            elif title in GG_ONLY_GROUPS:
                box.setVisible(is_gg)
        # GG 轴系布局选择器只在 GG 模式可用（视觉提示；组本身始终可见）
        layout_combo = self.input_edits.get("gg_layout")
        if isinstance(layout_combo, QComboBox):
            layout_combo.setEnabled(is_gg)

        # ── V20.1：刷新参考机型下拉（按循环过滤）──────────────
        self._refresh_preset_combo(cycle)

        # ── V20.1：用户手动切换循环时，询问是否加载该循环的参考机型工况 ──
        # 构造期(_ui_ready=False)与程序化写入(_suppress_preset_prompt=True)不弹。
        if getattr(self, "_ui_ready", False) and not getattr(self, "_suppress_preset_prompt", False):
            try:
                from engine_presets import ENGINE_PRESETS, default_preset_for_cycle
                pk = default_preset_for_cycle(cycle)
                label = ENGINE_PRESETS[pk]["label"]
                cyc_cn = "GG 燃气发生器开式循环" if is_gg else "FFSC 全流量分级燃烧循环"
                ret = QMessageBox.question(
                    self, "加载参考机型工况？",
                    f"已切换到 {cyc_cn}。\n\n"
                    f"是否同时加载参考机型工况？\n    {label}\n\n"
                    f"「是」= 覆盖左侧全部输入框为该机型参数；\n"
                    f"「否」= 仅切换循环，保留当前参数。",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if ret == QMessageBox.Yes:
                    self._apply_engine_preset(pk)
            except Exception:
                self._log_info("参考机型预设加载询问失败\n" + traceback.format_exc(),
                               source="Preset")

    def _refresh_preset_combo(self, cycle: str | None = None):
        """按循环方式重建参考机型下拉候选（V20.1）。"""
        combo = getattr(self, "_preset_combo", None)
        if combo is None:
            return
        if cycle is None:
            cycle = self._current_cycle_type()
        try:
            from engine_presets import presets_for_cycle
            cands = presets_for_cycle(cycle)
        except Exception:
            cands = {}
        prev = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for k, p in cands.items():
            combo.addItem(p["label"], userData=k)
            combo.setItemData(combo.count() - 1, p.get("source", ""), Qt.ToolTipRole)
        if prev is not None:
            for i in range(combo.count()):
                if combo.itemData(i) == prev:
                    combo.setCurrentIndex(i)
                    break
        combo.blockSignals(False)

    def _on_apply_preset_clicked(self):
        combo = getattr(self, "_preset_combo", None)
        if combo is None or combo.currentData() is None:
            return
        self._apply_engine_preset(str(combo.currentData()))

    def _apply_engine_preset(self, preset_key: str):
        """把参考机型工况（默认值+overrides）写入全部输入控件（V20.1）。

        写入期间抑制循环切换弹窗（cycle_type combo 变化会触发 _on_cycle_type_changed）。
        """
        from engine_presets import ENGINE_PRESETS, preset_input_values
        values = preset_input_values(preset_key)
        label = ENGINE_PRESETS[preset_key]["label"]
        self._suppress_preset_prompt = True
        try:
            for key, edit in self.input_edits.items():
                if key not in values:
                    continue
                val = values[key]
                if isinstance(edit, QComboBox):
                    for i in range(edit.count()):
                        if edit.itemData(i) == val:
                            edit.setCurrentIndex(i)
                            break
                    continue
                edit.setText(_to_display(key, val) if isinstance(val, float) else str(val))
        finally:
            self._suppress_preset_prompt = False
        # 显隐/机型下拉与写入后的 cycle_type 对齐（写入期间信号被抑制弹窗但仍触发显隐）
        self._refresh_preset_combo()
        src = ENGINE_PRESETS[preset_key].get("source", "")
        self._log_step(f"已应用参考机型工况：{label}", source="Preset")
        if src:
            self._log_info(f"预设口径：{src}", source="Preset")

    def reset_defaults(self):
        defaults = EngineInputs()
        # V20.1：恢复默认会把 cycle_type 切回 ffsc——程序化写入不弹预设询问框
        self._suppress_preset_prompt = True
        try:
            for key, edit in self.input_edits.items():
                si_val = getattr(defaults, key)
                if isinstance(edit, QComboBox):
                    # 材料字段：按 userData 反查默认 key
                    for i in range(edit.count()):
                        if edit.itemData(i) == si_val:
                            edit.setCurrentIndex(i)
                            break
                    continue
                # 使用显示单位格式恢复
                init_text = _to_display(key, si_val) if isinstance(si_val, float) else str(si_val)
                edit.setText(init_text)
        finally:
            self._suppress_preset_prompt = False
        self._refresh_preset_combo()
        self._append_log("已恢复默认参数（压力单位：MPa，推力单位：kN；循环=FFSC/Raptor 3 对标）。")

    def run_cycle(self):
        self._log_info("用户点击「运行设计点」", source="User")
        try:
            inputs = self._read_inputs()
        except Exception as e:
            self._log_error(f"输入参数校验失败: {e}", source="Input")
            QMessageBox.warning(self, "输入错误", str(e))
            return
        of_val = getattr(inputs, "of_ratio", None)
        of_str = f"{of_val:.2f}" if isinstance(of_val, (int, float)) else "—"
        _cyc = "GG 燃气发生器" if getattr(inputs, "cycle_type", "ffsc") == "gg" else "FFSC"
        self._log_step(
            f"启动 {_cyc} Newton 求解 — 目标推力 {inputs.thrust/1000:.1f} kN, "
            f"Pc={inputs.pc/1e6:.1f} MPa, O/F={of_str}",
            source="Solver",
        )
        self._set_log_status("求解中 / Running…", conv="… 计算中", conv_kind="neutral")
        self._set_busy(True, "开始设计点求解")
        worker = SolveWorker(inputs)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(self._on_solve_finished)
        worker.signals.error.connect(self._on_worker_error)
        self._active_worker = worker
        self.thread_pool.start(worker)

    def run_throttle(self):
        self._log_info("用户点击「节流扫描」", source="User")
        try:
            inputs = self._read_inputs()
        except Exception as e:
            self._log_error(f"输入参数校验失败: {e}", source="Input")
            QMessageBox.warning(self, "输入错误", str(e))
            return
        self._log_step(
            f"启动节流扫描 — {inputs.throttle_steps} 个工况点，从 100% 推力线性递减",
            source="Solver",
        )
        self._set_log_status("扫描中 / Sweeping…", conv="… 节流中", conv_kind="neutral")
        self._set_busy(True, "开始节流扫描")
        worker = ThrottleWorker(inputs)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(self._on_throttle_finished)
        worker.signals.error.connect(self._on_worker_error)
        self._active_worker = worker
        self.thread_pool.start(worker)

    @Slot(int, int, float, str)
    def _on_progress(self, i: int, n: int, norm: float, desc: str):
        pct = int(100 * min(max(i, 0), max(n, 1)) / max(n, 1))
        self.progress.setValue(pct)
        if norm > 0:
            self.progress_label.setText(f"{desc}: {i}/{n}, residual={norm:.3e}")
        else:
            self.progress_label.setText(f"{desc}: {i}/{n}")

    @Slot(object)
    def _on_solve_finished(self, results: CycleResults):
        self.last_results = results
        self._set_busy(False, "设计点求解完成")
        self.progress.setValue(100)
        # V20：结果标题随循环类型切换
        _is_gg = (str(getattr(results.inputs, "cycle_type", "ffsc")) == "gg")
        if getattr(self, "_result_title_label", None) is not None:
            self._result_title_label.setText(
                "GG 燃气发生器数字样机计算结果" if _is_gg else "FFSC 数字样机计算结果")
            self._result_subtitle_label.setText(
                "循环闭合 · GG 混合比/温度 · 涡轮泵功率平衡 · 开式排气比冲代价"
                if _is_gg else
                "循环闭合 · 预燃室混合比 · 涡轮泵功率平衡 · 几何估算")
        # 先算几何,再 populate。否则 _populate_results→_draw_topology 拿到的
        # self.last_geometry 还是上一轮(或 None),会导致拓扑图喷管在首次点击
        # fallback 成锥形、二次点击才变钟形(形状闪烁 bug)。
        if self.chk_auto_geometry.isChecked():
            self._log_step("自动几何参数计算…", source="Geometry")
            self.compute_geometry_from_last(silent=True)
        else:
            # 未勾选自动几何时也清掉上一轮的几何,避免拓扑图用过期 contour
            self.last_geometry = None
        self._populate_results(results)   # 内部会调用 _populate_log，写收敛/警告/结果摘要
        self.tabs.setCurrentIndex(self._tab_idx["overview"])

    @Slot(object)
    def _on_throttle_finished(self, sweep: List[dict]):
        self.last_throttle = sweep
        self._set_busy(False, "节流扫描完成")
        self.progress.setValue(100)
        n_conv = sum(1 for r in sweep if r.get("converged", True))
        n_total = len(sweep)
        if n_conv == n_total:
            self._log_ok(f"节流扫描完成 — {n_total} 个工况全部收敛", source="Solver")
            self._set_log_status("空闲 / Idle", conv=f"✓ 节流 {n_conv}/{n_total}", conv_kind="ok")
        else:
            self._log_warn(
                f"节流扫描完成 — {n_conv}/{n_total} 个工况收敛，{n_total-n_conv} 个失败",
                source="Solver",
            )
            self._set_log_status("空闲 / Idle", conv=f"⚠ 节流 {n_conv}/{n_total}", conv_kind="warn")
        self._populate_throttle(sweep)
        self.tabs.setCurrentIndex(self._tab_idx["throttle"])
        if hasattr(self, "engchar_tabs"):
            self.engchar_tabs.setCurrentIndex(0)   # 切到「节流特性」子页

    @Slot(str)
    def _on_worker_error(self, tb: str):
        self._set_busy(False, "计算失败")
        last_line = tb.splitlines()[-1] if tb.splitlines() else tb
        self._log_error(f"求解失败: {last_line}", source="Solver")
        # 完整 traceback 拆成多行 INFO，便于调试但不刷屏错误计数
        self._log_info("─── traceback ───\n" + tb, source="Solver")
        self._set_log_status("空闲 / Idle", conv="✗ 计算失败", conv_kind="error")
        QMessageBox.critical(self, "计算失败", last_line)

    def _set_busy(self, busy: bool, msg: str):
        self.btn_run.setDisabled(busy)
        self.btn_throttle.setDisabled(busy)
        # 扫描按钮可能尚未构建（_build_of_sweep_tab 在 __init__ 后期才执行），做容错
        if hasattr(self, "btn_run_of_sweep") and self.btn_run_of_sweep is not None:
            self.btn_run_of_sweep.setDisabled(busy)
        # 进入忙态时把进度条恢复到 0–100 范围并清零(避免被其它任务路径
        # 如热力学对比 setRange(0, n_total) 残留污染)
        if busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        self.progress_label.setText(msg)
        self.statusBar().showMessage(msg)

    def _populate_results(self, results: CycleResults):
        s = results.summary
        residual = float(s.get("solver_residual_norm", 1.0))
        has_warning = bool(results.warnings) or bool(s.get("solver_warning", 0.0))
        from convergence import numerical_convergence
        numerical = numerical_convergence(results)
        if numerical["status"] == "pass":
            self.badge.setText("已收敛")
            self.badge.setObjectName("BadgeOk")
        elif numerical["status"] == "unknown":
            self.badge.setText("收敛证据不完整")
            self.badge.setObjectName("BadgeWarning")
        else:
            self.badge.setText("未完全收敛")
            self.badge.setObjectName("BadgeError")
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)

        self._populate_overview(results)
        self._populate_components(results)
        self._populate_states(results)
        self._draw_topology(results)
        self._populate_log(results)
        # V10：刷新材料库页签（保存 results 以便点击时取工况）
        self._latest_results = results
        self._populate_materials_tab(results)
        # L2：热结构云图（依赖 last_geometry,已在 _on_solve_finished 中先算好）
        self._populate_thermstr(results)
        # L2：再生冷却沿程剖面（同一 WallField 物理，直观展示冷却）
        self._populate_cooling(results)
        # V19：推力室设计工作区（快照/型面/通道可行性/记分卡/站位游标）
        if hasattr(self, "chamber_panel"):
            try:
                self.chamber_panel.on_results_updated(results)
            except Exception:
                self._log_info("推力室工作区刷新失败\n" + traceback.format_exc(),
                               source="Chamber")
        # 方案B：高度特性（复用设计点结果，零额外求解，自动刷新）
        self._populate_altitude(results)

    def _populate_overview(self, results: CycleResults):
        self._clear_layout_keep_stretch(self.overview_layout)
        s = results.summary
        insert_at = max(self.overview_layout.count() - 1, 0)

        self.overview_layout.insertWidget(insert_at, self._make_status_strip(results)); insert_at += 1
        self.overview_layout.insertWidget(insert_at, self._make_kpi_row(results)); insert_at += 1

        # 详细结果仍按模块卡片展示，避免核心指标和详细参数混在一起。
        # V20：按结果的循环类型选择总览分组（FFSC / GG 两套语义）
        _groups = (OVERVIEW_GROUPS_GG
                   if str(getattr(results.inputs, "cycle_type", "ffsc")) == "gg"
                   else OVERVIEW_GROUPS)
        for title, fields in _groups:
            rows = []
            for key, label, unit in fields:
                value = s.get(key, None)
                if key == "thermo_source" and value is not None:
                    text = "Cantera" if float(value) > 0.5 else "经验模型"
                elif key in {"thrust_N", "target_thrust_N"}:
                    text = self._fmt_scaled(value, "force")
                    unit = ""
                elif key in {"pump_power_MW", "turbine_power_MW", "ox_pump_power_MW", "ox_turbine_power_MW", "fuel_pump_power_MW", "fuel_turbine_power_MW"}:
                    text = self._fmt(value)
                else:
                    text = self._fmt(value)
                rows.append((label, text, unit))
            self.overview_layout.insertWidget(insert_at, self._make_card(title, rows)); insert_at += 1
        if results.warnings:
            self.overview_layout.insertWidget(
                insert_at, self._make_warning_card("当前可行性警告", results.warnings))

    def _make_group_header(self, title: str, subtitle: str = "") -> QFrame:
        """
        创建分组区域标题栏（用于部件结果的大块分隔）。

        V10 视觉升级：
          · 标题字号从 13px → 19px，加粗
          · 副标题字号从 11px → 13px，颜色更亮
          · 顶部加左侧 6px 强调色块（视觉锚点）
          · 上下留白加大，让"大类标题"层级明显高于"部件标题"
        """
        header = QFrame()
        header.setObjectName("ComponentGroupHeader")
        header.setStyleSheet(
            "QFrame#ComponentGroupHeader {"
            "  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "    stop:0 #1e2d47, stop:1 #2a3a5c);"
            "  border-left: 6px solid #4f7dd1;"
            "  border-radius: 8px;"
            "  margin-top: 14px;"
            "  margin-bottom: 6px;"
            "}"
        )
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "color: #ffffff; font-size: 19px; font-weight: 800; "
            "letter-spacing: 1px; background: transparent;"
        )
        layout.addWidget(title_lbl)
        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setStyleSheet(
                "color: #b8c8e8; font-size: 13px; background: transparent;"
            )
            layout.addWidget(sub_lbl)
        layout.addStretch(1)
        return header

    def _component_status_badges(
        self, name: str, comp, inputs=None,
    ) -> List[Tuple[str, str]]:
        """
        根据 outputs 推断该部件的状态徽章。

        返回 [(text, level), ...]，level ∈ {"ok","warn","error","neutral"}。
        没有判定逻辑的部件返回 []。
        """
        o = comp.outputs
        badges: List[Tuple[str, str]] = []

        # ───── 涡轮（带 V10 几何/材料校核字段）─────
        if "shaft_speed_rpm" in o and "U_mean_actual_ms" in o:
            # (a) U_mean 与 u_max 比较
            U   = o.get("U_mean_actual_ms", 0)
            Umx = o.get("u_max_ms", 1e9)
            if U > Umx:
                badges.append((f"U_mean {U:.0f}>U_max {Umx:.0f}", "error"))
            # (b) 入口温度超限
            if "t_in_over_limit" in o:
                badges.append((f"T_in 超限 +{o['t_in_over_limit']:.0f}K", "error"))
            # (c) 材料 SF
            sf = o.get("rotor_safety_factor")
            if sf is not None:
                if sf < 1.0:
                    badges.append((f"动叶 SF={sf:.2f}<1.0", "error"))
                elif sf < 1.5:
                    badges.append((f"动叶 SF={sf:.2f}<1.5", "warn"))
                else:
                    badges.append((f"SF={sf:.2f}", "ok"))
            # (d) U/C0 偏离最优区间
            uc = o.get("u_over_c_actual")
            if uc is not None:
                if uc < 0.30 or uc > 0.70:
                    badges.append((f"U/C₀={uc:.2f} 偏离最优", "warn"))

        # ───── 泵（带 V10 叶尖/汽蚀字段）─────
        if "u_tip_est_ms" in o:
            utip = o.get("u_tip_est_ms", 0)
            ulim = o.get("tip_speed_limit_ms", 1e9)
            if utip > ulim:
                badges.append((f"u_tip {utip:.0f}>限 {ulim:.0f}", "error"))
            elif utip > 0.9 * ulim:
                badges.append((f"u_tip {utip:.0f}≈限", "warn"))
            else:
                badges.append((f"u_tip OK", "ok"))
            # Nss
            nss = o.get("suction_specific_speed_Nss")
            npsh = o.get("npsh_available_m")
            if isinstance(npsh, (int, float)) and math.isfinite(npsh) and npsh <= 0:
                badges.append((f"NPSHa={npsh:.2g} m ≤ 0", "error"))
            elif not isinstance(nss, (int, float)) or not math.isfinite(nss):
                # Above the critical temperature a saturation-based NPSH is
                # undefined; an absent value is not zero or cavitation proof.
                badges.append(("NPSH / Nss 未判定或不适用", "neutral"))
            elif nss > 45000:
                badges.append((f"Nss={nss:.0f} 穴蚀", "error"))
            elif nss > 30000:
                badges.append((f"Nss={nss:.0f} 需诱导轮", "warn"))
            # 公式自检（pump.py 实算 vs 教材手算定义）
            cc = self._pump_crosscheck(o)
            if cc is not None:
                cc_ok, _cc_rel, _ = cc
                badges.append(("公式自检 ✓" if cc_ok else "公式自检 ✗",
                               "ok" if cc_ok else "error"))

        # ───── 预燃室 ─────
        if "T_combustion_K" in o and inputs is not None:
            Tc = o["T_combustion_K"]
            if name in ("ox_preburner", "fuel_preburner"):
                constraint = preburner_temperature_constraint(inputs, name.split("_")[0])
                lim, status = constraint["limit_K"], constraint["status"]
                if status == "unset":
                    badges.append(("温度上限未设定 · 尚未评估", "neutral"))
                elif status == "documented" and not constraint["basis"]:
                    badges.append(("温度上限依据不完整", "neutral"))
                elif lim is not None and Tc > lim:
                    badges.append((f"T={Tc:.0f}>限 {lim:.0f}K", "error"))
                elif status == "provisional":
                    badges.append((f"T={Tc:.0f}K · 暂定限值依据待核实", "warn"))
                elif lim is not None:
                    badges.append((f"T={Tc:.0f}K · 满足方案上限", "ok"))

        return badges

    def _component_subtitle(self, name: str, comp, inputs=None) -> str:
        """
        从 V10 outputs 提取 *一句话* 副标题，显示在卡片标题正下方。
        替代原本被截断的灰色 notes 长行。
        """
        o = comp.outputs

        # 涡轮：转速 + U/C0 + 级数 + 材料名
        if "shaft_speed_rpm" in o and "U_mean_actual_ms" in o:
            n_rpm = o.get("shaft_speed_rpm", 0)
            uc    = o.get("u_over_c_actual", 0)
            stages= int(o.get("n_stages_recommended", 0))
            mat_name = ""
            if inputs is not None:
                key = (
                    inputs.turb_ox_rotor_material   if name.startswith("ox")   else
                    inputs.turb_fuel_rotor_material if name.startswith("fuel") else None
                )
                if key:
                    try:
                        from materials import get_material  # noqa
                        mat_name = get_material(key).display_name
                    except Exception:
                        mat_name = key
            tail = f"｜动叶：{mat_name}" if mat_name else ""
            return f"轴转速 {n_rpm:.0f} rpm  ·  U/C₀ {uc:.2f}  ·  推荐 {stages} 级{tail}"

        # 泵
        if "u_tip_est_ms" in o and "head_m" in o:
            n_rpm = o.get("nr_rpm", 0)
            utip  = o.get("u_tip_est_ms", 0)
            stages= int(o.get("n_stages", 1))
            H     = o.get("head_m", 0)
            P_MW  = o.get("power_MW", 0)
            base = f"轴转速 {n_rpm:.0f} rpm  ·  扬程 {H:.0f} m / {stages} 级  ·  叶尖 {utip:.0f} m/s  ·  P={P_MW:.1f} MW"
            cc = self._pump_crosscheck(o)
            if cc is not None:
                base += f"  ·  公式自检 {'✓ 与教材定义一致' if cc[0] else '✗ 公式不符'}"
            return base

        # 预燃室
        if "T_combustion_K" in o and "mixture_ratio_OF" in o:
            T  = o["T_combustion_K"]
            mr = o["mixture_ratio_OF"]
            return f"O/F={mr:.2f}  ·  T_PB={T:.0f} K"

        return ""

    def _component_rows(self, component_name: str, outputs: Dict[str, Any]) -> List[Tuple[str, str, str]]:
        rows: List[Tuple[str, str, str]] = []
        keys = set(outputs.keys())
        for key, value in outputs.items():
            preferred = COMPONENT_HIDE_IF_PRESENT.get(key)
            if preferred and preferred in keys:
                continue
            rows.append(self._component_row(component_name, key, value))
        return rows

    # ── 泵"公式自检"对拍（pump.py 实算 vs 教材手算定义）─────────────────
    # 复用 pump_speed_crosscheck.hand_calc（= pump.py 应实现的"规格"）。原始量
    # （Q/H/N/ω/NPSHa/ψ）泵 outputs 里都有，不必重算求解器。pump.py 的比转速/
    # 汽蚀/叶尖公式若被改错，这里立刻显示 ✗（卡片徽章变红、行内标红）。
    _PUMP_CC_ITEMS = [
        ("比转速 nq(EU)",      "specific_speed_Ns"),
        ("吸入比转速 Nss(US)", "suction_specific_speed_Nss"),
        ("叶尖速度 u_tip",     "u_tip_est_ms"),
        ("叶轮直径 D_imp",     "d_impeller_est_m"),
        ("Thoma 空化数 σ",     "thoma_cavitation_number_sigma"),
    ]

    def _pump_crosscheck(self, o: Dict[str, Any], rtol: float = 1e-4):
        """返回 (all_ok, max_rel, rows)；rows=[(label, '实算 vs 手算 ✓/✗', '')...]。
        无法计算（缺键/NPSHa≤0）时返回 None。"""
        try:
            from pump_speed_crosscheck import hand_calc
            Q   = float(o["volume_flow_m3s"]); H = float(o["head_m"])
            n   = int(round(o["n_stages"]));   N = float(o["nr_rpm"])
            om  = float(o["nr_rad_s"]);        npm = float(o["npsh_available_m"])
            psi = float(o["psi_head_coefficient"])
            if (not all(math.isfinite(v) for v in (Q, H, N, om, npm, psi))
                    or Q <= 0 or H <= 0 or npm <= 0 or psi <= 0):
                return None
            hand = hand_calc(Q, H, n, N, om, npm, psi)
        except Exception:
            return None
        rows: List[Tuple[str, str, str]] = []
        all_ok = True
        max_rel = 0.0
        for label, key in self._PUMP_CC_ITEMS:
            try:
                a = float(o[key]); b = float(hand[key])
            except (KeyError, TypeError, ValueError):
                return None
            if not math.isfinite(a) or not math.isfinite(b):
                return None
            rel = abs(a - b) / max(abs(b), 1e-30)
            max_rel = max(max_rel, rel)
            ok = rel <= rtol
            all_ok = all_ok and ok
            mark = "✓" if ok else f"✗ rel={rel:.0e}"
            rows.append((f"校核 · {label}", f"{a:.5g}  vs  {b:.5g}   {mark}", ""))
        return all_ok, max_rel, rows

    def _component_row(self, component_name: str, key: str, value: Any) -> Tuple[str, str, str]:
        label = (
            COMPONENT_SPECIFIC_LABELS.get(component_name, {}).get(key)
            or COMPONENT_LABEL_OVERRIDE.get(key)
            or KEY_LABELS.get(key, key)
        )
        if key == "mode":
            try:
                value = "Bartz 热流估算" if float(value) >= 0.5 else "固定热载荷"
            except (TypeError, ValueError):
                pass
        scale = COMPONENT_VALUE_SCALE.get(key)
        if scale is not None and value is not None:
            try:
                value = float(value) * scale
            except (TypeError, ValueError):
                pass
        return label, self._fmt(value), ""

    def _populate_components(self, results: CycleResults):
        self._clear_layout_keep_stretch(self.components_layout)
        insert_at = max(self.components_layout.count() - 1, 0)

        # 收集所有已知分组键，用于最后处理"其他"部件
        grouped_keys: set = set()
        for _, _, keys in COMPONENT_GROUPS:
            grouped_keys.update(keys)

        # ── 按分组顺序展示 ─────────────────────────────────────
        for group_title, group_subtitle, group_keys in COMPONENT_GROUPS:
            # 检查该组是否有任何部件结果
            available = [k for k in group_keys if k in results.components]
            if not available:
                continue

            # 插入组标题栏
            hdr = self._make_group_header(group_title, group_subtitle)
            self.components_layout.insertWidget(insert_at, hdr)
            insert_at += 1

            # 插入该组的各部件卡片
            for name in available:
                comp = results.components[name]
                rows = self._component_rows(name, comp.outputs)
                # 泵卡片末尾追加"公式自检"对拍行（实算 vs 教材手算）
                if name in ("ox_pump", "fuel_pump"):
                    _cc = self._pump_crosscheck(comp.outputs)
                    if _cc is not None:
                        rows = rows + _cc[2]
                # V10：用结构化徽章 + 一行 subtitle 取代以前被截断的 notes 长行
                badges   = self._component_status_badges(name, comp, results.inputs)
                subtitle = self._component_subtitle(name, comp, results.inputs)
                card = self._make_card(
                    COMPONENT_LABELS.get(name, name),
                    rows,
                    badges=badges,
                    subtitle=subtitle,
                    export_name=f"ffsc_{name}.png",
                )
                self.components_layout.insertWidget(insert_at, card)
                insert_at += 1

        # ── 其他（未在分组中定义的部件）────────────────────────
        extra = [k for k in results.components.keys() if k not in grouped_keys]
        if extra:
            hdr = self._make_group_header("其他部件", "")
            self.components_layout.insertWidget(insert_at, hdr)
            insert_at += 1
            for name in extra:
                comp = results.components[name]
                rows = self._component_rows(name, comp.outputs)
                badges   = self._component_status_badges(name, comp, results.inputs)
                subtitle = self._component_subtitle(name, comp, results.inputs)
                card = self._make_card(
                    COMPONENT_LABELS.get(name, name),
                    rows,
                    badges=badges,
                    subtitle=subtitle,
                    export_name=f"ffsc_{name}.png",
                )
                self.components_layout.insertWidget(insert_at, card)
                insert_at += 1

    def _populate_states(self, results: CycleResults):
        """
        V5.3.6：将状态点按流路拆到三张表（液氧/甲烷/主燃烧室）。
        点击任一行，下方详情卡片显示对应状态点的全部参数。
        """
        # 三条路径上各自的状态点 key 列表
        lox_keys  = [k for k, _ in _LOX_PATH if k in results.states and k != "main_chamber"]
        ch4_keys  = [k for k, _ in _CH4_PATH if k in results.states and k != "main_chamber"]
        main_keys = ["main_chamber"] if "main_chamber" in results.states else []

        # 维护一个全局有序列表（用于兼容旧的 CSV 导出）
        self._state_keys_by_row = lox_keys + ch4_keys + main_keys

        self._populate_path_table(self.lox_path_table,  lox_keys,  results)
        self._populate_path_table(self.ch4_path_table,  ch4_keys,  results)
        self._populate_path_table(self.main_path_table, main_keys, results)

        # 默认选中液氧储箱（第一行）
        if lox_keys:
            self.lox_path_table.selectRow(0)
            self._populate_state_detail(lox_keys[0])
        elif ch4_keys:
            self.ch4_path_table.selectRow(0)
            self._populate_state_detail(ch4_keys[0])

        # 刷新右侧图表
        self._draw_states_flowpath(results)
        self._draw_pressure_waterfall(results)

    def _populate_path_table(self, table, keys, results):
        """填充单条流路的状态点表（5 列）。"""
        table.setRowCount(0)
        for name in keys:
            st = results.states[name]
            label = self._state_label(name)
            row = table.rowCount()
            table.insertRow(row)
            values = [
                label,
                self._fmt_pressure_mpa(st.pressure),
                self._fmt_temperature(st.temperature),
                self._fmt_flow(st.mass_flow),
                self._fmt_density(st.density),
            ]
            for col, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if col == 0:
                    item.setData(Qt.UserRole, name)
                if col > 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, col, item)
        table.resizeColumnsToContents()

    def _on_path_table_selection_changed(self, table):
        """
        V5.3.6：任一流路表点击行，切换到该状态点的详情。
        同步取消其他两张表的选择高亮（视觉上一次只有一个被选中）。
        """
        selected = table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        item = table.item(row, 0)
        if item is None:
            return
        state_key = item.data(Qt.UserRole)
        if not state_key:
            return

        # 取消其他两张表的选中态
        for other in (self.lox_path_table, self.ch4_path_table, self.main_path_table):
            if other is not table:
                other.blockSignals(True)
                other.clearSelection()
                other.blockSignals(False)

        self._populate_state_detail(state_key)

    @staticmethod
    def _ordered_state_keys(states: Dict[str, Any]) -> List[str]:
        preferred = [key for key, _ in _LOX_PATH] + [key for key, _ in _CH4_PATH]
        ordered: List[str] = []
        seen = set()
        for key in preferred:
            if key in states and key not in seen:
                ordered.append(key)
                seen.add(key)
        for key in states.keys():
            if key not in seen:
                ordered.append(key)
        return ordered

    # ── 流路 P-T-ρ 曲线 ────────────────────────────────────────────────────

    def _init_states_flowpath_axes(self):
        """
        V5.3.6：流路曲线改为 2×2 布局——仅 P 和 T，去掉密度子图。
        子图高度因此翻倍，标注更清晰。
        """
        self.states_flow_fig.clear()
        self.states_flow_fig.patch.set_facecolor("#FAFAFA")
        gs = self.states_flow_fig.add_gridspec(
            2, 2, hspace=0.55, wspace=0.32,
            left=0.10, right=0.97, top=0.93, bottom=0.12,
        )
        panels = [
            (0, 0, "液氧流路 · 压力 [bar]"),
            (0, 1, "燃料流路 · 压力 [bar]"),
            (1, 0, "液氧流路 · 温度 [K]"),
            (1, 1, "燃料流路 · 温度 [K]"),
        ]
        self._sf_axes = {}
        keys = ["p_lox", "p_ch4", "t_lox", "t_ch4"]
        for (r, c, ttl), k in zip(panels, keys):
            ax = self.states_flow_fig.add_subplot(gs[r, c])
            self._style_ax_base(ax)
            ax.set_title(ttl, fontsize=9, fontweight="bold", color="#111827", pad=4)
            ax.text(0.5, 0.5, "计算后自动绘制", ha="center", va="center",
                    transform=ax.transAxes, color="#9CA3AF", fontsize=9, style="italic")
            self._sf_axes[k] = ax
        self.states_flow_canvas.draw()

    def _draw_states_flowpath(self, results):
        """V5.3.6：根据最新计算结果刷新流路 P-T 曲线（2×2，无密度子图）。"""
        if not MATPLOTLIB_AVAILABLE or not hasattr(self, "states_flow_fig"):
            return
        states = results.states
        LOX_COL = "#2563EB"
        CH4_COL = "#D97706"

        def _extract(path):
            xs, ps, ts, lbls = [], [], [], []
            for i, (key, lbl) in enumerate(path):
                if key not in states:
                    continue
                st = states[key]
                xs.append(i)
                ps.append(st.pressure / 1e5)
                ts.append(st.temperature)
                lbls.append(lbl)
            return xs, ps, ts, lbls

        lox_xs, lox_ps, lox_ts, lox_lbls = _extract(_LOX_PATH)
        ch4_xs, ch4_ps, ch4_ts, ch4_lbls = _extract(_CH4_PATH)

        def _draw_one(ax, xs, ys_raw, lbls, color, ylabel):
            ax.cla()
            self._style_ax_base(ax)
            pairs = [(x, y, l) for x, y, l in zip(xs, ys_raw, lbls)
                     if y is not None]
            if pairs:
                vx, vy, vl = zip(*pairs)
                vx, vy = list(vx), list(vy)
                ax.plot(vx, vy, "o-", color=color, lw=2.2, ms=6, zorder=5)
                for xi, yi in zip(vx, vy):
                    ax.annotate(
                        f"{yi:.0f}", (xi, yi),
                        textcoords="offset points", xytext=(0, 7),
                        ha="center", fontsize=8, color=color, fontweight="bold",
                    )
                ax.set_xticks(vx)
                ax.set_xticklabels(list(vl), rotation=35, ha="right", fontsize=8)
                ax.set_xlim(min(vx) - 0.5, max(vx) + 0.5)
                ax.margins(y=0.22)
            ax.set_ylabel(ylabel, fontsize=9, color="#374151")
            ax.tick_params(axis="y", labelsize=8)
            ax.grid(True, alpha=0.25, linestyle="--", color="#CBD5E1")

        _draw_one(self._sf_axes["p_lox"], lox_xs, lox_ps, lox_lbls, LOX_COL, "P [bar]")
        _draw_one(self._sf_axes["p_ch4"], ch4_xs, ch4_ps, ch4_lbls, CH4_COL, "P [bar]")
        _draw_one(self._sf_axes["t_lox"], lox_xs, lox_ts, lox_lbls, LOX_COL, "T [K]")
        _draw_one(self._sf_axes["t_ch4"], ch4_xs, ch4_ts, ch4_lbls, CH4_COL, "T [K]")

        # Re-apply titles (cla() clears them)
        titles = [
            ("p_lox", "液氧流路 · 压力 [bar]"),
            ("p_ch4", "燃料流路 · 压力 [bar]"),
            ("t_lox", "液氧流路 · 温度 [K]"),
            ("t_ch4", "燃料流路 · 温度 [K]"),
        ]
        for k, ttl in titles:
            self._sf_axes[k].set_title(
                ttl, fontsize=9, fontweight="bold", color="#111827", pad=4)

        self.states_flow_fig.tight_layout(pad=0.8, h_pad=2.0, w_pad=1.5)
        self.states_flow_canvas.draw()

    # ────────────────────────────────────────────────────────────
    # V5.3.6 新增：压降网络瀑布图
    # ────────────────────────────────────────────────────────────

    def _init_waterfall_axes(self):
        """初始化压降瀑布图占位框架。"""
        self.waterfall_fig.clear()
        self.waterfall_fig.patch.set_facecolor("#FAFAFA")
        gs = self.waterfall_fig.add_gridspec(
            1, 2, hspace=0.0, wspace=0.30,
            left=0.10, right=0.97, top=0.92, bottom=0.18,
        )
        self._wf_axes = {
            "lox": self.waterfall_fig.add_subplot(gs[0, 0]),
            "ch4": self.waterfall_fig.add_subplot(gs[0, 1]),
        }
        for k, ax in self._wf_axes.items():
            self._style_ax_base(ax)
            title = "液氧流路压降瀑布" if k == "lox" else "燃料流路压降瀑布"
            ax.set_title(title, fontsize=9, fontweight="bold", color="#111827", pad=4)
            ax.text(0.5, 0.5, "计算后自动绘制", ha="center", va="center",
                    transform=ax.transAxes, color="#9CA3AF", fontsize=9, style="italic")
        self.waterfall_canvas.draw()

    def _draw_pressure_waterfall(self, results):
        """
        V5.3.6 新增：压降瀑布图。

        每段绘成一个水平条，长度 = 该段压力（bar），颜色编码：
          ・蓝/橙 = 增压（泵升）
          ・红    = 损失（管路、冷却套、喷注器、阀门、涡轮膨胀）
          ・绿    = 终点（主燃烧室）

        数据来源：
          · summary 中已有 injector_dp_*  / cooling_dp / valve_line_dp
          · states 中的 pressure 直接给出每个节点压力
          · 中间"虚拟节点"（泵后 - 冷却 - 喷注器 - 阀门 - 预燃室入口）用减法重建
        """
        if not MATPLOTLIB_AVAILABLE or not hasattr(self, "waterfall_fig"):
            return

        s  = results.summary
        st = results.states

        # 把 Pa → bar 的小工具
        def bar(p): return p / 1e5 if p is not None else 0.0

        # ── 构造液氧侧序列（按压力递减）─────────────────────
        # 节点：储箱 → 管路后 → 泵后 → 阀门后 → 喷注器后(=预燃室入口) → 涡轮排气 → 主室前(+主喷注器压降)
        lox_pump_out = bar(s.get("lox_pump_out_pressure_Pa", 0))
        dp_valve_lox = s.get("valve_line_dp_MPa", 0) * 10.0      # bar
        dp_inj_pb_lox = lox_pump_out - dp_valve_lox - bar(st.get("ox_preburner_outlet").pressure if "ox_preburner_outlet" in st else 0)
        # 不要 negative
        dp_inj_pb_lox = max(0.0, dp_inj_pb_lox)
        p_pb_in_lox    = bar(st["ox_preburner_outlet"].pressure)   if "ox_preburner_outlet"   in st else 0
        p_turb_out_lox = bar(st["ox_turbine_exhaust"].pressure)    if "ox_turbine_exhaust"    in st else 0
        p_main_lox     = bar(st["main_chamber"].pressure)          if "main_chamber"          in st else 0

        lox_steps = [
            ("储箱",            bar(st["lox_tank"].pressure)     if "lox_tank" in st     else 6.0,    "#94a3b8", "+"),
            ("管路后",          bar(st["lox_pump_inlet"].pressure) if "lox_pump_inlet" in st else 3.1, "#cbd5e1", "−"),
            ("液氧泵后",        lox_pump_out,                                                            "#2563EB", "+"),
            ("阀门管路后",      lox_pump_out - dp_valve_lox,                                              "#fb923c", "−"),
            ("预燃室喷注器后",  p_pb_in_lox,                                                              "#f87171", "−"),
            ("富氧涡轮排气",    p_turb_out_lox,                                                           "#ef4444", "−"),
            ("主燃烧室",        p_main_lox,                                                               "#22c55e", "·"),
        ]
        # ── 甲烷侧序列 ─────────────────────────────────────
        ch4_pump_out = bar(s.get("fuel_pump_out_pressure_Pa", 0))
        dp_cool      = s.get("cooling_dp_MPa", 0) * 10.0
        dp_valve_ch4 = s.get("valve_line_dp_MPa", 0) * 10.0
        p_pb_in_ch4    = bar(st["fuel_preburner_outlet"].pressure) if "fuel_preburner_outlet" in st else 0
        p_turb_out_ch4 = bar(st["fuel_turbine_exhaust"].pressure)  if "fuel_turbine_exhaust" in st else 0
        p_main_ch4     = p_main_lox

        ch4_steps = [
            ("储箱",            bar(st["fuel_tank"].pressure)     if "fuel_tank" in st     else 6.0,    "#94a3b8", "+"),
            ("管路后",          bar(st["fuel_pump_inlet"].pressure) if "fuel_pump_inlet" in st else 3.1, "#cbd5e1", "−"),
            ("燃料泵后",        ch4_pump_out,                                                            "#D97706", "+"),
            ("再生冷却后",      bar(st["fuel_after_cooling"].pressure) if "fuel_after_cooling" in st
                                else max(0.0, ch4_pump_out - dp_cool),                                  "#10b981", "−"),
            ("阀门管路后",      max(0.0, ch4_pump_out - dp_cool - dp_valve_ch4),                         "#fb923c", "−"),
            ("预燃室喷注器后",  p_pb_in_ch4,                                                              "#f87171", "−"),
            ("富燃涡轮排气",    p_turb_out_ch4,                                                           "#ef4444", "−"),
            ("主燃烧室",        p_main_ch4,                                                               "#22c55e", "·"),
        ]

        def _draw_side(ax, steps, side_label):
            ax.cla()
            self._style_ax_base(ax)
            labels  = [s[0] for s in steps]
            values  = [s[1] for s in steps]
            colors  = [s[2] for s in steps]
            ypos    = list(range(len(labels)))[::-1]  # 上→下
            bars    = ax.barh(ypos, values, color=colors, edgecolor="#1f2937",
                              linewidth=0.6, alpha=0.92, height=0.72)
            ax.set_yticks(ypos)
            ax.set_yticklabels(labels, fontsize=8.5)
            ax.set_xlabel("压力 [bar]", fontsize=9)
            ax.tick_params(axis="x", labelsize=8)
            ax.grid(True, axis="x", alpha=0.25, linestyle="--", color="#CBD5E1")
            # V10：右侧留白 1.18 → 1.25，容纳"数值 + 压差"两行标注，避免被坐标轴裁掉
            xmax = max(values) * 1.25 if values else 100
            ax.set_xlim(0, xmax)
            ax.set_ylim(-0.7, len(labels) - 1 + 0.7)   # 给上下两行标注留竖向余量

            # 在每个条右端标注：上行=该节点绝对压力，下行=相对上一节点的压差。
            # V10：改为上下两行（原先左右并排，数值与压差会互相重叠），
            #      横向占用更小、图变窄时也不挤。
            prev = None
            for i, (lbl, val, col, sign) in enumerate(steps):
                y = ypos[i]
                has_delta = (prev is not None and i > 0 and abs(val - prev) > 0.5)
                # 有压差时数值上移半行、给下行压差让位；无压差时垂直居中
                val_dy = 0.22 if has_delta else 0.0
                ax.text(val + xmax * 0.012, y + val_dy, f"{val:.1f} bar",
                        va="center", ha="left", fontsize=8, color="#111827",
                        fontweight="bold")
                if has_delta:
                    diff = val - prev
                    ddot = "▲" if diff > 0 else "▼"
                    dcol = "#16a34a" if diff > 0 else "#dc2626"
                    ax.text(val + xmax * 0.012, y - 0.24, f"{ddot}{abs(diff):.1f}",
                            va="center", ha="left", fontsize=7.5,
                            color=dcol, fontweight="bold")
                prev = val

            title = ("液氧流路压降瀑布" if side_label == "lox"
                     else "燃料流路压降瀑布")
            ax.set_title(title, fontsize=9.5, fontweight="bold",
                         color="#111827", pad=4)
            for spine in ax.spines.values():
                spine.set_color("#E5E7EB")

        _draw_side(self._wf_axes["lox"], lox_steps, "lox")
        _draw_side(self._wf_axes["ch4"], ch4_steps, "ch4")
        self.waterfall_fig.tight_layout(pad=0.8, w_pad=2.5)
        self.waterfall_canvas.draw()

    def _state_label(self, key: str) -> str:
        return STATE_LABELS.get(key, f"{key} / {key}")

    @staticmethod
    def _fmt_pressure_mpa(v: Any) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v)/1e6:.3f}"
        except Exception:
            return str(v)

    @staticmethod
    def _fmt_temperature(v: Any) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v):.1f}"
        except Exception:
            return str(v)

    @staticmethod
    def _fmt_flow(v: Any) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v):.3f}"
        except Exception:
            return str(v)

    @staticmethod
    def _fmt_density(v: Any) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v):.3f}"
        except Exception:
            return str(v)

    @staticmethod
    def _fmt_enthalpy_mjkg(v: Any) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v)/1e6:.3f}"
        except Exception:
            return str(v)

    def _composition_items(self, composition: Dict[str, float]) -> List[Tuple[str, float]]:
        items = []
        for k, v in (composition or {}).items():
            try:
                fv = float(v)
            except Exception:
                continue
            if abs(fv) > 0:
                items.append((k, fv))
        items.sort(key=lambda kv: abs(kv[1]), reverse=True)
        return items

    def _composition_summary(self, composition: Dict[str, float], top_n: int = 4, threshold: float = 1e-4) -> str:
        items = [(k, v) for k, v in self._composition_items(composition) if abs(v) >= threshold]
        if not items:
            return "—"
        parts = [f"{k}={v:.3f}" for k, v in items[:top_n]]
        if len(items) > top_n:
            parts.append("…")
        return ", ".join(parts)

    def _clear_state_detail(self):
        while self.state_detail_layout.count() > 0:
            item = self.state_detail_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_state_selection_changed(self):
        # V5.3.6：旧的统一状态点表已拆为三张子表，由 _on_path_table_selection_changed 处理。
        # 此方法保留为空，避免外部引用断裂。
        return

    def _populate_state_detail(self, state_key: str):
        if self.last_results is None or state_key not in self.last_results.states:
            return
        self._selected_state_key = state_key
        st = self.last_results.states[state_key]

        self.state_title.setText(self._state_label(state_key))
        self.state_hint.setText("该状态点为循环计算得到的热力状态；组分为 Cantera/经验模型输出的质量分数。")

        detail_rows = [
            ("压力",        self._fmt_pressure_mpa(st.pressure),   "MPa"),
            ("温度",        self._fmt_temperature(st.temperature), "K"),
            ("质量流量",    self._fmt_flow(st.mass_flow),          "kg/s"),
            ("密度",        self._fmt_density(st.density),         "kg/m³"),
            ("比焓",        self._fmt_enthalpy_mjkg(st.enthalpy),  "MJ/kg"),
            ("主要组分",    self._composition_summary(st.composition, top_n=5), ""),
        ]
        self.state_detail_table.setRowCount(0)
        for param, value, unit in detail_rows:
            row = self.state_detail_table.rowCount()
            self.state_detail_table.insertRow(row)
            vals = [param, value, unit]
            for col, text in enumerate(vals):
                item = QTableWidgetItem(str(text))
                if col == 1:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.state_detail_table.setItem(row, col, item)
        self.state_detail_table.resizeColumnsToContents()

        self.species_table.setRowCount(0)
        for species, mf in self._composition_items(st.composition):
            percent = mf * 100.0
            display_species = SPECIES_LABELS.get(species, species)
            self._add_table_row(self.species_table, [
                display_species,
                f"{mf:.8e}" if abs(mf) < 1e-4 else f"{mf:.8f}",
                f"{percent:.4f}%" if abs(percent) >= 0.001 else "—",
            ])
        self.species_table.resizeColumnsToContents()

    def _state_csv_rows(self, state_key: str) -> List[Dict[str, Any]]:
        if self.last_results is None or state_key not in self.last_results.states:
            return []
        st = self.last_results.states[state_key]
        base = {
            "state_key": state_key,
            "state_label": self._state_label(state_key),
            "pressure_MPa": "" if st.pressure is None else float(st.pressure) / 1e6,
            "temperature_K": st.temperature,
            "mass_flow_kg_s": st.mass_flow,
            "density_kg_m3": st.density,
            "enthalpy_MJ_kg": "" if st.enthalpy is None else float(st.enthalpy) / 1e6,
        }
        rows = []
        items = self._composition_items(st.composition)
        if not items:
            row = dict(base)
            row.update({"species": "", "cfd_species_name": "", "mass_fraction": "", "percent": ""})
            rows.append(row)
        for species, mf in items:
            row = dict(base)
            row.update({
                "species": species,
                "cfd_species_name": SPECIES_LABELS.get(species, species),
                "mass_fraction": mf,
                "percent": mf * 100.0,
            })
            rows.append(row)
        return rows

    def _write_state_csv(self, path: str, state_keys: List[str]):
        fieldnames = [
            "state_key", "state_label", "pressure_MPa", "temperature_K", "mass_flow_kg_s",
            "density_kg_m3", "enthalpy_MJ_kg", "species", "cfd_species_name", "mass_fraction", "percent",
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for key in state_keys:
                for row in self._state_csv_rows(key):
                    writer.writerow(row)

    def copy_current_state_composition(self):
        if self.last_results is None or not self._selected_state_key:
            QMessageBox.information(self, "没有可复制内容", "请先运行设计点求解并选择一个状态点。")
            return
        rows = self._state_csv_rows(self._selected_state_key)
        lines = ["species,mass_fraction"]
        for row in rows:
            if row.get("species"):
                lines.append(f"{row['species']},{row['mass_fraction']}")
        QApplication.clipboard().setText("\n".join(lines))
        self._append_log(f"已复制当前状态点组分：{self._state_label(self._selected_state_key)}")

    def export_current_state_csv(self):
        if self.last_results is None or not self._selected_state_key:
            QMessageBox.information(self, "没有可导出内容", "请先运行设计点求解并选择一个状态点。")
            return
        default_name = f"state_{self._selected_state_key}_composition.csv"
        path, _ = QFileDialog.getSaveFileName(self, "导出当前状态点 CSV", default_name, "CSV (*.csv)")
        if not path:
            return
        self._write_state_csv(path, [self._selected_state_key])
        self._append_log(f"已导出当前状态点 CSV: {path}")

    def export_all_states_csv(self):
        if self.last_results is None:
            QMessageBox.information(self, "没有可导出内容", "请先运行设计点求解。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出全部状态点 CSV", "ffsc_states_for_cfd.csv", "CSV (*.csv)")
        if not path:
            return
        self._write_state_csv(path, list(self.last_results.states.keys()))
        self._append_log(f"已导出全部状态点 CSV: {path}")

    def _populate_log(self, results: CycleResults):
        """求解完成后追加结构化日志摘要（V5.4.4：ANSYS 风格分级日志）。"""
        s = results.summary
        residual = float(s.get("solver_residual_norm", 1.0))
        iters_val = s.get("solver_iterations")
        iters_str = self._fmt(iters_val)
        has_warning = bool(results.warnings) or bool(s.get("solver_warning", 0.0))
        from convergence import numerical_convergence
        numerical = numerical_convergence(results)
        converged = numerical["status"] == "pass"

        self._log_step(
            f"热力学引擎: {'Cantera (GRI-Mech 3.0)' if CANTERA_AVAILABLE else 'Empirical fallback (无 Cantera)'}",
            source="Solver",
        )
        self._log_step(
            f"Newton 迭代步数 = {iters_str},  归一化残差 = {residual:.4e}",
            source="Solver",
        )

        if converged:
            self._log_ok(
                f"完整数值收敛 — 内层、热反馈、温度目标与目标工况均已通过 (内层残差 {residual:.2e})",
                source="Solver",
            )
            self._set_log_status("空闲 / Idle", conv=f"✓ 已收敛 ({residual:.1e})", conv_kind="ok")
        elif numerical["status"] == "unknown":
            self._log_warn(
                f"完整收敛证据不足 — 内层残差 {residual:.2e} 不能替代外环及目标工况检查，请重新计算。",
                source="Solver",
            )
            self._set_log_status("空闲 / Idle", conv="⚠ 收敛证据不完整", conv_kind="warn")
        else:
            self._log_error(
                f"未完全收敛 — 至少一层未通过；内层残差 {residual:.2e}，结果仅供诊断。",
                source="Solver",
            )
            self._set_log_status("空闲 / Idle", conv=f"✗ 未收敛 ({residual:.1e})", conv_kind="error")

        if s.get("solver_warning", 0.0):
            self._log_warn(
                "solver_warning = 1：内层、热反馈、温度目标或目标工况尚未完整通过。",
                source="Solver",
            )

        # 关键工况要点：自动审计的几条物理量
        for label, key, unit, fmt in [
            ("真空推力",         "thrust_vac_N",                   "kN",   lambda v: f"{v/1000:.2f}"),
            ("海平面推力",       "thrust_N",                       "kN",   lambda v: f"{v/1000:.2f}"),
            ("真空比冲 Isp_vac", "Isp_vac_s",                      "s",    lambda v: f"{v:.2f}"),
            ("海平面比冲 Isp_sl","Isp_s",                          "s",    lambda v: f"{v:.2f}"),
            ("总质量流量",       "mdot_total_kg_s",                "kg/s", lambda v: f"{v:.2f}"),
            ("主燃烧室温度",     "chamber_temperature_K",          "K",    lambda v: f"{v:.0f}"),
            ("富氧预燃室温度",   "ox_preburner_T_combustion_K",    "K",    lambda v: f"{v:.0f}"),
            ("富燃预燃室温度",   "fuel_preburner_T_combustion_K",  "K",    lambda v: f"{v:.0f}"),
            ("液氧泵功率",       "ox_pump_power_MW",               "MW",   lambda v: f"{v:.2f}"),
            ("燃料泵功率",       "fuel_pump_power_MW",             "MW",   lambda v: f"{v:.2f}"),
        ]:
            v = s.get(key)
            if v is None:
                continue
            try:
                self._log_info(f"{label}: {fmt(float(v))} {unit}", source="Result")
            except Exception:
                pass

        for w in results.warnings:
            self._log_warn(str(w), source="Audit")

    def _draw_topology(self, results: CycleResults):
        """拓扑图渲染委托给 topology_drawing.FFSCTopologyRenderer (matplotlib)。

        渲染为 **SVG 矢量** 后用 QGraphicsSvgItem 放进 scene —— 滚轮放大时
        QSvgRenderer 按当前缩放重新栅格化,任意倍率都清晰(不再像位图那样糊)。

        交互保持不变:沿用 AutoFitGraphicsView 的滚轮缩放和拖动。
        渲染失败时 fallback 到错误信息文本,不让 GUI 崩。
        """
        from PySide6.QtWidgets import QGraphicsTextItem

        self.scene.clear()
        self.scene.setBackgroundBrush(QBrush(QColor("#f8fafc")))

        try:
            from topology_drawing import FFSCTopologyRenderer, GGTopologyRenderer
            from PySide6.QtSvgWidgets import QGraphicsSvgItem
            from PySide6.QtSvg import QSvgRenderer
            from PySide6.QtCore import QByteArray

            # 防御性:确保拓扑图拿到与"当前 results"对应的几何 contour。
            # 若 self.last_geometry 缺失(未勾选自动几何,或调用时序问题),
            # 就用当前 results 现算一份(纯代数,~ms 级),保证喷管始终是
            # 真实钟形,而不是 fallback 锥形。
            geom = self.last_geometry
            if not geom:
                try:
                    _inp = results.inputs
                    geom = geometry_to_dict(compute_geometry(
                        results,
                        contraction_ratio=getattr(_inp, "chamber_contraction_ratio", 2.5),
                        l_star=getattr(_inp, "chamber_l_star_m", 1.0),
                    ))
                except Exception:
                    geom = None

            # V20：按结果的循环类型选择渲染器（FFSC / GG 两套拓扑布局）
            _is_gg = (str(results.summary.get("cycle_type_text", "")) == "gg"
                      or str(getattr(results.inputs, "cycle_type", "ffsc")) == "gg")
            _renderer_cls = GGTopologyRenderer if _is_gg else FFSCTopologyRenderer
            renderer = _renderer_cls(
                summary=results.summary,
                geometry=geom,
                inputs_pc=getattr(results.inputs, "pc", None),
                inputs_thrust=getattr(results.inputs, "thrust", None),
            )
            # 保存渲染器,供"导出 PNG"按钮复用(re-render 出高分辨率位图/矢量)
            self._topology_renderer = renderer
            # SVG 矢量渲染:放大不糊
            svg_bytes = renderer.render_svg()
            svg_renderer = QSvgRenderer(QByteArray(svg_bytes))
            # 把 renderer 挂到 self,避免被 GC 回收(QGraphicsSvgItem 持有的是弱引用)
            self._topology_svg_renderer = svg_renderer
            svg_item = QGraphicsSvgItem()
            svg_item.setSharedRenderer(svg_renderer)
            self.scene.addItem(svg_item)
            bounds = svg_item.boundingRect()
            self.scene.setSceneRect(bounds)
            self.view.fit_scene()
        except Exception as ex:
            import traceback as _tb
            err = QGraphicsTextItem(
                "拓扑图渲染失败:\n{}\n\n请检查 topology_drawing.py".format(ex)
            )
            err.setFont(QFont(QApplication.instance().font().family(), 12))
            err.setDefaultTextColor(QColor("#dc2626"))
            err.setPos(40, 60)
            self.scene.addItem(err)
            self.scene.setSceneRect(0, 0, 800, 200)
            self.view.fit_scene()
            _tb.print_exc()

    def _topo_text(self, text: str, x: float, y: float, size: int = 9, bold: bool = False,
                   color: QColor | None = None, width: float | None = None):
        item = QGraphicsTextItem(text)
        item.setDefaultTextColor(color or QColor("#111827"))
        item.setFont(QFont(QApplication.instance().font().family(), size, QFont.Bold if bold else QFont.Normal))
        if width is not None:
            item.setTextWidth(width)
        item.setPos(x, y)
        self.scene.addItem(item)
        return item

    def _topo_line(self, x1: float, y1: float, x2: float, y2: float, color: QColor, width: float = 2,
                   alpha: int = 255, dash: bool = False):
        c = QColor(color)
        c.setAlpha(alpha)
        pen = QPen(c, width)
        if dash:
            pen.setStyle(Qt.DashLine)
        line = QGraphicsLineItem(x1, y1, x2, y2)
        line.setPen(pen)
        self.scene.addItem(line)
        return line

    def _topo_polyline(self, pts: List[Tuple[float, float]], color: QColor, width: float = 4,
                       arrow: bool = True, label: str = ""):
        if len(pts) < 2:
            return
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            self._topo_line(x1, y1, x2, y2, color, width)
        if arrow:
            self._topo_arrow_head(pts[-2], pts[-1], color, width)
        if label:
            mid = pts[len(pts) // 2]
            self._topo_text(label, mid[0] + 5, mid[1] - 25, size=8, color=QColor("#374151"), width=150)

    def _topo_arrow_head(self, p0: Tuple[float, float], p1: Tuple[float, float], color: QColor, width: float = 4):
        x0, y0 = p0
        x1, y1 = p1
        ang = math.atan2(y1 - y0, x1 - x0)
        size = max(9, width * 2.4)
        p_left = QPointF(x1 - size * math.cos(ang - math.pi / 6), y1 - size * math.sin(ang - math.pi / 6))
        p_right = QPointF(x1 - size * math.cos(ang + math.pi / 6), y1 - size * math.sin(ang + math.pi / 6))
        poly = QGraphicsPolygonItem(QPolygonF([QPointF(x1, y1), p_left, p_right]))
        poly.setBrush(QBrush(color))
        poly.setPen(QPen(color))
        self.scene.addItem(poly)

    def _topo_dashed_box(self, x: float, y: float, w: float, h: float, label: str, color: QColor):
        rect = QGraphicsRectItem(x, y, w, h)
        pen = QPen(color, 2)
        pen.setStyle(Qt.DashLine)
        rect.setPen(pen)
        rect.setBrush(QBrush(Qt.NoBrush))
        self.scene.addItem(rect)
        self._topo_text(label, x + w / 2 - 75, y + 5, size=12, bold=True, color=QColor("#111827"))

    def _draw_component_box(self, x: float, y: float, w: float, h: float, label: str,
                            fill: QColor, stroke: QColor):
        rect = QGraphicsRectItem(x, y, w, h)
        rect.setBrush(QBrush(fill))
        rect.setPen(QPen(stroke, 2))
        self.scene.addItem(rect)
        self._topo_text(label, x + 5, y + 8, size=9, bold=True, color=QColor("#111827"), width=w - 10)

    def _draw_valve_symbol(self, x: float, y: float, label: str = "阀"):
        r = 18
        rect = QGraphicsRectItem(x - r, y - r, 2 * r, 2 * r)
        rect.setBrush(QBrush(QColor("#ffffff")))
        rect.setPen(QPen(QColor("#111827"), 2))
        self.scene.addItem(rect)
        self._topo_line(x - r + 4, y - r + 4, x + r - 4, y + r - 4, QColor("#111827"), 2)
        self._topo_line(x + r - 4, y - r + 4, x - r + 4, y + r - 4, QColor("#111827"), 2)
        self._topo_text(label, x - 9, y + r + 2, size=8, color=QColor("#374151"))

    def _draw_pump_symbol(self, x: float, y: float, label: str, color: QColor, mirror: bool = False):
        # 近似泵符号：扩压壳体 + 出口短管
        sign = -1 if mirror else 1
        pts = [
            QPointF(x, y + 20), QPointF(x + sign * 35, y), QPointF(x + sign * 68, y),
            QPointF(x + sign * 68, y + 70), QPointF(x + sign * 35, y + 70), QPointF(x, y + 50)
        ]
        poly = QGraphicsPolygonItem(QPolygonF(pts))
        poly.setBrush(QBrush(QColor("#ffffff")))
        poly.setPen(QPen(color, 2.5))
        self.scene.addItem(poly)
        self._topo_text(label, x - (75 if mirror else 5), y + 82, size=10, bold=True, color=QColor("#111827"), width=130)

    def _draw_turbine_symbol(self, x: float, y: float, label: str, color: QColor):
        pts = [QPointF(x, y), QPointF(x + 55, y + 10), QPointF(x + 55, y + 60), QPointF(x, y + 70)]
        poly = QGraphicsPolygonItem(QPolygonF(pts))
        poly.setBrush(QBrush(QColor("#ffffff")))
        poly.setPen(QPen(color, 2.5))
        self.scene.addItem(poly)
        self._topo_text(label, x - 10, y + 75, size=8, bold=True, color=QColor("#374151"), width=95)

    def _draw_chamber_nozzle(self, x: float, y: float):
        path = QPainterPath()
        path.moveTo(x - 85, y - 50)
        path.lineTo(x + 85, y - 50)
        path.lineTo(x + 85, y + 20)
        path.cubicTo(x + 50, y + 40, x + 38, y + 78, x + 28, y + 120)
        path.cubicTo(x + 80, y + 190, x + 110, y + 245, x + 120, y + 300)
        path.lineTo(x - 120, y + 300)
        path.cubicTo(x - 110, y + 245, x - 80, y + 190, x - 28, y + 120)
        path.cubicTo(x - 38, y + 78, x - 50, y + 40, x - 85, y + 20)
        path.closeSubpath()
        item = QGraphicsPathItem(path)
        item.setBrush(QBrush(QColor("#fef08a")))
        item.setPen(QPen(QColor("#ef4444"), 2.5))
        self.scene.addItem(item)
        self._topo_text("主燃烧室\nCombustion chamber", x - 70, y - 25, size=11, bold=True, color=QColor("#111827"), width=145)
        self._topo_line(x - 32, y + 120, x + 32, y + 120, QColor("#9ca3af"), 8, alpha=140)
        self._topo_text("噴管 / Nozzle", x - 42, y + 255, size=10, bold=True, color=QColor("#374151"))

    def _draw_heat_exchanger(self, x: float, y: float):
        self._topo_text("再生冷却换热通道\nHeat exchanger", x, y, size=10, bold=True, color=QColor("#111827"), width=180)
        for i in range(6):
            yy = 545 + i * 34
            self._topo_line(400, yy, 600, yy, QColor("#9ca3af"), 10, alpha=105)
            self._topo_arrow_head((455, yy), (510, yy), QColor("#dc2626"), 4)

    def _topo_number(self, num: int, x: float, y: float, note: str):
        size = 46
        path = QPainterPath()
        path.addEllipse(x, y, size, size)
        circ = QGraphicsPathItem(path)
        circ.setBrush(QBrush(QColor("#ffffff")))
        circ.setPen(QPen(QColor("#ef4444"), 3))
        self.scene.addItem(circ)
        self._topo_text(str(num), x + 14, y + 3, size=18, bold=True, color=QColor("#ef4444"))
        self._topo_text(note, x + size + 5, y + 12, size=8, color=QColor("#6b7280"), width=120)

    def _draw_topology_summary(self, x: float, y: float, s: Dict[str, Any],
                               width: float = 215, height: float = 220):
        """工况摘要卡片（V5.4.5：移到右侧不遮挡主图，列出实时关键量）。"""
        def _mpa(key):
            v = s.get(key)
            try:
                return f"{float(v)/1e6:.2f} MPa" if v is not None else "—"
            except Exception:
                return "—"
        def _kgs(key):
            v = s.get(key)
            try:
                return f"{float(v):.1f} kg/s" if v is not None else "—"
            except Exception:
                return "—"
        def _num(key, fmt="{:.3f}"):
            v = s.get(key)
            try:
                return fmt.format(float(v)) if v is not None else "—"
            except Exception:
                return "—"

        rows = [
            ("总流量 ṁ",        _kgs("mdot_total_kg_s")),
            ("LOX 流量",         _kgs("mdot_ox_kg_s")),
            ("燃料流量",         _kgs("mdot_fuel_kg_s")),
            ("LOX 泵出口 P",     _mpa("lox_pump_out_pressure_Pa")),
            ("燃料泵出口 P",     _mpa("fuel_pump_out_pressure_Pa")),
            ("富氧 PB  O/F",     _num("ox_preburner_mr",   "{:.2f}")),
            ("富燃 PB  O/F",     _num("fuel_preburner_mr", "{:.3f}")),
            ("主燃室 Pc",        _mpa("chamber_pressure_Pa")),
        ]

        card = QGraphicsRectItem(x, y, width, height)
        card.setBrush(QBrush(QColor("#ffffff")))
        card.setPen(QPen(QColor("#cbd5e1"), 1.5))
        self.scene.addItem(card)
        # 标题栏（淡蓝）
        title_bar = QGraphicsRectItem(x, y, width, 26)
        title_bar.setBrush(QBrush(QColor("#eff6ff")))
        title_bar.setPen(QPen(QColor("#cbd5e1"), 1.5))
        self.scene.addItem(title_bar)
        self._topo_text("当前工况摘要 / Operating Point", x + 10, y + 6,
                        size=10, bold=True, color=QColor("#1e3a8a"),
                        width=width - 20)

        yy = y + 34
        for k, v in rows:
            self._topo_text(k, x + 12, yy, size=8, color=QColor("#64748b"), width=120)
            self._topo_text(v, x + 110, yy, size=8, bold=True,
                            color=QColor("#111827"), width=width - 120)
            yy += 22

    def _draw_topology_spec_card(self, x: float, y: float, s: Dict[str, Any],
                                 results: "CycleResults",
                                 width: float = 215, height: float = 240):
        """
        技术规格卡片（V5.4.5：Maslov 图风格的 spec sheet — 静态/半静态信息）。
        包含循环类型、推进剂、设计推力、Isp、Pc、O/F、节流范围。
        """
        inp = results.inputs

        def _kn():
            v = s.get("thrust_N")
            try:
                return f"{float(v)/1000:.0f} kN" if v is not None else f"{inp.thrust/1000:.0f} kN (target)"
            except Exception:
                return f"{inp.thrust/1000:.0f} kN (target)"

        def _isp():
            # 优先显示真空比冲(发动机内禀属性)
            v = s.get("Isp_vac_s", s.get("Isp_s"))
            try:
                return f"{float(v):.1f} s (vac)" if v is not None else f"{inp.target_isp:.0f} s (init.)"
            except Exception:
                return "—"

        def _mpa(v: float) -> str:
            try:
                return f"{v/1e6:.1f} MPa"
            except Exception:
                return "—"

        try:
            of_design = f"{inp.of_ratio:.2f}"
        except Exception:
            of_design = "—"

        throttle_min = getattr(inp, "throttle_min", None)
        try:
            throttle_str = f"{float(throttle_min)*100:.0f}–100%" if throttle_min is not None else "—"
        except Exception:
            throttle_str = "—"

        rows = [
            ("循环 Cycle",        "FFSC 全流量分级燃烧"),
            ("推进剂 Propellant", "液氧 + 燃料"),
            ("设计推力 F",         _kn()),
            ("真空比冲 Isp_vac",   _isp()),
            ("燃烧室压力 Pc",      _mpa(inp.pc)),
            ("总混合比 O/F",       of_design),
            ("喷管膨胀比 ε",       f"{getattr(inp, 'expansion_ratio', 0):.1f}" if hasattr(inp, "expansion_ratio") else "—"),
            ("节流范围 Throttle", throttle_str),
        ]

        card = QGraphicsRectItem(x, y, width, height)
        card.setBrush(QBrush(QColor("#ffffff")))
        card.setPen(QPen(QColor("#cbd5e1"), 1.5))
        self.scene.addItem(card)
        # 标题栏（淡灰）
        title_bar = QGraphicsRectItem(x, y, width, 26)
        title_bar.setBrush(QBrush(QColor("#f1f5f9")))
        title_bar.setPen(QPen(QColor("#cbd5e1"), 1.5))
        self.scene.addItem(title_bar)
        self._topo_text("技术规格 / Specification", x + 10, y + 6,
                        size=10, bold=True, color=QColor("#0f172a"),
                        width=width - 20)

        yy = y + 34
        for k, v in rows:
            self._topo_text(k, x + 12, yy, size=8, color=QColor("#64748b"), width=110)
            self._topo_text(v, x + 105, yy, size=8, bold=True,
                            color=QColor("#0f172a"), width=width - 115)
            yy += 22

    def compute_geometry_from_last(self, silent: bool = False):
        if self.last_results is None:
            if not silent:
                QMessageBox.information(self, "没有求解结果", "请先运行设计点求解。")
            return
        try:
            inputs = self.last_results.inputs
            geom = compute_geometry(
                self.last_results,
                contraction_ratio=getattr(inputs, "chamber_contraction_ratio", 2.5),
                l_star=getattr(inputs, "chamber_l_star_m", 1.0),
            )
            self.last_geometry = geometry_to_dict(geom)
            self._populate_geometry(self.last_geometry)
            if not silent:
                self.tabs.setCurrentIndex(self._tab_idx["geometry"])
                self._append_log("几何参数计算完成。")
        except Exception as e:
            self._append_log(traceback.format_exc())
            if not silent:
                QMessageBox.warning(self, "几何计算失败", str(e))

    def _populate_geometry(self, geom: Dict[str, Any]):
        # 隐藏总表始终装全量数据(供导出/调试),卡片按当前 tab 过滤
        self.geometry_table.setRowCount(0)
        for obj_name, obj in geom.items():
            title = GEOMETRY_OBJECT_LABELS.get(obj_name, obj_name)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in {"contour_x", "contour_r"}:
                        val = f"{len(v)} 个轮廓点" if isinstance(v, list) else self._fmt(v)
                    elif isinstance(v, list):
                        val = json.dumps(v[:5], ensure_ascii=False) + (" ..." if len(v) > 5 else "")
                    else:
                        val = self._fmt(v)
                    label = GEOMETRY_KEY_LABELS.get(k, k)
                    self._add_table_row(self.geometry_table, [title, label, val, "SI 单位，字段名注明者除外"])
            else:
                label = GEOMETRY_OBJECT_LABELS.get(obj_name, obj_name)
                self._add_table_row(self.geometry_table, ["总体", label, self._fmt(obj), ""])
        self.geometry_table.resizeColumnsToContents()

        self._render_geometry_cards()
        self._draw_geometry(geom)

    def _current_geometry_filter_keys(self) -> Optional[Set[str]]:
        """根据上方图纸 tab 返回下方卡片应显示的 geom 顶层键集合。"""
        panel = getattr(self, "geometry_drawing_panel", None)
        if panel is None:
            return None
        idx = panel.tabs.currentIndex()
        if 0 <= idx < len(GEOMETRY_TAB_FILTER_KEYS):
            return GEOMETRY_TAB_FILTER_KEYS[idx]
        return None

    def _render_geometry_cards(self):
        """按当前过滤集合重新铺设下方参数卡片(不重建总表/图纸)。"""
        geom = getattr(self, "last_geometry", None)
        if not geom:
            return
        self._clear_layout_keep_stretch(self.geometry_cards_layout)
        insert_at = max(self.geometry_cards_layout.count() - 1, 0)

        filter_keys = self._current_geometry_filter_keys()
        overall_rows: List[Tuple[str, str, str]] = []

        for obj_name, obj in geom.items():
            if filter_keys is not None and obj_name not in filter_keys:
                continue
            title = GEOMETRY_OBJECT_LABELS.get(obj_name, obj_name)
            if isinstance(obj, dict):
                rows = []
                for k, v in obj.items():
                    if k in {"contour_x", "contour_r"}:
                        val = f"{len(v)} 个轮廓点" if isinstance(v, list) else self._fmt(v)
                    elif isinstance(v, list):
                        val = json.dumps(v[:5], ensure_ascii=False) + (" ..." if len(v) > 5 else "")
                    else:
                        val = self._fmt(v)
                    label = GEOMETRY_KEY_LABELS.get(k, k)
                    rows.append((label, val, ""))
                self.geometry_cards_layout.insertWidget(insert_at, self._make_card(title, rows))
                insert_at += 1
            else:
                label = GEOMETRY_OBJECT_LABELS.get(obj_name, obj_name)
                overall_rows.append((label, self._fmt(obj), ""))

        if overall_rows:
            self.geometry_cards_layout.insertWidget(
                insert_at, self._make_card("总体尺寸", overall_rows)
            )

    def _on_geometry_drawing_tab_changed(self, _index: int):
        if getattr(self, "last_geometry", None) is None:
            return
        self._render_geometry_cards()

    def _draw_geometry(self, geom: Dict[str, Any]):
        if hasattr(self, "geometry_drawing_panel"):
            self.geometry_drawing_panel.update_geometry(geom)

    def closeEvent(self, event):
        """退出前收尾 3D 渲染窗口,趁 GL 上下文有效让 VTK 正常清理;
        并关掉 VTK 警告显示,避免关闭瞬间刷一堆 wglMakeCurrent 报错。"""
        try:
            from vtkmodules.vtkCommonCore import vtkObject
            vtkObject.GlobalWarningDisplayOff()
        except Exception:
            pass
        panel = getattr(self, "geometry_drawing_panel", None)
        if panel is not None and hasattr(panel, "cleanup"):
            try:
                panel.cleanup()
            except Exception:
                pass
        super().closeEvent(event)

    def _populate_throttle(self, sweep: List[dict]):
        self.throttle_table.setRowCount(0)
        for pt in sweep:
            res = pt.get("results")
            s = res.summary if res is not None else {}
            self._add_table_row(self.throttle_table, [
                self._fmt(pt.get("throttle_ratio", 0) * 100),
                self._fmt(pt.get("thrust_target_N", 0) / 1e3),
                self._fmt(s.get("thrust_N", 0) / 1e3),
                self._fmt(pt.get("pc_Pa", 0) / 1e6),
                self._fmt(s.get("Isp_s", 0)),
                self._fmt(s.get("mdot_total_kg_s", 0)),
                self._fmt(s.get("chamber_temperature_K", 0)),
                self._fmt(s.get("ox_preburner_T_combustion_K", 0)),
                self._fmt(s.get("fuel_preburner_T_combustion_K", 0)),
                "是" if pt.get("converged") else "否",
            ])
        self.throttle_table.resizeColumnsToContents()
        self._update_throttle_charts(sweep)

    def export_json(self):
        if self.last_results is None:
            QMessageBox.information(self, "没有可导出结果", "请先运行设计点求解。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出结果", "ffsc_results.json", "JSON (*.json)")
        if not path:
            return
        data = {
            "inputs": asdict(self.last_results.inputs),
            "summary": self.last_results.summary,
            "states": {k: asdict(v) for k, v in self.last_results.states.items()},
            "components": {k: asdict(v) for k, v in self.last_results.components.items()},
            "warnings": self.last_results.warnings,
            "geometry": self.last_geometry,
        }
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self._append_log(f"已导出 JSON: {path}")

    def export_cfturbo_md(self):
        """把四个叶轮机械（液氧泵/燃料泵/富氧涡轮/富燃涡轮）设计点导出为
        CFTurbo 录入参考的 Markdown 文档。"""
        if self.last_results is None:
            QMessageBox.information(self, "没有可导出结果", "请先运行设计点求解。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CFTurbo 设计点参考 (Markdown)",
            "ffsc_cfturbo_design_point.md", "Markdown (*.md)")
        if not path:
            return
        try:
            from cfturbo_export import export_cfturbo_markdown
            export_cfturbo_markdown(self.last_results, path)
            self._append_log(f"已导出 CFTurbo 设计点 Markdown: {path}")
            QMessageBox.information(
                self, "导出成功",
                f"已导出液氧泵/燃料泵/富氧涡轮/富燃涡轮的 CFTurbo 录入参考：\n{path}")
        except Exception as ex:
            QMessageBox.warning(self, "导出失败", f"无法导出 CFTurbo Markdown：\n{ex}")

    def _append_log(self, text: str):
        """旧接口（向后兼容）：直接走 INFO 级日志。"""
        self._log_info(text)


    @staticmethod
    def _fmt(v: Any) -> str:
        if v is None:
            return "—"
        if isinstance(v, bool):
            return "是" if v else "否"
        if isinstance(v, str):
            return v
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        if f == 0.0:
            return "0"
        abs_f = abs(f)
        if abs_f >= 1e6 or (abs_f < 1e-3 and abs_f > 0):
            return f"{f:.4e}"
        if abs_f >= 100:
            return f"{f:.3f}"
        if abs_f >= 1:
            return f"{f:.4f}"
        return f"{f:.5f}"

    @staticmethod
    def _add_table_row(table: QTableWidget, values: list):
        row = table.rowCount()
        table.insertRow(row)
        for col, val in enumerate(values):
            item = QTableWidgetItem(str(val))
            if col > 0:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, col, item)

    def _on_font_size_changed(self, text: str):
        size_map = {"小 (12px)": 12, "中 (14px)": 14, "大 (16px)": 16, "特大 (18px)": 18}
        self._font_size = size_map.get(text, 14)
        self._apply_style()

    def _apply_style(self):
        fs   = self._font_size
        fs_s = max(fs - 2, 9)
        fs_t = fs + 8
        fs_k = fs + 9
        fs_c = fs + 2

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: #F5F7FA; color: #111827;
                /* 拉丁字母/数字优先用 Segoe UI(更清晰利落),中文回落到
                   微软雅黑 UI 变体;思源黑体作为跨平台/已安装时的更优选项。
                   字体族按"命中即用"顺序排列。 */
                font-family: 'Segoe UI', 'Source Han Sans SC', 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif;
                font-size: {fs}px;
            }}
            QToolBar {{
                background: #FFFFFF; border: none;
                border-bottom: 1px solid #E5E7EB;
                spacing: 6px; padding: 4px 8px;
            }}
            QToolButton {{
                background: #F8FAFC; color: #1F2937;
                border: 1px solid #E5E7EB; border-radius: 6px;
                padding: 5px 10px; font-size: {fs}px; font-weight: 600;
            }}
            QToolButton:hover {{ background: #EFF6FF; border-color: #93C5FD; color: #1D4ED8; }}
            QPushButton {{
                background: #2563EB; color: #FFFFFF;
                border: none; border-radius: 8px;
                padding: 7px 18px; font-size: {fs}px; font-weight: 700;
            }}
            QPushButton:hover  {{ background: #1D4ED8; }}
            QPushButton:pressed {{ background: #1E40AF; }}
            QPushButton:disabled {{ background: #93C5FD; color: #BFDBFE; }}
            QPushButton#SecondaryButton {{
                background: #F3F4F6; color: #374151;
                border: 1px solid #D1D5DB; border-radius: 8px;
                padding: 5px 14px; font-size: {fs}px; font-weight: 600;
            }}
            QPushButton#SecondaryButton:hover {{
                background: #E5E7EB; border-color: #9CA3AF;
            }}
            QLineEdit#ParamEdit {{
                background: #FFFFFF; border: 1px solid #D1D5DB;
                border-radius: 7px; padding: 5px 7px;
                font-family: 'Cascadia Mono', Consolas, monospace;
                font-size: {fs}px;
            }}
            QLineEdit#ParamEdit:focus {{ border: 1px solid #2563EB; background: #F8FBFF; }}
            QProgressBar {{
                background: #E5E7EB; border: none;
                border-radius: 5px; height: 8px;
            }}
            QProgressBar::chunk {{ background: #2563EB; border-radius: 5px; }}
            QTabWidget::pane {{ border: 1px solid #E5E7EB; background: #FFFFFF; border-radius: 12px; }}
            QTabBar::tab {{
                background: #F3F4F6; color: #6B7280;
                border: 1px solid #E5E7EB; border-bottom: none;
                padding: 6px 16px; font-size: {fs}px; font-weight: 600;
                border-radius: 8px 8px 0 0; margin-right: 2px;
            }}
            QTabBar::tab:selected {{ background: #FFFFFF; color: #1D4ED8; font-weight: 700; }}
            QTabBar::tab:hover:!selected {{ background: #EFF6FF; color: #2563EB; }}
            QTableWidget {{
                background: #FFFFFF; border: 1px solid #E5E7EB;
                gridline-color: #F3F4F6; alternate-background-color: #F9FAFB;
                font-size: {fs}px;
            }}
            QHeaderView::section {{
                background: #F9FAFB; color: #374151;
                border: none; border-bottom: 1px solid #E5E7EB;
                padding: 5px 10px; font-size: {fs_s}px; font-weight: 700;
            }}
            QTextEdit {{
                background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 8px;
                font-family: 'Cascadia Mono', Consolas, 'Microsoft YaHei';
                font-size: {fs_s}px;
            }}
            QComboBox {{
                background: #FFFFFF; border: 1px solid #D1D5DB;
                border-radius: 6px; padding: 3px 8px; font-size: {fs_s}px;
            }}
            QLabel#PanelTitle  {{ font-size: {fs_t}px; font-weight: 900; color: #0F172A; }}
            QLabel#MutedLabel  {{ color: #6B7280; font-size: {fs_s}px; }}
            QLabel#BadgeNeutral {{ background: #E5E7EB; color: #374151; border-radius: 11px; padding: 5px 12px; font-weight: 800; font-size: {fs_s}px; }}
            QLabel#BadgeOk      {{ background: #DCFCE7; color: #15803D; border-radius: 11px; padding: 5px 12px; font-weight: 800; font-size: {fs_s}px; }}
            QLabel#BadgeWarning {{ background: #FEF3C7; color: #B45309; border-radius: 11px; padding: 5px 12px; font-weight: 800; font-size: {fs_s}px; }}
            QLabel#BadgeError   {{ background: #FEE2E2; color: #B91C1C; border-radius: 11px; padding: 5px 12px; font-weight: 800; font-size: {fs_s}px; }}
            QFrame#ResultCard {{ background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 14px; }}
            QFrame#ResultCard:hover {{ border-color: #CBD5E1; }}
            QFrame#KpiCard_ok, QFrame#KpiCard_neutral,
            QFrame#KpiCard_warning, QFrame#KpiCard_error {{
                background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px;
            }}
            QFrame#KpiCard_ok      {{ border-left: 4px solid #16A34A; }}
            QFrame#KpiCard_warning {{ border-left: 4px solid #D97706; }}
            QFrame#KpiCard_error   {{ border-left: 4px solid #DC2626; }}
            QFrame#KpiCard_neutral {{ border-left: 4px solid #94A3B8; }}
            QLabel#KpiTitle {{ color: #6B7280; font-size: {fs_s}px; font-weight: 600; }}
            QLabel#KpiValue {{ color: #0F172A; font-size: {fs_t}px; font-weight: 900; }}
            QLabel#KpiHint  {{ color: #9CA3AF; font-size: {fs_s}px; }}
            QLabel#CardTitle  {{ font-size: {fs_c}px; font-weight: 900; color: #111827; }}
            QLabel#KVKey   {{ color: #6B7280; font-size: {fs}px; }}
            QLabel#KVValue {{ color: #111827; font-family: 'Cascadia Mono', Consolas, 'Microsoft YaHei'; font-weight: 800; font-size: {fs}px; }}
            QFrame#StatusStrip {{ background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 14px; }}
            QLabel#PillNeutral {{ background: #F3F4F6; color: #374151; border-radius: 10px; padding: 4px 10px; font-weight: 800; font-size: {fs_s}px; }}
            QLabel#PillOk      {{ background: #DCFCE7; color: #15803D; border-radius: 10px; padding: 4px 10px; font-weight: 800; font-size: {fs_s}px; }}
            QLabel#PillWarn    {{ background: #FEF3C7; color: #B45309; border-radius: 10px; padding: 4px 10px; font-weight: 800; font-size: {fs_s}px; }}
            QLabel#PillError   {{ background: #FEE2E2; color: #B91C1C; border-radius: 10px; padding: 4px 10px; font-weight: 800; font-size: {fs_s}px; }}
            QScrollBar:vertical {{ background: #F3F4F6; width: 8px; border-radius: 4px; }}
            QScrollBar::handle:vertical {{ background: #CBD5E1; border-radius: 4px; min-height: 24px; }}
            QScrollBar::handle:vertical:hover {{ background: #94A3B8; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar:horizontal {{ background: #F3F4F6; height: 8px; border-radius: 4px; }}
            QScrollBar::handle:horizontal {{ background: #CBD5E1; border-radius: 4px; min-width: 24px; }}
            QScrollBar::handle:horizontal:hover {{ background: #94A3B8; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
            QSplitter::handle {{ background: #E5E7EB; }}
            QSplitter::handle:horizontal {{ width: 5px; }}
            QSplitter::handle:vertical   {{ height: 5px; }}
            QScrollArea {{ background: transparent; border: none; }}
            QGroupBox {{
                background: #FFFFFF; border: 1px solid #E5E7EB;
                border-radius: 12px; margin-top: 12px; padding: 10px;
                font-size: {fs}px; font-weight: 700;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #374151; }}
        """)


def main():
    import sys
    from PySide6.QtCore import qInstallMessageHandler

    def _qt_msg_filter(mode, ctx, message):
        # 过滤已知无害噪声:Qt SVG 字体链接警告(流程拓扑 SVG 渲染时大量刷屏,
        # 不影响显示)。注意:类别 "qt.svg" 在 ctx.category 里,不在 message 里。
        cat = ""
        try:
            cat = ctx.category or ""
        except Exception:
            cat = ""
        if cat == "qt.svg":
            return
        # 兜底:按消息内容匹配(link #... is undefined!)
        if "is undefined" in message and "link #" in message:
            return
        try:
            prefix = (cat + ": ") if cat else ""
            sys.stderr.write(prefix + message + "\n")
        except Exception:
            pass

    qInstallMessageHandler(_qt_msg_filter)
    app = QApplication(sys.argv)
    app.setApplicationName("FFSC Engine Simulator")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
