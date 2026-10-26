import assert from "node:assert/strict";
import test from "node:test";
import {renderThermodynamicApplicability,thermodynamicDisplayState,thermoRangeDisplayState,pureFluidDisplayState,thermoApplicabilityBadge} from "../webapp/static/thermo-applicability.js";
import {componentAnalysisItems,renderAnalysis} from "../webapp/static/results.js";

const state={id:"main_chamber",label:"主燃烧室",T_K:3743,p_Pa:33e6,thermo_range:{status:"extrapolated",outside_mass_fraction:.94,
  valid_temperature_min_K:200,valid_temperature_max_K:3500,
  species_outside:[{species:"H2O",mass_fraction:.35,min_temperature_K:200,max_temperature_K:3500}]}};
const evidence={schema_version:1,status:"review_required",states:[state],
  gas_model:{id:"ideal-gas",label:"理想气体混合物",description:"高压混合燃气模型适用性尚未独立验证。"},
  nozzle:{id:"frozen_chamber",label:"主室组成冻结",description:"从主室起保持组成，沿喷管变温计算热容。"},
  combustion:{id:"adiabatic",label:"焓平衡（不附加经验燃烧损失）",description:"再生冷却吸热仍单独计入。",loss_W:0,cooling_heat_transfer_W:90e6},
  enthalpy_reference:{id:"aligned_v1",temperature_K:298.15,pressure_Pa:101325,formula:"hchem(Tref) + hfluid(T,p) − hfluid(Tref,pref)",description:"化学焓与流体焓差对齐。"},
  notes:["温区检查不估计性能误差。"]};
const result={calculation:{thermodynamic_applicability:evidence}};

test("historical missing and unknown evidence never become a positive applicability verdict",()=>{
  for(const input of [{},{calculation:{thermodynamic_applicability:{schema_version:99,states:[state]}}},{thermodynamic_applicability:{schema_version:1,states:[]}}]){
    assert.equal(thermodynamicDisplayState(input).available,false);
    assert.match(renderThermodynamicApplicability(input),/未保存完整.*历史记录不会被自动判为有效/s);
    assert.doesNotMatch(thermoApplicabilityBadge(input),/success|通过|有效/);
  }
});

test("v2 pure-fluid ports expose phase, declared bounds and missing melting evidence without certification",()=>{
  const pure={id:"fuel_after_cooling",label:"燃料冷却出口",species:"CH4",T_K:380,p_Pa:60e6,phase:"supercritical",
    status:"within_declared_ranges",limits:{Tmin_K:90.6941,Tmax_K:625,pmax_Pa:1e9},melting_boundary:{status:"not_available"}};
  const input={thermodynamic_applicability:{...evidence,schema_version:2,pure_fluid_states:[pure],
    cooling:{id:"regen_quasi1d_ph_v1",label:"统一准一维冷却",total_heat_transfer_W:125e6},
    combustion:{...evidence.combustion,cooling_heat_transfer_W:0}}};
  const html=renderThermodynamicApplicability(input);
  assert.equal(thermodynamicDisplayState(input).available,true);
  assert.equal(pureFluidDisplayState(pure),"within_declared_ranges");
  assert.match(html,/燃料冷却出口/); assert.match(html,/超临界单相/);
  assert.match(html,/90.6941–625 K/); assert.match(html,/该流体未提供熔化线/);
  assert.match(html,/不能证明汽蚀能力/); assert.match(html,/不等于物性精度已验证/);
  assert.match(html,/再生冷却吸热：<b>125 MW/); assert.doesNotMatch(html,/再生冷却吸热：<b>0 MW/);
  assert.equal(pureFluidDisplayState({...pure,T_K:700}),"unavailable");
  assert.equal(pureFluidDisplayState({...pure,phase:"twophase"}),"unavailable");
  const invalid={...pure,T_K:700,status:"invalid_or_unavailable",reason:"<script>range failure</script>"};
  const failed=renderThermodynamicApplicability({thermodynamic_applicability:{...input.thermodynamic_applicability,pure_fluid_states:[invalid]}});
  assert.match(failed,/700/); assert.match(failed,/无效或查询失败/);
  assert.match(failed,/&lt;script&gt;range failure&lt;\/script&gt;/); assert.doesNotMatch(failed,/<script>/);
});

test("legacy records do not synthesize pure-fluid applicability",()=>{
  assert.match(renderThermodynamicApplicability(result),/未保存纯推进剂温压域/);
});

test("temperature extrapolation records retain the actual temperature and expose species ranges",()=>{
  const html=renderThermodynamicApplicability(result);
  assert.match(html,/3,743/);
  assert.match(html,/3,500/);
  assert.match(html,/H2O/);
  assert.match(html,/94 %/);
  assert.match(html,/发生温区外推/);
  assert.match(html,/不是性能误差估计/);
  assert.match(html,/298.15 K · 101,325 Pa/);
  assert.match(html,/frozen|主室组成冻结/);
  assert.match(html,/附加燃烧损失：<b>0 MW/);
  assert.match(html,/再生冷却吸热：<b>90 MW/);
});

test("range coverage is distinct from gas model validation even when the server claims coverage",()=>{
  const covered={...state,T_K:1000,thermo_range:{...state.thermo_range,status:"within_declared_ranges",species_outside:[],outside_mass_fraction:0}};
  assert.equal(thermoRangeDisplayState(covered),"within_declared_ranges");
  const input={thermodynamic_applicability:{...evidence,status:"within_declared_ranges",states:[covered]}};
  assert.equal(thermodynamicDisplayState(input).status,"review_required");
  assert.match(renderThermodynamicApplicability(input),/在所查物种温区内/);
  assert.match(renderThermodynamicApplicability(input),/不代表高压状态方程/);
  assert.equal(thermoRangeDisplayState({...covered,T_K:4000}),"unavailable");
  assert.equal(thermoRangeDisplayState({...covered,thermo_range:{...covered.thermo_range,outside_mass_fraction:null}}),"unavailable");
  assert.equal(thermoRangeDisplayState({...covered,thermo_range:{...covered.thermo_range,species_outside:[{species:"OH"}]}}),"extrapolated");
});

test("all recorded text is escaped and missing numeric evidence is not shown as zero",()=>{
  const input={thermodynamic_applicability:{...evidence,gas_model:{label:'<img src=x onerror=bad>',description:'<script>bad</script>'},
    states:[{...state,T_K:null,p_Pa:null,label:'<unsafe>',thermo_range:{status:"unavailable"}}],notes:['<script>bad</script>']}};
  const html=renderThermodynamicApplicability(input);
  assert.match(html,/&lt;img src=x onerror=bad&gt;/);
  assert.match(html,/&lt;unsafe&gt;/);
  assert.doesNotMatch(html,/<script>|<img/);
  assert.match(html,/<td>—<\/td><td>—<\/td>/);
  assert.match(html,/温区证据不足/);
});

test("adiabatic result hides inactive historical factor while legacy result labels it honestly",()=>{
  const input={calculation:{inputs:{combustion_loss_model:"adiabatic"},components:{main_chamber:{outputs:{T_chamber_K:3743,combustion_efficiency:.99}}}}};
  assert.deepEqual(componentAnalysisItems(input)[0].fields.map(item=>item.key),["T_chamber_K"]);
  input.calculation.inputs.combustion_loss_model="legacy_temperature_rise";
  assert.match(componentAnalysisItems(input)[0].fields.find(item=>item.key==="combustion_efficiency").label,/历史温升折减/);
});

test("steady overview contains applicability independently of convergence",()=>{
  const html=renderAnalysis("overview",{status:"succeeded",result:{...result,calculation:{...result.calculation,summary:{},inputs:{}}}});
  assert.match(html,/热力学适用性与模型假设/);
  assert.match(html,/热力学适用性待核实/);
});
