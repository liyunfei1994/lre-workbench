// Pure presentation of sealed saved valve calculations; never infer properties.
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
const list = value => Array.isArray(value) ? value : [];
const fmt = (value, scale=1) => typeof value === "number" && Number.isFinite(value) ? Number((value*scale).toPrecision(6)).toString() : "—";
const hash = value => value ? `<code title="${esc(value)}">${esc(String(value).slice(0, 12))}…</code>` : "未保存";
const createdTime = value => {
  const date = new Date(typeof value === "number" && Number.isFinite(value) ? value * 1000 : NaN);
  return Number.isFinite(date.getTime()) ? date.toLocaleString("zh-CN", {hour12:false}) : "时间未保存";
};
const labels = {MFV:"主燃料阀", OPFV:"富氧预燃室燃料阀", FPOV:"富燃预燃室氧化剂阀"};
const states = {available:"需求已计算", feasible:"候选可行", infeasible:"候选不可行", unavailable:"超出模型或缺少物性"};
const reasons = {
  requirement_only:"有效面积需求，尚未确定真实硬件", within_travel:"开度在允许行程内", at_travel_limit:"已达到行程限位",
  above_maximum_flow:"所需流量超过允许最大开度能力", below_minimum_flow:"所需流量低于允许最小开度能力",
  no_admissible_opening:"没有可行开度", nonpositive_pressure_drop:"没有正向可用压差",
  pressure_drop_ratio_exceeds_model_domain:"压差比超出候选模型适用域", inlet_properties_unavailable:"入口物性不可用",
  not_single_dense_phase:"入口不是支持的单相稠密流体", density_below_dense_candidate_domain:"入口密度低于稠密流体适用范围",
  compressibility_exceeds_model_domain:"压缩性筛查超限",
  outlet_properties_unavailable:"等焓出口物性不可用", outlet_not_single_dense_phase:"出口不是支持的单相稠密流体",
  outlet_density_below_dense_candidate_domain:"出口密度低于稠密流体适用范围", outlet_at_or_below_vapor_pressure:"出口达到或低于饱和蒸汽压力",
  outlet_density_change_exceeds_model_domain:"等焓节流密度变化超出近似适用域",
  below_minimum_design_pressure_drop:"实际压差低于声明的最小设计需求"
};
const sourceText = source => `${esc(source?.project_id || "未保存方案")} · 修订 ${esc(source?.revision ?? "—")} · 计算 ${esc(source?.source_design_job_id || "—")}`;
const sourceDetails = source => `<details><summary>本次计算来源与版本</summary><p>${sourceText(source)}</p><dl class="fs-metadata"><dt>输入指纹</dt><dd>${hash(source?.input_hash)}</dd><dt>拓扑指纹</dt><dd>${hash(source?.topology_hash)}</dd>${Object.entries(source?.characteristic_hash || {}).map(([id, value]) => `<dt>${esc(id)} 特性指纹</dt><dd>${hash(value)}</dd>`).join("")}</dl></details>`;

function renderValveActuators(rows) {
  return `<details><summary>执行器初选 · 行程与阶跃响应</summary><p class="fine">按保存的时间常数和速度限值，估算从行程下限到上限及反向指令的 90% 响应时间。复用共享执行器内核；没有耦合阀芯受力、流体瞬态或整机控制。</p><div class="fs-table-wrap"><table class="fs-table"><thead><tr><th>阀门</th><th>允许行程 %</th><th>时间常数 s</th><th>速度限值 行程/s</th><th>上升 t90 s</th><th>下降 t90 s</th></tr></thead><tbody>${rows.map(row => {
    const definition=row.actuator_definition||{}, preview=row.actuator_preview||{};
    return `<tr><th scope="row">${esc(row.id)}</th><td>${fmt(definition.opening_min,100)}–${fmt(definition.opening_max,100)}</td><td>${fmt(definition.actuator_tau_s)}</td><td>${fmt(definition.actuator_rate_per_s)}</td><td>${fmt(preview.rise_t90_s)}</td><td>${fmt(preview.fall_t90_s)}</td></tr>`;
  }).join("")}</tbody></table></div><p class="fine">完整阶跃样本随多工况 JSON 包保留，各样本绑定原始方案、设计任务及特性指纹。该估计不代表闭环控制带宽或发动机响应时间。</p></details>`;
}

export function renderValveDesign(report) {
  if (!report || report.status === "missing") return '<section class="fs-card fs-valve-design"><h3>实体阀门 · 保存设计点</h3><p class="fine">这条历史记录未保存实体阀结果。概念设计中定义三阀后，新建计算可记录阀门入口状态、面积需求及候选开度。</p></section>';
  if (report.scope !== "saved_valve_design") return '<p class="fs-error">实体阀报告类型无法识别。</p>';
  const rows = list(report.rows);
  const sizing = report.configuration?.mode === "requirement_sizing";
  const stateText = report.status === "conflict" ? "证据冲突，不能汇总或交接" : report.status !== "recorded" ? "证据不完整，不能汇总或交接" : report.calculation_status === "pass" ? (sizing ? "三阀需求可计算" : "三阀候选工况可行") : "本设计点包含不可行或不支持的阀门工况";
  const hydraulic = rows.map(row => {
    const r = row.result || {}, c = row.configuration || {}, inlet = r.inlet_state || {};
    return `<tr><th scope="row">${esc(row.id)}<small>${esc(labels[row.id] || "")}</small></th><td>${esc(c.fluid || "—")}<small>${esc(c.upstream_node || "—")} → ${esc(c.downstream_node || "—")}</small></td><td>${fmt(r.mass_flow_kg_s)}</td><td>${fmt(r.upstream_pressure_Pa, 1e-6)}</td><td>${fmt(r.downstream_pressure_Pa, 1e-6)}</td><td>${fmt(r.upstream_temperature_K)}</td><td>${fmt(inlet.density_kg_m3)}</td><td>${fmt(r.pressure_drop_Pa, 1e-6)}</td></tr>`;
  }).join("");
  const capacities = rows.map(row => {
    const r = row.result || {}, c = row.configuration || {};
    return `<tr><th scope="row">${esc(row.id)}</th><td>${fmt(r.required_cda_m2, 1e6)}</td><td>${fmt(r.required_rated_cda_m2, 1e6)}</td><td>${fmt(c.rated_cda_m2, 1e6)}</td><td>${fmt(r.opening, 100)}</td><td>${fmt(r.opening_margin_lower, 100)} / ${fmt(r.opening_margin_upper, 100)}</td><td>${fmt(r.flow_margin_kg_s)}</td><td>${esc(states[r.status] || "未保存")}<small>${r.saturated === true ? "已达限位或超能力；" : ""}${esc(reasons[r.reason] || r.reason || "")}</small></td></tr>`;
  }).join("");
  const content = `<section class="fs-card fs-valve-design" aria-label="实体阀门保存结果"><h3>实体阀门 · ${sizing ? "工况面积需求" : "候选开度校核"}</h3><p class="${report.calculation_status === "pass" && report.status === "recorded" ? "fs-notice" : "fs-error"}">${stateText}</p><p class="fine">${sourceText(report.source)}。所有入口量来自相同安装端口和本次保存结果。</p><div class="fs-table-wrap"><table class="fs-table"><thead><tr><th>阀门</th><th>介质与安装位置</th><th>流量 kg/s</th><th>入口 MPa</th><th>出口 MPa</th><th>入口 K</th><th>入口密度 kg/m³</th><th>阀压差 MPa</th></tr></thead><tbody>${hydraulic}</tbody></table></div><div class="fs-table-wrap"><table class="fs-table"><thead><tr><th>阀门</th><th>工况 CdA mm²</th><th>参考开度所需额定 CdA mm²</th><th>候选额定 CdA mm²</th><th>开度 %</th><th>下 / 上行程余量 百分点</th><th>全开流量余量 kg/s</th><th>工况状态</th></tr></thead><tbody>${capacities}</tbody></table></div><p class="fine">CdA 是有效面积，不能直接当作实际孔口面积。${sizing ? "需求模式没有已选硬件，因此不显示实际开度或候选能力余量。" : "开度不可行时不将其截断到限位冒充可行解。"}</p>${list(report.issues).length ? `<ul class="fs-warnings">${report.issues.map(issue => `<li>${esc(issue)}</li>`).join("")}</ul>` : ""}${sourceDetails(report.source)}<details><summary>候选模型与适用限制</summary><ul>${list(report.limitations).map(note => `<li>${esc(note)}</li>`).join("")}</ul>${rows.map(row => `<p class="fine">${esc(row.id)}：${esc(row.result?.inlet_state?.backend || "入口物性未保存")}；${esc(row.result?.inlet_state?.phase || "相态未保存")}；特性 ${esc(row.configuration?.characteristic_version || "—")}；${esc(row.result?.detail || "")}</p>`).join("")}</details></section>`;
  return content.replace("</section>", `${renderValveActuators(rows)}</section>`);
}

export function renderValveSelectionEnvelope(report) {
  if (!report) return "";
  if (report.scope !== "saved_valve_selection_envelope") return '<p class="fs-error">阀门多工况包类型无法识别。</p>';
  const display = (quantity, value) => fmt(value, quantity.unit === "Pa" ? 1e-6 : quantity.unit === "m²" ? 1e6 : 1);
  const unit = quantity => quantity.unit === "Pa" ? "MPa" : quantity.unit === "m²" ? "mm²" : quantity.unit;
  const extremum = (quantity, key) => {
    const point = quantity[key];
    return point ? `${display(quantity, point.value)} <small>${esc(unit(quantity))} · ${sourceText(point.source)}</small>` : "未保存";
  };
  return `<section class="fs-card fs-valve-selection"><h3>三阀 · 多工况选型需求包</h3><p class="fine">${list(report.sources).length} 个保存工况；方案 ${esc(report.project_id)}；拓扑 ${hash(report.topology_hash)}。</p><p class="${report.all_points_feasible ? "fs-notice" : "fs-error"}">${report.all_points_feasible ? "所选工况的已保存阀模型检查均通过；仍需部件和硬件验证。" : "含不可行或不支持的工况；本包不能作为能力通过证明。"}</p>${Object.entries(report.valves || {}).map(([id, valve]) => `<h4>${esc(id)} · ${esc(labels[id] || "")}</h4><div class="fs-table-wrap"><table class="fs-table"><thead><tr><th>量</th><th>最小值及驱动工况</th><th>最大值及驱动工况</th><th>数据范围</th></tr></thead><tbody>${Object.values(valve.ranges || {}).map(quantity => `<tr><th scope="row">${esc(quantity.label)}</th><td>${extremum(quantity, "min")}</td><td>${extremum(quantity, "max")}</td><td>${quantity.status === "complete" ? "所选工况完整" : quantity.status === "partial" ? `缺 ${list(quantity.missing_sources).length} 个工况` : "本模式未保存"}</td></tr>`).join("")}</tbody></table></div>`).join("")}<p class="fine">各极值分别保留其完整驱动点和入口状态，不能把不同工况的流量、压差与密度组合成新的设计点。选型包指纹 ${hash(report.package_sha256)}。</p></section>`;
}

export function valveSelectionJobs(jobs, projectId) {
  return list(jobs).filter(job => job && job.project_id === projectId && job.status === "succeeded"
    && (job.mode || "concept_design") === "concept_design" && typeof job.id === "string" && job.id);
}

export function renderValveSelectionPicker(jobs, {selected=[], busy=false, error="", report=null, expanded=false}={}) {
  const ids = new Set(selected);
  return `<details class="concept-reference fs-valve-selection-disclosure" data-valve-selection-details${expanded ? " open" : ""}><summary>多工况选型包</summary><section class="fs-card fs-valve-selection-picker" aria-label="选择阀门设计工况"><h3>选择设计工况</h3><p class="fine">显式选择当前方案的已完成设计记录。只有保存了实体三阀结果，且拓扑及三阀特性相同的工况才能汇总；不同修订可以保留各自来源。</p>${jobs.length ? `<fieldset ${busy ? "disabled" : ""}><legend>保存工况（已选 ${ids.size} 项）</legend><div class="fs-valve-job-list">${jobs.map(job => `<label class="fs-valve-job"><input type="checkbox" data-valve-job value="${esc(job.id)}" ${ids.has(job.id) ? "checked" : ""}> 修订 ${esc(job.revision ?? "—")} · ${esc(createdTime(job.created))} · ${esc(job.id)}</label>`).join("")}</div></fieldset><div class="fs-actions"><button type="button" data-valve-build ${busy || !ids.size ? "disabled" : ""}>${busy ? "正在核对工况…" : "生成选型需求包"}</button><button type="button" data-valve-download ${busy || !report ? "disabled" : ""}>下载选型包 JSON</button></div>` : '<p class="fs-empty">当前方案还没有已完成的概念设计记录。</p>'}${error ? `<p class="fs-error" role="alert">${esc(error)}</p>` : ""}<p class="fine">生成结果不会修改历史记录。保存包包含输入、拓扑、特性指纹及各极值的完整驱动工况。</p></section>${renderValveSelectionEnvelope(report)}</details>`;
}

export function mountValveSelection(container, {api, project, jobs=[], notify=()=>{}}={}) {
  if (!container || typeof api !== "function" || !project?.id) return ()=>{};
  const projectId = project.id, eligible = valveSelectionJobs(jobs, projectId), selected = new Set();
  let busy = false, error = "", report = null, disposed = false, request = 0;
  const render = () => {
    if (disposed) return;
    const expanded = container.querySelector?.("[data-valve-selection-details]")?.open === true;
    container.innerHTML = renderValveSelectionPicker(eligible, {selected:[...selected], busy, error, report, expanded});
  };
  const change = event => {
    const checkbox = event.target.closest?.("[data-valve-job]");
    if (!checkbox || !container.contains(checkbox) || busy) return;
    if (!eligible.some(job => job.id === checkbox.value)) return;
    checkbox.checked ? selected.add(checkbox.value) : selected.delete(checkbox.value);
    // A changed selection cannot download the previous set under a new label.
    report = null; error = ""; render();
  };
  const click = async event => {
    const button = event.target.closest?.("button");
    if (!button || !container.contains(button) || button.disabled || busy || disposed) return;
    if (button.hasAttribute("data-valve-build")) {
      if (!selected.size) return;
      const token = ++request, jobIds = [...selected];
      busy = true; report = null; error = ""; render();
      try {
        const answer = await api(`/projects/${encodeURIComponent(projectId)}/valve-selection-envelope`, "POST", {job_ids:jobIds});
        if (disposed || request !== token) return;
        if (answer?.scope !== "saved_valve_selection_envelope" || answer.project_id !== projectId)
          throw new Error("返回选型包与当前方案或报告类型不一致。");
        const returned = list(answer.sources).map(source => source.source_design_job_id);
        if (returned.length !== jobIds.length || returned.some(id => !selected.has(id)) || new Set(returned).size !== returned.length)
          throw new Error("返回选型包的工况来源与显式选择不一致。");
        report = answer;
      } catch (failure) {
        if (disposed || request !== token) return;
        error = failure?.message || String(failure); notify(error);
      } finally {
        if (!disposed && request === token) { busy = false; render(); }
      }
    } else if (button.hasAttribute("data-valve-download") && report) {
      const blob = new Blob([JSON.stringify(report, null, 2)], {type:"application/json;charset=utf-8"});
      const url = URL.createObjectURL(blob), link = container.ownerDocument.createElement("a");
      link.href = url; link.download = `valve-selection-${String(projectId).replace(/[^\w-]/g, "_")}-${report.package_sha256?.slice(0, 12) || "saved"}.json`;
      container.ownerDocument.body.append(link); link.click(); link.remove();
      setTimeout(()=>URL.revokeObjectURL(url), 30000);
    }
  };
  container.addEventListener("change", change); container.addEventListener("click", click); render();
  return () => { disposed = true; request++; container.removeEventListener("change", change); container.removeEventListener("click", click); };
}
