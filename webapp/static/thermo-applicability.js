// Scientific applicability is evidence attached to a saved result, not a solver success flag.
const finite = value => typeof value === "number" && Number.isFinite(value);
const object = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const list = value => Array.isArray(value) ? value : [];
const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const fmt = value => !finite(value) ? "—" : value !== 0 && Math.abs(value) < 1e-4 ? value.toExponential(3) : value.toLocaleString("zh-CN", {maximumSignificantDigits:6});
const scale = (value, factor) => finite(value) ? value * factor : null;
const cell = value => `<td>${escape(value)}</td>`;
const badge = (status, label) => `<span class="ta-badge ta-${status === "review_required" ? "review" : "unknown"}">${escape(label)}</span>`;
const table = (headings, rows) => `<div class="ta-table-wrap"><table><thead><tr>${headings.map(value=>`<th scope="col">${escape(value)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;

export function thermodynamicDisplayState(result = {}) {
  const evidence = result.calculation?.thermodynamic_applicability ?? result.thermodynamic_applicability;
  if (![1,2].includes(evidence?.schema_version) || (!list(evidence.states).length && !list(evidence.pure_fluid_states).length)) return {available:false,status:"unavailable",label:"热力学适用性未记录",evidence:null};
  return {available:true,status:"review_required",label:"热力学适用性待核实",evidence};
}

export function thermoApplicabilityBadge(result = {}) {
  const state=thermodynamicDisplayState(result);
  return badge(state.status,state.label);
}

export function thermoRangeDisplayState(state = {}) {
  const range=object(state.thermo_range), outside=list(range.species_outside);
  if (range.status === "extrapolated" || outside.length || (finite(range.outside_mass_fraction) && range.outside_mass_fraction > 0)) return "extrapolated";
  if (range.status === "within_declared_ranges" && finite(state.T_K) && finite(range.valid_temperature_min_K)
      && finite(range.valid_temperature_max_K) && state.T_K >= range.valid_temperature_min_K
      && state.T_K <= range.valid_temperature_max_K && range.outside_mass_fraction === 0) return "within_declared_ranges";
  return "unavailable";
}

const rangeLabel = status => ({extrapolated:"发生温区外推",within_declared_ranges:"在所查物种温区内",unavailable:"温区证据不足"}[status]);

export function pureFluidDisplayState(state = {}) {
  if (state.status === "invalid_or_unavailable") return "invalid_or_unavailable";
  const limits=object(state.limits);
  if (state.status === "within_declared_ranges" && [state.T_K,state.p_Pa,limits.Tmin_K,limits.Tmax_K,limits.pmax_Pa].every(finite)
      && state.T_K >= limits.Tmin_K && state.T_K <= limits.Tmax_K && state.p_Pa > 0 && state.p_Pa <= limits.pmax_Pa
      && ["liquid","gas","supercritical_liquid","supercritical_gas","supercritical"].includes(state.phase)) return "within_declared_ranges";
  return "unavailable";
}

function pureFluidEvidence(evidence) {
  const rows=list(evidence.pure_fluid_states).map(object);
  if (!rows.length) return `<p class="ta-notice">本记录未保存纯推进剂温压域、相态与熔化线检查；不以当前模型补写历史证据。</p>`;
  const phases={liquid:"液体",gas:"气体",supercritical_liquid:"压缩液体（p > pc，T < Tc）",supercritical_gas:"超临界温度气体",supercritical:"超临界单相",twophase:"两相"};
  const meltingLabels={above_melting_boundary:"高于熔化边界",not_available:"该流体未提供熔化线",not_near_solid_boundary:"不在当前熔化边界检查温区",outside_melting_correlation_pressure_range:"压力超出熔化线相关式范围"};
  const htmlRows=rows.map(row=>{
    const limits=object(row.limits), melting=object(row.melting_boundary), status=pureFluidDisplayState(row);
    const statusText=status === "within_declared_ranges" ? "温压域与单相已检查" : status === "invalid_or_unavailable" ? "无效或查询失败" : "证据不足";
    const meltText=(meltingLabels[melting.status] || "未记录") + (finite(melting.temperature_K) ? ` · ${fmt(melting.temperature_K)} K` : "");
    return `<tr class="${status === "invalid_or_unavailable" ? "ta-extrapolated" : ""}">${cell(row.label || row.id || "未命名端口")}${cell(row.species || limits.fluid || "—")}${cell(fmt(row.T_K))}${cell(fmt(scale(row.p_Pa,1e-6)))}${cell(phases[row.phase] || row.phase || "—")}${cell(`${fmt(limits.Tmin_K)}–${fmt(limits.Tmax_K)} K；p ≤ ${fmt(scale(limits.pmax_Pa,1e-6))} MPa`)}${cell(meltText)}<td title="${escape(row.reason || row.description || "")}">${escape(statusText)}${row.reason ? `<small class="ta-pure-reason">${escape(row.reason)}</small>` : ""}</td></tr>`;
  });
  return `<details class="ta-details ta-pure"><summary>纯推进剂实际端口适用性（${rows.length} 个记录节点）</summary><p class="ta-notice">检查 HEOS 声明温压范围、单相及可用熔化边界。超临界单相不等于液体；稠密超临界泵入口是模型域判据，不能证明汽蚀能力。范围内也不等于物性精度已验证；没有熔化线时保留未验证项。</p>${table(["端口","工质","温度 / K","压力 / MPa","相态","HEOS 声明范围","熔化边界","检查状态"],htmlRows)}</details>`;
}

function stateTable(states) {
  return table(["燃气节点","温度 / K","压力 / MPa","所查物种共同温区 / K","温区状态","外推物种质量占比"],states.map(state=>{
    const range=object(state.thermo_range), status=thermoRangeDisplayState(state);
    return `<tr class="${status === "extrapolated" ? "ta-extrapolated" : ""}">${cell(state.label || state.id || "未命名节点")}${cell(fmt(state.T_K))}${cell(fmt(scale(state.p_Pa,1e-6)))}${cell(`${fmt(range.valid_temperature_min_K)}–${fmt(range.valid_temperature_max_K)}`)}${cell(rangeLabel(status))}${cell(finite(range.outside_mass_fraction) ? `${fmt(range.outside_mass_fraction * 100)} %` : "—")}</tr>`;
  }));
}

function speciesDetails(states) {
  const rows=states.flatMap(state=>list(state.thermo_range?.species_outside).map(value=>{
    const species=object(value);
    return `<tr>${cell(state.label || state.id || "未命名节点")}${cell(species.species || species.name || "未记录")}${cell(fmt(scale(species.mass_fraction,100)))}${cell(fmt(species.min_temperature_K))}${cell(fmt(species.max_temperature_K))}</tr>`;
  }));
  if (!rows.length) return "";
  return `<details class="ta-details"><summary>查看外推物种、质量分数与多项式温区（${rows.length} 项）</summary>${table(["节点","物种","质量分数 / %","下限 / K","上限 / K"],rows)}</details>`;
}

function modelCard(title, model, fallback) {
  const item=object(model);
  return `<article><h3>${escape(title)}</h3><b>${escape(item.label || item.id || fallback)}</b><p>${escape(item.description || "本记录未保存说明。")}</p></article>`;
}

function dataEvidence(evidence) {
  const data=object(evidence.thermochemical_data);
  if(!data.id)return `<p class="ta-notice">本记录未单独保存热化学数据指纹；按历史记录解释，不以当前数据替换。</p>`;
  return `<details class="ta-details"><summary>本次气相热化学数据：${escape(data.id)}</summary><p>${escape(data.description)}</p><dl><div><dt>数据与计算库</dt><dd>${escape(data.label)} · Cantera ${escape(data.cantera_version)}</dd></div><div><dt>全部 ${escape(data.species_count)} 个物种共同声明温区</dt><dd>${fmt(data.common_min_temperature_K)}–${fmt(data.common_max_temperature_K)} K</dd></div><div><dt>数据 SHA-256</dt><dd>${escape(data.sha256)}</dd></div></dl><p>高温数据扩展不改变理想气体状态方程；共同温区也不是高压精度保证。</p></details>`;
}

export function renderThermodynamicApplicability(result = {}) {
  const state=thermodynamicDisplayState(result), evidence=state.evidence;
  const header=`<div class="ta-heading"><div><span class="ta-kicker">THERMODYNAMIC APPLICABILITY</span><h2>热力学适用性与模型假设</h2><p>温区、状态方程与热化学假设分别核实；数值收敛和守恒闭合不能替代适用性验证。</p></div>${badge(state.status,state.label)}</div>`;
  if (!state.available) return `<section class="panel thermo-applicability" aria-label="热力学适用性与模型假设">${header}<p class="ta-notice">本记录未保存完整的热力学适用性证据。重新计算后可查看逐节点温区、焓基准和燃气模型；历史记录不会被自动判为有效。</p></section>`;
  const states=list(evidence.states).map(object), reference=object(evidence.enthalpy_reference), combustion=object(evidence.combustion);
  const extrapolated=states.filter(item=>thermoRangeDisplayState(item) === "extrapolated");
  const notes=list(evidence.notes).filter(item=>typeof item === "string");
  const heat=object(evidence.cooling).total_heat_transfer_W ?? combustion.cooling_heat_transfer_W;
  const heatLoss=finite(combustion.loss_W) ? `<p class="ta-loss">本次附加燃烧损失：<b>${fmt(combustion.loss_W * 1e-6)} MW</b>${finite(heat) ? `；再生冷却吸热：<b>${fmt(heat * 1e-6)} MW</b>（内部传热，分开记账）` : ""}。</p>` : "";
  return `<section class="panel thermo-applicability" aria-label="热力学适用性与模型假设">${header}<p class="ta-notice">${extrapolated.length ? `${extrapolated.length} 个记录节点发生热力学多项式温区外推。保留求解温度，不将温度截断到数据上限。` : states.length ? "节点未报告温区外推，不代表高压状态方程、物种集合或实际发动机性能已经验证。" : "本记录缺少燃气温区证据。"} 外推物种占比用于定位数据覆盖范围，不是性能误差估计。</p>${dataEvidence(evidence)}${stateTable(states)}${speciesDetails(states)}${pureFluidEvidence(evidence)}<div class="ta-models">${modelCard("燃气状态方程",evidence.gas_model,"未记录")}${modelCard("主喷管热化学假设",evidence.nozzle,"未记录")}${modelCard("主室燃烧损失",combustion,"未记录")}${evidence.cooling ? modelCard("再生冷却模型",evidence.cooling,"未记录") : ""}</div>${heatLoss}<details class="ta-details ta-reference"><summary>化学焓与流体焓的参考态对齐</summary><p>${escape(reference.description || "本记录未说明焓参考态。")}</p><dl><div><dt>参考态</dt><dd>${fmt(reference.temperature_K)} K · ${fmt(reference.pressure_Pa)} Pa</dd></div><div><dt>对齐关系</dt><dd>${escape(reference.formula || "未记录")}</dd></div><div><dt>基准标识</dt><dd>${escape(reference.id || "未记录")}</dd></div></dl><p>主室温度、比热比及摩尔质量的初猜属于数值初值，与此焓参考态不同。更换物性后端时应重新核对参考态，不能直接比较不同零点的绝对焓。</p></details>${notes.length ? `<ul class="ta-notes">${notes.map(note=>`<li>${escape(note)}</li>`).join("")}</ul>` : ""}<p class="ta-next">独立热化学对照见左侧「热力学校核」：须同时核对入口焓、物种集合和喷管假设。CEA 对照的适用范围及结果独立记录。</p></section>`;
}
