import assert from "node:assert/strict";
import test from "node:test";
import {coolingDisplayState,coolingCurveSegments,renderCooling} from "../webapp/static/cooling.js";
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
