import assert from "node:assert/strict";
import test from "node:test";
import {mergeInputDefaults, savedInputDocument, preburnerMode, selectPreburnerMode, parameterVisible, parameterPresentation,
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
