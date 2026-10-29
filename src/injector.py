"""
injector.py

中心圆孔＋外环缝同轴喷注元件的初步通流尺寸。

当前采用不可压缩 Cd 孔板关系。FFSC 主喷注器由调用方传入两股涡轮
排气的密度；其他循环可传入相应工质密度。密度来源和适用性由调用方
说明，使用燃气密度不等于已经解决可压缩喷注问题。

单元数、内管壁厚、Cd 均为设计假设；环缝使用精确环形面积反解。
未设计切向孔、旋流槽、集流腔、外筒壁厚和轴向结构。历史旋流指标与
面板热流估算只作未标定的经验参考，不代表完成旋流或燃烧设计。
这不是 Raptor 实机喷注器的复现。NASA SP-8089 / SP-125 仅为一般
喷注器设计背景，不应据此宣称本模型的 FFSC 气—气适用性已验证。
"""

from __future__ import annotations

import math
from core import ComponentResult, _check_range, ValidationError

# ────────────────────────────────────────────────────────────────
# 物理常数与经验参数
# ────────────────────────────────────────────────────────────────

# 喷注器入口工质密度 [kg/m³]（FFSC 主喷注器=气-气；以下为 standalone 兜底默认，
# cycle 运行时会传入所选物性模型算得的工质密度）。
# 量级参考：富氧热气 ~150–220、富燃热气 ~80–120 kg/m³ @ 33 MPa、700–800 K。
_RHO_OX_GAS_DEFAULT   = 180.0   # 富氧热气密度兜底 [kg/m³]
_RHO_FUEL_GAS_DEFAULT = 95.0    # 富燃热气密度兜底 [kg/m³]

# 两侧流量系数的历史设计假设；未由具体孔口/旋流槽标定。
_CD_OX_DEFAULT   = 0.70
_CD_FUEL_DEFAULT = 0.65

# 旋流参数（历史假设；没有相应切向孔/槽几何求解）
_K_SWIRL_OX   = 0.45  # 氧化剂旋流系数
_K_SWIRL_FUEL = 0.50  # 燃料旋流系数


class Injector:
    """
    中心圆孔＋外环缝的同轴喷注器初步通流尺寸。

    参数
    ────
    mdot_ox     : 氧化剂总质量流量 [kg/s]
    mdot_fuel   : 燃料总质量流量 [kg/s]
    pc          : 燃烧室压力 [Pa]
    dp_ratio_ox : 氧喷注器压降 / pc [-]
    dp_ratio_fuel : 燃料喷注器压降 / pc [-]
    chamber_radius : 燃烧室内半径 [m]（决定喷注面面积）
    rho_ox      : 氧化剂支路密度 [kg/m³]（默认值仅为燃气占位假设）
    rho_fuel    : 燃料支路密度 [kg/m³]（默认值仅为燃气占位假设）
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
        for key, value in {
            "mdot_ox": mdot_ox, "mdot_fuel": mdot_fuel, "pc": pc,
            "dp_ratio_ox": dp_ratio_ox, "dp_ratio_fuel": dp_ratio_fuel,
            "chamber_radius": chamber_radius, "rho_ox": rho_ox,
            "rho_fuel": rho_fuel, "cd_ox": cd_ox, "cd_fuel": cd_fuel,
        }.items():
            self._require_positive_finite(key, value)
        if mdot_per_element is not None:
            self._require_positive_finite("mdot_per_element", mdot_per_element)
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

    @staticmethod
    def _require_positive_finite(name: str, value: float) -> None:
        """Reject undefined inputs/derived values rather than serialize NaN/inf."""
        try:
            valid = not isinstance(value, bool) and math.isfinite(value) and value > 0
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise ValidationError(f"Injector.{name} 必须为有限正数，实际为 {value!r}")

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

        这是质量流量关系中的平均法向通流速度，已包含 Cd 一次。
        不由此推断切向速度、旋流锥角或气体出口实际速度场。
        """
        v_ox   = self.cd_ox   * math.sqrt(2.0 * dp_ox   / self.rho_ox)
        v_fuel = self.cd_fuel * math.sqrt(2.0 * dp_fuel / self.rho_fuel)
        return v_ox, v_fuel

    def _num_elements(self, v_ox: float, v_fuel: float) -> int:
        """
        估算喷注元件数量。

        历史启发式：目标单元总流量（默认 1 kg/s），再按等效占用
        间距 8–20 mm 调整，至少 20 个。不是实际面板排布或热负荷优化。
        """
        # 喷注面积
        A_face = math.pi * self.chamber_radius ** 2

        if self.mdot_per_element is not None:
            mdot_elem = self.mdot_per_element
        else:
            # 经验：高压发动机（pc > 20 MPa）喷注密度高，
            # 每 cm² 面积流量约 0.8–2.0 kg/s
            # → 每元件占据面积 ~1.2–2.5 cm²
            # → 等效圆直径（元件间距）~12–18 mm
            # 此处由单元流量 1.0 kg/s 估算（含 ox+fuel）
            mdot_total_per_elem = 1.0  # kg/s per element（初始估计）

            # 根据面积和流量双重约束
            mdot_elem = mdot_total_per_elem

        mdot_total = self.mdot_ox + self.mdot_fuel
        self._require_positive_finite("mdot_total", mdot_total)
        self._require_positive_finite("requested_element_count", mdot_total / mdot_elem)
        N = max(20, int(round(mdot_total / mdot_elem)))

        # 同时根据元件间距约束（不能太密或太稀）
        # 元件间距 s = sqrt(A_face / N)，单位 m
        # 合理范围：8–20 mm
        s = math.sqrt(A_face / max(N, 1))
        if s < 0.008:       # 太密，降低 N
            N = int(A_face / (0.008 ** 2))
        elif s > 0.020:     # 太稀，增大 N
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
          A_ox   = mdot_ox / (N · ρ_ox · v_ox)
                 = (π/4) · d_ox²
          → d_ox = sqrt(4·A_ox / π)

          A_fuel = mdot_fuel / (N · ρ_fuel · v_fuel)
                 = π · δ_fuel · (d_post + δ_fuel)
          → δ_fuel = (sqrt(d_post² + 4 A_fuel/π) - d_post) / 2

        v 已包含 Cd；面积不能重复除以 Cd。采用上式的有理化形式避免
        小环缝时的相减消去。不裁剪孔径或缝宽，越界在 evaluate 中报告。

        返回 (d_ox_mm, delta_fuel_mm, d_post_mm, d_outer_mm)
        """
        for key, value in (("num_elements", N), ("dp_ox", dp_ox),
                           ("dp_fuel", dp_fuel), ("v_ox", v_ox), ("v_fuel", v_fuel)):
            self._require_positive_finite(key, value)
        # 通流面积（单元）；v 已含 Cd。
        A_ox_per = self.mdot_ox / N / self.rho_ox / v_ox
        A_fuel_per = self.mdot_fuel / N / self.rho_fuel / v_fuel
        self._require_positive_finite("a_ox_per_element", A_ox_per)
        self._require_positive_finite("a_fuel_per_element", A_fuel_per)
        d_ox = 2.0 * math.sqrt(A_ox_per / math.pi)   # [m]

        # 氧化剂 post 外径（含 post 壁厚约 0.5 mm）
        d_post = d_ox + 2.0 * 5e-4
        root = math.hypot(d_post, 2.0 * math.sqrt(A_fuel_per / math.pi))
        delta_fuel = (2.0 / math.pi) * A_fuel_per / (root + d_post)

        # 外环直径（外筒内径）
        d_outer = d_post + 2.0 * delta_fuel
        if not math.isfinite(d_outer) or d_outer <= d_post:
            raise ValidationError("Injector 环缝尺寸超出浮点数可表示范围，无法验证返回直径的通流面积")

        return d_ox * 1000.0, delta_fuel * 1000.0, d_post * 1000.0, d_outer * 1000.0

    def _momentum_flux_ratio(
        self, v_ox: float, v_fuel: float
    ) -> float:
        """
        动量通量比 J = (ρ_f·v_f²) / (ρ_o·v_o²)

        物理意义（气-气）：
          J < 1 → 富氧气主导（中心气射流穿透外环富燃气）
          J ~ 0.3–0.8 → 历史经验参考区间，未验证本工况的最优性
          J > 1 → 外环支路的法向动量通量更高
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
        仅保留历史假设指标。没有切向孔/槽几何或速度场求解，不能据此
        声称形成旋流、回流区或稳定燃烧。
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
        保留历史未标定量级公式及上下界；不能用作面板热设计边界。
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

        # 独立按实际返回的直径回算面积/流量；不使用反解得到的缝宽代替
        # 外径，这样能够发现浮点表示、单位转换或几何修改造成的不一致。
        d_ox_m, d_post_m, d_outer_m = (
            d_ox_mm / 1000.0, d_post_mm / 1000.0, d_outer_mm / 1000.0
        )
        a_ox_required = mdot_ox_per / self.rho_ox / v_ox
        a_fuel_required = mdot_fuel_per / self.rho_fuel / v_fuel
        a_ox_geometry = math.pi * d_ox_m ** 2 / 4.0
        a_fuel_geometry = math.pi * (d_outer_m - d_post_m) * (d_outer_m + d_post_m) / 4.0
        mdot_ox_geometry = N * self.rho_ox * v_ox * a_ox_geometry
        mdot_fuel_geometry = N * self.rho_fuel * v_fuel * a_fuel_geometry
        error_ox = (mdot_ox_geometry - self.mdot_ox) / self.mdot_ox
        error_fuel = (mdot_fuel_geometry - self.mdot_fuel) / self.mdot_fuel
        closure_passed = all(math.isfinite(e) and abs(e) <= 1e-10 for e in (error_ox, error_fuel))
        if not closure_passed:
            raise ValidationError("Injector 返回几何的质量流量回算不闭合；请检查数值尺度和环缝可表示性")

        range_warnings = []
        if not 1.0 <= d_ox_mm <= 50.0:
            range_warnings.append(
                "中心孔径超出历史参考范围 1–50 mm；保留质量连续性计算值，需复核单元数和几何假设。")
        if not 0.2 <= delta_fuel_mm <= 5.0:
            range_warnings.append(
                "环缝宽度超出历史参考范围 0.2–5 mm；保留质量连续性计算值，需复核制造及通流假设。")
        layout_warnings = []
        if not 8.0 <= s_mm <= 20.0:
            layout_warnings.append("等效单元间距超出历史参考范围 8–20 mm；尚未完成实际面板排布。")
        if d_outer_mm >= s_mm:
            layout_warnings.append("环缝外径达到或超过等效单元间距；需独立校核面板排布和外筒壁厚。")
        envelope_area_fraction = N * math.pi * d_outer_m ** 2 / (4.0 * A_face)
        if envelope_area_fraction > 1.0:
            layout_warnings.append("全部单元外包圆面积超过喷注面面积；当前单元方案不能直接布置。")
        geometry_warnings = range_warnings + layout_warnings

        # 混合分析
        J_note = ""
        if J < 0.2:
            J_note = "  氧化剂侧法向动量通量较高，混合效果尚需验证"
        elif J > 1.0:
            J_note = "  燃料侧法向动量通量较高，混合效果尚需验证"
        else:
            J_note = "  历史参考范围为 0.3–0.8；不作为本工况混合性能合格判据"

        S_note = "历史假设值；未求解切向孔/槽，不能用于判断旋流或燃烧稳定性"

        notes = [
            "中心圆孔＋外环缝同轴喷注元件初步通流尺寸；工质相态由调用方确定。",
            f"喷注元件数 N = {N}（启发式假设），等效占用间距 s = {s_mm:.1f} mm，未完成实际排布。",
            f"氧化剂支路中心孔径 d_ox = {d_ox_mm:.2f} mm，平均法向通流速度 v_ox = {v_ox:.1f} m/s",
            f"燃料支路环缝宽度 δ = {delta_fuel_mm:.2f} mm，平均法向通流速度 v_fuel = {v_fuel:.1f} m/s",
            "不可压缩 Cd 孔板关系；速度已含 Cd，孔口面积不重复计入 Cd。燃气可压缩性尚未建模。",
            "内管单侧壁厚 0.5 mm 和 Cd 为设计假设；外筒壁厚、轴向长度及旋流孔槽尚未设计。",
            f"返回几何的质量流量相对回算误差：氧化剂 {error_ox:.3g}，燃料 {error_fuel:.3g}。",
            f"动量通量比 J = {J:.3f}；{J_note}",
            f"旋流数：S_ox = {S_ox:.2f}，S_fuel = {S_fuel:.2f}；{S_note}",
            "NASA SP-8089 / SP-125 为一般设计背景，不能代替本工况的气—气模型适用性验证。",
            *geometry_warnings,
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
                "post_wall_thickness_mm" : 0.5,
                "a_ox_required_per_element_m2": a_ox_required,
                "a_fuel_required_per_element_m2": a_fuel_required,
                "a_ox_geometry_per_element_m2": a_ox_geometry,
                "a_fuel_geometry_per_element_m2": a_fuel_geometry,
                "mdot_ox_geometry_kgs"   : mdot_ox_geometry,
                "mdot_fuel_geometry_kgs" : mdot_fuel_geometry,
                "mass_flow_relative_error_ox": error_ox,
                "mass_flow_relative_error_fuel": error_fuel,
                "geometry_closure_passed": float(closure_passed),
                "geometry_range_warning_count": float(len(range_warnings)),
                "geometry_layout_warning_count": float(len(layout_warnings)),
                "element_envelope_area_fraction": envelope_area_fraction,
                "rho_ox_kgm3"            : self.rho_ox,
                "rho_fuel_kgm3"          : self.rho_fuel,
                # 流量（单元件）
                "mdot_ox_kgs"            : self.mdot_ox,
                "mdot_fuel_kgs"          : self.mdot_fuel,
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
            profile={"geometry_contract": {
                "version": 1,
                "type": "coaxial_circular_annular",
                "flow_model": "incompressible_orifice",
                "velocity_definition": "Cd*sqrt(2*dp/rho); mean normal mass-flow velocity",
                "status": "closed_with_warnings" if geometry_warnings else "closed",
                "swirl_geometry_resolved": False,
                "layout_resolved": False,
                "assumptions": {
                    "num_elements": "target element mass flow, 8–20 mm equivalent spacing, minimum 20; heuristic only",
                    "target_mdot_per_element_kgs": self.mdot_per_element if self.mdot_per_element is not None else 1.0,
                    "post_wall_thickness_mm": 0.5,
                    "cd_ox": self.cd_ox,
                    "cd_fuel": self.cd_fuel,
                    "discharge_coefficients": "design assumptions; not calibrated to resolved port geometry",
                    "range_limits": "historical screening ranges, not validated manufacturing or performance limits",
                },
                "warnings": geometry_warnings,
            }},
        )
        if not all(math.isfinite(value) for value in result.outputs.values()):
            raise ValidationError("Injector 计算结果出现非有限值，请检查输入数值尺度")
        return result
