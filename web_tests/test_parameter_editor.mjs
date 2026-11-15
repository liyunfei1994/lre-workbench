import assert from "node:assert/strict";
import test from "node:test";
import {mergeInputDefaults, savedInputDocument, methaneThermoSummary, upgradeMethaneDatasetDraft, selectThermochemistryModel, upgradeCoolingModelDraft, preburnerMode, selectPreburnerMode, parameterVisible, parameterPresentation,
  parameterGroups, renderParameterGroups, parameterSearchText, parameterLayoutDependency,renderParameterEvidence,renderParameterReference,
  designEnvironment, selectDesignEnvironment, refreshDesignEnvironment, parameterValue, parameterInheritance,
  refreshParameterInheritance, renderParameterField} from "../webapp/static/parameter-editor.js";
import {applyThermochemistryBaseline,thermoModelDecisionState} from "../webapp/static/thermochemistry-model.js";

const base = {cycle_type:"ffsc",propellant_pair:"lox_methane",use_target_preburner_temp:1,
  use_target_preburner_mr:1,pb_mr:60,pb_temperature:750,split:.06,cooling_bartz_mode:1,
  cooling_dp_auto:0,cooling_dp:5e6,rotor_rpm:26500};

const supplyOverride = {key:"fuel_tank_pressure_pa",label:"甲烷出口绝对压力",type:"number",default:null,
  nullable:true,show_effective_value:true,show_role:true,fallback_key:"tank_pressure_pa",factor:1e-6,unit:"MPa",
  role:"boundary",role_label:"接口条件"};

test("supply controls present historical effective values without materializing independent inputs",()=>{
  for(const own of [{},{fuel_tank_pressure_pa:null}]){
    const inputs=Object.freeze({tank_pressure_pa:425000,...own}),snapshot=structuredClone(inputs);
    assert.equal(parameterValue(supplyOverride,inputs),.425);
    const markup=renderParameterField(supplyOverride,inputs,{compact:true});
    assert.match(markup,/data-input="fuel_tank_pressure_pa" value="0\.425"/);
    assert.match(markup,/沿用共用设定：0\.425 MPa/);
    assert.match(markup,/class="parameter-role">接口条件/);
    assert.match(markup,/aria-describedby="parameter-inheritance-fuel_tank_pressure_pa"/);
    assert.match(markup,/id="parameter-inheritance-fuel_tank_pressure_pa"/);
    assert.deepEqual(inputs,snapshot);
  }
});

test("supply overrides preserve explicit zero and invalid values instead of silently inheriting",()=>{
  const field={...supplyOverride,key:"fuel_feed_line_dp_pa",fallback_key:"feed_line_dp_pa",role:"model_assumption"};
  for(const [value,display] of [[0,0],[42000,.042]]){
    const inputs=Object.freeze({feed_line_dp_pa:50000,fuel_feed_line_dp_pa:value});
    const state=parameterInheritance(field,inputs);
    assert.equal(state.inherited,false);assert.equal(state.value,value);assert.equal(state.valid,true);
    assert.equal(parameterValue(field,inputs),display);
    assert.match(state.text,/本路独立设定/);assert.match(state.text,/清空后沿用共用设定：0\.05 MPa/);
  }
  for(const value of [NaN,Infinity,"",false,"0.4"]){
    const inputs=Object.freeze({feed_line_dp_pa:50000,fuel_feed_line_dp_pa:value});
    const state=parameterInheritance(field,inputs);
    assert.equal(state.inherited,false);assert.equal(state.valid,false);assert.equal(state.value,value);
    assert.match(state.text,/独立设定无效/);
  }
  for(const value of [undefined,null,NaN,"300000"]){
    const state=parameterInheritance(supplyOverride,{tank_pressure_pa:value});
    assert.equal(state.inherited,true);assert.equal(state.valid,false);assert.match(state.text,/共用设定缺失或无效/);
  }
});

test("supply source notes refresh while preserving the active input and stored fallback contract",()=>{
  const note={dataset:{parameterInheritance:supplyOverride.key},textContent:""};
  const root={querySelectorAll:selector=>{assert.equal(selector,"[data-parameter-inheritance]");return [note];}};
  for(const [value,inherited,valid] of [[null,true,true],[630000,false,true],[NaN,false,false]]){
    const inputs=Object.freeze({tank_pressure_pa:425000,fuel_tank_pressure_pa:value});
    refreshParameterInheritance(root,[supplyOverride],inputs);
    assert.equal(note.dataset.inherited,String(inherited));
    assert.equal(note.dataset.valid,String(valid));
    assert.equal(inputs.fuel_tank_pressure_pa,value);
  }
  const groups=[{fields:[supplyOverride]}];
  assert.equal(parameterLayoutDependency("fuel_tank_pressure_pa",groups),true);
  assert.equal(parameterLayoutDependency("tank_pressure_pa",groups),true);
  assert.equal(parameterLayoutDependency("thrust",groups),false);
  assert.equal(parameterInheritance({...supplyOverride,show_effective_value:false},{tank_pressure_pa:300000}),null);
  assert.equal(parameterValue({...supplyOverride,show_effective_value:false},{tank_pressure_pa:300000}),"");
});

test("design environments are inferred without rounding historical pressures or treating blanks as vacuum",()=>{
  for(const [pressure,key] of [[101325,"sea_level"],[0,"vacuum"],[100000,"custom"],[101324.999,"custom"],[1,"custom"],
    [null,"invalid"],[undefined,"invalid"],["","invalid"],["0","invalid"],[false,"invalid"],[-1,"invalid"],[NaN,"invalid"],[Infinity,"invalid"]]){
    const inputs=Object.freeze({...base,ambient_pressure:pressure});
    const result=designEnvironment(inputs);
    assert.equal(result.key,key,`ambient pressure ${String(pressure)}`);assert.ok(result.label);
    assert.equal(inputs.ambient_pressure,pressure);
  }
});

test("explicit standard environment selections change only ambient pressure and preserve no-op identity",()=>{
  const inputs=Object.freeze({...base,ambient_pressure:100000,thrust:800000,target_isp:321,metadata:{source:"saved"}});
  for(const [mode,pressure] of [["sea_level",101325],["vacuum",0]]){
    const selected=selectDesignEnvironment(inputs,mode);
    assert.notEqual(selected,inputs);assert.deepEqual(selected,{...inputs,ambient_pressure:pressure});
    assert.equal(selectDesignEnvironment(selected,mode),selected);
    assert.equal(inputs.ambient_pressure,100000);
  }
  for(const mode of ["custom","invalid","",null])assert.throws(()=>selectDesignEnvironment(inputs,mode));
});

test("environment status refresh updates labels and pressed states without touching an input",()=>{
  const labels=[{textContent:""}],buttons=["sea_level","vacuum"].map(mode=>({dataset:{designEnvironment:mode},
    attributes:new Map(),setAttribute(key,value){this.attributes.set(key,String(value));}}));
  const root={querySelectorAll:selector=>selector==="[data-design-environment-label]"?labels:
    selector==="[data-design-environment]"?buttons:[]};
  for(const [pressure,key] of [[0,"vacuum"],[101325,"sea_level"],[100000,"custom"],[null,"invalid"]]){
    const inputs=Object.freeze({ambient_pressure:pressure});
    refreshDesignEnvironment(root,inputs);
    assert.equal(labels[0].textContent,designEnvironment({ambient_pressure:pressure}).label);
    for(const button of buttons)assert.equal(button.attributes.get("aria-pressed"),String(button.dataset.designEnvironment===key));
    assert.equal(inputs.ambient_pressure,pressure);
  }
});

test("design flow initialization stays in the primary controls with its numerical role and editable pressure",()=>{
  const group={id:"design",title:"设计目标与工况",compact:true,primary_initials:["target_isp"],fields:[
    {key:"thrust",label:"目标推力",default:800000,factor:.001,role:"design_input"},
    {key:"ambient_pressure",label:"环境压力",default:101325,factor:1e-6,role:"boundary"},
    {key:"target_isp",label:"比冲初值",default:321,role:"initial_guess",inline_help:"用于总流量初始化",help:"最终比冲为计算输出"},
  ]};
  const inputs=Object.freeze({...base,thrust:800000,ambient_pressure:100000,target_isp:321});
  const markup=renderParameterGroups([group],inputs);
  assert.match(markup,/data-field="target_isp" data-role="initial_guess"/);
  assert.equal([...markup.matchAll(/data-input="target_isp"/g)].length,1);
  assert.doesNotMatch(markup,/data-initials="design"|高级设置 · 迭代初值/);
  for(const details of markup.matchAll(/<details\b[^>]*>[\s\S]*?<\/details>/g))assert.doesNotMatch(details[0],/data-input="target_isp"/);
  assert.match(markup,/<input[^>]*data-input="ambient_pressure"[^>]*value="0\.1"/);
  assert.deepEqual([...markup.matchAll(/<button\b[^>]*data-design-environment="([^"]+)"[^>]*>/g)].map(match=>match[1]).sort(),["sea_level","vacuum"]);
  assert.doesNotMatch(markup,/data-input="(?:design_environment|environment_mode)"/);
  assert.equal(inputs.ambient_pressure,100000);
});

test("parameter forms link to definitions while retaining only present accessible help",()=>{
  const fields=[
    {key:"first",label:"当前参数",default:1,help:"完整原理说明",inline_help:"当前模式提示",provenance:{label:"取值依据"}},
    {key:"second",label:"第二参数",default:2,help_sections:[{label:"定义",text:"详细定义"}]},
    {key:"third",label:"第三参数",default:3,analysis_modes:{fixed_hardware_steady:{description:"固定硬件含义"}}},
  ];
  const inputs=Object.freeze({...base,first:1,second:2,third:3});
  for(const compact of [true,false]){
    const html=renderParameterGroups([{id:"test",title:"设置",compact,fields}],inputs);
    const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
    for(const match of html.matchAll(/aria-describedby="([^"]+)"/g))
      for(const id of match[1].split(/\s+/))assert.equal(ids.filter(value=>value===id).length,1,`${compact}: ${id}`);
    assert.doesNotMatch(html,/parameter-notes/);
    if(compact){
      assert.match(html,/data-help-chapter="parameter:test"[^>]*>参数定义与依据/);
      assert.match(html,/当前模式提示/);
      assert.doesNotMatch(html,/完整原理说明|详细定义|固定硬件含义/);
    }else assert.match(html,/完整原理说明/);
  }
});

test("parameter reference is read-only, escapes content and retains complete field evidence",()=>{
  const group=Object.freeze({id:'group"',title:"参数组",description:"组说明 <条件>",notes_intro:"采用范围",fields:[
    {key:"condition",label:"条件",unit:"MPa",role:"boundary",help_sections:[
      {label:"参数定义",text:"绝对压力 <说明>"},{label:"计算作用",text:"完整计算作用"}],
      provenance:{label:"工程假设",detail:'依据 <来源> "修订"'},
      choices:[{value:1,label:"模式 <一>"}]},
    {key:"initial",label:"初值",role:"initial_guess",help:"完整初值定义",inline_help:"简短初值提示"},
    {key:"hidden",label:"不适用参数",hidden:true,help:"不应显示"},
    {key:"valves",label:"阀门定义",custom_editor:"valve_configuration",help:"阀门参数说明"},
  ]});
  const inputs=Object.freeze({...base,condition:1,initial:2,valves:"saved"}),before=JSON.stringify(inputs);
  const html=renderParameterReference(group,inputs);
  for(const text of ["组说明 &lt;条件&gt;","采用范围","MPa","参数定义","绝对压力 &lt;说明&gt;",
    "完整计算作用","依据 &lt;来源&gt; &quot;修订&quot;","完整初值定义","模式 &lt;一&gt;","阀门参数说明"])
    assert.ok(html.includes(text),text);
  assert.doesNotMatch(html,/<(?:input|select|textarea|button|details)\b|data-input=|data-valve-action=|不适用参数|id="undefined"/);
  const fixed=renderParameterReference({...group,fields:group.fields.map(field=>({...field,read_only:true}))},inputs);
  assert.match(fixed,/记录取值：模式 &lt;一&gt;/);assert.doesNotMatch(fixed,/可选项：/);
  assert.equal(JSON.stringify(inputs),before);
});

test("manual parameter roles follow the draft mode without modifying stored values",()=>{
  const group={id:"mode",fields:[{key:"pb_mr",label:"给定混合比",role:"design_input",help:"原始定义",
    roles_by_mode:{temperature:"initial_guess"},labels_by_mode:{temperature:"混合比初值",mixture_ratio:"目标混合比"},
    help_by_mode:{temperature:"初始化定义",mixture_ratio:"设计输入定义"}},
    {key:"pb_temperature",label:"指定温度",visibility:{preburner_modes:["temperature"]},help:"温度定义"}]};
  const inputs=Object.freeze({...base});
  const temperature=renderParameterReference(group,inputs);
  assert.match(temperature,/混合比初值/);assert.match(temperature,/迭代初值/);assert.match(temperature,/指定温度/);
  const ratio=renderParameterReference(group,{...inputs,use_target_preburner_temp:0});
  assert.match(ratio,/目标混合比/);assert.doesNotMatch(ratio,/混合比初值|迭代初值|指定温度/);
  assert.equal(inputs.pb_mr,60);assert.equal(inputs.use_target_preburner_temp,1);
});

test("explicit model selection binds EOS species and loss without changing data or engineering inputs",()=>{
  const old=Object.freeze({...base,gas_eos:"ideal-gas",gas_species_policy:"auto",methane_thermo_dataset:"gri30",
    combustion_loss_model:"legacy_temperature_rise",combustion_efficiency:.98,pc:33e6,target_isp:327});
  const before=structuredClone(old);
  for(const eos of ["ideal-gas","Peng-Robinson","Peng-Robinson-Boston-Mathias"]){
    const changed=selectThermochemistryModel(old,eos);
    assert.deepEqual(changed,{...before,gas_eos:eos,gas_species_policy:eos==="ideal-gas"?"full":"pr_supported_v1",combustion_loss_model:"adiabatic"});
    assert.equal(selectThermochemistryModel(changed,eos),changed,"an exact applied model is a no-op");
    assert.equal(thermoModelDecisionState(changed).runnableSupported,true);
    const prior=Object.freeze({...changed,gas_species_policy:"auto",combustion_loss_model:"legacy_temperature_rise"});
    assert.deepEqual(selectThermochemistryModel(prior,eos),changed,"reapplying the same EOS resolves legacy settings explicitly");
    assert.equal(prior.gas_species_policy,"auto");assert.equal(prior.combustion_loss_model,"legacy_temperature_rise");
  }
  assert.deepEqual(old,before);
  for(const eos of ["Redlich-Kwong",null,"",undefined,"constructor"])assert.throws(()=>selectThermochemistryModel(old,eos),/支持的/);
  for(const unsupported of [{...old,cycle_type:"gg"},{...old,cycle_type:null},{...old,propellant_pair:"lox_hydrogen"},{...old,propellant_pair:null}])
    assert.throws(()=>selectThermochemistryModel(unsupported,"ideal-gas"),/全流量补燃/);
  const missingPropellant={...old};delete missingPropellant.propellant_pair;
  assert.equal(selectThermochemistryModel(missingPropellant,"ideal-gas").gas_species_policy,"full");
  const invalidDataset=Object.freeze({...old,methane_thermo_dataset:"unknown_dataset"});
  assert.equal(selectThermochemistryModel(invalidDataset,"Peng-Robinson").methane_thermo_dataset,"unknown_dataset","model choice must not silently replace the data record");
  assert.equal(parameterLayoutDependency("gas_eos",[]),true);
  assert.equal(parameterLayoutDependency("gas_species_policy",[]),true);
});

test("saved EOS defaults match server legacy identity while explicit values stay untouched",()=>{
  const defaults={gas_eos:"ideal-gas",gas_species_policy:"auto"},old={pc:33e6};
  assert.deepEqual(mergeInputDefaults(defaults,old,true),{...old,gas_eos:"ideal-gas",gas_species_policy:"full"});
  assert.deepEqual(mergeInputDefaults(defaults,old),{...old,...defaults});
  const saved={...old,gas_eos:"ideal-gas",gas_species_policy:"pr_supported_v1"},before=structuredClone(saved);
  assert.deepEqual(mergeInputDefaults(defaults,saved,true),saved);
  assert.deepEqual(saved,before);assert.deepEqual(old,{pc:33e6});
});

test("new design defaults do not silently replace omitted historical thermochemistry settings",()=>{
  const defaults={gas_eos:"ideal-gas",gas_species_policy:"full",combustion_loss_model:"adiabatic",methane_thermo_dataset:"gri30_highT"};
  assert.deepEqual(mergeInputDefaults(defaults,{}),defaults);
  const completeDefaults={...defaults,cooling_channel_height_m:.002,cooling_wall_temp_limit_status:"unset"};
  assert.deepEqual(mergeInputDefaults(completeDefaults,{}),completeDefaults);
  for(const savedSnapshot of [false,true]){
    const original=Object.freeze({pc:33e6});
    const expected={...defaults,...original,combustion_loss_model:"legacy_temperature_rise",
      gas_species_policy:savedSnapshot?"full":"auto",methane_thermo_dataset:savedSnapshot?"gri30":"gri30_highT"};
    assert.deepEqual(mergeInputDefaults(defaults,original,savedSnapshot),expected);
    const pr=Object.freeze({...original,gas_eos:"Peng-Robinson-Boston-Mathias"});
    assert.deepEqual(mergeInputDefaults(defaults,pr,savedSnapshot),{...expected,gas_eos:pr.gas_eos});
    const explicit=Object.freeze({...pr,gas_species_policy:"auto",combustion_loss_model:"adiabatic",methane_thermo_dataset:"gri30"});
    assert.deepEqual(mergeInputDefaults(defaults,explicit,savedSnapshot),explicit);
  }
  assert.equal(mergeInputDefaults(defaults,{},true).combustion_loss_model,"legacy_temperature_rise");
  const invalid={gas_eos:"unknown",gas_species_policy:"unknown",combustion_loss_model:"unknown"};
  assert.deepEqual(mergeInputDefaults(defaults,invalid),{...defaults,...invalid});
});

test("fixed architecture is displayed as a value rather than an editable selector",()=>{
  const groups=[{id:"architecture",fields:[{key:"cycle_type",label:"循环方式",read_only:true,
    choices:[{value:"ffsc",label:"全流量补燃循环（FFSC）"}]}]}];
  const before={...base};
  const html=renderParameterGroups(groups,base);
  assert.match(html,/<output class="parameter-fixed-value"[^>]*>全流量补燃循环（FFSC）<\/output>/);
  assert.doesNotMatch(html,/<select|<input|data-input=/);
  assert.deepEqual(base,before);
  const legacy=renderParameterGroups(groups,{...base,cycle_type:"gg"});
  assert.match(legacy,/>gg<\/output>/);
  assert.doesNotMatch(legacy,/全流量补燃循环/);
});

test("configuration page describes the recorded cycle without editable or run controls",()=>{
  const groups=[{id:"cycle",title:"发动机构型与循环原理",fields:[
    {key:"cycle_type",read_only:true,default:"ffsc"},
    {key:"propellant_pair",read_only:true,default:"lox_methane"},
  ]}];
  const inputs=Object.freeze({...base}),before=structuredClone(inputs);
  const html=renderParameterGroups(groups,inputs);
  assert.match(html,/data-group="cycle"/);
  assert.match(html,/data-field="cycle_type"/);
  assert.match(html,/data-field="propellant_pair"/);
  assert.match(html,/全流量补燃循环（FFSC）/);
  assert.match(html,/富氧预燃燃气驱动氧侧涡轮.*液氧主泵/);
  assert.match(html,/富燃预燃燃气驱动燃料侧涡轮.*甲烷主泵/);
  assert.match(html,/进入主燃烧室混合并完成燃烧/);
  assert.match(html,/尚不支持切换循环形式或自由装配部件拓扑/);
  assert.doesNotMatch(html,/<(?:input|select|button|textarea)\b|data-input=|确认并保存|运行设计点/);
  assert.deepEqual(inputs,before);
});

test("configuration identity preserves unsupported or absent historical values",()=>{
  const groups=[{id:"cycle",title:"发动机构型与循环原理",cycles:["ffsc"],propellants:["lox_methane"],fields:[
    {key:"cycle_type",default:"ffsc",read_only:true,cycles:["ffsc"],propellants:["lox_methane"]},
    {key:"propellant_pair",default:"lox_methane",read_only:true,cycles:["ffsc"],propellants:["lox_methane"]},
  ]}];
  for(const inputs of [{}, {cycle_type:"gg",propellant_pair:"lox_hydrogen"},
    {cycle_type:"ffsc"}, {cycle_type:"gg",propellant_pair:"lox_methane"}]) {
    const before=structuredClone(inputs),visible=parameterGroups(groups,inputs);
    assert.deepEqual(visible.map(group=>group.id),["cycle"]);
    assert.equal(visible[0].fields.length,2);
    const html=renderParameterGroups(visible,inputs);
    assert.match(html,/构型信息待核对/);
    assert.doesNotMatch(html,/id="configuration-shafts"|id="configuration-gas-path"/);
    assert.deepEqual(inputs,before);
  }
  const empty=renderParameterGroups(groups,{});
  assert.equal((empty.match(/>未记录<\/output>/g)||[]).length,2);
  assert.doesNotMatch(empty,/全流量补燃|液氧／甲烷/);
  const unsupported=renderParameterGroups(groups,{cycle_type:"gg",propellant_pair:"lox_hydrogen"});
  assert.match(unsupported,/>gg<\/output>/);
  assert.match(unsupported,/>lox_hydrogen<\/output>/);
  const untrusted=renderParameterGroups(groups,{cycle_type:'<script>alert("x")</script>',propellant_pair:"lox_methane"});
  assert.doesNotMatch(untrusted,/<script>/);
  assert.match(untrusted,/&lt;script&gt;/);
});
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

test("new custom constraints stay unset while saved legacy snapshots retain their original active limits",()=>{
  const defaults={ox_preburner_temp_limit_status:"unset",fuel_preburner_temp_limit_status:"unset",ox_preburner_temp_limit:800};
  assert.equal(mergeInputDefaults(defaults,{}).ox_preburner_temp_limit_status,"unset");
  const old={ox_preburner_temp_limit:812};
  const restored=mergeInputDefaults(defaults,old,true);
  assert.equal(restored.ox_preburner_temp_limit_status,"provisional");
  assert.equal(restored.fuel_preburner_temp_limit_status,"provisional");
  assert.equal(restored.ox_preburner_temp_limit,812);
  assert.deepEqual(old,{ox_preburner_temp_limit:812});
  for(const status of ["unset","documented","provisional"])
    assert.equal(mergeInputDefaults(defaults,{...old,ox_preburner_temp_limit_status:status},true).ox_preburner_temp_limit_status,status);
});

test("temperature limits remain visible while source details fold without losing values",()=>{
  const field={key:"ox_preburner_temp_limit_basis",label:"约束来源与适用工况",default:"",multiline:true,help:"来源说明"};
  const group={id:"preburner_design",title:"预燃室热力匹配条件",compact:true,section_notes:true,fields:[field],sections:[
    {id:"limits",title:"出口燃气温度限值与依据",keys:[field.key],field_groups:[{title:"富氧预燃室",side:"ox",keys:[field.key]}]},
  ]};
  const inputs=Object.freeze({[field.key]:"已保存依据 <试验>"});
  const html=renderParameterGroups([group],inputs);
  assert.doesNotMatch(html,/data-preburner-interfaces|data-preburner-results/);
  assert.match(html,/<section[^>]*data-parameter-section="limits"/);
  assert.doesNotMatch(html,/<details[^>]*data-parameter-section="limits"/);
  assert.match(html,/<details[^>]*data-parameter-fold="preburner_design:ox:basis"[^>]*>/);
  assert.doesNotMatch(html,/<details[^>]*data-parameter-fold="preburner_design:ox:basis"[^>]* open/);
  assert.match(html,/<textarea rows="2"[^>]*>已保存依据 &lt;试验&gt;<\/textarea>/);
  assert.equal(html.split(`data-input="${field.key}"`).length,2);
  assert.doesNotMatch(html,new RegExp(`(?:id|aria-describedby)="parameter-help-${field.key}"`));
  assert.match(html,/data-help-chapter="parameter:preburner_design"/);
  const expanded=renderParameterGroups([group],inputs,new Set(["preburner_design:ox:basis"]));
  assert.match(expanded,/<details[^>]*data-parameter-fold="preburner_design:ox:basis"[^>]* open/);
  const missing=renderParameterGroups([group],{[field.key]:"",ox_preburner_temp_limit_status:"documented"});
  assert.match(missing,/<details[^>]*data-parameter-fold="preburner_design:ox:basis"[^>]* open/);
  assert.match(missing,/（必填）/);
});

test("an unset preburner limit keeps its assessment guidance and preserves inactive values",()=>{
  const status={key:"fuel_preburner_temp_limit_status",label:"限值状态",default:"unset",
    choices:[{value:"unset",label:"未设置"},{value:"provisional",label:"设计暂定"}]};
  const visibility={all:[{key:status.key,op:"eq",value:"provisional"}]};
  const fields=[status,{key:"fuel_preburner_temp_limit",label:"温度上限",visibility},
    {key:"fuel_preburner_temp_limit_basis",label:"依据",multiline:true,visibility}];
  const group={id:"preburner_design",fields,sections:[{id:"limits",title:"出口燃气温度限值与依据",
    keys:fields.map(field=>field.key),field_groups:[{title:"富燃预燃室",side:"fuel",keys:fields.map(field=>field.key)}]}]};
  const inputs=Object.freeze({fuel_preburner_temp_limit_status:"unset",fuel_preburner_temp_limit:900,fuel_preburner_temp_limit_basis:"保留依据"});
  const html=renderParameterGroups(parameterGroups([group],inputs),inputs);
  assert.match(html,/data-preburner-limit-side="fuel"/);
  assert.match(html,/未建立温度上限：可进行循环求解，该项工程校核为未评估/);
  assert.doesNotMatch(html,/data-input="fuel_preburner_temp_limit"|data-input="fuel_preburner_temp_limit_basis"/);
  const restored={...inputs,fuel_preburner_temp_limit_status:"provisional"};
  const restoredHtml=renderParameterGroups(parameterGroups([group],restored),restored);
  assert.match(restoredHtml,/value="900"/);
  assert.match(restoredHtml,/>保留依据<\/textarea>/);
});

test("numerical initialization is visible without nested folding and retains compact accessible explanations",()=>{
  const inputs=Object.freeze({...base,chamber_temp_guess:3200,chamber_gamma:1.12,molar_mass:18});
  const seeds=["chamber_temp_guess","chamber_gamma","molar_mass"].map(key=>({key,label:key,
    default:inputs[key],role:"initial_guess",help:"首轮估计；最终由焓与组成计算",
    visibility:{all:[{key:"cooling_bartz_mode",op:"gt",value:0}]}}));
  const group={id:"solver",title:"数值求解",compact:true,initials_inline:true,
    initials_title:"燃气状态启动估计",initials_help:"影响收敛过程",fields:seeds};
  const html=renderParameterGroups(parameterGroups([group],inputs),inputs);
  assert.match(html,/<section class="parameter-subsection parameter-initials">/);
  assert.match(html,/燃气状态启动估计/);
  assert.doesNotMatch(html,/data-initials="solver"|高级设置 · 迭代初值/);
  for(const key of seeds.map(f=>f.key)){
    assert.match(html,new RegExp(`data-input="${key}"`));
    assert.doesNotMatch(html,new RegExp(`(?:id|aria-describedby)="parameter-help-${key}"`));
  }
  assert.match(html,/data-help-chapter="parameter:solver"/);
  assert.match(renderParameterReference(group,inputs),/首轮估计；最终由焓与组成计算/);
  assert.deepEqual(parameterGroups([group],{...inputs,cooling_bartz_mode:0}),[]);
  assert.equal(inputs.chamber_temp_guess,3200);
});

test("cooling model is read-only for routine designs and budget conversion is explicit",()=>{
  const group=[{id:"cooling",title:"再生冷却模型",fields:[{key:"cooling_bartz_mode",label:"当前冷却模型",read_only:true}]}];
  const modern=Object.freeze({...base,propellant_pair:"lox_methane"});
  const html=renderParameterGroups(group,modern);
  for(const method of ["Bartz", "灰体辐射", "导热率积分", "肋片效率", "Dittus–Boelter", "CoolProp", "Darcy", "一维动量与能量"])
    assert.ok(html.includes(method),method);
  assert.match(html,/循环求解过程中反复更新/);
  assert.match(html,/计算结果 → 再生冷却/);
  assert.match(html,/class="cooling-methods-field"/);
  for(const id of ["chamber_contour","chamber_channels","wall_thermal"])
    assert.match(html,new RegExp(`data-readiness-group="${id}"`));
  assert.doesNotMatch(html,/data-input="cooling_bartz_mode"|<select|data-upgrade-cooling/);
  const old=Object.freeze({...modern,cooling_bartz_mode:0,cooling_heat_load:17e6,cooling_dp_pa:2e6});
  const legacy=renderParameterGroups(group,old);
  assert.match(legacy,/历史兼容/);assert.match(legacy,/data-upgrade-cooling/);
  assert.doesNotMatch(legacy,/class="cooling-methods"|Dittus–Boelter|循环计算中的冷却反馈/);
  const upgraded=upgradeCoolingModelDraft(old);
  assert.deepEqual(upgraded,{...old,cooling_bartz_mode:1});
  assert.equal(old.cooling_bartz_mode,0);
  assert.equal(upgradeCoolingModelDraft(modern),modern);
  const kerosene={...old,propellant_pair:"lox_kerosene"};
  assert.equal(upgradeCoolingModelDraft(kerosene),kerosene);
  assert.doesNotMatch(renderParameterGroups(group,kerosene),/data-upgrade-cooling/);
  assert.match(renderParameterGroups(group,kerosene),/尚未接入/);
  for(const invalid of [null,undefined,NaN,-1]){
    const view=renderParameterGroups(group,{...modern,cooling_bartz_mode:invalid});
    assert.match(view,/冷却模型标识缺失或无效/);
    assert.doesNotMatch(view,/Dittus–Boelter/);
  }
  const ffsc=renderParameterGroups(group,{...modern,cycle_type:"ffsc"});
  assert.match(ffsc,/全部燃料先流经再生冷却通道/);
  assert.doesNotMatch(renderParameterGroups(group,{...modern,cycle_type:"gg"}),/两个预燃室|全部燃料先流经/);
});

test("read-only thermochemistry describes the actual loss and cooling models",()=>{
  const catalog=[{id:"gri30_highT",file:"gri30_highT.yaml",species_count:53,
    common_temperature_range_K:[300,5000],gas_eos:"ideal-gas",cantera_version:"3.2.0",
    source_label:"Cantera 官方随附数据",source_url:"https://example.test/cantera",
    file_description:"Joseph Shepherd NASA TM-4513"}];
  const modern={...base,methane_thermo_dataset:"gri30_highT",combustion_loss_model:"adiabatic"};
  const view=methaneThermoSummary(modern,catalog);
  assert.match(view.summary,/化学平衡（给定压力与焓）/);assert.match(view.summary,/冻结组成 · 沿程冷却/);
  assert.match(view.summary,/理想气体混合物/);assert.match(view.details,/300–5000 K/);
  assert.match(view.details,/HEOS/);assert.match(view.details,/REFPROP 对照/);
  assert.doesNotMatch(view.summary,/<select|data-input/);assert.equal(view.historical,false);
  const budget=methaneThermoSummary({...modern,cooling_bartz_mode:0},catalog);
  assert.match(budget.summary,/计入给定冷却吸热/);assert.match(budget.summary,/绝热膨胀/);
  const old=methaneThermoSummary({...modern,methane_thermo_dataset:"gri30",combustion_loss_model:"legacy_temperature_rise"},catalog);
  assert.match(old.summary,/化学平衡后温升折减/);assert.match(old.details,/不会自动改变该模型/);
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
    {name:"thrust",value_si:2451662.5,classification:"published",source_url:"https://www.spacex.com/updates",source_title:"厂商原始说明",source_locator:"推力指标段",scope:"仅支持所述推力",source_date:"2026-05-12",checked_on:"2026-10-23",note:"250 tf"},
    {name:"pc",value_si:33e6,classification:"engineering_assumption",source_url:"javascript:alert(1)",reviewed_on:"2026-11-05",source_path:"src/engine_presets.py:42",note:"<img src=x>"},
    {name:"target_isp",value_si:327,classification:"numerical_initial_guess",note:"用于流量初值"},
  ],[{key:"thrust",label:"目标推力",factor:.001,unit:"kN"},{key:"pc",label:"室压",factor:1e-6,unit:"MPa"}]);
  assert.match(html,/2,451\.6625 kN/);
  assert.match(html,/33 MPa/);
  assert.match(html,/厂商公开值/);
  assert.match(html,/工程假设/);
  assert.match(html,/数值初值/);
  assert.match(html,/发布 2026-05-12 · 资料核验 2026-10-23/);
  assert.match(html,/>厂商原始说明<\/a>/);
  assert.match(html,/来源定位：推力指标段/);assert.match(html,/支持范围：仅支持所述推力/);
  assert.match(html,/项目设定 · 未登记外部数值依据/);assert.match(html,/数值初始化设置/);
  assert.match(html,/说明更新 2026-11-05/);assert.doesNotMatch(html,/核对 2026-11-05/);
  assert.match(html,/本地代码位置：<code>src\/engine_presets\.py:42<\/code>/);
  assert.doesNotMatch(html,/href="[^"]*engine_presets|原始来源<\/a>/);
  assert.match(html,/href="https:\/\/www.spacex.com\/updates"/);
  assert.match(html,/&lt;img src=x&gt;/);
  assert.doesNotMatch(html,/javascript:|<img/);
  assert.equal(renderParameterEvidence(undefined),"");
});

test("historical source scope and display-only overrides cannot become manufacturer claims or alter stored inputs",()=>{
  const evidence=[{name:"of_ratio",value_si:3.6,value_display:"3.60（2019 参考基准）",classification:"historical_reference",
    source_title:"Sierra 原始报告",source_url:"https://www.faa.gov/report.pdf",source_locator:"第 1 页 §2",
    scope:"2019 海平面基准；不能证明 Raptor 3 实机值",checked_on:"2026-11-05",group:"历史型号参考",
    verification_note:"检索文本核验；原文直连超时"},
    {name:"seed",value_si:999,value_display:0,classification:"numerical_initial_guess",reviewed_on:"2026-11-05"}];
  const before=structuredClone(evidence),html=renderParameterEvidence(evidence,[{key:"of_ratio",label:"整机氧燃质量比",unit:"—"},{key:"seed",unit:"K"}]);
  for(const text of ["历史型号参考 · 本示例采用","3.60（2019 参考基准）","第 1 页 §2","不能证明 Raptor 3 实机值","检索文本核验；原文直连超时","分组：历史型号参考"])
    assert(html.includes(text),text);
  assert.doesNotMatch(html,/厂商公开值|参考基准） —|>0 K</);
  assert.match(html,/<td>0<\/td>/);assert.deepEqual(evidence,before);
});

test("new evidence metadata remains text and only valid credential-free HTTPS sources become links",()=>{
  const attack='<img src=x onerror="bad()">',evidence=[{name:attack,value_si:1,value_display:attack,classification:"engineering_assumption",
    source_url:"https://user:secret@example.com/report",source_title:attack,source_locator:attack,scope:attack,reviewed_on:attack,group:attack,verification_note:attack,source_path:attack},
    {name:"bad_scheme",classification:"published",source_url:"javascript:alert(1)",source_title:"不可链接"},
    {name:"broken_https",classification:"published",source_url:"https://",source_title:"来源不完整"}];
  const html=renderParameterEvidence(evidence);
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|<script| onerror="bad|href=|secret|javascript:/);
  assert.match(html,/项目设定 · 未登记外部数值依据/);
});

test("chamber model reports only active legacy correction as read-only without changing inputs",()=>{
  const group={id:"chamber_chemistry",title:"主燃烧室热力学模型",advanced_title:"历史兼容设置",fields:[
    {key:"combustion_loss_model",label:"主燃烧室经验温升修正",advanced:true,choices:[{value:"adiabatic",label:"不附加经验修正"},{value:"legacy_temperature_rise",label:"温升折减（历史兼容）"}]},
    {key:"combustion_efficiency",label:"历史温升折减系数",advanced:true,default:1,visibility:{all:[{key:"combustion_loss_model",op:"eq",value:"legacy_temperature_rise"}]}}
  ]};
  const draw=inputs=>renderParameterGroups(parameterGroups([group],inputs),inputs);
  const inputs={...base,combustion_loss_model:"adiabatic",combustion_efficiency:.98};
  const before=structuredClone(inputs),html=draw(inputs);
  assert.match(html,/化学平衡（压力与焓约束）/);
  assert.match(html,/涡轮：冻结入口组成/);
  assert.match(html,/data-help-chapter="properties"/);
  assert.doesNotMatch(html,/压力与焓约束说明|当前计算由 Cantera 执行|高级设置|NASA 化学平衡计算程序说明/);
  assert.doesNotMatch(html,/<select|<input|<details|data-input=|旧版本温度修正|温升折减系数/);
  const saved=Object.freeze({...inputs,combustion_loss_model:"legacy_temperature_rise"});
  const legacy=draw(saved);
  assert.match(legacy,/旧版本温度修正/);
  assert.match(legacy,/<dt>原温升折减系数<\/dt><dd data-field="combustion_efficiency">0.98<\/dd>/);
  assert.match(legacy,/当前保存配置与所选模型不匹配，不能提交设计点/);
  assert.match(legacy,/data-thermochemistry-apply="ideal-gas"/);
  assert.doesNotMatch(legacy,/<select|<input|<details|data-input=/);
  assert.deepEqual(saved,{...inputs,combustion_loss_model:"legacy_temperature_rise"});
  assert.doesNotMatch(draw({...inputs,cycle_type:"gg"}),/两股涡轮排气/);
  const invalid=draw({...inputs,combustion_loss_model:"invalid"});
  assert.match(invalid,/<dt>温度修正设置<\/dt><dd data-field="combustion_loss_model">未识别：invalid<\/dd>/);
  assert.match(invalid,/计算模型设置无效/);
  assert.match(invalid,/data-thermochemistry-apply="ideal-gas"/);
  assert.equal(parameterLayoutDependency("combustion_loss_model",[group]),true);
  assert.deepEqual(inputs,before);
});

test("adopting the thermochemistry baseline explicitly updates model identity and preserves engineering inputs",()=>{
  const inputs=Object.freeze({...base,gas_eos:"Peng-Robinson-Boston-Mathias",gas_species_policy:"pr_supported_v1",
    methane_thermo_dataset:"gri30",combustion_loss_model:"legacy_temperature_rise",combustion_efficiency:.97,
    pc:33e6,ox_preburner_temp_target:750,fuel_preburner_temp_target:850,target_isp:327});
  const before=structuredClone(inputs),changed=applyThermochemistryBaseline(inputs);
  assert.deepEqual(changed,{...before,gas_eos:"ideal-gas",gas_species_policy:"full",methane_thermo_dataset:"gri30_highT",combustion_loss_model:"adiabatic"});
  assert.deepEqual(inputs,before);
  assert.equal(applyThermochemistryBaseline(changed),changed);
  // A restricted ideal phase also needs an explicit species change, even though its EOS is unchanged.
  assert.equal(applyThermochemistryBaseline({...changed,gas_species_policy:"pr_supported_v1"}).gas_species_policy,"full");
  for(const unsupported of [{...inputs,cycle_type:"gg"},{...inputs,propellant_pair:"lox_hydrogen"},{...inputs,propellant_pair:null}])
    assert.equal(applyThermochemistryBaseline(unsupported),unsupported);
});

test("draft model summaries distinguish species restriction, historical scope and invalid configurations",()=>{
  const baseline=Object.freeze({...base,gas_eos:"ideal-gas",gas_species_policy:"auto",methane_thermo_dataset:"gri30_highT",combustion_loss_model:"adiabatic"});
  const normal=thermoModelDecisionState(baseline);
  assert.equal(normal.baseline,true);assert.equal(normal.restricted,false);assert.equal(normal.speciesCount,53);
  assert.equal(normal.routineSupported,true);assert.equal(normal.runnableSupported,true);
  const oldDataset=thermoModelDecisionState({...baseline,methane_thermo_dataset:"gri30"});
  assert.equal(oldDataset.routineSupported,true);assert.equal(oldDataset.runnableSupported,true);assert.equal(oldDataset.baseline,false);
  for(const eos of ["Peng-Robinson","Peng-Robinson-Boston-Mathias"]){
    const saved=Object.freeze({...baseline,gas_eos:eos}),before=structuredClone(saved);
    const view=thermoModelDecisionState(saved);
    assert.equal(view.baseline,false);assert.equal(view.speciesCount,16);
    assert.equal(view.routineSupported,false);assert.equal(view.runnableSupported,true);
    assert.ok(view.limitations.some(text=>text.includes("H、O、OH")));
    assert.ok(view.limitations.some(text=>text.includes("增加计算量")));
    assert.equal(view.stations.find(station=>station.id==="main_chamber").eos,eos==="Peng-Robinson"?"理想气体混合物":"真实气体（PR–BM）");
    assert.deepEqual(saved,before);
    const missing=thermoModelDecisionState({...saved,gas_species_policy:"full"});
    assert.equal(missing.unsupportedFull,true);assert.equal(missing.runnableSupported,false);assert.ok(missing.limitations.some(text=>text.includes("不可计算")));
  }
  const invalid=thermoModelDecisionState({...baseline,gas_eos:"bad",gas_species_policy:"bad",methane_thermo_dataset:"bad"});
  assert.equal(invalid.speciesCount,null);assert.equal(invalid.knownEos,false);assert.equal(invalid.baseline,false);
  assert.equal(invalid.routineSupported,false);
  assert.equal(invalid.limitations.length,3);
  for(const key of ["gas_eos","gas_species_policy","methane_thermo_dataset","combustion_loss_model"])
    for(const value of [null,"",undefined,"unknown","constructor"]){
      const settings=Object.freeze({...baseline,[key]:value}),before=structuredClone(settings);
      assert.equal(thermoModelDecisionState(settings).routineSupported,false,`${key}=${String(value)} must not inherit a valid model`);
      assert.equal(thermoModelDecisionState(settings).runnableSupported,false,`${key}=${String(value)} must not inherit a runnable model`);
      assert.deepEqual(settings,before);
    }
});

test("thermochemistry editor exposes only the model selector and keeps its configuration read-only",()=>{
  const groups=[{id:"chamber_chemistry",title:"燃烧热化学与燃气物性",fields:[
    {key:"gas_eos",label:"燃气物性模型",choices:[{value:"ideal-gas",label:"理想气体混合物（默认）"},{value:"Peng-Robinson",label:"Peng–Robinson（PR，预燃室与涡轮 · 研究模型）"},{value:"Peng-Robinson-Boston-Mathias",label:"Peng–Robinson–Boston–Mathias（PR–BM，全燃气流程 · 研究模型）"}]},
    {key:"gas_species_policy",label:"参与平衡计算的化学物种",default:"auto",choices:[{value:"auto",label:"旧版本自动选择"},{value:"full",label:"53 种"},{value:"pr_supported_v1",label:"16 种"}]},
    {key:"combustion_loss_model",label:"温度修正方式",choices:[{value:"adiabatic",label:"不作温升折减"},{value:"legacy_temperature_rise",label:"旧版本温升折减"}]},
  ]}];
  const baseline={...base,gas_eos:"ideal-gas",gas_species_policy:"auto",methane_thermo_dataset:"gri30_highT",combustion_loss_model:"adiabatic"};
  const plain=renderParameterGroups(groups,baseline);
  assert.match(plain,/当前计算模型/);
  assert.match(plain,/化学平衡 \+ 理想气体混合物/);
  assert.match(plain,/53 种气相物种（含 H、O、OH）/);
  assert.match(plain,/候选物种，实际含量由计算确定/);
  for(const key of ["gas_eos","gas_species_policy","combustion_loss_model"])
    assert.equal([...plain.matchAll(new RegExp(`data-field="${key}"`,"g"))].length,1,`${key} retains one finder anchor`);
  assert.deepEqual([...plain.matchAll(/data-input="([^"]+)"/g)].map(match=>match[1]),["gas_eos"]);
  assert.equal([...plain.matchAll(/<select\b/g)].length,1);
  assert.match(plain,/选择模型同时采用其物种配置，不附加温升折减；保存并重新计算后生效/);
  assert.match(plain,/aria-describedby="parameter-inline-help-gas_eos"/);
  assert.match(plain,/id="parameter-inline-help-gas_eos"/);
  assert.doesNotMatch(plain,/data-thermochemistry-baseline|data-thermochemistry-apply/);
  assert.doesNotMatch(plain,/草稿|基准方案|研究与历史方案|计算实现|校核状态/);
  assert.doesNotMatch(plain,/<input|<details|模型对照|旧版本温度修正/);
  const expanded=renderParameterGroups(groups,baseline,new Set(["chamber_chemistry-species"]));
  assert.equal(expanded,plain,"stale fold preferences must not restore removed controls");
  const oldData=Object.freeze({...baseline,methane_thermo_dataset:"gri30"});
  const oldDataView=renderParameterGroups(groups,oldData);
  assert.match(oldDataView,/GRI30 热化学数据/);
  assert.doesNotMatch(oldDataView,/data-thermochemistry-baseline|data-thermochemistry-apply|不能提交设计点/);
  assert.equal(oldData.methane_thermo_dataset,"gri30");
  for(const eos of ["Peng-Robinson","Peng-Robinson-Boston-Mathias"]){
    const saved=Object.freeze({...baseline,gas_eos:eos}),before=structuredClone(saved);
    const html=renderParameterGroups(groups,saved);
    assert.doesNotMatch(html,/data-thermochemistry-baseline|data-thermochemistry-apply|不能提交设计点/);
    assert.match(html,/<b>研究模型<\/b>/);
    assert.match(html,/求解较慢，高压混合物的预测尚未完成试验验证/);
    assert.match(html,/16 种气相物种（不含 H、O、OH）/);
    assert.deepEqual([...html.matchAll(/data-input="([^"]+)"/g)].map(match=>match[1]),["gas_eos"]);
    assert.doesNotMatch(html,/<input|<details/);
    assert.deepEqual(saved,before);
  }
  const invalid=renderParameterGroups(groups,{...baseline,gas_species_policy:'<img src=x onerror=bad>'});
  assert.doesNotMatch(invalid,/<img/);assert.match(invalid,/&lt;img/);
  assert.match(invalid,/计算模型设置无效，不能提交设计点/);
  assert.match(invalid,/data-thermochemistry-apply="ideal-gas"/);
  for(const key of ["gas_eos","gas_species_policy","methane_thermo_dataset","combustion_loss_model"]){
    const unknown=Object.freeze({...baseline,[key]:"unrecognized_model_value"}),before=structuredClone(unknown);
    const html=renderParameterGroups(groups,unknown);
    assert.match(html,/unrecognized_model_value/);
    assert.match(html,/不能提交设计点/);
    if(["gas_eos","methane_thermo_dataset"].includes(key)){
      assert.match(html,/data-thermochemistry-baseline/);
      assert.match(html,/恢复默认模型配置/);
      assert.doesNotMatch(html,/data-thermochemistry-apply/);
    }else assert.match(html,/data-thermochemistry-apply="ideal-gas"/);
    assert.deepEqual([...html.matchAll(/data-input="([^"]+)"/g)].map(match=>match[1]),["gas_eos"]);
    assert.doesNotMatch(html,/<input|<details/);
    assert.deepEqual(unknown,before);
  }
});

test("PR research configurations remain runnable while mismatches require explicit model application",()=>{
  const groups=[{id:"chamber_chemistry",title:"燃烧热化学与燃气物性",fields:[
    {key:"gas_eos",label:"燃气物性模型",choices:[{value:"ideal-gas",label:"理想气体混合物"},{value:"Peng-Robinson",label:"Peng–Robinson"},{value:"Peng-Robinson-Boston-Mathias",label:"Peng–Robinson–Boston–Mathias"}]},
    {key:"gas_species_policy",label:"允许参与平衡的物种范围",default:"auto",choices:[{value:"full",label:"源文件完整集合"},{value:"pr_supported_v1",label:"受限16物种（不含H/O/OH）"},{value:"auto",label:"历史自动策略（沿用原方案）"}]},
  ]}];
  const baseline={...base,gas_eos:"ideal-gas",gas_species_policy:"full",methane_thermo_dataset:"gri30_highT",combustion_loss_model:"adiabatic"};
  const eosOnly={...baseline,gas_eos:"Peng-Robinson-Boston-Mathias"};
  const blocked=renderParameterGroups(groups,eosOnly);
  assert.match(blocked,/当前保存配置与所选模型不匹配，不能提交设计点/);
  assert.match(blocked,/PR 参数未覆盖完整物种范围/);
  assert.match(blocked,/53 种气相物种/);
  assert.match(blocked,/data-thermochemistry-apply="Peng-Robinson-Boston-Mathias"/);
  assert.match(blocked,/应用所选模型配置/);
  assert.match(blocked,/保留所选热化学数据/);
  assert.match(blocked,/确认并保存为新版本后重新计算/);
  assert.doesNotMatch(blocked,/<input|<details/);
  for(const eos of ["ideal-gas","Peng-Robinson","Peng-Robinson-Boston-Mathias"]){
    const explicit=Object.freeze({...eosOnly,gas_eos:eos,gas_species_policy:"pr_supported_v1"}),before=structuredClone(explicit);
    const restricted=renderParameterGroups(groups,explicit);
    if(eos==="ideal-gas"){
      assert.match(restricted,/不能提交设计点/);
      assert.match(restricted,/data-thermochemistry-apply="ideal-gas"/);
    }else{
      assert.doesNotMatch(restricted,/不能提交设计点|data-thermochemistry-apply|data-thermochemistry-baseline/);
      assert.match(restricted,/<b>研究模型<\/b>/);
    }
    assert.match(restricted,/16 种气相物种（不含 H、O、OH）/);
    assert.deepEqual([...restricted.matchAll(/data-input="([^"]+)"/g)].map(match=>match[1]),["gas_eos"]);
    assert.doesNotMatch(restricted,/<input|<details/);
    assert.deepEqual(explicit,before);
  }
  const historical=Object.freeze({...eosOnly,gas_species_policy:"auto"}),before=structuredClone(historical);
  const original=renderParameterGroups(groups,historical);
  assert.match(original,/16 种气相物种/);
  assert.match(original,/<option value="Peng-Robinson-Boston-Mathias" selected>/);
  assert.doesNotMatch(original,/不能提交设计点|data-thermochemistry-apply|data-thermochemistry-baseline/);
  assert.deepEqual(historical,before);
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
  assert.equal(mergeInputDefaults(defaults,{chamber_inner_wall_material:"grcop42"},true).wall_thermal_dataset_id,"grcop42_chen2023_lpbf_v1");
  assert.equal(mergeInputDefaults(defaults,{chamber_inner_wall_material:"cucrzr"},true).wall_thermal_dataset_id,"");
  const selected={chamber_inner_wall_material:"grcop42",wall_thermal_dataset_id:"cuzr_qi2024_heat_treated_v1"};
  assert.equal(mergeInputDefaults(defaults,selected,true).wall_thermal_dataset_id,selected.wall_thermal_dataset_id);
  const field={key:"wall_thermal_dataset_id",label:"数据集",default:defaults.wall_thermal_dataset_id,
    empty_choice_label:"请选择有来源的热物性数据集（历史材料无对应曲线）",choices:[{value:defaults.wall_thermal_dataset_id,label:"GRCop-84"}]};
  const html=renderParameterGroups([{id:"wall",fields:[field]}],{wall_thermal_dataset_id:""});
  assert.match(html,/value="" disabled selected>请选择有来源/);
  assert.doesNotMatch(html,/value="grcop84_ellis2000_extruded_high_fe_v1" selected/);
});

test("wall material mismatch points to the same editable group without changing saved choices",()=>{
  const group={id:"wall_thermal",fields:[
    {key:"chamber_inner_wall_material",label:"燃气侧内衬材料",default:"grcop42",type:"select",
      choices:[{value:"grcop84",label:"GRCop-84"},{value:"grcop42",label:"GRCop-42"}]},
    {key:"wall_thermal_dataset_id",label:"数据集",default:"d42",type:"select",
      choices:[{value:"d42",label:"GRCop-42 数据"}],dataset_catalog:[{dataset_id:"d42",material_id:"grcop42"}]}]};
  const inputs={chamber_inner_wall_material:"grcop84",wall_thermal_dataset_id:"d42",wall_manufacturing_state:"unspecified",chamber_outer_wall_material:"i625"};
  const before=structuredClone(inputs),html=renderParameterGroups([group],inputs);
  assert.match(html,/<select data-input="chamber_inner_wall_material"/);
  assert.match(html,/value="grcop84" selected/);
  assert.match(html,/请在本组的“燃气侧内衬材料”选择对应材料/);
  assert.doesNotMatch(html,/推力室·结构与材料/);
  assert.deepEqual(inputs,before);
  const corrected=renderParameterGroups([group],{...inputs,chamber_inner_wall_material:"grcop42"});
  assert.doesNotMatch(corrected,/内衬材料与默认热物性数据集不一致/);
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
