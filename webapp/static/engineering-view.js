// Persistent concept-design companion. Draft topology and saved calculations
// have separate render paths; editing a draft never reconstructs saved geometry.
import {buildCycleModel,supportsCycleResult,savedValveTopology} from "./cycle-model.js";
import {renderCycleWorkspace,mountCycleWorkspace} from "./cycle-schematic.js";
import {flowAnalysisModel,componentAnalysisItems} from "./results.js";
import {renderNozzleGeometry,mountNozzleGeometry} from "./nozzle-geometry.js";
import {renderPumpGeometry,mountPumpGeometry} from "./pump-geometry.js";
import {renderTurbineGeometry,mountTurbineGeometry} from "./turbine-geometry.js";
import {thermoModelDecisionState} from "./thermochemistry-model.js";
import {renderSolverTask,isSolverGroup} from "./solver-workspace.js";
import {renderPreburnerInterfaces,renderPreburnerSummary} from "./preburner-design.js";

const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const finite=value=>typeof value==="number"&&Number.isFinite(value);
const statusNames={queued:"等待计算",running:"计算中",succeeded:"计算完成",failed:"执行失败",cancelled:"已取消",timed_out:"计算超时",interrupted:"服务中断"};
const resultViews={cycle:{label:"保存循环图",view:"topology"},preburner:{label:"预燃室匹配摘要",view:"design_requirements",section:"preburner-requirements"},nozzle:{label:"推力室内型面",view:"geometry"},pump:{label:"泵概念几何",view:"pump_geometry"},turbine:{label:"涡轮概念几何",view:"turbine_geometry"}};
const nodeDefs=[
  ["lox_tank","液氧贮箱出口",125,55,"ox","inlet"],
  ["fuel_tank","甲烷贮箱出口",475,55,"fuel","inlet"],
  ["ox_pump","液氧主泵",125,145,"ox","pumps"],
  ["fuel_pump","甲烷主泵",475,145,"fuel","pumps"],
  ["cooling_channel","再生冷却",475,235,"fuel","chamber_channels"],
  ["ox_preburner","富氧预燃室",125,330,"oxgas","preburner_design"],
  ["fuel_preburner","富燃预燃室",475,330,"fuelgas","preburner_design"],
  ["ox_preburner_turbine","氧侧涡轮",125,425,"oxgas","turbines"],
  ["fuel_preburner_turbine","燃料侧涡轮",475,425,"fuelgas","turbines"],
  ["main_chamber","主燃烧室",300,520,"gas","design"],
  ["nozzle","喷管",300,610,"gas","chamber_contour"],
];
const supplyObjects=["lox_tank","fuel_tank","ox_boost_pump","fuel_boost_pump","ox_feed_line","fuel_feed_line","lox_pump_inlet","fuel_pump_inlet"];
const groupObjects={
  design:["main_chamber","nozzle"],inlet:supplyObjects,
  pumps:["ox_pump","fuel_pump"],turbines:["ox_preburner_turbine","fuel_preburner_turbine"],
  component_efficiencies:["ox_pump","fuel_pump","ox_preburner_turbine","fuel_preburner_turbine"],
  chamber_chemistry:["ox_preburner","fuel_preburner","ox_preburner_turbine","fuel_preburner_turbine","main_chamber","nozzle"],
  preburner_design:["ox_preburner","fuel_preburner"],preburners:["ox_preburner","fuel_preburner"],engineering_constraints:["ox_preburner","fuel_preburner"],
  chamber_contour:["main_chamber","nozzle"],thermal_geometry:["main_chamber","nozzle","cooling_channel"],nozzle_performance:["nozzle"],cooling:["cooling_channel"],
  chamber_cooling_layout:["cooling_channel","main_chamber","nozzle"],chamber_channels:["cooling_channel"],wall_thermal:["cooling_channel","main_chamber","nozzle"],
  valve_configuration:["MFV","OPFV","FPOV"],
  pressure:["ox_pump","fuel_pump","ox_preburner","fuel_preburner","main_chamber"],
  pressure_ffsc:["ox_pump","fuel_pump","ox_preburner","fuel_preburner","main_chamber"],
  initialization:["ox_preburner","fuel_preburner","main_chamber","nozzle"],
};

export function engineeringRelationsForGroup(id){
  if(id==="engineering_constraints")return [["preburner_design","工作温度或氧燃比规定循环设计条件；上限用于检查该工作点是否满足已登记的工程要求"]];
  if(id==="nozzle_performance")return [["chamber_contour","总体面积比约束部件型面与出口尺寸"],["design","总体推力与环境共同定义设计点"],["chamber_chemistry","燃气模型参与膨胀性能计算"]];
  const cooling=[["thermal_geometry","共享型面与覆盖范围确定受热面积"],["cooling","冷却模型决定热负荷与循环如何耦合"],["chamber_channels","详细模型由通道尺寸计算换热、流速和压降"],["wall_thermal","详细模型由材料热物性计算实际壁温"],["pressure_ffsc","冷却压降反馈供给压力需求"],["solver","网格节点数控制沿程离散精度"]];
  if(["cooling","thermal_geometry","chamber_cooling_layout","chamber_channels","wall_thermal","chamber_contour"].includes(id))return cooling;
  if(["pumps","turbines","component_efficiencies"].includes(id))return [["inlet","入口状态影响泵功与吸入条件"],["component_efficiencies","效率假设参与轴功率平衡"],["pressure_ffsc","压力预算决定泵压需求"],["preburner_design","预燃燃气为涡轮提供能量"],["pumps","泵转速和级数用于尺寸初选"],["turbines","涡轮速比和级数用于尺寸初选"]];
  if(["preburner_design","preburners","pressure_ffsc","pressure","valve_configuration"].includes(id))return [["preburner_design","温度或混合比定义预燃室匹配条件"],["pressure_ffsc","支路压降与供给压力共同匹配"],["valve_configuration","阀门流路与开度影响流量分配"],["chamber_chemistry","热化学模型计算出口状态"],["initialization","当前模式的迭代初值在此管理"]];
  return [["design","总体目标、室压、氧燃比和环境边界"],["inlet","供给条件定义系统入口"],["chamber_chemistry","物性与热化学模型用于各燃气部件"],["component_efficiencies","效率假设参与能量与轴功匹配"]];
}
function renderEngineeringRelations(state){
  if(state.groupId==="preburner_design")return renderPreburnerInterfaces(state.draft||{},state.groups)+'<button type="button" class="ev-preburner-summary-link" data-ev-preburner-summary>查看预燃室匹配摘要</button>';
  const rows=engineeringRelationsForGroup(state.groupId).filter(([id])=>state.groups?.some(group=>group.id===id));
  if(!rows.length)return "";
  const note=engineeringObjectsForGroup(state.groupId).length?"相关对象已在图中高亮。":"本页属于系统级设置，可沿图中对象查看局部参数。";
  return `<section class="ev-relations"><h3>参数归属与相互影响</h3><p class="ev-note">${note}以下入口编辑同一份方案参数。</p>${rows.map(([id,description])=>`<div><button type="button" data-ev-related="${id}"${state.groupId==="engineering_constraints"?' data-ev-origin="engineering_constraints"':""}>${esc(state.groups.find(group=>group.id===id).title)}</button><span>${esc(description)}</span></div>`).join("")}</section>`+(state.groupId==="engineering_constraints"?'<button type="button" class="ev-preburner-summary-link" data-ev-preburner-summary>查看记录中的温度与限值校核</button>':"");
}

export function engineeringObjectsForGroup(groupId){return [...(groupObjects[groupId]||[])];}
export function engineeringGroupForObject(objectId,groups=[]){
  const candidates=supplyObjects.includes(objectId)?["inlet"]
    :objectId==="cooling_channel"?["chamber_channels","cooling"]
    :["MFV","OPFV","FPOV"].includes(objectId)?["valve_configuration"]
    :/preburner$/.test(objectId)?["preburner_design","preburners"]
    :[nodeDefs.find(node=>node[0]===objectId)?.[5]];
  return candidates.find(id=>id&&groups.some(group=>group.id===id))||null;
}

export function engineeringRecord(state={}){
  const record=state.record;
  // Result objects from another project, including an unresolved project identity,
  // must never acquire the current draft's version or component labels.
  return record&&state.project?.id&&record.project_id===state.project.id
    &&(!state.selectedJobId||state.selectedJobId===record.id)?record:null;
}
function time(value){
  if(!finite(value)||!Number.isFinite(new Date(value*1000).getTime()))return "时间未记录";
  return new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}).format(new Date(value*1000));
}
function revision(value){return value==null?"版本未记录":`方案 v${value}`;}
function empty(title,detail){return `<div class="ev-empty"><h3>${esc(title)}</h3><p>${esc(detail)}</p></div>`;}

export function draftEngineeringKey(draft={}){
  return JSON.stringify([draft?.cycle_type??"ffsc",draft?.propellant_pair??"lox_methane",savedValveTopology(draft)]);
}

export function draftThermochemistryKey(draft={}){
  return JSON.stringify([draft.cycle_type,draft.propellant_pair,draft.gas_eos,draft.gas_species_policy,
    draft.methane_thermo_dataset,draft.combustion_loss_model]);
}

export function renderEngineeringThermochemistry(draft={}){
  if((draft.cycle_type??"ffsc")!=="ffsc"||(draft.propellant_pair??"lox_methane")!=="lox_methane")
    return empty("当前构型暂无燃气模型分配图","此图适用于液氧甲烷全流量补燃循环。");
  const model=thermoModelDecisionState(draft);
  const nodes=model.stations.map(station=>({...station,id:({ox_turbine:"ox_preburner_turbine",fuel_turbine:"fuel_preburner_turbine"})[station.id]||station.id}));
  const card=station=>`<button type="button" class="ev-thermo-node ev-object" data-ev-object="${station.id}" aria-pressed="false"><strong>${esc(station.label)}</strong><span>${esc(station.eos)}</span><small>${esc(station.chemistry)}</small></button>`;
  return `<section class="ev-thermo" aria-label="当前设置的燃气模型分配">
    <div class="ev-diagram-caption"><strong>工质与热物性分配</strong><span>当前模型配置</span></div>
    <p class="ev-note">氧／甲烷供给、泵和冷却剂：CoolProp HEOS 纯流体模型。预燃室之后按下图使用燃气热数据与 EOS；壁面材料独立定义。</p>
    <div class="ev-thermo-pair"><button type="button" data-property-open="fluid">供给与冷却剂 · HEOS 数据</button><button type="button" data-property-open="material">壁面材料 · ${draft.cooling_bartz_mode===1?"详细模型使用导热率":"当前仅保留部件草案"}</button></div>
    <p class="ev-note ev-thermo-data">${esc(model.datasetLabel)} · ${esc(model.speciesLabel)}</p>
    <div class="ev-thermo-flow">
      <div class="ev-thermo-pair">${nodes.slice(0,2).map(card).join("")}</div>
      <div class="ev-thermo-pair ev-thermo-arrows" aria-hidden="true"><span>↓</span><span>↓</span></div>
      <div class="ev-thermo-pair">${nodes.slice(2,4).map(card).join("")}</div>
      <p class="ev-thermo-transfer">两路实际排气的组成与焓汇入主燃烧室</p>
      <div class="ev-thermo-merge" aria-hidden="true">↓</div>
      ${card(nodes[4])}<div class="ev-thermo-merge" aria-hidden="true">↓</div>${card(nodes[5])}
    </div>
    <p class="ev-selection" data-ev-selection></p>
    <p class="ev-note">本图表示模型配置；温度、组成及适用性检查见保存结果。修改配置后需重新计算设计点。</p><button type="button" data-property-open="gas">查看所选热化学文件、物种与温区</button>
  </section>`;
}

// Display the actual draft boundaries, not solved fluid states. A missing side
// override inherits its historical shared input; invalid explicit values must
// remain invalid, rather than silently reverting to a plausible old value.
export function supplyDraftModel(draft={}){
  const read=(key,legacy,valid)=>{
    const inherited=legacy!=null&&draft[key]==null,value=inherited?draft[legacy]:draft[key];
    return {value:finite(value)&&valid(value)?value:null,inherited,
      state:value==null?"missing":finite(value)&&valid(value)?"given":"invalid"};
  };
  const positive=value=>value>0,nonnegative=value=>value>=0,efficiency=value=>value>0&&value<=1;
  return ["ox","fuel"].map(side=>{
    const tank=read(`${side}_tank_pressure_pa`,"tank_pressure_pa",positive);
    const temperature=read(side==="ox"?"ox_inlet_temp":"fuel_inlet_temp",null,positive);
    const boost=read(`${side}_boost_outlet_pressure_pa`,"inlet_pressure",positive);
    const loss=read(`${side}_feed_line_dp_pa`,"feed_line_dp_pa",nonnegative);
    const eta=read(`${side}_boost_pump_efficiency`,"boost_pump_efficiency",efficiency);
    const issues=[];
    if([tank,temperature,boost,loss,eta].some(field=>field.state==="invalid"))issues.push("存在无效给定值，请核对左侧输入。");
    if(tank.value!=null&&boost.value!=null&&boost.value<tank.value)issues.push("前置泵出口压力低于贮箱出口压力。");
    if(boost.value!=null&&loss.value!=null&&boost.value-loss.value<=0)issues.push("管路压降达到或超过前置泵出口压力。");
    const inletPressure=boost.value!=null&&loss.value!=null&&boost.value-loss.value>0
      &&!(tank.value!=null&&boost.value<tank.value)?boost.value-loss.value:null;
    return {side,label:side==="ox"?"液氧支路":"液甲烷支路",tank,temperature,boost,loss,eta,inletPressure,issues};
  });
}

export function draftSupplyKey(draft={}){
  return JSON.stringify([draft?.cycle_type??"ffsc",draft?.propellant_pair??"lox_methane",supplyDraftModel(draft)]);
}

export function renderEngineeringSupply(draft={}){
  if(!["ffsc","gg"].includes(draft.cycle_type??"ffsc")||(draft.propellant_pair??"lox_methane")!=="lox_methane")
    return empty("当前供给构型暂无专项简图","此视图用于液氧甲烷的前置泵供给方案，不套用至其他推进剂构型。");
  const format=(value,scale=1)=>value==null?"—":new Intl.NumberFormat("zh-CN",{maximumSignificantDigits:6}).format(value/scale);
  const metric=(label,field,unit,scale=1)=>`<div><dt>${label}</dt><dd>${format(field.value,scale)}${field.value==null?"":` <small>${unit}</small>`}</dd></div>`;
  const title=(id,label,kind)=>`<button type="button" class="ev-supply-node ev-object" data-ev-object="${id}" aria-pressed="false"><span>${label}</span><small>${kind}</small></button>`;
  return `<section class="ev-supply" aria-label="当前设置的推进剂供给链"><div class="ev-diagram-caption"><strong>推进剂供给链</strong><span>设计点给定条件</span></div>
    <p class="ev-note">两路独立输送，当前方案均配置前置低压泵。前置泵由外部功源供能。</p>
    <div class="ev-supply-grid">${supplyDraftModel(draft).map(branch=>{
      const {side,tank,temperature,boost,loss,eta,inletPressure,issues}=branch,prefix=side==="ox"?"lox":"fuel";
      const inherited=[tank,boost,loss,eta].filter(field=>field.inherited&&field.state!=="missing").length;
      return `<article class="ev-supply-branch ${side}" aria-label="${branch.label}"><h3>${branch.label}</h3>
        <ol class="ev-supply-chain">
          <li>${title(`${prefix}_tank`,"贮箱出口","接口给定")}<dl>${metric("绝对压力",tank,"MPa",1e6)}${metric("液体温度",temperature,"K")}</dl></li>
          <li>${title(`${side}_boost_pump`,"前置低压泵","设计假设")}<dl>${metric("出口绝对压力",boost,"MPa",1e6)}${metric("等熵效率",eta,"")}</dl></li>
          <li>${title(`${side}_feed_line`,"供给管路","设计点给定")}<dl>${metric("压力损失",loss,"MPa",1e6)}</dl></li>
          <li class="ev-supply-derived">${title(`${prefix}_pump_inlet`,"主泵入口","压力关系计算")}<dl>${metric("绝对压力",{value:inletPressure},"MPa",1e6)}<div><dt>液体温度</dt><dd class="ev-pending">求解后查看</dd></div></dl></li>
        </ol>
        ${issues.length?`<p class="ev-error" role="status">${issues.map(esc).join(" ")}</p>`:""}
        ${inherited?`<p class="ev-supply-legacy">${inherited} 项沿用历史共用设置；两路流体保持独立。</p>`:""}
      </article>`;
    }).join("")}</div>
    <p class="ev-supply-equation">主泵入口压力 = 前置泵出口压力 − 管路压降</p>
    <p class="ev-note">图中压力关系随当前设置更新；泵后温度、相态及吸入条件应查看保存的计算结果。此图不表示前置泵能力或汽蚀余量已通过校核。</p>
  </section>`;
}
// The full result page may coexist in hidden DOM. SVG URL references must not
// resolve to a marker owned by that other viewer.
export function scopeEngineeringCycle(markup){return markup.replace(/(\bid="|url\(#)cy-arrow-/g,"$1ev-saved-cy-arrow-");}
export function renderEngineeringSystem(draft={}){
  if((draft.cycle_type??"ffsc")!=="ffsc"||(draft.propellant_pair??"lox_methane")!=="lox_methane")
    return empty("构型暂无系统简图","当前简图适用于液氧甲烷全流量补燃构型。其他构型不会套用此连接关系。");
  const topology=savedValveTopology(draft);
  if(topology==="unrecognized")return empty("阀门连接待确认","当前阀门配置尚未形成可识别的流路，请核对阀门与流路设置。");
  const before=topology==="paper_split_before_cooling_v1";
  const edges=[
    ["lox_tank","ox_pump","ox","M125 79V121"],["fuel_tank","fuel_pump","fuel","M475 79V121"],
    ["ox_pump","ox_preburner","ox","M125 169V306"],["fuel_pump","cooling_channel","fuel","M475 169V211"],
    ["cooling_channel","fuel_preburner","fuel","M475 259V306"],
    ["ox_pump","fuel_preburner","ox","M125 195H320V302H385V330H400"],
    [before?"fuel_pump":"cooling_channel","ox_preburner","fuel",before?"M475 190H275V330H200":"M475 280H265V330H200"],
    ["ox_preburner","ox_preburner_turbine","oxgas","M125 354V401"],["fuel_preburner","fuel_preburner_turbine","fuelgas","M475 354V401"],
    ["ox_preburner_turbine","main_chamber","oxgas","M125 449V480H260V496"],
    ["fuel_preburner_turbine","main_chamber","fuelgas","M475 449V480H340V496"],
    ["main_chamber","nozzle","gas","M300 544V586"],
    ["ox_preburner_turbine","ox_pump","shaft","M50 425H25V145H50"],
    ["fuel_preburner_turbine","fuel_pump","shaft","M550 425H610V145H550"],
  ];
  return `<div class="ev-system"><div class="ev-diagram-caption"><strong>液氧甲烷 · 全流量补燃</strong><span>当前构型</span></div>
    <svg class="ev-diagram" viewBox="0 0 640 655" role="group" aria-label="当前发动机系统简图，选择对象可定位对应参数组">
      <defs>${["ox","fuel","oxgas","fuelgas","gas"].map(tone=>`<marker id="ev-arrow-${tone}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path class="ev-arrow ${tone}" d="M0 0L10 5L0 10Z"/></marker>`).join("")}</defs>
      ${edges.map(([from,to,tone,path])=>`<path class="ev-edge ${tone}" data-ev-from="${from}" data-ev-to="${to}" d="${path}"${tone!=="shaft"?` marker-end="url(#ev-arrow-${tone})"`:""}/>`).join("")}
      <text class="ev-edge-label" x="205" y="218">氧交叉供给</text><text class="ev-edge-label" x="${before?280:270}" y="${before?180:272}">燃料交叉供给</text>
      <text class="ev-shaft-label" transform="translate(16 320) rotate(-90)">轴功率传递</text><text class="ev-shaft-label" transform="translate(626 320) rotate(-90)">轴功率传递</text>
      ${nodeDefs.map(([id,label,x,y,tone])=>`<g class="ev-object ${tone}" data-ev-object="${id}" role="button" tabindex="0" aria-label="${label}，定位参数" aria-pressed="false"><title>${label} · 选择以定位设置</title><rect x="${x-75}" y="${y-24}" width="150" height="48" rx="5"/><text x="${x}" y="${y+5}" text-anchor="middle">${label}</text></g>`).join("")}
    </svg><div class="ev-legend"><span class="ox">液氧</span><span class="fuel">甲烷</span><span class="gas">燃气</span><span class="shaft">机械轴</span></div>
    <p class="ev-selection" data-ev-selection></p><p class="ev-note">简图表示介质流向与轴功率传递；供给段合并表示，阀门与局部管路省略。无连接标记的线条交叉不表示相通。${before?"燃料交叉支路从冷却段上游引出。":"燃料在冷却段下游分流。"}图形不表示实际布局或尺寸。</p></div>`;
}

export function renderEngineeringSource(state={},tab="system"){
  const record=engineeringRecord(state),project=state.project;
  if(tab==="task")return `<div class="ev-source-line"><strong>当前任务配置 · ${state.dirty?"基于":""}${esc(revision(project?.revision))}</strong><span>${state.dirty?"有未保存修改":"已保存"}</span></div><p class="ev-note">给定条件、待求量与数值准则；保存结果按其计算记录单独读取。</p>`;
  if(tab==="system")return `<div class="ev-source-line"><strong>当前设置 · ${state.dirty?"基于":""}${esc(revision(project?.revision))}</strong><span>${state.dirty?"有未保存修改":"已保存"}</span></div><p class="ev-note">${state.groupId==="inlet"?"供给链与给定条件；本页压力关系不替代保存的求解结果。":state.groupId==="chamber_chemistry"?"燃气模型的部件覆盖与组分处理；选择对象可定位相应设置。":"系统连接示意；参数设置通过对象高亮定位。"}</p>`;
  if(!record)return `<strong>${state.recordError?"计算记录读取失败":state.record?"记录不属于当前方案":state.selectedJobId?"正在读取所选记录":"尚无保存结果"}</strong><p class="ev-note">保存结果与当前设置分别管理；本区仅显示所选计算记录。</p>`;
  const freshness=state.workflow?.freshness||{state:"unknown",label:"版本一致性待确认",reason:"尚未完成输入快照比较。"};
  return `<div class="ev-source-line"><strong>保存结果 · ${esc(revision(record.revision))}</strong><span>${esc(statusNames[record.status]||"状态未记录")}</span></div><p class="ev-record-time">${esc(time(record.created))}（北京时间）</p><div class="ev-freshness" data-state="${esc(freshness.state)}"><strong>${esc(freshness.label)}</strong><p>${esc(freshness.reason)}</p></div><details class="ev-trace"><summary>记录标识</summary><code>${esc(record.id)}</code></details>`;
}

export function engineeringSourceTag(state={},tab="system"){
  const version=value=>value==null?"版本未记录":`v${value}`;
  if(tab==="system"||tab==="task")return state.dirty?`当前设置 · 基于 ${version(state.project?.revision)} · 未保存`:`当前设置 ${version(state.project?.revision)} · 已保存`;
  const record=engineeringRecord(state);
  if(!record)return state.recordError?"读取失败":state.record?"来源不匹配":state.selectedJobId?"读取中":"尚无结果";
  const freshness=state.workflow?.freshness?.state;
  const suffix=({stale:" · 待更新",invalid:" · 已撤回",current:""})[freshness]??" · 待核对";
  return `结果 ${version(record.revision)}${suffix}`;
}

/** A single instance survives group navigation and help. Only body-affecting
 * changes remount a saved viewer, retaining camera/selection during draft edits. */
export function mountEngineeringView(host,{onGroup,onView,onRecord,onState,onComponent,onError}={}){
  if(!host?.querySelector)return {update(){},dispose(){}};
  let state={},tab="system",resultView="cycle",bodyKey=null,bodyRecord=null,cleanup=null,disposed=false,selectedObject=null,lastGroup=null,lastProject=null,lastNumerical=false,returnGroup=null;
  host.classList.add("engineering-view");
  host.innerHTML=`<div class="ev-header"><div><h2>工程视图</h2><p>系统构型与保存结果</p></div><span class="ev-source-tag" data-ev-source-tag>当前设置</span></div>
    <div class="ev-tabs" role="group" aria-label="工程视图来源"><button type="button" data-ev-tab="task" aria-pressed="false" hidden>任务摘要</button><button type="button" data-ev-tab="system" aria-pressed="true">系统简图</button><button type="button" data-ev-tab="saved" aria-pressed="false">保存结果</button></div>
    <div class="ev-context" data-ev-context></div><div class="ev-controls" data-ev-controls hidden><label>计算记录<select data-ev-record aria-label="工程视图计算记录"></select></label><div class="ev-view-control"><label>结果视图<select data-ev-result-view aria-label="保存结果视图">${Object.entries(resultViews).map(([id,v])=>`<option value="${id}">${v.label}</option>`).join("")}</select></label><button type="button" data-ev-open>完整视图 ↗</button></div></div><div class="ev-body" data-ev-body></div>`;
  const context=host.querySelector("[data-ev-context]"),controls=host.querySelector("[data-ev-controls]"),body=host.querySelector("[data-ev-body]"),recordSelect=host.querySelector("[data-ev-record]"),viewSelect=host.querySelector("[data-ev-result-view]");
  const fail=error=>onError?.(error?.message||String(error));
  const invoke=(callback,...args)=>{try{Promise.resolve(callback?.(...args)).catch(fail);}catch(error){fail(error);}};
  const openComponent=id=>{if(!state.busy){if(onComponent)invoke(onComponent,id);else invoke(onView,"components");}};
  function mountSaved(record){
    const result=record.result;
    if(!result){body.innerHTML=empty(statusNames[record.status]||"没有计算结果",["queued","running"].includes(record.status)?"所选任务尚未产生保存结果。":"所选记录未包含可用结果，请在完整视图中检查任务与诊断。");return;}
    if(!supportsCycleResult(result,record.inputs)){body.innerHTML=empty("历史构型暂不支持此视图","此记录不属于当前液氧甲烷全流量补燃分析范围；请在历史记录中查看原始数据。");return;}
    // A detached child host gives the cycle listener the same lifetime as its
    // markup. Geometry controllers additionally dispose global resize hooks.
    body.innerHTML='<div data-ev-saved-body></div>';
    const savedHost=body.querySelector("[data-ev-saved-body]");
    if(resultView==="cycle"){
      savedHost.innerHTML=scopeEngineeringCycle(renderCycleWorkspace(buildCycleModel(result,flowAnalysisModel(result),componentAnalysisItems(result))));
      savedHost.dataset.cyFit="true";
      mountCycleWorkspace(savedHost,record.id,{onComponent:openComponent,onError:fail});
    }else if(resultView==="preburner"){
      savedHost.innerHTML=renderPreburnerSummary(record);
    }else{
      const key={nozzle:"nozzle_geometry",pump:"pump_geometry",turbine:"turbine_geometry"}[resultView],geometry=record[key]||result[key];
      if(!geometry){savedHost.innerHTML=empty("本记录未保存该几何视图","可切换其他结果视图。几何仅取自所选记录，不使用当前设置补画。");return;}
      if(geometry.source?.record_id&&geometry.source.record_id!==record.id){savedHost.innerHTML=empty("几何来源与所选记录不一致","此几何数据未显示，请在完整记录中核对来源标识。");return;}
      const render={nozzle:renderNozzleGeometry,pump:renderPumpGeometry,turbine:renderTurbineGeometry}[resultView];
      const mount={nozzle:mountNozzleGeometry,pump:mountPumpGeometry,turbine:mountTurbineGeometry}[resultView];
      savedHost.innerHTML=render(geometry);cleanup=mount(savedHost,geometry);
    }
  }
  function highlight(){
    const ids=engineeringObjectsForGroup(state.groupId);
    if(!ids.includes(selectedObject))selectedObject=ids[0]||null;
    body.querySelectorAll("[data-ev-object]").forEach(node=>{
      const active=ids.includes(node.dataset.evObject);node.classList.toggle("is-selected",active);node.setAttribute("aria-pressed",String(active));
      const enabled=Boolean(engineeringGroupForObject(node.dataset.evObject,state.groups));node.setAttribute("aria-disabled",String(!enabled));node.setAttribute("tabindex",enabled?"0":"-1");
    });
    body.querySelectorAll("[data-ev-from]").forEach(edge=>edge.classList.toggle("is-active",ids.includes(edge.dataset.evFrom)||ids.includes(edge.dataset.evTo)));
    const selection=body.querySelector("[data-ev-selection]");
    if(selection){const group=state.groups?.find(group=>group.id===state.groupId);selection.textContent=group?`当前设置：${group.title}${ids.some(id=>nodeDefs.some(n=>n[0]===id))?" · 相关对象已高亮":" · 系统级设置"}`:"选择系统对象可定位参数组。";}
    const savedHost=body.querySelector("[data-ev-saved-body]");
    if(tab==="saved"&&resultView==="cycle"&&savedHost&&selectedObject){
      savedHost.dataset.cySelected=selectedObject;
      mountCycleWorkspace(savedHost,bodyRecord?.id,{onComponent:openComponent,onError:fail});
    }
  }
  function paint(){
    const record=engineeringRecord(state),projectId=state.project?.id;
    if(projectId!==lastProject){lastProject=projectId;selectedObject=null;lastGroup=null;bodyKey=null;bodyRecord=null;lastNumerical=false;returnGroup=null;}
    if(state.groupId===returnGroup)returnGroup=null;
    const numerical=isSolverGroup(state.groupId);
    if(numerical&&!lastNumerical&&tab==="system")tab="task";
    if(!numerical&&tab==="task")tab="system";
    lastNumerical=numerical;
    const taskTab=host.querySelector('[data-ev-tab="task"]');if(taskTab)taskTab.hidden=!numerical;
    host.querySelectorAll("[data-ev-tab]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.evTab===tab)));
    host.querySelector("[data-ev-source-tag]").textContent=engineeringSourceTag(state,tab);
    const source=renderEngineeringSource(state,tab)+(returnGroup?`<button type="button" class="ev-return" data-ev-return="${esc(returnGroup)}">返回${esc(returnGroup==="engineering_constraints"?"工程限值与校核":"预燃室热力匹配条件")}</button>`:"");if(context.innerHTML!==source)context.innerHTML=source;
    controls.hidden=tab!=="saved";
    const jobs=(state.jobs||[]).filter(job=>job.project_id===projectId);
    if(record&&!jobs.some(job=>job.id===record.id))jobs.unshift(record);
    const options=jobs.length?jobs.map(job=>`<option value="${esc(job.id)}">${esc(revision(job.revision))} · ${esc(time(job.created))} · ${esc(statusNames[job.status]||"状态未记录")}</option>`).join(""):'<option value="">尚无计算记录</option>';
    if(recordSelect.innerHTML!==options)recordSelect.innerHTML=options;
    recordSelect.value=state.selectedJobId||record?.id||"";recordSelect.disabled=Boolean(state.busy)||!jobs.length;
    viewSelect.value=resultView;viewSelect.disabled=Boolean(state.busy);
    host.querySelector("[data-ev-open]").disabled=Boolean(state.busy)||!record;
    host.querySelector("[data-ev-open]").textContent=resultView==="preburner"?"完整结果与部件交接 ↗":"完整视图 ↗";
    const supply=state.groupId==="inlet",thermochemistry=state.groupId==="chamber_chemistry";
    const key=tab==="task"?`task:${JSON.stringify([state.draft,state.groups,state.solverControls])}`:tab==="system"?`${state.groupId}:`+(supply?`supply:${draftSupplyKey(state.draft||{})}`:thermochemistry?`thermo:${draftThermochemistryKey(state.draft||{})}`:`system:${draftEngineeringKey(state.draft||{})}:${state.groupId==="preburner_design"?state.draft?.cooling_bartz_mode:""}`):`saved:${resultView}:${record?.id||""}:${record?.status||""}:${state.recordError||""}`;
    const remount=bodyKey!==key||(tab==="saved"&&bodyRecord!==record);
    if(remount){
      cleanup?.();cleanup=null;bodyKey=key;bodyRecord=record;
      try{
        if(tab==="task")body.innerHTML=renderSolverTask(state);
        else if(tab==="system")body.innerHTML=(supply?renderEngineeringSupply(state.draft||{}):thermochemistry?renderEngineeringThermochemistry(state.draft||{}):renderEngineeringSystem(state.draft||{}))+renderEngineeringRelations(state);
        else if(record)mountSaved(record);
        else if(state.recordError)body.innerHTML=empty("计算记录读取失败",state.recordError)+(state.selectedJobId?'<button type="button" data-ev-retry>重新读取记录</button>':"");
        else body.innerHTML=empty(state.record?"结果来源不匹配":state.selectedJobId?"正在读取计算记录":"尚无保存结果",state.record?"请选择当前方案的计算记录。":state.selectedJobId?"读取完成后将在此显示。":"完成参数定义后，可在“输入检查与运行”提交设计点计算。");
      }catch(error){body.innerHTML=empty("视图暂时无法显示",error?.message||String(error));fail(error);}
    }
    if(remount||lastGroup!==state.groupId){highlight();lastGroup=state.groupId;}
  }
  function click(event){
    const target=event.target?.closest?.("[data-ev-tab],[data-ev-open],[data-ev-retry],[data-ev-object],[data-cy-object],[data-flow-state],[data-ev-related],[data-task-group],[data-ev-return],[data-ev-preburner-summary]");
    if(!target||!host.contains(target))return;
    if(target.dataset.flowState!==undefined){
      event.preventDefault();
      if(!state.busy){if(onState)invoke(onState,target.dataset.flowState,target.dataset.flowGroup);else invoke(onView,"states");}
      return;
    }
    // This only changes the read-only pane; input validation need not block it.
    if(target.dataset.evPreburnerSummary!==undefined){tab="saved";resultView="preburner";paint();return;}
    if(state.busy)return;
    if(target.dataset.evReturn){invoke(onGroup,target.dataset.evReturn);return;}
    if(target.dataset.evRelated||target.dataset.taskGroup){event.stopPropagation?.();if(["preburner_design","engineering_constraints"].includes(target.dataset.evOrigin))returnGroup=target.dataset.evOrigin;invoke(onGroup,target.dataset.evRelated||target.dataset.taskGroup);return;}
    if(target.dataset.evTab){if(target.dataset.evTab==="saved"&&tab!=="saved"&&["preburner_design","engineering_constraints"].includes(state.groupId))resultView="preburner";tab=target.dataset.evTab;paint();return;}
    if(target.dataset.evRetry!==undefined){if(state.selectedJobId)invoke(onRecord,state.selectedJobId);return;}
    if(target.hasAttribute("data-ev-open")){invoke(onView,resultViews[resultView].view,resultViews[resultView].section);return;}
    const objectId=target.dataset.evObject||target.dataset.cyObject,group=engineeringGroupForObject(objectId,state.groups);
    if(group){selectedObject=objectId;invoke(onGroup,group);}
  }
  function keydown(event){if(["Enter"," "].includes(event.key)&&event.target?.closest?.("[data-ev-object],[data-cy-object]")){event.preventDefault();click(event);}}
  function change(event){
    if(state.busy)return;
    if(event.target===viewSelect){resultView=Object.hasOwn(resultViews,viewSelect.value)?viewSelect.value:"cycle";paint();}
    if(event.target===recordSelect&&recordSelect.value)invoke(onRecord,recordSelect.value);
  }
  host.addEventListener("click",click);host.addEventListener("keydown",keydown);host.addEventListener("change",change);
  return {
    update(next){if(!disposed){state=next||{};paint();}},
    dispose(){if(disposed)return;disposed=true;cleanup?.();host.removeEventListener("click",click);host.removeEventListener("keydown",keydown);host.removeEventListener("change",change);host.innerHTML="";},
  };
}
