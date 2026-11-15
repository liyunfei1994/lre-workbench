// A withdrawal notice is derived from saved identities; raw results stay unchanged.
const BM="Peng-Robinson-Boston-Mathias",withdrawn="pr_boston_mathias_v1";
export const withdrawnModelReason="此记录使用已撤回的 PR–BM v1 物理模型：化学平衡压力项被重复计入，保存的组成、温度及整机性能不能作为有效设计结果。原始记录仅供追溯；禁止用于新的校核对比或设计交接，请使用修正模型重新运行设计点。";
export function savedModelValidity(result){
  function contains(value){
    if(!value||typeof value!=="object")return false;
    const version=typeof value.eos_model==="object"?value.eos_model?.version:value.eos_model;
    if((value.eos??value.candidate_eos)===BM&&version===withdrawn)return true;
    return Object.values(value).some(item=>item&&typeof item==="object"&&contains(item));
  }
  const blocked=contains(result);
  return {status:blocked?"invalid_superseded":"not_listed_invalid",reuse_blocked:blocked,reason:blocked?withdrawnModelReason:null};
}
export function recordModelValidity(record){
  const derived=savedModelValidity(record?.result);
  return derived.reuse_blocked?derived:record?.model_validity??derived;
}
export function renderModelValidity(record){
  const state=recordModelValidity(record);
  if(!state.reuse_blocked)return "";
  const escaped=String(state.reason||withdrawnModelReason).replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  return `<section class="panel" role="alert" aria-label="物理模型已撤回"><h2>物理模型已撤回 · 结果无效</h2><p>${escaped}</p><p>下方数值为原始历史输出，不表示有效性能或物理闭合。</p></section>`;
}
