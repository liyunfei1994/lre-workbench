import assert from "node:assert/strict";
import test from "node:test";
import {renderSteadyDesignSummary,renderSteadyDesignBasis,renderSupplyBasis} from "../webapp/static/steady-design.js";
import {renderGasHandoff} from "../webapp/static/gas-handoff.js";

const fixture=()=>({schema_version:1,status:"review_required",scope_label:"FFSC 稳态总体设计基线",source:"保存记录 A",gates:[{id:"energy",label:"能量闭合",status:"pass",target:"checks",detail:"重核保存状态"},{id:"constraints",label:"系统约束",status:"review",target:"checks",detail:"壁温限值未设定"}],models:[{label:"燃气 EOS",value:"ideal-gas"}],modules:[{label:"泵",status:"recorded",delivers:"所需泵功",pending:"特性图验证"}],actions:[{label:"高压筛查",detail:"选择同一记录",target:"models"}],stations:[{label:"涡轮入口",p_Pa:60e6,T_K:750,state_basis:{pressure:"total",temperature:"total"},status:"complete"}]});

test("overall evidence separates equation closure from hardware and preserves source",()=>{
  const r=fixture(), before=JSON.stringify(r), html=renderSteadyDesignSummary(r)+renderSteadyDesignBasis(r);
  for(const text of ["能量闭合","已核对","待确认","保存记录 A","特性图验证","ideal-gas","p：总态","未记录"])assert.ok(html.includes(text),text);
  assert.equal(JSON.stringify(r),before);
  assert.doesNotMatch(html,/整机验证通过/);
});
test("unsupported or missing framework is not fabricated",()=>{
  for(const r of [null,{}, {schema_version:1,status:"not_applicable"}])assert.equal(renderSteadyDesignSummary(r)+renderSteadyDesignBasis(r),"");
});
test("source strings are escaped and actions restricted to existing tabs",()=>{
  const r=fixture();r.gates[0].label='<img src=x onerror="bad()">';r.gates[0].target='" onclick="bad()';
  const html=renderSteadyDesignSummary(r);
  assert.doesNotMatch(html,/<img| onclick=/);assert.match(html,/&lt;img/);
});
test("legacy enthalpy percentages are suppressed and path evidence absence is visible",()=>{
  const s={stations:[{label:"test",eos_comparisons:[{backend:"PR",status:"computed",paired_ideal_baseline:{enthalpy_j_kg:-100},candidate:{enthalpy_j_kg:-90},differences:{enthalpy_j_kg:{difference:10,relative_difference:.1}},frozen_expansion:{status:"computed",isentropic_drop_j_kg:2000}}]}]};
  const html=renderGasHandoff(null,s);
  assert.match(html,/基准相关，不作百分比/);assert.match(html,/绝对差/);assert.match(html,/历史筛查未保存膨胀路径/);
});

test("supply evidence distinguishes missing records and undefined supercritical margins",()=>{
  const supply={description:"saved",limits:"需 NPSHr 数据",ports:[
    {label:"历史入口",T_K:100,p_Pa:3e5,screen:{}},
    {label:"<script>入口</script>",T_K:200,p_Pa:30e6,screen:{phase:"supercritical",
      saturation_temperature_status:"not_defined_at_pressure",vapor_pressure_status:"not_defined_at_temperature"}},
    {label:"液氧贮箱",T_K:70,p_Pa:3e5,screen:{phase:"liquid",saturation_temperature_K:102,
      subcooling_K:32,static_npsh_component_m:24}}],external_powers:[{label:"液氧泵",power_W:484000}]};
  const before=structuredClone(supply),html=renderSupplyBasis(supply);
  assert.match(html,/未保存筛查/);assert.match(html,/该压力不定义/);assert.match(html,/不适用/);
  assert.match(html,/NPSH 静压分量/);assert.match(html,/484 kW/);
  assert.doesNotMatch(html,/<script>|抗汽蚀通过|NaN/);
  assert.deepEqual(supply,before);
});
