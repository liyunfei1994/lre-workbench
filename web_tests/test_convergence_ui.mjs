import assert from "node:assert/strict";
import {test} from "node:test";
import {numericalDisplayState,renderConvergence,residualPlot,renderGasInitialization} from "../webapp/static/convergence.js";

function record() {
  const layer=(residual,tolerance,unit="—")=>({enabled:true,status:"pass",residual,tolerance,unit,iterations:2,termination_reason:"converged"});
  return {assessment:{numerical:{status:"pass",label:"数值收敛",residual:1e-6}},calculation:{
    summary:{solver_residual_norm:1e-6},convergence:{schema_version:1,
      layers:{inner:layer(1e-6,1e-4),thermal:{...layer(2,5,"K"),checks:[{name:"temperature",residual:2,tolerance:5},{name:"gamma",residual:1e-5,tolerance:1e-4},{name:"molar_mass",residual:1e-5,tolerance:1e-4}]},preburner:layer(1,3,"K"),continuation:{...layer(0,0),reached_ratio:1,termination_reason:"target_reached"}},
      inner_runs:[
        {id:1,stage_ratio:.6,thermal_iteration:1,preburner_iteration:1,tolerance:1e-4,termination_reason:"converged",points:[{iteration:0,residual_norm:.2,residuals:{thrust:.2}},{iteration:1,residual_norm:1e-5,residuals:{thrust:1e-5}}]},
        {id:2,stage_ratio:1,thermal_iteration:2,preburner_iteration:1,tolerance:1e-4,termination_reason:"converged",points:[{iteration:0,residual_norm:.1},{iteration:1,residual_norm:0}]},
      ],
      thermal_history:[{iteration:1,preburner_iteration:1,residual_K:120,gamma_relative_residual:.001,molar_mass_relative_residual:.002},{iteration:2,preburner_iteration:1,residual_K:2,gamma_relative_residual:1e-5,molar_mass_relative_residual:2e-5}],
      preburner_history:[{iteration:1,ox_error_K:-20,fuel_error_K:30,residual_K:30},{iteration:2,ox_error_K:-1,fuel_error_K:0,residual_K:1}],
    },
  }};
}

const coupledCriteria=[
  ["pressure_residual_Pa",.5,"Pa"], ["hot_side_temperature_residual_K",.001,"K"],
  ["geometry_relative_residual",2e-8,"1"], ["heat_relative_residual",2e-8,"1"],
  ["hot_side_relative_residual",5e-7,"1"], ["cell_heat_relative_residual",5e-7,"1"],
];
function coupledRecord(){
  const data=record(),trace=data.calculation.convergence;
  data.calculation.inputs={cooling_bartz_mode:1};
  trace.layers.thermal={enabled:true,status:"pass",source:"regen_quasi1d_ph_v1",residual:1e-7,tolerance:.001,unit:"K",iterations:2,
    termination_reason:"coupled_feedback_tolerance_met",checks:coupledCriteria.map(([name,tolerance,unit])=>({name,residual:tolerance*.1,tolerance,unit})),
    initialization_difference:{temperature_K:100,gamma_relative:.01,molar_mass_relative:.02}};
  trace.thermal_history=[1,2].map(iteration=>({source:"regen_quasi1d_ph_v1",iteration,preburner_iteration:1,accepted:true,pressure_relaxation:.5,
    ...Object.fromEntries(coupledCriteria.map(([name,tolerance])=>[name,tolerance*(iteration===1 ? 10 : .1)]))}));
  return data;
}

test("unified coupling accepts six physical checks without old gamma or MW residuals",()=>{
  const data=coupledRecord(),before=structuredClone(data),html=renderConvergence(data);
  assert.equal(numericalDisplayState(data).status,"pass");
  for(const label of ["冷热侧与供给耦合","供给与冷却压力残差","冷热侧耦合温差","共同几何相对残差","总吸热量相对残差","热气状态向量相对残差","逐单元热量相对残差"])
    assert(html.includes(label),label);
  assert.match(html,/末次求解起点与结果差异（不参与收敛验收）/);
  assert.match(html,/<td>主室温度差<\/td><td>100<\/td>/);
  assert.doesNotMatch(html,/γ 相对残差|M 相对残差|主室温度反馈残差|热外环 0/);
  assert.deepEqual(data,before);
});

test("startup comparison reads submitted estimates and canonical recorded chamber, never warm starts or draft",()=>{
  const data=coupledRecord();
  data.submitted_inputs={chamber_temp_guess:3200,chamber_gamma:1.12,molar_mass:18,cooling_bartz_mode:1};
  data.calculation.inputs={chamber_temp_guess:3600,chamber_gamma:1.3,molar_mass:28,cooling_bartz_mode:1};
  data.calculation.states={main_chamber:{temperature:3797,metadata:{gamma:1.1897,molar_mass:22.601}}};
  data.calculation.summary.chamber_temperature_K=9999; // Not the canonical port.
  const before=structuredClone(data),html=renderGasInitialization(data);
  assert.match(html,/<td>3,200<\/td><td>3,797<\/td>/);
  assert.match(html,/<td>1.12<\/td><td>1.1897<\/td>/);
  assert.match(html,/<td>18<\/td><td>22.601<\/td>/);
  assert.match(html,/收敛计算状态/);assert.doesNotMatch(html,/9,999|3,600/);
  assert.deepEqual(data,before);
  data.calculation.convergence.layers.inner.status="fail";
  assert.match(renderGasInitialization(data),/当前迭代状态（未验收）/);
  delete data.calculation.states.main_chamber.metadata.gamma;
  assert.match(renderGasInitialization(data),/<td>1.12<\/td><td>—<\/td>/);
  data.submitted_inputs.cooling_bartz_mode=0;
  assert.match(renderGasInitialization(data),/未使用这三个冷却启动估计/);
  const empty=renderGasInitialization({});
  assert.match(empty,/记录状态（收敛待核）/);
  assert.doesNotMatch(empty,/3550|3,550|1.18|21/);
});

test("every new coupled check is mandatory and its source version controls acceptance",()=>{
  for(const [name] of coupledCriteria){
    const data=coupledRecord();
    data.calculation.convergence.layers.thermal.checks=data.calculation.convergence.layers.thermal.checks.filter(c=>c.name!==name);
    assert.equal(numericalDisplayState(data).status,"unknown",name);
  }
  for(const source of [undefined,"unknown", "regen_quasi1d_ph_v2"]){
    const data=coupledRecord();data.calculation.convergence.layers.thermal.source=source;
    assert.equal(numericalDisplayState(data).status,"unknown");
  }
  for(const [name,limit] of coupledCriteria){
    const data=coupledRecord(),check=data.calculation.convergence.layers.thermal.checks.find(c=>c.name===name);
    check.residual=limit*2;check.tolerance=1e9;
    assert.equal(numericalDisplayState(data).status,"fail",name);
    assert.match(renderConvergence(data),/冷热侧与供给六项耦合证据缺失或未达标/);
  }
});

test("missing coupled samples and initialization fields remain missing instead of zero",()=>{
  const data=coupledRecord(),trace=data.calculation.convergence;
  delete trace.thermal_history[0].hot_side_temperature_residual_K;
  trace.thermal_history[0].residual_K=0; // Old generic field cannot fill the missing new measurement.
  delete trace.layers.thermal.initialization_difference.gamma_relative;
  const html=renderConvergence(data);
  assert.match(html,/<td>比热比相对差<\/td><td>—<\/td>/);
  const chart=html.split('<figcaption>冷热侧耦合温差</figcaption>')[1].split('</figure>')[0];
  assert.match(chart,/缺失 \/ 无效值 1 个/);
  assert.match(chart,/零值 0 个/);
  for(const value of [null,NaN,-1]){
    const broken=coupledRecord();broken.calculation.convergence.layers.thermal.checks[0].residual=value;
    assert.equal(numericalDisplayState(broken).status,"unknown");
  }
});

test("a small inner residual cannot conceal a failed thermal layer or stale pass flag",()=>{
  const data=record();
  assert.equal(numericalDisplayState(data).status,"pass");
  data.calculation.summary.solver_warning=1;
  assert.equal(numericalDisplayState(data).status,"fail");
  data.calculation.summary.solver_warning=0;
  data.calculation.summary.solver_residual_norm=.1;
  assert.equal(numericalDisplayState(data).status,"fail");
  data.calculation.summary.solver_residual_norm=1e-6;
  data.calculation.convergence.layers.thermal.residual=14.083;
  assert.equal(numericalDisplayState(data).status,"fail");
  const html=renderConvergence(data);
  assert.match(html,/数值未收敛/);
  assert.match(html,/14\.083/);
  assert.match(html,/容差 5 K/);
  assert.match(html,/data-layer="thermal"[^]*?cv-fail/);
});

test("all enabled layers and valid scalar and subsidiary evidence are required",()=>{
  const data=record();
  delete data.calculation.convergence.layers.continuation;
  assert.equal(numericalDisplayState(data).status,"unknown");
  data.calculation.convergence.layers.continuation={enabled:false,status:"disabled"};
  assert.equal(numericalDisplayState(data).status,"unknown");
  data.calculation.convergence.layers.continuation={enabled:true,status:"pass",residual:0,tolerance:0,reached_ratio:1,termination_reason:"target_reached"};
  assert.equal(numericalDisplayState(data).status,"pass");
  const thermalChecks=data.calculation.convergence.layers.thermal.checks;
  data.calculation.convergence.layers.thermal.checks=[];
  assert.equal(numericalDisplayState(data).status,"unknown");
  data.calculation.convergence.layers.thermal.checks=thermalChecks;
  data.calculation.convergence.layers.inner.residual=NaN;
  assert.equal(numericalDisplayState(data).status,"unknown");
  data.calculation.convergence.layers.inner.residual=1e-6;
  data.calculation.convergence.layers.thermal.checks=[{key:"gamma_relative_residual",residual:.01,tolerance:.001}];
  assert.equal(numericalDisplayState(data).status,"fail");
});

test("requested feedback cannot be disabled and continuation must prove target completion",()=>{
  for (const inputs of [{cooling_bartz_mode:true},{use_target_preburner_temp:1},{cycle_type:"gg",use_target_gg_temp:1}]) {
    const data=record(); data.calculation.inputs=inputs;
    const key=inputs.cooling_bartz_mode || inputs.cooling_dp_auto ? "thermal" : "preburner";
    data.calculation.convergence.layers[key]={enabled:false,status:"disabled"};
    assert.equal(numericalDisplayState(data).status,"fail");
    assert.match(renderConvergence(data),/输入要求执行的迭代层/);
  }
  const data=record();
  data.calculation.convergence.layers.continuation.reached_ratio=.75;
  assert.equal(numericalDisplayState(data).status,"fail");
  delete data.calculation.convergence.layers.continuation.reached_ratio;
  assert.equal(numericalDisplayState(data).status,"unknown");
  data.calculation.convergence.layers.continuation.last_stage_ratio=1;
  assert.equal(numericalDisplayState(data).status,"pass");
  data.calculation.convergence.layers.continuation.termination_reason="inner_failed_at_target";
  assert.equal(numericalDisplayState(data).status,"fail");
});

test("deprecated cooling pressure switch does not require a second thermal layer",()=>{
  const data=record();
  data.calculation.inputs={cooling_bartz_mode:0,cooling_dp_auto:1};
  data.calculation.convergence.layers.thermal={enabled:false,status:"disabled"};
  assert.equal(numericalDisplayState(data).status,"pass");
});

test("legacy records are immutable and never receive invented traces or complete pass",()=>{
  const data=record();
  delete data.calculation.convergence;
  const before=structuredClone(data);
  assert.equal(numericalDisplayState(data).status,"unknown");
  assert.match(renderConvergence(data),/历史记录未保存完整收敛轨迹/);
  assert.doesNotMatch(renderConvergence(data),/<svg/);
  assert.deepEqual(data,before);
  data.calculation.summary.tc_outer_residual_K=14.083;
  assert.equal(numericalDisplayState(data).status,"fail");
  data.calculation.summary.tc_outer_residual_K=2;
  data.calculation.summary.gg_temp_outer_error_K=-4;
  assert.equal(numericalDisplayState(data).status,"fail");
  data.calculation.summary.gg_temp_outer_error_K=0;
  data.calculation.summary.solver_residual_norm=1e-4;
  assert.equal(numericalDisplayState(data).status,"fail");
});

test("runs stay separate and only the final independent Newton solve is expanded",()=>{
  const html=renderConvergence(record());
  assert.equal((html.match(/class="cv-run"/g)||[]).length,2);
  assert.equal((html.match(/class="cv-run" open/g)||[]).length,1);
  assert.match(html,/工况比例 60%/);
  assert.match(html,/工况比例 100%/);
  assert.equal((html.match(/<figcaption>Newton 归一化残差范数/g)||[]).length,2);
  assert.match(html,/查看逐步残差与方程分量/);
  assert.match(html,/γ 相对残差/);
  assert.match(html,/M 相对残差/);
});

test("method evidence distinguishes bounded TRF, scales, rejected attempts and missing work counts",()=>{
  const data=record(), trace=data.calculation.convergence;
  Object.assign(trace.inner_runs[0],{backend:"newton",nfev:12,njev:3,rejected_steps:1,residual_scales:[1000],variable_scales:[100],warm_start:false});
  Object.assign(trace.inner_runs[1],{backend:"trf",nfev:7,njev:2,rejected_steps:0,residual_scales:[2000],variable_scales:[200],warm_start:true});
  trace.continuation_history=[{attempt:1,stage_ratio:.5,step:.5,accepted:false,termination_reason:"line_search_failed"},{attempt:2,stage_ratio:.25,step:.25,accepted:true,termination_reason:"tolerance_met"}];
  const before=structuredClone(data), html=renderConvergence(data);
  assert.match(html,/TRF 有界信赖域 归一化残差范数/);
  assert.match(html,/函数求值（含差分与试探）<\/span><strong>19/);
  assert.match(html,/热启动内层调用<\/span><strong>1/);
  assert.match(html,/查看本次求解的固定缩放/);
  assert.match(html,/容许绝对残差/);
  assert.match(html,/查看自适应延续与重试（2 次）/);
  assert.match(html,/拒绝/);
  assert.match(html,/50%/);
  assert.deepEqual(data,before);
  delete trace.inner_runs[0].nfev;
  assert.match(renderConvergence(data),/函数求值（含差分与试探）<\/span><strong>—/);
});

test("rejected thermal proposals are visible in the table and gaps in the accepted curve",()=>{
  const data=record();
  Object.assign(data.calculation.convergence.thermal_history[0],{accepted:false,relaxation:.25});
  Object.assign(data.calculation.convergence.thermal_history[1],{accepted:true,relaxation:.5});
  const html=renderConvergence(data);
  assert.match(html,/松弛系数/);
  assert.match(html,/拒绝并回退/);
  assert.doesNotMatch(html,/主室温度反馈残差[^]*?<title>温度外环 1 · 热反馈迭代轮次 1 · 120 K/);
});

test("zero, null, negative norms and nonfinite samples are distinct and break curves",()=>{
  const html=residualPlot({title:"测试",tolerance:1e-4,series:[{label:"范数",points:[
    {x:0,y:1},{x:1,y:null},{x:2,y:.1},{x:3,y:-1},{x:4,y:.01},{x:5,y:0},{x:6,y:Infinity},{x:7,y:NaN},{x:8,y:1e-5},
  ]}]});
  assert.match(html,/零值 1 个/);
  assert.match(html,/无效值 4 个/);
  assert.match(html,/<rect class="cv-mark/);
  const path=html.match(/<path[^>]+ d="([^"]+)"/)[1];
  assert.equal((path.match(/M/g)||[]).length,4);
  assert.equal((path.match(/L/g)||[]).length,0);
  assert.doesNotMatch(html,/NaN|Infinity|undefined/);
  assert.match(html,/容差 1\.000e-4/);
});

test("constant zero and extreme finite data generate only finite coordinates",()=>{
  for (const values of [[0,0],[Number.MIN_VALUE,Number.MAX_VALUE]]) {
    const html=residualPlot({title:"数值范围",tolerance:0,series:[{label:"范数",points:values.map((y,x)=>({x,y}))}]});
    assert.match(html,/<svg/);
    assert.doesNotMatch(html,/NaN|Infinity|undefined/);
  }
  assert.match(residualPlot({title:"缺失",series:[{points:[{x:0,y:-1},{x:1,y:null}]}]}),/没有有效残差数据/);
});

test("all persisted labels and reasons are escaped in text, titles and table headings",()=>{
  const data=record(),attack='<img src=x onerror="evil()">';
  data.calculation.convergence.inner_runs[0].id=attack;
  data.calculation.convergence.inner_runs[0].termination_reason=attack;
  data.calculation.convergence.inner_runs[0].points[0].residuals={[attack]:.1};
  data.calculation.convergence.layers.inner.unit=attack;
  const html=renderConvergence(data);
  assert.doesNotMatch(html,/<img|<script|onerror="evil/);
  assert.match(html,/&lt;img/);
  assert.match(residualPlot({title:attack,unit:attack,series:[{label:attack,points:[{x:1,y:1}]}]}),/&quot;evil/);
});

test("GG temperature history is signed in the data table and absolute on the residual plot",()=>{
  const data=record();
  data.calculation.convergence.preburner_history=[{iteration:1,gg_error_K:-4,residual_K:4}];
  const html=renderConvergence(data);
  assert.match(html,/GG 温度偏差绝对值/);
  assert.match(html,/<td>-4<\/td>/);
  assert.match(html,/外环轮次 1 · 4 K/);
  assert.doesNotMatch(html,/<figcaption>[^<]*富氧/);
});
