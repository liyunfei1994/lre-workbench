// Pressure-design presentation. This module never evaluates a pressure loss or
// changes a design input; editable fields remain owned by the parameter editor.
const escape = value => String(value ?? "").replace(/[&<>"']/g,
  char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const array = value => Array.isArray(value) ? value : [];
const number = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:6}) : "未记录";
const mpa = value => number(finite(value) ? value / 1e6 : undefined);
const firstNumber = (...values) => values.find(finite);
const scaled = (value, factor) => finite(value) ? value * factor : undefined;
const jump = (id, label) => `<button type="button" data-pressure-group="${escape(id)}">${escape(label)} →</button>`;

export const pressureBranches = Object.freeze([
  {id:"ox_to_opb", label:"氧化剂 → 富氧预燃室", upstream:"氧化剂主泵出口", downstream:"富氧预燃室", fluid:"ox"},
  {id:"ox_to_fpb", label:"氧化剂 → 富燃预燃室", upstream:"氧化剂主泵出口", downstream:"富燃预燃室", fluid:"ox"},
  {id:"fuel_to_opb", label:"燃料 → 富氧预燃室", upstream:"再生冷却出口", downstream:"富氧预燃室", fluid:"fuel"},
  {id:"fuel_to_fpb", label:"燃料 → 富燃预燃室", upstream:"再生冷却出口", downstream:"富燃预燃室", fluid:"fuel"},
].map(Object.freeze));
const gasBranches = [
  {id:"ox_gas", side:"ox", label:"富氧燃气 → 主燃烧室", upstream:"富氧涡轮出口", key:"ox_turbine_exhaust_dp_ratio"},
  {id:"fuel_gas", side:"fuel", label:"富燃燃气 → 主燃烧室", upstream:"富燃涡轮出口", key:"fuel_turbine_exhaust_dp_ratio"},
];
const sharedKeys = ["valve_line_dp_ratio", "preburner_injector_dp_ratio"];
const branchKeys = branch => [`${branch.id}_line_dp_ratio`, `${branch.id}_injector_dp_ratio`];

export function pressureBranchForField(key) {
  return pressureBranches.find(branch => branchKeys(branch).includes(key))?.id ||
    gasBranches.find(branch => branch.key === key)?.id || null;
}

function reference(label, value, unit = "", formatter = number, key = "") {
  return `<div class="pressure-design-reference"><dt>${escape(label)}</dt><dd data-pressure-reference="${escape(key)}" data-pressure-unit="${escape(unit)}">${escape(formatter(value))}${unit && finite(value) ? ` <span>${escape(unit)}</span>` : ""}</dd></div>`;
}

export function renderPressureTopology(inputs = {}) {
  const cooling = inputs.cooling_bartz_mode === 0
    ? "采用给定冷却压降预算。"
    : finite(inputs.cooling_bartz_mode) && inputs.cooling_bartz_mode > 0
      ? "再生冷却压降由当前沿程模型计算。" : "冷却压降计算方式未记录。";
  const paths = [
    {kind:"ox", title:"氧化剂流路", start:"供给边界", pump:"氧化剂主泵", split:"泵后分流"},
    {kind:"fuel", title:"燃料流路", start:"供给边界", pump:"燃料主泵", split:"再生冷却后分流"},
  ];
  return `<div class="pressure-design-topology">
    <p class="pressure-design-intro">当前流路配置：氧化剂经主泵后分流，燃料经再生冷却后分流；两侧预燃室燃气分别驱动涡轮，涡轮排气进入主燃烧室。</p>
    <div class="pressure-design-paths">${paths.map(path => `<article class="pressure-design-path pressure-design-path--${path.kind}"><h4>${path.title}</h4><ol class="pressure-design-route"><li>${path.start}</li><li>${path.pump}</li>${path.kind === "fuel" ? "<li>再生冷却</li>" : ""}<li>${path.split}</li></ol><div class="pressure-design-path-branches">${pressureBranches.filter(branch => branch.fluid === path.kind).map(branch => `<button type="button" data-pressure-branch="${branch.id}"><span>${branch.label}</span><span aria-hidden="true">→</span></button>`).join("")}</div></article>`).join("")}</div>
    <div class="pressure-design-gas-route"><span>富氧 / 富燃预燃室</span><span aria-hidden="true">→</span><span>对应涡轮</span><span aria-hidden="true">→</span><span>燃气管路与喷注段</span><span aria-hidden="true">→</span><span>主燃烧室</span></div>
    <div class="pressure-design-context-grid"><article><h4>设计边界</h4><dl class="pressure-design-references">
      ${reference("主燃烧室设计压力", inputs.pc, "MPa", mpa, "pc")}
      ${reference("贮箱出口压力", inputs.tank_pressure_pa, "MPa", mpa, "tank_pressure_pa")}
      ${reference("氧化剂供给温度", inputs.ox_inlet_temp, "K", number, "ox_inlet_temp")}
      ${reference("燃料供给温度", inputs.fuel_inlet_temp, "K", number, "fuel_inlet_temp")}
    </dl><div class="pressure-design-links">${jump("design", "设计目标与工况")}${jump("inlet", "推进剂供给边界")}</div></article>
    <article><h4>模型接口</h4><p>${cooling}燃料主泵所需出口压力包含冷却段压降；四条预燃室供给支路从各自分流点起计。</p><p>预燃室出口作为涡轮入口；当前模型未单独计入两者之间的管路压降。</p><div class="pressure-design-links">${jump("cooling", "再生冷却模型")}${jump("preburner_design", "预燃室热力设计")}${jump("component_efficiencies", "泵与涡轮效率")}</div></article></div>
    <p class="pressure-design-note">压力采用集总绝对压力。当前设计模型未逐截面解析总压与静压；设计压降预算、循环匹配结果与部件实际供给能力分别记录。</p>
  </div>`;
}

function fieldBlock(fields, inputs, renderField, renderNotes) {
  return `<div class="pressure-design-fields">${fields.map(field => renderField(field, inputs, {compact:true})).join("")}</div>${fields.length ? renderNotes(fields, inputs) : ""}`;
}

export function renderPressureWorkspace(group, inputs = {}, renderField, renderNotes = () => "") {
  const fields = array(group?.fields), byKey = new Map(fields.map(field => [field.key, field]));
  const take = keys => keys.map(key => byKey.get(key)).filter(Boolean);
  const known = new Set([...sharedKeys, ...pressureBranches.flatMap(branchKeys), ...gasBranches.map(branch => branch.key)]);
  const shared = take(sharedKeys);
  const nav = branches => branches.map(branch => `<button type="button" data-pressure-branch="${branch.id}" aria-pressed="${branch.id === "ox_to_opb"}">${escape(branch.label)}</button>`).join("");
  return `<div class="pressure-design-workspace" data-pressure-workspace>
    <nav class="pressure-design-tabs" aria-label="压力分配工作区"><button type="button" data-pressure-view="topology" aria-pressed="false">流路与边界</button><button type="button" data-pressure-view="budget" aria-pressed="true">压降分配</button><button type="button" data-pressure-view="results" aria-pressed="false">匹配结果</button></nav>
    <section data-pressure-panel="topology" hidden>${renderPressureTopology(inputs)}</section>
    <section data-pressure-panel="budget">
      <p class="pressure-design-intro">按实际流路分配设计点压降。预燃室喷注压降以对应预燃室压力为基准；基础管阀与涡轮排气段压降以主燃烧室压力为基准。</p>
      ${shared.length ? `<details class="pressure-design-shared" data-pressure-shared><summary>统一缺省预算</summary><p class="pressure-design-note">支路字段留空时采用本组缺省值；单独填写支路预算后，以该支路数值为准。</p>${fieldBlock(shared, inputs, renderField, renderNotes)}<button type="button" data-pressure-apply-shared>将缺省预算应用到四条供给支路</button></details>` : ""}
      <div class="pressure-design-budget-layout"><nav class="pressure-design-branch-nav" aria-label="压降支路"><h4>预燃室供给支路</h4>${nav(pressureBranches)}<h4>涡轮排气段</h4>${nav(gasBranches)}</nav><div class="pressure-design-branch-content">
      ${pressureBranches.map(branch => `<section class="pressure-design-branch" data-pressure-branch-panel="${branch.id}" ${branch.id === "ox_to_opb" ? "" : "hidden"}><h4>${escape(branch.label)}</h4><div class="pressure-design-segment"><span>${escape(branch.upstream)}</span><span aria-hidden="true">→</span><span>基础管阀</span><span aria-hidden="true">→</span><span>喷注器</span><span aria-hidden="true">→</span><span>${escape(branch.downstream)}</span></div>${fieldBlock(take(branchKeys(branch)), inputs, renderField, renderNotes)}<p class="pressure-design-note">基础管阀预算包括管路与全开阀损失。共同供给压力匹配产生的附加节流压降在计算结果中列出；该压降不直接代表阀门开度。${branch.fluid === "fuel" ? "本段从再生冷却出口起计，不重复计入冷却段压降。" : ""}</p></section>`).join("")}
      ${gasBranches.map(branch => `<section class="pressure-design-branch" data-pressure-branch-panel="${branch.id}" hidden><h4>${escape(branch.label)}</h4><div class="pressure-design-segment"><span>${escape(branch.upstream)}</span><span aria-hidden="true">→</span><span>燃气管路与喷注段</span><span aria-hidden="true">→</span><span>主燃烧室</span></div>${fieldBlock(take([branch.key]), inputs, renderField, renderNotes)}<p class="pressure-design-note">当前采用涡轮出口至主燃烧室的合计压降预算。涡轮出口压力由主燃烧室压力与该段压降确定；涡轮膨胀压比由循环匹配求解。</p></section>`).join("")}
      </div></div>
      ${fields.some(field => !known.has(field.key)) ? `<div class="pressure-design-extra">${fieldBlock(fields.filter(field => !known.has(field.key)), inputs, renderField, renderNotes)}</div>` : ""}
    </section>
    <section data-pressure-panel="results" hidden><div data-pressure-results>${renderPressureResults(null, inputs)}</div></section>
  </div>`;
}

// Parent retains the selected view/branch across input edits. Search reveals all
// matching field panels; individual row visibility remains owned by its filter.
export function syncPressureWorkspace(root, {view = "budget", branch = "ox_to_opb", search = false, inputs} = {}) {
  const workspaces = root.matches?.("[data-pressure-workspace]") ? [root] : Array.from(root.querySelectorAll("[data-pressure-workspace]"));
  const selectedView = ["topology", "budget", "results"].includes(view) ? view : "budget";
  const selectedBranch = [...pressureBranches, ...gasBranches].some(item => item.id === branch) ? branch : "ox_to_opb";
  for (const workspace of workspaces) {
    const visibleView = search ? "budget" : selectedView;
    workspace.querySelectorAll("[data-pressure-panel]").forEach(panel => { panel.hidden = panel.dataset.pressurePanel !== visibleView; });
    workspace.querySelectorAll("[data-pressure-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.pressureView === visibleView)));
    workspace.querySelectorAll("[data-pressure-branch-panel]").forEach(panel => { panel.hidden = !search && panel.dataset.pressureBranchPanel !== selectedBranch; });
    workspace.querySelectorAll("[data-pressure-branch]").forEach(button => button.setAttribute("aria-pressed", String(!search && button.dataset.pressureBranch === selectedBranch)));
    if (search) workspace.querySelectorAll("[data-pressure-shared]").forEach(details => { details.open = true; });
    workspace.classList.toggle("pressure-design-searching", Boolean(search));
    if (object(inputs)) workspace.querySelectorAll("[data-pressure-reference]").forEach(node => {
      const value = inputs[node.dataset.pressureReference], unit = node.dataset.pressureUnit;
      node.textContent = `${unit === "MPa" ? mpa(value) : number(value)}${finite(value) && unit ? ` ${unit}` : ""}`;
    });
  }
}

function sameInputs(a, b) {
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return a === b;
  if (typeof a === "number") return finite(a) && finite(b) && a === b;
  if (typeof a !== "object") return a === b;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a), other = Object.keys(b);
  return keys.length === other.length && keys.every(key => Object.hasOwn(b, key) && sameInputs(a[key], b[key]));
}

function comparableInputs(inputs) {
  const result = {...inputs};
  // An omitted optional override and a null override both mean inherit the
  // existing shared budget. This is the only compatibility normalization.
  for (const key of pressureBranches.flatMap(branchKeys)) if (result[key] == null) delete result[key];
  return result;
}

function statusLabel(status) {
  return status === "pass" ? '<span class="pressure-design-status pressure-design-status--pass">闭合</span>'
    : status === "fail" ? '<span class="pressure-design-status pressure-design-status--fail">未闭合</span>'
      : '<span class="pressure-design-status">未记录</span>';
}

function table(headers, rows, className = "") {
  return `<div class="table-wrap"><table class="pressure-design-result-table ${className}"><thead><tr>${headers.map(label => `<th scope="col">${escape(label)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
function cells(values) { return values.map(value => `<td>${escape(value)}</td>`).join(""); }

export function renderPressureResults(record, inputs) {
  const calculation = record?.result?.calculation;
  if (!object(calculation)) return `<p class="pressure-design-empty">${record
    ? "所选记录未保存压力分配结果。请查看该任务的收敛诊断，或重新运行设计点。"
    : "尚无计算记录。运行设计点后显示主泵所需压力、各支路压降及压力闭合残差。"}</p>`;
  if (calculation.inputs?.cycle_type && calculation.inputs.cycle_type !== "ffsc") return '<p class="pressure-design-empty">所选记录不属于全流量补燃循环。请选择相应设计点的计算记录。</p>';
  const canCompare = object(calculation.inputs) && calculation.inputs.cycle_type === "ffsc" && object(inputs) && Object.keys(inputs).length > 0;
  const identity = !canCompare ? "unknown" : sameInputs(comparableInputs(calculation.inputs), comparableInputs(inputs)) ? "current" : "stale";
  const identityText = {current:"记录参数与当前参数一致", stale:"当前参数已修改；以下为所选记录的结果，需重新计算", unknown:"缺少完整参数快照，无法核对结果与当前参数的一致性"}[identity];
  const revision = record.revision == null ? "方案版本未记录" : `方案 v${record.revision}`;
  const time = finite(record.created) ? new Date(record.created * 1000) : null;
  const submitted = time && finite(time.getTime()) ? `提交时间：${time.toLocaleString("zh-CN", {hour12:false})}` : "提交时间未记录";
  const network = calculation.steady_closure?.feed_network;
  const context = `<div class="pressure-design-result-context" data-pressure-result-state="${identity}"><p class="pressure-design-result-source">${escape(`结果来源：${revision} · ${submitted}`)}</p><p class="${identity === "current" ? "fine" : "notice warning"}">${identityText}</p>${record.status === "succeeded" ? "" : '<p class="notice warning">所选任务未标记为计算完成；已保存数值仅供诊断。</p>'}</div>`;
  if (!object(network)) return `${context}<p class="pressure-design-empty">所选记录未保存逐支路压力分配数据。重新运行设计点后可查看支路压降与闭合残差。</p>`;
  const contract = object(network.pressure_contract) ? network.pressure_contract : {};
  const branchMap = new Map(array(network.branches).filter(object).map(row => [row.id, row]));
  const gasMap = new Map(array(contract.gas_paths).filter(object).map(row => [row.id, row]));
  const pumpMap = new Map(array(contract.pump_capacity?.pumps).filter(object).map(row => [row.id, row]));
  const budgetOrigin = network.mode === "fixed_resistance"
    ? "所选记录采用冻结支路阻力；各压降由该记录的流量、密度与阻力系数确定。"
    : object(network.design_loss_inputs?.branches) && pressureBranches.every(branch => object(network.design_loss_inputs.branches[branch.id]))
      ? "所选记录已保存四条支路各自采用的设计压降预算。"
      : object(network.design_loss_inputs?.branches)
        ? "所选记录的分支路预算定义不完整；缺失项未记录。"
      : "所选历史记录采用统一支路预算，未保存分支路预算定义。";
  const pumpRows = [["ox", "氧化剂主泵"], ["fuel", "燃料主泵"]].map(([side, label]) => {
    const row = pumpMap.get(side) || {};
    return `<tr><th scope="row">${label}</th>${cells([mpa(row.inlet_pressure_Pa), mpa(row.required_outlet_pressure_Pa), mpa(row.required_rise_Pa), mpa(row.cooling_drop_Pa), number(row.pressure_residual_Pa)])}<td>${statusLabel(row.pressure_link_status)}</td></tr>`;
  });
  const branchRows = pressureBranches.map(branch => {
    const row = branchMap.get(branch.id) || {};
    return `<tr><th scope="row"><button type="button" data-pressure-branch="${branch.id}">${escape(branch.label)}</button></th>${cells([number(row.mass_flow_kg_s), mpa(row.source_pressure_Pa), mpa(row.line_drop_Pa), mpa(row.valve_drop_Pa), mpa(row.injector_drop_Pa), mpa(row.destination_pressure_Pa), number(row.pressure_residual_Pa)])}<td>${statusLabel(row.status)}</td></tr>`;
  });
  const gasRows = gasBranches.map(branch => {
    const row = gasMap.get(branch.side) || {};
    return `<tr><th scope="row">${escape(branch.label)}</th>${cells([mpa(row.turbine_inlet_pressure_Pa), mpa(row.turbine_outlet_pressure_Pa), number(row.turbine_pressure_ratio), mpa(row.hot_gas_drop_Pa), mpa(row.chamber_pressure_Pa), number(row.pressure_residual_Pa)])}<td>${statusLabel(row.status)}</td></tr>`;
  });
  const summary = calculation.summary || {};
  const powerRows = [["ox", "氧化剂侧轴系"], ["fuel", "燃料侧轴系"]].map(([side, label]) => {
    const turbine = calculation.components?.[`${side}_preburner_turbine`]?.outputs || {};
    const pump = calculation.components?.[`${side}_pump`]?.outputs || {};
    const turbinePower = firstNumber(turbine.turbine_power_W, scaled(summary[`${side}_turbine_power_MW`], 1e6));
    const pumpPower = firstNumber(pump.power_W, scaled(summary[`${side}_pump_power_MW`], 1e6));
    const delta = finite(turbinePower) && finite(pumpPower) ? (turbinePower - pumpPower) / 1000 : undefined;
    return `<tr><th scope="row">${label}</th>${cells([number(scaled(turbinePower, 1e-6)), number(scaled(pumpPower, 1e-6)), number(delta)])}</tr>`;
  });
  return `${context}<div class="pressure-design-result-summary"><article><h4>支路压力闭合</h4>${statusLabel(network.status)}<p>最大残差 <strong>${escape(number(network.max_pressure_residual_Pa))}</strong> Pa</p><p>验收容差 ${escape(number(network.pressure_tolerance_Pa))} Pa</p></article><article><h4>供给流量闭合</h4><p>最大残差 <strong>${escape(number(network.max_mass_residual_kg_s))}</strong> kg/s</p><p>验收容差 ${escape(number(network.mass_tolerance_kg_s))} kg/s</p></article><article><h4>机组供给能力</h4><span class="pressure-design-status">待验证</span><p>当前结果为设计点压力需求。泵、涡轮与阀门特性尚未完成能力验证。</p></article></div>
    <p class="pressure-design-note">${budgetOrigin}</p>
    <section class="pressure-design-result-section"><h4>主泵压力需求</h4>${table(["部件", "入口压力 / MPa", "所需出口压力 / MPa", "所需压升 / MPa", "冷却段压降 / MPa", "压力连接残差 / Pa", "压力连接"], pumpRows)}</section>
    <section class="pressure-design-result-section"><h4>预燃室供给支路</h4>${table(["支路", "流量 / kg/s", "分流点压力 / MPa", "基础管阀压降 / MPa", "附加节流压降 / MPa", "喷注压降 / MPa", "预燃室压力 / MPa", "压力残差 / Pa", "支路闭合"], branchRows)}<p class="pressure-design-note">分流点压力 = 基础管阀压降 + 附加节流压降 + 喷注压降 + 预燃室压力。附加节流压降由共同供给压力配平确定，阀门开度需结合阀门特性另行求解。</p></section>
    <section class="pressure-design-result-section"><h4>涡轮膨胀与排气段</h4>${table(["燃气支路", "涡轮入口 / MPa", "涡轮出口 / MPa", "涡轮膨胀压比", "排气段压降 / MPa", "主燃烧室 / MPa", "压力残差 / Pa", "预算闭合"], gasRows)}</section>
    <section class="pressure-design-result-section"><h4>轴功率匹配</h4>${table(["轴系", "涡轮输出 / MW", "主泵需求 / MW", "功率差（涡轮 − 主泵）/ kW"], powerRows)}<p class="pressure-design-note">轴功率验收与完整迭代过程见“收敛诊断”。压力预算闭合不代表泵、涡轮及阀门在该工作点具有实际供给能力。</p></section>
    ${array(network.errors).length ? `<details class="pressure-design-errors" open><summary>压力闭合诊断</summary><ul>${network.errors.map(error => `<li>${escape(error)}</li>`).join("")}</ul></details>` : ""}
    <p class="pressure-design-note">${escape(contract.pressure_convention?.label || "压力口径未记录")}；未保存的量显示“未记录”。支路压降与残差均来自所选计算记录。</p>`;
}
