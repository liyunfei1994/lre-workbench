// Reference data are inspectable; saved evidence is never filled from this catalog.
import {thermoModelDecisionState} from "./thermochemistry-model.js";
import {renderThermodynamicApplicability} from "./thermo-applicability.js";

const esc=value=>String(value??"未记录").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const list=value=>Array.isArray(value)?value:[];
const finite=value=>typeof value==="number"&&Number.isFinite(value);
const fmt=value=>finite(value)?value.toLocaleString("zh-CN",{maximumSignificantDigits:7}):"—";
const range=value=>list(value).map(fmt).join("–")||"未记录";
const link=(url,label)=>/^https:\/\//.test(url||"")?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`:esc(label);
const facts=rows=>`<dl class="pl-facts">${rows.map(([label,value])=>`<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>`;
const table=(headers,rows)=>`<div class="pl-table"><table><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
const button=(category,label)=>`<button type="button" data-property-open="${category}">${label}</button>`;
const unrecorded='<p class="pl-note">此记录未保存详细数据快照；仅展示已记录的身份与状态，不使用当前安装数据补写历史依据。</p>';
const metricNames={cp_JmolK:"标准态摩尔比热 cₚ°",h_Jmol:"标准态摩尔焓 h°",s_JmolK:"标准态摩尔熵 s°",rho_kgm3:"密度",cp_JkgK:"定压比热",h_Jkg:"比焓",s_JkgK:"比熵",mu_Pas:"动力黏度",k_WmK:"导热率",conductivity:"导热率",heat_capacity:"比热"};

export function configuredPropertyUse(inputs={}){
  const model=thermoModelDecisionState(inputs),mode=inputs.cooling_bartz_mode;
  return {model,wallUse:mode===1?"详细冷却模型使用；实际材料分区及温区见计算记录":mode===0||mode===2?"部件草案 · 本模式不求解壁面导热":"冷却模式未识别，不能推定使用情况"};
}

export function renderPropertyConfiguration(inputs={},catalog=null){
  const {model,wallUse}=configuredPropertyUse(inputs);
  const dataset=catalog?.gas_datasets?.find(d=>d.id===model.datasetId);
  return `<section class="pl-section"><h3>当前方案的物性分工</h3><p>这里说明当前输入。保存记录保留当次实际使用的数据、组成和适用性证据。</p>${facts([
    ["计算工具",model.backendLabel],
    ["物种热数据",`${dataset?.file||model.datasetId} · ${model.datasetLabel}`],
    ["燃气状态方程",model.coverageLabel],
    ["候选物种",model.speciesLabel],
    ["化学过程","预燃室／主室：压力与焓约束的化学平衡；涡轮／喷管：冻结组成。设计点不积分反应速率。"],
    ["纯推进剂","CoolProp HEOS：氧与甲烷的供给、泵和冷却剂状态；REFPROP 仅作独立校核。"],
    ["壁面热物性",`${inputs.wall_thermal_dataset_id||"未指定"} · ${wallUse}`],
  ])}<div class="pl-actions">${button("gas","查看热化学数据")}${button("fluid","查看纯流体数据")}${button("material","查看壁面数据")}<button type="button" data-help-chapter="properties">物性方法与参考态说明</button></div></section>`;
}

export function propertyRecordState(record,project){
  if(!record||record.project_id&&project?.id&&record.project_id!==project.id)return {available:false};
  const calc=record.result?.calculation||{},evidence=calc.thermodynamic_applicability||{};
  return {available:true,calc,evidence,manifest:evidence.data_manifest?.schema_version===1?evidence.data_manifest:null};
}

export function recordDatasetMatch(record,project,dataset){
  const state=propertyRecordState(record,project);
  const hash=state.evidence?.thermochemical_data?.sha256;
  return Boolean(hash&&dataset?.sha256&&hash===dataset.sha256);
}

function compositionRows(state){
  return list(state.evidence.states).map(row=>{
    const fractions=row.mass_fractions||state.calc.states?.[row.id]?.composition;
    const entries=Object.entries(fractions||{}).filter(([,v])=>finite(v)&&v>0).sort((a,b)=>b[1]-a[1]);
    return `<details><summary>${esc(row.label||row.id)} · ${fmt(row.T_K)} K · 实际组成</summary><p>质量分数；零含量物种不列出。候选物种清单与实际组成分别记录。</p>${entries.length?table(["物种","质量分数"],entries.map(([k,v])=>[k,fmt(v)])):'<p>本记录未保存可读取的组成，不能由当前模型反推。</p>'}</details>`;
  }).join("");
}

export function renderPropertyRecord(record,project){
  const state=propertyRecordState(record,project);
  if(!state.available)return '<section class="pl-section"><h3>尚未选择本方案的计算记录</h3><p>完成设计点计算后，在计算记录中选择结果；数据资料浏览不需要先运行发动机。</p></section>';
  const data=state.evidence.thermochemical_data||{},manifest=state.manifest;
  const gas=manifest?.gas_dataset,candidates=manifest?.candidate_species;
  return `<section class="pl-section"><h3>所选计算记录 · v${esc(record.revision)}</h3><p>记录标识：${esc(record.id)}。以下信息来自保存结果，与当前草稿分别解释。</p>${facts([
    ["实际热化学文件",gas?.file||data.file||"文件名未记录"],["数据集标识",data.id],["记录的计算工具版本",data.cantera_version?`Cantera ${data.cantera_version}`:null],
    ["实际燃气模型",state.evidence.gas_model?.label||state.evidence.gas_model?.id],
    ["候选物种数",data.species_count],["热数据指纹",data.sha256],
    ["壁面导热使用",manifest?.wall_thermal?.use==="evaluated"?"本次详细模型已计算；按下方材料证据判断范围":manifest?.wall_thermal?.use==="not_evaluated"||state.evidence.solid_wall_thermal?.status==="not_evaluated"?"本次未求解壁面导热；材料配置仅为部件草案":"未保存使用身份，查阅已有材料证据"],
  ])}${manifest?`<details><summary>本次数据清单、来源和候选物种</summary><p>${esc(gas?.file_description)}</p><p>${link(gas?.source_url,"热化学源文件")} · 运行时 Cantera ${esc(gas?.cantera_version)}；源文件生成时版本 ${esc(gas?.generator_version)}。</p><p>源文件 ${esc(gas?.species_count)} 种；实际候选集合：${esc(list(candidates).join("、"))}。</p>${table(["纯流体","后端／运行时版本","EOS 文献键","流体数据指纹"],list(manifest.pure_fluids).map(f=>[f.label,`${f.backend} / ${f.version}`,f.eos_references,f.data_sha256]))}<p>${esc(manifest.wall_thermal?.note)}</p><details><summary>保存的逐物种热数据与输运参数</summary><p>以下为本次计算保存的源数据快照；候选列标识本次模型使用的集合。</p>${table(["物种","本次候选","标准态热模型","分段温区 / K","原始注记"],list(gas?.species).map(s=>[s.name,list(candidates).includes(s.name)?"是":"否",s.thermo?.model,range(s.thermo?.["temperature-ranges"]),s.source_note]))}<details><summary>展开保存的系数与输运参数</summary><pre>${esc(JSON.stringify(gas?.species,null,2))}</pre></details></details></details>`:unrecorded}<div class="pl-actions"><button type="button" data-pl-download="record">导出本记录物性依据</button><button type="button" data-workspace-view="validation">进入独立热力学校核</button></div></section>${renderThermodynamicApplicability(record.result||{})}<section class="pl-section"><h3>保存节点的实际组成</h3>${compositionRows(state)||'<p>本记录未保存燃气节点。</p>'}</section>`;
}

function fingerprint(data){
  return `<details class="pl-identity"><summary>版本与数据指纹</summary>${facts([
    ["数据标识",data.id||data.dataset_id],["运行时版本",data.cantera_version||data.version||data.revision],
    ...(data.generator?[["文件生成器",data.generator],["生成时版本（不是运行时版本）",data.generator_version],["文件生成日期",data.generation_date]]:[]),
    ["SHA-256",data.sha256||data.data_sha256],
  ])}<p>${esc(data.fingerprint_basis||"指纹用于识别数据内容，不代表精度验证。")}</p></details>`;
}

function gasDetails(data,member,query){
  const species=list(data.species),current=species.find(s=>s.name===member)||species[0];
  const filtered=species.filter(s=>s.name.toLowerCase().includes(query.toLowerCase()));
  const thermo=current?.thermo||{},temperatures=thermo["temperature-ranges"]||[];
  const coefficientRows=list(thermo.data).map((coeff,i)=>[`${fmt(temperatures[i])}–${fmt(temperatures[i+1])} K`,...coeff.map(v=>String(v))]);
  return `<section class="pl-section"><h3>${esc(data.label)}</h3><p>${esc(data.thermodynamic_role)} Cantera 是执行这些模型的计算工具。</p>${facts([
    ["源文件 / 相",`${data.file} / ${data.phase}`],["来源",data.source_label],["元素范围",list(data.elements).join("、")],
    ["源数据候选物种",`${data.species_count} 种；各物种温区交集 ${range(data.common_temperature_range_K)} K`],
    ["文件中的源状态方程",`${data.gas_eos}；本方案的实际 EOS 在模型配置和保存节点中分别显示。`],
    ["输运声明",`${data.source_transport_model}；气体分子输运参数独立于 NASA 热力学多项式。稀薄气体输运不等于高压输运已校核。`],
    ["源反应数据",`${data.source_reaction_count} 条 · ${data.source_kinetics_model}。${data.kinetics_role}`],
  ])}<p>${link(data.source_url,"查阅原始热化学文件")} · ${link("https://cantera.org/3.2/reference/thermo/species-thermo.html","物种标准态热力学定义")}</p><details><summary>源文件原始说明</summary><pre>${esc(data.file_description)}</pre></details>${fingerprint(data)}</section>
  <section class="pl-section"><h3>物种热数据</h3><p>NASA7 是分段多项式形式，提供标准态 cₚ°、h°、s°。这些曲线与燃气混合物的反应平衡性质不同。</p><div class="pl-species-layout"><div><label>查找物种<input data-pl-search type="search" value="${esc(query)}" placeholder="例如 H2O、OH、CH4"></label><div class="pl-species-list" aria-label="源数据物种"><p data-pl-count>${filtered.length} / ${species.length} 种</p>${species.map(s=>`<button type="button" data-pl-species="${esc(s.name)}" aria-pressed="${s.name===current?.name}"${filtered.includes(s)?"":" hidden"}><b>${esc(s.name)}</b><span>${range(s.thermo["temperature-ranges"])} K</span></button>`).join("")}</div></div><div><h4>${esc(current?.name)} · ${esc(thermo.model)}</h4>${facts([["元素组成",Object.entries(current?.composition||{}).map(([k,v])=>`${k}: ${v}`).join("；")],["摩尔质量",`${fmt(current?.molecular_weight_kg_kmol)} kg/kmol`],["参考压力",`${fmt(current?.reference_pressure_Pa)} Pa`],["分段温区",`${range(temperatures)} K`],["物种原始注记",current?.source_note||"源文件未登记注记"]])}<details><summary>多项式系数与计算定义</summary><p>每段 a₁…a₇ 按原始数据顺序展示，温度使用 K。</p><p>cₚ°/R = a₁ + a₂T + a₃T² + a₄T³ + a₅T⁴<br>h°/(RT) = a₁ + a₂T/2 + a₃T²/3 + a₄T³/4 + a₅T⁴/5 + a₆/T<br>s°/R = a₁ ln T + a₂T + a₃T²/2 + a₄T³/3 + a₅T⁴/4 + a₇</p>${table(["温区","a₁","a₂","a₃","a₄","a₅","a₆","a₇"],coefficientRows)}</details><details><summary>该物种的输运参数</summary><p>以下为 Cantera YAML 声明单位：分子直径 Å、势阱深度 K、偶极矩 Debye、极化率 Å³、转动松弛数无量纲。输运参数不属于 NASA7 热数据。</p><pre>${esc(JSON.stringify(current?.transport,null,2))}</pre></details></div></div></section>`;
}

function fluidDetails(data){
  const l=data.limits||{};
  return `<section class="pl-section"><h3>${esc(data.label)} · CoolProp HEOS</h3><p>${esc(data.scope)}</p>${facts([
    ["状态方程",data.eos_family],["EOS 文献键",data.eos_references],
    ["动力黏度文献键",data.transport_references?.viscosity],["导热率文献键",data.transport_references?.conductivity],
    ["状态方程温压域",`${fmt(l.Tmin_K)}–${fmt(l.Tmax_K)} K；p ≤ ${fmt(l.pmax_Pa/1e6)} MPa`],
    ["临界点",`${fmt(l.Tcritical_K)} K；${fmt(l.pcritical_Pa/1e6)} MPa`],
  ])}<p>${esc(data.range_note)}</p><p>${esc(data.reference_note)}</p><p>${link(data.source_url,"查阅流体模型、文献与适用范围")}</p>${fingerprint(data)}</section>`;
}

function materialDetails(data,inputs){
  const use=configuredPropertyUse(inputs).wallUse;
  return `<section class="pl-section"><h3>${esc(data.label)}</h3><p>数据集是特定文献试样的热物性；材料牌号、制造状态与曲线版本分别登记。</p>${facts([
    ["材料 / 成分",`${data.material_id} / ${data.composition}`],["试样制造状态",data.manufacturing_state?.label],
    ["导热率数据",`${range(data.conductivity_property?.range_K)} K · ${data.conductivity_property?.representation}`],
    ["比热数据",`${range(data.heat_capacity_property?.range_K)} K · ${data.heat_capacity_property?.representation}`],
    ["当前方案选用",inputs.wall_thermal_dataset_id===data.dataset_id?use:"此数据集未被当前草稿选作默认内衬；材料分区另行核对。"],
  ])}<p>稳态壁面导热使用导热率；比热可供热惯性建模参考，不能据此宣称当前稳态计算使用了热容量。强度、疲劳和寿命需要独立数据。</p>${list(data.sources).map(s=>`<p>${esc(s.authors?.join("、"))}（${esc(s.year)}），${link(s.url,s.title)}。${esc(s.report_or_journal)}；位置：${esc(s.locations?.join("；"))}</p>`).join("")}<ul>${list(data.limitations).map(t=>`<li>${esc(t)}</li>`).join("")}</ul><details><summary>曲线系数、离散点与原始出处</summary><pre>${esc(JSON.stringify({conductivity:data.conductivity_property,heat_capacity:data.heat_capacity_property},null,2))}</pre></details>${fingerprint(data)}<button type="button" data-setup-group="wall_thermal">设置内衬材料与制造状态</button></section>`;
}

export function curveSegments(rows,key){
  const segments=[];let segment=[],phase;
  for(const row of rows){
    if(row.status!=="available"||!finite(row.T_K)||!finite(row[key])||(phase&&row.phase&&row.phase!==phase)){
      if(segment.length)segments.push(segment);segment=[];
    }
    if(row.status==="available"&&finite(row.T_K)&&finite(row[key]))segment.push(row);
    phase=row.phase;
  }
  if(segment.length)segments.push(segment);return segments;
}

export function renderPropertyCurve(curve,key,markers=[]){
  const segments=curveSegments(list(curve.rows),key),rows=segments.flat(),unit=curve.units?.[key];
  if(!rows.length)return '<p class="pl-note">没有可绘制的有效点，请查看逐点状态。</p>';
  const xs=rows.map(r=>r.T_K),ys=rows.map(r=>r[key]),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
  const span=ymax-ymin||Math.max(1,Math.abs(ymax)*.05),lo=ymin-span*.05,hi=ymax+span*.05;
  const x=t=>80+(t-xmin)/(xmax-xmin||1)*650,y=v=>270-(v-lo)/(hi-lo)*220;
  const ticks=Array.from({length:5},(_,i)=>i/4);
  return `<figure class="pl-plot"><figcaption>${esc(metricNames[key]||key)} / ${esc(unit)}</figcaption><svg viewBox="0 0 800 330" role="img" aria-label="${esc(metricNames[key]||key)}随温度变化的资料曲线">${ticks.map(t=>`<line class="pl-grid" x1="80" x2="730" y1="${y(lo+t*(hi-lo))}" y2="${y(lo+t*(hi-lo))}"/><text x="72" y="${y(lo+t*(hi-lo))+4}" text-anchor="end">${esc(fmt(lo+t*(hi-lo)))}</text><text x="${x(xmin+t*(xmax-xmin))}" y="293" text-anchor="middle">${esc(fmt(xmin+t*(xmax-xmin)))}</text>`).join("")}<text x="405" y="320" text-anchor="middle">温度 / K</text>${markers.filter(m=>finite(m.T_K)&&m.T_K>=xmin&&m.T_K<=xmax).map(m=>`<line class="pl-marker" x1="${x(m.T_K)}" x2="${x(m.T_K)}" y1="50" y2="270"><title>${esc(m.label)}：${fmt(m.T_K)} K（仅温度位置）</title></line>`).join("")}${segments.map(segment=>segment.length===1?`<circle class="pl-point" cx="${x(segment[0].T_K)}" cy="${y(segment[0][key])}" r="3"/>`:`<polyline class="pl-line" points="${segment.map(r=>`${x(r.T_K)},${y(r[key])}`).join(" ")}"/>`).join("")}</svg><p>${esc(curve.definition)}</p></figure>`;
}

function download(name,text,type){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

export function mountPropertyLibrary(host,{api,getContext,initialTab="data",initialCategory="gas"}={}){
  let tab=initialTab,category=initialCategory,catalog=null,disposed=false,sequence=0,curve=null,metric="",query="";
  const selected={gas:"",fluid:"Methane",material:""},members={gas:"H2O",material:"conductivity"};
  const context=()=>getContext?.()||{};
  const contextKey=()=>{const c=context();return JSON.stringify([c.project?.id,c.project?.revision,c.record?.id,c.draft]);};
  let paintedContext=contextKey();
  const datasets=()=>category==="gas"?list(catalog?.gas_datasets):category==="fluid"?list(catalog?.pure_fluids):list(catalog?.wall_datasets);
  const selectedData=()=>datasets().find(d=>(d.id||d.dataset_id)===selected[category])||datasets()[0];
  const currentMember=()=>category==="gas"?members.gas:category==="material"?members.material:"";
  function curveDefaults(data){
    if(category==="gas"){const t=data.species.find(s=>s.name===members.gas)?.thermo["temperature-ranges"]||[300,3000];return [t[0],t.at(-1)];}
    if(category==="fluid")return [data.limits.Tmin_K,Math.min(600,data.limits.Tmax_K)];
    return data[members.material==="conductivity"?"conductivity_property":"heat_capacity_property"].range_K;
  }
  function paint(){
    if(disposed)return;
    const {draft={},record,project}=context();
    host.innerHTML=`<section class="property-library" aria-label="热物性与材料数据"><nav class="pl-tabs" aria-label="物性资料视图">${[["data","数据集浏览"],["configuration","当前方案配置"],["record","所选计算记录"]].map(([id,label])=>`<button type="button" data-pl-tab="${id}" aria-current="${tab===id?"page":"false"}">${label}</button>`).join("")}</nav><div data-pl-body></div></section>`;
    const body=host.querySelector("[data-pl-body]");
    if(tab==="record"){body.innerHTML=renderPropertyRecord(record,project);return;}
    if(tab==="configuration"){body.innerHTML=renderPropertyConfiguration(draft,catalog);return;}
    if(!catalog){body.innerHTML='<p role="status">正在读取已安装的数据文件与模型目录…</p>';return;}
    if(!selected.gas)selected.gas=draft.methane_thermo_dataset||"gri30";
    if(!selected.material)selected.material=draft.wall_thermal_dataset_id||catalog.wall_datasets?.[0]?.dataset_id;
    const data=selectedData();selected[category]=data?.id||data?.dataset_id;
    if(!data){body.innerHTML='<p>此分类未登记数据。</p>';return;}
    if(category==="gas"&&!data.species.some(s=>s.name===members.gas))members.gas=data.species[0]?.name;
    const [start,end]=curveDefaults(data);
    body.innerHTML=`<div class="pl-selectors"><label>数据类别<select data-pl-category>${[["gas","燃气热化学"],["fluid","纯推进剂"],["material","壁面材料"]].map(([id,label])=>`<option value="${id}"${category===id?" selected":""}>${label}</option>`).join("")}</select></label><label>已安装的数据集<select data-pl-dataset>${datasets().map(d=>`<option value="${esc(d.id||d.dataset_id)}"${d===data?" selected":""}>${esc(d.label)}</option>`).join("")}</select></label></div><p class="pl-note">当前浏览安装数据，切换条目仅用于查阅。方案模型在设置页修改；历史结果依据见“所选计算记录”。</p>${category==="gas"?gasDetails(data,members.gas,query):category==="fluid"?fluidDetails(data):materialDetails(data,draft)}<section class="pl-section"><h3>性质曲线</h3><form data-pl-curve-form class="pl-curve-form">${category==="material"?`<label>性质<select name="member"><option value="conductivity"${members.material==="conductivity"?" selected":""}>导热率</option><option value="heat_capacity"${members.material==="heat_capacity"?" selected":""}>比热</option></select></label>`:""}<label>起始温度 / K<input name="start_K" type="number" step="any" min="${start}" max="${category==="fluid"?data.limits.Tmax_K:end}" value="${start}" required></label><label>终止温度 / K<input name="end_K" type="number" step="any" min="${start}" max="${category==="fluid"?data.limits.Tmax_K:end}" value="${end}" required></label>${category==="fluid"?'<label>恒定绝对压力 / MPa<input name="pressure_MPa" type="number" min="0.000001" step="any" value="5" required></label>':""}<button type="submit">绘制资料曲线</button></form><p>按该性质的声明温区查询，越界请求不外推。缺失点和相态变化分别显示，不跨缺项连线。</p><p data-pl-error class="error" role="alert"></p><div data-pl-curve></div></section><div class="pl-actions"><button type="button" data-pl-download="dataset">导出所浏览数据资料</button><button type="button" data-help-chapter="properties">阅读物性方法说明</button></div>`;
  }
  function paintCurve(){
    const target=host.querySelector("[data-pl-curve]");if(!target||!curve)return;
    const keys=Object.keys(curve.units),{record,project}=context(),data=selectedData();
    if(!keys.includes(metric))metric=keys[0];
    const match=category==="gas"&&recordDatasetMatch(record,project,data);
    const markers=match?list(propertyRecordState(record,project).evidence?.states):[];
    target.innerHTML=`<label>显示性质<select data-pl-metric>${keys.map(k=>`<option value="${k}"${metric===k?" selected":""}>${esc(metricNames[k]||k)} / ${esc(curve.units[k])}</option>`).join("")}</select></label>${renderPropertyCurve(curve,metric,markers)}${category==="gas"?`<p>${match?"虚线仅标示所选记录的节点温度，数据指纹相同；并非该混合物的性质点。":"所选记录缺少相同的数据指纹，未叠加历史节点。"}</p>`:""}<details><summary>逐点数值与查询状态（${curve.rows.length} 点）</summary>${table(["温度 / K",...keys.map(k=>`${metricNames[k]||k} / ${curve.units[k]}`),"相态／状态"],curve.rows.map(r=>[fmt(r.T_K),...keys.map(k=>fmt(r[k])),r.reason||r.phase||r.status]))}</details><button type="button" data-pl-download="curve">导出曲线 CSV</button>`;
  }
  const click=event=>{
    const scope=event.target.closest("[data-pl-tab]");if(scope){tab=scope.dataset.plTab;sequence++;curve=null;paint();return;}
    const species=event.target.closest("[data-pl-species]");if(species){members.gas=species.dataset.plSpecies;sequence++;curve=null;paint();return;}
    const save=event.target.closest("[data-pl-download]");if(!save)return;
    if(save.dataset.plDownload==="curve"&&curve){const keys=["T_K",...Object.keys(curve.units),"phase","status","reason"];const quote=v=>`"${String(v??"").replaceAll('"','""')}"`;download("property-curve.csv","\uFEFF"+keys.join(",")+"\n"+curve.rows.map(r=>keys.map(k=>quote(r[k])).join(",")).join("\n"),"text/csv;charset=utf-8");}
    else if(save.dataset.plDownload==="record"){const {record,project}=context(),s=propertyRecordState(record,project);if(s.available)download("record-property-evidence.json",JSON.stringify({record_id:record.id,revision:record.revision,evidence:s.evidence},null,2),"application/json");}
    else if(save.dataset.plDownload==="dataset")download("property-dataset.json",JSON.stringify(selectedData(),null,2),"application/json");
  };
  const change=event=>{
    if(event.target.matches("[data-pl-metric]")){metric=event.target.value;paintCurve();return;}
    if(event.target.matches("[data-pl-category]"))category=event.target.value;
    else if(event.target.matches("[data-pl-dataset]"))selected[category]=event.target.value;
    else if(event.target.name==="member")members.material=event.target.value;
    else return;
    sequence++;curve=null;query="";paint();
  };
  const input=event=>{if(!event.target.matches("[data-pl-search]"))return;query=event.target.value;let count=0;host.querySelectorAll("[data-pl-species]").forEach(n=>{n.hidden=!n.dataset.plSpecies.toLowerCase().includes(query.toLowerCase());if(!n.hidden)count++;});host.querySelector("[data-pl-count]").textContent=`${count} / ${selectedData().species.length} 种`;};
  const submit=async event=>{
    if(!event.target.matches("[data-pl-curve-form]"))return;event.preventDefault();
    const token=++sequence,data=selectedData(),values=Object.fromEntries(new FormData(event.target)),error=host.querySelector("[data-pl-error]");
    const payload={kind:category==="gas"?"species":category,dataset:data.id||data.dataset_id,member:currentMember(),start_K:Number(values.start_K),end_K:Number(values.end_K),points:61,expected_sha256:data.sha256||data.data_sha256};
    if(category==="fluid")payload.pressure_Pa=Number(values.pressure_MPa)*1e6;
    error.textContent="";if(event.submitter)event.submitter.disabled=true;host.querySelector("[data-pl-curve]").textContent="正在查询资料曲线…";
    try{const out=await api("/property-library/curve","POST",payload);if(disposed||token!==sequence)return;curve=out;paintCurve();}
    catch(exc){if(!disposed&&token===sequence){error.textContent=exc.message;host.querySelector("[data-pl-curve]").textContent="";curve=null;}}
    finally{if(!disposed&&token===sequence&&event.submitter)event.submitter.disabled=false;}
  };
  host.addEventListener("click",click);host.addEventListener("change",change);host.addEventListener("input",input);host.addEventListener("submit",submit);
  paint();
  api("/property-library").then(out=>{if(disposed)return;catalog=out;paint();}).catch(error=>{if(!disposed&&tab==="data")host.querySelector("[data-pl-body]").innerHTML=`<p class="error" role="alert">${esc(error.message)}</p><button type="button" data-property-open="gas">重新读取资料</button>`;});
  return {refresh(){const next=contextKey();if(next!==paintedContext){paintedContext=next;sequence++;curve=null;paint();}},dispose(){disposed=true;sequence++;host.removeEventListener("click",click);host.removeEventListener("change",change);host.removeEventListener("input",input);host.removeEventListener("submit",submit);}};
}
