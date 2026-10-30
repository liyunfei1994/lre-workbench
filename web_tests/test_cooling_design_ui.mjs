import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {coolingDesignSource,createCoolingDesignForm,coolingDesignCandidateCount,buildCoolingDesignRequest,
  renderCoolingCandidateScatter,renderCoolingDesignResult,mountCoolingDesign} from "../webapp/static/cooling-design.js";

const clone=value=>structuredClone(value);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const context=()=>({project:{id:"p1",revision:3},draft:{cooling_n_channels:999},dirty:true,
  record:{id:"j1",project_id:"p1",revision:2,status:"succeeded",kind:"design",result:{calculation:{}}}});
const setup=()=>({source_job_id:"j1",source_revision:2,channel_counts:[240,300],widths_mm:[1.2,1.5],heights_mm:[6],wall_thicknesses_mm:[.5],
  maximum_candidates:64,constraints:{wall_temp_limit_K:800,wall_limit_status:"unset",wall_limit_basis:"",max_pressure_drop_MPa:null,min_rib_mm:null,max_aspect_ratio:null,max_velocity_ms:null,basis:""},
  boundary:{T_in_K:120,p_in_MPa:80,mdot_coolant_kg_s:160,source_wall_max_K:1179,source_dp_MPa:14}});
const candidate=(id=1)=>({id,status:"computed",geometry:{count:240,width_mm:1.5,height_mm:6,wall_mm:.5},pareto:true,
  metrics:{wall_max_K:900,dp_MPa:12,Q_MW:110,T_out_K:360,p_out_MPa:68,velocity_inlet_ms:19,velocity_throat_ms:130,velocity_max_ms:180,min_rib_mm:.8,throat_pitch_mm:2.3,throat_rib_mm:.8,max_aspect_ratio:4,energy_residual_W:.01},
  assessment:{status:"incomplete",material_status:"within_range",material_out_of_range_cells:0,manufacturing_match:"unknown",checks:[
    {id:"wall_temperature",actual:900,limit:null,status:"unset"},{id:"pressure_drop",actual:12,limit:15,status:"pass"}]},
  profile:{nodes:[{kind:"inlet_port",s_m:0,velocity_ms:99999,p_Pa:80e6},{kind:"channel",s_m:.1,velocity_ms:19,p_Pa:79e6},{kind:"channel",s_m:1,velocity_ms:130,p_Pa:70e6},{kind:"outlet_port",s_m:1.1,velocity_ms:88888,p_Pa:68e6}],
    cells:[{kind:"channel",s_m:.3,T_wall_hot_K:800},{kind:"channel",s_m:.8,T_wall_hot_K:900}]}});
const result=()=>({source_job_id:"j1",total:2,scope_note:"沿用保存边界；候选吸热尚未反馈整机。",candidates:[candidate(),{id:2,status:"failed",geometry:{count:300,width_mm:3,height_mm:6,wall_mm:.5},error:"通道宽度超过节距",pareto:false}]});

test("form and request bind saved design boundaries while preserving the draft and unset constraints",()=>{
  const ctx=context(),s=setup(),before={ctx:clone(ctx),s:clone(s)},form=createCoolingDesignForm(s),request=buildCoolingDesignRequest(ctx,form);
  assert.equal(request.source_job_id,"j1");assert.deepEqual(request.channel_counts,[240,300]);assert.deepEqual(request.widths_mm,[1.2,1.5]);
  assert.equal(request.constraints.wall_temp_limit_K,null);assert.equal(request.constraints.wall_limit_status,"unset");
  assert.equal(request.constraints.min_rib_mm,null);assert.equal(request.constraints.max_velocity_ms,null);
  assert.deepEqual(ctx,before.ctx);assert.deepEqual(s,before.s);assert.equal(Object.hasOwn(request,"boundary"),false);
  assert.equal(Object.hasOwn(request,"inputs"),false);assert.equal(Object.hasOwn(request,"source_revision"),false);
});

test("only completed local design records can supply a screening request",()=>{
  for(const mutate of [c=>c.record=null,c=>c.record.status="running",c=>c.record.kind="validation",c=>c.record.project_id="other",c=>c.record.result=null]){
    const ctx=context();mutate(ctx);assert.equal(coolingDesignSource(ctx),null);
    assert.throws(()=>buildCoolingDesignRequest(ctx,createCoolingDesignForm(setup())),/已完成/);
  }
});

test("candidate lists accept localized separators and remove duplicates without recomputing geometry",()=>{
  const form={...createCoolingDesignForm(setup()),channel_counts:"300，240; 300",widths_mm:"1.5 1.2",heights_mm:"6；8"};
  assert.equal(coolingDesignCandidateCount(form).count,8);
  const request=buildCoolingDesignRequest(context(),form);assert.deepEqual(request.channel_counts,[240,300]);assert.deepEqual(request.heights_mm,[6,8]);
});

test("nonphysical, noninteger and oversized candidate grids fail before submission",()=>{
  const base=createCoolingDesignForm(setup());
  for(const patch of [{channel_counts:"240.5"},{channel_counts:"0"},{widths_mm:"NaN"},{widths_mm:"Infinity"},{heights_mm:""},{heights_mm:"0"},{wall_thicknesses_mm:"4"}])
    assert.throws(()=>buildCoolingDesignRequest(context(),{...base,...patch}));
  const tooMany={...base,channel_counts:"200,220,240,260,280",widths_mm:"1,1.2,1.4,1.6,1.8",heights_mm:"4,6,8"};
  assert.equal(coolingDesignCandidateCount(tooMany).count,75);assert.throws(()=>buildCoolingDesignRequest(context(),tooMany),/75.*64/);
  assert.throws(()=>buildCoolingDesignRequest(context(),{...base,channel_counts:Array.from({length:17},(_,i)=>100+i).join(",")}),/1–16/);
});

test("wall-temperature permission requires explicit status and documented limits require evidence",()=>{
  const base=createCoolingDesignForm(setup());
  assert.throws(()=>buildCoolingDesignRequest(context(),{...base,wall_limit_status:"documented"}),/依据/);
  assert.throws(()=>buildCoolingDesignRequest(context(),{...base,wall_limit_status:"provisional",wall_temp_limit_K:""}),/限值/);
  assert.throws(()=>buildCoolingDesignRequest(context(),{...base,wall_limit_status:"guessed"}),/状态/);
  assert.throws(()=>buildCoolingDesignRequest(context(),{...base,max_pressure_drop_MPa:"-1"}),/压降/);
  const request=buildCoolingDesignRequest(context(),{...base,wall_limit_status:"documented",wall_limit_basis:"  试验报告 A，限定热处理状态  ",max_pressure_drop_MPa:"15",min_rib_mm:".8",max_aspect_ratio:"8",basis:"制造能力约束"});
  assert.equal(request.constraints.wall_temp_limit_K,800);assert.equal(request.constraints.wall_limit_basis,"试验报告 A，限定热处理状态");assert.equal(request.constraints.min_rib_mm,.8);
});

test("saved candidates display actual inlet and throat speeds, material status and separate constraints",()=>{
  const r=result(),before=clone(r),html=renderCoolingDesignResult(r,1,{canAdopt:true});
  assert.match(html,/实际通道入口速度/);assert.match(html,/19 m\/s/);assert.match(html,/实际喉部节点速度/);assert.match(html,/130 m\/s/);
  assert.match(html,/材料.*温区/);assert.match(html,/制造状态未确认/);assert.match(html,/尚未评估/);
  assert.match(html,/喉部代表通道截面/);assert.match(html,/节距 2\.3 · 肋宽 0\.8/);
  assert.match(html,/本次非支配候选/);assert.match(html,/不是全局最优/);assert.match(html,/候选吸热尚未反馈整机/);
  assert.doesNotMatch(html,/99,999|88,888/);assert.match(html,/通道宽度超过节距/);assert.deepEqual(r,before);
});

test("scatter excludes failed or nonfinite values and handles a single finite candidate",()=>{
  const r=result();r.candidates.push({...candidate(3),metrics:{dp_MPa:NaN,wall_max_K:Infinity}});
  const html=renderCoolingCandidateScatter(r.candidates,1);
  assert.match(html,/data-cd-candidate="1"/);assert.doesNotMatch(html,/data-cd-candidate="2"|data-cd-candidate="3"|NaN|Infinity/);
  assert.match(html,/aria-pressed="true"/);assert.match(html,/tabindex="0"/);
  assert.doesNotMatch(renderCoolingCandidateScatter([r.candidates[1]]),/<svg/);
});

test("failed and missing-profile candidates retain error information without invented curves",()=>{
  const r=result(),html=renderCoolingDesignResult(r,2,{canAdopt:true});
  assert.match(html,/计算失败/);assert.match(html,/通道宽度超过节距/);assert.doesNotMatch(html,/data-cd-action="adopt"/);
  delete r.candidates[0].profile;delete r.candidates[0].metrics.throat_pitch_mm;
  const partial=renderCoolingDesignResult(r,1);assert.match(partial,/缺少完整喉部通道截面/);assert.match(partial,/未保存可用沿程数值/);assert.doesNotMatch(partial,/NaN|Infinity/);
});

test("untrusted diagnostic strings are escaped in both tables and SVG",()=>{
  const r=result();r.scope_note='<script>alert(1)</script>';r.candidates[0].id='1" onload="bad';r.candidates[0].assessment.checks[0].id='<img src=x>';r.candidates[1].error='<img src=x onerror="bad">';
  const html=renderCoolingDesignResult(r,r.candidates[0].id,{canAdopt:true});
  assert.doesNotMatch(html,/<script>|<img src=x|onload="bad/);assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;img src=x/);assert.match(html,/1&quot; onload=&quot;bad/);
});

test("channel-design classes cannot inherit the separate chilldown chart stroke styles",()=>{
  const source=readFileSync(new URL("../webapp/static/cooling-design.css",import.meta.url),"utf8");
  const chilldown=readFileSync(new URL("../webapp/static/chilldown.css",import.meta.url),"utf8");
  const classes=text=>new Set([...text.matchAll(/\.([a-z][\w-]*)/g)].map(match=>match[1]));
  const other=classes(chilldown);
  const common=[...classes(source)].filter(name=>other.has(name)&&!name.startsWith("is-"));
  assert.deepEqual(common,[],"shared unscoped classes would change pre-cooling or channel-design plots");
  const html=renderCoolingDesignResult(result(),1);
  const oldClasses=[...html.matchAll(/class="([^"]*)"/g)].flatMap(match=>match[1].split(/\s+/)).filter(name=>name.startsWith("cd-"));
  assert.deepEqual(oldClasses,[]);
});

function container(){
  const listeners={},parts=new Map();let markup="",shellWrites=0;
  const view={listeners,parts,get shellWrites(){return shellWrites;},get innerHTML(){return markup;},set innerHTML(value){markup=value;shellWrites++;parts.clear();for(const selector of ["[data-cd-error]","[data-cd-settings]","[data-cd-output]","[data-cd-count]","[data-cd-settings] fieldset",'[data-cd-field="wall_temp_limit_K"]'])parts.set(selector,{innerHTML:"",textContent:"",disabled:false,classList:{toggle(){}}});},
    addEventListener(type,handler){listeners[type]=handler;},removeEventListener(type,handler){if(listeners[type]===handler)delete listeners[type];},querySelector(selector){return parts.get(selector)||null;},
    action(action){listeners.click({target:{closest:selector=>selector==="[data-cd-action]"?{dataset:{cdAction:action}}:null}});},
    field(key,value){listeners.input({target:{dataset:{cdField:key},value}});},
    chooseRecord(id){listeners.change({target:{hasAttribute:key=>key==="data-cd-record",value:id}});},
    chooseCandidate(id){listeners.click({target:{closest:selector=>selector==="[data-cd-candidate]"?{dataset:{cdCandidate:String(id)}}:null}});},
    output(){return parts.get("[data-cd-output]")?.innerHTML||"";},error(){return parts.get("[data-cd-error]")?.innerHTML||"";}};
  return view;
}
function server(record=null){
  const calls=[],s=setup();let saved=record;
  return {calls,setRecord(value){saved=value;},async api(path,method="GET",body){calls.push({path,method,body});
    if(path.includes("/setup?"))return clone(s);
    if(path==="/projects/p1/cooling-designs"&&method==="POST"){saved={id:"study1",project_id:"p1",revision:2,status:"queued",request:clone(body)};return {id:saved.id,status:saved.status};}
    if(path==="/projects/p1/cooling-designs")return saved?[clone(saved)]:[];
    if(path==="/cooling-designs/study1/cancel"){saved.status="cancelled";return {status:"cancelled"};}
    if(path==="/cooling-designs/study1")return clone(saved);
    throw Error(`unexpected ${method} ${path}`);
  }};
}

test("polling preserves unsent edits and does not replace the input subtree",async()=>{
  const host=container(),backend=server(),ctx=context(),ui=mountCoolingDesign(host,{api:backend.api,getContext:()=>ctx});
  try{
    await ui.refresh();host.field("channel_counts","260, 320");host.field("heights_mm","7");
    const writes=host.shellWrites;await ui.refresh();assert.equal(host.shellWrites,writes);
    host.action("run");await tick();const submitted=backend.calls.find(call=>call.method==="POST");
    assert.deepEqual(submitted.body.channel_counts,[260,320]);assert.deepEqual(submitted.body.heights_mm,[7]);
    assert.equal(submitted.body.source_job_id,"j1");assert.equal(ctx.draft.cooling_n_channels,999);assert.match(host.output(),/排队中/);
  }finally{ui.onLeave();}
});

test("adoption delegates only IDs and never writes the project or starts a design calculation",async()=>{
  const record={id:"study1",project_id:"p1",revision:2,status:"succeeded",request:{source_job_id:"j1"},result:result()},backend=server(record),host=container(),adopted=[];
  const ui=mountCoolingDesign(host,{api:backend.api,getContext:context,onAdopt:async value=>adopted.push(value)});
  try{await ui.refresh();host.chooseCandidate(1);host.action("adopt");await tick();assert.deepEqual(adopted,[{studyId:"study1",candidateId:1}]);assert.ok(backend.calls.every(call=>call.method==="GET"));}
  finally{ui.onLeave();}
});

test("reusing saved screening constraints keeps the new source and edited candidate dimensions",async()=>{
  const savedConstraints={wall_temp_limit_K:1200,wall_limit_status:"documented",wall_limit_basis:"试验报告 A",max_pressure_drop_MPa:16,min_rib_mm:.7,max_aspect_ratio:7,max_velocity_ms:150,basis:"制造及供压预算 B"};
  const record={id:"study1",project_id:"p1",revision:2,status:"succeeded",request:{source_job_id:"j1",constraints:savedConstraints},result:result()},before=clone(record),backend=server(record),host=container(),notices=[];
  const ctx=context();ctx.record.id="new-closed-cycle";ctx.record.revision=3;
  const ui=mountCoolingDesign(host,{api:backend.api,getContext:()=>ctx,notify:text=>notices.push(text)});
  try{
    await ui.refresh();host.field("channel_counts","280, 320");host.field("widths_mm","1.3");host.field("heights_mm","7, 9");host.field("wall_thicknesses_mm",".6");
    const shellWrites=host.shellWrites;host.action("reuse-constraints");
    assert.equal(host.shellWrites,shellWrites,"reusing constraints must not replace the dimension editor");
    assert.ok(backend.calls.every(call=>call.method==="GET"));assert.match(notices[0],/请重新计算/);assert.deepEqual(record,before);
    await ui.refresh();host.action("run");await tick();
    const request=backend.calls.find(call=>call.method==="POST").body;
    assert.equal(request.source_job_id,"new-closed-cycle");assert.deepEqual(request.channel_counts,[280,320]);assert.deepEqual(request.widths_mm,[1.3]);assert.deepEqual(request.heights_mm,[7,9]);assert.deepEqual(request.wall_thicknesses_mm,[.6]);
    assert.deepEqual(request.constraints,savedConstraints);assert.match(host.innerHTML,/重新反馈主循环后验算/);
  }finally{ui.onLeave();}
});

test("reusing an unset constraint preserves its unassessed state instead of inheriting a previous typed limit",async()=>{
  const saved={wall_temp_limit_K:null,wall_limit_status:"unset",wall_limit_basis:"",max_pressure_drop_MPa:null,min_rib_mm:null,max_aspect_ratio:null,max_velocity_ms:null,basis:""};
  const record={id:"study1",project_id:"p1",revision:2,status:"succeeded",request:{source_job_id:"j1",constraints:saved},result:result()},backend=server(record),host=container();
  const ui=mountCoolingDesign(host,{api:backend.api,getContext:context});
  try{
    await ui.refresh();host.field("wall_limit_status","provisional");host.field("wall_temp_limit_K","900");host.field("max_velocity_ms","100");host.action("reuse-constraints");host.action("run");await tick();
    assert.deepEqual(backend.calls.find(call=>call.method==="POST").body.constraints,saved);
    assert.equal(host.parts.get('[data-cd-field="wall_temp_limit_K"]').disabled,true);
    assert.match(renderCoolingDesignResult(result(),1),/旧筛选结果不能作为新工况通过的依据/);
  }finally{ui.onLeave();}
});

test("cancelled studies keep partial candidate results and cancellation uses its own endpoint",async()=>{
  const record={id:"study1",project_id:"p1",revision:2,status:"running",request:{source_job_id:"j1"},progress:{iteration:1,maximum:2},result:result()},backend=server(record),host=container();
  const ui=mountCoolingDesign(host,{api:backend.api,getContext:context});
  try{await ui.refresh();assert.match(host.output(),/1 \/ 2 个候选/);host.action("cancel");await tick();assert.ok(backend.calls.some(call=>call.path==="/cooling-designs/study1/cancel"&&call.method==="POST"));assert.match(host.output(),/已取消/);assert.match(host.output(),/喉部代表通道截面/);}
  finally{ui.onLeave();}
});

test("unsupported saved boundaries show an actionable error while keeping history readable",async()=>{
  const host=container(),ui=mountCoolingDesign(host,{getContext:context,api:async path=>{if(path.includes("/setup?"))throw Error("来源记录未保存沿程热侧状态，请重新运行设计点");return [];}});
  try{await ui.refresh();assert.match(host.error(),/重新运行设计点/);assert.match(host.parts.get("[data-cd-settings]").innerHTML,/暂不能用于/);assert.match(host.output(),/筛选记录/);}
  finally{ui.onLeave();}
});

test("pending responses cannot cross a project switch or revive a view after leaving",async()=>{
  let done;const promise=new Promise(resolve=>done=resolve),host=container();let ctx=context();
  const ui=mountCoolingDesign(host,{getContext:()=>ctx,api:()=>promise}),pending=ui.refresh();
  ctx={project:{id:"p2",revision:1},record:null};ui.sync();const before=host.innerHTML;done([]);await pending;assert.equal(host.innerHTML,before);assert.doesNotMatch(host.output(),/study1/);ui.onLeave();
  const backend=server(),host2=container();let complete;const notices=[];
  const ui2=mountCoolingDesign(host2,{getContext:context,notify:text=>notices.push(text),api:(path,method,body)=>method==="POST"?new Promise(resolve=>complete=resolve):backend.api(path,method,body)});
  await ui2.refresh();host2.action("run");ui2.onLeave();const oldOutput=host2.output();complete({id:"late-study",status:"queued"});await tick();assert.equal(host2.output(),oldOutput);assert.deepEqual(notices,[]);
});

test("null host is safe and remount removes old delegated listeners",async()=>{
  const safe=mountCoolingDesign(null);await safe.refresh();safe.onLeave();
  const host=container(),backend=server(),first=mountCoolingDesign(host,{api:backend.api,getContext:context}),old=host.listeners.click;
  const second=mountCoolingDesign(host,{api:backend.api,getContext:context});assert.notEqual(host.listeners.click,old);await second.refresh();first.onLeave();second.onLeave();
});
