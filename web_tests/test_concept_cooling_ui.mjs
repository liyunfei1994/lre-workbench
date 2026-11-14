import test from 'node:test';
import assert from 'node:assert/strict';
import {coolingDisplayState,renderCooling} from '../webapp/static/cooling.js';
import {designStartingInputs} from '../webapp/static/engineering-lifecycle.js';
import {renderCoolingChannelInputs} from '../webapp/static/cooling-channel-inputs.js';
import {renderSolverTask} from '../webapp/static/solver-workspace.js';

function record(){return {calculation:{components:{cooling_channel:{outputs:{mode:2,heat_load_W:12e6,T_out_K:300,P_out_Pa:20e6},
  profile:{version:1,model_id:'regen_concept_heat_v1',flow_direction:'gas_flow',
    assumptions:{reference_wall_temperature_K:800,estimated_pressure_drop_Pa:2e6,basis:'<script>reference</script>'},
    summary:{hot_area_m2:2,peak_heat_flux_W_m2:8e6,peak_x_m:.5},
    cells:[{kind:'heat_estimate',s_m:.2,x_m:.2,dA_hot_m2:1,q_W_m2:4e6,q_conv_W_m2:3e6,q_rad_W_m2:1e6,Q_W:4e6},
      {kind:'heat_estimate',s_m:.5,x_m:.5,dA_hot_m2:1,q_W_m2:8e6,q_conv_W_m2:7e6,q_rad_W_m2:1e6,Q_W:8e6}],
    balance:{energy_residual_W:0},validity:{status:'review',cooling_capability:'not_evaluated'}}}}}};}

test('concept displays estimates and input wall temperature without claiming solved wall or channel',()=>{
 const r=record(),before=structuredClone(r),html=renderCooling(r);
 assert.equal(coolingDisplayState(r).concept,true);
 for(const text of ['估算吸热功率','参考热面壁温 · 输入假设','估算峰值热流密度','冷却能力待校核','沿燃气流向的热面弧长'])assert.ok(html.includes(text));
 assert.match(html,/&lt;script&gt;reference&lt;\/script&gt;/);
 assert.doesNotMatch(html,/<script>|内核检查通过|记录最大热面壁温|冷却剂温度<|冷却剂压力</);
 assert.deepEqual(r,before);
});
test('incomplete saved estimate stays incomplete and missing values are not filled from draft',()=>{
 const r=record();r.calculation.inputs={cooling_reference_wall_temp_K:999};
 delete r.calculation.components.cooling_channel.profile.assumptions.reference_wall_temperature_K;
 r.calculation.components.cooling_channel.profile.cells[1].q_W_m2=null;
 const html=renderCooling(r);
 assert.match(html,/沿程估算字段不完整/);
 assert.match(html,/参考热面壁温 · 输入假设<\/span><strong>— <small>K/);
 assert.doesNotMatch(html,/>999 <small>K|NaN|Infinity|内核检查通过/);
});
test('new projects start with concept heat, preserve component drafts and optional original model',()=>{
 const preset={cooling_bartz_mode:1,cooling_n_channels:300,cooling_heat_load:90e6};
 const original=structuredClone(preset);
 assert.equal(designStartingInputs({},preset).cooling_bartz_mode,2);
 assert.equal(designStartingInputs({},preset).cooling_n_channels,300);
 assert.equal(designStartingInputs({},preset,'preset_geometry').cooling_bartz_mode,1);
 assert.deepEqual(preset,original);
 assert.equal(renderCoolingChannelInputs({cooling_bartz_mode:2}), '');
 assert.match(renderCoolingChannelInputs({cooling_bartz_mode:2},{componentDraft:true}), /通道几何定义方式/);
});
test('solver task records conceptual assumptions separately from numerical configuration',()=>{
 const html=renderSolverTask({draft:{cooling_bartz_mode:2,cooling_reference_wall_temp_K:800,cooling_estimated_dp_pa:5e6}});
 assert.match(html,/概略热负荷估算/);assert.match(html,/热面参考壁温 · 估算假设/);assert.match(html,/冷却压降估计/);
});
