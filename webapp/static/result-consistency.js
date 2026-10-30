const esc = value => String(value ?? "").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
export function renderResultConsistency(audit){
  if(!audit)return "";
  const checks=Array.isArray(audit.checks)?audit.checks:[];
  const conflicts=checks.filter(c=>c.status==="conflict"),missing=checks.filter(c=>c.status==="partial" || c.status==="missing");
  const title=conflicts.length?`记录存在 ${conflicts.length} 项数值冲突，请核对来源后重新计算`:
    `跨页一致性：已核对 ${audit.checked_count ?? 0} 项${missing.length?` · ${missing.length} 项缺少对照数据`:""}`;
  const rows=(conflicts.length?[...conflicts,...missing]:missing).map(c=>`<li><b>${esc(c.label || c.id)}</b> ${esc(c.note || "")}<small>${(c.values||[]).map(v=>`${esc(v.path)} = ${esc(v.value ?? "未记录")}`).join("； ")}</small></li>`).join("");
  return `<details class="record-consistency ${conflicts.length?"has-conflicts":""}"${conflicts.length?" open":""}><summary>${esc(title)}</summary><p>核对同一计算记录的节点、部件、单位副本及几何快照。部件图表优先使用保存的 SI 原值；历史冲突不会被当前草稿覆盖。一致不等于物理模型已经验证。</p>${rows?`<ul>${rows}</ul>`:'<p>已覆盖的重复量未发现差异。</p>'}</details>`;
}
