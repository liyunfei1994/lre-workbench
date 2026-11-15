// Read-only design context. Values belong to a saved calculation, never to a
// fresh evaluation or a design target copied into a result field.
import {numericalDisplayState} from "./convergence.js";
const escape = value => String(value ?? "").replace(/[&<>"']/g,
  char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const firstNumber = (...values) => values.find(finite);
const scaled = (value, factor) => finite(value) ? value * factor : undefined;
const format = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:6}) : "未记录";
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const jump = (id, label) => `<button type="button" data-ev-related="${escape(id)}" data-ev-origin="preburner_design">定位来源：${escape(label)} →</button>`;

export function renderPreburnerInterfaces(inputs = {}, groups) {
  const cooling = inputs.cooling_bartz_mode === 2 ? "吸热功率由概略受热面和参考壁温估算，压降为工程估计；出口焓反馈预燃室入口，实际冷却能力待部件校核。"
    : inputs.cooling_bartz_mode === 1
    ? "再生冷却的吸热与压降由沿程模型计算，冷却出口状态反馈预燃室入口。"
    : inputs.cooling_bartz_mode === 0
      ? "当前采用给定吸热与压降预算；预算值参与冷却出口状态及预燃室入口焓计算。"
      : "冷却模型尚未确定；需设置冷却吸热与压降的计算方式。";
  const cards = [
    ["推进剂入口状态", "预燃室入口采用泵后支路状态；贮箱温度与压力在供给边界中设置。", [["inlet", "供给边界"]]],
    ["压力分配", "按四条预燃室供给支路和两条涡轮排气通路设置设计压降；主泵出口压力需求由循环匹配计算。", [["pressure_ffsc", "压力分配与压降预算"]]],
    ["冷却与入口焓", cooling, [["cooling", "再生冷却模型"]]],
    ["工质与热化学", "各支路入口焓、组成和压力由系统传递；出口燃气状态使用方案选定的状态方程、物种集和热化学数据计算。", [["chamber_chemistry", "工质与热物性模型"]]],
    ["涡轮与轴功率匹配", "出口燃气状态用于计算涡轮功率，分别与氧化剂泵和燃料泵功率匹配；设计点采用给定部件效率。", [["component_efficiencies", "泵与涡轮效率"]]],
    ["迭代初值", "指定温度时，氧燃比仅作为反求初值；压力初猜与数值配置在总体设计点计算中管理。", [["initialization", "初始化"]]],
    ["工程限值（按需）", "温度上限不是循环匹配的必填条件。有明确工程限值时，在校核与记录中登记；未设置时保留工作温度求解，该项限值校核为未评估。", [["engineering_constraints", "工程限值与校核"]]],
  ];
  return `<section class="ev-relations preburner-relations"><h3>关联模型与参数来源</h3><p class="ev-note">入口状态、流量和压力由总体循环联立求解。以下为来源说明，需要调整时再定位到对应设置。</p>${cards.map(([title, description, links]) =>
    `<details><summary>${escape(title)}</summary><p>${escape(description)}</p>${links.filter(([id])=>!groups||groups.some(group=>group.id===id)).map(([id,label])=>jump(id,label)).join("")}</details>`).join("")}</section>`;
}

export function renderPreburnerConditions(mode) {
  const text = mode === "temperature"
    ? "给定：两侧出口燃气目标温度。待求：氧燃质量比、支路流量和预燃室压力；与供给网络及轴功率平衡联立闭合。"
    : mode === "mixture_ratio"
      ? "给定：两侧预燃室氧燃质量比。待求：出口燃气温度、支路流量和预燃室压力；与供给网络及轴功率平衡联立闭合。"
      : "历史自由分流模式：沿用记录中的分流与压力条件，温度及流量按历史模型计算。";
  return `<div class="preburner-condition-note"><p>${text}</p><p class="fine">温度指模型储腔参考态的出口平均燃气温度；当前模型将该出口状态直接传给涡轮入口。混合不均匀、局部峰值及金属温度由后续部件分析确定。</p></div>`;
}

// Missing historical status can only become provisional when a saved limit
// exists. Never manufacture a limit or a target from today's defaults.
export function preburnerLimitEvidence(inputs = {}, side, check = {}) {
  const key = `${side}_preburner_temp_limit`;
  const rawLimit = Object.hasOwn(check,"limit") ? check.limit : inputs[key];
  const historical = !Object.hasOwn(check,"limit_status") && !Object.hasOwn(inputs,`${key}_status`);
  const status = check.limit_status ?? inputs[`${key}_status`] ?? (finite(rawLimit) ? "provisional" : "unknown");
  const basis = String(check.limit_basis ?? inputs[`${key}_basis`] ?? "").trim();
  const limit = status === "unset" ? null : rawLimit;
  const valid = status === "unset" || (["provisional","documented"].includes(status) && finite(limit) && limit > 0 && (status !== "documented" || basis !== ""));
  return {status,basis,limit,historical,valid};
}

export function preburnerLimitSetupState(inputs, side) {
  const limit = preburnerLimitEvidence(inputs,side);
  const needsBasis = limit.status === "documented" && !limit.basis;
  const message = limit.status === "unset" ? "未建立温度上限：可进行循环求解，该项工程校核为未评估。"
    : !finite(limit.limit) || limit.limit <= 0 ? "限值设置不完整：请填写大于零的温度上限。"
    : needsBasis ? "尚未登记限值来源与适用工况：请补全下方依据说明。"
    : !limit.valid ? "限值依据状态无效，请重新选择。"
    : limit.status === "provisional" ? "暂定限值用于方案筛选，依据待核实；保存设置不表示限值已验证。"
    : "已登记来源与适用工况；登记本身不代表部件温度能力或寿命已验证。";
  return {status:limit.status,message,needsBasis,
    basisLabel:`来源与适用工况 · ${limit.basis?"已填写":"未登记"}${limit.status==="documented"?"（必填）":""}`};
}

export function renderPreburnerLimitSetup(inputs, side) {
  const state = preburnerLimitSetupState(inputs,side);
  return `<p class="preburner-limit-note" data-preburner-limit-state="${escape(state.status)}">${state.message}</p>`;
}

// Update explanatory text without rebuilding the textarea or moving its caret.
export function refreshPreburnerLimitSetup(root, inputs) {
  root.querySelectorAll("[data-preburner-limit-side]").forEach(node=>{
    const state=preburnerLimitSetupState(inputs,node.dataset.preburnerLimitSide);
    const note=node.querySelector("[data-preburner-limit-state]");
    if(note){note.textContent=state.message;note.dataset.preburnerLimitState=state.status;}
    const basis=node.querySelector(".preburner-basis");
    const label=basis?.querySelector("summary");
    if(label)label.textContent=state.basisLabel;
    if(basis&&state.needsBasis)basis.open=true;
  });
}

function sameInputs(a, b) {
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return a === b;
  if (typeof a === "number") return finite(a) && finite(b) && a === b;
  if (typeof a !== "object") return a === b;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a), otherKeys = Object.keys(b);
  return keys.length === otherKeys.length && keys.every(key => Object.hasOwn(b, key) && sameInputs(a[key], b[key]));
}

function comparableInputs(inputs) {
  const result = {...inputs};
  // Optional per-branch budgets were added after the first saved records.
  // Only these eight fields treat null and omission as the same inheritance
  // setting. Explicit values and all other fields retain strict comparison.
  for (const branch of ["ox_to_opb", "ox_to_fpb", "fuel_to_opb", "fuel_to_fpb"]) {
    for (const suffix of ["line_dp_ratio", "injector_dp_ratio"]) {
      const key = `${branch}_${suffix}`;
      if (result[key] == null) delete result[key];
    }
  }
  return result;
}

function sideValues(calculation, side) {
  const summary = calculation.summary || {};
  const component = calculation.components?.[`${side}_preburner`]?.outputs || {};
  const state = calculation.states?.[`${side}_preburner_outlet`] || {};
  const pump = calculation.components?.[`${side}_pump`]?.outputs || {};
  const turbine = calculation.components?.[`${side}_preburner_turbine`]?.outputs || {};
  const oxidizer = firstNumber(component.mdot_ox_kgs, summary[`mdot_ox_to_${side}_preburner_kg_s`]);
  const fuel = firstNumber(component.mdot_fuel_kgs, summary[`mdot_fuel_to_${side}_preburner_kg_s`]);
  const pumpPower = firstNumber(pump.power_W, scaled(summary[`${side}_pump_power_MW`], 1e6), scaled(pump.power_MW, 1e6));
  const turbinePower = firstNumber(turbine.turbine_power_W, scaled(summary[`${side}_turbine_power_MW`], 1e6), scaled(turbine.turbine_power_MW, 1e6));
  return {
    oxidizer, fuel,
    flow: firstNumber(component.mdot_total_kgs, state.mass_flow,
      finite(oxidizer) && finite(fuel) ? oxidizer + fuel : undefined),
    mixtureRatio: firstNumber(summary[`${side}_preburner_mr`], component.mixture_ratio_OF),
    pressure: scaled(firstNumber(component.preburner_pressure_Pa,
      summary[`${side}_preburner_pressure_Pa`], state.pressure,
      scaled(component.preburner_pressure_MPa, 1e6)), 1e-6),
    temperature: firstNumber(component.T_combustion_K, summary[`${side}_preburner_T_combustion_K`], state.temperature),
    turbinePower: scaled(turbinePower, 1e-6),
    pumpPower: scaled(pumpPower, 1e-6),
    powerDifference: finite(turbinePower) && finite(pumpPower) ? (turbinePower - pumpPower) / 1000 : undefined,
  };
}

export function renderPreburnerResults(record, inputs) {
  const calculation = record?.result?.calculation;
  if (!object(calculation)) return `<div class="preburner-empty"><p>${record
    ? "所选记录未保存预燃室计算结果。请查看该任务的收敛诊断，或重新运行设计点。"
    : "尚无计算记录。运行设计点后显示两侧预燃室的流量、压力、温度及轴功率匹配结果。"}</p></div>`;
  if (calculation.inputs?.cycle_type && calculation.inputs.cycle_type !== "ffsc") {
    return '<div class="preburner-empty"><p>所选记录不属于全流量补燃循环。请选择相应设计点的计算记录。</p></div>';
  }

  const canCompare = object(calculation.inputs) && calculation.inputs.cycle_type === "ffsc" &&
    object(inputs) && Object.keys(inputs).length > 0;
  const identity = inputs === undefined ? "saved" : !canCompare ? "unknown" : sameInputs(comparableInputs(calculation.inputs), comparableInputs(inputs)) ? "current" : "stale";
  const identityText = {
    saved:"以下数值属于所选计算记录；未使用当前编辑值补齐。",
    current:"记录参数与当前参数一致",
    stale:"当前参数已修改；以下为所选记录的结果，需重新计算",
    unknown:"缺少完整参数快照，无法核对结果与当前参数的一致性",
  }[identity];
  const revision = record.revision === undefined || record.revision === null ? "方案版本未记录" : `方案 v${record.revision}`;
  const submitted = finite(record.created) ? new Date(record.created * 1000) : null;
  const submittedLabel = submitted && finite(submitted.getTime())
    ? `提交时间：${submitted.toLocaleString("zh-CN", {hour12:false})}` : "提交时间未记录";
  const recordLabel = `结果来源：${revision} · ${submittedLabel}`;
  const status = record.status === "succeeded" ? "" : '<p class="notice warning">所选任务未标记为计算完成；已保存数值仅供诊断。</p>';
  const sides = [sideValues(calculation, "ox"), sideValues(calculation, "fuel")];
  const metrics = [
    ["氧化剂入口质量流量", "kg/s", "oxidizer"],
    ["燃料入口质量流量", "kg/s", "fuel"],
    ["出口燃气质量流量", "kg/s", "flow"],
    ["氧燃质量比", "—", "mixtureRatio"],
    ["预燃室压力", "MPa", "pressure"],
    ["出口燃气温度", "K", "temperature"],
    ["对应涡轮输出功率", "MW", "turbinePower"],
    ["对应主泵轴功率", "MW", "pumpPower"],
    ["轴功率差（涡轮 − 主泵）", "kW", "powerDifference"],
  ];
  return `<div class="preburner-result-context" data-preburner-result-state="${identity}">
    <p class="preburner-result-source">${escape(recordLabel)}</p><p class="preburner-result-status ${["current","saved"].includes(identity) ? "fine" : "notice warning"}">${identityText}</p>${status}
    <div class="table-wrap"><table class="preburner-result-table"><thead><tr><th scope="col">计算量</th><th scope="col">单位</th><th scope="col">富氧预燃室 / 氧化剂侧轴系</th><th scope="col">富燃预燃室 / 燃料侧轴系</th></tr></thead>
    <tbody>${metrics.map(([label, unit, key]) => `<tr><th scope="row">${escape(label)}</th><td>${escape(unit)}</td>${sides.map(side => `<td>${escape(format(side[key]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <p class="preburner-scope-note">本表为所选记录的系统计算量，收敛与约束判定见“收敛诊断”和“计算结果”。压力与温度采用模型储腔参考态；出口燃气温度不代表金属温度。局部温度分布、燃烧稳定性及材料相容性尚未评估。</p>
  </div>`;
}

function savedMode(calculation) {
  const inputs = calculation.inputs || {}, summary = calculation.summary || {};
  if (finite(inputs.use_target_preburner_temp)) {
    if (inputs.use_target_preburner_temp >= .5) return "temperature";
    if (finite(inputs.use_target_preburner_mr)) return inputs.use_target_preburner_mr >= .5 ? "mixture_ratio" : "free";
  }
  if (summary.preburner_temp_target_mode === 1) return "temperature";
  return "unknown";
}

export function preburnerSavedSummary(record, side) {
  const calculation = record?.result?.calculation || {}, inputs = calculation.inputs || {};
  const mode = savedMode(calculation), values = sideValues(calculation,side);
  const target = mode === "temperature" ? firstNumber(inputs[`${side}_preburner_temp_target`],calculation.summary?.[`${side}_preburner_temp_target_K`])
    : mode === "mixture_ratio" ? inputs[`${side}_preburner_mr_target`] : undefined;
  const actual = mode === "temperature" ? values.temperature : mode === "mixture_ratio" ? values.mixtureRatio : undefined;
  const check = record?.result?.assessment?.checks?.find(item=>item.key===`${side}_preburner_T_combustion_K`) || {};
  const evidence = preburnerLimitEvidence(inputs,side,check);
  const margin = evidence.valid && evidence.status !== "unset" && finite(values.temperature) ? evidence.limit-values.temperature : undefined;
  const converged = record?.status === "succeeded" && numericalDisplayState(record.result).status === "pass";
  let verdict = "未评估：限值未记录或设置不完整", tone = "unknown";
  if (evidence.status === "unset") verdict = "未评估：未建立温度上限";
  else if (evidence.valid && !finite(values.temperature)) verdict = "未评估：出口燃气温度未记录";
  else if (finite(margin)) {
    const provisional = evidence.status === "provisional";
    tone = margin < 0 ? "exceeded" : !converged ? "unknown" : provisional ? "provisional" : "within";
    verdict = margin < 0 ? `超出${provisional?"暂定":"已登记"}限值`
      : !converged ? "计算值未超限；数值收敛待核对"
      : provisional ? "满足暂定限值，依据待核实" : "满足已登记限值，适用工况需核对";
    if (!converged && margin < 0) verdict += "；数值收敛待核对";
  }
  return {mode,values,target,actual,evidence,margin,converged,verdict,tone,
    deviation:finite(target)&&finite(actual)?actual-target:undefined};
}

export function renderPreburnerSummary(record) {
  const calculation=record?.result?.calculation;
  if (!object(calculation) || calculation.inputs?.cycle_type && calculation.inputs.cycle_type!=="ffsc") return renderPreburnerResults(record);
  const numerical=numericalDisplayState(record.result), completed=record.status==="succeeded";
  const rows=[["ox","富氧预燃室"],["fuel","富燃预燃室"]].map(([side,label])=>{
    const model=preburnerSavedSummary(record,side),{mode,values,evidence}=model;
    const targetLabel=mode==="temperature"?"记录目标温度 / K":mode==="mixture_ratio"?"记录目标氧燃比":mode==="free"?"历史自由分流模式":"匹配方式未记录";
    const unit=mode==="temperature"?"K":"—";
    const limitLabel=evidence.status==="unset"?"未设置":evidence.status==="documented"?"依据已登记":evidence.status==="provisional"?(evidence.historical?"历史暂定":"设计暂定"):"未记录";
    return `<article class="preburner-summary-side"><h4>${label}</h4><dl>
      <div><dt>${targetLabel}</dt><dd>${escape(format(model.target))}</dd></div>
      <div><dt>计算出口燃气温度 / K</dt><dd>${escape(format(values.temperature))}</dd></div>
      <div><dt>计算氧燃质量比</dt><dd>${escape(format(values.mixtureRatio))}</dd></div>
      <div><dt>计算值 − 目标 / ${unit}</dt><dd>${escape(format(model.deviation))}</dd></div>
      ${evidence.status==="unset"?"":`<div><dt>记录温度上限 / K</dt><dd>${escape(format(evidence.limit))}</dd></div>
      <div><dt>限值依据状态</dt><dd>${limitLabel}</dd></div>
      <div><dt>温度裕量（上限 − 计算值）/ K</dt><dd>${escape(format(model.margin))}</dd></div>`}</dl>
      <p class="preburner-limit-verdict" data-limit-verdict="${model.tone}">${model.verdict}</p>
      ${evidence.status==="unset"?"":`<details><summary>记录中的限值依据与适用工况</summary><p>${escape(evidence.basis||"未登记")}</p></details>`}</article>`;
  });
  return `<section class="preburner-saved-summary"><h3>预燃室匹配摘要</h3><p class="fine">目标、计算值和限值均取自所选记录。偏差供对照，收敛判定采用记录中的完整数值准则。</p>
    <p class="preburner-numerical-state">数值状态：${completed?escape(numerical.label):"任务未成功完成，保存值仅供诊断"}</p>
    <div class="preburner-summary-grid">${rows.join("")}</div><p class="fine">出口燃气温度不等于金属温度；限值校核不替代局部热环境、材料相容性与寿命校核。</p></section>`;
}

export function renderPreburnerRequirements(record) {
  const calc=record?.result?.calculation;
  if (!object(calc) || calc.inputs?.cycle_type && calc.inputs.cycle_type!=="ffsc") return "";
  const branches=[["ox_to_opb","富氧预燃室 · 氧化剂入口"],["fuel_to_opb","富氧预燃室 · 燃料入口"],["ox_to_fpb","富燃预燃室 · 氧化剂入口"],["fuel_to_fpb","富燃预燃室 · 燃料入口"]];
  const rows=branches.map(([id,label])=>{
    const state=calc.states?.[`branch_${id}`]||{};
    return `<tr><th scope="row">${label}</th><td>${escape(format(state.mass_flow))}</td><td>${escape(format(scaled(state.pressure,1e-6)))}</td><td>${escape(format(state.temperature))}</td><td>${escape(format(scaled(state.enthalpy,1e-3)))}</td></tr>`;
  }).join("");
  return `<section class="lifecycle-card preburner-requirements" id="preburner-requirements"><h3>预燃室工作条件与部件交接</h3>
    ${renderPreburnerResults(record)}${renderPreburnerSummary(record)}
    <details><summary>入口支路状态与交接范围</summary><div class="table-wrap"><table><thead><tr><th scope="col">供给支路</th><th scope="col">质量流量 / kg/s</th><th scope="col">压力 / MPa</th><th scope="col">温度 / K</th><th scope="col">比焓 / kJ/kg</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="fine">支路端口为模型中的供给损失后集总状态，不能直接解释为喷注器上游总压。焓及组成应连同记录的物性模型与参考态交接；完整组成和端口定义在“流路状态”查看。</p>
    <p>将入口状态、流量、出口燃气条件和限值依据交给预燃室与喷注器设计。喷注孔及混合结构、局部热流与壁温、燃烧稳定性、材料相容性和寿命需在部件阶段校核。</p></details></section>`;
}
