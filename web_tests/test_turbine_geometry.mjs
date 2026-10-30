import assert from "node:assert/strict";
import {test} from "node:test";
import {inspectTurbineGeometry,buildTurbineSvg,buildTurbineCsv,buildTurbinePointTxt,buildTurbineDxf,buildTurbineMesh,buildTurbineObj,buildTurbineJson,turbineExportFilename,renderTurbineGeometry,mountTurbineGeometry} from "../webapp/static/turbine-geometry.js";

function turbine(stages=2) {
  const d={mean_diameter_m:.3,axial_length_m:stages*(.036+.045)+(2*stages-1)*.012,inlet_hub_diameter_m:.28,inlet_shroud_diameter_m:.32,outlet_hub_diameter_m:.26,outlet_shroud_diameter_m:.34,inlet_blade_height_m:.02,outlet_blade_height_m:.04,inlet_area_m2:Math.PI*(.16**2-.14**2),outlet_area_m2:Math.PI*(.17**2-.13**2),inlet_axial_velocity_m_s:120,outlet_axial_velocity_m_s:140,stator_axial_chord_m:.036,rotor_axial_chord_m:.045,row_gap_m:.012};
  const point=(z,boundary)=>[z,boundary==="hub"?.14-.01*z/d.axial_length_m:.16+.01*z/d.axial_length_m],rows=[];
  let z=0;
  for(let i=0;i<2*stages;i++) {const kind=i%2?"rotor":"stator",end=z+d[`${kind}_axial_chord_m`];rows.push({stage:Math.floor(i/2)+1,kind,label:`第 ${Math.floor(i/2)+1} 级${kind==="stator"?"静叶":"动叶"}排`,z_start_m:z,z_end_m:end,hub:[point(z,"hub"),point(end,"hub")],shroud:[point(z,"shroud"),point(end,"shroud")]});z=end+d.row_gap_m;}
  return {available:true,label:"氧侧涡轮",source:{kind:"saved_point_and_explicit_assumptions",record_id:"record-123",component_key:"turb_ox",assumption_prefix:"ox"},design_point:{mass_flow_kg_s:500,shaft_speed_rpm:20000,stages,pressure_in_pa:30e6,pressure_out_pa:15e6,temperature_in_k:900,temperature_out_k:760,power_w:10e6,density_in_kg_m3:100,density_out_kg_m3:60,mean_speed_m_s:200},dimensions:d,assumptions:{inlet_flow_coefficient:.6,outlet_flow_coefficient:.7,stator_axial_chord_ratio:.12,rotor_axial_chord_ratio:.15,row_gap_ratio:.04,geometry_state_basis:"system_pt_as_static",blockage_factor:1,leakage_mass_flow_kg_s:0},meridional:{hub:[0,.5,1].map(t=>point(t*d.axial_length_m,"hub")),shroud:[0,.5,1].map(t=>point(t*d.axial_length_m,"shroud")),coordinate_origin:"first_stator_inlet",rotation_axis:"z",unit:"m",construction:"linear_annulus_height_constant_mean_radius"},rows,checks:[{id:"continuity",label:"环形流道面积",status:"pass"},{id:"aerodynamics",label:"叶型气动性能",status:"not_evaluated",note:"需设计实际叶型。"}],notes:["内部截面仅为几何插值。"],references:[{title:"CFturbo 坐标导入",url:"https://manual.cfturbo.com/en/import_2dpointdata.html"}],geometry_sha256:"a".repeat(64)};
}
const model=()=>({schema_version:1,source:{record_id:"record-123"},turbines:{ox:turbine(),fuel:{...turbine(),label:"燃料侧涡轮"}}});

test("missing and old records never synthesize turbine coordinates",()=>{
  for (const t of [null,{}, {available:false,reason:"旧记录缺少涡轮几何假设",dimensions:turbine().dimensions,design_point:turbine().design_point}]) {
    assert.equal(inspectTurbineGeometry(t).valid,false);
    for (const build of [buildTurbineSvg,buildTurbineCsv,buildTurbineDxf,buildTurbineObj,buildTurbineJson]) assert.equal(build(t),"");
    assert.equal(buildTurbinePointTxt(t,"hub"),"");assert.deepEqual(buildTurbineMesh(t),{vertices:[],faces:[]});
  }
  const html=renderTurbineGeometry({turbines:{shared:{available:false,reason:"旧记录缺少涡轮几何假设",design_point:turbine().design_point}}});
  assert.doesNotMatch(html,/<svg|<canvas|data-tg-export/);assert.match(html,/旧记录缺少涡轮几何假设/);assert.match(html,/500/);assert.match(html,/缺少的假设和坐标不会由前端默认值补齐/);
  assert.match(renderTurbineGeometry(null),/本条记录未保存可用的涡轮系统点/);
});

test("invalid units, crossing contours, corrupted dimensions and misplaced rows fail closed",()=>{
  const mutations=[t=>t.meridional.unit="mm",t=>t.meridional.rotation_axis="x",t=>t.meridional.coordinate_origin="unknown",t=>t.meridional.hub[1][1]=NaN,t=>t.meridional.hub[1][0]=-.1,t=>t.meridional.hub[1][0]=.2,t=>t.meridional.shroud.pop(),t=>t.meridional.hub[1][1]=.17,t=>t.dimensions.mean_diameter_m=.4,t=>t.dimensions.inlet_area_m2=.1,t=>t.dimensions.outlet_blade_height_m=.08,t=>t.dimensions.axial_length_m=.3,t=>t.rows.pop(),t=>t.rows[1].kind="stator",t=>t.rows[1].stage=2,t=>t.rows[1].z_start_m=.01,t=>t.rows[1].hub[0][1]=.2,t=>t.rows[1].shroud[0][0]=.06,t=>t.design_point.stages=1.5,t=>t.dimensions.row_gap_m=0];
  for(const change of mutations){const t=turbine();change(t);assert.equal(inspectTurbineGeometry(t).valid,false,String(change));assert.equal(buildTurbineObj(t),"");}
});

test("one and multiple stages retain inlet/outlet annuli and row-envelope ordering",()=>{
  for(const count of [1,2,6]) {const t=turbine(count),data=inspectTurbineGeometry(t);assert.equal(data.valid,true,data.reason);assert.equal(data.rows.length,count*2);assert.equal(data.radius,.17);assert.equal(data.length,t.dimensions.axial_length_m);}
  assert.equal(inspectTurbineGeometry(turbine(7)).valid,false);
  const t=turbine(),svg=buildTurbineSvg(t),scale=Number(svg.match(/data-tg-scale="([^"]+)"/)[1]);assert.ok(Math.abs(scale-360/.34)<1e-10);
  const encoded=svg.match(/data-tg-coordinates="([^"]+)"/)[1].replaceAll("&quot;",'"');assert.deepEqual(JSON.parse(encoded),{hub:t.meridional.hub,shroud:t.meridional.shroud});
  for(const text of ["Lz 198 mm","Ds₁ 320 mm","Dh₁ 280 mm","Ds₂ 340 mm","Dh₂ 260 mm","Dm 300 mm","h₁ 20 mm","h₂ 40 mm","首级静叶入口 z = 0","z、r 等比例","不代表叶型","静叶排包络","动叶排包络"]) assert.ok(svg.includes(text),text);
  assert.equal((svg.match(/data-tg-row=/g)||[]).length,4);
});

test("CSV and separate headerless TXT files preserve canonical coordinates and record identity",()=>{
  const t=turbine(),csv=buildTurbineCsv(t).trim().split("\r\n");assert.equal(csv[0],"boundary,point_index,z_m,r_m,record_id,geometry_sha256");assert.equal(csv.length,7);
  const points=csv.slice(1).map(line=>line.split(",").slice(2,4).map(Number));assert.deepEqual(points,[...t.meridional.hub,...t.meridional.shroud]);assert.match(csv[1],/"record-123"/);assert.match(csv[1],new RegExp(t.geometry_sha256));
  for(const boundary of ["hub","shroud"])assert.deepEqual(buildTurbinePointTxt(t,boundary).trim().split("\r\n").map(line=>line.split("\t").map(Number)),t.meridional[boundary]);
  assert.equal(buildTurbinePointTxt(t,"unexpected"),"");
  t.source.record_id='record,"quoted"';assert.match(buildTurbineCsv(t),/"record,""quoted"""/);
});

test("DXF contains exactly two open polylines in metres and retains unrounded coordinates",()=>{
  const t=turbine(),lines=buildTurbineDxf(t).trim().split("\r\n"),pairs=[];for(let i=0;i<lines.length;i+=2)pairs.push([Number(lines[i]),lines[i+1]]);
  assert.ok(pairs.some((p,i)=>p[0]===9&&p[1]==="$INSUNITS"&&pairs[i+1][0]===70&&pairs[i+1][1]==="6"));assert.ok(pairs.some(p=>p[0]===999&&p[1].includes("record-123")));
  const curves=[];let curve=null,z=null;
  for(const [code,value] of pairs){if(code===0&&value==="LWPOLYLINE"){curve={points:[]};curves.push(curve);}else if(code===0)curve=null;else if(curve&&code===8)curve.layer=value;else if(curve&&code===70)curve.closed=Number(value);else if(curve&&code===10)z=Number(value);else if(curve&&code===20)curve.points.push([z,Number(value)]);}
  assert.equal(curves.length,2);for(const c of curves){assert.equal(c.closed,0);assert.deepEqual(c.points,t.meridional[c.layer.toLowerCase()]);}
});

test("3D and OBJ revolve saved boundaries into open surfaces without fictional blades or end caps",()=>{
  const t=turbine(),mesh=buildTurbineMesh(t,12),cut=buildTurbineMesh(t,12,true),curves=[...t.meridional.hub,...t.meridional.shroud];
  assert.equal(mesh.vertices.length,72);assert.equal(mesh.faces.length,48);assert.equal(cut.vertices.length,78);assert.equal(cut.faces.length,48);
  mesh.vertices.forEach((p,i)=>{const [z,r]=curves[Math.floor(i/12)];assert.equal(p[2],z);assert.ok(Math.abs(Math.hypot(p[0],p[1])-r)<1e-14);});
  assert.equal(buildTurbineMesh(t,100000).vertices.length,6*40);
  const obj=buildTurbineObj(t),vertices=[],faces=[],groups=[];
  for(const line of obj.trim().split("\n")){if(line.startsWith("v "))vertices.push(line.slice(2).split(" ").map(Number));if(line.startsWith("f "))faces.push(line.slice(2).split(" ").map(Number));if(line.startsWith("g "))groups.push(line.slice(2));}
  assert.equal(vertices.length,240);assert.equal(faces.length,160);assert.deepEqual(groups,["hub","shroud"]);assert.ok(vertices.every(p=>p.length===3&&p.every(Number.isFinite)));assert.ok(faces.every(f=>f.length===4&&f.every(i=>Number.isInteger(i)&&i>=1&&i<=vertices.length)));
  assert.match(obj,/units: m/);assert.match(obj,/Open surfaces only/);assert.match(obj,/record-123/);assert.match(obj,/origin=first_stator_inlet/);
});

test("exchange JSON preserves all assumptions, row envelopes and source; filenames support shared turbines",()=>{
  const m=model(),t=m.turbines.ox,data=JSON.parse(buildTurbineJson(t,m.source));assert.deepEqual(data.turbine,t);assert.equal(data.source.record_id,"record-123");assert.equal(data.exchange.length_unit,"m");assert.equal(data.exchange.coordinate_origin,"first_stator_inlet");assert.equal(data.exchange.rotation_axis,"z");assert.match(data.exchange.usage,/CFD-ready mesh/);
  assert.equal(turbineExportFilename(m,"ox","obj"),"turbine-ox-record-123.obj");assert.equal(turbineExportFilename(m,"shared","hub-txt"),"turbine-shared-record-123-hub.txt");m.source.record_id='../../<bad>';assert.doesNotMatch(turbineExportFilename(m,"fuel","dxf"),/[<>/\\]/);
});

test("selection follows available FFSC or GG branches, preserves physical scope and has no editable assumptions",()=>{
  const html=renderTurbineGeometry(model());for(const text of ["氧侧涡轮","燃料侧涡轮","二维子午面","三维流道边界","剖开 ¼","线框","尺寸与设计点 JSON","轮毂 TXT","外缘 TXT","尚未评估","系统 p、T 作为静态近似","几何插值","全部保存级数","CFD 网格","CFTurbo","NX / UG","OBJ 始终导出完整周向边界"])assert.ok(html.includes(text),text);
  assert.match(html,/data-tg-turbine="fuel" hidden/);assert.match(html,/data-tg-panel="3d" hidden/);assert.doesNotMatch(html,/<input|<select/);
  const shared=renderTurbineGeometry({turbines:{shared:{...turbine(),label:"共轴涡轮"}}});assert.match(shared,/data-tg-side="shared" aria-pressed="true"/);assert.doesNotMatch(shared,/data-tg-turbine="shared" hidden|data-tg-side="ox"|data-tg-side="fuel"/);
  const fuelOnly=renderTurbineGeometry({turbines:{fuel:turbine()}});assert.doesNotMatch(fuelOnly,/data-tg-turbine="fuel" hidden/);
});

test("untrusted text and links are escaped; rendering and exporters do not mutate record snapshots",()=>{
  const m=model();m.source.record_id='<script>alert(1)</script>';m.turbines.ox.label='<img src=x onerror="bad()">';m.turbines.ox.rows[0].label='<iframe>';m.turbines.ox.notes=['<svg onload=bad()>'];m.turbines.ox.checks[0].label='<iframe>';m.turbines.ox.references.push({title:"bad",url:"javascript:bad()"});m.turbines.fuel.reason='<img>';m.turbines.fuel.available=false;
  const before=JSON.stringify(m),html=renderTurbineGeometry(m);assert.doesNotMatch(html,/<script|<img|<iframe|<svg onload|javascript:/);assert.match(html,/&lt;script/);assert.match(html,/href="https:\/\/manual.cfturbo.com/);
  for(const build of [buildTurbineCsv,buildTurbineDxf,buildTurbineObj,buildTurbineJson])build(m.turbines.ox);assert.equal(JSON.stringify(m),before);
});

test("saved assumptions and provenance checks retain their declared statuses",()=>{
  const m=model();m.turbines.ox.checks=[{id:"state_convention",status:"assumed",label:"状态约定"},{id:"diameter_provenance",status:"info",label:"直径来源"}];
  const html=renderTurbineGeometry(m);assert.match(html,/采用显式假设/);assert.match(html,/参考信息/);assert.match(html,/涡轮功率（传动损失前）/);assert.doesNotMatch(html,/状态未记录/);
});

function fakeDom(keys=["ox","fuel"],withCanvas=false){
  class Element {constructor(dataset={}){this.dataset=dataset;this.events=new Map();this.attrs={};this.hidden=false;}addEventListener(type,fn){if(!this.events.has(type))this.events.set(type,new Set());this.events.get(type).add(fn);}removeEventListener(type,fn){this.events.get(type)?.delete(fn);}setAttribute(k,v){this.attrs[k]=v;}hasAttribute(k){return Object.hasOwn(this.attrs,k);}closest(){return this;}contains(){return true;}matches(){return true;}fire(type,event){for(const f of this.events.get(type)||[])f(event);}querySelector(key){return this.selectors?.[key] || null;}querySelectorAll(key){return this.groups?.[key] || [];}focus(){}getBoundingClientRect(){return {width:900,height:450};}}
  const root=new Element(),tabs=keys.map(side=>new Element({tgSide:side})),panels=[],frames=new Map(),observers=[],draws=[];let next=1;
  const win={devicePixelRatio:1,requestAnimationFrame:fn=>{const id=next++;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id),ResizeObserver:class{constructor(fn){this.fn=fn;observers.push(this);}observe(){}disconnect(){this.disconnected=true;}}};
  const ctx={setTransform(){},clearRect(){},fillRect(){},beginPath(){},lineTo(x,y){draws.push([x,y]);},moveTo(){},closePath(){},fill(){},stroke(){},fillText(){}};
  keys.forEach((side,i)=>{const panel=new Element({tgTurbine:side}),canvas=new Element(),error=new Element(),views=[new Element({tgView:"2d"}),new Element({tgView:"3d"})],viewPanels=[new Element({tgPanel:"2d"}),new Element({tgPanel:"3d"})];canvas.getContext=()=>withCanvas?ctx:null;panel.hidden=i!==0;viewPanels[1].hidden=true;error.hidden=true;panel.selectors={'[data-tg-canvas]':canvas,'[data-tg-canvas-error]':error,'[data-tg-panel="3d"]':viewPanels[1]};panel.groups={"[data-tg-view]":views,"[data-tg-panel]":viewPanels};panels.push(panel);});
  root.ownerDocument={defaultView:win};root.selectors=Object.fromEntries(panels.map(p=>[`[data-tg-turbine="${p.dataset.tgTurbine}"]`,p]));root.groups={'[data-tg-side]':tabs,'[data-tg-turbine]':panels};
  return {root,tabs,panels,frames,observers,draws,flush(){const callbacks=[...frames.values()];frames.clear();callbacks.forEach(fn=>fn());}};
}

test("switching preserves turbine-local view and cleanup cancels callbacks, observers and listeners",()=>{
  const dom=fakeDom(),cleanup=mountTurbineGeometry(dom.root,model());dom.root.fire("click",{target:dom.tabs[1]});assert.equal(dom.panels[0].hidden,true);assert.equal(dom.panels[1].hidden,false);assert.equal(dom.tabs[1].attrs["aria-pressed"],"true");
  const panel=dom.panels[1],view=panel.groups['[data-tg-view]'][1];panel.fire("click",{target:view});assert.equal(panel.groups['[data-tg-panel]'][1].hidden,false);assert.equal(dom.panels[0].groups['[data-tg-panel]'][1].hidden,true);assert.equal(panel.selectors['[data-tg-canvas-error]'].hidden,false);
  assert.equal(dom.frames.size,2);cleanup();assert.equal(dom.frames.size,0);assert.ok(dom.observers.every(o=>o.disconnected));assert.equal(dom.root._turbineCleanup,undefined);assert.equal(dom.root.events.get("click").size,0);assert.equal(panel.events.get("click").size,0);cleanup();
});

test("shared-turbine canvas rotates by keyboard and releases all state on remount",()=>{
  const dom=fakeDom(["shared"],true),m={turbines:{shared:turbine()}};mountTurbineGeometry(dom.root,m);const panel=dom.panels[0],canvas=panel.selectors['[data-tg-canvas]'];panel.fire("click",{target:panel.groups['[data-tg-view]'][1]});dom.flush();const first=dom.draws[0];assert.ok(dom.draws.length>0);assert.ok(dom.draws.every(p=>p.every(Number.isFinite)));
  dom.draws.length=0;canvas.fire("keydown",{key:"ArrowRight",preventDefault(){}});dom.flush();assert.notDeepEqual(dom.draws[0],first);
  const cleanup=mountTurbineGeometry(dom.root,m);assert.equal(dom.root.events.get("click").size,1);assert.ok(dom.observers[0].disconnected);cleanup();assert.equal(typeof mountTurbineGeometry(null,m),"function");mountTurbineGeometry(null,m)();
});

test("all six download buttons dispatch the selected turbine payload, MIME, filename and URL cleanup",async()=>{
  const dom=fakeDom(),m=model(),downloads=[],appended=[],revoked=[],timers=[],blobs=new Map();
  m.turbines.fuel={...turbine(1),label:"燃料侧涡轮"};
  const doc=dom.root.ownerDocument,win=doc.defaultView;
  win.Blob=Blob;
  win.URL={createObjectURL(blob){const url=`blob:turbine-test-${blobs.size}`;blobs.set(url,blob);return url;},revokeObjectURL(url){revoked.push(url);}};
  win.setTimeout=(fn,delay)=>{timers.push({fn,delay});return timers.length;};
  doc.body={append(anchor){appended.push(anchor);anchor.attached=true;}};
  doc.createElement=tag=>{assert.equal(tag,"a");return {click(){assert.equal(this.attached,true);downloads.push({url:this.href,name:this.download,blob:blobs.get(this.href),anchor:this});},remove(){this.attached=false;this.removed=true;}};};
  const cleanup=mountTurbineGeometry(dom.root,m);
  dom.root.fire("click",{target:dom.tabs[1]});
  assert.equal(dom.panels[1].hidden,false);
  const expected={csv:buildTurbineCsv(m.turbines.fuel),dxf:buildTurbineDxf(m.turbines.fuel),obj:buildTurbineObj(m.turbines.fuel),json:buildTurbineJson(m.turbines.fuel,m.source),"hub-txt":buildTurbinePointTxt(m.turbines.fuel,"hub"),"shroud-txt":buildTurbinePointTxt(m.turbines.fuel,"shroud")};
  for(const ext of Object.keys(expected)) {
    const button={dataset:{tgExport:ext},closest(){return this;},hasAttribute(){return false;}};
    dom.panels[1].fire("click",{target:button});
    const download=downloads.at(-1);
    assert.equal(download.name,turbineExportFilename(m,"fuel",ext));
    assert.match(download.name,/^turbine-fuel-record-123/);
    assert.equal(download.blob.type,ext==="json"?"application/json":"text/plain;charset=utf-8");
    assert.equal(await download.blob.text(),expected[ext]);
    assert.equal(download.anchor.removed,true);
    assert.notEqual(expected[ext],ext==="json"?buildTurbineJson(m.turbines.ox,m.source):({csv:buildTurbineCsv,dxf:buildTurbineDxf,obj:buildTurbineObj,"hub-txt":t=>buildTurbinePointTxt(t,"hub"),"shroud-txt":t=>buildTurbinePointTxt(t,"shroud")})[ext](m.turbines.ox));
  }
  assert.equal(downloads.length,6);assert.equal(appended.length,6);assert.equal(new Set(downloads.map(d=>d.url)).size,6);assert.deepEqual(revoked,[]);
  assert.equal(timers.length,6);assert.ok(timers.every(timer=>timer.delay===1000));
  cleanup();timers.forEach(timer=>timer.fn());
  assert.deepEqual(revoked,downloads.map(download=>download.url));
  assert.ok(appended.every(anchor=>anchor.removed&&!anchor.attached));
});
