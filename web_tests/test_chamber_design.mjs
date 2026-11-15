import test from "node:test";
import assert from "node:assert/strict";
import {
  renderChamberDesign, mountChamberDesign, inspectChamberGeometry,
  buildChamberSvg, buildChamberSurfaceSvg, chamberDesignCsv, chamberDesignJson,
} from "../webapp/static/chamber-design.js";

const field=(key,value,unit="—",label=key)=>({key,label,value,unit,status:value==null?"missing":"saved",source:`saved.${key}`,note:"同一计算记录"});
const composition=()=>({status:"saved",basis:"mass_fraction",mass_fraction_sum:1,rows:[
  {species:"H2O",mass_fraction:.6,mole_fraction:.7,molar_mass_kg_kmol:18.015},
  {species:"CO2",mass_fraction:.3,mole_fraction:.15,molar_mass_kg_kmol:44.01},
  {species:"CO",mass_fraction:.1,mole_fraction:.15,molar_mass_kg_kmol:28.01},
],note:"质量分数保持保存原值"});
const geometry=()=>({available:true,export_allowed:true,source:{label:"保存完整内型面截取"},contour_sha256:"abc123",contour:{x_m:[0,.1,.2,.3,.4],r_m:[.18,.18,.18,.14,.11],coordinate_origin:"injector_face"},dimensions:{chamber_diameter_m:.36,throat_diameter_m:.22,chamber_length_m:.2,convergent_length_m:.2,chamber_total_length_m:.4,throat_x_m:.4,total_length_m:2.4},metrics:[field("cc_l_star_m",1.23456789,"m","特征长度 L*")],consistency:{status:"pass",checks:[{id:"diameter",label:"主室内径一致性",status:"pass",geometry_value:.36,record_value:.36,unit:"m"}]}});
const model=()=>({available:true,schema_version:1,status:"review",source:{record_id:"record-1",label:"测试保存设计点"},
  metrics:[field("pressure_Pa",33e6,"Pa","保存压力"),field("temperature_K",3512.12345678,"K","主室温度"),field("mass_flow_kg_s",766.12345678,"kg/s","质量流量"),field("density_kg_m3",25,"kg/m³")],
  model:{id:"hp_equilibrium",label:"定压定焓气相平衡参考态",metadata:{mechanism:"gri30.yaml",eos:"ideal-gas",high_pressure_validation:"not_validated"},state_basis:{kind:"reservoir_approximation"},metrics:[field("legacy_temperature_factor",null)],notes:["零维主室参考态"]},
  inlets:[{id:"ox_turbine_exhaust",label:"富氧涡轮排气",metrics:[field("enthalpy_Jkg",-123456.789,"J/kg")],composition:composition(),state_basis:{reason:"总态接口尚需确认"}},{id:"fuel_turbine_exhaust",label:"富燃涡轮排气",metrics:[field("mass_flow_kg_s",180,"kg/s")],composition:composition(),state_basis:{kind:"reservoir_approximation"}}],
  composition:composition(),balances:{status:"consistent",metrics:[field("heat_loss_W",0,"W")],checks:[{key:"mass",label:"流量闭合",status:"pass",actual:766.12345678,expected:766.12345678,unit:"kg/s"}]},
  performance:{metrics:[field("c_star_effective_ms",1845.123456789,"m/s","系统有效 c*"),field("c_star_theoretical_ms",null,"m/s"),field("eta_cstar",null)]},geometry:geometry(),
  thermal:{status:"saved",throat_x_m:.4,scope:"保存的一维主室冷却单元",metrics:[field("heat_load_chamber_W",1.23456789e6,"W")],nodes:[{x_m:.03,q_W_m2:2e6,T_wall_hot_K:700,T_wall_cold_K:500,T_coolant_K:140,p_coolant_Pa:65e6,T_static_K:3500,p_gas_Pa:32e6,M:.12,T_aw_K:3509,Q_W:123456.78},{x_m:.2,q_W_m2:null,T_wall_hot_K:null},{x_m:.35,q_W_m2:4e6,T_wall_hot_K:800}],checks:[{key:"chamber_heat_sum",label:"主室热量核对",status:"pass",actual:1e6,expected:1e6,unit:"W"}]},
  materials:{status:"review",requested_material_id:"grcop84",actual_material_ids:["grcop84"],metrics:[field("audited_cells",1,"个")],issues:[],evidence:[{cell_index:12,x_m:.03,material_id:"grcop84",dataset_id:"nasa-84",manufacturing_state_id:"wrought",actual_temperature_range_K:[500,700],data_temperature_range_K:[293,1000],status:"within_range"}]},
  scope:[{key:"stability",label:"燃烧稳定性",status:"screening_only",note:"频率不构成稳定性证明"},{key:"kinetics",label:"有限速率反应动力学",status:"not_modelled",note:"未求解"}],checks:[{key:"cstar",label:"特征速度一致性",status:"pass",actual:1845,expected:1845}],
  references:[{title:"NASA SP-8087",year:1972,url:"https://ntrs.nasa.gov/citations/19730022965",use:"冷却与结构交接"}],handoff:[{topic:"反应流 CFD",available:"入口状态与内型面",required:"喷注器与网格"}],notes:["不重算历史记录"]});

test("saved main chamber page shows actual backend keys, five tabs and model scope",()=>{
  const value=model(),before=JSON.stringify(value),html=renderChamberDesign(value);
  assert.match(html,/主燃烧室设计与分析/);assert.match(html,/33<small>MPa/);assert.match(html,/1,845\.123<small>m\/s/);
  assert.equal((html.match(/role="tab"/g)||[]).length,5);assert.equal((html.match(/role="tabpanel"/g)||[]).length,5);
  assert.match(html,/gri30\.yaml/);assert.match(html,/ideal-gas/);assert.match(html,/总态接口尚需确认/);
  assert.match(html,/data-cd-action="validation"/);assert.match(html,/相同元素库存、入口焓、压力与允许物种/);
  assert.match(html,/不构成燃尽或燃烧稳定性验证/);assert.match(html,/不等于燃料转化率/);
  assert.match(html,/仅作筛查/);assert.match(html,/未建模/);assert.match(html,/主室热量核对/);
  assert.equal(JSON.stringify(value),before);
});

test("empty and partial records preserve missing values without invented plots",()=>{
  assert.match(renderChamberDesign(null),/请选择已完成/);
  const html=renderChamberDesign({available:true});
  assert.doesNotMatch(html,/NaN|Infinity|undefined|\[object Object\]/);
  assert.doesNotMatch(html,/class="nd-chart"|class="cd-contour"|class="cd-surface"/);
  assert.match(html,/未保存两股入口燃气/);assert.match(html,/没有保存可绘制/);
  assert.match(html,/未保存组分数据/);assert.match(html,/未记录验证证据/);
});

test("saved abnormal values, material mismatch and missing composition remain visible",()=>{
  const value=model();value.status="inconsistent";value.materials.status="inconsistent";
  value.materials.actual_material_ids=["grcop42"];value.materials.issues=["内衬方案 GRCop-84 与计算 GRCop-42 不一致"];
  value.metrics[0]={...value.metrics[0],value:null,status:"invalid"};
  value.composition={status:"invalid",mass_fraction_sum:1.2,rows:[{species:"O2",mass_fraction:1.2,mole_fraction:null}],note:"非归一，未转换"};
  const html=renderChamberDesign(value);
  assert.match(html,/role="alert"/);assert.match(html,/材料记录存在冲突/);assert.match(html,/grcop42/);
  assert.match(html,/nasa-84/);assert.match(html,/wrought/);assert.match(html,/500–700 K/);
  assert.match(html,/数据异常/);assert.match(html,/class="cd-invalid">1\.2/);assert.match(html,/非归一，未转换/);
  const exported=chamberDesignCsv(value,"composition");assert.match(exported,/"O2","1\.2",""/);
});

test("complete species table and CSV retain trace fractions without renormalization",()=>{
  const value=model();value.inlets=[];value.composition.rows=Array.from({length:15},(_,i)=>({species:`species-${i}`,mass_fraction:i===14?1e-12:.08,mole_fraction:null,molar_mass_kg_kmol:null}));
  value.composition.mass_fraction_sum=1.120000000001;
  const html=renderChamberDesign(value),csv=chamberDesignCsv(value,"composition");
  assert.match(html,/species-14/);assert.equal((html.match(/height="17" fill="#487995"/g)||[]).length,10);
  assert.equal((csv.match(/"record-1","species-/g)||[]).length,15);
  assert.match(csv,/"species-14","1e-12","",""/);assert.match(html,/1\.12/);
});

test("escaped labels, status fields, geometry and references cannot inject markup",()=>{
  const value=model();value.source.label='<img src=x onerror="bad()">';value.metrics[0].label='<script>bad()</script>';
  value.model.metadata.eos='<iframe src="bad">';value.materials.evidence[0].material_id='<svg onload="bad()">';
  value.references=[{title:"bad",url:"javascript:bad()",use:"<script>bad()</script>"}];value.status='bad" onclick="bad()';
  const html=renderChamberDesign(value);assert.doesNotMatch(html,/<img|<script|<iframe|javascript:|onclick=|<svg[^>]*onload=/);
  assert.match(html,/&lt;img/);assert.match(html,/&lt;script/);assert.match(html,/&lt;iframe/);
});

test("2D and 3D use only saved chamber points with explicit open path fills",()=>{
  const g=geometry(),before=JSON.stringify(g),svg=buildChamberSvg(g),mesh=buildChamberSurfaceSvg(g);
  assert.equal(inspectChamberGeometry(g).length,.4);assert.match(svg,/保存母线范围 400 mm/);
  assert.doesNotMatch(svg,/2,400|2\.4 m|NaN|Infinity/);assert.match(svg,/轴向与径向等比例/);
  const paths=[...svg.matchAll(/<path\s[^>]+>/g)].map(match=>match[0]);assert.ok(paths.every(path=>/fill="(?:none|#eaf1f6)"/.test(path)));
  assert.equal((mesh.match(/<polygon /g)||[]).length,4*32+2);
  assert.match(mesh,/两端开放/);assert.match(mesh,/无实体壁厚/);
  assert.notEqual(mesh,buildChamberSurfaceSvg(g,{angle:"side"}));
  assert.notEqual(mesh,buildChamberSurfaceSvg(g,{angle:"end"}));
  assert.equal(JSON.stringify(g),before);
});

test("invalid or uncertain geometry cannot silently export coordinates",()=>{
  for(const change of [g=>g.contour.x_m[2]=.1,g=>g.contour.r_m[1]=NaN,g=>g.contour.r_m[1]=0,g=>g.contour.x_m[4]=.5,g=>g.contour.x_m[4]=.399,g=>g.contour.x_m[0]=.001,g=>delete g.dimensions.throat_x_m]){
    const value=model();change(value.geometry);assert.equal(inspectChamberGeometry(value.geometry).valid,false);
    assert.equal(buildChamberSvg(value.geometry),"");assert.equal(buildChamberSurfaceSvg(value.geometry),"");assert.equal(chamberDesignCsv(value,"coordinates"),"");
  }
  for(const change of [g=>g.export_allowed=false,g=>delete g.export_allowed,g=>g.consistency.status="partial",g=>g.consistency.status="inconsistent"]){
    const value=model();change(value.geometry);assert.equal(chamberDesignCsv(value,"coordinates"),"");
    assert.match(renderChamberDesign(value),/data-cd-export="coordinates" disabled/);
  }
});

test("raw composition, manufacturing declarations and gas range evidence retain their own basis",()=>{
  const value=model();
  value.composition={status:"invalid",basis:"mole_fraction",mass_fraction_sum:null,rows:[{species:"H2O",raw_fraction:.6,mass_fraction:null,mole_fraction:null}],note:"保存基准不符，未转换"};
  value.materials.evidence[0].declared_manufacturing_state="unspecified";
  value.materials.evidence[0].manufacturing_match="unknown";
  value.model.temperature_range_evidence={status:"extrapolated",temperature_K:3512,valid_temperature_min_K:200,valid_temperature_max_K:3000,common_temperature_min_K:200,common_temperature_max_K:3000,outside_mass_fraction:.001,species_outside:[{species:"OH",mass_fraction:.001,min_temperature_K:200,max_temperature_K:3000}],interpretation:"温区外推不是误差百分比"};
  const before=JSON.stringify(value),html=renderChamberDesign(value),csv=chamberDesignCsv(value,"composition");
  assert.match(html,/原保存分数（未转换）/);assert.match(html,/保存基准：mole_fraction/);
  assert.match(csv,/"declared_basis","raw_fraction"/);assert.match(csv,/"mole_fraction","0\.6"/);
  assert.match(html,/数据集试样工艺：wrought/);assert.match(html,/方案声明：未指定/);assert.match(html,/尚未确认/);
  assert.match(html,/保存检查：存在温区外推/);assert.match(html,/200–3,000 K/);assert.match(html,/温区外推不是误差百分比/);
  assert.match(html,/0\.001/);assert.equal(JSON.stringify(value),before);
  value.materials.evidence[0].declared_manufacturing_state="different-process";
  value.materials.evidence[0].manufacturing_match="mismatch";
  assert.match(renderChamberDesign(value),/工艺声明不匹配/);
});

test("coordinates preserve record, raw precision, origin and point order in m or mm",()=>{
  const value=model();value.geometry.contour.x_m[1]=.123456789012345;const before=JSON.stringify(value);
  const csv=chamberDesignCsv(value,"coordinates",{unit:"m"}),mm=chamberDesignCsv(value,"coordinates",{unit:"mm"});
  assert.match(csv,/"record_id","x_m","r_m"/);assert.match(csv,/"0\.123456789012345","0\.18"/);
  assert.match(csv,/"injector_face","abc123"/);assert.match(mm,/"123\.456789012345","180"/);
  assert.equal(csv.trim().split("\r\n").length,6);assert.equal(chamberDesignCsv(value,"coordinates",{unit:"cm"}),"");
  assert.equal(JSON.stringify(value),before);
});

test("thermal charts preserve missing gaps and export every saved thermal column",()=>{
  const value=model(),html=renderChamberDesign(value),csv=chamberDesignCsv(value,"thermal");
  assert.equal((html.match(/class="nd-chart-line"/g)||[]).length,0);
  assert.equal((html.match(/class="nd-chart-dot"/g)||[]).length,4);
  for(const key of ["T_static_K","p_gas_Pa","M","T_aw_K","Q_W"])assert.match(csv,new RegExp('"'+key+'"'));
  assert.match(csv,/"123456\.78"/);assert.match(csv,/"0\.2","",""/);
  for(const change of [m=>m.thermal.status="invalid",m=>m.thermal.nodes[2].x_m=.4,m=>m.thermal.nodes[2].x_m=.2,m=>m.thermal.nodes[0].x_m=-.1]){
    const invalid=model();change(invalid);assert.equal(chamberDesignCsv(invalid,"thermal"),"");assert.doesNotMatch(renderChamberDesign(invalid),/class="nd-chart"/);
  }
});

test("partial thermal samples and location-specific material mismatch retain their evidence",()=>{
  const value=model();value.thermal.status="partial";value.thermal.note="热流缺少 1 个保存样本，曲线保留断点";
  value.materials.checks=[{label:"主室单元材料分配",status:"fail",x_m:.123,expected_material_id:"grcop84",expected_dataset_id:"expected-data",actual_material_id:"grcop42",actual_dataset_id:"actual-data",note:"按保存分区检查"}];
  const html=renderChamberDesign(value);assert.match(html,/热流缺少 1 个保存样本/);assert.match(html,/资料不全/);
  assert.match(html,/X = 0\.123 m/);assert.match(html,/期望材料 grcop84 \/ 数据集 expected-data/);assert.match(html,/实际材料 grcop42 \/ 数据集 actual-data/);
  assert.equal((html.match(/class="nd-chart-line"/g)||[]).length,0);
});

test("CSV is formula safe, retains signed enthalpy and zero, JSON is record faithful",()=>{
  const value=model();value.metrics.push({...field("formula",0),label:'=HYPERLINK("bad")',note:"one\ntwo"});
  const before=JSON.stringify(value),csv=chamberDesignCsv(value);
  assert.match(csv,/"3512\.12345678","K"/);assert.match(csv,/"-123456\.789","J\/kg"/);assert.doesNotMatch(csv,/"'-123456/);
  assert.match(csv,/"'=HYPERLINK\(""bad""\)"/);assert.match(csv,/"one\ntwo"/);assert.match(csv,/"0","W"/);
  assert.deepEqual(JSON.parse(chamberDesignJson(value)),value);assert.equal(JSON.stringify(value),before);
  value.metrics[0].value=Infinity;assert.equal(JSON.parse(chamberDesignJson(value)).metrics[0].value,null);
  assert.equal(chamberDesignCsv(null),"");assert.equal(chamberDesignJson(null),"");assert.equal(chamberDesignCsv(value,"unknown"),"");
});

function element(dataset={},selector="button"){
  return {dataset,selector,hidden:false,innerHTML:"",textContent:"",disabled:false,attributes:{},setAttribute(key,value){this.attributes[key]=value;},focus(){this.focused=true;},closest(query){return query===this.selector || query==="button" && this.selector==="[data-cd-tab]" ? this:null;},matches(query){return query===this.selector;}};
}
function workspace(value=model(),options={}){
  const tabs=["overview","chemistry","geometry","thermal","methods"].map(id=>element({cdTab:id},"[data-cd-tab]")),panels=tabs.map(tab=>element({cdPanel:tab.dataset.cdTab}));
  const views=["2d","3d"].map(id=>element({cdView:id})),gpanels=views.map(view=>element({cdGeometryPanel:view.dataset.cdView})),angles=["iso","side","end"].map(id=>element({cdAngle:id}));
  const feedback=element(),surface=element(),listeners=new Map(),downloads=[];
  const host={dataset:{},contains:()=>true,
    querySelectorAll:selector=>({"[data-cd-tab]":tabs,"[data-cd-panel]":panels,"[data-cd-view]":views,"[data-cd-geometry-panel]":gpanels,"[data-cd-angle]":angles})[selector]||[],
    querySelector:selector=>({"[data-cd-export-status]":feedback,"[data-cd-surface]":surface,'[data-cd-geometry-panel="2d"]':gpanels[0]})[selector]||null,
    addEventListener(type,listener){listeners.set(type,listener);},removeEventListener(type,listener){assert.equal(listeners.get(type),listener);listeners.delete(type);}};
  const dispose=mountChamberDesign(host,value,{onDownload:(...args)=>downloads.push(args),...options});
  return {host,tabs,panels,views,gpanels,angles,feedback,surface,listeners,downloads,dispose,event:(type,target,extra={})=>listeners.get(type)?.({target,preventDefault(){},...extra})};
}

test("mount supports keyboard tabs, geometry/validation navigation and cleanup",()=>{
  let geometryOpened=0,validationOpened=0;const ui=workspace(model(),{onGeometry:()=>geometryOpened++,onValidation:()=>validationOpened++});
  ui.event("click",ui.tabs[2]);assert.equal(ui.host.dataset.chamberDesignTab,"geometry");assert.equal(ui.panels[2].hidden,false);
  ui.event("keydown",ui.tabs[2],{key:"End"});assert.equal(ui.tabs[4].focused,true);assert.equal(ui.panels[4].hidden,false);
  ui.event("keydown",ui.tabs[4],{key:"ArrowRight"});assert.equal(ui.panels[0].hidden,false);
  ui.event("click",element({cdAction:"geometry"}));ui.event("click",element({cdAction:"validation"}));
  assert.equal(geometryOpened,1);assert.equal(validationOpened,1);ui.dispose();assert.equal(ui.listeners.size,0);
});

test("view controls change presentation only and exports follow selected units",()=>{
  const value=model(),before=JSON.stringify(value),ui=workspace(value);
  ui.event("click",ui.views[1]);assert.equal(ui.gpanels[1].hidden,false);assert.equal(ui.gpanels[0].hidden,true);
  ui.event("click",ui.angles[2]);assert.match(ui.surface.innerHTML,/三维旋转参考面/);assert.equal(ui.angles[2].attributes["aria-pressed"],"true");
  const select=element({},"[data-cd-unit]");select.value="m";ui.event("change",select);assert.match(ui.gpanels[0].innerHTML,/保存母线范围 0\.4 m/);
  ui.event("click",element({cdExport:"coordinates"}));assert.match(ui.downloads[0][1],/record-1-coordinates-m\.csv$/);assert.match(ui.downloads[0][0],/"x_m","r_m"/);
  ui.event("click",element({cdExport:"json"}));assert.deepEqual(JSON.parse(ui.downloads[1][0]),value);
  assert.match(ui.feedback.textContent,/已导出/);assert.equal(JSON.stringify(value),before);ui.dispose();
});

test("remount and empty records remove stale handlers; export failures remain text",()=>{
  const ui=workspace(model(),{onDownload(){throw new Error('<script>failed</script>');}});
  ui.event("click",element({cdExport:"metrics"}));assert.equal(ui.feedback.textContent,'<script>failed</script>');assert.equal(ui.feedback.innerHTML,"");
  const dispose=mountChamberDesign(ui.host,model());assert.equal(ui.listeners.size,3);dispose();assert.equal(ui.listeners.size,0);
  const second=workspace();mountChamberDesign(second.host,{available:false});assert.equal(second.listeners.size,0);
  assert.doesNotThrow(()=>mountChamberDesign(null,null)());
});
