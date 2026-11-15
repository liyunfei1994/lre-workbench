// Current input settings only. Saved results use their recorded model identity.
const EOS = {
  "ideal-gas":"理想气体混合物",
  "Peng-Robinson":"真实气体（PR）",
  "Peng-Robinson-Boston-Mathias":"真实气体（PR–BM）",
};
const DATASETS = {gri30:"GRI30 热化学数据",gri30_highT:"GRI30 高温热化学数据"};

export function selectThermochemistryModel(inputs,eos) {
  if(typeof eos!=="string"||!Object.hasOwn(EOS,eos))throw new Error("请选择支持的燃气物性模型。");
  const propellant=Object.hasOwn(inputs,"propellant_pair")?inputs.propellant_pair:"lox_methane";
  if(inputs.cycle_type!=="ffsc"||propellant!=="lox_methane")
    throw new Error("燃气物性模型选择仅适用于液氧／甲烷全流量补燃构型。");
  const values={gas_eos:eos,gas_species_policy:eos==="ideal-gas"?"full":"pr_supported_v1",combustion_loss_model:"adiabatic"};
  if(Object.entries(values).every(([key,value])=>inputs[key]===value))return inputs;
  return {...inputs,...values};
}

export function applyThermochemistryBaseline(inputs) {
  const propellant=Object.hasOwn(inputs,"propellant_pair")?inputs.propellant_pair:"lox_methane";
  if(inputs.cycle_type!=="ffsc" || propellant!=="lox_methane")return inputs;
  const values={gas_eos:"ideal-gas",gas_species_policy:"full",methane_thermo_dataset:"gri30_highT",combustion_loss_model:"adiabatic"};
  if(Object.entries(values).every(([key,value])=>inputs[key]===value))return inputs;
  return {...inputs,...values};
}

export function thermoModelDecisionState(inputs={},catalog=[]) {
  // Only omitted legacy fields use historical defaults. Invalid explicit
  // values must remain visible and must never appear runnable.
  const setting=(key,fallback)=>Object.hasOwn(inputs,key)?inputs[key]:fallback;
  const eos=setting("gas_eos","ideal-gas"),datasetId=setting("methane_thermo_dataset","gri30");
  const dataset=catalog.find(item=>item.id===datasetId),policy=setting("gas_species_policy","auto");
  const knownEos=typeof eos==="string"&&Object.hasOwn(EOS,eos),realGas=eos==="Peng-Robinson"||eos==="Peng-Robinson-Boston-Mathias";
  const restricted=policy==="pr_supported_v1" || (realGas&&policy==="auto");
  const full=policy==="full" || (eos==="ideal-gas"&&policy==="auto");
  const loss=setting("combustion_loss_model","legacy_temperature_rise");
  const knownDataset=typeof datasetId==="string"&&Object.hasOwn(DATASETS,datasetId);
  const baseline=eos==="ideal-gas"&&full&&datasetId==="gri30_highT"&&loss==="adiabatic";
  const routineSupported=eos==="ideal-gas"&&full&&knownDataset&&loss==="adiabatic";
  const runnableSupported=knownDataset&&loss==="adiabatic"&&(eos==="ideal-gas"&&full||realGas&&restricted);
  const sourceCount=dataset?.species_count??(knownDataset?53:null);
  const speciesCount=restricted?16:full?sourceCount:null;
  const unsupportedFull=realGas&&full;
  const speciesLabel=restricted?"16 种气相物种（不含 H、O、OH）":full?
    sourceCount===53?"53 种气相物种（含 H、O、OH）":`源数据气相物种${sourceCount?` · ${sourceCount} 种`:"（范围待核对）"}`:
    `未识别的物种范围：${policy}`;
  const mainEos=eos==="Peng-Robinson"?"ideal-gas":eos;
  const label=value=>typeof value==="string"&&Object.hasOwn(EOS,value)?EOS[value]:`未识别：${value}`;
  const stations=[
    {id:"ox_preburner",label:"富氧预燃室",eos:label(eos),chemistry:"化学平衡"},
    {id:"fuel_preburner",label:"富燃预燃室",eos:label(eos),chemistry:"化学平衡"},
    {id:"ox_turbine",label:"氧侧涡轮",eos:label(eos),chemistry:"冻结入口组成"},
    {id:"fuel_turbine",label:"燃料侧涡轮",eos:label(eos),chemistry:"冻结入口组成"},
    {id:"main_chamber",label:"主燃烧室",eos:label(mainEos),chemistry:loss==="adiabatic"?"化学平衡":loss==="legacy_temperature_rise"?"化学平衡后温升折减":"经验修正设置缺失或无效"},
    {id:"nozzle",label:"冷却燃气侧与喷管",eos:label(mainEos),chemistry:"主室组成冻结"},
  ];
  const limitations=[];
  if(restricted)limitations.push("当前集合未包含 H、O、OH 等解离物种，主燃烧室高温工况的适用性需单独评估。");
  if(unsupportedFull)limitations.push("当前组合不可计算：PR 参数未覆盖完整物种范围。应用所选模型配置后采用当前实现覆盖的 16 种物种。");
  if(!knownEos)limitations.push("燃气状态方程标识无效，请核对模型配置。");
  if(!restricted&&!full)limitations.push("物种策略标识无效，请核对模型配置。");
  if(!knownDataset)limitations.push("热化学数据集标识无效，请核对模型配置。");
  if(realGas)limitations.push("非理想平衡与状态反算增加计算量；高压混合物预测精度尚未完成试验验证。");
  if(loss==="legacy_temperature_rise")limitations.push("旧版本温升折减已启用：化学平衡计算后仍按原系数修正温升。原设置保留，重新计算前需应用所选模型配置。");
  if(!["adiabatic","legacy_temperature_rise"].includes(loss))limitations.push("经验温升修正设置缺失或无效，请核对历史方案配置。");
  return {
    eos,datasetId,policy,baseline,routineSupported,runnableSupported,realGas,restricted,speciesCount,sourceCount,unsupportedFull,knownEos,knownDataset,
    knownSpecies:restricted||full,loss,lossEnabled:loss==="legacy_temperature_rise",knownLoss:["adiabatic","legacy_temperature_rise"].includes(loss),
    eosLabel:label(eos),datasetLabel:knownDataset?DATASETS[datasetId]:`未识别：${datasetId}`,speciesLabel,
    profileLabel:eos==="ideal-gas"?"化学平衡 + 理想气体混合物":eos==="Peng-Robinson-Boston-Mathias"?
      "化学平衡 + 真实气体（PR–BM）":eos==="Peng-Robinson"?"化学平衡 + 分段燃气物性（PR / 理想气体）":"计算模型设置无效",
    backendLabel:eos==="Peng-Robinson-Boston-Mathias"?"项目 PR–BM 求解器；Cantera 物种热数据":knownEos?"Cantera：热力学与化学平衡运算；具体数据文件单独登记":"计算实现未识别",
    coverageLabel:eos==="Peng-Robinson"?"预燃室与涡轮：真实气体（PR）；主室、冷却燃气侧及喷管：理想气体混合物":knownEos?`预燃室、涡轮、主室、冷却燃气侧及喷管：${label(eos)}`:"覆盖范围未识别",
    lossLabel:loss==="adiabatic"?"未启用温升折减":loss==="legacy_temperature_rise"?"已启用旧版本温升折减":`未识别：${loss}`,
    temperatureRange:dataset?.common_temperature_range_K??null,
    validationLabel:realGas?"已有数值一致性校核；高压混合物试验验证未完成":"采用化学平衡基准；具体工况仍需检查数据温区与模型适用性",
    limitations,stations,
  };
}
