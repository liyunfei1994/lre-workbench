// Read-only context. Never infer a solved boundary or geometry from a draft value.
import {savedValveTopology} from "./cycle-model.js";
const optionalNullKeys = new Set([
  ...["ox_to_opb", "ox_to_fpb", "fuel_to_opb", "fuel_to_fpb"]
    .flatMap(branch => ["line_dp_ratio", "injector_dp_ratio"].map(suffix => `${branch}_${suffix}`)),
  "cooling_channel_roughness_m", "cooling_k_minor_inlet", "cooling_k_minor_outlet",
]);
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const finite = value => typeof value === "number" && Number.isFinite(value);
const positive = value => finite(value) && value > 0;
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
const fmt = (value, scale=1, allowSigned=false) => (allowSigned ? finite(value) : positive(value))
  ? Number((value * scale).toPrecision(6)).toLocaleString("zh-CN", {maximumFractionDigits:6, useGrouping:false}) : "未记录";

function comparable(inputs) {
  if (!object(inputs)) return null;
  return Object.fromEntries(Object.entries(inputs).filter(([key, value]) =>
    !(optionalNullKeys.has(key) && value == null) && !(key === "cooling_htc_enhancement_basis" && value === "")
    && !(key === "valve_configuration_json" && savedValveTopology(inputs) === null)));
}

function same(a, b) {
  if (typeof a === "number" || typeof b === "number") return finite(a) && finite(b) && a === b;
  if (a === b) return true;
  if (Array.isArray(a) || Array.isArray(b)) return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => same(v, b[i]));
  if (!object(a) || !object(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every(key => Object.hasOwn(b, key) && same(a[key], b[key]));
}

function inScope(inputs) {
  return object(inputs) && inputs.cycle_type === "ffsc" && inputs.propellant_pair === "lox_methane";
}

function flowIsRecorded(profile) {
  const geom = profile?.geometry;
  if (profile?.flow_direction !== "counterflow" || geom?.flow_direction !== "counterflow") return false;
  const nodes = geom.nodes, flowNodes = profile.nodes;
  if (!Array.isArray(nodes) || nodes.length < 2 || !Array.isArray(flowNodes)) return false;
  if (!nodes.every((node, i) => finite(node?.x_m) && positive(node?.r_m) && (!i || node.x_m < nodes[i-1].x_m))) return false;
  const channels = flowNodes.filter(node => node?.kind === "channel");
  return channels.length >= 2 && channels.every((node, i) => finite(node?.x_m) && (!i || node.x_m < channels[i-1].x_m));
}

function sourceState(inputs) {
  const thermalDataset = typeof inputs?.wall_thermal_dataset_id === "string" && inputs.wall_thermal_dataset_id.trim();
  const topology = savedValveTopology(inputs);
  return {
    layout: topology === "paper_split_before_cooling_v1"
      ? "燃料泵后分流：MFV → 逆流再生冷却 → 富燃预燃室；OPFV 旁路直接供给富氧预燃室。"
      : topology === "all_fuel_cooled_then_split_valves_v1"
        ? "全部燃料经 MFV → 逆流再生冷却，再分流至两预燃室；OPFV 位于冷却后的富氧支路。"
        : topology === "unrecognized" ? "阀门安装拓扑未识别，不能确定冷却入口位置。"
          : "燃料主泵出口 → 逆流再生冷却通道 → 两个预燃室的燃料支路。",
    geometrySource: typeof inputs?.cooling_channel_table_json === "string" && inputs.cooling_channel_table_json.trim()
      ? "沿程分布表；由推力室与喷管型面定位各断面" : "标量几何规则；宽度随局部半径变化，深度与壁厚按输入规则生成",
    materialSource: thermalDataset ? `当前选择的热物性数据集：${thermalDataset}` : "壁面材料与热物性数据集；尚未明确选择",
    thermalMode: inputs?.cooling_bartz_mode === 2 ? "当前采用概略热负荷估算；参考壁温为假设，通道草案不参与本次循环。实际壁温及冷却能力待详细模型校核。"
      : finite(inputs?.cooling_bartz_mode) && inputs.cooling_bartz_mode < .5
      ? "当前采用吸热量与压降预算；沿程热工几何结果需启用再生冷却沿程计算" : "主燃烧室热气状态、局部型面与辐射设置共同提供热侧条件",
  };
}

/** Only current, successful FFSC methane records may supply numeric values. */
export function coolingInputSummaryState(inputs, {record=null}={}) {
  const base = sourceState(inputs);
  const unavailable = (status, message) => ({...base, status, message, isCurrent:false, profile:null, boundary:null, throat:null});
  const calc = record?.result?.calculation;
  if (!record || !object(calc)) return unavailable("empty", "尚无冷却计算结果；入口条件由循环求解提供，无需在此重复填写。");
  // Check every identity source: never let a newer-looking wrapper hide an unsupported calculation.
  const snapshots = [record.inputs, record.result?.submitted_inputs, calc.inputs].filter(value => value !== undefined && value !== null);
  if (!inScope(inputs) || snapshots.some(snapshot => object(snapshot) && (
    (snapshot.cycle_type != null && snapshot.cycle_type !== "ffsc") ||
    (snapshot.propellant_pair != null && snapshot.propellant_pair !== "lox_methane")))) {
    return unavailable("unsupported", "记录或当前参数不属于液氧甲烷全流量补燃循环；请按当前方案重新计算。");
  }
  if (!inScope(calc.inputs) || snapshots.some(snapshot => !inScope(snapshot))) {
    return unavailable("unknown", "历史记录缺少完整输入身份，无法核对当前参数；请重新计算。");
  }
  if (snapshots.some(snapshot => !same(comparable(inputs), comparable(snapshot)))) {
    return unavailable("stale", "当前参数与已保存计算不同；历史结果不作为本方案当前条件，请重新运行设计点。");
  }
  if (record.status !== "succeeded") return unavailable("incomplete", "该记录未成功完成；诊断结果不作为当前冷却设计条件。");
  const component = calc.components?.cooling_channel, profile = component?.profile;
  if (profile?.version !== 1 || profile.model_id !== "regen_quasi1d_ph_v1") {
    return unavailable("unavailable", "已保存记录未包含可核对的准一维冷却结果；请运行沿程计算。");
  }
  if (!flowIsRecorded(profile)) return unavailable("flow_mismatch", "历史记录的冷却流向或坐标顺序无法与当前逆流布置核对；请重新计算。");
  const topology = savedValveTopology(calc.inputs), valveResult = calc.steady_closure?.valve_design;
  if (topology === "unrecognized") return unavailable("valve_mismatch", "保存的阀门安装拓扑无法识别，不能确定冷却入口。");
  if (!topology && valveResult?.configuration?.enabled)
    return unavailable("valve_mismatch", "保存的实体阀结果与计算输入不一致，不能按旧流路显示。");
  let inlet = calc.states?.fuel_after_pump ?? {};
  if (topology) {
    const config = JSON.parse(calc.inputs.valve_configuration_json);
    if (!same(config, valveResult?.configuration))
      return unavailable("valve_mismatch", "保存的实体阀配置与计算输入缺失或不一致，不能借用历史泵出口状态。");
    inlet = calc.states?.cooling_inlet;
    if (!object(inlet) || ![inlet.mass_flow,inlet.pressure,inlet.temperature].every(positive) || !finite(inlet.enthalpy))
      return unavailable("valve_boundary_missing", "本记录未完整保存 MFV 后冷却入口；不能以燃料主泵出口或总燃料流量替代。");
    const port=profile.nodes[0], channel=profile.nodes.find(node=>node.kind==="channel");
    const expectedFlow=topology==="paper_split_before_cooling_v1" ? calc.states?.branch_fuel_to_fpb?.mass_flow : calc.states?.fuel_after_pump?.mass_flow;
    const pairs=[[inlet.pressure,port?.p_Pa],[inlet.temperature,port?.T_K],
      [inlet.mass_flow,channel?.rho_kgm3*channel?.velocity_ms*channel?.A_flow_m2],
      [inlet.mass_flow,expectedFlow],[inlet.mass_flow,calc.states?.fuel_after_cooling?.mass_flow]];
    if (pairs.some(([a,b])=>!positive(b)||Math.abs(a-b)>Math.max(1e-7,2e-7*Math.max(Math.abs(a),Math.abs(b)))))
      return unavailable("valve_boundary_mismatch", "保存的冷却入口、分流量与沿程边界不一致；请重新计算，不使用全部燃料冷却的旧假设补齐。");
  }
  const geom = profile.geometry;
  const throat = finite(geom.x_throat_m)
    ? geom.nodes.find(node => Math.abs(node.x_m - geom.x_throat_m) <= 1e-9) ?? null : null;
  const boundary = {
    mdot:positive(inlet.mass_flow) ? inlet.mass_flow : null,
    pressure:positive(inlet.pressure) ? inlet.pressure : null,
    temperature:positive(inlet.temperature) ? inlet.temperature : null,
    enthalpy:finite(inlet.enthalpy) ? inlet.enthalpy : null,
  };
  return {...base, status:"current", isCurrent:true, profile, message:"当前参数对应的已保存计算；数值用于方案复核，不表示热工、结构或寿命验证已通过。",
    boundary, throat, revision:record.revision, valveTopology:topology,
    boundaryLabel:topology ? "MFV 出口 / 冷却入口" : "燃料主泵出口",
    materialDataset:profile.material_assignment?.default_dataset_id ?? null,
    materialZones:Array.isArray(profile.material_assignment?.zones) ? profile.material_assignment.zones.length : null,
    geometryDistribution:geom.channel_distribution,
  };
}

export function renderCoolingInputSummary(inputs, options={}) {
  const state = coolingInputSummaryState(inputs, options);
  const row = (label, value, unit) => `<tr><th scope="row">${esc(label)}</th><td>${esc(value)}</td><td>${esc(unit)}</td></tr>`;
  let computed = "";
  if (state.status === "current") {
    const b = state.boundary, g = state.throat ?? {};
    const hydraulics = state.profile.hydraulic_parameters;
    const correction = state.profile.heat_transfer_correction;
    const nonnegative = value => finite(value) && value >= 0 ? fmt(value, 1, true) : "未记录";
    const hydraulicNote = hydraulics?.version === 1
      ? `<p class="muted">该次计算水力参数：${hydraulics.roughness_mode === "absolute"
        ? `等效砂粒粗糙度 ks = ${positive(hydraulics.channel_roughness_m) || hydraulics.channel_roughness_m === 0 ? fmt(hydraulics.channel_roughness_m, 1e6, true) : "未记录"} μm`
        : hydraulics.roughness_mode === "relative" ? `相对粗糙度 ks/Dh = ${nonnegative(hydraulics.relative_roughness_input)}` : "粗糙度模式未记录"}；入口 K = ${nonnegative(hydraulics.k_minor_inlet)}，出口 K = ${nonnegative(hydraulics.k_minor_outlet)}。局部损失以相邻通道动压为基准。</p>` : "";
    const correctionNote = object(correction) ? `<p class="muted">该次计算喉部冷却侧修正倍率：${fmt(correction.factor)}；依据：${esc(correction.basis || "未记录")}。记录依据不等于独立验证通过。</p>` : "";
    computed = `<h5>已保存入口条件 · ${esc(state.boundaryLabel)}</h5><div class="table-wrap"><table><thead><tr><th>物理量</th><th>计算值</th><th>单位</th></tr></thead><tbody>${
      row(state.valveTopology ? "冷却支路质量流量" : "甲烷总质量流量", fmt(b.mdot), "kg/s") + row("冷却入口压力", fmt(b.pressure, 1e-6), "MPa") +
      row("冷却入口温度", fmt(b.temperature), "K") + row("冷却入口比焓", fmt(b.enthalpy, 1e-3, true), "kJ/kg")
    }</tbody></table></div><p class="muted">取自已保存循环的${esc(state.boundaryLabel)}；比焓沿用循环化学参考态，不与其他物性库的零点直接比较。</p>
    <h5>已保存喉部实际通道几何</h5><div class="table-wrap"><table><thead><tr><th>几何量</th><th>计算值</th><th>单位</th></tr></thead><tbody>${
      row("通道数 N", Number.isInteger(g.n_channels) ? fmt(g.n_channels) : "未记录", "—") +
      [["通道宽度 w", "width_m"], ["通道深度 h", "height_m"], ["热壁厚度 t", "wall_thickness_m"],
        ["通道节距", "pitch_m"], ["肋宽", "rib_m"], ["水力直径 Dh", "D_h_m"]]
        .map(([label, key]) => row(label, fmt(g[key], 1e3), "mm")).join("")
    }</tbody></table></div><p class="muted">来自已保存沿程几何的喉部断面；缺失值不由当前输入补算。实际宽、深、壁厚可沿轴向变化。</p>
    <p class="muted">该次计算材料数据集：${esc(state.materialDataset || "未记录")}${state.materialZones === null ? "" : `；局部分区 ${state.materialZones} 个`}。${state.revision == null ? "方案版本未记录" : `来源：方案 v${esc(state.revision)}`}。</p>${hydraulicNote}${correctionNote}`;
  }
  return `<section class="cooling-input-summary" data-cooling-input-state="${state.status}"><h4>冷却计算条件与来源</h4>
    <p>当前布置：${esc(state.layout)}入口流量、压力、温度和比焓随循环求解确定。</p>
    <ul><li>热侧与型面：${esc(state.thermalMode)}；与“推力室与喷管型面”“再生冷却布置方式”共用几何。</li>
    <li>通道几何：${esc(state.geometrySource)}。</li><li>材料：${esc(state.materialSource)}；局部分区及制造状态由材料设置提供。</li></ul>
    <p role="status">${esc(state.message)}</p>${computed}
    <p class="muted">模型范围：代表通道准一维稳态，冷却侧采用主体物性的 Dittus–Boelter 关联与肋片修正。近临界换热恶化、周向不均流及复用寿命未在本模型验证。</p></section>`;
}
