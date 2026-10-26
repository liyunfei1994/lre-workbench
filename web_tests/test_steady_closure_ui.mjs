import assert from "node:assert/strict";
import {test} from "node:test";
import {closureDisplayState, renderSteadyClosure} from "../webapp/static/steady-closure.js";

function fixture() {
  const routes=[
    ["ox_opb","LOX → 富氧预燃室","液氧主泵出口","富氧预燃室",570,84],
    ["ox_fpb","LOX → 富燃预燃室","液氧主泵出口","富燃预燃室",27,84],
    ["fuel_opb","CH₄ → 富氧预燃室","再生冷却出口","富氧预燃室",10,380],
    ["fuel_fpb","CH₄ → 富燃预燃室","再生冷却出口","富燃预燃室",156,380],
  ];
  return {calculation:{steady_closure:{schema_version:1,scope:"ffsc_lumped_design",assumptions:["全部甲烷经再生冷却后分流。"],feed_network:{status:"pass",mode:"design",branches:routes.map(([id,label,source,target,mass_flow_kg_s,temperature_K])=>({id,label,source,target,mass_flow_kg_s,temperature_K,temperature_location:"source_tap",source_pressure_Pa:70e6,destination_pressure_Pa:64e6,line_drop_Pa:1e6,injector_drop_Pa:3e6,valve_drop_Pa:2e6,pressure_residual_Pa:0,mass_residual_kg_s:0,status:"pass"})),max_pressure_residual_Pa:0,max_mass_residual_kg_s:0},energy_balance:{status:"pass",reference:"共同参考状态下的物理焓与生成焓",boundary:"主泵入口至主燃烧室出口的集总循环",terms:[{id:"inlet",label:"推进剂入口焓流",value_W:-20e6,role:"in",source:"入口质量流量与共同基准比焓"},{id:"outlet",label:"燃气出口焓流",value_W:-20e6,role:"out",source:"出口状态"},{id:"cooling",label:"再生冷却内部传热",value_W:100e6,role:"internal",source:"壁面吸热积分"}],checks:[{id:"loop",label:"集总循环能量",residual_W:0,tolerance_W:500,status:"pass"}],residual_W:0,tolerance_W:500,limitations:["本边界未计入喷管出口动能。"]}}}};
}

function pressureFixture() {
  const data=fixture(), network=data.calculation.steady_closure.feed_network;
  network.pressure_contract={schema_version:1,pressure_link_status:"pass",
    pressure_convention:{kind:"lumped_absolute_pressure",label:"集总绝对压力 / Pa",detail:"未独立解析总压与静压。"},
    pump_capacity:{status:"not_verified",detail:"缺少固定泵扬程/效率图，未证明固定硬件可提供该压力。",pumps:[
      {id:"ox",label:"液氧主泵",model_outlet_pressure_Pa:70e6,required_outlet_pressure_Pa:70e6,required_rise_Pa:69e6,pressure_residual_Pa:0,pressure_tolerance_Pa:1,pressure_link_status:"pass"},
      {id:"fuel",label:"甲烷主泵",model_outlet_pressure_Pa:75e6,required_outlet_pressure_Pa:75e6,required_rise_Pa:74e6,pressure_residual_Pa:0,pressure_tolerance_Pa:1,pressure_link_status:"pass"}]},
    gas_paths:["富氧燃气","富燃燃气"].map((label,index)=>({id:String(index),label,preburner_pressure_Pa:60e6,turbine_inlet_pressure_Pa:60e6,turbine_outlet_pressure_Pa:40e6,chamber_pressure_Pa:32e6,turbine_pressure_ratio:1.5,preburner_chamber_pressure_ratio:1.875,hot_gas_drop_Pa:8e6,pressure_residual_Pa:0,pressure_tolerance_Pa:1,status:"pass",detail:"预燃室出口直接作为涡轮入口。"})),
    parameter_roles:[{key:"cooling_dp_pa",label:"再生冷却给定压降",value:5e6,unit:"Pa",role:"specified_design_budget",source_status:"unverified_engineering_assumption",detail:"直接影响泵压需求，不是初值。"}],
    branch_state_links:network.branches.map(branch=>({id:branch.id,label:branch.label,source_state_residual_Pa:0,preburner_state_residual_Pa:0,branch_outlet_state_residual_Pa:0,max_pressure_residual_Pa:0,pressure_tolerance_Pa:1,status:"pass"}))};
  network.branches[0].pressure_nodes=[{id:"source_tap",label:"取流点",pressure_Pa:70e6},{id:"injector_upstream",label:"喷注器前（集总扣账）",pressure_Pa:67e6},{id:"preburner",label:"预燃室",pressure_Pa:64e6}];
  return data;
}

test("historical and unknown-version records remain unavailable and are not synthesized",()=>{
  for (const data of [{}, {calculation:{steady_closure:{schema_version:2,feed_network:{status:"pass"},energy_balance:{status:"pass"}}}}]) {
    const before=structuredClone(data), state=closureDisplayState(data), html=renderSteadyClosure(data);
    assert.equal(state.available,false);
    assert.equal(state.feed,"unavailable");
    assert.equal(state.energy,"unavailable");
    assert.match(html,/历史结果保持原样/);
    assert.doesNotMatch(html,/sc-pass/);
    assert.doesNotMatch(html,/<strong>0/);
    assert.deepEqual(data,before);
  }
});

test("all four routes and the energy boundary are visible, while detailed evidence is folded",()=>{
  const data=fixture(), before=structuredClone(data), state=closureDisplayState(data), html=renderSteadyClosure(data);
  assert.equal(state.feed,"pass");
  assert.equal(state.energy,"pass");
  for (const route of data.calculation.steady_closure.feed_network.branches) assert.ok(html.includes(route.label));
  assert.match(html,/再生冷却出口/);
  assert.match(html,/取流压力 \/ MPa/);
  assert.match(html,/目的压力 \/ MPa/);
  assert.match(html,/主泵入口至主燃烧室出口/);
  assert.match(html,/共同参考状态下的物理焓与生成焓/);
  assert.match(html,/<details class="sc-diagnostics"><summary>查看逐支路压力预算与守恒残差/);
  assert.match(html,/<details class="sc-diagnostics"><summary>查看分项残差与数据来源/);
  assert.doesNotMatch(html,/<details[^>]* open/);
  assert.deepEqual(data,before);
});

test("the same evidence renders through both raw CycleResults and web API envelopes",()=>{
  const data=fixture();
  assert.equal(renderSteadyClosure(data),renderSteadyClosure(data.calculation));
});

test("cooling hydraulic source uses only the saved mode and this calculation's pressure drop",()=>{
  const data=fixture();
  data.calculation.inputs={cooling_dp_auto:0,cooling_dp_pa:9e6};
  data.calculation.summary={cooling_dp_MPa:5};
  data.submitted_inputs={cooling_dp_auto:1};
  let html=renderSteadyClosure(data);
  assert.match(html,/冷却水力来源：给定压降，未证明与通道几何一致/);
  assert.match(html,/本次记录冷却压降：5 MPa/);
  assert.doesNotMatch(html,/本次记录冷却压降：9 MPa/);
  data.calculation.inputs.cooling_dp_auto=1;
  data.calculation.summary.cooling_dp_MPa=7.25;
  html=renderSteadyClosure(data);
  assert.match(html,/冷却水力来源：水力迭代计算/);
  assert.match(html,/本次记录冷却压降：7\.25 MPa/);
  assert.doesNotMatch(html,/未证明与通道几何一致/);
  assert.equal(html,renderSteadyClosure(data.calculation));
});

test("unified profile overrides obsolete input flags and inactive pressure budgets",()=>{
  const data=fixture();
  data.calculation.inputs={cooling_dp_auto:0,cooling_dp_pa:5e6};
  data.calculation.summary={cooling_dp_MPa:5};
  data.calculation.components={cooling_channel:{outputs:{delta_P_MPa:14.2599466,delta_P_source:1},
    profile:{version:1,model_id:"regen_quasi1d_ph_v1",summary:{dp_total_Pa:14.2599466e6}}}};
  const before=structuredClone(data), html=renderSteadyClosure(data);
  assert.match(html,/冷却水力来源：统一准一维沿程解/);
  assert.match(html,/本次记录冷却压降：14\.2599 MPa/);
  assert.doesNotMatch(html,/冷却水力来源：给定压降|本次记录冷却压降：5 MPa|同工况通道几何压降估算/);
  assert.deepEqual(data,before);
  assert.equal(html,renderSteadyClosure(data.calculation));
  data.calculation.components.cooling_channel.outputs.delta_P_MPa=5;
  assert.match(renderSteadyClosure(data),/保存的剖面与组件压降不一致/);
});

test("saved component pressure source takes precedence and unknown profiles stay unknown",()=>{
  const data=fixture();
  data.calculation.inputs={cooling_dp_auto:1};
  data.calculation.components={cooling_channel:{outputs:{mode:0,delta_P_source:0,delta_P_MPa:5}}};
  assert.match(renderSteadyClosure(data),/冷却水力来源：给定压降/);
  data.calculation.components.cooling_channel.profile={version:2,model_id:"regen_quasi1d_ph_v1"};
  const html=renderSteadyClosure(data);
  assert.match(html,/冷却水力来源：未知/);
  assert.match(html,/未识别版本的沿程数据/);
  assert.doesNotMatch(html,/冷却水力来源：水力迭代计算|冷却水力来源：给定压降/);
});

test("missing hydraulic provenance and invalid drops remain unknown instead of using draft defaults",()=>{
  const data=fixture();
  data.submitted_inputs={cooling_dp_auto:0,cooling_dp_pa:5e6};
  for (const value of [undefined,null,NaN,Infinity,-1,"5"]) {
    data.calculation.summary={cooling_dp_MPa:value};
    const html=renderSteadyClosure(data);
    assert.match(html,/冷却水力来源：未知/);
    assert.match(html,/本次记录冷却压降：— MPa/);
  }
  data.calculation.inputs={cooling_dp_auto:"0"};
  assert.match(renderSteadyClosure(data),/冷却水力来源：未知/);
  assert.match(renderSteadyClosure({}),/冷却水力来源：未知/);
});

test("same-state geometric pressure estimate is displayed only when saved and finite",()=>{
  const data=fixture();
  data.calculation.inputs={cooling_dp_auto:0};
  data.calculation.summary={cooling_dp_MPa:5};
  data.calculation.components={cooling_channel:{outputs:{delta_P_MPa:5,delta_P_hydraulic_MPa:9.75}}};
  let html=renderSteadyClosure(data);
  assert.match(html,/同工况通道几何压降估算：9\.75 MPa/);
  assert.match(html,/估算 − 采用值：\+4\.75 MPa/);
  assert.match(html,/不代表已验证的正确压降/);
  for (const value of [null,NaN,Infinity,-1,"9.75"]) {
    data.calculation.components.cooling_channel.outputs.delta_P_hydraulic_MPa=value;
    assert.doesNotMatch(renderSteadyClosure(data),/同工况通道几何压降估算/);
  }
  delete data.calculation.summary.cooling_dp_MPa;
  assert.match(renderSteadyClosure(data),/本次记录冷却压降：5 MPa/);
});

test("temperature labels distinguish saved tap states from post-loss preburner inlets",()=>{
  const data=fixture(), branches=data.calculation.steady_closure.feed_network.branches;
  assert.match(renderSteadyClosure(data),/取流温度 \/ K/);
  for (const branch of branches) branch.temperature_location="after_adiabatic_supply_loss";
  let html=renderSteadyClosure(data);
  assert.match(html,/供给损失后温度 \/ K/);
  assert.match(html,/温度对应等焓供给损失后的预燃室入口/);
  assert.doesNotMatch(html,/温度对应取流点/);
  branches[0].temperature_location="source_tap";
  html=renderSteadyClosure(data);
  assert.match(html,/记录温度 \/ K/);
  assert.match(html,/class="sc-temperature-location">取流温度/);
  assert.match(html,/class="sc-temperature-location">供给损失后温度/);
  delete branches[0].temperature_location;
  assert.match(renderSteadyClosure(data),/温度位置未记录/);
});

test("missing or nonfinite branch values cannot masquerade as zero or a passed network",()=>{
  for (const invalid of [null,undefined,NaN,Infinity,"70000000"]) {
    const data=fixture(), branch=data.calculation.steady_closure.feed_network.branches[0];
    branch.source_pressure_Pa=invalid;
    assert.equal(closureDisplayState(data).feed,"unavailable");
    const html=renderSteadyClosure(data);
    assert.match(html,/LOX → 富氧预燃室<\/td><td>液氧主泵出口<\/td><td>富氧预燃室<\/td><td>570<\/td><td>84<\/td><td>—<\/td>/);
    assert.doesNotMatch(html,/NaN|Infinity|undefined/);
  }
});

test("fewer than four unique branches cannot establish FFSC network closure",()=>{
  const data=fixture(), branches=data.calculation.steady_closure.feed_network.branches;
  branches[3].id=branches[2].id;
  assert.equal(closureDisplayState(data).feed,"unavailable");
  branches.pop();
  assert.equal(closureDisplayState(data).feed,"unavailable");
});

test("saved network tolerances expose inconsistent pass flags and minimum-head limitations",()=>{
  const data=fixture(), network=data.calculation.steady_closure.feed_network;
  Object.assign(network,{pressure_tolerance_Pa:1,mass_tolerance_kg_s:1e-8,pressure_reserve_status:"not_demonstrated"});
  Object.assign(network.branches[0],{source:"lox_after_pump",target:"ox_preburner",loss_source:"specified_design_pressure_budget"});
  let html=renderSteadyClosure(data);
  assert.match(html,/液氧主泵出口/);
  assert.match(html,/设计输入的喷注压降比与管路压降/);
  assert.match(html,/方程闭合未证明工程压力储备/);
  assert.match(html,/容差 1 Pa/);
  network.branches[0].pressure_residual_Pa=2;
  assert.equal(closureDisplayState(data).feed,"fail");
  network.branches[0].pressure_residual_Pa=0;
  network.max_mass_residual_kg_s=1e-7;
  assert.equal(closureDisplayState(data).feed,"fail");
});

test("negative valve loss and a signed pressure deficit preserve failed physical evidence",()=>{
  const data=fixture(), branch=data.calculation.steady_closure.feed_network.branches[2];
  branch.valve_drop_Pa=-4.2e6;
  branch.pressure_residual_Pa=-4.2e6;
  assert.equal(closureDisplayState(data).feed,"fail");
  assert.match(renderSteadyClosure(data),/-4\.200e\+6/);
  branch.valve_drop_Pa=0;
  branch.status="fail";
  assert.equal(closureDisplayState(data).feed,"fail");
});

test("energy closure requires a boundary, reference, complete terms and independent checks",()=>{
  for (const [key,value] of [["reference",""],["boundary",null],["terms",[]],["checks",[]],["residual_W",null],["tolerance_W",null]]) {
    const data=fixture();
    data.calculation.steady_closure.energy_balance[key]=value;
    assert.equal(closureDisplayState(data).energy,"unavailable",key);
  }
  const data=fixture();
  data.calculation.steady_closure.energy_balance.terms[1].value_W=null;
  assert.equal(closureDisplayState(data).energy,"unavailable");
  assert.match(renderSteadyClosure(data),/燃气出口焓流<\/td><td>输出边界<\/td><td>—/);
});

test("energy residual checks use absolute magnitude, retaining signs and recorded failure",()=>{
  const data=fixture(), energy=data.calculation.steady_closure.energy_balance;
  energy.residual_W=-501;
  assert.equal(closureDisplayState(data).energy,"fail");
  energy.residual_W=0;
  energy.checks[0].residual_W=501;
  assert.equal(closureDisplayState(data).energy,"fail");
  energy.checks[0].residual_W=0;
  energy.status="fail";
  assert.equal(closureDisplayState(data).energy,"fail");
});

test("internal heat is labelled separately and no unsupported whole-engine validation is asserted",()=>{
  const html=renderSteadyClosure(fixture());
  assert.match(html,/再生冷却内部传热<\/td><td>内部传递<\/td><td>\+100/);
  assert.match(html,/不重复计为边界净输入/);
  assert.match(html,/不能据此确认实际发动机流路或整机试验性能/);
  assert.match(html,/本边界未计入喷管出口动能/);
  assert.match(html,/推进剂入口焓流<\/td><td>输入边界<\/td><td>-20/);
});

test("malformed diagnostic entries remain readable without causing module startup failure",()=>{
  const data=fixture(), closure=data.calculation.steady_closure;
  closure.feed_network.branches=[null,12,{}];
  closure.energy_balance.terms=[null,"x",{}];
  closure.energy_balance.checks=[null,{},[]];
  assert.equal(closureDisplayState(data).feed,"unavailable");
  assert.equal(closureDisplayState(data).energy,"unavailable");
  assert.doesNotThrow(()=>renderSteadyClosure(data));
  closure.feed_network=null;
  closure.energy_balance=null;
  assert.doesNotThrow(()=>renderSteadyClosure(data));
  assert.equal(closureDisplayState(data).feed,"unavailable");
  assert.equal(closureDisplayState(data).energy,"unavailable");
});

test("all persisted labels and provenance are escaped as text",()=>{
  const data=fixture(), closure=data.calculation.steady_closure, attack='<img src=x onerror="alert(1)">';
  closure.assumptions=[attack];
  closure.feed_network.branches[0].label=attack;
  closure.energy_balance.reference=attack;
  closure.energy_balance.terms[0].source=attack;
  closure.energy_balance.limitations=[attack];
  const html=renderSteadyClosure(data);
  assert.doesNotMatch(html,/<img/);
  assert.ok((html.match(/&lt;img/g)||[]).length >= 5);
});

test("saved turbine ratio, PB chamber ratio, and pump demand have distinct identities",()=>{
  const data=pressureFixture(), before=structuredClone(data), html=renderSteadyClosure(data);
  assert.match(html,/πT = pT,in\/pT,out/);
  assert.match(html,/pPB\/pc/);
  assert.match(html,/<td>1\.5<\/td><td>1\.875<\/td><td>8<\/td>/);
  assert.match(html,/所需出口压力 \/ MPa/);
  assert.match(html,/液氧主泵<\/td><td>70<\/td><td>70<\/td><td>69<\/td>/);
  assert.match(html,/压力关系闭合/);
  assert.match(html,/sc-unavailable">固定泵能力未验证/);
  assert.doesNotMatch(html,/sc-pass">固定泵能力/);
  assert.match(html,/未独立解析总压与静压/);
  assert.deepEqual(data,before);
});

test("historical pressure records never synthesize new ratios or capacity from current inputs",()=>{
  const data=fixture();
  data.submitted_inputs={pc:32e6,ox_preburner_pressure_ratio:1.15,cooling_dp_pa:5e6};
  for (const contract of [undefined,null,{schema_version:2,gas_paths:pressureFixture().calculation.steady_closure.feed_network.pressure_contract.gas_paths}]) {
    data.calculation.steady_closure.feed_network.pressure_contract=contract;
    const html=renderSteadyClosure(data);
    assert.match(html,/本记录未保存压力定义与部件能力证据/);
    assert.doesNotMatch(html,/πT = pT,in\/pT,out/);
    assert.doesNotMatch(html,/压力关系闭合/);
  }
});

test("budget identity distinguishes inactive inputs and keeps missing demand values absent",()=>{
  const data=pressureFixture(), contract=data.calculation.steady_closure.feed_network.pressure_contract;
  let html=renderSteadyClosure(data);
  assert.match(html,/再生冷却给定压降<\/td><td>5 MPa<\/td><td>给定压降预算/);
  assert.match(html,/工程假设；未核实实机数据/);
  contract.parameter_roles[0].role="inactive_with_hydraulic_model";
  contract.pump_capacity.pumps[0].required_outlet_pressure_Pa=null;
  contract.pump_capacity.pumps[0].pressure_link_status="unavailable";
  html=renderSteadyClosure(data);
  assert.match(html,/当前不生效（采用水力模型）/);
  assert.match(html,/液氧主泵<\/td><td>70<\/td><td>—<\/td><td>69/);
  assert.match(html,/压力关系证据不足/);
  assert.doesNotMatch(html,/压力关系闭合/);
});

test("pressure link failures cannot be hidden behind a saved pass flag",()=>{
  const data=pressureFixture(), contract=data.calculation.steady_closure.feed_network.pressure_contract;
  contract.gas_paths[0].pressure_residual_Pa=-2;
  let html=renderSteadyClosure(data);
  assert.match(html,/压力关系未闭合/);
  assert.match(html,/富氧燃气<\/td><td>-2<\/td><td>1<\/td><td><span class="sc-badge sc-fail"/);
  contract.gas_paths[0].pressure_residual_Pa=null;
  html=renderSteadyClosure(data);
  assert.match(html,/压力关系证据不足/);
  assert.doesNotMatch(html,/压力关系闭合/);
});

test("historical pump demand conflicts remain visible outside folded evidence without rewriting",()=>{
  const data=pressureFixture(), contract=data.calculation.steady_closure.feed_network.pressure_contract;
  data.calculation.components={cooling_channel:{profile:{version:1,model_id:"regen_quasi1d_ph_v1",summary:{dp_total_Pa:14.2599466e6}}}};
  Object.assign(contract.pump_capacity.pumps[1],{model_outlet_pressure_Pa:87.7032811e6,required_outlet_pressure_Pa:78.4433345e6,required_rise_Pa:77.4933345e6});
  const before=structuredClone(data), html=renderSteadyClosure(data), visible=html.replace(/<details\b[\s\S]*?<\/details>/g,"");
  assert.match(visible,/保存的压力证据需复核/);
  assert.match(visible,/甲烷主泵存在压力关系差异/);
  assert.match(visible,/冷却压降身份报告与统一沿程模型不一致/);
  assert.match(visible,/87\.7033<\/td><td>78\.4433/);
  assert.match(visible,/历史记录未改写/);
  assert.match(visible,/压力关系未闭合/);
  assert.doesNotMatch(visible,/压力关系闭合/);
  assert.match(html,/<details class="sc-diagnostics"><summary>查看热气压力链与压力定义/);
  assert.deepEqual(data,before);
});

test("folded energy ledger retains failed check names and residuals in the visible summary",()=>{
  const data=fixture();
  data.calculation.steady_closure.energy_balance.checks[0].residual_W=501;
  const html=renderSteadyClosure(data), visible=html.replace(/<details\b[\s\S]*?<\/details>/g,"");
  assert.match(visible,/未闭合能量项/);
  assert.match(visible,/集总循环能量（残差 \+501 W）/);
  assert.match(html,/<details class="sc-diagnostics"><summary>查看能量收支明细/);
  assert.doesNotMatch(html,/<details[^>]* open/);
});

test("pressure link closure also requires all four saved branch state cross-checks",()=>{
  const data=pressureFixture(), contract=data.calculation.steady_closure.feed_network.pressure_contract;
  contract.branch_state_links[0].source_state_residual_Pa=-3;
  assert.match(renderSteadyClosure(data),/压力关系未闭合/);
  contract.branch_state_links[0].source_state_residual_Pa=0;
  contract.branch_state_links.pop();
  assert.match(renderSteadyClosure(data),/压力关系证据不足/);
  delete contract.branch_state_links;
  assert.match(renderSteadyClosure(data),/四支路与流体状态的压力交叉核对未记录/);
});

test("intermediate pressure nodes are recorded accounting values, not reconstructed measurements",()=>{
  const data=pressureFixture(), html=renderSteadyClosure(data);
  assert.match(html,/喷注器前（集总扣账）<\/td><td>67<\/td>/);
  assert.match(html,/不代表实物测点/);
  assert.match(html,/基础管路\/全开阀 \/ MPa/);
  assert.match(html,/附加调节阀 \/ MPa/);
  delete data.calculation.steady_closure.feed_network.branches[0].pressure_nodes;
  assert.match(renderSteadyClosure(data),/逐支路中间压力节点未记录/);
});

test("new pressure contract text remains escaped and malformed lists stay readable",()=>{
  const data=pressureFixture(), contract=data.calculation.steady_closure.feed_network.pressure_contract, attack='<script>alert(1)</script>';
  contract.pressure_convention.detail=attack;
  contract.pump_capacity.detail=attack;
  contract.parameter_roles[0].detail=attack;
  contract.gas_paths[0].label=attack;
  let html=renderSteadyClosure(data);
  assert.doesNotMatch(html,/<script>/);
  assert.ok((html.match(/&lt;script&gt;/g)||[]).length >= 4);
  contract.gas_paths=[null,12];
  contract.pump_capacity.pumps=[null,{}];
  contract.parameter_roles=[null,12];
  assert.doesNotThrow(()=>renderSteadyClosure(data));
  html=renderSteadyClosure(data);
  assert.match(html,/压力关系证据不足/);
  assert.doesNotMatch(html,/NaN|Infinity|undefined/);
});
