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
    ("chamber_contour", "推力室·型面与分区", (
        "chamber_contraction_ratio", "chamber_l_star_m", "chamber_conv_half_angle_deg",
        "nozzle_bell_fraction", "cooling_cover_eps")),
    ("chamber_channels", "推力室·冷却通道", (
        "cooling_channel_auto_size", "cooling_target_velocity_ms", "cooling_n_channels",
        "cooling_channel_width_m", "cooling_channel_height_m", "cooling_wall_thickness_m",
        "cooling_htc_enhancement", "cooling_channel_rel_roughness",
        "cooling_channel_k_minor", "cooling_wall_temp_limit_K")),
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
    included = set(GROUP_IDS[:10]) | {"pumps", "solver", "chamber_contour", "chamber_channels"}
    excluded = {"cooling_marching_enable", "cooling_dp_auto", "coolant_cp", "pump_tip_speed_limit_ms"}
    result = []
    for original in groups:
        if original["id"] not in included:
            continue
        group = deepcopy(original)
        group["fields"] = [f for f in group["fields"] if f["key"] not in excluded]
        if group["id"] == "solver":
            group["fields"] = [f for f in group["fields"] if f["key"] in ("nonlinear_solver", "cooling_grid_points")]
            group["title"] = "数值求解"
            group["description"] = "两种方法使用相同物理方程与残差验收判据。TRF 用于对照；求解器停止后仍检查全部启用的迭代层。"
        if group["id"] == "cycle":
            group["description"] = "循环按涡轮排气去向区分开式与闭式；轴系布局另行描述泵与涡轮的机械连接。本模型的双轴 GG 采用燃气并联分流、共用入口及背压假设。"
        elif group["id"] == "preburner_design":
            group["title"] = "预燃室设计方式与目标"
            group["description"] = "指定温度时反求混合比与分流；指定混合比时计算温度并检查运行上限。初值仅影响迭代起点，计算结果在右侧显示。"
            group["fields"].insert(0, preburner_mode_field())
        elif group["id"] == "preburners":
            group["description"] = "运行温度上限是方案验收条件，不代表材料寿命已验证。pPB/pc 与真正的涡轮进出口压比不同。"
        elif group["id"] == "thermo":
            group["description"] = "以下为准一维冷却与循环热反馈的迭代起点，不是最终燃气物性，也不是化学焓参考态。预算模式不显示。"
        elif group["id"] == "inlet":
            group["description"] = "温度从贮箱侧给定；FFSC 与 GG 均计算增压泵焓升、短管压降后的主泵入口状态。效率为模型假设；主室损失模型单独选择，历史温升折减仅为兼容保留。"
        elif group["id"] == "cooling":
            group["title"] = "再生冷却·系统热平衡"
            group["description"] = "准一维模式沿同一几何求压力与焓，并反馈热侧和主循环；端口、总量与曲线同源。Bartz 型热侧关联的曲率半径取规范轮廓的上游喉部圆弧，关联式适用性仍需校核。预算模式给定 Q 与 Δp，没有沿程曲线。"
            for field in group["fields"]:
                if field["key"] == "cooling_bartz_mode":
                    field.update(label="冷却计算方式", type="select", choices=[
                        {"value": 1, "label": "统一准一维：Bartz 型关联与压力·焓守恒"},
                        {"value": 0, "label": "给定 Q 与 Δp 预算（无沿程曲线）"}])
        elif group["id"] == "pumps":
            group["title"] = "涡轮泵·概念选型与转速设定"
            group["description"] = "指定或按比转速估算转速；尚未依据泵 / 涡轮特性图匹配工作点。压头系数仅保留于完整参数中的概念几何估算，相关诊断待统一。"
        elif group["id"] == "chamber_contour":
            group["title"] = "冷却系统几何·型面与覆盖"
            group["description"] = "准一维热侧、冷却通道长度与面积使用同一规范内轮廓；Bartz 型关联读取该轮廓的上游喉部曲率半径。冷却覆盖按出口面积比指定，结果记录实际起止位置。这里提供系统级几何，部件详细流动与结构另行分析。"
        elif group["id"] == "chamber_channels":
            group["title"] = "冷却系统几何·通道与壁面"
            group["description"] = "额定设计点可按入口流速估算通道深度；固定硬件节流沿用设计点几何。压降由流量、密度及截面积沿程求解，不能单独指定另一套水力长度。"
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
    for group_id, title, keys in CHAMBER_GROUPS:
        cycles = ["ffsc", "gg"]
        groups.append({
            "id": group_id, "title": title, "full_title": title, "cycles": cycles,
            "fields": [_field(key, CHAMBER_INPUTS[key][0], CHAMBER_INPUTS[key][1],
                               defaults, cycles, CHAMBER_INPUTS[key][2]) for key in keys],
        })
    groups.append({
        "id": "advanced", "title": "其他设计参数", "full_title": "其他设计参数",
        "cycles": ["ffsc", "gg"],
        "fields": [_field(key, label, unit, defaults, ["ffsc", "gg"])
                   for key, label, unit in EXTRA_FIELDS],
    })
    return {
        "version": 7, "defaults": defaults, "groups": groups,
        "parameter_role_labels": dict(ROLE_LABELS),
        "propellant_models": propellant_models(),
        "system_groups": _system_groups(groups),
        "scope": {"id": "engine_system", "label": "系统设计 · 运行仿真",
                  "description": "支持稳态循环匹配与适用范围明确的瞬态研究；热态扰动及预冷、点火、关机等模型分别说明边界，尚未形成经验证的整机全过程仿真。完整方案参数保留用于兼容与追溯。"},
        "overview_groups": {"ffsc": _overview(OVERVIEW_GROUPS), "gg": _overview(OVERVIEW_GROUPS_GG)},
        "component_labels": dict(COMPONENT_LABELS), "key_labels": dict(KEY_LABELS),
        "state_labels": dict(STATE_LABELS),
        "component_groups": [{"title": title, "subtitle": subtitle, "keys": keys}
                             for title, subtitle, keys in COMPONENT_GROUPS],
    }
