import assert from "node:assert/strict";
import test from "node:test";
import {renderInjectorDesign,injectorSectionSvg,injectorDesignJson,mountInjectorDesign} from "../webapp/static/injector-design.js";
import {renderComponentWorkspace} from "../webapp/static/component-analysis.js";

function fixture(status="consistent"){
  return {available:true,record_id:"point-1",source:"saved",stations:[
    {id:"main",component_id:"injector",label:"主燃烧室喷注器",status,shape_valid:true,
     dimensions:{face_diameter_m:.3,num_elements:50,d_ox_mm:4,d_post_mm:5,d_outer_mm:7,delta_fuel_mm:1},
     ports:[{id:"ox",label:"富氧",mass_flow_kg_s:23},{id:"fuel",label:"富燃",mass_flow_kg_s:7}],checks:[],warnings:[],limitations:[]},
    {id:"ox",component_id:"ox_preburner",label:"富氧预燃室喷注器",status:"boundary_only",ports:[],missing_geometry:["单元数"]},
    {id:"fuel",component_id:"fuel_preburner",label:"富燃预燃室喷注器",status:"boundary_only",ports:[],missing_geometry:["流量系数"]},
  ]};
}
test("three injectors are distinct; only saved main-chamber dimensions get a section",()=>{
  const model=fixture(),before=structuredClone(model),html=renderInjectorDesign(model);
  assert.equal((html.match(/data-id-panel=/g)||[]).length,3);
  assert.equal((html.match(/class="id-section"/g)||[]).length,1);
  assert.match(html,/详细孔径尚无设计依据/);
  assert.match(html,/旋流结构未定义/);
  assert.match(html,/不是针栓/);
  assert.match(html,/面积等效间距/);
  assert.deepEqual(model,before);
});
test("legacy and inconsistent records stay explicit; missing fields never become zero dimensions",()=>{
  assert.match(renderInjectorDesign(fixture("legacy")),/旧尺寸保持原值/);
  assert.match(renderInjectorDesign(fixture("inconsistent")),/未自动改写尺寸/);
  const m=fixture("incomplete");m.stations[0].dimensions={};m.stations[0].shape_valid=false;
  const html=renderInjectorDesign(m);
  assert.match(html,/未记录/);assert.doesNotMatch(html,/class="id-section"/);
  for(const bad of [NaN,Infinity,-4,0]){
    m.stations[0].shape_valid=true;m.stations[0].dimensions={d_ox_mm:bad,d_post_mm:5,d_outer_mm:7};
    assert.doesNotMatch(injectorSectionSvg(m.stations[0]),/<svg/);
  }
});
test("cross-section radii use recorded diameters and do not invent axial geometry",()=>{
  const svg=injectorSectionSvg(fixture().stations[0]);
  assert.match(svg,/r="140"/);assert.match(svg,/r="100"/);assert.match(svg,/r="80"/);
  assert.doesNotMatch(svg,/L_post|recess/);
});
test("preburner missing or contradictory boundaries cannot appear verified",()=>{
  const m=fixture();
  m.stations[1].boundary_status="inconsistent";
  m.stations[1].checks=[{label:"网络流量与支路状态",status:"fail"}];
  const html=renderInjectorDesign(m);
  assert.match(html,/保存支路与预燃室状态存在不一致/);
  assert.match(html,/网络流量与支路状态/);
  assert.match(html,/当前记录缺少完整的支路设计边界/);
});
test("render escapes saved strings and export preserves evidence and missing values",()=>{
  const m=fixture("legacy");m.record_id='<img src=x onerror="x">';m.stations[0].warnings=['<script>x</script>'];
  m.stations[0].ports[0].pressure_pa=null;
  const html=renderInjectorDesign(m);
  assert.doesNotMatch(html,/<script>|<img src=x/);assert.match(html,/&lt;script&gt;/);
  assert.deepEqual(JSON.parse(injectorDesignJson(m)),m);
  for(const v of [null,{}, {available:false,reason:"Unavailable"}])assert.doesNotMatch(renderInjectorDesign(v),/data-id-panel|class="id-section"/);
});
test("station navigation and component crosslinks have cleanup and exact targets",()=>{
  const m=fixture(),buttons=m.stations.map(s=>({dataset:{idTab:s.id},setAttribute(k,v){this[k]=v;}})),panels=m.stations.map(s=>({dataset:{idPanel:s.id},hidden:false}));
  const listeners=new Map();
  const host={querySelectorAll:q=>q==="[data-id-tab]"?buttons:panels,contains:()=>true,
    addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:(n,f)=>{if(listeners.get(n)===f)listeners.delete(n);}};
  let linked=null;
  const cleanup=mountInjectorDesign(host,m,{initialStation:"fuel",onInspectComponent:id=>linked=id});
  assert.deepEqual(panels.map(p=>p.hidden),[true,true,false]);
  listeners.get("click")({target:{closest:()=>({dataset:{idTab:"ox"}})}});
  assert.deepEqual(panels.map(p=>p.hidden),[true,false,true]);
  listeners.get("click")({target:{closest:()=>({dataset:{idComponent:"ox_preburner"}})}});
  assert.equal(linked,"ox_preburner");cleanup();assert.equal(listeners.size,0);
  mountInjectorDesign(null,m)();
});
test("component workspace exposes FFSC injector links without duplicating geometry",()=>{
  const make=id=>({id,title:id,fields:[],outputs:{}});
  const html=renderComponentWorkspace(["injector","ox_preburner","fuel_preburner","main_chamber"].map(make));
  for(const id of ["injector","ox_preburner","fuel_preburner","main_chamber"])assert(html.includes(`data-ca-injector-design="${id}"`));
  assert.doesNotMatch(renderComponentWorkspace(["injector","gas_generator","main_chamber"].map(make)),/data-ca-injector-design/);
});
