import assert from "node:assert/strict";
import test from "node:test";
import {renderGasHandoff} from "../webapp/static/gas-handoff.js";
import {buildValidationRequest,createValidationForm,renderValidationResult} from "../webapp/static/validation.js";

const report={schema_version:1,status:"complete",stations:[{id:"ox_in",label:"富氧涡轮入口",p_Pa:60e6,T_K:750,mass_flow_kg_s:500,h_Jkg:-510000,model:{backend:"Cantera",eos:"ideal-gas",enthalpy_reference:"chemical_298"},state_basis:{pressure:"total",temperature:"total",enthalpy:"static"},composition_mass_fractions:{O2:.9,H2O:.1},element_mass_fractions:{O:.988,H:.012},element_fluxes_kg_s:{O:494,H:6},checks:[{id:"mass_fraction_sum",label:"组成归一",status:"pass",residual:0,tolerance:1e-8,unit:"1"}]}],element_checks:[{id:"oxygen",label:"氧元素收支",status:"pass",residual:0,tolerance:1e-8}],component_balances:[{id:"main",label:"主室物料与能量收支",checks:[{id:"energy",label:"主室焓闭合",status:"fail",residual:1e4,tolerance:1}]}]};
const context=()=>({project:{id:"p1",revision:2},record:{id:"saved-design",project_id:"p1",revision:1,status:"succeeded",inputs:{propellant_pair:"lox_methane",cycle_type:"ffsc",pc:30e6,of_ratio:3.6,expansion_ratio:40,fuel_inlet_temp:110,ox_inlet_temp:90},result:{gas_handoff:report}},draft:{fuel_inlet_temp:999,ox_inlet_temp:888}});

test("gas screening request only identifies the frozen source and never imports draft or baseline controls",()=>{
  const ctx=context(),before=JSON.stringify(ctx),form={...createValidationForm(ctx),T_fuel_K:"1000",T_ox_K:"2000"};
  assert.deepEqual(buildValidationRequest(ctx,form,"gas_handoff"),{source_job_id:"saved-design",gas_screening:{enabled:true}});
  assert.equal(JSON.stringify(ctx),before);
  ctx.record.status="running";
  assert.throws(()=>buildValidationRequest(ctx,form,"gas_handoff"),/已完成/);
});

test("gas handoff presents full composition, element flux, source basis and nested component failures",()=>{
  const before=JSON.stringify(report),html=renderGasHandoff(report);
  for(const text of ["富氧涡轮入口","完整保存组成 · 2 项","元素质量流率 · 2 项","压力：总态","焓：静态","ideal-gas","chemical_298","主室物料与能量收支","主室焓闭合","未通过","交接字段完整"])assert.ok(html.includes(text),text);
  assert.match(html,/-510/);
  assert.match(html,/不等同于真实发动机性能验证/);
  assert.equal(JSON.stringify(report),before);
});

test("missing historical handoff is explicit and does not infer zero residual or passed validation",()=>{
  const html=renderGasHandoff(null,null,null);
  assert.match(html,/历史记录未保存/);
  assert.match(html,/尚未保存本设计点/);
  assert.match(html,/纯推进剂理论基线不代表/);
  assert.doesNotMatch(html,/守恒通过|交接字段完整|>0</);
});

test("EOS values are reported from backend records and missing values remain missing",()=>{
  const screening={status:"partial",stations:[{id:"ox_in",label:"富氧涡轮入口",status:"partial",baseline:{density_kg_m3:300,cp_J_kg_K:1100},eos_comparisons:[{id:"pr",label:"PR 候选",status:"ok",properties:{density_kg_m3:280},differences:{density_kg_m3:{relative_percent:-6.6666667}}},{id:"rk",label:"RK 候选",status:"unavailable",reason:"缺少物种参数"}]}]};
  const html=renderGasHandoff(report,screening);
  for(const text of ["PR 候选","280","-6.666667","未记录","缺少物种参数","不以纯氧或纯甲烷结果代替"])if(text!=="不以纯氧或纯甲烷结果代替")assert.ok(html.includes(text),text);
  assert.match(html,/缺少结果不表示无固碳风险/);
  assert.doesNotMatch(html,/14%|5.75/);
});

test("carbon equilibrium percentage is computed for display only and never called deposition rate",()=>{
  const screening={stations:[{id:"fuel",label:"富燃燃气",carbon_screen:{status:"ok",graphite_mass_fraction:.037,temperature_K:842,checks:[{id:"enthalpy",status:"pass",residual:1e-7,tolerance:1e-4}]}}]};
  const before=JSON.stringify(screening),html=renderGasHandoff(report,screening);
  assert.match(html,/3.7 %/);assert.match(html,/842 K/);
  assert.match(html,/不是实际积碳率/);assert.match(html,/不能换算为沉积量/);
  assert.equal(JSON.stringify(screening),before);
});

test("real chamber reproduction and matched CEA comparison retain distinct scopes",()=>{
  const comparison={status:"ok",source_ports:[{id:"ox",label:"富氧涡轮排气",mass_flow_kg_s:500,h_Jkg:-510000,composition_mass_fractions:{O2:.9,H2O:.1}}],target:{p_Pa:30e6,h_Jkg:-1400000,mass_flow_kg_s:700,heat_loss_W:0},production_reproduction:{status:"ok",temperature_K:3797,saved_temperature_K:3797,temperature_difference_K:0,enthalpy_residual_Jkg:1e-6,element_max_residual:1e-12},matched_cho_comparison:{status:"partial",cantera:{temperature_K:3790,enthalpy_Jkg:-1400000},cea:{reason:"CEA 后端不可用"},temperature_difference_K:null}};
  const html=renderGasHandoff(report,null,comparison);
  assert.match(html,/生产模型独立复算/);assert.match(html,/共同 CHO 物种集合/);
  assert.match(html,/3,797/);assert.match(html,/3,790/);assert.match(html,/CEA 后端不可用/);
  assert.match(html,/没有常温纯推进剂替代入口/);
  assert.match(html,/0<\/td>/);
  assert.match(html,/未记录/);
});

test("non-finite data and malformed containers cannot become physical values or markup",()=>{
  const attack='<img src=x onerror="bad()">';
  const bad={status:"incomplete",stations:[{id:attack,label:attack,p_Pa:Infinity,T_K:NaN,mass_flow_kg_s:null,model:{eos:attack},state_basis:null,composition_mass_fractions:{[attack]:.4},checks:[{id:attack,status:"error",reason:attack}]}],notes:[attack]};
  const html=renderGasHandoff(bad,{stations:null,notes:[attack]},{reason:attack});
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|\sonclick=|\sstyle=/);
  assert.doesNotMatch(html,/>Infinity<|>NaN</);assert.match(html,/未定义/);
  assert.doesNotThrow(()=>renderGasHandoff({stations:{ox:{checks:{}}}}, {stations:[null]}));
});

test("validation renderer routes gas reports and labels pure-propellant results separately",()=>{
  const html=renderValidationResult({gas_handoff:report},"gas_handoff");
  assert.match(html,/富氧涡轮入口/);
  const baseline=renderValidationResult({thermochemistry:{points:[],counts:{},provenance:{}}},"thermochemistry");
  assert.match(baseline,/本结果属于纯推进剂理论性能基线/);
  assert.match(baseline,/不包含预燃室、涡轮和冷却路径/);
});

test("production screening schema uses matched reduced baseline and converts ratio differences exactly once",()=>{
  const screening={status:"screened_with_limitations",stations:[{id:"ox",label:"实际燃气",baseline:{density_kg_m3:999},eos_comparisons:[{backend:"Cantera Peng-Robinson",status:"computed",scope:"reduced_composition_screening",full_composition_status:"unavailable",reduction:{removed_mass_fraction:.0000001},paired_ideal_baseline:{density_kg_m3:300,cp_j_kg_k:1000},candidate:{density_kg_m3:270,cp_j_kg_k:1200},differences:{density_kg_m3:{difference:-30,relative_difference:-.1}},paired_ideal_expansion:{isentropic_drop_j_kg:80000},frozen_expansion:{status:"computed",isentropic_drop_j_kg:90000}}],carbon_screen:{status:"computed",result:{solid_carbon_mass_fraction_total_gas_plus_solid:.057,temperature_k:843,mass_residual_kg:0,enthalpy_residual_j:.001,conservation_passed:true}}}]};
  const html=renderGasHandoff(null,screening);
  assert.match(html,/Cantera Peng-Robinson/);assert.match(html,/约简组成筛查/);
  assert.match(html,/不是完整燃气混合物的精度结论/);assert.match(html,/>300<\/td>/);
  assert.doesNotMatch(html,/>999<\/td>/);assert.match(html,/>-10<\/td>/);
  assert.match(html,/冻结组成膨胀对照/);assert.match(html,/>80<\/td>/);assert.match(html,/>90<\/td>/);
  assert.match(html,/5.7 %/);assert.match(html,/843 K/);assert.match(html,/已筛查 · 存在模型限制/);
});

test("chamber extrapolation warnings are visible in each backend section with dedicated range details",()=>{
  const outside={thermochemical_range_status:"extrapolated",temperature_range_evidence:{temperature_K:3800,outside_species:{H2O:{min_K:200,max_K:3500,mass_fraction:.4}}}};
  const comparison={status:"ok",production_reproduction:{status:"ok",...outside},matched_cho_comparison:{status:"ok",cantera:{...outside},cea:{thermochemical_range_status:"within_declared_ranges",temperature_range_evidence:{temperature_K:3801,outside_species:{}}}}};
  const html=renderGasHandoff(report,null,comparison);
  assert.match(html,/gh-message gh-warning">生产模型主室复算：发生热化学数据温区外推/);
  assert.match(html,/gh-message gh-warning">共同物种 Cantera：发生热化学数据温区外推/);
  assert.match(html,/<summary>生产模型主室复算 · 外推物种与声明温区<\/summary>/);
  assert.match(html,/<summary>共同物种 NASA CEA · 所查物种在声明温区内<\/summary>/);
  assert.match(html,/逐物种温区证据/);assert.match(html,/3,800/);
  assert.doesNotMatch(html,/NASA CEA：发生热化学数据温区外推/);
});

test("out-of-range EOS status and per-component range failures are prominent warnings",()=>{
  const html=renderGasHandoff(report,{stations:[{id:"fuel",eos_comparisons:[{backend:"REFPROP",status:"computed_outside_declared_range",within_all_component_declared_ranges:false,reference_within_all_component_declared_ranges:false,candidate:{density_kg_m3:200}}]}]});
  assert.match(html,/gh-badge gh-warning">已计算 · 超出声明范围/);
  assert.match(html,/gh-message gh-warning">至少一个混合物组分超出其声明的温度或压力范围/);
  assert.match(html,/焓熵对齐参考态超出至少一个组分的声明范围/);
  assert.doesNotMatch(html,/gh-badge gh-neutral">computed_outside_declared_range/);
});

test("non-FFSC handoff reports not-applicable without inventing missing-FFSC failures",()=>{
  const html=renderGasHandoff({status:"not_applicable",stations:[],notes:["当前为 GG 循环。"]});
  assert.match(html,/gh-badge gh-neutral">不适用/);assert.match(html,/此项针对全流量分级燃烧/);
  assert.match(html,/当前为 GG 循环/);assert.doesNotMatch(html,/gh-warning|请重新运行设计点|尚未保存实际涡轮排气/);
});
