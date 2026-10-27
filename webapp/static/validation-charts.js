/* Pure, snapshot-only thermochemistry charts. Values and differences come from the saved result. */
const finite = value => typeof value === "number" && Number.isFinite(value);
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = value => finite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:7}) : "—";
const signed = value => finite(value) ? `${value > 0 ? "+" : ""}${number(value)}` : "—";
const unique = values => [...new Set(values.filter(finite))].sort((a,b)=>a-b);
const COLORS = ["#2264a2", "#b46b28", "#337d72", "#795b9f", "#b44861", "#537d36", "#1e829a", "#795641", "#475a96", "#a13a91"];
const color = index => COLORS[index] || `hsl(${Math.round((index*137.508)%360)},55%,36%)`;
export const THERMO_CHART_METRICS = Object.freeze([
  {key:"Isp_vac_s", label:"真空比冲", symbol:"Isp,vac", unit:"s", chamber:false},
  {key:"Cstar_m_s", label:"特征速度", symbol:"c*", unit:"m/s", chamber:true},
  {key:"Cf_vac", label:"真空推力系数", symbol:"Cf,vac", unit:"1", chamber:false},
  {key:"Tc_K", label:"燃烧室温度", symbol:"Tc", unit:"K", chamber:true},
]);

function metricValue(point, backend, metric){
  const data=point?.[backend], value=data?.metrics?.[metric];
  return data?.status==="ok" && finite(value) ? value : null;
}

/** Numeric x values are shared across epsilon series, so absent points leave a gap. */
export function prepareThermoChartData(result){
  const points=Array.isArray(result?.points)?result.points.filter(p=>finite(p?.of)&&finite(p?.eps)):[];
  const xValues=unique([...(Array.isArray(result?.request?.of_values)?result.request.of_values:[]),...points.map(p=>p.of)]);
  const epsValues=unique([...(Array.isArray(result?.request?.eps_values)?result.request.eps_values:[]),...points.map(p=>p.eps)]);
  const panels=THERMO_CHART_METRICS.map(metric=>{
    const groups=epsValues.map((eps,index)=>({eps,color:color(index),label:`ε = ${number(eps)}`,
      values:xValues.map(of=>{
        const matches=points.filter(p=>p.of===of&&p.eps===eps), point=matches[0];
        const cantera=metricValue(point,"cantera",metric.key),cea=metricValue(point,"cea",metric.key);
        const paired=cantera!==null&&cea!==null, difference=point?.differences?.[metric.key];
        return {of,cantera,cea,absolute:paired&&finite(difference?.absolute)?difference.absolute:null,
          relative:paired&&finite(difference?.relative_percent)?difference.relative_percent:null,
          point, duplicate:matches.length>1};
      })}));
    // Collapse chamber quantities only when every saved value and gap is identical.
    // A discrepancy is evidence to retain, even when this quantity normally ignores epsilon.
    const signature=group=>JSON.stringify(group.values.map(p=>[p.cantera,p.cea,p.absolute,p.relative,p.duplicate]));
    const collapsed=metric.chamber&&groups.length>1&&groups.every(g=>signature(g)===signature(groups[0]))&&
      groups.every(g=>g.values.every(p=>!p.duplicate));
    return {...metric,collapsed,groups:collapsed?[{...groups[0],eps:null,label:"所有 ε（保存结果一致）"}]:groups,
      missing:groups.reduce((sum,g)=>sum+g.values.filter(p=>p.cantera===null||p.cea===null).length,0)};
  });
  return {points,xValues,epsValues,panels,singlePoint:xValues.length===1,request:result?.request||{},scope:result?.scope||{}};
}

function domain(values, symmetric=false){
  if(!values.length)return [0,1];
  const lo=Math.min(...values),hi=Math.max(...values);
  if(symmetric){const bound=Math.max(Math.abs(lo),Math.abs(hi));return bound?[ -bound*1.18,bound*1.18 ]:[-.1,.1];}
  const padding=(hi-lo||Math.abs(hi)*.03||1)*.12;
  return [lo-padding,hi+padding];
}

function axisNumber(value,span){
  if(Math.abs(value)<1e-13)return "0";
  if(Math.abs(value)>=1e6||Math.abs(value)<.001)return value.toExponential(2);
  const decimals=Math.min(6,Math.max(0,2-Math.floor(Math.log10(Math.abs(span)||1))));
  return value.toLocaleString("en-US",{maximumFractionDigits:decimals,minimumFractionDigits:0});
}

function segments(values,key){
  const output=[];let current=[];
  for(const value of values){
    if(finite(value[key])&&!value.duplicate)current.push(value);
    else{if(current.length)output.push(current);current=[];}
  }
  if(current.length)output.push(current);
  return output;
}

function marker(backend,x,y,stroke,title,attrs=""){
  const common=`class="tc-marker" stroke="${stroke}" fill="#fff" stroke-width="1.8" ${attrs}`;
  return backend==="cea"?`<rect ${common} x="${x-3}" y="${y-3}" width="6" height="6"><title>${escape(title)}</title></rect>`:
    `<circle ${common} cx="${x}" cy="${y}" r="3.2"><title>${escape(title)}</title></circle>`;
}

function plotSvg(panel,data,difference=false){
  const width=600,height=difference?112:278,left=96,right=25,top=difference?23:24,bottom=difference?28:44;
  const innerWidth=width-left-right,innerHeight=height-top-bottom;
  const allValues=panel.groups.flatMap(g=>g.values.flatMap(p=>difference?[p.relative]:[p.cantera,p.cea])).filter(finite);
  const [low,high]=domain(allValues,difference),first=data.xValues[0]??0,last=data.xValues.at(-1)??1;
  const x=value=>left+(last===first ? .5 : (value-first)/(last-first))*innerWidth;
  const y=value=>top+(high-value)/(high-low)*innerHeight;
  const xticks=data.xValues.length<=8?data.xValues:Array.from({length:6},(_,i)=>first+(last-first)*i/5);
  const yticks=Array.from({length:difference?3:5},(_,i)=>low+(high-low)*i/(difference?2:4));
  const label=difference?`${panel.label}相对差，百分比`:`${panel.label}，${panel.unit}，横轴为 O/F 氧燃质量比`;
  const axes=yticks.map(value=>`<line class="tc-grid" x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}"/><text class="tc-tick" x="${left-11}" y="${y(value)+4}" text-anchor="end">${escape(axisNumber(value,high-low))}</text>`).join("")+
    xticks.map(value=>`<line class="tc-grid" x1="${x(value)}" x2="${x(value)}" y1="${top}" y2="${height-bottom}"/><text class="tc-tick" x="${x(value)}" y="${height-bottom+18}" text-anchor="middle">${escape(axisNumber(value,last-first))}</text>`).join("")+
    `<line class="tc-axis" x1="${left}" x2="${left}" y1="${top}" y2="${height-bottom}"/><line class="tc-axis" x1="${left}" x2="${width-right}" y1="${height-bottom}" y2="${height-bottom}"/>`+
    (difference?`<line class="tc-zero" x1="${left}" x2="${width-right}" y1="${y(0)}" y2="${y(0)}"/><text class="tc-unit" x="${left}" y="13">相对差 / %</text>`:
      `<text class="tc-unit" x="${left}" y="13">${escape(panel.symbol)} / ${escape(panel.unit)}</text><text class="tc-axis-title" x="${left+innerWidth/2}" y="${height-4}" text-anchor="middle">O/F 氧燃质量比</text>`);
  const series=panel.groups.map(group=>(difference?["relative"]:["cantera","cea"]).map(backend=>{
    const isDiff=backend==="relative",name=isDiff?"CEA − Cantera":backend==="cantera"?"Cantera":"CEA";
    const paths=segments(group.values,backend).filter(s=>s.length>1).map(s=>`<polyline class="tc-series ${backend==="cea"?"tc-dashed":""}" stroke="${group.color}" data-backend="${backend}" data-eps="${group.eps??"all"}" points="${s.map(p=>`${x(p.of)},${y(p[backend])}`).join(" ")}"/>`).join("");
    const marks=group.values.filter(p=>finite(p[backend])).map(p=>marker(backend,x(p.of),y(p[backend]),group.color,
      `${name} · O/F = ${number(p.of)} · ${group.label}: ${number(p[backend])} ${isDiff?"%":panel.unit}`,
      `data-backend="${backend}" data-eps="${group.eps??"all"}" data-of="${p.of}" data-value="${p[backend]}"`)).join("");
    return paths+marks;
  }).join("")).join("");
  const empty=allValues.length?"":`<text class="tc-empty-svg" x="${left+innerWidth/2}" y="${top+innerHeight/2}" text-anchor="middle">${difference?"无有效配对差值":"暂无有效数值"}</text>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" class="tc-plot${difference?" tc-difference":""}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escape(label)}"><title>${escape(label)}</title>${axes}${series}${empty}</svg>`;
}

function panelHint(panel,data){
  if(panel.collapsed)return "燃烧室量 · 各膨胀比返回值完全一致，合并显示";
  if(panel.chamber&&data.epsValues.length>1)return "燃烧室量 · 返回值或可用状态不同，保留各膨胀比";
  return panel.chamber?"燃烧室量":"喷管性能 · 按膨胀比分组";
}

function seriesLegend(panel){
  return `<div class="tc-eps-legend">${panel.groups.map(group=>`<span><svg viewBox="0 0 22 12" width="22" height="12" aria-hidden="true"><line x1="1" x2="21" y1="6" y2="6" stroke="${group.color}" stroke-width="2"/></svg>${escape(group.label)}</span>`).join("")}</div>`;
}

function methodLegend(){
  return `<div class="tc-method-legend"><span><svg viewBox="0 0 34 14" width="34" height="14" aria-hidden="true"><line x1="1" x2="33" y1="7" y2="7" stroke="#3a5369" stroke-width="1.8"/><circle cx="17" cy="7" r="3" fill="#fff" stroke="#3a5369" stroke-width="1.8"/></svg>Cantera · 实线圆点</span><span><svg viewBox="0 0 34 14" width="34" height="14" aria-hidden="true"><line x1="1" x2="33" y1="7" y2="7" stroke="#3a5369" stroke-width="1.8" stroke-dasharray="6 4"/><rect x="14" y="4" width="6" height="6" fill="#fff" stroke="#3a5369" stroke-width="1.8"/></svg>NASA CEA · 虚线方点</span></div>`;
}

function conditionText(data){
  const r=data.request,mode={equilibrium:"平衡流",frozen_chamber:"燃烧室冻结",frozen_throat:"喉部冻结"}[r.mode]||"膨胀假设见记录";
  const fuel={lox_methane:"液氧 / 甲烷",lox_kerosene:"液氧 / 正十二烷（煤油替代）"}[r.propellant_pair]||"推进剂见记录";
  return `${fuel} · Pc ${finite(r.pc_pa)?number(r.pc_pa/1e6):"—"} MPa · 燃料 ${number(r.T_fuel_K)} K / 氧化剂 ${number(r.T_ox_K)} K · ${mode}`;
}

function singleCard(panel,data){
  return `<article class="tc-panel tc-single-card" data-thermo-metric="${panel.key}"><header><h4>${escape(panel.label)} <span>${escape(panel.symbol)} / ${escape(panel.unit)}</span></h4><p>${escape(panelHint(panel,data))}</p></header><div class="tc-single-table-wrap"><table class="tc-single-table"><thead><tr><th>膨胀比</th><th>Cantera</th><th>NASA CEA</th><th>差值</th><th>相对差 / %</th></tr></thead><tbody>${panel.groups.map(group=>{
    const p=group.values[0];return `<tr><th>${group.eps===null?"全部 ε":escape(number(group.eps))}</th><td>${escape(number(p.cantera))}</td><td>${escape(number(p.cea))}</td><td>${escape(signed(p.absolute))}</td><td>${escape(signed(p.relative))}</td></tr>`;
  }).join("")}</tbody></table></div><p class="tc-card-foot">差值单位：${escape(panel.unit)} · CEA − Cantera；缺失值以 — 表示</p></article>`;
}

export function renderThermoCharts(result){
  const data=prepareThermoChartData(result);
  if(!data.xValues.length)return '<div class="tc-no-data">暂无已保存的热力学对比点。</div>';
  const heading=data.singlePoint?`单工况对比 · O/F = ${number(data.xValues[0])}`:"性能对比曲线 · 随 O/F 的变化";
  const explanation=data.singlePoint?"这条历史记录仅有一个 O/F，不能形成趋势曲线。下方同时比较四项指标；运行多点扫描后即可查看曲线。":
    "横轴为实际 O/F；颜色区分膨胀比，线型区分计算后端。下方小图显示相对差，0 表示两个后端数值相同。悬停数据点查看数值。";
  return `<section class="tc-dashboard" aria-label="热力学性能图形对比"><div class="tc-chart-heading">${data.singlePoint?`<h3>${escape(heading)}</h3>`:""}<p>${escape(conditionText(data))}</p></div><p class="tc-reading-guide">${escape(explanation)}</p>${!data.singlePoint?methodLegend():""}<div class="tc-chart-grid">${data.panels.map(panel=>data.singlePoint?singleCard(panel,data):
    `<article class="tc-panel" data-thermo-metric="${panel.key}"><header><h4>${escape(panel.label)} <span>${escape(panel.symbol)} / ${escape(panel.unit)}</span></h4><p>${escape(panelHint(panel,data))}</p></header>${plotSvg(panel,data)}${seriesLegend(panel)}${plotSvg(panel,data,true)}${panel.missing?`<p class="tc-gap-note">${panel.missing} 个工况缺少完整配对；曲线在缺失处断开，不填零。</p>`:""}</article>`).join("")}</div><p class="tc-chart-foot">相对差 = (CEA − Cantera) / |Cantera| × 100%，采用该记录保存的差值。曲线接近表示数值接近；数据库、允许产物及物态的差异仍需结合本次比较条件判断。</p></section>`;
}

const SVG_STYLE=`
  svg{font-family:Arial,"Microsoft YaHei",sans-serif;color:#263e52;background:#fff}
  .tc-grid{stroke:#e2e9ee;stroke-width:1}.tc-axis{stroke:#8fa3b3;stroke-width:1}
  .tc-zero{stroke:#879bae;stroke-width:1;stroke-dasharray:3 3}.tc-tick{fill:#536b7e;font-size:11px;font-family:Consolas,monospace}
  .tc-unit,.tc-axis-title{fill:#536b7e;font-size:11px}.tc-series{fill:none;stroke-width:2;stroke-linejoin:round}.tc-dashed{stroke-dasharray:6 4}
  .tc-empty-svg{fill:#7f8e9b;font-size:12px}.tc-export-title{font-size:22px;font-weight:700;fill:#223c52}
  .tc-export-subtitle{font-size:12px;fill:#536b7e}.tc-export-panel-title{font-size:16px;font-weight:700;fill:#223c52}
  .tc-export-label{font-size:11px;fill:#536b7e}.tc-export-value{font-size:13px;fill:#253c4e;font-family:Consolas,"Microsoft YaHei",monospace}
`;

/** A self-contained report: usable as an SVG download or rendered into a PNG canvas. */
export function buildThermoChartSvg(result){
  const data=prepareThermoChartData(result),width=1280,panelWidth=616;
  const rows=Math.max(1,...data.panels.map(p=>p.groups.length));
  const legendRows=Math.ceil(Math.max(1,...data.panels.map(p=>p.groups.length))/3);
  const panelHeight=data.singlePoint?Math.max(200,115+rows*35):488+legendRows*19;
  const top=118,height=top+panelHeight*2+88;
  const panels=data.panels.map((panel,index)=>{
    const px=16+(index%2)*632,py=top+Math.floor(index/2)*(panelHeight+14);
    let contents;
    if(data.singlePoint){
      const columns=[18,130,263,400,510];
      contents=["膨胀比","Cantera","NASA CEA","差值","相对差 / %"].map((label,i)=>`<text class="tc-export-label" x="${columns[i]}" y="86">${escape(label)}</text>`).join("")+
        panel.groups.map((group,i)=>{
          const p=group.values[0],y=115+i*35;
          return `<line x1="16" x2="600" y1="${y+10}" y2="${y+10}" stroke="#e2e9ee"/>`+
            [group.eps===null?"全部 ε":number(group.eps),number(p.cantera),number(p.cea),signed(p.absolute),signed(p.relative)].map((text,j)=>`<text class="tc-export-value" x="${columns[j]}" y="${y}">${escape(text)}</text>`).join("");
        }).join("");
    }else{
      contents=`<g transform="translate(7 65)">${plotSvg(panel,data).replace('<svg xmlns=', '<svg width="600" height="278" xmlns=')}</g>`+
        panel.groups.map((group,i)=>`<g transform="translate(${24+i%3*195} ${354+Math.floor(i/3)*19})"><line x1="0" x2="18" y1="0" y2="0" stroke="${group.color}" stroke-width="2"/><text class="tc-export-label" x="24" y="4">${escape(group.label)}</text></g>`).join("")+
        `<g transform="translate(7 ${362+legendRows*19})">${plotSvg(panel,data,true).replace('<svg xmlns=', '<svg width="600" height="112" xmlns=')}</g>`;
    }
    return `<g transform="translate(${px} ${py})"><rect width="${panelWidth}" height="${panelHeight}" fill="#fff" stroke="#cbd7e0"/><text class="tc-export-panel-title" x="18" y="29">${escape(`${panel.label} · ${panel.symbol} / ${panel.unit}`)}</text><text class="tc-export-label" x="18" y="49">${escape(panelHint(panel,data))}</text>${contents}</g>`;
  }).join("");
  const guide=data.singlePoint?`单工况 O/F = ${number(data.xValues[0])} · 仅比较保存的数值，不生成趋势曲线。`:
    "横轴 O/F · 颜色区分 ε · Cantera 实线圆点 / CEA 虚线方点 · 小图为相对差 / % · 缺失处断开";
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="NASA CEA 与 Cantera 四项热力学指标对比"><defs><style>${SVG_STYLE}</style></defs><rect width="${width}" height="${height}" fill="#f5f8fa"/><text class="tc-export-title" x="18" y="34">NASA CEA × Cantera · 热力学对比</text><text class="tc-export-subtitle" x="18" y="61">${escape(conditionText(data))}</text><text class="tc-export-subtitle" x="18" y="87">${escape(guide)}</text>${panels}<text class="tc-export-label" x="18" y="${height-32}">相对差 = (CEA − Cantera) / |Cantera| × 100%，来自保存记录；数值接近不等同于实机验证或精度合格。</text><text class="tc-export-label" x="18" y="${height-13}">产品数据库、允许物种和相态的适用范围，请结合该校核记录的比较条件说明。</text></svg>`;
}
