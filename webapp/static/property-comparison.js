/* Pure property report. Only saved backend values and saved differences are displayed. */
const finite=value=>typeof value==="number"&&Number.isFinite(value);
const escape=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number=value=>!finite(value)?"—":value!==0&&(Math.abs(value)<1e-4||Math.abs(value)>=1e7)?value.toExponential(4):value.toLocaleString("zh-CN",{maximumSignificantDigits:7});
const signed=value=>finite(value)?`${value>0?"+":""}${number(value)}`:"—";
const PHASE={liquid:"液相",gas:"气相",twophase:"两相",supercritical:"超临界",supercritical_gas:"超临界气体",supercritical_liquid:"超临界液体",critical_point:"临界点",unknown:"未确定",not_imposed:"未确定"};
const phase=value=>PHASE[value]||value||"未记录";
const fluidName=value=>({Oxygen:"氧",Methane:"甲烷","n-Dodecane":"正十二烷"}[value]||value);
const STATUS={pass:"可计算",partial:"部分可用",fail:"不可计算",unavailable:"不可用",not_provided:"未请求"};
const unusable=new Set(["fail","error","unavailable","not_provided"]);
export const PROPERTY_COMPARISON_METRICS=Object.freeze([
  {key:"density_kg_m3",label:"密度",symbol:"ρ",unit:"kg/m³",factor:1},
  {key:"cp_J_kg_K",label:"定压比热",symbol:"cp",unit:"kJ/(kg·K)",factor:.001},
  {key:"viscosity_Pa_s",label:"动力黏度",symbol:"μ",unit:"μPa·s",factor:1e6},
  {key:"conductivity_W_m_K",label:"导热系数",symbol:"λ",unit:"mW/(m·K)",factor:1e3},
  {key:"enthalpy_delta_J_kg",label:"参考焓差",symbol:"h − h₀",unit:"kJ/kg",factor:.001,reference:true},
  {key:"entropy_delta_J_kg_K",label:"参考熵差",symbol:"s − s₀",unit:"kJ/(kg·K)",factor:.001,reference:true},
]);
const EXTRA_METRICS=[
  {key:"cv_J_kg_K",label:"定容比热",symbol:"cv",unit:"kJ/(kg·K)",factor:.001},
  {key:"speed_sound_m_s",label:"声速",symbol:"a",unit:"m/s",factor:1},
];
const RAW_METRICS=[
  {key:"enthalpy_J_kg",label:"原始焓（各库基准）",symbol:"h",unit:"kJ/kg",factor:.001,raw:true},
  {key:"entropy_J_kg_K",label:"原始熵（各库基准）",symbol:"s",unit:"kJ/(kg·K)",factor:.001,raw:true},
];
const allMetrics=[...PROPERTY_COMPARISON_METRICS,...EXTRA_METRICS,...RAW_METRICS];

function backendValue(row,backend,key){
  const source=backend==="heos"?row:row?.refprop,value=source?.properties_si?.[key];
  return source&&!unusable.has(backend==="heos"?(source.heos_status||source.status):source.status)&&finite(value)?value:null;
}
function matchingReference(row){
  const a=row?.reference_state,b=row?.refprop?.reference_state;
  return a&&b&&finite(a.temperature_K)&&finite(a.pressure_Pa)&&a.temperature_K===b.temperature_K&&a.pressure_Pa===b.pressure_Pa;
}
function phaseMismatch(row){return row?.comparison?.phase_agreement===false||!!(row?.phase&&row?.refprop?.phase&&row.phase!==row.refprop.phase);}
function metricPair(row,metric){
  const heos=backendValue(row,"heos",metric.key),refprop=backendValue(row,"refprop",metric.key);
  const excluded=row?.comparison?.excluded_properties?.[metric.key];
  const blocked=metric.raw||phaseMismatch(row)||["not_comparable","unavailable","not_requested"].includes(row?.comparison?.status)||!!excluded||
    (metric.reference&&!matchingReference(row));
  const saved=row?.refprop?.differences?.[metric.key],paired=heos!==null&&refprop!==null;
  return {heos:heos===null?null:heos*metric.factor,refprop:refprop===null?null:refprop*metric.factor,
    absolute:paired&&!blocked&&finite(saved?.heos_minus_refprop)?saved.heos_minus_refprop*metric.factor:null,
    relative:paired&&!blocked&&finite(saved?.relative_difference)?saved.relative_difference*100:null,
    paired,blocked,reason:metric.raw?"各库原始基准；不直接相减":excluded||
      (phaseMismatch(row)?"两后端相态不同，暂不比较差值":metric.reference&&!matchingReference(row)?"共同参考状态缺失或不一致":!paired?"缺少有效配对":!saved?"历史记录未保存差值":"")};
}
function referenceText(row){
  const ref=row?.reference_state;
  return ref&&finite(ref.temperature_K)&&finite(ref.pressure_Pa)?`${number(ref.temperature_K)} K / ${number(ref.pressure_Pa/1e6)} MPa`:"未记录";
}
function referenceSummary(points,backend){
  const sources=points.map(row=>backend==="heos"?row:row.refprop).filter(source=>{
    const ref=source?.reference_state;
    return ref&&finite(ref.temperature_K)&&finite(ref.pressure_Pa)&&ref.temperature_K>0&&ref.pressure_Pa>0;
  });
  if(!sources.length)return `未记录（0/${points.length} 点）`;
  const unique=new Set(sources.map(source=>`${source.reference_state.temperature_K}/${source.reference_state.pressure_Pa}`));
  return unique.size===1?`${referenceText(sources[0])}（${sources.length}/${points.length} 点记录）`:
    `多组参考状态（${unique.size} 种；${sources.length}/${points.length} 点记录，详见表格）`;
}
function stateText(row){return `${number(row.temperature_K)} K / ${number(finite(row.pressure_Pa)?row.pressure_Pa/1e6:null)} MPa`;}

/** Select one fluid at a time; never draw a trend through unrelated fluids or varying T and P. */
export function preparePropertyComparisonData(section,selection={}){
  const rows=Array.isArray(section?.checks)?section.checks.filter(row=>row&&typeof row==="object").map((row,index)=>({...row,reportIndex:index+1})):[];
  const groups=[...new Set(rows.map(row=>String(row.fluid??"未记录工质")))];
  const group=groups.includes(selection.group)?selection.group:groups[0];
  let points=rows.filter(row=>String(row.fluid??"未记录工质")===group);
  const temperatures=[...new Set(points.map(p=>p.temperature_K))],pressures=[...new Set(points.map(p=>p.pressure_Pa))];
  const numeric=points.every(p=>finite(p.temperature_K)&&finite(p.pressure_Pa));
  const axis=numeric&&points.length>1&&pressures.length===1&&temperatures.length>1?"temperature":
    numeric&&points.length>1&&temperatures.length===1&&pressures.length>1?"pressure":"state";
  const coordinate=p=>axis==="temperature"?p.temperature_K:axis==="pressure"?p.pressure_Pa/1e6:p.reportIndex;
  if(axis!=="state")points=[...points].sort((a,b)=>coordinate(a)-coordinate(b));
  points=points.map(row=>({...row,x:coordinate(row),duplicate:axis!=="state"&&points.filter(p=>coordinate(p)===coordinate(row)).length>1}));
  const panels=PROPERTY_COMPARISON_METRICS.map(metric=>({...metric,values:points.map(row=>({...metricPair(row,metric),row,x:row.x,duplicate:row.duplicate}))}));
  const differences=rows.flatMap(row=>[...PROPERTY_COMPARISON_METRICS,...EXTRA_METRICS].map(metric=>({...metricPair(row,metric),metric,row}))).filter(p=>finite(p.relative));
  const largest=differences.reduce((best,p)=>!best||Math.abs(p.relative)>Math.abs(best.relative)?p:best,null);
  const pairedCount=rows.filter(row=>allMetrics.some(metric=>!metric.raw&&metricPair(row,metric).paired)).length;
  const comparableCount=rows.filter(row=>allMetrics.some(metric=>!metric.raw&&finite(metricPair(row,metric).absolute))).length;
  const mismatchCount=rows.filter(phaseMismatch).length;
  return {rows,groups,group,points,panels,axis,single:points.length===1,largest,pairedCount,comparableCount,mismatchCount,
    axisLabel:axis==="temperature"?"温度 T / K":axis==="pressure"?"压力 P / MPa":"独立状态点编号",
    condition:axis==="temperature"?`等压扫描 · P = ${number(points[0].pressure_Pa/1e6)} MPa`:
      axis==="pressure"?`等温扫描 · T = ${number(points[0].temperature_K)} K`:points.length===1?stateText(points[0]):"独立状态点 · 温度和压力同时变化，不连接成趋势线"};
}

function domain(values,symmetric=false){
  if(!values.length)return symmetric?[-1,1]:[0,1];
  const min=Math.min(...values),max=Math.max(...values);
  if(symmetric){const span=Math.max(Math.abs(min),Math.abs(max));return span?[-span*1.2,span*1.2]:[-.1,.1];}
  const pad=(max-min||Math.abs(max)*.04||1)*.12;return [min-pad,max+pad];
}
function tick(value,span){
  if(value===0)return "0";
  if(Math.abs(value)>=1e6||Math.abs(value)<.001)return value.toExponential(2);
  return value.toLocaleString("en-US",{maximumFractionDigits:Math.min(7,Math.max(0,2-Math.floor(Math.log10(Math.abs(span)||1))))});
}
function paths(values,backend){
  const output=[];let current=[],lastPhase;
  for(const point of values){
    const currentPhase=backend==="heos"?point.row.phase:backend==="refprop"?point.row.refprop?.phase:`${point.row.phase}/${point.row.refprop?.phase}`;
    if(!finite(point[backend])||point.duplicate){if(current.length)output.push(current);current=[];lastPhase=null;continue;}
    if(current.length&&currentPhase!==lastPhase){output.push(current);current=[];}
    current.push(point);lastPhase=currentPhase;
  }
  if(current.length)output.push(current);return output;
}
function marker(backend,x,y,title,attrs=""){
  return backend==="refprop"?`<rect class="pc-marker pc-refprop" x="${x-3}" y="${y-3}" width="6" height="6" ${attrs}><title>${escape(title)}</title></rect>`:
    `<circle class="pc-marker ${backend==="relative"?"pc-relative":"pc-heos"}" cx="${x}" cy="${y}" r="3.2" ${attrs}><title>${escape(title)}</title></circle>`;
}
function trendSvg(panel,data,difference=false){
  const width=600,height=difference?116:254,left=88,right=24,top=23,bottom=difference?30:43;
  const w=width-left-right,h=height-top-bottom,values=panel.values.flatMap(p=>difference?[p.relative]:[p.heos,p.refprop]).filter(finite);
  let [lo,hi]=domain(values,difference);
  if(!difference&&!panel.reference&&values.length&&values.every(value=>value>=0))lo=Math.max(0,lo);
  const xs=data.points.map(p=>p.x),xmin=Math.min(...xs),xmax=Math.max(...xs);
  const x=value=>left+(xmax===xmin?.5:(value-xmin)/(xmax-xmin))*w,y=value=>top+(hi-value)/(hi-lo)*h;
  const xticks=xs.length<=8?[...new Set(xs)]:Array.from({length:6},(_,i)=>xmin+(xmax-xmin)*i/5);
  const yticks=Array.from({length:difference?3:5},(_,i)=>lo+(hi-lo)*i/(difference?2:4));
  const axes=yticks.map(v=>`<line class="pc-grid" x1="${left}" x2="${width-right}" y1="${y(v)}" y2="${y(v)}"/><text class="pc-tick" x="${left-10}" y="${y(v)+4}" text-anchor="end">${escape(tick(v,hi-lo))}</text>`).join("")+
    xticks.map(v=>`<line class="pc-grid" x1="${x(v)}" x2="${x(v)}" y1="${top}" y2="${height-bottom}"/><text class="pc-tick" x="${x(v)}" y="${height-bottom+18}" text-anchor="middle">${escape(tick(v,xmax-xmin))}</text>`).join("")+
    `<line class="pc-axis" x1="${left}" x2="${left}" y1="${top}" y2="${height-bottom}"/><line class="pc-axis" x1="${left}" x2="${width-right}" y1="${height-bottom}" y2="${height-bottom}"/>`+
    (difference?`<line class="pc-zero" x1="${left}" x2="${width-right}" y1="${y(0)}" y2="${y(0)}"/><text class="pc-unit" x="${left}" y="13">相对差 / %</text>`:
      `<text class="pc-unit" x="${left}" y="13">${escape(panel.symbol)} / ${escape(panel.unit)}</text><text class="pc-unit" x="${left+w/2}" y="${height-3}" text-anchor="middle">${escape(data.axisLabel)}</text>`);
  const series=(difference?["relative"]:["heos","refprop"]).map(backend=>{
    const segments=data.axis==="state"?"":paths(panel.values,backend).filter(segment=>segment.length>1).map(segment=>
      `<polyline class="pc-series pc-${backend}" data-backend="${backend}" points="${segment.map(p=>`${x(p.x)},${y(p[backend])}`).join(" ")}"/>`).join("");
    return segments+panel.values.filter(p=>finite(p[backend])).map(p=>marker(backend,x(p.x),y(p[backend]),
      `${backend==="relative"?"HEOS − REFPROP":backend==="heos"?"CoolProp HEOS":"REFPROP"} · ${stateText(p.row)} · ${number(p[backend])} ${difference?"%":panel.unit}`,
      `data-backend="${backend}" data-coordinate="${p.x}" data-value="${p[backend]}"`)).join("");
  }).join("");
  const empty=values.length?"":`<text class="pc-empty-svg" x="${left+w/2}" y="${top+h/2}" text-anchor="middle">${difference?"没有可比较的已保存差值":"没有可用数值"}</text>`;
  const label=`${panel.label}${difference?"相对差":"对比"}，${data.axisLabel}`;
  return `<svg xmlns="http://www.w3.org/2000/svg" class="pc-plot${difference?" pc-difference":""}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escape(label)}"><title>${escape(label)}</title>${axes}${series}${empty}</svg>`;
}
function singleSvg(panel){
  const p=panel.values[0],width=600,height=170,left=102,right=172,plotWidth=width-left-right;
  const vals=[p.heos,p.refprop].filter(finite),lo=Math.min(0,...vals),hi=Math.max(0,...vals),span=hi-lo||1;
  const x=value=>left+(value-lo)/span*plotWidth;
  const rows=[{backend:"heos",label:"HEOS",value:p.heos,y:43},{backend:"refprop",label:"REFPROP",value:p.refprop,y:90}];
  const bars=rows.map(row=>`<text class="pc-unit" x="16" y="${row.y+5}">${row.label}</text>`+(finite(row.value)?
    `<rect class="pc-bar pc-${row.backend}" x="${Math.min(x(0),x(row.value))}" y="${row.y-11}" width="${Math.max(.8,Math.abs(x(row.value)-x(0)))}" height="20"><title>${row.label}: ${escape(number(row.value))} ${escape(panel.unit)}</title></rect>`:"")+
    `<text class="pc-value" x="${width-16}" y="${row.y+5}" text-anchor="end">${escape(number(row.value))}</text>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" class="pc-plot pc-single-plot" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escape(panel.label)}单点数值对比"><title>${escape(panel.label)} · 两后端同一零基准刻度</title><line class="pc-axis" x1="${x(0)}" x2="${x(0)}" y1="22" y2="109"/>${bars}<text class="pc-unit" x="16" y="138">差值 ${escape(signed(p.absolute))} ${escape(panel.unit)}</text><text class="pc-delta-value" x="${width-16}" y="138" text-anchor="end">相对差 ${escape(signed(p.relative))} %</text><text class="pc-unit" x="16" y="158">${escape(p.reason||"差值来自保存记录；柱长采用共同零基准")}</text></svg>`;
}
function legend(){return `<div class="pc-legend"><span><svg viewBox="0 0 35 14" width="35" height="14" aria-hidden="true"><line class="pc-series pc-heos" x1="1" x2="34" y1="7" y2="7"/><circle class="pc-marker pc-heos" cx="17" cy="7" r="3"/></svg>CoolProp HEOS · 实线圆点</span><span><svg viewBox="0 0 35 14" width="35" height="14" aria-hidden="true"><line class="pc-series pc-refprop" x1="1" x2="34" y1="7" y2="7"/><rect class="pc-marker pc-refprop" x="14" y="4" width="6" height="6"/></svg>REFPROP · 虚线方点</span></div>`;}

function comparisonSummary(row){
  if(phaseMismatch(row))return "相态不一致，差值不作同状态比较";
  return row.comparison?.reason||row.refprop?.reason||row.reason||"请结合各项物性检查结果判断。";
}
function detailsTable(data){
  const optional=EXTRA_METRICS.filter(metric=>data.points.some(p=>finite(p.properties_si?.[metric.key])||finite(p.refprop?.properties_si?.[metric.key])));
  const metrics=[...PROPERTY_COMPARISON_METRICS,...optional,...RAW_METRICS];
  return `<details class="pc-details pc-data" open><summary>逐点完整数值 · HEOS / REFPROP / 差值 / 相对差</summary><div class="pc-table-wrap"><table class="pc-table"><thead><tr><th>物性</th><th>单位</th><th>CoolProp HEOS</th><th>REFPROP</th><th>差值 HEOS − REFPROP</th><th>相对差 / %</th><th>口径与可用性</th></tr></thead>${data.points.map(row=>`<tbody><tr class="pc-state-row"><th colspan="7">#${row.reportIndex} · ${escape(fluidName(row.fluid))} · ${escape(stateText(row))} <span>HEOS ${escape(phase(row.phase))} / REFPROP ${escape(phase(row.refprop?.phase))}</span></th></tr>${metrics.map(metric=>{
    const p=metricPair(row,metric);return `<tr><th>${escape(metric.label)} <span>${escape(metric.symbol)}</span></th><td>${escape(metric.unit)}</td><td>${escape(number(p.heos))}</td><td>${escape(number(p.refprop))}</td><td>${escape(signed(p.absolute))}</td><td>${escape(signed(p.relative))}</td><td>${escape(p.reason||"已保存的同点差值")}</td></tr>`;
  }).join("")}</tbody>`).join("")}</table></div><p class="pc-foot">原始 h、s 使用各库基准，仅列值；用于对照的是 h − h₀、s − s₀。缺失值显示 —，不替换为零。</p></details>`;
}
function limitText(backend){
  const l=backend?.limits;if(!l)return "范围未记录";
  return `T ${number(l.temperature_min_K)}–${number(l.temperature_max_K)} K；P ≤ ${number(finite(l.pressure_max_Pa)?l.pressure_max_Pa/1e6:null)} MPa`;
}
function metadataText(value){
  if(value==null)return "未记录";
  const labels={backend_name:"物性后端",fluid_names:"工质名称",cas:"CAS 编号",CAS:"CAS 编号",eos_reference:"状态方程文献",viscosity_reference:"黏度模型文献",conductivity_reference:"导热模型文献",fluid_definition_sha256:"流体定义 SHA-256",fluid_definition_source:"流体定义来源",melting_line_reference:"熔化线文献"};
  return typeof value==="object"?Object.entries(value).map(([k,v])=>`${labels[k]||k}: ${Array.isArray(v)?v.join("、"):typeof v==="object"?JSON.stringify(v):String(v)}`).join("；")||"未记录":String(value);
}
function meltingText(backend){
  const value=backend?.melting_boundary;
  if(!value)return "未记录；未证明已排除固相";
  const status={pass:"处于所查熔化线以上",fail:"不在所查稳定流体域",unavailable:"熔化线未核实"}[value.status]||"熔化线未核实";
  return `${status}；T熔化 ${number(value.temperature_K)} K；T − T熔化 ${signed(value.margin_K)} K。${value.reason||""}`;
}
function transportText(backend){
  const value=backend?.transport_applicability;
  return value ? `${value.status==="not_assessed"?"适用性未评估":value.status||"未记录"}；${value.reason||""}` : "未记录；EOS 温压范围不能替代输运关联式适用范围";
}
function applicabilityRows(row){
  return [["固液边界",meltingText(row),meltingText(row.refprop)],
    ["输运模型",transportText(row),transportText(row.refprop)],
    ["范围口径",row.range_scope||"未记录",row.refprop?.range_scope||"未记录"]]
    .map(values=>`<tr><th>${escape(values[0])}</th><td>${escape(values[1])}</td><td>${escape(values[2])}</td></tr>`).join("");
}
function criticalText(value){
  if(!value||(!finite(value.temperature_K)&&!finite(value.pressure_Pa)))return "未记录";
  return `临界温度 ${number(value.temperature_K)} K；临界压力 ${number(finite(value.pressure_Pa)?value.pressure_Pa/1e6:null)} MPa`;
}
function stateDetails(data){
  return `<details class="pc-details"><summary>相态、适用范围与参考状态 · ${data.points.length} 个状态点</summary><div class="pc-state-grid">${data.points.map(row=>
    `<article class="pc-state"><h4>#${row.reportIndex} · ${escape(stateText(row))}</h4><p class="${phaseMismatch(row)?"pc-warning":""}">${escape(comparisonSummary(row))}</p><table><thead><tr><th>项目</th><th>HEOS</th><th>REFPROP</th></tr></thead><tbody><tr><th>计算状态</th><td>${escape(STATUS[row.heos_status||row.status]||row.heos_status||row.status||"未记录")}</td><td>${escape(STATUS[row.refprop?.status]||row.refprop?.status||"未请求")}</td></tr><tr><th>相态</th><td>${escape(phase(row.phase))}</td><td>${escape(phase(row.refprop?.phase))}</td></tr><tr><th>温压范围</th><td>${escape(limitText(row))}</td><td>${escape(limitText(row.refprop))}</td></tr>${applicabilityRows(row)}<tr><th>焓熵参考点</th><td>${escape(referenceText(row))}</td><td>${escape(referenceText(row.refprop))}</td></tr><tr><th>临界状态</th><td>${escape(criticalText(row.critical_state))}</td><td>${escape(criticalText(row.refprop?.critical_state))}</td></tr><tr><th>模型信息</th><td>${escape(metadataText(row.model_metadata))}</td><td>${escape(metadataText(row.refprop?.model_metadata))}</td></tr><tr><th>诊断</th><td>${escape(row.reason||"—")}</td><td>${escape(row.refprop?.reason||"—")}</td></tr></tbody></table></article>`).join("")}</div></details>`;
}
function visibleDiagnostics(data){
  const failed=new Set(["fail","error","unavailable"]);
  const rows=data.rows.map(row=>({row,issues:[
    failed.has(row.heos_status||row.status)?`HEOS：${row.reason||"该工况不可计算"}`:null,
    failed.has(row.refprop?.status)?`REFPROP：${row.refprop.reason||"该工况不可计算"}`:null,
  ].filter(Boolean)})).filter(item=>item.issues.length);
  if(!rows.length)return "";
  return `<div class="pc-diagnostic-strip" role="status"><strong>${rows.length} 个状态点包含不可用结果</strong><ul>${rows.slice(0,3).map(({row,issues})=>`<li>#${row.reportIndex} · ${escape(row.fluid)} · ${escape(stateText(row))} — ${escape(issues.join("；"))}</li>`).join("")}</ul>${rows.length>3?`<p>其余 ${rows.length-3} 个点的原因见下方“相态、适用范围与参考状态”。</p>`:""}</div>`;
}
export function renderPropertyComparison(section,selection={}){
  const data=preparePropertyComparisonData(section,selection);
  if(!data.rows.length)return '<div class="pc-empty">尚无已保存的真实流体校核结果。</div>';
  const largest=data.largest;
  const summary=`<div class="pc-summary"><div><span>双后端数值</span><strong>${data.pairedCount} / ${data.rows.length}</strong><small>状态点具有至少一项配对物性</small></div><div><span>可比较差值</span><strong>${data.comparableCount} / ${data.rows.length}</strong><small>来自记录保存的同条件差值</small></div><div><span>最大 |相对差| · 全部工质</span><strong>${largest?escape(number(Math.abs(largest.relative)))+" %":"—"}</strong><small>${largest?escape(`${fluidName(largest.row.fluid)} · ${largest.metric.label} · #${largest.row.reportIndex}`):"没有有效配对差值"}</small></div><div><span>相态不一致</span><strong>${data.mismatchCount}</strong><small>计算可用性与精度验证分别判断</small></div></div>`;
  const selectionControl=data.groups.length>1?`<label class="pc-group-label">图表工质<select data-property-group>${data.groups.map(group=>`<option value="${escape(group)}"${group===data.group?" selected":""}>${escape(fluidName(group))} · ${escape(group)}</option>`).join("")}</select></label>`:"";
  const toolbar=`<div class="pc-toolbar"><div><h3>真实流体物性 · HEOS × REFPROP</h3><p>${escape(fluidName(data.group))} · ${escape(data.condition)}</p></div><div class="pc-export"><button type="button" data-validation-action="export-property-png">导出 PNG</button><button type="button" data-validation-action="export-property-svg">导出 SVG</button><button type="button" data-validation-action="export-property-csv">导出全部 CSV</button></div></div>`;
  const panels=`<div class="pc-chart-grid">${data.panels.map(panel=>`<article class="pc-panel" data-property-metric="${panel.key}"><header><h4>${escape(panel.label)} <span>${escape(panel.symbol)} / ${escape(panel.unit)}</span></h4><p>${panel.reference?"各后端扣除相同 T₀、P₀ 参考点后比较":"同一温度、压力和纯工质"}</p></header>${data.single?singleSvg(panel):trendSvg(panel,data)+trendSvg(panel,data,true)}</article>`).join("")}</div>`;
  const notes=Array.isArray(section.notes)?section.notes:[];
  return `<section class="pc-dashboard" aria-label="真实流体详细对比">${toolbar}${summary}${visibleDiagnostics(data)}<p class="pc-guide">差值 = HEOS − REFPROP；相对差 = 差值 / |REFPROP| × 100%。差值采用后端保存结果。数值接近表示实现或数据库的一致性，不能替代试验精度验证。</p><div class="pc-chart-controls">${selectionControl}${legend()}</div>${!data.single?`<p class="pc-foot">${escape(data.axisLabel)}；曲线在缺值、重复坐标或相态变化处断开。纵轴按数据自动缩放；相对差图以 0 为中心，悬停可查看数值。</p>`:""}${panels}${detailsTable(data)}${stateDetails(data)}<details class="pc-details"><summary>数据库口径与原始记录</summary><ul class="pc-notes">${notes.map(note=>`<li>${escape(note)}</li>`).join("")}</ul><p class="pc-foot">本记录后端：CoolProp ${escape(section.capabilities?.coolprop?.version||"未记录")} / REFPROP ${escape(section.capabilities?.refprop?.version||"未记录")}。状态“可计算”仅指该物性在此工况返回有效数值；本页没有设定统一精度验收阈值。</p><pre>${escape(JSON.stringify(section,null,2))}</pre></details></section>`;
}

const EXPORT_STYLE=`svg{font-family:Arial,"Microsoft YaHei",sans-serif;background:#fff}.pc-grid{stroke:#e1e9ee;stroke-width:1}.pc-axis{stroke:#93a4b2;stroke-width:1}.pc-zero{stroke:#859aa9;stroke-width:1;stroke-dasharray:3 3}.pc-tick{fill:#526a7c;font:11px Consolas,monospace}.pc-unit{fill:#60788b;font-size:11px}.pc-value{fill:#213e54;font:15px Consolas,monospace}.pc-delta-value{fill:#30526d;font:12px Consolas,monospace}.pc-series{fill:none;stroke-width:2}.pc-heos{stroke:#2469a5}.pc-refprop{stroke:#b05b3a;stroke-dasharray:6 4}.pc-relative{stroke:#5e778a}.pc-marker{fill:#fff;stroke-width:1.8;stroke-dasharray:none}.pc-bar.pc-heos{fill:#3d79aa;stroke:none}.pc-bar.pc-refprop{fill:#b96d4d;stroke:none}.pc-empty-svg{fill:#8795a0;font-size:12px}.pc-title{fill:#213f55;font-size:21px;font-weight:700}.pc-subtitle{fill:#587386;font-size:12px}.pc-panel-title{fill:#25445b;font-size:15px;font-weight:700}`;

export function buildPropertyComparisonSvg(section,selection={}){
  const data=preparePropertyComparisonData(section,selection),width=1280,panelHeight=data.single?240:434,top=140,height=top+3*(panelHeight+14)+92;
  if(!data.points.length)return `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="160" viewBox="0 0 900 160"><text x="24" y="60">尚无真实流体校核结果</text></svg>`;
  const unavailable=data.points.filter(row=>!PROPERTY_COMPARISON_METRICS.some(metric=>metricPair(row,metric).paired)).length;
  const panels=data.panels.map((panel,index)=>{
    const x=16+index%2*632,y=top+Math.floor(index/2)*(panelHeight+14);
    const plot=data.single?`<g transform="translate(8 58)">${singleSvg(panel).replace('<svg xmlns=','<svg width="600" height="170" xmlns=')}</g>`:
      `<g transform="translate(8 57)">${trendSvg(panel,data).replace('<svg xmlns=','<svg width="600" height="254" xmlns=')}</g><g transform="translate(8 311)">${trendSvg(panel,data,true).replace('<svg xmlns=','<svg width="600" height="116" xmlns=')}</g>`;
    return `<g transform="translate(${x} ${y})"><rect width="616" height="${panelHeight}" fill="#fff" stroke="#cbd7e0"/><text class="pc-panel-title" x="17" y="28">${escape(`${panel.label} · ${panel.symbol} / ${panel.unit}`)}</text><text class="pc-subtitle" x="17" y="48">${panel.reference?"同一参考状态差值（详见校核记录）":"相同温度、压力、纯工质"}</text>${plot}</g>`;
  }).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="真实流体 HEOS 与 REFPROP 六项物性对比"><defs><style>${EXPORT_STYLE}</style></defs><rect width="${width}" height="${height}" fill="#f4f7f9"/><text class="pc-title" x="18" y="33">真实流体物性 · CoolProp HEOS × REFPROP</text><text class="pc-subtitle" x="18" y="58">${escape(fluidName(data.group))} · ${escape(data.condition)}</text><text class="pc-subtitle" x="18" y="81">${data.points.length} 个保存状态点 · ${unavailable} 个点缺少双后端数值 · HEOS 蓝色实线圆点 / REFPROP 橙色虚线方点 · ${data.single?"同一零基准柱长":"缺值和相变处断线"}</text><text class="pc-subtitle" x="18" y="104">CoolProp ${escape(section.capabilities?.coolprop?.version||"未记录")} / REFPROP ${escape(section.capabilities?.refprop?.version||"未记录")} · 参考点 HEOS ${escape(referenceSummary(data.points,"heos"))} / REFPROP ${escape(referenceSummary(data.points,"refprop"))}</text>${panels}<text class="pc-subtitle" x="18" y="${height-51}">差值 = HEOS − REFPROP；相对差 = 差值 / |REFPROP| × 100%。仅使用保存的配对差值；原始绝对焓熵不直接相减。</text><text class="pc-subtitle" x="18" y="${height-29}">HEOS 与 REFPROP 可能共享状态方程和实验数据；相互接近不能替代实验精度验证。</text><text class="pc-subtitle" x="18" y="${height-8}">${escape(data.group==="n-Dodecane"?"正十二烷是纯工质替代模型，不等同于真实 RP-1。":"适用范围、相态诊断与各点完整数值见该校核记录及 CSV。")}</text></svg>`;
}
function csvCell(value){
  if(value==null)return '""';
  const safe=typeof value==="number"?(finite(value)?String(value):""):String(value).replace(/^[=+@\-\t\r]/,"'$&");
  return `"${safe.replace(/"/g,'""')}"`;
}
export function buildPropertyComparisonCsv(section){
  const data=preparePropertyComparisonData(section),header=["状态点","工质","温度 / K","压力 / MPa","物性","单位","HEOS","REFPROP","HEOS − REFPROP","相对差 / %","HEOS 相态","REFPROP 相态","口径","HEOS 参考温度 / K","HEOS 参考压力 / Pa","REFPROP 参考温度 / K","REFPROP 参考压力 / Pa"];
  const rows=data.rows.flatMap(row=>allMetrics.map(metric=>{
    const p=metricPair(row,metric);return [row.reportIndex,row.fluid,row.temperature_K,finite(row.pressure_Pa)?row.pressure_Pa/1e6:null,metric.label,metric.unit,p.heos,p.refprop,p.absolute,p.relative,phase(row.phase),phase(row.refprop?.phase),p.reason||"已保存的同点差值",row.reference_state?.temperature_K,row.reference_state?.pressure_Pa,row.refprop?.reference_state?.temperature_K,row.refprop?.reference_state?.pressure_Pa];
  }));
  return "\uFEFF"+[header,...rows].map(row=>row.map(csvCell).join(",")).join("\r\n");
}
