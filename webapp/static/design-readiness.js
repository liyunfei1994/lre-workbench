import {parameterGroups, parameterPresentation, parameterValue} from "./parameter-editor.js";

const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));

// This pass detects incomplete edits. The server remains responsible for ranges,
// cross-field constraints, and checks on fields outside the active editor.
export function collectLocalReadiness(groups, draft) {
  const issues = [], seen = new Set();
  for (const group of parameterGroups(groups, draft)) {
    for (const field of group.fields) {
      if (field.virtual || field.read_only || seen.has(field.key)) continue;
      seen.add(field.key);
      const value = draft[field.key], {label} = parameterPresentation(field, draft);
      if (field.nullable && value == null) continue;
      const numeric = typeof field.default === "number" || ["number", "int"].includes(field.type);
      const missing = value == null || value === "";
      let message = "";
      if (numeric && missing) message = `请填写${label}。`;
      else if (numeric && !Number.isFinite(value)) message = `${label}必须为有限数值。`;
      else if (field.type === "int" && !Number.isInteger(value)) message = `${label}必须为整数。`;
      else if (field.choices && !field.choices.some(choice => String(choice.value) === String(numeric ? value : parameterValue(field, draft)))) {
        message = `请选择有效的${label}。`;
      } else if (field.required && missing) message = `请填写${label}。`;
      if (message) issues.push({fields: [field.key], message});
    }
  }
  return {issues};
}

export function buildDesignReadiness(activeGroups, issues = [], {phase = "pending", warnings = [], groupReviews = null} = {}) {
  const validIssues = issues.filter(issue => typeof issue?.message === "string" && issue.message.trim()).map(issue => ({
    fields: Array.isArray(issue.fields) ? [...new Set(issue.fields.filter(key => typeof key === "string"))] : [],
    message: issue.message,
  }));
  const validationPhase = validIssues.length ? "invalid" : phase;
  const mapped = new Set();
  const groups = activeGroups.map(({id, title, fields, requires_review, configuration_context, numerical_configuration, optional_configuration, configuration_label, deferred_design}) => {
    const contextKeys = configuration_context ? [configuration_context].flat().map(item => item.key) : [];
    const fieldKeys = [...new Set([...fields.map(field => field.key), ...contextKeys])];
    // A fixed calculation model still needs explicit acceptance. Methods-only
    // explanations without such a requirement remain read-only.
    const requiresReview = !numerical_configuration && !optional_configuration && !deferred_design && (requires_review === true || fields.some(field => !field.read_only));
    const groupIssues = validIssues.filter(issue => issue.fields.some(key => fieldKeys.includes(key)));
    for (const issue of groupIssues) mapped.add(issue);
    let status = validationPhase;
    if (groupIssues.length) status = "invalid";
    else if (validationPhase === "invalid") status = requiresReview || numerical_configuration || optional_configuration ? "filled" : "readonly";
    else if (validationPhase === "ready" && (numerical_configuration || optional_configuration || deferred_design)) status = "ready";
    else if (validationPhase === "ready" && !requiresReview) status = "readonly";
    else if (validationPhase === "ready" && groupReviews) {
      const review = groupReviews.get(id);
      status = review === "preset" || review === "confirmed" ? review : "review";
    }
    return {id, title, fieldKeys, issues: groupIssues, status, requiresReview,
      numericalConfiguration:Boolean(numerical_configuration),
      optionalConfiguration:Boolean(optional_configuration),
      deferredDesign:Boolean(deferred_design),
      configurationLabel:status==="ready" && deferred_design ? "部件草案" : (numerical_configuration || optional_configuration) && status==="ready" ? configuration_label || "配置有效" : null};
  });
  const pendingReviewCount = groups.filter(group => group.status === "review").length;
  return {phase: pendingReviewCount ? "review" : validationPhase, validationPhase, groups, issues: validIssues,
    globalIssues: validIssues.filter(issue => !mapped.has(issue)), pendingReviewCount,
    readyCount: groups.filter(group => ["ready", "preset", "confirmed"].includes(group.status)).length,
    readonlyCount: groups.filter(group => group.status === "readonly").length,
    warnings: validationPhase === "ready" ? warnings.filter(note => typeof note === "string" && note.trim()) : []};
}

export const designReadinessStatusLabels = {
  ready: "✓ 已就绪", invalid: "待修正", filled: "已填写", pending: "待检查", unavailable: "未完成检查",
  preset: "采用示例", confirmed: "✓ 已确认", review: "待确认", readonly: "只读说明",
};

function groupLink(group, field, caption) {
  const fieldAttr = field ? ` data-readiness-field="${escape(field)}"` : "";
  return `<button type="button" class="design-readiness-link" data-readiness-group="${escape(group.id)}"${fieldAttr}>${escape(caption)}</button>`;
}

// Parameter pages describe only the active group. Whole-design checks, warnings
// without a field attribution, and run eligibility belong to the run review.
export function renderDesignGroupReadiness(model, groupId, {freshness = null, hasResult = false} = {}) {
  const group = model?.groups?.find(item => item.id === groupId);
  if (!group) {
    const message = groupId
      ? "此设置项未列入参数检查清单。相关说明以当前页面内容为准。"
      : "请选择左侧设置项，查看本组参数状态。";
    return `<section class="design-readiness design-group-readiness" data-readiness-state="unselected" aria-label="本组参数状态">
      <div class="design-readiness-heading" aria-live="polite"><b>本组参数状态</b><span class="design-readiness-message">${message}</span></div>
    </section>`;
  }
  const validationPhase = model.validationPhase || model.phase;
  // A stored confirmation is not evidence that the latest input check finished.
  const status = ["pending", "checking", "unavailable"].includes(validationPhase) ? validationPhase : group.status;
  const labels = {...designReadinessStatusLabels, pending:"检查中", checking:"检查中", ready:"校验通过"};
  if(group.configurationLabel)labels.ready=group.configurationLabel;
  const messages = {
    pending: "正在检查本组参数，确认状态将在检查完成后更新。",
    checking: "正在检查本组参数，确认状态将在检查完成后更新。",
    unavailable: "参数检查未完成，请重新检查。",
    invalid: "本组存在输入问题，请按下列提示修正。",
    filled: "本组未发现已定位的输入问题；整机检查尚未通过，本组确认状态待复核。",
    readonly: "本组为只读说明，无需确认参数。",
    confirmed: "本组参数已确认并保存。修改参数后需重新确认。",
    preset: "本组已采用示例参数。请结合设计要求核对取值。",
    review: "本组输入校验通过。请核对取值并确认本组参数。",
    ready: "本组输入校验通过。",
  };
  if(group.numericalConfiguration)messages.ready="本次数值配置已通过输入检查，随方案保存；计算后仍需检查完整收敛证据。";
  if(groupId==="preburner_design"){
    labels.confirmed="✓ 输入已确认";
    messages.confirmed="预燃室设计工作条件已确认并保存；计算结果与数值收敛分别核对。";
  }
  if(group.optionalConfiguration)messages.ready="限值配置有效，随方案保存，无需额外确认。未设置上限不妨碍循环匹配；工程校核结论以计算记录为准。";
  const issueList = (group.issues || []).map(issue => {
    const field = issue.fields?.find(key => group.fieldKeys.includes(key));
    return `<li><span>${escape(issue.message)}</span>${field ? groupLink(group, field, "定位参数") : ""}</li>`;
  }).join("");
  const groupChanged = freshness?.changedGroups?.some(item => (typeof item === "string" ? item : item.id) === groupId);
  const resultDifference = `${groupChanged ? "本组输入" : freshness?.changedGroups?.length ? "其他输入" : "当前输入"}与所选计算记录不同。`;
  const resultMessage = resultDifference + (hasResult
    ? "确认与保存仅更新输入，历史结果仍使用原计算条件。"
    : "该记录尚无可用计算结果，确认与保存不会更新已提交的任务。");
  const resultNote = freshness?.state === "stale" ? `<div class="design-group-result-status" data-record-state="stale"${hasResult ? ' data-result-state="stale"' : ""} aria-label="所选计算记录状态">
    <div class="design-readiness-heading"><b>${hasResult ? "所选结果需重算" : "所选任务输入与当前方案不同"}</b><span class="design-readiness-message">${resultMessage}</span></div>
    <button type="button" class="design-readiness-link" data-workspace-view="design_run">前往输入检查与运行</button>
  </div>` : "";
  return `<section class="design-readiness design-group-readiness" data-readiness-state="${escape(status)}" aria-label="本组参数状态">
    <div class="design-readiness-heading" aria-live="polite"><b>本组参数状态</b>
    <span class="design-readiness-group-status" data-status="${escape(status)}">${escape(labels[status] || "待检查")}</span>
    <span class="design-readiness-message">${escape(messages[status] || "本组参数状态待检查。")}</span></div>
    ${status === "unavailable" ? '<button class="design-readiness-link" type="button" data-readiness-retry>重新检查</button>' : ""}
    ${issueList ? `<ul class="design-readiness-issues">${issueList}</ul>` : ""}
    ${model.warnings?.length ? '<p class="fine">整机输入提示见“输入检查与运行”。</p>' : ""}
    ${resultNote}
  </section>`;
}

export function renderDesignReadiness(model) {
  const {phase, groups, issues, readyCount, readonlyCount = 0, pendingReviewCount, warnings = []} = model;
  const hasReviewedGroups = groups.some(group => group.status === "preset" || group.status === "confirmed");
  const message = {
    ready: !readyCount ? "输入校验通过，可提交设计点计算。"
      : hasReviewedGroups ? `${readyCount} 个适用分组已就绪，设计条件已确认或采用示例，数值配置已通过检查，可提交设计点计算。`
      : `${readyCount} 个适用分组已通过输入校验，可提交设计点计算。`,
    review: `输入校验通过。还有 ${pendingReviewCount} 组参数待确认。`,
    invalid: "请修正以下输入后运行设计点。",
    pending: "正在检查当前参数。",
    unavailable: "未能完成参数校验，请重试。",
  }[phase];
  const issueList = issues.map(issue => {
    const links = groups.filter(group => group.issues.includes(issue)).map(group =>
      groupLink(group, issue.fields.find(key => group.fieldKeys.includes(key)), `定位：${group.title}`)).join("");
    return `<li><span>${escape(issue.message)}</span>${links}</li>`;
  }).join("");
  const items = groups.map(group => {
    const field = group.issues[0]?.fields.find(key => group.fieldKeys.includes(key));
    return `<li>${groupLink(group, field, group.title)}<span class="design-readiness-group-status" data-status="${escape(group.status)}">${escape(group.configurationLabel || designReadinessStatusLabels[group.status])}</span></li>`;
  }).join("");
  const firstReview = groups.find(group => group.status === "review");
  return `<section class="design-readiness" data-readiness-state="${escape(phase)}" aria-label="计算前检查">
    <div class="design-readiness-heading" aria-live="polite"><b>计算前检查</b>
    <span class="design-readiness-group-status" data-status="${escape(phase)}">${designReadinessStatusLabels[phase]}</span>
    <span class="design-readiness-message">${escape(message)}${readonlyCount ? ` ${readonlyCount} 个只读分组无需确认。` : ""}</span></div>
    ${firstReview ? groupLink(firstReview, null, "查看待确认分组") : ""}
    ${phase === "unavailable" ? '<button class="design-readiness-link" type="button" data-readiness-retry>重新检查</button>' : ""}
    ${issueList ? `<ul class="design-readiness-issues">${issueList}</ul>` : ""}
    ${warnings.length ? `<details class="design-readiness-warnings"><summary>输入提示（${warnings.length}）</summary><ul>${warnings.map(note => `<li>${escape(note)}</li>`).join("")}</ul></details>` : ""}
    ${items ? `<details class="design-readiness-groups"><summary>分组状态（${groups.length}）</summary><ul>${items}</ul></details>` : ""}
  </section>`;
}
