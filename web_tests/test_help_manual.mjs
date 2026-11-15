import assert from "node:assert/strict";
import test from "node:test";
import {helpChapterForGroup, normalizeHelpChapter, renderHelpManual, searchHelpManual} from "../webapp/static/help-manual.js";

const inputs={cycle_type:"ffsc",propellant_pair:"lox_methane",use_target_preburner_temp:1,use_target_preburner_mr:1};
const groups=[{id:"system",title:"设计目标与工况",fields:[
  {key:"ambient_pressure",label:"环境压力",unit:"MPa",default:101325,type:"number",factor:1e-6,help:"环境压力按绝对压力输入。",provenance:{label:"方案边界",detail:"设计任务书"}},
  {key:"isp_guess",label:"比冲初值",unit:"s",default:327,type:"number",help_sections:[{label:"计算作用",text:"用于初始化推进剂总质量流量，不作为性能要求。"}]},
]},{id:"solver",title:"数值求解",fields:[{key:"tolerance",label:"收敛准则",default:1e-6,type:"number",help:"检查迭代残差。"}]}];
const catalog={principles:["初值只用于启动迭代。"],rows:[{id:"pressure",label:"主燃烧室压力",concept_design:{description:"给定设计输入。"},fixed_hardware_steady:{description:"工作点计算输出。"}}]};

test("chapters normalize unknown or removed parameter locations without trusting HTML",()=>{
  assert.equal(helpChapterForGroup("system"),"parameter:system");
  assert.equal(helpChapterForGroup(""),"parameters");
  assert.equal(normalizeHelpChapter("parameter:system",groups),"parameter:system");
  assert.equal(normalizeHelpChapter("parameter:removed",groups),"workflow");
  assert.equal(normalizeHelpChapter("<script>",groups),"workflow");
  assert.equal(normalizeHelpChapter("modes",[]),"modes");
  assert.doesNotThrow(()=>renderHelpManual({groups:null,inputs:null}));
});

test("parameter references are read only and retain definitions, units and provenance",()=>{
  const html=renderHelpManual({chapter:"parameter:system",groups,inputs,catalog});
  assert.match(html,/id="help-article-title"[^>]*>设计目标与工况/);
  for(const text of ["环境压力","MPa","绝对压力","计算作用","初始化推进剂总质量流量","方案边界","设计任务书"])assert.ok(html.includes(text),text);
  assert.doesNotMatch(html,/data-input=|data-valve-action|data-cooling-channel-action|data-workspace-action|id="apply-preset"|type="submit"/);
  assert.equal((html.match(/<input\b/g)||[]).length,1,"only the documentation search is editable");
});

test("configuration help describes the first read-only entry and separates record sources",()=>{
  const configurationGroups=[{id:"cycle",title:"发动机构型与循环原理",fields:[]},...groups];
  const draft=Object.freeze({...inputs});
  const html=renderHelpManual({chapter:"parameter:cycle",groups:configurationGroups,inputs:draft});
  assert.match(html,/id="help-article-title"[^>]*>发动机构型与循环原理/);
  for(const text of ["总体方案与循环设计的首个入口","按方案只读显示","设计目标与环境边界在同页编辑","无需全局二维或三维设置","依据当前草稿","依据所选计算记录","不使用历史记录替代当前草稿"])assert.ok(html.includes(text),text);
  assert.doesNotMatch(html,/data-input=|data-workspace-action|type="submit"|实际取值、来源记录和确认状态请在参数设置中核对/);
  assert.equal((html.match(/<input\b/g)||[]).length,1);
  assert.ok(searchHelpManual("构型 轴系",{groups:configurationGroups,inputs:draft}).some(item=>item.id==="parameter:cycle"));
  const index=renderHelpManual({chapter:"parameters",groups:configurationGroups,inputs:draft});
  assert.match(index,/data-help-chapter="parameter:cycle">发动机构型与循环原理<\/button><span>只读构型说明/);
});

test("search covers parameter units, full explanation sections and chapter text",()=>{
  const search=query=>searchHelpManual(query,{groups,inputs,catalog});
  assert.ok(search("MPa 绝对压力").some(item=>item.id==="parameter:system"));
  assert.ok(search("初始化推进剂").some(item=>item.id==="parameter:system"));
  assert.ok(search("工作点计算输出").some(item=>item.id==="modes"));
  assert.ok(search("页面导航不自动提交").some(item=>item.id==="workflow"));
  assert.deepEqual(search("does-not-exist"),[]);
  assert.deepEqual(search("   "),[]);
  const html=renderHelpManual({query:"does-not-exist",groups,inputs,catalog});
  assert.match(html,/role="status">0 个章节/);
  assert.match(html,/未找到匹配章节/);
  assert.match(html,/data-help-clear>清除/);
});

test("documentation uses draft mode explanations and avoids a fabricated current mode",()=>{
  const temperature=renderHelpManual({chapter:"theory",groups,inputs,catalog});
  assert.match(temperature,/由当前概念设计草稿生成/);
  assert.match(temperature,/data-design-mode="temperature"/);
  const mixture=renderHelpManual({chapter:"theory",groups,inputs:{...inputs,use_target_preburner_temp:0},catalog});
  assert.match(mixture,/data-design-mode="mixture_ratio"/);
  const empty=renderHelpManual({chapter:"theory",groups,inputs:{},catalog});
  assert.match(empty,/当前没有适用的概念设计草稿/);
  assert.doesNotMatch(empty,/data-design-mode=/);
  const modes=renderHelpManual({chapter:"modes",groups,inputs,catalog});
  assert.match(modes,/概念设计 · 当前模式/);
  assert.match(modes,/当前安装版本的参数定义/);
  const noDraft=renderHelpManual({chapter:"modes",groups,inputs:{},catalog});
  assert.doesNotMatch(noDraft,/ · 当前模式/);
});

test("version labels describe parameter definitions rather than claiming a software version",()=>{
  const html=renderHelpManual({version:"参数定义 v3"});
  assert.match(html,/文档依据：参数定义 v3 · 随当前安装版本提供/);
  assert.doesNotMatch(html,/软件版本.*v3/);
  assert.match(renderHelpManual(),/文档依据：版本未登记/);
});

test("external schema and search content is escaped and duplicate groups are collapsed",()=>{
  const attack='<script>alert("x")</script>&';
  const malicious=[{id:attack,title:attack,fields:[{key:"x",label:attack,unit:attack,default:0,help:attack}]},{id:attack,title:"duplicate",fields:[]}];
  const html=renderHelpManual({chapter:helpChapterForGroup(attack),query:attack,groups:malicious,version:attack,returnLabel:attack});
  assert.doesNotMatch(html,/<script|<img|onclick=/);
  assert.match(html,/&lt;script&gt;/);
  assert.doesNotMatch(html,/duplicate/);
  assert.match(html,/id="help-search"[^>]*value="&lt;script&gt;/);
});

test("reading or searching cannot mutate the supplied draft or definitions",()=>{
  const draft=Object.freeze({...inputs}), reference=structuredClone(groups), roles=structuredClone(catalog);
  const before=JSON.stringify({draft,reference,roles});
  for(const chapter of ["workflow","theory","parameters","modes","examples","troubleshooting","parameter:system"])
    renderHelpManual({chapter,query:"初值",groups:reference,inputs:draft,catalog:roles});
  assert.equal(JSON.stringify({draft,reference,roles}),before);
});

test("missing model catalog is explicit and retains useful documentation navigation",()=>{
  const html=renderHelpManual({chapter:"modes",groups:[]});
  assert.match(html,/未提供分析模式参数对照数据/);
  assert.match(html,/data-help-chapter="workflow"/);
  assert.match(html,/data-help-chapter="parameters"/);
  assert.match(html,/data-help-return/);
});

test("thermochemistry reference separates physical assumptions and identifies the actual backend",()=>{
  const chemistryGroups=[...groups,{id:"chamber_chemistry",title:"燃烧热化学与燃气物性",fields:[]}];
  const draft=Object.freeze({...inputs,gas_eos:"Peng-Robinson-Boston-Mathias",methane_thermo_dataset:"gri30_highT"});
  const html=renderHelpManual({chapter:"parameter:chamber_chemistry",groups:chemistryGroups,inputs:draft});
  for(const text of ["残余 Helmholtz","Cantera 提供物种标准态热化学数据","两股涡轮排气的实际", "同一换热量不得", "16 种物种", "H、O、OH", "冻结主燃烧室组成", "3600 K 或 4000 K", "同一状态方程", "共同 16 物种", "数值实现校核", "试验", "点火延迟", "历史 auto 策略按原状态方程解释", "53 种候选物种", "并非每种物种在每个工况下都有非零含量", "与发动机部件或几何模型无关", "名称说明所用方法，不表示精度等级", "参数已确认", "结果需重算", "两项状态可同时存在", "设计模型决策", "可作为研究模型用于设计点计算", "保留所选热化学数据", "每次选择均取消旧版本温升折减", "不会自动保存", "已退出常规设计执行", "不是 PR 状态方程的理论物种数上限", "热力学校核 → 理想气体 / PR", "不替代本页的模型选择", "计算结果不能作为已验证性能", "遗漏高温解离物种可能导致平衡温度偏高", "不提供独立选择"])
    assert.ok(html.includes(text),text);
  assert.doesNotMatch(html,/总体设计基准方案|研究与历史方案|概念设计草稿|再打开|化学物种范围（模型对照）|采用理想气体与完整物种模型/);
  assert.doesNotMatch(html,/本页不提供逐项切换|采用当前设计模型|PR 系列不作为当前常规设计的可选配置|16 种范围仅用于历史记录与专门校核/);
  assert.doesNotMatch(html,/data-input=|data-workspace-action|data-thermo-action|data-use-thermo-baseline|type="submit"/);
  assert.equal((html.match(/<input\b/g)||[]).length,1,"only the documentation search is editable");
  assert.equal(draft.gas_eos,"Peng-Robinson-Boston-Mathias");
  assert.ok(searchHelpManual("4000",{groups:chemistryGroups,inputs:draft}).some(item=>item.id==="parameter:chamber_chemistry"));
  const ideal=renderHelpManual({chapter:"parameter:chamber_chemistry",groups:chemistryGroups,inputs:{...inputs,gas_eos:"ideal-gas"}});
  assert.match(ideal,/当前理想气体混合物由 Cantera/);
  const legacy=renderHelpManual({chapter:"parameter:chamber_chemistry",groups:chemistryGroups,inputs:{...inputs,gas_eos:"Peng-Robinson"}});
  assert.match(legacy,/预燃室采用 Cantera 原生 PR/);
  const unknown=renderHelpManual({chapter:"parameter:chamber_chemistry",groups:chemistryGroups,inputs:{...inputs,gas_eos:"unknown"}});
  assert.match(unknown,/未登记可识别的燃气物性模型/);
  assert.doesNotMatch(unknown,/当前理想气体混合物由 Cantera/);
});
