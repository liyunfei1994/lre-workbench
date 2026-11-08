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
  const groups = activeGroups.map(({id, title, fields}) => {
    const fieldKeys = fields.map(field => field.key);
    // Use the active (visibility-filtered) fields: historical cooling budgets
    // remain editable, while a methods-only group has no parameter to confirm.
    const requiresReview = fields.some(field => !field.read_only);
    const groupIssues = validIssues.filter(issue => issue.fields.some(key => fieldKeys.includes(key)));
    for (const issue of groupIssues) mapped.add(issue);
    let status = validationPhase;
    if (groupIssues.length) status = "invalid";
    else if (validationPhase === "invalid") status = requiresReview ? "filled" : "readonly";
    else if (validationPhase === "ready" && !requiresReview) status = "readonly";
    else if (validationPhase === "ready" && groupReviews) {
      const review = groupReviews.get(id);
      status = review === "preset" || review === "confirmed" ? review : "review";
    }
    return {id, title, fieldKeys, issues: groupIssues, status, requiresReview};
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

export function renderDesignReadiness(model) {
  const {phase, groups, issues, readyCount, readonlyCount = 0, pendingReviewCount, warnings = []} = model;
  const hasReviewedGroups = groups.some(group => group.status === "preset" || group.status === "confirmed");
  const message = {
    ready: !readyCount ? "输入校验通过，可提交设计点计算。"
      : hasReviewedGroups ? `${readyCount} 个参数分组已确认或采用示例，输入校验通过，可提交设计点计算。`
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
    return `<li>${groupLink(group, field, group.title)}<span class="design-readiness-group-status" data-status="${escape(group.status)}">${designReadinessStatusLabels[group.status]}</span></li>`;
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
