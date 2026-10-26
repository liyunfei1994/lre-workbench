// Flow-state presentation only. Quantities are normalized by flow-model.js.
// This view never evaluates properties, joins unconnected paths, or modifies solver records.
const escape = value => String(value ?? "").replace(/[&<>"']/g, char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const fmt = value => {
  if (value == null || (typeof value === "number" && !finite(value))) return "—";
  if (finite(value)) return value!==0 && (Math.abs(value)<.001 || Math.abs(value)>=1e7)
    ? value.toExponential(3) : value.toLocaleString("zh-CN",{maximumFractionDigits:3});
  if (typeof value === "object") { try { return JSON.stringify(value); } catch { return "—"; } }
  return String(value);
};
const exact = value => finite(value) ? String(value) : "未提供";
const branch = value => ["ox","fuel","gas","chamber"].includes(String(value)) ? String(value) : "other";
const COMPONENTS = {ox_pump:"液氧泵",fuel_pump:"燃料泵",cooling_channel:"再生冷却",ox_preburner:"富氧预燃室",fuel_preburner:"富燃预燃室",gas_generator:"燃气发生器",main_chamber:"主燃烧室",injector:"喷注器",nozzle:"主喷管",ox_preburner_turbine:"富氧涡轮",fuel_preburner_turbine:"富燃涡轮",gg_turbine:"GG 涡轮",gg_turbine_ox:"液氧泵涡轮",gg_turbine_fuel:"燃料泵涡轮",gg_exhaust_nozzle:"GG 排气喷管"};
const QUANTITIES = [
  {key:"pressure",label:"压力",symbol:"p",unit:"MPa"},
  {key:"temperature",label:"温度",symbol:"T",unit:"K"},
  {key:"massFlow",label:"质量流量",symbol:"ṁ",unit:"kg/s"},
  {key:"density",label:"密度",symbol:"ρ",unit:"kg/m³"},
  {key:"enthalpy",label:"比焓",symbol:"h",unit:"kJ/kg"},
];

function prepare(model) {
  const seen=new Set();
  const nodes=(Array.isArray(model?.nodes) ? model.nodes : []).filter(node=>node && node.id!=null).filter(node=>{
    const id=String(node.id); if (seen.has(id)) return false; seen.add(id); return true;
  }).map(node=>({...node,id:String(node.id),code:String(node.code || node.id),title:String(node.title || node.id),group:String(node.group || "other"),
    metadata:Array.isArray(node.metadata) ? node.metadata.filter(Boolean) : [],
    componentIds:Array.isArray(node.componentIds) ? node.componentIds.map(String) : [],
  }));
  const groups=(Array.isArray(model?.groups) ? model.groups : []).filter(group=>group && group.id!=null).map(group=>({
    id:String(group.id),title:String(group.title || group.id),caption:String(group.caption || ""),
    segments:(Array.isArray(group.segments) ? group.segments : []).filter(Array.isArray).map(segment=>segment.map(String)),
  })).filter(group=>nodes.some(node=>node.group===group.id));
  for (const node of nodes) if (!groups.some(group=>group.id===node.group)) groups.push({id:node.group,title:"其他记录节点",caption:"按当前计算记录列出；未定义连续流路。",color:"#778596",segments:[]});
  return {nodes,groups};
}

function nodeButton(node, group, first, extra="") {
  return `<button type="button" class="sa-node${first ? " is-selected" : ""}" data-sa-node="${escape(node.id)}" data-sa-kind="${escape(node.group)}" aria-pressed="${first}" title="${escape(node.title)}；压力 ${exact(node.pressure)} MPa；温度 ${exact(node.temperature)} K"><span class="sa-node-code">${escape(node.code)}</span><span class="sa-node-name">${escape(node.title)}</span><span class="sa-node-readout"><b>${escape(fmt(node.pressure))}</b><small>MPa</small><i>·</i><b>${escape(fmt(node.temperature))}</b><small>K</small></span>${extra}</button>`;
}

function route(group,nodes,firstId) {
  const byId=new Map(nodes.map(node=>[node.id,node]));
  const mentioned=new Set(group.segments.flat());
  const segments=[...group.segments,...nodes.filter(node=>node.group===group.id && !mentioned.has(node.id)).map(node=>[node.id])];
  return `<section class="sa-route" data-sa-route="${escape(group.id)}" data-sa-color="${branch(group.id)}"><div class="sa-route-heading"><h3><span class="sa-branch-dot"></span>${escape(group.title)}</h3><span>${escape(group.caption)}</span></div><div class="sa-route-segments">${segments.map((segment,index)=>{
    // A missing record is a visible break. Never join the nodes on either side of it.
    return `<div class="sa-route-segment" aria-label="${escape(group.title)}第 ${index+1} 段">${segment.map((id,i)=>{
      const node=byId.get(id); if (!node) return '<span class="sa-route-gap" title="该节点未返回状态">···</span>';
      const connected=i>0 && byId.has(segment[i-1]);
      return `${connected ? '<span class="sa-route-arrow" aria-hidden="true">→</span>' : ""}${nodeButton(node,group,node.id===firstId)}`;
    }).join("")}</div>`;
  }).join("")}</div></section>`;
}

/** Separate paths and missing values always yield separate line fragments. */
export function flowChartData(nodes,group,key) {
  const byId=new Map(nodes.map(node=>[node.id,node]));
  const ids=[...new Set([...group.segments.flat().filter(id=>byId.has(id)),...nodes.filter(node=>node.group===group.id).map(node=>node.id)])];
  const fragments=[];
  for (const segment of group.segments) {
    let fragment=[];
    for (const id of segment) {
      const node=byId.get(id);
      if (node && finite(node[key])) fragment.push(id);
      else { if (fragment.length) fragments.push(fragment); fragment=[]; }
    }
    if (fragment.length) fragments.push(fragment);
  }
  return {ids,fragments,points:ids.map(id=>byId.get(id)).filter(node=>finite(node[key]))};
}

function chart(nodes,group,quantity,firstId) {
  const data=flowChartData(nodes,group,quantity.key), byId=new Map(nodes.map(node=>[node.id,node]));
  const values=data.points.map(node=>node[quantity.key]);
  if (!values.length) return `<figure class="sa-chart-panel"><figcaption><h3>${quantity.label}<span>${quantity.symbol}</span></h3><b>${quantity.unit}</b></figcaption><div class="sa-chart-empty">本流路尚未返回${quantity.label}数据</div></figure>`;
  const divisor=Math.max(...values.map(Math.abs)) || 1;
  const normalized=values.map(value=>value/divisor);
  let low=quantity.key==="pressure" ? Math.min(0,...normalized) : Math.min(...normalized), high=Math.max(...normalized);
  if (low===high) { low=Math.min(0,low); high=high===0 ? 1 : high; if (low===high) high=0; }
  const x=index=>62+(data.ids.length<2 ? 220 : index*440/(data.ids.length-1));
  const y=value=>164-130*((value/divisor-low)/(high-low));
  const ticks=Array.from({length:5},(_,i)=>{
    const normalizedValue=low+(high-low)*i/4;
    const tickValue=normalizedValue*divisor;
    return `<g><line class="sa-gridline" x1="62" x2="502" y1="${164-130*i/4}" y2="${164-130*i/4}"/><text class="sa-y-label" x="51" y="${168-130*i/4}" text-anchor="end">${escape(fmt(tickValue))}</text></g>`;
  }).join("");
  const paths=data.fragments.filter(segment=>segment.length>1).map(segment=>`<polyline class="sa-plot-line" fill="none" points="${segment.map(id=>`${x(data.ids.indexOf(id))},${y(byId.get(id)[quantity.key])}`).join(" ")}"/>`).join("");
  const marks=data.points.map(node=>`<g class="sa-plot-node${node.id===firstId ? " is-selected" : ""}" data-sa-node="${escape(node.id)}" data-sa-kind="${escape(node.group)}" role="button" tabindex="0" aria-label="${escape(node.code)} ${escape(node.title)}，${quantity.label} ${escape(exact(node[quantity.key]))} ${quantity.unit}" aria-pressed="${node.id===firstId}"><title>${escape(node.title)} · ${quantity.symbol} = ${escape(exact(node[quantity.key]))} ${quantity.unit}</title><circle class="sa-point-halo" cx="${x(data.ids.indexOf(node.id))}" cy="${y(node[quantity.key])}" r="10"/><circle class="sa-point" cx="${x(data.ids.indexOf(node.id))}" cy="${y(node[quantity.key])}" r="4"/></g>`).join("");
  const labels=data.ids.map((id,index)=>`<text class="sa-x-label" x="${x(index)}" y="187" text-anchor="middle">${escape(byId.get(id).code)}</text>`).join("");
  return `<figure class="sa-chart-panel"><figcaption><h3>${quantity.label}<span>${quantity.symbol}</span></h3><b>${quantity.unit}</b></figcaption><svg class="sa-chart" viewBox="0 0 536 205" role="group" aria-label="${escape(group.title)}${quantity.label}节点对比。横轴为离散节点编号。"><title>${escape(group.title)} · ${quantity.label}节点对比</title>${ticks}${paths}${marks}${labels}</svg><p class="sa-chart-foot">${quantity.key==="temperature" ? "纵轴按本流路温度范围显示" : "统一压力刻度"} · 节点间连线仅表示已定义的流路顺序</p></figure>`;
}

function composition(node) {
  const pairs=node.composition && typeof node.composition==="object" && !Array.isArray(node.composition) ? Object.entries(node.composition) : [];
  if (!pairs.length) return '<p class="sa-empty-note">本记录未返回组分质量分数。</p>';
  const valid=pairs.filter(([,value])=>finite(value));
  const sorted=[...pairs].sort((a,b)=>(finite(b[1]) ? b[1] : -Infinity)-(finite(a[1]) ? a[1] : -Infinity));
  const major=sorted.filter(([,value])=>!finite(value) || value>=.001 || value<0);
  const trace=sorted.filter(([,value])=>finite(value) && value>=0 && value<.001);
  const sum=valid.length ? valid.reduce((total,[,value])=>total+value,0) : null;
  const rows=list=>list.map(([name,value])=>{
    const width=finite(value) && value>=0 && value<=1 ? value*100 : 0;
    return `<div class="sa-species" title="${escape(name)}：原始质量分数 ${escape(exact(value))}"><span class="sa-species-name">${escape(name)}</span><svg class="sa-species-track" viewBox="0 0 100 5" preserveAspectRatio="none" aria-hidden="true"><rect class="sa-species-background" width="100" height="5"/><rect class="sa-species-fill" width="${width}" height="5"/></svg><b>${escape(fmt(value))}</b></div>`;
  }).join("");
  return `<div class="sa-composition-heading"><span>组分</span><span>质量分数 Yᵢ</span></div>${rows(major)}${trace.length ? `<details class="sa-trace"><summary>痕量组分 · ${trace.length} 项 <small>Yᵢ &lt; 0.001</small></summary>${rows(trace)}</details>` : ""}<div class="sa-composition-total"><span>已报告质量分数之和</span><b title="${escape(exact(sum))}">${escape(fmt(sum))}</b></div><p class="sa-empty-note">按原始质量分数展示，不自动归一化。条长以 0–1 为标尺。</p>`;
}

function inspector(node,group,first) {
  return `<article class="sa-inspector" data-sa-panel="${escape(node.id)}" data-sa-kind="${escape(node.group)}" data-sa-title="${escape(node.title)}" data-sa-code="${escape(node.code)}" data-sa-color="${branch(group.id)}"${first ? "" : " hidden"}><div class="sa-inspector-title"><span class="sa-detail-code">${escape(node.code)}</span><div><p>${escape(group.title)} / 节点详情</p><h3>${escape(node.title)}</h3></div></div><div class="sa-state-quantities">${QUANTITIES.map(quantity=>`<div class="sa-state-quantity${["pressure","temperature"].includes(quantity.key) ? " sa-primary-quantity" : ""}"><span>${quantity.label}<small>${quantity.symbol}</small></span><strong title="${escape(exact(node[quantity.key]))} ${quantity.unit}">${escape(fmt(node[quantity.key]))}<small>${quantity.unit}</small></strong></div>`).join("")}</div><section class="sa-inspector-section"><div class="sa-section-title"><h4>流体组成</h4><span>MASS FRACTION</span></div>${composition(node)}</section>${node.metadata.length ? `<section class="sa-inspector-section"><div class="sa-section-title"><h4>状态说明与数据来源</h4><span>PROVENANCE</span></div><dl class="sa-metadata">${node.metadata.map(field=>`<div><dt title="${escape(field.key)}">${escape(field.label || field.key)}</dt><dd>${escape(fmt(field.value))}${field.unit && field.unit!=="—" && field.unit!=="-" ? ` <small>${escape(field.unit)}</small>` : ""}</dd></div>`).join("")}</dl></section>` : ""}<div class="sa-node-links"><button type="button" data-sa-topology="${escape(node.id)}">查看循环原理图 <span>↗</span></button>${node.componentIds.map(id=>`<button type="button" data-sa-component="${escape(id)}">${escape(COMPONENTS[id] || id)} · 性能分析 <span>→</span></button>`).join("")}</div></article>`;
}

export function renderFlowWorkspace(model) {
  const {nodes,groups}=prepare(model);
  if (!nodes.length) return '<section class="sa-workspace sa-workspace-empty" aria-label="流路状态分析"><span aria-hidden="true">◎</span><h2>暂无流路状态数据</h2><p>选择已完成的设计点计算记录后，可在此检查各位置的压力、温度、流量与组成。</p></section>';
  const initialGroup=groups[0],first=nodes.find(node=>node.group===initialGroup.id) || nodes[0];
  const groupById=new Map(groups.map(group=>[group.id,group]));
  return `<section class="sa-workspace" aria-label="流路状态分析"><header class="sa-workspace-header"><div><span class="sa-overline">FLOW STATE EXPLORER</span><h2>沿流路检查每一个状态</h2><p>同一稳态设计点 · 位置上的流体状态与流量</p></div><div class="sa-workspace-count"><strong>${nodes.length}</strong><span>记录节点<small>${groups.length} 组流路</small></span></div></header><div class="sa-tabs" role="group" aria-label="流路分类">${groups.map(group=>`<button type="button" class="sa-tab${group===initialGroup ? " is-selected" : ""}" data-sa-color="${branch(group.id)}" data-sa-group="${escape(group.id)}" aria-pressed="${group===initialGroup}"><span class="sa-branch-dot"></span>${escape(group.title)}<b>${nodes.filter(node=>node.group===group.id).length}</b></button>`).join("")}<button type="button" class="sa-tab sa-tab-all" data-sa-group="all" aria-pressed="false">全部节点<b>${nodes.length}</b></button></div><div class="sa-route-container">${groups.map(group=>route(group,nodes,first.id)).join("")}</div><div class="sa-chart-context"><span>流路状态分布</span><b data-sa-chart-caption>${escape(initialGroup.title)}</b><small>横轴为离散节点，间距不代表空间距离或时间</small></div>${groups.map(group=>`<div class="sa-chart-pair" data-sa-charts="${escape(group.id)}" data-sa-color="${branch(group.id)}"${group!==initialGroup ? " hidden" : ""}>${chart(nodes,group,QUANTITIES[0],first.id)}${chart(nodes,group,QUANTITIES[1],first.id)}</div>`).join("")}<div class="sa-analysis-body"><section class="sa-ledger"><div class="sa-ledger-heading"><div><h3>节点数据表</h3><p>选择一行，在右侧检查该位置的状态与组成</p></div><span><b data-sa-visible-count>${nodes.filter(node=>node.group===initialGroup.id).length}</b> 个节点</span></div><div class="sa-table-wrap"><table class="sa-table"><thead><tr><th scope="col">节点 / 位置</th>${QUANTITIES.map(quantity=>`<th scope="col">${quantity.label}<small>${quantity.unit}</small></th>`).join("")}</tr></thead><tbody>${nodes.map(node=>`<tr data-sa-row="${escape(node.id)}" data-sa-kind="${escape(node.group)}" class="${node===first ? "is-selected" : ""}"><th scope="row"><button type="button" data-sa-node="${escape(node.id)}" data-sa-kind="${escape(node.group)}" aria-pressed="${node===first}"><span class="sa-table-code" data-sa-color="${branch(node.group)}">${escape(node.code)}</span><span>${escape(node.title)}</span></button></th>${QUANTITIES.map(quantity=>`<td title="${escape(exact(node[quantity.key]))} ${quantity.unit}">${escape(fmt(node[quantity.key]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="sa-ledger-note">— 表示未返回数据。比焓取决于参考基准；不同工质与物性模型的原始比焓不直接比较。</p><div class="sa-reading-guide"><span>状态与性能的区别</span><p>这里查看某一位置的 <b>p、T、ṁ、ρ、h 与组成</b>；部件性能分析查看跨部件的 <b>压升、功率、效率与换热量</b>。</p></div></section><aside class="sa-inspector-container" aria-label="所选节点详情">${nodes.map(node=>inspector(node,groupById.get(node.group),node===first)).join("")}</aside></div><footer class="sa-statusline"><span><i></i>当前节点 <b data-sa-selection>${escape(first.code)} · ${escape(first.title)}</b></span><span>来自当前计算记录 · 不进行插值或物性补算</span></footer></section>`;
}

const mounted=new WeakMap();
export function mountFlowWorkspace(host,recordId,{onComponent,onTopology}={}) {
  if (!host?.querySelector || !host.dataset) return;
  const previous=mounted.get(host);
  if (previous) { host.removeEventListener("click",previous.click); host.removeEventListener("keydown",previous.keydown); }
  mounted.delete(host);
  const workspace=host.querySelector(".sa-workspace");
  if (!workspace) return;
  const panels=[...workspace.querySelectorAll("[data-sa-panel]")],tabs=[...workspace.querySelectorAll("[data-sa-group]")];
  const nodes=[...workspace.querySelectorAll("[data-sa-node]")],rows=[...workspace.querySelectorAll("[data-sa-row]")];
  const routes=[...workspace.querySelectorAll("[data-sa-route]")],charts=[...workspace.querySelectorAll("[data-sa-charts]")];
  if (!panels.length) return;
  const currentRecord=String(recordId ?? ""),changed=host.dataset.saRecordId!==currentRecord;
  host.dataset.saRecordId=currentRecord;
  let group=changed ? tabs[0]?.dataset.saGroup : host.dataset.saGroup;
  let selected=changed ? undefined : host.dataset.saSelected;
  function update() {
    if (!tabs.some(tab=>tab.dataset.saGroup===group)) group=tabs[0]?.dataset.saGroup;
    const visible=panels.filter(panel=>group==="all" || panel.dataset.saKind===group);
    if (!visible.some(panel=>panel.dataset.saPanel===selected)) selected=visible[0]?.dataset.saPanel;
    const panel=panels.find(item=>item.dataset.saPanel===selected);
    const activeGroup=panel?.dataset.saKind || group;
    host.dataset.saGroup=group; host.dataset.saSelected=selected || "";
    tabs.forEach(tab=>{ const active=tab.dataset.saGroup===group; tab.classList.toggle("is-selected",active); tab.setAttribute("aria-pressed",String(active)); });
    nodes.forEach(node=>{ const active=node.dataset.saNode===selected; node.classList.toggle("is-selected",active); node.setAttribute("aria-pressed",String(active)); });
    panels.forEach(item=>{ item.hidden=item!==panel; });
    routes.forEach(route=>{ route.hidden=group!=="all" && route.dataset.saRoute!==group; });
    charts.forEach(chart=>{ chart.hidden=chart.dataset.saCharts!==activeGroup; });
    rows.forEach(row=>{ row.hidden=group!=="all" && row.dataset.saKind!==group; row.classList.toggle("is-selected",row.dataset.saRow===selected); });
    const count=workspace.querySelector("[data-sa-visible-count]"),selection=workspace.querySelector("[data-sa-selection]"),caption=workspace.querySelector("[data-sa-chart-caption]");
    if (count) count.textContent=String(visible.length);
    if (selection) selection.textContent=panel ? `${panel.dataset.saCode} · ${panel.dataset.saTitle}` : "—";
    if (caption) caption.textContent=routes.find(route=>route.dataset.saRoute===activeGroup)?.querySelector("h3")?.textContent || "当前流路";
  }
  update();
  const activate=event=>{
    const target=event.target?.closest?.("[data-sa-group], [data-sa-node], [data-sa-row], [data-sa-component], [data-sa-topology]");
    if (!target || !workspace.contains(target) || target.disabled) return;
    if (target.dataset.saComponent!=null) { onComponent?.(target.dataset.saComponent); return; }
    if (target.dataset.saTopology!=null) { onTopology?.(target.dataset.saTopology || selected); return; }
    if (target.dataset.saGroup!=null) group=target.dataset.saGroup;
    else { selected=target.dataset.saNode ?? target.dataset.saRow; if (group!=="all") group=target.dataset.saKind; }
    update();
  };
  const keydown=event=>{
    // Native buttons retain their own keyboard behavior; SVG node markers need an explicit activation.
    if (!["Enter"," "].includes(event.key)) return;
    const target=event.target?.closest?.('[role="button"][data-sa-node]');
    if (target && workspace.contains(target)) { event.preventDefault(); activate(event); }
  };
  host.addEventListener("click",activate); host.addEventListener("keydown",keydown);
  mounted.set(host,{click:activate,keydown});
}
