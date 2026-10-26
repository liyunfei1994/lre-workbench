import assert from "node:assert/strict";
import test from "node:test";
import {prepareThermoChartData,renderThermoCharts,buildThermoChartSvg} from "../webapp/static/validation-charts.js";

function point(of,eps,value=300){
  const metrics={Isp_vac_s:value+eps,Cstar_m_s:value*5,Cf_vac:value/200,Tc_K:value*10};
  return {of,eps,cantera:{status:"ok",metrics},cea:{status:"ok",metrics:Object.fromEntries(Object.entries(metrics).map(([k,v])=>[k,v-1]))},
    differences:Object.fromEntries(Object.keys(metrics).map(key=>[key,{absolute:-1,relative_percent:-1/metrics[key]*100}]))};
}
function result(points,ofValues,epsValues){return {points,request:{of_values:ofValues||[...new Set(points.map(p=>p.of))],eps_values:epsValues||[...new Set(points.map(p=>p.eps))],pc_pa:3e7,T_fuel_K:110,T_ox_K:90,mode:"equilibrium",propellant_pair:"lox_methane"}};}

test("four metric panels use real, nonuniform O/F coordinates and stable epsilon groups",()=>{
  const fixture=result([point(4,80),point(2,40),point(2.5,80),point(4,40),point(2,80),point(2.5,40)]);
  const data=prepareThermoChartData(fixture),html=renderThermoCharts(fixture);
  assert.deepEqual(data.xValues,[2,2.5,4]);assert.deepEqual(data.epsValues,[40,80]);
  assert.equal(data.panels.length,4);assert.equal((html.match(/data-thermo-metric=/g)||[]).length,4);
  const marks=[...html.matchAll(/<circle [^>]*data-backend="cantera" data-eps="40" data-of="([\d.]+)"[^>]*cx="([\d.]+)"/g)].slice(0,3);
  assert.deepEqual(marks.map(m=>Number(m[1])),[2,2.5,4]);
  const x=marks.map(m=>Number(m[2]));assert.equal((x[1]-x[0])/(x[2]-x[0]),.25);
  assert.match(html,/data-eps="40" points="/);assert.match(html,/data-eps="80" points="/);
  assert.match(html,/tc-dashed/);assert.match(html,/NASA CEA · 虚线方点/);
});

test("missing middle values break paths and zero is a valid stored value",()=>{
  const points=[point(2,40),point(3,40),point(4,40)];
  points[1].cea={status:"error",reason:"failed"};points[0].cantera.metrics.Isp_vac_s=0;
  const html=renderThermoCharts(result(points));
  assert.doesNotMatch(html,/<polyline[^>]*data-backend="cea"/);
  assert.match(html,/data-backend="cantera" data-eps="40" data-of="2" data-value="0"/);
  assert.match(html,/曲线在缺失处断开/);assert.doesNotMatch(html,/NaN|Infinity/);
});

test("entirely absent grid points also prevent interpolated lines across failures",()=>{
  const html=renderThermoCharts(result([point(2,40),point(4,40)],[2,3,4],[40]));
  assert.doesNotMatch(html,/<polyline/);assert.match(html,/1 个工况缺少完整配对/);
});

test("chamber quantities collapse only exactly identical values, differences and gaps",()=>{
  const fixture=result([point(2,40),point(2,80),point(3,40),point(3,80)]);
  let panels=prepareThermoChartData(fixture).panels;
  assert.equal(panels.find(p=>p.key==="Cstar_m_s").groups.length,1);
  assert.equal(panels.find(p=>p.key==="Isp_vac_s").groups.length,2);
  fixture.points[1].cantera.metrics.Cstar_m_s+=.00000001;
  panels=prepareThermoChartData(fixture).panels;
  assert.equal(panels.find(p=>p.key==="Cstar_m_s").groups.length,2);
  assert.equal(panels.find(p=>p.key==="Tc_K").groups.length,1);
  fixture.points[1].differences.Tc_K.relative_percent=null;
  assert.equal(prepareThermoChartData(fixture).panels.find(p=>p.key==="Tc_K").groups.length,2);
});

test("one O/F gives four numerical cards, multiple epsilon rows and no fake trend",()=>{
  const fixture=result([point(3.6,40),point(3.6,80)]),html=renderThermoCharts(fixture);
  assert.match(html,/单工况对比 · O\/F = 3.6/);assert.equal((html.match(/tc-single-card/g)||[]).length,4);
  assert.doesNotMatch(html,/<polyline|tc-plot/);assert.match(html,/不能形成趋势曲线/);
  assert.match(html,/>40<\/th>/);assert.match(html,/>80<\/th>/);
});

test("server differences are shown verbatim; missing differences are never recomputed",()=>{
  const p=point(3.6,40);p.differences.Isp_vac_s={absolute:123.456,relative_percent:77.777};
  p.differences.Tc_K=null;
  const html=renderThermoCharts(result([p]));
  assert.match(html,/\+123.456/);assert.match(html,/\+77.777/);
  const temperature=html.split('data-thermo-metric="Tc_K"')[1];assert.match(temperature,/<td>—<\/td><td>—<\/td>/);
});

test("untrusted strings and nonfinite values cannot become executable SVG markup",()=>{
  const fixture=result([point(2,40),point(3,40)]);
  fixture.request.mode='<script>alert(1)</script>';fixture.request.propellant_pair='" onload="evil';
  fixture.points.push({of:'<script>',eps:40});fixture.points[0].cea.metrics.Tc_K=Infinity;
  for(const rendered of [renderThermoCharts(fixture),buildThermoChartSvg(fixture)]){
    assert.doesNotMatch(rendered,/<script|onload=|onclick=|style="|NaN|Infinity/);
  }
});

test("export is self-contained with units, source conditions, four panels and legends",()=>{
  const svg=buildThermoChartSvg(result([point(2,40),point(3,40)]));
  assert.match(svg,/^<svg xmlns="http:\/\/www.w3.org\/2000\/svg"/);assert.match(svg,/<style>/);
  assert.match(svg,/Pc 30 MPa/);assert.match(svg,/O\/F/);assert.match(svg,/燃烧室温度/);
  assert.match(svg,/特征速度/);assert.match(svg,/Cantera 实线圆点/);assert.doesNotMatch(svg,/<image|href=/);
});
