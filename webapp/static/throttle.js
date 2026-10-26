// Source-bound steady throttle sweeps. Charts use saved points, never the draft.
import {renderSteadyClosure} from "./steady-closure.js";
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const number = value => finite(value) ? value.toLocaleString("zh-CN",{maximumSignificantDigits:7}) : "—";
const short = value => finite(value) ? (value!==0&&(Math.abs(value)<.001||Math.abs(value)>=1e6)?value.toExponential(2):value.toLocaleString("en-US",{maximumSignificantDigits:4})) : "—";
const scale = (value,factor) => finite(value) ? value*factor : null;
const date = value => value ? new Date(value*1000).toLocaleString("zh-CN",{hour12:false}) : "—";
const running = record => ["queued","running"].includes(record?.status);
const STATUS = {queued:"排队中",running:"计算中",succeeded:"扫描完成",failed:"执行失败",cancelled:"已取消",timed_out:"超时",interrupted:"服务中断",pass:"通过",fail:"未通过",review:"需复核",unknown:"未判定",unavailable:"不可用",pending:"未计算",converged:"已收敛",not_converged:"未收敛"};
const badge = (status,label) => `<span class="th-badge th-status-${["pass","fail","failed","review","converged","not_converged","running","queued"].includes(status)?status:"neutral"}">${escape(label||STATUS[status]||status||"未判定")}</span>`;
const notice = (text,kind="note") => `<p class="th-notice th-notice-${kind}">${escape(text)}</p>`;
const metric = (point,key) => point?.converged===true && finite(point.summary?.[key]) ? point.summary[key] : null;
const errorPercent = point => {
  const actual=metric(point,"thrust_N"),target=point?.thrust_target_N;
  return finite(actual)&&finite(target)&&target>0 ? (actual-target)/target*100 : null;
};

export function throttleSource(context){
  const record=context?.record;
  return record?.status==="succeeded" && record.result && record.id && record.project_id===context?.project?.id ? record : null;
}
function sourceInputs(context){const record=throttleSource(context);return record?.result?.calculation?.inputs||record?.inputs||record?.result?.submitted_inputs||{};}
export function buildThrottleRequest(context,form){
  const source=throttleSource(context);
  if(!source)throw Error("请先选择本项目已完成的稳态设计记录。");
  const minimum=Number(form.minimum_percent),count=Number(form.point_count);
  if(String(form.minimum_percent??"").trim()===""||!Number.isFinite(minimum)||minimum<5||minimum>99)throw Error("最低节流比例须在 5%–99% 之间；这是扫描输入范围，不代表发动机可用范围。");
  if(!Number.isInteger(count)||count<2||count>15)throw Error("扫描点数须为 2–15 的整数。");
  return {source_job_id:source.id,throttle_min:minimum/100,throttle_steps:count};
}

/** Keep the requested grid, including not-yet-computed and failed locations. */
export function prepareThrottleData(result){
  const saved=Array.isArray(result?.points)?result.points.filter(point=>finite(point?.throttle_ratio)):[];
  const request=result?.request||{},minimum=request.throttle_min,count=request.throttle_steps;
  const ratios=finite(minimum)&&minimum>0&&minimum<1&&Number.isInteger(count)&&count>=2&&count<=15 ?
    Array.from({length:count},(_,i)=>minimum+(1-minimum)*i/(count-1)) : saved.map(point=>point.throttle_ratio);
  // Floating-point scan endpoints can differ by a few ulps between Python and JS.
  for(const point of saved)if(!ratios.some(value=>Math.abs(value-point.throttle_ratio)<1e-9))ratios.push(point.throttle_ratio);
  const unique=[...new Set(ratios)].sort((a,b)=>a-b);
  const points=unique.map(ratio=>{
    const matches=saved.filter(point=>Math.abs(point.throttle_ratio-ratio)<1e-9);
    if(matches.length>1)return {throttle_ratio:ratio,status:"review",duplicate:true,converged:false,error:"同一节流比例存在重复记录；图表不连线，请核对原始数据。"};
    return matches[0]||{throttle_ratio:ratio,status:"pending",converged:null,pending:true};
  });
  const summarySeries=(key,label,color,factor=1)=>({key,label,color,value:point=>scale(metric(point,key),factor)});
  const temperatures=[
    ["chamber_temperature_K","主燃烧室","#af634c"],
    ["ox_preburner_T_combustion_K","富氧预燃室","#6a66a1"],
    ["fuel_preburner_T_combustion_K","富燃预燃室","#b38135"],
    ["gg_T_combustion_K","燃气发生器","#b38135"],
  ].filter(([key])=>saved.some(point=>finite(metric(point,key)))).map(args=>summarySeries(...args));
  const panels=[
    {id:"thrust",label:"推力跟随",unit:"kN",hint:"当前环境压力下的目标与实际推力",series:[summarySeries("thrust_N","实际推力","#2e6c96",1e-3),{key:"target",label:"目标推力",color:"#7e8792",dashed:true,value:point=>scale(point.thrust_target_N,1e-3)}]},
    {id:"isp",label:"比冲",unit:"s",hint:"当前环境与真空性能分别显示",series:[summarySeries("Isp_s","当前环境","#2e6c96"),summarySeries("Isp_vac_s","真空","#427e72")]},
    {id:"pressure",label:"燃烧室压力",unit:"MPa",hint:"固定主喷管喉部面积下逐点求解",series:[{key:"pc_Pa",label:"燃烧室压力",color:"#2e6c96",value:point=>point.converged===true?scale(point.pc_Pa,1e-6):null}]},
    {id:"mass",label:"质量流量",unit:"kg/s",hint:"总流量与两路推进剂供给",series:[summarySeries("mdot_total_kg_s","总流量","#3e596f"),summarySeries("mdot_ox_kg_s","氧化剂","#3f8cba"),summarySeries("mdot_fuel_kg_s","燃料","#af8240")]},
    {id:"temperature",label:"温度分布",unit:"K",hint:"仅显示该循环实际返回的温度",series:temperatures},
    {id:"tracking",label:"推力相对偏差",unit:"%",hint:"(实际 − 目标) / 目标 × 100%；不替代约束判定",symmetric:true,series:[{key:"tracking_error_percent",label:"相对目标偏差",color:"#8064a0",value:errorPercent}]},
  ];
  return {points,panels,requested:points.length,computed:points.filter(point=>!point.pending).length,request,baseline:result?.baseline||{}};
}

function domain(values,symmetric){
  if(!values.length)return symmetric?[-1,1]:[0,1];
  if(symmetric){const bound=Math.max(...values.map(Math.abs));return bound?[-bound*1.18,bound*1.18]:[-.01,.01];}
  const low=Math.min(...values),high=Math.max(...values),padding=(high-low||Math.abs(high)*.05||1)*.13;
  return [Math.max(0,low-padding),high+padding];
}
function splitSegments(points,read){
  const segments=[];let current=[];
  for(const point of points){const value=read(point);if(finite(value)&&!point.duplicate)current.push({point,value});else{if(current.length)segments.push(current);current=[];}}
  if(current.length)segments.push(current);return segments;
}
function plot(panel,data){
  const width=570,height=275,left=82,right=23,top=24,bottom=44,inner=width-left-right;
  const values=panel.series.flatMap(series=>data.points.map(series.value)).filter(finite),[low,high]=domain(values,panel.symmetric);
  const first=data.points[0]?.throttle_ratio??0,last=data.points.at(-1)?.throttle_ratio??1;
  const x=value=>left+(first===last ? 0.5 : (value-first)/(last-first))*inner,y=value=>top+(high-value)/(high-low)*(height-top-bottom);
  const xticks=data.points.length<=7?data.points.map(point=>point.throttle_ratio):Array.from({length:5},(_,i)=>first+(last-first)*i/4);
  const axes=Array.from({length:5},(_,i)=>low+(high-low)*i/4).map(value=>`<line class="th-grid" x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}"/><text class="th-tick" x="${left-10}" y="${y(value)+4}" text-anchor="end">${escape(short(value))}</text>`).join("")+
    xticks.map(value=>`<line class="th-grid th-grid-v" x1="${x(value)}" x2="${x(value)}" y1="${top}" y2="${height-bottom}"/><text class="th-tick" x="${x(value)}" y="${height-bottom+19}" text-anchor="middle">${escape(short(value*100))}</text>`).join("")+
    `<path class="th-axis" d="M ${left} ${top} V ${height-bottom} H ${width-right}"/><text class="th-axis-label" x="${left}" y="13">${escape(panel.unit)}</text><text class="th-axis-label" x="${left+inner/2}" y="${height-3}" text-anchor="middle">节流指令 / %</text>`+
    (panel.symmetric?`<line class="th-zero" x1="${left}" x2="${width-right}" y1="${y(0)}" y2="${y(0)}"/>`:"");
  const gaps=data.points.filter(point=>!point.pending&&point.converged!==true).map(point=>`<g class="th-failed-marker" data-failed-ratio="${point.throttle_ratio}"><title>${escape(`${number(point.throttle_ratio*100)}% · 无有效固定主喷管喉部工况或记录冲突，保留缺口`)}</title><path d="M ${x(point.throttle_ratio)-4} ${top+2} l 8 8 m 0 -8 l -8 8"/></g>`).join("");
  const series=panel.series.map(series=>splitSegments(data.points,series.value).map(segment=>{
    const line=segment.length>1?`<polyline class="th-line${series.dashed?" th-dashed":""}" stroke="${series.color}" data-series="${series.key}" points="${segment.map(({point,value})=>`${x(point.throttle_ratio)},${y(value)}`).join(" ")}"/>`:"";
    return line+segment.map(({point,value})=>{
      const px=x(point.throttle_ratio),py=y(value),title=`${number(point.throttle_ratio*100)}% · ${series.label}: ${number(value)} ${panel.unit}${point.status==="review"?" · 需复核": ""}`;
      return `<g data-ratio="${point.throttle_ratio}" data-series="${series.key}" data-value="${value}"><title>${escape(title)}</title>${point.status==="review"&&!series.dashed?`<path class="th-marker th-review-marker" stroke="${series.color}" d="M ${px} ${py-4} l 4 4 l -4 4 l -4 -4 Z"/>`:`<circle class="th-marker" cx="${px}" cy="${py}" r="3" stroke="${series.color}"/>`}</g>`;
    }).join("");
  }).join("")).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" class="th-plot" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escape(`${panel.label}，纵轴 ${panel.unit}，横轴为目标推力比例（节流指令）百分比`)}"><title>${escape(panel.label)}</title>${axes}${gaps}${series}${values.length?"":`<text class="th-plot-empty" x="${left+inner/2}" y="${height/2}" text-anchor="middle">暂无有效数值</text>`}</svg>`;
}
function legend(panel){return `<div class="th-legend">${panel.series.map(series=>`<span><svg width="25" height="12" viewBox="0 0 25 12" aria-hidden="true"><line x1="1" x2="24" y1="6" y2="6" stroke="${series.color}" stroke-width="2"${series.dashed?' stroke-dasharray="5 3"':""}/></svg>${escape(series.label)}</span>`).join("")}</div>`;}
function conditionText(result){const b=result?.baseline||{};return `基准推力 ${number(scale(b.thrust_N,1e-3))} kN · 环境压力 ${number(scale(b.ambient_pressure_Pa,1e-6))} MPa · 总供给 O/F ${number(b.of_ratio)} · 膨胀比 ${number(b.expansion_ratio)}`;}
export function renderThrottleCharts(result){
  const data=prepareThrottleData(result);
  return `<div class="th-chart-grid">${data.panels.map((panel,index)=>`<article class="th-chart" data-throttle-chart="${panel.id}"><header><span class="th-panel-index">${String(index+1).padStart(2,"0")}</span><div><h4>${escape(panel.label)}</h4><p>${escape(panel.hint)}</p></div></header>${plot(panel,data)}${legend(panel)}</article>`).join("")}</div>`;
}
function numericalState(point){
  if(point.pending)return "pending";
  const status=point.assessment?.numerical?.status;
  return status==="pass"?"converged":status==="fail"?"not_converged":point.converged===true?"converged":"unknown";
}
function numericalStatus(point){return badge(numericalState(point));}
function selectedPointDetail(point){
  if(!point)return "";
  const checks=Array.isArray(point.assessment?.checks)?point.assessment.checks:[];
  const warnings=Array.isArray(point.warnings)?point.warnings:[];
  return `<section class="th-point-detail" aria-label="所选节流点诊断"><header><h3>${escape(number(point.throttle_ratio*100))}% 节流点诊断</h3>${numericalStatus(point)}${badge(point.assessment?.status||"unknown","系统约束 · "+(STATUS[point.assessment?.status]||"未判定"))}</header>${point.error?notice(typeof point.error==="string"?point.error:JSON.stringify(point.error),"error"):""}<div class="th-detail-metrics"><div><span>数值残差</span><b>${number(point.assessment?.numerical?.residual??point.summary?.solver_residual_norm)}</b></div><div><span>主喷管喉部面积 / m²</span><b>${number(point.geometry?.throat_area_m2)}</b></div><div><span>面积相对偏差 / %</span><b>${number(scale(point.geometry?.relative_error,100))}</b></div><div><span>主喷管喉部闭合</span>${badge(point.geometry?.status||"unknown")}</div></div>${checks.length?`<div class="th-table-wrap"><table class="th-checks"><thead><tr><th>系统约束</th><th>当前值</th><th>判据</th><th>状态</th></tr></thead><tbody>${checks.map(check=>`<tr data-check-status="${escape(check.status)}"><td>${escape(check.label||check.key)}${check.detail?`<small>${escape(check.detail)}</small>`:""}</td><td>${number(check.value)} ${escape(check.unit)}</td><td>${check.criterion==="minimum"?"≥":check.criterion==="maximum"?"≤":""} ${number(check.limit)} ${escape(check.unit)}</td><td>${badge(check.status)}</td></tr>`).join("")}</tbody></table></div>`:notice(point.pending?"该扫描点尚未计算。":"本点未返回系统约束数据，不能据此判为工程通过。")}${point.inputs?.cycle_type==="ffsc"?renderSteadyClosure(point):""}${warnings.length?`<ul class="th-warnings">${warnings.map(value=>`<li>${escape(typeof value==="string"?value:JSON.stringify(value))}</li>`).join("")}</ul>`:""}<details class="th-raw"><summary>查看该点完整保存数据</summary><pre>${escape(JSON.stringify(point,null,2))}</pre></details></section>`;
}
export function renderThrottleResponseGains(result) {
  const response = result?.response_gains;
  if (!response) {
    return `<section class="th-response" aria-label="有限区间节流响应增益"><h3>节流响应增益</h3>${notice("这条历史记录尚未保存响应增益。重新运行扫描后，可比较各部件输出对节流指令的变化程度。")}</section>`;
  }
  const metrics = Array.isArray(response.metrics) ? response.metrics : [];
  const intervals = Array.isArray(response.intervals) ? response.intervals : [];
  if (response.status === "unavailable" || !metrics.length) {
    return `<section class="th-response" aria-label="有限区间节流响应增益"><h3>节流响应增益</h3>${notice(response.reason || "需要相邻有效工况及同次扫描的100%归一化参考点。")}</section>`;
  }
  const nominal = intervals.at(-1);
  const intervalText = interval => `${number(scale(interval?.from_ratio,100))}%–${number(scale(interval?.to_ratio,100))}%`;
  const interpretation = value => !finite(value) ? "缺少有效区间" : Math.abs(value) < 1e-8 ? "本扫描中基本不变" : value < 0 ? "随指令增加而下降" : value > 1 ? "相对变化大于指令" : "相对变化不大于指令";
  const rows = metrics.map(item => {
    const segments = Array.isArray(item.segments) ? item.segments : [];
    const nominalStatus = segments.at(-1)?.status || "unavailable";
    return `<tr><td>${escape(item.label)}</td><td>${number(item.reference_value)} <small>${escape(item.unit)}</small></td><td>${number(item.near_nominal_gain)}</td><td>${number(item.gain_min)} ～ ${number(item.gain_max)}</td><td>${escape(interpretation(item.near_nominal_gain))}</td><td>${badge(nominalStatus,nominalStatus === "pass" ? "所检约束通过" : undefined)}</td></tr>`;
  }).join("");
  const details = intervals.map((interval,index) => `<details class="th-response-interval"><summary>${escape(intervalText(interval))} · 指令步长 ${number(scale(interval.step_ratio,100))} 个百分点 · ${escape(interval.status === "pass" ? "所检约束通过" : STATUS[interval.status] || "未判定")}</summary>${interval.reason ? notice(interval.reason,"warning") : ""}<div class="th-table-wrap"><table><thead><tr><th>指标</th><th>归一化增益 G / —</th><th>割线斜率 ΔY/Δu</th><th>状态</th></tr></thead><tbody>${metrics.map(item => {
    const segment = Array.isArray(item.segments) ? item.segments[index] : null;
    return `<tr><td>${escape(item.label)}</td><td>${number(segment?.gain)}</td><td>${number(segment?.slope_per_ratio)} <small>${escape(item.unit)} / 指令比例</small></td><td>${badge(segment?.status || "unavailable")}</td></tr>`;
  }).join("")}</tbody></table></div></details>`).join("");
  return `<section class="th-response" aria-label="有限区间节流响应增益"><header><div><span class="th-kicker">COUPLED STEADY RESPONSE</span><h3>节流响应增益</h3></div>${badge(response.status === "available" ? "pass" : "review",response.status === "available" ? "可计算区间完整" : "存在复核区间或缺口")}</header><p>比较各输出的相对变化与节流指令变化。G = (ΔY / Y₁₀₀) / Δu，以同次扫描的100%结果归一化；G = 1 表示相对变化相同，G = 0 表示本区间基本不变。</p><p class="th-response-method">有限区间割线 · 耦合节流响应 · 不跨失败点 · 不代表独立参数偏导数或实机验证</p>${response.reference?.status === "review" ? notice("100%参考工况的系统约束未通过或未确认；全部增益仅用于诊断。","warning") : ""}<div class="th-table-wrap"><table><thead><tr><th>输出指标</th><th>100%参考值</th><th>近额定增益 G / —<small>${escape(intervalText(nominal))}</small></th><th>已算区间 G 范围</th><th>近额定响应</th><th>近额定区间判定</th></tr></thead><tbody>${rows}</tbody></table></div><details class="th-response-details"><summary>查看每个相邻区间与有量纲斜率</summary>${details}</details><p class="th-response-method">增益依赖扫描步长和模型假设；当前未给出步长收敛检查、重复性试验或统计置信区间。归一化温度采用 K。</p></section>`;
}

export function renderThrottleResult(result,selectedRatio=null,recordStatus=null){
  if(!result?.points?.length){
    const emptyStates={
      queued:["扫描已提交，正在排队等待","计算基准与扫描设置已保存；开始求解后会逐点显示结果。"],
      running:["正在求解首个工况","首个工况返回后将显示曲线和逐点诊断。可以取消扫描；已返回的工况会保留。"],
      cancelled:["扫描已取消，尚未返回工况","这条记录保留了计算基准与扫描请求。可以调整设置后重新运行。"],
      timed_out:["扫描已超时，尚未返回工况","请查看任务错误与计算设置；本次没有可绘制的工况数据。"],
      interrupted:["扫描已中断，尚未返回工况","请查看任务状态；本次没有可绘制的工况数据。"],
      failed:["扫描执行失败，尚未返回工况","请查看上方任务错误；本次没有可绘制的工况数据。"],
      succeeded:["本次扫描未返回工况记录","请检查任务记录与诊断；没有工况数据时不会生成性能曲线。"],
    };
    const [title,description]=emptyStates[recordStatus]||["建立从额定工况到部分推力的稳态性能曲线","选择已完成的设计记录，设置最低节流比例与点数后运行。每一点独立求解；结果逐点保留。"];
    return `<section class="th-empty" data-throttle-empty-status="${escape(recordStatus||"initial")}"><span>STEADY THROTTLE SWEEP</span><h3>${escape(title)}</h3><p>${escape(description)}</p><div>推力跟随　·　比冲　·　室压　·　质量流量　·　温度　·　约束检查</div></section>`;
  }
  const data=prepareThrottleData(result),computed=data.points.filter(point=>!point.pending),chosen=data.points.find(point=>point.throttle_ratio===selectedRatio)||computed.find(point=>point.status!=="pass")||computed[0];
  const converged=computed.filter(point=>numericalState(point)==="converged").length,passed=computed.filter(point=>point.status==="pass").length,review=computed.filter(point=>point.status==="review").length,failed=computed.filter(point=>point.converged!==true).length;
  return `<div class="th-summary"><div><span>已计算 / 请求</span><b>${computed.length} <small>/ ${data.requested}</small></b></div><div><span>数值收敛</span><b>${converged}</b></div><div><span>综合检查通过</span><b>${passed}</b></div><div class="${review||failed?"th-summary-attention":""}"><span>需复核 / 无有效工况</span><b>${review} <small>/ ${failed}</small></b></div></div><div class="th-chart-toolbar"><div><h3>稳态节流性能曲线</h3><p>${escape(conditionText(result))}</p></div><div><button data-throttle-action="export-svg">导出 SVG</button><button data-throttle-action="export-png">导出 PNG</button></div></div><p class="th-reading-guide">横轴为目标推力比例（节流指令），曲线仅连接相邻的已算工况。菱形表示需复核；× 表示未形成有效工况（数值求解或主喷管喉部闭合未通过）。失败点与未计算点保留缺口，不补零。此曲线不是时间响应。</p>${renderThrottleCharts(result)}${renderThrottleResponseGains(result)}<section class="th-data"><header><h3>逐点性能与判定</h3><span>点击节流比例查看诊断 · 缺失值以 — 表示</span><button data-throttle-action="export-csv">导出 CSV</button></header><div class="th-table-wrap"><table class="th-points"><thead><tr><th>节流 / %</th><th>目标推力 / kN</th><th>实际推力 / kN</th><th>推力偏差 / %</th><th>室压 / MPa</th><th>比冲 / s</th><th>总流量 / kg·s⁻¹</th><th>数值求解</th><th>系统约束</th><th>主喷管喉部</th></tr></thead><tbody>${data.points.map(point=>`<tr class="${point===chosen?"is-selected":""}" data-point-status="${escape(point.status)}"><td><button class="th-point-select" data-throttle-ratio="${point.throttle_ratio}" aria-pressed="${point===chosen}" aria-label="查看 ${escape(number(point.throttle_ratio*100))}% 节流点">${number(point.throttle_ratio*100)}</button></td><td>${number(scale(point.thrust_target_N,1e-3))}</td><td>${number(scale(metric(point,"thrust_N"),1e-3))}</td><td>${number(errorPercent(point))}</td><td>${number(point.converged===true?scale(point.pc_Pa,1e-6):null)}</td><td>${number(metric(point,"Isp_s"))}</td><td>${number(metric(point,"mdot_total_kg_s"))}</td><td>${numericalStatus(point)}</td><td>${badge(point.assessment?.status||"unknown")}</td><td>${badge(point.geometry?.status||"unknown")}</td></tr>`).join("")}</tbody></table></div></section>${selectedPointDetail(chosen)}${Array.isArray(result.limitations)&&result.limitations.length?`<details class="th-scope" open><summary>模型适用范围与本次计算口径</summary><ul>${result.limitations.map(text=>`<li>${escape(text)}</li>`).join("")}</ul></details>`:""}`;
}

const SVG_STYLE=`svg{font-family:Arial,"Microsoft YaHei",sans-serif;background:#fff;color:#263e52}.th-grid{stroke:#e2e7eb;stroke-width:1}.th-grid-v{stroke-dasharray:3 4}.th-axis{fill:none;stroke:#91a1ae;stroke-width:1}.th-tick{stroke:none;fill:#617584;font:11px Consolas,monospace}.th-axis-label{fill:#617584;font-size:11px}.th-line{fill:none;stroke-width:2;stroke-linejoin:round}.th-dashed{stroke-dasharray:6 4}.th-marker{fill:white;stroke-width:1.8}.th-review-marker{fill:#fff0d6}.th-zero{stroke:#9b8bb4;stroke-dasharray:4 4}.th-failed-marker{fill:none;stroke:#ad5141;stroke-width:1.6}.th-plot-empty{fill:#738391;font-size:12px}.th-export-title{fill:#243e50;font-size:22px;font-weight:700}.th-export-label{fill:#536d80;font-size:12px}.th-export-panel-title{fill:#2d485c;font-size:16px;font-weight:700}`;
export function buildThrottleSvg(result){
  const data=prepareThrottleData(result),width=1220,height=1240;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><style>${SVG_STYLE}</style><rect width="100%" height="100%" fill="#fff"/><text class="th-export-title" x="30" y="36">稳态节流性能分析</text><text class="th-export-label" x="30" y="62">${escape(conditionText(result))}</text><text class="th-export-label" x="30" y="85">离散稳态工况，不表示时间响应 · 缺失 / 无有效工况点断线 · 菱形：需复核</text>${data.panels.map((panel,i)=>{
    const x=25+(i%2)*595,y=112+Math.floor(i/2)*360;
    return `<g transform="translate(${x} ${y})"><rect width="575" height="340" fill="#fff" stroke="#d2dce3"/><text class="th-export-panel-title" x="16" y="26">${escape(panel.label)}</text><svg x="0" y="40" width="570" height="275" viewBox="0 0 570 275">${plot(panel,data).replace(/^<svg[^>]*>/,"").replace(/<\/svg>$/,"")}</svg>${panel.series.map((series,j)=>`<g transform="translate(${20+j*130} 326)"><line x1="0" x2="21" y1="-4" y2="-4" stroke="${series.color}" stroke-width="2"${series.dashed?' stroke-dasharray="5 3"':""}/><text class="th-export-label" x="27" y="0">${escape(series.label)}</text></g>`).join("")}</g>`;
  }).join("")}<text class="th-export-label" x="30" y="1222">来源记录：${escape(result?.source?.job_id||"未记录")} · 数值收敛与系统约束分别判定；参考完整结果与诊断。</text></svg>`;
}
export function buildThrottleCsv(result){
  const fields=["节流比例_pct","目标推力_kN","实际推力_kN","推力偏差_pct","室压_MPa","比冲_s","真空比冲_s","总流量_kg_s","氧化剂流量_kg_s","燃料流量_kg_s","燃烧室温度_K","数值收敛","系统约束","主喷管喉部","综合状态","错误"];
  const cell=value=>{let text=String(value??"");if(typeof value==="string"&&/^\s*[=+@\-\t\r]/.test(text))text="'"+text;return `"${text.replace(/"/g,'""')}"`;};
  const rows=prepareThrottleData(result).points.map(point=>[Number((point.throttle_ratio*100).toPrecision(10)),scale(point.thrust_target_N,1e-3),scale(metric(point,"thrust_N"),1e-3),errorPercent(point),point.converged===true?scale(point.pc_Pa,1e-6):null,metric(point,"Isp_s"),metric(point,"Isp_vac_s"),metric(point,"mdot_total_kg_s"),metric(point,"mdot_ox_kg_s"),metric(point,"mdot_fuel_kg_s"),metric(point,"chamber_temperature_K"),STATUS[numericalState(point)],point.assessment?.status,point.geometry?.status,point.status,typeof point.error==="string"?point.error:point.error?JSON.stringify(point.error):""]);
  return "\uFEFF"+[fields,...rows].map(row=>row.map(cell).join(",")).join("\r\n");
}

function displayedResult(record){
  if(record?.result?.points?.length)return record.result;
  if(record?.progress?.points?.length)return {...record.result,request:record.request,source:{job_id:record.source_job_id||record.request?.source_job_id,revision:record.revision},points:record.progress.points,complete:false};
  return record?.result||null;
}
function progressPanel(record){
  if(!record)return "";
  const progress=record.progress||{},result=displayedResult(record),completed=progress.completed??result?.counts?.completed??result?.points?.length??0,total=record.request?.throttle_steps??result?.counts?.total??0;
  const count=finite(completed)&&finite(total)&&total>0?Math.max(0,Math.min(total,completed)):0;
  return `<div class="th-run-status" aria-live="polite">${badge(record.status)}<span>${escape(finite(total)&&total>0?`${count} / ${total} 个工况已返回`:"等待扫描数据")}</span>${running(record)&&finite(progress.current_ratio)?`<span>当前 ${number(progress.current_ratio*100)}%</span>`:""}<span class="th-record-id" title="${escape(record.id)}">记录 ${escape(String(record.id).slice(0,8))}</span></div>${total>0?`<progress class="th-progress" max="${total}" value="${count}" aria-label="已计算的节流工况">${count} / ${total}</progress>`:""}`;
}

export function mountThrottle(container,{api,getContext,notify=()=>{}}){
  let projectId="",sourceId="",records=[],selected=null,selectedRatio=null,error="",busy=false,live=true,timer=null,sequence=0;
  let form={minimum_percent:"40",point_count:"5"};
  function stop(){if(timer!==null)clearTimeout(timer);timer=null;}
  function syncContext(){
    const context=getContext()||{},id=context.project?.id||"",source=throttleSource(context);
    if(projectId!==id){++sequence;stop();projectId=id;records=[];selected=null;selectedRatio=null;sourceId="";error="";busy=false;form={minimum_percent:"40",point_count:"5"};}
    sourceId=source?.id||"";return context;
  }
  const current=(token,id)=>live&&token===sequence&&getContext()?.project?.id===id;
  function render(){
    const context=syncContext(),source=throttleSource(context),inputs=sourceInputs(context),stale=source&&(context.dirty||source.revision!==context.project?.revision),result=displayedResult(selected);
    const selectedSource=selected?.source_job_id||selected?.request?.source_job_id||result?.source?.job_id,active=records.filter(running).length;
    const sourceText=source?`方案 v${source.revision} · ${date(source.created)} · ${source.id}`:"未选择已完成的设计记录";
    container.innerHTML=`<section class="throttle-workspace" aria-label="稳态节流性能分析"><div class="th-heading"><div><span class="th-kicker">STEADY OPERATING ENVELOPE</span><h2>多工况扫描工作区</h2><p>固定主喷管喉部 · 多工况循环匹配 · 逐点工程约束</p></div><button data-throttle-action="refresh" ${busy?"disabled":""}>刷新记录</button></div><div class="th-source"><b>计算基准</b><span>${escape(sourceText)}</span></div>${!source?notice("先在计算记录中选择一个已完成的稳态设计点。该记录将作为本次扫描的固定基准。","warning"):stale?notice("当前参数或方案版本与所选设计记录不同。扫描使用已保存设计快照；如需使用新参数，请先运行设计点。","warning"):""}<section class="th-settings"><header><h3>扫描设置</h3><span>100% = 所选设计记录的额定工况</span></header><div class="th-settings-grid"><label>最低节流比例 / %<input data-throttle-field="minimum_percent" type="number" min="5" max="99" step="any" value="${escape(form.minimum_percent)}" ${busy?"disabled":""}></label><label>最高节流比例 / %<input value="100" readonly aria-label="最高节流比例固定为100%"></label><label>扫描点数<input data-throttle-field="point_count" type="number" min="2" max="15" step="1" value="${escape(form.point_count)}" ${busy?"disabled":""}></label><div class="th-baseline"><span>基准目标推力 / kN</span><b>${number(scale(inputs.thrust,1e-3))}</b></div><div class="th-baseline"><span>基准室压 / MPa</span><b>${number(scale(inputs.pc,1e-6))}</b></div><div class="th-baseline"><span>环境压力 / MPa</span><b>${number(scale(inputs.ambient_pressure,1e-6))}</b></div></div><div class="th-settings-foot"><p>按扫描比例逐点求解，保持基准主喷管喉部面积。低节流是否可行由各点收敛与约束结果判定；不计算启动、关机或节流过渡。</p><button class="primary" data-throttle-action="run" ${!source||busy||active>=2?"disabled":""}>${busy?"正在提交…":"运行稳态节流扫描"}</button></div>${active>=2?notice("已有两个扫描任务等待或运行，请稍后提交。","warning"):""}</section><section class="th-output"><div class="th-history"><label>扫描记录<select data-throttle-record ${busy?"disabled":""}><option value="">选择已保存的扫描</option>${records.map(record=>`<option value="${escape(record.id)}" ${selected?.id===record.id?"selected":""}>v${escape(record.revision)} · ${escape(date(record.created))} · ${escape(STATUS[record.status]||record.status)}</option>`).join("")}</select></label><button data-throttle-action="export-json" ${selected?"":"disabled"}>导出 JSON</button><button data-throttle-action="cancel" ${running(selected)&&!busy?"":"disabled"}>取消扫描</button></div>${error?notice(error,"error"):""}${selected?.error?notice(typeof selected.error==="string"?selected.error:JSON.stringify(selected.error),"error"):""}${progressPanel(selected)}${selectedSource&&selectedSource!==sourceId?notice("正在展示其他设计记录的历史扫描。上方扫描设置对应当前所选基准，不会改写这条历史结果。","warning"):""}${renderThrottleResult(result,selectedRatio,selected?.status)}</section></section>`;
  }
  function schedule(){stop();if(live&&records.some(running))timer=setTimeout(()=>{refresh().catch(()=>{});},1800);}
  async function refresh(){
    live=true;stop();syncContext();const id=projectId,token=++sequence;render();if(!id)return;
    try{
      const list=await api(`/projects/${encodeURIComponent(id)}/throttles`);if(!current(token,id))return;
      records=Array.isArray(list)?list:[];const next=records.find(record=>record.id===selected?.id)||records[0];
      if(next){const detail=await api(`/throttles/${encodeURIComponent(next.id)}`);if(!current(token,id))return;selected=detail;}else selected=null;
      error="";
    }catch(problem){if(current(token,id))error=problem.message||"无法读取节流扫描记录。";}
    if(current(token,id)){render();schedule();}
  }
  async function chooseRecord(id){
    stop();const token=++sequence,project=projectId;if(!id){selected=null;render();schedule();return;}
    try{const detail=await api(`/throttles/${encodeURIComponent(id)}`);if(current(token,project)){selected=detail;selectedRatio=null;error="";render();schedule();}}
    catch(problem){if(current(token,project)){error=problem.message;render();schedule();}}
  }
  async function run(){
    if(busy)return;const context=syncContext();let request;
    try{request=buildThrottleRequest(context,form);}catch(problem){error=problem.message;render();return;}
    stop();const id=projectId,token=++sequence;busy=true;error="";render();
    try{const created=await api(`/projects/${encodeURIComponent(id)}/throttles`,"POST",request);if(!current(token,id))return;selected={project_id:id,revision:context.record.revision,source_job_id:request.source_job_id,request,status:"queued",...created};selectedRatio=null;records=[selected,...records.filter(record=>record.id!==selected.id)];notify("稳态节流扫描已提交，结果将逐点显示。");}
    catch(problem){if(current(token,id))error=problem.message;}
    finally{if(current(token,id)){busy=false;render();schedule();}}
  }
  async function cancel(){
    if(busy||!running(selected))return;stop();const id=projectId,recordId=selected.id,token=++sequence;busy=true;render();
    try{await api(`/throttles/${encodeURIComponent(recordId)}/cancel`,"POST",{});if(!current(token,id))return;notify("已请求取消扫描；已完成的工况会保留。");busy=false;await refresh();}
    catch(problem){if(current(token,id)){busy=false;error=problem.message;render();schedule();}}
  }
  function download(blob,name){const url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function exportData(format){
    if(!selected)return;const snapshot=selected,result=displayedResult(snapshot),name=`throttle-${String(snapshot.id).replace(/[^a-zA-Z0-9_-]/g,"_")}`;
    try{
      if(format==="json"){download(new Blob([JSON.stringify(snapshot,null,2)],{type:"application/json"}),`${name}.json`);return;}
      if(!result?.points?.length)return;
      if(format==="csv"){download(new Blob([buildThrottleCsv(result)],{type:"text/csv;charset=utf-8"}),`${name}.csv`);return;}
      const svg=buildThrottleSvg(result);
      if(format==="svg"){download(new Blob([svg],{type:"image/svg+xml;charset=utf-8"}),`${name}.svg`);return;}
      const image=new Image();await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error("PNG 生成失败，请使用 SVG 导出。"));image.src=`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;});
      const canvas=document.createElement("canvas");canvas.width=image.naturalWidth*2;canvas.height=image.naturalHeight*2;const ctx=canvas.getContext("2d");if(!ctx)throw Error("浏览器无法生成 PNG，请使用 SVG 导出。");ctx.fillStyle="#fff";ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height);const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));if(!blob)throw Error("PNG 导出失败。");download(blob,`${name}.png`);
    }catch(problem){if(live){error=problem.message;render();}}
  }
  container.addEventListener("input",event=>{const field=event.target.dataset?.throttleField;if(["minimum_percent","point_count"].includes(field))form[field]=event.target.value;});
  container.addEventListener("change",event=>{if(event.target.hasAttribute?.("data-throttle-record"))chooseRecord(event.target.value);});
  container.addEventListener("click",event=>{
    const button=event.target.closest?.("button");if(!button||button.disabled)return;
    if(button.dataset.throttleRatio!==undefined){selectedRatio=Number(button.dataset.throttleRatio);render();return;}
    const action=button.dataset.throttleAction;if(action==="run")run();else if(action==="refresh")refresh();else if(action==="cancel")cancel();else if(action?.startsWith("export-"))exportData(action.slice(7));
  });
  render();return {refresh,sync(){if(live)render();},onLeave(){live=false;++sequence;busy=false;stop();}};
}
