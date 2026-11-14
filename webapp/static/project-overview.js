// Scheme-level orientation. All evidence comes from immutable saved records;
// opening this page never changes inputs, runs a solver or binds hardware.
import {numericalDisplayState} from "./convergence.js";
import {recordModelValidity} from "./model-validity.js";

const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const fmt = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits: 6}) : "—";
const object = value => value && typeof value === "object" && !Array.isArray(value);
const canonical = value => typeof value === "number" && !Number.isFinite(value) ? {nonfinite:String(value)} : Array.isArray(value) ? value.map(canonical) : object(value)
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const equal = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
const savedInputs = record => record?.inputs ?? record?.result?.submitted_inputs ?? record?.result?.calculation?.inputs ?? record?.result?.inputs;
const revision = value => value == null ? "版本未记录" : `v${value}`;
const recordStatuses = {queued:"等待计算",running:"计算中",succeeded:"计算已完成",failed:"执行失败",cancelled:"已取消",timed_out:"计算超时",interrupted:"服务中断"};
const recordDateFormat = new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"});
function recordTime(value) {
  if(!finite(value))return "未记录";
  const date=new Date(value * 1000);
  if(!Number.isFinite(date.getTime()))return "未记录";
  const parts=Object.fromEntries(recordDateFormat.formatToParts(date).map(part=>[part.type,part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}
function renderSelectedRecord(record,open=false) {
  if(!record)return '<b>尚无计算记录</b><small>运行总体设计点后生成记录</small>';
  return `<b>设计点计算 · 方案 ${escape(revision(record.revision))}</b>
    <small>提交：${recordTime(record.created)}（北京时间）</small>
    <small>执行状态：${escape(recordStatuses[record.status] || "状态未记录")}</small>
    <details class="project-record-details"${open?" open":""}><summary>任务时间与记录编号</summary><div>
      <small>提交人：${escape(record.author || "未记录")}</small>
      ${record.started!=null?`<small>任务开始：${recordTime(record.started)}（北京时间）</small>`:""}
      ${record.finished!=null?`<small>任务结束：${recordTime(record.finished)}（北京时间）</small>`:""}
      <small>内部记录编号（UUID）</small><code>${escape(record.id || "未记录")}</code>
      <small>用于准确查找和关联这一次计算。同一方案版本可有多次计算，各自保留独立编号。</small>
    </div></details>`;
}

const supplyFallbacks = Object.fromEntries(["ox", "fuel"].flatMap(side => [
  [`${side}_tank_pressure_pa`, "tank_pressure_pa"],
  [`${side}_boost_outlet_pressure_pa`, "inlet_pressure"],
  [`${side}_feed_line_dp_pa`, "feed_line_dp_pa"],
  [`${side}_boost_pump_efficiency`, "boost_pump_efficiency"],
]));
function supplyComparisonInputs(inputs) {
  if (!object(inputs)) return {inputs, unresolved:[]};
  const projected = {...inputs}, unresolved = [];
  for (const [key, fallback] of Object.entries(supplyFallbacks)) {
    if (!Object.hasOwn(inputs, key) && !Object.hasOwn(inputs, fallback)) continue;
    // Resolve within each immutable snapshot, never from today's defaults or
    // the opposite snapshot. Empty strings and non-finite values are explicit
    // invalid edits and must not inherit a plausible historical value.
    const value = inputs[key] == null ? inputs[fallback] : inputs[key];
    if (value == null) { delete projected[key]; unresolved.push(key); }
    else projected[key] = value;
  }
  return {inputs:projected, unresolved};
}

export function compareProjectInputs(draft, saved, groups = []) {
  const draftSupply = supplyComparisonInputs(draft), savedSupply = supplyComparisonInputs(saved);
  draft = draftSupply.inputs; saved = savedSupply.inputs;
  const draftKeys = object(draft) ? Object.keys(draft).filter(key => draft[key] !== undefined) : [];
  const savedKeys = object(saved) ? Object.keys(saved).filter(key => saved[key] !== undefined) : [];
  const missingKeys = [...new Set([...draftKeys.filter(key => !savedKeys.includes(key)), ...savedSupply.unresolved])];
  const missingDraftKeys = [...new Set([...savedKeys.filter(key => !draftKeys.includes(key)), ...draftSupply.unresolved])];
  const known = Boolean(object(draft) && object(saved) && draftKeys.length && savedKeys.length);
  // Missing historical fields are absent evidence, not observed changes. Only
  // common persisted fields can prove a difference; defaults cannot prove a match.
  const changedKeys = known ? draftKeys.filter(key => savedKeys.includes(key) && !equal(draft[key], saved[key])) : [];
  const changedGroups = groups.map(group => {
    // Read-only model decisions remain part of the owning group's input identity.
    const context = group.configuration_context ? [group.configuration_context].flat() : [];
    const fields = [...new Set([...(group.fields || []), ...context]
      .filter(field => changedKeys.includes(field.key)).map(field => field.key))];
    return {id:group.id, title:group.title, fields};
  })
    .filter(group => group.fields.length);
  return {known, complete:known && !missingKeys.length && !missingDraftKeys.length,
    missingKeys, missingDraftKeys, changedKeys, changedGroups};
}

function constraintsState(result) {
  const assessment = result?.assessment || {}, checks = assessment.checks || [];
  if (assessment.status === "fail" || checks.some(check => check.status === "fail"))
    return {state:"fail", label:"存在未通过项", detail:"保存记录中存在未通过的工程约束，请定位具体判据。"};
  if (assessment.scope !== "system" || !(result?.calculation_scope?.version >= 3))
    return {state:"unknown", label:"待确认", detail:"缺少当前范围的系统约束证据；历史校核通过不能替代当前评估。"};
  const inputs = result.calculation?.inputs || result.submitted_inputs || {};
  const uncertainLimits = checks.some(check => {
    const side = /^(ox|fuel)_preburner_T_combustion_K$/.exec(check.key || "")?.[1];
    if (!side) return false;
    const status = check.limit_status ?? inputs[`${side}_preburner_temp_limit_status`] ?? "provisional";
    const basis = check.limit_basis ?? inputs[`${side}_preburner_temp_limit_basis`];
    const limit = check.limit ?? inputs[`${side}_preburner_temp_limit`];
    return status !== "documented" || !String(basis || "").trim() || !finite(limit) || limit <= 0;
  });
  if (assessment.status === "pass" && checks.length && checks.every(check => check.status === "pass") && !uncertainLimits)
    return {state:"pass", label:"已检查项通过", detail:"仅表示本记录列出的系统约束通过，不代表整机验证或寿命认证。"};
  return {state:"unknown", label:"待核实", detail:uncertainLimits ? "预燃室温度上限仍缺少确定依据，需核实后重新评估。" : "存在未评估项或缺少完整约束明细。"};
}

function inputDimension(readiness) {
  const phase = readiness?.phase;
  return {id:"inputs", title:"输入完整性", state:phase === "ready" ? "pass" : phase === "invalid" ? "fail" : "unknown",
    label:({ready:"可计算", invalid:"存在问题", review:"待确认", pending:"检查中", unavailable:"检查未完成"})[phase] || "待检查",
    detail:phase === "ready" ? "当前草稿已通过输入检查；计算结果需单独检查收敛与约束。"
      : phase === "review" ? `${readiness.pendingReviewCount || 0} 个参数分组待确认。`
      : phase === "invalid" ? "请修正下面列出的输入问题。" : "请进入“输入检查与运行”查看当前草稿的检查状态。", view:"design_run"};
}

function modelIdentities(inputs = {}, result = null) {
  const model = result?.environment?.gas_model || result?.science_environment?.gas_model || {};
  const eosVersion = (typeof model.eos_model === "string" ? model.eos_model : model.eos_model?.version) || model.version;
  return [
    ["燃气状态方程", inputs.gas_eos],
    ["热化学数据集", inputs.methane_thermo_dataset],
    ["燃气物种范围", inputs.gas_species_policy],
    ["冷热侧物性方法", inputs.hot_side_thermo_method],
    ...(eosVersion ? [["模型版本", eosVersion]] : []),
    ...(model.mechanism ? [["实际加载数据文件", model.mechanism]] : []),
    ...(model.cantera_version ? [["Cantera 版本", model.cantera_version]] : []),
    ...(model.mechanism_sha256 ? [["数据文件 SHA-256", model.mechanism_sha256]] : []),
  ].map(([label, value]) => ({label, value:value == null ? "未记录" : String(value)}));
}

export function buildProjectWorkflow({project = null, draft = null, record = null, readiness = null, groups = [], jobs = []} = {}) {
  const foreign = Boolean(record?.project_id && project?.id && record.project_id !== project.id);
  const ownedRecord = foreign ? null : record;
  const result = ownedRecord?.result || null;
  const snapshot = ownedRecord ? savedInputs(ownedRecord) : null;
  const comparison = compareProjectInputs(draft, snapshot, groups);
  const validity = recordModelValidity(ownedRecord);
  const state = foreign ? "foreign" : !ownedRecord ? "missing" : validity.reuse_blocked ? "invalid"
    : !comparison.known ? "unknown" : comparison.changedKeys.length ? "stale" : comparison.complete ? "current" : "unknown";
  const freshness = {...comparison, state,
    label:({foreign:"记录不属于当前方案", missing:"尚无计算记录", invalid:"模型已撤回", unknown:"版本一致性待确认", stale:"结果待更新", current:"输入与记录一致"})[state],
    reason:({foreign:"请从当前方案选择计算记录。", missing:"完成需求、模型和求解设置后，在“输入检查与运行”核对当前草稿并提交首个设计点计算。",
      invalid:validity.reason || "此记录的物理模型已撤回，请重新计算。", unknown:`保存记录缺少完整可比输入快照${comparison.missingKeys.length ? `（${comparison.missingKeys.length} 项当前输入未记录）` : ""}，不能判定它与当前草稿一致。`,
      stale:`${comparison.changedKeys.length} 项输入与所选记录不同。${comparison.missingKeys.length ? `另有 ${comparison.missingKeys.length} 项当前输入未记录。` : ""}旧结果保留；依赖该设计点的部件初选与运行分析需要重新核对。`,
      current:"当前计算输入与记录快照一致。方案名称、说明或保存版本的变化不等于计算输入变化。"})[state]};
  const numerical = result ? numericalDisplayState(result) : null;
  const constraints = result ? constraintsState(result) : {state:"unknown", label:"未评估", detail:"尚无系统约束结果。"};
  const dimensions = [inputDimension(readiness),
    {id:"numerical", title:"数值收敛", state:numerical?.status || "unknown", label:numerical?.label || "未计算", detail:result ? "来自所选记录的分层收敛证据，任务完成状态不能替代收敛判据。" : "运行设计点后检查残差与各迭代层。", view:"solver_diagnostics"},
    {id:"constraints", title:"工程约束", ...constraints, view:"results_overview"},
    {id:"evidence", title:"模型验证证据", state:validity.reuse_blocked ? "fail" : "unknown", label:validity.reuse_blocked ? "模型已撤回" : "待验证",
      detail:validity.reuse_blocked ? validity.reason : "热物性校核、数值收敛与工程约束分别查看；当前没有完整实机或寿命验证结论。", view:"validation"},
  ];
  const issues = (readiness?.issues || []).map(issue => {
    const group = groups.find(group => (group.fields || []).some(field => (issue.fields || []).includes(field.key)));
    return {message:issue.message, group:group?.id, field:issue.fields?.[0], label:group ? `检查${group.title}` : "查看输入检查", view:group ? undefined : "design_run"};
  });
  for (const group of readiness?.groups || []) if (group.status === "review")
    issues.push({kind:"review", message:`${group.title}尚未确认，请检查设置及依据后确认本组参数。`, group:group.id, label:`检查${group.title}`});
  if (state === "stale") for (const group of comparison.changedGroups)
    issues.push({message:`${group.title}已变更；相关设计点及其下游分析待更新。`, group:group.id, field:group.fields[0], label:`查看${group.title}`});
  if (["invalid", "unknown", "foreign"].includes(state)) issues.push({message:freshness.reason, view:state === "invalid" ? "solver_diagnostics" : "history", label:state === "invalid" ? "查看模型与求解诊断" : "查看版本与记录"});
  for (const dimension of dimensions.filter(item => item.state === "fail" && item.id !== "inputs"))
    issues.push({message:`${dimension.title}：${dimension.label}。${dimension.detail}`, view:dimension.view, label:`查看${dimension.title}`});
  const firstInput = readiness?.groups?.find(group => ["invalid", "review", "pending", "unavailable"].includes(group.status));
  const actions = [firstInput ? {label:`继续设置：${firstInput.title}`, group:firstInput.id}
    : {label:["stale", "invalid"].includes(state) ? "前往检查并重新计算" : "前往输入检查与运行", view:"design_run"},
    {label:result ? "查看保存结果与约束" : "设置需求与设计边界", ...(result ? {view:"results_overview"} : {group:groups.find(group => group.id === "design")?.id || groups[0]?.id, view:"overview"})},
  ];
  const summary = result?.calculation?.summary || {}, recordInputs = savedInputs(ownedRecord) || {};
  const sameAmbient = finite(draft?.ambient_pressure) && finite(snapshot?.ambient_pressure) && draft.ambient_pressure === snapshot.ambient_pressure;
  const thrustTarget = finite(draft?.thrust) ? draft.thrust / 1000 : null;
  const thrustValue = finite(summary.thrust_N) ? summary.thrust_N / 1000 : null;
  const metrics = [
    {label:"工况推力", unit:"kN", target:thrustTarget, value:thrustValue,
      delta:sameAmbient && finite(thrustTarget) && finite(thrustValue) ? thrustValue - thrustTarget : null,
      note:!sameAmbient && result ? "环境边界不同或未记录，不直接计算目标差值。" : "保存计算值与当前目标之差；正值表示高于目标。"},
    {label:"工况比冲", unit:"s", target:null, value:finite(summary.Isp_s) ? summary.Isp_s : null, delta:null, note:"当前输入未定义独立比冲目标。"},
    {label:"真空比冲", unit:"s", target:null, value:finite(summary.Isp_vac_s) ? summary.Isp_vac_s : null, delta:null, note:"真空计算值，不能替代给定环境下的比冲。"},
  ];
  return {project, draft, groups, record:ownedRecord, freshness, dimensions, issues, actions, metrics,
    hasResult:Boolean(result), resultRevision:ownedRecord?.revision,
    boundaries:[{label:"燃烧室压力", unit:"MPa", current:finite(draft?.pc) ? draft.pc / 1e6 : null, saved:finite(recordInputs.pc) ? recordInputs.pc / 1e6 : null},
      {label:"总氧燃质量比", unit:"", current:draft?.of_ratio, saved:recordInputs.of_ratio},
      {label:"环境压力", unit:"kPa", current:finite(draft?.ambient_pressure) ? draft.ambient_pressure / 1000 : null, saved:finite(recordInputs.ambient_pressure) ? recordInputs.ambient_pressure / 1000 : null}],
    currentModels:modelIdentities(draft || {}), savedModels:modelIdentities(recordInputs, result),
    activeJobs:jobs.filter(job => (!project?.id || job.project_id === project.id) && ["queued", "running"].includes(job.status)),
    cycle:draft?.cycle_type, propellant:draft?.propellant_pair};
}

function actionButton(action, primary = false) {
  const target = action.group ? `data-readiness-group="${escape(action.group)}"${action.field ? ` data-readiness-field="${escape(action.field)}"` : ""}`
    : `data-workspace-view="${escape(action.view || "overview")}"`;
  return `<button type="button" class="project-overview-action${primary ? " is-primary" : ""}" ${target}>${escape(action.label)}</button>`;
}


function differenceValue(field, value) {
  if(value === undefined)return "未记录";
  if(value === null)return "未指定";
  const option=field?.choices?.find(item=>item.value===value);
  if(option)return escape(option.label);
  if(finite(value))return escape(Number((value*(field?.factor||1)).toPrecision(12)));
  return escape(typeof value === "object"?JSON.stringify(value,null,2):String(value));
}

export function renderProjectDifferences(model) {
  const {freshness,groups=[],record,draft}=model;
  if(!record)return `<p>${escape(freshness.reason)}</p>`;
  const current=supplyComparisonInputs(draft).inputs||{},saved=supplyComparisonInputs(savedInputs(record)).inputs||{};
  const keys=[...new Set([...freshness.changedKeys,...freshness.missingKeys,...freshness.missingDraftKeys])];
  const rows=keys.map(key=>{
    const definitions=group=>[...(group.fields||[]),...[group.configuration_context||[]].flat()];
    const group=groups.find(item=>definitions(item).some(field=>field.key===key));
    const field=group&&definitions(group).find(item=>item.key===key);
    const label=field?.label||key;
    return `<tr><th scope="row">${escape(label)}${field?.unit?` <small>[${escape(field.unit)}]</small>`:""}${group?`<small>${actionButton({label:group.title,group:group.id,field:key})}</small>`:""}</th><td><pre>${differenceValue(field,current[key])}</pre></td><td><pre>${differenceValue(field,saved[key])}</pre></td></tr>`;
  }).join("");
  return `<p>${escape(freshness.reason)}</p>${keys.length?`<div class="project-differences-table"><table><thead><tr><th>输入项</th><th>当前草稿</th><th>所选记录 ${escape(revision(record.revision))}</th></tr></thead><tbody>${rows}</tbody></table></div>`:""}<p class="fine">只比较计算输入；未记录的历史值不使用当前默认值补齐。确认或保存参数不会改写历史计算。</p>`;
}

export function renderProjectContext(model,{dirty=false,differencesOpen=false}={}) {
  const {project,record,freshness}=model;
  if(!project)return "";
  const recordLabel=record?`所选记录 ${revision(record.revision)} · ${recordStatuses[record.status]||"状态未记录"}`:"尚无计算记录";
  return `<div class="project-context-strip" data-project-freshness="${escape(freshness.state)}">
    <div class="project-context-line"><span>当前输入 <b>${escape(revision(project.revision))}</b> · <span${dirty?' class="project-unsaved"':""}>${dirty?"有未保存修改":"已保存"}</span></span><span class="project-context-record"${record?` title="提交于 ${recordTime(record.created)}（北京时间）"`:""}>${escape(recordLabel)}</span>${freshness.state!=="missing"?`<span class="project-context-state">${escape(freshness.label)}</span>`:""}<div class="project-context-actions"><span class="project-summary-mobile">${actionButton({label:"方案摘要",view:"project_overview"})}</span>${model.activeJobs.length?actionButton({label:`${model.activeJobs.length} 项计算进行中`,view:"design_monitor"}):""}${actionButton({label:"输入检查与运行",view:"design_run"})}${record?actionButton({label:"计算记录",view:"history"}):""}</div></div>
    ${record?`<details class="project-input-differences"${differencesOpen?" open":""}><summary>${freshness.changedKeys.length?`查看差异（${freshness.changedKeys.length}）`:freshness.state==="current"?"查看输入一致性":"查看一致性详情"}</summary>${renderProjectDifferences(model)}</details>`:""}
  </div>`;
}

export function renderRecordProvenance(model,{open=false,recordOpen=false}={}) {
  const record=model.record;
  if(!record)return '<h2>模型与数据来源</h2><p class="fine">尚未选择当前方案的计算记录。</p>';
  return `<h2>模型与数据来源</h2><div class="project-record-context">${renderSelectedRecord(record,recordOpen)}</div><details class="project-model-provenance"${open?" open":""}><summary>查看本次计算的模型与数据标识</summary><div class="project-differences-table"><table><thead><tr><th>模型或数据</th><th>所选记录 ${escape(revision(record.revision))}</th></tr></thead><tbody>${model.savedModels.map(item=>`<tr><th scope="row">${escape(item.label)}</th><td>${escape(item.value)}</td></tr>`).join("")}</tbody></table></div><p class="fine">仅展示这次计算保存的来源；未记录的版本、文件和校验值不以当前环境补齐。</p></details>`;
}

export function renderProjectOverview(model) {
  const {project}=model;
  const propellant=model.propellant==="lox_methane"?"液氧／甲烷":model.propellant||"未记录";
  const cycle=model.cycle==="ffsc"?"全流量补燃循环（FFSC）":model.cycle||"未记录";
  return `<div class="project-overview project-summary" data-project-freshness="${escape(model.freshness.state)}">
    <section class="project-overview-panel"><h2>${escape(project?.name||"尚未选择方案")}</h2>${project?.description?`<p class="project-summary-description">${escape(project.description)}</p>`:""}<dl class="project-summary-facts"><div><dt>推进剂</dt><dd>${escape(propellant)}</dd></div><div><dt>循环形式</dt><dd>${escape(cycle)}</dd></div></dl><div class="project-overview-actions">${actionButton({label:"发动机定义与设计工况",group:"design"})}${actionButton({label:"编辑方案信息与查看记录",view:"history"})}</div></section>
    <section class="project-overview-panel"><h3>继续工作</h3><div class="project-overview-actions">${model.actions.map((action,index)=>actionButton(action,index===0)).join("")}</div><p class="project-overview-note">设计参数通过任务树设置，系统图说明部件连接及参数归属。输入检查、收敛诊断、工程约束与验证依据在各自页面查看。</p></section>
  </div>`;
}
