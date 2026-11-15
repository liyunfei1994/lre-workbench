import assert from "node:assert/strict";
import test from "node:test";
import {buildProjectWorkflow, compareProjectInputs, renderProjectOverview,renderProjectContext,renderProjectDifferences,renderRecordProvenance} from "../webapp/static/project-overview.js";

const inputs = () => ({cycle_type:"ffsc",propellant_pair:"lox_methane",thrust:1000000,pc:20000000,ambient_pressure:101325,of_ratio:3.5,gas_eos:"ideal-gas",methane_thermo_dataset:"gri30"});
const groups = [{id:"design",title:"设计目标",fields:[{key:"thrust"},{key:"pc"}]}, {id:"chamber_chemistry",title:"燃气热力学模型",fields:[{key:"gas_eos"},{key:"methane_thermo_dataset"}]}];
const fixture = () => {
  const draft = inputs();
  return {project:{id:"p1",name:"试验方案",revision:5,inputs:{...draft}},draft,groups,
    record:{id:"r1",project_id:"p1",revision:4,status:"succeeded",inputs:{...draft},result:{calculation:{inputs:{...draft},summary:{thrust_N:1010000,Isp_s:310,Isp_vac_s:350}},assessment:{status:"pass"}}}};
};

test("compact context distinguishes dirty metadata, input equality and execution from convergence",()=>{
  const data=fixture(),before=JSON.stringify(data),model=buildProjectWorkflow(data);
  const html=renderProjectContext(model,{dirty:true,differencesOpen:true});
  assert.match(html,/当前输入 <b>v5/);assert.match(html,/所选记录 v4 · 计算已完成/);
  assert.match(html,/有未保存修改/);assert.match(html,/输入与记录一致/);
  assert.match(html,/<details class="project-input-differences" open>/);
  assert.doesNotMatch(html,/完整数值收敛|data-evidence=|SHA-256/);
  assert.equal(JSON.stringify(data),before);
});

test("difference details use field units and preserve missing historical evidence",()=>{
  const data=fixture();data.groups[0].fields[1]={key:"pc",label:"室压",unit:"MPa",factor:1e-6};
  data.draft.pc=25e6;delete data.record.inputs.thrust;
  const html=renderProjectDifferences(buildProjectWorkflow(data));
  assert.match(html,/室压 <small>\[MPa\]/);assert.match(html,/<pre>25<\/pre>/);assert.match(html,/<pre>20<\/pre>/);
  assert.match(html,/data-readiness-field="pc"/);assert.match(html,/未记录/);
  assert.match(html,/不使用当前默认值补齐/);
});

test("model provenance belongs only to the selected immutable record and is folded",()=>{
  const data=fixture();data.draft.methane_thermo_dataset="new-draft-dataset";
  data.record.result.environment={gas_model:{mechanism:"old-file.yaml",mechanism_sha256:"abc123",cantera_version:"old-version"}};
  const model=buildProjectWorkflow(data),html=renderRecordProvenance(model);
  assert.match(html,/设计点计算 · 方案 v4/);assert.match(html,/old-file.yaml/);assert.match(html,/abc123/);
  assert.doesNotMatch(html,/new-draft-dataset|<details[^>]+ open/);
  assert.match(renderRecordProvenance(model,{open:true,recordOpen:true}),/class="project-model-provenance" open/);
  data.record.project_id="foreign";
  assert.doesNotMatch(renderRecordProvenance(buildProjectWorkflow(data)),/old-file.yaml|abc123/);
});

test("summary and differences escape free text and never duplicate the full dashboard",()=>{
  const data=fixture();data.project.description='<img src=x onerror="bad()">';data.draft.gas_eos='<script>bad()</script>';
  const model=buildProjectWorkflow(data),html=renderProjectOverview(model)+renderProjectDifferences(model);
  assert.match(html,/&lt;img/);assert.match(html,/&lt;script/);
  assert.doesNotMatch(html,/<img|<script|project-evidence-grid|project-cycle-preview|SHA-256/);
});

test("metadata-only revision changes do not mark results stale", () => {
  const data = fixture();
  data.project.name = "只修改名称";
  const before = JSON.stringify(data.record);
  const model = buildProjectWorkflow(data);
  assert.equal(model.freshness.state,"current");
  assert.equal(model.record.revision,4);
  assert.equal(model.project.revision,5);
  assert.equal(JSON.stringify(data.record),before);
  assert.equal(model.metrics[0].delta,10);
});

test("changed model inputs identify settings and downstream refresh without removing old results", () => {
  const data = fixture();data.draft.gas_eos="Peng-Robinson";
  const model = buildProjectWorkflow(data);
  assert.equal(model.freshness.state,"stale");
  assert.deepEqual(model.freshness.changedKeys,["gas_eos"]);
  assert.equal(model.freshness.changedGroups[0].id,"chamber_chemistry");
  assert.equal(model.metrics[0].value,1010);
  assert.ok(model.issues.some(issue => issue.group === "chamber_chemistry"));
  const html = renderProjectContext(model);
  assert.match(html,/旧结果保留/);
  assert.match(html,/data-readiness-group="chamber_chemistry"/);
  assert.match(html,/依赖该设计点的部件初选与运行分析需要重新核对/);
});

test("thermochemical data changes belong to their model group alongside other changed inputs", () => {
  for(const contextAsArray of [true,false]){
    const data=fixture(),context={key:"methane_thermo_dataset",label:"热化学数据集"};
    data.groups=[data.groups[0],{id:"chamber_chemistry",title:"燃烧热化学与燃气物性",fields:[{key:"gas_eos"}],
      configuration_context:contextAsArray?[context]:context}];
    data.draft.pc=25e6;data.draft.methane_thermo_dataset="gri30_highT";
    const before=JSON.stringify(data),model=buildProjectWorkflow(data);
    assert.equal(model.freshness.state,"stale");
    assert.deepEqual(model.freshness.changedKeys,["pc","methane_thermo_dataset"]);
    assert.deepEqual(model.freshness.changedGroups,[
      {id:"design",title:"设计目标",fields:["pc"]},
      {id:"chamber_chemistry",title:"燃烧热化学与燃气物性",fields:["methane_thermo_dataset"]},
    ]);
    assert.ok(model.issues.some(issue=>issue.group==="chamber_chemistry"&&issue.field==="methane_thermo_dataset"));
    assert.equal(JSON.stringify(data),before,"change attribution must not rewrite input snapshots or model metadata");
  }
});

test("configuration context attribution keeps real input keys unique and preserves missing historical evidence", () => {
  const data=fixture();
  data.groups[1]={...data.groups[1],configuration_context:[{key:"methane_thermo_dataset"}]};
  data.draft.methane_thermo_dataset="gri30_highT";
  const comparison=compareProjectInputs(data.draft,data.record.inputs,data.groups);
  assert.deepEqual(comparison.changedGroups,[{id:"chamber_chemistry",title:"燃气热力学模型",fields:["methane_thermo_dataset"]}]);
  data.groups[1].fields=[{key:"gas_eos"}];
  delete data.record.inputs.methane_thermo_dataset;
  const model=buildProjectWorkflow(data);
  assert.equal(model.freshness.state,"unknown");
  assert.deepEqual(model.freshness.changedGroups,[]);
  assert.deepEqual(model.freshness.missingKeys,["methane_thermo_dataset"]);
});

test("input comparison is recursive and ignores object key ordering", () => {
  assert.equal(compareProjectInputs({valves:{a:1,b:2}},{valves:{b:2,a:1}}).changedKeys.length,0);
  assert.deepEqual(compareProjectInputs({valves:[1,2]},{valves:[2,1]}).changedKeys,["valves"]);
  assert.deepEqual(compareProjectInputs({v:NaN},{v:null}).changedKeys,["v"]);
});

test("normalizing historical defaults cannot establish complete saved input evidence", () => {
  const data=fixture();delete data.record.inputs.methane_thermo_dataset;
  assert.equal(buildProjectWorkflow(data).freshness.state,"unknown");
  const model=buildProjectWorkflow({...data,normalizedRecordInputs:{...inputs(),...data.record.inputs}});
  assert.equal(model.freshness.state,"unknown");
  assert.deepEqual(model.freshness.missingKeys,["methane_thermo_dataset"]);
  assert.deepEqual(model.freshness.changedKeys,[]);
  assert.equal(model.savedModels.find(item=>item.label === "热化学数据集").value,"未记录");
});

const supplyKeys=["ox","fuel"].flatMap(side=>[`${side}_tank_pressure_pa`,`${side}_boost_outlet_pressure_pa`,`${side}_feed_line_dp_pa`,`${side}_boost_pump_efficiency`]);
const legacySupply=()=>({tank_pressure_pa:3e5,inlet_pressure:1e6,feed_line_dp_pa:5e4,boost_pump_efficiency:.7});
function supplyFixture(){
  const data=fixture();Object.assign(data.record.inputs,legacySupply());
  data.draft={...data.record.inputs,...Object.fromEntries(supplyKeys.map(key=>[key,null]))};
  data.groups=[...data.groups,{id:"inlet",title:"推进剂供给条件",fields:supplyKeys.map(key=>({key}))}];
  return data;
}
test("new nullable supply fields preserve legacy input identity without changing saved evidence",()=>{
  const data=supplyFixture(),before=JSON.stringify(data),model=buildProjectWorkflow(data);
  assert.equal(model.freshness.state,"current");assert.deepEqual(model.freshness.missingKeys,[]);
  assert.deepEqual(model.freshness.changedKeys,[]);assert.equal(JSON.stringify(data),before);
  const reversed=compareProjectInputs(data.record.inputs,data.draft,data.groups);
  assert(reversed.complete);assert.deepEqual(reversed.changedKeys,[]);
});
test("a new side supply override is compared with its own saved shared boundary",()=>{
  const data=supplyFixture();data.draft.ox_tank_pressure_pa=4e5;
  const model=buildProjectWorkflow(data);
  assert.equal(model.freshness.state,"stale");assert.deepEqual(model.freshness.changedKeys,["ox_tank_pressure_pa"]);
  assert.deepEqual(model.freshness.changedGroups,[{id:"inlet",title:"推进剂供给条件",fields:["ox_tank_pressure_pa"]}]);
  assert(model.issues.some(issue=>issue.group==="inlet"));
  data.draft.ox_tank_pressure_pa=3e5;
  assert.equal(buildProjectWorkflow(data).freshness.state,"current","an explicit equal boundary does not change calculation input");
  data.draft.fuel_feed_line_dp_pa=0;
  assert.deepEqual(buildProjectWorkflow(data).freshness.changedKeys,["fuel_feed_line_dp_pa"],"zero loss is an explicit override");
});
test("missing historical supply fallback remains unknown for both independent branches",()=>{
  const data=supplyFixture();delete data.record.inputs.tank_pressure_pa;
  let model=buildProjectWorkflow(data);
  assert.equal(model.freshness.state,"unknown");assert.deepEqual(model.freshness.changedKeys,[]);
  for(const side of ["ox","fuel"])assert(model.freshness.missingKeys.includes(`${side}_tank_pressure_pa`));
  data.draft.ox_tank_pressure_pa=4e5;
  model=buildProjectWorkflow(data);assert.equal(model.freshness.state,"unknown","current draft cannot supply missing historical evidence");
  assert.deepEqual(model.freshness.changedKeys,[]);
  const incomplete={cycle_type:"ffsc",ox_tank_pressure_pa:null};
  assert.equal(compareProjectInputs(incomplete,incomplete).complete,false,"two unresolved nulls do not prove a supplied boundary");
});
test("invalid explicit supply values cannot inherit a matching historical boundary",()=>{
  for(const value of ["",NaN,Infinity,-1]){
    const data=supplyFixture();data.draft.fuel_boost_outlet_pressure_pa=value;
    const model=buildProjectWorkflow(data);
    assert.equal(model.freshness.state,"stale");
    assert.deepEqual(model.freshness.changedKeys,["fuel_boost_outlet_pressure_pa"]);
  }
});

test("empty or non-object snapshots remain unknown even when caller supplies complete defaults", () => {
  for (const snapshot of [{},[],"invalid",42]) {
    const data=fixture();data.record.inputs=snapshot;
    const model=buildProjectWorkflow({...data,normalizedRecordInputs:inputs()});
    assert.equal(model.freshness.state,"unknown");
    assert.equal(model.freshness.complete,false);
    assert.equal(model.freshness.changedKeys.length,0);
    assert.equal(model.freshness.missingKeys.length,Object.keys(data.draft).length);
  }
});

test("partial snapshots distinguish unknown fields from real differences", () => {
  const data=fixture();data.record.inputs={gas_eos:"ideal-gas"};
  const matching=buildProjectWorkflow(data);
  assert.equal(matching.freshness.state,"unknown");
  assert.deepEqual(matching.freshness.changedKeys,[]);
  assert.ok(matching.freshness.missingKeys.includes("thrust"));
  data.draft.gas_eos="Peng-Robinson";
  const changed=buildProjectWorkflow(data);
  assert.equal(changed.freshness.state,"stale");
  assert.deepEqual(changed.freshness.changedKeys,["gas_eos"]);
  assert.equal(changed.freshness.complete,false);
  assert.match(changed.freshness.reason,/当前输入未记录/);
});

test("all pending group confirmations appear as actionable overview work", () => {
  const data=fixture();
  data.readiness={phase:"review",pendingReviewCount:14,issues:[],groups:Array.from({length:14},(_,index)=>({id:`group_${index}`,title:`参数组 ${index + 1}`,status:"review"}))};
  const model=buildProjectWorkflow(data),html=renderProjectOverview(model);
  assert.equal(model.issues.filter(issue=>issue.kind === "review").length,14);
  assert.match(html,/data-readiness-group="group_0"/);
  assert.doesNotMatch(html,/data-readiness-group="group_13"/,"the full checklist belongs to input review");
  assert.doesNotMatch(html,/当前未列出阻断问题/);
  assert.equal(model.dimensions[0].detail,"14 个参数分组待确认。");
});

test("overview routes computation work through the dedicated review page", () => {
  for (const state of ["missing", "current", "stale", "invalid"]) {
    const data=fixture();
    data.readiness={phase:"ready",pendingReviewCount:0,groups:[],issues:[]};
    if(state==="missing")data.record=null;
    if(state==="stale")data.draft.pc=25000000;
    if(state==="invalid")data.record.result.environment={gas_model:{eos:"Peng-Robinson-Boston-Mathias",eos_model:{version:"pr_boston_mathias_v1"}}};
    const before=JSON.stringify(data),model=buildProjectWorkflow(data),html=renderProjectOverview(model);
    assert.equal(model.freshness.state,state);
    assert.equal(model.actions[0].view,"design_run",state);
    assert.match(model.actions[0].label,/前往.*检查/);
    assert.match(html,/data-workspace-view="design_run"/);
    assert.doesNotMatch(html,/data-workspace-action="run"|id="(?:header-run|submit-job|design-run-submit)"/);
    assert.equal(JSON.stringify(data),before,"rendering the next step cannot mutate or submit the current draft");
  }
});

test("input evidence opens the full review while a specific unresolved group remains directly editable", () => {
  for(const phase of ["ready","invalid","review","pending","unavailable"]){
    const data=fixture();
    data.readiness={phase,groups:[],issues:[{fields:["unmapped_field"],message:"跨组检查未完成"}]};
    const model=buildProjectWorkflow(data);
    assert.equal(model.dimensions.find(item=>item.id==="inputs").view,"design_run",phase);
    assert.equal(model.issues.find(item=>item.message==="跨组检查未完成").view,"design_run",phase);
  }
  const data=fixture();
  data.readiness={phase:"review",pendingReviewCount:1,issues:[],groups:[{id:"design",title:"设计目标",status:"review"}]};
  const model=buildProjectWorkflow(data);
  assert.equal(model.actions[0].group,"design");
  assert.equal(model.actions[0].view,undefined);
  assert.match(model.actions[0].label,/继续设置/);
});

test("job success does not imply convergence, constraint acceptance or model validation", () => {
  const model=buildProjectWorkflow(fixture());
  for(const dimension of model.dimensions) assert.notEqual(dimension.state,"pass");
  assert.equal(model.dimensions.find(item=>item.id === "evidence").label,"待验证");
});

test("input readiness and current-scope constraint checks are independent", () => {
  const data=fixture();
  data.readiness={phase:"ready",groups:[],issues:[],pendingReviewCount:0};
  data.record.result.calculation_scope={version:3};
  data.record.result.assessment={scope:"system",status:"pass",checks:[{key:"temperature",status:"pass"}]};
  const model=buildProjectWorkflow(data);
  assert.equal(model.dimensions.find(item=>item.id === "inputs").state,"pass");
  assert.equal(model.dimensions.find(item=>item.id === "constraints").state,"pass");
  assert.notEqual(model.dimensions.find(item=>item.id === "numerical").state,"pass");
  data.record.result.assessment.checks.push({key:"ox_preburner_T_combustion_K",status:"pass",limit:800});
  assert.equal(buildProjectWorkflow(data).dimensions.find(item=>item.id === "constraints").state,"unknown");
});

test("missing inputs, foreign records and empty projects remain honest", () => {
  const data=fixture();delete data.record.inputs;delete data.record.result.calculation.inputs;
  assert.equal(buildProjectWorkflow(data).freshness.state,"unknown");
  data.record.project_id="another";
  const foreign=buildProjectWorkflow(data);
  assert.equal(foreign.freshness.state,"foreign");
  assert.equal(foreign.record,null);
  assert.equal(foreign.metrics[0].value,null);
  const empty=buildProjectWorkflow();
  assert.equal(empty.freshness.state,"missing");
  assert.doesNotThrow(()=>renderProjectOverview(empty));
});

test("legacy result-level inputs remain usable as an immutable snapshot", () => {
  const data=fixture();delete data.record.inputs;delete data.record.result.calculation.inputs;
  data.record.result.inputs=inputs();
  assert.equal(buildProjectWorkflow(data).freshness.state,"current");
  data.draft.pc=25000000;
  assert.equal(buildProjectWorkflow(data).freshness.state,"stale");
  assert.equal(data.record.result.inputs.pc,20000000);
});

test("environment changes suppress a misleading thrust-target delta", () => {
  const data=fixture();data.draft.ambient_pressure=0;
  const metric=buildProjectWorkflow(data).metrics[0];
  assert.equal(metric.value,1010);
  assert.equal(metric.delta,null);
  assert.match(metric.note,/不直接计算目标差值/);
});

test("withdrawn models retain original output with explicit invalid evidence", () => {
  const data=fixture();data.record.result.environment={gas_model:{eos:"Peng-Robinson-Boston-Mathias",eos_model:{version:"pr_boston_mathias_v1"}}};
  const model=buildProjectWorkflow(data);
  assert.equal(model.freshness.state,"invalid");
  assert.equal(model.metrics[0].value,1010);
  assert.equal(model.dimensions.find(item=>item.id === "evidence").state,"fail");
  assert.match(renderProjectContext(model),/模型已撤回/);
  assert.match(renderRecordProvenance(model),/pr_boston_mathias_v1/);
});

test("readiness issues link to fields, and user values are escaped", () => {
  const data=fixture();data.project.name='<img src=x onerror="bad()">';
  data.readiness={phase:"invalid",issues:[{fields:["pc"],message:"请填写压力"}],groups:[{id:"design",title:"设计目标",status:"invalid"}]};
  const model=buildProjectWorkflow(data),html=renderProjectOverview(model);
  assert.equal(model.issues[0].group,"design");
  assert.equal(model.issues[0].field,"pc");
  assert.match(html,/data-readiness-group="design"/);
  assert.match(html,/&lt;img/);
  assert.doesNotMatch(html,/<img src=x/);
  assert.doesNotMatch(html,/project-evidence-grid|project-cycle-preview|SHA-256/);
});

test("configuration summary uses the draft while detailed diagram retains saved source", () => {
  const data=fixture();
  data.record.inputs.cycle_type="historical_cycle";
  const before=JSON.stringify(data);
  const html=renderProjectOverview(buildProjectWorkflow(data));
  assert.match(html,/液氧／甲烷/);
  assert.match(html,/全流量补燃循环（FFSC）/);
  assert.match(html,/data-readiness-group="design">发动机定义与设计工况/);
  assert.doesNotMatch(html,/historical_cycle|project-cycle-preview|详细循环图来源/);
  assert.equal(JSON.stringify(data),before);
});

test("missing or unsupported draft identities cannot fabricate a current FFSC diagram", () => {
  const data=fixture();
  delete data.draft.cycle_type;
  const missing=renderProjectOverview(buildProjectWorkflow(data));
  assert.match(missing,/<dt>循环形式<\/dt><dd>未记录/);
  assert.doesNotMatch(missing,/class="project-cycle-preview"/);
  data.draft.cycle_type='<script>foreign</script>';
  const unsupported=renderProjectOverview(buildProjectWorkflow(data));
  assert.doesNotMatch(unsupported,/全流量补燃循环（FFSC）/);
  assert.match(unsupported,/&lt;script&gt;foreign&lt;\/script&gt;/);
  assert.doesNotMatch(unsupported,/<script>|class="project-cycle-preview"/);
  const empty=renderProjectOverview(buildProjectWorkflow());
  assert.doesNotMatch(empty,/class="project-cycle-preview"|查看保存记录的详细循环图/);
});
