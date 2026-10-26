import assert from 'node:assert/strict';
import test from 'node:test';
import {buildTransientRequest,transientPanels,renderTransientResult,buildTransientCsv,buildTransientSvg,mountTransient,renderTransientReadiness,transientRunBlockReason} from '../webapp/static/transient.js';
const source={id:'design-1',project_id:'p1',status:'succeeded',revision:1,created:1};
const setup={ready:true,defaults:{duration_s:1,sample_count:201,fuel_area_ratio:.95,ox_area_ratio:.95},parameters:[{key:'fuel_area_ratio',label:'燃料阀面积',min:.9,max:1.1}]};
const row=t=>({time_s:t,pc_Pa:8e6-t*1e5,thrust_N:300000-t*2000,shaft_ox_rpm:20000,shaft_fuel_rpm:20000,mdot_ox_kg_s:80,mdot_fuel_kg_s:20,chamber_temperature_K:3300,gg_pressure_Pa:9e6,gg_temperature_K:850,fuel_area_ratio:t?.95:1,ox_area_ratio:t?.95:1});
const result=()=>({source:{job_id:'design-1'},samples:[row(0),row(.5),row(1)],audits:{status:'pass',mass_relative_error:1e-10},metrics:{last_time_s:1,interpretation:'末点值不等于已达到新稳态'},model_description:{title:'热态模型',equations:['dm/dt = in - out'],experiment_validated:false},limitations:['不适用于冷态启动'],complete:true,termination_reason:'completed'});
const record=()=>({id:'tr-1',project_id:'p1',revision:1,created:1,status:'succeeded',request:{source_job_id:'design-1'},result:result()});
test('requests use immutable source and explicit model settings; unreadiness cannot submit',()=>{
 const before=structuredClone(setup);assert.equal(buildTransientRequest(source,setup,{fuel_area_ratio:'1.02'}).fuel_area_ratio,1.02);assert.deepEqual(setup,before);
 for(const value of ['',NaN,Infinity,.8,1.2])assert.throws(()=>buildTransientRequest(source,setup,{fuel_area_ratio:value}));
 assert.throws(()=>buildTransientRequest(source,{...setup,ready:false},{}),/准备度/);assert.throws(()=>buildTransientRequest({...source,status:'running'},setup,{}),/已完成/);
});
test('charts use physical time, six panels and only existing gas branches',()=>{
 const r=result();const panels=transientPanels(r);assert.equal(panels.length,6);assert.equal(panels[0].series.length,2);
 const html=renderTransientResult(record(),1);assert.equal((html.match(/class="tr-chart"/g)||[]).length,6);assert.match(html,/采样时刻：0.5 s/);assert.match(html,/时间 \/ s/);assert.doesNotMatch(html,/NaN|Infinity|富氧预燃室/);
});
test('missing values break chart curves instead of inventing interpolated states',()=>{
 const rec=record();rec.result.samples[1].pc_Pa=null;const html=renderTransientResult(rec);const path=html.match(/data-series="pc_Pa"[^>]*d="([^"]+)"/)[1];assert.equal((path.match(/M/g)||[]).length,2);assert.doesNotMatch(path,/L/);
});
test('partial and out-of-domain results preserve curves, termination and honest validation status',()=>{
 const rec=record();rec.status='cancelled';rec.result.complete=false;rec.result.termination_reason='局部模型范围终止';const html=renderTransientResult(rec);
 assert.match(html,/局部模型范围终止/);assert.match(html,/尚未进行；当前为研究模型/);assert.match(html,/末点值不等于已达到新稳态/);assert.match(html,/最大|相对质量闭合误差/);
});
test('diagnostic and source strings are escaped in html and standalone export',()=>{
 const rec=record();rec.result.termination_reason='<script>x</script>';rec.result.source.job_id='<img src=x>';rec.result.limitations=['<b>danger</b>'];
 assert.doesNotMatch(renderTransientResult(rec),/<script>|<b>danger/);assert.doesNotMatch(buildTransientSvg(rec.result),/<img|<script/);
});
test('CSV includes actual samples, blank missing values; SVG has self-contained scientific plots',()=>{
 const r=result();r.samples[1].pc_Pa=null;const csv=buildTransientCsv(r);assert.match(csv,/time_s/);assert.match(csv,/"0.5",""/);assert.doesNotMatch(csv,/null|NaN/);
 const svg=buildTransientSvg(r);assert.match(svg,/width="1220" height="1150"/);assert.match(svg,/未经试验标定/);assert.doesNotMatch(svg,/<image|<foreignObject|NaN|Infinity/);
});
function container(){const listeners={};return {innerHTML:'',addEventListener(t,h){listeners[t]=h;},action(a){listeners.click({target:{closest:()=>({dataset:{transientAction:a}})}});},field(k,v){listeners.input({target:{dataset:{transientField:k},value:v}});}};}
const tick=()=>new Promise(r=>setImmediate(r));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const context=()=>({project:{id:'p1',revision:1},record:source,dirty:true});
test('controller loads readiness and submits source settings without current draft inputs',async()=>{
 const view=container(),calls=[];const ui=mountTransient(view,{getContext:context,api:async(path,method,body)=>{calls.push({path,method,body});if(path.includes('/setup?'))return setup;if(method==='POST')return {id:'new',status:'queued'};return [];}});
 await ui.refresh();view.field('fuel_area_ratio','1.01');view.action('run');await tick();const sent=calls.find(c=>c.method==='POST');assert.equal(sent.body.source_job_id,source.id);assert.equal(sent.body.fuel_area_ratio,1.01);assert.equal(sent.body.inputs,undefined);assert.match(view.innerHTML,/冻结输入/);ui.onLeave();
});
test('late requests cannot overwrite a different selected project',async()=>{
 const view=container(),p=deferred();let ctx=context();const ui=mountTransient(view,{getContext:()=>ctx,api:()=>p.promise});const refresh=ui.refresh();ctx={project:{id:'p2',revision:1},record:null};ui.sync();p.resolve([]);await refresh;assert.doesNotMatch(view.innerHTML,/design-1/);ui.onLeave();
});
test('leaving cancels polling and prevents late submission updates or notices',async()=>{
 const view=container(),p=deferred(),notices=[];const ui=mountTransient(view,{getContext:context,notify:x=>notices.push(x),api:async(path,method)=>method==='POST'?p.promise:path.includes('setup?')?setup:[]});await ui.refresh();view.action('run');ui.onLeave();const before=view.innerHTML;p.resolve({id:'late',status:'queued'});await tick();assert.equal(view.innerHTML,before);assert.deepEqual(notices,[]);
});
test('readiness failures and network errors are visible without fake results',async()=>{
 const view=container();const ui=mountTransient(view,{getContext:context,api:async path=>path.includes('setup?')?{...setup,ready:false,issues:['供给欠压']}:[]});await ui.refresh();assert.match(view.innerHTML,/供给欠压/);assert.match(view.innerHTML,/data-transient-action="run" disabled/);assert.doesNotMatch(view.innerHTML,/<svg/);ui.onLeave();
 const fail=container();const ui2=mountTransient(fail,{getContext:context,api:async()=>{throw Error('连接失败');}});await ui2.refresh();assert.match(fail.innerHTML,/连接失败/);ui2.onLeave();
});

test('relative mode normalizes each series to its own initial value and keeps physical inspection and CSV',()=>{
 const rec=record(),panels=transientPanels(rec.result,true);assert.equal(panels[0].unit,'相对初值变化 / %');assert.equal(panels[0].series[0].baseline,8e6);
 const html=renderTransientResult(rec,2,true);assert.match(html,/自身 t=0 值归一化/);assert.match(html,/室压 \/ MPa/);assert.match(html,/7.9/);assert.doesNotMatch(html,/NaN|Infinity/);
 assert.match(buildTransientSvg(rec.result,true),/相对初值变化/);assert.match(buildTransientCsv(rec.result),/7900000/);
});

test('blocked initialization explains saved constraint values without inventing missing pressures',()=>{
 const s={...source,result:{assessment:{checks:[{key:'crossflow_fuel_to_oxpb_margin_MPa',label:'CH₄ → 富氧预燃室供给压力裕度',value:-4.367,limit:0,unit:'MPa',criterion:'minimum',status:'fail',detail:'取流点扣除损失'},{key:'unknown',label:'未返回项',value:null,limit:1,unit:'K',criterion:'maximum',status:'unknown'},{key:'passed',label:'已通过项',value:0,status:'pass'}]}}};
 const before=structuredClone(s),html=renderTransientReadiness({ready:false,issues:['供给欠压 <script>']},s);
 assert.match(html,/-4.367 MPa/);assert.match(html,/≥ 0 MPa/);assert.match(html,/— K/);assert.match(html,/改变本页的仿真时长或扰动幅度不会修复/);assert.match(html,/冻结结果/);assert.doesNotMatch(html,/已通过项|<script>|NaN|undefined/);assert.deepEqual(s,before);
 assert.doesNotMatch(renderTransientReadiness(setup,s),/供给压力裕度|不可初始化/);
});

test('disabled run always has a specific reason for prerequisite, connection, submission or queue state',()=>{
 assert.match(transientRunBlockReason(null,null,false),/尚未选择/);
 assert.match(transientRunBlockReason(source,null,false,0,'连接失败'),/恢复连接/);
 assert.match(transientRunBlockReason(source,null,false),/正在检查/);
 assert.match(transientRunBlockReason(source,{ready:false},false),/运行已禁用/);
 assert.match(transientRunBlockReason(source,setup,true),/正在提交/);
 assert.match(transientRunBlockReason(source,setup,false,2),/已有 2 个/);
 assert.equal(transientRunBlockReason(source,setup,false,0),'');
});

test('source inspection is read-only navigation and unreadiness still cannot post a job',async()=>{
 const view=container(),calls=[];let inspected=0;
 const ui=mountTransient(view,{getContext:context,onInspectSource:()=>inspected++,api:async(path,method)=>{calls.push(method);return path.includes('setup?')?{...setup,ready:false,issues:['欠压']}:[];}});
 await ui.refresh();assert.match(view.innerHTML,/不可初始化/);assert.match(view.innerHTML,/aria-describedby="transient-run-status"/);view.action('inspect-source');assert.equal(inspected,1);view.action('run');await tick();assert.equal(calls.includes('POST'),false);ui.onLeave();
});

test('switching to a new valid source clears the old readiness block without changing its stored record',async()=>{
 const view=container();let ctx=context();const ui=mountTransient(view,{getContext:()=>ctx,api:async(path)=>path.includes('setup?')?path.includes('design-2')?setup:{...setup,ready:false,issues:['旧记录供给欠压']}:[]});
 await ui.refresh();assert.match(view.innerHTML,/旧记录供给欠压/);ctx={...ctx,record:{...source,id:'design-2'}};ui.sync();await ui.refresh();assert.doesNotMatch(view.innerHTML,/旧记录供给欠压|data-transient-action="run" disabled/);assert.match(view.innerHTML,/design-2/);ui.onLeave();
});
