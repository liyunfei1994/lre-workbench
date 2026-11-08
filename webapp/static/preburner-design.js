// Read-only design context. Values belong to a saved calculation, never to a
// fresh evaluation or a design target copied into a result field.
const escape = value => String(value ?? "").replace(/[&<>"']/g,
  char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const firstNumber = (...values) => values.find(finite);
const scaled = (value, factor) => finite(value) ? value * factor : undefined;
const format = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:6}) : "未记录";
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const jump = (id, label) => `<button type="button" data-preburner-group="${escape(id)}">${escape(label)} →</button>`;

export function renderPreburnerInterfaces(inputs = {}) {
  const cooling = finite(inputs.cooling_bartz_mode) && inputs.cooling_bartz_mode > 0
    ? "再生冷却的吸热与压降由沿程模型计算，冷却出口状态反馈预燃室入口。"
    : inputs.cooling_bartz_mode === 0
      ? "当前采用给定吸热与压降预算；预算值参与冷却出口状态及预燃室入口焓计算。"
      : "冷却模型尚未确定；需设置冷却吸热与压降的计算方式。";
  const cards = [
    ["推进剂入口状态", "预燃室入口采用泵后支路状态；贮箱温度与压力在供给边界中设置。", [["inlet", "供给边界"]]],
    ["压力分配", "按四条预燃室供给支路和两条涡轮排气通路设置设计压降；主泵出口压力需求由循环匹配计算。", [["pressure_ffsc", "压力分配与压降预算"]]],
    ["冷却与入口焓", cooling, [["cooling", "再生冷却模型"]]],
    ["涡轮与轴功率匹配", "出口燃气状态用于计算涡轮功率，分别与氧化剂泵和燃料泵功率匹配；设计点采用给定部件效率。", [["component_efficiencies", "泵与涡轮效率"]]],
  ];
  return `<div class="preburner-interface-grid">${cards.map(([title, description, links]) =>
    `<article class="preburner-interface-card"><h4>${escape(title)}</h4><p>${escape(description)}</p><div class="preburner-interface-links">${links.map(([id, label]) => jump(id, label)).join("")}</div></article>`).join("")}</div>`;
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
  const identity = !canCompare ? "unknown" : sameInputs(comparableInputs(calculation.inputs), comparableInputs(inputs)) ? "current" : "stale";
  const identityText = {
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
    <p class="preburner-result-source">${escape(recordLabel)}</p><p class="preburner-result-status ${identity === "current" ? "fine" : "notice warning"}">${identityText}</p>${status}
    <div class="table-wrap"><table class="preburner-result-table"><thead><tr><th scope="col">计算量</th><th scope="col">单位</th><th scope="col">富氧预燃室 / 氧化剂侧轴系</th><th scope="col">富燃预燃室 / 燃料侧轴系</th></tr></thead>
    <tbody>${metrics.map(([label, unit, key]) => `<tr><th scope="row">${escape(label)}</th><td>${escape(unit)}</td>${sides.map(side => `<td>${escape(format(side[key]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <p class="preburner-scope-note">本表为所选记录的系统计算量，收敛与约束判定见“收敛诊断”和“计算结果”。压力与温度采用模型储腔参考态；出口燃气温度不代表金属温度。局部温度分布、燃烧稳定性及材料相容性尚未评估。</p>
  </div>`;
}
