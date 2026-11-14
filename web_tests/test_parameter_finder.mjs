import assert from "node:assert/strict";
import test from "node:test";
import {findDesignParameters, renderParameterMatches} from "../webapp/static/parameter-finder.js";

const inputs = Object.freeze({cycle_type:"ffsc",propellant_pair:"lox_methane",use_target_preburner_temp:1,use_target_preburner_mr:1});
const field = (key, label, extra={}) => ({key,label,role:"design_input",...extra});

test("blank lookup returns no results and does not mutate the schema or inputs",()=>{
  const groups=Object.freeze([Object.freeze({id:"design",title:"设计目标与工况",fields:Object.freeze([Object.freeze(field("thrust","目标推力"))])})]);
  for (const query of ["", "  \n\t ", null, undefined]) assert.deepEqual(findDesignParameters(groups,inputs,query),[]);
  assert.equal(findDesignParameters(groups,inputs,"推力")[0].fieldKey,"thrust");
  assert.equal(groups[0].fields[0].label,"目标推力");
  assert.deepEqual(Object.keys(inputs),["cycle_type","propellant_pair","use_target_preburner_temp","use_target_preburner_mr"]);
});

test("scope excludes unsupported cycles, propellants, hidden fields and inactive mode values",()=>{
  const groups=[
    {id:"unsupported",title:"旧循环",cycles:["gg"],fields:[field("old","压力")]},
    {id:"inlet",title:"推进剂入口",fields:[
      field("methane","入口压力",{propellants:["lox_methane"]}),
      field("hydrogen","入口压力",{propellants:["lox_hydrogen"]}),
      field("hidden","压力",{hidden:true}),
      field("other_cycle","压力",{cycles:["gg"]}),
    ]},
    {id:"preburner_design",title:"预燃室",fields:[
      field("temperature","温度模式压力",{visibility:{preburner_modes:["temperature"]}}),
      field("ratio","混合比模式压力",{visibility:{preburner_modes:["mixture_ratio"]}}),
      field("inactive","压力说明",{visibility:{all:[{key:"enable",op:"eq",value:1}]}}),
    ]},
  ];
  assert.deepEqual(findDesignParameters(groups,inputs,"压力").map(match=>match.fieldKey),["methane","temperature"]);
  assert.deepEqual(findDesignParameters(groups,{...inputs,use_target_preburner_temp:0},"压力").map(match=>match.fieldKey),["methane","ratio"]);
});

test("read-only descriptions and virtual controls are discoverable with their current labels",()=>{
  const groups=[{id:"preburner_design",title:"预燃室设计",fields:[
    field("preburner_design_mode","预燃室设计模式",{virtual:true}),
    field("model","燃气模型说明",{read_only:true,help:"平衡计算假设"}),
    field("ratio","混合比",{labels_by_mode:{temperature:"混合比初值",mixture_ratio:"混合比目标"}}),
  ]}];
  assert.equal(findDesignParameters(groups,inputs,"模式")[0].fieldKey,"preburner_design_mode");
  assert.equal(findDesignParameters(groups,inputs,"平衡计算")[0].fieldKey,"model");
  assert.equal(findDesignParameters(groups,inputs,"混合比初值")[0].label,"混合比初值");
  assert.deepEqual(findDesignParameters(groups,{...inputs,use_target_preburner_temp:0},"混合比初值"),[]);
  assert.equal(findDesignParameters(groups,{...inputs,use_target_preburner_temp:0},"混合比目标")[0].label,"混合比目标");
});

test("name exact and prefix matches lead text matches with stable ordering",()=>{
  const groups=[{id:"design",title:"设计",fields:[
    field("help_only","室压",{help:"目标推力相关计算"}),
    field("contains","真空目标推力"),
    field("prefix_a","目标推力设置"),
    field("exact","目标推力"),
    field("prefix_b","目标推力参考"),
    field("other","无关字段"),
  ]}];
  assert.deepEqual(findDesignParameters(groups,inputs,"目标推力").map(match=>match.fieldKey),["exact","prefix_a","prefix_b","contains","help_only"]);
  assert.deepEqual(findDesignParameters(groups,inputs,"目标 推力").map(match=>match.fieldKey),["exact","prefix_a","prefix_b","contains","help_only"]);
});

test("multiword queries use AND matching and units support names case-insensitively",()=>{
  const groups=[{id:"design",title:"设计目标与工况",fields:[
    field("pc","燃烧室压力",{unit:"MPa"}),
    field("ambient","环境压力",{unit:"MPa"}),
    field("temperature","燃烧室温度",{unit:"K"}),
    field("misc","辅助量",{help:"压力说明但无单位"}),
  ]}];
  assert.deepEqual(findDesignParameters(groups,inputs,"燃烧室 mpa").map(match=>match.fieldKey),["pc"]);
  assert.deepEqual(findDesignParameters(groups,inputs,"压力 MPa").map(match=>match.fieldKey),["pc","ambient"]);
  assert.deepEqual(findDesignParameters(groups,inputs,"ＭＰａ").map(match=>match.fieldKey),["pc","ambient"]);
  assert.deepEqual(findDesignParameters(groups,inputs,"压力 K"),[]);
});

test("results carry current setup paths and only the public lookup fields",()=>{
  const groups=[
    {id:"design",title:"设计目标与工况",fields:[field("thrust","目标推力",{unit:"kN"})]},
    {id:"component_efficiencies",title:"效率假设",fields:[field("eta","泵效率")]},
    {id:"solver",title:"数值求解",fields:[field("tolerance","残差容差")]},
    {id:"future_component",title:"新增部件",fields:[field("future","未来参数")]},
  ];
  assert.deepEqual(findDesignParameters(groups,inputs,"推力"),[{groupId:"design",fieldKey:"thrust",label:"目标推力",unit:"kN",path:"总体方案与循环设计 / 设计目标与工况"}]);
  assert.equal(findDesignParameters(groups,inputs,"泵效率")[0].path,"总体方案与循环设计 / 总体模型与假设 / 效率假设");
  assert.equal(findDesignParameters(groups,inputs,"容差")[0].path,"总体方案与循环设计 / 总体设计点计算 / 数值求解");
  assert.equal(findDesignParameters(groups,inputs,"未来")[0].path,"总体方案与循环设计 / 总体模型与假设 / 新增部件");
});

test("rendering gives inert buttons with escaped labels, units, paths and destinations",()=>{
  const unsafe='<script>alert("x")</script>&\'';
  const matches=Object.freeze([Object.freeze({groupId:unsafe,fieldKey:unsafe,label:unsafe,unit:unsafe,path:unsafe})]);
  const html=renderParameterMatches(matches,"x");
  assert.match(html,/<ul class="parameter-matches"><li><button type="button"/);
  assert.match(html,/data-parameter-match-group="&lt;script&gt;/);
  assert.match(html,/data-parameter-match-field="&lt;script&gt;/);
  assert.match(html,/class="parameter-match-label"/);
  assert.match(html,/class="parameter-match-unit"/);
  assert.match(html,/class="parameter-match-path"/);
  assert.match(html,/&lt;script&gt;alert\(&quot;x&quot;\)&lt;\/script&gt;&amp;&#39;/);
  assert.doesNotMatch(html,/<script|onclick=|role="(?:option|listbox)"|<form/);
  assert.equal(matches[0].groupId,unsafe);
});

test("empty and unmatched searches have clear feedback and never reflect executable query text",()=>{
  assert.match(renderParameterMatches([],"  "),/输入参数名称，查找当前方案中的设置。/);
  const html=renderParameterMatches([],'<img src=x onerror="alert(1)">');
  assert.match(html,/没有找到/);
  assert.match(html,/&lt;img/);
  assert.doesNotMatch(html,/<img|<button/);
  const unitless=renderParameterMatches([{groupId:"solver",fieldKey:"max_iter",label:"迭代次数",unit:"",path:"总体方案与循环设计 / 循环匹配与求解设置 / 数值求解"}],"迭代");
  assert.doesNotMatch(unitless,/parameter-match-unit|\[\]/);
});
