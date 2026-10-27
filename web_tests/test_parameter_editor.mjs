import assert from "node:assert/strict";
import test from "node:test";
import {mergeInputDefaults, savedInputDocument, methaneThermoSummary, upgradeMethaneDatasetDraft, upgradeCoolingModelDraft, preburnerMode, selectPreburnerMode, parameterVisible, parameterPresentation,
  parameterGroups, renderParameterGroups, parameterSearchText, parameterLayoutDependency,renderParameterEvidence} from "../webapp/static/parameter-editor.js";

const base = {cycle_type:"ffsc",propellant_pair:"lox_methane",use_target_preburner_temp:1,
  use_target_preburner_mr:1,pb_mr:60,pb_temperature:750,split:.06,cooling_bartz_mode:1,
  cooling_dp_auto:0,cooling_dp:5e6,rotor_rpm:26500};
const mode = {key:"preburner_design_mode",label:"预燃室设计目标",virtual:true,role:"design_input",
  choices:[{value:"temperature",label:"指定预燃室温度"},{value:"mixture_ratio",label:"指定预燃室混合比"}]};
const fields = [mode,
  {key:"pb_temperature",label:"预燃室目标温度",default:750,role:"design_input",visibility:{preburner_modes:["temperature"]}},
  {key:"pb_mr",label:"预燃室 O/F",default:60,role:"design_input",roles_by_mode:{temperature:"initial_guess"},
    labels_by_mode:{temperature:"混合比初值",mixture_ratio:"目标混合比"}},
  {key:"split",label:"分流初值",default:.06,role:"initial_guess",visibility:{preburner_modes:["legacy_free_split"]}},
  {key:"fixed_q",label:"给定吸热量",default:90e6,visibility:{all:[{key:"cooling_bartz_mode",op:"lte",value:.5}]}},
  {key:"cooling_dp",label:"给定冷却压降",default:5e6,visibility:{all:[{key:"cooling_dp_auto",op:"lte",value:.5}]}},
  {key:"Ns",label:"比转速目标",default:30,visibility:{all:[{key:"rotor_rpm",op:"lte",value:0}]}},
  {key:"coolant_cp",label:"旧固定比热",default:2400,hidden:true},
];
const groups=[{id:"pb",title:"预燃室",cycles:["ffsc"],fields}];

test("cooling model is read-only for routine designs and budget conversion is explicit",()=>{
  const group=[{id:"cooling",title:"再生冷却模型",fields:[{key:"cooling_bartz_mode",label:"当前冷却模型",read_only:true}]}];
  const modern=Object.freeze({...base,propellant_pair:"lox_methane"});
  const html=renderParameterGroups(group,modern);
  assert.match(html,/统一准一维冷却/);
  assert.doesNotMatch(html,/data-input="cooling_bartz_mode"|<select|data-upgrade-cooling/);
  const old=Object.freeze({...modern,cooling_bartz_mode:0,cooling_heat_load:17e6,cooling_dp_pa:2e6});
  const legacy=renderParameterGroups(group,old);
  assert.match(legacy,/历史兼容/);assert.match(legacy,/data-upgrade-cooling/);
  const upgraded=upgradeCoolingModelDraft(old);
  assert.deepEqual(upgraded,{...old,cooling_bartz_mode:1});
  assert.equal(old.cooling_bartz_mode,0);
  assert.equal(upgradeCoolingModelDraft(modern),modern);
  const kerosene={...old,propellant_pair:"lox_kerosene"};
  assert.equal(upgradeCoolingModelDraft(kerosene),kerosene);
  assert.doesNotMatch(renderParameterGroups(group,kerosene),/data-upgrade-cooling/);
  assert.match(renderParameterGroups(group,kerosene),/尚未接入/);
});

test("read-only thermochemistry describes the actual loss and cooling models",()=>{
  const catalog=[{id:"gri30_highT",file:"gri30_highT.yaml",species_count:53,
    common_temperature_range_K:[300,5000],gas_eos:"ideal-gas",cantera_version:"3.2.0",
    source_label:"Cantera 官方随附数据",source_url:"https://example.test/cantera",
    file_description:"Joseph Shepherd NASA TM-4513"}];
  const modern={...base,methane_thermo_dataset:"gri30_highT",combustion_loss_model:"adiabatic"};
  const view=methaneThermoSummary(modern,catalog);
  assert.match(view.summary,/焓约束化学平衡/);assert.match(view.summary,/冻结组成 · 沿程冷却/);
  assert.match(view.summary,/理想气体混合物/);assert.match(view.details,/300–5000 K/);
  assert.match(view.details,/HEOS/);assert.match(view.details,/REFPROP 对照/);
  assert.doesNotMatch(view.summary,/<select|data-input/);assert.equal(view.historical,false);
  const budget=methaneThermoSummary({...modern,cooling_bartz_mode:0},catalog);
  assert.match(budget.summary,/扣除给定冷却吸热/);assert.match(budget.summary,/绝热膨胀/);
  const old=methaneThermoSummary({...modern,methane_thermo_dataset:"gri30",combustion_loss_model:"legacy_temperature_rise"},catalog);
  assert.match(old.summary,/焓平衡后温升折减/);assert.match(old.details,/不会自动改变该模型/);
  assert.equal(old.historical,true);
  const unknown=methaneThermoSummary({...modern,methane_thermo_dataset:"<bad>"});
  assert.match(unknown.summary,/未识别：&lt;bad&gt;/);assert.doesNotMatch(unknown.details,/5000/);
});

test("explicit high-T upgrade only changes the historical methane draft field",()=>{
  const old=Object.freeze({...base,methane_thermo_dataset:"gri30",combustion_loss_model:"legacy_temperature_rise",cooling_bartz_mode:0});
  const upgraded=upgradeMethaneDatasetDraft(old);
  assert.notEqual(upgraded,old);assert.deepEqual(upgraded,{...old,methane_thermo_dataset:"gri30_highT"});
  assert.equal(old.methane_thermo_dataset,"gri30");
  assert.equal(upgradeMethaneDatasetDraft(upgraded),upgraded);
  const kerosene={...old,propellant_pair:"lox_kerosene"};
  assert.equal(upgradeMethaneDatasetDraft(kerosene),kerosene);
});

test("mode inference honors temperature precedence and never rewrites stored flags",()=>{
  for(const [temp,mr,expected] of [[1,1,"temperature"],[1,0,"temperature"],[.5,0,"temperature"],
    [0,1,"mixture_ratio"],[0,.5,"mixture_ratio"],[0,0,"legacy_free_split"]]){
    const inputs=Object.freeze({...base,use_target_preburner_temp:temp,use_target_preburner_mr:mr});
    assert.equal(preburnerMode(inputs),expected);
    renderParameterGroups(parameterGroups(groups,inputs),inputs);
    assert.equal(inputs.use_target_preburner_temp,temp);
    assert.equal(inputs.use_target_preburner_mr,mr);
  }
});

test("explicit modern modes update only canonical legacy flags",()=>{
  const before={...base,use_target_preburner_temp:0,use_target_preburner_mr:0,opaque:"preserve"};
  for(const value of ["temperature","mixture_ratio"]){
    const after=selectPreburnerMode(before,value);
    assert.deepEqual(after,{...before,use_target_preburner_temp:value==="temperature"?1:0,use_target_preburner_mr:1});
    assert.ok(!("preburner_design_mode" in after));
  }
  assert.equal(before.use_target_preburner_mr,0);
  assert.throws(()=>selectPreburnerMode(before,"legacy_free_split"),/请选择/);
});

test("legacy rendering preserves free split and requires explicit selection to migrate",()=>{
  const legacy={...base,use_target_preburner_temp:0,use_target_preburner_mr:0};
  const html=renderParameterGroups(parameterGroups(groups,legacy),legacy);
  assert.match(html,/value="legacy_free_split" selected disabled/);
  assert.match(html,/加载时不会自动转换/);
  assert.match(html,/data-input="split"/);
  assert.doesNotMatch(html,/data-input="pb_temperature"/);
});

test("only active fields render and role changes move mixture ratio out of collapsed initials",()=>{
  const temperature=renderParameterGroups(parameterGroups(groups,base),base);
  assert.match(temperature,/details class="parameter-initials" data-initials="pb"><summary>/);
  assert.match(temperature,/data-input="pb_temperature"/);
  assert.match(temperature,/data-role="initial_guess"[^]*?混合比初值/);
  assert.doesNotMatch(temperature,/data-input="(?:fixed_q|split|Ns|coolant_cp)"/);
  const mr=selectPreburnerMode(base,"mixture_ratio");
  const mixed=renderParameterGroups(parameterGroups(groups,mr),mr);
  assert.match(mixed,/目标混合比/);
  assert.doesNotMatch(mixed,/parameter-initials|data-input="pb_temperature"/);
  assert.match(renderParameterGroups(parameterGroups(groups,base),base,new Set(["pb"])),/data-initials="pb" open/);
});

test("visibility respects cycle, propellant, all/any and mode while retaining hidden draft values",()=>{
  const inputs={...base,cooling_bartz_mode:0,cooling_dp_auto:1,rotor_rpm:0};
  const before=JSON.stringify(inputs);
  const visible=parameterGroups(groups,inputs)[0].fields.map(field=>field.key);
  assert.ok(visible.includes("fixed_q")&&visible.includes("Ns"));
  assert.ok(!visible.includes("cooling_dp"));
  assert.equal(JSON.stringify(inputs),before);
  assert.deepEqual(parameterGroups(groups,{...inputs,cycle_type:"gg"}),[]);
  assert.equal(parameterVisible({propellants:["lox_kerosene"]},inputs),false);
  assert.equal(parameterVisible({visibility:{all:[{key:"rotor_rpm",op:"lte",value:0}],any:[{key:"cooling_dp_auto",op:"eq",value:1},{key:"rotor_rpm",op:"gt",value:100}]}},inputs),true);
  assert.equal(parameterVisible({visibility:{all:[{key:"missing",op:"lte",value:0}]}},inputs),false);
});

test("display metadata is escaped and searchable, dynamic role labels match role",()=>{
  const field={...fields[2],role_label:"设计输入",help:"<script>alert(1)</script>",
    provenance:{kind:"assumption",label:"工程假设",detail:'" onmouseover="bad'}};
  const view=parameterPresentation(field,base);
  assert.equal(view.role_label,"迭代初值");
  const html=renderParameterGroups([{id:"a",title:"<unsafe>",fields:[field]}],base);
  assert.match(html,/&lt;script&gt;/);
  assert.match(html,/title="&quot; onmouseover=&quot;bad"/);
  assert.doesNotMatch(html,/<script>|<unsafe>/);
  assert.match(parameterSearchText(field,base),/混合比初值.*迭代初值.*工程假设/);
});

test("layout dependencies include hidden-field controllers so changing modes refreshes immediately",()=>{
  for(const key of ["preburner_design_mode","cycle_type","propellant_pair","cooling_bartz_mode","cooling_dp_auto","rotor_rpm"])
    assert.equal(parameterLayoutDependency(key,groups),true,key);
  assert.equal(parameterLayoutDependency("pb_mr",groups),false);
});

test("reference evidence distinguishes official values from assumptions, converts display units and escapes links",()=>{
  const html=renderParameterEvidence([
    {name:"thrust",value_si:2451662.5,classification:"published",source_url:"https://www.spacex.com/updates",source_date:"2026-05-12",checked_on:"2026-10-23",note:"250 tf"},
    {name:"pc",value_si:33e6,classification:"engineering_assumption",source_url:"javascript:alert(1)",note:"<img src=x>"},
    {name:"target_isp",value_si:327,classification:"numerical_initial_guess",note:"用于流量初值"},
  ],[{key:"thrust",label:"目标推力",factor:.001,unit:"kN"},{key:"pc",label:"室压",factor:1e-6,unit:"MPa"}]);
  assert.match(html,/2,451\.6625 kN/);
  assert.match(html,/33 MPa/);
  assert.match(html,/厂商公开值/);
  assert.match(html,/工程假设/);
  assert.match(html,/数值初值/);
  assert.match(html,/发布 2026-05-12 · 核对 2026-10-23/);
  assert.match(html,/href="https:\/\/www.spacex.com\/updates"/);
  assert.match(html,/&lt;img src=x&gt;/);
  assert.doesNotMatch(html,/javascript:|<img/);
  assert.equal(renderParameterEvidence(undefined),"");
});

test("numerical resolution gets its own advanced control without being mislabeled as an initial guess",()=>{
  const field={key:"cooling_grid_points",label:"冷却网格节点数",role:"solver_setting",advanced:true,default:120};
  const group={id:"solver",title:"数值求解",fields:[field]};
  const html=renderParameterGroups([group],{cooling_grid_points:120});
  assert.match(html,/data-initials="solver-numerics"><summary>高级设置 · 数值分辨率/);
  assert.match(html,/data-role="solver_setting"/);
  assert.doesNotMatch(html,/迭代初值|初值用于启动/);
  assert.match(renderParameterGroups([group],{cooling_grid_points:240},new Set(["solver-numerics"])),/data-initials="solver-numerics" open/);
});


test("saved snapshot identity preserves legacy data without changing new partial inputs",()=>{
  const defaults={methane_thermo_dataset:"gri30_highT",pc:33e6};
  const old={pc:20e6};
  assert.equal(mergeInputDefaults(defaults,old,true).methane_thermo_dataset,"gri30");
  assert.equal(mergeInputDefaults(defaults,old).methane_thermo_dataset,"gri30_highT");
  assert.equal(mergeInputDefaults(defaults,{}).methane_thermo_dataset,"gri30_highT");
  assert.deepEqual(old,{pc:20e6});
  for(const selected of ["gri30","gri30_highT"]){
    assert.equal(mergeInputDefaults(defaults,{...old,methane_thermo_dataset:selected},true).methane_thermo_dataset,selected);
  }
  assert.equal(savedInputDocument({inputs:old}),false);
  assert.equal(savedInputDocument({format:"unknown",schema_version:1,inputs:old}),false);
  assert.equal(savedInputDocument({format:"ffsc.workbench.case",schema_version:1,inputs:old}),true);
  assert.equal(savedInputDocument({format:"lre.workbench.inputs",schema_version:1,inputs:old}),true);
  assert.equal(savedInputDocument({project_name:"旧方案",base_revision:2,unsaved:false,inputs:old}),true);
});

test("schema subsections group current preburner targets and limits without duplicating numerical seeds",()=>{
  const limitStatus={key:"ox_preburner_temp_limit_status",label:"富氧运行上限状态",default:"provisional",role:"constraint",
    choices:[{value:"unset",label:"未设定"},{value:"provisional",label:"暂定"},{value:"documented",label:"已填写依据"}]};
  const limit={key:"ox_preburner_temp_limit",label:"富氧运行上限",default:800,role:"constraint",
    visibility:{any:[{key:limitStatus.key,op:"eq",value:"provisional"},{key:limitStatus.key,op:"eq",value:"documented"}]}};
  const basis={key:"ox_preburner_temp_limit_basis",label:"富氧上限依据",default:"",role:"constraint",multiline:true,visibility:limit.visibility};
  const group={id:"preburner_design",title:"预燃室设计",fields:[...fields.slice(0,3),limitStatus,limit,basis,{key:"future",label:"未归类字段",default:1}],sections:[
    {id:"targets",title:"设计方式与目标",description:"目标不等于运行上限",keys:[mode.key,"pb_temperature","pb_mr"]},
    {id:"limits",title:"运行温度约束",keys:[limitStatus.key,limit.key,basis.key,"pb_mr"]},
    {id:"empty",title:"空分区不展示",keys:["missing"]},
  ]};
  const inputs={...base,ox_preburner_temp_limit:800,ox_preburner_temp_limit_basis:"",future:1};
  const before=structuredClone(inputs),html=renderParameterGroups(parameterGroups([group],inputs),inputs);
  assert.match(html,/data-parameter-section="targets"/);assert.match(html,/data-parameter-section="limits"/);
  assert.match(html,/目标不等于运行上限/);assert.match(html,/value="provisional" selected/);
  assert.equal((html.match(/data-input="pb_mr"/g)||[]).length,1);
  assert.match(html,/data-initials="preburner_design">[^]*data-input="pb_mr"/);
  assert.match(html,/data-input="future"/);assert.doesNotMatch(html,/空分区不展示/);
  assert.deepEqual(inputs,before);
  const unset={...inputs,ox_preburner_temp_limit_status:"unset"};
  const unsetHtml=renderParameterGroups(parameterGroups([group],unset),unset);
  assert.doesNotMatch(unsetHtml,/data-input="ox_preburner_temp_limit"|data-input="ox_preburner_temp_limit_basis"/);
  assert.match(unsetHtml,/data-input="pb_temperature"/);
  assert.equal(unset.ox_preburner_temp_limit,800);
  assert.equal(parameterLayoutDependency(limitStatus.key,[group]),true);
  const mrInputs=selectPreburnerMode(inputs,"mixture_ratio");
  const mrHtml=renderParameterGroups(parameterGroups([group],mrInputs),mrInputs);
  assert.doesNotMatch(mrHtml,/data-input="pb_temperature"|parameter-initials/);
  assert.equal((mrHtml.match(/data-input="pb_mr"/g)||[]).length,1);
  // Search can leave a single field; it remains visible in its declared section.
  const searchHtml=renderParameterGroups([{...group,fields:[basis]}],inputs);
  assert.match(searchHtml,/data-parameter-section="limits"/);assert.doesNotMatch(searchHtml,/data-parameter-section="targets"/);
});

test("wall dataset selection exposes source state while axial design fields are not numerical resolution",()=>{
  const field={key:"wall_thermal_dataset_id",label:"内衬热物性数据集",default:"d84",role:"model_assumption",
    choices:[{value:"d84",label:"GRCop-84"}],dataset_catalog:[{dataset_id:"d84",
      manufacturing_state:{state_id:"extruded",label:"粉末冶金挤压态"},conductivity_property:{range_K:[80,1173]},
      sources:[{title:"NASA 原始手册",url:"https://ntrs.nasa.gov/citations/20020070630"}]}]};
  const group={id:"wall_thermal",title:"壁面材料与热物性",fields:[field,
    {key:"wall_material_zones_json",label:"轴向材料分区",default:"",advanced:true,role:"design_input"}],
    advanced_title:"轴向材料分区",advanced_help:"物理设计分区",
    configuration_context:[{key:"chamber_outer_wall_material",label:"外套配置",labels:{i625:"Inconel 625"},note:"不串入当前热阻"}]};
  const inputs={wall_thermal_dataset_id:"d84",wall_manufacturing_state:"unspecified",wall_material_zones_json:"",chamber_outer_wall_material:"i625"};
  const before=structuredClone(inputs),html=renderParameterGroups([group],inputs);
  assert.match(html,/80–1173 K/);
  assert.match(html,/方案制造状态未确认/);
  assert.match(html,/粉末冶金挤压态/);
  assert.match(html,/高级设置 · 轴向材料分区/);
  assert.match(html,/外套配置：Inconel 625/);
  assert.doesNotMatch(html,/数值分辨率|迭代初值|data-input="chamber_outer_wall_material"/);
  assert.match(renderParameterGroups([group],{...inputs,wall_manufacturing_state:"extruded"}),/方案制造状态与数据匹配/);
  assert.match(renderParameterGroups([group],{...inputs,wall_manufacturing_state:"other"}),/方案制造状态与数据不匹配/);
  assert.equal(parameterLayoutDependency("wall_thermal_dataset_id",[group]),true);
  assert.equal(parameterLayoutDependency("wall_manufacturing_state",[group]),true);
  assert.deepEqual(inputs,before);
});

test("legacy material import maps only evidenced curves and unsupported solids require an explicit choice",()=>{
  const defaults={wall_thermal_dataset_id:"grcop84_ellis2000_extruded_high_fe_v1",chamber_inner_wall_material:"grcop84"};
  assert.equal(mergeInputDefaults(defaults,{},true).wall_thermal_dataset_id,defaults.wall_thermal_dataset_id);
  assert.equal(mergeInputDefaults(defaults,{chamber_inner_wall_material:"cuzr"},true).wall_thermal_dataset_id,"cuzr_qi2024_heat_treated_v1");
  assert.equal(mergeInputDefaults(defaults,{chamber_inner_wall_material:"grcop42"},true).wall_thermal_dataset_id,"");
  const selected={chamber_inner_wall_material:"grcop42",wall_thermal_dataset_id:"cuzr_qi2024_heat_treated_v1"};
  assert.equal(mergeInputDefaults(defaults,selected,true).wall_thermal_dataset_id,selected.wall_thermal_dataset_id);
  const field={key:"wall_thermal_dataset_id",label:"数据集",default:defaults.wall_thermal_dataset_id,
    empty_choice_label:"请选择有来源的热物性数据集（历史材料无对应曲线）",choices:[{value:defaults.wall_thermal_dataset_id,label:"GRCop-84"}]};
  const html=renderParameterGroups([{id:"wall",fields:[field]}],{wall_thermal_dataset_id:""});
  assert.match(html,/value="" disabled selected>请选择有来源/);
  assert.doesNotMatch(html,/value="grcop84_ellis2000_extruded_high_fe_v1" selected/);
});

test("saved channel tables supersede scalar geometry without rewriting either input source",()=>{
  const scalarKeys=["cooling_channel_auto_size","cooling_target_velocity_ms","cooling_n_channels",
    "cooling_channel_width_m","cooling_channel_height_m","cooling_wall_thickness_m"];
  const group={id:"chamber_channels",title:"通道与壁面",geometry_source_key:"cooling_channel_table_json",
    fields:[...scalarKeys.map(key=>({key,label:key,default:1,visibility:{all:[{key:"cooling_channel_table_json",op:"empty"}]}})),
      {key:"cooling_channel_rel_roughness",label:"粗糙度",default:.003}]};
  const draw=inputs=>renderParameterGroups(parameterGroups([group],inputs),inputs);
  const inputs={cooling_bartz_mode:1,cooling_channel_auto_size:1,cooling_n_channels:300,cooling_channel_table_json:""};
  const scalar=draw(inputs);
  assert.match(scalar,/几何来源：标量规则/);
  assert.match(scalar,/入口速度估算深度/);
  for(const key of scalarKeys) assert.match(scalar,new RegExp(`data-input="${key}"`));
  const row={anchor:"throat",n:200,w_mm:1.8,h_mm:6,t_mm:.7};
  for(const table of [[row],{version:1,rows:[row]}]) {
    const saved={...inputs,cooling_channel_table_json:JSON.stringify(table)},before=structuredClone(saved);
    const html=draw(saved);
    assert.match(html,/沿程通道表（1 个断点）/);
    assert.match(html,/喉部/);
    assert.match(html,/自动定深与目标流速不生效/);
    for(const key of scalarKeys) assert.doesNotMatch(html,new RegExp(`data-input="${key}"`));
    assert.match(html,/data-input="cooling_channel_rel_roughness"/);
    assert.doesNotMatch(html,/data-input="cooling_channel_table_json"/);
    assert.deepEqual(saved,before);
  }
  assert.equal(parameterLayoutDependency("cooling_channel_table_json",[group]),true);
  assert.doesNotMatch(draw({...inputs,cooling_bartz_mode:0}),/几何来源/);
});

test("malformed or empty channel table definitions stay visible and never enable inactive scalars",()=>{
  const group={id:"channels",geometry_source_key:"cooling_channel_table_json",fields:[
    {key:"cooling_n_channels",label:"通道数",visibility:{all:[{key:"cooling_channel_table_json",op:"empty"}]}},
    {key:"cooling_channel_rel_roughness",label:"粗糙度"}]};
  for(const raw of [" ","[]","{}","null",'{"rows":[]}',"not-json",'[null]','{"rows":{}}']){
    const inputs={cooling_bartz_mode:1,cooling_channel_table_json:raw};
    const html=renderParameterGroups(parameterGroups([group],inputs),inputs);
    assert.match(html,/无法展示为非空断点表/);
    assert.doesNotMatch(html,/data-input="cooling_n_channels"/);
    assert.equal(inputs.cooling_channel_table_json,raw);
  }
  const malicious={cooling_bartz_mode:1,cooling_channel_table_json:'[{"anchor":"<img src=x>","w_mm":"<script>"}]'};
  const html=renderParameterGroups(parameterGroups([group],malicious),malicious);
  assert.match(html,/&lt;img src=x&gt;/);
  assert.match(html,/&lt;script&gt;/);
  assert.match(html,/未填写/);
  assert.doesNotMatch(html,/<script>|<img/);
});
