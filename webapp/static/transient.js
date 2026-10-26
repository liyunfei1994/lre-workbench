// A time-domain workspace for the explicitly limited hot-state physical model.
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const num = value => finite(value) ? (value !== 0 && Math.abs(value) < .001 ? value.toExponential(3) : value.toLocaleString("zh-CN", {maximumSignificantDigits:6})) : "—";
const date = value => value ? new Date(value * 1000).toLocaleString("zh-CN", {hour12:false}) : "—";
const active = record => ["queued", "running"].includes(record?.status);
const names = {queued:"排队中",running:"积分中",succeeded:"计算完成",cancelled:"已取消",failed:"执行失败",timed_out:"超时",interrupted:"服务中断",pass:"通过",fail:"未通过",review:"需复核",unknown:"未判定",pending:"等待积分完成"};
const badge = status => `<span class="tr-badge tr-${["pass","fail","failed","review","running"].includes(status) ? status : "neutral"}">${esc(names[status] || status || "未判定")}</span>`;
const notice = text => `<div class="tr-notice">${esc(text)}</div>`;

export function buildTransientRequest(source, setup, values) {
  if (!source?.id || source.status !== "succeeded") throw Error("请选择已完成的稳态设计记录。");
  if (!setup?.ready) throw Error("当前稳态基准不满足瞬态模型初始化条件，请先检查准备度。");
  const request = {source_job_id:source.id};
  for (const [key, value] of Object.entries(setup.defaults || {})) {
    const raw = values[key] ?? value;
    const parameter = (setup.parameters || []).find(item => item.key === key);
    if (typeof value === "number") {
      if (String(raw).trim() === "" || !finite(Number(raw))) throw Error(`${parameter?.label || key} 必须是有限数值。`);
      request[key] = Number(raw);
      if (parameter && ((finite(parameter.min) && request[key] < parameter.min) || (finite(parameter.max) && request[key] > parameter.max))) throw Error(`${parameter.label} 超出本模型允许范围。`);
    } else request[key] = raw;
  }
  return request;
}

const series = (key,label,color,factor=1) => ({key,label,color,factor});
export function transientPanels(result,relative=false) {
  const rows = Array.isArray(result?.samples) ? result.samples : [];
  const panels = [
    {name:"压力响应",unit:"MPa",series:[series("pc_Pa","主燃烧室","#25688c",1e-6),series("gg_pressure_Pa","燃气发生器","#aa7b36",1e-6),series("ox_preburner_pressure_Pa","富氧预燃室","#746898",1e-6),series("fuel_preburner_pressure_Pa","富燃预燃室","#a25f48",1e-6)]},
    {name:"推力响应",unit:"kN",series:[series("thrust_N","当前环境推力","#25688c",1e-3)]},
    {name:"轴系转速",unit:"rpm",series:[series("shaft_ox_rpm","氧侧 / 共轴","#3f88af"),series("shaft_fuel_rpm","燃料侧","#aa7b36")]},
    {name:"供给质量流量",unit:"kg/s",series:[series("mdot_ox_kg_s","氧化剂","#3f88af"),series("mdot_fuel_kg_s","燃料","#aa7b36")]},
    {name:"腔体温度",unit:"K",series:[series("chamber_temperature_K","主燃烧室","#a25f48"),series("gg_temperature_K","燃气发生器","#aa7b36"),series("ox_preburner_temperature_K","富氧预燃室","#746898"),series("fuel_preburner_temperature_K","富燃预燃室","#aa7b36")]},
    {name:"阀门有效面积指令",unit:"% 基准面积",series:[series("fuel_area_ratio","燃料阀","#aa7b36",100),series("ox_area_ratio","氧化剂阀","#3f88af",100)]},
  ];
  return panels.map(panel => ({...panel,unit:relative?"相对初值变化 / %":panel.unit,series:panel.series.filter(s => rows.some(row => finite(row[s.key]))).map(s=>({...s,baseline:relative?rows[0]?.[s.key]:null})).filter(s=>!relative||(finite(s.baseline)&&s.baseline!==0))}));
}

const plotted=(s,value)=>finite(s.baseline)?100*(value/s.baseline-1):value*s.factor;
function chart(panel, rows, index) {
  const w=570,h=280,l=80,r=25,t=25,b=45;
  const validRows=rows.filter(row => finite(row.time_s));
  const times=validRows.map(row=>row.time_s);
  const t0=Math.min(...times),t1=Math.max(...times);
  const vals=panel.series.flatMap(s=>validRows.map(row=>row[s.key]).filter(finite).map(v=>plotted(s,v)));
  const minimum=vals.length?Math.min(...vals):0,maximum=vals.length?Math.max(...vals):1;
  const pad=(maximum-minimum || Math.abs(maximum)*.02 || 1)*.15;
  const lo=minimum-pad,hi=maximum+pad;
  const x=v=>l+(v-t0)/(t1-t0 || 1)*(w-l-r), y=v=>t+(hi-v)/(hi-lo)*(h-t-b);
  const ticks=Array.from({length:5},(_,i)=>i/4);
  const axes=ticks.map(q=>`<line class="tr-grid" x1="${l}" x2="${w-r}" y1="${y(lo+q*(hi-lo))}" y2="${y(lo+q*(hi-lo))}"/><text class="tr-tick" x="${l-9}" y="${y(lo+q*(hi-lo))+4}" text-anchor="end">${esc(num(lo+q*(hi-lo)))}</text><text class="tr-tick" x="${x(t0+q*(t1-t0))}" y="${h-b+20}" text-anchor="middle">${esc(num(t0+q*(t1-t0)))}</text>`).join("");
  const paths=panel.series.map(s=>{
    let pen=false; let path="";
    for(const row of validRows) {
      if(!finite(row[s.key])) {pen=false;continue;}
      path+=`${pen?"L":"M"}${x(row.time_s)},${y(plotted(s,row[s.key]))} `;pen=true;
    }
    return `<path data-series="${s.key}" class="tr-line" stroke="${s.color}" d="${path}"><title>${esc(s.label)}</title></path>`;
  }).join("");
  return `<article class="tr-chart"><div class="tr-chart-heading"><span>${String(index+1).padStart(2,"0")}</span><h3>${esc(panel.name)}</h3></div><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(panel.name)}，时间 s，${esc(panel.unit)}"><title>${esc(panel.name)}</title>${axes}<path class="tr-axis" d="M${l},${t}V${h-b}H${w-r}"/><text class="tr-unit" x="${l}" y="13">${esc(panel.unit)}</text><text class="tr-unit" x="${w/2}" y="${h-4}" text-anchor="middle">时间 / s</text>${paths}</svg><div class="tr-legend">${panel.series.map(s=>`<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("")}</div></article>`;
}

const diagnosticLabels = {status:"守恒闭合判定",nominal_max_scaled_derivative_per_s:"初始状态最大归一化导数 / s⁻¹",max_abs_mass_balance_error_kg:"最大质量账目误差 / kg",max_abs_energy_balance_error_J:"最大能量账目误差 / J",mass_relative_error:"相对质量闭合误差",energy_relative_error:"相对能量闭合误差",basis:"审计口径",sample_count:"有效输出采样数",initial_thrust_N:"初始推力 / N",final_thrust_N:"末点推力 / N",max_thrust_N:"最大推力 / N",min_thrust_N:"最小推力 / N",initial_pc_Pa:"初始室压 / Pa",final_pc_Pa:"末点室压 / Pa",last_time_s:"最后有效时刻 / s",interpretation:"指标解释"};
function objectTable(value) {
  if(!value || typeof value !== "object") return notice("尚未返回诊断数据。");
  const items=Array.isArray(value)?value.map((v,i)=>[v.label || v.name || String(i+1),v]):Object.entries(value);
  return `<div class="tr-table-wrap"><table><thead><tr><th>项目</th><th>结果 / 说明</th></tr></thead><tbody>${items.map(([key,v])=>`<tr><td>${esc(v?.label||diagnosticLabels[key]||key)}</td><td>${key==="status"?badge(v):typeof v === "object" && v !== null ? esc(JSON.stringify(v,null,2)) : finite(v)?num(v):esc(v)}</td></tr>`).join("")}</tbody></table></div>`;
}

function modelDetails(model) {
  if(!model) return "";
  return `<p>${esc(model.title||"零维热态模型")}</p><div class="tr-equations">${(model.equations||[]).map(eq=>`<code>${esc(eq)}</code>`).join("")}</div><p>试验标定：${model.experiment_validated===true?"已提供验证记录":"尚未进行；当前为研究模型"}。完整容腔、轴系和喷管参数保存在 JSON 导出中。</p>`;
}

export function renderTransientResult(record, selectedIndex=0,relative=false) {
  const result=record?.result;
  const rows=Array.isArray(result?.samples)?result.samples:[];
  if(!rows.length) return `<div class="tr-empty"><span>TIME DOMAIN / 0D</span><h3>${active(record)?"正在建立并积分物理状态方程":record?"本记录尚无可显示的时间采样":"从已收敛的热态设计点开始"}</h3><p>阀门面积指令改变后，腔体储存量和转子动能随时间演化。冷态点火、预冷充填和燃烧不稳定性尚未建模。</p></div>`;
  const row=rows[Math.min(Math.max(0,selectedIndex),rows.length-1)];
  const fields=[["pc_Pa","室压 / MPa",1e-6],["thrust_N","推力 / kN",1e-3],["chamber_temperature_K","燃气温度 / K",1],["shaft_ox_rpm","氧侧或共轴转速 / rpm",1],["shaft_fuel_rpm","燃料侧转速 / rpm",1],["mdot_ox_kg_s","氧化剂供给 / kg/s",1],["mdot_fuel_kg_s","燃料供给 / kg/s",1]];
  return `<div class="tr-result-heading"><div><h2>热态扰动时间响应</h2><p>${rows.length} 个输出采样 · 曲线来自时间积分 · 阀门面积比例不等于推力指令</p></div><div><button data-transient-action="absolute" aria-pressed="${!relative}">绝对值</button><button data-transient-action="relative" aria-pressed="${relative}">相对初值 / %</button><button data-transient-action="csv">导出 CSV</button><button data-transient-action="svg">导出 SVG</button></div></div>${notice("研究模型：热态小范围扰动，采用局部部件关系与冻结热物性。数值通过与守恒闭合不代表试验验证通过。")}${relative?notice("各曲线以自身 t=0 值归一化：ΔY / Y₀ × 100%。下方时间截面及 CSV 仍保留实际物理量。"):""}<div class="tr-chart-grid">${transientPanels(result,relative).map((panel,i)=>chart(panel,rows,i)).join("")}</div><section class="tr-sample"><h3>时间截面检查</h3><label>采样时刻：${num(row.time_s)} s<input data-transient-sample type="range" min="0" max="${rows.length-1}" step="1" value="${rows.indexOf(row)}" aria-label="查看时间采样"></label><div class="tr-sample-grid">${fields.filter(([key])=>finite(row[key])).map(([key,label,factor])=>`<div><span>${label}</span><b>${num(row[key]*factor)}</b></div>`).join("")}</div></section><section class="tr-diagnostics"><h3>守恒与数值审计</h3>${objectTable(result.audits)}<h3>响应指标</h3>${objectTable(result.metrics)}</section>${result.termination_reason&&result.termination_reason!=="completed"?notice(result.termination_reason):""}<details class="tr-model" open><summary>模型方程、假设与适用范围</summary>${modelDetails(result.model_description)}${Array.isArray(result.limitations)?`<ul>${result.limitations.map(text=>`<li>${esc(text)}</li>`).join("")}</ul>`:""}</details>`;
}

export function buildTransientCsv(result) {
  const rows=result?.samples||[];
  const keys=[...new Set(rows.flatMap(row=>Object.keys(row)))];
  const cell=v=>`"${String(v??"").replace(/"/g,'""')}"`;
  return "\uFEFF"+[keys,...rows.map(row=>keys.map(key=>finite(row[key])?row[key]:typeof row[key]==="string"?"'"+row[key]:""))].map(row=>row.map(cell).join(",")).join("\r\n");
}

export function buildTransientSvg(result,relative=false) {
  const style="text{font-family:Arial,'Microsoft YaHei',sans-serif;fill:#4a6274;font-size:11px}.tr-grid{stroke:#e1e6eb}.tr-line{fill:none;stroke-width:2}.tr-axis{fill:none;stroke:#9aaab8}";
  const panels=transientPanels(result,relative),rows=result.samples||[];
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1220" height="1150" viewBox="0 0 1220 1150"><style>${style}</style><rect width="1220" height="1150" fill="white"/><text x="30" y="35" style="font-size:22px">热态工作点扰动 · 零维瞬态研究模型</text><text x="30" y="59">冻结热物性 / 局部部件关系 / 未经试验标定 · 来源 ${esc(result.source?.job_id||"—")}</text>${panels.map((panel,i)=>{const svg=chart(panel,rows,i).match(/<svg[\s\S]*?<\/svg>/)?.[0]||"";return `<g transform="translate(${25+(i%2)*600} ${85+Math.floor(i/2)*350})"><text x="12" y="20" style="font-size:16px">${esc(panel.name)}</text><svg x="0" y="35" width="570" height="280" viewBox="0 0 570 280">${svg.replace(/^<svg[^>]*>/,"").replace(/<\/svg>$/,"")}</svg>${panel.series.map((s,j)=>`<text x="${15+j*133}" y="338" style="fill:${s.color}">${esc(s.label)}</text>`).join("")}</g>`;}).join("")}</svg>`;
}

export function transientRunBlockReason(source,setup,busy,activeCount=0,error="") {
  if (!source) return "尚未选择已完成的稳态设计记录。";
  if (!setup) return error ? "初始化检查未完成，请恢复连接后刷新记录。" : "正在检查所选稳态记录的初始化条件。";
  if (!setup.ready) return "运行已禁用：所选稳态记录未通过瞬态初始化检查，请先处理初始化准备度中的阻断项。";
  if (busy) return "正在提交任务，请稍候。";
  if (activeCount>=2) return "本方案已有 2 个等待或运行中的瞬态任务，请等待完成或取消任务。";
  return "";
}

export function renderTransientReadiness(setup,source) {
  if (!setup) return notice(source?"正在读取来源设计点与模型参数。":"先完成并选中稳态设计点，再建立瞬态初始条件。");
  if (setup.ready) return `<p>稳态基准可用于本局部模型初始化。下列转动惯量、预燃室容积与部件斜率仍需工程数据标定。</p>`;
  const checks=(source?.result?.assessment?.checks||[]).filter(item=>item.status!=="pass");
  const crossfeed=checks.some(item=>String(item.key||"").startsWith("crossflow_"));
  const criterion=item=>`${item.criterion==="minimum"?"≥":item.criterion==="maximum"?"≤":"判据"} ${num(item.limit)} ${esc(item.unit||"")}`;
  return `<div class="tr-readiness-block"><strong>需要先修正稳态基准</strong><ul class="tr-issues">${(setup.issues||[]).map(item=>`<li>${esc(typeof item==="string"?item:JSON.stringify(item))}</li>`).join("")}</ul>${checks.length?`<div class="tr-table-wrap"><table><thead><tr><th>未通过 / 未知约束</th><th>本记录数值</th><th>通过判据</th><th>物理含义</th></tr></thead><tbody>${checks.map(item=>`<tr><td>${esc(item.label||item.key)}</td><td>${num(item.value)} ${esc(item.unit||"")}</td><td>${criterion(item)}</td><td>${esc(item.detail||"请查看该稳态记录的系统约束校核。")}</td></tr>`).join("")}</tbody></table></div>`:""}${crossfeed?`<p><b>供给压力裕度 = 取流点压力 − 阀路损失 − 预燃室压力 − 喷注压降。</b>负值表示当前假设的取流路径缺少压头；数值收敛不能替代这项检查。改变本页的仿真时长或扰动幅度不会修复该稳态记录。</p><p>应先核对取流位置、冷却与阀路压降、预燃室压力和喷注压降，再重新求解系统匹配。若采用补压泵，需要将它的流量、压升及功率纳入模型。</p>`:""}<ol><li>返回稳态性能分析，检查这条计算记录的约束和警告。</li><li>修正模型或参数后重新运行设计点，选择新生成且约束通过的记录。</li><li>返回瞬态响应分析；初始化检查通过后，运行按钮会自动启用。</li></ol><button data-transient-action="inspect-source">查看稳态基准与约束</button><p class="tr-readiness-note">本页使用所选计算记录的冻结结果；修改左侧参数后，需重新计算并选择新记录。</p></div>`;
}

export function mountTransient(container,{api,getContext,notify=()=>{},onInspectSource=()=>{}}) {
  let projectId="",sourceId="",setup=null,values={},relative=false,records=[],selected=null,selectedIndex=0,error="",busy=false,live=true,timer=null,seq=0;
  const context=()=>getContext()||{};
  const source=()=>{const c=context();return c.record?.status==="succeeded"&&c.record?.project_id===c.project?.id?c.record:null;};
  function stop(){if(timer!==null)clearTimeout(timer);timer=null;}
  function syncContext(){
    const p=context().project?.id||"",s=source()?.id||"";
    if(p!==projectId||s!==sourceId){++seq;stop();if(p!==projectId){records=[];selected=null;selectedIndex=0;}projectId=p;sourceId=s;setup=null;values={};error="";busy=false;}
  }
  const current=(token,p,s)=>live&&seq===token&&context().project?.id===p&&(source()?.id||"")===s;
  function fields(keys){return (setup?.parameters||[]).filter(p=>keys?keys.includes(p.key):!["duration_s","sample_count","command_time_s","command_ramp_s","fuel_area_ratio","ox_area_ratio"].includes(p.key)).map(p=>`<label>${esc(p.label)}${p.unit?` / ${esc(p.unit)}`:""}<input type="number" data-transient-field="${esc(p.key)}" value="${esc(values[p.key]??setup.defaults[p.key])}" ${finite(p.min)?`min="${p.min}"`:""} ${finite(p.max)?`max="${p.max}"`:""} step="${p.step||"any"}"><small>${esc(p.source||"")}${p.description?` · ${esc(p.description)}`:""}</small></label>`).join("");}
  function render(){
    const c=context(),s=source(),progress=selected?.progress||{},result=selected?.result;
    const returned=result?.samples?.length||0,maximum=selected?.request?.sample_count||0;
    const completed=finite(progress.completed)?progress.completed:returned;
    const blockReason=transientRunBlockReason(s,setup,busy,records.filter(active).length,error);
    container.innerHTML=`<section class="transient-workspace" aria-label="瞬态响应研究工作区"><div class="tr-intro"><div><span>TIME DOMAIN / PHYSICS MODEL</span><h2>热态工作点扰动</h2><p>质量储存 · 热能储存 · 轴系转动惯性 · 有效流通面积</p></div><button data-transient-action="refresh">刷新记录</button></div><div class="tr-scope-strip"><b>当前能力</b><span>GG 单轴 / 双轴、FFSC 的热态局部扰动</span><span>冷态启动与关机：待建立点火、两相充填和实测部件模型</span></div><div class="tr-source"><b>稳态基准</b><span>${s?`方案 v${s.revision} · ${date(s.created)} · ${esc(s.id)}`:"请选择已完成的稳态设计记录"}</span></div>${s&&(c.dirty||s.revision!==c.project?.revision)?notice("瞬态分析使用所选记录的冻结输入，当前未保存参数不参与瞬态计算。"):""}${error?notice(error):""}<section class="tr-setup"><div class="tr-section-heading"><h3>初始化准备度</h3>${setup?.ready?badge("pass"):setup?'<span class="tr-badge tr-fail">不可初始化</span>':badge("unknown")}</div>${renderTransientReadiness(setup,s)}<h3>仿真时间与阀门扰动</h3><div class="tr-field-grid">${fields(["duration_s","sample_count","command_time_s","command_ramp_s","fuel_area_ratio","ox_area_ratio"])}</div><details class="tr-parameters"><summary>模型参数与来源 · 容积、惯量、局部特性</summary><div class="tr-field-grid">${fields(null)}</div></details><div class="tr-run-row"><p id="transient-run-status" role="status">${blockReason?esc(blockReason):"初态为已经点火且达到平衡的设计点。阀门面积扰动不是推力闭环控制。"}</p><button class="primary" data-transient-action="run" ${blockReason?'disabled aria-describedby="transient-run-status"':""}>${busy?"提交中…":"运行热态瞬态分析"}</button></div></section><section class="tr-output"><div class="tr-history"><label>瞬态计算记录<select data-transient-record><option value="">选择已保存的分析</option>${records.map(r=>`<option value="${esc(r.id)}" ${selected?.id===r.id?"selected":""}>v${r.revision} · ${date(r.created)} · ${esc(names[r.status]||r.status)}</option>`).join("")}</select></label><button data-transient-action="json" ${selected?"":"disabled"}>导出 JSON</button><button data-transient-action="cancel" ${active(selected)&&!busy?"":"disabled"}>取消分析</button></div>${selected?`<div class="tr-job-state">${badge(selected.status==="succeeded"&&result?.complete===false?"review":selected.status)}<span>${esc(progress.phase||"")}</span><span>${returned} 个时间采样</span><small>记录 ${esc(selected.id.slice(0,8))}</small></div>`:""}${maximum?`<progress max="${maximum}" value="${Math.min(maximum,completed)}" aria-label="瞬态积分进度"></progress>`:""}${selected?.error?notice(selected.error):""}${selected?.request?.source_job_id&&selected.request.source_job_id!==sourceId?notice("当前显示历史记录；其输入与上方新任务设置相互独立。"):""}${renderTransientResult(selected,selectedIndex,relative)}</section></section>`;
  }
  function schedule(){stop();if(live&&records.some(active))timer=setTimeout(()=>refresh(),1500);}
  async function refresh(){
    live=true;stop();syncContext();const p=projectId,s=sourceId,token=++seq;render();if(!p)return;
    try{
      const [list,config]=await Promise.all([api(`/projects/${encodeURIComponent(p)}/transients`),s&&!setup?api(`/projects/${encodeURIComponent(p)}/transients/setup?source_job_id=${encodeURIComponent(s)}`):Promise.resolve(setup)]);
      if(!current(token,p,s))return;
      if(config&&!setup){setup=config;values={...config.defaults};}
      records=Array.isArray(list)?list:[];const next=records.find(r=>r.id===selected?.id)||records[0];
      if(next){const detail=await api(`/transients/${encodeURIComponent(next.id)}`);if(!current(token,p,s))return;selected=detail;}else selected=null;
      error="";
    }catch(e){if(current(token,p,s))error=e.message;}
    if(current(token,p,s)){render();schedule();}
  }
  async function run(){
    if(busy)return;syncContext();let request;
    try{request=buildTransientRequest(source(),setup,values);}catch(e){error=e.message;render();return;}
    const p=projectId,s=sourceId,token=++seq;stop();busy=true;error="";render();
    try{const created=await api(`/projects/${encodeURIComponent(p)}/transients`,"POST",request);if(!current(token,p,s))return;selected={...created,request,revision:source().revision,created:Date.now()/1000};records=[selected,...records];selectedIndex=0;notify("瞬态积分任务已提交。");}
    catch(e){if(current(token,p,s))error=e.message;}
    finally{if(current(token,p,s)){busy=false;render();schedule();}}
  }
  async function choose(id){stop();const p=projectId,s=sourceId,token=++seq;if(!id){selected=null;render();schedule();return;}try{const detail=await api(`/transients/${encodeURIComponent(id)}`);if(current(token,p,s)){selected=detail;selectedIndex=0;error="";render();schedule();}}catch(e){if(current(token,p,s)){error=e.message;render();schedule();}}}
  async function cancel(){if(!active(selected)||busy)return;const p=projectId,s=sourceId,token=++seq;stop();busy=true;render();try{await api(`/transients/${encodeURIComponent(selected.id)}/cancel`,"POST",{});if(!current(token,p,s))return;busy=false;await refresh();}catch(e){if(current(token,p,s)){busy=false;error=e.message;render();schedule();}}}
  function download(format){
    if(!selected)return;const result=selected.result;
    if(format!=="json"&&!result?.samples?.length)return;
    const data=format==="json"?JSON.stringify(selected,null,2):format==="csv"?buildTransientCsv(result):buildTransientSvg(result,relative);
    const blob=new Blob([data],{type:format==="json"?"application/json":format==="csv"?"text/csv;charset=utf-8":"image/svg+xml;charset=utf-8"});
    const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=`transient-${selected.id}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  container.addEventListener("input",e=>{if(e.target.dataset?.transientField)values[e.target.dataset.transientField]=e.target.value;});
  container.addEventListener("change",e=>{if(e.target.hasAttribute?.("data-transient-record"))choose(e.target.value);if(e.target.hasAttribute?.("data-transient-sample")){selectedIndex=Number(e.target.value);render();}});
  container.addEventListener("click",e=>{const b=e.target.closest?.("button");if(!b||b.disabled)return;const a=b.dataset.transientAction;if(a==="run")run();else if(a==="refresh")refresh();else if(a==="cancel")cancel();else if(a==="inspect-source")onInspectSource();else if(a==="absolute"||a==="relative"){relative=a==="relative";render();}else if(["json","csv","svg"].includes(a))download(a);});
  syncContext();render();return {refresh,sync(){if(live){syncContext();render();}},onLeave(){live=false;++seq;busy=false;stop();}};
}
