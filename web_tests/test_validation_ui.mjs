/** Validation UI contracts: source binding, asynchronous state, boundaries and escaping. */
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";
import {createValidationForm,buildValidationRequest,renderValidationResult,scanValues,propertyPoints} from "../webapp/static/validation.js";

import {renderThermoCharts,buildThermoChartSvg} from "../webapp/static/validation-charts.js";

import {renderPropertyComparison,buildPropertyComparisonSvg,buildPropertyComparisonCsv} from "../webapp/static/property-comparison.js";
import {renderThermodynamicApplicability} from "../webapp/static/thermo-applicability.js";

const clone=value=>JSON.parse(JSON.stringify(value));
const inputs={propellant_pair:"lox_kerosene",cycle_type:"gg",gg_layout:"single_shaft",pc:9.7e6,
  of_ratio:2.36,expansion_ratio:16,fuel_inlet_temp:290,ox_inlet_temp:90,inlet_pressure:1e6,ambient_pressure:101325};
function context(){return {project:{id:"p1",revision:3},dirty:true,draft:{...inputs,pc:33e6,propellant_pair:"lox_methane"},
  record:{id:"design-1",project_id:"p1",revision:2,status:"succeeded",created:1,inputs:clone(inputs),
    result:{submitted_inputs:clone(inputs),calculation:{inputs:clone(inputs),summary:{thrust_N:845000}}}}};}
const capabilities={cantera:{available:true,version:"3.2"},cea:{available:false,reason:"RocketCEA 未安装"},
  coolprop:{available:true,version:"7.2"},refprop:{available:false,reason:"未安装 REFPROP"}};
const result={source:{job_id:"design-1",revision:2},conservation:{kind:"conservation",status:"partial",checks:[
  {id:"mass",label:"总流量闭合",status:"pass",left_si:300,right_si:300,difference_si:0,tolerance_si:.001,unit:"kg/s"},
  {id:"energy",label:"完整系统能量",status:"unavailable",reason:"缺少统一边界焓"}]},
  thermochemistry:{provenance:{cantera_version:"3.2.0",rocketcea_version:"1.2.1",coolprop_version:"7.2.0",
    mechanism:"nDodecane_Reitz.yaml",phase:"nDodecane_IG",mechanism_sha256:"mechanism-fingerprint",
    cea_database_files:[{name:"thermo.lib",sha256:"cea-database-fingerprint"}]},
    counts:{requested:2,cantera_success:2,cea_success:1,paired_success:1},availability:{cea:{available:false,reason:"独立后端缺失"}},points:[
    {of:2.36,eps:16,pc_pa:9.7e6,cantera:{status:"ok",metrics:{Isp_vac_s:316,Tc_K:3600}},
      cea:{status:"ok",metrics:{Isp_vac_s:320,Tc_K:3630}},differences:{Isp_vac_s:{absolute:4,relative_percent:1.2658}},
      comparable:{conditions_aligned:true,products_aligned:false,notes:["产品集不同"]}},
    {of:2.5,eps:16,cantera:{status:"ok",metrics:{Isp_vac_s:318}},cea:{status:"error",reason:"CEA 点计算失败"},differences:{Isp_vac_s:null},comparable:{notes:["无法对比"]}},
  ]},properties:{kind:"properties",status:"partial",checks:[{id:"fluid-1",fluid:"n-Dodecane",temperature_K:290,pressure_Pa:1e6,status:"pass",phase:"liquid",
    properties_si:{density_kg_m3:750,enthalpy_J_kg:300000,cp_J_kg_K:2100},refprop:{status:"unavailable",reason:"缺少授权后端"}}]},
  experiment:{kind:"experiment",status:"fail",checks:[{id:"thrust",label:"推力",status:"fail",predicted_si:845000,measured_si:820000,difference_si:25000,tolerance_si:5000,unit:"N",reason:"超出容差"}]}};

test("validation defaults and requests bind the frozen completed design, never the current draft",()=>{
  const ctx=context(),before=clone(ctx),form=createValidationForm(ctx);
  assert.equal(form.pc_MPa,"9.7");assert.equal(form.T_fuel_K,"290");
  assert.match(form.property_points,/n-Dodecane, 290, 1/);
  const request=buildValidationRequest(ctx,{...form,scan_mode:"point"},"thermochemistry");
  assert.deepEqual(request,{source_job_id:"design-1",thermochemistry:{propellant_pair:"lox_kerosene",pc_pa:9.7e6,of_values:[2.36],eps_values:[16],T_fuel_K:290,T_ox_K:90,mode:"equilibrium",product_policy:"native_databases",cantera_dataset:"production",methane_thermo_dataset:"gri30"}});
  assert.deepEqual(buildValidationRequest(ctx,form,"conservation"),{source_job_id:"design-1"});
  assert.deepEqual(ctx,before);
  delete ctx.record.inputs;
  assert.equal(createValidationForm(ctx).pc_MPa,"9.7");
  delete ctx.record.result.submitted_inputs;
  assert.equal(createValidationForm(ctx).pc_MPa,"9.7");
});

test("unfinished, absent and foreign-project source jobs cannot be submitted",()=>{
  for(const mutate of [ctx=>ctx.record.status="running",ctx=>ctx.record=null,ctx=>ctx.record.project_id="p-other",ctx=>ctx.record.result=null]){
    const ctx=context();mutate(ctx);
    assert.throws(()=>buildValidationRequest(ctx,createValidationForm(ctx),"conservation"),/已完成/);
  }
});

test("thermochemistry grid, mode and numeric boundaries fail before an API request",()=>{
  const ctx=context(),base={...createValidationForm(ctx),scan_mode:"list"};
  for(const changes of [{of_values:Array.from({length:16},(_,i)=>i+1).join(","),eps_values:"10,20,30,40"},{of_values:""},
    {pc_MPa:"Infinity"},{T_fuel_K:"-1"},{mode:"unsupported"},{eps_values:"0"}]){
    assert.throws(()=>buildValidationRequest(ctx,{...base,...changes},"thermochemistry"));
  }
  const good=buildValidationRequest(ctx,{...base,of_values:"1，2 3;4",eps_values:"10,20,30,40,50"},"thermochemistry");
  assert.equal(good.thermochemistry.of_values.length*good.thermochemistry.eps_values.length,20);
});

test("property points preserve fluid identity and convert MPa to Pa with a twenty-point limit",()=>{
  const ctx=context(),form=createValidationForm(ctx);
  const request=buildValidationRequest(ctx,{...form,compare_refprop:true},"properties");
  assert.deepEqual(request.properties,{compare_refprop:true,points:[{fluid:"Oxygen",temperature_K:90,pressure_Pa:1e6},{fluid:"n-Dodecane",temperature_K:290,pressure_Pa:1e6}]});
  for(const property_points of ["RP1,290,1","Methane,100,0","Oxygen,90","Oxygen,NaN,1","Oxygen,10001,1","Oxygen,90,1e308",Array(21).fill("Oxygen,90,1").join("\n")]){
    assert.throws(()=>buildValidationRequest(ctx,{...form,property_points},"properties"));
  }
});

test("property defaults prefer recorded state points and leave absent pressures blank instead of inventing them",()=>{
  const ctx=context();
  ctx.record.result.calculation.states={lox_pump_inlet:{temperature:92,pressure:.8e6},fuel_pump_inlet:{temperature:294,pressure:.9e6}};
  assert.equal(createValidationForm(ctx).property_points,"Oxygen, 92, 0.8\nn-Dodecane, 294, 0.9");
  delete ctx.record.result.calculation.states;
  delete ctx.record.inputs.inlet_pressure;
  assert.equal(createValidationForm(ctx).property_points,"Oxygen, 90, \nn-Dodecane, 290, ");
  assert.throws(()=>buildValidationRequest(ctx,createValidationForm(ctx),"properties"),/压力/);
  ctx.record.inputs.ox_tank_pressure=.4e6;ctx.record.inputs.fuel_tank_pressure=.3e6;
  assert.equal(createValidationForm(ctx).property_points,"Oxygen, 90, 0.4\nn-Dodecane, 290, 0.3");
});

test("experiment requires confirmed conditions and real measurements with explicit uncertainty or tolerance",()=>{
  const ctx=context(),form=createValidationForm(ctx);
  assert.equal(form.exp_thrust,"");assert.equal(form.conditions_confirmed,false);
  assert.throws(()=>buildValidationRequest(ctx,form,"experiment"),/确认试验工况/);
  form.conditions_confirmed=true;
  assert.throws(()=>buildValidationRequest(ctx,form,"experiment"),/至少填写/);
  form.exp_thrust="840";
  assert.throws(()=>buildValidationRequest(ctx,form,"experiment"),/容差或测量不确定度/);
  form.exp_thrust_tolerance="5";form.exp_thrust_uncertainty="2";
  const request=buildValidationRequest(ctx,form,"experiment");
  assert.deepEqual(request.experiment.measurements,[{metric:"thrust_N",value:840,unit:"kN",tolerance:5,uncertainty:2}]);
  assert.deepEqual(request.experiment.conditions.ambient_pressure,{value:.101325,unit:"MPa",tolerance:0});
  assert.equal(request.experiment.conditions.propellant_pair,"lox_kerosene");
  assert.equal(request.experiment.role,"holdout");
  form.experiment_role="calibration";form.exp_thrust="0";
  assert.equal(buildValidationRequest(ctx,form,"experiment").experiment.measurements[0].value,0);
  assert.equal(buildValidationRequest(ctx,form,"experiment").experiment.role,"calibration");
});

test("results expose unavailable backends, per-point failures and comparison mismatches without claiming pass",()=>{
  const html=renderValidationResult(result,"thermochemistry");
  assert.match(html,/独立后端缺失/);assert.match(html,/CEA 点计算失败/);assert.match(html,/不可直接对比/);
  assert.match(html,/产品集不同/);assert.match(html,/<svg/);assert.match(html,/1 \/ 2 点具有双后端结果/);
  assert.match(html,/版本与数据库指纹/);assert.match(html,/Cantera 版本/);assert.match(html,/CEA 数据库 thermo.lib/);
  assert.match(html,/mechanism-fingerprint/);assert.match(html,/cea-database-fingerprint/);
  assert.match(html,/&quot;provenance&quot;/);
  assert.doesNotMatch(html,/validation-pass/);
  const conservation=renderValidationResult(result,"conservation");
  assert.match(conservation,/kg\/s/);assert.match(conservation,/缺少统一边界焓/);
  const properties=renderValidationResult(result,"properties");
  assert.match(properties,/750/);assert.match(properties,/2.1/);assert.match(properties,/缺少授权后端/);
  assert.match(properties,/真实流体|物性对比/);assert.doesNotMatch(properties,/<span>properties<\/span>/);
  const experiment=renderValidationResult(result,"experiment");
  assert.match(experiment,/25,000/);assert.match(experiment,/超出容差/);assert.match(experiment,/validation-fail/);
});

test("dynamic messages, identities and diagnostics are escaped; markup remains CSP compatible",()=>{
  const attack='<img src=x onerror="alert(1)"><script>alert(2)</script>';
  const poisoned=clone(result);
  poisoned.thermochemistry.availability.cea.reason=attack;
  poisoned.thermochemistry.points[0].comparable.notes=[attack];
  poisoned.thermochemistry.points[1].cea.reason=attack;
  poisoned.thermochemistry.scope={detail:attack};
  poisoned.thermochemistry.provenance.cea_database_files[0].name=attack;
  poisoned.thermochemistry.provenance.mechanism_sha256=attack;
  poisoned.properties.checks[0].fluid=attack;
  poisoned.experiment.checks[0].label=attack;
  for(const tab of ["thermochemistry","properties","experiment"]){
    const html=renderValidationResult(poisoned,tab);
    assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|<script|\sstyle=|\sonclick=/);
  }
});

const moduleSource=readFileSync(new URL("../webapp/static/validation.js",import.meta.url),"utf8").replace(/^import .*;\r?$/gm,"").replace(/^export /gm,"");
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function mounted(fetcher,initial=context()){
  const listeners=new Map(),timers=new Map(),calls=[],notifications=[],downloads=[];
  let ctx=initial,nextTimer=1;
  const container={innerHTML:"",addEventListener(type,callback){listeners.set(type,callback);}};
  const sandbox=vm.createContext({console,Blob,renderThermoCharts,buildThermoChartSvg,renderPropertyComparison,buildPropertyComparisonSvg,buildPropertyComparisonCsv,renderThermodynamicApplicability,URL:{createObjectURL:()=>"blob:test",revokeObjectURL(){}},
    document:{createElement:()=>({click(){downloads.push(this.download);}})},
    setTimeout(callback){const id=nextTimer++;timers.set(id,callback);return id;},clearTimeout(id){timers.delete(id);}});
  vm.runInContext(moduleSource+"\n globalThis.mount=mountValidation;",sandbox);
  const api=async(path,method="GET",body)=>{calls.push({path,method,body:body&&clone(body)});return fetcher(path,method,body);};
  const controller=sandbox.mount(container,{api,getContext:()=>ctx,notify:text=>notifications.push(text)});
  return {container,controller,calls,timers,notifications,downloads,setContext(value){ctx=value;},
    click(action){listeners.get("click")({target:{closest:()=>({dataset:{validationAction:action}})}});},
    tab(value){listeners.get("click")({target:{closest:()=>({dataset:{validationTab:value}})}});},
    field(key,value){listeners.get("input")({target:{dataset:{validationField:key},type:typeof value==="boolean"?"checkbox":"text",value,checked:value}});},
    changeField(key,value){listeners.get("change")({target:{dataset:{validationField:key},value}});},
    group(value){listeners.get("change")({target:{value,hasAttribute:key=>key==="data-property-group"}});},
    record(value){listeners.get("change")({target:{value,hasAttribute:key=>key==="data-validation-record"}});},
    async tick(){const entry=timers.entries().next().value;if(entry){timers.delete(entry[0]);entry[1]();await settle();}}};
}
function standardAPI(path){if(path==="/validation/capabilities")return clone(capabilities);if(path==="/projects/p1/validations")return [];throw Error(`Unexpected ${path}`);}

test("mount shows source differences, missing dependencies and all four tabs without touching engine data",async()=>{
  const ctx=context(),before=clone(ctx),h=mounted(standardAPI,ctx);
  await h.controller.refresh();
  assert.match(h.container.innerHTML,/方案 v2/);assert.match(h.container.innerHTML,/当前草稿或方案版本/);
  assert.match(h.container.innerHTML,/RocketCEA 未安装/);assert.match(h.container.innerHTML,/REFPROP/);
  for(const tab of ["thermochemistry","conservation","properties","experiment"])assert.match(h.container.innerHTML,new RegExp(`data-validation-tab="${tab}"`));
  h.tab("experiment");
  assert.match(h.container.innerHTML,/data-validation-field="conditions_confirmed" type="checkbox" >/);
  assert.match(h.container.innerHTML,/液氧 \/ 煤油/);
  for(const [label,unit] of [["环境压力下推力","kN"],["环境压力下比冲","s"],["总流量","kg/s"]]){
    for(const name of ["实测值","判定容差","测量不确定度"])assert.ok(h.container.innerHTML.includes(`aria-label="${label}${name} / ${unit}"`));
  }
  h.tab("conservation");
  assert.match(h.container.innerHTML,/质量流量与轴功率闭合/);
  assert.doesNotMatch(h.container.innerHTML,/质量流量、推力|推力闭合/);
  assert.deepEqual(ctx,before);assert.ok(h.calls.every(call=>call.method==="GET"));
  h.controller.onLeave();
});

test("submit only the current section, retain immutable source, then cancel and export its separate record",async()=>{
  const ctx=context(),before=clone(ctx);let status="queued";
  const record=()=>({id:"v-1",project_id:"p1",revision:2,status,request:{source_job_id:"design-1"},result:null});
  const h=mounted((path,method,body)=>{
    if(path==="/validation/capabilities")return clone(capabilities);
    if(path==="/projects/p1/validations"&&method==="POST"){assert.deepEqual(clone(body),{source_job_id:"design-1"});return {id:"v-1"};}
    if(path==="/projects/p1/validations")return [record()];
    if(path==="/validations/v-1")return record();
    if(path==="/validations/v-1/cancel"){status="cancelled";return {status};}
    throw Error(`Unexpected ${path}`);
  },ctx);
  h.tab("conservation");h.click("run");await settle();
  assert.match(h.container.innerHTML,/排队中/);assert.ok(h.timers.size>0);
  h.click("cancel");await settle();
  assert.match(h.container.innerHTML,/已取消/);assert.equal(h.timers.size,0);
  h.click("export");assert.deepEqual(h.downloads,["validation-v-1.json"]);
  assert.deepEqual(ctx,before);
  assert.deepEqual(h.calls.filter(call=>call.method==="POST").map(call=>call.path),["/projects/p1/validations","/validations/v-1/cancel"]);
  h.controller.onLeave();
});

test("bad grid never submits and API failure is visible without modifying the source result",async()=>{
  const ctx=context(),before=clone(ctx),h=mounted((path,method)=>{if(method==="POST")throw Error("校核队列已满");return standardAPI(path);},ctx);
  h.field("scan_mode","list");h.field("of_values",Array.from({length:16},(_,i)=>i+1).join(","));h.field("eps_values","10,20,30,40");h.click("run");await settle();
  assert.match(h.container.innerHTML,/不得超过 60/);assert.equal(h.calls.length,0);
  h.field("of_values","2.36");h.field("eps_values","16");h.click("run");await settle();
  assert.match(h.container.innerHTML,/校核队列已满/);assert.deepEqual(ctx,before);
  h.controller.onLeave();
});

test("leaving stops polling and ignores in-flight responses; a new project cannot show old validation data",async()=>{
  let release;
  const pending=new Promise(resolve=>{release=resolve;});
  const h=mounted(path=>path==="/validation/capabilities"?clone(capabilities):path==="/projects/p2/validations"?[]:pending);
  const refresh=h.controller.refresh();
  h.controller.onLeave();const leftHTML=h.container.innerHTML;
  release([{id:"old-record",status:"running"}]);await refresh;
  assert.equal(h.container.innerHTML,leftHTML);assert.equal(h.timers.size,0);
  const other=context();other.project.id="p2";other.record.project_id="p2";other.record.id="design-2";
  h.setContext(other);
  await h.controller.refresh();
  assert.doesNotMatch(h.container.innerHTML,/old-record/);
  h.controller.onLeave();
});

test("late selected-record response cannot replace the more recently selected validation",async()=>{
  let releaseOld;
  const old=new Promise(resolve=>{releaseOld=resolve;});
  const h=mounted(path=>{
    if(path==="/validation/capabilities")return clone(capabilities);
    if(path==="/projects/p1/validations")return [];
    if(path==="/validations/old")return old;
    if(path==="/validations/new")return {id:"new",status:"succeeded",revision:2,result:clone(result)};
    throw Error(`Unexpected ${path}`);
  });
  await h.controller.refresh();h.record("old");h.record("new");await settle();
  releaseOld({id:"old",status:"failed",error:"old failure"});await settle();
  assert.match(h.container.innerHTML,/校核记录 new/);assert.doesNotMatch(h.container.innerHTML,/old failure/);
  h.controller.onLeave();
});

test("sync refreshes dirty warnings without network requests or discarding entered validation conditions",()=>{
  const ctx=context();ctx.dirty=false;ctx.project.revision=ctx.record.revision;
  const h=mounted(standardAPI,ctx);
  h.field("pc_MPa","12.5");
  assert.doesNotMatch(h.container.innerHTML,/当前草稿或方案版本/);
  ctx.dirty=true;h.controller.sync();
  assert.match(h.container.innerHTML,/当前草稿或方案版本/);
  assert.match(h.container.innerHTML,/data-validation-field="pc_MPa" value="12.5"/);
  assert.equal(h.calls.length,0);
  assert.match(h.container.innerHTML,/O\/F 起始值/);
  assert.doesNotMatch(h.container.innerHTML,/比热比 γ/);
  h.controller.onLeave();
});

test("source changes immediately before submit cannot combine a new job identity with the old job conditions",async()=>{
  const h=mounted((path,method)=>method==="POST"?{id:"new-validation"}:standardAPI(path));
  h.field("pc_MPa","77");
  const changed=context();changed.record.id="design-2";changed.record.revision=7;changed.record.inputs.pc=11e6;
  h.setContext(changed);h.click("run");await settle();
  assert.equal(h.calls[0].body.source_job_id,"design-2");
  assert.equal(h.calls[0].body.thermochemistry.pc_pa,11e6);
  assert.match(h.container.innerHTML,/方案 v7/);
  h.controller.onLeave();
});

test("submission keeps its original source revision if another source is selected while POST is pending",async()=>{
  let finish;
  const pending=new Promise(resolve=>{finish=resolve;});
  const h=mounted((path,method)=>method==="POST"?pending:standardAPI(path));
  h.tab("conservation");h.click("run");
  const changed=context();changed.record.id="design-2";changed.record.revision=8;
  h.setContext(changed);h.controller.sync();
  finish({id:"validation-original"});await settle();
  assert.match(h.container.innerHTML,/校核记录 validation-original · 方案 v2/);
  assert.match(h.container.innerHTML,/另一设计记录的校核结果/);
  assert.equal(h.calls[0].body.source_job_id,"design-1");
  h.controller.onLeave();
});

test("submission completed after a project switch does not insert the old project record into the new view",async()=>{
  let finish;
  const pending=new Promise(resolve=>{finish=resolve;});
  const h=mounted((path,method)=>method==="POST"?pending:standardAPI(path));
  h.click("run");
  const changed=context();changed.project.id="p2";changed.record.project_id="p2";changed.record.id="design-other";
  h.setContext(changed);h.controller.sync();
  finish({id:"old-project-validation",status:"queued"});await settle();
  assert.doesNotMatch(h.container.innerHTML,/old-project-validation/);
  assert.match(h.container.innerHTML,/design-other/);
  assert.equal(h.timers.size,0);
  h.controller.onLeave();
});

test("a slow cancellation completed after leaving cannot refresh the hidden page or restart polling",async()=>{
  let finishCancel;
  const cancellation=new Promise(resolve=>{finishCancel=resolve;});
  const active={id:"v-slow",project_id:"p1",revision:2,status:"running",request:{source_job_id:"design-1"}};
  const h=mounted(path=>{
    if(path==="/validation/capabilities")return clone(capabilities);
    if(path==="/projects/p1/validations")return [active];
    if(path==="/validations/v-slow")return active;
    if(path==="/validations/v-slow/cancel")return cancellation;
    throw Error(`Unexpected ${path}`);
  });
  await h.controller.refresh();
  assert.ok(h.timers.size>0);
  h.click("cancel");
  h.controller.onLeave();
  const leftHTML=h.container.innerHTML,requestCount=h.calls.length;
  finishCancel({status:"cancelled"});await settle();
  assert.equal(h.calls.length,requestCount);
  assert.equal(h.container.innerHTML,leftHTML);
  assert.equal(h.timers.size,0);
  assert.deepEqual(h.notifications,[]);
});


test("range scans include exact endpoints and produce the desktop 8 by 3 comparison without mutating design data",()=>{
  const ctx=context(),before=clone(ctx),form=createValidationForm(ctx);
  assert.equal(form.scan_mode,"range");assert.equal(scanValues(form).length,8);
  const scan={...form,of_start:"2.5",of_end:"5.0",of_count:"8",eps_values:"40,80,120"};
  const request=buildValidationRequest(ctx,scan,"thermochemistry");
  const grid=request.thermochemistry;
  assert.equal(grid.of_values[0],2.5);assert.equal(grid.of_values.at(-1),5);
  assert.equal(grid.of_values.length*grid.eps_values.length,24);
  for(let i=1;i<8;i++)assert.ok(Math.abs(grid.of_values[i]-grid.of_values[i-1]-2.5/7)<1e-10);
  assert.deepEqual(ctx,before);
  for(const change of [{of_start:"5",of_end:"2.5"},{of_start:"2.5",of_end:"2.5"},{of_count:"1"},{of_count:"21"},{of_count:"2.5"},{of_end:"21"}])
    assert.throws(()=>buildValidationRequest(ctx,{...scan,...change},"thermochemistry"));
  assert.equal(buildValidationRequest(ctx,{...scan,of_count:"20"},"thermochemistry").thermochemistry.of_values.length,20);
  assert.throws(()=>buildValidationRequest(ctx,{...scan,of_count:"20",eps_values:"10,20,30,40"},"thermochemistry"),/60/);
});

test("chart exports retain the selected immutable record and all four metric panels",async()=>{
  const record={id:"v-export",status:"succeeded",project_id:"p1",revision:2,result:clone(result)};
  const h=mounted(path=>path==="/validation/capabilities"?clone(capabilities):path==="/projects/p1/validations"?[record]:record);
  await h.controller.refresh();
  assert.match(h.container.innerHTML,/性能对比曲线/);
  assert.match(h.container.innerHTML,/data-validation-action="export-png"/);
  h.click("export-svg");await settle();
  assert.deepEqual(h.downloads,["thermochemistry-v-export.svg"]);
  h.controller.onLeave();
});


test("a refresh started before submission cannot replace the newly queued scan or stop its polling",async()=>{
  let finishList;
  const list=new Promise(resolve=>{finishList=resolve;});
  const h=mounted((path,method)=>{
    if(path==="/validation/capabilities")return clone(capabilities);
    if(method==="POST")return {id:"new-scan"};
    if(path==="/projects/p1/validations")return list;
    if(path==="/validations/old-scan")return {id:"old-scan",status:"succeeded",result:clone(result)};
    throw Error(`Unexpected ${path}`);
  });
  const refreshing=h.controller.refresh();
  h.click("run");await settle();
  finishList([{id:"old-scan",status:"succeeded"}]);await refreshing;
  assert.match(h.container.innerHTML,/校核记录 new-scan/);
  assert.doesNotMatch(h.container.innerHTML,/校核记录 old-scan/);
  assert.ok(h.timers.size>0);
  h.controller.onLeave();
});

test("clearing a record selection invalidates its pending detail response",async()=>{
  let finish;
  const detail=new Promise(resolve=>{finish=resolve;});
  const h=mounted(path=>path==="/validations/slow"?detail:standardAPI(path));
  h.record("slow");h.record("");
  finish({id:"slow",status:"succeeded",result:clone(result)});await settle();
  assert.doesNotMatch(h.container.innerHTML,/校核记录 slow/);
  h.controller.onLeave();
});


test("property temperature and pressure sweeps preserve exact bounds, fixed coordinates and source snapshot",()=>{
  const ctx=context(),before=clone(ctx),form=createValidationForm(ctx);
  const base={...form,property_mode:"temperature",property_fluid:"Methane",property_start:"90",property_end:"120",property_count:"7",property_fixed:"0.95"};
  const points=propertyPoints(base);
  assert.equal(points.length,7);assert.equal(points[0].temperature_K,90);assert.equal(points.at(-1).temperature_K,120);
  assert.ok(points.every(p=>p.pressure_Pa===950000&&p.fluid==="Methane"));
  const pressure={...base,property_mode:"pressure",property_start:"0.1",property_end:"10",property_fixed:"100",property_count:"20"};
  const request=buildValidationRequest(ctx,pressure,"properties");
  assert.equal(request.properties.points.length,20);assert.equal(request.properties.points[0].pressure_Pa,100000);
  assert.equal(request.properties.points.at(-1).pressure_Pa,1e7);assert.ok(request.properties.points.every(p=>p.temperature_K===100));
  assert.equal(request.source_job_id,"design-1");assert.deepEqual(ctx,before);
  for(const change of [{property_start:""},{property_end:"Infinity"},{property_fixed:"1e308"},{property_end:"10001"},{property_count:"21"},{property_count:"1"},{property_count:"2.5"},{property_fixed:"0"},{property_fluid:"RP-1"},{property_end:"80"},{property_mode:"bad"}]){
    assert.throws(()=>propertyPoints({...base,...change}));
  }
});

test("property scan modes initialize from frozen inlet states and retain fields across refresh",async()=>{
  const h=mounted(standardAPI);
  h.tab("properties");h.changeField("property_mode","temperature");
  assert.match(h.container.innerHTML,/data-validation-field="property_start" value="81"/);
  assert.match(h.container.innerHTML,/data-validation-field="property_end" value="99"/);
  assert.match(h.container.innerHTML,/data-validation-field="property_fixed" value="1"/);
  h.changeField("property_fluid","n-Dodecane");
  assert.match(h.container.innerHTML,/data-validation-field="property_start" value="261"/);
  h.changeField("property_mode","pressure");
  assert.match(h.container.innerHTML,/data-validation-field="property_fixed" value="290"/);
  h.field("property_start","0.4");await h.controller.refresh();
  assert.match(h.container.innerHTML,/data-validation-field="property_start" value="0.4"/);
  h.changeField("property_fluid","Methane");
  assert.match(h.container.innerHTML,/data-validation-field="property_fixed" value=""/);
  h.controller.onLeave();
});

test("property result exports use saved results even after editing the next scan conditions",async()=>{
  const record={id:"property-export",status:"succeeded",project_id:"p1",revision:2,result:clone(result)};
  const h=mounted(path=>path==="/validation/capabilities"?clone(capabilities):path==="/projects/p1/validations"?[record]:record);
  await h.controller.refresh();h.tab("properties");
  h.field("property_points","Oxygen,90,1");
  h.click("export-property-svg");await settle();h.click("export-property-csv");await settle();
  assert.deepEqual(h.downloads,["properties-property-export.svg","properties-property-export.csv"]);
  assert.deepEqual(record.result,result);assert.ok(h.calls.every(c=>c.method==="GET"));
  h.controller.onLeave();
});


test("recorded main-nozzle assumption initializes independent comparison without rewriting the source",()=>{
  const ctx=context();
  ctx.record.result.calculation.thermodynamic_applicability={nozzle:{id:"frozen_chamber"}};
  const before=clone(ctx);
  assert.equal(createValidationForm(ctx).mode,"frozen_chamber");
  assert.deepEqual(ctx,before);
});

test("advanced comparison datasets require the common methane gas set and persist explicit choices",()=>{
  const ctx=context(),form={...createValidationForm(ctx),product_policy:"matched_cho_gas",cantera_dataset:"nasa_gas_candidate"};
  assert.throws(()=>buildValidationRequest(ctx,form,"thermochemistry"),/仅适用于液氧/);
  ctx.record.inputs.propellant_pair="lox_methane";
  const result=buildValidationRequest(ctx,form,"thermochemistry").thermochemistry;
  assert.equal(result.product_policy,"matched_cho_gas");
  assert.equal(result.cantera_dataset,"nasa_gas_candidate");
  assert.throws(()=>buildValidationRequest(ctx,{...form,product_policy:"native_databases"},"thermochemistry"),/共同 CHO16/);
  assert.throws(()=>buildValidationRequest(ctx,{...form,cantera_dataset:"unverified"},"thermochemistry"),/有效的/);
});

test("changing comparison product scope resets the inapplicable candidate dataset",()=>{
  const ctx=context();ctx.record.inputs.propellant_pair="lox_methane";
  const h=mounted(standardAPI,ctx);
  h.changeField("product_policy","matched_cho_gas");
  h.changeField("cantera_dataset","nasa_gas_candidate");
  assert.match(h.container.innerHTML,/NASA TM-4513/);
  assert.match(h.container.innerHTML,/不含凝聚相和离子/);
  h.changeField("product_policy","native_databases");
  assert.doesNotMatch(h.container.innerHTML,/data-validation-field="cantera_dataset"/);
  h.controller.onLeave();
});

test("thermochemical range evidence exposes both extrapolated and unverified species",()=>{
  const fixture=clone(result);
  fixture.thermochemistry.points[0].cantera.diagnostics={station_temperature_ranges:{chamber:{temperature_K:3800,outside_species:{H2O:{min_K:200,max_K:3500,mass_fraction:.3}}}}};
  fixture.thermochemistry.points[0].cea.diagnostics={station_temperature_ranges:{chamber:{temperature_K:3801,outside_species:{},unverified_species:["<unknown>"]}}};
  const html=renderValidationResult(fixture,"thermochemistry");
  assert.match(html,/发生温区外推/);
  assert.match(html,/H2O：200–3,500 K/);
  assert.match(html,/存在未核实物种/);
  assert.match(html,/&lt;unknown&gt;/);
});
