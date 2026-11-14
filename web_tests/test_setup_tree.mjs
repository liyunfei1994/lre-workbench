import test from "node:test";
import assert from "node:assert/strict";
import {setupBranch,setupSection,setupNavigationGroup,setupNodes,setupProgress,renderSetupBranch} from "../webapp/static/setup-tree.js";

test("every schema group retains an actionable location, including new groups",()=>{
  for(const id of ["cycle","design","chamber_chemistry","pressure_ffsc","future_group"])
    assert.equal(setupNavigationGroup(id),"concept","overall assumptions have an actionable task location");
  assert.deepEqual(setupSection("cycle"),{id:"configuration",label:"发动机定义与设计工况"});
  assert.equal(setupSection("design").id,"configuration");
  assert.equal(setupSection("chamber_chemistry").id,"models");
  assert.equal(setupSection("pressure_ffsc").id,"design_point");
  assert.equal(setupSection("pumps").id,"pumps");
  assert.equal(setupNavigationGroup("pumps"),"components");
  assert.ok(setupSection("future_group").label);
  assert.equal(setupBranch("future_group"),"models");
});
test("configuration has one direct concept entry and does not count as an efficiency assumption",()=>{
  const groups=[{id:"cycle",title:"发动机构型与循环原理",fields:[]},
    {id:"component_efficiencies",title:"泵与涡轮效率假设",fields:[]}];
  const nodes=setupNodes(groups,{groups:[{id:"cycle",status:"readonly"},{id:"component_efficiencies",status:"review"}]});
  const configuration=renderSetupBranch(nodes,"configuration","cycle");
  assert.match(configuration,/data-setup-group="cycle"/);
  assert.match(configuration,/aria-current="page"/);
  assert.match(configuration,/data-status="readonly"/);
  assert.doesNotMatch(configuration,/data-setup-group="component_efficiencies"/);
  assert.doesNotMatch(renderSetupBranch(nodes,"models"),/data-setup-group="cycle"/);
  assert.equal(setupProgress(nodes).label,"1 组设置 · 1 待确认");
  assert.equal(setupProgress(nodes,"configuration").label,"只读说明");
});

test("input confirmation and validation remain independent of stale results",()=>{
  const groups=[{id:"design",title:"目标",fields:[{key:"thrust"}]},{id:"chamber_chemistry",title:"燃气模型",fields:[]}];
  const readiness={groups:[{id:"design",status:"invalid",issues:[{fields:["thrust"],message:"推力不能为空"}]},{id:"chamber_chemistry",status:"confirmed"}]};
  const nodes=setupNodes(groups,readiness,[{id:"design"},{id:"chamber_chemistry"}],true);
  assert.equal(nodes[0].state,"invalid");assert.equal(nodes[0].field,"thrust");
  assert.equal(nodes[1].state,"confirmed");assert.equal(nodes[1].label,"已确认");
  assert.equal(nodes[0].changed,true);assert.equal(nodes[1].changed,true);
  assert.match(nodes[0].reason,/推力不能为空/);
  assert.match(nodes[1].reason,/本组输入与所选计算记录不同.*确认参数不会改变该记录的输入/);
  assert.equal(setupProgress(nodes,"models").label,"输入已就绪");
  const confirmed=renderSetupBranch(nodes,"models","chamber_chemistry");
  assert.match(confirmed,/data-status="confirmed">已确认/);
  assert.doesNotMatch(confirmed,/data-status="stale"|待更新/);
  assert.match(renderSetupBranch(nodes,"configuration","design"),/data-setup-field="thrust"/);
  assert.match(renderSetupBranch(nodes,"configuration","design"),/aria-current="page"/);
  const withoutRecord=setupNodes(groups,readiness,["chamber_chemistry"],false)[1];
  assert.equal(withoutRecord.state,"confirmed");assert.equal(withoutRecord.changed,false);
  assert.equal(withoutRecord.resultReason,"");
});
test("tree labels and issue reasons are escaped; an unchecked group is never ready",()=>{
  const nodes=setupNodes([{id:'future"',title:'<img onerror=x>',fields:[]}],null);
  const html=renderSetupBranch(nodes,"models");
  assert.equal(nodes[0].state,"pending");assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
  assert.match(html,/data-setup-group="future&quot;"/);
});

test("progress counts editable settings while keeping example adoption distinct from validation",()=>{
  const nodes=[{branch:"requirements",state:"confirmed"},{branch:"requirements",state:"preset"},
    {branch:"requirements",state:"readonly"},{branch:"models",state:"ready"}];
  const progress=setupProgress(nodes,"requirements");
  assert.equal(progress.label,"输入已就绪");assert.equal(progress.state,"ready");
  assert.match(progress.reason,/1 组已确认/);assert.match(progress.reason,/1 组采用示例/);
  assert.match(progress.reason,/计算、工程约束与验证证据分别核对/);
  assert.equal(setupProgress(nodes).label,"3 组设置 · 输入已就绪");
  assert.equal(setupProgress([{branch:"models",state:"readonly"}],"models").label,"只读说明");
});

test("branch progress counts input problems without counting stale results as pending input",()=>{
  const nodes=[{branch:"requirements",state:"invalid"},{branch:"requirements",state:"review"},
    {branch:"requirements",state:"confirmed",changed:true},{branch:"models",state:"pending"},{branch:"models",state:"pending"}];
  assert.equal(setupProgress(nodes,"requirements").label,"2 待处理");
  assert.equal(setupProgress(nodes,"requirements").state,"invalid");
  assert.equal(setupProgress(nodes,"models").label,"2 检查中");
  assert.equal(setupProgress(nodes,"models").state,"pending");
  assert.equal(setupProgress(nodes).label,"5 组设置 · 4 待处理");
  assert.match(setupProgress(nodes).reason,/1 组存在问题.*1 组待确认.*1 组已确认.*2 组检查中/);
  assert.doesNotMatch(setupProgress(nodes).reason,/待更新/);
});

test("a changed read-only explanation stays outside the editable settings count",()=>{
  const nodes=setupNodes([{id:"cycle",title:"构型说明",fields:[]}],{groups:[{id:"cycle",status:"readonly"}]},["cycle"],true);
  assert.equal(nodes[0].state,"readonly");assert.equal(nodes[0].changed,true);
  assert.equal(setupProgress(nodes).label,"0 组设置 · 只读说明");
});
