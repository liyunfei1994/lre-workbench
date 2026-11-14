// Append-only solver observations. No values are inferred from final summaries,
// progress percentages, or the editor's current (possibly changed) parameters.
const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const finite=value=>typeof value==="number"&&Number.isFinite(value);
const active=status=>["queued","running"].includes(status);
const statuses={queued:"等待计算",running:"计算中",succeeded:"计算完成",failed:"执行失败",cancelled:"已取消",timed_out:"计算超时",interrupted:"服务中断"};
const layers={inner:"循环内层",thermal:"热状态闭合",preburner:"预燃室温度",continuation:"延续求解",task:"任务"};
const evaluationRoles={initial:"初始状态求值",finite_difference:"雅可比差分求值",trial:"试探步求值",trust_region_trial:"信赖域试探求值"};
const quantities={
  acceptance_norm:{label:"残差 / 允许误差（最大值）",unit:"1"},
  thrust_normalized_residual:{label:"推力残差 / 允许误差",unit:"1"},
  ox_power_normalized_residual:{label:"氧侧功率残差 / 允许误差",unit:"1"},
  fuel_power_normalized_residual:{label:"燃料侧功率残差 / 允许误差",unit:"1"},
  thrust_residual_N:{label:"推力方程残差",unit:"N"},
  ox_power_residual_W:{label:"氧侧功率平衡残差",unit:"W"},
  fuel_power_residual_W:{label:"燃料侧功率平衡残差",unit:"W"},
  mass_flow_kg_s:{label:"推进剂总质量流量",unit:"kg/s"},
  ox_pressure_ratio:{label:"氧侧涡轮压比",unit:"1"},
  fuel_pressure_ratio:{label:"燃料侧涡轮压比",unit:"1"},
  thrust_N:{label:"推力",unit:"N"},Isp_s:{label:"比冲",unit:"s"},
  chamber_temperature_K:{label:"主燃烧室温度",unit:"K"},
  residual_K:{label:"温度闭合残差",unit:"K"},
  ox_error_K:{label:"富氧预燃室温度偏差",unit:"K"},
  fuel_error_K:{label:"富燃预燃室温度偏差",unit:"K"},
  stage_ratio:{label:"延续系数",unit:"1"},reached_ratio:{label:"已通过的延续系数",unit:"1"},
};
const number=value=>!finite(value)?"—":value===0?"0":Math.abs(value)>=1e5||Math.abs(value)<1e-3?value.toExponential(3):Number(value.toPrecision(6)).toString();
const date=value=>finite(value)?new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}).format(new Date(value*1000)):"时间未记录";
const isPoint=event=>event?.kind==="point";
const runKey=event=>String(event.run_id??`${event.layer}:unidentified`);

/** Preserve run boundaries and missing observations instead of bridging gaps. */
export function monitorSegments(events,{layer="inner",key="acceptance_norm",run="all"}={}){
  const runs=new Map();
  for(const event of events){
    if(event.layer!==layer||!isPoint(event)||(run!=="all"&&runKey(event)!==run))continue;
    const id=runKey(event);if(!runs.has(id))runs.set(id,{id,segments:[],open:null});
    const item=runs.get(id),value=event.values?.[key];
    if(!finite(value)||!finite(event.seq)){item.open=null;continue;}
    if(!item.open){item.open=[];item.segments.push(item.open);}
    item.open.push({x:event.seq,y:value,event});
  }
  return [...runs.values()].flatMap(item=>item.segments.map(points=>({run:item.id,points})));
}

/** Retain spikes and endpoints when a long history exceeds SVG display density. */
export function monitorDisplayPoints(points,limit=500){
  if(points.length<=limit)return points;
  const chosen=new Set([0,points.length-1]),bucket=Math.ceil((points.length-2)/Math.max(1,Math.floor((limit-2)/2)));
  for(let start=1;start<points.length-1;start+=bucket){
    let low=start,high=start;
    for(let i=start;i<Math.min(points.length-1,start+bucket);i++){if(points[i].y<points[low].y)low=i;if(points[i].y>points[high].y)high=i;}
    chosen.add(low);chosen.add(high);
  }
  return [...chosen].sort((a,b)=>a-b).map(index=>points[index]);
}

export function renderMonitorChart(events,{layer="inner",key="acceptance_norm",keys=[key],run="all",log=false,absolute=false,title}={}){
  const segments=keys.flatMap(series=>monitorSegments(events,{layer,key:series,run}).map(segment=>({...segment,series,points:segment.points.map(point=>({...point,y:absolute?Math.abs(point.y):point.y}))}))),points=segments.flatMap(segment=>segment.points),quantity=quantities[key]||{label:key,unit:""};
  const name=title||quantity.label;
  if(!points.length)return `<div class="sm-empty-chart"><strong>${esc(name)}</strong><p>尚无已记录的有效采样点。</p></div>`;
  const positive=points.filter(point=>point.y>0),useLog=log&&positive.length>0&&points.every(point=>point.y>=0),zeros=points.filter(point=>point.y===0).length;
  // Exact zeros are drawn as distinct triangles on a labelled boundary, never
  // replaced by a fabricated positive residual or connected to the log curve.
  const transformed=points.filter(point=>!useLog||point.y>0).map(point=>useLog?Math.log10(point.y):point.y);
  let yLow=transformed.reduce((low,value)=>Math.min(low,value),Infinity),yHigh=transformed.reduce((high,value)=>Math.max(high,value),-Infinity);
  const tolerances=points.map(point=>key==="acceptance_norm"?1:key==="residual_K"?(point.event.residual_tolerance_K??point.event.tolerance):null).filter(finite);
  const tolerance=tolerances.length&&tolerances.every(value=>value===tolerances[0])?tolerances[0]:null;
  if(tolerance!==null&&(!useLog||tolerance>0)){const tick=useLog?Math.log10(tolerance):tolerance;yLow=Math.min(yLow,tick);yHigh=Math.max(yHigh,tick);}
  const pad=yHigh===yLow?(useLog?1:Math.max(Math.abs(yLow)*.1,1)):(yHigh-yLow)*.08;yLow-=pad;yHigh+=pad;
  const xLow=points.reduce((low,point)=>Math.min(low,point.x),Infinity),xHigh=points.reduce((high,point)=>Math.max(high,point.x),-Infinity);
  const w=720,h=270,left=83,right=24,top=24,bottom=49,plotW=w-left-right,plotH=h-top-bottom;
  const x=value=>left+(xHigh===xLow?.5:(value-xLow)/(xHigh-xLow))*plotW;
  const y=value=>top+(yHigh-(useLog?Math.log10(value):value))/(yHigh-yLow)*plotH;
  let sampled=false;
  const paths=segments.map((segment,index)=>{
    const shown=monitorDisplayPoints(segment.points);sampled||=shown.length<segment.points.length;
    const color=["#247aa4","#a16a28","#358574","#82629c"][(keys.length>1?keys.indexOf(segment.series):index)%4];
    let open=false,path="";
    for(const point of shown){if(useLog&&point.y===0){open=false;continue;}path+=`${open?"L":"M"}${x(point.x).toFixed(2)},${y(point.y).toFixed(2)} `;open=true;}
    return `<path d="${path}" fill="none" stroke="${color}" stroke-width="2"/>${shown.map(point=>{const px=x(point.x),py=useLog&&point.y===0?top+plotH:y(point.y),description=`记录 ${point.x} · ${segment.run} · 迭代 ${point.event.iteration??"—"} · ${quantities[segment.series]?.label||segment.series} ${number(point.y)} ${quantity.unit}`;return useLog&&point.y===0?`<path d="M${px-4},${py+3}L${px+4},${py+3}L${px},${py-4}Z" fill="${color}"><title>${esc(description)}（精确零值）</title></path>`:`<circle cx="${px}" cy="${py}" r="2.4" fill="${color}"><title>${esc(description)}</title></circle>`;}).join("")}`;
  }).join("");
  const grid=Array.from({length:5},(_,i)=>{const raw=yLow+(yHigh-yLow)*i/4,value=useLog?10**raw:raw,py=top+plotH-plotH*i/4;return `<line x1="${left}" y1="${py}" x2="${w-right}" y2="${py}" class="sm-grid"/><text x="${left-8}" y="${py+4}" text-anchor="end">${esc(number(value))}</text>`;}).join("");
  const ticks=Array.from({length:xLow===xHigh?1:5},(_,i)=>Math.round(xLow+(xHigh-xLow)*i/4)).filter((value,i,list)=>list.indexOf(value)===i).map(value=>`<text x="${x(value)}" y="${h-28}" text-anchor="middle">${value}</text>`).join("");
  const threshold=tolerance!==null&&(!useLog||tolerance>0)?`<line x1="${left}" y1="${y(tolerance)}" x2="${w-right}" y2="${y(tolerance)}" class="sm-threshold"/><text x="${w-right-5}" y="${y(tolerance)-5}" text-anchor="end" class="sm-threshold-label">准则 ${esc(number(tolerance))}</text>`:"";
  const legend=keys.length>1?`<div class="sm-legend">${keys.map((series,index)=>`<span><i class="sm-series-${index%4}"></i>${esc(quantities[series]?.label||series)}</span>`).join("")}</div>`:"";
  return `<figure class="sm-chart"><figcaption><strong>${esc(name)}</strong><span>${esc(quantity.unit)} · ${useLog?"对数坐标":"线性坐标"}</span></figcaption>${legend}<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(name)}，${points.length} 个实际采样点，横轴为记录序号"><title>${esc(name)}，独立求解轮次不连线</title>${grid}${threshold}${paths}${ticks}<text x="${left+plotW/2}" y="${h-5}" text-anchor="middle">记录序号（按实际采样顺序）</text></svg>${zeros&&useLog?`<p class="sm-note">△ 表示精确零值（${zeros} 点），置于图底边界并断开对数曲线。</p>`:""}${sampled?'<p class="sm-note">长序列按区间极值与端点抽样显示；导出文件保留全部原始记录。</p>':""}</figure>`;
}

/** Independent polling lifecycle, also used by browser and deterministic tests. */
export function createSolverMonitorFeed({api,onChange=()=>{},setTimer=setTimeout,clearTimer=clearTimeout,now=Date.now}={}){
  let generation=0,timer=null,pending=false,disposed=false;
  let data={projectId:null,jobId:null,job:null,events:[],nextSeq:0,visible:false,loading:false,error:"",complete:false,lastReceivedAt:null};
  const notify=()=>onChange({...data});
  const stopTimer=()=>{if(timer!==null){clearTimer(timer);timer=null;}};
  function schedule(delay=1000){stopTimer();if(!disposed&&data.visible&&data.jobId&&!data.complete)timer=setTimer(()=>{timer=null;poll();},delay);}
  async function poll(){
    if(disposed||pending||!data.visible||!data.jobId||data.complete)return;
    const ticket=generation,projectId=data.projectId,jobId=data.jobId;pending=true;data.loading=true;notify();
    try{
      const response=await api(`/jobs/${encodeURIComponent(jobId)}/monitor?after=${data.nextSeq}&limit=500`);
      if(disposed||ticket!==generation)return;
      if(response?.job?.id!==jobId||response.job.project_id!==projectId)throw new Error("监控记录与当前方案不一致。");
      if(response.schema_version!==1||!Array.isArray(response.events))throw new Error("监控数据格式不受支持。");
      const incoming=response.events.filter(event=>Number.isSafeInteger(event.seq)&&event.seq>data.nextSeq).sort((a,b)=>a.seq-b.seq);
      const unique=incoming.filter((event,index)=>index===0||event.seq!==incoming[index-1].seq);
      const last=unique.at(-1)?.seq??data.nextSeq;
      if(!Number.isSafeInteger(response.next_seq)||response.next_seq<last||response.next_seq<data.nextSeq)throw new Error("监控记录游标无效。");
      if(response.has_more&&response.next_seq===data.nextSeq)throw new Error("监控记录读取未取得进展。");
      data={...data,job:response.job,events:[...data.events,...unique],nextSeq:response.next_seq,error:"",lastReceivedAt:now(),complete:!active(response.job.status)&&!response.has_more};
      // Drain backlogs in separate turns so input and navigation remain responsive.
      schedule(response.has_more?0:1000);
    }catch(error){if(!disposed&&ticket===generation){data.error=error?.message||"监控数据读取失败。";schedule(1500);}}
    finally{if(!disposed&&ticket===generation){pending=false;data.loading=false;notify();}}
  }
  return {
    update({projectId,jobId,job=null,visible=false}){
      if(disposed)return;
      if(projectId!==data.projectId||jobId!==data.jobId){generation++;stopTimer();pending=false;data={projectId,jobId,job,events:[],nextSeq:0,visible:Boolean(visible),loading:false,error:"",complete:false,lastReceivedAt:null};notify();}
      const wasVisible=data.visible;data.visible=Boolean(visible);
      if(!data.visible){stopTimer();return;}
      if(!jobId)return;
      if(!wasVisible&&!data.complete)poll();else if(!pending&&!data.complete&&timer===null)poll();
    },
    refresh(){if(disposed)return;data.complete=false;stopTimer();poll();},
    snapshot(){return {...data,events:[...data.events]};},
    dispose(){disposed=true;generation++;stopTimer();pending=false;},
  };
}

function renderRuns(events,selected){
  const seen=new Map();for(const event of events)if(event.layer==="inner"&&isPoint(event)&&!seen.has(runKey(event)))seen.set(runKey(event),event);
  return '<option value="all">全部内层轮次（分段显示）</option>'+[...seen].map(([id,event])=>{const c=event.context||{};return `<option value="${esc(id)}"${selected===id?" selected":""}>${esc(id)} · 预燃室 ${c.preburner_iteration??"—"} / 热状态 ${c.thermal_iteration??"—"} / 延续 ${c.continuation_attempt??"—"}</option>`;}).join("");
}
function runState(rows){
  const last=rows.at(-1),end=[...rows].reverse().find(event=>event.kind==="end");
  if(!last)return "尚无记录";
  if(end===last)return {pass:"满足该层准则",fail:"未满足该层准则",passed:"满足该层准则",converged:"满足该层准则",failed:"未满足该层准则",disabled:"本次未启用",skipped:"本次未启用",interrupted:"该层已中断"}[end.status]||end.termination_reason||"该层已结束";
  return isPoint(last)?`已记录 ${rows.filter(isPoint).length} 点`:"已开始";
}
export function monitorLayerState(events,layer){
  const rows=events.filter(event=>event.layer===layer);
  const latestRun=items=>{const id=items.at(-1)?.run_id;return items.filter(event=>event.run_id===id);};
  if(layer==="thermal"){
    const model=rows.filter(event=>event.context?.scope==="model_evaluation"),outer=rows.filter(event=>event.context?.scope==="outer_feedback");
    return `最近模型求值：${runState(latestRun(model))}；外层热反馈：${runState(latestRun(outer))}`;
  }
  return `${layer==="inner"?"最近轮次：":""}${runState(latestRun(rows))}`;
}
function detailsTable(events){
  const rows=events.slice(-30).reverse();
  return `<details class="sm-events"><summary>最近记录与终止信息（${events.length} 条）</summary><div class="sm-table-scroll"><table><thead><tr><th>序号 / 时间</th><th>层级 / 轮次</th><th>记录内容</th></tr></thead><tbody>${rows.map(event=>`<tr><td>${event.seq}<br><span>${number(event.elapsed_seconds)} s</span></td><td>${esc(layers[event.layer]||event.layer)}<br><span>${esc(event.run_id||"—")} · ${esc(event.iteration??"—")}</span></td><td>${esc(event.termination_reason||event.error||event.message||({start:"开始",end:"结束",point:"采样"}[event.kind])||event.kind)}${event.status?` · ${esc(event.status)}`:""}${event.values?`<div class="sm-values">${Object.entries(event.values).filter(([,value])=>finite(value)).map(([key,value])=>`${esc(quantities[key]?.label||key)}: ${esc(number(value))} ${esc(event.units?.[key]||quantities[key]?.unit||"")}`).join("； ")}</div>`:""}</td></tr>`).join("")}</tbody></table></div><p class="sm-note">此表列出最近 30 条。导出文件包含已接收的原始记录；终止并读取完成后可导出完整过程。</p></details>`;
}

export function mountSolverMonitor(host,{api,onSelectRecord=()=>{},setTimer,clearTimer,now}={}){
  if(!host)return {update(){},dispose(){}};
  let state={},data={events:[],job:null},run="all",thermalRun="latest",metric="mass_flow_kg_s",disposed=false,chartKey="";
  host.innerHTML=`<header class="sm-header"><div><h2>求解监控</h2><p>分层残差与关键物理量</p></div><span data-sm-state role="status"></span></header><div class="sm-source"><label>监控记录<select data-sm-record aria-label="选择监控计算记录"></select></label><div data-sm-version></div><p data-sm-connection role="status"></p><div class="sm-source-actions"><button type="button" data-sm-refresh>刷新监控</button><button type="button" data-sm-export>导出监控记录</button></div></div><div class="sm-body"><div data-sm-layer-state class="sm-layer-state"></div><p class="sm-note">任务执行状态与数值收敛分别记录；各层使用独立准则。曲线只连接同一轮求解的实际采样点。</p><div class="sm-chart-controls"><label>内层轮次<select data-sm-run aria-label="选择内层求解轮次"><option value="all">全部内层轮次（分段显示）</option></select></label><label>关键物理量<select data-sm-metric aria-label="选择监控物理量"></select></label></div><div data-sm-charts></div><div class="sm-thermal-controls" data-sm-thermal-controls hidden><label>热闭合求值轮次<select data-sm-thermal-run aria-label="选择热闭合求值轮次"><option value="latest">最近一次求值</option></select></label><p class="sm-note" data-sm-thermal-context></p></div><div data-sm-thermal-chart></div><div data-sm-events></div></div>`;
  const find=selector=>host.querySelector(selector);
  const write=(selector,html)=>{const element=find(selector);if(element&&element.innerHTML!==html)element.innerHTML=html;};
  function render(){
    if(disposed)return;
    const job=data.job,events=data.events||[],status=job?.status;
    find("[data-sm-state]").textContent=statuses[status]||"尚无计算记录";
    const pending=state.selectedJobId&&!job;
    write("[data-sm-version]",job?`<strong>计算输入 · 方案 v${esc(job.revision??"—")}</strong><span>${esc(date(job.created))}（北京时间）</span><p class="sm-note">当前方案 v${esc(state.project?.revision??"—")}${state.draftDirty?" · 有未保存修改":""}；监控数据对应本次提交时的输入快照。</p><details class="sm-id"><summary>计算记录标识</summary><code>${esc(job.id)}</code></details>`:`<p>${pending?"正在读取所选计算记录。":"提交设计点计算后，在此查看实际求解过程。"}</p>`);
    const connection=find("[data-sm-connection]");connection.className=data.error?"sm-connection-error":"sm-note";
    connection.textContent=data.error?`读取中断，已接收曲线保留。${data.error} 将自动重试。`:!job?"":data.complete?`历史记录读取完成 · ${events.length} 条${job.error?` · ${job.error}`:""}`:data.loading&&!events.length?"正在读取监控记录…":`${active(status)?"实时采集":"读取过程记录"} · ${events.length} 条${data.lastReceivedAt?` · 最近接收 ${new Date(data.lastReceivedAt).toLocaleTimeString("zh-CN",{hour12:false})}`:""}`;
    find("[data-sm-export]").disabled=!events.length;
    find("[data-sm-export]").textContent=data.complete?"导出完整监控记录":"导出已接收记录";
    write("[data-sm-layer-state]",["inner","thermal","preburner","continuation"].map(layer=>`<div><span>${layers[layer]}</span><strong>${esc(monitorLayerState(events,layer))}</strong></div>`).join(""));
    const runSelect=find("[data-sm-run]");if(run!=="all"&&!events.some(event=>runKey(event)===run))run="all";
    write("[data-sm-run]",renderRuns(events,run));runSelect.value=run;
    const available=Object.keys(quantities).filter(key=>key!=="acceptance_norm"&&!key.endsWith("normalized_residual")&&key!=="residual_K"&&events.some(event=>event.layer==="inner"&&finite(event.values?.[key])));
    if(!available.includes(metric))metric=available[0]||"mass_flow_kg_s";
    write("[data-sm-metric]",available.length?available.map(key=>`<option value="${key}"${key===metric?" selected":""}>${esc(quantities[key].label)} [${esc(quantities[key].unit)}]</option>`).join(""):'<option value="mass_flow_kg_s">等待物理量采样</option>');find("[data-sm-metric]").value=metric;
    const thermalRuns=new Map();for(const event of events)if(event.layer==="thermal"&&isPoint(event))thermalRuns.set(runKey(event),event);
    if(thermalRun!=="latest"&&!thermalRuns.has(thermalRun))thermalRun="latest";
    write("[data-sm-thermal-run]",'<option value="latest">最近一次求值（自动跟随）</option>'+[...thermalRuns].map(([id,event])=>`<option value="${esc(id)}"${thermalRun===id?" selected":""}>${esc(id)} · ${event.context?.scope==="model_evaluation"?"模型求值内热闭合":"外层热反馈"}</option>`).join(""));
    find("[data-sm-thermal-run]").value=thermalRun;find("[data-sm-thermal-controls]").hidden=!thermalRuns.size;
    const actualThermal=thermalRun==="latest"?[...thermalRuns.keys()].at(-1):thermalRun;
    const thermalEvent=thermalRuns.get(actualThermal),thermalContext=thermalEvent?.context||{};
    find("[data-sm-thermal-context]").textContent=thermalEvent?`${thermalContext.scope==="model_evaluation"?"模型求值内热闭合，包含试探步与雅可比求值；不代表循环内层已接受的迭代点。":"外层热状态反馈。"} 轮次 ${actualThermal}${thermalContext.parent_run_id?` · 所属内层 ${thermalContext.parent_run_id}`:""}${thermalContext.evaluation_role?` · ${evaluationRoles[thermalContext.evaluation_role]||thermalContext.evaluation_role}`:""}${thermalContext.model_evaluation!=null?` · 第 ${thermalContext.model_evaluation} 次模型求值`:""}${thermalContext.probe_variable?` · 扰动变量 ${thermalContext.probe_variable}`:""}`:"";
    const key=JSON.stringify([data.jobId,events.length,events.at(-1)?.seq,run,metric,actualThermal,data.complete]);
    if(key!==chartKey){chartKey=key;
      write("[data-sm-charts]",!events.length?`<div class="sm-empty-chart"><strong>${data.complete?"该记录未保存实时迭代过程":"等待求解器采样"}</strong><p>${data.complete?"早期计算记录不包含此类数据，无法据最终结果重建迭代曲线。":"初始化和物性计算期间可能暂时没有新的迭代点。"}</p></div>`:renderMonitorChart(events,{layer:"inner",key:"acceptance_norm",keys:["acceptance_norm","thrust_normalized_residual","ox_power_normalized_residual","fuel_power_normalized_residual"].filter(key=>events.some(event=>event.layer==="inner"&&finite(event.values?.[key]))),run,log:true,absolute:true,title:"循环内层 · 归一化物理残差（绝对值）"})+renderMonitorChart(events,{layer:"inner",key:metric,run})+["preburner"].filter(layer=>events.some(event=>event.layer===layer&&isPoint(event))).map(layer=>renderMonitorChart(events,{layer,key:"residual_K",title:`${layers[layer]} · 温度残差`})).join(""));
      write("[data-sm-thermal-chart]",thermalEvent?renderMonitorChart(events,{layer:"thermal",run:actualThermal,key:thermalEvent.context?.scope==="model_evaluation"?"acceptance_norm":"residual_K",log:thermalEvent.context?.scope==="model_evaluation",title:thermalEvent.context?.scope==="model_evaluation"?"模型求值内热闭合 · 归一化残差":"外层热反馈 · 温度残差"}):"");
      const expanded=find("[data-sm-events] details")?.open,oldScroll=find("[data-sm-events] .sm-table-scroll")?.scrollTop;
      write("[data-sm-events]",events.length?detailsTable(events):"");
      if(expanded&&find("[data-sm-events] details"))find("[data-sm-events] details").open=true;
      if(oldScroll!==undefined&&find("[data-sm-events] .sm-table-scroll"))find("[data-sm-events] .sm-table-scroll").scrollTop=oldScroll;
    }
  }
  const feed=createSolverMonitorFeed({api,setTimer,clearTimer,now,onChange:value=>{data=value;render();}});
  function onChange(event){
    if(event.target===find("[data-sm-record]"))onSelectRecord(event.target.value);
    else if(event.target===find("[data-sm-run]")){run=event.target.value;render();}
    else if(event.target===find("[data-sm-metric]")){metric=event.target.value;render();}
    else if(event.target===find("[data-sm-thermal-run]")){thermalRun=event.target.value;render();}
  }
  function onClick(event){
    if(event.target.closest?.("[data-sm-refresh]")){feed.refresh();return;}
    if(event.target.closest?.("[data-sm-export]")&&data.events.length){
      const blob=new Blob([JSON.stringify({schema_version:1,job:data.job,exported_at:new Date().toISOString(),complete:data.complete,partial:!data.complete,last_received_seq:data.nextSeq,events:data.events},null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),link=document.createElement("a");
      link.href=url;link.download=`solver-monitor-${data.job.id}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  }
  host.addEventListener("change",onChange);host.addEventListener("click",onClick);
  return {
    update(next){if(disposed)return;state=next;
      const jobs=(state.jobs||[]).filter(job=>job.project_id===state.project?.id),record=state.record?.project_id===state.project?.id?state.record:null;
      const id=state.selectedJobId||record?.id||jobs[0]?.id||null,selected=jobs.find(job=>job.id===id)||(record?.id===id?record:null);
      const options=jobs.some(job=>job.id===record?.id)||!record?jobs:[record,...jobs];
      write("[data-sm-record]",options.length?options.map(job=>`<option value="${esc(job.id)}"${job.id===id?" selected":""}>v${esc(job.revision??"—")} · ${esc(date(job.created))} · ${esc(statuses[job.status]||job.status)}</option>`).join(""):'<option value="">尚无计算记录</option>');find("[data-sm-record]").value=id||"";
      feed.update({projectId:state.project?.id||null,jobId:id,job:selected,visible:Boolean(state.visible&&state.project?.id)});render();
    },
    dispose(){if(disposed)return;disposed=true;feed.dispose();host.removeEventListener("change",onChange);host.removeEventListener("click",onClick);},
  };
}
