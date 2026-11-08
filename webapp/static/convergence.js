// Read-only rendering of persisted convergence evidence. Never rerun a solver here.
const finite = value => typeof value === "number" && Number.isFinite(value);
const nonnegative = value => finite(value) && value >= 0;
const list = value => Array.isArray(value) ? value : [];
const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const fmt = value => !finite(value) ? "—" : value !== 0 && (Math.abs(value) < .001 || Math.abs(value) >= 1e6) ? value.toExponential(3) : value.toLocaleString("zh-CN",{maximumSignificantDigits:6});
const LAYERS = [["inner","内层非线性求解"],["thermal","主室热力反馈"],["preburner","预燃室温度外环"],["continuation","延续法目标工况"]];
const COUPLED_SOURCE="regen_quasi1d_ph_v1";
// Versioned protocol mirrors regen_coupling.COUPLING_CRITERIA. A saved larger
// tolerance cannot relax the six physical criteria of this source version.
const COUPLED_CHECKS=[
  ["pressure_residual_Pa","供给与冷却压力残差",.5,"Pa"],
  ["hot_side_temperature_residual_K","冷热侧耦合温差",.001,"K"],
  ["geometry_relative_residual","共同几何相对残差",2e-8,"1"],
  ["heat_relative_residual","总吸热量相对残差",2e-8,"1"],
  ["hot_side_relative_residual","热气状态向量相对残差",5e-7,"1"],
  ["cell_heat_relative_residual","逐单元热量相对残差",5e-7,"1"],
];
const coupled=layer=>layer?.source === COUPLED_SOURCE;
const backendName = name => name === "trf" ? "TRF 有界信赖域" : name === "broyden" ? "Broyden 雅可比近似更新" : name === "newton" || !name ? "Newton" : String(name);
const REASONS = {
  converged:"达到全部容差", tolerance_met:"达到容差", residual_tolerance:"残差达到容差",
  max_iterations:"达到迭代次数上限", iteration_limit:"达到迭代次数上限",
  line_search_failed:"线搜索未找到可接受步长", stalled:"迭代停滞", solver_stalled:"求解器停滞",
  nonfinite_residual:"残差包含非有限值", nonfinite_state:"状态包含非有限值",
  invalid_residual:"残差无效", invalid_feedback:"热力反馈无效",
  not_enabled:"本工况未启用", disabled:"本工况未启用", not_applicable:"本工况不适用",
  target_reached:"已到达目标工况", target_not_reached:"尚未到达目标工况",
  legacy_incomplete:"历史记录缺少完整证据", inner_failed:"内层求解未收敛",
  missing_layer:"缺少本层证据", not_requested:"本工况未启用", fixed_heat_load:"给定吸热量模式，未启用热反馈",
  inner_solver_failed:"内层求解未收敛", invalid_thermal_feedback:"热力反馈数据无效",
  all_layers_converged:"所有启用层均已收敛", incomplete_or_failed_layers:"存在未通过或证据不完整的层",
  invalid_state:"状态或残差无效", not_reached:"尚未执行到本层", thermal_solver_failed:"热力反馈未收敛",
  stagnation:"残差下降停滞", trial_evaluation_failed:"试探步求值失败", dependent_layer_failed:"依赖的内层或热反馈未收敛",
  nonfinite_temperature:"温度含非有限值", target_stage_not_reached:"尚未到达目标工况",
  inner_failed_before_target:"到达目标工况前内层失败", inner_failed_at_target:"目标工况内层失败", running:"迭代尚未结束",
  dependent_solver_failed:"依赖的求解层未收敛", temperature_inversion_failed:"目标温度反算失败",
  jacobian_evaluation_failed:"雅可比矩阵求值失败", invalid_jacobian:"雅可比矩阵无效", invalid_step:"Newton 更新步无效",
  missing_full_evaluation:"缺少完整设计点求解证据", missing_feedback_checks:"缺少温度或物性反馈判据",
  required_layer_disabled:"输入要求执行的迭代层被标记为未启用",
  minimum_step_reached:"延续步长已到下限", attempt_limit:"延续尝试次数已到上限", stage_converged:"该延续阶段收敛",
  feedback_step_rejected:"热反馈试探未获接受", inner_failed_relaxation_reduced:"内层失败，减小热反馈松弛系数",
  feedback_growth_relaxation_reduced:"热反馈残差增长，退回并减小松弛系数",
  initial_evaluation_failed:"初始状态求值失败", evaluation_failed:"状态求值失败", jacobian_failed:"雅可比求值失败",
  stationary_nonzero_residual:"驻点残差未达标", cost_stagnation:"目标函数停滞，残差未达标",
  step_stagnation:"步长停滞，残差未达标", cost_and_step_stagnation:"目标函数与步长停滞，残差未达标",
  residual_stagnation:"残差停滞", optimizer_failed:"对照求解器失败", max_evaluations:"达到求值次数上限",
  coupled_feedback_tolerance_met:"冷热侧与供给六项耦合残差均达标",
  missing_or_failed_coupled_feedback_checks:"冷热侧与供给六项耦合证据缺失或未达标",
};
const CHECK_NAMES={temperature:"主室温度",gamma:"比热比相对变化",gamma_relative_residual:"比热比相对变化",molar_mass:"摩尔质量相对变化",molar_mass_relative_residual:"摩尔质量相对变化",ox_temperature:"富氧预燃室温度",fuel_temperature:"富燃预燃室温度",...Object.fromEntries(COUPLED_CHECKS.map(([name,label])=>[name,label]))};
const reason = value => REASONS[value] || value || "未记录终止原因";
const badge = (status, text) => `<span class="cv-badge cv-${["pass","fail","disabled"].includes(status) ? status : "unknown"}">${escape(text || ({pass:"已收敛",fail:"未收敛",disabled:"未启用",unknown:"待确认"}[status]) || "待确认")}</span>`;

export function initializationDiagnostic(result = {}) {
  const diagnostic=result?.initialization_diagnostics;
  return diagnostic?.schema_version===1&&diagnostic.status==="failed" ? diagnostic : null;
}

const INITIALIZATION_STAGES={initialization:"求解初始化",prepare_initial_guess:"准备初始猜测",forward_seed:"初始状态正向求值",
  prepare_temperature_inversion:"读取温度反求入口状态",ox_temperature_inversion:"富氧预燃室温度反求",fuel_temperature_inversion:"富燃预燃室温度反求"};
const INITIALIZATION_INPUTS={x:"实际初始向量",side:"预燃室侧别",T_target_K:"出口温度目标",T_fuel_in_K:"燃料入口温度",
  T_ox_in_K:"氧化剂入口温度",pressure_Pa:"压力",fuel_enthalpy_J_kg:"燃料入口比焓",ox_enthalpy_J_kg:"氧化剂入口比焓",
  mdot_fuel:"燃料质量流量",mdot_ox:"氧化剂质量流量",T_fuel:"燃料入口温度",T_ox:"氧化剂入口温度",pressure:"压力",
  fuel_enthalpy:"燃料入口比焓",ox_enthalpy:"氧化剂入口比焓",oxygen_fuel_mass_ratio:"氧燃质量比"};
const initializationStage=value=>INITIALIZATION_STAGES[value]||value||"未记录";
const recordedCount=value=>Number.isInteger(value)&&value>=0?fmt(value):"未记录";
const savedValue=value=>value==null?"未记录":typeof value==="number"?(finite(value)?String(value):"未记录"):
  typeof value==="object"?JSON.stringify(value):String(value);
function initializationInputs(inputs,units={},labels={}) {
  const rows=inputs&&typeof inputs==="object"&&!Array.isArray(inputs)?Object.entries(inputs):[];
  if(!rows.length)return '<p class="cv-empty">本阶段未保存实际尝试输入，不使用当前草稿或默认值补齐。</p>';
  return valueTable(["参数（保存字段）","实际尝试值","记录单位"],rows.map(([key,value])=>{
    const label=typeof labels?.[key]==="string"?labels[key]:INITIALIZATION_INPUTS[key];
    return [label?`${label}（${key}）`:key,savedValue(value),units?.[key]||"按保存字段定义"];
  }));
}
function initializationLocation(location) {
  if(!location||typeof location!=="object")return "";
  return `<details class="cv-values"><summary>异常来源（代码位置）</summary>${valueTable(["定位项","保存值"],[["文件",location.path||"未记录"],["函数",location.function||"未记录"],["行号",recordedCount(location.line)]])}</details>`;
}
function initializationPropertyCalls(diagnostic) {
  const calls=[];
  const latest=diagnostic.last_property_call,failed=diagnostic.last_property_failure;
  if(latest&&typeof latest==="object")calls.push(["最近一次实际热化学求值",latest]);
  if(failed&&typeof failed==="object"&&JSON.stringify(failed)!==JSON.stringify(latest))calls.push(["最近一次失败的热化学求值",failed]);
  return calls.map(([title,call])=>`<section class="cv-gas-initialization"><h3>${title} · ${call.status==="failed"?"失败":call.status==="passed"?"已完成":"状态未记录"}</h3><p class="cv-description">部件线索：${escape(call.component||"未定位")}。以下是热化学函数收到的候选状态，不是已收敛工作点。</p>${initializationInputs(call.attempted_inputs,call.input_units,call.input_labels)}${call.error_type||call.message?`<p class="cv-description">${escape(call.error_type||"异常类型未记录")}：${escape(call.message||"错误信息未记录")}</p>`:""}${initializationLocation(call.location)}</section>`).join("");
}

export function renderInitializationDiagnostics(result = {}) {
  const diagnostic=initializationDiagnostic(result);
  if(!diagnostic)return "";
  const failure=diagnostic.failure||{},steps=list(diagnostic.steps).filter(step=>step&&typeof step==="object"),counts=diagnostic.counts||{};
  const formal=diagnostic.formal_iteration_started;
  const formalState=formal===false?"正式稳态迭代尚未开始":formal===true?"记录标记正式迭代已开始，需核对对应轨迹":"正式稳态迭代是否开始：未记录";
  const scopes=diagnostic.counting_scope&&typeof diagnostic.counting_scope==="object"?diagnostic.counting_scope:{};
  const callRows=[["初始状态正向求值","forward"],["预燃室温度反求","inversion"],["温度反求中的物性调用","property"]]
    .map(([label,key])=>[label,recordedCount(counts[`${key}_attempted`]),recordedCount(counts[`${key}_completed`]),typeof scopes[key]==="string"?scopes[key]:"统计范围未记录"]);
  const countingNote=typeof diagnostic.counting_scope==="string"?diagnostic.counting_scope:"各类调用可能相互嵌套，不直接相加为模型总调用数。";
  const internalCalls=Object.hasOwn(counts,"component_internal_property_calls")?counts.component_internal_property_calls:diagnostic.component_internal_property_calls;
  const internalNote=recordedCount(internalCalls)==="未记录"?"前向部件内部物性调用次数未统计，不按零处理。":`前向部件内部物性调用次数：${recordedCount(internalCalls)}。`;
  const names=list(diagnostic.variable_names),units=list(diagnostic.variable_units),x=list(diagnostic.x);
  const vector=x.length?valueTable(["初始向量变量","实际尝试值","单位"],x.map((value,index)=>[names[index]||`变量 ${index+1}`,savedValue(value),units[index]||"未记录"])):
    '<p class="cv-empty">初始向量未保存，不构造默认向量。</p>';
  const statusText=status=>({passed:"已完成",completed:"已完成",succeeded:"已完成",failed:"失败",attempted:"已尝试",running:"未完成",not_started:"未开始",skipped:"未执行"}[status]||"状态未记录");
  const stepCards=steps.map(step=>`<details class="cv-values"${step.status==="failed"?" open":""}><summary>${escape(step.label||initializationStage(step.id))} · ${statusText(step.status)}</summary>${valueTable(["阶段证据","保存内容"],[["部件线索",step.component||"未定位"],["尝试调用次数",recordedCount(step.calls_attempted)],["成功完成次数",recordedCount(step.calls_completed)],["异常类型",step.error_type||"未记录"],["原始错误",step.message||"未记录"]])}${initializationInputs(step.attempted_inputs,step.input_units,step.input_labels)}</details>`).join("");
  return `<section class="panel convergence-workspace cv-diagnostics" aria-label="初始化失败诊断"><div class="cv-heading"><div><span class="cv-kicker">INITIALIZATION DIAGNOSTICS</span><h2>初始化失败诊断</h2><p>所选计算记录中的实际尝试与失败位置。</p></div>${badge("fail","初始化失败")}</div><div class="cv-diagnostic-summary cv-diagnostic-failed"><h4>${escape(formalState)}</h4><p>${formal===false?"初始化尚未形成可交给稳态迭代的状态，因此没有正式残差曲线。":"本诊断只展示初始化记录，不据此构造正式残差曲线。"} 该失败不能认定设计在物理上无解。</p><p>失败阶段：${escape(initializationStage(failure.stage||diagnostic.stage))}；部件线索：${escape(failure.component||"未定位")}。</p><p>异常类型：${escape(failure.error_type||"未记录")}；原始错误：${escape(failure.message||"未记录")}。</p></div>${initializationLocation(failure.location)}<h3>实际调用与完成情况</h3>${valueTable(["调用类别","尝试调用次数","成功完成次数","统计范围"],callRows)}<p class="cv-description">${escape(countingNote)} ${escape(internalNote)}</p><p class="cv-description">初始化耗时：${nonnegative(diagnostic.elapsed_seconds)?`${fmt(diagnostic.elapsed_seconds)} s`:"未记录"}。</p>${initializationPropertyCalls(diagnostic)}<h3>失败时使用的初始向量</h3>${vector}<h3>初始化步骤</h3>${stepCards||'<p class="cv-empty">未保存分步骤记录，请查看已保存错误与输入。</p>'}<details class="cv-values"><summary>查看实际尝试的完整输入（国际单位制快照）</summary><p class="cv-description">这是交给初始化过程的保存输入，不随当前草稿变化；阶段内部的实际调用参数见上方步骤。</p>${initializationInputs(diagnostic.attempted_inputs,diagnostic.input_units,diagnostic.input_labels)}</details></section>`;
}

/** Historical records stay immutable; this is a conservative display assessment. */
export function numericalDisplayState(result = {}) {
  const initialization=initializationDiagnostic(result);
  if(initialization)return {status:"fail",label:"初始化失败",phase:"initialization",formal_iteration_started:initialization.formal_iteration_started,residual:null,layers:{}};
  const recorded = result.assessment?.numerical || {}, trace = result.calculation?.convergence;
  let layers = trace?.layers || recorded.layers || {};
  if (trace?.schema_version === 1) {
    layers=Object.fromEntries(LAYERS.map(([key])=>{
      const raw=layers[key];
      if (!raw) return [key,{status:"unknown",termination_reason:"missing_layer"}];
      if (raw.enabled === false && raw.status === "disabled") return [key,raw];
      const checks=[raw,...list(raw.checks)];
      const failed=raw.status === "fail" || checks.some(check=>nonnegative(check?.residual) && nonnegative(check?.tolerance) && check.residual > check.tolerance);
      const complete=raw.enabled === true && raw.status === "pass" && checks.every(check=>nonnegative(check?.residual) && nonnegative(check?.tolerance));
      return [key,{...raw,status:failed ? "fail" : complete ? "pass" : "unknown"}];
    }));
    for (const key of ["inner","continuation"]) if (layers[key]?.enabled !== true) layers[key]={...layers[key],status:"unknown",termination_reason:"missing_full_evaluation"};
    if (layers.thermal?.enabled === true && layers.thermal.status === "pass") {
      const names=list(layers.thermal.checks).map(check=>check?.name || check?.key);
      if (coupled(layers.thermal)) {
        const values=Object.fromEntries(list(layers.thermal.checks).map(check=>[check?.name,check?.residual]));
        const present=COUPLED_CHECKS.every(([name])=>names.includes(name));
        const passed=COUPLED_CHECKS.every(([name,,limit])=>nonnegative(values[name]) && values[name] <= limit);
        if (!present || !passed) layers.thermal={...layers.thermal,status:present ? "fail" : "unknown",termination_reason:"missing_or_failed_coupled_feedback_checks"};
      } else if (!names.includes("temperature") || !names.some(name=>["gamma","gamma_relative_residual"].includes(name)) || !names.some(name=>["molar_mass","molar_mass_relative_residual"].includes(name))) {
        layers.thermal={...layers.thermal,status:"unknown",termination_reason:"missing_feedback_checks"};
      }
    }
    const inputs=result.calculation?.inputs || result.submitted_inputs || {};
    const on=value=>value === true || (finite(value) && value >= .5);
    const coolingOn=value=>value === true || (finite(value) && value > 0);
    const required={thermal:coolingOn(inputs.cooling_bartz_mode),preburner:on(inputs.use_target_preburner_temp)};
    for (const [key,isRequired] of Object.entries(required)) if (isRequired && layers[key].enabled !== true) layers[key]={...layers[key],status:"fail",termination_reason:"required_layer_disabled"};
    if (layers.continuation.status === "pass") {
      const reached=layers.continuation.reached_ratio ?? layers.continuation.last_stage_ratio;
      if (!finite(reached)) layers.continuation={...layers.continuation,status:"unknown",termination_reason:"missing_full_evaluation"};
      else if (reached !== 1 || layers.continuation.termination_reason !== "target_reached") layers.continuation={...layers.continuation,status:"fail",termination_reason:"target_not_reached"};
    }
    const active = LAYERS.map(([key]) => layers[key]).filter(layer => layer?.status !== "disabled");
    const knownFailure = active.some(layer => layer?.status === "fail");
    const complete = LAYERS.every(([key]) => ["disabled","pass"].includes(layers[key]?.status));
    const summary=result.calculation?.summary || {},norm=summary.solver_residual_norm,tolerance=layers.inner?.tolerance;
    const summaryFailure=Boolean(summary.solver_warning) || (nonnegative(norm) && nonnegative(tolerance) && norm > tolerance);
    const status = knownFailure || recorded.status === "fail" || summaryFailure ? "fail" : complete && recorded.status === "pass" && nonnegative(norm) ? "pass" : "unknown";
    return {...recorded,status,label:status === "pass" ? "完整数值收敛" : status === "fail" ? "数值未收敛" : "完整收敛待确认",layers};
  }
  const summary = result.calculation?.summary || {};
  const norm = summary.solver_residual_norm;
  const thermal = summary.tc_outer_residual_K;
  const pbErrors = [summary.ox_preburner_temp_error_K,summary.fuel_preburner_temp_error_K];
  const failed = recorded.status === "fail" || summary.solver_warning === true || (finite(summary.solver_warning) && summary.solver_warning > 0)
    || (finite(norm) && (norm < 0 || norm >= 1e-4)) || (finite(thermal) && (thermal < 0 || thermal > 5))
    || pbErrors.some(value => finite(value) && Math.abs(value) > 3);
  return {...recorded,status:failed ? "fail" : "unknown",label:failed ? "历史结果复核未收敛" : "历史收敛证据不完整",residual:finite(norm) ? norm : recorded.residual,legacy_incomplete:true,layers};
}

function valueTable(headings, rows) {
  return `<div class="cv-table-wrap"><table><thead><tr>${headings.map(title=>`<th scope="col">${escape(title)}</th>`).join("")}</tr></thead><tbody>${rows.map(cells=>`<tr>${cells.map(value=>`<td>${escape(value)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

const DIAGNOSTIC_LABELS={
  input_validation:"输入检查", model_evaluation:"模型求值", solver_processing:"求解处理",
  outside_instrumented_solver:"未记录调用的外部求解入口", recorded_model_calls:"已记录模型调用",
  initial:"初始状态", initialization:"求解初始化", trial:"线搜索试探", line_search:"线搜索试探", finite_difference:"有限差分探针",
  jacobian:"雅可比差分", initial_build:"初次建立", periodic_refresh:"周期重建", interval:"达到复用轮次",
  poor_progress:"残差下降不足", line_search_failed:"近似矩阵线搜索失败", singular:"数值秩不足",
  ill_conditioned:"矩阵病态", invalid_update:"近似更新无效", small_secant:"更新步过小",
  variable_step_limit:"变量步长上限", variable_limit:"变量步长上限", bound:"变量边界",
  bounds:"变量边界", bound_limit:"变量边界", insufficient_decrease:"残差平方和下降不足",
  armijo:"残差平方和下降不足", model_failure:"模型求值失败", evaluation_failed:"模型求值失败",
  domain_failure:"适用域求值失败", newton:"完整差分矩阵", broyden:"Broyden 近似矩阵",
  finite_difference_probe:"有限差分探针", rejected_trial:"未接受试探", accepted_trial:"接受试探",
  jacobian_rebuild:"完整雅可比重建", approximate_update:"雅可比近似更新",
  trust_region_trial:"信赖域试探", trust_region:"信赖域完整重建", bounded_one_sided:"边界内单侧差分", full_newton:"牛顿法完整重建",
  refresh_interval:"达到复用轮次", model_domain_or_evaluation_change:"适用域或模型求值状态变化",
  secant_step_too_small:"割线步过小", nonfinite_update:"近似矩阵包含非有限值",
  ill_conditioned_update:"近似矩阵病态", approximate_direction_failed:"近似矩阵未给出下降方向",
  approximate_line_search_failed:"近似矩阵线搜索失败", approximate_step_stagnation:"近似矩阵步长停滞",
  approximate_residual_stagnation:"近似矩阵残差停滞", accepted_secant:"接受步割线更新",
  jacobian_update:"雅可比近似更新", jacobian_refresh_scheduled:"计划重建雅可比",
  variable_bound:"变量边界", model_evaluation_failed:"模型求值失败",
  model_domain_violation:"模型报告适用域越界", insufficient_merit_decrease:"残差平方和下降不足",
  lower:"达到下界", upper:"达到上界",
};
const diagnosticLabel=value=>DIAGNOSTIC_LABELS[value] || REASONS[value] || value || "—";
const equationName=(equation,index,run)=>equation.label || list(run.equation_labels)[index] || ({thrust:"推力闭合",ox_power:"氧化剂轴功率平衡",fuel_power:"燃料轴功率平衡"}[equation.name]) || equation.name || `方程 ${index+1}`;
const namedItems=value=>list(value).map(item=>typeof item === "string" ? item : item?.name || item?.variable || item?.equation || "—").join("、") || "未记录";
function equationStatus(equation) {
  if (finite(equation.raw_residual) && nonnegative(equation.absolute_tolerance))
    return Math.abs(equation.raw_residual) <= equation.absolute_tolerance && equation.passed !== false ? "通过" : "未通过";
  return "证据缺失";
}

const numericalTolerance=run=>Object.hasOwn(run,"numerical_tolerance")?run.numerical_tolerance:run.tolerance;
function renderAcceptancePolicy(run) {
  const policy=run.acceptance_policy;
  if(policy?.schema_version!==1)return "";
  const equations=list(policy.equations).filter(equation=>equation&&typeof equation==="object");
  return `<section class="cv-acceptance-policy" aria-label="物理验收与数值缩放"><h3>物理验收与数值缩放</h3><p class="cv-description">每项方程按保存的绝对容差验收；数值尺度只用于求解。改变功率缩放不会放宽这里的功率允许误差。最大验收比 = 最大的“绝对物理残差 ÷ 对应绝对容差”，通过阈值为 1。</p>${valueTable(["方程","单位","固定绝对验收容差","依据"],equations.map(equation=>[equation.label||equation.name||"未命名方程",equation.unit||"未记录",fmt(equation.absolute_tolerance),equation.source||"未记录"]))}${valueTable(["记录量","值"],[["任务推力 / N",fmt(policy.task_thrust_N)],["固定推力参考量 / N",fmt(policy.thrust_reference_N)],["推力相对容差",fmt(policy.relative_thrust_tolerance)],["数值缩放范数停止阈值",fmt(numericalTolerance(run))]])}</section>`;
}

/** Render stored evidence only; no numerical success or independence is inferred from missing fields. */
export function renderSolverDiagnostics(run = {}) {
  const diagnostics=run.diagnostics;
  const policy=renderAcceptancePolicy(run);
  if (!diagnostics || diagnostics.schema_version !== 1) return policy+'<p class="cv-empty cv-diagnostic-missing">本次记录未保存逐项求解诊断；重算后可查看方程、变量、雅可比和步长缩减依据。</p>';
  const equations=list(diagnostics.equations).filter(item=>item && typeof item === "object");
  const variables=list(diagnostics.variables).filter(item=>item && typeof item === "object");
  const failures=list(diagnostics.failures).filter(Boolean), reductions=list(diagnostics.step_reductions).filter(Boolean), events=list(diagnostics.events).filter(Boolean);
  const failedEquations=equations.map((equation,index)=>({equation,index})).filter(({equation})=>equationStatus(equation)==="未通过");
  const missing=equations.filter(equation=>equationStatus(equation)==="证据缺失").length;
  const jacobian=diagnostics.jacobian || {};
  const rankKnown=nonnegative(jacobian.rank)&&nonnegative(jacobian.rows)&&nonnegative(jacobian.columns);
  const rankDeficient=rankKnown && jacobian.rank < Math.min(jacobian.rows,jacobian.columns);
  const lastFailure=failures.at(-1);
  const reductionUnobserved=diagnostics.step_reduction_observability==="backend_internal_unavailable";
  const summary=failedEquations.length ? `未达标方程：${failedEquations.map(({equation,index})=>equationName(equation,index,run)).join("、")}` : missing || !equations.length ? "逐项残差证据不完整" : "本次内层方程均满足记录中的绝对容差";
  const metrics=[["完整雅可比重建",fmt(diagnostics.jacobian_rebuilds)],["近似更新",fmt(diagnostics.approximate_updates)],["模型调用",fmt(diagnostics.model_evaluations)],["内层耗时 / s",fmt(diagnostics.elapsed_seconds)]];
  const nameAt=(name)=>variables.find(variable=>variable.name===name)?.name || name || "未定位变量";
  const stateAt=at=>list(at).map((value,index)=>`${variables[index]?.name || `变量 ${index+1}`} = ${fmt(value)} ${variables[index]?.unit || ""}`).join("；") || "未记录";
  const equationRows=equations.map((equation,index)=>[equationName(equation,index,run),equation.component || "未登记",equation.unit || "—",fmt(equation.raw_residual),fmt(equation.absolute_tolerance),fmt(equation.scaled_residual),fmt(equation.scale),equationStatus(equation)]);
  const acceptanceSeries=equations.map((equation,index)=>({label:equationName(equation,index,run),points:list(run.points).map(point=>({x:point?.iteration,y:finite(point?.raw_residuals?.[equation.name]) && finite(equation.absolute_tolerance) && equation.absolute_tolerance>0 ? Math.abs(point.raw_residuals[equation.name])/equation.absolute_tolerance : null}))}));
  const hasComponentHistory=acceptanceSeries.some(series=>series.points.some(point=>nonnegative(point.y)));
  const reductionCounts=Object.entries(diagnostics.step_reduction_counts || reductions.reduce((counts,item)=>{counts[item.reason]=(counts[item.reason]||0)+1;return counts;},Object.create(null))).map(([key,count])=>[diagnosticLabel(key),count]);
  const omitted=diagnostics.truncated_records || {};
  const omissionNote=Object.values(omitted).some(value=>finite(value)&&value>0) ? `日志达到保存上限：省略矩阵事件 ${fmt(omitted.events)} 条、求值失败 ${fmt(omitted.failures)} 条、步长缩减 ${fmt(omitted.step_reductions)} 条；统计计数仍包含全部调用，以下明细仅为已保存部分。` : "";
  return `<section class="cv-diagnostics" aria-label="逐项求解诊断"><div class="cv-diagnostic-summary ${failedEquations.length ? "cv-diagnostic-failed" : ""}"><h4>${escape(summary)}</h4><p>逐项绝对残差用于验收，缩放用于数值求解；二者分别保留。数值失败不能直接判定物理上无解。</p><p>部件适用域：${diagnostics.domain_status === "violation_observed" ? "求值过程中曾报告越界；请检查试探状态与模型范围。" : "未独立验证；数值收敛不构成部件范围验证。"}</p>${lastFailure ? `<p>最近一条已保存求值失败：${escape(diagnosticLabel(lastFailure.stage))} · ${escape(nameAt(lastFailure.variable || lastFailure.probe_variable))} · ${escape(lastFailure.component || "未定位部件")} · ${escape(lastFailure.equation || "未定位方程")} · ${escape(lastFailure.error)}。试探失败可能已被后续迭代恢复，请结合终止原因判断。</p>` : ""}</div><div class="cv-method-metrics">${metrics.map(([label,value])=>`<div><span>${escape(label)}</span><strong>${escape(value)}</strong></div>`).join("")}</div>${omissionNote ? `<p class="cv-notice">${escape(omissionNote)}</p>` : ""}${policy}${equations.length ? valueTable(["方程","部件 / 关系","单位","最终原始残差（有符号）","容许绝对残差","缩放残差","数值尺度","验收"],equationRows) : '<p class="cv-empty">未记录最终方程分量，不补零。</p>'}${hasComponentHistory ? `<details class="cv-values"><summary>查看逐项残差收敛曲线（按验收阈值归一化）</summary>${residualPlot({title:"各方程绝对残差 / 绝对容差",series:acceptanceSeries,tolerance:1,unit:"1",xLabel:"接受迭代步"})}</details>` : ""}<details class="cv-values"><summary>查看变量尺度、差分步长与变量边界</summary><p class="cv-description">步长、尺度和边界均使用各变量的记录单位；未登记的限制显示“未设置”。信赖域方法使用其内部步长控制，不能将缺失的变量级限制视为已执行。</p>${valueTable(["变量","单位","最终接受值","变量尺度","下界","上界","请求差分步长","实际差分步长（有符号）","变量步长上限","边界状态"],variables.map(variable=>[variable.name || "—",variable.unit || "—",fmt(variable.value),fmt(variable.scale),finite(variable.lower)?fmt(variable.lower):"未设置",finite(variable.upper)?fmt(variable.upper):"未设置",fmt(variable.requested_diff_step),fmt(variable.diff_step),finite(variable.max_step)?fmt(variable.max_step):"未设置",variable.at_bound===true ? "位于边界" : typeof variable.at_bound==="string" ? diagnosticLabel(variable.at_bound) : variable.at_bound===false || (Object.hasOwn(variable,"at_bound") && variable.at_bound===null && finite(variable.value)) ? "边界内" : "未记录"]))}</details><details class="cv-values"${rankDeficient ? " open" : ""}><summary>局部方程独立性：${rankKnown ? `数值秩 ${fmt(jacobian.rank)} / ${fmt(jacobian.rows)} 个方程、${fmt(jacobian.columns)} 个变量` : "未记录可用雅可比"}</summary><p class="cv-description">基于经过缩放的局部雅可比。数值秩不足表示当前点附近存在弱敏感或相关关系；满秩也不证明全局方程独立、解唯一或部件适用域有效。矩阵可能来自最终接受点之前的差分或近似更新。</p>${valueTable(["诊断项","记录值"],[["矩阵来源",diagnosticLabel(jacobian.source)],["条件数",finite(jacobian.condition_number)?fmt(jacobian.condition_number):rankDeficient?"奇异或数值秩不足":"未记录"],["弱敏感变量",namedItems(jacobian.weak_variables)],["相关方程",namedItems(list(jacobian.dependent_equations).map(name=>{const index=equations.findIndex(equation=>equation.name===name);return index>=0 ? equationName(equations[index],index,run) : name;}))],["奇异值",list(jacobian.singular_values).map(fmt).join("、") || "未记录"],["矩阵对应状态",stateAt(jacobian.at_x)],["完整雅可比尝试次数",fmt(diagnostics.jacobian_attempts)]] )}</details><details class="cv-values"${failures.length || reductions.length ? " open" : ""}><summary>步长缩减与适用域求值诊断 · ${reductionUnobserved ? "内部缩减未公开" : `${reductions.length} 条缩减记录`} / ${failures.length} 条求值失败记录</summary><p class="cv-description">${reductionUnobserved ? "信赖域后端没有公开内部步长缩减记录；空列表不能解读为没有缩减。" : reductionCounts.length ? escape(reductionCounts.map(([label,count])=>`${label} ${count} 次`).join("；")) : "本次记录没有步长缩减事件。"} 未报错不能证明部件特性图全域有效；适用域判断以模型返回的检查为准。</p>${reductions.length ? valueTable(["接受步位置","缩减原因","变量","缩减因子"],reductions.map(item=>[fmt(item.iteration),diagnosticLabel(item.reason),item.variable || "—",fmt(item.factor)])) : ""}${failures.length ? valueTable(["求值阶段","失败类型","变量","差分探针变量","部件","方程","原始错误","失败试探状态"],failures.map(item=>[diagnosticLabel(item.stage),diagnosticLabel(item.kind),item.variable || "未定位",item.probe_variable || "—",item.component || "未定位",item.equation || "未定位",item.error || "—",stateAt(item.x)])) : ""}</details><details class="cv-values"><summary>查看矩阵重建事件与模型调用统计</summary>${events.length ? valueTable(["接受步位置","事件","原因"],events.map(item=>[fmt(item.iteration),diagnosticLabel(item.kind),diagnosticLabel(item.reason)])) : '<p class="cv-description">未记录矩阵事件。</p>'}${valueTable(["模型调用阶段","次数"],Object.entries(diagnostics.evaluation_counts || {}).map(([stage,count])=>[diagnosticLabel(stage),fmt(count)]))}<p class="cv-description">模型调用包括差分探针、接受试探、拒绝试探及失败求值。曲线只包含接受状态，不以曲线点数替代工作量。</p></details></section>`;
}

/** Log plot: zero has a separate rail; missing/negative/non-finite samples break lines. */
export function residualPlot({title,series = [],tolerance,unit = "—",xLabel = "迭代步"}) {
  const safeSeries = list(series).map((item,index)=>({...item,color:index % 4,points:list(item?.points)}));
  const all = safeSeries.flatMap(item=>item.points);
  const usable = all.filter(point=>nonnegative(point?.x) && nonnegative(point?.y));
  if (!usable.length) return `<p class="cv-empty">${escape(title)}：没有有效残差数据。缺失、非有限值及负范数均不作为 0 绘制。</p>`;
  const positive = usable.map(point=>point.y).filter(value=>value > 0);
  if (finite(tolerance) && tolerance > 0) positive.push(tolerance);
  let low = positive.length ? Math.floor(Math.log10(Math.min(...positive))) : -1;
  let high = positive.length ? Math.ceil(Math.log10(Math.max(...positive))) : 1;
  if (high <= low) { low -= 1; high += 1; }
  const xmin = Math.min(...usable.map(point=>point.x)), xmax = Math.max(...usable.map(point=>point.x));
  const left=70,right=550,top=34,bottom=210,zeroY=239;
  const x = value => xmin === xmax ? (left+right)/2 : left+(value-xmin)/(xmax-xmin)*(right-left);
  const yExponent = exponent => top+(high-exponent)/(high-low)*(bottom-top);
  const y = value => value === 0 ? zeroY : yExponent(Math.log10(value));
  const ticks=[];
  const step = Math.max(1,Math.ceil((high-low)/5));
  for (let exponent=low;exponent<=high;exponent+=step) ticks.push(exponent);
  if (ticks[ticks.length-1] !== high) ticks.push(high);
  const grid=ticks.map(exponent=>`<g><line class="cv-grid" x1="${left}" x2="${right}" y1="${yExponent(exponent)}" y2="${yExponent(exponent)}"/><text class="cv-tick" x="60" y="${yExponent(exponent)+4}" text-anchor="end">1e${exponent}</text></g>`).join("");
  const xvalues = xmin === xmax ? [xmin] : Number.isInteger(xmin) && Number.isInteger(xmax) && xmax-xmin <= 8 ? Array.from({length:xmax-xmin+1},(_,index)=>xmin+index) : [...new Set([xmin,...[.25,.5,.75].map(fraction=>Math.round(xmin+(xmax-xmin)*fraction)),xmax])];
  const xticks=xvalues.map(value=>`<text class="cv-tick" x="${x(value)}" y="262" text-anchor="middle">${fmt(value)}</text>`).join("");
  const threshold = finite(tolerance) && tolerance >= 0 ? `<line class="cv-threshold" x1="${left}" x2="${right}" y1="${y(tolerance)}" y2="${y(tolerance)}"/><text class="cv-threshold-label" x="${right}" y="${y(tolerance)-6}" text-anchor="end">容差 ${fmt(tolerance)} ${escape(unit)}</text>` : "";
  let invalid=0,zeros=0;
  const paths=safeSeries.map(item=>{
    let path="",prior=null;
    const marks=item.points.map(point=>{
      if (!nonnegative(point?.x) || !nonnegative(point?.y)) { prior=null; invalid++; return ""; }
      const xx=x(point.x),yy=y(point.y);
      const label=`${item.label} · ${xLabel} ${fmt(point.x)} · ${fmt(point.y)} ${unit}`;
      if (point.y === 0) { zeros++; prior=null; return `<rect class="cv-mark cv-series-${item.color}" x="${xx-3}" y="${yy-3}" width="6" height="6"><title>${escape(label)}（零值独立标记）</title></rect>`; }
      path+=`${prior && point.x > prior.x ? "L" : "M"}${xx.toFixed(3)},${yy.toFixed(3)} `;
      prior=point;
      return `<circle class="cv-mark cv-series-${item.color}" cx="${xx}" cy="${yy}" r="3"><title>${escape(label)}</title></circle>`;
    }).join("");
    return `<path class="cv-line cv-series-${item.color}" d="${path.trim()}"/>${marks}`;
  }).join("");
  return `<figure class="cv-chart"><figcaption>${escape(title)}</figcaption><svg class="cv-plot" viewBox="0 0 580 290" role="img" aria-label="${escape(`${title}，纵轴为对数残差，单位 ${unit}；横轴为${xLabel}。容差 ${fmt(tolerance)}。零值独立显示，缺失或无效点断开曲线。`)}"><title>${escape(title)}</title><text class="cv-tick" x="${left}" y="17">残差 / ${escape(unit)} · 对数轴</text>${grid}<line class="cv-zero-rail" x1="${left}" x2="${right}" y1="${zeroY}" y2="${zeroY}"/><text class="cv-tick" x="60" y="${zeroY+4}" text-anchor="end">0</text>${threshold}${paths}${xticks}<text class="cv-tick" x="310" y="283" text-anchor="middle">${escape(xLabel)}</text></svg><div class="cv-legend">${safeSeries.map(item=>`<span class="cv-legend-${item.color}">${escape(item.label)}</span>`).join("")}<span class="cv-legend-tolerance">容差线</span></div><p class="cv-chart-note">零值 ${zeros} 个（独立方形标记） · 缺失 / 无效值 ${invalid} 个（保留断点）。悬停数据点可查看数值。</p></figure>`;
}

function layerCards(layers) {
  return `<div class="cv-layers">${LAYERS.map(([key,label])=>{
    const layer=layers?.[key] || {}, enabled=layer.status !== "disabled", status=layer.status || "unknown",unit=layer.unit === "1" ? "" : layer.unit || "—";
    const checks=Array.isArray(layer.checks) ? layer.checks : Object.entries(layer.checks || {}).map(([id,check])=>({...check,key:id}));
    const checkRows=checks.map(check=>{
      const residual=check.residual ?? check.value,recordedTolerance=check.tolerance ?? check.limit;
      const protocolTolerance=coupled(layer) ? COUPLED_CHECKS.find(([name])=>name === (check.name || check.key))?.[2] : undefined;
      const tolerance=nonnegative(protocolTolerance) && nonnegative(recordedTolerance) ? Math.min(protocolTolerance,recordedTolerance) : recordedTolerance;
      const status=check.status === "fail" ? "fail" : nonnegative(residual) && nonnegative(tolerance) ? residual <= tolerance ? "pass" : "fail" : "unknown";
      return [check.label || CHECK_NAMES[check.name || check.key] || check.name || check.key || "分项",`${fmt(residual)} ${check.unit || ""}`,`${fmt(tolerance)} ${check.unit || ""}`,status === "pass" ? "通过" : status === "fail" ? "未通过" : "待确认"];
    });
    return `<article class="cv-layer" data-layer="${key}"><div><h3>${key === "thermal" && coupled(layer) ? "冷热侧与供给耦合" : label}</h3>${badge(status)}</div><p class="cv-layer-value">${enabled ? `${fmt(layer.residual)} <small>${escape(unit)}</small>` : "—"}</p><p>容差 ${fmt(layer.tolerance)} ${escape(unit)} · ${fmt(layer.iterations)} 轮</p><p class="cv-reason">${escape(reason(layer.termination_reason))}</p>${checks.length ? `<details class="cv-checks"><summary>查看分项判据（${checks.length}）</summary>${valueTable(["判据","残差","容差","状态"],checkRows)}</details>` : ""}</article>`;
  }).join("")}</div>`;
}

function innerHistory(runs,thermalLayer) {
  const validRuns=list(runs).filter(run=>run && typeof run === "object");
  if (!validRuns.length) return '<p class="cv-empty">本记录未保存内层迭代轨迹。</p>';
  const renderRun=(run,index)=>{
    const points=list(run.points), label=`求解 ${run.id ?? index+1} · 工况比例 ${finite(run.stage_ratio) ? fmt(run.stage_ratio*100)+"%" : "—"} · 温度外环 ${fmt(run.preburner_iteration)}${coupled(thermalLayer) ? "" : ` · 热外环 ${fmt(run.thermal_iteration)}`}`;
    const last=points.at(-1), columns=[...new Set(points.flatMap(point=>Object.keys(point?.residuals || {})))];
    const scales=list(run.residual_scales), variableScales=list(run.variable_scales);
    const audit=run.backend ? `<p class="cv-description cv-run-audit">${escape(backendName(run.backend))} · ${run.warm_start ? "热启动" : "初始猜测"} · 函数求值 ${fmt(run.nfev)} 次 / 上限 ${fmt(run.evaluation_budget)} · 雅可比尝试 ${fmt(run.njev)} 次 · 未接受试探 ${fmt(run.rejected_steps)} 次</p>${run.diagnostics?.schema_version===1 ? "" : `<details class="cv-values"><summary>查看本次求解的固定缩放</summary><p class="cv-description">归一化残差 = 原始方程残差 / 固定尺度；尺度只在每次独立求解开始时确定。功率尺度随工况变化，不同求解的范数应结合尺度比较。</p>${valueTable(["方程","残差尺度（N 或 W）","容许绝对残差（N 或 W）"],scales.map((scale,i)=>[columns[i] || `方程 ${i+1}`,fmt(scale),fmt(finite(run.physical_tolerances?.[i]) ? run.physical_tolerances[i] : run.acceptance_policy ? null : finite(scale) && finite(numericalTolerance(run)) ? scale*numericalTolerance(run) : null)]))}${valueTable(["变量序号","变量尺度"],variableScales.map((scale,i)=>[i+1,fmt(scale)]))}</details>`}` : "";
    const acceptance=points.some(point=>Object.hasOwn(point || {},"acceptance_norm"));
    const componentLabels=columns.map(name=>{const i=list(run.diagnostics?.equations).findIndex(equation=>equation.name===name);return i>=0 ? equationName(run.diagnostics.equations[i],i,run) : name;});
    return `<details class="cv-run"${index === validRuns.length-1 ? " open" : ""}><summary><span>${escape(label)}</span><span>缩放范数 ${fmt(last?.residual_norm)}${acceptance ? ` · 验收比 ${fmt(last?.acceptance_norm)}` : String()} · ${escape(reason(run.termination_reason))}</span></summary>${audit}${renderSolverDiagnostics(run)}${residualPlot({title:`${backendName(run.backend)} ${acceptance ? "最大残差 / 验收阈值" : "归一化残差范数"}`,series:[{label:acceptance ? "最大验收比" : "‖r‖∞",points:points.map(point=>({x:point?.iteration,y:acceptance ? point?.acceptance_norm : point?.residual_norm}))}],tolerance:acceptance ? 1 : numericalTolerance(run)})}<details class="cv-values"><summary>查看逐步残差与方程分量</summary>${valueTable(["接受步","缩放残差范数",...(acceptance?["最大验收比"]:[]),...componentLabels,"累计模型调用","完整重建","近似更新","缩放步长","累计耗时 / s","步长缩减原因"],points.map(point=>[fmt(point?.iteration),fmt(point?.residual_norm),...(acceptance?[fmt(point?.acceptance_norm)]:[]),...columns.map(key=>fmt(point?.residuals?.[key])),fmt(point?.nfev),fmt(point?.jacobian_rebuilds),fmt(point?.approximate_updates),fmt(point?.step_norm),fmt(point?.elapsed_seconds),list(point?.reduction_reasons).map(diagnosticLabel).join("、")||"—"]))}</details></details>`;
  };
  return `<div class="cv-inner"><h3>内层残差曲线 <small>${validRuns.length} 次独立求解</small></h3><p class="cv-description">每次内层调用单独作图；不同延续阶段、热外环或温度外环之间不连线。默认展开最后一次求解。</p>${renderRun(validRuns.at(-1),validRuns.length-1)}${validRuns.length > 1 ? `<details class="cv-archive"><summary>查看此前 ${validRuns.length-1} 次内层求解</summary>${validRuns.slice(0,-1).map(renderRun).join("")}</details>` : ""}</div>`;
}

function methodAudit(trace) {
  const runs=list(trace.inner_runs), attempts=list(trace.continuation_history);
  if (!runs.some(run=>run.backend)) return "";
  const total=key=>runs.every(run=>nonnegative(run[key])) ? runs.reduce((sum,run)=>sum+run[key],0) : null;
  const diagnosticTotal=key=>runs.every(run=>run.diagnostics?.schema_version===1 && nonnegative(run.diagnostics[key])) ? runs.reduce((sum,run)=>sum+run.diagnostics[key],0) : null;
  const backend=[...new Set(runs.map(run=>backendName(run.backend)))].join(" / ");
  const cards=[["求解方法",backend],["函数求值（含差分与试探）",fmt(total("nfev"))],["完整雅可比重建",fmt(diagnosticTotal("jacobian_rebuilds"))],["近似更新",fmt(diagnosticTotal("approximate_updates"))],["雅可比计算尝试",fmt(total("njev"))],["未接受试探",fmt(total("rejected_steps"))],["内层累计耗时 / s",fmt(diagnosticTotal("elapsed_seconds"))],["热启动内层调用",fmt(runs.filter(run=>run.warm_start === true).length)]];
  return `<section class="cv-method"><div class="cv-method-metrics">${cards.map(([label,value])=>`<div><span>${escape(label)}</span><strong>${escape(value)}</strong></div>`).join("")}</div><p class="cv-description">内层曲线包含初始点与接受步；差分和被拒绝试探计入求值次数。图示为最大分量范数，线搜索控制的是残差平方和，曲线不要求逐步单调。算法停止后仍以四层判据验收。</p>${attempts.length ? `<details class="cv-values"><summary>查看自适应延续与重试（${attempts.length} 次）</summary>${valueTable(["温度外环","尝试","工况比例","步长","起点","结果","终止原因"],attempts.map((attempt,i)=>[fmt(attempt.preburner_iteration),fmt(attempt.attempt ?? i+1),finite(attempt.stage_ratio) ? `${fmt(attempt.stage_ratio*100)}%` : "—",fmt(attempt.step),attempt.warm_start ? "热启动" : "延续初值",attempt.accepted === true ? "接受" : attempt.accepted === false ? "拒绝" : "—",reason(attempt.termination_reason)]))}</details>` : ""}</section>`;
}

function historyBatches(points) {
  // Preserve chronological groups; resetting iteration numbers starts a new curve.
  const batches=[];
  for (const point of points) {
    if (!point || typeof point !== "object") continue;
    let batch=batches.at(-1);
    if (!batch || batch.id !== point.preburner_iteration || (finite(point.iteration) && point.iteration <= (batch.points.at(-1)?.iteration ?? -Infinity))) {
      batch={id:point.preburner_iteration,points:[]}; batches.push(batch);
    }
    batch.points.push(point);
  }
  return batches;
}

function coupledHistory(trace) {
  const points=list(trace.thermal_history).filter(point=>coupled(point)), batches=historyBatches(points);
  if (!points.length) return coupled(trace.layers?.thermal) ? '<p class="cv-empty">本记录未保存冷热侧与供给耦合轨迹；不使用初值差代替残差。</p>' : "";
  const charts=COUPLED_CHECKS.slice(0,2).map(([key,title,tolerance,unit])=>residualPlot({title,
    series:batches.map((batch,index)=>({label:`温度外环 ${batch.id ?? index+1}`,points:batch.points.map(point=>({x:point.iteration,y:point.accepted === false ? null : point[key]}))})),
    tolerance,unit,xLabel:"冷热侧耦合迭代轮次"})).join("");
  const rows=points.map(point=>[fmt(point.preburner_iteration),fmt(point.iteration),
    ...COUPLED_CHECKS.map(([key])=>fmt(point[key])),fmt(point.pressure_relaxation),
    point.accepted === false ? "拒绝并回退" : point.accepted === true ? "接受" : "未记录"]);
  return `${charts}<details class="cv-values"><summary>查看六项冷热侧与供给耦合残差</summary>${valueTable(["温度外环","耦合迭代",...COUPLED_CHECKS.map(([,label,,unit])=>`${label} / ${unit}`),"压力松弛系数","接受状态"],rows)}</details>`;
}

function initializationDifference(layer) {
  const difference=layer?.initialization_difference;
  if (!coupled(layer) || !difference || typeof difference !== "object") return "";
  return `<details class="cv-values cv-initialization"><summary>末次求解起点与结果差异（不参与收敛验收）</summary><p class="cv-description">起点可能来自前一工况的热启动，不等同于方案中的启动估计。这些差值不是物理收敛残差。</p>${valueTable(["起点差异","记录值","单位"],[["主室温度差",fmt(difference.temperature_K),"K"],["比热比相对差",fmt(difference.gamma_relative),"1"],["摩尔质量相对差",fmt(difference.molar_mass_relative),"1"]])}</details>`;
}

export function renderGasInitialization(result = {}) {
  const calculation=result.calculation || {}, inputs=result.submitted_inputs || calculation.inputs || {};
  const chamber=calculation.states?.main_chamber || {}, properties=chamber.metadata || {};
  const state=numericalDisplayState(result);
  const heading=state.status==="pass" ? "收敛计算状态" : state.status==="fail" ? "当前迭代状态（未验收）" : "记录状态（收敛待核）";
  const rows=[["主室温度","chamber_temp_guess",chamber.temperature,"K"],
    ["比热比 γ","chamber_gamma",properties.gamma,"—"],
    ["平均摩尔质量","molar_mass",properties.molar_mass,"kg/kmol"]];
  const budget=inputs.cooling_bartz_mode===0;
  return `<section class="cv-gas-initialization" aria-label="启动估计与主室计算状态"><h3>启动估计与主室计算状态</h3><p class="cv-description">启动估计与计算状态可以不同，验收看物理残差。下表来自所选计算记录，不随概念设计草稿变化。</p>${budget ? '<p class="cv-description">本记录采用给定冷却预算，未使用这三个冷却启动估计。</p>' : ""}${valueTable(["物理量","方案启动估计",heading,"单位"],rows.map(([label,key,value,unit])=>[label,fmt(inputs[key]),fmt(value),unit]))}</section>`;
}

function outerHistory(trace) {
  const thermal=list(trace.thermal_history).filter(point=>!coupled(point)), preburner=list(trace.preburner_history),batches=historyBatches(thermal);
  const thermalChart=thermal.length ? residualPlot({title:"主室温度反馈残差",series:batches.map((batch,index)=>({label:`温度外环 ${batch.id ?? index+1}`,points:batch.points.map(point=>({x:point.iteration,y:point.accepted === false ? null : point.residual_K}))})),tolerance:trace.layers?.thermal?.tolerance,unit:"K",xLabel:"热反馈迭代轮次"}) : '<p class="cv-empty">本记录未保存主室热力反馈轨迹。</p>';
  const pbKeys=[["ox_error_K","富氧温度偏差绝对值"],["fuel_error_K","富燃温度偏差绝对值"]];
  const pbChart=preburner.length ? residualPlot({title:"预燃室目标温度残差",series:pbKeys.map(([key,label])=>({label,points:preburner.map(point=>({x:point?.iteration,y:finite(point?.[key]) ? Math.abs(point[key]) : null}))})),tolerance:trace.layers?.preburner?.tolerance,unit:"K",xLabel:"温度外环轮次"}) : `<p class="cv-empty">${trace.layers?.preburner?.enabled === false ? "本工况未启用预燃室目标温度外环。" : "本记录未保存预燃室温度外环轨迹。"}</p>`;
  const legacyDetails=thermal.length ? `<details class="cv-values"><summary>查看热反馈数值（含比热比与摩尔质量）</summary>${valueTable(["温度外环","热外环","温度残差 / K","γ 相对残差","M 相对残差","松弛系数","接受状态"],thermal.map(point=>[fmt(point?.preburner_iteration),fmt(point?.iteration),fmt(point?.residual_K),fmt(point?.gamma_relative_residual),fmt(point?.molar_mass_relative_residual),fmt(point?.relaxation),point?.accepted === false ? "拒绝并回退" : point?.accepted === true ? "接受" : "未记录"]))}</details>` : "";
  return `<div class="cv-outer-grid"><section>${coupledHistory(trace)}${thermal.length || !coupled(trace.layers?.thermal) ? thermalChart+legacyDetails : ""}${initializationDifference(trace.layers?.thermal)}</section><section>${pbChart}${preburner.length ? `<details class="cv-values"><summary>查看温度偏差（有符号原始值）</summary>${valueTable(["外环轮次",...pbKeys.map(([,label])=>label.replace("绝对值","")+" / K"),"最大绝对残差 / K"],preburner.map(point=>[fmt(point?.iteration),...pbKeys.map(([key])=>fmt(point?.[key])),fmt(point?.residual_K)]))}</details>` : ""}</section></div>`;
}

export function renderConvergence(result = {}) {
  if(initializationDiagnostic(result))return renderInitializationDiagnostics(result);
  const state=numericalDisplayState(result),trace=result.calculation?.convergence;
  const header=`<div class="cv-heading"><div><span class="cv-kicker">SOLVER CONVERGENCE</span><h2>完整收敛与迭代历程</h2><p>计算结束后展示记录中的真实迭代轨迹；数值收敛与系统约束分别判定。</p></div>${badge(state.status,state.label)}</div>`;
  if (trace?.schema_version !== 1) {
    const summary=result.calculation?.summary || {};
    return `<section class="panel convergence-workspace" aria-label="完整收敛与迭代历程">${header}<p class="cv-notice">历史记录未保存完整收敛轨迹，无法还原残差曲线。以下为当前只读复核；原计算记录与原校核状态保持不变。重新运行设计点后可查看完整曲线和分层判据。</p>${renderGasInitialization(result)}${valueTable(["已有证据","记录值","复核说明"],[["内层残差范数",fmt(summary.solver_residual_norm ?? state.residual),"仅内层残差小，不能证明整个循环收敛"],["主室温度外环残差",`${fmt(summary.tc_outer_residual_K)} K`,"旧热反馈容差 5 K；仍缺少完整反馈与延续证据"],["原记录数值状态",result.assessment?.numerical?.label || result.assessment?.numerical?.status || "未记录","保留历史信息，不作为完整收敛结论"]])}</section>`;
  }
  const warmNotice=trace.warm_start?.rejection_reason==="incompatible_or_unversioned_vector"
    ? '<p class="cv-notice">未复用旧热启动向量：缺少变量身份标记，或与当前设计方式的变量定义不一致。本次使用原有冷初值；不会把涡轮压比当成交叉流量。</p>' : "";
  const feedbackDescription=coupled(state.layers?.thermal)
    ? "冷热侧与供给耦合检查压力、热气温差、共同几何、总吸热量、热气状态向量和逐单元热量六项残差；初值差独立展示，不作为收敛判据。"
    : "热力反馈同时检查温度、比热比和摩尔质量；未接受的热反馈试探保留在数值表中，曲线在该处断开。";
  return `<section class="panel convergence-workspace" aria-label="完整收敛与迭代历程">${header}${warmNotice}${layerCards(state.layers)}<p class="cv-description">完整收敛要求所有启用层满足各自判据，并到达目标工况。${feedbackDescription}</p>${renderGasInitialization(result)}${methodAudit(trace)}${outerHistory(trace)}${innerHistory(trace.inner_runs,trace.layers?.thermal)}</section>`;
}
