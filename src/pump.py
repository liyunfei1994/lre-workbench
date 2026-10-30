"""
pump.py  -  供给泵模型 V9

V9 修正：入口/出口密度分别计算（修复不可压缩误差 ~2%）
  - fluid_density(fluid, T, p) 模块级函数，供 cycle.py 储箱状态点复用
  - 泵功率/扬程/Ns 用入口密度 rho_in（正确的欧拉泵功公式）
  - 出口 FlowState.density 用 rho_out = fluid_density(fluid, T_out, p_out)
  - ComponentResult.outputs 新增 rho_in_kgm3 / rho_out_kgm3

V8 历史：比转速(Ns)分析 与 Ns-效率曲线
V7 历史：出口温度物理化 dT = dP/(rho*cp*eta)；LOX/CH4 密度含压力修正
"""

from __future__ import annotations
import math
from functools import lru_cache
from core import ComponentResult, FlowState, ValidationError, _check_range
from propellants import normalize_fuel_species, validate_surrogate_state
from fluid_properties import state_tp, state_ph, state_ps

_G      = 9.80665
_CP_LOX = 1700.0
_CP_CH4 = 3500.0
_ALPHA_LOX = 3.5e-3
_ALPHA_CH4 = 1.2e-3

# ── CoolProp HEOS 纯流体密度（声明范围检查；不等于实测或精度认证）──────
# 旧线性拟合在高压侧（泵出口 ~700 bar）系统性偏低 ~4–5%，且非物理地预测
# 密度「下降」（温升项压过弱的压力项）；实际致密相在 700 bar 下密度应「上升」。
# 改用 CoolProp 实算，并按原始 (T, p) 做 lru_cache —— 入口工况在 Newton 迭代里
# 反复以同值调用，缓存后近乎免费；出口随迭代缓变，命中也高。CoolProp 不可用或
# 库缺失时才回退线性拟合；查询越界是失败试探，不能改变物性模型。
try:
    from CoolProp.CoolProp import PropsSI as _PROPSSI, PhaseSI as _PHASESI
    _COOLPROP_OK = True
except Exception:
    _COOLPROP_OK = False

_COOLPROP_NAME = {"LOX": "Oxygen", "CH4": "Methane", "c12h26": "n-Dodecane"}


def _fluid_key(fluid: str) -> str:
    return "LOX" if fluid in ("LOX", "O2", "Oxygen") else normalize_fuel_species(fluid)


@lru_cache(maxsize=8192)
def _coolprop_density(fluid_name: str, temperature: float, pressure: float) -> float:
    """Exact-input cache: hydraulic residuals must not inherit rounding steps."""
    return state_tp(fluid_name, temperature, pressure)["rho_kgm3"]


@lru_cache(maxsize=8192)
def _coolprop_pump_outlet(fluid_name: str, Tin: float, pin: float,
                          pout: float, eta: float):
    """
    泵出口状态（CoolProp 严格焓法），按原始浮点输入缓存。
    不量化输入，以免引入阶梯状焓和破坏有限差分 Jacobian。
    返回 (T_out_actual_K, T_out_isentropic_K, dh_s_Jkg)。

      h_in, s_in (入口) → 等熵到 p_out 得 h_s/T_s → 实际焓升 = (h_s−h_in)/η
      → h_out = h_in + 实际焓升 → 由 (h_out, p_out) 反查 T_out。
    这比固定 cp/α 的一阶模型准确（甲烷高压压缩升温被一阶模型低估 ~7 K）。
    """
    inlet = state_tp(fluid_name, Tin, pin, require_liquid=True)
    isentropic = state_ps(fluid_name, pout, inlet["s_JkgK"])
    dh_s = isentropic["h_Jkg"] - inlet["h_Jkg"]
    if not math.isfinite(dh_s) or dh_s <= 0:
        raise ValueError("液体泵等熵升压必须对应正焓升。")
    outlet = state_ph(fluid_name, pout, inlet["h_Jkg"] + dh_s / eta)
    return outlet["T_K"], isentropic["T_K"], dh_s


def _density_linear_fallback(fluid: str, T: float, p: float) -> float:
    """线性拟合兜底（CoolProp 不可用 / 越界时）。"""
    if fluid == "LOX":
        rho = 1237.8 - 4.8 * (T - 70.0) + 5.0e-10 * 1237.8 * (p - 1e5)
        return max(900.0, rho)
    if fluid != "CH4":
        raise ValueError("煤油替代模型不提供甲烷密度回退。")
    base = 439.5 - 2.6 * (T - 100.0)
    pc   = 1.0 + 0.015 * (p - 1e5) / 1.0e7
    return max(300.0, base * pc)


def fluid_density(fluid: str, T: float, p: float) -> float:
    """
    液态推进剂密度  ρ = f(T, p)  [kg/m³]，优先 CoolProp 真实流体物性。

    入口侧（致密化 LOX@70K / LCH4@100K，~9.5 bar）与出口侧（~700 bar 稠密相）
    均采用 HEOS 状态方程并检查声明温压范围；不把计算值称为实测物性。

    参数
    ------
    fluid : 'LOX' 或 'CH4'
    T     : 温度 [K]
    p     : 压力 [Pa]
    """
    fluid = _fluid_key(fluid)
    if fluid == "c12h26":
        validate_surrogate_state(T, p)
        if not _COOLPROP_OK:
            raise RuntimeError("煤油替代模型需要 CoolProp；不提供甲烷密度回退。")
    if _COOLPROP_OK and fluid in _COOLPROP_NAME:
        try:
            return _coolprop_density(_COOLPROP_NAME[fluid], T, p)
        except Exception as exc:
            raise ValueError(f"{fluid} 密度查询失败：T={T:g} K，p={p:g} Pa；不切换物性模型。") from exc
    raise RuntimeError("纯推进剂密度需要 CoolProp HEOS；不使用线性拟合回退。")

# Ns-效率曲线插值节点 (Huzel&Huang NASA SP-125; 对应论文 Figure 4)
# Ns 单位: rad*m^(3/4)/s^2
_NS_ETA_TABLE = [
    (0.20, 0.30), (0.30, 0.38), (0.50, 0.48), (0.80, 0.58),
    (1.00, 0.64), (1.50, 0.72), (2.00, 0.78), (2.50, 0.82),
    (3.00, 0.850), (3.50, 0.865), (4.00, 0.870), (4.50, 0.870),
    (5.00, 0.865), (5.50, 0.855), (6.00, 0.840), (7.00, 0.800),
    (8.00, 0.748), (10.0, 0.650), (12.0, 0.550), (15.0, 0.420),
]


def _eta_from_ns(ns):
    if ns <= _NS_ETA_TABLE[0][0]:
        return _NS_ETA_TABLE[0][1]
    if ns >= _NS_ETA_TABLE[-1][0]:
        return _NS_ETA_TABLE[-1][1]
    for i in range(len(_NS_ETA_TABLE) - 1):
        ns0, eta0 = _NS_ETA_TABLE[i]
        ns1, eta1 = _NS_ETA_TABLE[i + 1]
        if ns0 <= ns <= ns1:
            t = (ns - ns0) / (ns1 - ns0)
            return eta0 + t * (eta1 - eta0)
    return 0.80


class Pump:
    # ────────────────────────────────────────────────────────
    # V10 设计原则（替代旧 _DEFAULT_RPM magic number）：
    #   1. nr_rpm **建议**作为必传参数，从 cycle.py 经 InputParameters
    #      的 lox_pump_speed_rpm_override / fuel_pump_speed_rpm_override 传入。
    #   2. 同一根轴上（FFSC 双轴）的泵和涡轮必须共用一个转速；
    #      该一致性由 cycle.py 负责，不在 Pump 类内做隐式默认。
    #   3. 为向后兼容，nr_rpm=None 时仍走旧默认值（25000/30000 rpm，
    #      对标 Raptor 公开估计），并发 warning 提示。
    # ────────────────────────────────────────────────────────
    _DEFAULT_RPM    = {"LOX": 25000.0, "CH4": 30000.0, "c12h26": 20000.0}
    _DEFAULT_STAGES = {"LOX": 1,       "CH4": 2, "c12h26": 1}

    def __init__(self, name, fluid, mdot, p_in, p_out, t_in, eta,
                 nr_rpm=None, n_stages=None,
                 tip_speed_limit_ms: float = 550.0,
                 npsh_available_pa: float | None = None,
                 impeller_material: str | None = None,
                 side: str | None = None,
                 head_coefficient_psi: float = 0.50):
        """
        Parameters
        ──────────
        nr_rpm              : 轴转速 [rpm]；None 时走 _DEFAULT_RPM（不推荐，会 warn）
        n_stages            : 级数；None 时走 _DEFAULT_STAGES
        head_coefficient_psi : 几何初选 ψ = g H级/u2²（CFturbo Ψ 为此值的两倍）
        tip_speed_limit_ms  : 叶尖速度 u_tip 上限 [m/s]，超出仅 warn
        npsh_available_pa   : 可用净正吸入压头 [Pa]（= p_in - p_vapor）；None 时跳过 Nss 校核
        """
        import warnings as _w
        _check_range(name + ".eta",  eta,  lo=0, hi=1.0, lo_inclusive=False)
        _check_range(name + ".mdot", mdot, lo=0, lo_inclusive=False)
        _check_range(name + ".t_in", t_in, lo=0, lo_inclusive=False)
        _check_range(name + ".p_in", p_in, lo=0, lo_inclusive=False)
        if p_out <= p_in:
            raise ValidationError(
                name + ": p_out=" + str(p_out) + " Pa <= p_in=" + str(p_in)
                + " Pa，泵出口压力必须大于入口压力")
        self.name     = name
        self.fluid    = _fluid_key(fluid)
        self.mdot     = mdot
        self.p_in     = p_in
        self.p_out    = p_out
        self.t_in     = t_in
        self.eta      = eta
        fk = self.fluid
        if nr_rpm is None:
            _w.warn(
                f"{name}: nr_rpm 未指定，使用默认 {self._DEFAULT_RPM[fk]} rpm。"
                f"建议从 InputParameters.{'lox' if fk=='LOX' else 'fuel'}_pump_speed_rpm_override "
                f"传入，以保证泵/涡轮同轴一致。",
                stacklevel=2,
            )
            self.nr_rpm = self._DEFAULT_RPM[fk]
        else:
            self.nr_rpm = float(nr_rpm)
        stage_value = n_stages if n_stages is not None else self._DEFAULT_STAGES[fk]
        if (isinstance(stage_value, bool) or not isinstance(stage_value, (int, float))
                or not math.isfinite(stage_value) or stage_value < 1
                or int(stage_value) != stage_value):
            raise ValidationError(name + ".n_stages 必须为正整数。")
        self.n_stages = int(stage_value)
        if not math.isfinite(self.nr_rpm) or self.nr_rpm <= 0:
            raise ValidationError(name + ".nr_rpm 必须为有限正数。")
        if (isinstance(head_coefficient_psi, bool)
                or not isinstance(head_coefficient_psi, (int, float))
                or not math.isfinite(head_coefficient_psi) or head_coefficient_psi <= 0):
            raise ValidationError(name + ".head_coefficient_psi 必须为有限正数。")
        self.head_coefficient_psi = float(head_coefficient_psi)
        self.tip_speed_limit_ms = float(tip_speed_limit_ms)
        self.npsh_available_pa  = npsh_available_pa
        self.impeller_material  = impeller_material
        self.side               = side or ("ox" if fluid == "LOX" else "fuel")

    def _density(self, p=None, T=None):
        """使用模块级 fluid_density；默认用入口条件 (p_in, t_in)"""
        return fluid_density(
            self.fluid,
            T if T is not None else self.t_in,
            p if p is not None else self.p_in,
        )

    def _cp(self):
        if self.fluid == "c12h26":
            validate_surrogate_state(self.t_in, self.p_in)
            if not _COOLPROP_OK:
                raise RuntimeError("正十二烷泵需要 CoolProp。")
            return float(_PROPSSI("C", "T", self.t_in, "P", self.p_in, "n-Dodecane"))
        return _CP_LOX if self.fluid == "LOX" else _CP_CH4

    def _thermal_expansion(self):
        """Approximate liquid volumetric expansion coefficient [1/K]."""
        if self.fluid == "c12h26":
            raise ValueError("正十二烷泵不使用甲烷固定热膨胀系数回退。")
        return _ALPHA_LOX if self.fluid == "LOX" else _ALPHA_CH4

    def _specific_speed(self, rho):
        nr_rad = self.nr_rpm * 2.0 * math.pi / 60.0
        q    = self.mdot / rho
        dp   = self.p_out - self.p_in
        hp   = dp / (rho * _G)
        hp1  = hp / self.n_stages
        # PT-02 统一：对外一律报欧洲比转速 nq = N[rpm]·√Q[m³/s] / H[m]^0.75，
        # 与 CFTurbo 的 nq、geometry/pumps.py 的 Ns_actual、core.py 的 Ns 目标
        # 同一约定（之前 pump 报 rad 制 ~3.15、geometry 报 ~30，两套并存易读错）。
        nq   = self.nr_rpm * math.sqrt(q) / (hp1 ** 0.75)
        # Huzel&Huang 效率曲线 (_NS_ETA_TABLE) 以 rad 制 ω·√Q/H^0.75 标定，
        # 故内部换算到 rad 制后查表（ns_rad = nq · 2π/60），曲线本身不动。
        ns_rad = nr_rad * math.sqrt(q) / (hp1 ** 0.75)
        eta_ns = _eta_from_ns(ns_rad)
        # 仅描述当前经验曲线的参考段，不认证通用最优泵型或效率。
        if 28.0 <= nq <= 48.0:
            diag = ("nq=%.1f (EU, rpm·√(m³/s)/m^0.75)，位于当前经验曲线参考段[28,48]；"
                    "曲线估算 eta_Ns=%.3f，尚未验证实际泵型和效率") % (nq, eta_ns)
        elif nq < 28.0:
            diag = ("nq=%.1f，低于当前经验曲线参考段[28,48]；"
                    "曲线估算 eta_Ns=%.3f。固定总扬程和流量时，提高转速或增加级数会升高 nq；是否适合需另行设计") % (nq, eta_ns)
        else:
            diag = ("nq=%.1f，高于当前经验曲线参考段[28,48]；"
                    "曲线估算 eta_Ns=%.3f。固定总扬程和流量时，降低转速或减少级数会降低 nq；是否需要混流型需另行设计") % (nq, eta_ns)
        return nq, eta_ns, diag

    def evaluate(self):
        if not _COOLPROP_OK:
            raise RuntimeError("液体泵需要 CoolProp HEOS 状态与相态判断。")
        inlet_properties = state_tp(self.fluid, self.t_in, self.p_in, require_liquid=True)
        # ── 入口密度（用于泵功、扬程、Ns 计算——欧拉泵功公式基于入口比容）
        rho_in  = self._density(p=self.p_in,  T=self.t_in)
        cp      = inlet_properties["cp_JkgK"]
        dp      = self.p_out - self.p_in

        # 泵轴功率（不可压水力口径，作回退/参考）：W = ṁ·ΔP / (ρ_in·η)
        power_hydraulic = self.mdot * dp / (rho_in * self.eta)

        # ── 泵出口温升 ───────────────────────────────────────────────
        # 优先用 CoolProp 严格焓法（绝热泵：h_out=h_in+(h_s−h_in)/η，再反查 T_out）；
        # 它正确捕捉真实流体的压缩升温（甲烷高压一阶模型低估约 7 K）。
        # CoolProp 不可用 / 越界时回退到固定 cp/α 的一阶模型。
        delta_t = delta_t_isentropic = delta_t_loss = None
        dh_s_real = None   # CoolProp 真实等熵焓升 [J/kg]，成功取得后用于轴功率
        fk = _COOLPROP_NAME[self.fluid]
        if self.fluid == "c12h26":
            validate_surrogate_state(self.t_in, self.p_out)
        if _COOLPROP_OK:
            try:
                T_out_rig, T_s, dh_s_real = _coolprop_pump_outlet(
                    fk, self.t_in, self.p_in, self.p_out, self.eta,
                )
                if self.fluid == "c12h26":
                    validate_surrogate_state(T_out_rig, self.p_out)
                delta_t = T_out_rig - self.t_in
                delta_t_isentropic = T_s - self.t_in
                delta_t_loss = delta_t - delta_t_isentropic
            except Exception as exc:
                raise ValueError(f"{self.fluid} 泵等熵焓升/出口温度求解失败；不切换物性模型。") from exc
        if delta_t is None:
            if self.fluid == "c12h26":
                raise RuntimeError("正十二烷泵需要 CoolProp 严格焓法。")
            # 回退：固定 cp/α 一阶模型
            delta_t_isentropic = self._thermal_expansion() * self.t_in * dp / (rho_in * cp)
            delta_t_loss = dp / (rho_in * cp) * (1.0 / self.eta - 1.0)
            delta_t = max(0.0, delta_t_isentropic + delta_t_loss)
        t_out   = self.t_in + delta_t

        # ── 泵轴功率：与出口温升统一功口径 ─────────────────────────────
        # CoolProp 可用时 P = ṁ·Δh_s/η（真实等熵焓升 ∫v·dp）。不可压式 ΔP/ρ_in
        # 沿 70 MPa 级压升路径密度上升、比容减小，比 ∫v·dp 偏大 ~2%(LOX)/~4%(CH4)，
        # 该偏差会直接坐进涡轮-泵功率平衡残差；统一口径后轴功率、出口温升、
        # 下游焓流三者自洽（涡轮所需功率随之下降 2–4%）。
        if dh_s_real is not None and dh_s_real > 0.0:
            power = self.mdot * dh_s_real / self.eta
        else:
            power = power_hydraulic

        # ── 出口密度（用于出口 FlowState——反映出口实际压力下的密度）
        rho_out = self._density(p=self.p_out, T=t_out)

        ns, eta_ns, ns_diag = self._specific_speed(rho_in)
        eta_diff = self.eta - eta_ns

        q_m3s  = self.mdot / rho_in          # 体积流量基于入口密度
        hp_tot = dp / (rho_in * _G)
        hp1    = hp_tot / self.n_stages
        nr_rad = self.nr_rpm * 2.0 * math.pi / 60.0
        h_in = cp * self.t_in
        h_out = h_in + power / self.mdot
        from thermo import CANTERA_AVAILABLE, reactant_enthalpy, enthalpy_reference_id
        energy_basis_available = bool(CANTERA_AVAILABLE and dh_s_real is not None)
        if energy_basis_available:
            mechanism_fuel = "c12h26" if self.fluid == "c12h26" else "CH4"
            h_in = reactant_enthalpy(self.fluid, self.t_in, self.p_in, mechanism_fuel)
            h_out = reactant_enthalpy(self.fluid, t_out, self.p_out, mechanism_fuel)

        # ── V10 新增：叶尖速度估算 + 校核 ─────────────────
        # 与几何初选共用 ψ = g·H_per_stage / u_tip²；不改变真实流体泵功。
        psi_pump = self.head_coefficient_psi
        u_tip_pump = math.sqrt(_G * hp1 / psi_pump)
        # 由 u_tip 和 ω 反推叶轮直径（仅作信息显示）
        d_impeller_est = 2.0 * u_tip_pump / nr_rad
        tip_speed_ok = u_tip_pump <= self.tip_speed_limit_ms
        tip_note = (
            f"  叶尖速度 u_tip≈{u_tip_pump:.0f} m/s（按 ψ={psi_pump} 估算，"
            f"D_imp≈{d_impeller_est*1000:.0f} mm）"
            f"{' ✓' if tip_speed_ok else f' ⚠ 超过上限 {self.tip_speed_limit_ms:.0f} m/s'}"
        )

        # ── 新增：泵叶轮材料 / 离心应力校核（与涡轮同口径）─────────
        # 叶轮轮缘离心应力 σ≈ρ_材料·u_tip²（保守上界），对比材料 σy(T_入口) → SF。
        # 富氧侧（液氧泵）硬校验 oxygen_compatible（禁 Ti）。
        mat_outputs: dict[str, float] = {}
        mat_notes: list[str] = []
        if self.impeller_material is not None:
            try:
                from materials import material_audit_row  # noqa: WPS433
                audit = material_audit_row(
                    material_key=self.impeller_material,
                    T_K=self.t_in,
                    U_ms=u_tip_pump,
                    side=(self.side or "fuel"),
                )
                mat_outputs.update({
                    "impeller_sigma_yield_T_MPa": audit["sigma_yield_T_MPa"],
                    "impeller_sigma_centrif_MPa": audit["sigma_centrifugal_MPa"],
                    "impeller_safety_factor":     audit["safety_factor"],
                    "impeller_oxygen_compatible": 1.0 if audit["oxygen_compatible"] else 0.0,
                })
                mat_notes.append("── 叶轮材料校核 ─────────────────────────")
                mat_notes.append(
                    f"  叶轮材料：{audit['material_name']}（氧兼容={'是' if audit['oxygen_compatible'] else '否'}）"
                )
                mat_notes.append(
                    f"  σy(T_in={self.t_in:.0f}K)={audit['sigma_yield_T_MPa']:.0f} MPa； "
                    f"σ_离心(ρ·u_tip²)={audit['sigma_centrifugal_MPa']:.0f} MPa； "
                    f"SF={audit['safety_factor']:.2f}"
                )
                for iss in audit["issues"]:
                    mat_notes.append(f"  {iss}")
            except (ImportError, KeyError, ValueError) as ex:
                mat_notes.append(f"  ⚠ 叶轮材料校核跳过：{ex}")

        # ── V10 新增：吸入比转速 Nss 校核（穴蚀） ────────
        # PT-01 修复：30000/45000 是【美制惯用】阈值 —— Nss=N[rpm]·√Q[gpm]/NPSH[ft]^0.75
        # （火箭诱导轮范围）。故 Nss 必须用同一套美制单位算后再比，旧代码用 SI
        # (m³/s, m) 算出 ~1046，永远 <30000 → 误报"无需诱导轮"（真实 ~54000）。
        nss = None
        nss_eu = None
        sigma_thoma = None
        npsh_m = (self.npsh_available_pa / (rho_in * _G)
                  if self.npsh_available_pa is not None else None)
        nss_note = ""
        if self.npsh_available_pa is not None and self.npsh_available_pa > 0:
            npsh_m  = self.npsh_available_pa / (rho_in * _G)
            # Thoma 空化数 σ = NPSHa / H（无量纲）：< σ_临界 即发生空化。
            # 与 CFTurbo 空化分析口径一致（NPSHa 越大、H 越小 → σ 越大越安全）。
            sigma_thoma = npsh_m / max(hp_tot, 1e-9)
            q_gpm   = q_m3s * 15850.3           # m³/s → US gpm
            npsh_ft = npsh_m * 3.280839895      # m → ft
            nss = self.nr_rpm * math.sqrt(q_gpm) / npsh_ft ** 0.75
            # 同步给出欧洲制 nss(EU)=N[rpm]·√Q[m³/s]/NPSH[m]^0.75，与 CFTurbo
            # "Suction specific speed → nss (EU)" 字段同口径（换算系数 Nss_US≈51.6·nss_EU）。
            # CFTurbo 常规范围 150–400（最高 1200）；火箭诱导轮泵远高于此。
            nss_eu = self.nr_rpm * math.sqrt(q_m3s) / npsh_m ** 0.75
            nss_ok = nss <= 45000.0
            nss_note = (
                f"  NPSHa={npsh_m:.1f} m → nss(EU)={nss_eu:.0f} [CFTurbo口径] / Nss(US)={nss:.0f} (rpm·√gpm/ft^0.75)"
                f"{' ✓ (<30000，无诱导轮可行)' if nss <= 30000.0 else (' ⚠ 30000–45000，需诱导轮' if nss <= 45000.0 else ' ⚠⚠ >45000，穴蚀风险高，须强诱导轮+过冷')}"
            )

        state = FlowState(
            name        = self.name + "_out",
            pressure    = self.p_out,
            temperature = t_out,
            mass_flow   = self.mdot,
            density     = rho_out,            # ← 出口实际密度（含压缩率修正）
            enthalpy    = h_out,
            composition = {"O2" if self.fluid == "LOX" else self.fluid: 1.0},
            metadata = ({"reference_id": enthalpy_reference_id(mechanism_fuel),
                         "fluid_range_status": "within_declared_ranges"}
                        if energy_basis_available else {}),
        )

        note_eta = ("  ⚠ 输入eta=%.3f 与曲线估算eta_Ns=%.3f 偏差%+.3f(>5%%)，建议核查转速/级数"
                    % (self.eta, eta_ns, eta_diff)
                    if abs(eta_diff) > 0.05
                    else ("  ✓ 输入效率与Ns曲线吻合，偏差=%+.3f(<5%%)" % eta_diff))

        notes = [
            "dP=%.2f MPa (%.1f bar)，温升 dT=%.2f K" % (dp/1e6, dp/1e5, delta_t),
            "  dT_isentropic=%.2f K, dT_loss=%.2f K（HEOS 等熵与实际焓升反算）"
                % (delta_t_isentropic, delta_t_loss),
            "ρ_in=%.1f kg/m³ → ρ_out=%.1f kg/m³（压缩率修正 %+.2f%%）"
                % (rho_in, rho_out, (rho_out/rho_in - 1)*100),
            ("P_shaft=%.3f MW @ eta_input=%.3f（CoolProp Δh_s 口径；不可压参考 %.3f MW）"
                % (power/1e6, self.eta, power_hydraulic/1e6)
             if dh_s_real is not None else
             "P_shaft=%.3f MW @ eta_input=%.3f（不可压口径 ΔP/ρ_in）" % (power/1e6, self.eta)),
            "── 比转速(Ns)分析 ──────────────────────────",
            "  转速 %.0f RPM (%.1f rad/s)，%d 级" % (self.nr_rpm, nr_rad, self.n_stages),
            "  Q=%.2f L/s，Hp(总)=%.0f m，Hp/级=%.0f m" % (q_m3s*1000, hp_tot, hp1),
            "  " + ns_diag,
            note_eta,
            "── 结构/汽蚀校核（V10）─────────────────────",
            tip_note,
        ]
        if nss_note:
            notes.append(nss_note)
        notes.append(f"入口相态 {inlet_properties['phase']}；HEOS 声明温压范围内。范围检查不代替汽蚀或物性精度验证。")
        if inlet_properties["pump_domain"] == "dense_supercritical":
            notes.append("超临界入口按 rho≥rho_critical 纳入稠密流体泵模型域；该操作判据不是液相判定或汽蚀能力证明。")
        notes += mat_notes

        result = ComponentResult(
            name = self.name,
            outputs = {
                "rho_in_kgm3"       : rho_in,
                "rho_out_kgm3"      : rho_out,
                "rho_kgm3"          : rho_in,   # 向后兼容别名
                "p_in_MPa"          : self.p_in / 1e6,
                "p_out_MPa"         : self.p_out / 1e6,
                "p_in_bar"          : self.p_in / 1e5,
                "p_out_bar"         : self.p_out / 1e5,
                "delta_p_Pa"        : dp,
                "delta_p_MPa"       : dp / 1e6,
                "delta_p_bar"       : dp / 1e5,
                "power_W"           : power,
                "power_MW"          : power / 1e6,
                "h_in_Jkg"          : h_in,
                "h_out_Jkg"         : h_out,
                "energy_residual_W" : self.mdot * (h_out - h_in) - power,
                "chemical_enthalpy_basis_available": float(energy_basis_available),
                "fluid_range_checked": 1.0,
                "inlet_dense_supercritical": float(inlet_properties["pump_domain"] == "dense_supercritical"),
                # 功口径诊断：CoolProp 真实等熵焓升 vs 不可压水力式（V14 统一为前者）
                "power_hydraulic_W" : power_hydraulic,
                "specific_work_isentropic_Jkg": (dh_s_real if dh_s_real is not None else dp / rho_in),
                "T_in_K"            : self.t_in,
                "T_out_K"           : t_out,
                "delta_T_K"         : delta_t,
                "delta_T_isentropic_K": delta_t_isentropic,
                "delta_T_loss_K"    : delta_t_loss,
                "eta"               : self.eta,
                "head_m"            : hp_tot,
                "head_m_per_stage"  : hp1,
                "volume_flow_m3s"   : q_m3s,
                "volume_flow_Ls"    : q_m3s * 1000.0,
                "volume_flow_m3h"   : q_m3s * 3600.0,   # 对标 CFTurbo Global setup（Q [m³/h]）
                "nr_rpm"            : self.nr_rpm,
                "nr_rad_s"          : nr_rad,
                "n_stages"          : float(self.n_stages),
                "specific_speed_Ns" : ns,
                "eta_from_Ns_curve" : eta_ns,
                "eta_deviation"     : eta_diff,
                # ── V10 新增：结构/汽蚀诊断 ───────────────
                "u_tip_est_ms"            : u_tip_pump,
                "tip_speed_limit_ms"      : self.tip_speed_limit_ms,
                "tip_speed_ok"            : 1.0 if tip_speed_ok else 0.0,
                "d_impeller_est_m"        : d_impeller_est,
                "psi_head_coefficient"    : psi_pump,
                # NPSHa 按惯例(CFTurbo/泵工程)以液柱米 m 报出：npsh_m = (p_in - p_vap)/(ρ_in·g)。
                # 入口 self.npsh_available_pa 是压力裕度[Pa]，此处转成压头[m]，与 σ/Nss/增压泵口径一致。
                "npsh_available_m"        : npsh_m,
                "npsh_applicable": float(npsh_m is not None),
                "suction_specific_speed_Nss": nss,
                "suction_specific_speed_nss_eu": nss_eu,
                "thoma_cavitation_number_sigma": sigma_thoma,
                **mat_outputs,
            },
            notes = notes,
        )
        return state, result
