import assert from "node:assert/strict";
import test from "node:test";
import {coolingDisplayState,coolingCurveSegments,coolingMaterialState,coolingModelSummaryState,renderCooling} from "../webapp/static/cooling.js";
import {renderAnalysis} from "../webapp/static/results.js";

function record(){
  return {calculation:{inputs:{},summary:{},components:{cooling_channel:{
    outputs:{heat_load_W:999e6},
    profile:{version:1,model_id:"regen_quasi1d_ph_v1",flow_direction:"counterflow",
      summary:{Q_total_W:12e6,dp_total_Pa:3e6,T_out_K:370,p_out_Pa:67e6,h_out_Jkg:800000,h0_out_Jkg:800000,T_wall_hot_max_K:700},
      nodes:[
        {s_m:0,x_m:2,p_Pa:70e6,T_K:120,h_Jkg:500000,h0_Jkg:500000,rho_kgm3:450,velocity_ms:0},
        {s_m:1,x_m:1,p_Pa:68.5e6,T_K:250,h_Jkg:640000,h0_Jkg:642000,rho_kgm3:300,velocity_ms:60},
        {s_m:2,x_m:0,p_Pa:67e6,T_K:370,h_Jkg:800000,h0_Jkg:800000,rho_kgm3:200,velocity_ms:0}],
      cells:[
        {kind:"channel",s_m:.5,x_m:1.5,q_W_m2:10e6,T_wall_hot_K:700,T_wall_cold_K:600,Q_W:5e6,dp_friction_Pa:1e6,dp_momentum_Pa:.3e6,dp_local_Pa:.2e6},
        {kind:"channel",s_m:1.5,x_m:.5,q_W_m2:12e6,T_wall_hot_K:650,T_wall_cold_K:550,Q_W:7e6,dp_friction_Pa:1e6,dp_momentum_Pa:.4e6,dp_local_Pa:.1e6}],
      balance:{energy_residual_W:-.25,max_cell_energy_residual_W:.15,max_cell_momentum_residual_Pa:.02},
      validity:{status:"pass",warnings:[]},
      hot_side_coupling:{mode:"iterated_profile",status:"pass",residual_W:.8,tolerance_W:10,iterations:3},
    }
  }}}};
}

test("one saved profile supplies summaries and curves without changing result data",()=>{
  const result=record(), before=structuredClone(result), html=renderCooling(result);
  assert.deepEqual(result,before);
  assert.equal(coolingDisplayState(result).summary.Q_total_W,12e6);
  assert.match(html,/12 <small>MW/);
  assert.doesNotMatch(html,/999 <small>MW/);
  assert.match(html,/冷却剂压力/);
  assert.match(html,/冷却剂温度/);
  assert.match(html,/冷却剂比焓/);
  assert.match(html,/热面壁温/);
  assert.match(html,/壁面热流密度/);
  assert.match(html,/逆流：冷却剂与热气流向相反/);
  assert.match(html,/-0.25 W/);
  assert.match(html,/0.8 W/);
  assert.match(html,/内核检查通过/);
  assert.match(html,/不能替代.*独立验证/);
});

test("budget and historical output never generates a fake spatial profile",()=>{
  const result={calculation:{components:{cooling_channel:{outputs:{heat_load_W:90e6,delta_P_MPa:5,T_out_K:380}}}}};
  const state=coolingDisplayState(result), html=renderCooling(result);
  assert.equal(state.available,false);
  assert.equal(state.summary.dp_total_Pa,5e6);
  assert.match(html,/90 <small>MW/);
  assert.match(html,/没有保存沿程冷却解/);
  assert.match(html,/不会使用当前参数补算历史曲线/);
  assert.doesNotMatch(html,/<svg|<polyline|内核检查通过/);
});

test("unsupported or incomplete profile cannot claim kernel success",()=>{
  for(const change of [{version:2},{model_id:"future_model"},{nodes:[]},{cells:[]}]){
    const result=record();Object.assign(result.calculation.components.cooling_channel.profile,change);
    assert.equal(coolingDisplayState(result).available,false);
    assert.doesNotMatch(renderCooling(result),/<svg|内核检查通过/);
  }
});

test("missing finite values remain missing and gaps do not join a curve",()=>{
  const points=[{s_m:0,p_Pa:3e6},{s_m:.5,p_Pa:2.8e6},{s_m:1,p_Pa:null},{s_m:1.5,p_Pa:2.5e6},{s_m:2,p_Pa:2e6}];
  assert.deepEqual(coolingCurveSegments(points,"p_Pa",1e-6),[[{x:0,y:3},{x:.5,y:2.8}],[{x:1.5,y:2.5},{x:2,y:2}]]);
  const result=record();result.calculation.components.cooling_channel.profile.nodes[1].T_K=NaN;
  const html=renderCooling(result);
  assert.doesNotMatch(html,/NaN|Infinity/);
  assert.match(html,/<td>—<\/td>/);
  assert.match(html,/部分沿程字段缺失或非有限/);
  assert.equal(coolingDisplayState(result).kind,"review");
  assert.equal(coolingDisplayState({}).summary.Q_total_W,null);
});

test("counterflow uses coolant distance and never sorts or reverses physical records",()=>{
  const points=[{s_m:0,x_m:3,T_K:100},{s_m:1,x_m:2,T_K:200},{s_m:.5,x_m:1,T_K:300},{s_m:2,x_m:0,T_K:400}];
  assert.deepEqual(coolingCurveSegments(points,"T_K"),[[{x:0,y:100},{x:1,y:200}],[{x:.5,y:300},{x:2,y:400}]]);
});

test("manifold bookkeeping values do not become a wall thermal solution",()=>{
  const result=record();
  result.calculation.components.cooling_channel.profile.cells.unshift({kind:"inlet_manifold",s_m:0,x_m:2,q_W_m2:999e6,T_wall_hot_K:9999,T_wall_cold_K:9998});
  const html=renderCooling(result), figures=html.match(/<figure[\s\S]*?<\/figure>/g).join("");
  assert.doesNotMatch(figures,/9999 K|9998 K|999 MW/);
  assert.match(html,/入口集流腔/);
});

test("kernel warnings are visible and override a contradictory positive label",()=>{
  const result=record();result.calculation.components.cooling_channel.profile.validity.warnings=["<script>物性越界</script>"];
  const state=coolingDisplayState(result), html=renderCooling(result);
  assert.equal(state.kind,"review");
  assert.match(html,/适用性需核查/);
  assert.match(html,/&lt;script&gt;物性越界&lt;\/script&gt;/);
  assert.doesNotMatch(html,/<script>|内核检查通过/);
});

test("missing hot-side coupling is explicitly unknown despite coolant convergence",()=>{
  const result=record();delete result.calculation.components.cooling_channel.profile.hot_side_coupling;
  assert.match(renderCooling(result),/未保存热气侧耦合诊断.*不能由冷却剂自身收敛推断/s);
});

test("all coupled residuals, recorded tolerances and complete history remain visible",()=>{
  const result=record(), profile=result.calculation.components.cooling_channel.profile;
  profile.hot_side_coupling={status:"pass",iterations:2,
    pressure_residual_Pa:.12,pressure_tolerance_Pa:.5,
    hot_side_temperature_residual_K:.0002,temperature_tolerance_K:.001,
    hot_side_relative_residual:1e-7,hot_side_relative_tolerance:5e-7,
    cell_heat_relative_residual:2e-7,cell_heat_relative_tolerance:5e-7,
    geometry_relative_residual:1e-9,geometry_relative_tolerance:2e-8,
    heat_relative_residual:2e-9,heat_relative_tolerance:2e-8,
    history:[{iteration:1,pressure_residual_Pa:900,hot_side_temperature_residual_K:32},
             {iteration:2,pressure_residual_Pa:.12,hot_side_temperature_residual_K:.0002,
              hot_side_relative_residual:1e-7,cell_heat_relative_residual:2e-7,
              geometry_relative_residual:1e-9,heat_relative_residual:2e-9}]};
  const before=structuredClone(result), html=renderCooling(result);
  assert.deepEqual(result,before);
  for(const label of ["供给/冷却压力残差","热气侧恢复温度残差","热气状态向量相对残差",
      "逐单元热量相对残差","喉部面积相对残差","总吸热量相对残差"])
    assert.ok(html.includes(label),label);
  for(const value of ["0.12 Pa","0.5 Pa","0.0002 K","0.001 K","5.000e-7","2.000e-8"])
    assert.ok(html.includes(value),value);
  assert.match(html,/耦合迭代历程（2 轮）/);
  assert.match(html,/<td>900<\/td>/);
  assert.doesNotMatch(html,/热量耦合残差（历史字段）/);
});

test("missing coupled residuals or tolerances never become zero-valued evidence",()=>{
  const result=record();
  result.calculation.components.cooling_channel.profile.hot_side_coupling={status:"fail",iterations:1,
    pressure_residual_Pa:12,history:[{iteration:1,pressure_residual_Pa:12}]};
  const html=renderCooling(result);
  assert.match(html,/<td>供给\/冷却压力残差<\/td><td>12 Pa<\/td><td>—<\/td>/);
  assert.match(html,/<td>热气侧恢复温度残差<\/td><td>—<\/td><td>—<\/td>/);
  assert.match(html,/未达到判据/);
});

test("overview and component page mount the same saved cooling presentation",()=>{
  for(const view of ["overview","components"]){
    const html=renderAnalysis(view,{status:"succeeded",result:record()});
    assert.equal((html.match(/aria-label="再生冷却沿程分析"/g)||[]).length,1);
    assert.match(html,/12 <small>MW/);
  }
});

test("fixed hardware area mismatch remains visible despite zero inner area residual",()=>{
  const result=record();
  result.calculation.components.cooling_channel.profile.hot_side_coupling={status:"pass",iterations:2,
    geometry_relative_residual:0,geometry_relative_tolerance:2e-8,
    fixed_hardware_area_relative_residual:.02,fixed_hardware_area_owner:"outer_throttle_solve"};
  const before=structuredClone(result),html=renderCooling(result);
  assert.match(html,/固定硬件喉部面积相对残差：0.02/);
  assert.match(html,/由固定喉部节流外层验收/);
  assert.match(html,/不能据其为零判断固定硬件喉部约束已通过/);
  assert.deepEqual(result,before);
});

test("rejected pressure trials remain diagnostics and do not generate physical curves",()=>{
  const result=record(),coupling=result.calculation.components.cooling_channel.profile.hot_side_coupling;
  const beforeCharts=renderCooling(result).match(/<figure[\s\S]*?<\/figure>/g);
  coupling.rejected_pressure_trials=[{iteration:1,trial_pressure_drop_Pa:5e6,next_pressure_drop_Pa:10e6,
    reason:"<bad> state",accepted_as_physical_state:false}];
  coupling.fixed_hardware_area_relative_residual=null;
  const html=renderCooling(result);
  assert.deepEqual(html.match(/<figure[\s\S]*?<\/figure>/g),beforeCharts);
  assert.match(html,/未接受的压力试探（1 次）/);
  assert.match(html,/&lt;bad&gt; state/);
  assert.doesNotMatch(html,/固定硬件喉部面积相对残差/);
});

test("Bartz details use only saved correlation values and stay collapsed",()=>{
  const result=record(),profile=result.calculation.components.cooling_channel.profile;
  profile.gas_heat_transfer={geometry_model_id:"bartz_upstream_contour_curvature_v2",
    curvature_source:"geometry.throat_upstream_radius_m",curvature_definition:"effective upstream throat contour arc radius",
    throat_diameter_m:.2,curvature_radius_m:.15,curvature_factor:1.02919,
    transport_reference:"supplied_hot_side_state",
    validation_status:"geometry_consistent; transport_reference_and_sigma_require_validation"};
  profile.geometry={throat_upstream_radius_m:.999};
  result.calculation.inputs={throat_upstream_radius_m:1.234};
  const before=structuredClone(result),html=renderCooling(result);
  const detail=html.match(/<details class="cool-details cool-bartz-geometry">[\s\S]*?<\/details>/)?.[0];
  assert.ok(detail);
  assert.match(detail,/<td>150 mm<\/td>/);
  assert.match(detail,/<td>200 mm<\/td>/);
  assert.match(detail,/<td>1.02919<\/td>/);
  assert.match(detail,/规范内轮廓的上游喉部圆弧/);
  assert.match(detail,/本次传入的热气侧状态/);
  assert.match(detail,/几何来源一致不代表关联式已验证/);
  assert.match(detail,/参考态与 σ 修正的适用性仍待校核/);
  assert.doesNotMatch(detail,/<details[^>]*\bopen\b|999|1,234/);
  assert.deepEqual(result,before);
});

test("old cooling records cannot acquire Bartz provenance from other geometry or current inputs",()=>{
  const result=record(),profile=result.calculation.components.cooling_channel.profile;
  profile.geometry={throat_upstream_radius_m:.789,throat_radius_m:.456};
  result.calculation.inputs={throat_upstream_radius_m:.123};
  const before=structuredClone(result),html=renderCooling(result);
  const detail=html.match(/<details class="cool-details cool-bartz-geometry">[\s\S]*?<\/details>/)?.[0];
  assert.ok(detail);
  assert.match(detail,/历史记录未保存 Bartz 曲率来源，需重新计算/);
  assert.doesNotMatch(detail,/123|456|789|<table/);
  assert.match(html,/冷却剂压力/);
  assert.deepEqual(result,before);
});

test("partial and unfamiliar Bartz metadata preserves gaps and escapes source text",()=>{
  const result=record(),profile=result.calculation.components.cooling_channel.profile;
  profile.gas_heat_transfer={geometry_model_id:"future_model",curvature_source:"<script>source</script>",
    curvature_radius_m:NaN,throat_diameter_m:null,curvature_factor:null,transport_reference:"<img>"};
  let html=renderCooling(result);
  let detail=html.match(/<details class="cool-details cool-bartz-geometry">[\s\S]*?<\/details>/)?.[0];
  assert.match(detail,/几何来源版本未识别/);
  assert.match(detail,/&lt;script&gt;source&lt;\/script&gt;/);
  assert.match(detail,/&lt;img&gt;/);
  assert.match(detail,/<td>上游喉部曲率半径 Rᵤ<\/td><td>—<\/td>/);
  assert.doesNotMatch(detail,/<script|<img|NaN|Infinity/);
  profile.gas_heat_transfer={transport_reference:"chamber_reference_estimate"};
  html=renderCooling(result);
  assert.match(html,/历史记录未保存 Bartz 曲率来源，需重新计算/);
  assert.match(html,/主室参考状态估算/);
});

test("wall material report uses saved data, separates manufacturing and temperature evidence",()=>{
  const result=record(), profile=result.calculation.components.cooling_channel.profile;
  result.calculation.inputs={wall_thermal_dataset_id:"CURRENT_DRAFT_MUST_NOT_APPEAR",wall_manufacturing_state:"other"};
  profile.material_applicability={schema_version:1,status:"out_of_range",dataset_ids:["saved84"],out_of_range_cell_count:1,total_cells:2,
    min_temperature_K:550,max_temperature_K:1200,manufacturing_match:"unknown",
    datasets:[{dataset_id:"saved84",label:"GRCop-84 / 挤压态",manufacturing_state:{state_id:"extruded",label:"粉末冶金挤压态"},
      conductivity_property:{range_K:[80,1173]},sources:[{title:"NASA 材料手册",url:"https://ntrs.nasa.gov/citations/20020070630"}]}],
    cells:[{cell_index:1,x_m:.5,dataset_id:"saved84",status:"out_of_range",actual_temperature_range_K:[600,1200],
      data_temperature_range_K:[80,1173],action:"constant_endpoint_continuation"}]};
  const before=structuredClone(result),html=renderCooling(result);
  assert.match(html,/1 \/ 2 个热单元越界/);
  assert.match(html,/实际材料温度 550–1,200 K/);
  assert.match(html,/制造状态未确认/);
  assert.match(html,/导热率数据温区越界/);
  assert.match(html,/端点常值续算（未扩展有效温区）/);
  assert.match(html,/粉末冶金挤压态/);
  assert.match(html,/href="https:\/\/ntrs.nasa.gov\/citations\/20020070630"/);
  assert.doesNotMatch(html,/CURRENT_DRAFT_MUST_NOT_APPEAR|制造状态已匹配|材料已验证/);
  assert.deepEqual(result,before);
});

test("historical or inconsistent wall evidence never gets backfilled or a positive data-range label",()=>{
  const result=record(),profile=result.calculation.components.cooling_channel.profile;
  result.calculation.inputs={wall_thermal_dataset_id:"current84"};
  let html=renderCooling(result);
  assert.match(html,/历史记录未保存逐单元材料数据与温区检查/);
  assert.doesNotMatch(html,/current84|导热率数据温区内/);
  for(const patch of [{schema_version:99},{total_cells:null},{out_of_range_cell_count:NaN},{max_temperature_K:Infinity},{dataset_ids:[]}]){
    profile.material_applicability={schema_version:1,status:"within_range",dataset_ids:["saved"],total_cells:2,out_of_range_cell_count:0,
      min_temperature_K:500,max_temperature_K:700,manufacturing_match:"matched",...patch};
    assert.equal(coolingMaterialState(profile).kind,"unknown");
    html=renderCooling(result);
    assert.doesNotMatch(html,/导热率数据温区内|NaN|Infinity/);
  }
  profile.material_applicability={schema_version:1,status:"within_range",dataset_ids:["saved"],total_cells:2,out_of_range_cell_count:1,
    min_temperature_K:500,max_temperature_K:1300,manufacturing_match:"mismatch"};
  assert.equal(coolingMaterialState(profile).kind,"review");
  assert.match(renderCooling(result),/制造状态不匹配/);
});

test("material data HTML escapes source text and rejects unsafe links",()=>{
  const result=record(),profile=result.calculation.components.cooling_channel.profile;
  profile.material_applicability={schema_version:1,status:"within_range",dataset_ids:["x"],total_cells:2,out_of_range_cell_count:0,
    min_temperature_K:500,max_temperature_K:700,manufacturing_match:"matched",
    datasets:[{dataset_id:"x",label:"<script>bad</script>",manufacturing_state:{label:"<img>"},
      conductivity_property:{range_K:[80,1173]},sources:[{title:"<bad>",url:"javascript:alert(1)"}]}]};
  const html=renderCooling(result);
  assert.match(html,/导热率数据温区内/);
  assert.match(html,/&lt;script&gt;bad&lt;\/script&gt;/);
  assert.match(html,/&lt;img&gt;/);
  assert.doesNotMatch(html,/<script>|<img>|javascript:/);
});

function geometryRecord(){
  const result=record(),profile=result.calculation.components.cooling_channel.profile;
  const full=[{x_m:0,r_m:.2},{x_m:.5,r_m:.1},{x_m:1,r_m:.3},{x_m:2,r_m:.6}];
  profile.geometry={full_nodes:full,nodes:full.slice(0,3).reverse().map(p=>({...p,n_channels:300,width_m:p.r_m*.015,
    height_m:.005,wall_thickness_m:.0005,D_h_m:.002})),flow_direction:"counterflow",x_throat_m:.5,
    coordinate_origin:"injector_face",channel_distribution:"width_proportional_to_radius"};
  profile.material_assignment={default_dataset_id:"saved84",default_manufacturing_state:"extruded",zones:[
    {x_start_m:.3,x_end_m:.7,dataset_id:"saved42",manufacturing_state:"powder_bed_fusion"}]};
  profile.material_applicability={schema_version:1,status:"out_of_range",dataset_ids:["saved84","saved42"],
    total_cells:2,out_of_range_cell_count:1,min_temperature_K:550,max_temperature_K:1200,manufacturing_match:"unknown",
    datasets:[{dataset_id:"saved84",label:"GRCop-84 保存态"},{dataset_id:"saved42",label:"GRCop-42 分区态"}],
    wall_temperature_limit_K:800,wall_temperature_limit_status:"exceeded",
    conduction_model:"integral_k_dT=q_wall_times_thickness"};
  profile.model_methods={version:1,hot_side:{id:"saved-hot",label:"本次热侧方法",detail:"保存的热侧定义"},
    wall:{id:"saved-wall",label:"本次壁面方法",detail:"保存的壁面定义"},
    cold_side:{id:"saved-cold",label:"本次冷侧方法",detail:"保存的冷侧定义"},
    hydraulics:{id:"saved-pressure",label:"本次压降方法",detail:"保存的压降定义"},heat_transfer_enabled:true,friction_enabled:false};
  profile.validity.property_domain="within_HEOS_declared_single_phase_ranges";
  return result;
}

test("model overview presents the saved partial coverage, actual channel dimensions and material zones",()=>{
  const result=geometryRecord(),profile=result.calculation.components.cooling_channel.profile;
  result.calculation.inputs={cooling_channel_count:999,wall_thermal_dataset_id:"LIVE_DRAFT",cooling_coverage_area_ratio:999};
  const before=structuredClone(result), model=coolingModelSummaryState(profile),html=renderCooling(result);
  assert.equal(model.drawingAvailable,true);
  assert.equal(model.inletX,1);
  assert.equal(model.outletX,0);
  assert.match(html,/型面出口 x=2 m/);
  assert.match(html,/冷却剂入口 x=1 m → 出口 x=0 m/);
  assert.match(html,/<dt>通道数量<\/dt><dd>300<\/dd>/);
  assert.match(html,/1.5–4.5 × 5/);
  assert.match(html,/GRCop-84 保存态；1 个轴向覆盖分区/);
  assert.match(html,/<td>0.3 m<\/td><td>0.7 m<\/td><td>GRCop-42 分区态<\/td>/);
  assert.doesNotMatch(html,/LIVE_DRAFT/);
  assert.deepEqual(result,before);
});

test("saved methods and check summaries do not turn applicability into hardware qualification",()=>{
  const result=geometryRecord(),html=renderCooling(result);
  for(const phrase of ["本次热侧方法","本次壁面方法","本次冷侧方法","保存的压降定义","换热：启用；摩擦：关闭",
    "在 HEOS 声明单相范围内","超过方案壁温限值","1 / 2 个热单元越界","不等于材料使用资格"])
    assert.ok(html.includes(phrase),phrase);
  const methods=html.match(/<details class="cool-details cool-method-detail">[\s\S]*?<\/details>/)?.[0];
  assert.ok(methods);
  assert.doesNotMatch(methods,/<details[^>]*\bopen\b/);
  assert.doesNotMatch(html,/方法明细未随记录保存|硬件检查通过/);
});

test("coflow and counterflow arrows follow the recorded node order without mutating it",()=>{
  const result=geometryRecord(),profile=result.calculation.components.cooling_channel.profile;
  profile.flow_direction="coflow";profile.geometry.flow_direction="coflow";profile.geometry.nodes.reverse();
  const before=structuredClone(result),html=renderCooling(result);
  assert.equal(coolingModelSummaryState(profile).drawingAvailable,true);
  assert.match(html,/冷却剂入口 x=0 m → 出口 x=1 m/);
  assert.match(html,/顺流：冷却剂与热气流向相同/);
  assert.deepEqual(result,before);
});

test("corrupt geometry or conflicting direction cannot draw an invented cooled outline",()=>{
  const patches=[
    p=>{p.geometry.full_nodes[1].r_m=NaN;},
    p=>{p.geometry.nodes[0].r_m=.333;},
    p=>{p.geometry.nodes[0].x_m=3;},
    p=>{p.flow_direction="coflow";},
    p=>{p.geometry.nodes[1]=null;},
    p=>{delete p.geometry.full_nodes;}
  ];
  for(const patch of patches){
    const result=geometryRecord(),profile=result.calculation.components.cooling_channel.profile;patch(profile);
    const html=renderCooling(result),figure=html.match(/<figure class="cool-scope-figure">[\s\S]*?<\/figure>/)?.[0];
    assert.equal(coolingModelSummaryState(profile).drawingAvailable,false);
    assert.match(figure,/无法绘制范围示意/);
    assert.doesNotMatch(figure,/<svg|NaN|Infinity/);
    assert.match(html,/冷却剂压力/);
  }
});

test("missing historical methods or hardware fields stay unrecorded instead of adopting current defaults",()=>{
  const result=geometryRecord(),profile=result.calculation.components.cooling_channel.profile;
  delete profile.model_methods;delete profile.material_applicability;delete profile.material_assignment;
  for(const node of profile.geometry.nodes)delete node.height_m;
  result.calculation.inputs={cooling_channel_height_m:.009,wall_thermal_dataset_id:"NEW_DEFAULT"};
  const html=renderCooling(result);
  assert.match(html,/方法明细未随记录保存/);
  assert.match(html,/冷侧换热方法未记录/);
  assert.match(html,/1.5–4.5 × 未完整记录/);
  assert.match(html,/壁温约束尚未评估/);
  assert.doesNotMatch(html,/NEW_DEFAULT|本次冷侧方法|Dittus|Gnielinski|未超过方案壁温限值/);
});

test("unsupported method metadata is not treated as a known historical contract",()=>{
  const result=geometryRecord(),profile=result.calculation.components.cooling_channel.profile;
  profile.model_methods.version=99;
  const html=renderCooling(result);
  assert.match(html,/方法明细未随记录保存/);
  assert.doesNotMatch(html,/本次热侧方法|保存的压降定义/);
});

test("model overview and method details escape saved text",()=>{
  const result=geometryRecord(),profile=result.calculation.components.cooling_channel.profile;
  profile.model_methods.hot_side.label="<script>hot</script>";
  profile.model_methods.hot_side.detail="<img src=x onerror=alert(1)>";
  profile.material_assignment.default_dataset_id="<bad>";
  const html=renderCooling(result);
  assert.match(html,/&lt;script&gt;hot&lt;\/script&gt;/);
  assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html,/<script>|<img |<bad>/);
});

test("saved budget records keep their recorded totals and never receive a quasi-one-dimensional model summary",()=>{
  const result={calculation:{inputs:{cooling_mode:"physics"},components:{cooling_channel:{outputs:{mode:0,heat_load_W:20e6,delta_P_MPa:2}}}}};
  const before=structuredClone(result),html=renderCooling(result);
  assert.match(html,/历史给定预算记录/);
  assert.match(html,/20 <small>MW/);
  assert.doesNotMatch(html,/本次模型与计算范围|冷却剂流向|统一准一维 · 保存记录|<svg/);
  assert.deepEqual(result,before);
});
