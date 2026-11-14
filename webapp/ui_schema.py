"""JSON presentation contract backed by the desktop's shared declarations.

Values remain in EngineInputs' SI units. A numeric field's display value is
``value * factor``; clients divide by that factor before submitting a project.
This module deliberately does not import Qt or the desktop window.
"""
from dataclasses import asdict
from copy import deepcopy

from webapp.solver import platform_defaults
from lre_workbench.materials.catalog import list_materials_for_part
from lre_workbench.properties.propellants import propellant_models
from lre_workbench.domain.parameters import (parameter_metadata, preburner_mode_field, pump_speed_mode_field,
                                turbine_stage_mode_field, ROLE_LABELS)
from lre_workbench.domain.analysis_roles import analysis_parameter_roles
from lre_workbench.systems.pressure_budget import BRANCH_BUDGET_FIELDS
from lre_workbench.materials.wall import list_wall_datasets
from lre_workbench.properties.datasets import methane_dataset_catalog
from lre_workbench.components.valves import valve_configuration_catalog
from lre_workbench.domain.metadata import (
    _MATERIAL_FIELD_META, CHAMBER_INPUTS,
    NONLINEAR_SOLVER_CHOICES, COMBUSTION_LOSS_CHOICES, THERMO_DATASET_CHOICES,
    TURBINE_BOUNDARY_CHOICES, TURBINE_MEANLINE_CHOICES, TURBINE_GEOMETRY_STATE_CHOICES,
    DISPLAY_UNIT_MAP, PARAM_GROUPS, SUPPLY_FIELDS,
    OVERVIEW_GROUPS, COMPONENT_LABELS, COMPONENT_GROUPS,
    KEY_LABELS, STATE_LABELS,
)

GROUP_IDS = (
    "cycle", "design", "inlet", "thermo", "preburners", "preburner_design",
    None, "pressure_ffsc", "pressure", "cooling", "solver",
    "pumps", "turbines", "turbine_materials",
)

CHAMBER_GROUPS = (
    ("chamber_contour", "推力室型面与覆盖", (
        "expansion_ratio", "chamber_contraction_ratio", "chamber_l_star_m", "chamber_conv_half_angle_deg",
        "nozzle_bell_fraction", "nozzle_contour_method", "nozzle_initial_angle_deg",
        "nozzle_exit_angle_deg", "cooling_cover_eps")),
    ("chamber_channels", "推力室·冷却通道", (
        "cooling_channel_auto_size", "cooling_sizing_reference", "cooling_target_velocity_ms", "cooling_n_channels",
        "cooling_channel_width_m", "cooling_channel_height_m", "cooling_wall_thickness_m",
        "cooling_htc_enhancement", "cooling_channel_rel_roughness",
        "cooling_channel_k_minor", "cooling_wall_temp_limit_status", "cooling_wall_temp_limit_K",
        "cooling_wall_temp_limit_basis")),
    ("chamber_film", "推力室·膜冷却与辐射", (
        "film_cooling_fraction", "film_effectiveness_initial", "film_decay_length_m",
        "cooling_gas_emissivity")),
    ("chamber_structure", "推力室·结构与材料", (
        "chamber_wall_allow_pa", "chamber_wall_safety_factor",
        "chamber_inner_wall_material", "chamber_outer_wall_material")),
)

# These EngineInputs are preserved by the desktop case format but are not
# registered as visible fields in its main panel. Expose them separately so a
# browser edit never drops a supported design input.
EXTRA_FIELDS = (
    ("cooling_channel_roughness_m", "绝对等效粗糙度 ks", "m"),
    ("cooling_k_minor_inlet", "入口局部阻力系数", "—"),
    ("cooling_k_minor_outlet", "出口局部阻力系数", "—"),
    ("cooling_htc_enhancement_basis", "换热标定依据与适用范围", ""),
    ("cooling_wall_conductivity", "内衬导热系数", "W/(m·K)"),
    ("lox_pump_impeller_material", "液氧泵叶轮材料", ""),
    ("fuel_pump_impeller_material", "燃料泵叶轮材料", ""),
    ("cooling_channel_table_json", "冷却通道沿程分布表（JSON）", ""),
)

WALL_THERMAL_FIELDS = (
    ("wall_thermal_dataset_id", "铜合金内衬热物性数据集", ""),
    ("wall_manufacturing_state", "方案内衬制造状态", ""),
    ("wall_material_zones_json", "轴向材料分区（JSON）", ""),
)


def _wall_thermal_group(defaults):
    catalog = [dataset.metadata() for dataset in list_wall_datasets()]
    cycles = ["ffsc"]
    fields = [_field(key, label, unit, defaults, cycles)
              for key, label, unit in WALL_THERMAL_FIELDS]
    for field in fields:
        field.update(role="model_assumption", role_label="模型假设",
                     visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
    fields[0].update(type="select", choices=[{"value": item["dataset_id"], "label": item["label"]}
                                            for item in catalog], dataset_catalog=catalog,
                     empty_choice_label="请选择有来源的热物性数据集（历史材料无对应曲线）",
                     help="用于燃气侧铜合金内衬热壁及通道肋片的稳态导热。按文献试样的制造状态和温区选取，不代表外套、涂层或整台推力室均使用该材料。更换数据集后需重新运行设计点。")
    fields[1].update(type="select", choices=[
        {"value": "unspecified", "label": "未指定 / 待确认"},
        *[{"value": item["manufacturing_state"]["state_id"],
           "label": item["manufacturing_state"]["label"]} for item in catalog],
        {"value": "other", "label": "其他制造状态（与所选数据不匹配）"}],
        help="用于标记方案与数据试样的制造状态是否匹配；选择匹配状态不构成实际制造或结构使用资格认证。")
    fields[2].update(advanced=True, multiline=True, role="design_input", role_label="设计输入",
                     help='留空时使用上方数据集；未覆盖的间隙同样继承默认数据。x 从喷注面起算，单位 m，区段不能重叠且须在冷却覆盖内。格式：[{"x_start_m":0.2,"x_end_m":0.4,"dataset_id":"cuzr_qi2024_heat_treated_v1","manufacturing_state":"qi2024_solution_cold_work_aged"}]')
    structural_context = [
        {"key": key, "label": label,
         "labels": {material.key: material.display_name for material in list_materials_for_part(part)},
         "note": note}
        for key, part, label, note in (
            ("chamber_inner_wall_material", "chamber_inner_wall", "燃气侧内衬材料配置",
              "默认热物性数据集须与内衬材料身份一致；旧值保留供核查，显式轴向材料分区另列。修改后需重新计算。"),
            ("chamber_outer_wall_material", "chamber_outer_wall", "外套材料配置",
             "作为结构配置保留；未把外套厚度串入燃气至冷却剂的热阻，也未验证承载能力。"))]
    return {"id": "wall_thermal", "title": "推力室内衬材料与热物性", "full_title": "推力室内衬材料与热物性",
            "cycles": cycles, "fields": fields,
            "description": "这里定义推力室燃气侧铜合金内衬，包括冷却覆盖内的燃烧室、喉部和喷管段。新方案默认采用 GRCop-42 的文献热物性；已有方案保留原材料。导热率、制造状态、壁温限值和复用寿命分别核查，不能仅凭牌号判断寿命。",
            "advanced_title": "轴向材料分区",
            "advanced_help": "分区是物理设计输入，不是数值初值。完整方案保留原始定义，结果保存实际采用的逐单元数据。",
            "configuration_context": structural_context}


def _title(text):
    return text.split(" / ", 1)[0]


def _cycles(title):
    return ["ffsc"]


def _field(key, label, unit, defaults, cycles, tooltip=""):
    default = defaults[key]
    factor, unit = DISPLAY_UNIT_MAP.get(key, (1, unit))
    result = {
        "key": key, "label": _title(label), "full_label": label,
        "unit": unit, "factor": factor, "default": default,
        "type": "text" if isinstance(default, str) else "int" if isinstance(default, int) else "number",
        "cycles": cycles,
    }
    if tooltip:
        result["tooltip"] = tooltip
    if key in ("steady_ox_power_tolerance_W", "steady_fuel_power_tolerance_W"):
        result["cycles"] = ["ffsc"]
    if key == "coolant_cp":
        # The surrogate branch uses CoolProp enthalpy, not the legacy fixed cp.
        result["propellants"] = ["lox_methane"]
    choices = None
    if key == "cycle_type":
        choices = [("ffsc", "全流量补燃循环（FFSC）")]
    elif key == "propellant_pair":
        choices = [("lox_methane", "液氧／甲烷")]
    elif key == "nonlinear_solver":
        choices = NONLINEAR_SOLVER_CHOICES
    elif key == "combustion_loss_model":
        choices = COMBUSTION_LOSS_CHOICES
    elif key == "methane_thermo_dataset":
        choices = THERMO_DATASET_CHOICES
    elif key == "gas_eos":
        choices = [("ideal-gas", "理想气体混合物（默认）"),
                   ("Peng-Robinson", "Peng–Robinson（PR，预燃室与涡轮 · 研究模型）"),
                   ("Peng-Robinson-Boston-Mathias", "Peng–Robinson–Boston–Mathias（PR–BM，全燃气流程 · 研究模型）")]
    elif key == "gas_species_policy":
        choices = [("auto", "随状态方程选择"), ("full", "完整热化学物种"),
                   ("pr_supported_v1", "PR 参数支持的共同物种")]
    elif key == "turbine_boundary_basis":
        choices = TURBINE_BOUNDARY_CHOICES
    elif key == "turbine_meanline_mode":
        choices = TURBINE_MEANLINE_CHOICES
    elif key.endswith("_turbine_geometry_state_basis"):
        choices = TURBINE_GEOMETRY_STATE_CHOICES
    elif key in _MATERIAL_FIELD_META:
        part, oxygen_only = _MATERIAL_FIELD_META[key]
        choices = [(m.key, m.display_name) for m in list_materials_for_part(part)
                   if not oxygen_only or m.oxygen_compatible]
    elif key in ("lox_pump_impeller_material", "fuel_pump_impeller_material"):
        choices = [(m.key, m.display_name) for m in list_materials_for_part("pump_impeller")
                   if not key.startswith("lox_") or m.oxygen_compatible]
    if choices:
        result["type"] = "select"
        result["choices"] = [{"value": value, "label": label} for value, label in choices]
    result.update(parameter_metadata(key))
    if key == "gas_eos":
        result.update(label="燃气物性模型", full_label="燃气物性模型", role="model_assumption", role_label="模型假设", advanced=False,
                      help="用于计算密度、焓、熵和比热。默认采用理想气体混合物及完整候选物种；PR 与 PR–BM 可作为研究模型用于设计点计算，当前参数覆盖 16 种化学物种。",
                      help_sections=[
                          {"label": "化学状态与物性", "text": "预燃室和主燃烧室按入口组成、焓和压力求化学平衡。状态方程用于计算性质和化学势，不等同于反应速率模型。循环计算中的涡轮与喷管模型采用冻结组成。"},
                          {"label": "模型名称", "text": "PR 为 Peng–Robinson 状态方程；PR–BM 为采用 Boston–Mathias 温度修正的 Peng–Robinson 状态方程。它们描述真实气体的非理想性质，名称不代表精度等级。"},
                          {"label": "模型选择", "text": "显式选择理想气体时采用完整候选物种；选择 PR 或 PR–BM 时采用当前参数支持的 16 种物种。每次选择均取消旧版本温升折减，保留所选热化学数据。确认保存为新版本后须重新计算；此页选择设计模型，热力学校核提供独立模型对照。"},
                          {"label": "物种与数据", "text": "化学物种指分子、原子或自由基，例如 H₂O、H 和 OH，与几何部件无关。源数据包含 53 种候选物种，实际组成受入口元素约束。PR 与 PR–BM 的参数目前覆盖其中 16 种，未包含 H、O、OH 等解离物种。这是当前参数覆盖限制，不是 PR 状态方程的理论物种数上限，也不表示精度档位。"},
                          {"label": "部件覆盖", "text": "PR–BM 覆盖预燃室、涡轮、主燃烧室、冷却燃气侧和喷管；PR 分段模型仅覆盖预燃室与涡轮，后续燃气流程采用理想气体。各接口传递实际组分与焓。"},
                          {"label": "模型校核", "text": "PR–BM 已开展热力学一致性与数值实现校核，高压混合物的试验精度、全局多相稳定性及高压输运仍缺少验证。研究模型的计算结果不能作为已验证性能。现有理想气体 / PR 整机对照使用共同 16 物种，不能代替完整物种与受限物种的比较。"},
                      ])
    elif key in ("gas_species_policy", "hot_side_thermo_method"):
        result.update(hidden=True, role="model_assumption", role_label="模型假设")
    if key in ('cycle_type', 'propellant_pair'):
        result['read_only'] = True
    return result


def _overview(groups):
    return [{"title": _title(title), "fields": [
        {"key": key, "label": label, "unit": unit} for key, label, unit in fields
    ]} for title, fields in groups]


def _system_groups(groups):
    """Select the system editor while retaining the complete case contract.

    Detailed component assumptions remain in defaults/groups for lossless case
    import/export, but are not advertised as available design workspaces.
    """
    included = (set(GROUP_IDS[:10]) - {None, "pressure"}) | {"pumps", "turbines", "solver", "chamber_contour", "chamber_channels", "wall_thermal", "valve_configuration"}
    excluded = {"cooling_marching_enable", "cooling_dp_auto", "coolant_cp", "pump_tip_speed_limit_ms",
                "methane_thermo_dataset", "gas_species_policy", "hot_side_thermo_method"}
    result = []
    for original in groups:
        if original["id"] not in included:
            continue
        if original["id"] in ("preburners", "thermo"):
            # All preburner inputs have one system-editor home. Keep the full
            # desktop/case groups below unchanged for compatibility.
            continue
        group = deepcopy(original)
        group["fields"] = [f for f in group["fields"] if f["key"] not in excluded]
        # Historical Q and pressure budgets have one compatibility editor home.
        group["fields"] = [f for f in group["fields"] if f["key"] != "cooling_dp_pa"]
        if group["id"] == "solver":
            group["fields"] = [f for f in group["fields"] if f["key"] in (
                "nonlinear_solver", "cooling_grid_points", "steady_ox_power_tolerance_W",
                "steady_fuel_power_tolerance_W")]
            group["fields"].extend(deepcopy(next(g for g in groups if g["id"] == "thermo")["fields"]))
            group["title"] = "数值求解"
            group["compact"] = True
            group["summary"] = "启动估计影响求解过程；主室温度与物性由入口组成、焓和压力计算。"
            group["initials_inline"] = True
            group["initials_title"] = "燃气状态启动估计"
            group["initials_help"] = "用于首轮冷却与尺寸估算，通常沿用方案值。收敛困难时调整，并在结果区核对完整残差。"
        if group["id"] == "cycle":
            group["title"] = "发动机构型与循环原理"
            group["description"] = "说明当前方案的推进剂组合、循环形式、轴系驱动关系及燃气通路。构型信息按方案记录显示，不在本页修改。"
            for field in group["fields"]:
                fixed = {"cycle_type": ("ffsc", "全流量补燃循环（FFSC）"),
                         "propellant_pair": ("lox_methane", "液氧／甲烷")}.get(field["key"])
                if fixed:
                    field.update(read_only=True, choices=[{"value": fixed[0], "label": fixed[1]}],
                                 help={"cycle_type": "方案记录的循环形式。当前设计工作区支持全流量补燃循环；氧侧与燃料侧分别采用独立涡轮—泵轴系，两路涡轮排气进入主燃烧室。",
                                       "propellant_pair": "方案记录的氧化剂与燃料组合。当前设计工作区支持液氧／甲烷；供给温度与压力在“推进剂供给条件”中定义。"}[field["key"]])
        elif group["id"] == "design":
            group["description"] = "目标推力与下方环境压力构成同一设计工况；室压和总氧燃比为指定输入。主喷管面积比统一在“推力室与喷管型面”中设置，推力、比冲和流量的计算值在结果区显示。"
            group["notes_intro"] = "本组规定概念设计点的目标与边界。比冲是总体性能指标，本页比冲初值仅用于流量初始化，性能要求应另行规定。完整计算还需设置推进剂供给条件、循环模型、部件效率、压力损失及喷管几何。"
        elif group["id"] == "preburner_design":
            group["title"] = "预燃室热力匹配条件"
            group["compact"] = True
            group["section_notes"] = True
            group["summary"] = "规定预燃室在总体循环中的设计工作条件，并管理相关工程限值。"
            group["initials_help"] = "用于启动迭代，通常沿用方案值；收敛困难时再调整。"
            previous = next(g for g in groups if g["id"] == "preburners")
            group["fields"].extend(deepcopy(previous["fields"]))
            group["fields"].insert(0, preburner_mode_field())
            group["sections"] = [
                {"id": "targets", "title": "设计工作条件", "context": "preburner-conditions",
                 "description": "两侧采用同一匹配方式，分别给定各自目标；预燃室氧燃质量比按各自入口流量定义。",
                 "keys": ["preburner_design_mode", "ox_preburner_temp_target", "fuel_preburner_temp_target",
                          "ox_preburner_mr_target", "fuel_preburner_mr_target"]},
                {"id": "limits", "title": "出口燃气温度限值与依据",
                 "description": "温度目标规定工作点，温度上限用于工程校核。上限未设置仍可求解，但该项显示“未评估”；超限不会自动降低目标或调整解。出口燃气温度上限不代表壁面或涡轮金属的允许温度。",
                 "field_groups": [{"title": f"{label}预燃室", "side": side,
                                   "keys": [f"{side}_preburner_temp_limit{suffix}" for suffix in ("_status", "", "_basis")]}
                                  for side, label in (("ox", "富氧"), ("fuel", "富燃"))],
                 "keys": [f"{side}_preburner_temp_limit{suffix}" for side in ("ox", "fuel")
                          for suffix in ("_status", "", "_basis")]},
            ]
        elif group["id"] == "inlet":
            # Separate interface conditions, component assumptions and chemistry.
            # Keep the inlet ID so existing navigation state still resolves.
            group["title"] = "推进剂供给条件"
            group["compact"] = True
            group["summary"] = "两路分别建模：贮箱出口 → 前置低压泵（外部供功）→ 供给管路 → 主泵入口。"
            group["description"] = "本页定义单个设计工况的接口条件与供给链假设。给定值参与本次计算，不是数值初值；示例温度和压力需按方案接口要求确认。当前采用前置低压泵方案，尚未求解贮箱增压过程或由部件特性匹配供给压力。"
            efficiency_keys = {"pump_efficiency_ox", "pump_efficiency_fuel",
                               "turbine_efficiency_ox", "turbine_efficiency_fuel"}
            combustion_keys = {"combustion_loss_model", "combustion_efficiency", "gas_eos"}
            efficiency = [f for f in group["fields"] if f["key"] in efficiency_keys]
            combustion = [f for f in group["fields"] if f["key"] in combustion_keys]
            group["fields"] = [f for f in group["fields"]
                               if f["key"] not in efficiency_keys | combustion_keys |
                               {"tank_pressure_pa", "inlet_pressure", "feed_line_dp_pa", "boost_pump_efficiency"}]
            for field in group["fields"]:
                field["show_role"] = True
                if field.get("fallback_key"):
                    field["show_effective_value"] = True
            group["sections"] = [
                {"id": "supply", "title": "贮箱出口接口条件",
                 "description": "给定出口液体的温度和绝对压力。绝对压力以真空为零点；此处不是气枕压力的计算结果。",
                 "field_groups": [{"title": label, "keys": [f"{side}_inlet_temp", f"{side}_tank_pressure_pa"]}
                                  for side, label in (("ox", "液氧"), ("fuel", "液甲烷"))],
                 "keys": [f"{side}_{suffix}" for side in ("ox", "fuel")
                          for suffix in ("inlet_temp", "tank_pressure_pa")]},
                {"id": "boost", "title": "前置泵与供给管路假设",
                 "description": "前置泵出口压力为设计给定值；效率和管路压降为设计点假设。当前未由泵特性或管路几何求解这些数值。",
                 "field_groups": [{"title": label, "keys": [f"{side}_{suffix}" for suffix in
                                   ("boost_outlet_pressure_pa", "boost_pump_efficiency", "feed_line_dp_pa")]}
                                  for side, label in (("ox", "液氧"), ("fuel", "液甲烷"))],
                 "keys": [f"{side}_{suffix}" for side in ("ox", "fuel") for suffix in
                          ("boost_outlet_pressure_pa", "boost_pump_efficiency", "feed_line_dp_pa")]},
            ]
            result.append(group)
            result.append({"id": "component_efficiencies", "title": "泵与涡轮效率假设",
                           "cycles": group["cycles"], "compact": True,
                           "summary": "设计点采用常效率；实际性能图需由部件设计或试验提供。",
                           "fields": efficiency,
                           "sections": [
                               {"id": "pumps", "title": "主泵等熵效率", "keys": ["pump_efficiency_ox", "pump_efficiency_fuel"]},
                               {"id": "turbines", "title": "涡轮等熵效率", "keys": ["turbine_efficiency_ox", "turbine_efficiency_fuel"]},
                           ]})
            for field in combustion:
                field.update(read_only=field["key"] != "gas_eos", advanced=False)
                if field["key"] == "combustion_loss_model":
                    field.update(label="温度修正方式", full_label="温度修正方式",
                                 choices=[{"value": "adiabatic", "label": "不作温升折减"},
                                          {"value": "legacy_temperature_rise", "label": "旧版本温升折减"}],
                                 inline_help="常规设计不采用温升折减；原设置仅作为历史模型信息保留。",
                                 help="旧版本先求化学平衡，再按系数降低温升，并把相应焓差计入经验热汇。该方法已退出常规设计执行，原设置与结果仍可查看。该系数不等于燃烧效率，也不用于把温度调整到预期值。")
                elif field["key"] == "combustion_efficiency":
                    field.update(label="旧版本温升折减系数", full_label="旧版本温升折减系数",
                                 help="记录旧版本温升折减所用的系数，供解释历史结果；常规设计不再执行该修正。该系数未经试验标定，不代表特征速度效率或反应完成度。")
            species = deepcopy(next(f for source in groups for f in source["fields"]
                                    if f["key"] == "gas_species_policy"))
            # Retain valid historical identities for validation and read-only
            # descriptions; explicit EOS choices determine the supported set.
            species.update(label="参与平衡计算的化学物种", full_label="参与平衡计算的化学物种",
                           hidden=False, advanced=False, read_only=True,
                           choices=[{"value": "full", "label": "53 种化学物种（含 H、O、OH）"},
                                    {"value": "pr_supported_v1", "label": "16 种化学物种（PR 参数覆盖范围）"},
                                    {"value": "auto", "label": "旧版本自动选择（沿用已保存设置）"}],
                           inline_help="范围随燃气物性模型确定。理想气体采用完整候选物种；PR 系列采用参数支持的 16 种物种，缺少 H、O、OH 等解离物种。",
                           help="物种是参与平衡计算的化学种类。理想气体模型采用源数据的 53 种候选物种，实际组成受入口元素约束。16 种是当前 PR 参数的覆盖范围，不是 PR 理论物种数上限；缺少高温解离物种可能导致温度偏高，须独立评估物种删减影响。物种范围仅显示，不提供单独切换。历史 auto 值按原状态方程解释。查看或保存其他参数不会改写原物种范围；显式选择燃气物性模型时才采用其对应范围。")
            combustion.append(species)
            result.append({"id": "chamber_chemistry", "title": "工质与热物性模型",
                           "cycles": group["cycles"], "compact": True, "requires_review": True,
                           "summary": "选择本次设计采用的燃气物性模型，核对化学物种范围。模型变更后须确认保存并重新计算。",
                           "configuration_context": [
                               {"key": "methane_thermo_dataset", "label": "热化学数据",
                                "labels": dict(THERMO_DATASET_CHOICES),
                                "note": "数据变更后需重新确认本组参数并计算。"},
                           ],
                           "fields": combustion})
            continue
        elif group["id"] == "pressure_ffsc":
            group.update(title="压力分配与压降预算", full_title="压力分配与压降预算",
                         compact=True, pressure_workspace=True,
                         summary="按供给支路和燃气通路分配设计压降，计算泵出口压力需求及支路节流需求。")
            group["fields"].extend(deepcopy(f) for source in groups for f in source["fields"]
                                   if f["key"] == "valve_line_dp_ratio")
            labels = {"preburner_injector_dp_ratio": "统一喷注器压降比（相对于各自预燃室压力）",
                      "valve_line_dp_ratio": "统一管路及全开阀压降比（相对于主燃烧室压力）",
                      "ox_turbine_exhaust_dp_ratio": "富氧燃气通路压降比（相对于主燃烧室压力）",
                      "fuel_turbine_exhaust_dp_ratio": "富燃燃气通路压降比（相对于主燃烧室压力）"}
            for field in group["fields"]:
                field.update(factor=100, unit="%", cycles=["ffsc"])
                if field["key"] in labels:
                    field["label"] = labels[field["key"]]
        elif group["id"] == "cooling":
            group["fields"].extend(deepcopy(f) for source in groups for f in source["fields"] if f["key"] == "cooling_dp_pa")
            group["title"] = "再生冷却计算方法"
            group["description"] = "说明当前热流、壁面导热、冷却剂换热及压降的计算方法。型面、通道和材料分别设置；冷却吸热与压降参与整机循环迭代，收敛后在计算结果中校核。"
            group["advanced_title"] = "历史预算参数"
            group["advanced_help"] = "仅当前方案仍采用给定预算时生效；保留原值用于复现，不生成沿程解。"
            for field in group["fields"]:
                if field["key"] == "cooling_bartz_mode":
                    field.update(label="当前计算方法", read_only=True)
                elif field["key"] in ("cooling_heat_load", "cooling_dp_pa"):
                    field.update(advanced=True)
        elif group["id"] == "pumps":
            group["title"] = "泵方案与尺寸初估"
            group["description"] = "循环计算给出泵所需的流量、压升和功率；本组提供转速、级数与几何初选假设。初选值在本轮按所选方式使用，不是求解器迭代初值，也不代表部件性能已验证。运行后在“泵设计与建模交接”查看设计点、吸入条件、尺寸与流道；部件校核后可修正假设并重新匹配循环。"
            group["fields"].extend(pump_speed_mode_field(side) for side in ("lox", "fuel"))
            group["sections"] = [
                {"id": side, "title": title, "keys": [f"{side}_pump_{suffix}" for suffix in (
                    "speed_mode", "speed_rpm_override", "specific_speed_target", "n_stages", "head_coefficient_psi",
                    "inlet_flow_coefficient", "outlet_flow_coefficient", "eye_hub_ratio", "axial_length_ratio") ]}
                for side, title in (("lox", "液氧泵"), ("fuel", "甲烷泵"))
            ]
        elif group["id"] == "turbines":
            group["title"] = "涡轮方案与尺寸初估"
            group["description"] = "循环给出流量、焓降和功率，富氧涡轮与液氧泵同轴，富燃涡轮与甲烷泵同轴；本组初选速比和级数用于尺寸初估，不自动反馈涡轮效率。可选均径校核不求逐级压力或叶型。"
            group["advanced_title"] = "概念流道参考假设"
            group["advanced_help"] = "仅供旧静态参考口径下的概念环道估算；CFturbo 子午面不受这些假设约束。总态接口模式不生成该环道。"
            for field in group["fields"]:
                if field["key"].startswith(("ox_turbine_", "fuel_turbine_")):
                    field["advanced"] = True
                    field.setdefault("visibility", {}).setdefault("all", []).append(
                        {"key": "turbine_boundary_basis", "op": "eq", "value": "unspecified"})
                if field["key"].endswith("_n_stages_user"):
                    field["label"] = field["label"].split("（", 1)[0]
                    field.setdefault("visibility", {}).setdefault("all", []).append(
                        {"key": field["key"], "op": "neq", "value": 0})
            group["fields"].extend(turbine_stage_mode_field(side) for side in ("ox", "fuel"))
            group["sections"] = [
                {"id": "state_basis", "title": "状态量定义", "keys": ["turbine_boundary_basis"]},
                {"id": "meanline", "title": "可选均径校核", "keys": ["turbine_meanline_mode"]},
            ] + [
                {"id": side, "title": title,
                 "keys": [f"turb_{side}_{suffix}" for suffix in
                          ("u_over_c_target", "stage_mode", "n_stages_user", "u_max_ms", "reaction_degree",
                           "meanline_flow_coefficient", "stage_work_fractions")]
                         + [f"{side}_turbine_{suffix}" for suffix in
                            ("geometry_state_basis", "inlet_flow_coefficient", "outlet_flow_coefficient",
                             "stator_axial_chord_ratio", "rotor_axial_chord_ratio", "row_gap_ratio")]}
                for side, title in (("ox", "富氧涡轮（液氧泵轴系）"),
                                     ("fuel", "富燃涡轮（甲烷泵轴系）"))
            ]
        elif group["id"] == "wall_thermal":
            # The system editor does not expose chamber_structure. Give its
            # existing liner field one editable home without changing storage.
            structure = next(g for g in groups if g["id"] == "chamber_structure")
            liner = deepcopy(next(f for f in structure["fields"]
                                  if f["key"] == "chamber_inner_wall_material"))
            liner.update(label="燃气侧内衬材料", advanced=False, hidden=False, read_only=False,
                         visibility=deepcopy(group["fields"][0]["visibility"]),
                         help="手动选择方案内衬材料，并分别核对下方热物性数据集和制造状态。材料与数据集必须匹配；选择材料不会自动改换数据集、制造状态或历史记录。保存后重新计算设计点。")
            group["fields"].insert(0, liner)
        elif group["id"] == "chamber_contour":
            group["title"] = "推力室与喷管型面"
            group["full_title"] = group["title"]
            group["description"] = "设置燃烧室、收敛段和喷管的初步内型面。默认值为可修改的几何设计起点；尺寸由设计点计算确定，供循环、燃气侧和冷却侧共同使用。冷却覆盖位置在“再生冷却布置方式”中设置，沿用本组型面。"
            layout = deepcopy(group)
            layout.update(id="chamber_cooling_layout", title="再生冷却布置方式",
                          full_title="再生冷却布置方式",
                          description="当前再生冷却覆盖从喷注面延伸至指定下游截面，冷却剂默认逆流。面积比用于定位该截面，不表示冷却覆盖百分比。缩短覆盖后，剩余喷管段仍参与气体膨胀计算，但尚无独立的辐射冷却、膜冷却或发汗冷却壁温校核。")
            layout["fields"] = [f for f in layout["fields"] if f["key"] == "cooling_cover_eps"]
            for field in layout["fields"]:
                field.update(label="再生冷却下游边界面积比 A冷/At（0=跟随主喷管出口）",
                             help="A冷为覆盖边界处的燃气通流截面积，At为喉部面积；沿扩张段定位，与轴向长度比例或壁面积比例不同。0是跟随主喷管出口的输入约定，不是物理面积比。该位置是覆盖下游边界，默认逆流时为冷却剂入口侧；指定位置不代表该段或剩余喷管段已通过热校核。")
            group["fields"] = [f for f in group["fields"] if f["key"] != "cooling_cover_eps"]
            result.extend((group, layout))
            continue
        elif group["id"] == "chamber_channels":
            group["title"] = "再生冷却通道设计"
            group["full_title"] = group["title"]
            group["description"] = "定义通道几何、换热与阻力假设及热设计校核条件。当前采用矩形代表通道的准一维稳态计算；几何尺寸是待校核的设计输入，不能由计算收敛直接认定可制造或满足寿命要求。"
            additions = {"cooling_channel_table_json", "cooling_channel_roughness_m", "cooling_k_minor_inlet",
                         "cooling_k_minor_outlet", "cooling_htc_enhancement_basis"}
            group["fields"].extend(deepcopy(f) for source in groups for f in source["fields"] if f["key"] in additions)
            for field in group["fields"]:
                if field["key"] == "cooling_channel_table_json":
                    field.update(hidden=False, custom_editor="channel_distribution", role="design_input",
                                 role_label="设计输入", visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
            group["sections"] = [
                {"id": "conditions", "title": "计算条件与数据来源", "context": "cooling-input-summary"},
                {"id": "geometry", "title": "通道几何定义", "context": "cooling-channel-inputs", "description": "各通道均匀分流，沿型面子午线布置。节距、肋宽、总流通面积与水力直径由型面和通道尺寸推导；当前不求解螺旋通道、分叉或周向不均匀分流。", "keys": ["cooling_channel_table_json", "cooling_channel_auto_size", "cooling_sizing_reference", "cooling_target_velocity_ms", "cooling_n_channels", "cooling_channel_width_m", "cooling_channel_height_m", "cooling_wall_thickness_m"]},
                {"id": "models", "title": "换热与压降模型", "context": "cooling-model-inputs", "description": "冷却侧采用主体物性 Dittus–Boelter 湍流关联式和简化层流处理；真实甲烷物性查询不代表近临界换热恶化、高宽比温度分层或扰流结构已验证。压降计入 Darcy 摩擦、局部损失及流动加速。", "keys": ["cooling_htc_enhancement", "cooling_htc_enhancement_basis", "cooling_channel_roughness_m", "cooling_channel_rel_roughness", "cooling_k_minor_inlet", "cooling_k_minor_outlet", "cooling_channel_k_minor"]},
                {"id": "limits", "title": "热设计校核条件", "description": "先求实际壁温，再按登记限值校核；本栏不会截断壁温，也不会自动调整通道使其达标。材料热物性数据温区、设计壁温限值和结构寿命分别判断。未设定限值表示该项尚未评估。", "keys": ["cooling_wall_temp_limit_status", "cooling_wall_temp_limit_K", "cooling_wall_temp_limit_basis"]},
            ]
            group["geometry_source_key"] = "cooling_channel_table_json"
        result.append(group)
    return _ffsc_system_presentation(result)


def _ffsc_system_presentation(groups):
    """Use FFSC labels and help in every platform editor."""
    design_notes = {
        "thrust": ("设计目标", [
            ("参数定义", "设计点在指定环境压力下应达到的主喷管推力。"),
            ("设计作用", "作为循环匹配目标，与两侧轴功率平衡条件联立求解推进剂总质量流量及涡轮工作参数。"),
            ("取值依据", "依据运载器总体要求、发动机配置和设计工况确定。推力指标对应的环境压力应与本页设置一致。"),
        ]),
        "pc": ("选定设计参数", [
            ("参数定义", "设计点主燃烧室的集总绝对压力 p_c。"),
            ("设计作用", "用于主室热化学计算、喷管入口条件及循环压力预算。本轮设计点求解保持室压不变。"),
            ("取值依据", "结合性能要求、泵功需求、冷却能力和结构承载条件，经方案权衡选定。软件默认示例 33 MPa 为项目设计假设。"),
        ]),
        "of_ratio": ("选定设计参数", [
            ("参数定义", "整机液氧总供给质量流量与甲烷总供给质量流量之比，O/F = ṁ_ox / ṁ_f，无量纲。"),
            ("设计作用", "按给定总流量确定液氧与甲烷的供给流量。两侧预燃室的局部氧燃比分别按各自入口流量定义。"),
            ("取值依据", "结合性能、推进剂装载与供给要求及冷却条件选定。本轮求解保持整机氧燃比不变；软件默认示例 3.6 为项目设计假设。"),
        ]),
        "ambient_pressure": ("工况边界", [
            ("参数定义", "设计工况下喷管外部的绝对压力 p_a。"),
            ("设计作用", "参与压力推力项 (p_e − p_a) A_e 的计算，其中 p_e、A_e 分别为喷管出口压力和面积。工况推力与比冲采用该环境条件，真空性能单独计算。"),
            ("取值依据", "0 MPa 表示真空；0.101325 MPa 为标准海平面参考条件。其他取值作为自定义背压，由飞行高度及所用大气模型，或地面试验背压条件确定。"),
        ]),
        "target_isp": ("数值初值", [
            ("参数定义", "用于初始化推进剂总质量流量的比冲估计值 Isp₀。"),
            ("计算作用", "按 ṁ₀ = F目标 / (g₀ · Isp₀) 构造流量初值，g₀ = 9.80665 m/s²。最终比冲由喷管模型计算；该初值影响迭代过程，不作为性能目标或验收条件。"),
            ("取值依据", "按相近推进剂组合和喷管构型，在本页设计环境下的性能估计设置。切换环境后应重新核定该估计值。软件默认 327 s 为数值初始化设置。"),
        ]),
    }
    help_text = {
        "expansion_ratio": "主喷管出口面积 Ae / 喉部面积 At；循环、推力室轮廓和冷却使用同一个输入。它不同于燃烧室收缩比及冷却终点面积比。34.34 是示例几何假设。",
        "valve_line_dp_ratio": "基础管路及全开阀的设计压降与主燃烧室压力之比。未单独设置的支路沿用本值；供给压力匹配得到的附加调节压降单列。",
    }
    for group in groups:
        group.update(cycles=["ffsc"], propellants=["lox_methane"])
        if group["id"] == "design":
            group["primary_initials"] = ["target_isp"]
        for field in group["fields"]:
            key = field["key"]
            field.update(cycles=["ffsc"], propellants=["lox_methane"])
            if key in help_text:
                field["help"] = help_text[key]
            if key in design_notes:
                role_label, sections = design_notes[key]
                # Selection guidance describes how to choose an input; it is
                # not evidence that a source has been recorded for this case.
                field.update(role_label=role_label, provenance=None, show_fixed_mode_note=False,
                             help_sections=[{"label": label, "text": text} for label, text in sections],
                             help=" ".join(f"{label}：{text}" for label, text in sections))
            if key == "thrust":
                field["label"] = "目标推力（设计环境）"
            elif key == "ambient_pressure":
                field["label"] = "环境压力（绝压）"
            elif key == "target_isp":
                field.update(label="比冲初值（流量初始化）",
                             inline_help="仅用于估算推进剂总质量流量初值，不作为比冲性能要求；最终比冲由模型计算。")
            field["label"] = field["label"].replace("燃料泵", "甲烷泵").replace("燃料贮箱", "甲烷贮箱")
            if key.startswith(("turb_ox_", "ox_turbine_")) or key == "turbine_efficiency_ox":
                if "富氧侧" not in field["label"]:
                    field["label"] = field["label"].replace("氧侧", "富氧侧")
            elif key.startswith(("turb_fuel_", "fuel_turbine_")) or key == "turbine_efficiency_fuel":
                field["label"] = field["label"].replace("燃料侧／共轴", "富燃侧").replace("燃料侧", "富燃侧")
            field["full_label"] = field["label"]
            # These conditions describe inactive historic layouts, not additional
            # architecture choices in the specialized FFSC editor.
            for operator in ("all", "any"):
                conditions = field.get("visibility", {}).get(operator)
                if conditions:
                    field["visibility"][operator] = [c for c in conditions if c["key"] != "gg_layout"]
    return groups


def _design_workflow_groups(legacy_groups, *, separate_constraints=True):
    """Separate physical definitions from task numerics, retaining case keys.

    Conditional preburner fields retain one schema definition; the editor projects
    their active role into initialization without copying their persisted values.
    """
    groups = deepcopy(legacy_groups)
    definition = next(group for group in groups if group["id"] == "cycle")
    groups = [group for group in groups if group["id"] != "cycle"]
    initials = []
    for group in groups:
        moved = [f for f in group["fields"]
                 if f.get("role") == "initial_guess" and not f.get("roles_by_mode")]
        initials.extend(dict(f, initialization_origin=group["id"]) for f in moved)
        group["fields"] = [f for f in group["fields"] if f not in moved]
        group.pop("primary_initials", None)
        if moved or any("initial_guess" in f.get("roles_by_mode", {}).values() or
                        f.get("role") == "initial_guess" for f in group["fields"]):
            group["initialization_link"] = group["id"] != "preburner_design"
        if group["id"] == "design":
            group.update(title="发动机定义与设计工况", engine_definition=True)
            group["fields"] = definition["fields"] + group["fields"]
            group["notes_intro"] = "本组规定设计目标与工况。比冲由模型计算；用于流量初始化的比冲估计在“总体设计点计算 → 初始化”中设置。"
        elif group["id"] == "cooling":
            group.update(title="冷却与循环耦合模型", requires_review=True,
                         description="总体阶段用概略型面、冷却范围与参考壁温估算热负荷，再由详细通道模型校核实际壁温与压降。外部指定热量用于已有边界数据或敏感性研究。切换模型保留各自输入与部件草案。",
                         advanced_title="模型依据", advanced_help="模型选择随本次输入版本保存。")
            for field in group["fields"]:
                if field["key"] == "cooling_bartz_mode":
                    field.update(label="冷却模型", type="select", read_only=False, advanced=False,
                                 help="概略模型在参考壁温假设下计算热流分布并积分吸热功率，反馈循环与冷却出口焓；不判断冷却能力。详细模型联立燃气、通道与壁面求解。已有方案保留原模型，需手动切换并重新计算。",
                                 choices=[{"value": 2, "label": "概略热负荷 · 由工况与受热面估算"},
                                          {"value": 1, "label": "部件耦合 · 详细通道热工与水力计算"},
                                          {"value": 0, "label": "外部指定 · 给定吸热功率与压降"}])
                elif field["key"] in ("cooling_heat_load", "cooling_dp_pa"):
                    field["advanced"] = False
                    if field["key"] == "cooling_heat_load":
                        field.update(label="外部指定吸热功率", help="仅外部指定模式生效。用于试验、外部热分析给定的热功率或敏感性研究；90 MW 是历史示例，不是发动机通用设计要求。概略及详细模式均忽略此值。")
                elif field["key"].startswith(("cooling_reference_", "cooling_estimated_", "cooling_estimate_")):
                    field["advanced"] = False
                field["full_label"] = field["label"]
        elif group["id"] == "solver":
            group.update(title="求解设置", numerical_configuration=True,
                         summary="本次设计点的数值方法、收敛准则与离散分辨率。配置随方案保存，并写入计算任务。",
                         section_notes=True, sections=[
                             {"id": "method", "title": "非线性求解方法", "keys": ["nonlinear_solver"]},
                             {"id": "acceptance", "title": "数值收敛准则", "keys": ["steady_ox_power_tolerance_W", "steady_fuel_power_tolerance_W"],
                              "description": "以下为轴功率方程的绝对残差容差。工程允许偏差与约束另行校核；完整内置准则见工程视图中的任务摘要。"},
                             {"id": "discretization", "title": "数值离散", "keys": ["cooling_grid_points"],
                              "description": "节点数控制冷却沿程分辨率。通道数量和尺寸在部件设置中定义；加密后需比较结果变化。"},
                         ])
            for name in ("initials_inline", "initials_title", "initials_help"):
                group.pop(name, None)
            for field in group["fields"]:
                field["advanced"] = False
    order = ["target_isp", "chamber_temp_guess", "chamber_gamma", "molar_mass"]
    initials.sort(key=lambda f: order.index(f["key"]) if f["key"] in order else len(order))
    for field in initials:
        field["label"] = field["label"].replace("启动估计", "迭代初值")
        field["full_label"] = field["label"]
    groups.append({"id": "initialization", "title": "初始化", "cycles": ["ffsc"],
                   "propellants": ["lox_methane"], "compact": True, "section_notes": True,
                   "numerical_configuration": True, "collect_initial_guesses": True,
                   "summary": "使用当前方案保存的迭代初值。它们影响求解路径与收敛，不作为最终状态或设计约束；瞬态物理初始条件在各自任务中定义。",
                   "fields": initials, "sections": [
                       {"id": "flow", "title": "总流量初始化", "keys": ["target_isp"],
                        "description": "用设计环境下的比冲估计构造流量初猜，最终比冲从计算结果读取。"},
                       {"id": "gas", "title": "燃气状态迭代初值", "keys": ["chamber_temp_guess", "chamber_gamma", "molar_mass"],
                        "description": "用于首轮冷却与喉部尺寸估算，后续由热化学及耦合计算更新。"},
                       {"id": "preburner", "title": "预燃室与循环迭代初值", "keys": [
                           "ox_preburner_mr_target", "fuel_preburner_mr_target", "ox_preburner_pressure_ratio",
                           "fuel_preburner_pressure_ratio", "fuel_split_to_ox_preburner", "ox_split_to_ox_preburner"],
                        "description": "仅显示当前模式中的迭代初值。指定混合比时，该混合比在预燃室热力匹配条件中作为设计条件设置。"},
                   ]})
    # A performance-level area ratio has one owner; the full contour is a
    # component design. Both existing model paths keep the same EngineInputs.
    contour = next(group for group in groups if group["id"] == "chamber_contour")
    performance = [field for field in contour["fields"] if field["key"] == "expansion_ratio"]
    contour["fields"] = [field for field in contour["fields"] if field["key"] != "expansion_ratio"]
    if performance:
        groups.append({"id": "nozzle_performance", "title": "喷管总体膨胀条件",
                        "cycles": ["ffsc"], "propellants": ["lox_methane"], "compact": True, "requires_review": True,
                       "summary": "总体性能采用的喷管面积比。具体内型面在部件设计中定义，并引用此处同一个面积比。",
                       "fields": performance})
    # One geometric definition is shared by overall heat estimation and later
    # component work. Component pages link to it; they never own a second copy.
    coarse_keys = {"chamber_contraction_ratio", "chamber_l_star_m", "chamber_conv_half_angle_deg",
        "nozzle_bell_fraction", "nozzle_contour_method", "nozzle_initial_angle_deg", "nozzle_exit_angle_deg",
        "cooling_cover_eps", "cooling_gas_emissivity"}
    coarse_fields = []
    for group in groups:
        coarse_fields.extend(f for f in group["fields"] if f["key"] in coarse_keys)
        group["fields"] = [f for f in group["fields"] if f["key"] not in coarse_keys]
        if group["id"] in ("chamber_contour", "chamber_cooling_layout"):
            group["shared_geometry_group"] = "thermal_geometry"
    for field in coarse_fields:
        field["advanced"] = field["key"] in {"nozzle_contour_method", "nozzle_initial_angle_deg", "nozzle_exit_angle_deg", "chamber_conv_half_angle_deg", "cooling_gas_emissivity"}
        if field.get("visibility", {}).get("all"):
            field["visibility"]["all"] = [r for r in field["visibility"]["all"] if r["key"] != "cooling_bartz_mode"]
    groups.append({"id": "thermal_geometry", "title": "概略型面与冷却范围", "cycles": ["ffsc"],
        "propellants": ["lox_methane"], "requires_review": True, "fields": coarse_fields,
        "summary": "用概略几何确定受热面积；总体与部件阶段共同引用这一份型面。",
        "description": "推力、室压和混合比不能唯一确定受热面积。本页给出相对尺寸、轮廓形状与冷却覆盖范围；喉部面积随设计点匹配更新，受热面积由同一轮廓计算。通道宽深和材料在部件阶段定义。",
        "advanced_title": "轮廓与辐射假设", "advanced_help": "这些参数影响受热面积和局部热流；示例值需要结合方案依据核对。"})
    if separate_constraints:
        preburner = next(group for group in groups if group["id"] == "preburner_design")
        limits = next(section for section in preburner["sections"] if section["id"] == "limits")
        limit_keys = set(limits["keys"])
        limit_fields = [field for field in preburner["fields"] if field["key"] in limit_keys]
        preburner["fields"] = [field for field in preburner["fields"] if field["key"] not in limit_keys]
        preburner["sections"] = [section for section in preburner["sections"] if section["id"] != "limits"]
        preburner["summary"] = "规定预燃室在总体循环中的设计工作条件；温度或氧燃质量比参与循环匹配。"
        limits["description"] = (
            "按项目需要启用出口平均燃气温度上限。未设置时仍可进行总体循环匹配，该项工程校核为未评估。"
            "启用后，输入检查会拒绝目标温度高于上限的矛盾设置；计算后检查是否超限。"
            "上限不参与循环方程求解，不会自动调整工作点，也不代表壁面或涡轮金属允许温度。")
        groups.append({"id": "engineering_constraints", "title": "工程限值与校核", "cycles": ["ffsc"],
                       "propellants": ["lox_methane"], "compact": True, "section_notes": True,
                       "optional_configuration": True, "requires_review": False,
                       "summary": "登记方案采用的工程限值与依据。当前提供两侧预燃室出口燃气温度校核；无须为首次循环匹配额外设定上限。",
                       "fields": limit_fields, "sections": [limits]})
    from webapp.engineering_workflow import COMPONENTS
    for group in groups:
        owner = next((c for c in COMPONENTS if group["id"] in c["groups"]), None)
        group["workflow_stage"] = "components" if owner else "assessment" if group.get("optional_configuration") else "concept"
        if owner:
            group["component_owner"] = owner["id"]
            group["design_dependency"] = ("sizing" if owner["id"] in ("pumps", "turbines")
                                          else "cooling" if owner["id"] in ("chamber", "cooling") else "cycle")
            group["component_result_views"] = owner["views"]
            if owner["id"] == "cooling":
                # Component geometry can be prepared while the overall model
                # still uses budgets. Keep table/scalar and material choices;
                # only remove the old condition that hid all detailed inputs.
                for field in group["fields"]:
                    visibility = field.get("visibility")
                    if visibility and "all" in visibility:
                        visibility["all"] = [rule for rule in visibility["all"]
                                             if rule.get("key") != "cooling_bartz_mode"]
    workflow_order = [
        "design", "inlet", "component_efficiencies", "chamber_chemistry", "cooling", "thermal_geometry",
        "preburner_design", "pressure_ffsc", "nozzle_performance", "pumps", "turbines", "chamber_contour",
        "chamber_cooling_layout", "chamber_channels", "wall_thermal", "valve_configuration",
        "solver", "initialization", "engineering_constraints",
    ]
    positions = {group_id: index for index, group_id in enumerate(workflow_order)}
    return sorted(groups, key=lambda group: positions.get(group["id"], len(positions)))


def design_ui_schema():
    """Return fresh JSON data; all defaults and units retain desktop semantics."""
    defaults = platform_defaults()
    groups = []
    for group_id, (title, fields) in zip(GROUP_IDS, PARAM_GROUPS, strict=True):
        if group_id is None:
            continue
        cycles = _cycles(title)
        groups.append({
            "id": group_id, "title": _title(title), "full_title": title, "cycles": cycles,
            "fields": [_field(key, label, unit, defaults, cycles) for key, label, unit in fields if key in defaults],
        })
        if group_id == "inlet":
            groups[-1]["fields"].extend(_field(key, label, unit, defaults, cycles)
                                        for key, label, unit in SUPPLY_FIELDS)
        if group_id == "inlet":
            present = {field["key"] for field in groups[-1]["fields"]}
            for key, label in (("gas_eos", "燃气状态方程"), ("gas_species_policy", "燃气物种范围"),
                               ("hot_side_thermo_method", "热侧热力学计算方法")):
                if key in defaults and key not in present:
                    groups[-1]["fields"].append(_field(key, label, "", defaults, cycles))
        if group_id == "pressure_ffsc":
            branch_labels = {"ox_to_opb": "氧化剂至富氧预燃室", "fuel_to_opb": "燃料至富氧预燃室",
                             "ox_to_fpb": "氧化剂至富燃预燃室", "fuel_to_fpb": "燃料至富燃预燃室"}
            for branch, keys in BRANCH_BUDGET_FIELDS.items():
                for kind, key in keys.items():
                    label = "管路及全开阀压降比" if kind == "line" else "喷注器压降比"
                    field = _field(key, f"{branch_labels[branch]}：{label}", "—", defaults, cycles)
                    field.update(nullable=True, fallback_key="valve_line_dp_ratio" if kind == "line"
                                 else "preburner_injector_dp_ratio")
                    groups[-1]["fields"].append(field)
        if group_id == "preburners":
            for side, label in (("ox", "富氧"), ("fuel", "富燃")):
                groups[-1]["fields"].extend([
                    _field(f"{side}_preburner_temp_limit_status", f"{label}预燃室温度限值依据状态", "", defaults, cycles),
                    _field(f"{side}_preburner_temp_limit_basis", f"{label}预燃室限值来源与适用工况", "", defaults, cycles),
                ])
    for group_id, title, keys in CHAMBER_GROUPS:
        cycles = ["ffsc"]
        groups.append({
            "id": group_id, "title": title, "full_title": title, "cycles": cycles,
            "fields": [_field(key, CHAMBER_INPUTS[key][0], CHAMBER_INPUTS[key][1],
                               defaults, cycles, CHAMBER_INPUTS[key][2]) for key in keys],
        })
    groups.append(_wall_thermal_group(defaults))
    if "valve_configuration_json" in defaults:
        valve_field = _field("valve_configuration_json", "三阀定义、安装位置与特性", "", defaults, ["ffsc"])
        valve_field.update(custom_editor="valve_configuration", role="design_input", role_label="设计输入",
                           valve_catalog=valve_configuration_catalog(),
                           help="MFV 主燃料阀、OPFV 富氧预燃室燃料阀、FPOV 富燃预燃室氧化剂阀。拓扑、特性版本与设计条件随方案和计算记录保存。")
        groups.append({"id": "valve_configuration", "title": "阀门与流路", "full_title": "阀门与流路",
                       "cycles": ["ffsc"], "fields": [valve_field],
                       "description": "阀门用于通断、分配和调节推进剂流量；本阶段初选 MFV、OPFV、FPOV 三只调节阀的有效面积与开度。启用时按当前主室压的 1% 给出最小压降初值，实际压差和可行性由网络复核。"})
    groups.append({
        "id": "advanced", "title": "其他设计参数", "full_title": "其他设计参数",
        "cycles": ["ffsc"],
        "fields": [_field(key, label, unit, defaults, ["ffsc"])
                   for key, label, unit in EXTRA_FIELDS],
    })
    groups = _ffsc_system_presentation(groups)
    legacy_system_groups = _system_groups(groups)
    from lre_workbench.solvers.controls import design_solver_controls
    from webapp.engineering_workflow import workflow_catalog
    supported = lambda key: not key.startswith('gg_') and key != 'gas_generator'
    return {
        "version": 21, "defaults": defaults, "groups": groups,
        "analysis_parameter_roles": analysis_parameter_roles(),
        "parameter_role_labels": dict(ROLE_LABELS),
        "propellant_models": [model | {"supported_cycles": ("ffsc",)}
                              for model in propellant_models() if model["id"] == "lox_methane"],
        "methane_thermo_datasets": methane_dataset_catalog(),
        "system_groups": _design_workflow_groups(legacy_system_groups),
        "design_workflow_version": 4,
        "engineering_workflow": workflow_catalog(),
        "solver_controls": design_solver_controls(),
        "review_legacy_groups": [g for g in legacy_system_groups if g["id"] in ("design", "preburner_design")],
        "review_previous_groups": [g for g in _design_workflow_groups(legacy_system_groups, separate_constraints=False)
                                   if g["id"] in ("preburner_design", "initialization")],
        "scope": {"id": "engine_system", "label": "液氧甲烷 FFSC · 系统设计与运行仿真",
                  "description": "专用于液氧甲烷全流量补燃循环发动机的概念设计与运行研究；热态扰动及预冷、点火、关机等模型分别说明边界，尚未形成经验证的整机全过程仿真。已有记录保留用于追溯。"},
        "overview_groups": {"ffsc": _overview(OVERVIEW_GROUPS)},
        "component_labels": {k:v for k,v in COMPONENT_LABELS.items() if supported(k)},
        "key_labels": {k:v for k,v in KEY_LABELS.items() if supported(k)},
        "state_labels": {k:v for k,v in STATE_LABELS.items() if supported(k)},
        "component_groups": [{"title": title, "subtitle": subtitle.replace('预燃室/燃气发生器', '预燃室').replace('；GG 模式含开式排气喷管', ''), "keys": [k for k in keys if supported(k)]}
                             for title, subtitle, keys in COMPONENT_GROUPS if any(supported(k) for k in keys)],
    }
