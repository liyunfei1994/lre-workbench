import assert from "node:assert/strict";
import test from "node:test";
import {buildDesignReadiness, collectLocalReadiness, renderDesignReadiness} from "../webapp/static/design-readiness.js";
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
    assert.match(html, /1 个参数分组已确认或采用示例/);
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
