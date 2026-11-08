import assert from "node:assert/strict";
import test from "node:test";
import {FIXED_BOUNDARIES,fixedSteadyForm,buildFixedSteadyRequest,renderFixedSteadyTask,mountFixedSteady} from "../webapp/static/fixed-steady.js";

const copy=value=>JSON.parse(JSON.stringify(value));
function task(source="design-1"){
  return {schema_version:2,mode:"fixed_hardware_steady",source:{job_id:source,project_id:"p1",revision:2,result_sha256:"a".repeat(64)},
    hardware:{schema_version:1,id:"b".repeat(64),payload:{cycle_type:"ffsc",propellant_pair:"lox_methane",nozzle:{throat_area_m2:.02,expansion_ratio:35},feed:{},cooling:{}}},
    operating_conditions:{boundaries:{ambient_pressure:101325,tank_pressure_pa:800000,fuel_inlet_temp:110,ox_inlet_temp:90},controls:{valve_openings:{}},control_targets:{}},
    legacy_design_assumptions:{inlet_pressure:{value:700000,role:"design_target"},feed_line_dp_pa:{value:100000,role:"model_assumption"}},
    initial_guess:{mode:"fixed_hardware_steady",variables:{chamber_pressure_pa:2e7}},constraints:{},
    readiness:{can_run:false,status:"blocked",blockers:[{code:"missing_solver",label:"固定硬件稳态求解器",detail:"尚未实现"}]}};
}
function context(){return {project:{id:"p1"},jobs:[1,2].map(n=>({id:`design-${n}`,project_id:"p1",revision:n,status:"succeeded",created:n})),record:{id:"design-1",project_id:"p1",revision:1,status:"succeeded",result:{}}};}
function host(){return {innerHTML:"",oninput:null,onchange:null,onclick:null};}
function click(container,action){return container.onclick({target:{closest:()=>({dataset:{fixedAction:action}})}});}
function change(container,key,value){return container.onchange({target:{dataset:{[key]:""},value}});}
function edit(container,key,value){container.oninput({target:{dataset:{fixedBoundary:key},value}});}
const saved=(definition,id="candidate-1")=>({id,project_id:"p1",source_job_id:definition.source.job_id,created:2,task:copy(definition)});

test("boundary request uses explicit fields and units without inheriting design targets or hardware",()=>{
  const template=task(),before=copy(template),form=fixedSteadyForm(template);
  form.ambient_pressure="0";form.ox_preburner_temperature=750;form.fuel_preburner_temperature=850;form.of_ratio=5;form.throat_area_m2=10;
  const request=buildFixedSteadyRequest("design-1",form);
  assert.deepEqual(Object.keys(request),["source_job_id","operating_conditions"]);
  assert.deepEqual(Object.keys(request.operating_conditions.boundaries),FIXED_BOUNDARIES.map(field=>field.key));
  assert.equal(request.operating_conditions.boundaries.tank_pressure_pa,800000);
  assert.equal(request.operating_conditions.boundaries.ambient_pressure,0);
  assert.deepEqual(request.operating_conditions.controls,{valve_openings:{}});
  assert.deepEqual(request.operating_conditions.control_targets,{});
  assert.deepEqual(template,before);
  for(const bad of ["",NaN,Infinity,-1])assert.throws(()=>buildFixedSteadyRequest("design-1",{...form,fuel_inlet_temp:bad}));
  const ignored=buildFixedSteadyRequest("design-1",{...form,inlet_pressure:"99",feed_line_dp_pa:"0.7",shaft_speed_rpm:"20000"});
  assert.deepEqual(ignored,request);
  assert.deepEqual(Object.keys(ignored.operating_conditions.boundaries),["ambient_pressure","tank_pressure_pa","fuel_inlet_temp","ox_inlet_temp"]);
  assert.throws(()=>buildFixedSteadyRequest("",form),/概念设计/);
});

test("rendered definitions escape source, hardware and missing capabilities and expose no concept target inputs",()=>{
  const template=task();template.source.job_id='<img src=x onerror="alert(1)">';template.hardware.id="<script>x</script>";
  template.readiness.blockers[0].detail="<svg onload=alert(1)>";
  template.initial_guess.variables.note="<b>unsafe</b>";
  const html=renderFixedSteadyTask(template,fixedSteadyForm(template),{readOnly:true});
  assert.doesNotMatch(html,/<img|<script|<svg|<b>unsafe/);
  assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;svg/);
  assert.equal((html.match(/readonly/g)||[]).length,4);
  assert.doesNotMatch(html,/data-fixed-action="run"|data-fixed-boundary="(?:of_ratio|ox_preburner|fuel_preburner|throat_area)/);
  assert.match(html,/物理求解尚未开放/);
  assert.match(html,/固定的是阀门的尺寸与特性，开度属于可调运行设定/);
  assert.match(html,/来源设计假设 · 未采纳为运行边界/);
  assert.doesNotMatch(html,/data-fixed-boundary="(?:inlet_pressure|feed_line_dp_pa)"/);
});

test("legacy task retains six historical values without promoting budgets to current boundaries",()=>{
  const old=task();old.schema_version=1;
  old.operating_conditions.boundaries.inlet_pressure=700000;
  old.operating_conditions.boundaries.feed_line_dp_pa=100000;
  delete old.legacy_design_assumptions;
  const before=copy(old),html=renderFixedSteadyTask(old,fixedSteadyForm(old),{readOnly:true});
  assert.equal((html.match(/readonly/g)||[]).length,6);
  assert.match(html,/data-fixed-boundary="inlet_pressure" value="0.7" readonly/);
  assert.match(html,/data-fixed-boundary="feed_line_dp_pa" value="0.1" readonly/);
  assert.match(html,/版本 1 历史定义/);assert.match(html,/未采纳为新固定硬件任务的外部边界/);
  assert.deepEqual(old,before);
});

test("old service templates cannot silently create new tasks using legacy assumptions",async()=>{
  const container=host(),state=context(),old=task(),calls=[];old.schema_version=1;
  const view=mountFixedSteady(container,{getContext:()=>state,api:async(path,method)=>{
    calls.push({path,method});return path.endsWith('/tasks')?[]:old;
  }});
  await view.refresh();await click(container,"save");
  assert.match(container.innerHTML,/请重启网页服务并刷新/);
  assert.equal(calls.filter(call=>call.method==='POST').length,0);
});

test("new definitions use saved role meanings while legacy histories show explicitly current reference",async()=>{
  const container=host(),state=context(),definition=task(),old=task();
  const catalog=description=>({rows:[{id:"valve_opening",label:"阀门开度",concept_design:{description:"选型要求"},fixed_hardware_steady:{description}}],principles:[]});
  definition.parameter_roles=catalog("保存的身份定义");old.schema_version=1;
  state.roleCatalog=catalog("当前身份参考");
  const record=saved(old),view=mountFixedSteady(container,{getContext:()=>state,api:async(path)=>{
    if(path.endsWith("/tasks"))return [record];if(path.includes("/setup?"))return definition;return record;
  }});
  await view.refresh();assert.match(container.innerHTML,/保存的身份定义/);assert.doesNotMatch(container.innerHTML,/当前身份参考/);
  await change(container,"fixedRecord","candidate-1");
  assert.match(container.innerHTML,/当前身份参考/);assert.match(container.innerHTML,/该历史任务未保存参数定义/);
});

test("saving persists a separate immutable definition and history stays read-only",async()=>{
  const container=host(),state=context(),calls=[],definition=task(),record=saved(definition);
  const view=mountFixedSteady(container,{getContext:()=>state,api:async(path,method,body)=>{
    calls.push({path,method,body});
    if(path.endsWith("/tasks")&&method==="POST"){const next=copy(definition);next.operating_conditions=copy(body.operating_conditions);return saved(next);}
    if(path.endsWith("/tasks"))return [record];
    if(path.includes("/setup?"))return definition;
    if(path.endsWith("/tasks/candidate-1"))return record;
    throw new Error(path);
  }});
  await view.refresh();edit(container,"fuel_inlet_temp","115");await click(container,"save");
  const submitted=calls.find(call=>call.method==="POST");
  assert.equal(submitted.path,"/projects/p1/fixed-steady/tasks");
  assert.equal(submitted.body.operating_conditions.boundaries.fuel_inlet_temp,115);
  assert.match(container.innerHTML,/所有字段只读/);
  assert.match(container.innerHTML,/href="\/api\/projects\/p1\/fixed-steady\/tasks\/candidate-1\/download"/);
  edit(container,"fuel_inlet_temp","999");await click(container,"save");
  assert.equal(calls.filter(call=>call.method==="POST").length,1);
  await click(container,"draft");assert.match(container.innerHTML,/data-fixed-boundary="fuel_inlet_temp" value="115"/);
  await change(container,"fixedRecord","candidate-1");
  assert.match(container.innerHTML,/data-fixed-boundary="fuel_inlet_temp" value="110" readonly/);
  assert.ok(calls.every(call=>!call.path.includes("/run")&&!call.path.endsWith("/jobs")&&!call.path.includes("throttle")));
});

test("each source retains its own operating draft across refreshes and history",async()=>{
  const container=host(),state=context();let setupCalls=0;
  const view=mountFixedSteady(container,{getContext:()=>state,api:async(path)=>{
    if(path.endsWith("/tasks"))return [];
    setupCalls++;return task(path.endsWith("design-2")?"design-2":"design-1");
  }});
  await view.refresh();edit(container,"fuel_inlet_temp","118");
  await view.refresh();await click(container,"refresh");
  assert.match(container.innerHTML,/data-fixed-boundary="fuel_inlet_temp" value="118"/);
  await change(container,"fixedSource","design-2");edit(container,"fuel_inlet_temp","122");
  await change(container,"fixedSource","design-1");
  assert.match(container.innerHTML,/data-fixed-boundary="fuel_inlet_temp" value="118"/);
  await change(container,"fixedSource","design-2");
  assert.match(container.innerHTML,/data-fixed-boundary="fuel_inlet_temp" value="122"/);
  assert.equal(setupCalls,2);
});

test("late setup from a previous project cannot replace the active task or source",async()=>{
  const container=host(),state=context();let release;
  const pending=new Promise(resolve=>{release=resolve;});
  const view=mountFixedSteady(container,{getContext:()=>state,api:async(path)=>{
    if(path.endsWith("/tasks"))return [];
    if(path.startsWith("/projects/p1/"))return pending;
    const next=task("new-design");next.source.project_id="p2";return next;
  }});
  const first=view.refresh();
  state.project={id:"p2"};state.record=null;state.jobs=[{id:"new-design",project_id:"p2",revision:1,status:"succeeded"}];
  await view.refresh();release(task("old-source"));await first;
  assert.match(container.innerHTML,/new-design/);assert.doesNotMatch(container.innerHTML,/old-source/);
  edit(container,"fuel_inlet_temp","120");view.onLeave();await view.refresh();
  assert.match(container.innerHTML,/data-fixed-boundary="fuel_inlet_temp" value="120"/);
});

test("saved task history remains readable when the current design cannot provide a template",async()=>{
  const container=host(),state=context(),record=saved(task("historical-design"));
  const view=mountFixedSteady(container,{getContext:()=>state,api:async(path)=>{
    if(path.endsWith("/tasks"))return [record];
    if(path.includes("/setup?"))throw new Error("当前设计缺少可冻结硬件");
    return record;
  }});
  await view.refresh();assert.match(container.innerHTML,/当前设计缺少可冻结硬件/);
  assert.match(container.innerHTML,/candidate-1/);
  await change(container,"fixedRecord","candidate-1");
  assert.match(container.innerHTML,/historical-design/);
  assert.match(container.innerHTML,/所有字段只读/);
});
