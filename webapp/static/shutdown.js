// Independent shutdown/restart research units. No whole-engine restart verdict.
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite = value => typeof value === 'number' && Number.isFinite(value);
const clone = value => JSON.parse(JSON.stringify(value));
const fmt = value => finite(value) ? (value !== 0 && Math.abs(value) < .001 ? value.toExponential(3) : value.toLocaleString('zh-CN',{maximumSignificantDigits:6})) : '—';
const active = record => ['queued','running'].includes(record?.status);
const stamp = value => value ? new Date(value*1000).toLocaleString('zh-CN',{hour12:false}) : '—';
const names = {queued:'排队中',running:'积分中',succeeded:'计算完成',cancelled:'已取消',failed:'执行失败',timed_out:'超时',interrupted:'服务中断',ready:'可建立初态',pass:'通过',fail:'未通过',review:'需复核',pending:'待完成',unknown:'未判定'};
const modeLabels = {gas:'气相关阀与再点火',inventory:'余液泄放与再充填',rotor:'转子惰转与管网反流'};
const colors = ['#347e9c','#b07648','#7875a6','#3b8c79','#aa5464','#799143','#627d91','#a38b46'];
const color = (value,index=0) => /^#[0-9a-f]{3,8}$/i.test(value||'') ? value : colors[index%colors.length];
const badge = status => `<span class="sd-badge sd-${['pass','fail','failed','review','running'].includes(status)?status:'neutral'}">${esc(names[status]||status||'未判定')}</span>`;
const notice = (message,error=false) => `<div class="sd-notice ${error?'sd-error':''}">${esc(message)}</div>`;
const getMode = (setup,key) => (setup?.modes||[]).find(mode=>mode.value===key);

function displayFields(target,parameters) {
  for(const p of parameters||[]) if(finite(target?.[p.key])) target[p.key]=Number((target[p.key]*(p.factor||1)).toPrecision(12));
  return target;
}
export function shutdownDisplayDefaults(mode) {
  const values=displayFields(clone(mode?.defaults||{}),mode?.parameters);
  for(const circuit of values.circuits||[]) displayFields(circuit,mode?.circuit_parameters);
  return values;
}
function readFields(target,values,parameters) {
  for(const p of parameters||[]) {
    if(p.readonly||values?.[p.key]===undefined) continue;
    const raw=values[p.key];
    if(p.type==='boolean') {
      if(typeof raw!=='boolean') throw Error(`${p.label||p.key} 必须为布尔值。`);
      target[p.key]=raw;continue;
    }
    if(Array.isArray(p.options)) {
      const option=p.options.find(item=>item.value===raw);
      if(!option) throw Error(`${p.label||p.key} 不是支持的选项。`);
      target[p.key]=option.value;continue;
    }
    if(raw===null||typeof raw==='boolean'||String(raw).trim()===''||!Number.isFinite(Number(raw))) throw Error(`${p.label||p.key} 必须是有限数值。`);
    const value=Number((Number(raw)/(p.factor||1)).toPrecision(14));
    if((finite(p.min)&&value<p.min)||(finite(p.max)&&value>p.max)) throw Error(`${p.label||p.key} 超出允许范围。`);
    if((p.type==='integer'||p.key==='sample_count')&&!Number.isInteger(value)) throw Error(`${p.label||p.key} 必须为整数。`);
    target[p.key]=value;
  }
}
export function buildShutdownRequest(source,setup,modeKey,values) {
  if(!source?.id||source.status!=='succeeded') throw Error('请选择已完成的设计记录，以保存工质与方案来源。');
  const mode=getMode(setup,modeKey);
  if(!mode?.ready) throw Error('本研究单元初始化检查未通过，请查看准备度说明。');
  const settings=clone(mode.defaults||{});
  readFields(settings,values,mode.parameters);
  if(Array.isArray(settings.circuits)) {
    if(!Array.isArray(values?.circuits)||values.circuits.length!==settings.circuits.length) throw Error('回路输入不完整，请重新读取初始化设置。');
    settings.circuits.forEach((circuit,index)=>readFields(circuit,values.circuits[index],mode.circuit_parameters));
  }
  return {source_job_id:source.id,mode:modeKey,settings};
}
export function shutdownEvents(result) {
  return (Array.isArray(result?.events)?result.events:[]).filter(event=>finite(event.time_s)).map(event=>({...event,label:event.label||event.name||'给定事件'}));
}
function flatten(object,prefix='',out={}) {
  for(const [key,value] of Object.entries(object||{})) {
    const name=prefix?`${prefix}.${key}`:key;
    if(value&&typeof value==='object'&&!Array.isArray(value)) flatten(value,name,out);
    else if(!Array.isArray(value)) out[name]=value;
  }
  return out;
}
export function shutdownPanels(result,fallbackCharts=[]) {
  const charts=Array.isArray(result?.charts)?result.charts:fallbackCharts;
  const samples=(result?.samples||[]).filter(row=>finite(row.time_s));
  const circuitIds=[...new Set(samples.flatMap(row=>(row.circuits||[]).map(c=>c.id)))];
  return charts.map((chart,index)=>{
    const series=[];
    for(const [si,definition] of (chart.series||[]).entries()) {
      const ids=definition.scope==='global'||!circuitIds.length?[null]:circuitIds;
      ids.forEach((id,ci)=>{
        const circuit=samples.flatMap(row=>row.circuits||[]).find(c=>c.id===id);
        const key=id===null?definition.key:`circuit.${id}.${definition.key}`;
        series.push({...definition,key,label:id===null?definition.label:`${circuit?.label||id} · ${definition.label}`,factor:finite(definition.factor)?definition.factor:1,color:color(ci===0?definition.color:null,si+ci*2),dashed:definition.dashed??/_command$/.test(definition.key),step:definition.step??definition.key==='igniter_power_W'});
      });
    }
    const rows=samples.map(row=>{
      const flat=flatten(row);
      for(const circuit of row.circuits||[]) flatten(circuit,`circuit.${circuit.id}`,flat);
      return flat;
    });
    return {name:chart.title||chart.name||`响应 ${index+1}`,unit:chart.unit||'—',hint:chart.description||chart.hint||'',events:shutdownEvents(result),rows,series:series.filter(s=>rows.some(row=>finite(row[s.key])))};
  });
}

function eventLabelLayout(events,x,minTime,maxTime,left,right) {
  const points=(events||[]).map((event,i)=>({...event,index:i+1,x:x(event.time_s)})).filter(event=>event.time_s>=minTime&&event.time_s<=maxTime).sort((a,b)=>a.x-b.x||a.index-b.index);
  const groups=[];
  for(const point of points) {const prior=groups.at(-1);if(prior&&point.x-prior.at(-1).x<42)prior.push(point);else groups.push([point]);}
  const describe=group=>{
    const ids=[...new Set(group.map(event=>event.index))].sort((a,b)=>a-b),runs=[],name=value=>`E${String(value).padStart(2,'0')}`;
    for(let i=0;i<ids.length;i++){const start=ids[i];let end=start;while(ids[i+1]===end+1)end=ids[++i];runs.push(start===end?name(start):`${name(start)}–${name(end)}`);}
    const label=runs.join(' / '),width=Math.min(right-left,Math.max(10,label.length*4.9)),center=(group[0].x+group.at(-1).x)/2;
    return {events:group,ids,label,width,x:Math.max(left,Math.min(right-width,center-width/2))};
  };
  for(let i=1;i<groups.length;){const previous=describe(groups[i-1]),next=describe(groups[i]);if(previous.x+previous.width+8>next.x){groups.splice(i-1,2,[...groups[i-1],...groups[i]]);i=Math.max(1,i-1);}else i++;}
  return {points,groups:groups.map(describe)};
}
function plot(panel,index=0) {
  const w=620,h=290,left=88,right=25,top=35,bottom=45,rows=panel.rows,times=rows.map(row=>row.time_s),x0=times.length?Math.min(...times):0,x1=times.length?Math.max(...times):1;
  const values=panel.series.flatMap(s=>rows.filter(row=>finite(row[s.key])).map(row=>row[s.key]*s.factor));
  const lo=values.length?Math.min(...values):0,hi=values.length?Math.max(...values):1,pad=(hi-lo||Math.abs(hi)*.02||1)*.12,y0=lo-pad,y1=hi+pad;
  const x=v=>left+(v-x0)/(x1-x0||1)*(w-left-right),y=v=>top+(y1-v)/(y1-y0)*(h-top-bottom);
  const axes=Array.from({length:5},(_,i)=>{const q=i/4,vx=x0+q*(x1-x0),vy=y0+q*(y1-y0);return `<line class="sd-grid" x1="${left}" x2="${w-right}" y1="${y(vy)}" y2="${y(vy)}"/><text class="sd-tick" x="${left-9}" y="${y(vy)+4}" text-anchor="end">${esc(fmt(vy))}</text><text class="sd-tick" x="${x(vx)}" y="${h-bottom+20}" text-anchor="middle">${esc(fmt(vx))}</text>`;}).join('');
  const paths=panel.series.map(s=>{
    let path='',pen=false;for(const row of rows){if(!finite(row[s.key])){pen=false;continue;}path+=pen&&s.step?`H${x(row.time_s)}V${y(row[s.key]*s.factor)} `:`${pen?'L':'M'}${x(row.time_s)},${y(row[s.key]*s.factor)} `;pen=true;}
    const valid=rows.filter(row=>finite(row[s.key]));
    return `<path data-series="${esc(s.key)}" class="sd-line" stroke="${s.color}" ${s.dashed?'stroke-dasharray="6 4"':''} d="${path}"><title>${esc(s.label)}</title></path>${valid.length===1?`<circle cx="${x(valid[0].time_s)}" cy="${y(valid[0][s.key]*s.factor)}" r="3" fill="${s.color}"/>`:''}`;
  }).join('');
  const layout=eventLabelLayout(panel.events,x,x0,x1,left,w-right);
  const markers=layout.points.map(event=>`<g class="sd-event" data-event-marker="${event.index}"><title>E${String(event.index).padStart(2,'0')} · ${esc(event.label)} · ${fmt(event.time_s)} s</title><line x1="${event.x}" x2="${event.x}" y1="${top}" y2="${h-bottom}"/></g>`).join('')+layout.groups.map(group=>`<g class="sd-event"><text data-event-label="${group.ids.join(',')}" x="${group.x}" y="${top-8}" textLength="${group.width}" lengthAdjust="spacingAndGlyphs">${group.label}</text></g>`).join('');
  return `<article class="sd-chart"><div class="sd-chart-heading"><span>${String(index+1).padStart(2,'0')}</span><div><h3>${esc(panel.name)}</h3><p>${esc(panel.hint)}</p></div></div><svg class="sd-plot" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(panel.name)}，时间 / s，${esc(panel.unit)}"><title>${esc(panel.name)}</title>${axes}${markers}${y0<0&&y1>0?`<line class="sd-zero" x1="${left}" x2="${w-right}" y1="${y(0)}" y2="${y(0)}"/>`:''}<path class="sd-axis" d="M${left},${top}V${h-bottom}H${w-right}"/><text class="sd-unit" x="12" y="15">${esc(panel.unit)}</text><text class="sd-unit" x="${w/2}" y="${h-4}" text-anchor="middle">时间 / s</text>${paths}${values.length?'':`<text class="sd-unit" x="${w/2}" y="${h/2}" text-anchor="middle">该量暂无有效数据</text>`}</svg><div class="sd-legend">${panel.series.map(s=>`<span><svg width="22" height="10" viewBox="0 0 22 10" aria-hidden="true"><line x1="1" x2="21" y1="5" y2="5" stroke="${s.color}" stroke-width="2" ${s.dashed?'stroke-dasharray="5 3"':''}/></svg>${esc(s.label)}</span>`).join('')}</div></article>`;
}

const labels={status:'守恒审计状态',complete:'是否完成给定积分区间',reason:'说明',basis:'指标定义',meaning:'解释',scope:'适用范围',experiment_validated:'独立试验验证',engine_initialization_supported:'整机初态接口',ignition_success:'自主点火成功判定',ignition_delay_s:'自主点火延迟 / s',extinction_detected:'自主熄火判定',engine_restart_success:'整机再启动成功判定',peak_pressure_Pa:'压力峰值 / Pa',peak_temperature_K:'气体温度峰值 / K',peak_wall_temperature_K:'壁温峰值 / K',max_abs_mass_error_kg:'最大质量残差 / kg',max_abs_total_energy_error_J:'最大总能量残差 / J',max_abs_fluid_energy_error_J:'最大流体能量残差 / J',max_abs_wall_energy_error_J:'最大壁面能量残差 / J',max_abs_mass_balance_error_kg:'最大质量残差 / kg',max_abs_energy_balance_error_J:'最大能量残差 / J',mass_relative_error:'质量相对残差',energy_relative_error:'能量相对残差',circuits:'各研究回路',mass_tolerance_kg:'质量审计容差 / kg',energy_tolerance_J:'能量审计容差 / J',max_abs_element_error_kg:'最大元素质量残差 / kg',reference_pressure_Pa:'用户参考压力 / Pa'};
Object.assign(labels,{
  id:'回路编号',label:'回路名称',initial_mass_kg:'初始库存 / kg',final_mass_kg:'末态库存 / kg',minimum_sampled_mass_kg:'输出采样中的最小库存 / kg',minimum_sampled_mass_time_s:'采样最小库存时刻 / s',final_wall_temperature_K:'末态壁温 / K',restart_event_observed:'再次供给／驱动事件已到达',last_sample_before_restart_mass_kg:'再开启前最后采样库存 / kg',integrated_inlet_mass_kg:'累计流入质量 / kg',integrated_outlet_mass_kg:'累计流出质量 / kg',complete_drain:'完全排空判定',restart_success:'再启动成功判定',reignition_success:'自主再点火成功判定',restart_residual_mass_kg:'再次供给时残余库存 / kg',restart_wall_temperature_K:'再次供给时壁温 / K',shutdown_to_restart_mass_change_kg:'关阀至再供给的库存变化 / kg',max_abs_hydraulic_energy_error_J:'最大液压能量残差 / J',max_abs_rotor_energy_error_J:'最大转子能量残差 / J',max_abs_pump_work_error_J:'最大泵功账目残差 / J',relative_errors:'各守恒量相对残差',relative_tolerance:'相对容差',mass:'质量',hydraulic:'液压储能',rotor:'转子动能',total:'联合能量',pump:'泵功账目',max_abs_fluid_energy_balance_error_J:'最大流体能量残差 / J',max_abs_wall_energy_balance_error_J:'最大壁面能量残差 / J',max_eos_density_relative_error:'物性密度闭合相对误差',max_eos_specific_energy_error_J_kg:'物性比内能闭合误差 / J/kg',max_phase_volume_closure_relative_error:'气液体积闭合相对误差'
});

Object.assign(labels,{
 first_reference_pressure_time_s:'首次观察到达到用户参考压力 / s',reference_pressure_time_resolution_s:'参考压力检测最大检查间隔 / s',reference_pressure_meaning:'参考压力响应指标定义',integrated_igniter_energy_J:'累计外部点火输入能量 / J',initial_speed_rpm:'初始轴转速 / rpm',final_speed_rpm:'末态轴转速 / rpm',minimum_sampled_speed_rpm:'输出采样中的最低轴转速 / rpm',minimum_sampled_speed_time_s:'采样最低轴转速时刻 / s',last_sample_before_restart_speed_rpm:'再驱动前最后采样轴速 / rpm',minimum_sampled_pump_mass_flow_kg_s:'采样最小泵支路流量 / kg/s',minimum_sampled_outlet_mass_flow_kg_s:'采样最小出口流量 / kg/s',sampled_pump_reverse_flow_observed:'输出采样已观察到泵支路反流',sampled_outlet_reverse_flow_observed:'输出采样已观察到出口支路反流',reverse_flow_observation_tolerance_kg_s:'反流识别流量容差 / kg/s',sampled_reverse_rotation_observed:'输出采样已观察到反转',initial_rotor_energy_J:'初始转子动能 / J',final_rotor_energy_J:'末态转子动能 / J'
});

function dataTable(object,depth=0) {
  if(!object||typeof object!=='object'||!Object.keys(object).length)return notice('暂无检查数据。');
  return `<div class="sd-table"><table><thead><tr><th>检查项</th><th>结果 / 说明</th></tr></thead><tbody>${Object.entries(object).map(([key,value])=>`<tr><td>${esc(labels[key]||key)}</td><td>${key==='status'?badge(value):value&&typeof value==='object'?depth<2?dataTable(value,depth+1):`<pre>${esc(JSON.stringify(value,null,2))}</pre>`:typeof value==='boolean'?(value?'是':'否'):value===null&&/ignition|extinction|restart_success|complete_drain/.test(key)?'本阶段不作自主判定':finite(value)?fmt(value):esc(value??'—')}</td></tr>`).join('')}</tbody></table></div>`;
}
function eventTable(result) {
  const events=shutdownEvents(result);
  if(!events.length)return notice('本记录未保存事件时刻。');
  return `<div class="sd-table"><table><thead><tr><th>编号</th><th>时间 / s</th><th>事件</th><th>执行状态</th><th>事件定义</th></tr></thead><tbody>${events.map((event,i)=>`<tr><td>E${String(i+1).padStart(2,'0')}</td><td>${fmt(event.time_s)}</td><td>${esc(event.label)}</td><td>${event.observed===true?'有效积分已到达':event.observed===false?'尚未到达':'按保存记录显示'}</td><td>${esc(event.description||event.basis||'给定输入事件；不代表自主燃烧或再启动成功判据。')}</td></tr>`).join('')}</tbody></table></div>`;
}
function timeline(result,row) {
  const events=shutdownEvents(result),duration=Math.max(result?.samples?.at(-1)?.time_s||0,...events.map(event=>event.time_s),1e-12);
  if(!events.length)return '';
  const left=18,right=982,x=t=>left+t/duration*(right-left),groups=eventLabelLayout(events,x,0,duration,left,right).groups;
  const markers=groups.map(group=>`<g class="sd-timeline-marker ${group.events.every(event=>event.observed===false)?'sd-pending-event':''}"><title>${group.events.map(event=>`${esc(event.label)} · ${fmt(event.time_s)} s`).join(' / ')}</title><rect x="${group.x}" y="7" width="${group.width+8}" height="20" rx="4"/><text x="${group.x+(group.width+8)/2}" y="21" text-anchor="middle">${group.label}</text></g>`).join('');
  return `<div class="sd-timeline"><svg viewBox="0 0 1000 35" role="img" aria-label="关机与再启动事件时间轴"><line class="sd-timeline-base" x1="${left}" x2="${right}" y1="17" y2="17"/><line class="sd-timeline-elapsed" x1="${left}" x2="${x(Math.max(0,Math.min(duration,row.time_s)))}" y1="17" y2="17"/>${markers}</svg><div class="sd-timeline-labels"><span>0 s</span><span>${fmt(duration)} s</span></div></div>`;
}
const phaseLabels={liquid:'液相',gas:'气相',twophase:'气液两相',supercritical:'超临界流体',supercritical_liquid:'超临界液体区',supercritical_gas:'超临界气体区',critical_point:'临界点'};
const sampleFields={gas:[['pressure_Pa','绝对压力 / MPa',1e-6],['temperature_K','气体温度 / K',1],['wall_temperature_K','壁面温度 / K',1],['mass_kg','气相库存 / g',1000],['ox_mass_flow_kg_s','氧气入口 / g/s',1000],['fuel_mass_flow_kg_s','甲烷入口 / g/s',1000],['outlet_mass_flow_kg_s','排出流量 / g/s',1000],['igniter_power_W','外加热功率 / kW',.001]],inventory:[['pressure_Pa','绝对压力 / MPa',1e-6],['temperature_K','流体温度 / K',1],['wall_temperature_K','壁面温度 / K',1],['mass_kg','总库存 / kg',1],['liquid_mass_kg','液相库存 / kg',1],['vapor_mass_kg','气相库存 / kg',1],['liquid_volume_fraction','液体体积分数 / %',100],['outlet_mass_flow_kg_s','排出流量 / kg/s',1]],rotor:[['manifold_pressure_Pa','容腔绝压 / MPa',1e-6],['speed_rpm','转子转速 / rpm',1],['pump_torque_Nm','泵轴转矩 / N·m',1],['drive_torque_Nm','外部驱动转矩 / N·m',1],['pump_mass_flow_kg_s','泵支路流量 / kg/s',1],['outlet_mass_flow_kg_s','阀支路流量 / kg/s',1],['rotor_energy_J','转子动能 / J',1],['valve_opening','实际阀位 / %',100]]};

export function renderShutdownResult(record,index=0,fallbackCharts=[]) {
  const result=record?.result,rows=result?.samples||[],mode=result?.mode||record?.request?.mode||'gas';
  if(!rows.length)return `<div class="sd-empty"><span>SHUTDOWN / HOLD / RESTART</span><h3>${active(record)?'正在积分连续状态与给定事件':record?'本记录暂无有效时间采样':'从同一连续初态计算关机、停留与再开启'}</h3><p>分别研究气相反应、真实流体库存和转子管网；各单元保留自身的质量、能量与执行机构状态。</p><p>各单元独立计算，尚未构成整机耦合再启动模型。</p></div>`;
  const row=rows[Math.min(Math.max(0,index),rows.length-1)],circuits=row.circuits?.length?row.circuits:[{...row,id:'gas',label:'气相反应容腔'}],panels=shutdownPanels(result,fallbackCharts),request=result.request||record.request||{};
  return `<div class="sd-result-heading"><div><span>CONTINUOUS STATE / EVENT RESPONSE</span><h2>${esc(modeLabels[mode]||'关机与再启动')} · 时间响应</h2><p>${rows.length} 个实际时间采样 · 参数、模型与图表定义随记录冻结</p></div><div><button data-shutdown-action="csv">导出 CSV</button><button data-shutdown-action="svg" ${panels.length?'':'disabled'}>导出 SVG</button><button data-shutdown-action="snapshot" ${result.endpoint_snapshot?'':'disabled'}>导出末态快照</button></div></div>${result.complete===false?notice('本记录仅包含有效积分时间段；停止时保留有效末态，不代表给定关机与再启动过程已完成。'):''}${result.reason&&result.reason!=='completed'?notice(result.reason):''}${result.termination_reason&&result.termination_reason!=='completed'&&result.termination_reason!==result.reason?notice(result.termination_reason):''}<div class="sd-result-scope"><b>独立研究单元</b><span>${esc(modeLabels[mode]||mode)}</span><span>不作整机再启动成功判定</span></div><section class="sd-sample"><div class="sd-section-heading"><div><h3>时间截面与状态延续</h3><p>事件改变边界输入；库存、内能、壁温及转动状态沿积分连续演化。</p></div><b class="sd-time">${fmt(row.time_s)} <small>s</small></b></div>${timeline(result,row)}<label class="sd-time-slider">采样时刻：${fmt(row.time_s)} s<input data-shutdown-sample type="range" min="0" max="${rows.length-1}" value="${rows.indexOf(row)}" step="1" aria-label="关机与再启动时间截面"></label>${circuits.map(circuit=>`<div class="sd-circuit-state"><h4>${esc(circuit.label||circuit.id||'研究容腔')}${circuit.phase?`<small>${esc(phaseLabels[circuit.phase]||circuit.phase)}</small>`:''}</h4><div class="sd-sample-grid">${(sampleFields[mode]||sampleFields.gas).map(([key,label,factor])=>`<div><span>${label}</span><b>${fmt(finite(circuit[key])?circuit[key]*factor:null)}</b></div>`).join('')}</div></div>`).join('')}</section><details class="sd-group" open><summary>事件时刻与图中标记</summary>${eventTable(result)}</details>${panels.length?`<div class="sd-chart-grid">${panels.map(plot).join('')}</div>`:notice('本历史记录未保存图表定义；完整数值仍可从 CSV 和 JSON 导出。')}<details class="sd-group" open><summary>过程指标与守恒审计</summary><div class="sd-audit-columns"><section><h3>过程指标</h3>${dataTable(result.metrics)}</section><section><h3>质量与能量审计</h3>${dataTable(result.audits)}</section></div></details><details class="sd-group"><summary>计算过程、冻结输入与模型范围</summary><div class="sd-model"><ol><li>从本研究单元的初始库存、能量及执行机构状态开始积分。</li><li>按给定关阀、停留与再开启事件更新边界，不在阶段切换时重置状态。</li><li>记录实际响应、流动方向、连续能量账目和适用域终止原因。</li><li>保存有效末态；局部响应与守恒通过不替代试验验证。</li></ol><p>${esc(result.model_description?.title||result.model_description?.name||'关机与再启动独立研究单元')}</p>${Array.isArray(result.model_description?.equations)?`<div class="sd-equations">${result.model_description.equations.map(eq=>`<code>${esc(eq)}</code>`).join('')}</div>`:''}<ul>${(result.limitations||[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ul><details><summary>完整模型与冻结输入</summary><pre>${esc(JSON.stringify({mode,model:result.model_description,request},null,2))}</pre></details></div></details>`;
}
export function buildShutdownCsv(result) {
  const rows=(result?.samples||[]).map(row=>{const flat=flatten(row);for(const circuit of row.circuits||[])flatten(circuit,`circuit.${circuit.id}`,flat);return flat;});
  const keys=[...new Set(rows.flatMap(row=>Object.keys(row)))],cell=value=>`"${String(value??'').replace(/"/g,'""')}"`;
  return '\uFEFF'+[keys,...rows.map(row=>keys.map(key=>typeof row[key]==='string'?"'"+row[key]:finite(row[key])||typeof row[key]==='boolean'?row[key]:''))].map(row=>row.map(cell).join(',')).join('\r\n');
}
export function buildShutdownSvg(result,fallbackCharts=[]) {
  const panels=shutdownPanels(result,fallbackCharts),events=shutdownEvents(result),rowCount=Math.ceil(panels.length/2),height=115+rowCount*420+45+events.length*22;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1320" height="${height}" viewBox="0 0 1320 ${height}"><style>text{font:11px Arial,'Microsoft YaHei',sans-serif;fill:#526c7c}.sd-grid{stroke:#e0e7ec}.sd-axis{stroke:#96a7b3;fill:none}.sd-line{fill:none;stroke-width:2}.sd-zero{stroke:#9a8aaa;stroke-dasharray:4 4}.sd-event line{stroke:#b8a581;stroke-dasharray:3 5;opacity:.6}.sd-event text{font-size:8px;fill:#9a825a}.sd-export-title{font-size:22px}.sd-export-subtitle{font-size:15px}.sd-export-events{font-size:13px}</style><rect width="1320" height="${height}" fill="white"/><text x="30" y="35" class="sd-export-title">${esc(modeLabels[result.mode]||'关机与再启动')} · 时间响应</text><text x="30" y="60">独立研究单元 · 连续状态 · 不作整机再启动成功判定</text><text x="30" y="82">来源 ${esc(result.source?.job_id||'—')} · ${result.complete===false?'仅有效积分时间段':'按记录保存的实际时间采样'}</text>${panels.map((panel,i)=>{const svg=plot(panel,i).match(/<svg[\s\S]*?<\/svg>/)?.[0]||'';return `<g transform="translate(${25+i%2*655} ${110+Math.floor(i/2)*420})"><text x="10" y="20" class="sd-export-subtitle">${esc(panel.name)}</text><svg x="0" y="30" width="620" height="290" viewBox="0 0 620 290">${svg.replace(/^<svg[^>]*>/,'').replace(/<\/svg>$/,'')}</svg>${panel.series.map((s,j)=>`<g transform="translate(${15+j%2*305} ${342+Math.floor(j/2)*20})"><line x1="0" x2="17" y1="-4" y2="-4" stroke="${s.color}" stroke-width="2" ${s.dashed?'stroke-dasharray="5 3"':''}/><text x="23" y="0">${esc(s.label)}</text></g>`).join('')}</g>`;}).join('')}<g transform="translate(35 ${115+rowCount*420})"><text y="0" class="sd-export-events">事件标记</text>${events.map((event,i)=>`<text y="${25+i*22}">E${String(i+1).padStart(2,'0')} · ${fmt(event.time_s)} s · ${esc(event.label)} · ${event.observed===false?'尚未到达':'按记录显示'}</text>`).join('')}</g></svg>`;
}
export function buildShutdownSnapshot(record) {
  if(!record?.result?.endpoint_snapshot)throw Error('本记录尚未生成有效末态快照。');
  return {schema:'shutdown-restart-endpoint-v1',study_id:record.id,mode:record.result.mode||record.request?.mode,source:clone(record.result.source||{}),complete:record.result.complete,reason:record.result.reason||record.result.termination_reason,scope:'仅为本独立研究单元末态；不代表整机再启动就绪或成功。',engine_initialization_supported:false,endpoint_snapshot:clone(record.result.endpoint_snapshot),audits:clone(record.result.audits||{})};
}
export function shutdownRunBlockReason(source,setup,modeKey,busy,count=0,error='') {
  if(!source)return '请选择已完成的设计记录，以确定工质和方案来源。';
  if(!setup)return error?'初始化未完成，请检查连接后刷新。':'正在读取研究单元设置。';
  if(!getMode(setup,modeKey)?.ready)return '当前研究单元尚不支持该来源或初态，请查看上方说明。';
  if(busy)return '正在提交任务，请稍候。';
  if(count>=2)return '本项目已有 2 个等待或运行中的关机研究，请等待完成或取消。';
  return '';
}

export function mountShutdown(container,{api,getContext,notify=()=>{}}) {
  let projectId='',sourceId='',setup=null,modeKey='gas',drafts={},records=[],selected=null,selectedIndex=0,error='',setupError='',busy=false,live=true,timer=null,seq=0;
  const openGroups=new Set(),context=()=>getContext()||{},mode=()=>getMode(setup,modeKey),values=()=>drafts[modeKey]||{};
  const source=()=>{const c=context();return c.record?.status==='succeeded'&&c.record.project_id===c.project?.id?c.record:null;};
  const stop=()=>{if(timer!==null)clearTimeout(timer);timer=null;};
  function syncContext(){const p=context().project?.id||'',s=source()?.id||'';if(p!==projectId||s!==sourceId){++seq;stop();if(p!==projectId){records=[];selected=null;selectedIndex=0;}projectId=p;sourceId=s;setup=null;drafts={};error='';setupError='';busy=false;}}
  const current=(token,p,s)=>live&&seq===token&&context().project?.id===p&&(source()?.id||'')===s;
  function field(p,circuitIndex=null) {
    const value=circuitIndex===null?values()[p.key]:values().circuits?.[circuitIndex]?.[p.key],factor=p.factor||1,attrs=`data-shutdown-field="${esc(p.key)}" ${circuitIndex===null?'':`data-shutdown-circuit="${circuitIndex}"`}`;
    const control=p.type==='boolean'?`<input type="checkbox" ${attrs} ${value?'checked':''} ${p.readonly?'disabled':''}>`:Array.isArray(p.options)?`<select ${attrs} ${p.readonly?'disabled':''}>${p.options.map(option=>`<option value="${esc(option.value)}" ${option.value===value?'selected':''}>${esc(option.label||option.value)}</option>`).join('')}</select>`:`<input type="number" ${attrs} value="${esc(value??'')}" ${finite(p.min)?`min="${p.min*factor}"`:''} ${finite(p.max)?`max="${p.max*factor}"`:''} step="${finite(p.step)?p.step:'any'}" ${p.readonly?'readonly':''}>`;
    return `<label class="${p.type==='boolean'?'sd-boolean':''}"><span>${esc(p.label||p.key)}${p.unit&&p.unit!=='—'?` / ${esc(p.unit)}`:''}</span>${control}<small>${esc(p.description||'')}</small></label>`;
  }
  function parameterGroups(parameters,circuitIndex=null) {
    const groups=[...new Set(parameters.map(p=>p.group||'研究参数'))];
    return groups.map((group,i)=>{const groupKey=`${modeKey}:${circuitIndex??'global'}:${group}`,isOpen=openGroups.has(groupKey)||(!openGroups.has(`closed:${groupKey}`)&&i<2);return `<details class="sd-group" data-shutdown-group="${esc(groupKey)}" ${isOpen?'open':''}><summary><span>${String(i+1).padStart(2,'0')}</span>${esc(group)}</summary><div class="sd-field-grid">${parameters.filter(p=>(p.group||'研究参数')===group).map(p=>field(p,circuitIndex)).join('')}</div></details>`;}).join('');
  }
  function resultCharts(){const result=selected?.result;return result?.charts||((result?.mode||selected?.request?.mode)===modeKey?mode()?.charts||[]:getMode(setup,result?.mode||selected?.request?.mode)?.charts||[]);}
  function render(){
    const s=source(),ctx=context(),m=mode(),block=shutdownRunBlockReason(s,setup,modeKey,busy,records.filter(active).length,setupError),progress=selected?.progress||{},samples=selected?.result?.samples||[],maximum=Math.max(selected?.request?.settings?.sample_count||0,finite(progress.maximum)?progress.maximum:0,samples.length);
    const focused=typeof document!=='undefined'&&container.contains?.(document.activeElement)?document.activeElement:null,focusKey=focused?.dataset?.shutdownField,focusCircuit=focused?.dataset?.shutdownCircuit;
    container.innerHTML=`<section class="shutdown-workspace" aria-label="关机与再启动研究工作区"><div class="sd-heading"><div><span>SHUTDOWN / HOLD / RESTART</span><h2>关机与再启动研究</h2><p>给定时序 · 状态连续 · 残余库存 · 壁面余热 · 转子与反流</p></div><button data-shutdown-action="refresh" ${busy?'disabled':''}>刷新记录</button></div><div class="sd-source"><b>追溯记录</b><span>${s?`方案 v${s.revision} · ${stamp(s.created)} · ${esc(s.id)}`:'请选择已完成的设计记录'}</span><span>独立研究边界 / 冻结来源</span></div>${s&&(ctx.dirty||s.revision!==ctx.project?.revision)?notice('来源使用所选设计记录的冻结输入；左侧当前编辑参数不参与本页计算。各研究单元的初态与时序在本页独立设置。'):''}${setupError?notice(setupError,true):''}${error?notice(error,true):''}<nav class="sd-modes" aria-label="独立研究单元">${(setup?.modes||Object.entries(modeLabels).map(([value,label])=>({value,label,description:'正在读取模型设置'}))).map((item,i)=>`<button data-shutdown-mode="${esc(item.value)}" aria-pressed="${item.value===modeKey}" ${busy?'disabled':''}><span class="sd-mode-number">0${i+1}</span><span><b>${esc(item.label||modeLabels[item.value])}</b><small>${esc(item.description||'')}</small></span><i>${item.ready===false?'需复核':item.value===modeKey?'已选择':'研究单元'}</i></button>`).join('')}</nav>${notice('三个研究单元各自保持状态连续并独立计算，尚未连接成整机耦合关机与再启动模型。给定再点火事件不代表自主点火成功判定。')}<section class="sd-setup"><div class="sd-section-heading"><div><h3>${esc(m?.label||modeLabels[modeKey]||'研究单元')} · 输入与时序</h3><p>${esc(m?.description||'读取模型、适用范围和可编辑参数。')}</p></div>${badge(m?.ready?'ready':m?'review':'unknown')}</div>${(m?.issues?.length||(!setup?.ready&&setup?.issues?.length))?`<ul class="sd-issues">${[...(m?.issues||[]),...(!setup?.ready?setup?.issues||[]:[])].map(item=>`<li>${esc(typeof item==='string'?item:JSON.stringify(item))}</li>`).join('')}</ul>`:''}${parameterGroups(m?.parameters||[])}${(values().circuits||[]).map((circuit,index)=>`<section class="sd-circuit-input"><div class="sd-circuit-heading"><span>回路 ${String(index+1).padStart(2,'0')}</span><h3>${esc(circuit.label||circuit.id)}</h3><small>${esc(circuit.fluid||'')}</small></div>${parameterGroups(m?.circuit_parameters||[],index)}</section>`).join('')}<details class="sd-scope"><summary>模型边界与参数依据</summary><ul>${[...(setup?.limitations||[]),...(m?.limitations||[])].map(item=>`<li>${esc(item)}</li>`).join('')}</ul></details><div class="sd-run-row"><p id="shutdown-run-reason" role="status">${esc(block||'按给定事件连续积分；保存有效时间段、守恒审计与末态快照。')}</p><button class="primary" data-shutdown-action="run" ${block?'disabled aria-describedby="shutdown-run-reason"':''}>${busy?'提交中…':'运行本研究单元'}</button></div></section><section class="sd-output"><div class="sd-history"><label>关机与再启动研究记录<select data-shutdown-record ${busy?'disabled':''}><option value="">选择已保存的分析</option>${records.map(record=>`<option value="${esc(record.id)}" ${record.id===selected?.id?'selected':''}>${esc(modeLabels[record.request?.mode||record.mode]||'研究记录')} · v${record.revision} · ${stamp(record.created)} · ${esc(names[record.status]||record.status)}</option>`).join('')}</select></label><button data-shutdown-action="json" ${selected?'':'disabled'}>导出 JSON</button><button data-shutdown-action="cancel" ${active(selected)&&!busy?'':'disabled'}>取消分析</button></div>${selected?`<div class="sd-job-state">${badge(selected.status==='succeeded'&&selected.result?.complete===false?'review':selected.status)}<span>${esc(progress.phase||'')}</span><span>${samples.length} 个时间采样</span><small>记录 ${esc(selected.id.slice(0,8))}</small></div>`:''}${maximum?`<progress max="${maximum}" value="${Math.min(maximum,finite(progress.completed)?progress.completed:samples.length)}" aria-label="关机与再启动积分进度"></progress>`:''}${selected?.error?notice(selected.error,true):''}${selected?notice('下方结果与导出使用所选历史记录的冻结输入；上方切换研究单元或修改参数不会改变既有结果。'):''}${renderShutdownResult(selected,selectedIndex,resultCharts())}</section></section>`;
    if(focusKey){const matches=container.querySelectorAll?.('[data-shutdown-field]')||[],next=[...matches].find(el=>el.dataset.shutdownField===focusKey&&el.dataset.shutdownCircuit===focusCircuit);next?.focus?.({preventScroll:true});}
  }
  function schedule(){stop();if(live&&records.some(active))timer=setTimeout(()=>refresh(),1500);}
  async function refresh(){
    if(busy)return;live=true;stop();syncContext();const p=projectId,s=sourceId,token=++seq;render();if(!p)return;
    const [listResponse,configResponse]=await Promise.allSettled([api(`/projects/${encodeURIComponent(p)}/shutdown-studies`),s&&!setup?api(`/projects/${encodeURIComponent(p)}/shutdown-studies/setup?source_job_id=${encodeURIComponent(s)}`):Promise.resolve(setup)]);
    if(!current(token,p,s))return;
    if(configResponse.status==='fulfilled'){const config=configResponse.value;if(config&&!setup){setup=config;drafts=Object.fromEntries((config.modes||[]).map(item=>[item.value,shutdownDisplayDefaults(item)]));if(!getMode(config,modeKey))modeKey=config.modes?.find(item=>item.ready)?.value||config.modes?.[0]?.value||'gas';}setupError='';}else setupError=configResponse.reason?.message||'无法读取研究单元设置。';
    if(listResponse.status==='fulfilled'){
      records=Array.isArray(listResponse.value)?listResponse.value:[];
      const next=records.find(record=>record.id===selected?.id)||records[0];
      try{if(next){const detail=await api(`/shutdown-studies/${encodeURIComponent(next.id)}`);if(!current(token,p,s))return;selected=detail;}else selected=null;error='';}
      catch(e){if(current(token,p,s))error=e.message;}
    }else error=listResponse.reason?.message||'无法读取研究记录。';
    if(current(token,p,s)){render();schedule();}
  }
  async function run(){
    if(busy)return;syncContext();let request;try{const block=shutdownRunBlockReason(source(),setup,modeKey,busy,records.filter(active).length,setupError);if(block)throw Error(block);request=buildShutdownRequest(source(),setup,modeKey,values());}catch(e){error=e.message;render();return;}
    const p=projectId,s=sourceId,token=++seq;stop();busy=true;error='';render();
    try{const created=await api(`/projects/${encodeURIComponent(p)}/shutdown-studies`,'POST',request);if(!current(token,p,s))return;selected={...created,request,revision:source().revision,created:Date.now()/1000};records=[selected,...records];selectedIndex=0;notify('关机与再启动研究任务已提交。');}
    catch(e){if(current(token,p,s))error=e.message;}
    finally{if(current(token,p,s)){busy=false;render();schedule();}}
  }
  async function choose(id){if(busy)return;stop();const p=projectId,s=sourceId,token=++seq;if(!id){selected=null;render();schedule();return;}try{const detail=await api(`/shutdown-studies/${encodeURIComponent(id)}`);if(current(token,p,s)){selected=detail;selectedIndex=0;error='';render();schedule();}}catch(e){if(current(token,p,s)){error=e.message;render();schedule();}}}
  async function cancel(){if(!active(selected)||busy)return;const p=projectId,s=sourceId,token=++seq;stop();busy=true;render();try{await api(`/shutdown-studies/${encodeURIComponent(selected.id)}/cancel`,'POST',{});if(!current(token,p,s))return;busy=false;await refresh();}catch(e){if(current(token,p,s)){busy=false;error=e.message;render();schedule();}}}
  function download(format){if(!selected||(format!=='json'&&!selected.result?.samples?.length))return;try{const data=format==='json'?JSON.stringify(selected,null,2):format==='snapshot'?JSON.stringify(buildShutdownSnapshot(selected),null,2):format==='csv'?buildShutdownCsv(selected.result):buildShutdownSvg(selected.result,resultCharts()),ext=format==='snapshot'?'json':format,type=ext==='json'?'application/json':ext==='csv'?'text/csv;charset=utf-8':'image/svg+xml;charset=utf-8',url=URL.createObjectURL(new Blob([data],{type})),a=document.createElement('a');a.href=url;a.download=`shutdown-${selected.result?.mode||selected.request?.mode}-${format==='snapshot'?'endpoint-':'study-'}${selected.id}.${ext}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){error=e.message;render();}}
  function updateField(target){const d=target.dataset||{},key=d.shutdownField;if(!key)return;const circuit=d.shutdownCircuit===undefined?null:Number(d.shutdownCircuit),p=(circuit===null?mode()?.parameters:mode()?.circuit_parameters)?.find(item=>item.key===key);if(!p||p.readonly)return;const targetValues=circuit===null?values():values().circuits?.[circuit];if(!targetValues)return;targetValues[key]=p.type==='boolean'?target.checked:Array.isArray(p.options)?p.options.find(option=>String(option.value)===target.value)?.value:target.value;}
  container.addEventListener('input',event=>updateField(event.target));
  container.addEventListener('change',event=>{updateField(event.target);if(event.target.hasAttribute?.('data-shutdown-record'))choose(event.target.value);if(event.target.hasAttribute?.('data-shutdown-sample')){selectedIndex=Number(event.target.value);render();}});
  container.addEventListener('toggle',event=>{const group=event.target.dataset?.shutdownGroup;if(group){if(event.target.open){openGroups.add(group);openGroups.delete(`closed:${group}`);}else{openGroups.delete(group);openGroups.add(`closed:${group}`);}}},true);
  container.addEventListener('click',event=>{const button=event.target.closest?.('button');if(!button||button.disabled)return;const action=button.dataset.shutdownAction,requestedMode=button.dataset.shutdownMode;if(requestedMode&&getMode(setup,requestedMode)&&!busy){modeKey=requestedMode;error='';render();}else if(action==='run')run();else if(action==='refresh')refresh();else if(action==='cancel')cancel();else if(['json','csv','svg','snapshot'].includes(action))download(action);});
  syncContext();render();return {refresh,sync(){if(live){syncContext();render();}},onLeave(){live=false;++seq;busy=false;stop();}};
}
