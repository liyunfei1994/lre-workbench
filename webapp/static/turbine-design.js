// Read-only turbine design handoff. Values and exports come from the selected saved record.
const finite=value=>typeof value==="number"&&Number.isFinite(value);
const escape=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const keys=model=>["ox","fuel","shared"].filter(key=>Object.hasOwn(model?.turbines||{},key));
const labels={ox:"富氧涡轮",fuel:"富燃涡轮",shared:"燃气发生器共轴涡轮"};
const statuses={saved:"已保存",saved_with_extrapolation:"部分超出机制温区",derived:"由保存值计算",ready:"已齐备",assumed:"采用假设",missing:"待补充",incomplete:"待补充",conflict:"冲突",pass:"检查通过",unavailable:"未保存",not_evaluated:"待设计",disabled:"未启用",kinematic_only:"运动学初选"};
const array=value=>Array.isArray(value)?value:[];
const textValue=value=>value==null||typeof value==="number"&&!Number.isFinite(value)?"":typeof value==="object"?JSON.stringify(value):String(value);
const displayValue=value=>{
  if(value==null||typeof value==="number"&&!Number.isFinite(value))return "—";
  if(!finite(value))return textValue(value);
  if(value!==0&&(Math.abs(value)<1e-4||Math.abs(value)>=1e9)){
    const [coefficient,exponent]=value.toExponential(7).split("e");
    return `${Number(coefficient)}e${exponent}`;
  }
  return value.toLocaleString("zh-CN",{maximumSignificantDigits:8});
};
const fieldStatus=field=>!textValue(field?.value)?"missing":Object.hasOwn(statuses,field?.status)?field.status:"missing";
const sourceText=(field,fallback="")=>typeof field?.source==="string"?field.source:field?.source?textValue(field.source):fallback;
const badge=status=>`<span class="td-status td-status-${Object.hasOwn(statuses,status)?status:"missing"}">${escape(statuses[status]||"待确认")}</span>`;
const safeUrl=value=>{try{const url=new URL(value);return ["https:","http:"].includes(url.protocol)?url.href:"";}catch{return "";}};

function fieldTable(fields,fallback="",empty="本记录未保存此组设计条件。") {
  const items=array(fields);if(!items.length)return `<p class="td-empty">${escape(empty)}</p>`;
  return `<div class="td-table-wrap"><table class="td-fields"><thead><tr><th>设计条件</th><th>保存值</th><th>单位</th><th>状态</th><th>来源 / 说明</th></tr></thead><tbody>${items.map(field=>`<tr data-td-field="${escape(field?.key)}"><th scope="row">${escape(field?.label||field?.key||"未命名条件")}</th><td class="td-value">${escape(displayValue(field?.value))}</td><td class="td-unit">${escape(field?.unit||"—")}</td><td>${badge(fieldStatus(field))}</td><td class="td-provenance">${sourceText(field,fallback)?`<span>${escape(sourceText(field,fallback))}</span>`:""}${field?.note?`<p>${escape(field.note)}</p>`:""}</td></tr>`).join("")}</tbody></table></div>`;
}

function checkList(checks) {
  const items=array(checks);return items.length?`<ul class="td-checks">${items.map(check=>`<li><div><strong>${escape(check?.label||check?.id||"检查项")}</strong>${badge(check?.status)}</div>${check?.note?`<p>${escape(check.note)}</p>`:""}</li>`).join("")}</ul>`:"";
}

function requirementList(items) {
  return array(items).length?`<ul class="td-requirements">${items.map(item=>`<li>${escape(item)}</li>`).join("")}</ul>`:"";
}

function propertyTable(table) {
  const columns=array(table?.columns),rows=array(table?.rows),range=array(table?.temperature_range_k);
  const rangeText=range.length===2&&range.every(finite)?`${displayValue(range[0])}–${displayValue(range[1])} K`:"温区未记录";
  const rangeSummary=`${rangeText} · ${rows.length} 个保存温度点`;
  return `<details class="td-details td-properties"><summary>温度依赖物性表 <span>${escape(rangeSummary)}</span> ${badge(table?.status)}</summary>${table?.status==="saved_with_extrapolation"?'<p class="td-range-note">带标记的采样超出机制共同声明温区；需核对参与物种的温区与外推适用性。</p>':""}<p class="td-caption">${table?.composition_policy==="frozen_saved_inlet"?"按已保存入口质量分数冻结组分。":"组分处理方式："+escape(table?.composition_policy||"未记录")}${finite(table?.pressure_pa)?` 采样压力 ${escape(displayValue(table.pressure_pa))} Pa。`:" 采样压力未记录。"}${table?.note?` ${escape(table.note)}`:""}</p>${columns.length&&rows.length?`<div class="td-table-wrap"><table class="td-properties-table"><thead><tr>${columns.map(column=>`<th>${escape(column?.label||column?.key)}<small>${escape(column?.unit||"—")}</small></th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr${row?.within_mechanism_common_temperature_range===false?' class="td-range-outside"':""}>${columns.map((column,i)=>`<td>${escape(displayValue(row?.[column.key]))}${i===0&&row?.within_mechanism_common_temperature_range===false?" †":""}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`:'<p class="td-empty">本记录没有保存可用的温区物性采样。任务书保留缺项，未在读取时补算或外推。</p>'}</details>`;
}

function renderComposition(items) {
  const composition=[...array(items)].sort((a,b)=>{
    const av=finite(a?.mass_fraction)?a.mass_fraction:-Infinity,bv=finite(b?.mass_fraction)?b.mass_fraction:-Infinity;
    return av===bv?0:bv-av;
  });
  if(!composition.length)return '<p class="td-empty">未保存入口组分，不能据此指定实际混合气体。</p>';
  const valid=item=>finite(item?.mass_fraction)&&item.mass_fraction>=0&&item.mass_fraction<=1;
  const major=composition.filter(item=>valid(item)&&item.mass_fraction>=1e-6),trace=composition.filter(item=>valid(item)&&item.mass_fraction<1e-6),invalid=composition.filter(item=>!valid(item));
  const table=rows=>`<table><thead><tr><th>组分</th><th>质量分数 Y</th></tr></thead><tbody>${rows.map(item=>`<tr><th scope="row">${escape(item?.species||"未命名组分")}</th><td title="${escape(textValue(item?.mass_fraction))}">${escape(displayValue(item?.mass_fraction))}</td></tr>`).join("")}</tbody></table>`;
  return `${major.length?`<p class="td-composition-threshold">主要组分 · Y ≥ 1e-6</p>${table(major)}`:trace.length?'<p class="td-caption">组分低于首屏阈值，完整数据见微量组分。</p>':""}${trace.length?`<details class="td-details td-trace"><summary>微量组分 · ${trace.length} 项 <span>0 ≤ Y &lt; 1e-6</span></summary>${table(trace)}</details>`:""}${invalid.length?`<div class="td-composition-invalid"><h4>组分缺项 / 异常值</h4>${table(invalid)}</div>`:""}`;
}

function renderGas(gas,fallback) {
  const model=gas?.model||{};
  const modelLabels={mechanism:"物性机制",mechanism_sha256:"机制文件标识",phase:"相名称",gas_eos:"状态方程",transport_model:"输运模型",source:"物性来源",cantera_version:"Cantera 版本",common_min_temperature_k:"机制共同温区下限 / K",common_max_temperature_k:"机制共同温区上限 / K",enthalpy_entropy_reference:"焓 / 熵参考系"};
  return `<section class="td-section"><div class="td-section-heading"><div><span class="td-kicker">FLUID / GAS PROPERTIES</span><h3>入口燃气与物性</h3></div>${badge(gas?.status)}</div><div class="td-gas-grid"><div class="td-composition"><h4>保存的入口组分</h4><p class="td-caption">${gas?.composition_basis==="mass_fraction"?"质量分数 Y（无量纲），保持记录中的原值。":"组分基准："+escape(gas?.composition_basis||"未记录")}</p>${renderComposition(gas?.composition)}</div><div>${fieldTable(gas?.fields,fallback,"本记录未保存燃气物性。")}</div></div>${Object.keys(modelLabels).some(key=>model[key])?`<details class="td-details td-model"><summary>物性模型与来源</summary><dl>${Object.entries(modelLabels).filter(([key])=>model[key]).map(([key,label])=>`<div><dt>${label}</dt><dd>${escape(textValue(model[key]))}</dd></div>`).join("")}</dl></details>`:""}${propertyTable(gas?.property_table)}</section>`;
}

const MEANLINE_COLUMNS=[
  ["stage","级次","—"],["work_fraction","比功分配","—"],["work_j_kg","级比功","J/kg"],["loading_coefficient","载荷系数","—"],["u_m_s","圆周速度 U","m/s"],["ca_m_s","轴向速度 ca","m/s"],
  ["cu1_m_s","入口绝对切向速度 cu₁","m/s"],["cu2_m_s","出口绝对切向速度 cu₂","m/s"],["wu1_m_s","入口相对切向速度 wu₁","m/s"],["wu2_m_s","出口相对切向速度 wu₂","m/s"],
  ["c1_m_s","入口绝对速度 c₁","m/s"],["c2_m_s","出口绝对速度 c₂","m/s"],["w1_m_s","入口相对速度 w₁","m/s"],["w2_m_s","出口相对速度 w₂","m/s"],
  ["alpha1_deg","入口绝对流角 α₁","°"],["alpha2_deg","出口绝对流角 α₂","°"],["beta1_deg","入口相对流角 β₁","°"],["beta2_deg","出口相对流角 β₂","°"],
  ["rotor_static_drop_j_kg","动叶静焓降代数量","J/kg"],["reaction_target","目标反动度","—"],["reaction_check","反动度代数检查","—"],["euler_residual_j_kg","Euler 残差","J/kg"],
];

function renderMeanline(meanline) {
  const rows=array(meanline?.rows),status=meanline?.status||"disabled";
  const table=(title,indices)=>`<h4>${title}</h4><div class="td-table-wrap"><table class="td-properties-table"><thead><tr>${indices.map(i=>`<th>${MEANLINE_COLUMNS[i][1]}<small>${MEANLINE_COLUMNS[i][2]}</small></th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${indices.map(i=>`<td>${escape(displayValue(row?.[MEANLINE_COLUMNS[i][0]]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  return `<details class="td-details td-meanline"><summary>均径速度三角形与逐级比功初选 ${badge(status)}</summary>${meanline?.reason?`<p class="td-caption">${escape(meanline.reason)}</p>`:""}${status==="kinematic_only"&&rows.length?`<p class="td-caption">仅对给定反动度、轴速系数与比功闭合 Euler 代数关系；不提供静态、压比、叶型或性能预测。</p>${table("逐级比功与载荷",[0,1,2,3,4,5,19,20,21])}${table("转子入口 / 出口速度",[0,6,7,8,9,10,11,12,13])}${table("流角与动叶焓降",[0,14,15,16,17,18])}${checkList(meanline.checks)}`:!meanline?.reason?'<p class="td-caption">本记录未启用均径运动学初选，尚无速度三角形结果。</p>':""}${array(meanline?.notes).length?`<ul class="td-notes">${meanline.notes.map(note=>`<li>${escape(note)}</li>`).join("")}</ul>`:""}</details>`;
}

function renderNodeValues(machine,fallback) {
  const point=machine?.design_point||{},configs=[["pressure_in_pa","系统入口节点压力","Pa"],["pressure_out_pa","系统出口节点压力","Pa"],["temperature_in_k","系统入口节点温度","K"],["temperature_out_k","系统出口节点温度","K"],["pressure_ratio","系统节点压力比","—"]];
  const fields=configs.filter(([key])=>Object.hasOwn(point,key)).map(([key,label,unit])=>({key:`node_${key}`,label,unit,value:point[key],status:finite(point[key])?"saved":"missing",source:fallback}));
  const basis=point.state_basis==="total_to_total"?"显式总总接口近似":point.state_basis||"未声明";
  return `<details class="td-details td-node-values"${!point.state_basis?" open":""}><summary>保存的系统节点值与状态约定</summary><p class="td-caption">状态基准：${escape(basis)}。${point.state_assumption?escape(point.state_assumption):"节点值按原记录列出；未声明状态定义时不把 p、T 自动标为总压或总温。"}</p>${fieldTable(fields,fallback,"本记录未保存原始节点压力与温度。")}</details>`;
}

function renderMachine(machine,key,selected) {
  const fallback=machine?.source?.component_key||"保存的系统记录",checks=array(machine?.checks),powerChecks=array(machine?.power_matching?.checks);
  const conflicts=[...checks,...powerChecks].filter(check=>check?.status==="conflict");
  const hasConflict=machine?.status==="conflict"||conflicts.length||[...array(machine?.global_fields),...array(machine?.power_matching?.fields),...array(machine?.gas?.fields),...array(machine?.stage_handoff?.fields)].some(field=>field?.status==="conflict");
  const title=hasConflict?"存在待解决冲突":machine?.ready_for_global_setup?"Global setup 数据齐备":"Global setup 待补充";
  const links=array(machine?.references).map(item=>({...item,url:safeUrl(item?.url)})).filter(item=>item.url);
  return `<article class="td-machine" data-td-machine="${key}"${selected?"":" hidden"}><div class="td-machine-heading"><div><h3>${escape(machine?.label||labels[key])}</h3><p>系统部件：${escape(fallback)}${machine?.source?.inlet_state_key?` · 入口节点：${escape(machine.source.inlet_state_key)}`:""}${machine?.source?.outlet_state_key?` · 出口节点：${escape(machine.source.outlet_state_key)}`:""}</p></div><span class="td-readiness ${hasConflict?"is-conflict":machine?.ready_for_global_setup?"is-ready":""}">${title}</span></div><div class="td-toolbar"><p>按当前计算记录整理 CFturbo 输入与设计约束。</p><div>${machine?.source?.component_key?'<button type="button" data-td-inspect>查看部件结果</button>':""}${["json","csv","md"].map(ext=>`<button type="button" data-td-export="${ext}">${{json:"JSON 数据",csv:"CSV 条件表",md:"Markdown 任务书"}[ext]}</button>`).join("")}</div></div><div class="td-primary-grid"><section class="td-section"><div class="td-section-heading"><div><span class="td-kicker">GLOBAL SETUP</span><h3>全局设计条件</h3></div></div>${fieldTable(machine?.global_fields,fallback)}${renderNodeValues(machine,fallback)}${array(machine?.missing_requirements).length?`<div class="td-pending"><h4>进入 Global setup 前需补充</h4>${requirementList(machine.missing_requirements)}</div>`:""}</section><section class="td-section"><div class="td-section-heading"><div><span class="td-kicker">POWER / EFFICIENCY</span><h3>功率匹配与效率口径</h3></div></div>${fieldTable(machine?.power_matching?.fields,fallback)}${checkList(powerChecks)}</section></div>${renderGas(machine?.gas,fallback)}<section class="td-section"><div class="td-section-heading"><div><span class="td-kicker">1D DESIGN HANDOFF</span><h3>一维设计约束与后续输入</h3></div></div>${fieldTable(machine?.stage_handoff?.fields,fallback)}${machine?.stage_handoff?.note?`<p class="td-caption">${escape(machine.stage_handoff.note)}</p>`:""}${requirementList(machine?.stage_handoff?.missing_requirements)}<p class="td-caption">此处整理一维设计输入与约束；逐级气动闭合、叶型与性能仍需专业设计。</p>${renderMeanline(machine?.meanline)}</section><details class="td-details td-audit"${conflicts.length?" open":""}><summary>记录一致性审计${conflicts.length?` · ${conflicts.length} 项冲突`:""}</summary>${checkList(checks)}${array(machine?.notes).length?`<ul class="td-notes">${machine.notes.map(note=>`<li>${escape(note)}</li>`).join("")}</ul>`:""}${machine?.geometry?.note?`<p class="td-caption">概念几何：${escape(machine.geometry.note)}</p>`:""}<p class="td-fingerprint">设计任务书标识：${escape(machine?.design_sha256||"未保存")}</p></details>${links.length?`<footer class="td-references"><strong>CFturbo 手册</strong>${links.map(item=>`<a href="${escape(item.url)}" target="_blank" rel="noopener noreferrer">${escape(item.title||item.url)}</a>`).join("")}</footer>`:""}</article>`;
}

export function renderTurbineDesign(model) {
  const sides=keys(model);
  return `<section class="turbine-design" data-turbine-design><header class="td-title"><span class="td-kicker">TURBINE SYSTEM DESIGN</span><h2>涡轮总体设计 <span>CFturbo 设计任务书</span></h2><p>系统设计点、真实燃气物性与待确认条件，汇总到同一计算记录。</p></header><p class="td-source">计算记录：<span>${escape(model?.source?.record_id||"未指定")}</span> · 读取保存值；修改输入后需重新计算。</p>${sides.length?`<div class="td-select" role="group" aria-label="涡轮选择">${sides.map((key,i)=>`<button type="button" data-td-side="${key}" aria-pressed="${i===0}">${escape(model.turbines[key]?.label||labels[key])}</button>`).join("")}</div>${sides.map((key,i)=>renderMachine(model.turbines[key],key,i===0)).join("")}`:'<p class="td-empty">本条记录没有保存可用的涡轮部件。计算设计点后可查看设计任务书。</p>'}</section>`;
}

function selectedMachine(model,key) {return keys(model).includes(key)&&model.turbines[key]&&typeof model.turbines[key]==="object"?model.turbines[key]:null;}

export function buildTurbineDesignJson(model,key) {
  const turbine=selectedMachine(model,key);if(!turbine)return "";
  return JSON.stringify({schema_version:model.schema_version??1,kind:"turbine_global_design_handoff",source:model.source||{},source_sha256:model.source_sha256??null,turbine_key:key,turbine,exchange:{values:"saved record values; null denotes missing; field units and statuses retained",scope:"Global setup and one-dimensional constraints; optional meanline Euler kinematic closure only, no solved aerodynamic stage or blade design"}},null,2)+"\n";
}

function exchangeRows(model,key) {
  const turbine=selectedMachine(model,key);if(!turbine)return [];
  const rows=[],fallback=turbine.source?.component_key||"",add=(section,id,label,value,unit="",status="saved",source=fallback,note="")=>rows.push({section,key:id,label,value,unit,status,source,note});
  const fields=(section,items)=>array(items).forEach(field=>add(section,field?.key,field?.label,field?.value,field?.unit,field?.status,sourceText(field,fallback),field?.note));
  add("identity","label","涡轮",turbine.label);add("identity","status","任务书状态",turbine.status);add("identity","ready_for_global_setup","Global setup 数据齐备",turbine.ready_for_global_setup);
  Object.entries(turbine.source||{}).forEach(([id,value])=>add("source",id,id,value));
  Object.entries(turbine.design_point||{}).forEach(([id,value])=>add("design_point",id,id,value));
  fields("global_setup",turbine.global_fields);fields("power_matching",turbine.power_matching?.fields);fields("gas.properties",turbine.gas?.fields);fields("stage_handoff",turbine.stage_handoff?.fields);
  add("gas.composition","basis","组分基准",turbine.gas?.composition_basis,"",turbine.gas?.status);
  array(turbine.gas?.composition).forEach(item=>add("gas.composition",item?.species,item?.species,item?.mass_fraction,"mass_fraction",turbine.gas?.status));
  Object.entries(turbine.gas?.model||{}).forEach(([id,value])=>add("gas.model",id,id,value));
  const table=turbine.gas?.property_table||{};
  for(const id of ["status","temperature_range_k","pressure_pa","composition_policy","note"])if(Object.hasOwn(table,id))add("gas.property_table",id,id,table[id],id==="temperature_range_k"?"K":id==="pressure_pa"?"Pa":"",table.status);
  const knownColumns=new Map(array(table.columns).map(column=>[column.key,column]));
  const gasUnit=id=>knownColumns.get(id)?.unit||({pressure_pa:"Pa",temperature_k:"K",molar_mass_kg_kmol:"kg/kmol",within_mechanism_common_temperature_range:"—"})[id]||"";
  const gasLabel=id=>knownColumns.get(id)?.label||({molar_mass_kg_kmol:"摩尔质量",within_mechanism_common_temperature_range:"位于机制共同声明温区"})[id]||id;
  array(table.rows).forEach((row,i)=>[...new Set([...knownColumns.keys(),...Object.keys(row)])].forEach(id=>add("gas.property_table",`${i}.${id}`,`${textValue(row.temperature_k)} K · ${gasLabel(id)}`,row[id],gasUnit(id),table.status)));
  for(const [endpoint,properties] of Object.entries(turbine.gas?.endpoint_properties||{}))if(properties&&typeof properties==="object")Object.entries(properties).forEach(([id,value])=>add(`gas.endpoint.${endpoint}`,id,gasLabel(id),value,gasUnit(id),turbine.gas?.status));
  for(const [section,items] of [["audit",turbine.checks],["power_matching.audit",turbine.power_matching?.checks]])array(items).forEach(check=>add(section,check?.id,check?.label,"","",check?.status,fallback,check?.note));
  for(const [section,items] of [["missing_requirements",turbine.missing_requirements],["stage_handoff.missing_requirements",turbine.stage_handoff?.missing_requirements],["notes",turbine.notes]])array(items).forEach((note,i)=>add(section,String(i+1),"",note,"",section.includes("missing")?"missing":"saved"));
  if(turbine.stage_handoff?.note)add("stage_handoff","note","说明",turbine.stage_handoff.note);
  const meanline=turbine.meanline;
  if(meanline&&typeof meanline==="object"){
    for(const [id,value] of Object.entries(meanline))if(!["rows","checks","notes"].includes(id))add("meanline",id,id,value,id.endsWith("_j_kg")?"J/kg":"",meanline.status);
    array(meanline.rows).forEach((row,i)=>MEANLINE_COLUMNS.forEach(([id,label,unit])=>add("meanline.rows",`${i}.${id}`,`第 ${textValue(row.stage)} 级 · ${label}`,row[id],unit,meanline.status)));
    array(meanline.checks).forEach(check=>add("meanline.audit",check?.id,check?.label,"","",check?.status,fallback,check?.note));
    array(meanline.notes).forEach((note,i)=>add("meanline.notes",String(i+1),"说明",note,"",meanline.status));
  }
  Object.entries(turbine.geometry||{}).forEach(([id,value])=>add("geometry",id,id,value));
  add("identity","design_sha256","设计任务书标识",turbine.design_sha256);
  array(turbine.references).forEach((reference,i)=>add("reference",String(i+1),reference?.title,reference?.url));
  return rows;
}

export function buildTurbineDesignCsv(model,key) {
  const rows=exchangeRows(model,key);if(!rows.length)return "";
  // Quote every cell. Text beginning with spreadsheet formula characters is neutralized.
  const cell=value=>{let text=textValue(value);if(typeof value==="string"&&/^[\s]*[=+@-]/.test(text))text="'"+text;return `"${text.replaceAll('"','""')}"`;};
  const columns=["section","key","label","value","unit","status","source","note"];
  return [columns.concat(["turbine_key","record_id"]).join(","),...rows.map(row=>columns.map(column=>cell(row[column])).concat([cell(key),cell(model?.source?.record_id)]).join(","))].join("\r\n")+"\r\n";
}

const md=value=>textValue(value).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replace(/([\\`*_\[\]#])/g,"\\$1").replaceAll("|","\\|").replace(/\r?\n/g,"<br>");
export function buildTurbineDesignMarkdown(model,key) {
  const turbine=selectedMachine(model,key),rows=exchangeRows(model,key);if(!turbine)return "";
  const sectionTitles={identity:"任务书标识",source:"来源记录",design_point:"保存的系统设计点",global_setup:"Global setup",power_matching:"功率匹配与效率口径","gas.properties":"入口燃气物性","gas.composition":"入口组分质量分数","gas.model":"物性模型与来源","gas.property_table":"温区物性采样",stage_handoff:"一维设计约束",audit:"记录一致性审计","power_matching.audit":"功率匹配审计",missing_requirements:"Global setup 待补充项","stage_handoff.missing_requirements":"一维设计待补充项",notes:"说明",geometry:"概念几何状态",reference:"参考手册",meanline:"均径初选设置","meanline.rows":"逐级均径初选结果","meanline.audit":"均径 Euler 代数检查","meanline.notes":"均径初选说明"};
  const lines=[`# ${md(turbine.label||labels[key])} · CFturbo 设计任务书`,"",`计算记录：${md(model?.source?.record_id||"未指定")}`,"",`任务书状态：${md(statuses[turbine.status]||turbine.status||"待确认")}；Global setup 数据齐备：${turbine.ready_for_global_setup?"是":"否"}。`,"","保存值与单位按记录保留；缺失值留空。可选均径初选仅闭合 Euler 运动学关系，尚未完成逐级气动、叶型与性能设计。",""];
  for(const section of [...new Set(rows.map(row=>row.section))]) {
    lines.push(`## ${sectionTitles[section]||md(section)}`,"","| 条件 | 保存值 | 单位 | 状态 | 来源 / 说明 |","| --- | --- | --- | --- | --- |");
    for(const row of rows.filter(item=>item.section===section))lines.push(`| ${md(row.label||row.key)} | ${md(row.value)} | ${md(row.unit)} | ${md(statuses[row.status]||row.status)} | ${md([row.source,row.note].filter(Boolean).join(" · "))} |`);
    lines.push("");
  }
  return lines.join("\n")+"\n";
}

export function turbineDesignExportFilename(model,key,extension) {
  const machineKey=["ox","fuel","shared"].includes(key)?key:"unknown",record=String(model?.source?.record_id||model?.turbines?.[machineKey]?.source?.record_id||"unrecorded").replace(/[^a-zA-Z0-9_-]/g,"_").slice(0,80);
  return `turbine-design-${machineKey}-${record}.${["json","csv","md"].includes(extension)?extension:"txt"}`;
}

export function mountTurbineDesign(container,model,{onInspectComponent,initialComponent}={}) {
  const root=container?.matches?.("[data-turbine-design]")?container:container?.querySelector?.("[data-turbine-design]");if(!root)return ()=>{};
  root._turbineDesignCleanup?.();const doc=root.ownerDocument,win=doc.defaultView||window,sides=keys(model);let selected=sides.find(key=>initialComponent&&model.turbines[key]?.source?.component_key===initialComponent)||sides[0],disposed=false;
  for(const tab of root.querySelectorAll("[data-td-side]"))tab.setAttribute("aria-pressed",String(tab.dataset.tdSide===selected));
  for(const panel of root.querySelectorAll("[data-td-machine]"))panel.hidden=panel.dataset.tdMachine!==selected;
  for(const button of root.querySelectorAll("[data-td-inspect]"))button.hidden=typeof onInspectComponent!=="function";
  const click=event=>{if(disposed)return;const button=event.target?.closest?.("button");if(!button||!root.contains(button))return;
    const key=button.dataset.tdSide;
    if(sides.includes(key)){selected=key;for(const tab of root.querySelectorAll("[data-td-side]"))tab.setAttribute("aria-pressed",String(tab.dataset.tdSide===key));for(const panel of root.querySelectorAll("[data-td-machine]"))panel.hidden=panel.dataset.tdMachine!==key;return;}
    const owner=button.closest?.("[data-td-machine]")?.dataset?.tdMachine;
    if(owner&&owner!==selected)return;
    if(button.hasAttribute("data-td-inspect")){const component=model?.turbines?.[selected]?.source?.component_key;if(component&&typeof onInspectComponent==="function")onInspectComponent(component,selected);return;}
    const ext=button.dataset.tdExport;if(!["json","csv","md"].includes(ext))return;
    const content={json:buildTurbineDesignJson,csv:buildTurbineDesignCsv,md:buildTurbineDesignMarkdown}[ext](model,selected);if(!content)return;
    const blob=new win.Blob([content],{type:{json:"application/json",csv:"text/csv;charset=utf-8",md:"text/markdown;charset=utf-8"}[ext]}),url=win.URL.createObjectURL(blob),anchor=doc.createElement("a");
    anchor.href=url;anchor.download=turbineDesignExportFilename(model,selected,ext);doc.body.append(anchor);try{anchor.click();}finally{anchor.remove();win.setTimeout(()=>win.URL.revokeObjectURL(url),1000);}
  };
  root.addEventListener("click",click);
  const cleanup=()=>{disposed=true;root.removeEventListener("click",click);if(root._turbineDesignCleanup===cleanup)delete root._turbineDesignCleanup;};root._turbineDesignCleanup=cleanup;return cleanup;
}
