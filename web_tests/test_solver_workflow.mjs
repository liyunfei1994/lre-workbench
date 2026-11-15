import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {createHash} from "node:crypto";
import {fileURLToPath} from "node:url";
import test from "node:test";
import {parameterGroups,parameterPresentation,renderParameterGroups,preburnerMode} from "../webapp/static/parameter-editor.js";
import {buildDesignReadiness,collectLocalReadiness,renderDesignGroupReadiness} from "../webapp/static/design-readiness.js";
import {findDesignParameters} from "../webapp/static/parameter-finder.js";
import {migrateWorkflowReview} from "../webapp/static/parameter-review.js";
import {renderSolverTask} from "../webapp/static/solver-workspace.js";
import {setupNavigationGroup,setupSection} from "../webapp/static/setup-tree.js";

const output=spawnSync("python",["-X","utf8","-c","import sys,json; sys.path.insert(0,'src'); from webapp.ui_schema import design_ui_schema; print(json.dumps(design_ui_schema()))"],{cwd:fileURLToPath(new URL("../",import.meta.url)),encoding:"utf8",timeout:20000});
assert.equal(output.status,0,output.stderr);
const schema=JSON.parse(output.stdout),original=JSON.stringify(schema),base=schema.defaults;
const modes=[{mode:"temperature",use_target_preburner_temp:1,use_target_preburner_mr:1},
  {mode:"mixture_ratio",use_target_preburner_temp:0,use_target_preburner_mr:1},
  {mode:"legacy_free_split",use_target_preburner_temp:0,use_target_preburner_mr:0}];
const owner=(groups,key)=>groups.filter(g=>g.fields.some(f=>f.key===key)).map(g=>g.id);

test("engine definition is the first single editor with readonly identity and editable design conditions",()=>{
  const groups=parameterGroups(schema.system_groups,base),before=JSON.stringify(base),first=groups[0];
  assert.equal(first.id,"design");assert.equal(first.title,"发动机定义与设计工况");
  assert.ok(first.engine_definition);assert.ok(!groups.some(group=>group.id==="cycle"));
  const html=renderParameterGroups([first],base);
  for(const key of ["cycle_type","propellant_pair"]){
    assert.deepEqual(owner(groups,key),["design"]);
    assert.match(html,new RegExp(`data-field="${key}" data-role="configuration"`));
    assert.doesNotMatch(html,new RegExp(`data-input="${key}"`));
  }
  for(const key of ["thrust","pc","of_ratio","ambient_pressure"])
    assert.equal((html.match(new RegExp(`data-input="${key}"`,"g"))||[]).length,1);
  assert.match(html,/循环原理与建模范围/);assert.doesNotMatch(html,/data-definition-help="engine-definition" open/);
  assert.match(renderParameterGroups([first],base,new Set(["engine-definition"])),/data-definition-help="engine-definition" open/);
  assert.equal(findDesignParameters(groups,base,"推进剂组合")[0].groupId,"design");
  assert.equal(findDesignParameters(groups,base,"推进剂组合")[0].path,"总体方案与循环设计 / 发动机定义与设计工况");
  assert.equal(JSON.stringify(base),before);
});

for(const flags of modes)test(`${flags.mode}: active roles have one editor and routing does not rewrite stored values`,()=>{
  const draft={...base,...flags},before=JSON.stringify(draft),groups=parameterGroups(schema.system_groups,draft);
  assert.equal(preburnerMode(draft),flags.mode);
  const fields=groups.flatMap(g=>g.fields);
  assert.equal(fields.length,new Set(fields.map(f=>f.key)).size);
  for(const field of fields){
    if(parameterPresentation(field,draft).role==="initial_guess")assert.deepEqual(owner(groups,field.key),["initialization"]);
  }
  assert.deepEqual(owner(groups,"target_isp"),["initialization"]);
  assert.deepEqual(owner(groups,"chamber_gamma"),["initialization"]);
  assert.deepEqual(owner(groups,"steady_ox_power_tolerance_W"),["solver"]);
  assert.deepEqual(owner(groups,"cooling_grid_points"),["solver"]);
  assert.deepEqual(owner(groups,"ox_preburner_pressure_ratio"),[flags.mode==="legacy_free_split"?"preburner_design":"initialization"]);
  if(flags.mode!=="legacy_free_split")assert.deepEqual(owner(groups,"ox_preburner_mr_target"),[flags.mode==="temperature"?"initialization":"preburner_design"]);
  assert.deepEqual(parameterGroups(groups,draft),groups,"projection is safe for render/search/readiness reuse");
  const html=renderParameterGroups(groups,draft);
  for(const key of ["target_isp","chamber_temp_guess","chamber_gamma","molar_mass","cooling_grid_points"])
    assert.equal((html.match(new RegExp(`data-input="${key}"`,"g"))||[]).length,1,key);
  assert.equal(JSON.stringify(draft),before);assert.equal(JSON.stringify(schema),original);
});

test("mode changes move search and validation to the active owner and preserve numeric edits",()=>{
  const edited={...base,ox_preburner_mr_target:42.5};
  for(const flags of modes.slice(0,2)){
    const draft={...edited,...flags},groups=parameterGroups(schema.system_groups,draft);
    const result=findDesignParameters(schema.system_groups,draft,"富氧 氧燃质量比").find(r=>r.fieldKey==="ox_preburner_mr_target");
    assert.ok(result);
    assert.equal(result.groupId,flags.mode==="temperature"?"initialization":"preburner_design");
    assert.match(result.path,flags.mode==="temperature"?/总体设计点计算 \/ 初始化/:/循环匹配条件/);
    const invalid={...draft,ox_preburner_mr_target:null},active=parameterGroups(schema.system_groups,invalid);
    const readiness=buildDesignReadiness(active,collectLocalReadiness(schema.system_groups,invalid).issues,{phase:"ready",groupReviews:new Map()});
    assert.equal(readiness.groups.find(g=>g.id===result.groupId).status,"invalid");
    assert.equal(readiness.globalIssues.length,0);
    assert.equal(groups.find(g=>g.id===result.groupId).fields.find(f=>f.key==="ox_preburner_mr_target").default,base.ox_preburner_mr_target);
    assert.equal(draft.ox_preburner_mr_target,42.5);
  }
});

test("valid numerical defaults and custom values need no engineering confirmation; invalid values still block",()=>{
  for(const delta of [{},{steady_ox_power_tolerance_W:2}]){
    const groups=parameterGroups(schema.system_groups,{...base,...delta});
    const ready=buildDesignReadiness(groups,[],{phase:"ready",groupReviews:new Map()});
    const solver=ready.groups.find(g=>g.id==="solver");
    assert.equal(solver.status,"ready");assert.equal(solver.requiresReview,false);
    assert.equal(solver.configurationLabel,Object.keys(delta).length?"已自定义":"采用默认");
    assert.equal(ready.groups.find(g=>g.id==="design").status,"review");
    assert.doesNotMatch(renderDesignGroupReadiness(ready,"solver"),/data-review-design-group/);
    const invalid=buildDesignReadiness(groups,[{fields:["steady_ox_power_tolerance_W"],message:"容差必须为正数"}],{phase:"ready",groupReviews:new Map()});
    assert.equal(invalid.validationPhase,"invalid");assert.equal(invalid.groups.find(g=>g.id==="solver").configurationLabel,null);
  }
});

const signatureFor=(g,inputs)=>JSON.stringify([schema.version,g.fields.map(f=>[f.key,f.virtual?preburnerMode(inputs):inputs[f.key]])]);
const digest=async value=>createHash("sha256").update(value).digest("hex");

test("optional limits have a separate editor and never create a mandatory review step",()=>{
  for(const status of ["unset","provisional","documented"]){
    const draft={...base,ox_preburner_temp_limit_status:status,ox_preburner_temp_limit:825,ox_preburner_temp_limit_basis:"试验 A"};
    const before=JSON.stringify(draft),groups=parameterGroups(schema.system_groups,draft);
    const main=groups.find(g=>g.id==="preburner_design"),limits=groups.find(g=>g.id==="engineering_constraints");
    assert.doesNotMatch(renderParameterGroups([main],draft),/data-input="(?:ox|fuel)_preburner_temp_limit/);
    assert.deepEqual(owner(groups,"ox_preburner_temp_limit_status"),["engineering_constraints"]);
    assert.equal(setupNavigationGroup(limits.id),"results");assert.equal(setupSection(limits.id).id,"constraints");
    const match=findDesignParameters(schema.system_groups,draft,"富氧预燃室温度限值依据状态")[0];
    assert.equal(match.groupId,limits.id);assert.equal(match.path,"校核与记录 / 工程限值与校核");
    const reviews=new Map(groups.filter(g=>!g.optional_configuration).map(g=>[g.id,"confirmed"]));
    const ready=buildDesignReadiness(groups,collectLocalReadiness(schema.system_groups,draft).issues,{phase:"ready",groupReviews:reviews});
    assert.equal(ready.phase,"ready");assert.equal(ready.pendingReviewCount,0);
    assert.equal(ready.groups.find(g=>g.id===limits.id).requiresReview,false);
    assert.equal(ready.groups.find(g=>g.id===limits.id).configurationLabel,status==="unset"?"未设置限值":"已启用 1 项限值");
    const html=renderParameterGroups([limits],draft);
    assert.equal(html.includes('data-input="ox_preburner_temp_limit"'),status!=="unset");
    assert.equal(JSON.stringify(draft),before);
  }
});

test("incomplete enabled limits still block and point to the optional limit editor",()=>{
  const draft={...base,ox_preburner_temp_limit_status:"provisional",ox_preburner_temp_limit:null};
  const groups=parameterGroups(schema.system_groups,draft);
  const model=buildDesignReadiness(groups,collectLocalReadiness(schema.system_groups,draft).issues,{phase:"ready",groupReviews:new Map()});
  assert.equal(model.validationPhase,"invalid");assert.equal(model.groups.find(g=>g.id==="engineering_constraints").status,"invalid");
  assert.equal(model.globalIssues.length,0);
  assert.match(renderDesignGroupReadiness(model,"engineering_constraints"),/data-readiness-group="engineering_constraints" data-readiness-field="ox_preburner_temp_limit"/);
});

test("splitting limits preserves an exact preceding-layout review but never blesses changed working conditions",async()=>{
  for(const flags of modes){
    const inputs={...base,...flags,ox_preburner_temp_limit_status:"provisional",fuel_preburner_temp_limit_status:"documented",fuel_preburner_temp_limit_basis:"报告 A"};
    const old=parameterGroups(schema.review_previous_groups,inputs).find(g=>g.id==="preburner_design");
    const group=parameterGroups(schema.system_groups,inputs).find(g=>g.id==="preburner_design");
    for(const hashed of [false,true]){
      const signature=signatureFor(old,inputs),reviews={preburner_design:{kind:"confirmed",signature:hashed?await digest(signature):signature}};
      const migrate=draft=>migrateWorkflowReview({reviews,inputs:draft,group,previousGroups:schema.review_previous_groups,legacyGroups:schema.review_legacy_groups,signatureFor,digest});
      assert.deepEqual(await migrate(inputs),{kind:"confirmed",signature:signatureFor(group,inputs)});
      const key=flags.mode==="temperature"?"ox_preburner_temp_target":flags.mode==="mixture_ratio"?"ox_preburner_mr_target":"ox_preburner_pressure_ratio";
      assert.equal(await migrate({...inputs,[key]:inputs[key]+1}),null);
      assert.equal(await migrate({...inputs,ox_preburner_temp_limit:826}),null,"a changed old signature cannot migrate");
      assert.equal(signatureFor(group,inputs),signatureFor(group,{...inputs,ox_preburner_temp_limit:826}),"after migration, independent limit edits retain working-condition confirmation");
    }
  }
});
test("an exact old physical review migrates after moving initials; changed values cannot inherit it",async()=>{
  for(const id of ["design","preburner_design"]){
    const legacy=parameterGroups(schema.review_legacy_groups,base).find(g=>g.id===id);
    const group=parameterGroups(schema.system_groups,base).find(g=>g.id===id);
    for(const hashed of [false,true]){
      const previous=signatureFor(legacy,base);
      const reviews={[id]:{kind:"confirmed",signature:hashed?await digest(previous):previous}};
      const migrate=inputs=>migrateWorkflowReview({reviews,inputs,group,legacyGroups:schema.review_legacy_groups,signatureFor,digest});
      assert.deepEqual(await migrate(base),{kind:"confirmed",signature:signatureFor(group,base)});
      const changed=id==="design"?{...base,pc:base.pc+1e6}:{...base,ox_preburner_temp_target:base.ox_preburner_temp_target+5};
      assert.equal(await migrate(changed),null);
      const initial=id==="design"?"target_isp":"ox_preburner_mr_target";
      assert.equal(signatureFor(group,base),signatureFor(group,{...base,[initial]:base[initial]+1}),"numerical edits leave the new physical confirmation intact");
    }
  }
});

test("task summary uses execution controls, active equations and current inputs without fabricating results",()=>{
  const draft={...base,thrust:2e6,steady_ox_power_tolerance_W:4,steady_fuel_power_tolerance_W:7};
  const groups=parameterGroups(schema.system_groups,draft);
  const html=renderSolverTask({draft,groups,solverControls:schema.solver_controls});
  assert.match(html,/2 N/);assert.match(html,/4 W/);assert.match(html,/7 W/);
  for(const row of schema.solver_controls.coupling_criteria)assert.ok(html.includes(row.label));
  assert.match(html,/40 次尝试/);assert.match(html,/反求混合比/);
  assert.match(html,/历史判据从该记录/);assert.doesNotMatch(html,/工程约束已通过|计算已完成/);
  const mr=renderSolverTask({draft:{...draft,use_target_preburner_temp:0},groups,solverControls:schema.solver_controls});
  assert.doesNotMatch(mr,/预燃室温度反求/);
  const budget=renderSolverTask({draft:{...draft,cooling_bartz_mode:0,valve_configuration_json:""},groups,solverControls:schema.solver_controls});
  assert.match(budget,/不启用热状态外层反馈/);
  assert.doesNotMatch(budget,/热侧温度反馈|热状态温度反馈|冷却轴向网格节点数/);
  assert.doesNotMatch(renderSolverTask({draft:{...draft,nonlinear_solver:'<img src=x onerror="bad">'}}),/<img|onerror="/);
});
