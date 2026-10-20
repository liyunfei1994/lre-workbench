"""
injector.py

同轴旋流喷注器详细设计模块（Coaxial Swirl Injector Design）

理论依据与设计方法
────────────────────────────────────────────────────────────────
Raptor 发动机使用同轴旋流（Coaxial Swirl）喷注器。⚠ 关键：本机是
**全流量分级燃烧（FFSC）**，进入主燃烧室的不是液氧/液甲烷液滴喷雾，
而是经预燃室+涡轮后的**两股高温气体**——富氧热气与富燃热气，因此这是
**气-气同轴旋流混合**，而非传统的液-气雾化喷注：
  - 内管（ox post）：富氧热气（中心射流）
  - 环缝（annular gap）：富燃热气经切向孔注入 → 旋流
  - 两股热气在喷注面下游靠旋流 + 湍流剪切快速混合（无液膜、无雾化）
  - 公开资料（Raptor 2 约 640 单元）：富氧气走中心管，富燃气切向钻孔入

注意：因是气-气，喷射密度用**真实气密度**（由 cycle 传入，~10²kg/m³ 量级），
      而非液密度（~10³kg/m³）；否则喷射速度/动量比/孔径会系统性失真。

主要设计参数（参考 NASA SP-8089, Huzel & Huang NASA SP-125）：
  1. 元件数量 N：由单元面积（火焰管面积 / N）和热流密度确定
  2. 氧化剂孔径 d_ox：由 Cd 和压降关系确定（孔板方程）
  3. 燃料环缝宽度 δ_fuel：由 Cd 和压降确定
  4. 动量通量比 J：燃料侧动量通量 / 氧化剂侧动量通量
     J = (ρ_f·v_f²)/(ρ_o·v_o²)，理想范围 J=0.3–0.8
  5. 旋流数 S（Swirl Number）：切向动量通量 / 轴向动量通量
     S > 0.6 时可形成稳定旋流雾化锥
  6. 喷注面热流密度 q_face：限制在 5–15 MW/m²

注意：Raptor 喷注器的具体参数属于 SpaceX 商业机密，
      本模块采用公开文献的同类发动机工程经验数据进行估算。

版本历史
────────────────────────────────────────────────────────────────
V1 (初始版)：基于 NASA SP-125 / SP-8089 的工程设计方法
"""

from __future__ import annotations

import math
from core import ComponentResult, _check_range, ValidationError

# ────────────────────────────────────────────────────────────────
# 物理常数与经验参数
# ────────────────────────────────────────────────────────────────

# 喷注器入口工质密度 [kg/m³]（FFSC 主喷注器=气-气；以下为 standalone 兜底默认，
# cycle 运行时会传入由 ρ=P·MW/(R·T) 算得的真实气密度）。
# 量级参考：富氧热气 ~150–220、富燃热气 ~80–120 kg/m³ @ 33 MPa、700–800 K。
_RHO_OX_GAS_DEFAULT   = 180.0   # 富氧热气密度兜底 [kg/m³]
_RHO_FUEL_GAS_DEFAULT = 95.0    # 富燃热气密度兜底 [kg/m³]

# 同轴旋流喷注器流量系数（经验值）
# Cd_ox：液氧内管（旋流切向槽），典型 0.65–0.75
# Cd_fuel：甲烷环缝（螺旋槽），典型 0.60–0.70
_CD_OX_DEFAULT   = 0.70
_CD_FUEL_DEFAULT = 0.65

# 旋流参数（经验）
# K_s = 旋流切向面积 / 轴向截面面积，决定旋流强度
# 典型值 0.3–0.6 对应旋流数 S ~0.5–1.0
_K_SWIRL_OX   = 0.45  # 氧化剂旋流系数
_K_SWIRL_FUEL = 0.50  # 燃料旋流系数


class Injector:
    """
    同轴旋流喷注器设计计算（Coaxial Swirl Injector）

    参数
    ────
    mdot_ox     : 氧化剂总质量流量 [kg/s]
    mdot_fuel   : 燃料总质量流量 [kg/s]
    pc          : 燃烧室压力 [Pa]
    dp_ratio_ox : 氧喷注器压降 / pc [-]
    dp_ratio_fuel : 燃料喷注器压降 / pc [-]
    chamber_radius : 燃烧室内半径 [m]（决定喷注面面积）
    rho_ox      : 氧化剂密度 [kg/m³]（可选，默认液氧）
    rho_fuel    : 燃料密度 [kg/m³]（可选，默认液甲烷）
    cd_ox       : 氧化剂流量系数（可选）
    cd_fuel     : 燃料流量系数（可选）
    mdot_per_element : 单元设计流量 [kg/s]（可选，默认自动）
    """

    def __init__(
        self,
        mdot_ox: float,
        mdot_fuel: float,
        pc: float,
        dp_ratio_ox: float,
        dp_ratio_fuel: float,
        chamber_radius: float,
        rho_ox: float = _RHO_OX_GAS_DEFAULT,
        rho_fuel: float = _RHO_FUEL_GAS_DEFAULT,
        cd_ox: float = _CD_OX_DEFAULT,
        cd_fuel: float = _CD_FUEL_DEFAULT,
        mdot_per_element: float | None = None,
    ):
        _check_range("Injector.mdot_ox",      mdot_ox,        lo=0, lo_inclusive=False)
        _check_range("Injector.mdot_fuel",    mdot_fuel,      lo=0, lo_inclusive=False)
        _check_range("Injector.pc",           pc,             lo=0, lo_inclusive=False)
        _check_range("Injector.dp_ratio_ox",  dp_ratio_ox,    lo=0.01, hi=0.60)
        _check_range("Injector.dp_ratio_fuel",dp_ratio_fuel,  lo=0.01, hi=0.60)
        _check_range("Injector.chamber_radius", chamber_radius, lo=0.01, hi=1.0)
        _check_range("Injector.rho_ox",       rho_ox,         lo=5.0)   # 气-气:气密度可低
        _check_range("Injector.rho_fuel",     rho_fuel,       lo=5.0)
        _check_range("Injector.cd_ox",        cd_ox,          lo=0.30, hi=0.95)
        _check_range("Injector.cd_fuel",      cd_fuel,        lo=0.30, hi=0.95)

        self.mdot_ox        = mdot_ox
        self.mdot_fuel      = mdot_fuel
        self.pc             = pc
        self.dp_ratio_ox    = dp_ratio_ox
        self.dp_ratio_fuel  = dp_ratio_fuel
        self.chamber_radius = chamber_radius
        self.rho_ox         = rho_ox
        self.rho_fuel       = rho_fuel
        self.cd_ox          = cd_ox
        self.cd_fuel        = cd_fuel
        self.mdot_per_element = mdot_per_element

    # ────────────────────────────────────────────────────────────
    # 内部计算方法
    # ────────────────────────────────────────────────────────────

    def _pressure_drops(self) -> tuple[float, float]:
        """计算氧化剂和燃料的喷注器压降 [Pa]"""
        dp_ox   = self.dp_ratio_ox   * self.pc
        dp_fuel = self.dp_ratio_fuel * self.pc
        return dp_ox, dp_fuel

    def _injection_velocities(self, dp_ox: float, dp_fuel: float) -> tuple[float, float]:
        """
        用伯努利方程计算喷注速度。

        v = Cd · sqrt(2·ΔP / ρ)

        注意：这是"有效轴向速度"，旋流喷注器的实际速度更高，
        轴向分量 = 实际速度 × cos(半锥角)，此处按轴向分量计算。
        """
        v_ox   = self.cd_ox   * math.sqrt(2.0 * dp_ox   / self.rho_ox)
        v_fuel = self.cd_fuel * math.sqrt(2.0 * dp_fuel / self.rho_fuel)
        return v_ox, v_fuel

    def _num_elements(self, v_ox: float, v_fuel: float) -> int:
        """
        估算喷注元件数量。

        方法：
        1. 计算单元总质量流量（ox + fuel per element）
           - 由喷注面面积 / 元件占据面积决定
           - 同时不超过单元最大热释放（防止局部过热）
        2. 单元 O/F = 整机 O/F（均匀混合假设）
        3. mdot_per_element 默认基于面积均布：
           - 每个元件面积约 (π·d_p²/4)，d_p ~3–8 mm（元件间距）
           - 对应每元件流量约 0.5–2.0 kg/s
        """
        # 喷注面积
        A_face = math.pi * self.chamber_radius ** 2

        if self.mdot_per_element is not None:
            mdot_elem = max(self.mdot_per_element, 0.1)
        else:
            # 经验：高压发动机（pc > 20 MPa）喷注密度高，
            # 每 cm² 面积流量约 0.8–2.0 kg/s
            # → 每元件占据面积 ~1.2–2.5 cm²
            # → 等效圆直径（元件间距）~12–18 mm
            # Raptor 量级（~600 kg/s, 直径~300mm）→ N~300–600
            # 此处由单元流量 1.0 kg/s 估算（含 ox+fuel）
            mdot_total_per_elem = 1.0  # kg/s per element（初始估计）

            # 根据面积和流量双重约束
            mdot_elem = mdot_total_per_elem

        mdot_total = self.mdot_ox + self.mdot_fuel
        N = max(20, int(round(mdot_total / mdot_elem)))

        # 同时根据元件间距约束（不能太密或太稀）
        # 元件间距 s = sqrt(A_face / N)，单位 m
        # 合理范围：8–20 mm
        s = math.sqrt(A_face / max(N, 1))
        if s < 0.008:       # 太密，增大 N 上限
            N = int(A_face / (0.008 ** 2))
        elif s > 0.020:     # 太稀，减小 N
            N = int(A_face / (0.020 ** 2))

        return max(20, N)

    def _element_geometry(
        self,
        N: int,
        dp_ox: float,
        dp_fuel: float,
        v_ox: float,
        v_fuel: float,
    ) -> tuple[float, float, float, float]:
        """
        计算单个喷注元件的氧化剂孔径 d_ox 和燃料环缝宽度 δ_fuel。

        孔板方程（单孔面积法）：
          A_ox   = mdot_ox / (N · ρ_ox · v_ox · Cd_ox)
                 = (π/4) · d_ox²
          → d_ox = sqrt(4·A_ox / π)

          A_fuel = mdot_fuel / (N · ρ_fuel · v_fuel · Cd_fuel)
          → δ_fuel = (d_post + δ_fuel)²环缝面积反推
                   ≈ A_fuel / (π · d_post)（小环缝近似）

        返回 (d_ox_mm, delta_fuel_mm, d_post_mm, d_outer_mm)
        """
        # 氧化剂孔面积（单孔）
        A_ox_per = self.mdot_ox / max(N * self.rho_ox * self.cd_ox * math.sqrt(
            2.0 * dp_ox / max(self.rho_ox, 1.0)
        ), 1e-9)
        d_ox = 2.0 * math.sqrt(A_ox_per / math.pi)   # [m]
        d_ox = max(1e-3, min(0.05, d_ox))             # 限制 1–50 mm

        # 甲烷环缝面积（单元）
        A_fuel_per = self.mdot_fuel / max(N * self.rho_fuel * self.cd_fuel * math.sqrt(
            2.0 * dp_fuel / max(self.rho_fuel, 1.0)
        ), 1e-9)

        # 氧化剂 post 外径（含 post 壁厚约 0.5 mm）
        d_post = d_ox + 2.0 * 5e-4      # post 外径 ≈ 孔径 + 1 mm 壁厚
        # 燃料环缝宽度：A_fuel = π·d_post·δ
        delta_fuel = A_fuel_per / max(math.pi * d_post, 1e-9)
        delta_fuel = max(2e-4, min(5e-3, delta_fuel))    # 0.2–5 mm

        # 外环直径（外筒内径）
        d_outer = d_post + 2.0 * delta_fuel

        return d_ox * 1000.0, delta_fuel * 1000.0, d_post * 1000.0, d_outer * 1000.0

    def _momentum_flux_ratio(
        self, v_ox: float, v_fuel: float
    ) -> float:
        """
        动量通量比 J = (ρ_f·v_f²) / (ρ_o·v_o²)

        物理意义（气-气）：
          J < 1 → 富氧气主导（中心气射流穿透外环富燃气）
          J ~ 0.3–0.8 → 最优混合范围（文献推荐）
          J > 1 → 富燃气主导（可能导致混合不均）
        """
        num = self.rho_fuel * v_fuel ** 2
        den = self.rho_ox   * v_ox   ** 2
        return num / max(den, 1e-9)

    def _swirl_numbers(self) -> tuple[float, float]:
        """
        估算旋流数 S（Swirl Number）。

        S = (切向动量通量·R) / (轴向动量通量·R)
          ≈ K_swirl / sqrt(1 - K_swirl²)

        其中 K_swirl = 旋流面积比（切向槽面积 / 总截面积）
        旋流数 > 0.6 时旋流强，增强气-气掺混（FFSC 主喷注无液膜，
        靠旋流 + 湍流剪切快速混合，而非液滴雾化）。

        参考：Lefebvre & McDonell, "Atomization and Sprays", 2nd Ed.（旋流数定义）
        """
        def _s(k):
            k = min(k, 0.99)
            return k / max(math.sqrt(1.0 - k ** 2), 1e-6)

        S_ox   = _s(_K_SWIRL_OX)
        S_fuel = _s(_K_SWIRL_FUEL)
        return S_ox, S_fuel

    def _face_heat_flux(self, N: int) -> float:
        """
        估算喷注面热流密度 [MW/m²]。

        喷注面因辐射和回流接受一定热流；
        Raptor 级别约 2–8 MW/m²。
        此处用经验公式：q_face ≈ 0.02 × pc × v_ox / 1e6  [MW/m²]
        """
        dp_ox, _ = self._pressure_drops()
        v_ox, _  = self._injection_velocities(dp_ox, 0.0)
        A_face   = math.pi * self.chamber_radius ** 2
        # 热流密度经验公式（量级估算）
        q_face = 0.015 * self.pc * v_ox / 1e7   # [MW/m²]
        q_face = max(1.0, min(20.0, q_face))
        return q_face

    # ────────────────────────────────────────────────────────────
    # 主接口
    # ────────────────────────────────────────────────────────────

    def evaluate(self) -> ComponentResult:
        """
        执行喷注器设计计算，返回 ComponentResult。
        """
        dp_ox, dp_fuel = self._pressure_drops()
        v_ox, v_fuel   = self._injection_velocities(dp_ox, dp_fuel)
        N              = self._num_elements(v_ox, v_fuel)

        d_ox_mm, delta_fuel_mm, d_post_mm, d_outer_mm = self._element_geometry(
            N, dp_ox, dp_fuel, v_ox, v_fuel
        )

        J       = self._momentum_flux_ratio(v_ox, v_fuel)
        S_ox, S_fuel = self._swirl_numbers()
        A_face  = math.pi * self.chamber_radius ** 2
        s_mm    = 1000.0 * math.sqrt(A_face / max(N, 1))  # 元件间距 [mm]
        q_face  = self._face_heat_flux(N)

        # 每元件流量
        mdot_ox_per   = self.mdot_ox   / N
        mdot_fuel_per = self.mdot_fuel / N

        # 混合分析
        J_note = ""
        if J < 0.2:
            J_note = "  ⚠ J 偏低（氧化剂射流过强），可考虑降低 ΔP_ox 或增大 d_ox"
        elif J > 1.0:
            J_note = "  ⚠ J 偏高（燃料射流主导），可考虑降低 ΔP_fuel 或减小环缝"
        else:
            J_note = "  ✓ 动量通量比 J 在推荐范围 0.3–0.8 内" if 0.3 <= J <= 0.8 else "  ○ J 在可接受范围"

        S_note = ("✓ 旋流数 > 0.6，形成稳定旋流，增强气-气混合" if min(S_ox, S_fuel) >= 0.6
                  else "○ 旋流数偏低，混合以射流剪切为主")

        notes = [
            f"气-气同轴旋流喷注（FFSC：两股热气，非液滴雾化）",
            f"喷注元件数 N = {N}，单元间距 s = {s_mm:.1f} mm",
            f"富氧热气中心孔径 d_ox = {d_ox_mm:.2f} mm，喷射速度 v_ox = {v_ox:.1f} m/s",
            f"富燃热气环缝宽度 δ = {delta_fuel_mm:.2f} mm，喷射速度 v_fuel = {v_fuel:.1f} m/s",
            f"动量通量比 J = {J:.3f}；{J_note}",
            f"旋流数：S_ox = {S_ox:.2f}，S_fuel = {S_fuel:.2f}；{S_note}",
            f"理论依据：NASA SP-8089 / SP-125，同轴旋流喷注器经验设计方法",
        ]

        result = ComponentResult(
            name="injector",
            outputs={
                # 总体参数
                "face_diameter_m"        : 2.0 * self.chamber_radius,
                "face_area_m2"           : A_face,
                "num_elements"           : float(N),
                "element_spacing_mm"     : s_mm,
                # 压降
                "dp_ox_Pa"               : dp_ox,
                "dp_ox_MPa"              : dp_ox / 1e6,
                "dp_ox_ratio"            : self.dp_ratio_ox,
                "dp_fuel_Pa"             : dp_fuel,
                "dp_fuel_MPa"            : dp_fuel / 1e6,
                "dp_fuel_ratio"          : self.dp_ratio_fuel,
                # 喷射速度
                "v_ox_ms"                : v_ox,
                "v_fuel_ms"              : v_fuel,
                # 元件几何（单个元件）
                "d_ox_mm"                : d_ox_mm,
                "d_post_mm"              : d_post_mm,
                "delta_fuel_mm"          : delta_fuel_mm,
                "d_outer_mm"             : d_outer_mm,
                # 流量（单元件）
                "mdot_ox_per_element_kgs": mdot_ox_per,
                "mdot_fuel_per_element_kgs": mdot_fuel_per,
                # 混合特征
                "momentum_flux_ratio_J"  : J,
                "swirl_number_ox"        : S_ox,
                "swirl_number_fuel"      : S_fuel,
                "cd_ox"                  : self.cd_ox,
                "cd_fuel"                : self.cd_fuel,
                # 热流密度
                "face_heat_flux_MWm2"    : q_face,
            },
            notes=notes,
        )
        return result
