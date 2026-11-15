import assert from "node:assert/strict";
import test from "node:test";
import {renderGasEosComparison} from "../webapp/static/gas-eos-comparison.js";
import {buildValidationRequest,createValidationForm,renderValidationResult} from "../webapp/static/validation.js";
import {methaneThermoSummary} from "../webapp/static/parameter-editor.js";

const run=()=>({status:"computed",convergence:{status:"pass"},closure:{feed_network:{status:"pass"},pressure_contract:{pressure_link_status:"pass"},energy_balance:{status:"pass"}}});
function fixture(){return {schema_version:1,status:"complete",kind:"full_ffsc_eos_comparison",
  source_model:{eos:"ideal-gas",species_policy:"full"},
  model_scope:{species_policy:"pr_supported_v1",species_count:3,species_names:["O2","H2O","CO2"]},
  runs:{"ideal-gas":run(),"Peng-Robinson":run()},
  metrics:[{id:"ox_turbine_power_W",label:"富氧涡轮功率",unit:"W",ideal:100,pr:125,difference:25,relative_difference:.25}],
  stations:[{id:"ox_preburner_outlet",label:"富氧预燃室出口",basis:{ideal:{kind:"reservoir_approximation"},pr:{kind:"reservoir_approximation"}},
    properties:[{id:"temperature_K",label:"温度",unit:"K",ideal:750,pr:760,difference:10,relative_difference:10/750}],
    composition:[{species:"O2",ideal:.9,pr:.85,difference:-.05}]}],limitations:["测试候选参数来源"]};}

test("EOS request is source bound and carries no draft inputs or frozen-point overrides",()=>{
  const context={project:{id:"A",revision:4},draft:{gas_eos:"Peng-Robinson",pc:1},record:{id:"source-A",project_id:"A",revision:2,status:"succeeded",inputs:{pc:33e6,gas_eos:"ideal-gas",combustion_loss_model:"adiabatic"},result:{calculation:{inputs:{cycle_type:"ffsc",propellant_pair:"lox_methane"}}}}};
  const before=structuredClone(context);
  assert.deepEqual(buildValidationRequest(context,createValidationForm(context),"gas_eos_comparison"),{source_job_id:"source-A",gas_eos_comparison:{enabled:true,candidate_eos:"Peng-Robinson"}});
  assert.deepEqual(context,before);
  context.project.id="B";
  assert.throws(()=>buildValidationRequest(context,{},"gas_eos_comparison"),/本项目已完成/);
});

test("complete EOS report distinguishes full rematching and restricted species from original source",()=>{
  const value=fixture(),before=structuredClone(value),html=renderValidationResult({gas_eos_comparison:value},"gas_eos_comparison");
  for(const text of ["两套完整整机匹配","原设计可能采用完整物种集合","预燃室化学平衡","冻结组成涡轮膨胀","富氧涡轮功率","全部保存组分","pr_supported_v1","测试候选参数来源"])assert.ok(html.includes(text),text);
  assert.match(html,/相对差 \/ %/);assert.match(html,/<td>25<\/td>/);
  assert.match(html,/储腔近似/);assert.match(html,/站点压力链/);
  assert.deepEqual(value,before);
});

test("failed side preserves numeric attempts but suppresses all reported differences",()=>{
  const value=fixture();value.status="partial";value.runs["Peng-Robinson"]={status:"not_converged",reason:"无法闭合富燃轴功",convergence:{status:"fail"}};
  value.metrics[0].difference=987654;value.metrics[0].relative_difference=123.456;
  value.stations[0].properties[0].difference=345678;value.stations[0].composition[0].difference=765432;
  const html=renderGasEosComparison(value);
  assert.match(html,/无法闭合富燃轴功/);assert.match(html,/不展示两端差值/);assert.match(html,/<td>125<\/td>/);
  assert.doesNotMatch(html,/987,654|345,678|765,432|12,345/);
});

test("different species identities suppress differences even when both cycles closed",()=>{
  const value=fixture();value.status="unavailable";value.reason="两侧保存的物种集合身份不一致。";
  value.metrics[0].difference=987654;
  const html=renderGasEosComparison(value);
  assert.match(html,/两端分别通过收敛与闭合验收/);
  assert.match(html,/物种集合身份不一致/);
  assert.doesNotMatch(html,/987,654|差值 = PR 前段/);
});

test("EOS zero baselines and incomplete historical records are not invented",()=>{
  const value=fixture();value.metrics[0]={id:"margin",label:"余量",unit:"Pa",ideal:0,pr:20,difference:20,relative_difference:null};
  assert.match(renderGasEosComparison(value),/<td>20<\/td><td>—<\/td>/);
  assert.match(renderGasEosComparison(null),/尚未运行/);
  assert.match(renderGasEosComparison({status:"unavailable",reason:"未保存来源物性"}),/未保存来源物性/);
});

test("EOS pending report distinguishes an active calculation without replacing saved results",()=>{
  assert.match(renderGasEosComparison(null,true),/正在分别求解并核对两套模型，完成后显示对比/);
  assert.doesNotMatch(renderGasEosComparison(null,true),/尚未运行/);
  assert.match(renderGasEosComparison(null,false),/尚未运行/);
  const value=fixture(),before=structuredClone(value);
  assert.equal(renderGasEosComparison(value,true),renderGasEosComparison(value,false));
  assert.deepEqual(value,before);
});

test("EOS report escapes saved identities reasons labels and diagnostic payloads",()=>{
  const value=fixture(),attack='<img src=x onerror="alert(1)">';
  value.runs["Peng-Robinson"].reason=attack;value.metrics[0].label=attack;
  value.stations[0].composition[0].species=attack;value.model_scope.species_names=[attack];value.limitations=[attack];
  const html=renderGasEosComparison(value);
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|<script| style=/);
});

test("editor distinguishes EOS and effective species from the source thermochemistry dataset",()=>{
  const catalog=[{id:"gri30_highT",gas_eos:"ideal-gas",species_count:53,common_temperature_range_K:[300,5000]}];
  const value={methane_thermo_dataset:"gri30_highT",gas_eos:"Peng-Robinson",gas_species_policy:"auto"};
  const before=structuredClone(value),summary=methaneThermoSummary(value,catalog);
  assert.match(summary.summary,/PR 预燃室\/涡轮 · 受限候选/);assert.match(summary.summary,/PR 参数支持的受限集合/);
  assert.match(summary.summary,/主室 \/ 喷管/);assert.match(summary.details,/主燃烧室及喷管保留理想气体模型/);assert.match(summary.details,/实际焓与组成/);
  assert.match(summary.details,/热化学源文件 53 种/);assert.match(summary.details,/缺少参数的自由基/);assert.deepEqual(value,before);
  assert.match(methaneThermoSummary({methane_thermo_dataset:"gri30_highT"},catalog).summary,/理想气体混合物/);
});

test("segmented EOS report uses each saved station model rather than its run label",()=>{
  const value=fixture();
  value.stations.push({id:"chamber",label:"主燃烧室",properties:[],composition:[]});
  value.runs["ideal-gas"].stations={ox_preburner_outlet:{model:{eos:"ideal-gas"}},chamber:{model:{eos:"ideal-gas"}}};
  value.runs["Peng-Robinson"].stations={ox_preburner_outlet:{model:{eos:"Peng-Robinson"}},chamber:{model:{eos:"ideal-gas"}}};
  const html=renderGasEosComparison(value);
  assert.match(html,/PR 前段方案仅在预燃室和涡轮采用 Peng–Robinson，主室及喷管仍采用理想气体/);
  assert.match(html,/实际状态方程：全理想方案 理想气体；PR 前段方案 Peng–Robinson/);
  assert.match(html,/实际状态方程：全理想方案 理想气体；PR 前段方案 理想气体/);
  delete value.runs["Peng-Robinson"].stations.chamber.model;
  assert.match(renderGasEosComparison(value),/实际状态方程：全理想方案 理想气体；PR 前段方案 未记录/);
});

test("BM report uses the full-cycle run identity and preserves historical segmented reports",()=>{
  const value=fixture(),bm="Peng-Robinson-Boston-Mathias";
  value.schema_version=2;
  value.model_scope={...value.model_scope,candidate_eos:bm,main_chamber_nozzle:bm,eos_model_sha256:"bm-model-v1"};
  value.model_scope.hot_side_thermo_method="same_eos";
  value.source_model={eos:bm,species_policy:"pr_supported_v1",scope:"full_pr_bm"};
  value.runs[bm]=value.runs["Peng-Robinson"];delete value.runs["Peng-Robinson"];
  for(const current of Object.values(value.runs))current.closure.hot_side_method={status:"pass",requested_method:"same_eos"};
  value.stations.push({id:"main_chamber",label:"主燃烧室",properties:[],composition:[]});
  value.runs[bm].stations={main_chamber:{model:{eos:bm}}};
  const before=structuredClone(value),html=renderGasEosComparison(value);
  assert.match(html,/PR–BM 全燃气方案/);assert.match(html,/Boston–Mathias 高温 alpha 修正/);
  assert.match(html,/PR–BM 全燃气方案 Peng–Robinson–Boston–Mathias/);
  assert.match(html,/bm-model-v1/);assert.match(html,/<td>25<\/td>/);
  assert.match(html,/共同热侧算法已核对/);assert.match(html,/单元中心采样/);assert.match(html,/实际总温／静温比/);
  assert.doesNotMatch(html,/PR 前段方案|主室及喷管仍采用理想气体|不覆盖主室\/喷管/);
  assert.deepEqual(value,before);
  const historical=renderGasEosComparison(fixture());
  assert.match(historical,/PR 前段方案/);assert.doesNotMatch(historical,/PR–BM 全燃气方案/);
  value.runs[bm].status="not_converged";value.runs[bm].reason="BM 主室未闭合";
  value.metrics[0].difference=987654;
  const failed=renderGasEosComparison(value);
  assert.match(failed,/BM 主室未闭合/);assert.doesNotMatch(failed,/987,654/);
});

test("historical or failed BM thermal protocol cannot claim a pure EOS difference",()=>{
  const value=fixture(),bm="Peng-Robinson-Boston-Mathias";
  value.model_scope.candidate_eos=bm;
  value.runs[bm]=value.runs["Peng-Robinson"];delete value.runs["Peng-Robinson"];
  value.metrics[0].difference=987654;
  const old=structuredClone(value),historical=renderGasEosComparison(value);
  assert.match(historical,/不能将差异单独归因于 EOS/);assert.doesNotMatch(historical,/987,654/);
  assert.deepEqual(value,old);
  value.model_scope.hot_side_thermo_method="same_eos";
  value.runs["ideal-gas"].closure.hot_side_method={status:"pass",requested_method:"same_eos"};
  value.runs[bm].closure.hot_side_method={status:"fail",requested_method:"same_eos"};
  assert.doesNotMatch(renderGasEosComparison(value),/987,654|共同热侧算法已核对/);
  for(const current of Object.values(value.runs))current.closure.hot_side_method={status:"not_applicable",requested_method:"same_eos"};
  const unheated=renderGasEosComparison(value);
  assert.match(unheated,/未启用分布加热/);assert.match(unheated,/987,654/);
});

test("BM editor summary declares every gas segment while preserving source dataset identity",()=>{
  const catalog=[{id:"gri30_highT",gas_eos:"ideal-gas",species_count:53}],
    inputs={methane_thermo_dataset:"gri30_highT",gas_eos:"Peng-Robinson-Boston-Mathias",gas_species_policy:"auto"};
  const before=structuredClone(inputs),view=methaneThermoSummary(inputs,catalog);
  assert.match(view.summary,/PR–BM 全燃气流程/);assert.match(view.summary,/PR 参数支持的受限集合/);
  assert.match(view.details,/主燃烧室、冷却燃气侧及喷管统一采用 Peng–Robinson–Boston–Mathias/);
  assert.match(view.details,/热化学源文件 53 种/);assert.doesNotMatch(view.details,/主燃烧室及喷管保留理想气体/);
  assert.deepEqual(inputs,before);
});
