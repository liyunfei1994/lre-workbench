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
from lre_workbench.domain.metadata import (
    _MATERIAL_FIELD_META, CHAMBER_INPUTS,
    NONLINEAR_SOLVER_CHOICES, COMBUSTION_LOSS_CHOICES, THERMO_DATASET_CHOICES,
    TURBINE_BOUNDARY_CHOICES, TURBINE_MEANLINE_CHOICES, TURBINE_GEOMETRY_STATE_CHOICES,
    DISPLAY_UNIT_MAP, PARAM_GROUPS,
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
    included = (set(GROUP_IDS[:10]) - {None, "pressure"}) | {"pumps", "turbines", "solver", "chamber_contour", "chamber_channels", "wall_thermal"}
    excluded = {"cooling_marching_enable", "cooling_dp_auto", "coolant_cp", "pump_tip_speed_limit_ms",
                "methane_thermo_dataset"}
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
            group["title"] = "发动机架构"
            group["description"] = "平台专用于液氧甲烷全流量补燃循环。富氧涡轮驱动液氧泵，富燃涡轮驱动甲烷泵；两路涡轮排气进入主燃烧室。"
            for field in group["fields"]:
                fixed = {"cycle_type": ("ffsc", "全流量补燃循环（FFSC）"),
                         "propellant_pair": ("lox_methane", "液氧／甲烷")}.get(field["key"])
                if fixed:
                    field.update(read_only=True, choices=[{"value": fixed[0], "label": fixed[1]}],
                                 help="本平台采用的发动机架构。")
        elif group["id"] == "design":
            group["description"] = "目标推力与下方环境压力构成同一设计工况；室压和总氧燃比为指定输入。主喷管面积比统一在“推力室与喷管型面”中设置，推力、比冲和流量的计算值在结果区显示。"
        elif group["id"] == "preburner_design":
            group["title"] = "预燃室热力设计"
            group["compact"] = True
            group["section_notes"] = True
            group["summary"] = "按指定出口燃气温度或氧燃质量比，与供给系统及两侧轴系联立求解设计点。"
            group["initials_help"] = "用于启动迭代，通常沿用方案值；收敛困难时再调整。"
            previous = next(g for g in groups if g["id"] == "preburners")
            group["fields"].extend(deepcopy(previous["fields"]))
            group["fields"].insert(0, preburner_mode_field())
            group["sections"] = [
                {"id": "targets", "title": "设计条件",
                 "description": "指定出口燃气温度时反求氧燃质量比；指定氧燃质量比时计算出口燃气温度。",
                 "keys": ["preburner_design_mode", "ox_preburner_temp_target", "fuel_preburner_temp_target",
                          "ox_preburner_mr_target", "fuel_preburner_mr_target"]},
                {"id": "interfaces", "title": "系统接口", "context": "preburner-interfaces"},
                {"id": "results", "title": "匹配结果", "context": "preburner-results"},
                {"id": "limits", "title": "设计约束（可选）", "collapsed": True,
                 "description": "出口燃气温度上限用于设计点校核。未设置时不作该项判定；设计暂定值用于方案筛选，依据已登记表示已记录约束来源与适用工况。该限值不代表壁面或涡轮金属的允许温度。",
                 "field_groups": [{"title": f"{label}预燃室",
                                   "keys": [f"{side}_preburner_temp_limit{suffix}" for suffix in ("_status", "", "_basis")]}
                                  for side, label in (("ox", "富氧"), ("fuel", "富燃"))],
                 "keys": [f"{side}_preburner_temp_limit{suffix}" for side in ("ox", "fuel")
                          for suffix in ("_status", "", "_basis")]},
            ]
        elif group["id"] == "inlet":
            # Separate interface conditions, component assumptions and chemistry.
            # Keep the inlet ID so existing navigation state still resolves.
            group["title"] = "推进剂供给边界"
            group["compact"] = True
            group["summary"] = "当前供给方案：贮箱出口 → 前置增压泵（外部供功）→ 短管 → 主泵。"
            efficiency_keys = {"pump_efficiency_ox", "pump_efficiency_fuel",
                               "turbine_efficiency_ox", "turbine_efficiency_fuel"}
            combustion_keys = {"combustion_loss_model", "combustion_efficiency"}
            efficiency = [f for f in group["fields"] if f["key"] in efficiency_keys]
            combustion = [f for f in group["fields"] if f["key"] in combustion_keys]
            group["fields"] = [f for f in group["fields"]
                               if f["key"] not in efficiency_keys | combustion_keys]
            group["sections"] = [
                {"id": "supply", "title": "贮箱出口液体边界",
                 "keys": ["fuel_inlet_temp", "ox_inlet_temp", "tank_pressure_pa"]},
                {"id": "boost", "title": "前置增压与管路预算",
                 "keys": ["inlet_pressure", "boost_pump_efficiency", "feed_line_dp_pa"]},
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
                field["advanced"] = True
            result.append({"id": "chamber_chemistry", "title": "主燃烧室热力学模型",
                           "cycles": group["cycles"], "compact": True,
                            "summary": "计算主燃烧室燃气温度、平衡组分及热力学性质。",
                            "advanced_title": "历史兼容设置",
                            "advanced_help": "用于复现旧方案的经验温升修正。修改后需重新确认参数并计算，已保存记录保留原模型。",
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
    help_text = {
        "thrust": "在本栏给定环境压力下匹配的主喷管推力目标。命中目标不构成独立性能验证。",
        "of_ratio": "氧燃质量比 = 液氧质量流量 ÷ 甲烷质量流量（无量纲），不是体积比、摩尔比或当量比。此项指定整机总供给配比；两侧预燃室和主燃烧室的局部配比分别计算。3.6 为示例输入。",
        "expansion_ratio": "主喷管出口面积 Ae / 喉部面积 At；循环、推力室轮廓和冷却使用同一个输入。它不同于燃烧室收缩比及冷却终点面积比。34.34 是示例几何假设。",
        "valve_line_dp_ratio": "基础管路及全开阀的设计压降与主燃烧室压力之比。未单独设置的支路沿用本值；供给压力匹配得到的附加调节压降单列。",
    }
    for group in groups:
        group.update(cycles=["ffsc"], propellants=["lox_methane"])
        for field in group["fields"]:
            key = field["key"]
            field.update(cycles=["ffsc"], propellants=["lox_methane"])
            if key in help_text:
                field["help"] = help_text[key]
            field["label"] = field["label"].replace("燃料泵", "甲烷泵").replace("燃料贮箱", "甲烷贮箱")
            if key.startswith(("turb_ox_", "ox_turbine_")) or key == "turbine_efficiency_ox":
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
                    _field(f"{side}_preburner_temp_limit_status", f"{label}预燃室温度约束状态", "", defaults, cycles),
                    _field(f"{side}_preburner_temp_limit_basis", f"{label}预燃室约束来源与适用工况", "", defaults, cycles),
                ])
    for group_id, title, keys in CHAMBER_GROUPS:
        cycles = ["ffsc"]
        groups.append({
            "id": group_id, "title": title, "full_title": title, "cycles": cycles,
            "fields": [_field(key, CHAMBER_INPUTS[key][0], CHAMBER_INPUTS[key][1],
                               defaults, cycles, CHAMBER_INPUTS[key][2]) for key in keys],
        })
    groups.append(_wall_thermal_group(defaults))
    groups.append({
        "id": "advanced", "title": "其他设计参数", "full_title": "其他设计参数",
        "cycles": ["ffsc"],
        "fields": [_field(key, label, unit, defaults, ["ffsc"])
                   for key, label, unit in EXTRA_FIELDS],
    })
    groups = _ffsc_system_presentation(groups)
    supported = lambda key: not key.startswith('gg_') and key != 'gas_generator'
    return {
        "version": 18, "defaults": defaults, "groups": groups,
        "analysis_parameter_roles": analysis_parameter_roles(),
        "parameter_role_labels": dict(ROLE_LABELS),
        "propellant_models": [model | {"supported_cycles": ("ffsc",)}
                              for model in propellant_models() if model["id"] == "lox_methane"],
        "methane_thermo_datasets": methane_dataset_catalog(),
        "system_groups": _system_groups(groups),
        "scope": {"id": "engine_system", "label": "液氧甲烷 FFSC · 系统设计与运行仿真",
                  "description": "专用于液氧甲烷全流量补燃循环发动机的概念设计与运行研究；热态扰动及预冷、点火、关机等模型分别说明边界，尚未形成经验证的整机全过程仿真。已有记录保留用于追溯。"},
        "overview_groups": {"ffsc": _overview(OVERVIEW_GROUPS)},
        "component_labels": {k:v for k,v in COMPONENT_LABELS.items() if supported(k)},
        "key_labels": {k:v for k,v in KEY_LABELS.items() if supported(k)},
        "state_labels": {k:v for k,v in STATE_LABELS.items() if supported(k)},
        "component_groups": [{"title": title, "subtitle": subtitle.replace('预燃室/燃气发生器', '预燃室').replace('；GG 模式含开式排气喷管', ''), "keys": [k for k in keys if supported(k)]}
                             for title, subtitle, keys in COMPONENT_GROUPS if any(supported(k) for k in keys)],
    }
