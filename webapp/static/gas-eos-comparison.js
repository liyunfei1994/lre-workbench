// Saved, source-bound comparisons of two complete FFSC calculations.
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const rows = value => Array.isArray(value) ? value : [];
const finite = value => typeof value === "number" && Number.isFinite(value);
const number = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:7}) : "—";
const statusNames = {complete:"两端计算完成",partial:"部分结果",unavailable:"不可用",computed:"已收敛并闭合",not_converged:"未收敛或未闭合",error:"计算失败",pass:"通过",fail:"未通过",ok:"有结果",not_assessed:"未评估",not_applicable:"未启用分布加热",not_requested:"历史协议未要求"};
const statusValue = value => typeof value === "string" ? value : value?.status ?? value?.pressure_link_status;
const badge = value => {
  const status=statusValue(value), style=["pass","fail","error","unavailable"].includes(status) ? status : "neutral";
  return `<span class="validation-badge validation-${style}">${escape(statusNames[status] || status || "未记录")}</span>`;
};
const message = (text, warning=false) => `<p class="validation-message${warning?" validation-message-warning":""}">${escape(text)}</p>`;
const table = (headers, data) => `<div class="validation-table-wrap"><table><thead><tr>${headers.map(h=>`<th scope="col">${escape(h)}</th>`).join("")}</tr></thead><tbody>${data.length ? data.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join("")}</tr>`).join("") : `<tr><td class="validation-empty" colspan="${headers.length}">暂无该项保存数据</td></tr>`}</tbody></table></div>`;
const noteList = values => rows(values).length ? `<ul class="validation-notes">${rows(values).map(text=>`<li>${escape(typeof text==="string"?text:JSON.stringify(text))}</li>`).join("")}</ul>` : "";
const PR_BM = "Peng-Robinson-Boston-Mathias";
function candidateModel(section) {
  const key=section.model_scope?.candidate_eos || (section.runs?.[PR_BM] ? PR_BM : "Peng-Robinson");
  const full=key===PR_BM;
  return {key,full,label:full?"PR–BM 全燃气方案":"PR 前段方案",short:full?"PR–BM 全燃气":"PR 前段"};
}

function commonHotSideVerified(section,candidate) {
  if(!candidate.full)return true;
  return section.model_scope?.hot_side_thermo_method==="same_eos" && ["ideal-gas",candidate.key].every(key=>{
    const audit=section.runs?.[key]?.closure?.hot_side_method;
    return audit?.requested_method==="same_eos" && ["pass","not_applicable"].includes(audit?.status);
  });
}

function metricTable(metrics, comparable, candidate) {
  return table(["指标","单位","全理想方案",candidate.label,`${candidate.short} − 全理想`,"相对差 / %"], rows(metrics).map(row=>[
    escape(row.label || row.id),escape(row.unit),escape(number(row.ideal)),escape(number(row.pr)),
    escape(number(comparable ? row.difference : null)),escape(number(comparable && finite(row.relative_difference) ? row.relative_difference*100 : null))
  ]));
}

function runCard(key, run={}) {
  const closure=run.closure || {};
  const label=key==="ideal-gas"?"全理想方案":key===PR_BM?"PR–BM 全燃气方案":"PR 前段方案";
  const description=key==="ideal-gas"?"预燃室、涡轮、主室和喷管均采用理想气体。":key===PR_BM?
    "预燃室、涡轮、主室、冷却燃气侧和喷管统一采用 Peng–Robinson–Boston–Mathias。":"预燃室和涡轮采用标准 Peng–Robinson；主室和喷管采用理想气体。";
  return `<section class="eos-run"><h4>${label}</h4><p class="fine">${description}</p>${badge(run.status)}${run.reason?message(run.reason,true):""}`+
    table(["验收项","结果"],[
      ["数值收敛",badge(run.convergence)],
      ["四支路流量与配比",badge(closure.feed_network)],
      ["站点压力链",badge(closure.pressure_contract)],
      ["能量账",badge(closure.energy_balance)],
      ...(closure.hot_side_method?[["共同热侧算法",badge(closure.hot_side_method)]]:[])
    ])+noteList(run.warnings)+`<details class="validation-diagnostics"><summary>本次求解与验收明细</summary><pre>${escape(JSON.stringify({eos:run.eos,status:run.status,operating_point:run.operating_point,convergence:run.convergence,closure:run.closure,model_scope:run.model_scope,station_models:run.station_models},null,2))}</pre></details></section>`;
}

function basisText(basis) {
  if(typeof basis==="string")return basis;
  if(!basis || typeof basis!=="object")return "未记录";
  const names={total:"总态",static:"静态",reservoir_approximation:"储腔近似",unspecified:"总／静态未指定",thermodynamic:"热力学状态"};
  const kind=basis.kind || basis.pressure || basis.enthalpy;
  return names[kind] || kind || "未记录";
}

function stationEos(station, runs, key) {
  const model=runs[key]?.stations?.[station.id]?.model || station.models?.[key] || station.models?.[key==="ideal-gas"?"ideal":"pr"];
  const eos=typeof model==="string"?model:model?.eos;
  return {"ideal-gas":"理想气体", "Peng-Robinson":"Peng–Robinson", [PR_BM]:"Peng–Robinson–Boston–Mathias"}[eos] || eos || "未记录";
}

function stationPanel(station, comparable, runs, candidate) {
  const composition=rows(station.composition);
  return `<details class="validation-data-details eos-station" open><summary>${escape(station.label || station.id)}</summary>`+
    `<p class="fine">站点实际状态方程：全理想方案 ${escape(stationEos(station,runs,"ideal-gas"))}；${candidate.label} ${escape(stationEos(station,runs,candidate.key))}。</p>`+
    `<p class="fine">状态口径：全理想方案 ${escape(basisText(station.basis?.ideal))}；${candidate.label} ${escape(basisText(station.basis?.pr))}。各列取自各自重新匹配的工作点，温度、压力及组成可以不同。</p>`+
    metricTable(station.properties,comparable,candidate)+`<details class="validation-data-details"><summary>全部保存组分 · 质量分数</summary>`+
    table(["物种","全理想方案",candidate.label,"差值 / 质量分数"],composition.map(row=>[
      escape(row.species),escape(number(row.ideal)),escape(number(row.pr)),escape(number(comparable?row.difference:null))
    ]))+`</details></details>`;
}

export function renderGasEosComparison(section, active=false) {
  if(!section)return message(active ? "正在分别求解并核对两套模型，完成后显示对比。" : "尚未运行理想气体 / PR 整机对比。以所选设计记录的输入分别重新求解两套模型，结果保存在独立校核记录中。");
  const candidate=candidateModel(section),runs=section.runs || {}, bothComputed=runs["ideal-gas"]?.status==="computed" && runs[candidate.key]?.status==="computed";
  const commonHotSide=commonHotSideVerified(section,candidate);
  const comparable=section.status==="complete" && bothComputed && commonHotSide;
  const scope=section.model_scope || {}, species=rows(scope.species_names);
  return `<section class="eos-comparison" aria-label="理想气体与 PR 整机匹配对比">`+
    `<div class="validation-result-summary">${badge(section.status)}<b>全理想 / ${candidate.short} · 两套完整整机匹配</b></div>`+
    message("两端使用相同的 16 种 PR 参数支持物种和同一份设计输入，分别重算预燃室化学平衡、冻结组成涡轮膨胀、冷却和整机匹配。"+(candidate.full?
      "PR–BM 全燃气方案在预燃室、涡轮、主室、冷却燃气侧和喷管统一采用 Peng–Robinson–Boston–Mathias，按实际排气焓与组成重新求解。Boston–Mathias 高温 alpha 修正具有独立模型身份。":
      "全理想方案的各燃气段采用理想气体；PR 前段方案仅在预燃室和涡轮采用 Peng–Robinson，主室及喷管仍采用理想气体，并接收 PR 排气的实际焓与组成重新求解。")+"原设计可能采用完整物种集合，不能直接作为这里的理想气体对照值。")+
    (candidate.full?message(commonHotSide?
      "共同热侧算法已核对：两侧统一采用广义冻结组成准一维加热求解、单元中心采样，以及实际总温／静温比 Bartz 修正；未启用分布加热时该项记为不适用。":
      "本报告未完整保存或未通过共同热侧算法核验，不能将差异单独归因于 EOS；保留两端数值，不展示两端差值。",!commonHotSide):"")+
    message("受限物种候选不覆盖缺少参数的自由基及完整高温解离。"+(candidate.full?"PR–BM 全燃气模型与混合规则尚未通过高压实验验证。":"该历史分段候选不覆盖主室/喷管高温真实气体。")+(commonHotSide?"差值表示所声明模型及共同物种集合内的 EOS 敏感性，不是相对实机的误差。":"缺少共同算法证据的历史报告不作为单一 EOS 敏感性结论。"))+
    (section.reason?message(section.reason,true):"")+
    `<div class="eos-run-grid">${runCard("ideal-gas",runs["ideal-gas"])}${runCard(candidate.key,runs[candidate.key])}</div>`+
    (comparable?message(`差值 = ${candidate.label} − 全理想方案；相对差 = 差值 / |全理想方案| × 100%。全理想参考值为零时不计算相对差。`):
      message(bothComputed?"两端分别通过收敛与闭合验收。两端的可比性未通过，不展示两端差值。":"至少一端未完成收敛和独立闭合验收。保留各端数值与失败原因，不展示两端差值。",true))+
    `<h3>整机匹配结果</h3>${metricTable(section.metrics,comparable,candidate)}`+
    `<h3>燃气站点与组分</h3>${rows(section.stations).map(station=>stationPanel(station,comparable,runs,candidate)).join("") || message("未保存可展示的燃气站点数据。")}`+
    `<details class="validation-data-details"><summary>物种范围与来源模型</summary><p class="fine">原设计状态方程：${escape(section.source_model?.eos || "未记录")}；原设计物种策略：${escape(section.source_model?.species_policy || "未记录")}。</p><p class="fine">对比物种策略：${escape(scope.species_policy || "未记录")}；${escape(scope.species_count ?? (species.length || "未记录"))} 种物种。</p><p class="fine">${species.map(escape).join("、") || "本记录未保存物种清单。"}</p></details>`+
    `<details class="validation-diagnostics"><summary>来源范围与模型指纹</summary><pre>${escape(JSON.stringify({source_model:section.source_model,model_scope:scope},null,2))}</pre></details>`+
    noteList(section.limitations)+`</section>`;
}
