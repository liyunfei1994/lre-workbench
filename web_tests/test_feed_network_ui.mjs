import assert from 'node:assert/strict';
import test from 'node:test';
import {parseSuterCsv,buildFeedRequest,feedPanels,renderFeedResult,buildFeedCsv,buildFeedSvg,feedRunBlockReason,mountFeedNetwork} from '../webapp/static/feed-network.js';

const source={id:'design-1',project_id:'p1',status:'succeeded',revision:1,created:1};
const circuit=id=>({id,label:id==='ox'?'液氧供给':'燃料供给',tank_pressure_Pa:1e6,outlet_pressure_Pa:3e6,initial_manifold_pressure_Pa:8e6,volume_m3:.01,pump_head_Pa:8e6,pump_flow_m3_s:.1,pump_speed_rpm:20000,pump_efficiency:.8,rho_kg_m3:1000,bulk_modulus_Pa:1e9,pressure_floor_Pa:10000,initial_valve_opening:1,final_valve_opening:.8,pump_map:{kind:'analytic_demo',slope:.1,source:'未标定演示图',points:[],max_speed_ratio:1.25,max_flow_ratio:2},property_evidence:{backend:'HEOS'}});
const setup=()=>({ready:true,issues:[],defaults:{duration_s:2,sample_count:301,command_time_s:.2,command_ramp_s:.5,rtol:1e-6,circuits:[circuit('ox'),circuit('fuel')]},parameters:[{key:'duration_s',label:'仿真时长',min:.05,max:20,step:.05},{key:'sample_count',label:'采样数',min:21,max:2001,step:1}],circuit_parameters:[{key:'tank_pressure_Pa',label:'供给压力',unit:'MPa',factor:1e-6,min:1e3,max:2e8,step:.01},{key:'volume_m3',label:'容腔体积',factor:1000,unit:'L',min:1e-6,max:10,step:.1},{key:'final_valve_opening',label:'末端开度',factor:100,unit:'%',min:0,max:1,step:1}],source_constraints:{checks:[{key:'crossflow',label:'源机型欠压',value:-4.367,unit:'MPa',status:'fail'}]},limitations:['独立单相研究模型']});
const values=()=>({duration_s:2,sample_count:301,circuits:['ox','fuel'].map(id=>({...circuit(id),tank_pressure_Pa:1,volume_m3:10,final_valve_opening:80}))});
const csv='theta_deg,head,torque\n0,1,1\n45,0.5,0.5\n90,-0.1,0\n180,-1,-1\n360,1,1';
const row=t=>({time_s:t,mass_balance_error_kg:t*1e-12,hydraulic_energy_error_J:t*1e-6,circuits:['ox','fuel'].map((id,i)=>({id,pump_flow_m3_s:.1-t*.15,outlet_flow_m3_s:.1,pump_mass_flow_kg_s:100-t*150+i,outlet_mass_flow_kg_s:100,manifold_pressure_Pa:8e6-t*1e6,inventory_kg:10-t,mass_balance_error_kg:(i?1:-1)*t*1e-12,hydraulic_energy_error_J:(i?1:-1)*t*1e-6,speed_rpm:20000-t*20000,pump_head_Pa:7e6,pump_torque_Nm:400-t*600,pump_shaft_power_W:1e6-t*2e6,pump_hydraulic_power_W:.8e6-t*2e6,pump_loss_W:.2e6,valve_command:t?.4:1,valve_opening:t?.6:1,effective_area_ratio:t?.61:1}))});
const result=()=>({source:{job_id:'design-1'},samples:[row(0),row(.5),row(1)],request:setup().defaults,audits:{status:'pass',mass_relative_error:1e-12},model_description:{title:'供给网络',equations:['L dQ/dt = Δp − R Q |Q|']},limitations:['不模拟点火'],complete:true,termination_reason:'completed'});
const record=()=>({id:'feed-1',project_id:'p1',revision:1,created:1,status:'succeeded',request:{source_job_id:'design-1',...setup().defaults},result:result()});

test('Suter parser requires explicit header, sorted finite points and periodic endpoints',()=>{
 assert.equal(parseSuterCsv('\uFEFF'+csv).length,5);
 for(const invalid of ['',csv.replace('theta_deg','angle'),csv.replace('45,0.5,0.5','0,0.5,0.5'),csv.replace('45,0.5,0.5','45,NaN,0.5'),csv.replace('360,1,1','360,2,1'),csv.replace('360,1,1','359,1,1'),csv.replace('0,1,1','-1,1,1')])assert.throws(()=>parseSuterCsv(invalid));
});
test('Suter points remain user data; no curves are generated from missing or malformed rows',()=>{
 assert.deepEqual(parseSuterCsv(csv)[1],{theta_deg:45,head:.5,torque:.5});
 assert.throws(()=>parseSuterCsv('theta_deg,head,torque\n0,1,1\n360,1,1'));
 assert.doesNotThrow(()=>parseSuterCsv(csv+'\n'));
});
test('request converts displayed MPa, litres and percent back to SI without altering frozen source',()=>{
 const config=setup(),before=structuredClone(config),request=buildFeedRequest(source,config,values());
 assert.equal(request.circuits[0].tank_pressure_Pa,1e6);assert.equal(request.circuits[0].volume_m3,.01);assert.equal(request.circuits[0].final_valve_opening,.8);assert.equal(request.source_job_id,'design-1');assert.deepEqual(config,before);assert.equal(request.inputs,undefined);
});
test('request validation handles empty and out-of-domain fields and preserves read-only pump scales',()=>{
 for(const bad of ['',NaN,Infinity,-1,201]){const v=values();v.circuits[0].tank_pressure_Pa=bad;assert.throws(()=>buildFeedRequest(source,setup(),v));}
 const v=values();v.circuits[0].rho_kg_m3=1;v.circuits[0].pump_head_Pa=9;const req=buildFeedRequest(source,setup(),v);assert.equal(req.circuits[0].rho_kg_m3,1000);assert.equal(req.circuits[0].pump_head_Pa,8e6);
 assert.throws(()=>buildFeedRequest(source,{...setup(),ready:false},v),/初始化/);assert.throws(()=>buildFeedRequest({...source,status:'running'},setup(),v),/已完成/);
});
test('imported map requires a source, periodic table and explicit coefficients',()=>{
 const v=values();v.circuits[0].pump_map={...v.circuits[0].pump_map,kind:'suter_table',source:'paper table',points:parseSuterCsv(csv)};
 assert.deepEqual(buildFeedRequest(source,setup(),v).circuits[0].pump_map.points,parseSuterCsv(csv));v.circuits[0].pump_map.source='';assert.throws(()=>buildFeedRequest(source,setup(),v),/来源/);
});
test('nine time panels isolate selected circuit, preserve reverse flow and separate audit units',()=>{
 const p=feedPanels(result(),'fuel');assert.equal(p.length,9);assert.equal(p[1].rows.at(-1).pump_mass_flow_kg_s,-49);assert.equal(p[7].unit,'kg');assert.equal(p[8].unit,'J');assert.equal(p[4].name,'外部规定转速');
 const html=renderFeedResult(record(),'fuel',2);assert.equal((html.match(/class="fn-chart"/g)||[]).length,9);assert.match(html,/-49/);assert.match(html,/采样时刻：1 s/);assert.match(html,/规定转速历程/);assert.doesNotMatch(html,/NaN|Infinity|undefined/);
});
test('missing samples break paths; no synthetic interpolation through absent flow data',()=>{
 const rec=record();rec.result.samples[1].circuits[0].pump_mass_flow_kg_s=null;
 const path=renderFeedResult(rec).match(/data-series="pump_mass_flow_kg_s"[^>]*d="([^"]*)"/)[1];assert.equal((path.match(/M/g)||[]).length,2);assert.doesNotMatch(path,/L/);
});
test('partial results and missing results explain physical scope without faking curves',()=>{
 const rec=record();rec.status='cancelled';rec.result.complete=false;rec.result.termination_reason='压力低于单相下限';const html=renderFeedResult(rec);
 assert.match(html,/有效时间段/);assert.match(html,/压力低于单相下限/);assert.match(html,/不模拟点火/);assert.doesNotMatch(renderFeedResult(null),/<svg/);
});
test('HTML and standalone SVG escape untrusted sources and diagnostics',()=>{
 const rec=record();rec.result.source.job_id='<img src=x>';rec.result.termination_reason='<script>x</script>';rec.result.request.circuits[0].pump_map.source='<script>map</script>';rec.result.model_description.equations=['<b>eq</b>'];
 assert.doesNotMatch(renderFeedResult(rec),/<script>|<b>eq/);assert.doesNotMatch(buildFeedSvg(rec.result),/<img|<script/);
});
test('CSV exports both circuits in SI with numeric negatives and guards string formula injection',()=>{
 const r=result();r.samples[1].circuits[0].pump_flow_m3_s=null;r.samples[0].note='=SUM(A1)';const csvOut=buildFeedCsv(r);
 assert.match(csvOut,/ox.pump_mass_flow_kg_s/);assert.match(csvOut,/fuel.pump_mass_flow_kg_s/);assert.match(csvOut,/"-50"/);assert.match(csvOut,/'=SUM/);assert.doesNotMatch(csvOut,/null|NaN/);
 const svg=buildFeedSvg(r,'ox');assert.match(svg,/width="1220"/);assert.match(svg,/供给网络动态/);assert.doesNotMatch(svg,/<image|<foreignObject|NaN|Infinity/);
});
test('run reasons distinguish source, connectivity, readiness and queue state',()=>{
 assert.match(feedRunBlockReason(null,null,false),/请选择/);assert.match(feedRunBlockReason(source,null,false,0,'offline'),/检查连接/);assert.match(feedRunBlockReason(source,{ready:false},false),/输入尚不完整/);assert.match(feedRunBlockReason(source,setup(),true),/正在提交/);assert.match(feedRunBlockReason(source,setup(),false,2),/已有 2 个/);assert.equal(feedRunBlockReason(source,setup(),false),'');
});

function container(){const listeners={};return {innerHTML:'',addEventListener(t,h){listeners[t]=h;},action(a,extra={}){listeners.click({target:{closest:()=>({dataset:{feedAction:a,...extra}})}});},field(k,v,circuit){listeners.input({target:{dataset:{feedField:k,...(circuit?{feedCircuit:circuit}:{})},value:v}});},change(d,value){return listeners.change({target:{dataset:d,value,hasAttribute:()=>false}});},csv(id,text){listeners.input({target:{dataset:{feedMapCsv:id},value:text}});}};}
const tick=()=>new Promise(r=>setImmediate(r));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const context=()=>({project:{id:'p1',revision:1},record:source,dirty:true});
test('controller shows source constraint failure as diagnostic yet enables independent ready bench',async()=>{
 const view=container(),calls=[];const ui=mountFeedNetwork(view,{getContext:context,api:async(path,method,body)=>{calls.push({path,method,body});return path.includes('/setup?')?setup():method==='POST'?{id:'new',status:'queued'}:[];}});
 await ui.refresh();assert.match(view.innerHTML,/源机型欠压/);assert.match(view.innerHTML,/-4.367 MPa/);assert.doesNotMatch(view.innerHTML,/data-feed-action="run" disabled/);assert.match(view.innerHTML,/step="0.01"/);view.field('tank_pressure_Pa','1.2','ox');view.action('run');await tick();assert.equal(calls.find(v=>v.method==='POST').body.circuits[0].tank_pressure_Pa,1.2e6);assert.match(view.innerHTML,/冻结输入/);ui.onLeave();
});
test('unready setup or failed request disables run and retains clear reason',async()=>{
 const view=container();const ui=mountFeedNetwork(view,{getContext:context,api:async p=>p.includes('setup?')?{...setup(),ready:false,issues:['不是单相液体']}:[]});await ui.refresh();assert.match(view.innerHTML,/不是单相液体/);assert.match(view.innerHTML,/data-feed-action="run" disabled/);ui.onLeave();
 const v=container(),ui2=mountFeedNetwork(v,{getContext:context,api:async()=>{throw Error('无法连接');}});await ui2.refresh();assert.match(v.innerHTML,/无法连接/);assert.match(v.innerHTML,/检查连接/);ui2.onLeave();
});
test('new project and source reject stale setup, record and submission results',async()=>{
 const view=container(),pending=deferred();let ctx=context();const ui=mountFeedNetwork(view,{getContext:()=>ctx,api:()=>pending.promise});const refresh=ui.refresh();ctx={project:{id:'p2',revision:2},record:null};ui.sync();pending.resolve([]);await refresh;assert.doesNotMatch(view.innerHTML,/design-1|源机型欠压/);ui.onLeave();
});
test('leaving cancels polling and ignores late post result and notification',async()=>{
 const view=container(),pending=deferred(),notices=[];const ui=mountFeedNetwork(view,{getContext:context,notify:v=>notices.push(v),api:async(path,method)=>method==='POST'?pending.promise:path.includes('setup?')?setup():[]});await ui.refresh();view.action('run');ui.onLeave();const before=view.innerHTML;pending.resolve({id:'late',status:'queued'});await tick();assert.equal(view.innerHTML,before);assert.deepEqual(notices,[]);
});
test('Suter input is validated on run, preview reflects confirmed data and demo switch clears table',async()=>{
 const view=container(),calls=[];const ui=mountFeedNetwork(view,{getContext:context,api:async(path,method,body)=>{calls.push({method,body});return path.includes('setup?')?setup():method==='POST'?{id:'new',status:'queued'}:[];}});await ui.refresh();await view.change({feedMapKind:'ox'},'suter_table');view.csv('ox','broken');view.action('run');await tick();assert.equal(calls.some(c=>c.method==='POST'),false);assert.match(view.innerHTML,/CSV 首行/);view.csv('ox',csv);view.action('validate-map',{feedCircuit:'ox'});assert.match(view.innerHTML,/已确认的周期泵图/);assert.match(view.innerHTML,/data-feed-group="泵图" open/);await view.change({feedMapKind:'ox'},'analytic_demo');view.action('run');await tick();assert.deepEqual(calls.find(c=>c.method==='POST').body.circuits[0].pump_map.points,[]);ui.onLeave();
});
test('saved result uses its own map source and branch labels, independent of new editor setup',async()=>{
 const view=container(),rec=record();rec.result.request.circuits[0].pump_map.source='historical map';const ui=mountFeedNetwork(view,{getContext:context,api:async p=>p.includes('setup?')?setup():p.startsWith('/projects/')?[rec]:rec});await ui.refresh();assert.match(view.innerHTML,/historical map/);assert.match(view.innerHTML,/液氧供给/);ui.onLeave();
});
