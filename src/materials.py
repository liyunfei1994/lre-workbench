"""
materials.py

涡轮（及涡轮泵）材料数据库 —— V10 新增

设计目的
─────────────────────────────────────────────────────────────
让数字样机第一次具有"材料"这个物理概念。在此之前：
  · 涡轮叶片只算热力学（焓降、功率、效率）；
  · 泵叶轮只算压头、流量、Ns；
  · 没有任何模块知道"材料"为何物。
本模块按 NASA SP-8110 (Liquid Rocket Engine Turbines, 1974) Table II
列出的火箭涡轮材料经验，并补充现代（21 世纪后）增材/锻造工艺常用合金，
按 *部位* 分库。校核层 (mechanical.py 候选) 与 turbine.py 会直接查表。

部位分类（4 类）
─────────────────────────────────────────────────────────────
  rotor_blade   动叶（rotating blade）—— 离心应力 + 高温热燃气
  stator_vane   静叶 / 喷嘴（stator vane / nozzle）—— 高温但无离心应力
  disk          轮盘 / 转子盘（disk / rotor）—— 内应力 + 中等温度
  housing       壳体 / 歧管 / 喷嘴壳（housing / manifold）—— 压力 + 中温

富氧涡轮特别约束（ASTM G124 风险判据）
─────────────────────────────────────────────────────────────
  · 钴 (Co) 在 LOX/GOX 中会自燃。Co > 5% 通常禁用于富氧侧高温区。
  · 富氧侧所有"流道接触"零件（blade, vane, manifold, housing）
    必须 oxygen_compatible == True。
  · 富氧涡轮 T_in 上限取 min(material.T_max_use_K, ~820 K)，
    超过则镍基合金抗氧化层破坏速率指数级上升。

引用
─────────────────────────────────────────────────────────────
  · NASA SP-8110 "Liquid Rocket Engine Turbines" (1974), Table II
  · NASA TM-2007-214410, "Materials for Reusable Liquid Rocket Engines"
  · Inconel 718 / Mar-M247 / Monel: Special Metals & Haynes 公开数据手册
  · ASTM G124 "Standard Test Method for Determining the Combustion
    Behavior of Metallic Materials in Oxygen-Enriched Atmospheres"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


# =========================================
# 数据结构
# =========================================

@dataclass(frozen=True)
class TurbineMaterial:
    """
    涡轮(及泵/燃烧室壁)单一材料的设计参数。

    所有应力 [MPa],密度 [kg/m³],温度 [K]。

    Fields
    ──────
    key                :内部唯一键(用于 InputParameters 字符串引用)
    name               :人读名称(品牌名,例 "Inconel 718")
    parts              :本材料可用于哪些部位
                        - 涡轮泵:rotor_blade / stator_vane / disk / housing
                        - 燃烧室:chamber_inner_wall / chamber_outer_wall
                        - 喷管  :nozzle_inner_wall / nozzle_outer_wall
                        - 通用  :tubing / manifold
    density_kgm3       :密度
    sigma_yield_RT_MPa :室温屈服强度(0.2% offset)
    sigma_yield_hot_MPa:高温屈服强度(在 T_hot_ref_K 下)
    T_hot_ref_K        :上一行屈服强度对应的温度
    T_max_use_K        :长期使用温度上限(连续工作 ≥ 100 小时)
    T_max_short_K      :短时工作温度上限(< 600 s 单次启动)
    cobalt_fraction    :Co 质量分数(关键:>0.05 在富氧侧禁用)
    oxygen_compatible  :是否允许接触高压氧气(ASTM G124 经验判据)
    process            :典型加工方式(forged / cast / AM / wrought)
    notes              :使用注意
    name_cn            :中文学名/俗称(例 "镍基沉淀硬化高温合金");
                        默认空串,UI 显示时若非空则与英文名并列展示。
    thermal_conductivity_WmK : 室温热导率 [W/(m·K)];燃烧室内壁关键指标。
                              默认 0(未知);> 0 时 UI 会显示并提示。

    ── L2 力学物性(热应力 / FEA 必需)──────────────────────────
    youngs_modulus_GPa : 弹性模量 E [GPa]。默认 200(钢/镍基量级)。
    cte_1e6_per_K      : 线膨胀系数 α [1e-6 /K]。默认 13。
                         热应力 σ_th = α·E·ΔT/(1-ν) 的核心系数。
    poisson_ratio      : 泊松比 ν。默认 0.30。
    spec_heat_J_kgK    : 比热容 cp [J/(kg·K)]。默认 460(钢量级);瞬态热分析用。
    """
    key:                  str
    name:                 str
    parts:                List[str]
    density_kgm3:         float
    sigma_yield_RT_MPa:   float
    sigma_yield_hot_MPa:  float
    T_hot_ref_K:          float
    T_max_use_K:          float
    T_max_short_K:        float
    cobalt_fraction:      float
    oxygen_compatible:    bool
    process:              str
    notes:                str
    name_cn:              str = ""
    thermal_conductivity_WmK: float = 0.0
    # ── L2 力学物性(默认值为镍基/钢量级,关键材料逐一精确给出)──
    youngs_modulus_GPa:   float = 200.0
    cte_1e6_per_K:        float = 13.0
    poisson_ratio:        float = 0.30
    spec_heat_J_kgK:      float = 460.0
    # 随温变化物性模型："" = 用上面的常数/分段线性插值（默认）；
    #   "grcop84" = 查 grcop_properties（NASA Ellis 手册回归式）给 k(T)/cp(T)/σy(T)/UTS(T)。
    temp_prop_model:      str = ""

    def thermal_stress(self, delta_T_K: float, constraint: float = 1.0) -> float:
        """
        热应力一阶估算 [MPa]。

        σ_th = constraint · α · E · ΔT / (1 - ν)

        constraint:约束系数。
          1.0  → 完全约束(双端固支,最保守)
          0.5  → 穿壁线性温度梯度的弯曲热应力(薄壁推力室常用)
        ΔT:壁面温差 [K](燃气侧 - 冷却侧,或 T_aw - T_coolant)。
        """
        E_Pa = self.youngs_modulus_GPa * 1.0e9
        alpha = self.cte_1e6_per_K * 1.0e-6
        sigma_Pa = constraint * alpha * E_Pa * delta_T_K / max(1.0 - self.poisson_ratio, 1e-6)
        return sigma_Pa / 1.0e6

    @property
    def display_name(self) -> str:
        """UI 友好的显示名:有中文学名则 '英文(中文)',否则仅英文。"""
        if self.name_cn:
            return f"{self.name} ({self.name_cn})"
        return self.name

    def _grcop_handbook_process(self) -> str:
        """GRCop 手册回归式的工艺态：含 HIP 取 HIPed，否则 extruded（默认、最全、最强）。"""
        return "hip" if "HIP" in self.process.upper() else "extruded"

    def _is_copper_family(self) -> bool:
        """铜合金族判据（k>150 或 key 命中铜系牌号），与热结构模块共用同一口径。"""
        k0 = self.thermal_conductivity_WmK or 25.0
        key = (self.key or "").lower()
        return (k0 > 150.0) or any(s in key for s in ("grcop", "narloy", "copper", "ofhc", "cucrzr", "glidcop"))

    def thermal_conductivity_at(self, T_K: float) -> float:
        """随温导热 k(T) [W/(m·K)] —— 全项目**唯一** k(T) 入口（V19 口径统一）。

        优先级：① grcop84 手册回归式（NASA Ellis）；② 铜合金族通用缩放
        1.08−1.6e-4·T（夹持 0.85–1.08，迁自 thermal_structural._k_of_T，
        300K≈1.03·k₀ / 900K≈0.94·k₀）；③ 其它材料随温变化弱，用室温常数。
        cooling_marching / thermal_structural / fea_chamber / cycle 单点冷却
        全部经由本方法取 k，消除三套 k(T) 并存。
        """
        if self.temp_prop_model == "grcop84":
            import grcop_properties as _gp
            return _gp.thermal_conductivity(T_K)
        k0 = self.thermal_conductivity_WmK or 0.0
        if k0 and self._is_copper_family():
            f = max(0.85, min(1.08, 1.08 - 1.6e-4 * T_K))
            return k0 * f
        return k0

    def youngs_modulus_at(self, T_K: float) -> float:
        """随温弹性模量 E(T) [Pa] —— 唯一 E(T) 入口（迁自 thermal_structural._E_of_T）。

        铜合金随温升明显软化（RT≈0.96·E₀ → 900K≈0.76·E₀）；镍基/钢缓降。
        详细设计应替换为各牌号实测 E(T) 曲线。
        """
        E0 = self.youngs_modulus_GPa * 1e9
        if self._is_copper_family():
            f = max(0.70, min(1.0, 1.06 - 3.3e-4 * T_K))
        else:
            f = max(0.80, min(1.0, 1.03 - 1.2e-4 * T_K))
        return E0 * f

    def spec_heat_at(self, T_K: float) -> float:
        """随温比热 cp(T) [J/(kg·K)]。挂 grcop84 手册模型则查回归式，否则回退常数。"""
        if self.temp_prop_model == "grcop84":
            import grcop_properties as _gp
            return _gp.specific_heat(T_K)
        return self.spec_heat_J_kgK

    def uts_at(self, T_K: float) -> float:
        """随温抗拉强度 UTS(T) [MPa]。挂 grcop84 手册模型则查回归式，否则回退室温估计。"""
        if self.temp_prop_model == "grcop84":
            import grcop_properties as _gp
            return _gp.ultimate_strength(T_K, process=self._grcop_handbook_process())
        return self.sigma_yield_RT_MPa * 1.3  # 粗略：UTS≈1.3×σy（无数据时）

    def sigma_yield_at(self, T_K: float) -> float:
        """
        在任意温度处的屈服强度 [MPa]。

        若挂了随温物性模型（temp_prop_model="grcop84"），优先用 NASA 手册回归式
        σy(T)（grcop_properties.yield_strength），把"材料=常数"升级为"材料=函数"。
        否则回退到原有的"线性插值 + 平台外推"近似：

        T <= 300 K  ：返回 sigma_yield_RT_MPa
        T == T_hot_ref_K：返回 sigma_yield_hot_MPa
        T > T_max_use_K：在 short-time 区按指数衰减，直到 T_max_short_K 处 ~50% hot
        中间区间   ：线性插值
        """
        if self.temp_prop_model == "grcop84":
            import grcop_properties as _gp
            return _gp.yield_strength(T_K, process=self._grcop_handbook_process())
        if T_K <= 300.0:
            return self.sigma_yield_RT_MPa
        if T_K <= self.T_hot_ref_K:
            # 300 K → T_hot_ref_K 线性插值
            f = (T_K - 300.0) / max(self.T_hot_ref_K - 300.0, 1.0)
            return self.sigma_yield_RT_MPa + f * (self.sigma_yield_hot_MPa - self.sigma_yield_RT_MPa)
        if T_K <= self.T_max_use_K:
            # T_hot_ref_K → T_max_use_K 缓降到 80% 热态
            f = (T_K - self.T_hot_ref_K) / max(self.T_max_use_K - self.T_hot_ref_K, 1.0)
            return self.sigma_yield_hot_MPa * (1.0 - 0.20 * f)
        # 超出长期上限：陡降到 short_time 上限处 50% 热态
        f = min(1.0, (T_K - self.T_max_use_K) / max(self.T_max_short_K - self.T_max_use_K, 1.0))
        return self.sigma_yield_hot_MPa * (0.80 - 0.30 * f)


# =========================================
# 材料库
# =========================================
#
# 命名约定：
#   key 全小写下划线，前缀按"工艺/特化"区分，例如：
#     inconel718         通用 718 锻件
#     inconel718_lowco   低钴 718（富氧专用）
#     inconel718_am      增材 718（粗晶/各向异性）
#   parts 字段约束：必须取自 {"rotor_blade","stator_vane","disk","housing"}
#
# 数据来源（强度/温度）取自：
#   · Special Metals "Inconel alloy 718" datasheet (Pub. SMC-045)
#   · NASA TM-2007-214410 §3
#   · Haynes International "Hastelloy X" datasheet
#   · NASA SP-8110 Table II（早期火箭涡轮用例）
# 数据应视为"工程一阶估算"，详细设计需查最新材料证书。

_MATERIALS_LIST: List[TurbineMaterial] = [

    # ──── 镍基高温合金（通用首选）───────────────────────────
    TurbineMaterial(
        key="inconel718",
        name="Inconel 718（锻造）",
        name_cn="镍基沉淀硬化高温合金",
        parts=["rotor_blade", "stator_vane", "disk", "housing"],
        density_kgm3=8190.0,
        sigma_yield_RT_MPa=1170.0,
        sigma_yield_hot_MPa=950.0,
        T_hot_ref_K=922.0,           # 650 ℃
        T_max_use_K=980.0,
        T_max_short_K=1100.0,
        cobalt_fraction=0.01,        # 实测 Co ≤ 1%
        oxygen_compatible=True,
        process="forged",
        thermal_conductivity_WmK=11.4,
        youngs_modulus_GPa=200.0,    # 镍基 ~200 GPa
        cte_1e6_per_K=13.0,          # ~13e-6/K
        poisson_ratio=0.29,
        spec_heat_J_kgK=435.0,
        notes="火箭涡轮通用合金；强度/抗氧化/可焊性平衡，AM 与锻件均可。",
    ),

    TurbineMaterial(
        key="inconel718_lowco",
        name="Inconel 718 低钴版（富氧专用）",
        name_cn="低钴镍基高温合金（富氧专用）",
        parts=["rotor_blade", "stator_vane", "disk", "housing"],
        density_kgm3=8190.0,
        sigma_yield_RT_MPa=1100.0,
        sigma_yield_hot_MPa=900.0,
        T_hot_ref_K=900.0,
        T_max_use_K=920.0,
        T_max_short_K=1050.0,
        cobalt_fraction=0.005,
        oxygen_compatible=True,
        process="forged",
        thermal_conductivity_WmK=11.4,
        notes="富氧涡轮首选；Co 严格 ≤0.5% 以满足 ASTM G124 自燃裕度。",
    ),

    TurbineMaterial(
        key="inconel718_am",
        name="Inconel 718（激光增材 LPBF）",
        name_cn="激光选区熔化镍基高温合金",
        parts=["rotor_blade", "stator_vane", "housing"],
        density_kgm3=8190.0,
        sigma_yield_RT_MPa=1100.0,
        sigma_yield_hot_MPa=860.0,
        T_hot_ref_K=922.0,
        T_max_use_K=950.0,
        T_max_short_K=1080.0,
        cobalt_fraction=0.01,
        oxygen_compatible=True,
        process="AM (LPBF)",
        thermal_conductivity_WmK=11.4,
        notes="Raptor 时代主流：复杂内冷通道一体成型；各向异性需考虑。",
    ),

    TurbineMaterial(
        key="inconel_x750",
        name="Inconel X-750",
        name_cn="镍铬铁沉淀硬化合金",
        parts=["rotor_blade", "stator_vane", "disk", "housing"],
        density_kgm3=8280.0,
        sigma_yield_RT_MPa=970.0,
        sigma_yield_hot_MPa=620.0,
        T_hot_ref_K=922.0,
        T_max_use_K=980.0,
        T_max_short_K=1100.0,
        cobalt_fraction=0.01,
        oxygen_compatible=True,
        process="forged",
        thermal_conductivity_WmK=12.0,
        notes="SP-8110 历史常用；强度低于 718，但抗松弛优，盘/叶/壳通用。",
    ),

    TurbineMaterial(
        key="rene41",
        name="René 41",
        name_cn="镍基铸造高温合金（雷尼 41）",
        parts=["rotor_blade", "disk", "stator_vane"],
        density_kgm3=8240.0,
        sigma_yield_RT_MPa=1100.0,
        sigma_yield_hot_MPa=900.0,
        T_hot_ref_K=1144.0,          # 871 ℃
        T_max_use_K=1170.0,
        T_max_short_K=1280.0,
        cobalt_fraction=0.11,
        oxygen_compatible=False,     # Co ~11%，禁用富氧
        process="forged",
        thermal_conductivity_WmK=10.0,
        notes="耐温更高，适合富燃高温区；含 Co 不可用于富氧。",
    ),

    # ──── 高温铸造合金（动叶专用）─────────────────────────
    TurbineMaterial(
        key="marm247_am",
        name="Mar-M247（增材/定向凝固）",
        name_cn="马尔 M247 镍基定向凝固高温合金",
        parts=["rotor_blade"],
        density_kgm3=8540.0,
        sigma_yield_RT_MPa=1100.0,
        sigma_yield_hot_MPa=1000.0,
        T_hot_ref_K=1144.0,
        T_max_use_K=1150.0,
        T_max_short_K=1270.0,
        cobalt_fraction=0.10,
        oxygen_compatible=False,
        process="AM / DS-cast",
        thermal_conductivity_WmK=10.5,
        notes="高温强度顶级，富燃涡轮动叶可选；含 Co 严禁富氧。",
    ),

    TurbineMaterial(
        key="alloy713c",
        name="Alloy 713C（铸造镍基）",
        name_cn="713C 镍基铸造高温合金",
        parts=["rotor_blade"],
        density_kgm3=7920.0,
        sigma_yield_RT_MPa=850.0,
        sigma_yield_hot_MPa=760.0,
        T_hot_ref_K=1089.0,          # 816 ℃
        T_max_use_K=1100.0,
        T_max_short_K=1200.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="cast",
        thermal_conductivity_WmK=11.0,
        notes="SP-8110 历史用例；无 Co，可用于富氧叶但抗氧化层薄，需慎用。",
    ),

    # ──── 钴基合金（仅限富燃 / 严禁富氧）─────────────────
    TurbineMaterial(
        key="stellite21",
        name="Stellite 21",
        name_cn="司太立 21 钴基铸造合金",
        parts=["rotor_blade", "stator_vane"],
        density_kgm3=8300.0,
        sigma_yield_RT_MPa=690.0,
        sigma_yield_hot_MPa=560.0,
        T_hot_ref_K=1089.0,
        T_max_use_K=1100.0,
        T_max_short_K=1200.0,
        cobalt_fraction=0.62,
        oxygen_compatible=False,
        process="cast",
        thermal_conductivity_WmK=14.8,
        notes="SP-8110 早期常用；Co=62%，富氧严禁；现代多被 718/X-750 取代。",
    ),

    # ──── 富氧专用（铜/镍铜，低强度高氧兼容）──────────────
    TurbineMaterial(
        key="monel400",
        name="Monel 400",
        name_cn="蒙乃尔 400 镍铜合金",
        parts=["housing", "stator_vane"],
        density_kgm3=8800.0,
        sigma_yield_RT_MPa=240.0,
        sigma_yield_hot_MPa=180.0,
        T_hot_ref_K=750.0,
        T_max_use_K=755.0,
        T_max_short_K=810.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="forged",
        thermal_conductivity_WmK=21.8,
        notes="氧兼容性最佳（高 Cu 含量阻燃）；强度低，仅做静子/壳体。",
    ),

    TurbineMaterial(
        key="monelk500",
        name="Monel K-500",
        name_cn="蒙乃尔 K-500 沉淀硬化镍铜合金",
        parts=["disk", "housing", "stator_vane"],
        density_kgm3=8460.0,
        sigma_yield_RT_MPa=690.0,
        sigma_yield_hot_MPa=480.0,
        T_hot_ref_K=755.0,
        T_max_use_K=810.0,
        T_max_short_K=870.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="forged",
        thermal_conductivity_WmK=17.5,
        notes="氧兼容；K-500 比 400 强，适合富氧涡轮盘 / 壳体。",
    ),

    # ──── 壳体 / 歧管 / 喷嘴专用 ─────────────────────────
    TurbineMaterial(
        key="hastelloyx",
        name="Hastelloy X",
        name_cn="哈氏 X 镍铬钼钨耐热合金",
        parts=["housing", "stator_vane", "chamber_outer_wall"],
        density_kgm3=8220.0,
        sigma_yield_RT_MPa=360.0,
        sigma_yield_hot_MPa=280.0,
        T_hot_ref_K=1144.0,
        T_max_use_K=1200.0,
        T_max_short_K=1300.0,
        cobalt_fraction=0.015,
        oxygen_compatible=True,
        process="wrought / AM",
        thermal_conductivity_WmK=12.0,
        notes="高温壳体/歧管；抗氧化优，T_max 高于 718。",
    ),

    TurbineMaterial(
        key="inconel625",
        name="Inconel 625",
        name_cn="镍铬钼铌固溶强化合金",
        parts=["housing", "stator_vane", "chamber_outer_wall"],
        density_kgm3=8440.0,
        sigma_yield_RT_MPa=480.0,
        sigma_yield_hot_MPa=380.0,
        T_hot_ref_K=922.0,
        T_max_use_K=980.0,
        T_max_short_K=1100.0,
        cobalt_fraction=0.01,
        oxygen_compatible=True,
        process="wrought",
        thermal_conductivity_WmK=9.8,
        notes="耐蚀优秀，富氧/富燃壳体都可；强度低于 718，不做转子件。",
    ),

    # ──── 转子盘专用（高强、可锻）────────────────────────
    TurbineMaterial(
        key="alloy16_25_6",
        name="16-25-6 合金钢（盘）",
        name_cn="16-25-6 奥氏体耐热钢",
        parts=["disk"],
        density_kgm3=8000.0,
        sigma_yield_RT_MPa=950.0,
        sigma_yield_hot_MPa=620.0,
        T_hot_ref_K=922.0,
        T_max_use_K=950.0,
        T_max_short_K=1050.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="forged",
        thermal_conductivity_WmK=15.0,
        notes="SP-8110 历史火箭涡轮盘材料；无 Co，可用于富氧盘。",
    ),

    TurbineMaterial(
        key="nasa_hr1",
        name="NASA HR-1（富氧涡轮盘新材料）",
        name_cn="NASA HR-1 富氧高强度合金",
        parts=["disk", "rotor_blade"],
        density_kgm3=8050.0,
        sigma_yield_RT_MPa=1050.0,
        sigma_yield_hot_MPa=820.0,
        T_hot_ref_K=922.0,
        T_max_use_K=980.0,
        T_max_short_K=1100.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="AM / forged",
        thermal_conductivity_WmK=14.0,
        notes="NASA Marshall 专为高压氧环境开发；强度接近 718、零 Co。",
    ),

    # =====================================================
    # ── V11 新增:燃烧室/喷管 *壁体* 材料 ────────────────
    # =====================================================
    # 主燃烧室和喷管的壁体不是涡轮件,但物性参数(密度/强度/热导/温度上限)
    # 共用 TurbineMaterial 结构。新增 parts 类别:
    #   chamber_inner_wall  主燃烧室内壁(燃气侧,高热流)
    #   chamber_outer_wall  主燃烧室外壳(结构层,承外压)
    #   nozzle_inner_wall   喷管内壁(膨胀段燃气侧)
    # 数据源:NASA TM-2014-218360 (GRCop), NASA TM-2010-216640 (NARloy-Z),
    #        NIST SRD 134 (Cu 合金热物性)

    TurbineMaterial(
        key="grcop84",
        name="GRCop-84",
        name_cn="NASA GR-Cop-84 铜铬铌弥散强化合金",
        parts=["chamber_inner_wall", "nozzle_inner_wall"],
        density_kgm3=8620.0,              # 手册 §2.2.1：8.62±0.08 g/cm³（原 8756 偏高）
        sigma_yield_RT_MPa=226.0,         # 手册 σy(300K) extruded（随温曲线见 temp_prop_model）
        sigma_yield_hot_MPa=130.0,        # 手册 σy(773K)=130 extruded（与回归式自洽）
        T_hot_ref_K=773.0,           # 500 ℃
        T_max_use_K=1023.0,          # 750 ℃ 长期(手册目标 700℃"并可能更高")
        T_max_short_K=1123.0,        # 850 ℃ 短时
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="AM (LPBF) / 锻造",
        thermal_conductivity_WmK=285.0,   # 手册 k(300K) 高Fe；室温显示值，随温查 k(T)
        youngs_modulus_GPa=130.0,         # 铜合金 ~130 GPa（手册本版弹性表为空，近似）
        cte_1e6_per_K=17.0,               # 铜合金 ~17e-6/K（手册热膨胀式 OCR 不可靠，近似）
        poisson_ratio=0.33,
        spec_heat_J_kgK=382.0,            # 手册 cp(300K)；随温查 cp(T)
        temp_prop_model="grcop84",        # ★ 挂 NASA 手册回归式：k(T)/cp(T)/σy(T)/UTS(T)
        notes="NASA Glenn 研发的 Cu-8at%Cr-4at%Nb 弥散强化合金。Cr2Nb 颗粒弥散强化,"
              "800℃ 仍保持强度与抗氧化/抗蠕变。Raptor/RS-25E 内壁主选。"
              "★随温物性来自 Ellis《Aerospace Structural Materials Handbook Supplement: "
              "GRCop-84》回归式（k/cp/σy/UTS 随温，extruded 态）。注：导热式基于高Fe"
              "(150–250ppm)批,现代低Fe(120ppm)料 RT 导热更高(~340);严控 Cr:Nb>1.15、"
              "不可铸造、高温钎焊会粗化 Cr2Nb 降强度。",
    ),

    TurbineMaterial(
        key="grcop42",
        name="GRCop-42",
        name_cn="NASA GR-Cop-42 铜铬铌弥散强化合金（增材优化版）",
        parts=["chamber_inner_wall", "nozzle_inner_wall"],
        density_kgm3=8800.0,
        sigma_yield_RT_MPa=210.0,
        sigma_yield_hot_MPa=140.0,
        T_hot_ref_K=773.0,
        T_max_use_K=923.0,           # 650 ℃ 长期(析出相少于84,耐温略低)
        T_max_short_K=1023.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="AM (LPBF)",
        thermal_conductivity_WmK=310.0,   # 低 Cr-Nb(4Cr2Nb)→导热高于84
        youngs_modulus_GPa=130.0,
        cte_1e6_per_K=17.0,
        poisson_ratio=0.33,
        spec_heat_J_kgK=385.0,
        notes="GRCop-84 的低 Cr-Nb 含量改进版（Cu-4Cr-2Nb at%）,"
              "析出相更少→导热更高(~310)但耐温略低;更适合 SLM 增材(粉末流动性好)。"
              "Raptor 2 / Vulcan BE-4 内壁选用。",
    ),

    TurbineMaterial(
        key="cucrzr",
        name="CuCrZr",
        name_cn="铜铬锆时效硬化合金",
        parts=["chamber_inner_wall", "nozzle_inner_wall"],
        density_kgm3=8900.0,
        sigma_yield_RT_MPa=350.0,    # 时效态室温强度较高
        sigma_yield_hot_MPa=150.0,   # >350 ℃ 过时效,强度跌
        T_hot_ref_K=573.0,
        T_max_use_K=623.0,           # 350 ℃ 长期上限(过时效软化)
        T_max_short_K=720.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="锻造 / 时效",
        thermal_conductivity_WmK=330.0,   # 导热最高(析出相少)
        youngs_modulus_GPa=128.0,
        cte_1e6_per_K=17.0,
        poisson_ratio=0.34,
        spec_heat_J_kgK=385.0,
        notes="传统铜合金(Cu-Cr-Zr):导热率最高(~330),但 >350 ℃ 过时效软化、温度上限低。"
              "早期/中低热流发动机内壁;高压发动机喉部温度区不够用——作对比基准。",
    ),

    TurbineMaterial(
        key="glidcop_al15",
        name="Glidcop AL-15",
        name_cn="氧化铝弥散强化铜（ODS）",
        parts=["chamber_inner_wall", "nozzle_inner_wall"],
        density_kgm3=8810.0,
        sigma_yield_RT_MPa=330.0,
        sigma_yield_hot_MPa=220.0,   # ODS 不过时效,高温保强
        T_hot_ref_K=773.0,
        T_max_use_K=1123.0,          # 850 ℃(Al2O3 弥散热稳定)
        T_max_short_K=1223.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="粉末冶金 / 内氧化",
        thermal_conductivity_WmK=365.0,   # 高导热 + 高温稳定
        youngs_modulus_GPa=130.0,
        cte_1e6_per_K=16.6,
        poisson_ratio=0.33,
        spec_heat_J_kgK=380.0,
        notes="Al2O3 纳米弥散强化铜:导热接近纯铜(~365)且高温不过时效,"
              "兼具高导热与高温强度;火箭室壁研究型候选(飞行验证少于 GRCop)。",
    ),

    TurbineMaterial(
        key="narloy_z",
        name="NARloy-Z",
        name_cn="NARloy-Z 铜锆银合金（SSME 经典）",
        parts=["chamber_inner_wall", "nozzle_inner_wall"],
        density_kgm3=8920.0,
        sigma_yield_RT_MPa=124.0,
        sigma_yield_hot_MPa=82.0,
        T_hot_ref_K=773.0,
        T_max_use_K=811.0,           # 538 ℃ 长期上限,比 GRCop 低
        T_max_short_K=977.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="forged / 钎焊",
        thermal_conductivity_WmK=316.0,
        youngs_modulus_GPa=124.0,
        cte_1e6_per_K=18.0,
        poisson_ratio=0.34,
        spec_heat_J_kgK=385.0,
        notes="航天飞机主发动机 SSME 主燃烧室内壁标杆材料(Cu-3Ag-0.5Zr),"
              "热导率极高;高温强度弱于 GRCop,寿命受限。",
    ),

    TurbineMaterial(
        key="inconel718_wall",
        name="Inconel 718（壳体）",
        name_cn="镍基沉淀硬化高温合金（外壳用）",
        parts=["chamber_outer_wall", "nozzle_inner_wall"],
        density_kgm3=8190.0,
        sigma_yield_RT_MPa=1170.0,
        sigma_yield_hot_MPa=950.0,
        T_hot_ref_K=922.0,
        T_max_use_K=980.0,
        T_max_short_K=1100.0,
        cobalt_fraction=0.01,
        oxygen_compatible=True,
        process="forged / AM",
        thermal_conductivity_WmK=11.4,
        youngs_modulus_GPa=200.0,
        cte_1e6_per_K=13.0,
        poisson_ratio=0.29,
        spec_heat_J_kgK=435.0,
        notes="再生冷却推力室的结构外壳:承外压、密封冷却通道。"
              "Raptor / RS-25E / Merlin 1D 通用做法。",
    ),

    # =====================================================
    # ── V12 新增:泵 / 喷注器 / 结构件材料 ────────────────
    # =====================================================
    # 新增 parts 类别:
    #   pump_impeller   泵叶轮(离心应力 + 工质接触;富氧侧必须氧兼容)
    #   pump_inducer    诱导轮(泵入口防汽蚀;同叶轮约束)
    #   pump_housing    泵壳/蜗壳(承压 + 工质接触)
    #   injector_face   喷注面板(高热流面,需高导热或耐热)
    #   injector_body   喷注器体/歧管(承压分配)
    #   valve_body      阀体/管路(承压)
    #   structure       通用结构件/支架/法兰
    #   fastener        紧固件/螺栓
    # ⚠ 安全铁律:钛合金(Ti)在 LOX 中受冲击会自燃 → 富氧侧严禁,
    #            oxygen_compatible=False。

    # ── 泵叶轮:富氧侧(LOX 接触,必须氧兼容、无 Ti)────────────
    TurbineMaterial(
        key="monelk500_impeller",
        name="Monel K-500（液氧泵叶轮）",
        name_cn="蒙乃尔 K-500 沉淀硬化镍铜合金",
        parts=["pump_impeller", "pump_inducer", "pump_housing"],
        density_kgm3=8460.0,
        sigma_yield_RT_MPa=690.0,
        sigma_yield_hot_MPa=600.0,
        T_hot_ref_K=600.0,
        T_max_use_K=755.0,
        T_max_short_K=870.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="forged / cast",
        thermal_conductivity_WmK=17.5,
        youngs_modulus_GPa=180.0,
        cte_1e6_per_K=13.7,
        poisson_ratio=0.32,
        spec_heat_J_kgK=420.0,
        notes="液氧泵叶轮/诱导轮经典材料:高 Cu 阻燃、氧兼容、低温韧性好。"
              "RS-25 / RD-170 LOX 泵叶轮量级。",
    ),

    TurbineMaterial(
        key="inconel718_pump_ox",
        name="Inconel 718（富氧泵叶轮,低钴）",
        name_cn="低钴镍基高温合金(富氧泵)",
        parts=["pump_impeller", "pump_inducer", "pump_housing"],
        density_kgm3=8190.0,
        sigma_yield_RT_MPa=1100.0,
        sigma_yield_hot_MPa=950.0,
        T_hot_ref_K=600.0,
        T_max_use_K=920.0,
        T_max_short_K=1050.0,
        cobalt_fraction=0.005,
        oxygen_compatible=True,
        process="AM / forged",
        thermal_conductivity_WmK=11.4,
        youngs_modulus_GPa=200.0,
        cte_1e6_per_K=13.0,
        poisson_ratio=0.29,
        spec_heat_J_kgK=435.0,
        notes="高扬程液氧泵叶轮:强度远高于 Monel,低钴满足富氧;"
              "现代高压级燃泵(Raptor 量级)首选。",
    ),

    # ── 泵叶轮:富燃/甲烷侧(可用 Ti/Al,非氧接触)─────────────
    TurbineMaterial(
        key="ti6al4v",
        name="Ti-6Al-4V（甲烷泵叶轮/结构）",
        name_cn="TC4 钛合金",
        parts=["pump_impeller", "pump_inducer", "structure"],
        density_kgm3=4430.0,           # 轻!约镍基一半
        sigma_yield_RT_MPa=880.0,
        sigma_yield_hot_MPa=620.0,
        T_hot_ref_K=600.0,
        T_max_use_K=620.0,
        T_max_short_K=700.0,
        cobalt_fraction=0.0,
        oxygen_compatible=False,       # ⚠ 钛在 LOX/GOX 中冲击自燃,富氧严禁!
        process="forged / AM",
        thermal_conductivity_WmK=6.7,
        youngs_modulus_GPa=114.0,
        cte_1e6_per_K=8.6,
        poisson_ratio=0.34,
        spec_heat_J_kgK=560.0,
        notes="高比强度,甲烷泵叶轮/诱导轮/支架理想。"
              "⚠ 严禁接触液氧/高压氧(冲击自燃),仅富燃/燃料侧。",
    ),

    TurbineMaterial(
        key="al7075",
        name="Aluminum 7075-T6（甲烷泵/轻结构）",
        name_cn="7075 高强铝合金",
        parts=["pump_impeller", "pump_housing", "structure"],
        density_kgm3=2810.0,
        sigma_yield_RT_MPa=503.0,
        sigma_yield_hot_MPa=200.0,
        T_hot_ref_K=420.0,
        T_max_use_K=450.0,             # 铝高温弱
        T_max_short_K=500.0,
        cobalt_fraction=0.0,
        oxygen_compatible=False,       # 一般不做氧接触高速件
        process="forged / machined",
        thermal_conductivity_WmK=130.0,
        youngs_modulus_GPa=71.7,
        cte_1e6_per_K=23.6,            # 铝膨胀大
        poisson_ratio=0.33,
        spec_heat_J_kgK=960.0,
        notes="低温甲烷泵叶轮/泵壳/轻结构;低温下强度反升,适合深冷甲烷。"
              "高温段弱,不可近燃气。",
    ),

    # ── 喷注器:面板(高热流)+ 体/歧管 ────────────────────────
    TurbineMaterial(
        key="ofhc_copper_face",
        name="OFHC Copper C10100（喷注面板）",
        name_cn="无氧高导铜",
        parts=["injector_face"],
        density_kgm3=8940.0,
        sigma_yield_RT_MPa=70.0,       # 软,但导热极高
        sigma_yield_hot_MPa=40.0,
        T_hot_ref_K=573.0,
        T_max_use_K=673.0,
        T_max_short_K=800.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="forged / brazed",
        thermal_conductivity_WmK=391.0,   # 纯铜,最高导热
        youngs_modulus_GPa=117.0,
        cte_1e6_per_K=17.0,
        poisson_ratio=0.34,
        spec_heat_J_kgK=385.0,
        notes="喷注面板传统材料:极高导热把面板热量导走防烧蚀;"
              "强度低,靠背板/筋支撑。现代多被 GRCop 取代。",
    ),

    TurbineMaterial(
        key="ss316l",
        name="316L Stainless（喷注器体/管路/泵壳）",
        name_cn="316L 奥氏体不锈钢",
        parts=["injector_body", "valve_body", "structure", "pump_housing"],
        density_kgm3=8000.0,
        sigma_yield_RT_MPa=290.0,
        sigma_yield_hot_MPa=170.0,
        T_hot_ref_K=811.0,
        T_max_use_K=1090.0,
        T_max_short_K=1200.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="wrought / AM",
        thermal_conductivity_WmK=16.3,
        youngs_modulus_GPa=193.0,
        cte_1e6_per_K=16.0,
        poisson_ratio=0.30,
        spec_heat_J_kgK=500.0,
        notes="通用承压/管路/喷注器体;易焊、耐蚀、氧兼容、廉价。"
              "强度中等,不做高速转子。",
    ),

    TurbineMaterial(
        key="inconel718_injector",
        name="Inconel 718（喷注器体/歧管）",
        name_cn="镍基沉淀硬化高温合金(喷注器)",
        parts=["injector_body", "injector_face", "valve_body"],
        density_kgm3=8190.0,
        sigma_yield_RT_MPa=1170.0,
        sigma_yield_hot_MPa=950.0,
        T_hot_ref_K=922.0,
        T_max_use_K=980.0,
        T_max_short_K=1100.0,
        cobalt_fraction=0.01,
        oxygen_compatible=True,
        process="AM (LPBF)",
        thermal_conductivity_WmK=11.4,
        youngs_modulus_GPa=200.0,
        cte_1e6_per_K=13.0,
        poisson_ratio=0.29,
        spec_heat_J_kgK=435.0,
        notes="现代增材一体喷注器(Raptor 量级):同体集成数百喷注单元;"
              "强度高、可承高压分配腔。",
    ),

    # ── 紧固件 / 中温结构 ────────────────────────────────────
    TurbineMaterial(
        key="a286",
        name="A-286（紧固件/中温结构）",
        name_cn="A-286 铁基沉淀硬化高温合金",
        parts=["fastener", "structure", "disk"],
        density_kgm3=7920.0,
        sigma_yield_RT_MPa=590.0,
        sigma_yield_hot_MPa=480.0,
        T_hot_ref_K=922.0,
        T_max_use_K=975.0,
        T_max_short_K=1050.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="forged",
        thermal_conductivity_WmK=14.0,
        youngs_modulus_GPa=201.0,
        cte_1e6_per_K=16.5,
        poisson_ratio=0.31,
        spec_heat_J_kgK=460.0,
        notes="火箭发动机标准紧固件材料:中温强度好、氧兼容、廉价于镍基。"
              "也用作中温支架/法兰。",
    ),

    # =====================================================
    # ── V13 新增:轴承 / 轴 / 密封 / 万向节 / 管路 / 贮箱 ──
    # =====================================================
    # 新增 parts 类别:
    #   shaft     涡轮泵主轴(扭矩 + 弯曲 + 临界转速)
    #   bearing   轴承(接触应力 + 低温 + DN 值)
    #   seal      动密封/迷宫密封
    #   gimbal    万向节轴承(推力传递 + 摆动)
    #   tubing    管路 / 波纹管 / 柔性接头
    #   tank      贮箱 / 主结构壳体

    TurbineMaterial(
        key="si3n4_bearing",
        name="Si₃N₄ 氮化硅陶瓷（混合轴承球）",
        name_cn="氮化硅陶瓷",
        parts=["bearing"],
        density_kgm3=3230.0,           # 陶瓷,极轻
        sigma_yield_RT_MPa=700.0,      # 用抗弯强度近似(脆性材料无屈服)
        sigma_yield_hot_MPa=650.0,
        T_hot_ref_K=1073.0,
        T_max_use_K=1273.0,
        T_max_short_K=1473.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="HIP 烧结",
        thermal_conductivity_WmK=30.0,
        youngs_modulus_GPa=310.0,      # 极硬
        cte_1e6_per_K=3.2,             # 膨胀极小,尺寸稳定
        poisson_ratio=0.27,
        spec_heat_J_kgK=700.0,
        notes="火箭涡轮泵混合陶瓷球轴承滚珠:密度小→离心载荷低,"
              "硬度高、耐磨、低温自润滑差需介质润滑。SSME/Raptor 涡轮泵采用。"
              "脆性材料,sigma_yield 实为抗弯强度近似。",
    ),

    TurbineMaterial(
        key="440c_bearing",
        name="440C 不锈轴承钢（套圈）",
        name_cn="440C 高碳铬马氏体不锈钢",
        parts=["bearing"],
        density_kgm3=7650.0,
        sigma_yield_RT_MPa=1900.0,     # 淬硬态极高
        sigma_yield_hot_MPa=1400.0,
        T_hot_ref_K=600.0,
        T_max_use_K=590.0,
        T_max_short_K=700.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="淬火回火",
        thermal_conductivity_WmK=24.0,
        youngs_modulus_GPa=200.0,
        cte_1e6_per_K=10.1,
        poisson_ratio=0.28,
        spec_heat_J_kgK=460.0,
        notes="低温泵轴承套圈传统材料:高硬度耐磨、耐蚀。"
              "常与 Si₃N₄ 球配成混合陶瓷轴承。",
    ),

    TurbineMaterial(
        key="maraging300_shaft",
        name="Maraging 300（涡轮泵主轴）",
        name_cn="300 马氏体时效钢",
        parts=["shaft", "gimbal", "structure"],
        density_kgm3=8000.0,
        sigma_yield_RT_MPa=1900.0,     # 超高强度
        sigma_yield_hot_MPa=1500.0,
        T_hot_ref_K=700.0,
        T_max_use_K=730.0,
        T_max_short_K=820.0,
        cobalt_fraction=0.09,          # 含 Co ~9%
        oxygen_compatible=False,
        process="时效硬化",
        thermal_conductivity_WmK=25.0,
        youngs_modulus_GPa=190.0,
        cte_1e6_per_K=10.3,
        poisson_ratio=0.31,
        spec_heat_J_kgK=450.0,
        notes="超高强度钢:涡轮泵主轴、万向节轴承座。"
              "高强度+好韧性,但含 Co 不可用于富氧流道。",
    ),

    TurbineMaterial(
        key="ss321_bellows",
        name="321 不锈钢（波纹管/柔性管路）",
        name_cn="321 钛稳定化奥氏体不锈钢",
        parts=["tubing", "seal", "structure"],
        density_kgm3=7900.0,
        sigma_yield_RT_MPa=240.0,
        sigma_yield_hot_MPa=140.0,
        T_hot_ref_K=811.0,
        T_max_use_K=1090.0,
        T_max_short_K=1170.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="wrought / 焊接",
        thermal_conductivity_WmK=16.1,
        youngs_modulus_GPa=193.0,
        cte_1e6_per_K=16.6,
        poisson_ratio=0.30,
        spec_heat_J_kgK=500.0,
        notes="柔性波纹管、万向节柔性接头、推进剂管路:"
              "Ti 稳定化抗晶间腐蚀、可焊、抗疲劳波纹优。",
    ),

    TurbineMaterial(
        key="al2219_tank",
        name="Aluminum 2219-T87（贮箱/主结构）",
        name_cn="2219 铝铜合金",
        parts=["tank", "structure"],
        density_kgm3=2840.0,
        sigma_yield_RT_MPa=393.0,
        sigma_yield_hot_MPa=200.0,
        T_hot_ref_K=420.0,
        T_max_use_K=440.0,
        T_max_short_K=500.0,
        cobalt_fraction=0.0,
        oxygen_compatible=False,
        process="焊接 / machined",
        thermal_conductivity_WmK=120.0,
        youngs_modulus_GPa=73.0,
        cte_1e6_per_K=22.3,
        poisson_ratio=0.33,
        spec_heat_J_kgK=864.0,
        notes="低温推进剂贮箱/箭体主结构传统材料:可焊、低温韧性优、"
              "比强度好。航天飞机外贮箱、猎鹰贮箱采用。",
    ),

    # ── 喷管延伸段 / SpaceX 超合金(部分为公开推测的工程量级) ──────
    # 数据源:SpaceX 公开信息(Musk 推文/发布会)、Wikipedia「SpaceX Raptor」、
    #        Niobium C-103 取自 ASM / RL10 / Apollo SPS 公开手册。
    # 注意:SX300/SX500 的精确成分 SpaceX 未公开,以下为「工程量级占位」,仅供构型/选材示意。
    TurbineMaterial(
        key="sx300",
        name="SX300 (SpaceX superalloy)",
        name_cn="SpaceX SX300 镍基超合金（推测，富氧涡轮/歧管）",
        parts=["housing", "manifold", "nozzle_outer_wall", "stator_vane"],
        density_kgm3=8200.0,
        sigma_yield_RT_MPa=1050.0,
        sigma_yield_hot_MPa=850.0,
        T_hot_ref_K=1000.0,
        T_max_use_K=1100.0,
        T_max_short_K=1250.0,
        cobalt_fraction=0.0,           # 富氧侧必须低 Co
        oxygen_compatible=True,
        process="熔模铸造 / 增材(AM)",
        thermal_conductivity_WmK=12.0,
        youngs_modulus_GPa=205.0,
        cte_1e6_per_K=13.0,
        poisson_ratio=0.30,
        spec_heat_J_kgK=460.0,
        notes="SpaceX 自研、Inconel 改良超合金,用于 Raptor 富氧涡轮/高压歧管。"
              "成分未公开;此处为工程量级占位值。抗氧化、高强,>800 bar 富氧环境。",
    ),

    TurbineMaterial(
        key="sx500",
        name="SX500 (SpaceX superalloy)",
        name_cn="SpaceX SX500 镍基超合金（推测，更高温富氧）",
        parts=["housing", "manifold", "stator_vane", "rotor_blade"],
        density_kgm3=8300.0,
        sigma_yield_RT_MPa=1150.0,
        sigma_yield_hot_MPa=950.0,
        T_hot_ref_K=1050.0,
        T_max_use_K=1200.0,
        T_max_short_K=1300.0,
        cobalt_fraction=0.0,
        oxygen_compatible=True,
        process="熔模铸造 / 增材(AM)",
        thermal_conductivity_WmK=12.0,
        youngs_modulus_GPa=210.0,
        cte_1e6_per_K=13.0,
        poisson_ratio=0.30,
        spec_heat_J_kgK=460.0,
        notes="SX300 的更高温版本,用于 Raptor 富氧侧最热区(预燃室出口/涡轮入口)。"
              "成分未公开;此处为工程量级占位值。",
    ),

    TurbineMaterial(
        key="niobium_c103",
        name="Niobium C-103 (Nb-10Hf-1Ti)",
        name_cn="铌合金 C-103（辐射冷却喷管延伸段）",
        parts=["nozzle_extension", "nozzle_outer_wall"],
        density_kgm3=8850.0,
        sigma_yield_RT_MPa=240.0,       # 强度低,靠高温稳定 + 辐射散热
        sigma_yield_hot_MPa=140.0,
        T_hot_ref_K=1366.0,             # 1093 ℃
        T_max_use_K=1600.0,             # 含 R-512 硅化物涂层后辐射工作温度
        T_max_short_K=1900.0,
        cobalt_fraction=0.0,
        oxygen_compatible=False,        # 裸铌高温剧烈氧化,必须硅化物涂层
        process="成形/焊接 + R-512E 硅化物抗氧化涂层",
        thermal_conductivity_WmK=42.0,
        youngs_modulus_GPa=96.0,
        cte_1e6_per_K=7.5,
        poisson_ratio=0.38,
        spec_heat_J_kgK=270.0,
        notes="经典辐射冷却喷管延伸段材料(Apollo SPS、RL10、Merlin Vacuum)。"
              "高熔点、低膨胀,靠红热辐射散热;裸材抗氧化差,必须硅化物涂层。"
              "Raptor Vacuum 改用再生冷却高镍延伸段,不用铌——此条供对比/可选构型。",
    ),
]

# 索引：key → TurbineMaterial
MATERIALS: Dict[str, TurbineMaterial] = {m.key: m for m in _MATERIALS_LIST}


# =========================================
# 中文工程/学术牌号对照（国标 GH/GB 牌号或常用中文学名）
# =========================================
# 给出国内工程界/学术界对这些进口/国际牌号的标准称呼，便于论文与选材沟通。
# 命名规则：GH=变形高温合金，K/DZ=铸造/定向凝固高温合金，0xCr..=不锈钢国标(GB/T 20878)，
#           NCu=镍铜，TC=钛合金，QCr=铬青铜，TU=无氧铜。「≈/近似」表示成分相近非完全等同。
# 注：GRCop/NARloy/SX 系列为美国/SpaceX 专有，国内无直接对标牌号。
GB_GRADE: Dict[str, str] = {
    "inconel718":          "GH4169",
    "inconel718_lowco":    "GH4169（低钴控制）",
    "inconel718_am":       "GH4169（激光增材）",
    "inconel718_wall":     "GH4169",
    "inconel718_pump_ox":  "GH4169（低钴）",
    "inconel718_injector": "GH4169",
    "inconel625":          "GH3625（≈NS336）",
    "inconel_x750":        "GH4145",
    "rene41":              "GH4141（近似）",
    "hastelloyx":          "GH3536（旧称 GH536）",
    "marm247_am":          "定向凝固镍基（≈DZ417G / DZ125）",
    "alloy713c":           "K418 铸造镍基（近似）",
    "stellite21":          "司太立 21 钴基铸造合金",
    "monel400":            "NCu28-2.5-1.5（蒙乃尔 400）",
    "monelk500":           "NCu30（蒙乃尔 K-500）",
    "monelk500_impeller":  "NCu30（蒙乃尔 K-500）",
    "alloy16_25_6":        "16-25-6 奥氏体耐热钢",
    "nasa_hr1":            "NASA 专有（无国标对标）",
    "a286":                "GH2132",
    "ti6al4v":             "TC4（Ti-6Al-4V）",
    "al7075":              "7A09（≈7075）",
    "al2219_tank":         "2219 / 2A14 系铝铜合金",
    "ss316l":              "022Cr17Ni12Mo2（≈316L）",
    "ss321_bellows":       "06Cr18Ni11Ti（≈321）",
    "maraging300_shaft":   "00Ni18Co8Mo5TiAl（18Ni-300 马氏体时效钢）",
    "440c_bearing":        "9Cr18Mo（≈440C 轴承钢）",
    "si3n4_bearing":       "氮化硅陶瓷 Si₃N₄",
    "cucrzr":              "QCr0.5（铬锆铜）",
    "grcop84":             "美国 NASA 专有（无国标对标）",
    "grcop42":             "美国 NASA 专有（无国标对标）",
    "narloy_z":            "美国 NASA 专有（无国标对标）",
    "glidcop_al15":        "Al₂O₃ 弥散强化铜（进口 Glidcop）",
    "ofhc_copper_face":    "TU1 无氧铜（≈C10100）",
    "niobium_c103":        "铌合金 Nb-10Hf-1Ti（国内对标如 Nb521）",
    "sx300":               "SpaceX 专有（成分未公开）",
    "sx500":               "SpaceX 专有（成分未公开）",
}


def gb_grade(key: str) -> str:
    """返回材料的中文工程/学术牌号（国标 GH/GB 或常用学名）；无对应返回空串。"""
    return GB_GRADE.get(key, "")


def material_evidence(key: str) -> dict:
    """Expose data provenance separately from the existing screening constants.

    No stored oxygen-compatibility flag in this catalogue is a test certificate.
    In particular, proprietary SX compositions and properties are placeholders.
    """
    material = get_material(key)
    placeholder = key in {"sx300", "sx500"}
    return {
        "key": key,
        "status": "placeholder" if placeholder else "engineering_reference",
        "status_label": "工程估算 / 占位（未经验证）" if placeholder else "工程参考 / 简化物性",
        "estimated": True,
        "source": ("材料成分及可追溯实测物性未公开；本库仅填入示意量级。"
                   if placeholder else
                   "本地材料库汇编；模块引用 NASA SP-8110、NASA TM-2007-214410 与厂家资料，"
                   "各牌号数值和温度插值为简化工程数据，尚无逐项来源与批次证书。"),
        "oxygen_compatibility_status": ("unknown" if placeholder else
                                        "screening_allowed" if material.oxygen_compatible else "screening_excluded"),
        "oxygen_test_validated": False,
        "oxygen_label": ("富氧相容性未知（未验证）" if placeholder else
                          "经验判据允许（非试验认证）" if material.oxygen_compatible else "经验判据不允许"),
        "note": ("SX300 / SX500 的密度、强度、温度上限、Co 含量及富氧相容性均不能作为实测结论。"
                 if placeholder else
                 "选材及安全裕度需要匹配温度、热处理、工艺、载荷和服役环境的可追溯数据。"),
    }


# =========================================
# 查询与校核辅助
# =========================================

def get_material(key: str) -> TurbineMaterial:
    """按 key 取材料；未知 key 抛 KeyError 并给出可用列表。"""
    if key not in MATERIALS:
        raise KeyError(
            f"未知材料 key='{key}'。可用列表：{sorted(MATERIALS.keys())}"
        )
    return MATERIALS[key]


def list_materials_for_part(part: str) -> List[TurbineMaterial]:
    """列出某部位（rotor_blade / stator_vane / disk / housing）可用材料。"""
    return [m for m in _MATERIALS_LIST if part in m.parts]


def list_oxygen_compatible(part: str | None = None) -> List[TurbineMaterial]:
    """列出富氧兼容材料；可选限定部位。"""
    out = [m for m in _MATERIALS_LIST if m.oxygen_compatible]
    if part is not None:
        out = [m for m in out if part in m.parts]
    return out


def assert_oxygen_compatible(material_key: str, where: str = "") -> None:
    """
    富氧侧硬校验：抛 ValueError 如果材料不允许富氧。
    `where` 用于错误信息（例如 "ox_turbine.rotor_blade"）。
    """
    m = get_material(material_key)
    if not m.oxygen_compatible:
        raise ValueError(
            f"材料 '{m.name}' (Co={m.cobalt_fraction*100:.1f}%) "
            f"不允许用于富氧侧 {where}（ASTM G124 自燃风险）。"
            f"可用富氧材料：{[mm.key for mm in list_oxygen_compatible()]}"
        )


def material_audit_row(material_key: str, T_K: float, U_ms: float, side: str) -> dict:
    """
    单一材料 / 单一部位的快速校核行。

    返回字典，供 turbine.py 和 mechanical.py 复用。
    """
    m = get_material(material_key)
    sigma_yield_T = m.sigma_yield_at(T_K)
    sigma_centrifugal = m.density_kgm3 * U_ms * U_ms / 1.0e6  # MPa
    safety_factor = sigma_yield_T / max(sigma_centrifugal, 1e-6)
    issues: List[str] = []
    if side == "ox" and not m.oxygen_compatible:
        issues.append(
            f"CRITICAL: {m.name} 含 Co={m.cobalt_fraction*100:.1f}%，"
            f"禁用于富氧（ASTM G124）"
        )
    if T_K > m.T_max_use_K:
        if T_K <= m.T_max_short_K:
            issues.append(
                f"WARNING: T={T_K:.0f}K 超过长期上限 {m.T_max_use_K:.0f}K "
                f"(短时上限 {m.T_max_short_K:.0f}K)"
            )
        else:
            issues.append(
                f"CRITICAL: T={T_K:.0f}K 超过短时上限 {m.T_max_short_K:.0f}K"
            )
    if safety_factor < 1.2:
        issues.append(
            f"WARNING: 离心应力 σ={sigma_centrifugal:.0f}MPa vs "
            f"σy(T)={sigma_yield_T:.0f}MPa，安全系数 {safety_factor:.2f}<1.2"
        )
    return {
        "material_key":         material_key,
        "material_name":        m.name,
        "T_K":                  T_K,
        "U_ms":                 U_ms,
        "sigma_yield_T_MPa":    sigma_yield_T,
        "sigma_centrifugal_MPa": sigma_centrifugal,
        "safety_factor":        safety_factor,
        "oxygen_compatible":    m.oxygen_compatible,
        "issues":               issues,
    }


# =========================================
# 推荐组合（默认材料配置）
# =========================================
#
# 给 InputParameters 设默认材料时使用。每根轴一套：
#   rotor_blade / stator_vane / disk / housing
#
# 富氧侧（LOX 涡轮）：全氧兼容，T_in 上限 ~820 K
# 富燃侧（CH4 涡轮）：高温合金，T_in 上限 ~950 K
#
DEFAULT_OX_TURBINE_MATERIALS = {
    "rotor_blade": "inconel718_lowco",
    "stator_vane": "inconel625",
    "disk":        "nasa_hr1",
    "housing":     "inconel625",
}

DEFAULT_FUEL_TURBINE_MATERIALS = {
    "rotor_blade": "marm247_am",
    "stator_vane": "hastelloyx",
    "disk":        "inconel718",
    "housing":     "hastelloyx",
}

# 燃烧室壁默认材料(Raptor 1 量级:GRCop-84 内壁 + Inconel-718 外壳)
DEFAULT_CHAMBER_WALL_MATERIALS = {
    "chamber_inner_wall": "grcop84",
    "chamber_outer_wall": "inconel718_wall",
}

# 喷管壁默认材料(按分段)
#   nozzle_inner_wall          : 喉部+扩张段 燃气侧(再生冷却铜合金)
#   nozzle_outer_wall          : 扩张段 外壳/结构层(高镍)
#   nozzle_extension           : 真空延伸段(Raptor RVac 风格:再生冷却高镍)
#   nozzle_extension_radiative : 真空延伸段(Merlin Vac / 经典风格:辐射冷却铌)
DEFAULT_NOZZLE_WALL_MATERIALS = {
    "nozzle_inner_wall":          "grcop84",
    "nozzle_outer_wall":          "inconel718_wall",
    "nozzle_extension":           "inconel718_wall",
    "nozzle_extension_radiative": "niobium_c103",
}

# 泵默认材料:富氧侧(LOX 接触,无 Ti)vs 富燃侧(可用 Ti,轻)
DEFAULT_OX_PUMP_MATERIALS = {
    "pump_impeller": "inconel718_pump_ox",
    "pump_inducer":  "monelk500_impeller",
    "pump_housing":  "inconel718_pump_ox",
}
DEFAULT_FUEL_PUMP_MATERIALS = {
    "pump_impeller": "ti6al4v",
    "pump_inducer":  "ti6al4v",
    "pump_housing":  "ss316l",
}

# 喷注器默认材料(现代增材一体式)
DEFAULT_INJECTOR_MATERIALS = {
    "injector_face": "grcop84",        # 高热流面用铜合金
    "injector_body": "inconel718_injector",
}


def get_pump_material(side: str = "ox", part: str = "pump_impeller") -> TurbineMaterial:
    """便捷查询:返回泵某部件的默认材料。

    side ∈ {"ox","fuel"};part ∈ {"pump_impeller","pump_inducer","pump_housing"}
    """
    table = DEFAULT_OX_PUMP_MATERIALS if side == "ox" else DEFAULT_FUEL_PUMP_MATERIALS
    return get_material(table[part])


def get_injector_material(part: str = "injector_face") -> TurbineMaterial:
    """便捷查询:返回喷注器某部件的默认材料。

    part ∈ {"injector_face","injector_body"}
    """
    return get_material(DEFAULT_INJECTOR_MATERIALS[part])


def get_chamber_wall_material(layer: str = "chamber_inner_wall") -> TurbineMaterial:
    """便捷查询:返回燃烧室某壁层的默认材料。

    layer ∈ {"chamber_inner_wall", "chamber_outer_wall"}
    """
    return get_material(DEFAULT_CHAMBER_WALL_MATERIALS[layer])


def get_nozzle_wall_material(layer: str = "nozzle_inner_wall") -> TurbineMaterial:
    """便捷查询:返回喷管某壁层的默认材料。"""
    return get_material(DEFAULT_NOZZLE_WALL_MATERIALS[layer])
