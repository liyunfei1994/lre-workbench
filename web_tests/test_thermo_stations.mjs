import assert from "node:assert/strict";
import test from "node:test";
import {renderThermoStations} from "../webapp/static/thermo-stations.js";

function fixture(){
  const station={temperature_K:3000,pressure_Pa:33e6,enthalpy_Jkg:-1000000,density_kg_m3:20,sound_speed_m_s:1000,MW_kg_kmol:18,gamma_isentropic:1.2,mass_fractions:{H2O:.7,CO:.3}};
  const cea={...station,temperature_K:3002,enthalpy_Jkg:-999000,mass_fractions:{H2O:.6,"*CO":.4}};
  return {provenance:{inlet_enthalpy_basis:"Production reference; finite-pressure residual removed (v2), held identical for all datasets."},points:[{
    of:3.6,eps:34.34,reactants:{fuel_enthalpy_Jkg:-4e6,oxidizer_enthalpy_Jkg:-3e5,mixture_enthalpy_Jkg:-1e6},comparable:{conditions_aligned:true},
    cantera:{status:"ok",stations:{chamber:station,throat:{temperature_K:2400,mass_fractions:{H2O:.6,CO:.4}}}},
    cea:{status:"ok",stations:{chamber:cea,throat:{temperature_K:2401,mass_fractions:{H2O:.5,"*CO":.5}}},diagnostics:{composition_verification:{
      status:"verified",reader_version:2,name_value_readback_verified:true,element_conservation_verified:true,
    }}},
  }]};
}
function stateRow(html,key){return html.match(new RegExp(`<tr data-thermo-state="${key}">.*?</tr>`))?.[0];}

test("states use saved quantities, correct units and a collapsed default view",()=>{
  const result=fixture(),before=JSON.stringify(result),html=renderThermoStations(result);
  assert.match(html,/<details class="validation-data-details" data-thermo-stations>/);
  assert.match(html,/data-thermo-point/);assert.match(html,/data-thermo-station/);
  assert.match(stateRow(html,"temperature_K"),/<td>3,000<\/td><td>3,002<\/td><td>\+2<\/td>/);
  assert.match(stateRow(html,"pressure_Pa"),/<td>MPa<\/td><td>33<\/td><td>33<\/td><td>0<\/td>/);
  assert.match(stateRow(html,"enthalpy_Jkg"),/<td>kJ\/kg<\/td><td>-1,000<\/td><td>-999<\/td><td>\+1<\/td>/);
  assert.match(html,/不能一律解释为定组成 cp\/cv/);
  assert.equal(JSON.stringify(result),before);
});

test("point and station selections are bounded and switching can retain the open view",()=>{
  const result=fixture();result.points.push(structuredClone(result.points[0]));result.points[1].of=4;
  const html=renderThermoStations(result,{pointIndex:1,station:"throat",open:true});
  assert.match(html,/data-thermo-stations open/);assert.match(html,/<option value="1" selected>2 · O\/F 4/);
  assert.match(html,/<option value="throat" selected>/);
  assert.match(stateRow(html,"temperature_K"),/<td>2,400<\/td><td>2,401<\/td><td>\+1<\/td>/);
  assert.match(stateRow(html,"pressure_Pa"),/<td>—<\/td><td>—<\/td><td>—<\/td>/);
  const fallback=renderThermoStations(result,{pointIndex:-2,station:'<script>alert(1)</script>'});
  assert.match(fallback,/<option value="0" selected>/);assert.match(fallback,/<option value="chamber" selected>/);
  assert.doesNotMatch(fallback,/<script/);
});

test("enthalpy differences require an explicit shared reference, never just successful solvers",()=>{
  const result=fixture();delete result.provenance;
  let html=renderThermoStations(result);
  assert.match(stateRow(html,"enthalpy_Jkg"),/<td>-1,000<\/td><td>-999<\/td><td>—<\/td>/);
  assert.match(html,/暂不计算焓差/);
  result.points[0].reactants.reference={id:"cantera_gri30_highT_coolprop_298p15K_ideal_reference_v2"};
  html=renderThermoStations(result);assert.match(stateRow(html,"enthalpy_Jkg"),/<td>\+1<\/td>/);
  result.points[0].comparable.conditions_aligned=false;
  assert.match(stateRow(renderThermoStations(result),"enthalpy_Jkg"),/<td>—<\/td><\/tr>/);
  result.points[0].comparable.conditions_aligned=true;result.points[0].reactants.mixture_enthalpy_Jkg=null;
  assert.match(stateRow(renderThermoStations(result),"enthalpy_Jkg"),/<td>—<\/td><\/tr>/);
});

test("missing, string and nonfinite quantities stay absent while actual zero remains zero",()=>{
  const result=fixture(),station=result.points[0].cea.stations.chamber;
  delete station.pressure_Pa;station.density_kg_m3="20";station.sound_speed_m_s=Infinity;station.MW_kg_kmol=null;station.enthalpy_Jkg=NaN;
  result.points[0].cantera.stations.chamber.gamma_isentropic=0;station.gamma_isentropic=0;
  const html=renderThermoStations(result);
  for(const key of ["pressure_Pa","density_kg_m3","sound_speed_m_s","MW_kg_kmol","enthalpy_Jkg"])assert.match(stateRow(html,key),/<td>—<\/td><td>—<\/td><\/tr>/);
  assert.match(stateRow(html,"gamma_isentropic"),/<td>0<\/td><td>0<\/td><td>0<\/td>/);
  assert.doesNotMatch(html,/NaN|Infinity/);
});

test("composition matches only unique names with the CEA star marker, without phase or isomer guessing",()=>{
  const result=fixture();
  result.points[0].cantera.stations.chamber.mass_fractions={CO:.3,"CH2(S)":.02,CH2:.03,"C(gr)":.01,H2:0};
  result.points[0].cea.stations.chamber.mass_fractions={"*CO":.4,"CH2(s)":.01,CH2:.04,C:.01,H2:0};
  const html=renderThermoStations(result);
  assert.match(html,/data-thermo-species="CO"><td>CO \/ \*CO<\/td><td>0.3<\/td><td>0.4<\/td><td>\+0.1<\/td>/);
  assert.match(html,/data-thermo-species="CH2\(S\)"><td>CH2\(S\)<\/td><td>0.02<\/td><td>—<\/td><td>—<\/td>/);
  assert.match(html,/data-thermo-species="CH2\(s\)"><td>CH2\(s\)<\/td><td>—<\/td><td>0.01<\/td><td>—<\/td>/);
  assert.match(html,/data-thermo-species="C\(gr\)"><td>C\(gr\)<\/td><td>0.01<\/td><td>—<\/td><td>—<\/td>/);
  assert.match(html,/data-thermo-species="H2"><td>H2<\/td><td>0<\/td><td>0<\/td><td>0<\/td>/);
  assert.match(html,/质量分数不重新归一化/);
});

test("ambiguous CEA name normalization preserves every entry and withholds differences",()=>{
  const result=fixture();result.points[0].cantera.stations.chamber.mass_fractions={CO:.3};
  result.points[0].cea.stations.chamber.mass_fractions={CO:.1,"*CO":.2};
  const html=renderThermoStations(result),rows=[...html.matchAll(/<tr data-thermo-species="CO">.*?<\/tr>/g)].map(match=>match[0]);
  assert.equal(rows.length,3);for(const row of rows)assert.match(row,/<td>—<\/td><td>名称对应不唯一/);
  assert.match(html,/产物质量分数 · 3 行/);
});

test("old or failed records explain unavailable stations instead of deriving state from macro metrics",()=>{
  const old={points:[{of:3.6,eps:34.34,cantera:{status:"ok",metrics:{Tc_K:3000}},cea:{status:"ok"}}]};
  let html=renderThermoStations(old);assert.match(html,/旧记录如仅有性能指标，需重新运行校核/);assert.doesNotMatch(html,/data-thermo-state=/);
  const result=fixture();result.points[0].cea.status="error";
  html=renderThermoStations(result);assert.match(stateRow(html,"temperature_K"),/<td>3,000<\/td><td>—<\/td><td>—<\/td>/);
  assert.match(renderThermoStations({}),/本记录没有工况点/);
});

test("historical CEA composition is withheld even when its saved fractions sum to one",()=>{
  const result=fixture();
  result.points[0].cea.stations.chamber.mass_fractions={"*CO":.6,"WRONG_SPECIES_ORDER":.4};
  result.points[0].cea.diagnostics={species_reader:"CEA common block; mass-sum checked without renormalization",reported_mass_fraction_sum_residuals:{chamber:0}};
  const before=JSON.stringify(result),html=renderThermoStations(result);
  assert.match(html,/data-thermo-composition-warning/);
  assert.match(html,/质量分数之和为 1 不能单独证明/);
  assert.match(html,/请重新运行本项校核/);
  assert.doesNotMatch(html,/WRONG_SPECIES_ORDER|<td>0.6<\/td>|<td>0.4<\/td>/);
  assert.match(html,/data-thermo-species="CO"><td>CO<\/td><td>0.3<\/td><td>—<\/td><td>—<\/td>/);
  assert.match(html,/Cantera 单端数据；CEA 组分待重新校核/);
  assert.match(stateRow(html,"temperature_K"),/<td>3,000<\/td><td>3,002<\/td><td>\+2<\/td>/);
  assert.match(stateRow(html,"enthalpy_Jkg"),/<td>-1,000<\/td><td>-999<\/td><td>\+1<\/td>/);
  assert.equal(JSON.stringify(result),before);
});

test("CEA composition needs every new readback and conservation verification flag",()=>{
  for(const [field,value] of [["status","failed"],["reader_version",1],["name_value_readback_verified",false],["element_conservation_verified",false]]){
    const result=fixture();result.points[0].cea.diagnostics.composition_verification[field]=value;
    const html=renderThermoStations(result);
    assert.match(html,/data-thermo-composition-warning/);
    assert.doesNotMatch(html,/CO \/ \*CO/);
  }
  const verified=renderThermoStations(fixture());
  assert.doesNotMatch(verified,/data-thermo-composition-warning/);
  assert.match(verified,/CO \/ \*CO/);
});

test("untrusted species names and selection strings are escaped and cannot create markup",()=>{
  const result=fixture();result.points[0].cantera.stations.chamber.mass_fractions={'<img src=x onerror="alert(1)">':.01};
  const html=renderThermoStations(result,{station:'" onclick="attack',open:' onclick="attack'});
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img| onclick=|data-thermo-stations open/);
});
