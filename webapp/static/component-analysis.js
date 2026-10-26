// Component presentation only. Every value arrives in its displayed unit from results.js.
// The immutable solver outputs are deliberately never read or changed here.
const CATEGORIES = [
  {id:"supply", title:"泵与供给", short:"供给", mark:"P", ids:["ox_pump","fuel_pump"]},
  {id:"shaft", title:"涡轮与轴功", short:"轴功", mark:"T", ids:["ox_preburner_turbine","fuel_preburner_turbine","gg_turbine","gg_turbine_ox","gg_turbine_fuel"]},
  {id:"combustion", title:"燃烧与喷注", short:"燃烧", mark:"C", ids:["ox_preburner","fuel_preburner","gas_generator","main_chamber","injector"]},
  {id:"thermal", title:"冷却与换热", short:"冷却", mark:"Q", ids:["cooling_channel"]},
  {id:"thrust", title:"喷管与推力", short:"推力", mark:"N", ids:["nozzle","gg_exhaust_nozzle"]},
];
const OTHER_CATEGORY = {id:"other",title:"其他部件",short:"其他",mark:"·",ids:[]};
const categoryOf = id => CATEGORIES.find(category=>category.ids.includes(id)) || OTHER_CATEGORY;
const escape = value => String(value ?? "").replace(/[&<>"']/g, char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const format = value => {
  if (value == null || (typeof value === "number" && !finite(value))) return "—";
  if (finite(value)) return value !== 0 && (Math.abs(value)<.001 || Math.abs(value)>=1e8)
    ? value.toExponential(3) : value.toLocaleString("zh-CN",{maximumFractionDigits:3});
  if (typeof value === "string") return value.trim() && !/^[+-]?(?:NaN|Infinity)$/i.test(value.trim()) ? value : "—";
  return typeof value === "boolean" ? String(value) : "—";
};
const unitText = field => field?.unit && field.unit !== "-" ? String(field.unit) : "—";
const quantity = field => field ? `${format(field.value)}${unitText(field)==="—" ? "" : ` ${unitText(field)}`}` : "—";
const getField = (item, keys) => {
  const candidates=keys.map(key=>item.fields.find(field=>field.key===key)).filter(Boolean);
  return candidates.find(field=>finite(field.value)) || candidates[0];
};
const SYMBOLS = {
  p_in_Pa:"pᵢₙ",p_in_MPa:"pᵢₙ",p_in_bar:"pᵢₙ",P_in_Pa:"pᵢₙ",
  p_out_Pa:"pₒᵤₜ",p_out_MPa:"pₒᵤₜ",p_out_bar:"pₒᵤₜ",P_out_Pa:"pₒᵤₜ",
  delta_p_Pa:"Δp",delta_p_MPa:"Δp",delta_P_MPa:"Δp",T_in_K:"Tᵢₙ",T_out_K:"Tₒᵤₜ",T_exhaust_K:"Tₒᵤₜ",
  delta_T_K:"ΔT",delta_T_isentropic_K:"ΔTₛ",delta_T_loss_K:"ΔTₗ",rho_in_kgm3:"ρᵢₙ",rho_out_kgm3:"ρₒᵤₜ",rho_kgm3:"ρ",
  power_W:"P",power_MW:"P",power_hydraulic_W:"Pinc",eta:"ηₚ",head_m:"H",volume_flow_m3s:"Qᵥ",nr_rpm:"n",npsh_available_m:"NPSHₐ",
  specific_work_isentropic_Jkg:"wₛ",turbine_power_W:"Pₜ",turbine_power_MW:"Pₜ",eta_turbine:"ηₜ",mass_flow_kgs:"ṁ",
  turbine_pressure_ratio:"πₜ",pressure_recovery_ratio:"σ",isentropic_drop_Jkg:"Δhₛ",actual_drop_Jkg:"Δh",actual_temp_drop_K:"ΔT",
  shaft_speed_rpm:"n",cp_in_Jkgk:"cₚ,ᵢₙ",gamma_in:"γᵢₙ",molar_mass_in:"Mᵢₙ",t_in_over_limit:"ΔTlim",
  mdot_total_kgs:"ṁ",mdot_fuel_kgs:"ṁf",mdot_ox_kgs:"ṁox",mdot_chamber_kgs:"ṁcc",phi:"φ",mixture_ratio_OF:"O/F",fuel_fraction:"xf",
  T_combustion_K:"Tcomb",T_chamber_K:"Tcc",T_chamber_ideal_K:"Tcc,ideal",combustion_efficiency:"kT",
  h_in_Jkg:"hᵢₙ",h_combustion_Jkg:"hcomb",h_chamber_Jkg:"hcc",cp_Jkgk:"cₚ",gamma:"γ",molar_mass_kg_per_kmol:"M",
  preburner_pressure_Pa:"pPB",preburner_pressure_MPa:"pPB",injector_dp_Pa:"Δpᵢ",injector_dp_MPa:"Δpᵢ",injector_dp_bar:"Δpᵢ",injector_dp_ratio:"Δpᵢ/p",
  dp_ox_Pa:"Δpox",dp_ox_MPa:"Δpox",dp_ox_ratio:"Δpox/p",dp_fuel_Pa:"Δpf",dp_fuel_MPa:"Δpf",dp_fuel_ratio:"Δpf/p",
  heat_load_W:"Q̇",heat_load_MW:"Q̇",cp_avg_J_kg_K:"c̄ₚ",thrust_N:"F",thrust_vac_N:"Fvac",Isp_s:"Isp",Isp_vac_s:"Isp,vac",
  Ve_ms:"Vexit",exit_velocity_ms:"Vexit",c_star_ms:"c*",Cf:"CF",Cf_vac:"CF,vac",Cf_sl:"CF,amb",Cf_momentum:"CF,mom",Cf_pressure:"CF,p",
  mach_exit:"Maexit",exit_mach:"Maexit",p_exit_Pa:"pexit",exit_pressure_Pa:"pexit",p_amb_Pa:"pamb",expansion_ratio:"ε",T_exit_K:"Texit",gamma_exit:"γexit",
};
const symbolFor = (item,field) => item.id==="gas_generator" && ["preburner_pressure_Pa","preburner_pressure_MPa"].includes(field.key)
  ? "pGG" : SYMBOLS[field.key] || "—";
const PRESSURE_IN = ["p_in_MPa","p_in_Pa","P_in_Pa","p_in_bar"];
const PRESSURE_OUT = ["p_out_MPa","p_out_Pa","P_out_Pa","p_out_bar"];
const TEMP_OUT = ["T_out_K","T_exhaust_K"];

function prepareItems(source) {
  return (Array.isArray(source) ? source : []).filter(item=>item && typeof item==="object").map(item=>({
    id:String(item.id ?? ""), title:String(item.title || item.id || "未命名部件"),
    fields:(Array.isArray(item.fields) ? item.fields : []).filter(field=>field && typeof field.key==="string").map(field=>({
      key:field.key,label:String(field.label || field.key),value:field.value,unit:String(field.unit || "—"),
    })), category:categoryOf(String(item.id ?? "")),
  }));
}

function primaryFields(item) {
  const byCategory = {
    supply:[["power_MW","power_W"],PRESSURE_OUT,["eta"],["T_out_K"]],
    shaft:[["turbine_power_MW","turbine_power_W"],["turbine_pressure_ratio"],["T_exhaust_K"],["mass_flow_kgs"]],
    combustion:item.id==="injector"
      ? [["dp_ox_MPa","dp_ox_Pa"],["dp_fuel_MPa","dp_fuel_Pa"],["dp_ox_ratio"],["dp_fuel_ratio"]]
      : [["T_chamber_K","T_combustion_K"],["mdot_chamber_kgs","mdot_total_kgs"],["mixture_ratio_OF","phi"],["preburner_pressure_MPa","preburner_pressure_Pa","combustion_efficiency"]],
    thermal:[["heat_load_MW","heat_load_W"],["delta_T_K"],["delta_P_MPa"],["T_out_K"]],
    thrust:[["thrust_N"],["thrust_vac_N"],["Isp_s"],["Isp_vac_s"]],
  };
  const selected=(byCategory[item.category.id] || []).map(keys=>getField(item,keys)).filter(Boolean);
  // Prefer the category's actual reported quantities; incomplete records remain visibly incomplete.
  for (const field of item.fields) {
    if (selected.length>=4) break;
    if (!selected.includes(field) && finite(field.value)) selected.push(field);
  }
  return selected.slice(0,4);
}

function kpis(item) {
  const fields=primaryFields(item);
  return fields.length ? `<div class="ca-kpis">${fields.map(field=>`<div class="ca-kpi"><span>${escape(field.label)}</span><strong>${escape(format(field.value))}<small>${escape(unitText(field))}</small></strong><span class="ca-symbol">${escape(symbolFor(item,field))}</span></div>`).join("")}</div>` : "";
}

function stateBlock(title, fields, empty) {
  const actual=fields.filter(Boolean);
  return `<div class="ca-state"><span class="ca-state-title">${escape(title)}</span>${actual.length ? actual.map(field=>`<div class="ca-state-value"><span>${escape(field.label)}</span><b>${escape(quantity(field))}</b></div>`).join("") : `<p class="ca-empty-note">${escape(empty)}</p>`}</div>`;
}

function processSymbol(category) {
  const shapes={
    supply:'<circle cx="44" cy="35" r="20"/><path d="M34 24 58 35 34 46Z"/><path d="M8 35h16m40 0h16"/>',
    shaft:'<path d="M26 15 61 25v20L26 55Z"/><path d="M7 35h19m35 0h20M44 35v27"/>',
    thermal:'<rect x="23" y="13" width="42" height="44" rx="3"/><path d="M8 35h21V23h12v24h12V35h27"/>',
    combustion:'<path d="M27 17h34v36H27Z"/><path d="M8 25h19M8 45h19m34-10h19M34 24v22m7-22v22"/>',
    thrust:'<path d="M22 18q26 0 27 17 1 17 24 20V15Q50 18 49 35 48 52 22 52Z"/><path d="M6 35h16m31 0h29"/>',
    other:'<rect x="23" y="15" width="42" height="40" rx="3"/><path d="M8 35h15m42 0h15"/>',
  };
  return `<svg class="ca-process-symbol" viewBox="0 0 88 72" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round">${shapes[category.id]}</g></svg>`;
}

function schematic(item) {
  let left,right;
  if (["supply","shaft","thermal"].includes(item.category.id)) {
    left=stateBlock("入口",[getField(item,PRESSURE_IN),getField(item,["T_in_K"])],"未返回入口状态量");
    right=stateBlock("出口",[getField(item,PRESSURE_OUT),getField(item,TEMP_OUT)],"未返回出口状态量");
  } else if (item.id==="injector") {
    left=stateBlock("氧化剂支路",[getField(item,["dp_ox_MPa","dp_ox_Pa"]),getField(item,["dp_ox_ratio"])],"未返回氧化剂压降");
    right=stateBlock("燃料支路",[getField(item,["dp_fuel_MPa","dp_fuel_Pa"]),getField(item,["dp_fuel_ratio"])],"未返回燃料压降");
  } else if (item.category.id==="combustion") {
    left=stateBlock("混合条件",[getField(item,["mdot_ox_kgs"]),getField(item,["mdot_fuel_kgs"]),getField(item,["mixture_ratio_OF","phi"])],"未返回混合条件");
    right=stateBlock("燃烧结果",[getField(item,["T_chamber_K","T_combustion_K"]),getField(item,["preburner_pressure_MPa","preburner_pressure_Pa"]),getField(item,["mdot_chamber_kgs","mdot_total_kgs"])],"未返回燃烧状态量");
  } else if (item.category.id==="thrust") {
    left=stateBlock("环境条件",[getField(item,["p_amb_Pa"])],"未返回环境压力");
    right=stateBlock("喷管出口",[getField(item,["p_exit_Pa","exit_pressure_Pa"]),getField(item,["T_exit_K"]),getField(item,["exit_velocity_ms","Ve_ms"])],"未返回出口状态量");
  } else {
    return '<p class="ca-empty-note ca-visual-empty">本记录未定义该部件的过程关系，请查看参数表。</p>';
  }
  const process=["supply","shaft","thermal","combustion"].includes(item.category.id) && item.id!=="injector";
  return `<div class="ca-process${process ? " ca-process-directional" : ""}">${left}<div class="ca-process-center">${processSymbol(item.category)}<span>${escape(item.id==="injector" ? "喷注压降" : item.category.short)}</span></div>${right}</div><p class="ca-caption">${process ? "箭头表示过程方向；状态量来自当前记录。" : "按当前记录展示条件与结果；符号为部件示意。"}</p>`;
}

/** A comparison is drawn only when two finite, nonnegative values have the same displayed unit. */
function comparison(item, title, leftKeys, rightKeys, labels) {
  const a=getField(item,leftKeys),b=getField(item,rightKeys);
  if (!a || !b || !finite(a.value) || !finite(b.value) || a.value<0 || b.value<0 || unitText(a)!==unitText(b)) return "";
  const max=Math.max(a.value,b.value) || 1;
  const unit=unitText(a), width=240, x=70, top=29;
  const bars=[a,b].map((field,index)=>{
    const y=top+index*32;
    return `<g><text class="ca-chart-label" x="60" y="${y+11}" text-anchor="end">${escape(labels[index])}</text><rect class="ca-bar ca-bar-${index}" x="${x}" y="${y}" width="${width*(field.value/max)}" height="17" rx="1"/><text class="ca-chart-value" x="322" y="${y+12}">${escape(format(field.value))}</text></g>`;
  }).join("");
  const ticks=[0,.5,1].map(frac=>`<g><line class="ca-chart-grid" x1="${x+width*frac}" y1="20" x2="${x+width*frac}" y2="87"/><text class="ca-chart-axis" x="${x+width*frac}" y="103" text-anchor="middle">${escape(format(max*frac))}</text></g>`).join("");
  const description=`${title}。${labels[0]} ${quantity(a)}；${labels[1]} ${quantity(b)}。`;
  return `<figure class="ca-comparison"><figcaption>${escape(title)}<span>${escape(unit)}</span></figcaption><svg class="ca-chart" viewBox="0 0 410 112" role="img" aria-label="${escape(description)}"><title>${escape(description)}</title>${ticks}${bars}</svg></figure>`;
}

function charts(item) {
  const definitions={
    supply:[["压力对比",PRESSURE_IN,PRESSURE_OUT,["入口","出口"]],["温度对比",["T_in_K"],TEMP_OUT,["入口","出口"]]],
    shaft:[["压力对比",PRESSURE_IN,PRESSURE_OUT,["入口","出口"]],["温度对比",["T_in_K"],TEMP_OUT,["入口","出口"]]],
    thermal:[["温度对比",["T_in_K"],TEMP_OUT,["入口","出口"]],["压力对比",PRESSURE_IN,PRESSURE_OUT,["入口","出口"]]],
    combustion:item.id==="injector"
      ? [["喷注压降",["dp_ox_MPa","dp_ox_Pa"],["dp_fuel_MPa","dp_fuel_Pa"],["氧化剂","燃料"]]]
      : [["已报告支路流量",["mdot_ox_kgs"],["mdot_fuel_kgs"],["氧化剂","燃料"]],["燃烧温度",["T_chamber_ideal_K"],["T_chamber_K"],["理想","计算"]]],
    thrust:[["推力对比",["thrust_N"],["thrust_vac_N"],["计算工况","真空"]],["比冲对比",["Isp_s"],["Isp_vac_s"],["计算工况","真空"]]],
  };
  const content=(definitions[item.category.id] || []).map(args=>comparison(item,...args)).filter(Boolean);
  return content.length ? `<div class="ca-comparisons">${content.join("")}</div>` : '<div class="ca-chart-empty"><span class="ca-empty-mark" aria-hidden="true">↔</span><p>暂无可成对比较的数值</p><span>需要单位一致的入口 / 出口量，或同类工况量。已返回的参数仍列于下方。</span></div>';
}

function groupOf(field) {
  const key=field.key;
  if (/^(mode|flow_model|delta_P_source)$/.test(key)) return "模型与数据来源";
  if (/^(p_|P_|delta_[pP]|dp_|injector_dp|preburner_pressure|turbine_pressure|pressure_recovery|head_m|npsh)/.test(key)) return "压力与压降";
  if (/^(mdot_|mass_flow|volume_flow|mixture_ratio|phi$|fuel_fraction)/.test(key)) return "流量与配比";
  if (/^(T_|t_in_|delta_T|actual_temp|rho_|h_|cp_|gamma|molar_mass)/.test(key)) return "热力状态与物性";
  if (/^(power_|turbine_power|heat_load|eta|combustion_efficiency|specific_work|isentropic_drop|actual_drop|shaft_speed|nr_rpm)/.test(key)) return "能量与效率";
  if (/^(thrust_|Isp|Ve_|exit_velocity|c_star|Cf|mach_exit|exit_mach|expansion_ratio|exit_pressure)/.test(key)) return "膨胀与推进性能";
  return "其他系统参数";
}

function tables(item) {
  if (!item.fields.length) return '<div class="ca-no-fields"><h4>该部件没有可展示的系统参数</h4><p>当前记录未返回此部件的系统量。</p></div>';
  const groups=new Map();
  item.fields.forEach(field=>{
    const group=groupOf(field);
    if (!groups.has(group)) groups.set(group,[]);
    groups.get(group).push(field);
  });
  return `<div class="ca-parameter-groups">${[...groups].map(([title,fields])=>`<section class="ca-parameter-group"><div class="ca-group-heading"><h4>${escape(title)}</h4><span>${fields.length} 项</span></div><div class="ca-table-wrap"><table class="ca-table"><thead><tr><th scope="col">参数</th><th scope="col">符号</th><th scope="col">数值</th><th scope="col">单位</th></tr></thead><tbody>${fields.map(field=>`<tr><td title="${escape(field.key)}">${escape(field.label)}</td><td class="ca-symbol" title="${escape(field.key)}">${escape(symbolFor(item,field))}</td><td class="ca-value">${escape(format(field.value))}</td><td class="ca-unit">${escape(unitText(field))}</td></tr>`).join("")}</tbody></table></div></section>`).join("")}</div>`;
}

function inspector(item, selected) {
  return `<section class="ca-inspector" data-ca-panel="${escape(item.id)}" data-ca-kind="${item.category.id}" data-ca-title="${escape(item.title)}" aria-label="${escape(item.title)}性能结果"${selected ? "" : " hidden"}><div class="ca-inspector-heading"><div><div class="ca-breadcrumb">${escape(item.category.title)}<span>/</span>性能结果</div><h3>${escape(item.title)}</h3></div><span class="ca-result-tag">${item.fields.length} 项系统量</span></div>${kpis(item)}<div class="ca-visual-grid"><section class="ca-visual-panel"><div class="ca-subheading"><h4>过程量示意</h4><span>STATE</span></div>${schematic(item)}</section><section class="ca-visual-panel"><div class="ca-subheading"><h4>性能对比</h4><span>COMPARISON</span></div>${charts(item)}</section></div><div class="ca-data-heading"><h4>参数明细</h4><span>— 表示未提供或非有限值</span></div>${tables(item)}</section>`;
}

export function renderComponentWorkspace(source) {
  const items=prepareItems(source);
  if (!items.length) return '<section class="ca-workspace ca-empty-workspace" aria-label="部件性能分析"><span class="ca-empty-mark" aria-hidden="true">▤</span><h2>暂无部件性能结果</h2><p>当前计算记录未返回可展示的系统部件。完成设计点计算后，可在此查看各部件的性能量。</p></section>';
  const categories=[...CATEGORIES,...(items.some(item=>item.category.id==="other") ? [OTHER_CATEGORY] : [])];
  return `<section class="ca-workspace" aria-label="部件性能分析"><div class="ca-toolbar"><div class="ca-toolbar-label"><span class="ca-workspace-mark" aria-hidden="true">▤</span><b>部件工作区</b><span>${items.length} 个部件</span></div><span class="ca-toolbar-context">稳态设计点 · 系统计算结果</span></div><div class="ca-categories" role="group" aria-label="部件类别"><button type="button" class="ca-category is-selected" data-ca-category="all" aria-pressed="true">全部<span>${items.length}</span></button>${categories.map(category=>{
    const count=items.filter(item=>item.category.id===category.id).length;
    return `<button type="button" class="ca-category" data-ca-category="${category.id}" aria-pressed="false"${count ? "" : " disabled"}>${escape(category.title)}<span>${count}</span></button>`;
  }).join("")}</div><div class="ca-body"><aside class="ca-navigator" aria-label="部件对象"><div class="ca-navigator-heading"><h3>分析对象</h3><span data-ca-visible-count>${items.length}</span></div>${categories.map(category=>{
    const members=items.filter(item=>item.category.id===category.id);
    return members.length ? `<section class="ca-object-group" data-ca-group="${category.id}"><h4>${escape(category.title)}<span>${members.length}</span></h4>${members.map(item=>`<button type="button" class="ca-object${item===items[0] ? " is-selected" : ""}" data-ca-object="${escape(item.id)}" data-ca-kind="${category.id}" aria-current="${item===items[0] ? "true" : "false"}" title="${escape(item.title)}"><span class="ca-object-mark" aria-hidden="true">${category.mark}</span><span class="ca-object-text"><b>${escape(item.title)}</b><small>${item.fields.length} 项参数</small></span><span class="ca-object-chevron" aria-hidden="true">›</span></button>`).join("")}</section>` : "";
  }).join("")}<div class="ca-navigator-note">选择部件查看状态量、性能对比及完整系统参数。</div></aside><div class="ca-detail">${items.map((item,index)=>inspector(item,index===0)).join("")}</div></div><div class="ca-statusline"><span>当前对象：<b data-ca-selection>${escape(items[0].title)}</b></span><span>数值来自当前计算记录</span></div></section>`;
}

const mountHandlers=new WeakMap();

/** Call after replacing host.innerHTML. The host persists; no detached panel listener survives a render. */
export function mountComponentWorkspace(host, recordId) {
  if (!host?.querySelector || !host.dataset) return;
  const previous=mountHandlers.get(host);
  if (previous) host.removeEventListener("click",previous);
  mountHandlers.delete(host);
  const workspace=host.querySelector(".ca-workspace");
  if (!workspace) return;
  const buttons=[...workspace.querySelectorAll("[data-ca-object]")];
  const panels=[...workspace.querySelectorAll("[data-ca-panel]")];
  const categories=[...workspace.querySelectorAll("[data-ca-category]")];
  const groups=[...workspace.querySelectorAll("[data-ca-group]")];
  const currentRecord=String(recordId ?? "");
  const changed=host.dataset.caRecordId!==currentRecord;
  host.dataset.caRecordId=currentRecord;
  let category=changed ? "all" : host.dataset.caCategory || "all";
  let selected=changed ? buttons[0]?.dataset.caObject : host.dataset.caSelected;
  const update=()=>{
    if (!categories.some(button=>button.dataset.caCategory===category && !button.disabled)) category="all";
    const visible=buttons.filter(button=>category==="all" || button.dataset.caKind===category);
    if (!visible.some(button=>button.dataset.caObject===selected)) selected=visible[0]?.dataset.caObject || "";
    host.dataset.caCategory=category;
    host.dataset.caSelected=selected;
    categories.forEach(button=>{
      const active=button.dataset.caCategory===category;
      button.setAttribute("aria-pressed",String(active));
      button.classList.toggle("is-selected",active);
    });
    groups.forEach(group=>{group.hidden=category!=="all" && group.dataset.caGroup!==category;});
    buttons.forEach(button=>{
      const active=button.dataset.caObject===selected;
      button.setAttribute("aria-current",String(active));
      button.classList.toggle("is-selected",active);
    });
    panels.forEach(panel=>{panel.hidden=panel.dataset.caPanel!==selected;});
    const title=panels.find(panel=>panel.dataset.caPanel===selected)?.dataset.caTitle || "—";
    const selection=workspace.querySelector("[data-ca-selection]");
    const count=workspace.querySelector("[data-ca-visible-count]");
    if (selection) selection.textContent=title;
    if (count) count.textContent=String(visible.length);
  };
  update();
  if (!buttons.length) return;
  const handler=event=>{
    const target=event.target?.closest?.("[data-ca-category], [data-ca-object]");
    if (!target || !workspace.contains(target) || target.disabled) return;
    if (target.dataset.caCategory!=null) category=target.dataset.caCategory;
    else {
      selected=target.dataset.caObject;
      if (category!=="all" && category!==target.dataset.caKind) category=target.dataset.caKind;
    }
    update();
  };
  host.addEventListener("click",handler);
  mountHandlers.set(host,handler);
}
