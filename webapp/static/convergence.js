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
const backendName = name => name === "trf" ? "TRF 有界信赖域" : name === "newton" || !name ? "Newton" : String(name);
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
const CHECK_NAMES={temperature:"主室温度",gamma:"比热比相对变化",gamma_relative_residual:"比热比相对变化",molar_mass:"摩尔质量相对变化",molar_mass_relative_residual:"摩尔质量相对变化",ox_temperature:"富氧预燃室温度",fuel_temperature:"富燃预燃室温度",gg_temperature:"GG 温度",...Object.fromEntries(COUPLED_CHECKS.map(([name,label])=>[name,label]))};
const reason = value => REASONS[value] || value || "未记录终止原因";
const badge = (status, text) => `<span class="cv-badge cv-${["pass","fail","disabled"].includes(status) ? status : "unknown"}">${escape(text || ({pass:"已收敛",fail:"未收敛",disabled:"未启用",unknown:"待确认"}[status]) || "待确认")}</span>`;

/** Historical records stay immutable; this is a conservative display assessment. */
export function numericalDisplayState(result = {}) {
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
    const required={thermal:coolingOn(inputs.cooling_bartz_mode),preburner:on(inputs[inputs.cycle_type === "gg" ? "use_target_gg_temp" : "use_target_preburner_temp"])};
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
  const pbErrors = [summary.ox_preburner_temp_error_K,summary.fuel_preburner_temp_error_K,summary.gg_temp_outer_error_K];
  const failed = recorded.status === "fail" || summary.solver_warning === true || (finite(summary.solver_warning) && summary.solver_warning > 0)
    || (finite(norm) && (norm < 0 || norm >= 1e-4)) || (finite(thermal) && (thermal < 0 || thermal > 5))
    || pbErrors.some(value => finite(value) && Math.abs(value) > 3);
  return {...recorded,status:failed ? "fail" : "unknown",label:failed ? "历史结果复核未收敛" : "历史收敛证据不完整",residual:finite(norm) ? norm : recorded.residual,legacy_incomplete:true,layers};
}

function valueTable(headings, rows) {
  return `<div class="cv-table-wrap"><table><thead><tr>${headings.map(title=>`<th scope="col">${escape(title)}</th>`).join("")}</tr></thead><tbody>${rows.map(cells=>`<tr>${cells.map(value=>`<td>${escape(value)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
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
    const audit=run.backend ? `<p class="cv-description cv-run-audit">${escape(backendName(run.backend))} · ${run.warm_start ? "热启动" : "初始猜测"} · 函数求值 ${fmt(run.nfev)} 次 / 上限 ${fmt(run.evaluation_budget)} · 雅可比 ${fmt(run.njev)} 次 · 未接受试探 ${fmt(run.rejected_steps)} 次</p><details class="cv-values"><summary>查看本次求解的固定缩放</summary><p class="cv-description">归一化残差 = 原始方程残差 / 固定尺度；尺度只在每次独立求解开始时确定。功率尺度随工况变化，不同求解的范数应结合尺度比较。</p>${valueTable(["方程","残差尺度（N 或 W）","容许绝对残差（N 或 W）"],scales.map((scale,i)=>[columns[i] || `方程 ${i+1}`,fmt(scale),fmt(finite(scale) && finite(run.tolerance) ? scale*run.tolerance : null)]))}${valueTable(["变量序号","变量尺度"],variableScales.map((scale,i)=>[i+1,fmt(scale)]))}</details>` : "";
    return `<details class="cv-run"${index === validRuns.length-1 ? " open" : ""}><summary><span>${escape(label)}</span><span>${fmt(last?.residual_norm)} · ${escape(reason(run.termination_reason))}</span></summary>${audit}${residualPlot({title:`${backendName(run.backend)} 归一化残差范数`,series:[{label:"‖r‖∞",points:points.map(point=>({x:point?.iteration,y:point?.residual_norm}))}],tolerance:run.tolerance})}<details class="cv-values"><summary>查看逐步残差与方程分量</summary>${valueTable(["接受步","残差范数",...columns,"累计求值","缩放步长"],points.map(point=>[fmt(point?.iteration),fmt(point?.residual_norm),...columns.map(key=>fmt(point?.residuals?.[key])),fmt(point?.nfev),fmt(point?.step_norm)]))}</details></details>`;
  };
  return `<div class="cv-inner"><h3>内层残差曲线 <small>${validRuns.length} 次独立求解</small></h3><p class="cv-description">每次内层调用单独作图；不同延续阶段、热外环或温度外环之间不连线。默认展开最后一次求解。</p>${renderRun(validRuns.at(-1),validRuns.length-1)}${validRuns.length > 1 ? `<details class="cv-archive"><summary>查看此前 ${validRuns.length-1} 次内层求解</summary>${validRuns.slice(0,-1).map(renderRun).join("")}</details>` : ""}</div>`;
}

function methodAudit(trace) {
  const runs=list(trace.inner_runs), attempts=list(trace.continuation_history);
  if (!runs.some(run=>run.backend)) return "";
  const total=key=>runs.every(run=>nonnegative(run[key])) ? runs.reduce((sum,run)=>sum+run[key],0) : null;
  const backend=[...new Set(runs.map(run=>backendName(run.backend)))].join(" / ");
  const cards=[["求解方法",backend],["函数求值（含差分与试探）",fmt(total("nfev"))],["雅可比计算",fmt(total("njev"))],["未接受试探",fmt(total("rejected_steps"))],["热启动内层调用",fmt(runs.filter(run=>run.warm_start === true).length)]];
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
  return `<details class="cv-values cv-initialization"><summary>初值与当前解的差异（不参与收敛验收）</summary><p class="cv-description">这里只记录热反馈初值与已计算状态的差异；不作为额外物理方程，也不表示物性变化残差。</p>${valueTable(["初值差异","记录值","单位"],[["主室温度差",fmt(difference.temperature_K),"K"],["比热比相对差",fmt(difference.gamma_relative),"1"],["摩尔质量相对差",fmt(difference.molar_mass_relative),"1"]])}</details>`;
}

function outerHistory(trace) {
  const thermal=list(trace.thermal_history).filter(point=>!coupled(point)), preburner=list(trace.preburner_history),batches=historyBatches(thermal);
  const thermalChart=thermal.length ? residualPlot({title:"主室温度反馈残差",series:batches.map((batch,index)=>({label:`温度外环 ${batch.id ?? index+1}`,points:batch.points.map(point=>({x:point.iteration,y:point.accepted === false ? null : point.residual_K}))})),tolerance:trace.layers?.thermal?.tolerance,unit:"K",xLabel:"热反馈迭代轮次"}) : '<p class="cv-empty">本记录未保存主室热力反馈轨迹。</p>';
  const pbKeys=preburner.some(point=>Object.hasOwn(point || {},"gg_error_K")) ? [["gg_error_K","GG 温度偏差绝对值"]] : [["ox_error_K","富氧温度偏差绝对值"],["fuel_error_K","富燃温度偏差绝对值"]];
  const pbChart=preburner.length ? residualPlot({title:"预燃室目标温度残差",series:pbKeys.map(([key,label])=>({label,points:preburner.map(point=>({x:point?.iteration,y:finite(point?.[key]) ? Math.abs(point[key]) : null}))})),tolerance:trace.layers?.preburner?.tolerance,unit:"K",xLabel:"温度外环轮次"}) : `<p class="cv-empty">${trace.layers?.preburner?.enabled === false ? "本工况未启用预燃室目标温度外环。" : "本记录未保存预燃室温度外环轨迹。"}</p>`;
  const legacyDetails=thermal.length ? `<details class="cv-values"><summary>查看热反馈数值（含比热比与摩尔质量）</summary>${valueTable(["温度外环","热外环","温度残差 / K","γ 相对残差","M 相对残差","松弛系数","接受状态"],thermal.map(point=>[fmt(point?.preburner_iteration),fmt(point?.iteration),fmt(point?.residual_K),fmt(point?.gamma_relative_residual),fmt(point?.molar_mass_relative_residual),fmt(point?.relaxation),point?.accepted === false ? "拒绝并回退" : point?.accepted === true ? "接受" : "未记录"]))}</details>` : "";
  return `<div class="cv-outer-grid"><section>${coupledHistory(trace)}${thermal.length || !coupled(trace.layers?.thermal) ? thermalChart+legacyDetails : ""}${initializationDifference(trace.layers?.thermal)}</section><section>${pbChart}${preburner.length ? `<details class="cv-values"><summary>查看温度偏差（有符号原始值）</summary>${valueTable(["外环轮次",...pbKeys.map(([,label])=>label.replace("绝对值","")+" / K"),"最大绝对残差 / K"],preburner.map(point=>[fmt(point?.iteration),...pbKeys.map(([key])=>fmt(point?.[key])),fmt(point?.residual_K)]))}</details>` : ""}</section></div>`;
}

export function renderConvergence(result = {}) {
  const state=numericalDisplayState(result),trace=result.calculation?.convergence;
  const header=`<div class="cv-heading"><div><span class="cv-kicker">SOLVER CONVERGENCE</span><h2>完整收敛与迭代历程</h2><p>计算结束后展示记录中的真实迭代轨迹；数值收敛与系统约束分别判定。</p></div>${badge(state.status,state.label)}</div>`;
  if (trace?.schema_version !== 1) {
    const summary=result.calculation?.summary || {};
    return `<section class="panel convergence-workspace" aria-label="完整收敛与迭代历程">${header}<p class="cv-notice">历史记录未保存完整收敛轨迹，无法还原残差曲线。以下为当前只读复核；原计算记录与原校核状态保持不变。重新运行设计点后可查看完整曲线和分层判据。</p>${valueTable(["已有证据","记录值","复核说明"],[["内层残差范数",fmt(summary.solver_residual_norm ?? state.residual),"仅内层残差小，不能证明整个循环收敛"],["主室温度外环残差",`${fmt(summary.tc_outer_residual_K)} K`,"现行容差 5 K；仍缺少完整反馈与延续证据"],["原记录数值状态",result.assessment?.numerical?.label || result.assessment?.numerical?.status || "未记录","保留历史信息，不作为完整收敛结论"]])}</section>`;
  }
  const feedbackDescription=coupled(state.layers?.thermal)
    ? "冷热侧与供给耦合检查压力、热气温差、共同几何、总吸热量、热气状态向量和逐单元热量六项残差；初值差独立展示，不作为收敛判据。"
    : "热力反馈同时检查温度、比热比和摩尔质量；未接受的热反馈试探保留在数值表中，曲线在该处断开。";
  return `<section class="panel convergence-workspace" aria-label="完整收敛与迭代历程">${header}${layerCards(state.layers)}<p class="cv-description">完整收敛要求所有启用层满足各自判据，并到达目标工况。${feedbackDescription}</p>${methodAudit(trace)}${outerHistory(trace)}${innerHistory(trace.inner_runs,trace.layers?.thermal)}</section>`;
}
