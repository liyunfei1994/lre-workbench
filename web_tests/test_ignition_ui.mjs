import assert from 'node:assert/strict';
import test from 'node:test';
import {ignitionDisplayDefaults,buildIgnitionRequest,ignitionEvents,ignitionPanels,renderIgnitionResult,buildIgnitionCsv,buildIgnitionSvg,buildIgnitionSnapshot,ignitionRunBlockReason,mountIgnition} from '../webapp/static/ignition.js';

const source={id:'design-1',project_id:'p1',revision:2,created:1,status:'succeeded'};
const config=()=>({ready:true,issues:[],defaults:{duration_s:3,sample_count:301,rtol:1e-7,initial_mixture:'nitrogen',chemistry_enabled:true,initial_pressure_Pa:1e5,initial_temperature_K:300,volume_m3:.001,initial_of_ratio:4,inlet_ox_CdA_m2:1e-6,final_ox_opening:1,ox_open_time_s:.1,fuel_open_time_s:.2,ignition_time_s:.5,ignition_duration_s:.02,ignition_energy_J:1500,min_pressure_Pa:5e4,max_pressure_Pa:2e6,min_temperature_K:300,max_temperature_K:3000},parameters:[
 {key:'duration_s',label:'仿真时长',min:.005,max:10,step:.1,group:'时间设置'},
 {key:'sample_count',label:'输出采样数',min:21,max:1001,step:1,group:'时间设置'},
 {key:'initial_pressure_Pa',label:'初始绝对压力',min:1e4,max:2e6,factor:1e-6,unit:'MPa',step:.01,group:'容腔初态'},
 {key:'initial_temperature_K',label:'初始气体温度',min:300,max:2000,step:10,group:'容腔初态'},
 {key:'volume_m3',label:'反应容腔容积',min:1e-6,max:.1,factor:1000,unit:'L',step:.01,group:'容腔初态'},
 {key:'inlet_ox_CdA_m2',label:'氧气全开有效面积',min:0,max:1e-3,factor:1e6,unit:'mm²',step:.1,group:'气相供给边界'},
 {key:'final_ox_opening',label:'氧阀末端指令',min:0,max:1,factor:100,unit:'%',step:1,group:'阀门时序'},
 {key:'ignition_time_s',label:'外部点火事件时刻',min:0,max:10,step:.01,group:'外部点火事件'},
 {key:'ignition_duration_s',label:'点火热脉冲历时',min:.0001,max:5,step:.005,group:'外部点火事件'},
 {key:'ignition_energy_J',label:'外部点火输入能量',min:0,max:1e5,step:1,group:'外部点火事件'},
 ],source_constraints:{checks:[{label:'原设计供给裕度',value:-4.367,unit:'MPa',status:'fail'}]},limitations:['气相研究台，未耦合整机启动与液体雾化。']});
const sample=t=>({time_s:t,pressure_Pa:1e5+t*2e5,temperature_K:300+t*500,wall_temperature_K:300+t*10,mass_kg:.001+t*.001,internal_energy_J:500+t*1000,ox_mass_flow_kg_s:.004,fuel_mass_flow_kg_s:.001,outlet_mass_flow_kg_s:.002,ox_opening:.9,fuel_opening:.8,ox_command:1,fuel_command:1,igniter_power_W:t===.5?75000:0,chemical_heat_release_W:t*1e4,heat_to_wall_W:t*500,outlet_choked:t>.2,composition:{N2:.6,CH4:.08,O2:.2,H2O:.08,CO2:.04},mass_balance_error_kg:t*1e-13,energy_balance_error_J:t*1e-7,fluid_energy_balance_error_J:t*1e-7,wall_energy_balance_error_J:0,element_balance_error_kg:{C:t*1e-15,H:0,O:0,N:t*1e-14}});
const result=()=>({request:config().defaults,source:{job_id:source.id},samples:[sample(0),sample(.5),sample(1)],complete:true,termination_reason:'completed',metrics:{reference_pressure_Pa:3e5,pressure_threshold_time_s:1,ignition_success:{predicted:false,basis:'当前无实机判据'}},audits:{status:'pass',experiment_validated:false,max_abs_mass_balance_error_kg:1e-13,max_abs_energy_balance_error_J:1e-7},model_description:{title:'气相有限速率反应容腔',equations:['dU/dt = Σ m_in h_in − m_out h + Q_ign − Q_wall']},endpoint_snapshot:{time_s:1,pressure_Pa:3e5,composition:{CH4:.08}},limitations:['压力阈值到达时间不是点火延迟。']});
const record=()=>({id:'ignition-1',project_id:'p1',revision:2,created:1,status:'succeeded',request:{...config().defaults,source_job_id:source.id},result:result()});

test('numeric display units round-trip to SI without modifying setup or adding arbitrary fields',()=>{
 const setup=config(),before=structuredClone(setup),display=ignitionDisplayDefaults(setup);assert.equal(display.initial_pressure_Pa,.1);assert.equal(display.volume_m3,1);assert.equal(display.inlet_ox_CdA_m2,1);assert.equal(display.final_ox_opening,100);display.mechanism='kerosene';
 assert.deepEqual(buildIgnitionRequest(source,setup,display),{...before.defaults,source_job_id:source.id});assert.deepEqual(setup,before);
});
test('request rejects invalid numbers, range, sample count and unsupported initial mixture',()=>{
 for(const bad of ['',null,true,NaN,Infinity,-1,5]){const v=ignitionDisplayDefaults(config());v.initial_pressure_Pa=bad;assert.throws(()=>buildIgnitionRequest(source,config(),v));}
 const v=ignitionDisplayDefaults(config());v.sample_count=21.5;assert.throws(()=>buildIgnitionRequest(source,config(),v),/整数/);v.sample_count=301;v.initial_mixture='liquid';assert.throws(()=>buildIgnitionRequest(source,config(),v),/氮气/);v.initial_mixture='reactants';v.chemistry_enabled='false';assert.throws(()=>buildIgnitionRequest(source,config(),v),/布尔/);
});
test('finite-rate chemistry and zero-energy controls are explicit and retain frozen defaults',()=>{
 const v=ignitionDisplayDefaults(config());v.chemistry_enabled=false;v.ignition_energy_J=0;v.initial_mixture='reactants';const req=buildIgnitionRequest(source,config(),v);assert.equal(req.chemistry_enabled,false);assert.equal(req.ignition_energy_J,0);assert.equal(req.initial_mixture,'reactants');assert.equal(config().defaults.chemistry_enabled,true);
});
test('event bounds prevent a heat pulse beyond the output interval and require a source',()=>{
 const v=ignitionDisplayDefaults(config());v.ignition_time_s=2.99;assert.throws(()=>buildIgnitionRequest(source,config(),v),/热脉冲/);
 assert.throws(()=>buildIgnitionRequest({...source,status:'running'},config(),{}),/已完成/);assert.throws(()=>buildIgnitionRequest(source,{...config(),ready:false},{}),/初始化/);
});
test('eleven panels separate applied heat from chemical diagnostics and flatten species/element data',()=>{
 const panels=ignitionPanels(result());assert.equal(panels.length,11);assert.equal(panels[4].series[0].key,'igniter_power_W');assert.equal(panels[5].series[0].key,'chemical_heat_release_W');assert.equal(panels[6].rows[0]['Y.CH4'],.08);assert.ok(panels[9].series.some(s=>s.key==='element.C'));assert.equal(panels[10].series.length,3);
 const html=renderIgnitionResult(record(),1);assert.equal((html.match(/class="ig-chart"/g)||[]).length,11);assert.match(html,/采样时刻：0.5 s/);assert.match(html,/不是点火延迟/);assert.match(html,/未作自主判定/);assert.doesNotMatch(html,/NaN|Infinity|undefined/);
});
test('event markers preserve exact supplied times and never derive an ignition success event',()=>{
 const r=result();assert.deepEqual(ignitionEvents(r).map(e=>e.time_s),[.1,.2,.5,.52]);r.events=[{time_s:.503,label:'求解器报告事件',description:'已保存边界'},{time_s:null,label:'缺失'}];assert.equal(ignitionEvents(r).length,1);assert.equal(ignitionEvents(r)[0].time_s,.503);assert.match(renderIgnitionResult({...record(),result:r}),/E01/);assert.match(buildIgnitionSvg(r),/0.503 s/);
});
test('given rectangular heat pulse is drawn with exact step edges and the pressure reference is labeled',()=>{
 const r=result(),panels=ignitionPanels(r);assert.equal(panels[4].series[0].step,true);assert.equal(panels[0].series[1].label,'用户参考压力');assert.equal(panels[0].rows[0].reference_pressure_Pa,3e5);
 const html=renderIgnitionResult({...record(),result:r}),path=html.match(/data-series="igniter_power_W"[^>]*d="([^"]*)"/)[1];assert.match(path,/H[^V]+V/);assert.doesNotMatch(path,/L/);assert.match(html,/参考线仅为用户建压阈值/);
});
test('core event observed flags and numeric audit keys retain Chinese semantics',()=>{
 const rec=record();rec.result.events=[{name:'给定点火热脉冲开始',time_s:.5,kind:'prescribed',observed:true},{name:'给定点火热脉冲结束',time_s:3.1,kind:'prescribed',observed:false}];rec.result.audits={status:'pass',max_abs_total_energy_error_J:1e-7,max_abs_fluid_energy_error_J:1e-7,max_abs_wall_energy_error_J:0};rec.result.metrics={ignition_success:null,ignition_delay_s:null,extinction_detected:null};const html=renderIgnitionResult(rec);assert.match(html,/有效积分已到达/);assert.match(html,/尚未到达/);assert.match(html,/给定输入事件；不代表自主燃烧判定/);assert.match(html,/流体能量最大绝对残差/);assert.match(html,/本阶段不作此判定/);
});
test('nearby event labels merge in pixel space while all individual lines, titles and table rows remain',()=>{
 const rec=record();rec.result.samples=[sample(0),sample(3)];rec.result.events=[.05,.05,.1,.1,.3,.32].map((time_s,i)=>({time_s,name:`独立事件${i+1}`,observed:true}));
 const html=renderIgnitionResult(rec),svg=html.match(/<svg[\s\S]*?<\/svg>/)[0];assert.equal((svg.match(/data-event-marker=/g)||[]).length,6);assert.equal((svg.match(/data-event-label=/g)||[]).length,1);assert.match(svg,/data-event-label="1,2,3,4,5,6"/);assert.match(svg,/>E01–E06<\/text>/);
 for(let i=1;i<=6;i++){assert.match(svg,new RegExp(`<title>E0${i} · 独立事件${i} ·`));assert.match(html,new RegExp(`<td>独立事件${i}<\/td>`));}
});
test('event label bounds and collision spacing survive both plot edges and unsorted event numbers',()=>{
 const rec=record();rec.result.samples=[sample(0),sample(3)];rec.result.events=[0,3,.02,2.98,1.4,1.41].map((time_s,i)=>({time_s,label:`事件${i+1}`}));
 const firstSvg=renderIgnitionResult(rec).match(/<svg[\s\S]*?<\/svg>/)[0],labels=[...firstSvg.matchAll(/data-event-label="([^"]+)" x="([^"]+)" y="[^"]+" textLength="([^"]+)"/g)].map(match=>({ids:match[1],left:Number(match[2]),right:Number(match[2])+Number(match[3])}));
 assert.equal(labels.length,3);assert.deepEqual(labels.map(label=>label.ids),['1,3','5,6','2,4']);for(const label of labels){assert.ok(label.left>=85);assert.ok(label.right<=576+1e-9);}for(let i=1;i<labels.length;i++)assert.ok(labels[i].left-labels[i-1].right>=8-1e-9);
 assert.match(firstSvg,/>E01 \/ E03<\/text>/);assert.match(firstSvg,/>E02 \/ E04<\/text>/);assert.equal((firstSvg.match(/data-event-marker=/g)||[]).length,6);
 const exported=buildIgnitionSvg(rec.result);assert.match(exported,/data-event-label="1,3"/);assert.doesNotMatch(exported,/NaN|Infinity/);
});
test('missing samples break curves and no empty result invents chart data',()=>{
 const rec=record();rec.result.samples[1].temperature_K=null;const html=renderIgnitionResult(rec,1),path=html.match(/data-series="temperature_K"[^>]*d="([^"]*)"/)[1];assert.equal((path.match(/M/g)||[]).length,2);assert.doesNotMatch(path,/L/);assert.match(html,/气体温度 \/ K<\/span><b>—/);assert.doesNotMatch(renderIgnitionResult(null),/<svg/);
});
test('single sample is visible; absent diagnostics do not turn into zero series',()=>{
 const rec=record();rec.result.samples=[sample(0)];delete rec.result.samples[0].chemical_heat_release_W;assert.match(renderIgnitionResult(rec),/<circle cx=/);assert.equal(ignitionPanels(rec.result)[5].series.length,0);assert.match(renderIgnitionResult(rec),/该量暂无有效数据/);
});
test('partial records keep termination semantics and do not invent an endpoint',()=>{
 const rec=record();rec.result.complete=false;rec.result.termination_reason='达到用户温度停止边界';delete rec.result.endpoint_snapshot;const html=renderIgnitionResult(rec);assert.match(html,/不能作为完整启动过程/);assert.match(html,/达到用户温度停止边界/);assert.match(html,/data-ignition-action="snapshot" disabled/);assert.throws(()=>buildIgnitionSnapshot(rec),/尚未生成/);
});
test('snapshot remains isolated and explicitly excludes autonomous ignition and whole-engine initialization',()=>{
 const rec=record(),snapshot=buildIgnitionSnapshot(rec);assert.equal(snapshot.ignition_success_predicted,false);assert.equal(snapshot.engine_initialization_supported,false);assert.equal(snapshot.study_id,rec.id);snapshot.endpoint_snapshot.composition.CH4=1;assert.equal(rec.result.endpoint_snapshot.composition.CH4,.08);
});
test('CSV keeps SI, all returned species, zero values and blank missing values, guarding formulas',()=>{
 const r=result();r.samples[0].note='=HYPERLINK("x")';r.samples[1].temperature_K=null;r.samples[0].composition.HCO=1e-8;const csv=buildIgnitionCsv(r);assert.match(csv,/Y.HCO/);assert.match(csv,/element.C/);assert.match(csv,/"100000"/);assert.match(csv,/"0"/);assert.match(csv,/'=HYPERLINK/);assert.doesNotMatch(csv,/null|NaN|Infinity/);
});
test('HTML/SVG escape source, event, equation and species content without foreign objects',()=>{
 const rec=record();rec.result.source.job_id='<script>x</script>';rec.result.events=[{time_s:.5,label:'<img src=x onerror=x>'}];rec.result.samples[0].composition['<img src=x>']=.1;rec.result.model_description.equations=['<script>eq</script>'];assert.doesNotMatch(renderIgnitionResult(rec),/<img|<script>/);const svg=buildIgnitionSvg(rec.result);assert.match(svg,/width="1280"/);assert.doesNotMatch(svg,/<script|<foreignObject|<image|NaN|Infinity/);assert.match(svg,/&lt;img/);
});
test('run block reasons distinguish source readiness, connectivity, submissions and queue capacity',()=>{
 assert.match(ignitionRunBlockReason(null,null,false),/请选择/);assert.match(ignitionRunBlockReason(source,null,false,0,'offline'),/检查连接/);assert.match(ignitionRunBlockReason(source,{ready:false},false),/本来源/);assert.match(ignitionRunBlockReason(source,config(),true),/正在提交/);assert.match(ignitionRunBlockReason(source,config(),false,2),/已有 2 个/);assert.equal(ignitionRunBlockReason(source,config(),false),'');
});

function container(){const listeners={};return {innerHTML:'',addEventListener(type,handler){listeners[type]=handler;},action(action){listeners.click({target:{closest:()=>({dataset:{ignitionAction:action}})}});},field(key,value){listeners.input({target:{dataset:{ignitionField:key},value}});},change(attribute,value,checked=false){listeners.change({target:{dataset:{},value,checked,hasAttribute:name=>name===attribute}});}};}
const context=()=>({project:{id:'p1',revision:2},record:source,dirty:true});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};

test('ready gas bench preserves source constraint warnings without treating them as engine clearance',async()=>{
 const view=container(),posts=[];const ui=mountIgnition(view,{getContext:context,api:async(path,method,body)=>method==='POST'?(posts.push(body),{id:'new',status:'queued'}):path.includes('/setup?')?config():[]});await ui.refresh();assert.match(view.innerHTML,/原设计供给裕度/);assert.match(view.innerHTML,/-4.367 MPa/);assert.match(view.innerHTML,/左侧当前编辑参数不参与/);assert.match(view.innerHTML,/不自主预测点火成功/);assert.doesNotMatch(view.innerHTML,/data-ignition-action="run" disabled/);view.field('initial_pressure_Pa','0.2');view.action('run');await tick();assert.equal(posts[0].initial_pressure_Pa,2e5);ui.onLeave();
});
test('unsupported kerosene source remains blocked and never silently selects methane',async()=>{
 const view=container();const ui=mountIgnition(view,{getContext:context,api:async path=>path.includes('/setup?')?{...config(),ready:false,defaults:{},issues:['煤油/正十二烷尚无本页适用的启动反应模型。']}:[]});await ui.refresh();assert.match(view.innerHTML,/煤油\/正十二烷/);assert.match(view.innerHTML,/data-ignition-action="run" disabled/);ui.onLeave();
});
test('boolean chemistry and mixture selectors survive rerender and post as typed values',async()=>{
 const view=container(),posts=[];const ui=mountIgnition(view,{getContext:context,api:async(path,method,body)=>method==='POST'?(posts.push(body),{id:'new',status:'queued'}):path.includes('/setup?')?config():[]});await ui.refresh();view.change('data-ignition-chemistry','',false);view.change('data-ignition-mixture','reactants');assert.match(view.innerHTML,/惰性流动与外加热对照/);view.action('run');await tick();assert.equal(posts[0].chemistry_enabled,false);assert.equal(posts[0].initial_mixture,'reactants');ui.onLeave();
});
test('local invalid input is editable and corrected without refreshing setup',async()=>{
 const view=container(),posts=[];const ui=mountIgnition(view,{getContext:context,api:async(path,method,body)=>method==='POST'?(posts.push(body),{id:'new',status:'queued'}):path.includes('/setup?')?config():[]});await ui.refresh();view.field('initial_pressure_Pa','');view.action('run');await tick();assert.equal(posts.length,0);assert.match(view.innerHTML,/必须是有限数值/);assert.doesNotMatch(view.innerHTML,/data-ignition-action="run" disabled/);view.field('initial_pressure_Pa','.2');view.action('run');await tick();assert.equal(posts.length,1);ui.onLeave();
});
test('API validation error remains retryable after changing inputs',async()=>{
 const view=container(),posts=[];const ui=mountIgnition(view,{getContext:context,api:async(path,method,body)=>{if(method==='POST'){posts.push(body);if(posts.length===1)throw Error('422：初态越界');return {id:'new',status:'queued'};}return path.includes('/setup?')?config():[];}});await ui.refresh();view.action('run');await tick();assert.match(view.innerHTML,/422：初态越界/);assert.doesNotMatch(view.innerHTML,/data-ignition-action="run" disabled/);view.field('initial_temperature_K','350');view.action('run');await tick();assert.equal(posts[1].initial_temperature_K,350);ui.onLeave();
});
test('changing source/project rejects late async setup data',async()=>{
 const pending=deferred(),view=container();let ctx=context();const ui=mountIgnition(view,{getContext:()=>ctx,api:()=>pending.promise});const task=ui.refresh();ctx={project:{id:'p2'},record:null};ui.sync();pending.resolve([]);await task;assert.doesNotMatch(view.innerHTML,/design-1|原设计供给/);ui.onLeave();
});
test('leaving while submitting rejects a late result and notification',async()=>{
 const pending=deferred(),view=container(),notifications=[];const ui=mountIgnition(view,{getContext:context,notify:x=>notifications.push(x),api:async(path,method)=>method==='POST'?pending.promise:path.includes('/setup?')?config():[]});await ui.refresh();view.action('run');ui.onLeave();const html=view.innerHTML;pending.resolve({id:'late',status:'queued'});await tick();assert.equal(view.innerHTML,html);assert.deepEqual(notifications,[]);
});
test('historical source and timeslice remain independent of new settings',async()=>{
 const rec=record(),view=container();rec.request.source_job_id='old-source';const ui=mountIgnition(view,{getContext:context,api:async path=>path.includes('/setup?')?config():path.startsWith('/projects/')?[rec]:rec});await ui.refresh();assert.match(view.innerHTML,/正在显示历史研究记录/);view.change('data-ignition-sample','1');assert.match(view.innerHTML,/采样时刻：0.5 s/);view.field('ignition_energy_J','0');assert.equal(rec.result.request.ignition_energy_J,1500);ui.onLeave();
});
test('event-enriched sample progress uses the server maximum instead of the base requested grid',async()=>{
 const rec=record(),view=container();rec.progress={completed:300,maximum:305};rec.request.sample_count=301;const ui=mountIgnition(view,{getContext:context,api:async path=>path.includes('/setup?')?config():path.startsWith('/projects/')?[rec]:rec});await ui.refresh();assert.match(view.innerHTML,/<progress max="305" value="300"/);ui.onLeave();
});
test('cancel targets active study and refreshes partial result after acknowledgment',async()=>{
 const rec=record(),view=container(),calls=[];rec.status='running';let cancelled=false;const ui=mountIgnition(view,{getContext:context,api:async(path,method)=>{calls.push({path,method});if(path.endsWith('/cancel')){cancelled=true;rec.status='cancelled';rec.result.complete=false;return {status:'cancelled'};}return path.includes('/setup?')?config():path.startsWith('/projects/')?[rec]:rec;}});await ui.refresh();view.action('cancel');await tick();await tick();assert.equal(cancelled,true);assert.ok(calls.some(c=>c.path==='/ignition-studies/ignition-1/cancel'&&c.method==='POST'));assert.match(view.innerHTML,/不能作为完整启动过程/);ui.onLeave();
});
