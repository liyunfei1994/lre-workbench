"""Parameter usage shared by task contracts and both analysis pages.

This catalog describes ownership, not an implemented operating-point solver.
Conditional controls require an explicit actuator/boundary definition before
they can become accepted inputs. No design value is promoted into a control.
"""
from copy import deepcopy


def _row(key, label, concept_roles, concept, fixed_roles, fixed):
    return {"id": key, "label": label,
            "concept_design": {"roles": concept_roles, "description": concept},
            "fixed_hardware_steady": {"roles": fixed_roles, "description": fixed}}


_ROWS = [
    _row("preburner_temperature", "预燃室出口温度", ["design_target", "computed_output"],
         "温度目标模式：给定出口温度，反求氧燃质量比。指定氧燃质量比模式：计算出口温度。",
         ["computed_output", "constraint"],
         "计算输出。温度上限单独作为约束。"),
    _row("preburner_distribution", "预燃室氧燃质量比与交叉分流", ["design_input", "initial_guess", "computed_output"],
         "指定配比模式：给定氧燃质量比。温度目标模式：输入配比仅作反求初值。历史自由分流模式：交叉分流参数为初值。",
         ["computed_output"],
         "计算输出，由支路质量流量确定。来源计算结果可用作初值。"),
    _row("pump_outlet_pressure", "泵出口压力", ["computed_requirement", "design_input"],
         "主泵：按压力预算计算出口压力需求。前置增压泵：出口压力为给定设计值。",
         ["computed_output"],
         "计算输出，由泵特性与系统阻力匹配确定。"),
    _row("shaft_speed", "泵与涡轮转速", ["design_choice"],
         "初选假设。给定时本轮保持不变，也可按目标比转速估算；同轴涡轮共用。不是求解器初值或特性匹配得到的运行转速。",
         ["computed_output", "conditional_external_boundary"],
         "轴系匹配的求解量。当前不支持给定轴转速边界。"),
    _row("pump_stages", "泵级数", ["design_choice"],
         "本轮固定采用的级数方案，用于单级扬程与尺寸初估；不自动选级。",
         ["hardware_definition"],
         "属于泵结构定义；初选级数本身不能证明泵性能已验证。"),
    _row("pump_sizing_assumptions", "泵几何初选系数", ["design_choice"],
         "用于代表级流道尺寸初估，本轮按给定值使用；不是数值初值，也不反馈预测泵效率。",
         ["design_only"],
         "仅用于设计初估。固定硬件匹配需要实际几何和部件特性，不能用这些系数代替。"),
    _row("turbine_stages", "涡轮级数", ["design_choice", "computed_size"],
         "采用给定初选级数，或按现有速比规则估算；不是优化结果，也不证明逐级气动匹配。",
         ["hardware_definition"],
         "属于涡轮结构定义，须读取已确定的实际级数；设计输入的 0 仅表示按规则估算，不能作为硬件级数。"),
    _row("turbine_sizing_assumptions", "涡轮速比与几何初选假设", ["design_choice"],
         "用于尺寸初估及可选均径运动学校核，不是数值初值；不自动预测效率或反馈循环。",
         ["design_only"],
         "仅用于设计初估。固定硬件匹配需要实际几何和部件特性，不能用这些初选系数代替。"),
    _row("valve_hardware", "阀门尺寸、流量特性与开度限位", ["design_choice", "computed_requirement"],
         "全流量补燃循环：计算支路所需附加节流压降，供阀门选型。",
         ["hardware_definition"],
         "硬件参数，随硬件定义固定。"),
    _row("valve_opening", "阀门开度", ["design_assumption"],
         "设计点开度需依据阀门特性确定。当前设计模型未定义开度。",
         ["control_setting", "computed_control_state"],
         "运行设定或控制配平的调节量，受开度限位约束。当前未配置可用阀门特性，暂不接受开度输入。"),
    _row("chamber_pressure", "主燃烧室压力", ["design_input"],
         "给定设计输入。",
         ["computed_output"],
         "工作点计算输出。"),
    _row("thrust", "推力", ["design_target"],
         "给定环境压力下的设计目标。",
         ["computed_output", "conditional_control_target"],
         "工作点计算输出；可在控制配平任务中设为目标。"),
    _row("overall_mixture_ratio", "整机氧燃质量比", ["design_input"],
         "给定整机推进剂供给质量流量比。",
         ["computed_output", "conditional_control_target"],
         "按整机供给流量计算；可单独设为附加配平目标。"),
    _row("geometry", "喷管、冷却通道及喷注器几何", ["design_choice", "computed_size"],
         "设计输入或定尺寸结果，按部件及所选模型确定。",
         ["hardware_definition"],
         "硬件参数，工作点计算采用冻结的几何尺寸。"),
]

_FIELDS = {
    "preburner_temperature": {f"{s}_preburner_{suffix}" for s in ("ox", "fuel")
                             for suffix in ("temp_target", "temp_limit", "temp_limit_status", "temp_limit_basis")},
    "preburner_distribution": {"ox_preburner_mr_target", "fuel_preburner_mr_target",
                               "fuel_split_to_ox_preburner", "ox_split_to_ox_preburner"},
    "pump_outlet_pressure": {"inlet_pressure"},
    "shaft_speed": {"lox_pump_speed_rpm_override", "fuel_pump_speed_rpm_override"},
    "pump_stages": {f"{side}_pump_n_stages" for side in ("lox", "fuel")},
    "pump_sizing_assumptions": {f"{side}_pump_{suffix}" for side in ("lox", "fuel") for suffix in (
        "head_coefficient_psi", "inlet_flow_coefficient", "outlet_flow_coefficient", "eye_hub_ratio", "axial_length_ratio")},
    "turbine_stages": {f"turb_{side}_n_stages_user" for side in ("ox", "fuel")},
    "turbine_sizing_assumptions": {"turbine_meanline_mode"} | {
        f"turb_{side}_{suffix}" for side in ("ox", "fuel") for suffix in (
            "u_over_c_target", "reaction_degree", "meanline_flow_coefficient", "stage_work_fractions")
    } | {f"{side}_turbine_{suffix}" for side in ("ox", "fuel") for suffix in (
        "inlet_flow_coefficient", "outlet_flow_coefficient", "stator_axial_chord_ratio",
        "rotor_axial_chord_ratio", "row_gap_ratio")},
    "valve_hardware": {"valve_line_dp_ratio"},
    "chamber_pressure": {"pc"}, "thrust": {"thrust"}, "overall_mixture_ratio": {"of_ratio"},
    "geometry": {"expansion_ratio", "chamber_contraction_ratio", "chamber_l_star_m",
                 "chamber_conv_half_angle_deg", "nozzle_contour_method", "nozzle_bell_fraction",
                 "nozzle_initial_angle_deg", "nozzle_exit_angle_deg", "cooling_cover_eps",
                 "cooling_n_channels", "cooling_channel_width_m", "cooling_channel_height_m",
                 "cooling_wall_thickness_m", "cooling_channel_table_json", "cooling_channel_auto_size",
                 "cooling_sizing_reference", "cooling_target_velocity_ms"},
}


def analysis_parameter_roles():
    return {"version": 1, "rows": deepcopy(_ROWS), "principles": [
        "初选假设在本轮按给定值使用，校核后可修改；数值初值只用于启动迭代，最终结果应从计算输出读取。",
        "固定硬件稳态与控制配平目前仅支持任务配置，尚未开放发动机工作点求解。",
    ]}


def parameter_analysis_roles(key):
    # A quantity-level table is not a license to reinterpret every related
    # input as that quantity. Limits and legacy sizing/budget inputs have their
    # own identities, even when displayed alongside the same component.
    if "_preburner_temp_limit" in key:
        description = "温度上限用于约束检查；状态与依据随限值保存。"
        return {mode: {"roles": ["constraint"], "description": description}
                for mode in ("concept_design", "fixed_hardware_steady")}
    if key in {"turb_ox_u_max_ms", "turb_fuel_u_max_ms"}:
        return {mode: {"roles": ["constraint"], "description":
                       "节圆速度的参考校核上限，不是材料许用叶尖速度，也不替代结构校核。"}
                for mode in ("concept_design", "fixed_hardware_steady")}
    if key in {"turbine_boundary_basis", "ox_turbine_geometry_state_basis", "fuel_turbine_geometry_state_basis"}:
        return {mode: {"roles": ["model_assumption"], "description":
                       "状态量口径的声明；不进行总静转换，也不更改已有循环数值。部件匹配前须核对实际接口定义。"}
                for mode in ("concept_design", "fixed_hardware_steady")}
    if key == "valve_line_dp_ratio" or (key.startswith(("ox_to_", "fuel_to_")) and
                                       key.endswith(("_line_dp_ratio", "_injector_dp_ratio"))):
        return {
            "concept_design": {"roles": ["model_assumption"], "description": "设计支路压降预算，用于压力分配。"},
            "fixed_hardware_steady": {"roles": ["design_only"], "description":
                "仅用于设计阶段。固定硬件稳态需采用部件流量特性或冻结的阻力定义。"},
        }
    if key in {"cooling_channel_auto_size", "cooling_sizing_reference", "cooling_target_velocity_ms"}:
        return {
            "concept_design": {"roles": ["design_choice"], "description": "冷却通道的定尺寸设定。"},
            "fixed_hardware_steady": {"roles": ["design_only"], "description":
                "仅用于设计阶段。固定硬件任务采用保存的通道几何。"},
        }
    if key in {"lox_pump_specific_speed_target", "fuel_pump_specific_speed_target"}:
        return {
            "concept_design": {"roles": ["design_choice"], "description": "仅按目标比转速估算方式采用，不是数值初值。"},
            "fixed_hardware_steady": {"roles": ["design_only"], "description":
                "仅用于设计初选。固定硬件工作点的转速需由部件特性与轴系匹配确定。"},
        }
    for row in _ROWS:
        if key in _FIELDS.get(row["id"], set()):
            return {mode: deepcopy(row[mode]) for mode in ("concept_design", "fixed_hardware_steady")}
    return {}
