import assert from "node:assert/strict";
import {test} from "node:test";
import {renderFlowWorkspace,mountFlowWorkspace,flowChartData} from "../webapp/static/flow-analysis.js";

const state=(id,group="ox",extra={})=>({id,code:id.toUpperCase(),title:`位置 ${id}`,group,pressure:.9,temperature:80,massFlow:12,density:1100,enthalpy:-200,composition:{O2:1},metadata:[],componentIds:[],...extra});
const group=(id,segments,title=id)=>({id,title,caption:"流路顺序",color:"#427ca4",segments});
const model=()=>({nodes:[state("o1"),state("o2","ox",{pressure:20,temperature:90,componentIds:["ox_pump"]}),state("f1","fuel",{composition:{CH4:.91,H2:.00002}})],groups:[group("ox",[["o1","o2"]],"液氧供给"),group("fuel",[["f1"]],"燃料供给")]});

test("workspace separates path states from component performance and offers classified selection",()=>{
  const html=renderFlowWorkspace(model());
  assert.match(html,/流路状态分析/);
  assert.match(html,/离散节点，间距不代表空间距离或时间/);
  assert.match(html,/跨部件的 <b>压升、功率、效率与换热量/);
  assert.equal((html.match(/data-sa-panel=/g)||[]).length,3);
  assert.equal((html.match(/data-sa-row=/g)||[]).length,3);
  assert.equal((html.match(/data-sa-charts=/g)||[]).length,2);
  assert.match(html,/data-sa-component="ox_pump"/);
  assert.match(html,/data-sa-topology="o2">查看循环原理图/);
  assert.match(html,/title="-200 kJ\/kg">-200/);
});

test("charts never connect separate branches or skip missing values",()=>{
  const nodes=[state("a"),state("b"),state("c","ox",{pressure:null}),state("d"),state("e")];
  const route=group("ox",[["a","b"],["d","e"]]);
  assert.deepEqual(flowChartData(nodes,route,"pressure").fragments,[["a","b"],["d","e"]]);
  const missing=flowChartData(nodes,group("ox",[["a","c","d","missing","e"]]),"pressure");
  assert.deepEqual(missing.fragments,[["a"],["d"],["e"]]);
  assert(!missing.points.some(node=>node.id==="c"));
  const html=renderFlowWorkspace({nodes,groups:[route]});
  assert.equal((html.match(/<polyline/g)||[]).length,4,"two fragments on each of two quantity plots");
});

test("missing quantities never become zero, and all-missing charts have an explicit empty state",()=>{
  const nodes=[state("a","ox",{pressure:null,temperature:NaN,massFlow:Infinity,density:null,enthalpy:null})];
  const html=renderFlowWorkspace({nodes,groups:[group("ox",[["a"]])]});
  assert.match(html,/尚未返回压力数据/);
  assert.match(html,/尚未返回温度数据/);
  assert.doesNotMatch(html,/NaN|Infinity|<polyline|<circle/);
  assert.match(html,/title="未提供 kg\/s">—/);
});

test("composition preserves raw mass fractions and separates trace species without normalization",()=>{
  const source=model(); source.nodes[0].composition={O2:.8,H2O:.1,H2:.00002,missing:null};
  const before=structuredClone(source);
  const html=renderFlowWorkspace(source);
  assert.match(html,/class="sa-species-fill" width="80"/);
  assert.match(html,/class="sa-species-fill" width="10"/);
  assert.match(html,/原始质量分数 0\.00002/);
  assert.match(html,/痕量组分 · 1 项/);
  assert.match(html,/按原始质量分数展示，不自动归一化/);
  assert.match(html,/title="0\.90002">0\.9/);
  assert.deepEqual(source,before);
});

test("all text is escaped and group colors use CSP-compatible fixed palette selectors",()=>{
  const attack='<img src=x onerror="alert(1)">\'&';
  const source={nodes:[state(attack,attack,{code:attack,title:attack,composition:{[attack]:1},metadata:[{key:attack,label:attack,value:{unsafe:attack},unit:attack}],componentIds:[attack]})],groups:[{...group(attack,[[attack]],attack),caption:attack,color:'red; background:url("x")'}]};
  const html=renderFlowWorkspace(source);
  assert.doesNotMatch(html,/<img|onerror="alert|background:url/);
  assert.match(html,/&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;&#39;&amp;/);
  assert.match(html,/data-sa-color="other"/);
  assert.doesNotMatch(html,/\sstyle=/i);
});

test("every workspace color and species bar works with style-src self and no inline styles",()=>{
  const html=renderFlowWorkspace(model());
  assert.doesNotMatch(html,/\sstyle=|<style/i);
  assert.match(html,/data-sa-color="ox"/);
  assert.match(html,/data-sa-color="fuel"/);
  assert.match(html,/class="sa-species-track" viewBox="0 0 100 5"/);
  assert.match(html,/class="sa-species-fill" width="100" height="5"/);
});

test("missing or invalid composition does not falsely report a zero sum",()=>{
  const source=model();
  source.nodes=[state("o1","ox",{composition:{O2:null,H2:NaN,H2O:Infinity}})];
  const html=renderFlowWorkspace(source);
  assert.match(html,/已报告质量分数之和<\/span><b title="未提供">—<\/b>/);
  assert.doesNotMatch(html,/NaN|Infinity/);
  source.nodes[0].composition={O2:0};
  assert.match(renderFlowWorkspace(source),/已报告质量分数之和<\/span><b title="0">0<\/b>/);
});

test("zero, constant and extreme finite values produce finite SVG coordinates",()=>{
  for (const values of [[0,0],[1,1],[1e307,1e308],[-1e308,1e308],[1e-308,2e-308]]) {
    const source={nodes:values.map((value,index)=>state(`n${index}`,"ox",{pressure:value,temperature:value})),groups:[group("ox",[["n0","n1"]])]};
    const html=renderFlowWorkspace(source);
    assert.doesNotMatch(html,/NaN|Infinity/);
    assert.match(html,/role="button" tabindex="0"/);
  }
});

test("empty, unclassified and duplicate records do not create phantom data",()=>{
  for (const value of [null,undefined,{}, {nodes:[]}]) assert.match(renderFlowWorkspace(value),/暂无流路状态数据/);
  const html=renderFlowWorkspace({nodes:[state("a","unknown"),state("a","unknown")]});
  assert.equal((html.match(/data-sa-panel=/g)||[]).length,1);
  assert.match(html,/其他记录节点/);
  assert.doesNotMatch(html,/<polyline/);
});

class Element {
  constructor(dataset={},role="") { this.dataset=dataset;this.role=role;this.attrs={};this.classes=new Set();this.hidden=false;this.textContent="";this.classList={toggle:(name,on)=>on ? this.classes.add(name) : this.classes.delete(name)}; }
  setAttribute(name,value) {this.attrs[name]=value;}
  closest(selector) {return selector.startsWith('[role="button"]') && this.role!=="button" ? null : this;}
  querySelector(selector) {return selector==="h3" ? {textContent:`group:${this.dataset.saRoute}`} : null;}
}
function workspace(ids=["o1","o2","f1"]) {
  const kind=id=>id.startsWith("f") ? "fuel" : "ox";
  const panels=ids.map(id=>new Element({saPanel:id,saKind:kind(id),saCode:id.toUpperCase(),saTitle:`title:${id}`}));
  const nodes=ids.map(id=>new Element({saNode:id,saKind:kind(id)}));
  const marks=ids.map(id=>new Element({saNode:id,saKind:kind(id)},"button"));
  const rows=ids.map(id=>new Element({saRow:id,saKind:kind(id)}));
  const tabs=["ox","fuel","all"].map(id=>new Element({saGroup:id}));
  const routes=["ox","fuel"].map(id=>new Element({saRoute:id})),charts=["ox","fuel"].map(id=>new Element({saCharts:id}));
  const topology=new Element({saTopology:"o2"}),component=new Element({saComponent:"ox_pump"});
  const count=new Element(),selection=new Element(),caption=new Element();
  const elements={"[data-sa-panel]":panels,"[data-sa-node]":[...nodes,...marks],"[data-sa-row]":rows,"[data-sa-group]":tabs,"[data-sa-route]":routes,"[data-sa-charts]":charts};
  return {panels,nodes,marks,rows,tabs,routes,charts,topology,component,count,selection,caption,
    querySelectorAll:selector=>elements[selector] || [],
    querySelector:selector=>({"[data-sa-visible-count]":count,"[data-sa-selection]":selection,"[data-sa-chart-caption]":caption}[selector] || null),
    contains:element=>Object.values(elements).some(list=>list.includes(element)) || element===topology || element===component,
  };
}
function host() {
  return {dataset:{},workspace:workspace(),listeners:{click:new Set(),keydown:new Set()},
    querySelector(selector){return selector===".sa-workspace" ? this.workspace : null;},
    addEventListener(type,listener){this.listeners[type].add(listener);},removeEventListener(type,listener){this.listeners[type].delete(listener);},
    click(target){this.listeners.click.forEach(fn=>fn({target}));},key(target,key){let prevented=false;this.listeners.keydown.forEach(fn=>fn({target,key,preventDefault(){prevented=true;}}));return prevented;},
  };
}
const find=(items,key,value)=>items.find(item=>item.dataset[key]===value);

test("classification, node row and chart marker selection stay synchronized",()=>{
  const node=host(); mountFlowWorkspace(node,"a");
  assert.equal(node.dataset.saGroup,"ox"); assert.equal(node.dataset.saSelected,"o1");
  assert.equal(node.workspace.rows[2].hidden,true);
  node.click(node.workspace.rows[1]); assert.equal(node.dataset.saSelected,"o2");
  assert.equal(node.workspace.nodes[1].attrs["aria-pressed"],"true");
  node.click(find(node.workspace.tabs,"saGroup","fuel"));
  assert.equal(node.dataset.saSelected,"f1"); assert.equal(node.workspace.charts[0].hidden,true);
  node.click(find(node.workspace.tabs,"saGroup","all")); assert(node.workspace.rows.every(row=>!row.hidden));
  assert.equal(node.workspace.count.textContent,"3");
  assert.equal(node.key(node.workspace.marks[1],"Enter"),true);
  assert.equal(node.dataset.saSelected,"o2"); assert.equal(node.dataset.saGroup,"all");
  assert.equal(node.workspace.charts[1].hidden,true,"all table still charts only the selected node's flow");
  assert.equal(node.workspace.panels.filter(panel=>!panel.hidden).length,1);
  assert.equal(node.workspace.selection.textContent,"O2 · title:o2");
});

test("callbacks carry exact IDs, and native buttons do not receive duplicate keyboard activation",()=>{
  const node=host(),calls=[]; mountFlowWorkspace(node,"a",{onComponent:id=>calls.push(["component",id]),onTopology:id=>calls.push(["topology",id])});
  node.click(node.workspace.topology); node.click(node.workspace.component);
  assert.deepEqual(calls,[["topology","o2"],["component","ox_pump"]]);
  assert.equal(node.key(node.workspace.nodes[1],"Enter"),false);
  assert.equal(node.dataset.saSelected,"o1");
});

test("same record restores selections, new records reset, and detached event listeners are removed",()=>{
  const node=host(); mountFlowWorkspace(node,"a"); node.click(find(node.workspace.tabs,"saGroup","fuel"));
  const old=node.workspace.nodes[0]; node.workspace=workspace(); mountFlowWorkspace(node,"a");
  assert.equal(node.dataset.saSelected,"f1"); assert.equal(node.dataset.saGroup,"fuel");
  assert.equal(node.listeners.click.size,1); assert.equal(node.listeners.keydown.size,1);
  node.click(old); assert.equal(node.dataset.saSelected,"f1");
  node.workspace=workspace(); mountFlowWorkspace(node,"b"); assert.equal(node.dataset.saSelected,"o1");
  node.workspace=null; mountFlowWorkspace(node,"b");
  assert.equal(node.listeners.click.size,0); assert.equal(node.listeners.keydown.size,0);
});

test("external topology selection and missing node fallback use IDs without CSS selector interpolation",()=>{
  const node=host(); node.dataset={saRecordId:"a",saGroup:"all",saSelected:"f1"};
  mountFlowWorkspace(node,"a"); assert.equal(node.dataset.saSelected,"f1");
  node.workspace=workspace(["o2"]); mountFlowWorkspace(node,"a"); assert.equal(node.dataset.saSelected,"o2");
  mountFlowWorkspace(null,"a");
});
