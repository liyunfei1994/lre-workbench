// Saved-result adapter for the engine cycle schematic. No solver or property evaluation.
const finite = value => typeof value === "number" && Number.isFinite(value);
const object = value => value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const first = (...values) => values.find(finite) ?? null;
const scale = (value, factor) => finite(value) && Number.isFinite(value * factor) ? value * factor : null;
const clone = value => Array.isArray(value) ? value.map(clone)
  : value !== null && typeof value === "object" ? Object.fromEntries(Object.entries(value).map(([key,item])=>[key,clone(item)]))
  : typeof value === "number" && !finite(value) ? null : value;

const CONTRACTS = {
  ffsc: {
    nodes: ["lox_tank","fuel_tank","ox_pump","fuel_pump","fuel_cooling","ox_preburner","fuel_preburner","main_chamber","nozzle"],
    edges: [["lox_tank","ox_pump"],["fuel_tank","fuel_pump"],["fuel_pump","fuel_cooling"],["ox_pump","ox_preburner"],["ox_pump","fuel_preburner"],["fuel_cooling","ox_preburner"],["fuel_cooling","fuel_preburner"],["ox_preburner","main_chamber"],["fuel_preburner","main_chamber"],["main_chamber","nozzle"]],
  },
  gg: {
    nodes: ["lox_tank","fuel_tank","ox_pump","fuel_pump","fuel_cooling","gas_generator","gg_turbine","gg_exhaust","main_chamber","nozzle"],
    edges: [["lox_tank","ox_pump"],["fuel_tank","fuel_pump"],["ox_pump","main_chamber"],["fuel_pump","fuel_cooling"],["fuel_cooling","main_chamber"],["ox_pump","gas_generator"],["fuel_pump","gas_generator"],["gas_generator","gg_turbine"],["gg_turbine","gg_exhaust"],["main_chamber","nozzle"]],
  },
};
const FLOW_IDS = {
  lox_tank:["lox_tank"],fuel_tank:["fuel_tank"],
  ox_pump:["lox_pump_inlet","lox_after_pump"],fuel_pump:["fuel_pump_inlet","fuel_after_pump"],
  cooling_channel:["fuel_after_cooling"],ox_preburner:["ox_preburner_outlet"],fuel_preburner:["fuel_preburner_outlet"],
  ox_preburner_turbine:["ox_preburner_outlet","ox_turbine_exhaust"],
  fuel_preburner_turbine:["fuel_preburner_outlet","fuel_turbine_exhaust"],
  gas_generator:["gg_outlet"],gg_turbine:["gg_outlet","gg_turbine_exhaust"],
  gg_turbine_ox:[],gg_turbine_fuel:[],main_chamber:["main_chamber"],nozzle:[],gg_exhaust_nozzle:[],
};
const DEFS = {
  lox_tank:["液氧贮箱","tank","ox"],fuel_tank:["燃料贮箱","tank","fuel"],
  ox_pump:["液氧泵","pump","ox"],fuel_pump:["燃料泵","pump","fuel"],
  cooling_channel:["再生冷却","cooling","fuel"],
  ox_preburner:["富氧预燃室","preburner","oxgas"],fuel_preburner:["富燃预燃室","preburner","fuelgas"],
  ox_preburner_turbine:["富氧涡轮","turbine","oxgas"],fuel_preburner_turbine:["富燃涡轮","turbine","fuelgas"],
  gas_generator:["燃气发生器","preburner","fuelgas"],gg_turbine:["GG 涡轮","turbine","fuelgas"],
  gg_turbine_ox:["液氧泵涡轮","turbine","fuelgas"],gg_turbine_fuel:["燃料泵涡轮","turbine","fuelgas"],
  main_chamber:["主燃烧室","chamber","hot"],nozzle:["主喷管","nozzle","hot"],
  gg_exhaust_nozzle:["独立排气喷管","nozzle","fuelgas"],
};

function matches(nodes, edges, contract) {
  if (nodes.length!==contract.nodes.length || edges.length!==contract.edges.length) return false;
  const keys=nodes.map(node=>node?.key), pairs=edges.map(edge=>JSON.stringify([edge?.source,edge?.target]));
  return new Set(keys).size===keys.length && new Set(pairs).size===pairs.length
    && contract.nodes.every(key=>keys.includes(key))
    && contract.edges.every(pair=>pairs.includes(JSON.stringify(pair)));
}

function layout(inputs, rawComponents, summary) {
  const evidence=[];
  if (inputs.gg_layout!=null && !["single_shaft","dual_shaft"].includes(inputs.gg_layout)) return null;
  if (inputs.gg_layout!=null) evidence.push(inputs.gg_layout);
  const single=own(rawComponents,"gg_turbine"), ox=own(rawComponents,"gg_turbine_ox"), fuel=own(rawComponents,"gg_turbine_fuel");
  if (single && (ox || fuel)) return null;
  if (single) evidence.push("single_shaft");
  if (ox && fuel) evidence.push("dual_shaft");
  // A single orphaned branch does not establish the missing shaft's layout.
  if ((ox || fuel) && !(ox && fuel)) return null;
  if (finite(summary.gg_layout_dual)) {
    if (![0,1].includes(summary.gg_layout_dual)) return null;
    evidence.push(summary.gg_layout_dual===1 ? "dual_shaft" : "single_shaft");
  }
  return evidence.length && new Set(evidence).size===1 ? evidence[0] : null;
}

/** Inputs are immutable calculation inputs, with submitted inputs only for older records. */
export function buildCycleModel(result, flow = {}, components = []) {
  result=object(result);
  const calc=object(result.calculation), inputs=object(calc.inputs ?? result.submitted_inputs), summary=object(calc.summary);
  const rawComponents=object(calc.components);
  const available=new Set((Array.isArray(components)?components:[]).map(item=>item?.id).filter(id=>typeof id==="string"));
  const flowNodes=(Array.isArray(flow?.nodes)?flow.nodes:[]).filter(node=>node && typeof node.id==="string");
  const states=new Map(flowNodes.map(node=>[node.id,node]));
  const originalNodes=clone(Array.isArray(calc.topology_nodes)?calc.topology_nodes:[]);
  const originalEdges=clone(Array.isArray(calc.topology_edges)?calc.topology_edges:[]);
  const pair=inputs.propellant_pair ?? "lox_methane";
  const fuel=pair==="lox_kerosene" ? {name:"煤油",symbol:"C₁₂H₂₆",pair:"液氧 / 煤油（正十二烷替代物）"}
    : pair==="lox_methane" ? {name:"甲烷",symbol:"CH₄",pair:"液氧 / 甲烷"}
      : {name:"燃料",symbol:"燃料",pair:String(pair)};
  const label=text=>fuel.name==="煤油" ? text.replace(/甲烷|燃料/g,"煤油").replace(/CH4|CH₄/g,"C₁₂H₂₆") : text.replace(/燃料/g,fuel.name);
  const notes=["流路与机械轴分别表示介质连接和动力传递；图形不表示实际几何、尺寸或设备布置。"];
  const nodeContract=matches(originalNodes,originalEdges,CONTRACTS.ffsc)?"ffsc":matches(originalNodes,originalEdges,CONTRACTS.gg)?"gg":null;
  const savedKind=typeof inputs.cycle_type==="string"?inputs.cycle_type:null;
  let kind="generic";
  if (nodeContract && (!savedKind || savedKind===nodeContract)) {
    if (nodeContract==="ffsc") kind="ffsc";
    else {
      const shaft=layout(inputs,rawComponents,summary);
      if (shaft) kind=shaft==="dual_shaft"?"gg-dual":"gg-single";
      else notes.push("本记录的 GG 轴系信息缺失或相互不一致，保留原始拓扑，不推定单轴或双轴。 ");
    }
  }
  if (kind==="generic") notes.push("本记录的连接信息不完整，或不属于当前支持的循环布局。按记录中的节点和连接展示，不补造设备或连接。");
  if (pair==="lox_kerosene") notes.push("煤油采用正十二烷替代工质；图中数值来自该工程模型。 ");
  const out=id=>object(object(rawComponents[id]).outputs);
  const s=id=>states.get(id) || {};
  const metric=(name,value,unit)=>({label:name,value:finite(value)?value:null,unit});
  const node=(id, title=null, type=null, tone=null)=>{
    const definition=own(DEFS,id)?DEFS[id]:[id,"generic","neutral"];
    const info=out(id), stateIds=own(FLOW_IDS,id)?FLOW_IDS[id]:[];
    const linked=stateIds.map(key=>states.get(key)).filter(Boolean).map(clone);
    // A missing outlet must stay missing; the last available state may be the inlet.
    const end=s(stateIds.at(-1)), inlet=s(stateIds[0]);
    const metrics=[];
    const kindType=type || definition[1];
    if (kindType==="tank") metrics.push(metric("压力",end.pressure,"MPa"),metric("温度",end.temperature,"K"),metric("总供给流量",end.massFlow,"kg/s"));
    if (kindType==="pump") metrics.push(
      metric("出口压力",first(end.pressure,info.p_out_MPa,scale(info.p_out_Pa,1e-6),scale(info.p_out_bar,.1)),"MPa"),
      metric("出口温度",first(end.temperature,info.T_out_K),"K"),metric("总流量",end.massFlow,"kg/s"),
      metric("轴功率需求",first(info.power_MW,scale(info.power_W,1e-6)),"MW"),metric("转速",info.nr_rpm,"rpm"));
    if (kindType==="cooling") metrics.push(metric("出口压力",first(end.pressure,scale(info.P_out_Pa,1e-6)),"MPa"),
      metric("出口温度",first(end.temperature,info.T_out_K),"K"),metric("冷却流量",end.massFlow,"kg/s"),
      metric("吸热量",first(info.heat_load_MW,scale(info.heat_load_W,1e-6)),"MW"));
    if (kindType==="preburner") metrics.push(metric("出口压力",first(end.pressure,info.preburner_pressure_MPa,scale(info.preburner_pressure_Pa,1e-6)),"MPa"),
      metric("出口温度",first(end.temperature,info.T_combustion_K),"K"),metric("燃气流量",first(end.massFlow,info.mdot_total_kgs),"kg/s"));
    if (kindType==="turbine") metrics.push(metric("入口压力",first(scale(info.p_in_Pa,1e-6),inlet.pressure),"MPa"),
      metric("出口压力",first(scale(info.p_out_Pa,1e-6),end.pressure),"MPa"),
      metric("出口温度",first(info.T_exhaust_K,end.temperature),"K"),metric("燃气流量",first(info.mass_flow_kgs,end.massFlow),"kg/s"),
      metric("涡轮功率",first(info.turbine_power_MW,scale(info.turbine_power_W,1e-6)),"MW"),metric("转速",info.shaft_speed_rpm,"rpm"));
    if (kindType==="chamber") metrics.push(metric("室压",first(end.pressure,scale(summary.chamber_pressure_Pa,1e-6)),"MPa"),
      metric("温度",first(end.temperature,info.T_chamber_K),"K"),metric("主室流量",first(end.massFlow,info.mdot_chamber_kgs),"kg/s"));
    if (kindType==="nozzle") metrics.push(metric("当前环境推力",scale(info.thrust_N,.001),"kN"),metric("真空推力",scale(info.thrust_vac_N,.001),"kN"),
      metric("真空比冲",info.Isp_vac_s,"s"),metric("出口压力",first(scale(info.p_exit_Pa,1e-6),scale(info.exit_pressure_Pa,1e-6)),"MPa"),
      metric("出口温度",info.T_exit_K,"K"));
    return {id,title:title ?? label(definition[0]),type:kindType,tone:tone || definition[2],states:linked,componentId:available.has(id)?id:null,metrics};
  };
  let nodes=[],connections=[],shafts=[];
  const connect=(source,target,tone,text,value=null)=>connections.push({source,target,tone,label:finite(value)?`${text} · ${value.toLocaleString("zh-CN",{maximumFractionDigits:3})} kg/s`:text});
  const shaft=(source,target,text)=>shafts.push({source,target,label:text});
  if (kind==="generic") {
    const seen=new Set();
    nodes=originalNodes.filter(item=>typeof item?.key==="string"
      && Array.isArray(item.pos) && item.pos.length>=2 && item.pos.slice(0,2).every(finite)
      && !seen.has(item.key) && seen.add(item.key)).map(item=>{
      const actualId=item.key==="fuel_cooling"?"cooling_channel":item.key==="gg_exhaust"?"gg_exhaust_nozzle":item.key;
      const entry=node(actualId,label(typeof item.label==="string"?item.label:item.key),"generic","neutral");
      return {...entry,id:item.key};
    });
    const ids=new Set(nodes.map(item=>item.id));
    connections=originalEdges.filter(item=>ids.has(item?.source) && ids.has(item?.target)).map(item=>({source:item.source,target:item.target,tone:"neutral",label:label(typeof item.label==="string"?item.label:"")}));
  } else {
    nodes=["lox_tank","fuel_tank","ox_pump","fuel_pump","cooling_channel"].map(id=>node(id));
    connect("lox_tank","ox_pump","ox","液氧供给（含增压与管路）",s("lox_pump_inlet").massFlow);
    connect("fuel_tank","fuel_pump","fuel",`${fuel.name}供给（含增压与管路）`,s("fuel_pump_inlet").massFlow);
    notes.push("增压段与供给管路合并表示；主泵卡片从管路后入口状态开始，增压泵功率未计入主轴功率。 ");
    if (kind==="ffsc") {
      nodes.push(...["ox_preburner","fuel_preburner","ox_preburner_turbine","fuel_preburner_turbine"].map(id=>node(id)));
      connect("fuel_pump","cooling_channel","fuel","全部燃料冷却",s("fuel_after_cooling").massFlow);
      connect("ox_pump","ox_preburner","ox","液氧主路",first(out("ox_preburner").mdot_ox_kgs,summary.mdot_ox_to_ox_preburner_kg_s));
      connect("ox_pump","fuel_preburner","ox","液氧交叉供给",first(out("fuel_preburner").mdot_ox_kgs,summary.mdot_ox_to_fuel_preburner_kg_s));
      connect("cooling_channel","ox_preburner","fuel","冷却后燃料交叉供给",first(out("ox_preburner").mdot_fuel_kgs,summary.mdot_fuel_to_ox_preburner_kg_s));
      connect("cooling_channel","fuel_preburner","fuel","冷却后燃料主路",first(out("fuel_preburner").mdot_fuel_kgs,summary.mdot_fuel_to_fuel_preburner_kg_s));
      connect("ox_preburner","ox_preburner_turbine","oxgas","富氧燃气",s("ox_preburner_outlet").massFlow);
      connect("fuel_preburner","fuel_preburner_turbine","fuelgas","富燃燃气",s("fuel_preburner_outlet").massFlow);
      connect("ox_preburner_turbine","main_chamber","oxgas","富氧涡轮排气",s("ox_turbine_exhaust").massFlow);
      connect("fuel_preburner_turbine","main_chamber","fuelgas","富燃涡轮排气",s("fuel_turbine_exhaust").massFlow);
      shaft("ox_preburner_turbine","ox_pump","液氧轴：富氧涡轮驱动液氧泵");
      shaft("fuel_preburner_turbine","fuel_pump","燃料轴：富燃涡轮驱动燃料泵");
      notes.push("燃料全部经过冷却后再分配至两台预燃室；两条涡轮排气支路都进入主燃烧室。 ");
    } else {
      nodes.push(node("gas_generator"));
      connect("fuel_pump","cooling_channel","fuel","主路燃料冷却",s("fuel_after_cooling").massFlow);
      connect("ox_pump","main_chamber","ox","液氧主路");
      connect("cooling_channel","main_chamber","fuel","冷却后燃料主路",s("fuel_after_cooling").massFlow);
      connect("ox_pump","gas_generator","ox","GG 液氧供料",first(out("gas_generator").mdot_ox_kgs,summary.gg_mdot_ox_kg_s));
      connect("fuel_pump","gas_generator","fuel","GG 燃料供料（冷却前）",first(out("gas_generator").mdot_fuel_kgs,summary.gg_mdot_fuel_kg_s));
      if (kind==="gg-single") {
        nodes.push(node("gg_turbine"));
        connect("gas_generator","gg_turbine","fuelgas","GG 燃气",first(out("gg_turbine").mass_flow_kgs,s("gg_outlet").massFlow));
        connect("gg_turbine","gg_exhaust_nozzle","fuelgas","涡轮排气",s("gg_turbine_exhaust").massFlow);
        shaft("gg_turbine","ox_pump","同一根轴：单涡轮驱动双泵");
        shaft("gg_turbine","fuel_pump","同一根轴：单涡轮驱动双泵");
      } else {
        nodes.push(node("gg_turbine_ox"),node("gg_turbine_fuel"));
        for (const [id,pump,text] of [["gg_turbine_ox","ox_pump","液氧轴"],["gg_turbine_fuel","fuel_pump","燃料轴"]]) {
          connect("gas_generator",id,"fuelgas",`${text}并联燃气支路`,out(id).mass_flow_kgs);
          connect(id,"gg_exhaust_nozzle","fuelgas",`${text}涡轮排气`,out(id).mass_flow_kgs);
          shaft(id,pump,`${text}：独立涡轮驱动对应泵`);
        }
        notes.push("双轴 GG 共用燃气入口与背压，燃气并联分流。两涡轮卡片各用自身部件结果；G06 汇总排气记录的流量为总量、温度取液氧侧涡轮出口，并非两股燃气混合温度，未附到任一分支涡轮。 ");
      }
      nodes.push(node("gg_exhaust_nozzle"));
      notes.push("GG 供料在冷却前从两泵出口分出；涡轮排气独立排出，不进入主燃烧室。涡轮功率为机械损失折减前数值。 ");
    }
    nodes.push(node("main_chamber"),node("nozzle"));
    connect("main_chamber","nozzle","hot","主燃气",s("main_chamber").massFlow);
  }
  const totals=[metric("发动机总流量",summary.mdot_total_kg_s,"kg/s"),metric("当前环境总推力",scale(summary.thrust_N,.001),"kN"),
    metric("真空总推力",scale(summary.thrust_vac_N,.001),"kN"),metric("真空比冲",summary.Isp_vac_s,"s")];
  if (kind.startsWith("gg")) totals.push(metric("涡轮可用轴功率",summary.gg_turbine_power_delivered_MW,"MW"),metric("轴系机械效率",summary.gg_mech_efficiency,"—"));
  return {kind,fuel,nodes,connections,shafts,summary:totals,notes,originalNodes,originalEdges};
}
