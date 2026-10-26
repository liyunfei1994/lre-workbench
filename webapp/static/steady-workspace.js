// Presentation-only navigation for one immutable steady-state record.
const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
let sequence = 0;

export function renderSteadyWorkspace(pages) {
  const prefix = `steady-view-${++sequence}`;
  const selected = pages.some(page => page.id === "overview") ? "overview" : pages[0]?.id;
  const tabs = pages.map(page => `<button type="button" role="tab" id="${prefix}-tab-${escape(page.id)}" data-steady-tab="${escape(page.id)}" aria-controls="${prefix}-panel-${escape(page.id)}" aria-selected="${page.id === selected}" tabindex="${page.id === selected ? 0 : -1}">${escape(page.title)}${page.meta ? `<small>${escape(page.meta)}</small>` : ""}</button>`).join("");
  const panels = pages.map(page => `<section class="steady-page analysis-stack" role="tabpanel" id="${prefix}-panel-${escape(page.id)}" data-steady-panel="${escape(page.id)}" aria-labelledby="${prefix}-tab-${escape(page.id)}" tabindex="0"${page.id === selected ? "" : " hidden"}>${page.content}</section>`).join("");
  return `<div class="steady-workspace"><div class="steady-tabs" role="tablist" aria-label="稳态分析内容">${tabs}</div>${panels}</div>`;
}

export function mountSteadyWorkspace(host, recordId = "") {
  if (!host) return;
  const workspace = host.querySelector(".steady-workspace");
  if (!workspace) return;
  if (host.dataset.steadyRecordId !== String(recordId)) {
    host.dataset.steadyRecordId = String(recordId);
    host.dataset.steadySelected = "overview";
  }
  const activate = (root, id, focus = false) => {
    const tabs = [...root.querySelectorAll("[data-steady-tab]")];
    const selected = tabs.find(tab => tab.dataset.steadyTab === id) || tabs[0];
    if (!selected) return;
    host.dataset.steadySelected = selected.dataset.steadyTab;
    for (const tab of tabs) {
      const active = tab === selected;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    }
    for (const panel of root.querySelectorAll("[data-steady-panel]"))
      panel.hidden = panel.dataset.steadyPanel !== selected.dataset.steadyTab;
    if (focus) selected.focus();
  };
  activate(workspace, host.dataset.steadySelected || "overview");
  // Listeners resolve the current workspace after every rerender; no stale DOM
  // or record is captured, and a modal has its own host and selected page.
  if (host._steadyWorkspaceBound) return;
  host._steadyWorkspaceBound = true;
  host.addEventListener("click", event => {
    const target = event.target?.closest?.("[data-steady-tab], [data-steady-open]");
    const root = host.querySelector(".steady-workspace");
    if (!target || !root || !root.contains(target)) return;
    const jump = target.dataset.steadyOpen;
    activate(root, jump || target.dataset.steadyTab, !!jump);
    if (jump) root.querySelector("[role=tablist]")?.scrollIntoView?.({block:"nearest"});
  });
  host.addEventListener("keydown", event => {
    if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    const target = event.target?.closest?.("[data-steady-tab]");
    const root = host.querySelector(".steady-workspace");
    if (!target || !root || !root.contains(target)) return;
    const tabs = [...root.querySelectorAll("[data-steady-tab]")], index = tabs.indexOf(target);
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    activate(root, tabs[next].dataset.steadyTab, true);
  });
}
