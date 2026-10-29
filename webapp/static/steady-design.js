// Read-only presentation of the server's record-bound overall design evidence.
const escape=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const rows=v=>Array.isArray(v)?v:[];
const number=(v,scale=1)=>typeof v==="number"&&Number.isFinite(v)?(v*scale).toLocaleString("zh-CN",{maximumSignificantDigits:6}):"未记录";
const labels={pass:"已核对",fail:"未通过",review:"待确认",unknown:"证据不足",unavailable:"证据不足",recorded:"已保存模型结果",missing:"缺少记录",complete:"交接完整",incomplete:"交接不完整",inconsistent:"存在冲突"};
const badge=s=>`<span class="sd-state sd-${s==="fail"?"fail":s==="pass"?"pass":"review"}">${escape(labels[s]||"待确认")}</span>`;
const jump=(target,text)=>`<button type="button" data-steady-open="${escape(["checks","models","cooling","convergence"].includes(target)?target:"models")}">${escape(text)}</button>`;
const table=(heads,data)=>`<div class="table-wrap"><table><thead><tr>${heads.map(h=>`<th>${escape(h)}</th>`).join("")}</tr></thead><tbody>${data.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
const active=report=>report?.schema_version===1&&!["unavailable","not_applicable"].includes(report.status);

export function renderSupplyBasis(supply){
  if(!supply)return "";
  const phase={liquid:"液体",supercritical_liquid:"液态高压流体",supercritical:"超临界流体"};
  const cells=rows(supply.ports).map(port=>{
    const s=port.screen || {};
    const tsat=s.saturation_temperature_status==="not_defined_at_pressure"?"该压力不定义":number(s.saturation_temperature_K);
    const subcool=s.saturation_temperature_status==="not_defined_at_pressure"?"不适用":number(s.subcooling_K);
    const npsh=s.vapor_pressure_status==="not_defined_at_temperature"?"不适用":number(s.static_npsh_component_m);
    return [escape(port.label),escape(number(port.p_Pa,1e-6)),escape(number(port.T_K)),
      escape(phase[s.phase]||"未保存筛查"),escape(tsat),escape(subcool),escape(npsh)];
  });
  const models=[...new Set(rows(supply.ports).filter(p=>p.screen?.model).map(p=>`${p.screen.model.backend} ${p.screen.model.version} · ${p.screen.model.fluid}`))];
  return `<details class="sd-tasks" aria-label="供给边界与增压功核对"><summary>供给边界、过冷度与增压功</summary><p>${escape(supply.description)}</p>${table(["端口","绝压 / MPa","液温 / K","相态","饱和温度 / K","过冷度 / K","NPSH 静压分量 / m"],cells)}<p>${rows(supply.external_powers).map(p=>`${escape(p.label)}外部供功：${escape(number(p.power_W,1e-3))} kW`).join("；")}</p>${models.length?`<p>保存的物性来源：${escape(models.join("；"))}</p>`:""}<p class="sd-definition">${escape(supply.limits)}</p></details>`;
}

export function renderSteadyDesignSummary(report){
  if(!active(report))return "";
  const gates=rows(report.gates);
  return `<section class="panel sd-summary" aria-label="稳态总体设计依据"><div class="sd-heading"><div><span class="sd-kicker">STEADY DESIGN BASIS</span><h2>${escape(report.scope_label)}</h2></div>${jump("models","查看设计依据 →")}</div><p class="sd-conclusion">${report.status==="blocked"?"存在未通过项，先核对模型内的一致性与适用范围。":"已形成总体计算基线；下列结论分别成立，模型适用性与部件能力仍需核实。"}</p><div class="sd-gates">${gates.map(g=>`<article><div><b>${escape(g.label)}</b>${badge(g.status)}</div><p>${escape(g.detail)}</p>${jump(g.target,"查看依据")}</article>`).join("")}</div><details class="sd-tasks"><summary>本记录需要处理的事项（${rows(report.actions).length} 项）</summary><ul>${rows(report.actions).map(a=>`<li><b>${escape(a.label)}</b><p>${escape(a.detail)}</p>${jump(a.target,"查看")}</li>`).join("")}</ul></details></section>`;
}

export function renderSteadyDesignBasis(report){
  if(!active(report))return "";
  const basis=b=>{const names={total:"总态",static:"静态",chamber_reservoir:"储腔参考态",reservoir:"储腔参考态",thermodynamic:"热力学状态",unspecified:"未定义"};
    return `p：${names[b?.pressure]||"未定义"}；T：${names[b?.temperature]||"未定义"}`;};
  const stations=rows(report.stations).map(s=>[escape(s.label),escape(basis(s.state_basis)),escape(number(s.p_Pa,1e-6)),escape(number(s.T_K)),escape(number(s.mass_flow_kg_s)),escape(number(s.h_Jkg,1e-3)),escape(s.species_count),badge(s.status)]);
  return `<section class="panel sd-basis" aria-label="总体设计框架"><div class="sd-heading"><h2>总体设计依据与部件交接</h2><span>所选计算记录</span></div><p>${escape(report.source)}</p><dl class="sd-models">${rows(report.models).map(m=>`<div><dt>${escape(m.label)}</dt><dd>${escape(m.value)}</dd></div>`).join("")}</dl><p class="sd-definition">${escape(report.property_definitions)}</p>${renderSupplyBasis(report.supply)}<h3>各部件在总体平台中的职责</h3>${table(["系统环节","本记录","总体平台交付","后续专业设计／验证"],rows(report.modules).map(m=>[escape(m.label),badge(m.status),escape(m.delivers),escape(m.pending)]))}<details class="sd-tasks"><summary>查看燃烧与涡轮接口状态（${stations.length} 个端口）</summary><p>本表直接读取同一燃气交接记录。储腔总态近似仍是明确的系统模型约定；未解析速度时不自动换算静态。</p>${stations.length?table(["接口","状态定义","压力 / MPa","温度 / K","流量 / kg/s","化学参考焓 / kJ/kg","保存物种数","交接状态"],stations):"<p>历史记录缺少完整燃气交接；重新计算设计点可保存新报告。</p>"}</details><p class="sd-definition">独立的 Cantera–CEA、混合燃气 EOS 与凝聚相结果保存在“热力学校核 → 燃气交接与筛查”。请选择与本页相同的计算记录。几何与专业设计任务书沿用该记录，筛查候选模型不会自动改写循环。</p></section>`;
}
