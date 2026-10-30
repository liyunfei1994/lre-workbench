// System-design views of the shared solver's serialized results. No calculation lives here.
// The small unit adapters below mirror src/gui_pyside6.py.
import {renderComponentWorkspace} from "./component-analysis.js";
import {buildFlowModel} from "./flow-model.js";
import {renderFlowWorkspace} from "./flow-analysis.js";
import {buildCycleModel} from "./cycle-model.js";
import {renderCycleWorkspace} from "./cycle-schematic.js";
import {renderConvergence, numericalDisplayState} from "./convergence.js";
import {renderSteadyClosure, closureDisplayState, pressureContractDisplayState} from "./steady-closure.js";
import {renderThermodynamicApplicability, thermoApplicabilityBadge, thermodynamicDisplayState, thermoRangeDisplayState, pureFluidDisplayState} from "./thermo-applicability.js";
import {renderCooling} from "./cooling.js";
import {renderSteadyWorkspace} from "./steady-workspace.js";
import {renderSteadyDesignSummary,renderSteadyDesignBasis} from "./steady-design.js";
// Canonical SI outputs first: diagrams, handoff and geometry must not choose
// different numeric copies. Missing SI fields can use historical unit aliases.
const COMPONENT_ALIASES = [
  ["rho_in_kgm3","rho_kgm3"],
  ["p_in_Pa","p_in_MPa","p_in_bar"], ["p_out_Pa","p_out_MPa","p_out_bar"],
  ["power_W","power_MW"], ["turbine_power_W","turbine_power_MW"],
  ["heat_load_W","heat_load_MW"], ["delta_p_Pa","delta_p_MPa"],
  ["dp_ox_Pa","dp_ox_MPa"], ["dp_fuel_Pa","dp_fuel_MPa"],
  ["injector_dp_Pa","injector_dp_MPa","injector_dp_bar"],
  ["preburner_pressure_Pa","preburner_pressure_MPa"],
  ["Cf_sl","Cf"], ["p_exit_Pa","exit_pressure_Pa"],
  ["mach_exit","exit_mach"], ["Ve_ms","exit_velocity_ms"],
];
const COMPONENT_VALUE_SCALE = {
  "power_W": 1e-06,
  "power_hydraulic_W": 1e-06,
  "turbine_power_W": 1e-06,
  "heat_load_W": 1e-06,
  "p_in_Pa": 1e-06,
  "p_out_Pa": 1e-06,
  "delta_p_Pa": 1e-06,
  "p_in_bar": 0.1,
  "p_out_bar": 0.1,
  "injector_dp_bar": 0.1,
  "P_in_Pa": 1e-06,
  "P_out_Pa": 1e-06,
  "dp_ox_Pa": 1e-06,
  "dp_fuel_Pa": 1e-06,
  "p_exit_Pa": 1e-06,
  "exit_pressure_Pa": 1e-06,
  "p_amb_Pa": 1e-06,
  "thrust_N": 0.001,
  "thrust_vac_N": 0.001
};
const COMPONENT_LABEL_OVERRIDE = {
  "p_exit_Pa": "喷管出口压力 [MPa]",
  "exit_pressure_Pa": "喷管出口压力 [MPa]",
  "p_amb_Pa": "环境压力 [MPa]",
  "thrust_N": "推力 [kN]",
  "thrust_vac_N": "真空推力 [kN]",
  "power_hydraulic_W": "不可压近似轴功率参考 [MW]",
  "delta_p_Pa": "压升 [MPa]",
  "injector_dp_bar": "预燃室喷注器压降 [MPa]",
  "Ve_ms": "喷管出口速度 [m/s]",
  "exit_velocity_ms": "喷管出口速度 [m/s]",
  "specific_work_isentropic_Jkg": "等熵比功 [J/kg]",
  "T_chamber_ideal_K": "理想燃烧温度 [K]",
  "combustion_efficiency": "历史温升折减系数（非 c* 效率） [-]",
  "mdot_chamber_kgs": "主燃烧室流量 [kg/s]",
  "Cf_vac": "真空推力系数 Cf",
  "Cf_sl": "当前环境推力系数 Cf",
  "Cf_momentum": "定比热比参考 · 动量推力系数",
  "Cf_pressure": "定比热比参考 · 压力推力系数",
  "mach_exit": "定比热比参考 · 出口马赫数",
  "flow_model": "喷管热力学模型",
  "t_in_over_limit": "入口温度超限量 [K]"
};
const COMPONENT_SPECIFIC_LABELS = {
  "gas_generator": {
    "injector_dp_ratio": "燃气发生器喷注器压降比 ΔP/p_GG [-]",
    "injector_dp_Pa": "燃气发生器喷注器压降 [MPa]",
    "injector_dp_MPa": "燃气发生器喷注器压降 [MPa]",
    "injector_dp_bar": "燃气发生器喷注器压降 [MPa]",
    "preburner_pressure_Pa": "燃气发生器压力 [MPa]",
    "preburner_pressure_MPa": "燃气发生器压力 [MPa]"
  },
  "ox_pump": {
    "p_in_Pa": "泵入口压力 [MPa]",
    "p_out_Pa": "泵出口压力 [MPa]",
    "p_in_MPa": "泵入口压力 [MPa]",
    "p_out_MPa": "泵出口压力 [MPa]",
    "p_in_bar": "泵入口压力 [MPa]",
    "p_out_bar": "泵出口压力 [MPa]",
    "T_in_K": "泵入口温度 [K]",
    "T_out_K": "泵出口温度 [K]",
    "eta": "泵效率 [-]"
  },
  "fuel_pump": {
    "p_in_Pa": "泵入口压力 [MPa]",
    "p_out_Pa": "泵出口压力 [MPa]",
    "p_in_MPa": "泵入口压力 [MPa]",
    "p_out_MPa": "泵出口压力 [MPa]",
    "p_in_bar": "泵入口压力 [MPa]",
    "p_out_bar": "泵出口压力 [MPa]",
    "T_in_K": "泵入口温度 [K]",
    "T_out_K": "泵出口温度 [K]",
    "eta": "泵效率 [-]"
  },
  "cooling_channel": {
    "T_in_K": "冷却通道入口温度 [K]",
    "T_out_K": "冷却通道出口温度 [K]",
    "delta_T_K": "冷却剂温升 [K]"
  },
  "ox_preburner_turbine": {
    "T_in_K": "涡轮入口温度 [K]",
    "T_exhaust_K": "涡轮出口温度 [K]"
  },
  "fuel_preburner_turbine": {
    "T_in_K": "涡轮入口温度 [K]",
    "T_exhaust_K": "涡轮出口温度 [K]"
  }
};
// Deliberately allow system quantities only: shared records can also contain
// detailed sizing, material audits and wall profiles. The original payload and
// diagnostics remain available in the export and log, without reinterpreting it.
const fieldSet = text => new Set(text.trim().split(/\s+/));
const PUMP_FIELDS = fieldSet(`rho_in_kgm3 rho_out_kgm3 rho_kgm3 p_in_Pa p_out_Pa
  p_in_MPa p_out_MPa p_in_bar p_out_bar delta_p_Pa delta_p_MPa power_W power_MW
  power_hydraulic_W specific_work_isentropic_Jkg T_in_K T_out_K delta_T_K
  delta_T_isentropic_K delta_T_loss_K eta head_m volume_flow_m3s nr_rpm npsh_available_m`);
const PREBURNER_FIELDS = fieldSet(`mdot_total_kgs mdot_fuel_kgs mdot_ox_kgs phi
  T_combustion_K h_in_Jkg h_combustion_Jkg cp_Jkgk gamma fuel_fraction
  injector_dp_ratio injector_dp_Pa injector_dp_MPa injector_dp_bar
  preburner_pressure_Pa preburner_pressure_MPa mixture_ratio_OF`);
const TURBINE_FIELDS = fieldSet(`p_in_Pa p_out_Pa turbine_pressure_ratio pressure_recovery_ratio
  T_in_K T_exhaust_K isentropic_drop_Jkg actual_drop_Jkg actual_temp_drop_K
  turbine_power_W turbine_power_MW eta_turbine mass_flow_kgs cp_in_Jkgk gamma_in
  molar_mass_in shaft_speed_rpm t_in_over_limit`);
const NOZZLE_FIELDS = fieldSet(`thrust_N thrust_vac_N Isp_s Isp_vac_s Ve_ms exit_velocity_ms
  c_star_ms Cf Cf_vac Cf_sl Cf_momentum Cf_pressure mach_exit exit_mach
  p_exit_Pa exit_pressure_Pa p_amb_Pa expansion_ratio T_exit_K gamma_exit flow_model`);
const SYSTEM_COMPONENT_FIELDS = {
  ox_pump:PUMP_FIELDS, fuel_pump:PUMP_FIELDS,
  ox_preburner:PREBURNER_FIELDS, fuel_preburner:PREBURNER_FIELDS, gas_generator:PREBURNER_FIELDS,
  ox_preburner_turbine:TURBINE_FIELDS, fuel_preburner_turbine:TURBINE_FIELDS,
  gg_turbine:TURBINE_FIELDS, gg_turbine_ox:TURBINE_FIELDS, gg_turbine_fuel:TURBINE_FIELDS,
  nozzle:NOZZLE_FIELDS, gg_exhaust_nozzle:NOZZLE_FIELDS,
  cooling_channel:fieldSet(`mode heat_load_W heat_load_MW delta_T_K T_in_K T_out_K
    P_in_Pa P_out_Pa delta_P_MPa delta_P_source cp_avg_J_kg_K`),
  main_chamber:fieldSet(`T_chamber_K T_chamber_ideal_K combustion_efficiency gamma
    molar_mass_kg_per_kmol cp_Jkgk h_chamber_Jkg mixture_ratio_OF mdot_chamber_kgs`),
  injector:fieldSet(`dp_ox_Pa dp_ox_MPa dp_ox_ratio dp_fuel_Pa dp_fuel_MPa dp_fuel_ratio`),
};
const SYSTEM_SUMMARY_FIELDS = fieldSet(`mdot_fuel_kg_s mdot_ox_kg_s mdot_chamber_kg_s
  fuel_split_to_ox_preburner ox_split_to_ox_preburner ox_preburner_mr phi_ox_preburner
  fuel_preburner_mr phi_fuel_preburner chamber_mixture_ratio gg_mdot_kg_s gg_fraction gg_mr gg_phi
  ox_pump_power_MW fuel_pump_power_MW ox_turbine_power_MW fuel_turbine_power_MW
  ox_side_power_balance_ratio fuel_side_power_balance_ratio gg_turbine_power_MW
  gg_turbine_power_delivered_MW gg_mech_efficiency power_balance_ratio
  gg_pressure_MPa gg_turbine_pi_t gg_turbine_exhaust_T_K thrust_gg_exhaust_N
  Isp_gg_exhaust_s Isp_main_flow_s Isp_gg_penalty_s solver_iterations solver_inner_solves
  solver_iterations_last solver_function_evaluations solver_jacobian_evaluations solver_rejected_steps tc_outer_iterations tc_outer_iterations_total tc_outer_residual_K gg_temp_outer_error_K
  solver_residual_norm solver_warning thermo_source`);
// Labels and overview grouping are provided by /api/info.ui_schema, the same
// declarations consumed by the desktop. Keep only browser unit adapters above.
let desktopSchema = {overview_groups: {ffsc: [], gg: []}, component_labels: {}, key_labels: {}, state_labels: {}};

/** Call after loading /api/info and before rendering any calculation results. */
export function configureResultSchema(schema = {}) {
  const labels = source => Object.fromEntries(Object.entries(source || {}).filter(([,value]) => typeof value === "string"));
  const groups = cycle => (Array.isArray(schema?.overview_groups?.[cycle]) ? schema.overview_groups[cycle] : [])
    .filter(group => group && typeof group.title === "string" && Array.isArray(group.fields))
    .map(group => [group.title, group.fields.filter(field => field && SYSTEM_SUMMARY_FIELDS.has(field.key))
      .map(field => [field.key, field.label || field.key, field.unit || ""])]);
  desktopSchema = {
    overview_groups: {ffsc: groups("ffsc"), gg: groups("gg")},
    component_labels: labels(schema?.component_labels),
    key_labels: labels(schema?.key_labels),
    state_labels: labels(schema?.state_labels),
  };
}

const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const number = value => !finite(value) ? "—" : (value !== 0 && (Math.abs(value) < 0.001 || Math.abs(value) >= 1e8)) ? value.toExponential(3) : value.toLocaleString("zh-CN", {maximumFractionDigits: 3});
const scaled = (value, factor) => finite(value) ? value * factor : null;
const valueText = value => value == null ? "—" : typeof value === "number" ? number(value) : typeof value === "object" ? JSON.stringify(value) : String(value);
const statusNames = {pass:"通过", fail:"未通过", unknown:"待确认", review:"待复核", queued:"排队中", running:"计算中", succeeded:"计算完成", failed:"计算失败", cancelled:"已取消", interrupted:"已中断"};
const badge = (state, label) => `<span class="badge ${["pass","succeeded"].includes(state) ? "success" : ["fail","failed"].includes(state) ? "failed" : ["review","running","queued"].includes(state) ? "running" : ""}">${escape(label || statusNames[state] || state || "待确认")}</span>`;
const table = (headings, rows) => `<div class="table-wrap"><table><thead><tr>${headings.map(h=>`<th scope="col">${escape(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
const row = values => `<tr>${values.map(value=>`<td>${escape(value)}</td>`).join("")}</tr>`;
const panel = (title, contents) => `<section class="panel"><h2>${escape(title)}</h2>${contents}</section>`;
const warningList = warnings => warnings.length ? `<ul class="warnings">${warnings.map(w=>`<li>${escape(w)}</li>`).join("")}</ul>` : '<p class="muted">本次计算未返回警告。</p>';
const allWarnings = result => [...new Set([...(result.warnings || []), ...(result.calculation?.warnings || [])])];
const resultInputs = result => result.calculation?.inputs || result.submitted_inputs || {};
// Presentation follows the immutable calculation inputs, never the active editor.
// Records created before propellant_pair existed are methane calculations.
const fuelInfo = inputs => inputs.propellant_pair === "lox_kerosene"
  ? {name:"煤油",symbol:"C₁₂H₂₆",pair:"液氧 / 煤油（正十二烷替代物）",surrogate:true}
  : {name:"甲烷",symbol:"CH₄",pair:"液氧 / 甲烷",surrogate:false};
const fuelLabel = (text, inputs) => fuelInfo(inputs).surrogate
  ? String(text ?? "").replace(/甲烷/g,"煤油").replace(/CH₄|CH4/g,"C₁₂H₂₆") : String(text ?? "");
const cycleName = result => resultInputs(result).cycle_type === "gg" ? "燃气发生器循环（GG，开式）" : "全流量分级燃烧循环（FFSC，闭式）";
const isSystemAssessment = result => result.assessment?.scope === "system";
const legacySystemLimits = result => isSystemAssessment(result) && !(result.calculation_scope?.version >= 3);
const preburnerSide = check => /^(ox|fuel)_preburner_T_combustion_K$/.exec(check?.key || "")?.[1];
function preburnerLimit(result, side, check) {
  const inputs=resultInputs(result),key=`${side}_preburner_temp_limit`;
  const row=check || result.assessment?.checks?.find(item=>item.key===`${side}_preburner_T_combustion_K`);
  const historical=!Object.hasOwn(row || {},"limit_status")&&!Object.hasOwn(inputs,`${key}_status`);
  const status=row?.limit_status ?? inputs[`${key}_status`] ?? "provisional";
  const basis=String(row?.limit_basis ?? inputs[`${key}_basis`] ?? "").trim();
  const limit=status==="unset" ? null : Object.hasOwn(row || {},"limit") ? row.limit : inputs[key];
  const valid=["unset","provisional","documented"].includes(status) && (status==="unset" || finite(limit)&&limit>0) && (status!=="documented" || basis!=="");
  return {status,basis,limit,historical,valid};
}
function preburnerLimitLabel(evidence) {
  if (!evidence.valid) return "上限设置无效（未评估）";
  if (evidence.status==="unset") return "上限未设定（未评估）";
  if (evidence.status==="documented") return `上限 ${number(evidence.limit)} K（已填写依据，未验证）`;
  return `${evidence.historical?"历史暂定":"暂定"}上限 ${number(evidence.limit)} K（待核实）`;
}
const assessmentDisplayState = result => {
  const saved = result.assessment || {};
  const checks=(saved.checks || []).map(check=>{
    const side=preburnerSide(check);
    if (!side || resultInputs(result).cycle_type==="gg") return check;
    const evidence=preburnerLimit(result,side,check);
    const status=!evidence.valid || evidence.status==="unset" ? "unknown" :
      evidence.status==="provisional" && check.status==="pass" ? "review" : check.status;
    return {...check,status,preburner_limit_evidence:{...evidence,saved_check_status:check.status}};
  });
  let display={...saved,checks};
  if(saved.status==="pass" && checks.some(check=>check.preburner_limit_evidence && check.status!=="pass")) {
    const unknown=checks.some(check=>check.preburner_limit_evidence && check.status==="unknown");
    display={...display,status:unknown?"unknown":"review",label:unknown?"系统约束未完成评估":"系统约束待核实"};
  }
  // Keep immutable historical rows; an older pass did not include the common
  // cooling profile's thermal limits and cannot stand in for today's scope.
  return legacySystemLimits(result) && saved.status === "pass"
    ? {...display,status:"unknown",label:"历史系统约束范围，需重算"} : display;
};
const scopeNotice = result => result.calculation_scope?.id === "engine_system"
  ? '<p class="fine">系统设计 · 稳态设计点，用于循环匹配与设计约束检查。</p>' + (legacySystemLimits(result) ? '<div class="banner">历史系统校核未覆盖统一冷却剖面的沿程壁温限值；保留原校核表，需重新计算后按当前范围判定。</div>' : '')
  : '<div class="banner">历史记录：保留原计算与完整工程校核，尚未按当前系统范围重新运行。下方系统量展示不会改变原校核结论。</div>';

function welcome() {
  return `<div class="analysis-stack"><section class="panel welcome-card"><div class="welcome-copy"><span class="eyebrow">DESIGN POINT / 初步设计</span><h2>从一个设计点开始</h2><p class="muted">选择参考工况、设置参数，运行后在这里查看循环匹配与系统平衡。</p><ol class="welcome-steps"><li><span>01</span><div><b>选择工况</b><p>在参数设置中选择循环与参考机型。</p></div></li><li><span>02</span><div><b>运行设计点</b><p>检查推力、轴功率平衡和收敛状态。</p></div></li><li><span>03</span><div><b>查看分析</b><p>沿左侧工作流程查看流路状态与部件性能分析结果。</p></div></li></ol><p class="fine">点击上方「运行设计点」开始计算。</p></div><svg class="engine-illustration" viewBox="0 0 400 420" role="img" aria-label="液体火箭发动机结构示意，非计算几何"><defs><pattern id="engine-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#e8edf0" stroke-width="1"/></pattern><linearGradient id="engine-metal" x1="0" x2="1"><stop stop-color="#d9e1e6"/><stop offset=".5" stop-color="#fafcfd"/><stop offset="1" stop-color="#b9c7d1"/></linearGradient></defs><rect width="400" height="420" rx="12" fill="url(#engine-grid)"/><path d="M200 30V388M62 240H338" stroke="#b9c7d1" stroke-dasharray="5 5"/><g fill="url(#engine-metal)" stroke="#566f80" stroke-width="2"><path d="M164 151H236V218Q236 238 218 252Q223 300 283 352Q200 376 117 352Q177 300 182 252Q164 238 164 218Z"/><ellipse cx="200" cy="151" rx="36" ry="10"/><ellipse cx="200" cy="352" rx="83" ry="16"/><ellipse cx="200" cy="352" rx="67" ry="10" fill="#78909f"/><rect x="151" y="137" width="98" height="16" rx="4"/><circle cx="132" cy="103" r="23"/><circle cx="267" cy="103" r="23"/><circle cx="132" cy="103" r="10"/><circle cx="267" cy="103" r="10"/><rect x="172" y="69" width="56" height="28" rx="7"/></g><g fill="none" stroke-width="7"><path d="M101 52V91H109M155 103H171V137" stroke="#7d9aaf"/><path d="M298 52V91H290M244 103H230V137M262 119V204Q262 233 236 256L256 309" stroke="#bda172"/></g><g fill="none" stroke="#b99154" stroke-width="2"><path d="M173 174V215Q174 237 191 250Q188 293 141 344M227 174V215Q226 237 209 250Q212 293 259 344"/></g><g font-family="Microsoft YaHei, sans-serif" font-size="11" fill="#7b8a97"><text x="74" y="39">液氧</text><text x="280" y="39">燃料</text><text x="200" y="405" text-anchor="middle">ENGINE CONCEPT / 结构示意</text></g></svg></section>${panel("开始之前", '<div class="metric-list"><div><span>计算范围</span><b>液氧/甲烷：FFSC / GG；液氧/煤油替代物：GG</b></div><div><span>结果用途</span><b>循环匹配与系统级初步设计</b></div><div><span>设计点要求</span><b>先检查收敛，再检查系统约束</b></div></div>')}</div>`;
}

function statusStrip(result) {
  const numerical = numericalDisplayState(result), assessment = assessmentDisplayState(result);
  const fuel=fuelInfo(resultInputs(result));
  return `${scopeNotice(result)}<div class="status-strip">${badge(numerical.status, numerical.label || "缺少收敛信息")}${thermoApplicabilityBadge(result)}${badge(assessment.status, assessment.label || (isSystemAssessment(result) ? "系统约束待确认" : "原工程约束待确认"))}<span>残差 ${number(numerical.residual)}</span><span>${escape(cycleName(result))}</span><span>${escape(fuel.pair)}</span><span>警告 ${allWarnings(result).length} 项</span></div>${fuel.surrogate ? '<p class="fine">煤油采用正十二烷单组分替代物；当前为 GG 稳态循环与给定冷却吸热量模型，尚未按实际 RP-1 燃料或厂商发动机试验标定。</p>' : ""}`;
}

function kpi(title, value, unit, hint = "") {
  return `<article class="kpi-card"><span class="kpi-title">${escape(title)}</span><div class="kpi-value">${number(value)} <small>${escape(unit)}</small></div><p class="kpi-hint">${escape(hint)}</p></article>`;
}

const SUMMARY_SYMBOLS = {
  mdot_fuel_kg_s:"ṁf", mdot_ox_kg_s:"ṁox", mdot_chamber_kg_s:"ṁcc",
  fuel_split_to_ox_preburner:"xf→OPB", ox_split_to_ox_preburner:"xox→OPB",
  ox_preburner_mr:"O/FOPB", phi_ox_preburner:"φOPB", fuel_preburner_mr:"O/FFPB", phi_fuel_preburner:"φFPB",
  chamber_mixture_ratio:"O/Fcc", gg_mdot_kg_s:"ṁGG", gg_fraction:"αGG", gg_mr:"O/FGG", gg_phi:"φGG",
  ox_pump_power_MW:"Pp,ox", fuel_pump_power_MW:"Pp,f", ox_turbine_power_MW:"Pt,ox", fuel_turbine_power_MW:"Pt,f",
  ox_side_power_balance_ratio:"Rt/p,ox", fuel_side_power_balance_ratio:"Rt/p,f",
  gg_turbine_power_MW:"Pt,GG", gg_turbine_power_delivered_MW:"Pshaft", gg_mech_efficiency:"ηm", power_balance_ratio:"Rt/p",
  gg_pressure_MPa:"pGG", gg_turbine_pi_t:"πt", gg_turbine_exhaust_T_K:"Tt,out", thrust_gg_exhaust_N:"FGG,SL",
  Isp_gg_exhaust_s:"Isp,GG", Isp_main_flow_s:"Isp,main", Isp_gg_penalty_s:"ΔIsp",
  solver_iterations:"Niter", solver_inner_solves:"Nsolve", solver_iterations_last:"Nlast",
  solver_function_evaluations:"Nf", solver_jacobian_evaluations:"NJ", solver_rejected_steps:"Nreject",
  tc_outer_iterations:"NTc", tc_outer_iterations_total:"NTc,total", tc_outer_residual_K:"rTc", gg_temp_outer_error_K:"rT,GG",
  solver_residual_norm:"‖r‖", solver_warning:"—", thermo_source:"—",
};
const FLOW_KEYS = fieldSet(`mdot_fuel_kg_s mdot_ox_kg_s mdot_chamber_kg_s
  fuel_split_to_ox_preburner ox_split_to_ox_preburner ox_preburner_mr phi_ox_preburner
  fuel_preburner_mr phi_fuel_preburner chamber_mixture_ratio gg_mdot_kg_s gg_fraction gg_mr gg_phi`);
const POWER_KEYS = fieldSet(`ox_pump_power_MW fuel_pump_power_MW ox_turbine_power_MW fuel_turbine_power_MW
  ox_side_power_balance_ratio fuel_side_power_balance_ratio gg_turbine_power_MW
  gg_turbine_power_delivered_MW gg_mech_efficiency power_balance_ratio`);
const DIAGNOSTIC_KEYS = fieldSet(`solver_iterations solver_inner_solves solver_iterations_last solver_function_evaluations solver_jacobian_evaluations solver_rejected_steps
  tc_outer_iterations tc_outer_iterations_total tc_outer_residual_K gg_temp_outer_error_K solver_residual_norm solver_warning thermo_source`);
const signed = value => !finite(value) ? "—" : `${value > 0 ? "+" : ""}${number(value)}`;
const sum = (...values) => values.every(finite) ? values.reduce((total,value)=>total+value,0) : null;

function engineeringTable(fields, summary, inputs) {
  return `<div class="table-wrap"><table class="engineering-table"><thead><tr><th scope="col">参数</th><th scope="col">符号</th><th scope="col">数值</th><th scope="col">单位</th></tr></thead><tbody>${fields.map(([key,label,unit])=>{
    const value = key === "thermo_source" && finite(summary[key]) ? (summary[key] >= .5 ? "Cantera" : "替代热力学模型") : valueText(summary[key]);
    return `<tr><td title="${escape(key)}">${escape(fuelLabel(label,inputs))}</td><td class="metric-symbol">${escape(SUMMARY_SYMBOLS[key] || "—")}</td><td class="metric-value">${escape(value)}</td><td class="metric-unit">${escape(unit && unit !== "—" ? unit : "—")}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}

/** Independent bars share a physical axis; incomplete or negative inputs never become a normalized split. */
function quantityBars(items, scale, unit, title) {
  if (!(finite(scale) && scale > 0) || items.some(item=>!finite(item.value) || item.value < 0))
    return '<p class="engineering-caption">图示所需数据缺失或超出非负流量 / 功率范围；请查看下方数值与诊断。</p>';
  const left=118, width=330, top=28, rowHeight=29, height=top+items.length*rowHeight+27;
  const ticks=[0,.25,.5,.75,1].map(t=>`<g><line class="chart-grid" x1="${left+width*t}" y1="18" x2="${left+width*t}" y2="${height-27}"/><text class="chart-tick" x="${left+width*t}" y="${height-9}" text-anchor="middle">${(scale*t).toLocaleString("en-US",{maximumSignificantDigits:3})}</text></g>`).join("");
  const bars=items.map((item,index)=>{
    const y=top+index*rowHeight;
    return `<g><text class="chart-label" x="${left-10}" y="${y+11}" text-anchor="end">${escape(item.label)}</text><rect class="chart-bar-${item.kind}" x="${left}" y="${y}" width="${width*item.value/scale}" height="15"/><text class="chart-value" x="462" y="${y+11}">${number(item.value)}</text></g>`;
  }).join("");
  const description=`${title}，单位 ${unit}。${items.map(item=>`${item.label} ${number(item.value)}`).join("；")}。横轴 0 至 ${number(scale)} ${unit}。`;
  return `<svg class="engineering-chart" viewBox="0 0 570 ${height}" role="img" aria-label="${escape(description)}"><title>${escape(title)}</title><text class="chart-tick chart-unit" x="462" y="12">${escape(unit)}</text>${ticks}${bars}</svg>`;
}

function flowVisual(summary, inputs) {
  const gg=inputs.cycle_type === "gg", fuel=fuelInfo(inputs);
  const total=summary.mdot_total_kg_s;
  const feed=[{label:"液氧供给",value:summary.mdot_ox_kg_s,kind:"ox"},{label:`${fuel.name}供给`,value:summary.mdot_fuel_kg_s,kind:"fuel"}];
  const items=gg ? [...feed,{label:"主燃烧室",value:summary.mdot_chamber_kg_s,kind:"main"},{label:"燃气发生器",value:summary.gg_mdot_kg_s,kind:"gg"}] : feed;
  const values=items.map(item=>item.value), max=finite(total) && total >= 0 && values.every(finite) ? Math.max(total,...values) : null;
  let closure=`总供给 ṁ = ${number(total)} kg/s`;
  const supply=sum(summary.mdot_ox_kg_s,summary.mdot_fuel_kg_s);
  if (finite(total) && finite(supply)) closure+=` · 供给差额（液氧 + ${fuel.name} − 总量）${signed(supply-total)} kg/s`;
  if (gg) {
    const branches=sum(summary.mdot_chamber_kg_s,summary.gg_mdot_kg_s);
    if (finite(total) && finite(branches)) closure+=` · 分流差额（主室 + GG − 总量）${signed(branches-total)} kg/s`;
  }
  return `${quantityBars(items,max,"kg/s","系统质量流量")}<p class="engineering-caption">${escape(closure)}</p><p class="engineering-caption">${gg ? "供给与分流分别比较，各条采用同一质量流量刻度。" : `图示为液氧与${fuel.name}总供给；预燃室分流比例见表。`}</p>`;
}

function powerVisual(summary, inputs, assessment) {
  const gg=inputs.cycle_type === "gg";
  const dual=gg && (inputs.gg_layout === "dual_shaft" || summary.gg_layout_dual >= .5);
  const eta=summary.gg_mech_efficiency;
  // GG gross turbine work must include the recorded mechanical efficiency.
  // FFSC balance is defined directly against turbine outputs by the current model.
  const shafts=gg && !dual ? [{label:"共轴泵组",checkKey:"power_error_percent",pump:sum(summary.ox_pump_power_MW,summary.fuel_pump_power_MW),drive:summary.gg_turbine_power_delivered_MW}]
    : [{label:"液氧侧轴系",checkKey:"ox_side_power_error_percent",pump:summary.ox_pump_power_MW,drive:gg ? (finite(eta) ? scaled(summary.ox_turbine_power_MW,eta) : null) : summary.ox_turbine_power_MW},
      {label:`${fuelInfo(inputs).name}侧轴系`,checkKey:"fuel_side_power_error_percent",pump:summary.fuel_pump_power_MW,drive:gg ? (finite(eta) ? scaled(summary.fuel_turbine_power_MW,eta) : null) : summary.fuel_turbine_power_MW}];
  const usable=shafts.flatMap(shaft=>[shaft.pump,shaft.drive]).filter(value=>finite(value) && value >= 0);
  const scale=usable.length ? Math.max(...usable) : null;
  const sections=shafts.map(shaft=>{
    const delta=finite(shaft.drive) && finite(shaft.pump) ? shaft.drive-shaft.pump : null;
    const relative=finite(delta) && shaft.pump > 0 ? delta/shaft.pump*100 : null;
    const check=(assessment?.checks || []).find(check=>check.key === shaft.checkKey && check.unit === "%"
      && check.criterion === "maximum" && finite(check.limit) && check.limit >= 0);
    // A neutral numerical-difference band is a display aid, never a replacement
    // for the recorded engineering status. 1% is the solver assessment default.
    const tolerance=check ? check.limit : 1;
    const within=finite(relative) && Math.abs(relative) <= tolerance;
    const tone=!finite(relative) ? "delta-unknown" : within ? "delta-within-band" : delta < 0 ? "delta-deficit" : "delta-surplus";
    const bandText=`色标容差 ±${number(tolerance)}%（${check ? "本记录校核" : "默认参考"}）${within ? " · 差额在容差范围内" : ""}`;
    return `<section class="shaft-balance"><div class="shaft-balance-heading"><h3>${escape(shaft.label)}</h3><span class="balance-readout ${tone}">ΔP ${signed(delta)} MW${finite(relative) ? ` (${signed(relative)}%)` : ""}</span></div>${quantityBars([{label:"泵轴功率需求",value:shaft.pump,kind:"load"},{label:gg ? "涡轮可用轴功率" : "涡轮输出",value:shaft.drive,kind:"drive"}],scale,"MW",`${shaft.label}功率比较`)}<p class="engineering-caption">${escape(bandText)}</p></section>`;
  }).join("");
  return `${sections}<p class="engineering-caption">ΔP = 涡轮${gg ? "可用轴功率" : "输出"} − 泵轴功率需求；负值表示缺口。${gg ? "可用轴功率已计入本次记录的机械效率。" : "采用本模型的涡轮输出口径。"}增压泵功率未计入主轴比较。</p>`;
}

function metricGroup(title, fields, summary, inputs, index, assessment) {
  const available = fields.filter(([key]) => Object.hasOwn(summary, key));
  if (!available.length) return "";
  const diagnostic=available.every(([key])=>DIAGNOSTIC_KEYS.has(key));
  const flow=available.some(([key])=>FLOW_KEYS.has(key)), power=available.some(([key])=>POWER_KEYS.has(key));
  const chart=flow ? flowVisual(summary,inputs) : power ? powerVisual(summary,inputs,assessment) : "";
  const header=`<span class="panel-index">${String(index+1).padStart(2,"0")}</span><h2>${escape(title)}</h2><span class="panel-caption">${diagnostic ? "求解器诊断" : "设计点结果"}</span>`;
  const values=engineeringTable(available,summary,inputs);
  const content=chart ? `${chart}<details class="steady-table-details"><summary>查看${escape(title)}数值</summary>${values}</details>` : values;
  return diagnostic ? `<details class="panel engineering-panel diagnostics-panel"><summary class="engineering-panel-header diagnostics-title">${header}</summary>${content}</details>`
    : `<section class="panel engineering-panel"><div class="engineering-panel-header">${header}</div>${content}</section>`;
}

function steadyAttention(result) {
  const assessment=assessmentDisplayState(result), numerical=numericalDisplayState(result), inputs=resultInputs(result);
  const checks=Array.isArray(assessment.checks) ? assessment.checks : [];
  const failed=checks.filter(check=>check.status === "fail"), uncertain=checks.filter(check=>!["pass","fail"].includes(check.status));
  const lines=failed.map(check=>`<li><b>${escape(fuelLabel(check.label,inputs))}</b><span>${number(check.value)} ${escape(check.unit)} · ${check.preburner_limit_evidence ? escape(preburnerLimitLabel(check.preburner_limit_evidence)) : `要求 ${check.criterion === "minimum" ? "≥" : "≤"} ${number(check.limit)} ${escape(check.unit)}`}</span></li>`);
  if(numerical.status !== "pass") lines.unshift(`<li><b>${escape(numerical.label || "数值收敛待确认")}</b><span>本次性能仅用于诊断，需检查收敛过程。</span><button type="button" data-steady-open="convergence">查看收敛诊断 →</button></li>`);
  const closure=closureDisplayState(result), pressure=pressureContractDisplayState(closure.closure?.feed_network?.pressure_contract);
  const closureFailed=closure.feed === "fail" || closure.energy === "fail" || pressure === "fail";
  if(closureFailed)
    lines.push(`<li><b>${[closure.feed === "fail" ? "供给支路" : "",closure.energy === "fail" ? "能量收支" : "",pressure === "fail" ? "压力关系" : ""].filter(Boolean).join("、")}校核未通过</b><span>查看保存的分项残差与数据来源。</span></li>`);
  if(!lines.length) lines.push(`<li>${escape(assessment.status === "pass" ? "本记录已检查的系统约束通过。" : assessment.label || "系统约束证据待确认。")}</li>`);
  if(uncertain.length) lines.push(`<li>${uncertain.length} 项约束待核实或未评估，需查看依据与设置。</li>`);
  const thermo=thermodynamicDisplayState(result), states=Array.isArray(thermo.evidence?.states) ? thermo.evidence.states : [], pure=Array.isArray(thermo.evidence?.pure_fluid_states) ? thermo.evidence.pure_fluid_states : [];
  const extrapolated=states.filter(state=>thermoRangeDisplayState(state)==="extrapolated").length;
  const invalid=pure.filter(state=>pureFluidDisplayState(state)==="invalid_or_unavailable").length;
  const missing=states.filter(state=>thermoRangeDisplayState(state)==="unavailable").length+pure.filter(state=>pureFluidDisplayState(state)==="unavailable").length;
  const evidence=[];
  if(extrapolated) evidence.push(`${extrapolated} 个燃气节点发生温区外推`);
  if(invalid) evidence.push(`${invalid} 个纯推进剂端口无效或物性查询失败`);
  if(missing) evidence.push(`${missing} 个节点的适用性证据不足`);
  if(!states.length || !pure.length) evidence.push(`${!states.length ? "燃气温区" : ""}${!states.length && !pure.length ? "及" : ""}${!pure.length ? "纯推进剂" : ""}检查未记录`);
  if(!evidence.length) evidence.push("已记录节点未报告温区外推或纯流体域错误");
  return `<div class="steady-attention"><section class="steady-attention-card${failed.length || closureFailed || numerical.status !== "pass" ? " has-issues" : ""}" aria-label="关键校核结论"><div class="steady-card-heading"><h2>约束与闭合</h2><button type="button" data-steady-open="checks">完整校核 →</button></div><ul>${lines.join("")}</ul><p class="fine">${inputs.cycle_type !== "gg" ? "所需泵压不等于固定泵能力；泵与涡轮特性仍待验证。" : "数值收敛与系统约束分别判定。"}</p></section><section class="steady-attention-card" aria-label="模型适用性摘要"><div class="steady-card-heading"><h2>模型适用性</h2><button type="button" data-steady-open="models">检查依据 →</button></div><p class="steady-model-state">${escape(thermo.label)}</p><ul>${evidence.map(text=>`<li>${escape(text)}。</li>`).join("")}</ul><p class="fine">高压燃气状态方程与换热关联式仍需独立验证。</p></section></div>`;
}

function overview(result, record = {}) {
  const s = result.calculation?.summary || {}, inputs = resultInputs(result), assessment = assessmentDisplayState(result);
  const gg = inputs.cycle_type === "gg";
  const conditions = [["指定燃烧室压力",scaled(inputs.pc,1e-6),"MPa"],["环境压力边界",scaled(inputs.ambient_pressure,1e-6),"MPa"],["指定总氧燃比 O/F",inputs.of_ratio,"—"],["主喷管面积比 Ae/At",inputs.expansion_ratio,"—"]];
  const study = `<section class="steady-study" aria-label="稳态计算工况"><div class="steady-study-mode"><span>STEADY STATE</span><b>单工况 · 稳态设计点</b><small>本页显示稳态设计点；热态扰动见瞬态响应分析</small></div><dl>${conditions.map(([label,value,unit])=>`<div><dt>${label}</dt><dd>${number(value)} <small>${unit}</small></dd></div>`).join("")}</dl></section>`;
  // The non-vacuum summary follows the saved operating pressure, which can be
  // vacuum or any other boundary. An absent field is not a sea-level default.
  const ambientHint = finite(inputs.ambient_pressure)
    ? `pₐ = ${number(scaled(inputs.ambient_pressure,1e-3))} kPa` : "环境压力未记录";
  const tempHint = side => {
    const temperatureMode=finite(s.preburner_temp_target_mode)?s.preburner_temp_target_mode>=.5:inputs.use_target_preburner_temp>=.5;
    const target=finite(s[`${side}_preburner_temp_target_K`])?s[`${side}_preburner_temp_target_K`]:inputs[`${side}_preburner_temp_target`];
    const mr=finite(s[`${side}_preburner_mr_target`])?s[`${side}_preburner_mr_target`]:inputs[`${side}_preburner_mr_target`];
    const targetText=temperatureMode?`目标 ${number(target)} K · `:inputs.use_target_preburner_mr>=.5?`指定 O/F ${number(mr)} · `:"";
    return targetText+preburnerLimitLabel(preburnerLimit(result,side));
  };
  const kpis = [
    kpi("计算真空推力", scaled(s.thrust_vac_N,.001), "kN", `工况计算值 ${number(scaled(s.thrust_N,.001))} kN（${ambientHint}）· 工况推力目标误差 ${number(s.thrust_error_percent)}%`),
    kpi("计算真空比冲 Isp", s.Isp_vac_s, "s", `工况计算值 ${number(s.Isp_s)} s（${ambientHint}）`),
    kpi("总质量流量", s.mdot_total_kg_s, "kg/s", `LOX + ${fuelInfo(inputs).symbol}${fuelInfo(inputs).surrogate ? "（煤油替代物）" : ""}`),
    kpi("主燃烧室温度", s.chamber_temperature_K, "K", "燃气温度"),
    gg ? kpi("燃气发生器温度", s.gg_T_combustion_K, "K", `${inputs.use_target_gg_temp >= .5 ? `目标 ${number(inputs.gg_temp_target)} / ` : ""}上限 ${number(inputs.gg_temp_limit)} K`) : kpi("富氧预燃室温度", s.ox_preburner_T_combustion_K, "K", tempHint("ox")),
    gg ? kpi("GG 涡轮排气温度", s.gg_turbine_exhaust_T_K, "K", `涡轮压比 πt = ${number(s.gg_turbine_pi_t)}`) : kpi("富燃预燃室温度", s.fuel_preburner_T_combustion_K, "K", tempHint("fuel")),
  ].join("");
  const checks = (assessment.checks || []).map(check => {
    const evidence=check.preburner_limit_evidence;
    const criterion=evidence ? escape(preburnerLimitLabel(evidence))+
      (evidence.status==="documented"&&evidence.basis?`<small class="field-help">依据（用户声明）：${escape(evidence.basis)}</small>`:"")+
      (evidence.historical?`<small class="field-help">历史保存判定：${escape(statusNames[evidence.saved_check_status] || evidence.saved_check_status || "未记录")}。历史记录按当时限值判定；限值未登记依据，当前按暂定展示，原记录不变。</small>`:"") :
      `${check.criterion === "minimum" ? "≥" : "≤"} ${number(check.limit)} ${escape(check.unit)}`;
    const label=evidence && check.status==="unknown" ? "未评估" : evidence && check.status==="review" ? "暂定，待核实" : evidence && check.status==="pass" ? "数值满足给定上限" : undefined;
    return `<tr class="constraint-row" data-status="${escape(check.status)}"><td title="${escape(fuelLabel(check.detail,inputs))}">${escape(fuelLabel(check.label,inputs))}</td><td class="constraint-value">${number(check.value)} ${escape(check.unit)}</td><td class="constraint-limit">${criterion}</td><td>${badge(check.status,label)}</td></tr>`;
  });
  const engineering = panel(isSystemAssessment(result) ? "系统约束校核" : "历史工程校核", `<div class="panel-heading">${badge(assessment.status, assessment.label || "校核结果待确认")}<span class="muted">独立于数值收敛</span></div>${checks.length ? table(["校核项","当前值","判据","状态"],checks) : '<p class="muted">本记录未包含约束校核数据。</p>'}<p class="fine">${escape((assessment.limitations || ["数值收敛不代表整机工程可行。"]).join(" "))}</p>`);
  const groups = desktopSchema.overview_groups[gg ? "gg" : "ffsc"].map(([title, fields],index) => ({
    diagnostic:fields.length > 0 && fields.every(([key])=>DIAGNOSTIC_KEYS.has(key)),
    power:fields.some(([key])=>POWER_KEYS.has(key)),
    html:metricGroup(title.split(" / ")[0], fields, s, inputs, index, assessment),
  }));
  const main=groups.filter(group=>!group.diagnostic && group.html),left=main.filter(group=>!group.power),right=main.filter(group=>group.power);
  const mainGroups=left.length && right.length ? `<div class="summary-column">${left.map(group=>group.html).join("")}</div><div class="summary-column">${right.map(group=>group.html).join("")}</div>` : main.map(group=>group.html).join("");
  const diagnostics=groups.filter(group=>group.diagnostic).map(group=>group.html).join("");
  const warnings=allWarnings(result);
  const warningDetails=warnings.length ? `<details class="panel steady-warnings"><summary>本次模型警告（${warnings.length} 项）</summary>${warningList(warnings)}</details>` : "";
  const pages=[
    {id:"overview",title:"性能总览",content:`<div class="kpi-grid" aria-label="稳态性能摘要">${kpis}</div>${renderSteadyDesignSummary(record.steady_design??result.steady_design)||steadyAttention(result)}<div class="summary-grid">${mainGroups}</div>${warningDetails}`},
    {id:"checks",title:"系统校核",content:`${engineering}${gg ? "" : renderSteadyClosure(result)}`},
    {id:"cooling",title:"再生冷却",content:renderCooling(result)},
    {id:"convergence",title:"收敛诊断",content:`${renderConvergence(result)}${diagnostics}`},
    {id:"models",title:"模型与物性",content:renderSteadyDesignBasis(record.steady_design??result.steady_design)+renderThermodynamicApplicability(result)},
  ];
  return `<div class="analysis-stack steady-overview">${study}${statusStrip(result)}${renderSteadyWorkspace(pages)}</div>`;
}

export function flowAnalysisModel(result) {
  return buildFlowModel(result.calculation?.states || {}, {
    labels:desktopSchema.state_labels, inputs:resultInputs(result),
    componentIds:componentAnalysisItems(result).map(item=>item.id),
  });
}

function states(result) {
  return `<div class="analysis-stack">${statusStrip(result)}${renderFlowWorkspace(flowAnalysisModel(result))}</div>`;
}

export function componentAnalysisItems(result) {
  const inputs=resultInputs(result);
  const entries = Object.entries(result.calculation?.components || {}).filter(([name])=>Object.hasOwn(SYSTEM_COMPONENT_FIELDS,name));
  return entries.map(([name,component]) => {
    const outputs = component.outputs || {};
    const hiddenAliases = new Set();
    if (name === "main_chamber" && inputs.combustion_loss_model === "adiabatic") hiddenAliases.add("combustion_efficiency");
    for (const aliases of COMPONENT_ALIASES) {
      const present = aliases.filter(key=>SYSTEM_COMPONENT_FIELDS[name].has(key) && Object.hasOwn(outputs,key));
      const preferred = present.find(key=>finite(outputs[key])) || present[0];
      for (const key of present) if(key!==preferred) hiddenAliases.add(key);
    }
    const fields = Object.entries(outputs).filter(([key])=>SYSTEM_COMPONENT_FIELDS[name].has(key) && !hiddenAliases.has(key)).map(([key,raw])=>{
      let value = raw;
      let label = COMPONENT_SPECIFIC_LABELS[name]?.[key] || COMPONENT_LABEL_OVERRIDE[key] || desktopSchema.key_labels[key] || key;
      // Keep the pressure label and actual unit consistent even if a future result omits MPa aliases.
      const scale = {injector_dp_Pa:1e-6,preburner_pressure_Pa:1e-6,...COMPONENT_VALUE_SCALE}[key];
      if (finite(value) && scale != null) value *= scale;
      if (key === "mode" && finite(value)) value = value >= .5 ? "Bartz 型热流估算" : "固定热载荷";
      if (key === "delta_P_source" && finite(value)) value = value >= .5 ? "水力模型压降" : "给定压降";
      if (key === "flow_model" && finite(value)) value = value < 0 ? "理想气体" : value >= .5 ? "平衡流" : name === "nozzle" ? "主室组成冻结流" : "喷管入口组成冻结流";
      const matched = fuelLabel(label,inputs).match(/^(.*?)\s*\[([^\]]*)\]\s*$/);
      return {key,label:matched ? matched[1].trim() : fuelLabel(label,inputs),value,
        unit:matched ? (matched[2] === "-" ? "—" : matched[2]) : "—"};
    });
    const title = name === "cooling_channel" ? "再生冷却 · 系统吸热与压降" : desktopSchema.component_labels[name] || component.name || name;
    return {id:name,title:fuelLabel(title,inputs),fields,outputs};
  }).filter(item=>item.fields.length);
}

function components(result) {
  return `<div class="analysis-stack">${statusStrip(result)}${renderComponentWorkspace(componentAnalysisItems(result))}${renderCooling(result)}<p class="fine">部件性能来自当前设计点计算记录。原始部件说明与诊断完整保留在「日志与警告」和结果导出中。</p></div>`;
}

function log(result, record) {
  const inputs=resultInputs(result);
  const environment = result.environment || {};
  const metadata = [["任务",record.id],["方案版本",record.revision == null ? "—" : `v${record.revision}`],["核心版本",result.application_version],["Python",environment.python],["平台",environment.platform],["环境指纹",environment.fingerprint],["化学机理 SHA-256",environment.mechanism_sha256]];
  const dependencies = Object.entries(environment.dependencies || {}).map(([name,version])=>row([name,version]));
  metadata.splice(3,0,["计算范围",result.calculation_scope?.label || "历史计算（原工程校核范围）"]);
  metadata.splice(4,0,["推进剂模型",fuelInfo(inputs).pair]);
  const notes = Object.entries(result.calculation?.components || {}).filter(([,component])=>component.notes?.length)
    .map(([name,component])=>`<details><summary>${escape(fuelLabel(desktopSchema.component_labels[name] || component.name || name,inputs))} · 原始说明与诊断（${component.notes.length} 项）</summary>${warningList(component.notes)}</details>`).join("");
  const limitations = [...new Set([...(result.calculation_scope?.limitations || []),...(result.assessment?.limitations || [])])];
  return `<div class="analysis-stack">${statusStrip(result)}${panel("计算警告",warningList(allWarnings(result)))}${panel("模型范围与校核依据",warningList(limitations))}${notes ? panel("原始部件说明与诊断",`<p class="muted">以下内容按原计算记录保留，可能包含详细部件估算及其警告；原始诊断不会随系统视图筛选而删除。</p>${notes}`) : ""}${panel("计算环境与追溯",`<div class="result-meta">${table(["项目","值"],metadata.map(row))}</div>${dependencies.length ? table(["科学库","版本"],dependencies) : ""}<p class="fine">完整结果导出包含提交参数、计算范围、状态点、全部部件结果与原始诊断。</p>`)}${result.nonfinite_fields?.length ? panel("非有限数值字段",`<p class="muted">以下字段已标为缺失，未按 0 处理。</p>${warningList(result.nonfinite_fields)}`) : ""}</div>`;
}

function topology(result) {
  const model=buildCycleModel(result,flowAnalysisModel(result),componentAnalysisItems(result));
  return `<div class="analysis-stack">${statusStrip(result)}${renderCycleWorkspace(model)}</div>`;
}

/** Render a result panel without attaching handlers or mutating the record. */
export function renderAnalysis(view = "overview", record = null) {
  if (!record) return welcome();
  if (!record.result?.calculation) {
    const messages = {queued:"任务已进入计算队列。",running:"正在运行当前设计点，计算结果将在完成后显示。",failed:"本次计算失败，请检查错误信息后重试。",cancelled:"本次计算已取消。",interrupted:"本次计算已中断。"};
    return `<div class="analysis-stack"><div class="banner">${badge(record.status)}<p>${escape(record.error || messages[record.status] || "本记录尚无可用计算结果。")}</p></div>${panel("设计点计算", `<p class="muted">${["queued","running"].includes(record.status) ? "结果将在任务完成后自动显示。等待期间可在上方切换查看历史计算，或取消本次计算。" : "本次任务没有产生可用结果。检查参数和错误信息后，可再次运行设计点。"}</p>`)}</div>`;
  }
  const renderers = {overview,states,components,log,topology};
  return (Object.hasOwn(renderers, view) ? renderers[view] : overview)(record.result, record);
}
