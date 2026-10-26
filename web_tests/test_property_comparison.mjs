import assert from "node:assert/strict";
import test from "node:test";
import {preparePropertyComparisonData,renderPropertyComparison,buildPropertyComparisonSvg,buildPropertyComparisonCsv} from "../webapp/static/property-comparison.js";

function point(temperature=90,pressure=1e6,fluid="Oxygen"){
  const properties={density_kg_m3:1100-temperature,cp_J_kg_K:1800+temperature,viscosity_Pa_s:.0002,conductivity_W_m_K:.14,
    enthalpy_delta_J_kg:-300000,entropy_delta_J_kg_K:-2000,enthalpy_J_kg:-100000,entropy_J_kg_K:500,
    cv_J_kg_K:1000,speed_sound_m_s:900};
  const reference_state={temperature_K:300,pressure_Pa:1000};
  const refprop={status:"pass",phase:"liquid",reference_state:{...reference_state},properties_si:{...properties},differences:{}};
  for(const [key,value] of Object.entries(properties)){
    if(["enthalpy_J_kg","entropy_J_kg_K"].includes(key))continue;
    refprop.properties_si[key]=value*.999;
    refprop.differences[key]={heos_minus_refprop:value*.001,relative_difference:value*.001/Math.abs(value*.999)};
  }
  return {fluid,temperature_K:temperature,pressure_Pa:pressure,status:"pass",heos_status:"pass",phase:"liquid",reference_state,
    properties_si:properties,refprop,limits:{temperature_min_K:54,temperature_max_K:2000,pressure_max_Pa:1e9}};
}
const section=checks=>({kind:"properties",checks,capabilities:{coolprop:{version:"7.2.0"},refprop:{version:"10.0"}},notes:["Shared EOS is not independent experimental validation."]});

test("all-fluid summary with selected-fluid charts never connects distinct fluids",()=>{
  const fixture=section([point(),point(100,1e6,"Methane")]);
  const data=preparePropertyComparisonData(fixture),html=renderPropertyComparison(fixture);
  assert.deepEqual(data.groups,["Oxygen","Methane"]);assert.equal(data.group,"Oxygen");assert.equal(data.single,true);
  assert.match(html,/data-property-group/);assert.equal((html.match(/data-property-metric=/g)||[]).length,6);
  assert.match(html,/2 \/ 2/);assert.doesNotMatch(html,/<polyline/);
  assert.equal(preparePropertyComparisonData(fixture,{group:"Methane"}).group,"Methane");
  assert.equal(preparePropertyComparisonData(fixture,{group:"stale"}).group,"Oxygen");
});

test("single-state report gives six common-zero bar comparisons and full numeric property table",()=>{
  const html=renderPropertyComparison(section([point()]));
  assert.equal((html.match(/pc-single-plot/g)||[]).length,6);assert.match(html,/定容比热/);assert.match(html,/声速/);
  assert.match(html,/原始焓（各库基准）/);assert.match(html,/>200<\/text>/); // viscosity displayed in micro-Pascal seconds
  assert.match(html,/>140<\/text>/); // conductivity in mW/(m K)
  assert.match(html,/>-300<\/text>/); // referenced h is kJ/kg
  assert.match(html,/CoolProp HEOS/);assert.match(html,/HEOS − REFPROP/);assert.doesNotMatch(html,/精度通过/);
});

test("temperature scan uses sorted physical coordinates including nonuniform spacing",()=>{
  const fixture=section([point(120),point(80),point(90)]),data=preparePropertyComparisonData(fixture),html=renderPropertyComparison(fixture);
  assert.equal(data.axis,"temperature");assert.deepEqual(data.points.map(p=>p.x),[80,90,120]);
  const marks=[...html.matchAll(/<circle [^>]*cx="([\d.]+)"[^>]*data-backend="heos" data-coordinate="([\d.]+)"/g)].slice(0,3);
  assert.deepEqual(marks.map(m=>Number(m[2])),[80,90,120]);
  assert.equal((Number(marks[1][1])-Number(marks[0][1]))/(Number(marks[2][1])-Number(marks[0][1])),.25);
  assert.match(html,/<polyline[^>]*data-backend="heos"/);assert.match(html,/等压扫描 · P = 1 MPa/);
});

test("pressure scan uses MPa while mixed-temperature-pressure states remain disconnected",()=>{
  const fixture=section([point(90,1e6),point(90,3e6),point(90,1.5e6)]);
  assert.equal(preparePropertyComparisonData(fixture).axis,"pressure");
  assert.deepEqual(preparePropertyComparisonData(fixture).points.map(p=>p.x),[1,1.5,3]);
  const mixed=section([point(80,1e6),point(90,2e6)]),html=renderPropertyComparison(mixed);
  assert.equal(preparePropertyComparisonData(mixed).axis,"state");assert.doesNotMatch(html,/<polyline/);
  assert.match(html,/独立状态点编号/);assert.match(html,/不连接成趋势线/);
});

test("backend failures and duplicate coordinates break lines without replacing values by zero",()=>{
  const points=[point(80),point(90),point(100)];points[1].refprop={status:"unavailable",reason:"model failed"};
  let html=renderPropertyComparison(section(points));
  assert.doesNotMatch(html,/<polyline[^>]*data-backend="refprop"/);
  assert.match(html,/<polyline[^>]*data-backend="heos"/);assert.match(html,/model failed/);
  html=renderPropertyComparison(section([point(80),point(90),point(90),point(100)]));
  assert.doesNotMatch(html,/<polyline/);
});

test("phase transition breaks trends and mismatched backend phases suppress differences",()=>{
  const points=[point(80),point(90),point(100)];points[1].phase="gas";points[1].refprop.phase="gas";
  let html=renderPropertyComparison(section(points));assert.doesNotMatch(html,/<polyline/);
  points[1].refprop.phase="liquid";
  const data=preparePropertyComparisonData(section(points));assert.equal(data.mismatchCount,1);
  assert.equal(data.panels[0].values[1].relative,null);assert.equal(data.panels[0].values[1].absolute,null);
  html=renderPropertyComparison(section(points));assert.match(html,/两后端相态不同/);
});

test("same reference T/P is required for h/s comparisons, and raw offsets are never subtracted",()=>{
  const row=point();row.refprop.reference_state.temperature_K=400;
  row.refprop.differences.enthalpy_J_kg={heos_minus_refprop:1234,relative_difference:.9};
  const data=preparePropertyComparisonData(section([row]));
  assert.equal(data.panels.find(p=>p.key==="enthalpy_delta_J_kg").values[0].relative,null);
  assert.notEqual(data.panels[0].values[0].relative,null);
  const csv=buildPropertyComparisonCsv(section([row]));
  const rawRow=csv.split("\r\n").find(line=>line.includes("原始焓"));assert.match(rawRow,/"-100","-100","",""/);
  assert.match(renderPropertyComparison(section([row])),/共同参考状态缺失或不一致/);
});

test("historical missing differences remain absent, exact zero remains a legitimate saved value",()=>{
  const row=point();row.refprop.differences.density_kg_m3={heos_minus_refprop:0,relative_difference:0};
  delete row.refprop.differences.cp_J_kg_K;row.properties_si.enthalpy_delta_J_kg=0;row.refprop.properties_si.enthalpy_delta_J_kg=0;
  row.refprop.differences.enthalpy_delta_J_kg={heos_minus_refprop:0,relative_difference:null};
  const data=preparePropertyComparisonData(section([row]));
  assert.equal(data.panels[0].values[0].relative,0);assert.equal(data.panels[1].values[0].relative,null);
  assert.equal(data.panels[4].values[0].heos,0);assert.equal(data.panels[4].values[0].absolute,0);assert.equal(data.panels[4].values[0].relative,null);
  assert.match(renderPropertyComparison(section([row])),/历史记录未保存差值/);
});

test("saved authoritative differences, comparison exclusions, and aggregate states are respected",()=>{
  const row=point();row.status="fail";row.heos_status="pass";
  row.refprop.differences.density_kg_m3={heos_minus_refprop:50,relative_difference:.5};
  let data=preparePropertyComparisonData(section([row]));assert.equal(data.panels[0].values[0].relative,50);
  assert.equal(data.panels[0].values[0].absolute,50);
  row.comparison={status:"partial",excluded_properties:{density_kg_m3:"Excluded by backend"}};
  data=preparePropertyComparisonData(section([row]));assert.equal(data.panels[0].values[0].relative,null);
  assert.match(renderPropertyComparison(section([row])),/Excluded by backend/);
});

test("old HEOS-only records are readable and do not imply a REFPROP match",()=>{
  const row=point();delete row.refprop;delete row.heos_status;
  const html=renderPropertyComparison(section([row]));
  assert.match(html,/0 \/ 1/);assert.match(html,/没有有效配对差值/);assert.match(html,/缺少有效配对/);
  assert.match(html,/未请求/);assert.doesNotMatch(html,/NaN|Infinity/);
});

test("SVG includes six selected-fluid panels and CSV exports all fluid states with escaped text",()=>{
  const fixture=section([point(),point(100,1e6,"Methane")]);
  const svg=buildPropertyComparisonSvg(fixture,{group:"Methane"}),csv=buildPropertyComparisonCsv(fixture);
  assert.match(svg,/^<svg xmlns=/);assert.match(svg,/甲烷 · 100 K/);assert.match(svg,/<style>/);
  for(const label of ["密度","定压比热","动力黏度","导热系数","参考焓差","参考熵差"])assert.match(svg,new RegExp(label));
  assert.doesNotMatch(svg,/<image|href=|NaN|Infinity/);assert.match(csv,/Oxygen/);assert.match(csv,/Methane/);
  assert.equal(csv.charCodeAt(0),0xfeff);assert.equal(csv.split("\r\n").length,21);
});

test("untrusted labels and diagnostics cannot inject HTML, SVG, or spreadsheet formulas",()=>{
  const row=point();row.fluid='=HYPERLINK("evil")';row.reason='<script>alert(1)</script>';row.refprop.reason='"><img src=x onerror=evil>';
  row.model_metadata={EOS:'<script>alert(1)</script>'};row.properties_si.cp_J_kg_K=Infinity;
  const fixture=section([row]);fixture.notes=['<script>evil</script>'];
  for(const output of [renderPropertyComparison(fixture),buildPropertyComparisonSvg(fixture)]){
    assert.doesNotMatch(output,/<script|<img|onerror="|style="|NaN|Infinity/);
  }
  assert.match(renderPropertyComparison(fixture),/&lt;script&gt;/);
  assert.match(buildPropertyComparisonCsv(fixture),/"'=HYPERLINK/);
  for(const name of ['+formula','-formula','@formula','\tformula','\rformula']){
    row.fluid=name;assert.match(buildPropertyComparisonCsv(section([row])),new RegExp('"\''));
  }
});

test("empty and malformed saved records render without bogus chart coordinates",()=>{
  for(const fixture of [null,{},section([]),section([null])]){
    assert.match(renderPropertyComparison(fixture),/尚无/);assert.doesNotMatch(buildPropertyComparisonSvg(fixture),/NaN|Infinity/);
  }
  const row=point();row.temperature_K=null;row.pressure_Pa=null;
  assert.equal(preparePropertyComparisonData(section([row,point()])).axis,"state");
  assert.doesNotMatch(renderPropertyComparison(section([row])),/NaN|Infinity/);
});

test("numerical-precision differences keep nonzero scientific ticks and readable metadata units",()=>{
  const points=[point(80),point(90)];
  for(const row of points){
    row.refprop.differences.density_kg_m3={heos_minus_refprop:1e-16,relative_difference:1e-18};
    row.critical_state={temperature_K:154.58,pressure_Pa:5043000};
    row.model_metadata={backend_name:"HelmholtzEOSBackend",fluid_names:["Oxygen"],cas:"7782-44-7",eos_reference:"Schmidt-JPCRD-1985"};
  }
  const html=renderPropertyComparison(section(points));
  assert.match(html,/1\.20e-16/);assert.match(html,/1\.0000e-16/);
  assert.match(html,/临界温度 154.58 K；临界压力 5.043 MPa/);
  assert.match(html,/物性后端: HelmholtzEOSBackend/);assert.match(html,/CAS 编号: 7782-44-7/);
  assert.match(html,/状态方程文献: Schmidt-JPCRD-1985/);
});

test("positive property charts never pad below zero and failed points have visible explanations",()=>{
  const points=[point(80),point(90),point(100)];
  points[0].properties_si.density_kg_m3=1;points[0].refprop.properties_si.density_kg_m3=1;
  points[2].properties_si.density_kg_m3=1000;points[2].refprop.properties_si.density_kg_m3=1000;
  points[1].refprop={status:"fail",reason:"REFPROP outside T/P bounds"};
  const html=renderPropertyComparison(section(points));
  const mainDensity=html.split('data-property-metric="density_kg_m3"')[1].split('</svg>')[0];
  assert.doesNotMatch(mainDensity,/<text[^>]*class="pc-tick"[^>]*>-/);
  const beforeCharts=html.split('class="pc-chart-grid"')[0];
  assert.match(beforeCharts,/1 个状态点包含不可用结果/);assert.match(beforeCharts,/#2 · Oxygen · 90 K/);
  assert.match(beforeCharts,/REFPROP outside T\/P bounds/);
});

test("export reference summary includes later successful points when the first scan point failed",()=>{
  const first=point(80);first.status="fail";first.heos_status="fail";first.refprop={status:"fail"};
  delete first.reference_state;delete first.properties_si;
  const svg=buildPropertyComparisonSvg(section([first,point(90),point(100)]));
  assert.match(svg,/参考点 HEOS 300 K \/ 0.001 MPa（2\/3 点记录） \/ REFPROP 300 K \/ 0.001 MPa（2\/3 点记录）/);
  assert.match(svg,/3 个保存状态点 · 1 个点缺少双后端数值/);
  assert.doesNotMatch(svg,/参考点 HEOS 未记录/);
});

test("export explicitly reports multiple reference conditions instead of choosing one",()=>{
  const first=point(90),second=point(100);second.reference_state.temperature_K=400;
  const svg=buildPropertyComparisonSvg(section([first,second]));
  assert.match(svg,/参考点 HEOS 多组参考状态（2 种；2\/2 点记录，详见表格）/);
  assert.match(svg,/REFPROP 300 K \/ 0.001 MPa（2\/2 点记录）/);
});


test("solid boundary and transport applicability remain separate from calculability",()=>{
  const row=point(100,70e6,"Methane");
  row.melting_boundary={status:"fail",temperature_K:107.58,margin_K:-7.58,reason:"低于所查熔化线"};
  row.transport_applicability={status:"not_assessed",reason:"输运模型独立关联式"};
  row.model_metadata={fluid_definition_sha256:"<fingerprint>",melting_line_reference:"source"};
  row.refprop.melting_boundary={status:"unavailable",reason:"未核实"};
  const html=renderPropertyComparison(section([row]));
  assert.match(html,/不在所查稳定流体域/);
  assert.match(html,/107.58 K/);
  assert.match(html,/-7.58 K/);
  assert.match(html,/熔化线未核实/);
  assert.match(html,/适用性未评估/);
  assert.match(html,/流体定义 SHA-256: &lt;fingerprint&gt;/);
  assert.doesNotMatch(html,/<fingerprint>/);
  const old=renderPropertyComparison(section([point()]));
  assert.match(old,/未记录；未证明已排除固相/);
  assert.match(old,/EOS 温压范围不能替代输运关联式适用范围/);
});
