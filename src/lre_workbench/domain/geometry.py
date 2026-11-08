"""
geometry/types.py

发动机几何参数数据结构（从原 geometry.py 拆分，字段保持不变）。
所有长度单位 [m]，角度 [deg]（输出）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class NozzleGeometry:
    """喷管几何参数 (V7.1 扩充,补齐 CAD 建模所需字段)"""
    throat_diameter_m: float
    throat_radius_m: float
    exit_diameter_m: float
    exit_radius_m: float
    expansion_ratio: float
    contraction_ratio: float

    # 长度
    nozzle_length_m: float           # 喉部到出口
    convergent_length_m: float       # 收缩段(从燃烧室末端到喉部)长度
    total_length_m: float            # 燃烧室入口到喷管出口

    # 角度
    convergent_half_angle_deg: float
    throat_upstream_radius_m: float   # 喉部上游圆弧半径 Ru (~1.5 Rt)
    throat_downstream_radius_m: float # 喉部下游圆弧半径 Rd (~0.382 Rt)
    initial_expansion_angle_deg: float  # 喉部后初始膨胀角 θn
    exit_angle_deg: float               # 出口角 θe

    # 轮廓坐标 (从燃烧室端到喷管出口,x=0 在喉部)
    contour_x: List[float] = field(default_factory=list)  # 轴向 [m]
    contour_r: List[float] = field(default_factory=list)  # 半径 [m]

    # ── V7.1 新增:CAD 建模必需字段 ──────────────────
    bell_fraction: float = 0.80             # 相对同喉部/出口 15° 锥形喷管长度比
    nozzle_type: str = "parabolic_bell_engineering"  # 系统级工程近似，未进行推力优化
    nozzle_contour_method: str = "legacy_log_v1"
    angle_source: str = "historical_log_rule_unvalidated"
    coordinate_origin: str = "nozzle_throat"
    wall_thickness_mm: float = 0.0          # 喷管壁(含再生冷却层)总厚度
    inner_surface_area_m2: float = 0.0      # 燃气侧内表面积(用于热流分析)
    throat_axial_position_m: float = 0.0    # 喉部相对于燃烧室入口的轴向位置
    num_contour_points: int = 0             # 离散点数


@dataclass
class ChamberGeometry:
    """燃烧室几何参数 (V7.1 扩充,补齐 CAD 建模所需字段)"""
    diameter_m: float
    radius_m: float
    length_m: float                  # 圆柱段长度
    volume_m3: float                 # 圆柱段体积
    l_star_m: float                  # 特征长度 L*
    wall_thickness_mm: float         # 估算壁厚 (总厚度)
    contraction_ratio: float

    # ── V7.1 新增:CAD 建模必需字段 ──────────────────
    cylindrical_section_length_m: float = 0.0   # 圆柱段净长 (= length_m,显式输出)
    inner_wall_thickness_mm: float = 0.0        # 内壁(GRCop-84/铜合金)厚度
    cooling_channel_height_mm: float = 0.0      # 冷却通道径向高度
    cooling_channel_width_mm: float = 0.0       # 冷却通道周向宽度
    outer_wall_thickness_mm: float = 0.0        # 外壳(Inconel-718)厚度
    num_cooling_channels: int = 0               # 再生冷却通道数
    inner_surface_area_m2: float = 0.0          # 燃气侧总内表面积
    dome_radius_m: float = 0.0                  # 喷注端 dome 半径(等于 rc 时为平 dome)
    chamber_to_convergent_blend_radius_m: float = 0.0  # 圆柱段到收缩段过渡圆弧


@dataclass
class InjectorGeometry:
    """同一计算记录中的主喷注器尺寸；缺失值不以当前默认值补齐。"""
    face_diameter_m: float | None
    face_area_m2: float | None
    num_elements: int | None
    element_spacing_mm: float | None  # sqrt(A_face / N)，不是已设计的孔中心距
    d_ox_mm: float | None = None      # 中心流体圆孔直径
    d_post_mm: float | None = None    # 中心管外径（环缝内边界）
    d_outer_mm: float | None = None   # 环缝外边界（外筒内径）
    delta_fuel_mm: float | None = None  # 单侧径向环缝宽
    post_wall_thickness_mm: float | None = None
    geometry_closure_passed: bool | None = None
    geometry_source: str = "unavailable"
    geometry_status: str = "unavailable"
    geometry_warnings: List[str] = field(default_factory=list)


@dataclass
class PumpGeometry:
    """涡轮泵几何参数"""
    name: str
    fluid: str
    impeller_diameter_m: float
    shaft_speed_rpm: float
    specific_speed: float            # 量纲一比转速
    num_stages: int
    tip_speed_ms: float
    head_m: float                    # 扬程 [m]
    volume_flow_m3s: float           # 体积流量
    pump_type: str                   # "centrifugal" / "axial" / "mixed"


@dataclass
class TurbineGeometry:
    """涡轮几何参数"""
    name: str
    rotor_diameter_m: float
    shaft_speed_rpm: float           # 与泵同轴
    num_stages: int
    blade_height_mm: float
    u_over_c: float                  # 速比 U/C₀


@dataclass
class PreburnerGeometry:
    """预燃室几何参数"""
    name: str
    diameter_m: float
    length_m: float
    volume_m3: float


@dataclass
class NozzleFlow:
    """喷管过膨胀/分离/侧载 一维校核结果（Summerfield p_sep/p_a≈0.4 判据）"""
    chamber_pressure_pa: float = 0.0
    exit_pressure_pa: float = 0.0
    ambient_design_pa: float = 0.0
    gamma: float = 1.20
    npr_sea_level: float = 0.0        # pc / 101325
    npr_design: float = 0.0           # pc / p_amb_design
    separated_sea_level: bool = False
    separated_design: bool = False
    sep_pressure_sl_pa: float = 0.0   # 0.4 × 101325
    sep_area_ratio_sl: float = 0.0    # 海平面分离点面积比(未分离=0)
    sep_axial_x_sl_m: float = 0.0     # 海平面分离点轴向位置(喉部 x=0 坐标系)
    flowing_fraction_sl: float = 1.0  # 海平面满流段占喷管长度比例
    sideload_risk: str = ""           # 侧载风险定性
    summary: str = ""                 # 一句话结论


@dataclass
class EngineGeometry:
    """完整发动机几何参数汇总"""
    chamber: ChamberGeometry
    nozzle: NozzleGeometry
    injector: InjectorGeometry
    ox_pump: PumpGeometry
    fuel_pump: PumpGeometry
    ox_turbine: TurbineGeometry
    fuel_turbine: TurbineGeometry
    ox_preburner: PreburnerGeometry
    fuel_preburner: PreburnerGeometry

    # 喷管流动校核（过膨胀/分离/侧载）
    nozzle_flow: NozzleFlow

    # 总体尺寸
    overall_length_m: float          # 喷注器面到喷管出口
    overall_max_diameter_m: float    # 最大外径（喷管出口）
    dry_mass_estimate_kg: float      # 干重估算
