"""Presentation and screening for a turbine scenario, separate from saved results.

No thermodynamic state is relabelled or recomputed here.  Scenario inputs are
explicit design choices; they never change a cycle record or its fingerprints.
"""
from copy import deepcopy
import hashlib
import json
import math


WORKFLOW = [
    {"id": "boundary", "title": "1 · 确认循环边界", "note": "分别固定富氧、富燃侧的流量、转速、入口总态及出口压力口径；明确入口位于首排静叶前还是转子前。", "url": "https://manual.cfturbo.com/en/global_setup.html"},
    {"id": "gas", "title": "2 · 建立各自燃气", "note": "使用本记录的完整质量组成和温区物性；核对气体模型、比热、焓降和功率，不用液氧/液态甲烷或空气代替预燃燃气。", "url": "https://manual.cfturbo.com/en/gas-mixtures.html"},
    {"id": "dimensions", "title": "3 · 初选级负荷与主尺寸", "note": "比较级数、中径、速比、流量系数和反动度；本页方案只作独立均径初选，CFturbo 内继续求解总静态与环道。", "url": "https://manual.cfturbo.com/en/md_dimensions_axturbine.html"},
    {"id": "blades", "title": "4 · 设计静叶与动叶", "note": "核对角度基准与展向速度分布，选择叶片数、弦长、厚度、喉部与间隙；Zweifel 候选不代替叶型设计或动静干涉分析。", "url": "https://manual.cfturbo.com/en/bladeprop.html"},
    {"id": "verify", "title": "5 · 校核并回传", "note": "核对流量、功率、出口压力、损失、壅塞、强度与振动。保存外部工程版本及验证结果，再更新总体设计假设。", "url": "https://manual.cfturbo.com/en/md_parameter_axturbine.html"},
]

PARAMETER_MAP = [
    {"group": "全局边界", "parameter": "燃气质量流量 / 转速", "unit": "kg/s / rpm", "cfturbo": "Global setup · Mass flow / Revolutions", "source": "同一保存循环记录", "note": "分别对应各自预燃室和轴系，不采用同轴主泵的液体流量。"},
    {"group": "全局边界", "parameter": "入口总压 / 总温", "unit": "Pa / K", "cfturbo": "Global setup · Inlet conditions", "source": "显式总态接口约定", "note": "历史节点 p/T 未定义总静态时保持缺项；在总体参数确认口径并运行新记录。"},
    {"group": "全局边界", "parameter": "总总压比 / 总静压比", "unit": "—", "cfturbo": "Global setup · Pressure ratio πtt / πts", "source": "一致的入口/出口边界", "note": "πtt=pt,in/pt,out；πts=pt,in/pout。当前不从概念几何倒推出口静压。"},
    {"group": "燃气", "parameter": "组成、R、cp(T)、γ、输运性质", "unit": "质量分数 / SI", "cfturbo": "Fluids · Gas mixtures / Gas model", "source": "本记录燃气与物性表", "note": "表为冻结组成的保存采样；高压非理想性和外部气体模型须另行核对。"},
    {"group": "功率", "parameter": "实际比功、气体功率、轴功率", "unit": "J/kg / W", "cfturbo": "Main dimensions · Power / Efficiency", "source": "循环需求与损失预算", "note": "ṁΔhs 是等熵参考功率；机械损失须独立分配，不把整机效率复制给每排叶片。"},
    {"group": "级方案", "parameter": "级数 / 各级比功份额", "unit": "级 / —", "cfturbo": "Main dimensions · Multi stage options", "source": "本页显式方案", "note": "各级实际功之和闭合整台需求；份额不是压比，不对总压比直接开方。"},
    {"group": "主尺寸", "parameter": "中径、轮毂比、速比、流量系数", "unit": "m / —", "cfturbo": "Axial turbine · Parameters / Dimensions", "source": "尺寸初选与后续静态计算", "note": "本页保持恒中径恒轴速假设，未求静态密度、叶高或喉部面积。"},
    {"group": "叶排", "parameter": "绝对/相对流角与切向速度", "unit": "° / m/s", "cfturbo": "Blade properties · cu, cm / Blade angles", "source": "本页均径运动学解", "note": "本页角度自轴向量起，切向正向随转动；先转换 CFturbo 角度基准。流角不等于金属角。"},
    {"group": "叶排", "parameter": "静/动叶数、轴向弦长、节距、稠度", "unit": "片 / m / —", "cfturbo": "Blade properties · Number of blades", "source": "Zweifel 载荷初选", "note": "整数候选仅是二维经验初选，尚未确认可压缩性、端壁损失与模态避振。"},
    {"group": "详细设计", "parameter": "展向扭转、厚度、金属角、间隙", "unit": "° / m", "cfturbo": "Meridional contour / Mean lines / Blade profiles", "source": "CFturbo 叶型设计与验证", "note": "本页不生成缺少依据的叶片实体；交接 JSON/CSV 不是原生 .cft 工程。"},
]


def _dict(value):
    return value if isinstance(value, dict) else {}


def _number(value, positive=False):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and (not positive or value > 0) else None


def _field(key, label, value, unit, source, note="", status="saved"):
    return {"key": key, "label": label, "value": value, "unit": unit,
            "source": source, "note": note, "status": status if value is not None else "missing"}


def _outputs(result, side):
    calculation = _dict(_dict(result).get("calculation"))
    component = _dict(_dict(calculation.get("components")).get(side + "_preburner_turbine"))
    return _dict(component.get("outputs")), _dict(calculation.get("inputs"))


def turbine_workspace_view(result, design):
    """Attach this beside the saved handoff, never inside its hashed machines."""
    workspace = {"schema_version": 1, "workflow": deepcopy(WORKFLOW),
                 "parameter_map": deepcopy(PARAMETER_MAP), "turbines": {}}
    for side in ("ox", "fuel"):
        machine = _dict(_dict(design.get("turbines")).get(side))
        if not machine:
            continue
        point = _dict(machine.get("design_point"))
        outputs, inputs = _outputs(result, side)
        raw = "calculation.components." + side + "_preburner_turbine.outputs."
        seeds = {}

        def seed(key, label, value, unit, source, note="", status="saved"):
            seeds[key] = _field(key, label, value, unit, source, note, status)

        seed("stages", "候选级数", _number(point.get("stages"), True), "级", raw + "n_stages_recommended", "保存的候选，不代表气动校核已完成。")
        diameter = _number(point.get("mean_diameter_m"), True)
        seed("mean_diameter_m", "平均直径", diameter, "m", raw + "D_mean_m", "作为可编辑起点，不改写原记录。")
        phi_key = f"turb_{side}_meanline_flow_coefficient"
        phi = _number(inputs.get(phi_key), True)
        seed("flow_coefficient", "轴向流量系数 ca/U", phi if phi is not None else 0.6, "—",
             "calculation.inputs." + phi_key if phi is not None else "workspace.initial_assumption",
             "输入约束；不代表循环已求得轴向速度。", "saved" if phi is not None else "assumed")
        seed("reaction", "动叶静焓降 / 级欧拉比功", _number(point.get("reaction_degree_target")), "—", raw + "reaction_degree", "重复级条件下才能同时解释为整级反动度。")
        seed("work_split", "级功分配", "equal", "—", "workspace.initial_assumption", "起始选择为等实际比功，可改为自定义比例。", "assumed")
        seed("work_fractions", "自定义实际比功份额", [], "—", "workspace.initial_assumption", "自定义时逐级填入，和必须为 1。", "assumed")
        seed("inlet_swirl_m_s", "首级静叶入口切向速度", 0., "m/s", "workspace.initial_assumption", "起始假设为无旋入口；后续静叶入口继承前级转子出口旋流。", "assumed")
        for row in ("stator", "rotor"):
            label = "静叶" if row == "stator" else "动叶"
            ratio_key = f"{side}_turbine_{row}_axial_chord_ratio"
            ratio = _number(inputs.get(ratio_key), True)
            saved_ratio = ratio is not None
            ratio = ratio if saved_ratio else 0.1
            seed(row + "_axial_chord_m", label + "轴向弦长", _number(diameter * ratio, True) if diameter is not None else None, "m",
                 "calculation.inputs." + ratio_key if saved_ratio else "workspace.initial_assumption",
                 "由起始中径和弦长比取得的可编辑假设；不是已设计叶型。", "assumed")
            seed(row + "_zweifel", label + "Zweifel 系数", 0.9, "—", "workspace.initial_assumption",
                 "仅作二维经验初选起点；尚未校核本工况可压缩流适用性。", "assumed")
        engineering = [
            _field("mean_speed_m_s", "保存中径圆周速度", _number(outputs.get("U_mean_actual_ms"), True), "m/s", raw + "U_mean_actual_ms"),
            _field("yield_strength_mpa", "保存温度下材料屈服强度初值", _number(outputs.get("rotor_sigma_yield_T_MPa"), True), "MPa", raw + "rotor_sigma_yield_T_MPa", "当前简化模型采用入口气温，不等于已求得金属温度。"),
            _field("centrifugal_stress_mpa", "保存离心应力简化值 ρU²", _number(outputs.get("rotor_sigma_centrif_MPa"), True), "MPa", raw + "rotor_sigma_centrif_MPa", "未使用真实轮盘、叶根和叶片截面。"),
            _field("safety_factor", "保存简化强度比", _number(outputs.get("rotor_safety_factor"), True), "—", raw + "rotor_safety_factor", "材料屈服强度 / 简化离心应力；不代表整机或部件结构认证。"),
        ]
        sf = _number(outputs.get("rotor_safety_factor"), True)
        checks = [{"id": "structural_screening", "label": "简化强度筛查", "status": "review" if sf is not None else "missing",
                   "note": (f"本记录简化强度比为 {sf:.4g}，" + ("低于 1，需要重新审查尺寸与材料初选。" if sf < 1 else "仍需按真实几何和金属温度进行强度及振动校核。")) if sf is not None else "未保存材料与离心应力数据；不能给出结构通过结论。"},
                  {"id": "aerodynamic_scope", "label": "气动设计范围", "status": "review", "note": "均径初选闭合功和速度关系；静态通流面积、马赫数、壅塞、损失与详细叶型仍待设计。"}]
        workspace["turbines"][side] = {"seeds": seeds, "engineering_fields": engineering,
            "engineering_checks": checks, "source": deepcopy(_dict(machine.get("source"))) | {"design_sha256": machine.get("design_sha256")}}
    return workspace


def preliminary_result(result, design, side, scenario):
    """Server-owned record + user-owned assumptions; no client-supplied result."""
    if side not in ("ox", "fuel"):
        raise ValueError("请选择富氧或富燃涡轮。")
    machine = _dict(_dict(design.get("turbines")).get(side))
    if not machine or not machine.get("available") or machine.get("status") in ("conflict", "unavailable"):
        raise ValueError("来源涡轮记录不可用或存在冲突，请先核对来源。")
    from webapp.turbine_preliminary import compute_turbine_preliminary
    answer = compute_turbine_preliminary(machine, scenario)
    answer["source"] = deepcopy(_dict(answer.get("source"))) | deepcopy(_dict(machine.get("source"))) | {
        "design_sha256": machine.get("design_sha256"), "source_sha256": design.get("source_sha256"), "turbine_key": side,
        "kind": "saved_record_with_explicit_local_scenario"}
    outputs, _ = _outputs(result, side)
    baseline_u = _number(outputs.get("U_mean_actual_ms"), True)
    baseline_sigma = _number(outputs.get("rotor_sigma_centrif_MPa"), True)
    strength = _number(outputs.get("rotor_sigma_yield_T_MPa"), True)
    candidate_u = _number(_dict(answer.get("summary")).get("u_m_s"), True)
    sigma, sf = None, None
    try:
        if all(x is not None for x in (baseline_u, baseline_sigma, candidate_u)):
            sigma = _number(baseline_sigma * (candidate_u / baseline_u)**2, True)
        if strength is not None and sigma is not None:
            sf = _number(strength / sigma, True)
    except (OverflowError, ZeroDivisionError):
        sigma, sf = None, None
    answer["engineering_screening"] = {"method": "saved_rho_u_squared_scaling_v1",
        "fields": [_field("centrifugal_stress_mpa", "方案离心应力简化值", sigma, "MPa", "saved_stress × (U / U_saved)²", "仅缩放原记录 ρU² 模型，未求解新几何应力。", "derived"),
                   _field("safety_factor", "方案简化强度比", sf, "—", "saved_yield_strength / scenario_stress", "沿用保存材料及温度假设。", "derived")],
        "checks": [{"id": "scenario_structure", "label": "简化强度需复核", "status": "review" if sf is not None else "missing",
                    "note": (f"简化强度比 {sf:.4g}；" + ("低于 1。" if sf < 1 else "不构成结构通过结论。") + "需真实叶盘几何、材料温度、振动及疲劳校核。") if sf is not None else "缺少保存材料或应力依据，未计算方案强度比。"}]}
    identity = {"source": answer["source"], "scenario": answer["scenario"], "method": answer.get("method")}
    answer["scenario_sha256"] = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    payload = json.dumps({k: v for k, v in answer.items() if k != "calculation_sha256"},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    answer["calculation_sha256"] = hashlib.sha256(payload.encode()).hexdigest() if answer.get("status") == "preliminary" else None
    return answer
