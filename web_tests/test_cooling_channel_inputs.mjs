import test from "node:test";
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {applyCoolingChannelEdit as edit, inspectCoolingChannelTable as inspect,
  validateCoolingChannelRows as validate, renderCoolingChannelInputs as render,
  coolingChannelPendingIssues as pendingIssues, renderCoolingChannelGeometrySummary as summary,
  refreshCoolingChannelStatus as refresh}
  from "../webapp/static/cooling-channel-inputs.js";

const key = "cooling_channel_table_json";
const inputs = {[key]:"",cooling_bartz_mode:1,cooling_n_channels:300,cooling_channel_width_m:.0015,cooling_channel_height_m:.008,cooling_wall_thickness_m:.0005};
const row = (anchor="throat",overrides={}) => ({anchor,x_mm:0,n:300,w_mm:1.5,h_mm:8,t_mm:.5,...overrides});
const table = rows => JSON.stringify({version:1,rows});
const run = (state,action,options) => edit(state.inputs,state.editor,action,options);
const profile = {model_id:"regen_quasi1d_ph_v1",geometry:{L_cyl_m:.2,x_throat_m:.35,full_exit_x_m:1.2,nodes:[
  {x_m:1.2,n_channels:300,width_m:.003,height_m:.008,wall_thickness_m:.0005,D_h_m:.00436,pitch_m:.006,rib_m:.003,A_flow_m2:.0072},
  {x_m:.35,n_channels:300,width_m:.0015,height_m:.008,wall_thickness_m:.0005,D_h_m:.002526,pitch_m:.003,rib_m:.0015,A_flow_m2:.0036},
  {x_m:0,n_channels:300,width_m:.002,height_m:.008,wall_thickness_m:.0005,D_h_m:.0032,pitch_m:.004,rib_m:.002,A_flow_m2:.0048},
]}};

test("selecting a new table is a pending explicit design, never a false scalar conversion", () => {
  let state = edit(inputs,null,{type:"mode",value:"table"});
  assert.equal(state.inputs,inputs);
  assert.equal(state.editor.candidate.rows.length,3);
  assert.ok(state.editor.candidate.rows.every(r => r.w_mm===null && r.h_mm===null && r.t_mm===null && r.n===300));
  assert.equal(pendingIssues(state.editor)[0].fields[0],key);
  state = run(state,{type:"apply"});
  assert.equal(state.inputs[key],"");
  assert.match(state.editor.error,/第 1 行/);
  assert.equal(pendingIssues(state.editor).length,1);
});

test("complete explicit edits apply only on request and preserve unrelated input values", () => {
  let state = edit(inputs,null,{type:"new"});
  for(let i=0;i<3;i++) for(const [k,v] of Object.entries({w_mm:1.5+i*.2,h_mm:8,t_mm:.5}))
    state = run(state,{type:"edit",row:i,key:k,value:String(v)});
  state = run(state,{type:"edit",row:0,key:"n",value:"320"});
  assert.equal(state.inputs,inputs);
  assert.ok(state.editor.candidate.rows.every(r=>r.n===320));
  state = run(state,{type:"apply"});
  assert.equal(pendingIssues(state.editor).length,0);
  assert.equal(state.inputs.cooling_channel_width_m,inputs.cooling_channel_width_m);
  assert.equal(inspect(state.inputs[key]).kind,"editable");
  assert.deepEqual(JSON.parse(state.inputs[key]).rows.map(r=>r.w_mm),[1.5,1.7,1.9]);
});

test("table mode switching preserves exact legacy text and pending drafts in memory", () => {
  const raw = '{ "version": 1, "rows": [{"anchor":"throat", "n":300}], "vendor":"kept" }';
  let state = edit({...inputs,[key]:raw},null,{type:"mode",value:"scalar"});
  assert.equal(state.inputs[key],"");
  state = run(state,{type:"mode",value:"table"});
  assert.equal(state.inputs[key],raw);
  state = run(state,{type:"new"});
  state = run(state,{type:"edit",row:0,key:"w_mm",value:"2.25"});
  state = run(state,{type:"mode",value:"scalar"});
  assert.equal(state.editor.candidate,null);
  state = run(state,{type:"mode",value:"table"});
  assert.equal(state.inputs[key],raw);
  assert.equal(state.editor.candidate.rows[0].w_mm,2.25);
  state = run(state,{type:"cancel"});
  assert.equal(state.inputs[key],raw);
  assert.equal(pendingIssues(state.editor).length,0);
});

test("reading sparse, extended, invalid, or empty tables never fills defaults or falls back", () => {
  for(const raw of ['[]','{"rows":[]}','not-json',table([{anchor:"throat"}]),table([row("throat",{note:"keep"})])]) {
    const before={...inputs,[key]:raw};
    const classified=inspect(raw);
    assert.ok(["legacy","invalid"].includes(classified.kind));
    const html=render(before);
    assert.match(html,/编辑原始表/);
    assert.doesNotMatch(html,/data-cooling-channel-key="w_mm"/);
    assert.equal(before[key],raw);
    const attempt=edit(before,null,{type:"edit",row:0,key:"w_mm",value:"2"});
    assert.equal(attempt.inputs,before);
    assert.match(attempt.editor.error,/不能直接逐格编辑/);
  }
});

test("explicit raw repair requires complete values while retaining metadata byte for byte", () => {
  const before={...inputs,[key]:table([{anchor:"throat"}])};
  let state=edit(before,null,{type:"raw-edit"});
  state=run(state,{type:"apply"});
  assert.equal(state.inputs,before);
  assert.match(state.editor.error,/通道数量/);
  const raw='{ "version": 8, "source": "old file", "rows": ['+JSON.stringify(row("throat",{vendor_extra:{a:1}}))+'] }';
  state=run(state,{type:"raw",value:raw});
  state=run(state,{type:"apply"});
  assert.equal(state.inputs[key],raw);
  assert.equal(inspect(raw).kind,"legacy");
});

test("known array documents preserve their container form after explicit cell edits", () => {
  const raw=JSON.stringify([row("injector"),row("exit")]);
  let state=edit({...inputs,[key]:raw},null,{type:"edit",row:1,key:"h_mm",value:"9"});
  assert.equal(state.inputs[key],raw);
  state=run(state,{type:"apply"});
  assert.ok(Array.isArray(JSON.parse(state.inputs[key])));
  assert.equal(JSON.parse(state.inputs[key])[1].h_mm,9);
});

test("finite full dimensions, consistent channel count, and distinct stations are required", () => {
  assert.ok(validate([row("injector"),row("exit",{n:301})]).some(i=>i.key==="n"));
  assert.ok(validate([row("injector"),row("x_mm")]).some(i=>/重复/.test(i.message)));
  assert.ok(validate([row("throat"),row("throat")]).some(i=>/重复/.test(i.message)));
  for(const [k,v] of [["n",300.1],["n",true],["w_mm",null],["h_mm",Infinity],["t_mm",.1],["x_mm",-1]])
    assert.ok(validate([row("x_mm",{[k]:v})]).some(i=>i.key===k));
  assert.ok(validate([row("throat"),row("x_mm",{x_mm:350})],profile.geometry).some(i=>/重复/.test(i.message)));
  assert.ok(validate([row("x_mm",{x_mm:1201})],profile.geometry).some(i=>/超出/.test(i.message)));
  assert.deepEqual(validate([row("injector"),row("throat"),row("exit")],profile.geometry),[]);
});

test("row addition is incomplete until filled and cannot silently delete the final row", () => {
  let state=edit({...inputs,[key]:table([row()])},null,{type:"remove",row:0});
  assert.match(state.editor.error,/至少保留/);
  state=run(state,{type:"cancel"});
  state=run(state,{type:"add"});
  assert.equal(state.editor.candidate.rows[1].anchor,"x_mm");
  assert.equal(state.editor.candidate.rows[1].w_mm,null);
  state=run(state,{type:"apply"});
  assert.match(state.editor.error,/第 2 行/);
});

test("node sampling requires a matching current result and captures every solved station", () => {
  const stale=edit(inputs,null,{type:"sample"},{profile,profileCurrent:false});
  assert.equal(stale.inputs,inputs);
  assert.match(stale.editor.error,/当前输入一致/);
  let state=edit(inputs,null,{type:"sample"},{profile,profileCurrent:true});
  assert.equal(state.inputs,inputs);
  assert.deepEqual(state.editor.candidate.rows.map(r=>r.x_mm),[0,350,1200]);
  assert.deepEqual(state.editor.candidate.rows.map(r=>r.w_mm),[2,1.5,3]);
  state=run(state,{type:"apply"},{profile,profileCurrent:true});
  assert.deepEqual(JSON.parse(state.inputs[key]).rows.map(r=>r.x_mm),[0,350,1200]);
});

test("new imported content invalidates pending state rather than overwriting the import", () => {
  const state=edit(inputs,null,{type:"new"});
  const imported={...inputs,[key]:table([row("throat",{w_mm:2.2})])};
  const next=edit(imported,state.editor,{type:"apply"});
  assert.equal(next.inputs,imported);
  assert.equal(next.editor.candidate,null);
});

test("render escapes raw metadata and never invents geometric results for a stale draft", () => {
  const html=render({...inputs,[key]:'<script>alert(1)</script>'});
  assert.doesNotMatch(html,/<script>/);
  assert.match(html,/&lt;script&gt;/);
  assert.match(summary(profile,false),/重算当前设计点/);
  assert.doesNotMatch(summary(profile,false),/2\.526/);
  assert.match(summary(profile,true),/2\.526/);
  const state=edit(inputs,null,{type:"new"});
  assert.doesNotMatch(render(inputs,{editor:state.editor,profile,profileCurrent:true}),/当前已算通道几何/);
  assert.match(render(inputs),/按当地型面半径/);
});

test("applied table is accepted by the actual Python channel parser without defaults", () => {
  let state=edit(inputs,null,{type:"sample"},{profile,profileCurrent:true});
  state=run(state,{type:"apply"},{profile,profileCurrent:true});
  const result=spawnSync("python",["-X","utf8","-c",[
    "import sys,json",
    "sys.path.insert(0,'src')",
    "from lre_workbench.components.chambers.design import parse_channel_table",
    "from dataclasses import asdict",
    "print(json.dumps([asdict(r) for r in parse_channel_table(sys.stdin.read())]))",
  ].join("\n")],{cwd:fileURLToPath(new URL("../",import.meta.url)),input:state.inputs[key],encoding:"utf8"});
  assert.equal(result.status,0,result.stderr);
  assert.deepEqual(JSON.parse(result.stdout),JSON.parse(state.inputs[key]).rows);
});

function statusHost() {
  const nodes = Object.fromEntries(["pending","sources","validation","error","derived"].map(key =>
    [`[data-cooling-channel-${key}]`,{hidden:false,innerHTML:"",textContent:""}]));
  const sample = {hidden:false,disabled:true};
  nodes['[data-cooling-channel-action="sample"]']=sample;
  const fields = [
    {dataset:{coolingChannelKey:"w_mm",coolingChannelRow:"0"},value:"1.",disabled:true},
    {dataset:{coolingChannelKey:"n",coolingChannelRow:"0"},value:"300",disabled:true},
    {dataset:{coolingChannelKey:"n",coolingChannelRow:"1"},value:"300",disabled:true},
  ].map(field => ({...field,attributes:{},setAttribute(key,value){this.attributes[key]=value;}}));
  const host = {ownerDocument:{activeElement:fields[0]},matches:()=>true,
    querySelector:selector=>nodes[selector],querySelectorAll:selector=>selector==="[data-cooling-channel-key]"?fields:[]};
  return {host,nodes,fields,sample};
}

test("typing refresh shows pending actions without replacing focused controls or disabled state", () => {
  const before = {...inputs,[key]:table([row("injector"),row("exit")])};
  const ui = statusHost();
  assert.match(render(before),/data-cooling-channel-pending hidden/);
  let state = edit(before,null,{type:"edit",row:0,key:"w_mm",value:"1."});
  state = run(state,{type:"edit",row:0,key:"n",value:"320"});
  const pendingActions=ui.nodes["[data-cooling-channel-pending]"];
  refresh(ui.host,state.inputs,state.editor,{profile,profileCurrent:true});
  assert.equal(ui.nodes["[data-cooling-channel-pending]"],pendingActions);
  assert.equal(pendingActions.hidden,false);
  assert.equal(ui.nodes["[data-cooling-channel-sources]"].hidden,true);
  assert.equal(ui.fields[0].value,"1.");
  assert.deepEqual(ui.fields.slice(1).map(field=>field.value),["320","320"]);
  assert.ok(ui.fields.every(field=>field.disabled));
  assert.equal(ui.sample.disabled,true);
  assert.doesNotMatch(ui.nodes["[data-cooling-channel-derived]"].innerHTML,/当前已算通道几何/);
  state=run(state,{type:"edit",row:0,key:"w_mm",value:""});
  refresh(ui.host,state.inputs,state.editor);
  assert.equal(ui.fields[0].attributes["aria-invalid"],"true");
  assert.match(ui.nodes["[data-cooling-channel-validation]"].innerHTML,/第 1 行.*宽度/);
});

test("status refresh follows result freshness and raw JSON validity without table repaint", () => {
  const ui=statusHost();
  refresh(ui.host,inputs,null,{profile,profileCurrent:true});
  assert.equal(ui.sample.hidden,false);
  assert.match(ui.nodes["[data-cooling-channel-derived]"].innerHTML,/当前已算通道几何/);
  refresh(ui.host,inputs,null,{profile,profileCurrent:false});
  assert.equal(ui.sample.hidden,true);
  assert.doesNotMatch(ui.nodes["[data-cooling-channel-derived]"].innerHTML,/当前已算通道几何/);
  let state=edit({...inputs,[key]:table([row()])},null,{type:"raw-edit"});
  state=run(state,{type:"raw",value:"broken"});
  refresh(ui.host,state.inputs,state.editor);
  assert.match(ui.nodes["[data-cooling-channel-validation]"].innerHTML,/不是有效 JSON/);
  assert.equal(ui.nodes["[data-cooling-channel-pending]"].hidden,false);
});
