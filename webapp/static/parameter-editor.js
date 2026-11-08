// Presentation rules never migrate stored EngineInputs. Only an explicit mode
// change writes the two legacy flags, keeping drafts, imports and undo lossless.
import {renderPressureWorkspace} from "./pressure-design.js";
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
export const PREBURNER_MODE_KEY = "preburner_design_mode";

export function renderAnalysisParameterRoles(catalog, mode, {currentReference=false}={}) {
  if(!Array.isArray(catalog?.rows) || !catalog.rows.length)return "";
  const modes=[["concept_design","概念设计"],["fixed_hardware_steady","固定硬件稳态"]];
  const column=key=>key===mode?' class="is-current"':"";
  const rows=catalog.rows.map(row=>`<tr data-quantity="${escape(row.id)}"><th scope="row">${escape(row.label)}</th>${modes.map(([key])=>`<td${column(key)}>${escape(row[key]?.description || "尚未定义")}</td>`).join("")}</tr>`).join("");
  const notes=Array.isArray(catalog.principles)?catalog.principles.map(item=>`<p class="fine">${escape(item)}</p>`).join(""):"";
  const headings=modes.map(([key,label])=>`<th scope="col"${column(key)}>${label}${key===mode?' · 当前模式':""}</th>`).join("");
  return `<details class="analysis-parameter-roles"><summary>分析模式参数对照</summary>
    ${currentReference?'<p class="fine">该历史任务未保存参数定义，表中采用当前版本定义。</p>':""}${notes}
    <div class="table-wrap"><table class="analysis-role-table"><thead><tr><th scope="col">参数</th>${headings}</tr></thead><tbody>${rows}</tbody></table></div>
    <p class="fine">氧燃质量比为氧化剂质量流量与燃料质量流量之比（无量纲）。预燃室按各自入口流量计，整机按总供给流量计。</p>
  </details>`;
}

function fixedModeNote(field, id) {
  const description=field.analysis_modes?.fixed_hardware_steady?.description;
  return description?`<span class="field-help field-mode-role"${id?` id="${escape(id)}"`:""}>固定硬件稳态：${escape(description)}</span>`:"";
}

export function savedInputDocument(value) {
  // Recognized desktop files and previous web exports have snapshot identity.
  // A bare object or an arbitrary {inputs: ...} parameter patch does not.
  return Boolean(value && typeof value === "object" && value.inputs &&
    ((["ffsc.workbench.case", "lre.workbench.inputs"].includes(value.format) && value.schema_version === 1) ||
      (!value.format && typeof value.project_name === "string" && Number.isInteger(value.base_revision) &&
        value.base_revision > 0 && typeof value.unsaved === "boolean")));
}

// Retired shared-struct fields are accepted only when the file identifies FFSC.
// Unknown keys stay intact so the import boundary can reject them explicitly.
const RETIRED_PLATFORM_FIELDS = new Set([
  "gg_exhaust_expansion_ratio","gg_fraction_guess","gg_injector_dp_ratio","gg_layout",
  "gg_mech_efficiency","gg_mr_target","gg_temp_limit","gg_temp_target",
  "gg_turbine_backpressure_pa","use_target_gg_temp","injector_dp_ratio_ox","injector_dp_ratio_fuel",
]);
export function platformInputValues(inputs) {
  if(inputs?.cycle_type!=="ffsc"||(inputs.propellant_pair??"lox_methane")!=="lox_methane")return {...inputs};
  return Object.fromEntries(Object.entries(inputs).filter(([key])=>!RETIRED_PLATFORM_FIELDS.has(key)));
}

export function mergeInputDefaults(defaults, inputs, savedSnapshot = false) {
  inputs=platformInputValues(inputs);
  const result = {...defaults, ...inputs};
  if(savedSnapshot && Object.hasOwn(defaults,"chamber_inner_wall_material") && !Object.hasOwn(inputs,"chamber_inner_wall_material"))
    result.chamber_inner_wall_material="grcop84";
  if(savedSnapshot)for(const side of ["ox","fuel"]){
    const key=`${side}_preburner_temp_limit_status`;
    if(Object.hasOwn(defaults,key)&&!Object.hasOwn(inputs,key))result[key]="provisional";
  }
  for(const [key,legacy] of Object.entries({cooling_sizing_reference:"legacy_inlet_density_throat_width",cooling_wall_temp_limit_status:"provisional",cooling_channel_auto_size:1,cooling_channel_height_m:0.005}))
    if(Object.hasOwn(defaults,key) && !Object.hasOwn(inputs,key))result[key]=legacy;
  if(Object.hasOwn(defaults,"nozzle_contour_method") && !Object.hasOwn(inputs,"nozzle_contour_method")) result.nozzle_contour_method="legacy_log_v1";
  if (savedSnapshot && Object.hasOwn(defaults, "methane_thermo_dataset") && !Object.hasOwn(inputs, "methane_thermo_dataset")) result.methane_thermo_dataset = "gri30";
  if(Object.hasOwn(defaults,"wall_thermal_dataset_id") && !Object.hasOwn(inputs,"wall_thermal_dataset_id") &&
      (savedSnapshot || Object.hasOwn(inputs,"chamber_inner_wall_material"))) {
    const supported={grcop84:"grcop84_ellis2000_extruded_high_fe_v1",grcop42:"grcop42_chen2023_lpbf_v1",cuzr:"cuzr_qi2024_heat_treated_v1"};
    result.wall_thermal_dataset_id=supported[result.chamber_inner_wall_material] || "";
  }
  return result;
}

export function methaneThermoSummary(inputs, catalog = []) {
  const id = inputs.methane_thermo_dataset ?? "gri30";
  const dataset = catalog.find(item => item.id === id);
  const names = {gri30:"GRI30（历史数据）", gri30_highT:"GRI30 high-T"};
  const legacyLoss = (inputs.combustion_loss_model ?? "legacy_temperature_rise") === "legacy_temperature_rise";
  const spatialCooling = Boolean(inputs.cooling_bartz_mode);
  const chamber = legacyLoss ? "化学平衡后温升折减（历史兼容）" :
    inputs.combustion_loss_model === "adiabatic" ?
      (spatialCooling ? "化学平衡（给定压力与焓）" : "化学平衡（计入给定冷却吸热）") : "未识别的经验修正设置";
  const rows = [["甲烷热化学", names[id] || `未识别：${id}`], ["主燃烧室", chamber],
    ["主喷管热侧", spatialCooling ? "冻结组成 · 沿程冷却" : "冻结组成 · 绝热膨胀"],
    ["燃气状态方程", dataset?.gas_eos === "ideal-gas" ? "理想气体混合物" : dataset?.gas_eos || "数据未读取"]];
  const range = dataset?.common_temperature_range_K;
  const rangeText = Array.isArray(range) && range.length === 2 && range.every(Number.isFinite) ? `${range[0]}–${range[1]} K` : "未读取";
  const source = /^https:\/\//i.test(dataset?.source_url || "") ?
    `<a href="${escape(dataset.source_url)}" target="_blank" rel="noopener noreferrer">${escape(dataset.source_label || "数据来源")}</a>` : "来源未读取";
  const update = id === "gri30_highT" && dataset?.file_description?.includes("NASA TM-4513") ?
    "文件说明：Joseph Shepherd 依据 NASA TM-4513 更新高温热力学多项式，并非仅放宽温区。" : "";
  const historical = id === "gri30";
  const details = `<p class="fine">当前参数所用文件：${escape(dataset?.file || id)}；Cantera ${escape(dataset?.cantera_version || "版本未读取")}。${source}</p>` +
    `<p class="fine">${escape(dataset?.species_count ?? "未读取")} 种物种声明温区的交集：${escape(rangeText)}。交集外需逐物种检查，不代表所有物种都已越界。${escape(update)}</p>` +
    '<p class="fine">高温热数据提供物种焓、熵和比热；不能据此认定高压状态方程、反应速率、输运或积碳模型已验证。系统纯流体物性使用 CoolProp 的 HEOS 模型；REFPROP 对照在“热力学校核”中单独进行。</p>' +
    (legacyLoss ? '<p class="fine">此方案保留历史温升折减；升级热化学数据不会自动改变该模型。</p>' : "") +
    (historical ? '<p class="fine">旧方案继续使用其原数据复现。使用 high-T 时先建立草稿，点击“保存方案”生成新版本；旧版本和已保存结果保留。</p>' : '<p class="fine">此摘要对应当前参数。已保存结果使用各自记录的数据版本，修改后需重新计算。</p>');
  return {summary: rows.map(([label,value]) => `<p class="fine"><b>${escape(label)}：</b>${escape(value)}</p>`).join(""), details, historical};
}

export function upgradeMethaneDatasetDraft(inputs) {
  if ((inputs.propellant_pair || "lox_methane") !== "lox_methane" || (inputs.methane_thermo_dataset ?? "gri30") !== "gri30") return inputs;
  return {...inputs, methane_thermo_dataset:"gri30_highT"};
}

export function upgradeCoolingModelDraft(inputs) {
  if ((inputs.propellant_pair || "lox_methane") !== "lox_methane" || inputs.cooling_bartz_mode !== 0) return inputs;
  return {...inputs, cooling_bartz_mode:1};
}

function renderCoolingModel(inputs) {
  const spatial=typeof inputs.cooling_bartz_mode === "number" && Number.isFinite(inputs.cooling_bartz_mode) && inputs.cooling_bartz_mode>0;
  const budget=inputs.cooling_bartz_mode===0;
  if(spatial){
    const ffsc=inputs.cycle_type==="ffsc";
    const inlet=ffsc
      ? "当前全流量补燃循环模型中，燃料泵出口的全部燃料先流经再生冷却通道，再分配至两个预燃室。入口流量、压力与比焓由循环计算提供。"
      : "冷却剂入口流量、压力与比焓由当前循环模型提供，出口状态返回相应燃料支路。";
    const methods=[
      ["燃气侧换热", "Bartz 型对流换热关联式与灰体辐射近似。燃气侧采用冻结组分，沿程计入换热。", "对流与辐射热流密度"],
      ["壁面导热", "按温度相关导热率积分计算壁厚方向导热，并计入通道肋片效率修正。", "燃气侧壁温、冷却剂侧壁温"],
      ["冷却剂侧换热", "湍流采用 Dittus–Boelter 关联式；层流采用固定努塞尔数，过渡区插值。主体物性由 CoolProp 计算。", "换热系数、冷却剂温度"],
      ["通道流动与能量", "逐段联立一维动量与能量方程，计入 Darcy 摩擦、局部损失和流动加速。", "压力、比焓、流速与通道压降"],
    ];
    const links=[["chamber_contour","推力室与喷管型面"],["chamber_cooling_layout","再生冷却布置方式"],["chamber_channels","再生冷却通道设计"],["wall_thermal","推力室内衬材料与热物性"]];
    return `<section class="cooling-methods" aria-label="当前再生冷却计算方法">
      <header><span class="cooling-methods-kicker">稳态热力与流动计算</span><h3>沿程分段计算 · 与整机循环耦合</h3>
        <p>沿推力室型面离散冷却通道，以冷却剂压力和比焓为求解变量，各截面采用平均状态。热流、壁温、吸热量及压力损失由计算得到。</p></header>
      <div class="table-wrap"><table class="cooling-methods-table"><thead><tr><th scope="col">计算环节</th><th scope="col">当前方法</th><th scope="col">主要计算量</th></tr></thead>
        <tbody>${methods.map(([part,method,output])=>`<tr><th scope="row">${part}</th><td>${method}</td><td>${output}</td></tr>`).join("")}</tbody></table></div>
      <section class="cooling-methods-coupling"><h4>循环计算中的冷却反馈</h4><p>${inlet}</p>
        <ol><li>给定总体目标与初步几何</li><li>循环匹配与沿程冷却迭代</li><li>检查壁温、压降与数据适用范围</li></ol>
        <p>冷却吸热、压力损失和出口状态在循环求解过程中反复更新。收敛后汇总校核结果；调整几何或材料后重新计算设计点。</p></section>
      <nav class="cooling-methods-links" aria-label="冷却设计参数">${links.map(([id,label])=>`<button type="button" data-readiness-group="${id}">${label} →</button>`).join("")}</nav>
      <p class="cooling-methods-result-note">沿程曲线、能量收支和约束判定见“计算结果 → 再生冷却”。结果对应所选计算记录。</p>
      <details><summary>关联式、数值设置与适用范围</summary>
        <dl><dt>冷却剂换热</dt><dd>雷诺数不大于 2300 时，努塞尔数取 4.36；2300～3000 区间插值；不小于 3000 时采用 Dittus–Boelter 关联式。喉部换热标定系数及其依据在“再生冷却通道设计”中设置。</dd>
        <dt>水力计算</dt><dd>摩擦系数随雷诺数与相对粗糙度计算。局部损失系数在通道参数中设置，进出口集流腔按零速度边界处理。</dd>
        <dt>数值求解</dt><dd>每段联立求解压力与比焓，再与燃气侧及循环迭代。轴向网格分辨率在“数值求解”中设置，应通过网格加密检查结果变化。</dd>
        <dt>适用范围</dt><dd>当前采用单相冷却剂模型。未求解壁体储热、周向温差、两相流、甲烷裂解与积碳；近临界换热恶化未采用专用关联式。材料数据温区与设计壁温限值分别校核。</dd></dl>
      </details>
    </section>`;
  }
  if(!budget)return '<p class="notice warning">冷却模型标识缺失或无效；请检查保存的方案。</p>';
  const methane=(inputs.propellant_pair || "lox_methane")==="lox_methane";
  return '<div class="cooling-model-summary"><b>历史兼容：给定吸热与压降预算</b><p class="field-help">本方案保留原计算方式和预算参数，没有沿程冷却解；加载、保存和查看记录不会自动转换模型。</p>'+
    (methane?'<button type="button" data-upgrade-cooling>建立准一维冷却草稿</button><p class="field-help">切换后请核对型面、通道与材料，保存为新版本并重新计算。既有记录保留。</p>':'<p class="field-help">煤油替代工质尚未接入统一准一维冷却；当前仅保留预算模型兼容能力。</p>')+'</div>';
}

function renderChamberModel(inputs) {
  const mode=inputs.combustion_loss_model;
  const correction=mode==="adiabatic" ? '<p>经验温升修正未启用。</p>' :
    mode==="legacy_temperature_rise" ? '<p class="notice warning">当前方案启用历史温升折减。输出状态经过经验修正，设置见下方“历史兼容设置”。</p>' :
      '<p class="notice warning">经验温升修正设置缺失或无效，请检查下方“历史兼容设置”。</p>';
  const inlet=inputs.cycle_type==="ffsc" ? "两股涡轮排气的质量流量、组分与比焓，以及指定的主燃烧室压力。" :
    "燃料与氧化剂实际入口的质量流量、组分与比焓，以及指定的主燃烧室压力。";
  return `<section class="chamber-model-summary" aria-label="当前主燃烧室模型">
    <h3>化学平衡（给定压力与焓）</h3>
    <p>零维热力学模型。在给定压力下，按元素守恒和能量守恒求解化学平衡状态。</p>
    <dl><dt>计算输入</dt><dd>${inlet}</dd><dt>计算输出</dt><dd>燃气温度、平衡组分、比热、比热比及平均摩尔质量。</dd>
    <dt>能量约束</dt><dd>入口比焓包含化学生成焓与热状态贡献，按质量流量加权。冷却换热按所选再生冷却模型计入能量收支。</dd>
    <dt>模型范围</dt><dd>采用理想气体混合物假设，考虑平衡解离。未求解空间混合、有限速率反应及燃烧不稳定性。</dd></dl>
    ${correction}
    <details><summary>压力与焓约束说明</summary>
      <p>“给定压力与焓”指本次平衡计算的约束条件。温度与产物组分由求解得到，主燃烧室压力可随设计工况改变。</p>
      <p>给定冷却预算时，从入口焓流中扣除规定的吸热量。准一维冷却模式下，先求未扣除再生换热的平衡参考状态，再由热侧沿程模型扣除对应换热量。</p>
      <p>此处提供一个基础化学平衡模型。历史温升折减属于附加经验修正。喷管膨胀过程的组分假设在喷管模型中单独定义。</p>
      <p>方法说明见 <a href="https://www.cantera.org/stable/python/thermo.html#cantera.ThermoPhase.equilibrate" target="_blank" rel="noopener noreferrer">Cantera 化学平衡约束</a>。
      <a href="https://www.nasa.gov/glenn/research/chemical-equilibrium-with-applications/" target="_blank" rel="noopener noreferrer">NASA 化学平衡计算程序说明</a>可作为方法参考，当前计算由 Cantera 执行。</p>
    </details>
  </section>`;
}
const ROLE_LABELS = {
  design_input:"设计输入", boundary:"边界条件", model_assumption:"模型假设",
  constraint:"约束", initial_guess:"迭代初值", component_sizing:"部件选型",
  solver_setting:"求解设置", legacy:"历史兼容", result:"计算结果",
};

export function preburnerMode(inputs) {
  if (Number(inputs.use_target_preburner_temp ?? 1) >= .5) return "temperature";
  if (Number(inputs.use_target_preburner_mr ?? 1) >= .5) return "mixture_ratio";
  return "legacy_free_split";
}

export function selectPreburnerMode(inputs, mode) {
  if (!["temperature", "mixture_ratio"].includes(mode)) throw new Error("请选择指定预燃室温度或指定预燃室氧燃质量比。");
  return {...inputs, use_target_preburner_temp:mode === "temperature" ? 1 : 0,
    use_target_preburner_mr:1};
}

export function pumpSpeedMode(field, inputs) {
  const value = inputs[field.pump_speed_key];
  return value === 0 ? "estimated" : Number.isFinite(value) && value > 0 ? "specified" : "invalid";
}

// UI-only choice: keep the established numeric input in saved cases and exports.
export function selectPumpSpeedMode(inputs, field, mode, specifiedRpm) {
  const key = field.pump_speed_key;
  if (!field.virtual || !/^(lox|fuel)_pump_speed_rpm_override$/.test(key || "") ||
      !["specified", "estimated"].includes(mode)) throw new Error("请选择有效的泵转速初选方式。");
  const rpm = Number.isFinite(inputs[key]) && inputs[key] > 0 ? inputs[key] : specifiedRpm;
  if (mode === "specified" && !(Number.isFinite(rpm) && rpm > 0))
    throw new Error("缺少有效的初选转速，请核对方案默认值。");
  return {...inputs, [key]:mode === "estimated" ? 0 : rpm};
}

export function turbineStageMode(field, inputs) {
  const value = inputs[field.turbine_stages_key];
  return value === 0 ? "estimated" : value === null || Number.isInteger(value) && value > 0 ? "specified" : "invalid";
}

// A fresh manual choice stays empty until the designer supplies a stage count.
// The virtual selector is never saved; zero retains its existing estimate meaning.
export function selectTurbineStageMode(inputs, field, mode, specifiedStages) {
  const key = field.turbine_stages_key;
  if (!field.virtual || !/^turb_(ox|fuel)_n_stages_user$/.test(key || "") ||
      !["specified", "estimated"].includes(mode)) throw new Error("请选择有效的涡轮级数初选方式。");
  const stages = Number.isInteger(inputs[key]) && inputs[key] > 0 ? inputs[key] : specifiedStages;
  return {...inputs, [key]:mode === "estimated" ? 0 : Number.isInteger(stages) && stages > 0 ? stages : null};
}

export function parameterCompatible(item, inputs) {
  return (!item.cycles || item.cycles.includes(inputs.cycle_type)) &&
    (!item.propellants || item.propellants.includes(inputs.propellant_pair || "lox_methane"));
}

function conditionMatches(condition, inputs) {
  const actual = preburnerStatusValue(condition.key, inputs[condition.key]);
  // Do not trim: even whitespace or "[]" selects the table path in the solver
  // and must be surfaced for correction, never silently treated as scalar input.
  if (condition.op === "empty") return actual == null || actual === "";
  if (condition.op === "eq") return actual === condition.value;
  if (condition.op === "neq") return actual !== condition.value;
  if (condition.op === "gt") return typeof actual === "number" && actual > condition.value;
  if (condition.op === "lte") return typeof actual === "number" && actual <= condition.value;
  return false;
}

function preburnerStatusValue(key, value) {
  // Old snapshots had numeric limits but no evidence classification. Rendering
  // their controls as provisional preserves the saved values without a migration.
  return /^(ox|fuel)_preburner_temp_limit_status$/.test(key) && value == null ? "provisional" : value;
}

export function parameterVisible(field, inputs) {
  if (field.hidden || !parameterCompatible(field, inputs)) return false;
  const rules = field.visibility;
  if (!rules) return true;
  if (rules.preburner_modes && !rules.preburner_modes.includes(preburnerMode(inputs))) return false;
  if (rules.all && !rules.all.every(condition => conditionMatches(condition, inputs))) return false;
  if (rules.any && !rules.any.some(condition => conditionMatches(condition, inputs))) return false;
  return true;
}

export function parameterPresentation(field, inputs) {
  const mode = preburnerMode(inputs), override = field.roles_by_mode?.[mode];
  const role = typeof override === "string" ? override : override?.role || field.role;
  const label = field.labels_by_mode?.[mode] || field.label;
  const roleLabel = (typeof override === "object" && override?.role_label) ||
    (override ? ROLE_LABELS[role] : field.role_label) || ROLE_LABELS[role] || "";
  return {...field, role, role_label:roleLabel, label,
    help:field.help_by_mode?.[mode] || field.help || field.tooltip || ""};
}

export function parameterValue(field, inputs) {
  if (field.key === PREBURNER_MODE_KEY && field.virtual) return preburnerMode(inputs);
  if (field.virtual && field.pump_speed_key) return pumpSpeedMode(field, inputs);
  if (field.virtual && field.turbine_stages_key) return turbineStageMode(field, inputs);
  const value = preburnerStatusValue(field.key, inputs[field.key]);
  return value == null ? "" : typeof value === "number" ? Number((value * (field.factor || 1)).toPrecision(12)) : value;
}

export function parameterLayoutDependency(key, groups) {
  return [PREBURNER_MODE_KEY, "use_target_preburner_temp", "use_target_preburner_mr", "cycle_type", "propellant_pair", "combustion_loss_model", "chamber_inner_wall_material", "wall_thermal_dataset_id", "wall_manufacturing_state"].includes(key) || groups.some(group => group.fields.some(field =>
    [...(field.visibility?.all || []), ...(field.visibility?.any || [])].some(condition => condition.key === key)));
}

function renderWallDatasetInfo(field, inputs) {
  if(field.key!=="wall_thermal_dataset_id" || !Array.isArray(field.dataset_catalog))return "";
  const dataset=field.dataset_catalog.find(item=>item.dataset_id===inputs.wall_thermal_dataset_id);
  if(!dataset)return '<p class="field-help">当前数据集未识别；不会自动换成另一种材料。</p>';
  const materialConflict=dataset.material_id && inputs.chamber_inner_wall_material && dataset.material_id!==inputs.chamber_inner_wall_material;
  const identityNote=materialConflict ? `<p class="notice warning" role="alert">内衬材料与默认热物性数据集不一致：配置为 ${escape(inputs.chamber_inner_wall_material)}，本数据集属于 ${escape(dataset.material_id)}。请在“推力室·结构与材料”选择对应内衬，或在此选择与原材料一致的数据集；新计算前必须修正。历史结果和当前草稿不会自动改写。</p>` : "";
  const state=inputs.wall_manufacturing_state || "unspecified";
  const manufacturing=dataset.manufacturing_state || {},expected=manufacturing.state_id;
  const match=state==="unspecified" ? "方案制造状态未确认" : state===expected ? "方案制造状态与数据匹配" : "方案制造状态与数据不匹配";
  const conductivity=dataset.conductivity_property || {};
  const range=conductivity.range_K;
  const rangeText=Array.isArray(range) && range.length===2 && range.every(Number.isFinite) ? `${range[0]}–${range[1]} K` : "未登记";
  const source=dataset.sources?.[0] || {};
  const sourceLabel=source.title || dataset.source_title || "未登记";
  const url=source.url || dataset.source_url || "";
  const sourceHtml=/^https:\/\//i.test(url) ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(sourceLabel)}</a>` : escape(sourceLabel);
  const capacity = dataset.heat_capacity_property;
  const capacityNote = !capacity ? "本数据集未登记可计算的比热曲线。"
    : capacity.statistic === "composition_weighted_estimate" ? "比热来自成分加权估算，并非该合金的独立实测比热。" : "比热作为独立物性记录，未参与当前稳态壁体储热。";
  const locations=(conductivity.source_locations||[]).map(x=>`<li>${escape(x)}</li>`).join("");
  const limitations=(dataset.limitations||[]).map(x=>`<li>${escape(x)}</li>`).join("");
  return `${identityNote}<p class="field-help wall-dataset-summary">导热率数据温区：${escape(rangeText)}。${escape(match)}。稳态计算使用 k(T)。超温时保留诊断结果并标记越界，不扩展数据有效温区。</p><details class="wall-dataset-evidence"><summary>数据来源、制造状态与应用范围</summary><p class="fine">${escape(manufacturing.label || "制造状态未登记")}</p><p class="fine">${sourceHtml}</p>${locations?`<ul class="fine">${locations}</ul>`:""}<p class="fine">${capacityNote} ${dataset.reference_density_kg_m3 == null ? "未登记参考密度。" : `参考密度：${escape(dataset.reference_density_kg_m3)} kg/m³；${escape(dataset.density_basis || "依据见来源")}。`} 登记热物性并不代表已启用壁体储热瞬态。</p>${limitations?`<ul class="fine">${limitations}</ul>`:""}<p class="fine">数据版本：${escape(dataset.dataset_id)}。数据温区不是材料允许使用温度；制造状态匹配也不等于强度、热疲劳或复用寿命验证。</p></details>`;
}

export function parameterSearchText(field, inputs, groupTitle = "") {
  const view = parameterPresentation(field, inputs);
  return [view.label, view.full_label, view.key, view.unit, view.role_label, view.help,
    view.provenance?.label, view.provenance?.detail, groupTitle].join(" ").toLocaleLowerCase();
}

export function renderParameterField(field, inputs, {compact = false} = {}) {
  const view = parameterPresentation(field, inputs), value = parameterValue(view, inputs);
  if(view.custom_editor === "channel_distribution")return "";
  if(view.read_only && view.key==="cooling_bartz_mode")return `<div class="cooling-methods-field" data-field="cooling_bartz_mode" data-role="model_assumption">${renderCoolingModel(inputs)}</div>`;
  const virtualMode = view.virtual && view.key === PREBURNER_MODE_KEY;
  const legacy = virtualMode && value === "legacy_free_split";
  const helpId = `parameter-help-${view.key}`;
  const help = (view.help ? `<span class="field-help" id="${escape(helpId)}">${escape(view.help)}</span>` : "") + fixedModeNote(view,view.help?null:helpId);
  const evidence = view.provenance?.label ? `<span class="field-provenance" title="${escape(view.provenance.detail || view.provenance.label)}">依据：${escape(view.provenance.label)}</span>` : "";
  const options = view.choices || [];
  const numeric = typeof view.default === "number" || ["number", "int"].includes(view.type);
  const inherited = view.nullable && Number.isFinite(inputs[view.fallback_key])
    ? `沿用统一预算：${Number((inputs[view.fallback_key] * (view.factor || 1)).toPrecision(8))} ${view.unit || ""}` : view.placeholder || "";
  const control = view.read_only ? `<output class="parameter-fixed-value"${help ? ` aria-describedby="${escape(helpId)}"` : ""}>${escape(options.find(choice=>String(value)===String(choice.value))?.label??value)}</output>` :
    view.choices ? `<select data-input="${escape(view.key)}"${help ? ` aria-describedby="${escape(helpId)}"` : ""}>` +
    (legacy ? '<option value="legacy_free_split" selected disabled>历史模式：自由分流（保留）</option>' :
      options.some(choice => String(value) === String(choice.value)) ? "" : `<option value="" disabled selected>${escape(value==="" && view.empty_choice_label ? view.empty_choice_label : `无效值：${value}`)}</option>`) +
    options.map(choice => `<option value="${escape(choice.value)}"${String(value) === String(choice.value) ? " selected" : ""}>${escape(choice.label)}</option>`).join("") + "</select>" :
    view.multiline ? `<textarea rows="${view.key.endsWith("_preburner_temp_limit_basis") ? 2 : 5}" data-input="${escape(view.key)}"${help ? ` aria-describedby="${escape(helpId)}"` : ""}>${escape(value)}</textarea>` :
    `<input type="${numeric ? "number" : "text"}"${numeric ? ` step="any"${view.nullable ? "" : " required"}` : ""} data-input="${escape(view.key)}" value="${escape(value)}"${inherited ? ` placeholder="${escape(inherited)}"` : ""}${help ? ` aria-describedby="${escape(helpId)}"` : ""}>`;
  return `<label data-field="${escape(view.key)}" data-role="${escape(view.role || "")}" title="${escape(view.full_label || view.label)}"><span class="parameter-label">${escape(view.label)}${view.unit ? ` <span class="unit">[${escape(view.unit)}]</span>` : ""}</span>${view.role_label && !compact ? `<span class="parameter-role">${escape(view.role_label)}</span>` : ""}${control}${compact ? "" : help + evidence}${legacy ? '<span class="parameter-legacy-note" role="note">当前方案保留旧版自由分流模式与参数。选择指定温度或指定氧燃质量比后才转换；加载时不会自动转换。</span>' : ""}</label>${renderWallDatasetInfo(view,inputs)}`;
}

function renderParameterNotes(fields, inputs) {
  const notes = fields.map(field => parameterPresentation(field, inputs)).filter(view => view.help || view.provenance?.label || view.analysis_modes?.fixed_hardware_steady?.description);
  if (!notes.length) return "";
  return `<details class="parameter-notes"><summary>参数说明与依据</summary><dl>${notes.map(view =>
    `<dt>${escape(view.label)}</dt><dd>${view.help ? `<span class="field-help" id="parameter-help-${escape(view.key)}">${escape(view.help)}</span>` : ""}${fixedModeNote(view,view.help?null:`parameter-help-${view.key}`)}${view.provenance?.label ? `<span class="field-provenance" title="${escape(view.provenance.detail || view.provenance.label)}">依据：${escape(view.provenance.label)}</span>` : ""}</dd>`
  ).join("")}</dl></details>`;
}

export function parameterGroups(groups, inputs) {
  return groups.filter(group => parameterCompatible(group, inputs)).map(group => ({...group,
    fields:group.fields.filter(field => parameterVisible(field, inputs)),
  })).filter(group => group.fields.length);
}

function renderChannelGeometryInfo(group, inputs) {
  if(group.fields.some(field=>field.custom_editor === "channel_distribution"))return "";
  if (!group.geometry_source_key || !(inputs.cooling_bartz_mode > 0)) return "";
  const raw = inputs[group.geometry_source_key];
  if (raw == null || raw === "") {
    const depth = inputs.cooling_channel_auto_size > 0 ? "入口速度估算深度" : "指定深度";
    return `<p class="fine channel-geometry-source">几何来源：标量规则（通道宽随型面半径变化，${depth}）。</p>`;
  }
  const note = '<p class="fine">当前保存的沿程表覆盖通道数、宽度、深度和热壁厚，自动定深与目标流速不生效。被覆盖的标量仍保留在方案中。此处只读展示原表；保存不会改写它。</p>';
  let rows;
  try {
    const data = JSON.parse(raw);
    rows = Array.isArray(data) ? data : data?.rows;
    if (!Array.isArray(rows) || !rows.length || rows.some(row => !row || typeof row !== "object" || Array.isArray(row))) throw new Error("invalid table");
  } catch {
    return `<div class="channel-geometry-source"><p class="notice warning">几何来源：沿程通道表，但保存内容无法展示为非空断点表。需修复原表后计算；不会自动回退为标量几何。</p>${note}</div>`;
  }
  const anchors = {injector:"喷注面", conv_start:"收敛起点", throat:"喉部", exit:"主喷管出口", x_mm:"轴向坐标"};
  const cell = (row, key) => Object.hasOwn(row, key) ? escape(row[key]) : "未填写";
  const body = rows.map(row => `<tr><td>${escape(anchors[row.anchor] || row.anchor || "未填写")}</td><td>${cell(row,"x_mm")}</td><td>${cell(row,"n")}</td><td>${cell(row,"w_mm")}</td><td>${cell(row,"h_mm")}</td><td>${cell(row,"t_mm")}</td></tr>`).join("");
  return `<div class="channel-geometry-source"><p class="fine">几何来源：沿程通道表（${rows.length} 个断点）。</p>${note}<details><summary>查看保存的通道几何</summary><p class="fine">x 从喷注面起算；物理锚点随型面定位。宽、深、壁厚沿 x 线性插值，表两端之外沿用端值。未填列采用解析器默认值，不继承被覆盖的标量；当前内核要求各断点通道数相同。几何适用性由运行时统一校验。</p><div class="table-wrap"><table><thead><tr><th>定位</th><th>x / mm</th><th>N</th><th>宽 / mm</th><th>深 / mm</th><th>壁厚 / mm</th></tr></thead><tbody>${body}</tbody></table></div></details></div>`;
}

export function renderParameterGroups(groups, inputs, expanded = new Set()) {
  return groups.map(group => {
    const standard = [], seeds = [], numeric = [];
    for (const field of group.fields) {
      (parameterPresentation(field, inputs).role === "initial_guess" ? seeds : field.advanced ? numeric : standard).push(field);
    }
    const initialHelp = group.initials_help || "用于启动求解，折叠仅为简化日常操作。初值会影响收敛过程；同一物理解分支充分收敛后，结果应在容差范围内一致。";
    const advanced = !seeds.length ? "" : group.initials_inline
      ? `<section class="parameter-subsection parameter-initials"><h3>${escape(group.initials_title || "启动估计")}</h3><p class="fine">${escape(initialHelp)}</p>${seeds.map(field => renderParameterField(field, inputs, {compact:true})).join("")}${renderParameterNotes(seeds, inputs)}</section>`
      : `<details class="parameter-initials" data-initials="${escape(group.id)}"${expanded.has(group.id) ? " open" : ""}><summary>高级设置 · 迭代初值（${seeds.length}）</summary><p class="fine">${escape(initialHelp)}</p>${seeds.map(field => renderParameterField(field, inputs)).join("")}</details>`;
    const numericKey = group.id + "-numerics";
    const numericOnly=numeric.every(field=>field.role==="solver_setting");
    const advancedTitle=group.advanced_title || (numericOnly ? "数值分辨率" : "补充设置");
    const advancedHelp=group.advanced_help || (numericOnly ? "分辨率影响离散误差；应比较网格加密结果，不能用作性能调参。" : "这些输入参与模型配置；完整方案与计算记录保留实际取值。");
    const numerics = numeric.length ? `<details class="parameter-initials" data-initials="${escape(numericKey)}"${expanded.has(numericKey) ? " open" : ""}><summary>高级设置 · ${escape(advancedTitle)}（${numeric.length}）</summary><p class="fine">${escape(advancedHelp)}</p>${numeric.map(field => renderParameterField(field, inputs)).join("")}</details>` : "";
    const contexts=Array.isArray(group.configuration_context) ? group.configuration_context : group.configuration_context ? [group.configuration_context] : [];
    const contextText=contexts.map(context=>`<p class="fine wall-configuration-context">${escape(context.label)}：${escape(context.labels?.[inputs[context.key]] || inputs[context.key] || "未指定")}。${escape(context.note)}</p>`).join("");
    const compact = group.compact === true || ["design", "inlet"].includes(group.id);
    const controls = group.pressure_workspace
      ? renderPressureWorkspace(group, inputs, field => renderParameterField(field, inputs, {compact:true}), selected => renderParameterNotes(selected, inputs))
      : renderParameterSections(group, standard, inputs, {compact,expanded});
    const notes = compact && !group.section_notes && !group.pressure_workspace ? renderParameterNotes(standard, inputs) : "";
    return `<fieldset class="parameter-section" data-group="${escape(group.id)}"><legend>${escape(group.title)}</legend>${group.summary ? `<p class="fine">${escape(group.summary)}</p>` : ""}${group.description && !compact ? `<p class="fine">${escape(group.description)}</p>` : ""}${group.id==="chamber_chemistry" ? renderChamberModel(inputs) : ""}${renderChannelGeometryInfo(group, inputs)}${controls}${contextText}${notes}${advanced}${numerics}</fieldset>`;
  }).join("");
}

function renderCoolingModelInputs(inputs) {
  const absolute = inputs.cooling_channel_roughness_m != null;
  const split = inputs.cooling_k_minor_inlet != null || inputs.cooling_k_minor_outlet != null;
  const missingBasis = inputs.cooling_htc_enhancement !== 1 && !String(inputs.cooling_htc_enhancement_basis || "").trim();
  return `<div class="channel-model-context"><p class="fine">粗糙度：${absolute ? "采用绝对等效粗糙度，各截面按当地水力直径换算 ks/Dh；原相对值保留但不生效。" : "采用沿程固定 ks/Dh；填写绝对等效粗糙度后切换。"} 局部阻力：${split ? "采用分别给定的入口与出口系数；原系数之和保留但不生效。" : "沿用原系数之和，入口与出口各取一半，分别使用当地动压。"}</p>${missingBasis ? '<p class="notice warning">喉部换热标定系数不为 1，但尚未登记标定依据。可保留方案进行敏感性比较，不能据此认定换热已验证。</p>' : ""}</div>`;
}

function renderParameterSections(group, fields, inputs, options = {}) {
  const render = field => renderParameterField(field, inputs, options);
  if (!Array.isArray(group.sections)) return fields.map(render).join("");
  const rendered=new Set(), sections=[];
  for (const section of group.sections) {
    if(["preburner-interfaces","preburner-results","cooling-input-summary"].includes(section.context)){
      sections.push(`<section class="parameter-subsection" data-parameter-section="${escape(section.id)}"><h3>${escape(section.title)}</h3><div data-${section.context}></div></section>`);
      continue;
    }
    const keys=new Set(Array.isArray(section.keys)?section.keys:[]);
    const selected=[...keys].map(key=>fields.find(field=>field.key===key&&!rendered.has(key))).filter(Boolean);
    if (!selected.length) continue;
    selected.forEach(field=>rendered.add(field.key));
    const fieldGroups=Array.isArray(section.field_groups)?section.field_groups:[];
    const groupedKeys=new Set(fieldGroups.flatMap(item=>item.keys||[]));
    const content=fieldGroups.length?`<div class="parameter-constraint-columns">${fieldGroups.map(item=>`<section class="parameter-constraint-side"><h4>${escape(item.title)}</h4>${selected.filter(field=>(item.keys||[]).includes(field.key)).map(render).join("")}</section>`).join("")}</div>`+selected.filter(field=>!groupedKeys.has(field.key)).map(render).join(""):selected.map(render).join("");
    const context = section.context === "cooling-channel-inputs" ? '<div data-cooling-channel-inputs data-field="cooling_channel_table_json"></div>'
      : section.context === "cooling-model-inputs" ? renderCoolingModelInputs(inputs) : "";
    const body=`${section.description?`<p class="fine">${escape(section.description)}</p>`:""}${context}${content}${group.section_notes?renderParameterNotes(selected,inputs):""}`;
    const foldKey=`${group.id}:${section.id}`;
    const constraintLabels={unset:"未设置",provisional:"设计暂定",documented:"依据已登记"};
    const status=group.id==="preburner_design"&&section.id==="limits"
      ? `<span class="parameter-optional-status">${[["ox","富氧"],["fuel","富燃"]].map(([side,label])=>`${label}：${constraintLabels[preburnerStatusValue(`${side}_preburner_temp_limit_status`,inputs[`${side}_preburner_temp_limit_status`])]||"状态无效"}`).join(" · ")}</span>` : "";
    sections.push(section.collapsed
      ? `<details class="parameter-optional" data-parameter-fold="${escape(foldKey)}" data-parameter-section="${escape(section.id)}"${options.expanded?.has(foldKey)?" open":""}><summary>${escape(section.title)}${status}</summary><div class="parameter-subsection">${body}</div></details>`
      : `<section class="parameter-subsection" data-parameter-section="${escape(section.id)}"><h3>${escape(section.title)}</h3>${body}</section>`);
  }
  // Search filtering and future schema additions must never drop an otherwise
  // visible control merely because it is not assigned to a subsection yet.
  return sections.join("")+fields.filter(field=>!rendered.has(field.key)).map(render).join("");
}

export function renderParameterEvidence(evidence, fields = []) {
  if (!Array.isArray(evidence) || !evidence.length) return "";
  const names = {published:"厂商公开值",historical_reference:"历史型号参考 · 本示例采用",engineering_assumption:"工程假设",numerical_initial_guess:"数值初值 · 数值初始化设置"};
  const httpsUrl = value => {
    if(typeof value!=="string"||!/^https:\/\//i.test(value))return false;
    try{const url=new URL(value);return url.protocol==="https:"&&Boolean(url.hostname)&&!url.username&&!url.password;}catch{return false;}
  };
  const rows = evidence.map(item => {
    const field = fields.find(candidate => candidate.key === item.name);
    const displayOverride=typeof item.value_display==="string"&&item.value_display.trim()!==""||typeof item.value_display==="number"&&Number.isFinite(item.value_display);
    const value = displayOverride ? item.value_display : typeof item.value_si === "number" && Number.isFinite(item.value_si) ?
      (item.value_si * (field?.factor || 1)).toLocaleString("zh-CN",{maximumSignificantDigits:10}) : "—";
    const external=httpsUrl(item.source_url),title=item.source_title||"来源标题未记录";
    const source = external ? `<a href="${escape(item.source_url)}" target="_blank" rel="noopener noreferrer">${escape(title)}</a>` : item.source_title ? escape(item.source_title) : "";
    const externalEvidence=["published","historical_reference"].includes(item.classification);
    const dates = [item.published_on||item.source_date ? `发布 ${item.published_on||item.source_date}` : "",
      item.checked_on ? `${externalEvidence?"资料核验":"说明记录"} ${item.checked_on}` : "",
      item.reviewed_on ? `说明更新 ${item.reviewed_on}` : ""].filter(Boolean).join(" · ");
    const assumption=item.classification==="engineering_assumption"&&!external?'<small class="field-provenance">项目设定 · 未登记外部数值依据</small>':"";
    const notes=[item.source_locator ? `来源定位：${item.source_locator}` : "",item.scope ? `支持范围：${item.scope}` : "",
      item.verification_note ? `核验说明：${item.verification_note}` : ""].filter(Boolean).map(text=>`<small class="field-provenance">${escape(text)}</small>`).join("");
    return `<tr><td>${escape(field?.label || item.name)}${item.group?`<small class="field-provenance">分组：${escape(item.group)}</small>`:""}</td><td>${escape(value)}${!displayOverride&&field?.unit ? ` ${escape(field.unit)}` : ""}</td><td>${escape(names[item.classification] || "待核实")}${assumption}${source ? `<br>${source}` : ""}${dates ? `<small class="field-provenance">${escape(dates)}</small>` : ""}</td><td>${escape(item.note || "")}${notes}${item.source_path?`<small class="field-provenance">本地代码位置：<code>${escape(item.source_path)}</code></small>`:""}</td></tr>`;
  }).join("");
  return `<details class="reference-evidence"><summary>示例参数依据与项目设定（${evidence.length}）</summary><p class="fine">以下条目说明示例采用值的性质与支持范围；工程假设和数值初始化设置不表示厂商实机参数。当前方案经过修改的值以保存的输入为准。</p><div class="table-wrap"><table><thead><tr><th>参数</th><th>示例采用值</th><th>性质与来源</th><th>说明与支持范围</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}
