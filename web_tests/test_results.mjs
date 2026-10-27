/** Self-contained browser result contract tests. Run: node --test web_tests/test_results.mjs */
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";
import {configureResultSchema, renderAnalysis, componentAnalysisItems, flowAnalysisModel} from "../webapp/static/results.js";

// Same JSON shape as /api/info.ui_schema; deliberately tiny to prove the
// renderer consumes server declarations instead of a second hardcoded catalog.
const schema = {
  overview_groups: {
    ffsc: [{title:"FFSC 流量分配",fields:[{key:"fuel_split_to_ox_preburner",label:"燃料至富氧预燃室比例",unit:"—"}]}],
    gg: [{title:"GG 流量分配",fields:[{key:"gg_mdot_kg_s",label:"燃气发生器流量",unit:"kg/s"}]}],
  },
  component_labels: {ox_pump:"液氧泵",ox_preburner:"富氧预燃室",nozzle:"喷管",custom_part:"服务器新增部件"},
  key_labels: {
    power_W:"泵功率 [MW]",power_MW:"泵功率 [MW]",p_in_Pa:"入口压力 [MPa]",
    injector_dp_Pa:"预燃室喷注器压降 [MPa]",preburner_pressure_Pa:"预燃室压力 [MPa]",
    eta:"服务器声明的泵效率 [-]",
  },
  state_labels: {main_chamber:"主燃烧室",custom_state:"服务器新增状态点"},
};

function record(cycle = "ffsc") {
  const inputs = {
    cycle_type:cycle,ox_preburner_temp_limit:1000,fuel_preburner_temp_limit:1100,
    gg_temp_limit:950,gg_temp_target:900,use_target_gg_temp:1,
  };
  return {
    id:"job-test",status:"succeeded",revision:4,author:"测试用户",created:1792531200,
    result:{
      application_version:"7.1.0",submitted_inputs:{...inputs},
      calculation_scope:{id:"engine_system",version:1,label:"系统仿真 · 稳态设计点",limitations:["当前计算不包含启动、关机与重复使用寿命。"]},
      calculation:{
        convergence:{schema_version:1,layers:Object.fromEntries(["inner","thermal","preburner","continuation"].map(key=>[key,{enabled:true,status:"pass",residual:0,tolerance:.001,iterations:1,termination_reason:key === "continuation" ? "target_reached" : "converged",...(key === "continuation" ? {reached_ratio:1} : {}),...(key === "thermal" ? {checks:["temperature","gamma","molar_mass"].map(name=>({name,residual:0,tolerance:.001}))} : {})}]))},
        inputs,summary:{
          thrust_vac_N:1234567,thrust_N:1000000,thrust_error_percent:0,
          Isp_vac_s:350,Isp_s:300,mdot_total_kg_s:300,chamber_temperature_K:3400,
          ox_preburner_T_combustion_K:850,fuel_preburner_T_combustion_K:920,
          gg_T_combustion_K:900,gg_turbine_exhaust_T_K:500,gg_turbine_pi_t:20,
          gg_mdot_kg_s:12,fuel_split_to_ox_preburner:.06,solver_iterations:8,solver_residual_norm:0,
        },
        states:{
          main_chamber:{name:"main_chamber",pressure:20000000,temperature:3400,
            mass_flow:300,density:null,enthalpy:2000000,composition:{H2O:.6,CO2:.4},metadata:{mach:.1}},
          custom_state:{name:"custom_state",pressure:null,temperature:null,mass_flow:null,density:1,
            enthalpy:null,composition:{},metadata:{}},
        },
        components:{
          ox_pump:{name:"ox_pump",outputs:{power_W:2000000,p_in_Pa:10000000,eta:.8},notes:["泵效率说明"]},
          ox_preburner:{name:"ox_preburner",outputs:{injector_dp_Pa:2000000,preburner_pressure_Pa:20000000},notes:[]},
          nozzle:{name:"nozzle",outputs:{thrust_N:1000000,thrust_vac_N:1234567,p_exit_Pa:50000},notes:[]},
          custom_part:{name:"custom_part",outputs:{custom_metric:777},notes:["原始详细部件诊断"]},
        },
        topology_nodes:[{key:"source",label:"液氧泵",pos:[60,110]},{key:"sink",label:"主燃烧室",pos:[300,170]}],
        topology_edges:[{source:"source",target:"sink",label:"LOX 主路"}],
        warnings:["请核实材料数据"],
      },
      assessment:{
        scope:"system",
        numerical:{status:"pass",label:"数值收敛",residual:0,tolerance:.001,iterations:8},
        status:"fail",label:"系统约束未通过",checks:[{label:"轴功率平衡",detail:"允许绝对差 1%",
          status:"fail",value:3,limit:1,unit:"%",criterion:"maximum"}],
        limitations:["本校核不代表整机设计认证。"],
      },
      environment:{python:"3.14.4",platform:"Windows",dependencies:{numpy:"2.3.5",cantera:"3.2.0"},
        fingerprint:"environment-test",mechanism_sha256:"mechanism-test"},
      warnings:["请核实材料数据"],nonfinite_fields:[],
    },
  };
}

beforeEach(() => configureResultSchema(schema));

test("both cycle families show the six desktop KPI values with correct names", () => {
  const ffsc = renderAnalysis("overview",record());
  const gg = renderAnalysis("overview",record("gg"));
  for (const html of [ffsc,gg]) {
    assert.equal((html.match(/class="kpi-card"/g) || []).length,6);
    assert.match(html,/1,234\.567 <small>kN<\/small>/);
    assert.match(html,/计算真空推力/);
    assert.match(html,/工况计算值 1,000 kN（环境压力未记录）/);
    assert.match(html,/计算真空比冲 Isp/);
    assert.match(html,/350 <small>s<\/small>/);
    assert.match(html,/300 <small>kg\/s<\/small>/);
  }
  assert.match(ffsc,/富氧预燃室温度/);
  assert.match(ffsc,/富燃预燃室温度/);
  assert.doesNotMatch(ffsc,/GG 涡轮排气温度/);
  assert.match(gg,/燃气发生器温度/);
  assert.match(gg,/GG 涡轮排气温度/);
  assert.doesNotMatch(gg,/富氧预燃室温度/);
});

test("server overview declarations and cycle-specific groups drive rendering", () => {
  const ffsc = renderAnalysis("overview",record());
  const gg = renderAnalysis("overview",record("gg"));
  assert.match(ffsc,/FFSC 流量分配/);
  assert.match(ffsc,/燃料至富氧预燃室比例/);
  assert.doesNotMatch(ffsc,/GG 流量分配/);
  assert.match(gg,/GG 流量分配/);
  assert.match(gg,/<td class="metric-value">12<\/td><td class="metric-unit">kg\/s<\/td>/);
  assert.doesNotMatch(gg,/FFSC 流量分配/);
});

test("numerical convergence never turns failed system checks green", () => {
  const html = renderAnalysis("overview",record());
  assert.match(html,/<span class="badge success">完整数值收敛<\/span>/);
  assert.match(html,/<span class="badge failed">系统约束未通过<\/span>/);
  assert.match(html,/系统约束校核/);
  assert.doesNotMatch(html,/历史工程校核/);
  assert.match(html,/<td class="constraint-value">3 %<\/td><td class="constraint-limit">≤ 1 %<\/td><td><span class="badge failed">未通过/);
  assert.match(html,/独立于数值收敛/);
  assert.match(html,/残差 0/);
  assert.doesNotMatch(html,/尚未确认收敛/);
  const failed = record();
  failed.result.assessment.numerical = {status:"fail",label:"未收敛",residual:.1};
  assert.match(renderAnalysis("overview",failed),/仅用于诊断/);
});

test("all state points and SI conversions appear, including state details", () => {
  const html = renderAnalysis("states",record());
  const nodes=flowAnalysisModel(record().result).nodes;
  const chamber=nodes.find(node=>node.id==="main_chamber"),other=nodes.find(node=>node.id==="custom_state");
  assert.deepEqual([chamber.pressure,chamber.temperature,chamber.massFlow,chamber.density,chamber.enthalpy],[20,3400,300,null,2000]);
  assert.deepEqual([other.pressure,other.temperature,other.massFlow,other.density,other.enthalpy],[null,null,null,1,null]);
  assert.equal(chamber.composition.H2O,.6);
  assert.equal(chamber.metadata.find(field=>field.key==="mach").value,.1);
  for(const node of nodes){assert(html.includes(`data-sa-row="${node.id}"`));assert(html.includes(node.title));}
  assert.match(html,/H2O：原始质量分数 0\.6/);
  assert.match(html,/原始字段 · mach/);
});

test("system component values follow declared units and shared labels", () => {
  const html = renderAnalysis("components",record());
  const normalized = componentAnalysisItems(record().result).flatMap(item=>item.fields);
  for (const [key,label,value,unit] of [
    ["power_W","泵功率",2,"MW"],["p_in_Pa","泵入口压力",10,"MPa"],
    ["injector_dp_Pa","预燃室喷注器压降",2,"MPa"],["preburner_pressure_Pa","预燃室压力",20,"MPa"],
    ["thrust_N","推力",1000,"kN"],["thrust_vac_N","真空推力",1234.567,"kN"],
    ["p_exit_Pa","喷管出口压力",.05,"MPa"],
  ]) {
    const field=normalized.find(item=>item.key===key);
    assert.equal(field.label,label);
    assert.equal(field.unit,unit);
    assert(Math.abs(field.value-value) <= 1e-10*Math.max(1,Math.abs(value)),`${label} conversion`);
    assert(html.includes(`title="${key}"`),`${label} remains accessible in detailed tables`);
  }
  assert.match(html,/泵效率/);
  assert.doesNotMatch(html,/服务器新增部件|custom_metric/);
  const aliases = record();
  aliases.result.calculation.components.ox_pump.outputs.power_MW = 2;
  const aliasFields = componentAnalysisItems(aliases.result).find(item=>item.id==="ox_pump").fields;
  assert.equal(aliasFields.filter(item=>item.label==="泵功率").length,1,"Pa/W aliases should not duplicate displayed engineering units");
});

test("system view retains cycle quantities and leaves detailed design in the original record", () => {
  const input = record("gg");
  input.result.calculation.components.ox_pump.outputs.impeller_safety_factor = .5;
  input.result.calculation.components.ox_pump.outputs.d_impeller_est_m = .25;
  input.result.calculation.components.ox_pump.notes.push("叶轮材料强度诊断：未通过");
  input.result.calculation.components.cooling_channel = {outputs:{
    heat_load_MW:60,P_in_Pa:16000000,P_out_Pa:14000000,delta_P_MPa:2,
    T_in_K:100,T_out_K:350,T_wall_hot_K:900,channel_height_mm:5,
    delta_P_hydraulic_MPa:100,delta_P_source:0,
  },notes:["详细冷却诊断：水力压降超限"]};
  input.result.calculation.components.cooling_marching = {outputs:{T_wall_hot_max_K:1000},notes:["沿程历史诊断"]};
  input.result.calculation.components.gg_turbine = {outputs:{
    turbine_power_MW:5,turbine_pressure_ratio:20,mass_flow_kgs:10,
    T_in_K:900,T_exhaust_K:500,rotor_safety_factor:.3,D_mean_m:.2,
  },notes:[]};
  input.result.calculation.components.injector = {outputs:{dp_ox_MPa:2,num_elements:999},notes:[]};
  input.result.calculation.components.nozzle.outputs.At_m2 = .1;
  const before = structuredClone(input);
  const html = renderAnalysis("components",input);
  for (const key of ["heat_load_MW","P_in_Pa","T_out_K","delta_P_MPa","turbine_power_MW","mass_flow_kgs","dp_ox_MPa"])
    assert(html.includes(`title="${key}"`),key);
  for (const key of ["impeller_safety_factor","d_impeller_est_m","T_wall_hot_K","channel_height_mm",
    "delta_P_hydraulic_MPa","cooling_marching","T_wall_hot_max_K","rotor_safety_factor","D_mean_m","num_elements","At_m2"])
    assert(!html.includes(key),key);
  assert.match(html,/给定压降/);
  const logs = renderAnalysis("log",input);
  for (const note of ["叶轮材料强度诊断：未通过","详细冷却诊断：水力压降超限","沿程历史诊断"])
    assert(logs.includes(note),note);
  assert.deepEqual(input,before,"presentation must not delete details or change immutable results");
});

test("component aliases prefer valid engineering values without discarding a usable fallback", () => {
  const input=record();
  input.result.calculation.components.ox_pump.outputs={power_W:2000000,power_MW:null,
    p_in_Pa:1000000,p_in_MPa:1,delta_p_Pa:5000000};
  const fields=componentAnalysisItems(input.result).find(item=>item.id==="ox_pump").fields;
  assert.equal(fields.find(field=>field.key==="power_W").value,2);
  assert(!fields.some(field=>field.key==="power_MW"),"null preferred alias must not shadow the valid fallback");
  assert.equal(fields.find(field=>field.key==="delta_p_Pa").unit,"MPa");
  assert.equal(fields.find(field=>field.key==="delta_p_Pa").value,5);
  assert(!fields.some(field=>field.key==="p_in_Pa"));
  assert.equal(fields.find(field=>field.key==="p_in_MPa").value,1);
});

test("GG labels and nozzle velocity describe the stored quantities accurately", () => {
  const input=record("gg");
  input.result.calculation.components.gas_generator={outputs:{preburner_pressure_Pa:10000000,injector_dp_Pa:1200000,injector_dp_ratio:.12}};
  input.result.calculation.components.nozzle.outputs.Ve_ms=3200;
  input.result.calculation.components.nozzle.outputs.Cf=1.7;
  input.result.calculation.components.nozzle.outputs.Cf_sl=1.7;
  const items=componentAnalysisItems(input.result);
  const gg=items.find(item=>item.id==="gas_generator");
  assert(gg.fields.every(field=>!field.label.includes("预燃室")));
  assert.equal(gg.fields.find(field=>field.key==="preburner_pressure_Pa").value,10);
  const nozzle=items.find(item=>item.id==="nozzle");
  assert.equal(nozzle.fields.find(field=>field.key==="Ve_ms").label,"喷管出口速度");
  assert(!nozzle.fields.some(field=>field.key==="Cf"));
  assert.equal(nozzle.fields.find(field=>field.key==="Cf_sl").label,"当前环境推力系数 Cf");
});

test("legacy assessments stay explicitly historical and preserve failed detailed checks", () => {
  const input = record();
  delete input.result.calculation_scope;
  delete input.result.assessment.scope;
  input.result.assessment.label = "工程约束未通过";
  input.result.assessment.checks.push({label:"历史壁温校核",status:"fail",value:1000,limit:800,unit:"K"});
  const before = structuredClone(input);
  const html = renderAnalysis("overview",input);
  assert.match(html,/历史记录：保留原计算与完整工程校核/);
  assert.match(html,/历史工程校核/);
  assert.match(html,/历史壁温校核/);
  assert.match(html,/<span class="badge failed">工程约束未通过<\/span>/);
  assert.doesNotMatch(html,/系统约束校核|已检查的系统约束通过/);
  assert.deepEqual(input,before);
});

test("historical system pass is not presented as passing newly included thermal limits", () => {
  const input=record();
  input.result.assessment.status="pass";
  input.result.assessment.label="已检查的系统约束通过";
  input.result.assessment.checks=[{label:"旧轴功率平衡",status:"pass",value:0,limit:1,unit:"%"}];
  const before=structuredClone(input),html=renderAnalysis("overview",input);
  assert.match(html,/历史系统约束范围，需重算/);
  assert.match(html,/未覆盖统一冷却剖面的沿程壁温限值/);
  assert.match(html,/旧轴功率平衡/);
  assert.doesNotMatch(html,/已检查的系统约束通过/);
  assert.deepEqual(input,before);
});

test("current thermal limit failure remains separate from full numerical convergence",()=>{
  const input=record();
  input.result.calculation_scope.version=3;
  input.result.assessment.checks=[{key:"wall_temperature",label:"沿程热面壁温峰值",status:"fail",value:1252.873,limit:800,unit:"K",criterion:"maximum"}];
  const html=renderAnalysis("overview",input);
  assert.match(html,/完整数值收敛/);
  assert.match(html,/系统约束未通过/);
  assert.match(html,/沿程热面壁温峰值/);
  assert.match(html,/1,252.873 K/);
  assert.doesNotMatch(html,/历史系统约束范围/);
});

function preburnerRecord(status="provisional",basis="") {
  const input=record();input.result.calculation_scope.version=3;
  Object.assign(input.result.calculation.inputs,{use_target_preburner_temp:1,ox_preburner_temp_target:750,
    ox_preburner_temp_limit:800,ox_preburner_temp_limit_status:status,ox_preburner_temp_limit_basis:basis});
  input.result.calculation.summary.ox_preburner_T_combustion_K=749;
  input.result.assessment={...input.result.assessment,status:"pass",label:"系统约束通过",checks:[{
    key:"ox_preburner_T_combustion_K",label:"富氧预燃室温度",value:749,limit:status==="unset"?null:800,unit:"K",criterion:"maximum",
    status:status==="unset"?"unknown":status==="provisional"?"review":"pass",limit_status:status,limit_basis:basis,
  }]};
  return input;
}

test("unset preburner limits are not evaluated and saved inactive numbers never become criteria",()=>{
  const input=preburnerRecord("unset"),before=structuredClone(input),html=renderAnalysis("overview",input);
  assert.match(html,/目标 750 K · 上限未设定（未评估）/);
  assert.match(html,/系统约束未完成评估/);
  assert.match(html,/<td class="constraint-limit">上限未设定（未评估）<\/td>/);
  assert.match(html,/<span class="badge ">未评估<\/span>/);
  assert.doesNotMatch(html,/≤ 800|上限 800 K|<span class="badge success">系统约束通过/);
  assert.deepEqual(input,before);
});

test("provisional preburner limits remain review below the numerical threshold and fail above it",()=>{
  const input=preburnerRecord(),html=renderAnalysis("overview",input);
  assert.match(html,/目标 750 K · 暂定上限 800 K（待核实）/);
  assert.match(html,/系统约束待核实/);assert.match(html,/暂定，待核实/);
  assert.doesNotMatch(html,/<span class="badge success">系统约束通过/);
  input.result.assessment.status="fail";input.result.assessment.label="系统约束未通过";
  input.result.assessment.checks[0].status="fail";input.result.assessment.checks[0].value=850;
  const failed=renderAnalysis("overview",input);
  assert.match(failed,/系统约束未通过/);assert.match(failed,/850 K · 暂定上限 800 K（待核实）/);
});

test("documented preburner limits expose user evidence without implying validation and reject empty evidence",()=>{
  const input=preburnerRecord("documented",'部件试验计划 <source>'),html=renderAnalysis("overview",input);
  assert.match(html,/上限 800 K（已填写依据，未验证）/);
  assert.match(html,/依据（用户声明）：部件试验计划 &lt;source&gt;/);
  assert.match(html,/数值满足给定上限/);assert.doesNotMatch(html,/<source>/);
  input.result.assessment.checks[0].limit_basis="";input.result.calculation.inputs.ox_preburner_temp_limit_basis="";
  const absent=renderAnalysis("overview",input);
  assert.match(absent,/上限设置无效（未评估）/);assert.match(absent,/系统约束未完成评估/);
  assert.doesNotMatch(absent,/数值满足给定上限/);
});

test("historical preburner pass is annotated as provisional without rewriting its saved verdict",()=>{
  const input=preburnerRecord("provisional");
  delete input.result.calculation.inputs.ox_preburner_temp_limit_status;
  delete input.result.assessment.checks[0].limit_status;
  input.result.assessment.checks[0].status="pass";
  const before=structuredClone(input),html=renderAnalysis("overview",input);
  assert.match(html,/历史暂定上限 800 K/);
  assert.match(html,/历史保存判定：通过/);
  assert.match(html,/历史记录按当时限值判定；限值未登记依据，当前按暂定展示，原记录不变/);
  assert.match(html,/系统约束待核实/);assert.match(html,/暂定，待核实/);
  assert.doesNotMatch(html,/<span class="badge success">系统约束通过/);
  assert.deepEqual(input,before);
});

test("preburner hints follow immutable check evidence and active target mode instead of another input snapshot",()=>{
  const input=preburnerRecord("unset");
  Object.assign(input.result.submitted_inputs,{ox_preburner_temp_limit_status:"documented",ox_preburner_temp_limit:9999,ox_preburner_temp_limit_basis:"wrong snapshot"});
  Object.assign(input.result.calculation.inputs,{use_target_preburner_temp:0,use_target_preburner_mr:1,ox_preburner_mr_target:60});
  const html=renderAnalysis("overview",input);
  assert.match(html,/指定 O\/F 60 · 上限未设定（未评估）/);
  assert.doesNotMatch(html,/目标 750 K|9,999|wrong snapshot/);
});

test("overview declarations cannot accidentally add detailed geometry or material sections", () => {
  const expanded = structuredClone(schema);
  expanded.overview_groups.ffsc.push({title:"详细几何与材料",fields:[
    {key:"chamber_wall_thickness_mm",label:"壁厚",unit:"mm"},
    {key:"engine_mass_kg",label:"质量",unit:"kg"},
  ]});
  configureResultSchema(expanded);
  const input = record();
  Object.assign(input.result.calculation.summary,{chamber_wall_thickness_mm:2,engine_mass_kg:900});
  assert.doesNotMatch(renderAnalysis("overview",input),/详细几何与材料|壁厚|engine_mass_kg/);
});

test("missing and nonfinite quantities remain missing rather than zero or pass", () => {
  const missing = record();
  missing.result.calculation.summary.thrust_vac_N = null;
  missing.result.calculation.summary.Isp_vac_s = NaN;
  missing.result.calculation.summary.mdot_total_kg_s = Infinity;
  missing.result.assessment.numerical = {};
  const html = renderAnalysis("overview",missing);
  assert.match(html,/— <small>kN<\/small>/);
  assert.match(html,/— <small>s<\/small>/);
  assert.match(html,/— <small>kg\/s<\/small>/);
  assert.match(html,/完整收敛待确认/);
  assert.match(html,/仅用于诊断/);
  assert.doesNotMatch(html,/NaN|Infinity|undefined/);
  for (const view of ["overview","states","components","log","topology"]) {
    assert.doesNotThrow(() => renderAnalysis(view,{result:{calculation:{}}}),view);
  }
});

test("all dynamic text, HTML attributes, SVG labels and server schema labels are escaped", () => {
  const attack = '<img src=x onerror="alert(1)">&\'';
  const escaped = "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;&#39;";
  const hostile = record();
  hostile.id = attack;
  hostile.result.warnings = [attack];
  hostile.result.assessment.checks[0].label = attack;
  hostile.result.assessment.checks[0].detail = attack;
  hostile.result.environment.fingerprint = attack;
  hostile.result.calculation.states.custom_state.composition = {[attack]:.5};
  hostile.result.calculation.states.custom_state.metadata = {[attack]:attack};
  hostile.result.calculation.components.ox_pump.outputs.power_W = attack;
  hostile.result.calculation.components.ox_pump.notes = [attack];
  hostile.result.calculation.topology_nodes[0].label = attack;
  hostile.result.calculation.topology_edges[0].label = attack;
  const hostileSchema = structuredClone(schema);
  hostileSchema.component_labels.ox_pump = attack;
  hostileSchema.key_labels.power_W = attack;
  hostileSchema.state_labels.custom_state = attack;
  hostileSchema.overview_groups.ffsc[0].title = attack;
  configureResultSchema(hostileSchema);
  for (const view of ["overview","states","components","log","topology"]) {
    const html = renderAnalysis(view,hostile);
    assert(!html.includes(attack),`${view}: raw API content escaped`);
    assert(html.includes(escaped),`${view}: fixture exercised dynamic escaping`);
    assert(!html.includes('<img '),`${view}: no injected element`);
  }
  assert(!renderAnalysis("overview",{status:"failed",error:attack}).includes(attack));
});

test("topology uses returned nodes and valid edges, with cycle scope stated", () => {
  const input = record("gg");
  input.result.calculation.topology_nodes.push({key:"bad",label:"非法坐标",pos:[null,10]});
  input.result.calculation.topology_edges.push({source:"missing",target:"sink",label:"不存在的连接"});
  const html = renderAnalysis("topology",input);
  assert.match(html,/燃气发生器循环（GG，开式）/);
  assert.match(html,/液氧泵/);
  assert.match(html,/LOX 主路/);
  assert.match(html,/不表示实际布置或几何尺寸/);
  assert.doesNotMatch(html,/非法坐标|不存在的连接|NaN/);
  input.result.calculation.topology_nodes = [];
  assert.match(renderAnalysis("topology",input),/没有流程节点数据/);
});

test("warnings are deduplicated and logs retain environment and missing-field trace", () => {
  const input = record();
  input.result.nonfinite_fields = ["result.calculation.states.main_chamber.density"];
  const html = renderAnalysis("log",input);
  assert.equal((html.match(/<li>请核实材料数据<\/li>/g) || []).length,1);
  for (const text of ["job-test","v4","3.14.4","2.3.5","environment-test","mechanism-test",input.result.nonfinite_fields[0]]) {
    assert(html.includes(text),text);
  }
});

test("flow component links only target renderable components and topology labels mark actual interfaces",()=>{
  const input=record();
  input.result.calculation.states.lox_after_pump={pressure:10e6,temperature:90};
  input.result.calculation.states.ox_preburner_outlet={pressure:8e6,temperature:800};
  input.result.calculation.states.ox_turbine_exhaust={pressure:5e6,temperature:700};
  input.result.calculation.components.ox_pump.outputs={unsupported_legacy_field:1};
  const flow=flowAnalysisModel(input.result);
  assert(!flow.nodes.find(node=>node.id==="lox_after_pump").componentIds.includes("ox_pump"));
  input.result.calculation.topology_nodes=[{key:"ox_preburner",label:"富氧预燃室",pos:[100,100]}];
  const html=renderAnalysis("topology",input);
  assert.match(html,/data-flow-state="ox_preburner_outlet"/);
  assert.doesNotMatch(html,/data-flow-state="ox_turbine_exhaust"/);
  assert.match(html,/role="group" aria-label="记录中的循环连接原理图"/);
});

test("schema can refresh without stale labels and rendering does not mutate records", () => {
  const changed = structuredClone(schema);
  changed.component_labels.ox_pump = "新版部件名称";
  configureResultSchema(changed);
  changed.component_labels.ox_pump = "不应跟随外部突变";
  const input = record(), before = structuredClone(input);
  const html = renderAnalysis("components",input);
  assert.match(html,/新版部件名称/);
  assert.doesNotMatch(html,/服务器新增部件|不应跟随外部突变/);
  for (const view of ["overview","states","components","log","topology"]) renderAnalysis(view,input);
  assert.deepEqual(input,before);
  configureResultSchema(null);
  assert.doesNotThrow(() => renderAnalysis("overview",input));
  assert.doesNotMatch(renderAnalysis("components",input),/新版部件名称/);
});

test("welcome, queued, running and error records do not invent calculation results", () => {
  assert.match(renderAnalysis(),/从一个设计点开始/);
  for (const [status,message] of [["queued","队列"],["running","正在运行"],["failed","计算失败"],["cancelled","已取消"]]) {
    const html = renderAnalysis("overview",{status});
    assert(html.includes(message));
    assert.doesNotMatch(html,/class="kpi-card"/);
  }
  assert.doesNotThrow(() => renderAnalysis("constructor",record()));
});

function engineeringSchema(cycle) {
  const expanded=structuredClone(schema);
  expanded.overview_groups[cycle]=[
    {title:"流量分配 / Flow",fields:[
      {key:"mdot_fuel_kg_s",label:"甲烷总流量",unit:"kg/s"},
      {key:"mdot_ox_kg_s",label:"液氧总流量",unit:"kg/s"},
      {key:"gg_mdot_kg_s",label:"燃气发生器流量",unit:"kg/s"},
    ]},
    {title:"功率平衡 / Power",fields:[
      {key:"ox_pump_power_MW",label:"液氧泵功率",unit:"MW"},
      {key:"fuel_pump_power_MW",label:"甲烷泵功率",unit:"MW"},
      {key:"gg_turbine_power_delivered_MW",label:"可用轴功率",unit:"MW"},
    ]},
    {title:"求解状态 / Solver",fields:[{key:"solver_iterations",label:"Newton 步数",unit:"—"}]},
  ];
  configureResultSchema(expanded);
}

test("engineering tables separate symbols, values and units while solver diagnostics are collapsed", () => {
  engineeringSchema("gg");
  const input=record("gg");
  Object.assign(input.result.calculation.summary,{mdot_fuel_kg_s:60,mdot_ox_kg_s:240,mdot_chamber_kg_s:288,
    ox_pump_power_MW:2,fuel_pump_power_MW:3,gg_turbine_power_delivered_MW:4.8});
  const html=renderAnalysis("overview",input);
  assert.match(html,/<th scope="col">参数<\/th><th scope="col">符号<\/th><th scope="col">数值<\/th><th scope="col">单位<\/th>/);
  assert.match(html,/<td class="metric-symbol">ṁf<\/td><td class="metric-value">60<\/td><td class="metric-unit">kg\/s<\/td>/);
  assert.match(html,/<details class="panel engineering-panel diagnostics-panel"><summary/);
  assert.doesNotMatch(html,/<details class="panel engineering-panel diagnostics-panel" open/);
  assert.match(html,/Newton 步数/);
  assert.match(html,/role="img" aria-label="系统质量流量，单位 kg\/s/);
  assert(html.indexOf('class="summary-grid"') < html.indexOf("<h2>系统约束校核</h2>"));
  assert(html.indexOf("<h2>系统约束校核</h2>") < html.indexOf('class="panel engineering-panel diagnostics-panel"'));
});

test("flow comparisons retain the actual shared scale and reveal mass imbalance", () => {
  engineeringSchema("gg");
  const input=record("gg");
  Object.assign(input.result.calculation.summary,{mdot_total_kg_s:300,mdot_fuel_kg_s:60,mdot_ox_kg_s:240,
    mdot_chamber_kg_s:290,gg_mdot_kg_s:12});
  const html=renderAnalysis("overview",input);
  assert.match(html,/分流差额（主室 \+ GG − 总量）\+2 kg\/s/);
  assert.match(html,/class="chart-bar-ox" x="118" y="28" width="264"/);
  assert.match(html,/class="chart-bar-fuel" x="118" y="57" width="66"/);
  assert.match(html,/class="chart-bar-gg" x="118" y="115" width="13\.2"/);
  assert.match(html,/横轴 0 至 300 kg\/s/);
  input.result.calculation.summary.mdot_ox_kg_s=null;
  const missing=renderAnalysis("overview",input);
  assert.doesNotMatch(missing,/class="chart-bar-ox"/);
  assert.match(missing,/图示所需数据缺失/);
  assert.match(missing,/<td class="metric-value">—<\/td><td class="metric-unit">kg\/s<\/td>/);
});

test("single-shaft GG compares combined pumps with delivered work, preserving negative margin", () => {
  engineeringSchema("gg");
  const input=record("gg");
  input.result.calculation.inputs.gg_layout="single_shaft";
  Object.assign(input.result.calculation.summary,{ox_pump_power_MW:2,fuel_pump_power_MW:3,
    gg_turbine_power_MW:6,gg_turbine_power_delivered_MW:4.8});
  const html=renderAnalysis("overview",input);
  assert.match(html,/共轴泵组/);
  assert.match(html,/class="balance-readout delta-deficit">ΔP -0\.2 MW \(-4%\)/);
  assert.match(html,/泵轴功率需求 5；涡轮可用轴功率 4\.8/);
  assert.doesNotMatch(html,/液氧侧轴系|甲烷侧轴系/);
  assert.match(html,/系统约束未通过/);
  delete input.result.calculation.summary.gg_turbine_power_delivered_MW;
  const missing=renderAnalysis("overview",input);
  assert.match(missing,/ΔP — MW/);
  assert.doesNotMatch(missing,/class="chart-bar-drive"/);
});

test("dual-shaft GG exposes opposing shaft margins instead of hiding them in an aggregate", () => {
  engineeringSchema("gg");
  const input=record("gg");
  input.result.calculation.inputs.gg_layout="dual_shaft";
  Object.assign(input.result.calculation.summary,{ox_pump_power_MW:2,fuel_pump_power_MW:3,
    ox_turbine_power_MW:5,fuel_turbine_power_MW:5,gg_mech_efficiency:.5,
    gg_turbine_power_delivered_MW:5});
  const html=renderAnalysis("overview",input);
  assert.match(html,/液氧侧轴系/);
  assert.match(html,/甲烷侧轴系/);
  assert.match(html,/ΔP \+0\.5 MW \(\+25%\)/);
  assert.match(html,/ΔP -0\.5 MW \(-16\.667%\)/);
  assert.doesNotMatch(html,/共轴泵组/);
  delete input.result.calculation.summary.gg_mech_efficiency;
  const missing=renderAnalysis("overview",input);
  assert.equal((missing.match(/ΔP — MW/g) || []).length,2);
  assert.doesNotMatch(missing,/class="chart-bar-drive"/);
});

test("FFSC shaft comparison follows its own output convention without applying GG efficiency", () => {
  engineeringSchema("ffsc");
  const input=record("ffsc");
  Object.assign(input.result.calculation.summary,{ox_pump_power_MW:2,fuel_pump_power_MW:3,
    ox_turbine_power_MW:1.9,fuel_turbine_power_MW:3.1,gg_mech_efficiency:.5,
    mdot_fuel_kg_s:60,mdot_ox_kg_s:240});
  const before=structuredClone(input), html=renderAnalysis("overview",input);
  assert.match(html,/ΔP -0\.1 MW \(-5%\)/);
  assert.match(html,/ΔP \+0\.1 MW \(\+3\.333%\)/);
  assert.match(html,/采用本模型的涡轮输出口径/);
  assert.doesNotMatch(html,/可用轴功率已计入|class="chart-bar-main"|class="chart-bar-gg"/);
  assert.deepEqual(input,before);
});

test("small signed power residual remains visible and neutral using the recorded tolerance", () => {
  engineeringSchema("gg");
  const input=record("gg");
  Object.assign(input.result.calculation.summary,{ox_pump_power_MW:2,fuel_pump_power_MW:3,
    gg_turbine_power_delivered_MW:5-1.234e-12});
  const nearZero=renderAnalysis("overview",input);
  assert.match(nearZero,/class="balance-readout delta-within-band">ΔP -[\d.]+e-12 MW/);
  assert.match(nearZero,/色标容差 ±1%（默认参考） · 差额在容差范围内/);
  assert.doesNotMatch(nearZero,/class="balance-readout delta-deficit"/);
  assert.match(nearZero,/class="badge failed">系统约束未通过/);
  input.result.assessment.checks.push({key:"power_error_percent",unit:"%",criterion:"maximum",limit:.1});
  input.result.calculation.summary.gg_turbine_power_delivered_MW=4.99;
  const tightened=renderAnalysis("overview",input);
  assert.match(tightened,/class="balance-readout delta-deficit">ΔP -0.01 MW \(-0.2%\)/);
  assert.match(tightened,/色标容差 ±0.1%（本记录校核）/);
  assert.doesNotMatch(tightened,/差额在容差范围内/);
});

test("recorded propellant controls system labels without changing flow or power quantities", () => {
  engineeringSchema("gg");
  const methane=record("gg");
  methane.result.calculation.inputs.gg_layout="dual_shaft";
  Object.assign(methane.result.calculation.summary,{mdot_total_kg_s:300,mdot_fuel_kg_s:60,
    mdot_ox_kg_s:240,mdot_chamber_kg_s:290,gg_mdot_kg_s:12,ox_pump_power_MW:2,
    fuel_pump_power_MW:3,ox_turbine_power_MW:5,fuel_turbine_power_MW:5,gg_mech_efficiency:.5});
  const kerosene=structuredClone(methane);
  kerosene.result.calculation.inputs.propellant_pair="lox_kerosene";
  // Submitted inputs disagree deliberately: the actual serialized calculation wins.
  kerosene.result.submitted_inputs.propellant_pair="lox_methane";
  const before=structuredClone(kerosene), methaneHtml=renderAnalysis("overview",methane);
  const html=renderAnalysis("overview",kerosene);
  for(const text of ["煤油总流量","煤油供给","煤油泵功率","煤油侧轴系","LOX + C₁₂H₂₆","正十二烷单组分替代物","GG 稳态循环与给定冷却吸热量模型"]) assert(html.includes(text),text);
  assert.doesNotMatch(html,/甲烷|CH₄|CH4/);
  assert.match(methaneHtml,/甲烷供给|甲烷侧轴系/);
  for(const rendered of [html,methaneHtml]) {
    assert.match(rendered,/class="chart-bar-fuel" x="118" y="57" width="66"/);
    assert.match(rendered,/分流差额（主室 \+ GG − 总量）\+2 kg\/s/);
    assert.match(rendered,/ΔP \+0\.5 MW \(\+25%\)/);
    assert.match(rendered,/ΔP -0\.5 MW \(-16\.667%\)/);
  }
  assert.deepEqual(kerosene,before);
  // Rendering a subsequent legacy calculation restores methane, with no shared fuel state.
  assert.match(renderAnalysis("overview",methane),/甲烷侧轴系/);
  assert.doesNotMatch(renderAnalysis("overview",methane),/煤油侧轴系|正十二烷/);
});

test("kerosene state, component and topology titles adapt while raw chemical species and diagnostics stay intact", () => {
  const expanded=structuredClone(schema);
  expanded.state_labels.fuel_tank="甲烷储箱";
  expanded.component_labels.fuel_pump="甲烷泵";
  expanded.component_labels.gg_turbine_fuel="GG 涡轮（CH4 轴）";
  configureResultSchema(expanded);
  const input=record("gg");
  input.result.calculation.inputs.propellant_pair="lox_kerosene";
  input.result.calculation.states.fuel_tank={name:"甲烷储箱",pressure:300000,temperature:300,
    mass_flow:60,density:750,enthalpy:120000,composition:{c12h26:1},metadata:{}};
  input.result.calculation.states.fuel_boost_out={name:"甲烷增压泵出口",composition:{c12h26:1}};
  input.result.calculation.states.main_chamber.composition.CH4=.002;
  input.result.calculation.components.fuel_pump={name:"fuel_pump",outputs:{power_MW:3},notes:["原始 CH4 文本保持不变"]};
  input.result.calculation.components.gg_turbine_fuel={outputs:{turbine_power_MW:5}};
  input.result.calculation.topology_nodes=[{key:"fuel_tank",label:"甲烷储箱",pos:[0,0]},
    {key:"fuel_pump",label:"甲烷泵",pos:[200,0]}];
  input.result.calculation.topology_edges=[{source:"fuel_tank",target:"fuel_pump",label:"主路 CH4"}];
  const before=structuredClone(input), statesHtml=renderAnalysis("states",input);
  assert.match(statesHtml,/煤油储箱/);
  assert.match(statesHtml,/煤油增压泵出口/);
  assert.match(statesHtml,/c12h26：原始质量分数 1/);
  assert.match(statesHtml,/CH4：原始质量分数 0\.002/,"combustion species are data, not fuel labels");
  const componentsHtml=renderAnalysis("components",input);
  assert.match(componentsHtml,/>煤油泵</);
  assert.match(componentsHtml,/GG 涡轮（C₁₂H₂₆ 轴）/);
  const topologyHtml=renderAnalysis("topology",input);
  assert.match(topologyHtml,/煤油储箱|煤油泵/);
  assert.match(topologyHtml,/主路 C₁₂H₂₆/);
  assert.doesNotMatch(topologyHtml,/甲烷|CH4/);
  assert.match(renderAnalysis("log",input),/原始 CH4 文本保持不变/);
  assert.deepEqual(input,before);
});

test("propellant naming falls back to submitted inputs only when serialized calculation inputs are absent", () => {
  const input=record("gg");
  input.result.submitted_inputs.propellant_pair="lox_kerosene";
  delete input.result.calculation.inputs;
  assert.match(renderAnalysis("overview",input),/液氧 \/ 煤油（正十二烷替代物）/);
  input.result.calculation.inputs={cycle_type:"gg"};
  assert.match(renderAnalysis("overview",input),/液氧 \/ 甲烷/);
  assert.doesNotMatch(renderAnalysis("overview",input),/正十二烷/);
});

test("steady study conditions use saved inputs, preserve missing fields, and do not imply transient results", () => {
  const input=record();
  Object.assign(input.result.calculation.inputs,{pc:33e6,ambient_pressure:101325,of_ratio:3.6,expansion_ratio:34.34});
  input.result.submitted_inputs.pc=999e6;
  const before=structuredClone(input),html=renderAnalysis('overview',input);
  assert.match(html,/aria-label="稳态计算工况"/);
  assert.match(html,/单工况 · 稳态设计点/);
  assert.match(html,/本页显示稳态设计点；热态扰动见瞬态响应分析/);
  assert.match(html,/<dt>指定燃烧室压力<\/dt><dd>33 <small>MPa/);
  assert.match(html,/<dt>环境压力边界<\/dt><dd>0\.101 <small>MPa/);
  assert.match(html,/<dt>指定总氧燃比 O\/F<\/dt><dd>3\.6 /);
  assert.match(html,/<dt>主喷管面积比 Ae\/At<\/dt><dd>34\.34 /);
  assert.doesNotMatch(html,/<dd>999 /);
  assert.deepEqual(input,before);
  delete input.result.calculation.inputs.pc;
  assert.match(renderAnalysis('overview',input),/<dt>指定燃烧室压力<\/dt><dd>— /);
});

test("performance calculations retain their recorded operating pressure instead of assuming sea level", () => {
  for (const cycle of ["ffsc","gg"]) {
    for (const [pressure,label] of [[0,"0"],[35000,"35"],[101325,"101.325"]]) {
      const input=record(cycle);
      input.result.calculation.inputs.ambient_pressure=pressure;
      input.result.submitted_inputs.ambient_pressure=987654;
      const before=structuredClone(input), html=renderAnalysis("overview",input);
      assert(html.includes(`工况计算值 1,000 kN（pₐ = ${label} kPa）`));
      assert(html.includes(`工况计算值 300 s（pₐ = ${label} kPa）`));
      assert.match(html,/工况推力目标误差 0%/);
      assert.doesNotMatch(html,/海平面|987\.654 kPa/);
      assert.deepEqual(input,before,"presentation must not recompute or modify recorded values");
    }
  }
});

test("missing or invalid saved operating pressure is disclosed without borrowing another input document", () => {
  for (const pressure of [undefined,null,"101325",NaN]) {
    const input=record();
    input.result.calculation.inputs.ambient_pressure=pressure;
    input.result.submitted_inputs.ambient_pressure=101325;
    const html=renderAnalysis("overview",input);
    assert.match(html,/工况计算值 1,000 kN（环境压力未记录）/);
    assert.match(html,/工况计算值 300 s（环境压力未记录）/);
    assert.doesNotMatch(html,/海平面|pₐ = 101\.325 kPa/);
  }
  const legacy=record();
  delete legacy.result.calculation.inputs;
  legacy.result.submitted_inputs.ambient_pressure=25000;
  assert.match(renderAnalysis("overview",legacy),/工况计算值 1,000 kN（pₐ = 25 kPa）/);
});
