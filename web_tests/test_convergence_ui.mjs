import assert from "node:assert/strict";
import {test} from "node:test";
import {numericalDisplayState,renderConvergence,residualPlot,renderGasInitialization,renderSolverDiagnostics,initializationDiagnostic,renderInitializationDiagnostics} from "../webapp/static/convergence.js";

function diagnosticRun(){
  return {backend:"broyden",termination_reason:"max_iterations",nfev:9,njev:2,rejected_steps:2,
    equation_labels:["支路压力闭合"],points:[
      {iteration:0,residual_norm:.001,acceptance_norm:20,raw_residuals:{pressure:20},nfev:1},
      {iteration:1,residual_norm:.0001,acceptance_norm:2,raw_residuals:{pressure:-2},nfev:9,jacobian_rebuilds:2,approximate_updates:1,elapsed_seconds:.04},
    ],diagnostics:{schema_version:1,elapsed_seconds:.04,jacobian_rebuilds:2,jacobian_attempts:2,approximate_updates:1,model_evaluations:9,
      equations:[{name:"pressure",component:"支路阀门",unit:"Pa",raw_residual:-2,scaled_residual:-.0001,scale:20000,absolute_tolerance:1,passed:false}],
      variables:[{name:"支路流量",unit:"kg/s",value:2,scale:10,lower:0,upper:2,diff_step:.0001,max_step:.25,at_bound:true}],
      jacobian:{rank:0,rows:1,columns:1,condition_number:null,singular_values:[0],weak_variables:["支路流量"],dependent_equations:["压力闭合"],source:"finite_difference",at_x:[2],local_only:true},
      failures:[{stage:"finite_difference",variable:"支路流量",error:"超出登记范围",x:[2.0001]}],
      step_reductions:[{iteration:1,reason:"variable_step_limit",variable:"支路流量",factor:.5},{iteration:1,reason:"model_failure",factor:.5}],
      events:[{iteration:1,kind:"jacobian_rebuild",reason:"poor_progress"}],evaluation_counts:{initial:1,finite_difference:3,trial:5},
    }};
}

test("new diagnostics identify physical residual failure independently of a small scaled norm",()=>{
  const run=diagnosticRun(),before=structuredClone(run),html=renderSolverDiagnostics(run);
  for(const text of ["未达标方程：支路压力闭合","支路阀门","最终原始残差（有符号）","容许绝对残差","数值秩 0 / 1","不证明全局方程独立","支路流量","超出登记范围","变量步长上限","近似更新","完整雅可比重建","0.04"])
    assert(html.includes(text),text);
  assert.match(html,/<td>Pa<\/td><td>-2<\/td><td>1<\/td>/);
  assert.match(html,/奇异或数值秩不足/);
  assert.match(html,/试探失败可能已被后续迭代恢复/);
  assert.match(html,/压力闭合 · 接受迭代步 1 · 2 1/);
  assert.deepEqual(run,before);
});

test("acceptance plots use physical thresholds and keep missing accepted samples as gaps",()=>{
  const data=record(),run=diagnosticRun();data.calculation.convergence.inner_runs=[run];
  run.points.splice(1,0,{iteration:.5,residual_norm:0,raw_residuals:{}});
  const html=renderConvergence(data),chart=html.split('<figcaption>Broyden 雅可比近似更新 最大残差 / 验收阈值</figcaption>')[1].split('</figure>')[0];
  assert.match(chart,/容差 1/);assert.match(chart,/缺失 \/ 无效值 1 个/);assert.match(chart,/零值 0 个/);
  assert.match(html,/完整雅可比重建<\/span><strong>2/);assert.match(html,/近似更新<\/span><strong>1/);
  assert.match(html,/累计耗时 \/ s/);assert.match(html,/步长缩减原因/);
});

test("diagnostic missing values are unknown, true zeros preserved, persisted labels escaped",()=>{
  assert.match(renderSolverDiagnostics({}),/未保存逐项求解诊断/);
  const run=diagnosticRun(),attack='<img src=x onerror="bad()">';
  for(const key of ["elapsed_seconds","jacobian_rebuilds","approximate_updates","model_evaluations"])run.diagnostics[key]=0;
  run.diagnostics.equations[0].raw_residual=null;run.diagnostics.equations[0].passed=true;
  run.diagnostics.failures[0].error=attack;run.diagnostics.variables[0].name=attack;run.equation_labels=[attack];
  const html=renderSolverDiagnostics(run);
  assert.match(html,/逐项残差证据不完整/);assert.match(html,/证据缺失/);
  assert.match(html,/模型调用<\/span><strong>0/);assert.match(html,/内层耗时 \/ s<\/span><strong>0/);
  assert.doesNotMatch(html,/<img|<script|onerror="bad|NaN|undefined|Infinity/);assert.match(html,/&lt;img/);
  run.diagnostics.equations[0].raw_residual=0;run.diagnostics.equations[0].absolute_tolerance=0;
  assert.match(renderSolverDiagnostics(run),/均满足记录中的绝对容差/);
});

test("unobservable trust-region reductions and truncated logs never masquerade as complete zero counts",()=>{
  const run=diagnosticRun();
  run.diagnostics.step_reductions=[];
  run.diagnostics.step_reduction_observability="backend_internal_unavailable";
  run.diagnostics.truncated_records={events:15,failures:12,step_reductions:0};
  run.diagnostics.failures[0].variable="出口压力";run.diagnostics.failures[0].probe_variable="支路流量";
  run.diagnostics.failures[0].component="管路";run.diagnostics.failures[0].equation="压力闭合";
  run.diagnostics.variables[0].at_bound=null;
  const html=renderSolverDiagnostics(run);
  assert.match(html,/内部缩减未公开/);assert.match(html,/空列表不能解读为没有缩减/);
  assert.match(html,/省略矩阵事件 15 条、求值失败 12 条/);assert.match(html,/统计计数仍包含全部调用/);
  assert.match(html,/差分探针变量/);assert.match(html,/<td>出口压力<\/td><td>支路流量<\/td><td>管路<\/td><td>压力闭合/);
  assert.match(html,/边界内/);assert.doesNotMatch(html,/本次记录没有步长缩减事件/);
});

test("solver entry failure stages have full Chinese labels and unknown model counts stay unknown",()=>{
  const run=diagnosticRun(),stages={input_validation:"输入检查",model_evaluation:"模型求值",solver_processing:"求解处理",outside_instrumented_solver:"未记录调用的外部求解入口",recorded_model_calls:"已记录模型调用"};
  run.nfev=null;run.diagnostics.model_evaluations=null;run.diagnostics.evaluation_count_known=false;
  run.diagnostics.failures=Object.keys(stages).map(stage=>({stage,error:"失败记录",x:[]}));
  run.diagnostics.evaluation_counts={recorded_model_calls:0};
  const data=record();data.calculation.convergence.inner_runs=[run];
  const html=renderConvergence(data);
  for(const [key,label]of Object.entries(stages)){assert(html.includes(label),label);assert(!html.includes(key),key);}
  assert.match(html,/函数求值（含差分与试探）<\/span><strong>—/);
  assert.match(html,/模型调用<\/span><strong>—/);
});

function initializationFailure(){
  return {initialization_diagnostics:{schema_version:1,status:"failed",stage:"initialization",formal_iteration_started:false,
    attempted_inputs:{pc:33000000,ox_preburner_temp_target:750},variable_names:["总质量流量","富氧侧涡轮膨胀压力比","富燃侧涡轮膨胀压力比"],variable_units:["kg/s","1","1"],x:[700,1.8,2],
    steps:[{id:"forward_seed",label:"初始状态正向求值",component:"整机循环",status:"completed",attempted_inputs:{x:[700,1.8,2]},calls_attempted:1,calls_completed:1},
      {id:"ox_temperature_inversion",label:"富氧预燃室温度反求",component:"富氧预燃室",status:"failed",attempted_inputs:{T_target_K:750,pressure_Pa:48000000,T_ox_in_K:77},input_units:{T_target_K:"K",pressure_Pa:"Pa",T_ox_in_K:"K"},error_type:"ValueError",message:"温度超出物性范围",calls_attempted:1,calls_completed:0}],
    counts:{forward_attempted:1,forward_completed:1,inversion_attempted:1,inversion_completed:0,property_attempted:2,property_completed:1},
    failure:{stage:"ox_temperature_inversion",component:"富氧预燃室",error_type:"ValueError",message:"温度超出物性范围"},elapsed_seconds:0,
    counting_scope:"物性调用计数仅覆盖温度反求；前向内部物性调用次数未知。"}};
}

test("initialization failure renders actual attempts without fabricating residual curves or normal design results",()=>{
  const data=initializationFailure(),before=structuredClone(data),html=renderConvergence(data);
  assert.equal(initializationDiagnostic(data),data.initialization_diagnostics);
  const state=numericalDisplayState(data);
  assert.equal(state.label,"初始化失败");assert.equal(state.phase,"initialization");assert.equal(state.residual,null);
  for(const text of ["初始化失败诊断","正式稳态迭代尚未开始","该失败不能认定设计在物理上无解","富氧预燃室温度反求","ValueError","温度超出物性范围","成功完成次数","48000000","压力（pressure_Pa）","前向内部物性调用次数未知"])
    assert(html.includes(text),text);
  assert.match(html,/<td>总质量流量<\/td><td>700<\/td><td>kg\/s<\/td>/);
  assert.match(html,/初始化耗时：0 s/);
  assert.doesNotMatch(html,/<svg|cv-layers|cv-chart|完整数值收敛|历史记录未保存完整收敛轨迹/);
  assert.deepEqual(data,before);
});

test("unknown initialization counts and missing formal-start evidence are not replaced by zeros or assumptions",()=>{
  const data=initializationFailure(),diagnostic=data.initialization_diagnostics;
  diagnostic.counts={forward_attempted:0,forward_completed:0,property_attempted:null,property_completed:-1};
  delete diagnostic.formal_iteration_started;diagnostic.x=[];diagnostic.attempted_inputs=null;diagnostic.steps=[];
  const html=renderInitializationDiagnostics(data);
  assert.match(html,/<td>初始状态正向求值<\/td><td>0<\/td><td>0<\/td>/);
  assert.match(html,/<td>温度反求中的物性调用<\/td><td>未记录<\/td><td>未记录<\/td>/);
  assert.match(html,/正式稳态迭代是否开始：未记录/);assert.doesNotMatch(html,/正式稳态迭代尚未开始/);
  assert.match(html,/初始向量未保存/);assert.match(html,/不使用当前草稿或默认值补齐/);
  assert.match(html,/未保存分步骤记录/);
  for(const invalid of [null,{}, {initialization_diagnostics:{schema_version:2,status:"failed"}}, {initialization_diagnostics:{schema_version:1,status:"completed"}}]) {
    assert.equal(initializationDiagnostic(invalid),null);assert.equal(renderInitializationDiagnostics(invalid),"");
  }
});

test("initialization stage, errors and saved inputs are escaped and missing values remain missing",()=>{
  const data=initializationFailure(),attack='<img src=x onerror="bad()">',diagnostic=data.initialization_diagnostics;
  diagnostic.failure={stage:attack,component:attack,error_type:attack,message:attack};
  diagnostic.steps[1].label=attack;diagnostic.steps[1].attempted_inputs={side:attack,T_target_K:null};
  diagnostic.x[0]=null;diagnostic.variable_names[0]=attack;diagnostic.attempted_inputs={note:attack};
  const html=renderConvergence(data);
  assert.match(html,/&lt;img/);assert.match(html,/出口温度目标（T_target_K）<\/td><td>未记录/);
  assert.doesNotMatch(html,/<img|<script|onerror="bad|NaN|undefined|Infinity/);
});

test("initialization counted scopes and passed steps follow the persisted core schema",()=>{
  const data=initializationFailure(),diagnostic=data.initialization_diagnostics;
  diagnostic.steps[0].status="passed";
  diagnostic.counting_scope={forward:"完整前向模型调用",inversion:"预燃室温度反求调用",property:"仅温度反求中的热化学调用；前向部件内部调用未计数"};
  diagnostic.counts.component_internal_property_calls=null;
  const html=renderConvergence(data);
  assert.match(html,/初始状态正向求值 · 已完成/);
  for(const scope of Object.values(diagnostic.counting_scope))assert(html.includes(scope),scope);
  assert.match(html,/前向部件内部物性调用次数未统计，不按零处理/);
  assert.doesNotMatch(html,/\[object Object\]|初始状态正向求值 · 状态未记录/);
});

test("initialization preserves the failed thermochemistry candidate and code location separately from its seed",()=>{
  const data=initializationFailure(),diagnostic=data.initialization_diagnostics;
  diagnostic.steps.push({id:"prepare_temperature_inversion",status:"passed",calls_attempted:1,calls_completed:1});
  diagnostic.failure.location={path:"src/thermo.py",function:"combustion_state",line:123};
  diagnostic.last_property_call={component:"富氧预燃室",status:"failed",attempted_inputs:{mdot_fuel:1,mdot_ox:900,T_ox:77,oxygen_fuel_mass_ratio:900},
    input_units:{mdot_fuel:"kg/s",mdot_ox:"kg/s",T_ox:"K",oxygen_fuel_mass_ratio:"1"},input_labels:{mdot_ox:"本次氧化剂流量"},
    error_type:"ValueError",message:"实际候选超域",location:diagnostic.failure.location};
  diagnostic.last_property_failure=structuredClone(diagnostic.last_property_call);
  const before=structuredClone(data),html=renderConvergence(data);
  assert.match(html,/读取温度反求入口状态 · 已完成/);
  assert.match(html,/最近一次实际热化学求值 · 失败/);
  assert.match(html,/本次氧化剂流量（mdot_ox）<\/td><td>900<\/td><td>kg\/s/);
  assert.match(html,/总质量流量<\/td><td>700/);
  assert.match(html,/src\/thermo\.py/);assert.match(html,/combustion_state/);assert.match(html,/<td>行号<\/td><td>123/);
  assert.match(html,/候选状态，不是已收敛工作点/);
  assert.doesNotMatch(html,/最近一次失败的热化学求值/);
  assert.deepEqual(data,before);
  diagnostic.last_property_call.status="passed";diagnostic.last_property_call.attempted_inputs.mdot_ox=100;
  const recovered=renderConvergence(data);
  assert.match(recovered,/最近一次实际热化学求值 · 已完成/);assert.match(recovered,/最近一次失败的热化学求值 · 失败/);
  diagnostic.last_property_failure.location.path='<script>bad()</script>';
  const escaped=renderConvergence(data);assert.match(escaped,/&lt;script&gt;bad/);assert.doesNotMatch(escaped,/<script>/);
});

test("physical acceptance thresholds stay separate from the numerical scaled-norm threshold",()=>{
  const data=record(),run=diagnosticRun();data.calculation.convergence.inner_runs=[run];
  run.tolerance=1;run.numerical_tolerance=1e-8;run.physical_tolerances=[1];
  run.acceptance_policy={schema_version:1,id:"ffsc_task_absolute_residuals_v1",task_thrust_N:1000,thrust_reference_N:1000,relative_thrust_tolerance:1e-6,
    equations:[{name:"pressure",label:"支路压力闭合",unit:"Pa",absolute_tolerance:1,source:"固定验收容差"}]};
  let html=renderConvergence(data);
  assert.match(html,/改变功率缩放不会放宽这里的功率允许误差/);
  assert.match(html,/数值缩放范数停止阈值<\/td><td>1\.000e-8/);
  assert.match(html,/最大残差 \/ 验收阈值/);assert.match(html,/缩放范数 1\.000e-4 · 验收比 2/);
  for(const point of run.points)delete point.acceptance_norm;
  html=renderConvergence(data);
  const chart=html.split('<figcaption>Broyden 雅可比近似更新 归一化残差范数</figcaption>')[1].split('</figure>')[0];
  assert.match(chart,/容差 1\.000e-8/);assert.doesNotMatch(chart,/容差 1 —/);
  run.numerical_tolerance=null;
  const missing=renderConvergence(data).split('<figcaption>Broyden 雅可比近似更新 归一化残差范数</figcaption>')[1].split('</figure>')[0];
  assert.doesNotMatch(missing,/class="cv-threshold"/);
});

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
test("incompatible warm vector is explained without treating cold fallback as a physical failure",()=>{
  const data=record();
  data.calculation.convergence.warm_start={requested:true,accepted:false,fallback:true,rejection_reason:"incompatible_or_unversioned_vector"};
  const before=structuredClone(data),html=renderConvergence(data);
  assert.match(html,/未复用旧热启动向量/);
  assert.match(html,/不会把涡轮压比当成交叉流量/);
  assert.equal(numericalDisplayState(data).status,"pass");
  assert.deepEqual(data,before);
});
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
  for (const inputs of [{cooling_bartz_mode:true},{use_target_preburner_temp:1}]) {
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
  data.calculation.summary.fuel_preburner_temp_error_K=-4;
  assert.equal(numericalDisplayState(data).status,"fail");
  data.calculation.summary.fuel_preburner_temp_error_K=0;
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

test("Preburner temperature history is signed in the data table and absolute on the residual plot",()=>{
  const data=record();
  data.calculation.convergence.preburner_history=[{iteration:1,ox_error_K:-4,residual_K:4}];
  const html=renderConvergence(data);
  assert.match(html,/富氧温度偏差绝对值/);
  assert.match(html,/<td>-4<\/td>/);
  assert.match(html,/外环轮次 1 · 4 K/);
  assert.doesNotMatch(html,/<figcaption>[^<]*富氧/);
});
