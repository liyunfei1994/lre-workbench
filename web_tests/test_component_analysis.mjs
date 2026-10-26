/** Component workspace presentation and lifecycle contracts. Run: node --test web_tests/test_component_analysis.mjs */
import assert from "node:assert/strict";
import {test} from "node:test";
import {renderComponentWorkspace,mountComponentWorkspace} from "../webapp/static/component-analysis.js";

const field=(key,value,unit="—",label=key)=>({key,value,unit,label});
const component=(id,fields=[],title=id)=>({id,title,fields,outputs:{}});
const pump=()=>component("ox_pump",[
  field("power_MW",2.75,"MW","泵功率"),field("eta",.78,"—","泵效率"),
  field("p_in_MPa",.8,"MPa","泵入口压力"),field("p_out_MPa",21.5,"MPa","泵出口压力"),
  field("T_in_K",90,"K","泵入口温度"),field("T_out_K",96,"K","泵出口温度"),
],"液氧泵");
const nozzle=()=>component("nozzle",[
  field("thrust_N",1000,"kN","推力"),field("thrust_vac_N",1200,"kN","真空推力"),
  field("Isp_s",300,"s","比冲"),field("Isp_vac_s",360,"s","真空比冲"),
  field("p_amb_Pa",.04,"MPa","环境压力"),field("p_exit_Pa",.06,"MPa","出口压力"),
],"主喷管");

test("workspace groups every supported cycle component and retains every detail panel",()=>{
  const ids=["ox_pump","fuel_pump","ox_preburner","fuel_preburner","gas_generator","main_chamber","injector",
    "ox_preburner_turbine","fuel_preburner_turbine","gg_turbine","gg_turbine_ox","gg_turbine_fuel","cooling_channel","nozzle","gg_exhaust_nozzle"];
  const html=renderComponentWorkspace(ids.map(id=>component(id,[field("test",3)])));
  for (const title of ["泵与供给","涡轮与轴功","燃烧与喷注","冷却与换热","喷管与推力"]) assert(html.includes(title));
  assert.equal((html.match(/data-ca-panel=/g)||[]).length,ids.length);
  assert.equal((html.match(/data-ca-object=/g)||[]).length,ids.length);
  assert.equal((html.match(/aria-label="[^"]*性能结果" hidden/g)||[]).length,ids.length-1);
  assert.match(html,/data-ca-category="shaft" aria-pressed="false">涡轮与轴功<span>5<\/span>/);
  assert.match(html,/data-ca-category="combustion" aria-pressed="false">燃烧与喷注<span>5<\/span>/);
  assert.match(html,/aria-current="true" title="ox_pump"/);
  assert.match(html,/<th scope="col">参数<\/th><th scope="col">符号<\/th><th scope="col">数值<\/th><th scope="col">单位<\/th>/);
});

test("empty categories remain visible and a missing component set has a precise empty state",()=>{
  const html=renderComponentWorkspace([pump()]);
  assert.match(html,/data-ca-category="shaft" aria-pressed="false" disabled>涡轮与轴功<span>0<\/span>/);
  assert.match(html,/液氧泵/);
  for (const input of [[],null,undefined,{}]) {
    const empty=renderComponentWorkspace(input);
    assert.match(empty,/暂无部件性能结果/);
    assert.doesNotMatch(empty,/data-ca-panel|ca-chart"|通过/);
  }
  const noFields=renderComponentWorkspace([component("main_chamber",[],"主燃烧室")]);
  assert.match(noFields,/该部件没有可展示的系统参数/);
  assert.match(noFields,/未返回燃烧状态量/);
});

test("plots and state quantities consume normalized units without reading raw outputs",()=>{
  const item=pump();
  Object.defineProperty(item,"outputs",{get(){throw new Error("raw solver outputs must not be read");}});
  Object.freeze(item.fields);
  Object.freeze(item);
  const html=renderComponentWorkspace([item]);
  assert.match(html,/压力对比。入口 0\.8 MPa；出口 21\.5 MPa/);
  assert.match(html,/温度对比。入口 90 K；出口 96 K/);
  assert.match(html,/<td title="power_MW">泵功率<\/td>/);
  assert.match(html,/<td class="ca-value">2\.75<\/td><td class="ca-unit">MW<\/td>/);
  assert.doesNotMatch(html,/800,000|21,500,000|2,750,000/);
});

test("charts never compare incompatible units, missing values, negative values or invent reference thresholds",()=>{
  for (const pair of [[.8,"MPa",20,"Pa"],[null,"MPa",20,"MPa"],[-.8,"MPa",20,"MPa"],[NaN,"MPa",Infinity,"MPa"]]) {
    const item=component("ox_pump",[field("p_in_MPa",pair[0],pair[1]),field("p_out_MPa",pair[2],pair[3])]);
    const html=renderComponentWorkspace([item]);
    assert.match(html,/暂无可成对比较的数值/);
    assert.doesNotMatch(html,/<svg class="ca-chart"|NaN|Infinity|安全|通过|正常/);
    assert.match(html,/title="p_in_MPa"/);
    assert.match(html,/title="p_out_MPa"/);
  }
});

test("finite aliases are selected before a missing preferred alias",()=>{
  const item=component("ox_pump",[
    field("p_in_MPa",null,"MPa"),field("p_in_Pa",.8,"MPa"),
    field("p_out_MPa",null,"MPa"),field("p_out_Pa",20,"MPa"),
    field("power_MW",null,"MW"),field("power_W",2,"MW"),
  ]);
  const html=renderComponentWorkspace([item]);
  assert.match(html,/压力对比。入口 0\.8 MPa；出口 20 MPa/);
  assert.match(html,/<span>power_W<\/span><strong>2<small>MW<\/small>/);
});

test("zero and extreme finite comparisons stay truthful without nonfinite SVG coordinates",()=>{
  for (const values of [[0,0],[1e307,1e308],[1e-307,1e-308]]) {
    const html=renderComponentWorkspace([component("injector",[
      field("dp_ox_MPa",values[0],"MPa"),field("dp_fuel_MPa",values[1],"MPa"),
    ])]);
    assert.match(html,/<svg class="ca-chart"/);
    assert.doesNotMatch(html,/NaN|Infinity/);
    if (values[0]===0) assert.match(html,/喷注压降。氧化剂 0 MPa；燃料 0 MPa/);
  }
});

test("symbols retain the physical meaning of gas-generator pressure and ambient coefficients",()=>{
  const html=renderComponentWorkspace([
    component("gas_generator",[field("preburner_pressure_MPa",6,"MPa")]),
    component("nozzle",[field("Cf_sl",1.7)]),
    component("ox_pump",[field("power_hydraulic_W",2,"MW")]),
  ]);
  assert.match(html,/title="preburner_pressure_MPa">pGG<\/td>/);
  assert.match(html,/title="Cf_sl">CF,amb<\/td>/);
  assert.match(html,/title="power_hydraulic_W">Pinc<\/td>/);
});

test("nozzle comparisons describe the recorded environment and never claim a coefficient balance",()=>{
  const item=nozzle();
  item.fields.push(field("Cf",1.7),field("Cf_momentum",1.8),field("Cf_pressure",-.05));
  const html=renderComponentWorkspace([item]);
  assert.match(html,/推力对比。计算工况 1,000 kN；真空 1,200 kN/);
  assert.match(html,/比冲对比。计算工况 300 s；真空 360 s/);
  assert.match(html,/0\.04 MPa/);
  assert.doesNotMatch(html,/海平面|守恒|安全|通过/);
  assert.equal((html.match(/<svg class="ca-chart"/g)||[]).length,2);
});

test("combustion charts use actual branch flows, including nonclosing reported totals",()=>{
  const item=component("gas_generator",[
    field("mdot_ox_kgs",4,"kg/s"),field("mdot_fuel_kgs",2,"kg/s"),field("mdot_total_kgs",9,"kg/s"),
    field("T_combustion_K",900,"K"),
  ],"燃气发生器");
  const html=renderComponentWorkspace([item]);
  assert.match(html,/已报告支路流量。氧化剂 4 kg\/s；燃料 2 kg\/s/);
  assert.match(html,/<td class="ca-value">9<\/td><td class="ca-unit">kg\/s<\/td>/);
  assert.doesNotMatch(html,/66\.667|33\.333|100%|总流量 6|平衡通过/);
  const dual=renderComponentWorkspace([component("gg_turbine_ox",[],"GG 涡轮（液氧泵轴）")]);
  assert.match(dual,/GG 涡轮（液氧泵轴）/);
  assert.doesNotMatch(dual,/富氧/);
});

test("all text and attributes are escaped, and nonfinite values remain missing without mutation",()=>{
  const attack='<img src=x onerror="alert(1)">\'&';
  const item=component(attack,[field(attack,attack,attack,attack),field("n1",NaN),field("n2",Infinity),field("n3",-Infinity),field("n4",null)],attack);
  const before=structuredClone(item);
  Object.freeze(item.fields);
  Object.freeze(item);
  const html=renderComponentWorkspace([item]);
  assert.doesNotMatch(html,/<img|onerror="alert|NaN|Infinity/);
  assert.match(html,/&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;&#39;&amp;/);
  assert.equal((html.match(/<td class="ca-value">—<\/td>/g)||[]).length,4);
  assert.deepEqual(item,before);
});

// A deliberately small DOM double exercises delegated clicks and remount behavior without a browser package.
class Element {
  constructor(dataset={}) {this.dataset=dataset;this.attributes={};this.classes=new Set();this.hidden=false;this.disabled=false;this.textContent="";this.classList={toggle:(name,on)=>on ? this.classes.add(name) : this.classes.delete(name)};}
  setAttribute(name,value) {this.attributes[name]=value;}
  closest() {return this;}
}
function workspace(ids=["ox_pump","fuel_pump","nozzle"]) {
  const categories=["all","supply","shaft","thrust"].map(id=>new Element({caCategory:id}));
  categories[2].disabled=true;
  const kind=id=>id==="nozzle" ? "thrust" : "supply";
  const buttons=ids.map(id=>new Element({caObject:id,caKind:kind(id)}));
  const panels=ids.map(id=>new Element({caPanel:id,caKind:kind(id),caTitle:`title:${id}`}));
  const groups=["supply","thrust"].map(id=>new Element({caGroup:id}));
  const selection=new Element(),count=new Element();
  const elements={"[data-ca-category]":categories,"[data-ca-object]":buttons,"[data-ca-panel]":panels,"[data-ca-group]":groups};
  return {
    categories,buttons,panels,groups,selection,count,
    querySelectorAll:selector=>elements[selector] || [],
    querySelector:selector=>selector==="[data-ca-selection]" ? selection : selector==="[data-ca-visible-count]" ? count : null,
    contains:element=>Object.values(elements).some(group=>group.includes(element)),
  };
}
function host() {
  return {
    dataset:{},workspace:workspace(),listeners:new Set(),
    querySelector(selector){return selector===".ca-workspace" ? this.workspace : null;},
    addEventListener(type,listener){assert.equal(type,"click");this.listeners.add(listener);},
    removeEventListener(type,listener){assert.equal(type,"click");this.listeners.delete(listener);},
    click(target){for (const listener of [...this.listeners]) listener({target});},
  };
}
const button=(node,id)=>node.workspace.buttons.find(item=>item.dataset.caObject===id);
const category=(node,id)=>node.workspace.categories.find(item=>item.dataset.caCategory===id);

test("mount selects objects, filters categories and exposes the selected accessible state",()=>{
  const node=host();
  mountComponentWorkspace(node,"record-a");
  assert.equal(node.dataset.caSelected,"ox_pump");
  assert.equal(button(node,"ox_pump").attributes["aria-current"],"true");
  assert.equal(node.workspace.panels.filter(panel=>!panel.hidden).length,1);
  node.click(button(node,"fuel_pump"));
  assert.equal(node.dataset.caSelected,"fuel_pump");
  assert.equal(node.workspace.selection.textContent,"title:fuel_pump");
  node.click(category(node,"thrust"));
  assert.equal(node.dataset.caSelected,"nozzle");
  assert.equal(node.dataset.caCategory,"thrust");
  assert.equal(node.workspace.count.textContent,"1");
  assert.equal(node.workspace.groups[0].hidden,true);
  assert.equal(category(node,"thrust").attributes["aria-pressed"],"true");
  node.click(category(node,"shaft"));
  assert.equal(node.dataset.caCategory,"thrust","disabled category does nothing");
  node.click(category(node,"all"));
  assert.equal(node.dataset.caSelected,"nozzle","all preserves selection");
  assert.equal(node.workspace.count.textContent,"3");
  assert(node.workspace.groups.every(group=>!group.hidden));
});

test("mount preserves state on the same record, resets on a new record, and removes stale handlers",()=>{
  const node=host();
  mountComponentWorkspace(node,"record-a");
  node.click(category(node,"thrust"));
  const oldButton=button(node,"fuel_pump");
  node.workspace=workspace();
  mountComponentWorkspace(node,"record-a");
  assert.equal(node.listeners.size,1);
  assert.equal(node.dataset.caCategory,"thrust");
  assert.equal(node.dataset.caSelected,"nozzle");
  node.click(oldButton);
  assert.equal(node.dataset.caSelected,"nozzle","detached nodes do not change selection");
  node.workspace=workspace();
  mountComponentWorkspace(node,"record-b");
  assert.equal(node.dataset.caCategory,"all");
  assert.equal(node.dataset.caSelected,"ox_pump");
  assert.equal(node.listeners.size,1);
  node.workspace=null;
  mountComponentWorkspace(node,"record-b");
  assert.equal(node.listeners.size,0);
});

test("mount handles changed availability and hostile component IDs without constructing CSS selectors",()=>{
  const node=host();
  mountComponentWorkspace(node,"record-a");
  node.click(category(node,"thrust"));
  node.workspace=workspace(["fuel_pump"]);
  category(node,"thrust").disabled=true;
  mountComponentWorkspace(node,"record-a");
  assert.equal(node.dataset.caCategory,"all");
  assert.equal(node.dataset.caSelected,"fuel_pump");
  const hostile='pump"] [onload="alert(1)';
  node.workspace=workspace([hostile]);
  mountComponentWorkspace(node,"record-b");
  assert.equal(node.dataset.caSelected,hostile);
  assert.equal(node.workspace.panels[0].hidden,false);
  assert.equal(node.workspace.selection.textContent,`title:${hostile}`);
  mountComponentWorkspace(null,"missing");
});
