import assert from "node:assert/strict";
import test from "node:test";
import {controlTrimForm,buildControlTrimRequest,renderControlTrimDefinition,renderControlTrimCheck,trimControlPlot,mountControlTrim} from "../webapp/static/control-trim.js";

const copy=value=>JSON.parse(JSON.stringify(value));
const contract=(taskId="task-1")=>({schema_version:1,binding:{task_id:taskId,hardware_id:"hardware-1"},reference_thrust:{value_N:1000000,source_path:"calculation.summary.thrust_N",basis:"saved_concept_output",ambient_pressure_Pa:101325,validated_fixed_hardware:false},available_controls:[],defaults:{thrust_percent:100,thrust_relative_tolerance:.001},readiness:{can_run:false,blockers:[{code:"inner_model_unavailable",label:"待接入内层"}]}});
const definition=(taskId="task-1",percent=40)=>({id:"trim-1",task_id:taskId,created:1,definition:{mode:"fixed_hardware_steady_trim",scope:"task_definition_only",binding:{task_id:taskId},reference_thrust:contract(taskId).reference_thrust,thrust_percent:percent,thrust_relative_tolerance:.001,additional_targets:{},controls:[]}});
const report=()=>({schema_version:1,analysis_type:"control_trim_framework_check",scope:"numerical_framework_only",is_engine_result:false,model_id:"dimensionless",status:"converged",reason:"target_met",message:"代数目标满足",nfev:2,dof:{target_count:1,control_count:1,rank:1,independence:"full_row_rank"},controls:[{name:"u",label:"无量纲 u",unit:"1",lower:0,upper:1}],targets:[{name:"z",label:"无量纲输出 z",unit:"1",value:2,tolerance:.001}],points:[{iteration:0,controls:{u:.2},outputs:{z:1},residuals:{z:-1000},residual_norm:1000,inner_evaluation_id:1,active_bounds:[]},{iteration:1,controls:{u:1},outputs:{z:2},residuals:{z:0},residual_norm:0,inner_evaluation_id:2,active_bounds:[{name:"u",bound:"upper"}]}],evaluations:[{id:1,phase:"initial",controls:{u:.2},status:"valid",inner_status:"pass",inner_iterations:1,inner_residual_norm:0,inner_points:[{iteration:0,residual_norm:100},{iteration:1,residual_norm:0}]},{id:2,phase:"trial",controls:{u:1},status:"valid",inner_status:"pass",inner_iterations:1,inner_residual_norm:0,inner_points:[{iteration:0,residual_norm:200},{iteration:1,residual_norm:0}]}],limitations:["非发动机结果"]});
const check=(taskId="task-1",id="check-1")=>({id,task_id:taskId,created:2,report:report()});
const context=(taskId="task-1",projectId="p1")=>({projectId,taskId,baseUrl:`/api/projects/${projectId}/fixed-steady/tasks/${taskId}`});
const click=(host,action)=>host.onclick({target:{closest:()=>({dataset:{trimAction:action}})}});
const change=(host,data,value,checked)=>host.onchange({target:{dataset:data,value,checked}});
const input=(host,data,value)=>host.oninput({target:{dataset:data,value}});

test("trim target defaults never copy concept temperature and requests have a strict allowlist",()=>{
  const c=contract();c.defaults.additional_targets={ox_preburner_temperature:{value:750,tolerance:1}};c.hardware={fake:"do not send"};
  const before=copy(c),form=controlTrimForm(c);assert.equal(form.additional_targets.ox_preburner_temperature.enabled,false);assert.equal(form.additional_targets.ox_preburner_temperature.value,"");
  form.thrust_percent="40";form.concept_temperature=750;
  assert.deepEqual(buildControlTrimRequest(form,c),{thrust_percent:40,thrust_relative_tolerance:.001,additional_targets:{},controls:[]});assert.deepEqual(c,before);
  form.additional_targets.overall_of.enabled=true;assert.throws(()=>buildControlTrimRequest(form,c),/有限数值/);
  form.additional_targets.overall_of.value="3.5";assert.deepEqual(buildControlTrimRequest(form,c).additional_targets,{overall_of:{value:3.5,tolerance:.01}});
  for(const value of ["",0,-1,"Infinity"]){form.thrust_percent=value;assert.throws(()=>buildControlTrimRequest(form,c));}
});

test("only explicitly selected registered controls can enter a bounded trim definition",()=>{
  const c=contract();c.available_controls=[{valve_id:"v1",label:"已登记阀",lower:.1,upper:.9,initial:.5}];
  const form=controlTrimForm(c);assert.deepEqual(buildControlTrimRequest(form,c).controls,[]);
  form.controls.v1.enabled=true;assert.deepEqual(buildControlTrimRequest(form,c).controls,[{valve_id:"v1",lower:.1,upper:.9,initial:.5}]);
  form.controls.v1.upper="1";assert.throws(()=>buildControlTrimRequest(form,c),/登记边界/);form.controls.v1.upper=".9";
  form.controls.v1.initial=".05";assert.throws(()=>buildControlTrimRequest(form,c),/登记边界/);form.controls.v1.initial=".5";
  form.controls.secret={enabled:true,lower:0,upper:1,initial:.5};assert.throws(()=>buildControlTrimRequest(form,c),/未在当前硬件/);
});

test("definition explains the unvalidated reference, empty valves and control independence",()=>{
  const c=contract(),form=controlTrimForm(c);form.thrust_percent="40";
  const html=renderControlTrimDefinition(c,form);assert.match(html,/来源设计点已保存的计算推力输出/);assert.match(html,/尚未验证为固定硬件额定点/);
  assert.match(html,/400 kN/);assert.match(html,/尚未登记可操纵阀门/);assert.match(html,/等式目标 <b>1/);assert.match(html,/可调控制量 <b>0/);assert.match(html,/独立性 <b>尚未验证/);
  assert.match(html,/<button type="button" disabled[^>]*>发动机稳态配平/);assert.doesNotMatch(html,/data-trim-action="run"|<svg|750|850/);
  form.additional_targets.overall_of.enabled=true;form.additional_targets.ox_preburner_temperature.enabled=true;assert.match(renderControlTrimDefinition(c,form),/等式目标 <b>3/);
  c.available_controls=[{valve_id:'<script>x</script>',label:'<img src=x>',lower:0,upper:1,initial:.5}];assert.doesNotMatch(renderControlTrimDefinition(c,controlTrimForm(c)),/<script|<img/);
});

test("save action and no-result notice remain outside the collapsed blocker details",()=>{
  const html=renderControlTrimDefinition(contract(),controlTrimForm(contract()));
  let depth=0,saveSeen=false,noticeSeen=false;
  for(const token of html.matchAll(/<details\b[^>]*>|<\/details>|<button\b[^>]*data-trim-action="save"[^>]*>|<p\b[^>]*class="ct-no-results"[^>]*>/g)){
    if(token[0].startsWith("<details"))depth++;
    else if(token[0]==="</details>")depth--;
    else {assert.equal(depth,0,"an action or result notice must not be hidden inside collapsed details");if(token[0].startsWith("<button"))saveSeen=true;else noticeSeen=true;}
  }
  assert.equal(depth,0);assert.ok(saveSeen);assert.ok(noticeSeen);
});

test("self-check scope must be explicit and no missing trace is synthesized",()=>{
  for(const forged of [{scope:"engine"},{is_engine_result:true},{is_engine_result:undefined},{analysis_type:"fixed_hardware_steady_trim"}]){
    const html=renderControlTrimCheck({...report(),...forged});assert.match(html,/类型不匹配/);assert.doesNotMatch(html,/<svg/);
  }
  const r=report();r.points=[];r.evaluations=[];r.reason='<script>x</script>';const html=renderControlTrimCheck(r);assert.match(html,/没有外层接受步/);assert.match(html,/没有内层迭代轨迹/);assert.doesNotMatch(html,/<svg|<script/);
});

test("saved self-check traces draw outer error, bounded controls and separate inner histories",()=>{
  const r=report(),before=copy(r),html=renderControlTrimCheck(r,{downloadUrl:"/api/check"});
  assert.equal((html.match(/<svg /g)||[]).length,3);assert.match(html,/不使用上方推力百分比/);assert.match(html,/不证明真实内层模型已经稳定/);assert.match(html,/ct-bound/);assert.match(html,/>-1000</);assert.match(html,/u · 上限/);assert.match(html,/href="\/api\/check"/);assert.deepEqual(r,before);
  r.status="numerical_failure";assert.match(renderControlTrimCheck(r),/不能据此判定目标不可达/);
  r.status="target_not_met";assert.match(renderControlTrimCheck(r),/可达性未定/);
  r.status="target_unreachable";assert.match(renderControlTrimCheck(r),/缺少证据/);r.evidence={scope:"algebra"};assert.match(renderControlTrimCheck(r),/有边界证据/);
});

test("control plot preserves gaps and signs and shows only recorded bounds",()=>{
  const html=trimControlPlot([{iteration:0,controls:{u:-.5}},{iteration:1,controls:{u:null}},{iteration:2,controls:{u:.5}}],[{name:"u",label:"u",lower:-1,upper:1}]);
  const d=html.match(/<path[^>]* d="([^"]*)"/)[1];assert.equal((d.match(/M/g)||[]).length,2);assert.doesNotMatch(d,/L/);assert.match(html,/缺失 \/ 无效值 1 个/);assert.match(html,/-0.5/);
});

test("definitions are saved independently, history stays readonly and local draft survives",async()=>{
  const host={innerHTML:""},calls=[];const view=mountControlTrim(host,{api:async(path,method,body)=>{
    calls.push({path,method,body});if(path.endsWith("/setup"))return contract();if(path.endsWith("/definitions"))return method==="POST"?definition():[];return [];
  }});
  await view.setContext(context());input(host,{trimField:"thrust_percent"},"40");await click(host,"save");assert.match(host.innerHTML,/目标和阀位范围只读/);assert.match(host.innerHTML,/trim_id=trim-1/);
  assert.deepEqual(calls.find(call=>call.method==="POST").body,{thrust_percent:40,thrust_relative_tolerance:.001,additional_targets:{},controls:[]});
  input(host,{trimField:"thrust_percent"},"99");await click(host,"save");assert.equal(calls.filter(call=>call.method==="POST").length,1);
  change(host,{trimDefinition:""},"");assert.match(host.innerHTML,/data-trim-field="thrust_percent" value="40"/);assert.ok(calls.every(call=>!call.path.includes("throttles")&&!call.path.endsWith("/run")));
});

test("saved definition remains readable and exportable when current setup cannot be rebuilt",async()=>{
  const host={innerHTML:""},saved=definition("task-1",55),calls=[];
  saved.definition.reference_thrust.value_N=2000000;
  const before=copy(saved),view=mountControlTrim(host,{api:async(path,method,body)=>{
    calls.push({path,method,body});if(path.endsWith("/setup"))throw new Error("来源记录已不可用");if(path.endsWith("/definitions"))return [saved];return [];
  }});
  await view.setContext(context());assert.match(host.innerHTML,/来源记录已不可用/);
  change(host,{trimDefinition:""},"trim-1");
  assert.match(host.innerHTML,/data-trim-field="thrust_percent" value="55" disabled/);
  assert.match(host.innerHTML,/2000 kN/);assert.match(host.innerHTML,/1100 kN/);assert.match(host.innerHTML,/trim_id=trim-1/);
  assert.match(host.innerHTML,/目标和阀位范围只读/);assert.doesNotMatch(host.innerHTML,/data-trim-action="save"/);
  input(host,{trimField:"thrust_percent"},"99");await click(host,"save");assert.deepEqual(saved,before);
  change(host,{trimDefinition:""},"");await click(host,"save");
  assert.doesNotMatch(host.innerHTML,/data-trim-action="save"/);assert.ok(calls.every(call=>call.method!=="POST"));
});

test("typing or blurring numeric targets keeps the form DOM so the subsequent save click is retained",async()=>{
  let markup="",renders=0;const summary={textContent:""},calls=[];
  const host={get innerHTML(){return markup;},set innerHTML(value){markup=value;renders++;},querySelector:selector=>selector===".ct-target-value b"?summary:null};
  const view=mountControlTrim(host,{api:async(path,method,body)=>{
    calls.push({path,method,body});if(path.endsWith("/setup"))return contract();if(path.endsWith("/definitions"))return method==="POST"?definition():[];return [];
  }});
  await view.setContext(context());const before=renders;
  input(host,{trimField:"thrust_percent"},"40");assert.equal(summary.textContent,"400 kN");
  change(host,{trimField:"thrust_percent"},"40");
  input(host,{trimField:"thrust_relative_tolerance"},".002");change(host,{trimField:"thrust_relative_tolerance"},".002");
  assert.equal(renders,before,"typing and blur must not replace the input or save button");
  input(host,{trimField:"thrust_percent"},"");assert.equal(summary.textContent,"— kN");
  input(host,{trimField:"thrust_percent"},"40");await click(host,"save");
  assert.deepEqual(calls.find(call=>call.method==="POST").body,{thrust_percent:40,thrust_relative_tolerance:.002,additional_targets:{},controls:[]});
});

test("demo submissions contain only the chosen scenario and preserve prior evidence on failure",async()=>{
  const host={innerHTML:""},calls=[];let fail=false;
  const view=mountControlTrim(host,{api:async(path,method,body)=>{
    calls.push({path,method,body});if(path.endsWith("/setup"))return contract();if(method==="POST"){if(fail)throw new Error("self check unavailable");return check();}return [];
  }});
  await view.setContext(context());input(host,{trimField:"thrust_percent"},"40");change(host,{trimScenario:""},"inner_failure");await click(host,"check");
  assert.deepEqual(calls.at(-1),{path:"/projects/p1/fixed-steady/tasks/task-1/trim/checks",method:"POST",body:{scenario:"inner_failure"}});
  assert.match(host.innerHTML,/check_id=check-1/);fail=true;await click(host,"check");assert.match(host.innerHTML,/self check unavailable/);assert.match(host.innerHTML,/check_id=check-1/);assert.match(host.innerHTML,/发动机稳态配平 · 尚未开放/);
});

test("no saved fixed task means no API calls or demo run",async()=>{
  const host={innerHTML:""},view=mountControlTrim(host,{api:async()=>{throw new Error("unexpected API");}});
  await view.setContext(context(""));assert.match(host.innerHTML,/先保存并选择/);await click(host,"check");
});

test("foreign histories are omitted and same-task host replacement preserves form and in-flight check",async()=>{
  const host={innerHTML:""},replacement={innerHTML:""};let release;const pending=new Promise(resolve=>release=resolve);
  const view=mountControlTrim(host,{api:async(path,method)=>{
    if(path.endsWith("/setup"))return contract();if(path.endsWith("/definitions"))return [definition("other")];if(method==="POST")return pending;return [check("other","foreign")];
  }});
  await view.setContext(context());assert.doesNotMatch(host.innerHTML,/foreign|trim_id=trim-1/);input(host,{trimField:"thrust_percent"},"60");const running=click(host,"check");await view.setContext(context(),replacement);release(check());await running;
  assert.match(replacement.innerHTML,/data-trim-field="thrust_percent" value="60"/);assert.match(replacement.innerHTML,/check_id=check-1/);
});

test("late checks cannot overwrite another task or project",async()=>{
  const host={innerHTML:""};let release;const pending=new Promise(resolve=>release=resolve);
  const view=mountControlTrim(host,{api:async(path,method)=>{
    if(path.endsWith("/setup"))return contract(path.includes("task-2")?"task-2":"task-1");if(method==="POST")return pending;return [];
  }});
  await view.setContext(context());const running=click(host,"check");await view.setContext(context("task-2","p2"));release(check());await running;
  assert.match(host.innerHTML,/当前固定任务：task-2/);assert.doesNotMatch(host.innerHTML,/check_id=check-1|无量纲目标已满足/);
});

test("unmount invalidates pending requests and leaves cannot issue additional work",async()=>{
  const host={innerHTML:""};let release,calls=0;const pending=new Promise(resolve=>release=resolve);
  const view=mountControlTrim(host,{api:async(path,method)=>{calls++;if(path.endsWith("/setup"))return contract();return method==="POST"?pending:[];}});
  await view.setContext(context());const running=click(host,"check");view.onLeave();const before=host.innerHTML;release(check());await running;assert.equal(host.innerHTML,before);await view.refresh();assert.equal(calls,4);
});
