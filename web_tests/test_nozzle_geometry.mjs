import assert from "node:assert/strict";
import {test} from "node:test";
import {inspectNozzleGeometry,buildNozzleSvg,buildNozzleCsv,buildNozzleSurface,renderNozzleGeometry,mountNozzleGeometry} from "../webapp/static/nozzle-geometry.js";

const geometry=()=>({available:true,method:{id:"parabolic_bell",label:"抛物线钟形喷管 · 系统级工程近似",angle_source:"方案指定角度",notes:["不代表推力优化解。"]},source:{label:"保存的设计记录"},contour:{x_m:[0,.2,.4,.5,.7,1,1.5,2],r_m:[.2,.2,.14,.1,.15,.3,.4,.5],coordinate_origin:"injector_face"},dimensions:{throat_diameter_m:.2,exit_diameter_m:1,chamber_diameter_m:.4,chamber_length_m:.2,convergent_length_m:.3,nozzle_length_m:1.5,total_length_m:2,throat_x_m:.5,initial_angle_deg:30,exit_angle_deg:9,throat_upstream_radius_m:.15,throat_downstream_radius_m:.0382,expansion_ratio:25,bell_fraction:.8},cooling:{start_x_m:0,end_x_m:1.5}});

test("saved contour is required; no geometry is fabricated for historical records",()=>{
  for(const value of [null,{}, {available:false,dimensions:{throat_diameter_m:.2}}, {...geometry(),contour:{x_m:[0,1],r_m:[.2,.4]}}]){
    assert.equal(inspectNozzleGeometry(value).valid,false);assert.equal(buildNozzleSvg(value),"");assert.equal(buildNozzleCsv(value),"");assert.deepEqual(buildNozzleSurface(value),[]);assert.doesNotMatch(renderNozzleGeometry(value),/<svg|<canvas|data-nozzle-export/);
  }
});
test("invalid gaps, NaN, negative radius and reversed x never bridge into a fake profile",()=>{
  for(const [x,r] of [[[0,1,2],[.2,null,.4]],[[0,1,2],[.2,NaN,.4]],[[0,1,2],[.2,0,.4]],[[0,2,1],[.2,.3,.4]],[[0,0,0],[.2,.3,.4]]])assert.equal(inspectNozzleGeometry({...geometry(),contour:{x_m:x,r_m:r}}).valid,false);
});
test("SVG includes scale, open mirrored contours, dimensions, origin and cooling extent",()=>{
  const svg=buildNozzleSvg(geometry());
  assert.match(svg,/xmlns="http:\/\/www.w3.org\/2000\/svg"/);assert.match(svg,/data-geometry-scale="331"/);
  for(const text of ["总长 2 m","膨胀段 1.5 m","收缩段 0.3 m","直筒段 0.2 m","Dc 0.4 m","Dt 0.2 m","De 1 m","喉部","再生冷却覆盖","喷注面 x = 0","轴向与径向等比例"])assert.ok(svg.includes(text),text);
  // 0.2 m axial step and 0.2 m radial extent both use 331 px/m.
  assert.ok(svg.includes("M184,196.3 L250.2,196.3"));
});
test("unknown dimensions remain unknown and are not derived as unsupported record values",()=>{
  const g=geometry();g.dimensions={};g.cooling={start_x_m:-1,end_x_m:3};
  const html=renderNozzleGeometry(g);assert.match(html,/未记录/);assert.doesNotMatch(html,/Dt 0|De 0|总长 0|再生冷却覆盖/);assert.match(html,/<svg/);
});
test("exports preserve every canonical point, without rounding or post-hoc reconstruction",()=>{
  const g=geometry();g.contour.x_m[1]=.200000000000007;
  const csv=buildNozzleCsv(g),lines=csv.trim().split("\r\n");
  assert.equal(lines.length,g.contour.x_m.length+1);assert.equal(lines[0],"x_m,r_m,coordinate_origin");assert.equal(lines[2],"0.200000000000007,0.2,injector_face");assert.equal(lines.at(-1),"2,0.5,injector_face");
});
test("3D uses all saved axial stations and correct revolved radii, with open end rings",()=>{
  const g=geometry(),mesh=buildNozzleSurface(g,12);
  assert.equal(mesh.length,g.contour.x_m.length);assert.equal(mesh[0].length,12);
  mesh.forEach((ring,i)=>ring.forEach(point=>{assert.equal(point[0],g.contour.x_m[i]-1);assert.ok(Math.abs(Math.hypot(point[1],point[2])-g.contour.r_m[i])<1e-12);}));
  assert.equal(buildNozzleSurface(g,10000)[0].length,36);
});
test("render clearly separates gas boundary from structural model and exposes controls",()=>{
  const html=renderNozzleGeometry(geometry());
  for(const text of ["二维剖面","三维旋转面","导出坐标 CSV","导出剖面 SVG","复位视角","未表示壁厚、冷却槽道或承力外壳","角度来源","保存的设计记录"])assert.ok(html.includes(text),text);
  assert.match(html,/data-nozzle-panel="3d" hidden/);assert.match(html,/tabindex="0" aria-label="喷管/);
});
test("reconstructed record geometry is visibly labelled above the diagram",()=>{
  const g=geometry();g.source={kind:"reconstructed_from_record",label:"由本记录尺寸与输入重建的型面"};
  const html=renderNozzleGeometry(g),visible=html.slice(0,html.indexOf('<details'));
  assert.match(visible,/ng-source-reconstructed/);assert.match(visible,/由本记录尺寸与输入重建的型面/);assert.match(visible,/不补造冷却结果/);
});
test("user source and method text is escaped in HTML and exported SVG",()=>{
  const g=geometry();g.method.label='<img src=x onerror="bad()">';g.method.notes=['<script>bad()</script>'];g.source.label="<svg onload=bad()>";
  assert.doesNotMatch(renderNozzleGeometry(g),/<img|<script|<svg onload/);assert.match(renderNozzleGeometry(g),/&lt;img/);assert.doesNotMatch(buildNozzleSvg(g),/<img/);
});
test("rendering and export do not mutate the immutable result",()=>{
  const g=geometry(),before=JSON.stringify(g);renderNozzleGeometry(g);buildNozzleCsv(g);buildNozzleSurface(g);assert.equal(JSON.stringify(g),before);
  assert.equal(typeof mountNozzleGeometry(null,g),"function");mountNozzleGeometry(null,g)();
});
