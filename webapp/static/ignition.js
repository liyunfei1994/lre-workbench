// Gaseous event-response bench. Pressure rise is not an ignition-success certificate.
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite = value => typeof value === 'number' && Number.isFinite(value);
const clone = value => JSON.parse(JSON.stringify(value));
const fmt = value => finite(value) ? (value !== 0 && Math.abs(value) < .001 ? value.toExponential(3) : value.toLocaleString('zh-CN',{maximumSignificantDigits:6})) : '—';
const scaled = (value,factor=1) => finite(value) ? value*factor : null;
const active = record => ['queued','running'].includes(record?.status);
const names = {queued:'排队中',running:'积分中',succeeded:'计算完成',cancelled:'已取消',failed:'执行失败',timed_out:'超时',interrupted:'服务中断',ready:'初态可建立',pass:'通过',fail:'未通过',review:'需复核',pending:'待完成',unknown:'未判定',met:'达到用户阈值',not_met:'未达到用户阈值'};
const stamp = value => value ? new Date(value*1000).toLocaleString('zh-CN',{hour12:false}) : '—';
const badge = status => `<span class="ig-badge ig-${['pass','fail','failed','review','running'].includes(status)?status:'neutral'}">${esc(names[status]||status||'未判定')}</span>`;
const notice = (message,error=false) => `<div class="ig-notice ${error?'ig-error':''}">${esc(message)}</div>`;

export function ignitionDisplayDefaults(setup) {
  const values=clone(setup.defaults||{});
  for(const p of setup.parameters||[]) if(finite(values[p.key])) values[p.key]=Number((values[p.key]*(p.factor||1)).toPrecision(12));
  return values;
}

export function buildIgnitionRequest(source,setup,values) {
  if(!source?.id||source.status!=='succeeded') throw Error('请选择已完成的设计记录，以保存工质与方案来源。');
  if(!setup?.ready) throw Error('点火研究台初始化检查未通过，请查看准备度说明。');
  const out=clone(setup.defaults||{});out.source_job_id=source.id;
  for(const p of setup.parameters||[]) {
    if(p.readonly) continue;
    const raw=values[p.key];
    if(raw===undefined) continue;
    if(raw===null||typeof raw==='boolean'||String(raw).trim()===''||!Number.isFinite(Number(raw))) throw Error(`${p.label||p.key} 必须是有限数值。`);
    const value=Number((Number(raw)/(p.factor||1)).toPrecision(14));
    if((finite(p.min)&&value<p.min)||(finite(p.max)&&value>p.max)) throw Error(`${p.label||p.key} 超出允许范围。`);
    out[p.key]=value;
  }
  if(!Number.isInteger(out.sample_count)) throw Error('输出采样数必须为整数。');
  out.chemistry_enabled=values.chemistry_enabled??out.chemistry_enabled;
  if(typeof out.chemistry_enabled!=='boolean') throw Error('化学反应开关必须为布尔值。');
  out.initial_mixture=values.initial_mixture??out.initial_mixture;
  if(!['nitrogen','reactants'].includes(out.initial_mixture)) throw Error('初态仅支持氮气或未燃氧气/甲烷预混气。');
  if(finite(out.ignition_time_s)&&finite(out.ignition_duration_s)&&out.ignition_time_s+out.ignition_duration_s>out.duration_s) throw Error('外部点火热脉冲须在积分结束之前完成。');
  for(const key of ['ox_open_time_s','fuel_open_time_s']) if(finite(out[key])&&out[key]>=out.duration_s) throw Error('阀门指令开始时刻须早于积分结束时刻。');
  if(finite(out.min_pressure_Pa)&&finite(out.max_pressure_Pa)&&out.min_pressure_Pa>=out.max_pressure_Pa) throw Error('最低压力边界须小于最高压力边界。');
  if(finite(out.min_temperature_K)&&finite(out.max_temperature_K)&&out.min_temperature_K>=out.max_temperature_K) throw Error('最低气体温度须小于最高气体温度。');
  return out;
}

const series=(key,label,color,factor=1,dashed=false)=>({key,label,color,factor,dashed});
function flatRows(result) {
  return (result?.samples||[]).map(row=>{
    const flat={...row};
    for(const [key,value] of Object.entries(row.composition||{})) flat[`Y.${key}`]=value;
    for(const [key,value] of Object.entries(row.element_balance_error_kg||{})) flat[`element.${key}`]=value;
    for(const [key,value] of Object.entries(row.integrated_element_inlet_kg||{})) flat[`element_inlet.${key}`]=value;
    for(const [key,value] of Object.entries(row.integrated_element_outlet_kg||{})) flat[`element_outlet.${key}`]=value;
    return flat;
  });
}

export function ignitionEvents(result) {
  const list=Array.isArray(result?.events)?result.events:[];
  if(list.length) return list.filter(item=>finite(item.time_s)).map(item=>({...item,label:item.label||item.name||item.type||'模型事件'}));
  const req=result?.request||{};
  return [
    {time_s:req.ox_open_time_s,label:'氧阀指令开始',type:'command'},
    {time_s:req.fuel_open_time_s,label:'燃料阀指令开始',type:'command'},
    {time_s:req.ignition_time_s,label:'外加热脉冲开始',type:'prescribed'},
    {time_s:finite(req.ignition_time_s)&&finite(req.ignition_duration_s)?req.ignition_time_s+req.ignition_duration_s:null,label:'外加热脉冲结束',type:'prescribed'},
  ].filter(item=>finite(item.time_s));
}

export function ignitionPanels(result) {
  const reference=result?.request?.reference_pressure_Pa??result?.metrics?.reference_pressure_Pa;
  const rows=flatRows(result).map(row=>({...row,reference_pressure_Pa:finite(reference)?reference:null})),events=ignitionEvents(result),species=['CH4','O2','N2','CO2','H2O','CO','H2','OH'],colors=['#b16f3d','#337c9b','#9b9da7','#6c709e','#3b8b7a','#b64f5e','#697e4a','#b3943a'];
  const elements=[...new Set(rows.flatMap(row=>Object.keys(row).filter(key=>key.startsWith('element.'))))];
  const panels=[
    {name:'容腔建压响应',unit:'MPa',hint:'绝对压力；参考线仅为用户建压阈值，不是点火成功判据。',series:[series('pressure_Pa','容腔压力','#337c9b',1e-6),series('reference_pressure_Pa','用户参考压力','#a59472',1e-6,true)]},
    {name:'气体与壁面温度',unit:'K',hint:'容腔气体与集总壁面分别计算；温升不单独作为点火成功判据。',series:[series('temperature_K','气体','#b16f3d'),series('wall_temperature_K','壁面','#6c709e',1,true)]},
    {name:'气相供给与排出流量',unit:'g/s',hint:'实际流量由储源、容腔压力及阀位共同确定。',series:[series('ox_mass_flow_kg_s','氧气入口','#337c9b',1000),series('fuel_mass_flow_kg_s','甲烷入口','#b16f3d',1000),series('outlet_mass_flow_kg_s','等效出口','#5d7483',1000,true)]},
    {name:'阀门指令与实际开度',unit:'%',hint:'指令斜坡与执行机构响应分开显示。',series:[series('ox_command','氧阀指令','#91b6c8',100,true),series('ox_opening','氧阀实际','#337c9b',100),series('fuel_command','燃料阀指令','#d1ac88',100,true),series('fuel_opening','燃料阀实际','#b16f3d',100)]},
    {name:'给定点火事件 · 外加热功率',unit:'kW',hint:'用户给定的热脉冲是外部能量输入；按事件阶跃显示，不把采样间隔画成加热斜坡。',series:[{...series('igniter_power_W','外部热脉冲','#b3943a',1e-3),step:true}]},
    {name:'化学热释放率诊断',unit:'kW',hint:'由有限速率反应与组分热力学计算；不重复加入总能量方程。',series:[series('chemical_heat_release_W','化学热释放率','#b64f5e',1e-3)]},
    {name:'容腔主要组分',unit:'质量分数 / %',hint:'显示机理返回的主要气相物种；未显示物种仍保留在 CSV 和完整记录中。',series:species.map((key,i)=>series(`Y.${key}`,key,colors[i],100))},
    {name:'容腔质量库存',unit:'g',hint:'有限容积中的瞬时总质量，不是累计推进剂消耗量。',series:[series('mass_kg','容腔总质量','#337c9b',1000)]},
    {name:'壁面传热',unit:'kW',hint:'气体向壁面为正；正负方向遵循本记录中的能量定义。',series:[series('heat_to_wall_W','气体 → 壁面','#6c709e',1e-3)]},
    {name:'质量与元素守恒',unit:'kg',hint:'总质量及元素库存与进出口积分对照，化学反应不改变元素总量。',series:[series('mass_balance_error_kg','总质量误差','#3b8b7a'),...elements.map((key,i)=>series(key,`${key.slice(8)} 元素误差`,colors[i%colors.length],1,true))]},
    {name:'能量守恒',unit:'J',hint:'含生成焓基准；流动焓、外加热、壁面储能与环境换热统一记账。',series:[series('energy_balance_error_J','总能量误差','#3b8b7a'),series('fluid_energy_balance_error_J','流体能量误差','#337c9b',1,true),series('wall_energy_balance_error_J','壁面能量误差','#b16f3d',1,true)]},
  ];
  return panels.map(panel=>({...panel,rows,events,series:panel.series.filter(s=>rows.some(row=>finite(row[s.key])))}));
}

function eventLabelLayout(events,x,minTime,maxTime,left,right) {
  const points=(events||[]).map((event,i)=>({...event,index:i+1,x:x(event.time_s)}))
    .filter(event=>finite(event.time_s)&&event.time_s>=minTime&&event.time_s<=maxTime)
    .sort((a,b)=>a.x-b.x||a.index-b.index);
  const groups=[];
  for(const point of points) {
    const prior=groups.at(-1);
    if(prior&&point.x-prior.at(-1).x<42) prior.push(point);
    else groups.push([point]);
  }
  const describe=group=>{
    const ids=[...new Set(group.map(event=>event.index))].sort((a,b)=>a-b),runs=[];
    const name=value=>`E${String(value).padStart(2,'0')}`;
    for(let i=0;i<ids.length;i++) {
      const start=ids[i];let end=start;
      while(ids[i+1]===end+1)end=ids[++i];
      runs.push(start===end?name(start):`${name(start)}–${name(end)}`);
    }
    const label=runs.join(' / '),width=Math.min(right-left,Math.max(10,label.length*4.9));
    const center=(group[0].x+group.at(-1).x)/2;
    const labelX=Math.max(left,Math.min(right-width,center-width/2));
    return {events:group,ids,label,width,x:labelX};
  };
  // Clamp first, then merge remaining collisions. This also handles labels at
  // either plot edge, unsorted input events and non-consecutive event numbers.
  for(let i=1;i<groups.length;) {
    const previous=describe(groups[i-1]),next=describe(groups[i]);
    if(previous.x+previous.width+8>next.x) {
      groups.splice(i-1,2,[...groups[i-1],...groups[i]]);
      i=Math.max(1,i-1);
    } else i++;
  }
  return {points,groups:groups.map(describe)};
}

function plot(panel,index=0) {
  const w=600,h=280,left=85,right=24,top=35,bottom=45,rows=panel.rows.filter(row=>finite(row.time_s));
  const times=rows.map(row=>row.time_s),x0=times.length?Math.min(...times):0,x1=times.length?Math.max(...times):1;
  const values=panel.series.flatMap(s=>rows.filter(row=>finite(row[s.key])).map(row=>row[s.key]*s.factor));
  const lo=values.length?Math.min(...values):0,hi=values.length?Math.max(...values):1,pad=(hi-lo||Math.abs(hi)*.02||1)*.12,y0=lo-pad,y1=hi+pad;
  const x=v=>left+(v-x0)/(x1-x0||1)*(w-left-right),y=v=>top+(y1-v)/(y1-y0)*(h-top-bottom);
  const axes=Array.from({length:5},(_,i)=>{const q=i/4,vx=x0+q*(x1-x0),vy=y0+q*(y1-y0);return `<line class="ig-grid" x1="${left}" x2="${w-right}" y1="${y(vy)}" y2="${y(vy)}"/><text class="ig-tick" x="${left-9}" y="${y(vy)+4}" text-anchor="end">${esc(fmt(vy))}</text><text class="ig-tick" x="${x(vx)}" y="${h-bottom+20}" text-anchor="middle">${esc(fmt(vx))}</text>`;}).join('');
  const paths=panel.series.map(s=>{
    let path='',pen=false;for(const row of rows){if(!finite(row[s.key])){pen=false;continue;}path+=pen&&s.step?`H${x(row.time_s)}V${y(row[s.key]*s.factor)} `:`${pen?'L':'M'}${x(row.time_s)},${y(row[s.key]*s.factor)} `;pen=true;}
    return `<path data-series="${esc(s.key)}" class="ig-line" stroke="${s.color}" ${s.dashed?'stroke-dasharray="6 4"':''} d="${path}"><title>${esc(s.label)}</title></path>${rows.length===1&&finite(rows[0]?.[s.key])?`<circle cx="${x(rows[0].time_s)}" cy="${y(rows[0][s.key]*s.factor)}" r="3" fill="${s.color}"/>`:''}`;
  }).join('');
  const eventLayout=eventLabelLayout(panel.events,x,x0,x1,left,w-right);
  const markers=eventLayout.points.map(event=>`<g class="ig-event" data-event-marker="${event.index}"><title>E${String(event.index).padStart(2,'0')} · ${esc(event.label)} · ${fmt(event.time_s)} s</title><line x1="${event.x}" x2="${event.x}" y1="${top}" y2="${h-bottom}"/></g>`).join('')+
    eventLayout.groups.map(group=>`<g class="ig-event"><title>${group.events.map(event=>`E${String(event.index).padStart(2,'0')} · ${esc(event.label)} · ${fmt(event.time_s)} s`).join('\n')}</title><text data-event-label="${group.ids.join(',')}" x="${group.x}" y="${top-8}" textLength="${group.width}" lengthAdjust="spacingAndGlyphs">${group.label}</text></g>`).join('');
  return `<article class="ig-chart"><div class="ig-chart-heading"><span>${String(index+1).padStart(2,'0')}</span><div><h3>${esc(panel.name)}</h3><p>${esc(panel.hint)}</p></div></div><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(panel.name)}，时间 / s，${esc(panel.unit)}"><title>${esc(panel.name)}</title>${axes}${markers}${y0<0&&y1>0?`<line class="ig-zero" x1="${left}" x2="${w-right}" y1="${y(0)}" y2="${y(0)}"/>`:''}<path class="ig-axis" d="M${left},${top}V${h-bottom}H${w-right}"/><text class="ig-unit" x="14" y="15">${esc(panel.unit)}</text><text class="ig-unit" x="${w/2}" y="${h-4}" text-anchor="middle">时间 / s</text>${paths}${values.length?'':`<text class="ig-unit" x="${w/2}" y="${h/2}" text-anchor="middle">该量暂无有效数据</text>`}</svg><div class="ig-legend">${panel.series.map(s=>`<span><i style="border-top:${s.dashed?'dashed':'solid'} 2px ${s.color}"></i>${esc(s.label)}</span>`).join('')}</div></article>`;
}

const labels={status:'审计状态',experiment_validated:'独立试验验证',ignition_success_predicted:'自主点火成功预测',ignition_delay_predicted:'自主点火延迟预测',extinction_predicted:'熄火预测',max_abs_mass_balance_error_kg:'总质量最大绝对残差 / kg',max_abs_energy_balance_error_J:'总能量最大绝对残差 / J',max_abs_fluid_energy_balance_error_J:'流体能量最大绝对残差 / J',max_abs_wall_energy_balance_error_J:'壁面能量最大绝对残差 / J',max_abs_element_balance_error_kg:'各元素最大绝对残差 / kg',max_abs_mass_error_kg:'总质量最大绝对残差 / kg',max_abs_total_energy_error_J:'总能量最大绝对残差 / J',max_abs_fluid_energy_error_J:'流体能量最大绝对残差 / J',max_abs_wall_energy_error_J:'壁面能量最大绝对残差 / J',max_abs_element_error_kg:'各元素最大绝对残差 / kg',mass_tolerance_kg:'质量及元素审计容差 / kg',energy_tolerance_J:'能量审计容差 / J',meaning:'审计解释',max_species_sum_error:'组分质量分数归一化误差',pressure_threshold_time_s:'首次观察到达到用户参考压力 / s',reference_pressure_time_s:'首次观察到达到用户参考压力 / s',first_reference_pressure_time_s:'首次观察到达到用户参考压力 / s',reference_pressure_time_resolution_s:'参考压力检测的最大检查间隔 / s',reference_pressure_meaning:'参考压力响应指标定义',reference_pressure_Pa:'用户建压参考压力 / Pa',peak_pressure_Pa:'压力峰值 / Pa',peak_temperature_K:'气体温度峰值 / K',peak_chemical_heat_release_W:'化学热释放率峰值 / W',ignition_energy_J:'给定点火能量 / J',integrated_igniter_energy_J:'实际积分外加热量 / J',basis:'指标定义',scope:'适用范围',reason:'说明',predicted:'是否预测',ignition_success:'点火成功判定',ignition_delay:'点火延迟判定',ignition_delay_s:'自主点火延迟 / s',extinction:'熄火判定',extinction_detected:'自主熄火判定'};
function dataTable(object) {
  if(!object||typeof object!=='object'||!Object.keys(object).length)return notice('暂无检查数据。');
  return `<div class="ig-table"><table><thead><tr><th>检查项</th><th>结果 / 说明</th></tr></thead><tbody>${Object.entries(object).map(([key,value])=>`<tr><td>${esc(labels[key]||key)}</td><td>${key==='status'?badge(value):value&&typeof value==='object'?`<pre>${esc(JSON.stringify(value,null,2))}</pre>`:typeof value==='boolean'?(key==='experiment_validated'?(value?'已完成':'尚未完成'):/predicted|ignition|extinction/.test(key)?(value?'模型已给出，需独立核实':'本阶段不作此判定'):(value?'是':'否')):value===null&&/ignition_success|ignition_delay|extinction/.test(key)?'本阶段不作此判定':finite(value)?fmt(value):esc(value??'—')}</td></tr>`).join('')}</tbody></table></div>`;
}
function eventTable(result) {
  const events=ignitionEvents(result);
  if(!events.length)return notice('本记录未保存事件时刻。');
  return `<div class="ig-table"><table><thead><tr><th>图中编号</th><th>时间 / s</th><th>事件</th><th>执行状态</th><th>说明</th></tr></thead><tbody>${events.map((e,i)=>`<tr><td>E${String(i+1).padStart(2,'0')}</td><td>${fmt(e.time_s)}</td><td>${esc(e.label)}</td><td>${e.observed===true?'有效积分已到达':e.observed===false?'尚未到达':'按记录时刻显示'}</td><td>${esc(e.description||e.basis||(['prescribed','command'].includes(e.type||e.kind)?'给定输入事件；不代表自主燃烧判定。':e.type||e.kind||'按计算记录保存。'))}</td></tr>`).join('')}</tbody></table></div>`;
}
function schematic(row) {
  const dynamic=Boolean(row),r=row||{};
  return `<div class="ig-schematic" aria-label="气相点火事件研究边界"><div class="ig-supplies"><div class="ig-boundary ig-oxygen"><span>O₂ / 气相储源</span><b>${dynamic?`${fmt(scaled(r.ox_mass_flow_kg_s,1000))} g/s`:'固定压力与温度'}</b><small>执行阀 · 等效流阻</small></div><div class="ig-boundary ig-fuel"><span>CH₄ / 气相储源</span><b>${dynamic?`${fmt(scaled(r.fuel_mass_flow_kg_s,1000))} g/s`:'固定压力与温度'}</b><small>执行阀 · 等效流阻</small></div></div><span class="ig-flow">→</span><div class="ig-volume"><span>0D / 有限速率反应容腔</span><b>${dynamic?`${fmt(scaled(r.pressure_Pa,1e-6))} MPa · ${fmt(r.temperature_K)} K`:'组分库存 + 总内能 + 壁温'}</b><small>${dynamic?`外加热 ${fmt(scaled(r.igniter_power_W,1e-3))} kW`:'给定热脉冲，计算系统响应'}</small></div><span class="ig-flow">→</span><div class="ig-boundary"><span>等效排出边界</span><b>${dynamic?`${fmt(scaled(r.outlet_mass_flow_kg_s,1000))} g/s`:'准稳态孔口 + 背压'}</b><small>${dynamic?(r.outlet_choked===true?'出口壅塞':r.outlet_choked===false?'出口未壅塞':'流动状态待判定'):'不是完整扩张喷管'}</small></div></div>`;
}

export function renderIgnitionResult(record,index=0) {
  const result=record?.result,rows=result?.samples||[];
  if(!rows.length)return `<div class="ig-empty"><span>PRESCRIBED EVENT / GASEOUS REACTOR</span><h3>${active(record)?'正在积分气相反应与建压响应':record?'本记录暂无有效时间采样':'从可追溯的给定点火事件开始'}</h3><p>观察气体供给、阀门执行、外部点火能量、化学反应和壁面换热共同作用下的压力与组分变化。</p><p>本阶段不自主判定点火成功、点火延迟或熄火。</p></div>`;
  const row=rows[Math.min(Math.max(0,index),rows.length-1)],request=result.request||record.request||{},max=key=>{const v=rows.map(s=>s[key]).filter(finite);return v.length?Math.max(...v):null;};
  const fields=[['pressure_Pa','容腔绝压 / MPa',1e-6],['temperature_K','气体温度 / K',1],['wall_temperature_K','壁面温度 / K',1],['mass_kg','气体库存 / g',1000],['igniter_power_W','外加热功率 / kW',1e-3],['chemical_heat_release_W','化学热释放率 / kW',1e-3],['ox_opening','氧阀实际开度 / %',100],['fuel_opening','燃料阀实际开度 / %',100]];
  const composition=Object.entries(row.composition||{}).filter(([,v])=>finite(v)).sort((a,b)=>b[1]-a[1]);
  return `<div class="ig-result-heading"><div><span>TIME RESPONSE / CONSERVATION</span><h2>点火事件与建压时间响应</h2><p>${rows.length} 个输出采样 · 来源、模型与参数随记录冻结</p></div><div><button data-ignition-action="csv">导出 CSV</button><button data-ignition-action="svg">导出 SVG</button><button data-ignition-action="snapshot" ${result.endpoint_snapshot?'':'disabled'}>导出末态快照</button></div></div>${result.complete===false?notice('本记录只包含有效积分时间段；终止前的结果不能作为完整启动过程。'):''}${result.termination_reason&&result.termination_reason!=='completed'?notice(result.termination_reason):''}${request.chemistry_enabled===false?notice('本记录关闭化学反应，用于惰性供给与外部加热对照。'):''}<div class="ig-summary"><div><span>采样压力峰值</span><b>${fmt(scaled(max('pressure_Pa'),1e-6))}<small> MPa</small></b></div><div><span>采样气体温度峰值</span><b>${fmt(max('temperature_K'))}<small> K</small></b></div><div><span>点火成功 / 延迟 / 熄火</span><b class="ig-no-verdict">未作自主判定</b></div></div><section class="ig-sample"><div class="ig-section-heading"><div><h3>时间截面</h3><p>查看同一时刻的压力、反应库存与执行机构状态</p></div><b class="ig-time">${fmt(row.time_s)} <small>s</small></b></div><label class="ig-time-slider">采样时刻：${fmt(row.time_s)} s<input data-ignition-sample type="range" min="0" max="${rows.length-1}" value="${rows.indexOf(row)}" step="1" aria-label="点火事件时间截面"></label>${schematic(row)}<div class="ig-sample-grid">${fields.map(([key,label,factor])=>`<div><span>${label}</span><b>${fmt(scaled(row[key],factor))}</b></div>`).join('')}</div><details class="ig-group"><summary>当前时刻气相组分 · 质量分数</summary>${composition.length?`<div class="ig-composition">${composition.map(([key,value])=>`<div><span>${esc(key)}</span><b>${fmt(value*100)}%</b></div>`).join('')}</div>`:notice('本采样未返回组分数据。')}</details></section><details class="ig-group" open><summary>事件时刻与图中标记</summary>${eventTable(result)}</details><div class="ig-chart-grid">${ignitionPanels(result).map(plot).join('')}</div><section class="ig-metrics"><div class="ig-section-heading"><div><h3>建压响应指标与判定边界</h3><p>参考压力到达时间由用户阈值定义；不是点火延迟或燃烧成功判据。</p></div>${badge('review')}</div>${dataTable(result.metrics)}<p class="ig-reading">燃烧建立、点火延迟和熄火判据需要适用范围明确的试验依据；本研究台仅报告给定事件下的响应。</p></section><details class="ig-group"><summary>质量、元素与能量守恒审计</summary>${dataTable(result.audits)}</details><details class="ig-group"><summary>计算过程、冻结输入与模型范围</summary><div class="ig-model"><ol><li>由独立气相初态建立组分质量、总内能、壁温和阀位。</li><li>按给定阀序与点火事件分段积分，进出口携带上游组分和焓。</li><li>通过有限速率化学反应更新组分；使用同一生成焓基准求气体温度与压力。</li><li>单独积算外部能量与守恒账目，保留终止原因和可追溯末态。</li></ol><p>${esc(result.model_description?.title||'氧气/甲烷气相点火事件研究台')}</p>${Array.isArray(result.model_description?.equations)?`<div class="ig-equations">${result.model_description.equations.map(eq=>`<code>${esc(eq)}</code>`).join('')}</div>`:''}<ul>${(result.limitations||[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ul><details><summary>完整模型与参数</summary><pre>${esc(JSON.stringify({model:result.model_description,request},null,2))}</pre></details></div></details>`;
}

export function buildIgnitionCsv(result) {
  const rows=flatRows(result).map(row=>Object.fromEntries(Object.entries(row).filter(([,v])=>finite(v)||typeof v==='string'||typeof v==='boolean'||v===null)));
  const keys=[...new Set(rows.flatMap(row=>Object.keys(row)))],cell=value=>`"${String(value??'').replace(/"/g,'""')}"`;
  return '\uFEFF'+[keys,...rows.map(row=>keys.map(key=>typeof row[key]==='string'?"'"+row[key]:row[key]??''))].map(row=>row.map(cell).join(',')).join('\r\n');
}
export function buildIgnitionSvg(result) {
  const panels=ignitionPanels(result),events=ignitionEvents(result),rows=Math.ceil(panels.length/2),height=110+rows*390+35+events.length*20;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="${height}" viewBox="0 0 1280 ${height}"><style>text{font:11px Arial,'Microsoft YaHei',sans-serif;fill:#526c7c}.ig-grid{stroke:#e0e7ec}.ig-axis{stroke:#96a7b3;fill:none}.ig-line{fill:none;stroke-width:2}.ig-zero{stroke:#9a8aaa;stroke-dasharray:4 4}.ig-event line{stroke:#b8a581;stroke-dasharray:3 5;opacity:.6}.ig-event text{font-size:8px;fill:#9a825a}</style><rect width="1280" height="${height}" fill="white"/><text x="30" y="35" style="font-size:22px">点火事件与建压分析</text><text x="30" y="60">独立气相研究台 · 给定热脉冲 · 不自主判定点火成功、延迟或熄火</text><text x="30" y="80">来源 ${esc(result.source?.job_id||'—')} · ${result.complete===false?'仅有效积分时间段':'按记录保存的时间采样'}</text>${panels.map((panel,i)=>{const svg=plot(panel,i).match(/<svg[\s\S]*?<\/svg>/)?.[0]||'';return `<g transform="translate(${25+i%2*630} ${105+Math.floor(i/2)*390})"><text x="10" y="20" style="font-size:15px">${esc(panel.name)}</text><svg x="0" y="30" width="600" height="280" viewBox="0 0 600 280">${svg.replace(/^<svg[^>]*>/,'').replace(/<\/svg>$/,'')}</svg>${panel.series.map((s,j)=>`<g transform="translate(${15+j%4*145} ${330+Math.floor(j/4)*20})"><line x1="0" x2="17" y1="-4" y2="-4" stroke="${s.color}" stroke-width="2" ${s.dashed?'stroke-dasharray="5 3"':''}/><text x="23" y="0">${esc(s.label)}</text></g>`).join('')}</g>`;}).join('')}<g transform="translate(35 ${110+rows*390})"><text y="0" style="font-size:13px">事件标记</text>${events.map((event,i)=>`<text y="${25+i*20}">E${String(i+1).padStart(2,'0')} · ${fmt(event.time_s)} s · ${esc(event.label)}</text>`).join('')}</g></svg>`;
}
export function buildIgnitionSnapshot(record) {
  if(!record?.result?.endpoint_snapshot)throw Error('本记录尚未生成有效末态快照。');
  return {schema:'gaseous-ignition-endpoint-v1',study_id:record.id,source:clone(record.result.source||{}),complete:record.result.complete,termination_reason:record.result.termination_reason,scope:'仅为本气相反应容腔末态；尚未耦合完整发动机，也不作为点火成功或整机启动就绪依据。',ignition_success_predicted:false,engine_initialization_supported:false,endpoint_snapshot:clone(record.result.endpoint_snapshot),audits:clone(record.result.audits||{})};
}
export function ignitionRunBlockReason(source,setup,busy,count=0,error='') {
  if(!source)return '请选择已完成的设计记录，以确定工质和方案来源。';
  if(!setup)return error?'初始化未完成，请检查连接后刷新。':'正在读取点火研究台初始化设置。';
  if(!setup.ready)return '本来源或气相研究初态尚不支持，请处理上方初始化问题。';
  if(busy)return '正在提交任务，请稍候。';
  if(count>=2)return '本项目已有 2 个等待或运行中的点火研究，请等待完成或取消。';
  return '';
}

export function mountIgnition(container,{api,getContext,notify=()=>{}}) {
  let projectId='',sourceId='',setup=null,values={},records=[],selected=null,selectedIndex=0,error='',setupError='',busy=false,live=true,timer=null,seq=0;
  const openGroups=new Set(['时间设置','容腔初态','外部点火事件']),context=()=>getContext()||{};
  const source=()=>{const c=context();return c.record?.status==='succeeded'&&c.record.project_id===c.project?.id?c.record:null;};
  const stop=()=>{if(timer!==null)clearTimeout(timer);timer=null;};
  function syncContext(){const p=context().project?.id||'',s=source()?.id||'';if(p!==projectId||s!==sourceId){++seq;stop();if(p!==projectId){records=[];selected=null;selectedIndex=0;}projectId=p;sourceId=s;setup=null;values={};error='';setupError='';busy=false;}}
  const current=(token,p,s)=>live&&seq===token&&context().project?.id===p&&(source()?.id||'')===s;
  function field(p){const factor=p.factor||1;return `<label>${esc(p.label||p.key)}${p.unit&&p.unit!=='—'?` / ${esc(p.unit)}`:''}<input type="number" data-ignition-field="${esc(p.key)}" value="${esc(values[p.key]??'')}" ${finite(p.min)?`min="${p.min*factor}"`:''} ${finite(p.max)?`max="${p.max*factor}"`:''} step="${finite(p.step)?p.step:'any'}" ${p.readonly?'readonly':''}><small>${esc(p.description||'')}</small></label>`;}
  function render(){
    const s=source(),ctx=context(),block=ignitionRunBlockReason(s,setup,busy,records.filter(active).length,setupError),params=setup?.parameters||[],groups=[...new Set(params.map(p=>p.group||'研究参数'))];
    const checks=Array.isArray(setup?.source_constraints)?setup.source_constraints:setup?.source_constraints?.checks||[],progress=selected?.progress||{},samples=selected?.result?.samples||[],maximum=Math.max(selected?.request?.sample_count||0,finite(progress.maximum)?progress.maximum:0,samples.length);
    container.innerHTML=`<section class="ignition-workspace" aria-label="点火事件与建压分析工作区"><div class="ig-heading"><div><span>IGNITION EVENT / PRESSURE RESPONSE</span><h2>点火事件与建压分析</h2><p>给定热脉冲 · 气相有限速率反应 · 阀门执行 · 容腔建压</p></div><button data-ignition-action="refresh">刷新记录</button></div><div class="ig-source"><b>追溯记录</b><span>${s?`方案 v${s.revision} · ${stamp(s.created)} · ${esc(s.id)}`:'请选择已完成的设计记录'}</span><span>工质与方案来源 / 独立研究边界</span></div>${s&&(ctx.dirty||s.revision!==ctx.project?.revision)?notice('来源使用所选设计记录的冻结输入；左侧当前编辑参数不参与本研究台计算。气相初态、供给边界与点火事件在本页独立设置。'):''}${setupError?notice(setupError,true):''}${error?notice(error,true):''}<section class="ig-setup"><div class="ig-section-heading"><div><h3>气相点火事件研究台</h3><p>氧气 / 甲烷供给 · 氮气或未燃预混气初态 · 组分与总能量积分</p></div>${badge(setup?.ready?'ready':setup?'review':'unknown')}</div>${schematic()}${notice('当前只研究给定点火事件下的气相系统响应。尚未耦合低速泵、涡轮、雾化汽化与整机启动，不自主预测点火成功、点火延迟或熄火。')}${setup&&!setup.ready?`<ul class="ig-issues">${(setup.issues||[]).map(item=>`<li>${esc(typeof item==='string'?item:JSON.stringify(item))}</li>`).join('')}</ul>`:''}${checks.length?`<details class="ig-source-checks"><summary>来源稳态约束 · 仅供追溯</summary><div class="ig-table"><table><thead><tr><th>约束</th><th>原记录值</th><th>状态</th></tr></thead><tbody>${checks.map(item=>`<tr><td>${esc(item.label||item.key)}</td><td>${fmt(item.value)} ${esc(item.unit||'')}</td><td>${badge(item.status)}</td></tr>`).join('')}</tbody></table></div></details>`:''}${setup?.ready?`<div class="ig-modes"><label>初始气相库存<select data-ignition-mixture><option value="nitrogen" ${values.initial_mixture==='nitrogen'?'selected':''}>氮气初始库存</option><option value="reactants" ${values.initial_mixture==='reactants'?'selected':''}>未燃氧气 / 甲烷预混气</option></select><small>不使用稳态热燃气或预冷液体末态代替气相初态。</small></label><label class="ig-checkbox"><span><input type="checkbox" data-ignition-chemistry ${values.chemistry_enabled?'checked':''}>启用气相化学反应</span><small>${values.chemistry_enabled?'Cantera 有限速率反应；机理适用性仍需试验确认。':'当前关闭化学反应，用作惰性流动与外加热对照。'}</small></label></div>`:''}${groups.map((group,i)=>`<details class="ig-group" data-ignition-group="${esc(group)}" ${openGroups.has(group)?'open':''}><summary><span>${String(i+1).padStart(2,'0')}</span>${esc(group)}</summary><div class="ig-field-grid">${params.filter(p=>(p.group||'研究参数')===group).map(field).join('')}</div></details>`).join('')}<details class="ig-scope"><summary>模型边界与参数依据</summary><ul>${(setup?.limitations||[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ul></details><div class="ig-run-row"><p id="ignition-run-reason" role="status">${esc(block||'保存独立输入、事件时刻、有效积分时间段、守恒审计与末态快照。')}</p><button class="primary" data-ignition-action="run" ${block?'disabled aria-describedby="ignition-run-reason"':''}>${busy?'提交中…':'运行点火事件与建压分析'}</button></div></section><section class="ig-output"><div class="ig-history"><label>点火事件研究记录<select data-ignition-record><option value="">选择已保存的分析</option>${records.map(record=>`<option value="${esc(record.id)}" ${record.id===selected?.id?'selected':''}>v${record.revision} · ${stamp(record.created)} · ${esc(names[record.status]||record.status)}</option>`).join('')}</select></label><button data-ignition-action="json" ${selected?'':'disabled'}>导出 JSON</button><button data-ignition-action="cancel" ${active(selected)&&!busy?'':'disabled'}>取消分析</button></div>${selected?`<div class="ig-job-state">${badge(selected.status==='succeeded'&&selected.result?.complete===false?'review':selected.status)}<span>${esc(progress.phase||'')}</span><span>${samples.length} 个时间采样</span><small>记录 ${esc(selected.id.slice(0,8))}</small></div>`:''}${maximum?`<progress max="${maximum}" value="${Math.min(maximum,finite(progress.completed)?progress.completed:samples.length)}" aria-label="点火事件积分进度"></progress>`:''}${selected?.error?notice(selected.error,true):''}${selected?.request?.source_job_id&&selected.request.source_job_id!==sourceId?notice('正在显示历史研究记录；结果与导出使用该记录的冻结参数，与上方新任务设置独立。'):''}${renderIgnitionResult(selected,selectedIndex)}</section></section>`;
  }
  function schedule(){stop();if(live&&records.some(active))timer=setTimeout(()=>refresh(),1500);}
  async function refresh(){
    live=true;stop();syncContext();const p=projectId,s=sourceId,token=++seq;render();if(!p)return;
    try{const [list,config]=await Promise.all([api(`/projects/${encodeURIComponent(p)}/ignition-studies`),s&&!setup?api(`/projects/${encodeURIComponent(p)}/ignition-studies/setup?source_job_id=${encodeURIComponent(s)}`):Promise.resolve(setup)]);if(!current(token,p,s))return;if(config&&!setup){setup=config;values=ignitionDisplayDefaults(config);}records=Array.isArray(list)?list:[];const next=records.find(record=>record.id===selected?.id)||records[0];if(next){const detail=await api(`/ignition-studies/${encodeURIComponent(next.id)}`);if(!current(token,p,s))return;selected=detail;}else selected=null;error='';setupError='';}
    catch(e){if(current(token,p,s)){if(!setup)setupError=e.message;else error=e.message;}}
    if(current(token,p,s)){render();schedule();}
  }
  async function run(){
    if(busy)return;syncContext();let request;try{const block=ignitionRunBlockReason(source(),setup,busy,records.filter(active).length,setupError);if(block)throw Error(block);request=buildIgnitionRequest(source(),setup,values);}catch(e){error=e.message;render();return;}
    const p=projectId,s=sourceId,token=++seq;stop();busy=true;error='';render();
    try{const created=await api(`/projects/${encodeURIComponent(p)}/ignition-studies`,'POST',request);if(!current(token,p,s))return;selected={...created,request,revision:source().revision,created:Date.now()/1000};records=[selected,...records];selectedIndex=0;notify('点火事件与建压任务已提交。');}
    catch(e){if(current(token,p,s))error=e.message;}
    finally{if(current(token,p,s)){busy=false;render();schedule();}}
  }
  async function choose(id){stop();const p=projectId,s=sourceId,token=++seq;if(!id){selected=null;render();schedule();return;}try{const detail=await api(`/ignition-studies/${encodeURIComponent(id)}`);if(current(token,p,s)){selected=detail;selectedIndex=0;error='';render();schedule();}}catch(e){if(current(token,p,s)){error=e.message;render();schedule();}}}
  async function cancel(){if(!active(selected)||busy)return;const p=projectId,s=sourceId,token=++seq;stop();busy=true;render();try{await api(`/ignition-studies/${encodeURIComponent(selected.id)}/cancel`,'POST',{});if(!current(token,p,s))return;busy=false;await refresh();}catch(e){if(current(token,p,s)){busy=false;error=e.message;render();schedule();}}}
  function download(format){if(!selected||(format!=='json'&&!selected.result?.samples?.length))return;try{const data=format==='json'?JSON.stringify(selected,null,2):format==='snapshot'?JSON.stringify(buildIgnitionSnapshot(selected),null,2):format==='csv'?buildIgnitionCsv(selected.result):buildIgnitionSvg(selected.result),ext=format==='snapshot'?'json':format,type=ext==='json'?'application/json':ext==='csv'?'text/csv;charset=utf-8':'image/svg+xml;charset=utf-8',url=URL.createObjectURL(new Blob([data],{type})),a=document.createElement('a');a.href=url;a.download=`ignition-${format==='snapshot'?'endpoint-':'study-'}${selected.id}.${ext}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){error=e.message;render();}}
  container.addEventListener('input',e=>{const d=e.target.dataset||{};if(d.ignitionField)values[d.ignitionField]=e.target.value;});
  container.addEventListener('change',e=>{if(e.target.hasAttribute?.('data-ignition-record'))choose(e.target.value);if(e.target.hasAttribute?.('data-ignition-sample')){selectedIndex=Number(e.target.value);render();}if(e.target.hasAttribute?.('data-ignition-mixture')){values.initial_mixture=e.target.value;render();}if(e.target.hasAttribute?.('data-ignition-chemistry')){values.chemistry_enabled=e.target.checked;render();}});
  container.addEventListener('toggle',e=>{const group=e.target.dataset?.ignitionGroup;if(group){if(e.target.open)openGroups.add(group);else openGroups.delete(group);}},true);
  container.addEventListener('click',e=>{const button=e.target.closest?.('button');if(!button||button.disabled)return;const action=button.dataset.ignitionAction;if(action==='run')run();else if(action==='refresh')refresh();else if(action==='cancel')cancel();else if(['json','csv','svg','snapshot'].includes(action))download(action);});
  syncContext();render();return {refresh,sync(){if(live){syncContext();render();}},onLeave(){live=false;++seq;busy=false;stop();}};
}
