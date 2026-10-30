import assert from "node:assert/strict";
import {test} from "node:test";
import {inspectPumpGeometry,buildPumpSvg,buildPumpCsv,buildPumpPointTxt,buildPumpDxf,buildPumpMesh,buildPumpObj,buildPumpJson,pumpExportFilename,renderPumpGeometry,mountPumpGeometry} from "../webapp/static/pump-geometry.js";

const pump=()=>({available:true,label:"液氧泵",source:{kind:"saved_geometry",label:"本记录的概念定尺寸",record_id:"record-123"},design_point:{mass_flow_kg_s:500,density_in_kg_m3:1100,volume_flow_m3_s:500/1100,pressure_in_pa:1e6,pressure_out_pa:70e6,pressure_rise_pa:69e6,shaft_speed_rpm:20000,stages:2,total_head_m:6400,stage_head_m:3200,specific_speed_nq:35,tip_speed_m_s:250},assumptions:{head_coefficient_psi:.6,inlet_flow_coefficient:.15,outlet_flow_coefficient:.1,eye_hub_ratio:.4,axial_length_ratio:.3,blockage_factor:1,leakage_flow_m3_s:0},dimensions:{impeller_diameter_m:.24,eye_shroud_diameter_m:.12,eye_hub_diameter_m:.048,outlet_width_m:.008,axial_length_m:.072,inlet_area_m2:.0095,outlet_area_m2:.006,inlet_meridional_velocity_m_s:47.8,outlet_meridional_velocity_m_s:75.8},meridional:{hub:[[0,.024],[.04,.045],[.072,.12]],shroud:[[0,.06],[.025,.08],[.064,.12]],unit:"m",rotation_axis:"z",coordinate_origin:"impeller_eye_inlet"},checks:[{id:"continuity",label:"入口质量守恒",status:"pass"},{id:"cavitation",label:"汽蚀性能",status:"not_evaluated",note:"需要诱导轮和汽蚀设计。"}],notes:["等扬程代表级，非水力最优解。"],geometry_sha256:"a".repeat(64)});
const model=()=>({schema_version:1,source:{record_id:"record-123"},pumps:{ox:pump(),fuel:{...pump(),label:"甲烷泵"}}});

test("historical missing assumptions never fabricate coordinates or exporters",()=>{
  for(const p of [null,{}, {available:false,reason:"旧记录缺少几何假设",dimensions:pump().dimensions,design_point:pump().design_point}]){
    assert.equal(inspectPumpGeometry(p).valid,false);assert.equal(buildPumpSvg(p),"");assert.equal(buildPumpCsv(p),"");assert.equal(buildPumpPointTxt(p,"hub"),"");assert.equal(buildPumpDxf(p),"");assert.equal(buildPumpObj(p),"");assert.deepEqual(buildPumpMesh(p),{vertices:[],faces:[]});
  }
  const m=model();m.pumps.ox={available:false,reason:"旧记录缺少几何假设",design_point:pump().design_point};m.pumps.fuel={available:false};const html=renderPumpGeometry(m);
  assert.doesNotMatch(html,/<svg|<canvas|data-pg-export/);assert.match(html,/旧记录缺少几何假设/);assert.match(html,/500/);assert.match(html,/缺少的假设和坐标不会由前端默认值补齐/);
});
test("invalid units, geometry extents, NaN and dimensions fail closed",()=>{
  const mutations=[p=>p.meridional.unit="mm",p=>p.meridional.rotation_axis="x",p=>p.meridional.hub[1][1]=NaN,p=>p.meridional.hub[1][0]=-.1,p=>p.meridional.hub[1][0]=.2,p=>p.meridional.hub[1][1]=0,p=>p.dimensions.outlet_width_m=.2,p=>p.dimensions.eye_hub_diameter_m=.13,p=>p.dimensions.impeller_diameter_m=.4,p=>p.meridional.shroud[2][0]=.062,p=>p.meridional.hub[0][1]=.02];
  for(const change of mutations){const p=pump();change(p);assert.equal(inspectPumpGeometry(p).valid,false);assert.equal(buildPumpDxf(p),"");}
});
test("2D shows representative-stage dimensions with equal physical axis scale",()=>{
  const svg=buildPumpSvg(pump());assert.match(svg,/data-pg-scale="1625"/);
  for(const text of ["Lz 72 mm","D₂ 240 mm","D₁s 120 mm","D₁h 48 mm","b₂ 8 mm","入口 z = 0","不是泵总装尺寸","轮毂边界","轮盖边界"])assert.ok(svg.includes(text),text);
  // A 0.04 m axial change uses the same 1625 px/m factor as radius.
  assert.ok(svg.includes("M431.5,246 L496.5,211.875"));
  const encoded=svg.match(/data-pg-coordinates="([^"]+)"/)[1].replaceAll("&quot;",'"');assert.deepEqual(JSON.parse(encoded),pump().meridional.hub?{hub:pump().meridional.hub,shroud:pump().meridional.shroud}:null);
});
test("CSV and CFTurbo TXT preserve both canonical curves without rounded reconstruction",()=>{
  const p=pump();p.meridional.hub[1][0]=.040000000000001;
  const csv=buildPumpCsv(p).trim().split("\r\n");assert.equal(csv[0],"boundary,point_index,z_m,r_m");assert.equal(csv.length,7);assert.equal(csv[2],"hub,1,0.040000000000001,0.045");
  for(const side of ["hub","shroud"]){const lines=buildPumpPointTxt(p,side).trim().split("\r\n");assert.deepEqual(lines.map(line=>line.split("\t").map(Number)),p.meridional[side]);assert.doesNotMatch(lines[0],/[a-z]/i);}
  assert.equal(buildPumpPointTxt(p,"unexpected"),"");
});
test("DXF consists of two open LWPOLYLINE entities in metres with exact z/r pairs",()=>{
  const p=pump(),lines=buildPumpDxf(p).trim().split("\r\n"),pairs=[];for(let i=0;i<lines.length;i+=2)pairs.push([Number(lines[i]),lines[i+1]]);
  assert.ok(pairs.some((pair,i)=>pair[0]===9&&pair[1]==="$INSUNITS"&&pairs[i+1][0]===70&&pairs[i+1][1]==="6"));
  let curves=[],curve=null,z=null;
  for(const [code,value] of pairs){if(code===0&&value==="LWPOLYLINE"){curve={points:[]};curves.push(curve);}else if(code===0)curve=null;else if(curve&&code===8)curve.layer=value;else if(curve&&code===90)curve.count=Number(value);else if(curve&&code===70)curve.closed=Number(value);else if(curve&&code===10)z=Number(value);else if(curve&&code===20)curve.points.push([z,Number(value)]);}
  assert.equal(curves.length,2);for(const curve of curves){assert.equal(curve.closed,0);assert.equal(curve.count,3);assert.deepEqual(curve.points,p.meridional[curve.layer.toLowerCase()]);}
});
test("3D revolves the exact canonical z/r points and cutaway is a display option",()=>{
  const p=pump(),mesh=buildPumpMesh(p,12),cut=buildPumpMesh(p,12,true);assert.equal(mesh.vertices.length,72);assert.equal(mesh.faces.length,48);assert.equal(cut.vertices.length,78);assert.equal(cut.faces.length,48);
  const curves=[...p.meridional.hub,...p.meridional.shroud];mesh.vertices.forEach((point,i)=>{const [z,r]=curves[Math.floor(i/12)];assert.equal(point[2],z);assert.ok(Math.abs(Math.hypot(point[0],point[1])-r)<1e-14);});
  assert.equal(buildPumpMesh(p,100000).vertices.length,6*40);assert.ok(mesh.faces.every(face=>face.indices.length===4&&face.indices.every(i=>i>=0&&i<mesh.vertices.length)));
});
test("OBJ parses into finite vertices and valid face indices, with no fictional blades or solid end caps",()=>{
  const text=buildPumpObj(pump()),vertices=[],faces=[],groups=[];
  for(const line of text.trim().split("\n")){if(line.startsWith("v "))vertices.push(line.slice(2).split(" ").map(Number));if(line.startsWith("f "))faces.push(line.slice(2).split(" ").map(Number));if(line.startsWith("g "))groups.push(line.slice(2));}
  assert.equal(vertices.length,240);assert.equal(faces.length,160);assert.deepEqual(groups,["hub","shroud"]);assert.ok(vertices.every(p=>p.length===3&&p.every(Number.isFinite)));assert.ok(faces.every(face=>face.length===4&&face.every(i=>Number.isInteger(i)&&i>=1&&i<=vertices.length)));
  assert.match(text,/units: m/);assert.match(text,/Open surfaces only/);
});
test("JSON retains full record, assumptions and coordinate conventions; filenames isolate fluid and record",()=>{
  const m=model(),p=m.pumps.ox,data=JSON.parse(buildPumpJson(p,m.source));assert.deepEqual(data.pump,p);assert.equal(data.source.record_id,"record-123");assert.equal(data.exchange.length_unit,"m");assert.equal(data.exchange.rotation_axis,"z");assert.match(data.exchange.usage,/Not a proprietary project file/);
  assert.equal(pumpExportFilename(m,"ox","obj"),"pump-ox-record-123.obj");assert.equal(pumpExportFilename(m,"fuel","hub-txt"),"pump-fuel-record-123-hub.txt");m.source.record_id='../../<bad>';assert.doesNotMatch(pumpExportFilename(m,"ox","dxf"),/[<>/\\]/);
});
test("view exposes switching, legacy reasons, assumptions, CAD limits and interpreted check statuses",()=>{
  const html=renderPumpGeometry(model());for(const text of ["液氧泵","甲烷泵","二维子午面","三维流道边界","剖开 ¼","线框","尺寸与设计点 JSON","轮毂 TXT","轮盖 TXT","尚未评估","ψ = gH级 / u₂²","Ψ = 2ψ","多级泵显示等扬程假设下的一个代表级","CFTurbo","NX / UG"])assert.ok(html.includes(text),text);
  assert.match(html,/data-pg-pump="fuel" hidden/);assert.match(html,/data-pg-panel="3d" hidden/);assert.match(html,/data-pg-cutaway aria-pressed="true"/);assert.match(html,/OBJ 始终导出完整周向边界/);
});
test("all user-controlled texts are escaped; rendering does not mutate snapshots",()=>{
  const m=model();m.source.record_id='<script>alert(1)</script>';m.pumps.ox.label='<img src=x onerror="bad()">';m.pumps.ox.notes=['<svg onload=bad()>'];m.pumps.ox.checks[0].label='<iframe>';m.pumps.ox.source.label='<script>';m.pumps.fuel.reason='<img>';m.pumps.fuel.available=false;
  const before=JSON.stringify(m),html=renderPumpGeometry(m);assert.doesNotMatch(html,/<script|<img|<iframe|<svg onload/);assert.match(html,/&lt;script/);assert.equal(JSON.stringify(m),before);buildPumpCsv(m.pumps.ox);buildPumpDxf(m.pumps.ox);buildPumpObj(m.pumps.ox);assert.equal(JSON.stringify(m),before);
});
test("saved head scope and safe evidence links are shown without implying total pressure",()=>{
  const m=model();m.pumps.ox.design_point.head_scope="H=Δp/(ρg)，不含速度头。<script>";m.pumps.ox.references=[{title:"公式依据",url:"https://manual.cfturbo.com/en/md_parameters_pumpvent.html"},{title:"bad",url:"javascript:bad()"}];
  const html=renderPumpGeometry(m);assert.match(html,/泵压升 Δp/);assert.match(html,/入口密度参考总扬程/);assert.match(html,/每级参考扬程/);assert.match(html,/不含速度头。&lt;script&gt;/);assert.match(html,/href="https:\/\/manual.cfturbo.com/);assert.doesNotMatch(html,/javascript:|<script>|总压升/);
});

function fakeDom(){
  class Element {constructor(dataset={}){this.dataset=dataset;this.events=new Map();this.attrs={};this.hidden=false;}addEventListener(type,fn){if(!this.events.has(type))this.events.set(type,new Set());this.events.get(type).add(fn);}removeEventListener(type,fn){this.events.get(type)?.delete(fn);}setAttribute(k,v){this.attrs[k]=v;}hasAttribute(k){return Object.hasOwn(this.attrs,k);}closest(){return this;}contains(){return true;}matches(){return true;}fire(type,event){for(const f of this.events.get(type)||[])f(event);}querySelector(key){return this.selectors?.[key] || null;}querySelectorAll(key){return this.groups?.[key] || [];}}
  const root=new Element(),tabs=[new Element({pgSide:"ox"}),new Element({pgSide:"fuel"})],panels=[],frames=new Map(),canceled=[],observers=[];let next=1;
  const win={devicePixelRatio:1,requestAnimationFrame:fn=>{const id=next++;frames.set(id,fn);return id;},cancelAnimationFrame:id=>{canceled.push(id);frames.delete(id);},ResizeObserver:class{constructor(fn){this.fn=fn;observers.push(this);}observe(){}disconnect(){this.disconnected=true;}}};
  for(const side of ["ox","fuel"]){const panel=new Element({pgPump:side}),canvas=new Element(),error=new Element(),views=[new Element({pgView:"2d"}),new Element({pgView:"3d"})],viewPanels=[new Element({pgPanel:"2d"}),new Element({pgPanel:"3d"})];canvas.getContext=()=>null;panel.hidden=side==="fuel";viewPanels[1].hidden=true;error.hidden=true;panel.selectors={'[data-pg-canvas]':canvas,'[data-pg-canvas-error]':error,'[data-pg-panel="3d"]':viewPanels[1]};panel.groups={"[data-pg-view]":views,"[data-pg-panel]":viewPanels};panels.push(panel);}
  root.ownerDocument={defaultView:win};root.selectors={'[data-pg-pump="ox"]':panels[0],'[data-pg-pump="fuel"]':panels[1]};root.groups={'[data-pg-side]':tabs,'[data-pg-pump]':panels};return {root,tabs,panels,frames,canceled,observers};
}
test("component switch preserves pump-local view and cleanup removes listeners, observers and frames",()=>{
  const dom=fakeDom(),cleanup=mountPumpGeometry(dom.root,model());dom.root.fire("click",{target:dom.tabs[1]});assert.equal(dom.panels[0].hidden,true);assert.equal(dom.panels[1].hidden,false);assert.equal(dom.tabs[1].attrs["aria-pressed"],"true");
  const panel=dom.panels[1],view=panel.groups['[data-pg-view]'][1];panel.fire("click",{target:view});assert.equal(panel.groups['[data-pg-panel]'][1].hidden,false);assert.equal(dom.panels[0].groups['[data-pg-panel]'][1].hidden,true);assert.equal(panel.selectors['[data-pg-canvas-error]'].hidden,false);
  assert.equal(dom.frames.size,2);cleanup();assert.equal(dom.frames.size,0);assert.ok(dom.observers.every(o=>o.disconnected));assert.equal(dom.root._pumpCleanup,undefined);assert.equal(dom.root.events.get("click").size,0);assert.equal(panel.events.get("click").size,0);cleanup();
});
test("remount disposes prior listener state and missing containers are safe",()=>{
  const dom=fakeDom();mountPumpGeometry(dom.root,model());const cleanup=mountPumpGeometry(dom.root,model());assert.equal(dom.root.events.get("click").size,1);assert.ok(dom.observers.slice(0,2).every(o=>o.disconnected));cleanup();assert.equal(typeof mountPumpGeometry(null,model()),"function");mountPumpGeometry(null,model())();
});
