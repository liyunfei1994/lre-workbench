import assert from "node:assert/strict";
import {test} from "node:test";
import {buildCycleModel} from "../webapp/static/cycle-model.js";
import {buildFlowModel} from "../webapp/static/flow-model.js";

// These are the original topology contracts emitted by cycle.py and gg_cycle.py.
const topology = type => {
  const keys = type === "ffsc"
    ? ["lox_tank","fuel_tank","ox_pump","fuel_pump","fuel_cooling","ox_preburner","fuel_preburner","main_chamber","nozzle"]
    : ["lox_tank","fuel_tank","ox_pump","fuel_pump","fuel_cooling","gas_generator","gg_turbine","gg_exhaust","main_chamber","nozzle"];
  const pairs = type === "ffsc" ? [
    ["lox_tank","ox_pump"],["fuel_tank","fuel_pump"],["fuel_pump","fuel_cooling"],
    ["ox_pump","ox_preburner"],["ox_pump","fuel_preburner"],
    ["fuel_cooling","ox_preburner"],["fuel_cooling","fuel_preburner"],
    ["ox_preburner","main_chamber"],["fuel_preburner","main_chamber"],["main_chamber","nozzle"],
  ] : [
    ["lox_tank","ox_pump"],["fuel_tank","fuel_pump"],["ox_pump","main_chamber"],
    ["fuel_pump","fuel_cooling"],["fuel_cooling","main_chamber"],
    ["ox_pump","gas_generator"],["fuel_pump","gas_generator"],
    ["gas_generator","gg_turbine"],["gg_turbine","gg_exhaust"],["main_chamber","nozzle"],
  ];
  return {topology_nodes:keys.map((key,index)=>({key,label:key,pos:[index*100,100]})),
    topology_edges:pairs.map(([source,target])=>({source,target,label:"original"}))};
};
const component = outputs => ({outputs});
const state = (pressure,temperature,mass_flow) => ({pressure,temperature,mass_flow,composition:{CH4:.8,H2:.2},metadata:{}});
function fixture(type="ffsc", layout="single_shaft") {
  const states={
    lox_tank:state(.3e6,70,600),fuel_tank:state(.3e6,100,160),
    lox_pump_inlet:state(.95e6,70,600),fuel_pump_inlet:state(.95e6,100,160),
    lox_after_pump:state(70e6,83,600),fuel_after_pump:state(71e6,121,160),
    fuel_after_cooling:state(66e6,380,type==="ffsc"?160:154),main_chamber:state(33e6,3770,type==="ffsc"?760:752),
  };
  const components={
    ox_pump:component({power_W:37e6,nr_rpm:26500}),fuel_pump:component({power_MW:30,nr_rpm:38000}),
    cooling_channel:component({heat_load_W:130e6}),main_chamber:component({T_chamber_K:3770}),
    nozzle:component({thrust_N:2450e3,thrust_vac_N:2590e3,Isp_vac_s:346,p_exit_Pa:79000,T_exit_K:1325.755}),
  };
  const summary={mdot_total_kg_s:760,thrust_N:2453e3,thrust_vac_N:2595e3,Isp_vac_s:347};
  if (type==="ffsc") {
    Object.assign(states,{ox_preburner_outlet:state(61e6,750,580),fuel_preburner_outlet:state(57e6,850,180),
      ox_turbine_exhaust:state(41e6,690,580),fuel_turbine_exhaust:state(36e6,803,180)});
    Object.assign(components,{
      ox_preburner:component({mdot_ox_kgs:570,mdot_fuel_kgs:10}),fuel_preburner:component({mdot_ox_kgs:30,mdot_fuel_kgs:150}),
      ox_preburner_turbine:component({p_in_Pa:61e6,p_out_Pa:41e6,T_exhaust_K:690,mass_flow_kgs:580,turbine_power_W:37e6,shaft_speed_rpm:26500}),
      fuel_preburner_turbine:component({p_in_Pa:57e6,p_out_Pa:36e6,T_exhaust_K:803,mass_flow_kgs:180,turbine_power_MW:30,shaft_speed_rpm:38000}),
    });
  } else {
    Object.assign(states,{gg_outlet:state(10e6,850,8),gg_turbine_exhaust:state(.4e6,620,8)});
    Object.assign(components,{gas_generator:component({mdot_ox_kgs:2,mdot_fuel_kgs:6}),
      gg_exhaust_nozzle:component({thrust_N:3000,thrust_vac_N:5000,Isp_vac_s:64,exit_pressure_Pa:101325})});
    const turbine=(flow,power,temp)=>component({p_in_Pa:10e6,p_out_Pa:.4e6,T_exhaust_K:temp,mass_flow_kgs:flow,turbine_power_MW:power,shaft_speed_rpm:12000});
    if (layout==="dual_shaft") Object.assign(components,{gg_turbine_ox:turbine(5,4,620),gg_turbine_fuel:turbine(3,3,640)});
    else components.gg_turbine=turbine(8,7,620);
    Object.assign(summary,{gg_layout_dual:layout==="dual_shaft"?1:0,gg_mech_efficiency:.97,gg_turbine_power_delivered_MW:6.79});
  }
  return {calculation:{inputs:{cycle_type:type,propellant_pair:"lox_methane",gg_layout:layout},states,components,summary,...topology(type)}};
}
function build(result, ids=Object.keys(result.calculation?.components || {})) {
  const calc=result.calculation || {};
  return buildCycleModel(result,buildFlowModel(calc.states,{inputs:calc.inputs || result.submitted_inputs,componentIds:ids}),ids.map(id=>({id})));
}
const node=(model,id)=>model.nodes.find(item=>item.id===id);
const value=(model,id,label)=>node(model,id).metrics.find(item=>item.label===label)?.value;
const edge=(model,source,target)=>model.connections.find(item=>item.source===source && item.target===target);
const freezeDeep=value=>{
  if (value && typeof value==="object") {Object.values(value).forEach(freezeDeep);Object.freeze(value);}
  return value;
};

test("FFSC expands real turbines, connects fuel cross feed after cooling, and keeps separate shafts",()=>{
  const model=build(fixture());
  assert.equal(model.kind,"ffsc");
  assert.equal(model.nodes.length,11);
  assert.ok(edge(model,"cooling_channel","ox_preburner"));
  assert.ok(edge(model,"cooling_channel","fuel_preburner"));
  assert.equal(edge(model,"fuel_pump","ox_preburner"),undefined);
  assert.equal(edge(model,"ox_preburner","main_chamber"),undefined);
  assert.ok(edge(model,"ox_preburner","ox_preburner_turbine"));
  assert.ok(edge(model,"ox_preburner_turbine","main_chamber"));
  assert.deepEqual(model.shafts.map(({source,target})=>[source,target]),[
    ["ox_preburner_turbine","ox_pump"],["fuel_preburner_turbine","fuel_pump"],
  ]);
  assert.deepEqual(node(model,"ox_preburner_turbine").states.map(item=>item.id),["ox_preburner_outlet","ox_turbine_exhaust"]);
  assert.match(edge(model,"cooling_channel","ox_preburner").label,/10 kg\/s/);
  assert.match(edge(model,"fuel_pump","cooling_channel").label,/160 kg\/s/);
});

test("GG single shaft takes generator fuel before cooling and exhausts separately",()=>{
  const model=build(fixture("gg"));
  assert.equal(model.kind,"gg-single");
  assert.ok(edge(model,"fuel_pump","gas_generator"));
  assert.equal(edge(model,"cooling_channel","gas_generator"),undefined);
  assert.match(edge(model,"fuel_pump","cooling_channel").label,/154 kg\/s/);
  assert.match(edge(model,"fuel_pump","gas_generator").label,/6 kg\/s/);
  assert.ok(edge(model,"gg_turbine","gg_exhaust_nozzle"));
  assert.equal(edge(model,"gg_turbine","main_chamber"),undefined);
  assert.deepEqual(model.shafts.map(({source,target})=>[source,target]),[["gg_turbine","ox_pump"],["gg_turbine","fuel_pump"]]);
  assert.equal(model.summary.find(item=>item.label==="涡轮可用轴功率").value,6.79);
  assert.equal(value(model,"gg_turbine","涡轮功率"),7);
});

test("GG dual shaft uses separate component outputs and never relabels aggregate G06 as either outlet",()=>{
  const model=build(fixture("gg","dual_shaft"));
  assert.equal(model.kind,"gg-dual");
  assert.equal(node(model,"gg_turbine"),undefined);
  assert.equal(value(model,"gg_turbine_ox","出口温度"),620);
  assert.equal(value(model,"gg_turbine_fuel","出口温度"),640);
  assert.equal(value(model,"gg_turbine_ox","燃气流量"),5);
  assert.equal(value(model,"gg_turbine_fuel","燃气流量"),3);
  assert.deepEqual(node(model,"gg_turbine_ox").states,[]);
  assert.deepEqual(node(model,"gg_turbine_fuel").states,[]);
  assert.match(model.notes.join(" "),/G06.*总量.*并非.*混合温度/);
  assert.ok(edge(model,"gas_generator","gg_turbine_ox"));
  assert.ok(edge(model,"gas_generator","gg_turbine_fuel"));
  assert.equal(edge(model,"gg_turbine_ox","gg_turbine_fuel"),undefined);
  assert.deepEqual(model.shafts.map(({source,target})=>[source,target]),[["gg_turbine_ox","ox_pump"],["gg_turbine_fuel","fuel_pump"]]);
});

test("metrics preserve correct display units and distinguish main-nozzle thrust from whole engine",()=>{
  const model=build(fixture("gg"));
  assert.equal(value(model,"ox_pump","出口压力"),70);
  assert.equal(value(model,"ox_pump","轴功率需求"),37);
  assert.equal(value(model,"fuel_pump","轴功率需求"),30);
  assert.equal(value(model,"cooling_channel","吸热量"),130);
  assert.equal(value(model,"nozzle","当前环境推力"),2450);
  assert.equal(value(model,"gg_exhaust_nozzle","当前环境推力"),3);
  assert.equal(value(model,"nozzle","出口压力"),.079);
  assert.equal(value(model,"nozzle","出口温度"),1325.755);
  assert.equal(value(model,"gg_exhaust_nozzle","出口温度"),null);
  assert.equal(model.summary.find(item=>item.label==="当前环境总推力").value,2453);
  assert.equal(node(model,"ox_pump").metrics.find(item=>item.label==="出口压力").unit,"MPa");
});

test("saved calculation inputs override submitted values and old records fall back to submitted inputs",()=>{
  const result=fixture("gg");
  result.calculation.inputs.propellant_pair="lox_kerosene";
  result.submitted_inputs={propellant_pair:"lox_methane",cycle_type:"ffsc",gg_layout:"dual_shaft"};
  let model=build(result);
  assert.equal(model.kind,"gg-single");
  assert.equal(model.fuel.name,"煤油");
  assert.equal(node(model,"fuel_pump").title,"煤油泵");
  assert.match(model.fuel.pair,/正十二烷替代/);
  result.submitted_inputs={...result.calculation.inputs};
  delete result.calculation.inputs;
  model=build(result);
  assert.equal(model.kind,"gg-single");
  assert.equal(model.fuel.name,"煤油");
});

test("incomplete or unknown topology preserves original graph without inferred equipment or shafts",()=>{
  for (const mutate of [
    calc=>calc.topology_edges.pop(),
    calc=>calc.topology_nodes.push({key:"unknown",label:"<new>",pos:[0,0]}),
    calc=>calc.topology_edges[0]={source:"lox_tank",target:"fuel_pump",label:"custom"},
    calc=>calc.topology_nodes[0]={...calc.topology_nodes[0],key:"custom_tank"},
    calc=>calc.inputs.cycle_type="unsupported",
  ]) {
    const result=fixture();mutate(result.calculation);
    const model=build(result);
    assert.equal(model.kind,"generic");
    assert.deepEqual(model.shafts,[]);
    assert.equal(node(model,"ox_preburner_turbine"),undefined);
    assert.deepEqual(model.originalNodes,result.calculation.topology_nodes);
    assert.deepEqual(model.originalEdges,result.calculation.topology_edges);
  }
  assert.deepEqual(buildCycleModel(null).nodes,[]);
});

test("GG layout requires consistent record evidence and can use old component-only evidence",()=>{
  const old=fixture("gg","dual_shaft");
  delete old.calculation.inputs.gg_layout;delete old.calculation.summary.gg_layout_dual;
  assert.equal(build(old).kind,"gg-dual");
  for (const mutate of [
    calc=>{calc.inputs.gg_layout="dual_shaft";},
    calc=>{calc.summary.gg_layout_dual=1;},
    calc=>{calc.inputs.gg_layout="unknown";},
    calc=>{delete calc.inputs.gg_layout;delete calc.summary.gg_layout_dual;delete calc.components.gg_turbine;},
    calc=>{delete calc.inputs.gg_layout;delete calc.summary.gg_layout_dual;delete calc.components.gg_turbine;calc.components.gg_turbine_ox=component({});},
  ]) {
    const result=fixture("gg");mutate(result.calculation);
    const model=build(result);
    assert.equal(model.kind,"generic");
    assert.deepEqual(model.shafts,[]);
    assert.match(model.notes.join(" "),/轴系信息缺失或相互不一致/);
  }
});

test("missing outlet states do not use available inlet pressure or temperature as outlet",()=>{
  const result=fixture();
  delete result.calculation.states.lox_after_pump;
  delete result.calculation.states.ox_turbine_exhaust;
  result.calculation.components.ox_pump=component({power_W:37e6});
  result.calculation.components.ox_preburner_turbine=component({turbine_power_W:37e6});
  const model=build(result);
  assert.equal(value(model,"ox_pump","出口压力"),null);
  assert.equal(value(model,"ox_pump","出口温度"),null);
  assert.equal(value(model,"ox_preburner_turbine","入口压力"),61);
  assert.equal(value(model,"ox_preburner_turbine","出口压力"),null);
  assert.equal(value(model,"ox_preburner_turbine","出口温度"),null);
  assert.equal(node(model,"ox_pump").states.length,1);
});

test("missing branch flow and shaft power remain unavailable without fabricated differences or products",()=>{
  const result=fixture("gg","dual_shaft");
  result.calculation.components.gg_turbine_ox=component({p_in_Pa:10e6});
  delete result.calculation.components.gas_generator.outputs.mdot_fuel_kgs;
  delete result.calculation.summary.gg_turbine_power_delivered_MW;
  const model=build(result);
  assert.equal(value(model,"gg_turbine_ox","燃气流量"),null);
  assert.equal(value(model,"gg_turbine_ox","涡轮功率"),null);
  assert.doesNotMatch(edge(model,"gas_generator","gg_turbine_ox").label,/kg\/s/);
  assert.doesNotMatch(edge(model,"fuel_pump","gas_generator").label,/kg\/s/);
  assert.doesNotMatch(edge(model,"ox_pump","main_chamber").label,/kg\/s/);
  assert.equal(model.summary.find(item=>item.label==="涡轮可用轴功率").value,null);
});

test("nonfinite and numeric-string values stay missing while real zero is preserved",()=>{
  const result=fixture("gg","dual_shaft");
  result.calculation.components.gg_turbine_ox=component({p_in_Pa:Infinity,p_out_Pa:"400000",T_exhaust_K:NaN,mass_flow_kgs:0,turbine_power_W:-Infinity,shaft_speed_rpm:null});
  result.calculation.summary.thrust_N=Infinity;
  const model=build(result);
  assert.deepEqual(node(model,"gg_turbine_ox").metrics.map(item=>item.value),[null,null,null,0,null,null]);
  assert.equal(model.summary.find(item=>item.label==="当前环境总推力").value,null);
  assert.match(edge(model,"gas_generator","gg_turbine_ox").label,/0 kg\/s/);
});

test("component links use only actually renderable IDs and never infer inspector availability",()=>{
  const model=build(fixture(),["fuel_pump","nozzle"]);
  assert.equal(node(model,"fuel_pump").componentId,"fuel_pump");
  assert.equal(node(model,"nozzle").componentId,"nozzle");
  assert.equal(node(model,"ox_pump").componentId,null);
  assert.equal(node(model,"lox_tank").componentId,null);
});

test("adapter does not mutate saved records and returns independent nested state and topology copies",()=>{
  const result=fixture();
  const ids=Object.keys(result.calculation.components);
  const flow=buildFlowModel(result.calculation.states,{inputs:result.calculation.inputs,componentIds:ids});
  const before=structuredClone(result),beforeFlow=structuredClone(flow);
  const model=buildCycleModel(freezeDeep(result),freezeDeep(flow),freezeDeep(ids.map(id=>({id}))));
  assert.deepEqual(result,before);assert.deepEqual(flow,beforeFlow);
  node(model,"fuel_pump").states[0].composition.CH4=0;
  model.originalNodes[0].pos[0]=-1;
  assert.deepEqual(result,before);assert.deepEqual(flow,beforeFlow);
});

test("unknown labels remain literal data for UI escaping, and dangling original edges are not rendered",()=>{
  const result={calculation:{topology_nodes:[{key:"custom",label:'<img src=x onerror="alert(1)">',pos:[1,2]}],
    topology_edges:[{source:"custom",target:"missing",label:"dangling"}],inputs:{propellant_pair:"unknown"}}};
  const model=build(result);
  assert.equal(model.kind,"generic");
  assert.equal(model.nodes[0].title,'<img src=x onerror="alert(1)">');
  assert.equal(model.fuel.name,"燃料");
  assert.deepEqual(model.connections,[]);
  assert.equal(model.originalEdges.length,1);
});

test("generic graph filters invalid legacy coordinates and adapts saved fuel labels without changing originals",()=>{
  const result={calculation:{inputs:{propellant_pair:"lox_kerosene"},topology_nodes:[
    {key:"fuel_tank",label:"甲烷储箱",pos:[0,0]},
    {key:"fuel_pump",label:"甲烷泵",pos:[100,0]},
    {key:"bad",label:"非法坐标",pos:[null,10]},
    {key:"missing",label:"缺失坐标"},
  ],topology_edges:[
    {source:"fuel_tank",target:"fuel_pump",label:"主路 CH4"},
    {source:"fuel_pump",target:"bad",label:"不显示"},
  ]}};
  const before=structuredClone(result),model=build(result);
  assert.equal(model.kind,"generic");
  assert.deepEqual(model.nodes.map(item=>item.title),["煤油储箱","煤油泵"]);
  assert.equal(model.connections.length,1);
  assert.equal(model.connections[0].label,"主路 C₁₂H₂₆");
  assert.deepEqual(model.originalNodes,before.calculation.topology_nodes);
  assert.deepEqual(model.originalEdges,before.calculation.topology_edges);
  assert.deepEqual(result,before);
});

test("partial FFSC preburner keeps only its actual outlet state and does not attach turbine exhaust",()=>{
  const result=fixture();
  result.calculation.topology_nodes=[{key:"ox_preburner",label:"富氧预燃室",pos:[100,100]}];
  result.calculation.topology_edges=[];
  const model=build(result);
  assert.equal(model.kind,"generic");
  assert.deepEqual(model.nodes.map(item=>item.id),["ox_preburner"]);
  assert.deepEqual(model.nodes[0].states.map(item=>item.id),["ox_preburner_outlet"]);
  assert.equal(model.nodes[0].componentId,"ox_preburner");
  assert.deepEqual(model.shafts,[]);
});
