"""JSON presentation contract backed by the desktop's shared declarations.

Values remain in EngineInputs' SI units. A numeric field's display value is
``value * factor``; clients divide by that factor before submitting a project.
This module deliberately does not import Qt or the desktop window.
"""
from dataclasses import asdict
from copy import deepcopy

from webapp.solver import EngineInputs
from materials import list_materials_for_part
from propellants import propellant_models
from parameter_contract import parameter_metadata, preburner_mode_field, ROLE_LABELS
from wall_materials import list_wall_datasets
from thermo_data import methane_dataset_catalog
from ui_metadata import (
    _MATERIAL_FIELD_META, CHAMBER_INPUTS, CYCLE_TYPE_CHOICES, GG_LAYOUT_CHOICES, PROPELLANT_CHOICES,
    NONLINEAR_SOLVER_CHOICES, COMBUSTION_LOSS_CHOICES, THERMO_DATASET_CHOICES,
    DISPLAY_UNIT_MAP, FFSC_ONLY_GROUPS, GG_ONLY_GROUPS, PARAM_GROUPS,
    OVERVIEW_GROUPS, OVERVIEW_GROUPS_GG, COMPONENT_LABELS, COMPONENT_GROUPS,
    KEY_LABELS, STATE_LABELS,
)

GROUP_IDS = (
    "cycle", "design", "inlet", "thermo", "preburners", "preburner_design",
    "gas_generator", "pressure_ffsc", "pressure", "cooling", "solver",
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
    ("cooling_wall_conductivity", "内衬导热系数", "W/(m·K)"),
    ("lox_pump_impeller_material", "液氧泵叶轮材料", ""),
    ("fuel_pump_impeller_material", "燃料泵叶轮材料", ""),
    ("cooling_channel_table_json", "冷却通道沿程分布表（JSON）", ""),
)

WALL_THERMAL_FIELDS = (
    ("wall_thermal_dataset_id", "内衬热物性数据集", ""),
    ("wall_manufacturing_state", "方案内衬制造状态", ""),
    ("wall_material_zones_json", "轴向材料分区（JSON）", ""),
)


def _wall_thermal_group(defaults):
    catalog = [dataset.metadata() for dataset in list_wall_datasets()]
    cycles = ["ffsc", "gg"]
    fields = [_field(key, label, unit, defaults, cycles)
              for key, label, unit in WALL_THERMAL_FIELDS]
    for field in fields:
        field.update(role="model_assumption", role_label="模型假设",
                     visibility={"all": [{"key": "cooling_bartz_mode", "op": "gt", "value": 0}]})
    fields[0].update(type="select", choices=[{"value": item["dataset_id"], "label": item["label"]}
                                            for item in catalog], dataset_catalog=catalog,
                     empty_choice_label="请选择有来源的热物性数据集（历史材料无对应曲线）",
                     help="按资料对应的材料与制造状态选择；数据集直接提供稳态壁面导热率，不使用未注明来源的铜合金通用倍率。")
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
            ("chamber_inner_wall_material", "chamber_inner_wall", "历史结构内衬配置",
             "保留用于结构概念估算；当前冷却导热率由上方热物性数据集决定，二者不会自动同步。"),
            ("chamber_outer_wall_material", "chamber_outer_wall", "外套材料配置",
             "作为结构配置保留；未把外套厚度串入燃气至冷却剂的热阻，也未验证承载能力。"))]
    return {"id": "wall_thermal", "title": "壁面材料与热物性", "full_title": "壁面材料与热物性",
            "cycles": cycles, "fields": fields,
            "description": "固体壁面数据单独管理。稳态使用内衬导热率，数据温区、方案壁温约束和结构资格分别检查。",
            "advanced_title": "轴向材料分区",
            "advanced_help": "分区是物理设计输入，不是数值初值。完整方案保留原始定义，结果保存实际采用的逐单元数据。",
            "configuration_context": structural_context}


def _title(text):
    return text.split(" / ", 1)[0]


def _cycles(title):
    if title in FFSC_ONLY_GROUPS:
        return ["ffsc"]
    if title in GG_ONLY_GROUPS:
        return ["gg"]
    return ["ffsc", "gg"]


def _field(key, label, unit, defaults, cycles, tooltip=""):
    default = defaults[key]
    factor, unit = DISPLAY_UNIT_MAP.get(key, (1, unit))
    result = {
        "key": key, "label": _title(label), "full_label": label,
        "unit": unit, "factor": factor, "default": default,
        "type": "text" if isinstance(default, str) else "int" if isinstance(default, int) else "number",
        "cycles": ["gg"] if key == "gg_layout" else cycles,
    }
    if tooltip:
        result["tooltip"] = tooltip
    if key == "coolant_cp":
        # The surrogate branch uses CoolProp enthalpy, not the legacy fixed cp.
        result["propellants"] = ["lox_methane"]
    choices = None
    if key == "cycle_type":
        choices = CYCLE_TYPE_CHOICES
    elif key == "propellant_pair":
        choices = PROPELLANT_CHOICES
    elif key == "gg_layout":
        choices = GG_LAYOUT_CHOICES
    elif key == "nonlinear_solver":
        choices = NONLINEAR_SOLVER_CHOICES
    elif key == "combustion_loss_model":
        choices = COMBUSTION_LOSS_CHOICES
    elif key == "methane_thermo_dataset":
        choices = THERMO_DATASET_CHOICES
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
    included = set(GROUP_IDS[:10]) | {"pumps", "solver", "chamber_contour", "chamber_channels", "wall_thermal"}
    excluded = {"cooling_marching_enable", "cooling_dp_auto", "coolant_cp", "pump_tip_speed_limit_ms",
                "methane_thermo_dataset"}
    result = []
    for original in groups:
        if original["id"] not in included:
            continue
        if original["id"] == "preburners":
            # All preburner inputs have one system-editor home. Keep the full
            # desktop/case groups below unchanged for compatibility.
            continue
        group = deepcopy(original)
        group["fields"] = [f for f in group["fields"] if f["key"] not in excluded]
        # Historical Q and pressure budgets have one compatibility editor home.
        group["fields"] = [f for f in group["fields"] if f["key"] != "cooling_dp_pa"]
        if group["id"] == "solver":
            group["fields"] = [f for f in group["fields"] if f["key"] in ("nonlinear_solver", "cooling_grid_points")]
            group["title"] = "数值求解"
            group["description"] = "两种方法使用相同物理方程与残差验收判据。TRF 用于对照；求解器停止后仍检查全部启用的迭代层。"
        if group["id"] == "cycle":
            group["description"] = "循环按涡轮排气去向区分开式与闭式；轴系布局另行描述泵与涡轮的机械连接。本模型的双轴 GG 采用燃气并联分流、共用入口及背压假设。"
        elif group["id"] == "design":
            group["description"] = "目标推力与下方环境压力构成同一设计工况；室压和总氧燃比为指定输入。主喷管面积比统一在“推力室型面与覆盖”中设置，推力、比冲和流量的计算值在结果区显示。"
        elif group["id"] == "preburner_design":
            group["title"] = "预燃室设计"
            group["description"] = "温度或 O/F 用于概念设计点匹配；运行上限单独登记依据。固定硬件的实际温度还取决于流量、阀门及部件特性。"
            previous = next(g for g in groups if g["id"] == "preburners")
            group["fields"].extend(deepcopy(previous["fields"]))
            group["fields"].insert(0, preburner_mode_field())
            group["sections"] = [
                {"id": "targets", "title": "设计方式与目标",
                 "description": "仅显示当前生效的设计输入；O/F 为该预燃室的氧化剂 / 燃料质量流量比。",
                 "keys": ["preburner_design_mode", "ox_preburner_temp_target", "fuel_preburner_temp_target",
                          "ox_preburner_mr_target", "fuel_preburner_mr_target"]},
                {"id": "limits", "title": "运行温度约束",
                 "description": "设计目标不等于允许上限。没有限值或依据时，不把该项判为已通过。",
                 "keys": [f"{side}_preburner_temp_limit{suffix}" for side in ("ox", "fuel")
                          for suffix in ("_status", "", "_basis")]},
            ]
        elif group["id"] == "thermo":
            group["description"] = "以下为准一维冷却与循环热反馈的迭代起点，不是最终燃气物性，也不是化学焓参考态。预算模式不显示。"
        elif group["id"] == "inlet":
            group["description"] = "温度从贮箱侧给定；FFSC 与 GG 均计算增压泵焓升、短管压降后的主泵入口状态。效率为模型假设；主室损失模型单独选择，历史温升折减仅为兼容保留。"
        elif group["id"] == "cooling":
            group["fields"].extend(deepcopy(f) for source in groups for f in source["fields"] if f["key"] == "cooling_dp_pa")
            group["title"] = "再生冷却模型"
            group["description"] = "沿同一型面和通道几何联立压力与焓，吸热、压降及出口状态反馈主循环。计算范围、材料和模型依据见结果区“再生冷却”。"
            group["advanced_title"] = "历史预算参数"
            group["advanced_help"] = "仅当前方案仍采用给定预算时生效；保留原值用于复现，不生成沿程解。"
            for field in group["fields"]:
                if field["key"] == "cooling_bartz_mode":
                    field.update(label="当前冷却模型", read_only=True)
                elif field["key"] in ("cooling_heat_load", "cooling_dp_pa"):
                    field.update(advanced=True)
        elif group["id"] == "pumps":
            group["title"] = "泵转速与几何初选"
            group["description"] = "由保存设计点的流量、入口密度、压差和转速初选代表级离心叶轮流道。以下系数是可编辑的假设；运行设计点后，在“泵初步几何”查看尺寸、二维和三维图并导出坐标。"
            group["sections"] = [
                {"id": side, "title": title, "keys": [f"{side}_pump_{suffix}" for suffix in (
                    "speed_rpm_override", "specific_speed_target", "n_stages", "head_coefficient_psi",
                    "inlet_flow_coefficient", "outlet_flow_coefficient", "eye_hub_ratio", "axial_length_ratio") ]}
                for side, title in (("lox", "液氧泵"), ("fuel", "燃料泵"))
            ]
        elif group["id"] == "chamber_contour":
            group["description"] = "抛物线钟形喷管采用相切圆弧和二次 Bézier 曲线，属于系统级工程近似；指定端点角度不代表已完成推力优化。循环、热侧与冷却共用同一轮廓，尺寸和图示见左侧“推力室与喷管几何”。旧方案保留历史角度方法，切换后保存为新版本。"
        elif group["id"] == "chamber_channels":
            group["title"] = "冷却通道与设计约束"
            group["description"] = "尺寸为可编辑的起始方案，300 条通道和给定深度均不是推荐最优值。运行设计点后，在结果区“再生冷却”比较候选几何；采纳后重新计算整机。固定硬件变工况沿用已定几何。"
            group["sections"] = [
                {"id": "geometry", "title": "起始通道几何", "keys": ["cooling_channel_auto_size", "cooling_sizing_reference", "cooling_target_velocity_ms", "cooling_n_channels", "cooling_channel_width_m", "cooling_channel_height_m", "cooling_wall_thickness_m"]},
                {"id": "models", "title": "换热与阻力假设", "keys": ["cooling_htc_enhancement", "cooling_channel_rel_roughness", "cooling_channel_k_minor"]},
                {"id": "limits", "title": "方案壁温约束", "description": "实际计算壁温、材料热物性的数据温区和方案允许温度分别检查。", "keys": ["cooling_wall_temp_limit_status", "cooling_wall_temp_limit_K", "cooling_wall_temp_limit_basis"]},
            ]
            group["geometry_source_key"] = "cooling_channel_table_json"
        result.append(group)
    return result


def design_ui_schema():
    """Return fresh JSON data; all defaults and units retain desktop semantics."""
    defaults = asdict(EngineInputs())
    groups = []
    for group_id, (title, fields) in zip(GROUP_IDS, PARAM_GROUPS, strict=True):
        cycles = _cycles(title)
        groups.append({
            "id": group_id, "title": _title(title), "full_title": title, "cycles": cycles,
            "fields": [_field(key, label, unit, defaults, cycles) for key, label, unit in fields],
        })
        if group_id == "preburners":
            for side, label in (("ox", "富氧"), ("fuel", "富燃")):
                groups[-1]["fields"].extend([
                    _field(f"{side}_preburner_temp_limit_status", f"{label}侧温度约束状态", "", defaults, cycles),
                    _field(f"{side}_preburner_temp_limit_basis", f"{label}侧限值依据与适用条件", "", defaults, cycles),
                ])
    for group_id, title, keys in CHAMBER_GROUPS:
        cycles = ["ffsc", "gg"]
        groups.append({
            "id": group_id, "title": title, "full_title": title, "cycles": cycles,
            "fields": [_field(key, CHAMBER_INPUTS[key][0], CHAMBER_INPUTS[key][1],
                               defaults, cycles, CHAMBER_INPUTS[key][2]) for key in keys],
        })
    groups.append(_wall_thermal_group(defaults))
    groups.append({
        "id": "advanced", "title": "其他设计参数", "full_title": "其他设计参数",
        "cycles": ["ffsc", "gg"],
        "fields": [_field(key, label, unit, defaults, ["ffsc", "gg"])
                   for key, label, unit in EXTRA_FIELDS],
    })
    return {
        "version": 15, "defaults": defaults, "groups": groups,
        "parameter_role_labels": dict(ROLE_LABELS),
        "propellant_models": propellant_models(),
        "methane_thermo_datasets": methane_dataset_catalog(),
        "system_groups": _system_groups(groups),
        "scope": {"id": "engine_system", "label": "系统设计 · 运行仿真",
                  "description": "支持稳态循环匹配与适用范围明确的瞬态研究；热态扰动及预冷、点火、关机等模型分别说明边界，尚未形成经验证的整机全过程仿真。完整方案参数保留用于兼容与追溯。"},
        "overview_groups": {"ffsc": _overview(OVERVIEW_GROUPS), "gg": _overview(OVERVIEW_GROUPS_GG)},
        "component_labels": dict(COMPONENT_LABELS), "key_labels": dict(KEY_LABELS),
        "state_labels": dict(STATE_LABELS),
        "component_groups": [{"title": title, "subtitle": subtitle, "keys": keys}
                             for title, subtitle, keys in COMPONENT_GROUPS],
    }
