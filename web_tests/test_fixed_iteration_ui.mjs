import assert from "node:assert/strict";
import test from "node:test";
import {renderModelData,renderFrameworkReport,renderFrameworkChecks,variablePlot,renderDataDeclarationCheck} from "../webapp/static/fixed-iteration.js";
import {renderValveRequirements} from "../webapp/static/valve-requirements.js";
import {mountFixedSteady} from "../webapp/static/fixed-steady.js";

const report=()=>({schema_version:1,analysis_type:"numerical_framework_check",scope:"numerical_framework_only",is_engine_result:false,
  status:"pass",reason:"converged",model_id:"dimensionless_coupled_algebra_v1",nfev:12,njev:3,rejected_steps:1,tolerance:1,
  variables:[{name:"x",label:"无量纲变量 x",unit:"1"},{name:"y",label:"无量纲变量 y",unit:"1"}],
  residual_specs:[{name:"equation_x",label:"x² + y − 5",scale:1,tolerance:1e-8,unit:"1"},{name:"equation_y",label:"y² + x − 3",scale:2,tolerance:1e-8,unit:"1"}],
  points:[{iteration:0,variables:{x:1.5,y:1.4},residuals:{equation_x:-135000000,equation_y:23000000},raw_residuals:{equation_x:-1.35,equation_y:.46},residual_norm:135000000,nfev:1},
    {iteration:1,variables:{x:2,y:1},residuals:{equation_x:0,equation_y:0},raw_residuals:{equation_x:0,equation_y:0},residual_norm:0,nfev:12}],limitations:["只检查数值接口"]});
const record=(taskId="task-1",id="check-1")=>({id,task_id:taskId,created:1,report:report()});
const manifest=()=>({schema_version:1,status:"missing_data",ready:false,conventions:{pressure:"absolute"},items:[{id:"pump",label:"泵特性",status:"design_evidence_only",detail:"需部件特性",evidence:[{source_path:"hardware.feed",description:"仅设计点"}],fields:[{key:"data_reference",label:"数据文件引用",unit:"reference",value:null,status:"missing"}]}]});
const task=()=>({schema_version:2,source:{job_id:"design-1",project_id:"p1"},hardware:{id:"hardware-1",payload:{nozzle:{}}},operating_conditions:{boundaries:{ambient_pressure:101325,tank_pressure_pa:8e5,fuel_inlet_temp:110,ox_inlet_temp:90}},model_data:manifest(),readiness:{can_run:false,blockers:[]}});
const context=()=>({project:{id:"p1"},jobs:[{id:"design-1",project_id:"p1",revision:1,status:"succeeded"}],record:null});
const click=(host,action)=>host.onclick({target:{closest:()=>({dataset:{fixedAction:action}})}});
const choose=(host,key,value)=>host.onchange({target:{dataset:{[key]:""},value}});

test("data manifest keeps evidence separate from missing characteristics and escapes content",()=>{
  const m=manifest();m.items[0].label='<img src=x>';m.items[0].evidence[0].source_path='<script>bad</script>';
  const before=JSON.stringify(m),html=renderModelData(m,{templateUrl:"/api/template"});
  assert.match(html,/仅有设计点证据/);assert.match(html,/待补/);assert.match(html,/下载待补数据模板/);
  assert.doesNotMatch(html,/<img|<script/);assert.match(html,/&lt;script&gt;/);assert.equal(JSON.stringify(m),before);
  assert.match(renderModelData(null),/此历史任务未包含/);
});

test("framework renderer refuses engine or ambiguous records and never synthesizes missing traces",()=>{
  for(const modified of [{scope:"engine"},{analysis_type:"fixed_hardware_steady"},{is_engine_result:true},{is_engine_result:undefined}]){
    const html=renderFrameworkReport({...report(),...modified});assert.match(html,/类型不匹配/);assert.doesNotMatch(html,/<svg/);
  }
  const failed=report();failed.status="fail";failed.reason='<img src=x>';failed.points=[];failed.error='<script>bad</script>';
  const html=renderFrameworkReport(failed);
  assert.match(html,/数值自检未通过/);assert.match(html,/没有可用接受步/);assert.doesNotMatch(html,/<svg|<img|<script/);
  assert.match(html,/&lt;script&gt;/);
});

test("true saved points create residual and signed variable plots with original data table",()=>{
  const r=report(),before=JSON.stringify(r),html=renderFrameworkReport(r,{downloadUrl:"/api/check"});
  assert.equal((html.match(/<svg /g)||[]).length,2);assert.match(html,/数值自检通过/);
  assert.match(html,/不代表发动机流量、压力或温度/);assert.match(html,/自检通过不改变发动机任务/);
  assert.match(html,/原始残差 ÷ 尺度 ÷ 容差/);assert.match(html,/>-1.35</);assert.match(html,/拒绝步 1 次/);
  assert.match(html,/href="\/api\/check"/);assert.equal(JSON.stringify(r),before);
  const segmented=variablePlot([{iteration:0,variables:{x:-2}},{iteration:1,variables:{x:null}},{iteration:2,variables:{x:3}}],[{name:"x",label:"<svg>",unit:"1"}]);
  const path=segmented.match(/<path[^>]* d="([^"]*)"/)[1];
  assert.equal((path.match(/M/g)||[]).length,2);assert.doesNotMatch(path,/L/);assert.match(segmented,/缺失 \/ 无效值 1 个/);assert.match(segmented,/-2 1/);
});

test("framework diagnostics expose recorded Jacobian evidence without treating selfcheck as engine validation",()=>{
  const r=report();
  r.diagnostics={schema_version:1,jacobian_rebuilds:2,jacobian_attempts:2,approximate_updates:3,model_evaluations:12,elapsed_seconds:.02,
    equations:[{name:"r1",label:"无量纲代数方程",unit:"1",component:"数值自检",raw_residual:0,scaled_residual:0,scale:1,absolute_tolerance:1e-6,passed:true}],
    variables:[],jacobian:{rank:2,rows:2,columns:2,condition_number:4,source:"broyden",at_x:[2,1]},events:[],failures:[],step_reductions:[],evaluation_counts:{initial:1,trial:11}};
  const before=structuredClone(r),html=renderFrameworkReport(r);
  assert.match(html,/完整雅可比重建<\/span><strong>2/);assert.match(html,/近似更新<\/span><strong>3/);
  assert.match(html,/无量纲代数方程/);assert.match(html,/不证明全局方程独立/);
  assert.match(html,/自检通过不改变发动机任务的待补数据状态/);assert.deepEqual(r,before);
});

test("check picker excludes foreign task records and can display a failed attempt without deleting earlier traces",()=>{
  const good=record(),failed=record("task-1","check-2"),foreign=record("other","secret-check");failed.report.status="fail";
  const html=renderFrameworkChecks([failed,good,foreign],{taskId:"task-1",selectedId:"check-1",baseUrl:"/api/task",error:"refresh failed"});
  assert.doesNotMatch(html,/secret-check/);assert.match(html,/数值自检通过/);assert.match(html,/check_id=check-1/);assert.match(html,/refresh failed/);
  assert.match(renderFrameworkChecks([]),/data-fixed-action="framework-check" disabled/);
});

test("valve requirements preserve recorded zeros and distinguish locations and unavailable fields",()=>{
  const q=(value,path,unit="Pa")=>({value,source_path:path,unit,status:"recorded"});
  const report={scope:"concept_valve_requirements",source:"saved_calculation_only",record_id:"design-1",rows:[{id:"branch",label:"支路A",source:"fuel_tap",target:"preburner",temperature_location:"branch_end",quantities:{mass_flow:q(2,"branch.mass","kg/s"),extra_valve_drop:q(0,"branch.valve_drop"),source_tap_temperature:q(110,"states.fuel_tap.temperature_K","K"),supply_end_temperature:q(125,"states.branch.temperature_K","K"),source_pressure:q(null,"missing")}}],missing_fields:["阀门位置","流量特性"],warnings:["<script>bad</script>"]};
  const html=renderValveRequirements(report);assert.match(html,/额外节流需求/);assert.match(html,/>0 <small>Pa/);
  assert.match(html,/110 <small>K/);assert.match(html,/125 <small>K/);assert.match(html,/待补/);assert.match(html,/states.fuel_tap.temperature_K/);
  assert.match(html,/压降为零不能证明无需阀门/);assert.doesNotMatch(html,/<script/);assert.match(html,/&lt;script&gt;/);
  assert.match(renderValveRequirements({...report,source:"calculated_now"}),/报告类型不匹配/);
});

test("running a saved task check uses only the separate endpoint and preserves engine blockers",async()=>{
  const host={innerHTML:""},state=context(),definition=task(),saved={id:"task-1",task:definition},calls=[];
  const view=mountFixedSteady(host,{getContext:()=>state,api:async(path,method,body)=>{
    calls.push({path,method,body});if(path.includes("setup?"))return definition;
    if(path.endsWith("/tasks"))return method==="POST"?saved:[];
    if(path.endsWith("/framework-checks"))return method==="POST"?record():[];
    return saved;
  }});
  await view.refresh();await click(host,"framework-check");assert.equal(calls.filter(c=>c.method==="POST").length,0);
  await click(host,"save");assert.match(host.innerHTML,/\/tasks\/task-1\/data-template/);
  await click(host,"framework-check");assert.match(host.innerHTML,/数值自检通过/);assert.match(host.innerHTML,/物理求解尚未开放/);
  assert.ok(calls.every(c=>!c.path.includes("/run")&&!c.path.includes("/jobs")));
  assert.deepEqual(calls.at(-1),{path:"/projects/p1/fixed-steady/tasks/task-1/framework-checks",method:"POST",body:{}});
  assert.equal(definition.readiness.can_run,false);
  await click(host,"draft");assert.doesNotMatch(host.innerHTML,/check_id=check-1/);
});

test("failed history request leaves the saved task available and check failure keeps prior evidence",async()=>{
  const host={innerHTML:""},state=context(),saved={id:"task-1",task:task()};let fail=false;
  const view=mountFixedSteady(host,{getContext:()=>state,api:async(path,method)=>{
    if(path.includes("setup?"))return task();if(path.endsWith("/tasks"))return [saved];
    if(path.endsWith("/framework-checks")){if(method==="POST"){if(fail)throw new Error("resource limit");return record();}throw new Error("history unavailable");}
    return saved;
  }});
  await view.refresh();await choose(host,"fixedRecord","task-1");assert.match(host.innerHTML,/history unavailable/);assert.match(host.innerHTML,/所有字段只读/);
  await click(host,"framework-check");fail=true;await click(host,"framework-check");
  assert.match(host.innerHTML,/resource limit/);assert.match(host.innerHTML,/数值自检通过/);assert.match(host.innerHTML,/check_id=check-1/);
});

test("late framework check cannot overwrite the next project or become its check history",async()=>{
  const host={innerHTML:""},state=context(),definition=task(),saved={id:"task-1",task:definition};let release;
  const pending=new Promise(resolve=>{release=resolve;});
  const view=mountFixedSteady(host,{getContext:()=>state,api:async(path,method)=>{
    if(path.includes("setup?")){const t=task();if(path.startsWith("/projects/p2/"))t.source.job_id="new-source";return t;}
    if(path.endsWith("/tasks"))return method==="POST"?saved:[];
    if(path.endsWith("/framework-checks"))return pending;
    return saved;
  }});
  await view.refresh();await click(host,"save");const running=click(host,"framework-check");
  state.project={id:"p2"};state.jobs=[{id:"new-source",project_id:"p2",status:"succeeded"}];await view.refresh();
  release(record());await running;assert.match(host.innerHTML,/new-source/);assert.doesNotMatch(host.innerHTML,/check_id=check-1|数值自检通过/);
});

test("data declaration check is scoped to metadata and never adopts hardware",async()=>{
  const host={innerHTML:""},state=context(),definition=task(),saved={id:"task-1",task:definition},calls=[];
  const checked={scope:"data_declaration_only",can_run:false,adopted:false,complete:false,missing:["items.pump.reference"],limitations:[]};
  const view=mountFixedSteady(host,{getContext:()=>state,api:async(path,method,body)=>{
    calls.push({path,method,body});if(path.includes("setup?"))return definition;
    if(path.endsWith("/data-check"))return checked;
    return method==="POST"?saved:[];
  }});
  await view.refresh();await click(host,"save");const before=JSON.stringify(saved);
  const upload=file=>host.onchange({target:{dataset:{fixedDataFile:""},files:[file]}});
  await upload({size:5,text:async()=>'{bad'});assert.match(host.innerHTML,/不是有效 JSON/);assert.ok(calls.every(c=>!c.path.endsWith("/data-check")));
  await upload({size:200001,text:async()=>"{}"});assert.match(host.innerHTML,/不超过 200000 字节/);
  await upload({size:100000,text:async()=>JSON.stringify({name:"测".repeat(70000)})});assert.match(host.innerHTML,/编码为 UTF-8 后超过 200000 字节/);
  assert.ok(calls.every(c=>!c.path.endsWith("/data-check")));
  await upload({size:2,text:async()=>"{}"});assert.match(host.innerHTML,/登记仍有 1 个缺项/);assert.match(host.innerHTML,/没有采用或替换硬件/);
  assert.deepEqual(calls.at(-1),{path:"/projects/p1/fixed-steady/tasks/task-1/data-check",method:"POST",body:{}});
  assert.equal(JSON.stringify(saved),before);
  const forged=renderDataDeclarationCheck({...checked,adopted:true});assert.match(forged,/类型不匹配/);assert.doesNotMatch(forged,/登记仍有/);
});

test("switching project during local JSON read prevents any data-check submission",async()=>{
  const host={innerHTML:""},state=context(),saved={id:"task-1",task:task()},calls=[];let release;
  const fileText=new Promise(resolve=>{release=resolve;});
  const view=mountFixedSteady(host,{getContext:()=>state,api:async(path,method)=>{
    calls.push({path,method});if(path.includes("setup?"))return task();return method==="POST"?saved:[];
  }});
  await view.refresh();await click(host,"save");const reading=host.onchange({target:{dataset:{fixedDataFile:""},files:[{size:2,text:()=>fileText}]}});
  state.project={id:"p2"};state.jobs=[];await view.refresh();release("{}");await reading;
  assert.ok(calls.every(call=>!call.path.endsWith("/data-check")));assert.doesNotMatch(host.innerHTML,/登记仍有/);
});
