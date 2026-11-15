import assert from "node:assert/strict";
import test from "node:test";
import {buildDesignReadiness, collectLocalReadiness, renderDesignReadiness, renderDesignGroupReadiness} from "../webapp/static/design-readiness.js";
import {parameterGroups} from "../webapp/static/parameter-editor.js";

const groups = [
  {id:"design", title:"设计目标与工况", fields:[
    {key:"thrust", label:"目标推力", default:1},
    {key:"ambient_pressure", label:"环境压力", default:1},
  ]},
  {id:"preburners", title:"预燃室设计", cycles:["ffsc"], fields:[
    {key:"preburner_design_mode", label:"预燃室设计方式", default:"temperature", virtual:true, choices:[{value:"temperature"}]},
    {key:"ox_preburner_temp_target", label:"富氧预燃室温度目标", default:1, visibility:{preburner_modes:["temperature"]}},
    {key:"ox_preburner_temp_limit_basis", label:"富氧预燃室温度限值依据", default:""},
  ]},
  {id:"geometry", title:"推力室型面", fields:[
    {key:"nozzle_contour_method", label:"喷管型面", default:"current", choices:[{value:"current"},{value:"legacy",disabled:true}]},
  ]},
  {id:"cooling", title:"冷却通道", fields:[
    {key:"cooling_grid_points", label:"冷却离散单元数", default:100, type:"int"},
  ]},
];

const draft = {cycle_type:"ffsc", thrust:1, ambient_pressure:0, use_target_preburner_temp:0, use_target_preburner_mr:0,
  ox_preburner_temp_target:null, ox_preburner_temp_limit_basis:"", nozzle_contour_method:"legacy", cooling_grid_points:100};

test("local completeness accepts zero, optional blanks and legacy choices, and uses current design mode", () => {
  assert.deepEqual(collectLocalReadiness(groups, draft).issues, []);
  const {issues} = collectLocalReadiness(groups, {...draft, thrust:null, use_target_preburner_temp:1, cooling_grid_points:100.5});
  assert.deepEqual(issues.map(issue => issue.fields[0]), ["thrust","ox_preburner_temp_target","cooling_grid_points"]);
});

test("cross-group and unlocated server issues block readiness without marking other groups as verified", () => {
  const active = parameterGroups(groups, draft);
  const model = buildDesignReadiness(active, [
    {fields:["thrust","nozzle_contour_method"],message:"目标推力与喷管型面不匹配。"},
    {fields:["ox_preburner_temp_target"],message:"未定位的 <img> 校验错误。"},
  ], {phase:"invalid"});
  assert.deepEqual(model.groups.map(group => group.status), ["invalid","filled","invalid","filled"]);
  assert.equal(model.globalIssues.length, 1);
  assert.equal(model.readyCount, 0);
  const html = renderDesignReadiness(model);
  assert.match(html, /data-readiness-group="design" data-readiness-field="thrust"/);
  assert.match(html, /data-readiness-group="geometry" data-readiness-field="nozzle_contour_method"/);
  assert.doesNotMatch(html, /<img|data-readiness-field="ox_preburner_temp_target"|✓ 已就绪/);
  assert.match(html, /未定位的 &lt;img&gt; 校验错误/);
  const ready = buildDesignReadiness(active, [], {phase:"ready", warnings:["温度 < K，仅提示。"]});
  assert.equal(ready.readyCount, active.length);
  assert.match(renderDesignReadiness(ready), /输入提示（1）.*温度 &lt; K，仅提示。/);
});

test("validated group values keep their review status until the user confirms or adopts them", () => {
  const active = parameterGroups(groups, draft);
  const groupReviews = new Map([["design", "confirmed"], ["geometry", "preset"]]);
  const pending = buildDesignReadiness(active, [], {phase:"ready", groupReviews, warnings:["温度仅提示。"]});
  assert.deepEqual(pending.groups.map(group => group.status), ["confirmed", "review", "preset", "review"]);
  assert.equal(pending.validationPhase, "ready");
  assert.equal(pending.phase, "review");
  assert.equal(pending.pendingReviewCount, 2);
  const html = renderDesignReadiness(pending);
  assert.match(html, /输入校验通过。还有 2 组参数待确认。/);
  assert.match(html, /data-readiness-group="preburners">查看待确认分组/);
  assert.match(html, /输入提示（1）/);
  assert.doesNotMatch(html, /已就绪|可提交设计点计算/);

  groupReviews.set("preburners", "confirmed").set("cooling", "confirmed");
  const ready = buildDesignReadiness(active, [], {phase:"ready", groupReviews});
  assert.equal(ready.phase, "ready");
  assert.equal(ready.readyCount, active.length);
  assert.match(renderDesignReadiness(ready), /可提交设计点计算/);

  const invalid = buildDesignReadiness(active, [{fields:["thrust"], message:"推力错误。"}], {phase:"invalid", groupReviews});
  assert.equal(invalid.phase, "invalid");
  assert.equal(invalid.groups[0].status, "invalid");
});

const coolingMethods = {id:"cooling_methods", title:"再生冷却计算方法", fields:[
  {key:"cooling_bartz_mode", label:"当前计算方法", default:1, read_only:true},
  {key:"cooling_heat_load", label:"给定吸热量", default:20e6, advanced:true,
    visibility:{all:[{key:"cooling_bartz_mode", op:"eq", value:0}]}},
  {key:"cooling_dp_pa", label:"给定冷却压降", default:1e6, advanced:true,
    visibility:{all:[{key:"cooling_bartz_mode", op:"eq", value:0}]}},
]};

test("methods-only groups do not require confirmation or inflate confirmed parameter counts", () => {
  const active = parameterGroups([groups[0], coolingMethods], {cooling_bartz_mode:1});
  for (const storedReview of [null, "confirmed", "preset"]) {
    const groupReviews = new Map([["design", "confirmed"]]);
    if (storedReview) groupReviews.set("cooling_methods", storedReview);
    const model = buildDesignReadiness(active, [], {phase:"ready", groupReviews});
    assert.equal(model.phase, "ready");
    assert.equal(model.pendingReviewCount, 0);
    assert.equal(model.readyCount, 1);
    assert.equal(model.readonlyCount, 1);
    assert.equal(model.groups[1].status, "readonly");
    assert.equal(model.groups[1].requiresReview, false);
    const html = renderDesignReadiness(model);
    assert.match(html, /1 个适用分组已就绪/);
    assert.match(html, /1 个只读分组无需确认/);
    assert.match(html, /data-status="readonly">只读说明/);
    assert.doesNotMatch(html, /查看待确认分组|2 个参数分组已确认/);
  }
  const standalone = buildDesignReadiness([active[1]], [], {phase:"ready"});
  assert.equal(standalone.readyCount, 0);
  assert.equal(standalone.groups[0].status, "readonly");
  assert.doesNotMatch(renderDesignReadiness(standalone), /0 个适用分组|已确认或采用示例/);
});

test("read-only presentation does not bypass pending, unavailable or failed server validation", () => {
  const active = parameterGroups([groups[0], coolingMethods], {cooling_bartz_mode:1});
  const groupReviews = new Map([["design", "confirmed"], ["cooling_methods", "confirmed"]]);
  for (const phase of ["pending", "unavailable"]) {
    const model = buildDesignReadiness(active, [], {phase, groupReviews});
    assert.equal(model.phase, phase);
    assert.equal(model.groups[1].status, phase);
    assert.equal(model.readyCount, 0);
    assert.equal(model.readonlyCount, 0);
    assert.doesNotMatch(renderDesignReadiness(model), /可提交设计点计算|已确认或采用示例/);
  }
  const invalid = buildDesignReadiness(active, [{fields:["cooling_bartz_mode"], message:"冷却模型标识无效。"}],
    {phase:"ready", groupReviews});
  assert.equal(invalid.phase, "invalid");
  assert.equal(invalid.groups[1].status, "invalid");
  assert.equal(invalid.readyCount, 0);
  assert.match(renderDesignReadiness(invalid), /data-readiness-group="cooling_methods" data-readiness-field="cooling_bartz_mode"/);
  assert.match(renderDesignReadiness(invalid), /冷却模型标识无效/);
  const otherFailure = buildDesignReadiness(active, [{fields:[], message:"模型输入校验失败。"}], {phase:"ready", groupReviews});
  assert.equal(otherFailure.phase, "invalid");
  assert.equal(otherFailure.groups[1].status, "readonly");
  assert.equal(otherFailure.readyCount, 0);
  assert.equal(otherFailure.globalIssues.length, 1);
  assert.doesNotMatch(renderDesignReadiness(otherFailure), /可提交设计点计算/);
});

test("legacy editable cooling budgets still require review after visibility changes", () => {
  const legacy = {cooling_bartz_mode:0, cooling_heat_load:20e6, cooling_dp_pa:1e6};
  const active = parameterGroups([coolingMethods], legacy);
  assert.equal(active[0].fields.length, 3);
  const pending = buildDesignReadiness(active, [], {phase:"ready", groupReviews:new Map()});
  assert.equal(pending.phase, "review");
  assert.equal(pending.pendingReviewCount, 1);
  assert.equal(pending.readonlyCount, 0);
  assert.equal(pending.groups[0].requiresReview, true);
  assert.deepEqual(collectLocalReadiness([coolingMethods], {...legacy, cooling_heat_load:null}).issues.map(issue => issue.fields[0]), ["cooling_heat_load"]);
  const confirmed = buildDesignReadiness(active, [], {phase:"ready", groupReviews:new Map([["cooling_methods", "confirmed"]])});
  assert.equal(confirmed.readyCount, 1);
  assert.equal(confirmed.groups[0].status, "confirmed");
  const modern = parameterGroups([coolingMethods], {...legacy, cooling_bartz_mode:1});
  assert.equal(modern[0].fields.length, 1);
  assert.equal(buildDesignReadiness(modern, [], {phase:"ready", groupReviews:new Map()}).pendingReviewCount, 0);
  assert.equal(buildDesignReadiness(active, [], {phase:"ready", groupReviews:new Map()}).pendingReviewCount, 1);
});

test("virtual editable selectors continue to require group confirmation", () => {
  const active = [{id:"mode", title:"设计方式", fields:[{key:"preburner_design_mode", virtual:true}]}];
  const model = buildDesignReadiness(active, [], {phase:"ready", groupReviews:new Map()});
  assert.equal(model.pendingReviewCount, 1);
  assert.equal(model.groups[0].requiresReview, true);
});

test("a fixed model configuration requires confirmation and maps dataset issues to its group", () => {
  const active = [{id:"chamber_chemistry", title:"燃烧热化学与燃气物性", requires_review:true,
    configuration_context:[{key:"methane_thermo_dataset"}], fields:[{key:"gas_eos",read_only:true},{key:"gas_species_policy",read_only:true}]}];
  const pending = buildDesignReadiness(active, [], {phase:"ready",groupReviews:new Map()});
  assert.equal(pending.groups[0].requiresReview,true);
  assert.equal(pending.groups[0].status,"review");
  assert.equal(pending.readonlyCount,0);
  const confirmed = buildDesignReadiness(active, [], {phase:"ready",groupReviews:new Map([["chamber_chemistry","confirmed"]])});
  assert.equal(confirmed.groups[0].status,"confirmed");
  const blocked = buildDesignReadiness(active,[{fields:["methane_thermo_dataset"],message:"热化学数据集不适用于当前设计计算。"}],
    {phase:"ready",groupReviews:new Map([["chamber_chemistry","confirmed"]])});
  assert.equal(blocked.groups[0].status,"invalid");
  assert.equal(blocked.globalIssues.length,0);
  assert.match(renderDesignGroupReadiness(blocked,"chamber_chemistry"),/热化学数据集不适用/);
  assert.doesNotMatch(renderDesignGroupReadiness(blocked,"chamber_chemistry"),/无需确认参数|✓ 已确认/);
});

test("a parameter page shows only its own issues and links, including shared constraints", () => {
  const model = buildDesignReadiness(parameterGroups(groups, draft), [
    {fields:["thrust","nozzle_contour_method"], message:"推力与型面不匹配 <script>。"},
    {fields:["cooling_grid_points"], message:"冷却离散数无效。"},
    {fields:[], message:"整机检查连接中断。"},
  ], {phase:"ready", groupReviews:new Map([["design", "confirmed"]])});
  const html = renderDesignGroupReadiness(model, "design");
  assert.match(html, /本组参数状态/);
  assert.match(html, /data-readiness-state="invalid"/);
  assert.match(html, /推力与型面不匹配 &lt;script&gt;/);
  assert.match(html, /data-readiness-group="design" data-readiness-field="thrust"/);
  assert.doesNotMatch(html, /<script>|冷却离散数无效|整机检查连接中断|data-readiness-group="geometry"/);
  assert.doesNotMatch(html, /分组状态|计算前检查|已确认|可提交设计点计算/);

  const unrelated = renderDesignGroupReadiness(model, "preburners");
  assert.match(unrelated, /data-readiness-state="filled"/);
  assert.match(unrelated, /确认状态待复核/);
  assert.doesNotMatch(unrelated, /定位参数|输入校验通过|已确认|冷却离散数无效/);
});

test("a group summary distinguishes confirmation, adopted examples and read-only explanations", () => {
  const active = parameterGroups([...groups, coolingMethods], {...draft, cooling_bartz_mode:1});
  const model = buildDesignReadiness(active, [], {phase:"ready", warnings:["整机温度提示，不属于当前分组。"],
    groupReviews:new Map([["design", "confirmed"], ["geometry", "preset"]])});
  const confirmed = renderDesignGroupReadiness(model, "design");
  assert.match(confirmed, /data-status="confirmed"/);
  assert.match(confirmed, /本组参数已确认并保存/);
  assert.doesNotMatch(confirmed, /组参数待确认|整机温度提示|可提交设计点计算|分组状态/);
  assert.match(confirmed, /整机输入提示见“输入检查与运行”/);
  assert.match(renderDesignGroupReadiness(model, "geometry"), /data-status="preset".*采用示例/s);
  const pending = renderDesignGroupReadiness(model, "preburners");
  assert.match(pending, /data-status="review"/);
  assert.match(pending, /请核对取值并确认本组参数/);
  const readonly = renderDesignGroupReadiness(model, "cooling_methods");
  assert.match(readonly, /data-status="readonly"/);
  assert.match(readonly, /无需确认参数/);
  assert.doesNotMatch(readonly, /已确认|请核对取值并确认本组参数/);
  const validationOnly = buildDesignReadiness(active, [], {phase:"ready"});
  assert.match(renderDesignGroupReadiness(validationOnly, "design"), /data-status="ready">校验通过/);
  assert.doesNotMatch(renderDesignGroupReadiness(validationOnly, "design"), /已确认|已就绪|可提交/);
});

test("unfinished input checks never expose a stored confirmation as the active group status", () => {
  const active = parameterGroups([groups[0], coolingMethods], {cooling_bartz_mode:1});
  const saved = buildDesignReadiness(active, [], {phase:"ready", groupReviews:new Map([["design", "confirmed"]])});
  for (const phase of ["pending", "checking", "unavailable"]) {
    // Even a caller retaining the previous group model while checking must not
    // render the old confirmation or read-only badge as the validation result.
    const model = {...saved, phase, validationPhase:phase};
    for (const id of ["design", "cooling_methods"]) {
      const html = renderDesignGroupReadiness(model, id);
      assert.match(html, new RegExp(`data-readiness-state="${phase}"`));
      assert.doesNotMatch(html, /已确认|无需确认参数|校验通过|可提交/);
      if (phase === "unavailable") assert.match(html, /data-readiness-retry>重新检查/);
      else assert.match(html, /正在检查本组参数/);
    }
  }
});

test("a confirmed group reports stale selected results separately and offers only navigation", () => {
  const model = buildDesignReadiness(parameterGroups(groups, draft), [], {phase:"ready", groupReviews:new Map([["design", "confirmed"]])});
  const freshness = {state:"stale", changedGroups:[{id:"design"}]};
  const html = renderDesignGroupReadiness(model, "design", {freshness,hasResult:true});
  assert.match(html, /data-readiness-state="confirmed"/);
  assert.match(html, /data-status="confirmed">✓ 已确认/);
  assert.match(html, /data-result-state="stale"/);
  assert.match(html, /所选结果需重算/);
  assert.match(html, /本组输入与所选计算记录不同/);
  assert.match(html, /确认与保存仅更新输入/);
  assert.match(html, /type="button"[^>]+data-workspace-view="design_run">前往输入检查与运行/);
  assert.doesNotMatch(html, /type="submit"|data-readiness-state="stale"|data-status="stale"|data-review-design-group/);
  assert.equal(model.groups.find(group=>group.id==="design").status,"confirmed");
  assert.equal(freshness.state,"stale");
  const other = renderDesignGroupReadiness(model, "design", {freshness:{state:"stale",changedGroups:[{id:"preburners"}]},hasResult:true});
  assert.match(other,/其他输入与所选计算记录不同/);
  assert.doesNotMatch(other,/本组输入与所选计算记录不同/);
});

test("a changed task without results is not presented as an existing result to recompute", () => {
  const model = buildDesignReadiness(parameterGroups(groups, draft), [], {phase:"ready", groupReviews:new Map([["design", "confirmed"]])});
  const html = renderDesignGroupReadiness(model,"design",{freshness:{state:"stale",changedGroups:[{id:"design"}]},hasResult:false});
  assert.match(html,/data-status="confirmed"/);
  assert.match(html,/所选任务输入与当前方案不同/);
  assert.match(html,/尚无可用计算结果.*不会更新已提交的任务/);
  assert.doesNotMatch(html,/所选结果需重算|data-result-state="stale"/);
  assert.match(html,/data-workspace-view="design_run"/);
});

test("a selected result note requires proven differences rather than missing comparison evidence", () => {
  const model = buildDesignReadiness(parameterGroups(groups, draft), [], {phase:"ready", groupReviews:new Map([["design", "confirmed"]])});
  for(const state of ["current","missing","unknown","foreign","invalid"]){
    const html = renderDesignGroupReadiness(model,"design",{freshness:{state,changedGroups:[{id:"design"}]}});
    assert.match(html,/data-status="confirmed"/);
    assert.doesNotMatch(html,/所选结果需重算|data-result-state="stale"|data-workspace-view="design_run"/);
  }
});

test("missing or informational selections do not fall back to the full design checklist", () => {
  const model = buildDesignReadiness(parameterGroups(groups, draft), [], {phase:"ready", groupReviews:new Map()});
  for (const [id, pattern] of [["", /请选择左侧设置项/], ["reference", /未列入参数检查清单/]]) {
    const html = renderDesignGroupReadiness(model, id);
    assert.match(html, pattern);
    assert.match(html, /data-readiness-state="unselected"/);
    assert.doesNotMatch(html, /分组状态|设计目标与工况|data-readiness-group=|已确认|校验通过|可提交/);
  }
  assert.match(renderDesignGroupReadiness(null, ""), /请选择左侧设置项/);
});
