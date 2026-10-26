import assert from 'node:assert/strict';
import test from 'node:test';
import {buildChilldownRequest,chilldownDisplayDefaults,chilldownPanels,renderChilldownResult,buildChilldownCsv,buildChilldownSvg,buildChilldownSnapshot,chilldownRunBlockReason,mountChilldown} from '../webapp/static/chilldown.js';

const source={id:'design-1',project_id:'p1',revision:2,created:1,status:'succeeded'};
const circuit=id=>({id,label:id==='ox'?'液氧预冷单元':'燃料预冷单元',fluid:id==='ox'?'Oxygen':'Methane',initial_mode:'PT',initial_pressure_Pa:2e5,initial_temperature_K:290,initial_quality:1,volume_m3:.0002,supply_pressure_Pa:8e5,wall_temperature_K:290,wall_UA_W_K:15,inlet_CdA_m2:.5e-6,final_inlet_opening:1,target_liquid_volume_fraction:.95});
const config=()=>({ready:true,issues:[],defaults:{duration_s:20,sample_count:201,command_time_s:.2,command_ramp_s:.5,rtol:1e-6,circuits:[circuit('ox'),circuit('fuel')]},parameters:[{key:'duration_s',label:'仿真时长',min:.05,max:300,step:.5},{key:'sample_count',label:'采样数',min:21,max:1001,step:1}],circuit_parameters:[{key:'initial_pressure_Pa',label:'初始压力',unit:'MPa',factor:1e-6,min:1e4,max:1e7,step:.01,group:'容腔与初态'},{key:'initial_temperature_K',label:'初始温度',unit:'K',min:55,max:600,step:1,group:'容腔与初态'},{key:'initial_quality',label:'初始蒸气质量分数',unit:'%',factor:100,min:0,max:1,step:1,group:'容腔与初态'},{key:'volume_m3',label:'容积',unit:'L',factor:1000,min:1e-5,max:.1,step:.01,group:'容腔与初态'},{key:'inlet_CdA_m2',label:'有效入口面积',unit:'mm²',factor:1e6,min:0,max:1e-3,step:.1,group:'流动边界'},{key:'wall_UA_W_K',label:'壁流换热导纳',min:0,max:1e4,step:1,group:'金属壁面'}],source_constraints:{checks:[{label:'原设计交叉供给压力不足',value:-4.367,unit:'MPa',status:'fail'}]},limitations:['默认换热参数未经独立试验标定。']});
const sample=t=>({time_s:t,circuits:['ox','fuel'].map((id,i)=>({id,label:i?'燃料预冷单元':'液氧预冷单元',fluid:i?'Methane':'Oxygen',phase:'twophase',temperature_K:100+t,wall_temperature_K:290-t*5,pressure_Pa:2e5+t*1e3,mass_kg:.1+t*.02,liquid_mass_kg:t*.02,vapor_mass_kg:.1,quality:.8-t*.02,void_fraction:.99-t*.002,liquid_volume_fraction:.01+t*.002,inlet_mass_flow_kg_s:.03+i*.01,outlet_mass_flow_kg_s:.01,heat_to_fluid_W:2000-t*100,heat_from_ambient_W:0,inlet_opening:1,outlet_opening:.8,inlet_command:1,outlet_command:.8,density_kg_m3:50,mass_balance_error_kg:t*1e-12,energy_balance_error_J:t*1e-5,fluid_internal_energy_J:t*1e4,enthalpy_J_kg:1e5,inlet_choked:false,outlet_choked:true}))});
const result=()=>({source:{job_id:'design-1'},samples:[sample(0),sample(5),sample(10)],request:config().defaults,audits:{status:'pass',experiment_validated:false,max_abs_mass_balance_error_kg:1e-11,max_abs_energy_balance_error_J:1e-4},model_description:{title:'纯工质均相平衡预冷模型',equations:['dm/dt = m_in - m_out']},complete:true,termination_reason:'completed',readiness:{status:'not_met',ignition_ready:false,checks:[{id:'ox',wall_temperature_met:false,liquid_volume_met:false}]},endpoint_snapshot:{time_s:10,circuits:[{id:'ox',mass_kg:.3,wall_temperature_K:240,pressure_Pa:210000}]},limitations:['未解析膜态沸腾与空间液位。']});
const record=()=>({id:'chill-1',project_id:'p1',status:'succeeded',revision:2,created:1,request:{source_job_id:'design-1',...config().defaults},result:result()});

test('display defaults and request round-trip pressure, volume, area and percent without mutating setup',()=>{
 const setup=config(),before=structuredClone(setup),display=chilldownDisplayDefaults(setup);
 assert.equal(display.circuits[0].volume_m3,.2);assert.equal(display.circuits[0].inlet_CdA_m2,.5);assert.equal(display.circuits[0].initial_quality,100);
 const req=buildChilldownRequest(source,setup,display);assert.deepEqual(req,{...before.defaults,source_job_id:'design-1'});assert.deepEqual(setup,before);
});
test('PT/PQ requests validate only the active state coordinate and retain valid inactive defaults',()=>{
 const setup=config(),display=chilldownDisplayDefaults(setup);display.circuits[0].initial_quality='';
 assert.equal(buildChilldownRequest(source,setup,display).circuits[0].initial_quality,1);
 display.circuits[0].initial_mode='PQ';display.circuits[0].initial_quality=30;display.circuits[0].initial_temperature_K='';
 const req=buildChilldownRequest(source,setup,display);assert.equal(req.circuits[0].initial_quality,.3);assert.equal(req.circuits[0].initial_temperature_K,290);
 display.circuits[0].initial_quality='';assert.throws(()=>buildChilldownRequest(source,setup,display),/有限数值/);
 display.circuits[0].initial_mode='PH';assert.throws(()=>buildChilldownRequest(source,setup,display),/初态定义/);
});
test('invalid numbers, bounds, sample count and source readiness cannot reach API',()=>{
 for(const bad of ['',null,NaN,Infinity,-1,200]){const display=chilldownDisplayDefaults(config());display.circuits[0].initial_pressure_Pa=bad;assert.throws(()=>buildChilldownRequest(source,config(),display));}
 const display=chilldownDisplayDefaults(config());display.sample_count=21.5;assert.throws(()=>buildChilldownRequest(source,config(),display),/整数/);
 assert.throws(()=>buildChilldownRequest({...source,status:'running'},config(),{}),/已完成/);assert.throws(()=>buildChilldownRequest(source,{...config(),ready:false},{}),/初始化/);
});
test('read-only identity and fluid cannot be changed through the displayed editor values',()=>{
 const display=chilldownDisplayDefaults(config());display.circuits[0].fluid='Nitrogen';display.circuits[0].label='fake';display.circuits[0].unknown=9;
 const req=buildChilldownRequest(source,config(),display);assert.equal(req.circuits[0].fluid,'Oxygen');assert.equal(req.circuits[0].label,'液氧预冷单元');assert.equal(req.circuits[0].unknown,undefined);
});
test('nine panels isolate selected fluid and distinguish mass and volume fractions',()=>{
 const panels=chilldownPanels(result(),'fuel');assert.equal(panels.length,9);assert.equal(panels[4].rows[0].inlet_mass_flow_kg_s,.04);assert.equal(panels[3].series[0].label,'质量含气率 x');assert.equal(panels[3].series[1].label,'气相体积分数 α');assert.equal(panels[7].unit,'kg');assert.equal(panels[8].unit,'J');
 const html=renderChilldownResult(record(),'fuel',1);assert.equal((html.match(/class="cd-chart"/g)||[]).length,9);assert.match(html,/采样时刻：5 s/);assert.match(html,/非空间液位/);assert.match(html,/不等于整机点火条件/);assert.doesNotMatch(html,/NaN|Infinity|undefined/);
});
test('missing state quantities remain missing and break curves instead of becoming zero',()=>{
 const rec=record();rec.result.samples[1].circuits[0].temperature_K=null;
 rec.result.samples[1].circuits[0].quality=null;rec.result.samples[1].circuits[0].liquid_volume_fraction=null;
 const html=renderChilldownResult(rec,'ox',1),path=html.match(/data-series="temperature_K"[^>]*d="([^"]*)"/)[1];
 assert.equal((path.match(/M/g)||[]).length,2);assert.doesNotMatch(path,/L/);assert.match(html,/液相占比待计算 \/ 未定义/);assert.match(html,/质量含气率 \/ %<\/span><b>—/);
});
test('supercritical quality is not drawn and empty results contain no fabricated plots',()=>{
 const rec=record();for(const row of rec.result.samples){row.circuits[0].quality=null;row.circuits[0].void_fraction=null;row.circuits[0].liquid_volume_fraction=null;row.circuits[0].phase='supercritical';}
 assert.equal(chilldownPanels(rec.result,'ox')[3].series.length,0);assert.match(renderChilldownResult(rec),/该量未定义或暂无有效数据/);assert.doesNotMatch(renderChilldownResult(null),/<svg/);
});
test('single-sample results have visible markers rather than invisible move-only paths',()=>{
 const rec=record();rec.result.samples=[sample(0)];assert.match(renderChilldownResult(rec),/<circle cx=/);
});
test('partial results explicitly retain termination and no endpoint export without snapshot',()=>{
 const rec=record();rec.status='cancelled';rec.result.complete=false;rec.result.termination_reason='超出两相物性适用域';delete rec.result.endpoint_snapshot;
 const html=renderChilldownResult(rec);assert.match(html,/有效积分时间段/);assert.match(html,/超出两相物性适用域/);assert.match(html,/data-chill-action="snapshot" disabled/);assert.throws(()=>buildChilldownSnapshot(rec),/尚未生成/);
});
test('snapshot labels scope and does not mutate frozen results or assert ignition readiness',()=>{
 const rec=record(),snapshot=buildChilldownSnapshot(rec);assert.equal(snapshot.study_id,rec.id);assert.equal(snapshot.ignition_ready,false);assert.equal(snapshot.complete,true);assert.match(snapshot.scope,/全系统一致性/);snapshot.endpoint_snapshot.circuits[0].mass_kg=999;assert.equal(rec.result.endpoint_snapshot.circuits[0].mass_kg,.3);
});
test('CSV includes both fluids in SI, preserves null blanks and guards spreadsheet formulas',()=>{
 const r=result();r.samples[0].circuits[0].quality=null;r.samples[0].circuits[0].label='=SUM(A1)';r.samples[1].circuits[0].heat_to_fluid_W=-100;
 const csv=buildChilldownCsv(r);assert.match(csv,/ox.pressure_Pa/);assert.match(csv,/fuel.mass_kg/);assert.match(csv,/"200000"/);assert.match(csv,/"-100"/);assert.match(csv,/'=SUM/);assert.doesNotMatch(csv,/null|NaN|Infinity/);
});
test('HTML and standalone SVG escape untrusted labels, equations and sources',()=>{
 const rec=record();rec.result.request.circuits[0].label='<img src=x>';rec.result.termination_reason='<script>x</script>';rec.result.source.job_id='<script>id</script>';rec.result.model_description.equations=['<script>eq</script>'];
 assert.doesNotMatch(renderChilldownResult(rec),/<img|<script>/);const svg=buildChilldownSvg(rec.result);assert.match(svg,/width="1220"/);assert.match(svg,/均相平衡研究/);assert.doesNotMatch(svg,/<script|<image|<foreignObject|NaN|Infinity/);
});
test('run blocking distinguishes source, readiness, connectivity and active job limit',()=>{
 assert.match(chilldownRunBlockReason(null,null,false),/请选择/);assert.match(chilldownRunBlockReason(source,null,false,0,'offline'),/检查连接/);assert.match(chilldownRunBlockReason(source,{ready:false},false),/输入尚不完整/);assert.match(chilldownRunBlockReason(source,config(),true),/正在提交/);assert.match(chilldownRunBlockReason(source,config(),false,2),/已有 2 个/);assert.equal(chilldownRunBlockReason(source,config(),false),'');
});

function container(){const listeners={};return {innerHTML:'',addEventListener(type,handler){listeners[type]=handler;},action(action,extra={}){listeners.click({target:{closest:()=>({dataset:{chillAction:action,...extra}})}});},field(key,value,id){listeners.input({target:{dataset:{chillField:key,...(id?{chillCircuit:id}:{})},value}});},change(dataset,value,attribute='',checked=false){listeners.change({target:{dataset,value,checked,hasAttribute:name=>name===attribute}});}};}
const context=()=>({project:{id:'p1',revision:2},record:source,dirty:true});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};

test('controller shows frozen source diagnostics without blocking an independent ready thermal unit',async()=>{
 const view=container(),calls=[];const ui=mountChilldown(view,{getContext:context,api:async(path,method,body)=>{calls.push({path,method,body});return path.includes('/setup?')?config():method==='POST'?{id:'new',status:'queued'}:[];}});
 await ui.refresh();assert.match(view.innerHTML,/原设计交叉供给压力不足/);assert.match(view.innerHTML,/-4.367 MPa/);assert.match(view.innerHTML,/左侧当前编辑参数不参与/);assert.doesNotMatch(view.innerHTML,/data-chill-action="run" disabled/);assert.match(view.innerHTML,/step="0.01"/);
 view.field('initial_pressure_Pa','0.3','ox');view.action('run');await tick();assert.equal(calls.find(call=>call.method==='POST').body.circuits[0].initial_pressure_Pa,3e5);ui.onLeave();
});
test('mode switching shows only the active temperature or quality field and keeps other fluid independent',async()=>{
 const view=container();const ui=mountChilldown(view,{getContext:context,api:async path=>path.includes('setup?')?config():[]});await ui.refresh();assert.match(view.innerHTML,/data-chill-field="initial_temperature_K"/);assert.doesNotMatch(view.innerHTML,/data-chill-field="initial_quality"/);
 view.change({chillMode:'ox'},'PQ');assert.doesNotMatch(view.innerHTML,/data-chill-field="initial_temperature_K"/);assert.match(view.innerHTML,/data-chill-field="initial_quality"/);assert.match(view.innerHTML,/温度由状态方程给出/);
 view.action('',{chillEditCircuit:'fuel'});assert.match(view.innerHTML,/data-chill-field="initial_temperature_K"/);ui.onLeave();
});
test('unready setup and connectivity failures explain why run is disabled',async()=>{
 const view=container();const ui=mountChilldown(view,{getContext:context,api:async path=>path.includes('setup?')?{...config(),ready:false,issues:['初态落在物性域外']}:[]});await ui.refresh();assert.match(view.innerHTML,/初态落在物性域外/);assert.match(view.innerHTML,/data-chill-action="run" disabled/);ui.onLeave();
 const view2=container(),ui2=mountChilldown(view2,{getContext:context,api:async()=>{throw Error('无法连接');}});await ui2.refresh();assert.match(view2.innerHTML,/检查连接/);ui2.onLeave();
});
test('changing project ignores late setup and records while keeping source isolation',async()=>{
 const pending=deferred(),view=container();let ctx=context();const ui=mountChilldown(view,{getContext:()=>ctx,api:()=>pending.promise});const task=ui.refresh();ctx={project:{id:'p2'},record:null};ui.sync();pending.resolve([]);await task;assert.doesNotMatch(view.innerHTML,/design-1|原设计交叉供给/);ui.onLeave();
});
test('leaving ignores late submissions and stops future notification and rendering',async()=>{
 const pending=deferred(),view=container(),notifications=[];const ui=mountChilldown(view,{getContext:context,notify:message=>notifications.push(message),api:async(path,method)=>method==='POST'?pending.promise:path.includes('setup?')?config():[]});await ui.refresh();view.action('run');ui.onLeave();const before=view.innerHTML;pending.resolve({id:'late',status:'queued'});await tick();assert.equal(view.innerHTML,before);assert.deepEqual(notifications,[]);
});
test('saved results retain their own labels and source independent of editable setup',async()=>{
 const view=container(),rec=record();rec.result.request.circuits[0].label='历史容腔记录';rec.request.source_job_id='older-source';const ui=mountChilldown(view,{getContext:context,api:async path=>path.includes('setup?')?config():path.startsWith('/projects/')?[rec]:rec});await ui.refresh();assert.match(view.innerHTML,/历史容腔记录/);assert.match(view.innerHTML,/正在显示历史研究记录/);view.change({},'1','data-chill-sample');assert.match(view.innerHTML,/采样时刻：5 s/);ui.onLeave();
});

test('local validation error permits corrected input submission without refreshing setup',async()=>{
 const view=container(),posts=[];const ui=mountChilldown(view,{getContext:context,api:async(path,method,body)=>{if(method==='POST'){posts.push(body);return {id:'corrected',status:'queued'};}return path.includes('setup?')?config():[];}});
 await ui.refresh();view.field('initial_pressure_Pa','','ox');view.action('run');await tick();assert.equal(posts.length,0);assert.match(view.innerHTML,/初始压力 必须是有限数值/);assert.doesNotMatch(view.innerHTML,/data-chill-action="run" disabled/);
 view.field('initial_pressure_Pa','0.25','ox');view.action('run');await tick();assert.equal(posts.length,1);assert.equal(posts[0].circuits[0].initial_pressure_Pa,250000);ui.onLeave();
});

test('server validation rejection permits edited retry without a refresh',async()=>{
 const view=container(),posts=[];const ui=mountChilldown(view,{getContext:context,api:async(path,method,body)=>{if(method==='POST'){posts.push(body);if(posts.length===1)throw Error('422：初态超出允许范围');return {id:'corrected',status:'queued'};}return path.includes('setup?')?config():[];}});
 await ui.refresh();view.action('run');await tick();assert.match(view.innerHTML,/422：初态超出允许范围/);assert.doesNotMatch(view.innerHTML,/data-chill-action="run" disabled/);
 view.field('initial_temperature_K','280','ox');view.action('run');await tick();assert.equal(posts.length,2);assert.equal(posts[1].circuits[0].initial_temperature_K,280);ui.onLeave();
});

test('run selection submits only included units and excludes invalid unused fields',async()=>{
 const view=container(),posts=[];const ui=mountChilldown(view,{getContext:context,api:async(path,method,body)=>{if(method==='POST'){posts.push(body);return {id:'single',status:'queued'};}return path.includes('setup?')?config():[];}});
 await ui.refresh();assert.match(view.innerHTML,/运行工质/);assert.match(view.innerHTML,/下方标签仅切换参数编辑/);assert.match(view.innerHTML,/data-chill-include="fuel" checked/);
 view.change({chillInclude:'fuel'},'', '',false);view.field('initial_pressure_Pa','','fuel');view.action('run');await tick();assert.equal(posts.length,1);assert.deepEqual(posts[0].circuits.map(c=>c.id),['ox']);assert.equal(posts[0].selected_circuit_ids,undefined);ui.onLeave();
});

test('empty run selection disables submit until a unit is included again',async()=>{
 const view=container();const ui=mountChilldown(view,{getContext:context,api:async path=>path.includes('setup?')?config():[]});await ui.refresh();view.change({chillInclude:'ox'},'','',false);view.change({chillInclude:'fuel'},'','',false);assert.match(view.innerHTML,/请至少勾选一个工质/);assert.match(view.innerHTML,/data-chill-action="run" disabled/);
 view.change({chillInclude:'fuel'},'','',true);assert.doesNotMatch(view.innerHTML,/data-chill-action="run" disabled/);assert.match(view.innerHTML,/data-chill-include="fuel" checked/);ui.onLeave();
});
