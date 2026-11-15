// Presentation rules never migrate stored EngineInputs. Only an explicit mode
// change writes the two legacy flags, keeping drafts, imports and undo lossless.
import {renderPressureWorkspace} from "./pressure-design.js";
import {renderValveConfiguration} from "./valve-configuration.js";
import {thermoModelDecisionState} from "./thermochemistry-model.js";
import {renderPreburnerConditions,renderPreburnerLimitSetup,preburnerLimitSetupState} from "./preburner-design.js";
export {applyThermochemistryBaseline,selectThermochemistryModel} from "./thermochemistry-model.js";
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
  if(field.show_fixed_mode_note === false)return "";
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
  if(!savedSnapshot&&!Object.keys(inputs).length)return {...defaults};
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
  if(savedSnapshot||Object.keys(inputs).length){
    // An omitted historical setting must not inherit the new design preset.
    for(const [key,legacy] of Object.entries({gas_eos:"ideal-gas",combustion_loss_model:"legacy_temperature_rise"}))
      if(Object.hasOwn(defaults,key)&&!Object.hasOwn(inputs,key))result[key]=legacy;
    if(Object.hasOwn(defaults,"gas_species_policy")&&!Object.hasOwn(inputs,"gas_species_policy"))
      result.gas_species_policy=savedSnapshot?"full":"auto";
  }
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
  const eos = inputs.gas_eos ?? "ideal-gas";
  const fullPR = eos === "Peng-Robinson-Boston-Mathias";
  const restricted = inputs.gas_species_policy === "pr_supported_v1" || (["Peng-Robinson","Peng-Robinson-Boston-Mathias"].includes(eos) && (inputs.gas_species_policy ?? "auto") === "auto");
  const eosLabel = {"ideal-gas":"理想气体混合物", "Peng-Robinson":"PR 预燃室/涡轮 · 受限候选", "Peng-Robinson-Boston-Mathias":"PR–BM 全燃气流程 · 受限候选"}[eos] || `未识别：${eos}`;
  const spatialCooling = Boolean(inputs.cooling_bartz_mode);
  const chamber = legacyLoss ? "化学平衡后温升折减（历史兼容）" :
    inputs.combustion_loss_model === "adiabatic" ?
      (spatialCooling ? "化学平衡（给定压力与焓）" : "化学平衡（计入给定冷却吸热）") : "未识别的经验修正设置";
  const rows = [["甲烷热化学", names[id] || `未识别：${id}`], ["主燃烧室", chamber],
    ["主喷管热侧", spatialCooling ? "冻结组成 · 沿程冷却" : "冻结组成 · 绝热膨胀"],
    ["燃气状态方程", eosLabel], ["主室 / 喷管", fullPR ? "Peng–Robinson–Boston–Mathias" : "理想气体混合物"], ["燃气物种范围", restricted ? "PR 参数支持的受限集合" : "所选热化学文件的完整集合"]];
  const range = dataset?.common_temperature_range_K;
  const rangeText = Array.isArray(range) && range.length === 2 && range.every(Number.isFinite) ? `${range[0]}–${range[1]} K` : "未读取";
  const source = /^https:\/\//i.test(dataset?.source_url || "") ?
    `<a href="${escape(dataset.source_url)}" target="_blank" rel="noopener noreferrer">${escape(dataset.source_label || "数据来源")}</a>` : "来源未读取";
  const update = id === "gri30_highT" && dataset?.file_description?.includes("NASA TM-4513") ?
    "文件说明：Joseph Shepherd 依据 NASA TM-4513 更新高温热力学多项式，并非仅放宽温区。" : "";
  const historical = id === "gri30";
  const details = `<p class="fine">当前参数所用文件：${escape(dataset?.file || id)}；Cantera ${escape(dataset?.cantera_version || "版本未读取")}。${source}</p>` +
    `<p class="fine">热化学源文件 ${escape(dataset?.species_count ?? "未读取")} 种物种声明温区的交集：${escape(rangeText)}。交集外需逐物种检查，不代表所有物种都已越界。${escape(update)}</p>` +
    (eos === "Peng-Robinson" ? '<p class="fine">预燃室和涡轮采用 Peng–Robinson，主燃烧室及喷管保留理想气体模型。主室接收 PR 涡轮排气的实际焓与组成后重新求解，整机随之重新匹配；本候选不覆盖主室/喷管高温真实气体。</p>' : "") +
    (fullPR ? '<p class="fine">预燃室、冻结组成涡轮、主燃烧室、冷却燃气侧及喷管统一采用 Peng–Robinson–Boston–Mathias，按实际排气焓与组成重新匹配整机。Boston–Mathias 高温 alpha 修正以独立模型身份保存，不改写旧标准 PR 记录；仍需独立高压验证。</p>' : "") +
    (restricted ? '<p class="fine">本方案采用已登记 PR 参数的 16 种受限物种集合，实际物种清单随结果保存。该集合不覆盖缺少参数的自由基及完整高温解离；它不同于源文件的完整物种范围。在“热力学校核 → 理想气体 / PR”中，两端使用相同受限集合，对比全理想方案与来源记录所声明 PR 候选的整机匹配结果。</p>' : "") +
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
  if(inputs.cooling_bartz_mode===2)return `<section class="cooling-methods" aria-label="概略热负荷计算方法"><h3>概略受热面 → 沿程热流估算 → 吸热功率 → 循环反馈</h3><p>在热面参考壁温假设下计算燃气侧对流与简化辐射热流，按覆盖面积积分。燃气状态与喉部尺度随循环迭代，冷却剂出口由吸热功率与焓平衡确定；压降使用单独的工程估计。</p><p>参考壁温不是求得壁温或允许温度。本模式不需要通道宽深和材料，不判定冷却能力；后续详细模型再求实际壁温、冷却剂沿程状态及水力压降。</p><button type="button" data-setup-group="thermal_geometry">设置概略型面与冷却范围 →</button></section>`;
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
        <dt>数值求解</dt><dd>每段联立求解压力与比焓，再与燃气侧及循环迭代。轴向网格分辨率在“总体设计点计算 → 求解设置”中设置，应通过网格加密检查结果变化。</dd>
        <dt>适用范围</dt><dd>当前采用单相冷却剂模型。未求解壁体储热、周向温差、两相流、甲烷裂解与积碳；近临界换热恶化未采用专用关联式。材料数据温区与设计壁温限值分别校核。</dd></dl>
      </details>
    </section>`;
  }
  if(!budget)return '<p class="notice warning">冷却模型标识缺失或无效；请检查保存的方案。</p>';
  const methane=(inputs.propellant_pair || "lox_methane")==="lox_methane";
  return '<div class="cooling-model-summary"><b>历史兼容：给定吸热与压降预算</b><p class="field-help">本方案保留原计算方式和预算参数，没有沿程冷却解；加载、保存和查看记录不会自动转换模型。</p>'+
    (methane?'<button type="button" data-upgrade-cooling>建立准一维冷却草稿</button><p class="field-help">切换后请核对型面、通道与材料，保存为新版本并重新计算。既有记录保留。</p>':'<p class="field-help">煤油替代工质尚未接入统一准一维冷却；当前仅保留预算模型兼容能力。</p>')+'</div>';
}

function renderThermochemistryDecisions(group,inputs) {
  const model=thermoModelDecisionState(inputs);
  const eosField=group.fields.find(field=>field.key==="gas_eos");
  const selector=eosField?`<div class="thermochemistry-model-selector">${renderParameterField({...eosField,show_role:false,inline_help:"选择模型同时采用其物种配置，不附加温升折减；保存并重新计算后生效。"},inputs,{compact:true})}</div>`:"";
  const facts=[
    ["计算工具",model.backendLabel],
    ["燃烧计算","化学平衡（压力与焓约束）；预燃室与主燃烧室分别求解",model.loss==="adiabatic"?"combustion_loss_model":null],
    ["应用范围",model.coverageLabel],
    ["当前化学物种",`${model.speciesLabel}。参与平衡的候选物种，实际含量由计算确定。`,"gas_species_policy"],
    ["热化学数据",`${model.datasetLabel} · ${model.knownDataset?model.datasetId+".yaml":"标识待核对"}；物种标准态热数据采用分段 NASA7 多项式。`],
    ["膨胀组分处理","涡轮：冻结入口组成；冷却燃气侧与喷管：主室组成冻结"],
    ["纯推进剂物性","CoolProp HEOS：氧、甲烷的供给、泵与冷却剂状态。后端及文献来源见物性资料。"],
    ["气体输运","源文件的分子输运参数与 mixture-averaged 模型；与 NASA 热数据及 EOS 分开核查，高压输运尚未验证。"],
    ["壁面热物性",`${inputs.wall_thermal_dataset_id||"未指定内衬数据集"}；${inputs.cooling_bartz_mode===1?"详细冷却中使用导热率，制造状态与温区按结果核对":"当前模式不求解壁面导热，保留为部件草案"}`],
  ];
  if(!model.knownEos)facts.push(["物性模型标识",String(model.eos)]);
  if(model.lossEnabled){
    facts.push(["旧版本温度修正","化学平衡计算后按原系数折减主燃烧室温升","combustion_loss_model"]);
    facts.push(["原温升折减系数",Object.hasOwn(inputs,"combustion_efficiency")?String(inputs.combustion_efficiency):"未记录","combustion_efficiency"]);
  }else if(!model.knownLoss)facts.push(["温度修正设置",model.lossLabel,"combustion_loss_model"]);
  const invalid=!model.knownEos||!model.knownSpecies||!model.knownDataset||!model.knownLoss;
  const notice=model.runnableSupported?"":`<div class="thermochemistry-configuration-error" role="note"><b>${invalid?"计算模型设置无效，不能提交设计点。":"当前保存配置与所选模型不匹配，不能提交设计点。"}</b><p>${model.unsupportedFull?"PR 参数未覆盖完整物种范围。":""}原设置与已有计算记录保留。</p></div>`;
  const research=model.realGas?'<div class="thermochemistry-decision-limits" role="note"><b>研究模型</b><p>当前 PR 模型实现覆盖 16 种气相物种，不含 H、O、OH 等解离物种。求解较慢，高压混合物的预测尚未完成试验验证。</p></div>':"";
  const propellant=Object.hasOwn(inputs,"propellant_pair")?inputs.propellant_pair:"lox_methane";
  const canAdopt=inputs.cycle_type==="ffsc"&&propellant==="lox_methane";
  let action="";
  if(!model.runnableSupported&&canAdopt){
    action=model.knownEos&&model.knownDataset
      ? `<div class="thermochemistry-baseline-action"><button type="button" data-thermochemistry-apply="${escape(model.eos)}">应用所选模型配置</button><p>采用${model.realGas?"当前 PR 参数覆盖的 16 种候选物种":"源数据的完整 53 种候选物种"}，不附加温升折减；保留所选热化学数据。操作只更新当前设置，确认并保存为新版本后重新计算，已有记录不变。</p></div>`
      : '<div class="thermochemistry-baseline-action"><button type="button" data-thermochemistry-baseline>恢复默认模型配置</button><p>改用理想气体混合物、GRI30 高温数据及 53 种候选物种，不附加温升折减。操作只更新当前设置，确认并保存为新版本后重新计算，已有记录不变。</p></div>';
  }
  return `<fieldset class="parameter-section" data-group="${escape(group.id)}"><legend>${escape(group.title)}</legend><section class="thermochemistry-decision" aria-label="当前工质与热物性模型"><header class="thermochemistry-decision-header"${eosField?"":' data-field="gas_eos"'}><div><p class="fine">当前计算模型</p><h3>${escape(model.profileLabel)}</h3></div></header>${selector}<dl class="thermochemistry-decision-facts">${facts.map(([label,value,key])=>`<dt>${escape(label)}</dt><dd${key?` data-field="${escape(key)}"`:""}>${escape(value)}</dd>`).join("")}</dl>${notice}${research}${action}${model.datasetId==="gri30"?'<button type="button" data-thermo-upgrade>使用 high-T 建立新版草稿</button>':""}<div class="thermochemistry-reference-link"><button type="button" data-property-open="gas">数据来源、物种与性质曲线</button><button type="button" data-property-open="record">本次计算实际使用依据</button><button type="button" data-help-chapter="properties">物性方法与参考态说明</button></div></section></fieldset>`;
}
const ROLE_LABELS = {
  design_input:"设计输入", boundary:"边界条件", model_assumption:"模型假设",
  constraint:"约束", initial_guess:"迭代初值", component_sizing:"部件选型",
  solver_setting:"求解设置", legacy:"历史兼容", result:"计算结果",
};

export function designEnvironment(inputs) {
  const pressure = inputs?.ambient_pressure;
  if (pressure === 0) return {key:"vacuum", label:"真空"};
  if (pressure === 101325) return {key:"sea_level", label:"标准海平面"};
  if (Number.isFinite(pressure) && pressure > 0) return {key:"custom", label:"自定义背压"};
  return {key:"invalid", label:"环境压力无效"};
}

// A shortcut changes only the existing absolute-pressure input, never the case's
// thrust, Isp estimate, history or model identity.
export function selectDesignEnvironment(inputs, mode) {
  if (!["sea_level", "vacuum"].includes(mode)) throw new Error("请选择标准海平面或真空环境。");
  const pressure = mode === "sea_level" ? 101325 : 0;
  return inputs.ambient_pressure === pressure ? inputs : {...inputs, ambient_pressure:pressure};
}

export function refreshDesignEnvironment(root, inputs) {
  const environment = designEnvironment(inputs);
  for (const label of root.querySelectorAll("[data-design-environment-label]")) label.textContent = environment.label;
  for (const button of root.querySelectorAll("[data-design-environment]"))
    button.setAttribute("aria-pressed", String(button.dataset.designEnvironment === environment.key));
  return environment;
}

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
  const inherited = parameterInheritance(field, inputs);
  const value = inherited ? inherited.value : preburnerStatusValue(field.key, inputs[field.key]);
  return value == null ? "" : typeof value === "number" ? Number((value * (field.factor || 1)).toPrecision(12)) : value;
}

// Effective values are a presentation of the saved fallback contract. Opening
// or saving an unrelated field must not materialize nullable per-side overrides.
export function parameterInheritance(field, inputs = {}) {
  if (!field.nullable || !field.show_effective_value || !field.fallback_key) return null;
  const inherited = inputs[field.key] == null;
  const fallback = inputs[field.fallback_key], value = inherited ? fallback : inputs[field.key];
  const fallbackText = Number.isFinite(fallback)
    ? `${Number((fallback * (field.factor || 1)).toPrecision(12))}${field.unit ? ` ${field.unit}` : ""}` : "";
  const valid = Number.isFinite(value);
  const text = inherited
    ? valid ? `沿用共用设定：${fallbackText}。编辑后采用本路独立设定。`
      : "共用设定缺失或无效，请填写本路数值。"
    : !valid ? "本路独立设定无效，请填写有限数值。"
      : `本路独立设定。${fallbackText ? `清空后沿用共用设定：${fallbackText}。` : "共用设定缺失或无效，不能留空。"}`;
  return {inherited, value, valid, text};
}

export function refreshParameterInheritance(root, fields, inputs) {
  if (!root?.querySelectorAll) return;
  const byKey = new Map(fields.map(field => [field.key, field]));
  for (const note of root.querySelectorAll("[data-parameter-inheritance]")) {
    const field = byKey.get(note.dataset.parameterInheritance);
    if (!field) continue;
    const state = parameterInheritance(field, inputs);
    if (!state) continue;
    note.textContent = state.text;
    note.dataset.inherited = String(state.inherited);
    note.dataset.valid = String(state.valid);
  }
}

export function parameterLayoutDependency(key, groups) {
  return [PREBURNER_MODE_KEY, "use_target_preburner_temp", "use_target_preburner_mr", "cycle_type", "propellant_pair", "combustion_loss_model", "gas_eos", "gas_species_policy", "chamber_inner_wall_material", "wall_thermal_dataset_id", "wall_manufacturing_state"].includes(key) || groups.some(group => group.fields.some(field =>
    (field.show_effective_value && [field.key, field.fallback_key].includes(key)) ||
    [...(field.visibility?.all || []), ...(field.visibility?.any || [])].some(condition => condition.key === key)));
}

function renderWallDatasetInfo(field, inputs) {
  if(field.key!=="wall_thermal_dataset_id" || !Array.isArray(field.dataset_catalog))return "";
  const dataset=field.dataset_catalog.find(item=>item.dataset_id===inputs.wall_thermal_dataset_id);
  if(!dataset)return '<p class="field-help">当前数据集未识别；不会自动换成另一种材料。</p>';
  const materialConflict=dataset.material_id && inputs.chamber_inner_wall_material && dataset.material_id!==inputs.chamber_inner_wall_material;
  const identityNote=materialConflict ? `<p class="notice warning" role="alert">内衬材料与默认热物性数据集不一致：配置为 ${escape(inputs.chamber_inner_wall_material)}，本数据集属于 ${escape(dataset.material_id)}。请在本组的“燃气侧内衬材料”选择对应材料，或选择与原材料一致的数据集；材料、数据集和制造状态分别核对，新计算前必须修正。历史结果和当前草稿不会自动改写。</p>` : "";
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
  return [view.label, view.full_label, view.key, view.unit, view.role_label, view.help, view.inline_help,
    view.provenance?.label, view.provenance?.detail, groupTitle].join(" ").toLocaleLowerCase();
}

function renderParameterHelp(view, id) {
  const sections=Array.isArray(view.help_sections)?view.help_sections:[];
  const identity=id?` id="${escape(id)}"`:"";
  if(sections.length)return `<span class="field-help"${identity}>${sections.map(section=>`<span class="field-help"><b>${escape(section.label)}：</b>${escape(section.text)}</span>`).join("")}</span>`;
  return view.help?`<span class="field-help"${identity}>${escape(view.help)}</span>`:"";
}

export function renderParameterField(field, inputs, {compact = false, expanded = new Set()} = {}) {
  const view = parameterPresentation(field, inputs), value = parameterValue(view, inputs);
  if(view.custom_editor === "valve_configuration")return renderValveConfiguration(inputs,view,{expanded});
  if(view.custom_editor === "channel_distribution")return "";
  if(view.read_only && view.key==="cooling_bartz_mode")return `<div class="cooling-methods-field" data-field="cooling_bartz_mode" data-role="model_assumption">${renderCoolingModel(inputs)}</div>`;
  const virtualMode = view.virtual && view.key === PREBURNER_MODE_KEY;
  const legacy = virtualMode && value === "legacy_free_split";
  const helpId = `parameter-help-${view.key}`;
  const mainHelp = renderParameterHelp(view,helpId);
  const help = mainHelp + fixedModeNote(view,mainHelp?null:helpId);
  const inlineHelpId = `parameter-inline-help-${view.key}`;
  const inlineHelp = compact && view.inline_help ? `<span class="field-help parameter-inline-help" id="${escape(inlineHelpId)}">${escape(view.inline_help)}</span>` : "";
  const inheritance = parameterInheritance(view, inputs), inheritanceId = `parameter-inheritance-${view.key}`;
  const inheritanceNote = inheritance ? `<span class="field-help parameter-inheritance" id="${escape(inheritanceId)}" data-parameter-inheritance="${escape(view.key)}" data-inherited="${inheritance.inherited}" data-valid="${inheritance.valid}" aria-live="polite">${escape(inheritance.text)}</span>` : "";
  // Full definitions are in the manual. Reference only help actually rendered
  // beside this control, never an ID belonging to a different document view.
  const describedBy = [!compact && help ? helpId : "", inlineHelp ? inlineHelpId : "", inheritanceNote ? inheritanceId : ""].filter(Boolean).join(" ");
  const description = describedBy ? ` aria-describedby="${escape(describedBy)}"` : "";
  const evidence = view.provenance?.label ? `<span class="field-provenance" title="${escape(view.provenance.detail || view.provenance.label)}">依据：${escape(view.provenance.label)}</span>` : "";
  const options = view.choices || [];
  const numeric = typeof view.default === "number" || ["number", "int"].includes(view.type);
  const inherited = inheritance ? "清空后沿用共用设定" : view.nullable && Number.isFinite(inputs[view.fallback_key])
    ? `沿用统一预算：${Number((inputs[view.fallback_key] * (view.factor || 1)).toPrecision(8))} ${view.unit || ""}` : view.placeholder || "";
  const control = view.read_only ? `<output class="parameter-fixed-value"${description}>${escape(options.find(choice=>String(value)===String(choice.value))?.label??value)}</output>` :
    view.choices ? `<select data-input="${escape(view.key)}"${description}>` +
    (legacy ? '<option value="legacy_free_split" selected disabled>历史模式：自由分流（保留）</option>' :
      options.some(choice => String(value) === String(choice.value)) ? "" : `<option value="" disabled selected>${escape(value==="" && view.empty_choice_label ? view.empty_choice_label : `无效值：${value}`)}</option>`) +
    options.map(choice => `<option value="${escape(choice.value)}"${String(value) === String(choice.value) ? " selected" : ""}>${escape(choice.label)}</option>`).join("") + "</select>" :
    view.multiline ? `<textarea rows="${view.key.endsWith("_preburner_temp_limit_basis") ? 2 : 5}" data-input="${escape(view.key)}"${description}>${escape(value)}</textarea>` :
    `<input type="${numeric ? "number" : "text"}"${numeric ? ` step="any"${view.nullable ? "" : " required"}` : ""} data-input="${escape(view.key)}" value="${escape(value)}"${inherited ? ` placeholder="${escape(inherited)}"` : ""}${description}>`;
  return `<label data-field="${escape(view.key)}" data-role="${escape(view.role || "")}" title="${escape(view.full_label || view.label)}"><span class="parameter-label">${escape(view.label)}${view.unit ? ` <span class="unit">[${escape(view.unit)}]</span>` : ""}</span>${view.role_label && (!compact || view.show_role) ? `<span class="parameter-role">${escape(view.role_label)}</span>` : ""}${control}${inheritanceNote}${compact ? inlineHelp : help + evidence}${legacy ? '<span class="parameter-legacy-note" role="note">当前方案保留旧版自由分流模式与参数。选择指定温度或指定氧燃质量比后才转换；加载时不会自动转换。</span>' : ""}</label>${renderWallDatasetInfo(view,inputs)}`;
}

function renderParameterNotes(fields, inputs, groupId) {
  const notes = fields.map(field => parameterPresentation(field, inputs)).filter(view => view.help || view.help_sections?.length || view.provenance?.label || view.analysis_modes?.fixed_hardware_steady?.description);
  if (!notes.length) return "";
  return `<div class="parameter-help-link"><button type="button" data-help-chapter="${escape(`parameter:${groupId}`)}">参数定义与依据</button></div>`;
}

// Read-only documentation uses the same presentation metadata as the editor.
// It deliberately does not render custom editors, selected values or actions.
export function renderParameterReference(group, inputs = {}) {
  if(!group)return '<p class="fine">尚未选择参数组。</p>';
  const notes=(Array.isArray(group.fields)?group.fields:[]).filter(field=>parameterVisible(field,inputs)).map(field=>parameterPresentation(field,inputs));
  const introduction=[group.summary,group.description,group.notes_intro,group.initials_help].filter(Boolean);
  const sections=(Array.isArray(group.sections)?group.sections:[]).filter(section=>section.description).map(section=>`<p class="fine"><b>${escape(section.title)}：</b>${escape(section.description)}</p>`).join("");
  const entries=notes.map(view=>{
    const definition=renderParameterHelp(view)|| (view.inline_help?`<span class="field-help">${escape(view.inline_help)}</span>`:"");
    const source=[view.provenance?.label,view.provenance?.detail].filter(Boolean);
    const provenance=[...new Set(source)].map(text=>`<span class="field-provenance">${escape(text)}</span>`).join("");
    const choices=Array.isArray(view.choices)&&view.choices.length?`<p class="fine">${view.read_only?"记录取值":"可选项"}：${view.choices.map(choice=>escape(choice.label)).join("；")}。</p>`:"";
    return `<dt>${escape(view.full_label||view.label)}${view.unit?` <span class="unit">[${escape(view.unit)}]</span>`:""}${view.role_label?` <span class="parameter-role">${escape(view.role_label)}</span>`:""}</dt><dd>${definition||'<span class="field-help">详细定义尚未登记。</span>'}${fixedModeNote(view)}${provenance?`<div class="parameter-reference-source"><b>依据与来源</b>${provenance}</div>`:""}${choices}</dd>`;
  }).join("");
  return `${introduction.map(text=>`<p class="fine">${escape(text)}</p>`).join("")}${sections}<dl class="parameter-reference">${entries}</dl>${notes.length?"":'<p class="fine">当前模式没有适用参数。</p>'}`;
}

export function parameterGroups(groups, inputs) {
  const configuration = group => group.id === "cycle" && group.fields.every(field => field.read_only);
  const visible = groups.filter(group => configuration(group) || parameterCompatible(group, inputs)).map(group => ({...group,
    fields:configuration(group) ? group.fields.filter(field => !field.hidden) : group.fields.filter(field => parameterVisible(field, inputs)),
  }));
  const initialization = visible.find(group => group.collect_initial_guesses);
  if(initialization){
    const keys=new Set(initialization.fields.map(field=>field.key));
    for(const group of visible){
      if(group===initialization)continue;
      group.fields=group.fields.filter(field=>{
        if(parameterPresentation(field,inputs).role!=="initial_guess")return true;
        if(!keys.has(field.key)){initialization.fields.push({...field,initialization_origin:group.id});keys.add(field.key);}
        return false;
      });
    }
  }
  for(const group of visible){
    if(group.workflow_stage!=="components")continue;
    group.deferred_design=group.design_dependency==="sizing" || (group.design_dependency==="cooling" && inputs.cooling_bartz_mode!==1);
  }
  for(const group of visible)if(group.id==="thermal_geometry" && inputs.cooling_bartz_mode===0){group.deferred_design=true;group.requires_review=false;}
  for(const group of visible)if(group.optional_configuration){
    const active=["ox","fuel"].filter(side=>["provisional","documented"].includes(inputs[`${side}_preburner_temp_limit_status`]));
    group.configuration_label=active.length?`已启用 ${active.length} 项限值`:"未设置限值";
  }
  return visible.filter(group => group.fields.length || group.shared_geometry_group).map(group=>group.numerical_configuration?{...group,
    configuration_label:group.fields.every(field=>Object.is(inputs[field.key],field.default))?"采用默认":"已自定义",
  }:group);
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

function renderEngineConfiguration(group, inputs, embedded = false, expanded = false) {
  // Configuration identity belongs to the loaded scheme. Do not substitute
  // current defaults for missing or unsupported historical identities.
  const supported = inputs?.cycle_type === "ffsc" && inputs?.propellant_pair === "lox_methane";
  const identities = [
    ["propellant_pair", "推进剂组合", "lox_methane", "液氧／甲烷"],
    ["cycle_type", "循环形式", "ffsc", "全流量补燃循环（FFSC）"],
  ];
  const facts = identities.map(([key, label, recognized, description]) => {
    const value = inputs?.[key];
    const display = value === recognized ? description : value == null || value === "" ? "未记录" : String(value);
    return `<div data-field="${key}" data-role="configuration"><dt id="configuration-label-${key}">${label}</dt><dd><output aria-labelledby="configuration-label-${key}">${escape(display)}</output></dd></div>`;
  }).join("");
  const explanation = supported
    ? `<section class="engine-configuration-section" aria-labelledby="configuration-shafts"><h3 id="configuration-shafts">轴系驱动关系</h3><p>氧侧与燃料侧采用独立轴系，每侧涡轮与对应主泵同轴。</p><dl class="engine-configuration-shafts"><div><dt>氧侧轴系</dt><dd>富氧预燃燃气驱动氧侧涡轮，涡轮轴功用于驱动液氧主泵。</dd></div><div><dt>燃料侧轴系</dt><dd>富燃预燃燃气驱动燃料侧涡轮，涡轮轴功用于驱动甲烷主泵。</dd></div></dl></section>
      <section class="engine-configuration-section" aria-labelledby="configuration-gas-path"><h3 id="configuration-gas-path">燃气通路</h3><p>两路预燃燃气分别经对应涡轮膨胀做功，随后进入主燃烧室混合并完成燃烧，燃气经主喷管排出。</p><p>系统连接关系见工程视图区。部件状态与流量取自计算记录，需在设计点计算完成后查看。</p></section>
      <section class="engine-configuration-scope" aria-labelledby="configuration-scope"><h3 id="configuration-scope">建模范围</h3><p>当前设计工作区采用液氧／甲烷全流量补燃构型，循环形式与轴系连接为给定条件。尚不支持切换循环形式或自由装配部件拓扑。</p><p>总体设计采用系统级集中参数热力匹配；冷却沿程与喷管由各自模型计算，部件几何用于概念与初步设计。这里无需选择全局二维或三维。</p><p>本页定义设计点目标与环境边界。物性、效率和部件设置通过任务树或系统图进入；数值方法与初值在“设计点求解”中设置。</p></section>`
    : `<section class="engine-configuration-scope" aria-labelledby="configuration-scope"><h3 id="configuration-scope">构型信息待核对</h3><p>方案记录的构型信息不完整，或超出当前设计工作区支持范围。此处保留原始标识；轴系驱动关系与燃气通路暂不作说明。</p></section>`;
  const content=`<p class="engine-configuration-intro">当前发动机定义 · 方案构型为给定条件</p><dl class="engine-configuration-facts">${facts}</dl><details class="engine-definition-help" data-definition-help="engine-definition"${expanded?" open":""}><summary>循环原理与建模范围</summary>${explanation}</details>`;
  return embedded?`<section class="engine-configuration engine-definition" aria-label="发动机定义">${content}</section>`:`<fieldset class="parameter-section engine-configuration" data-group="${escape(group.id)}"><legend>${escape(group.title)}</legend>${content}</fieldset>`;
}

export function renderParameterGroups(groups, inputs, expanded = new Set()) {
  return groups.map(group => {
    if (group.id === "cycle") return renderEngineConfiguration(group, inputs);
    if (group.id === "chamber_chemistry") return renderThermochemistryDecisions(group,inputs);
    if(group.shared_geometry_group && !group.fields.length)return `<fieldset class="parameter-section" data-group="${escape(group.id)}"><legend>${escape(group.title)}</legend><p>总体估算与部件设计共用同一份型面及冷却范围。请在共享定义中修改，保存后重新计算以更新尺寸与热负荷。</p><button type="button" data-setup-group="${escape(group.shared_geometry_group)}">查看及修改概略型面与冷却范围 →</button><p class="fine">通道宽深、壁厚和材料在部件设计中单独定义。共享型面不表示硬件已冻结或冷却能力已校核。</p></fieldset>`;
    const standard = [], seeds = [], numeric = [];
    const primaryInitials = new Set(Array.isArray(group.primary_initials) ? group.primary_initials : []);
    for (const field of group.fields) {
      if(group.engine_definition && field.read_only)continue;
      (group.collect_initial_guesses || primaryInitials.has(field.key) ? standard : parameterPresentation(field, inputs).role === "initial_guess" ? seeds : field.advanced ? numeric : standard).push(field);
    }
    const initialHelp = group.initials_help || "用于启动求解，折叠仅为简化日常操作。初值会影响收敛过程；同一物理解分支充分收敛后，结果应在容差范围内一致。";
    const advanced = !seeds.length ? "" : group.initials_inline
      ? `<section class="parameter-subsection parameter-initials"><h3>${escape(group.initials_title || "启动估计")}</h3><p class="fine">${escape(initialHelp)}</p>${seeds.map(field => renderParameterField(field, inputs, {compact:true})).join("")}${renderParameterNotes(seeds, inputs, group.id)}</section>`
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
      ? renderPressureWorkspace(group, inputs, field => renderParameterField(field, inputs, {compact:true}), selected => renderParameterNotes(selected, inputs, group.id))
      : group.id === "design" && standard.some(field => field.key === "ambient_pressure")
        ? renderDesignInputs(group, standard, inputs, {compact,expanded})
      : renderParameterSections(group, standard, inputs, {compact,expanded});
    const notes = (compact && !group.section_notes && !group.pressure_workspace ? renderParameterNotes(standard, inputs, group.id) : "") + (group.id==="cooling" && inputs.cooling_bartz_mode===2 ? renderCoolingModel(inputs) : "");
    const related=group.initialization_link?'<div class="parameter-related"><button type="button" data-setup-group="initialization">查看相关迭代初值 →</button></div>':group.collect_initial_guesses?'<div class="parameter-related"><button type="button" data-setup-group="design">发动机定义与设计工况</button><button type="button" data-setup-group="preburner_design">预燃室热力匹配条件</button></div>':"";
    return `<fieldset class="parameter-section" data-group="${escape(group.id)}"><legend>${escape(group.title)}</legend>${group.engine_definition?renderEngineConfiguration(group,inputs,true,expanded.has("engine-definition")):""}${group.summary ? `<p class="fine">${escape(group.summary)}</p>` : ""}${group.description && !compact ? `<p class="fine">${escape(group.description)}</p>` : ""}${renderChannelGeometryInfo(group, inputs)}${controls}${contextText}${notes}${advanced}${numerics}${related}</fieldset>`;
  }).join("");
}

function renderDesignInputs(group, fields, inputs, options) {
  const environment = designEnvironment(inputs);
  const pressure = fields.find(field => field.key === "ambient_pressure");
  const primary = ["thrust","pc","of_ratio","target_isp"].map(key => fields.find(field => field.key === key)).filter(Boolean);
  const primaryKeys = new Set(["ambient_pressure", ...primary.map(field => field.key)]);
  const shortcuts = [["sea_level","标准海平面"],["vacuum","真空"]].map(([key,label]) =>
    `<button type="button" data-design-environment="${key}" aria-pressed="${environment.key === key}">${label}</button>`).join("");
  return `<section class="design-environment" aria-label="设计工作环境"><div class="design-environment-summary"><span class="fine">当前设计环境</span><strong data-design-environment-label aria-live="polite">${escape(environment.label)}</strong><div class="design-environment-actions" role="group" aria-label="环境快捷选择">${shortcuts}</div></div>${renderParameterField(pressure,inputs,options)}<p class="fine design-environment-note">环境压力按绝对压力输入（MPa），可直接填写自定义背压。目标推力对应当前设计环境，切换环境后应重新核定；总流量的数值估计在“总体设计点计算 → 初始化”中管理。</p></section>`+
    `<div class="design-primary-inputs">${primary.map(field => renderParameterField(field,inputs,options)).join("")}</div>`+
    (group.section_notes ? renderParameterNotes([pressure,...primary],inputs,group.id) : "")+
    renderParameterSections(group,fields.filter(field => !primaryKeys.has(field.key)),inputs,options);
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
    if(section.context==="cooling-input-summary"){
      sections.push(`<section class="parameter-subsection" data-parameter-section="${escape(section.id)}"><h3>${escape(section.title)}</h3><div data-${section.context}></div></section>`);
      continue;
    }
    const keys=new Set(Array.isArray(section.keys)?section.keys:[]);
    const selected=[...keys].map(key=>fields.find(field=>field.key===key&&!rendered.has(key))).filter(Boolean);
    if (!selected.length) continue;
    selected.forEach(field=>rendered.add(field.key));
    const fieldGroups=Array.isArray(section.field_groups)?section.field_groups:[];
    const groupedKeys=new Set(fieldGroups.flatMap(item=>item.keys||[]));
    const content=fieldGroups.length?`<div class="parameter-constraint-columns">${fieldGroups.map(item=>{
      const sideFields=selected.filter(field=>(item.keys||[]).includes(field.key));
      const preburnerSide=["preburner_design","engineering_constraints"].includes(group.id)&&["ox","fuel"].includes(item.side);
      const basis=preburnerSide&&sideFields.find(field=>field.key.endsWith("_temp_limit_basis"));
      const body=sideFields.filter(field=>field!==basis).map(render).join("");
      const basisKey=`${group.id}:${item.side}:basis`;
      const setup=preburnerSide?preburnerLimitSetupState(inputs,item.side):null;
      return `<section class="parameter-constraint-side"${preburnerSide?` data-preburner-limit-side="${escape(item.side)}"`:""}><h4>${escape(item.title)}</h4>${body}${preburnerSide?renderPreburnerLimitSetup(inputs,item.side):""}${basis?`<details class="preburner-basis" data-parameter-fold="${escape(basisKey)}"${options.expanded?.has(basisKey)||setup.needsBasis?" open":""}><summary>${escape(setup.basisLabel)}</summary>${render(basis)}</details>`:""}</section>`;
    }).join("")}</div>`+selected.filter(field=>!groupedKeys.has(field.key)).map(render).join(""):selected.map(render).join("");
    const context = section.context === "cooling-channel-inputs" ? '<div data-cooling-channel-inputs data-field="cooling_channel_table_json"></div>'
      : section.context === "cooling-model-inputs" ? renderCoolingModelInputs(inputs)
      : section.context === "preburner-conditions" ? renderPreburnerConditions(preburnerMode(inputs)) : "";
    const body=`${section.description?`<p class="fine">${escape(section.description)}</p>`:""}${context}${content}${group.section_notes?renderParameterNotes(selected,inputs,group.id):""}`;
    const foldKey=`${group.id}:${section.id}`;
    sections.push(section.collapsed
      ? `<details class="parameter-optional" data-parameter-fold="${escape(foldKey)}" data-parameter-section="${escape(section.id)}"${options.expanded?.has(foldKey)?" open":""}><summary>${escape(section.title)}</summary><div class="parameter-subsection">${body}</div></details>`
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
