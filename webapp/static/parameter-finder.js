// Find the current case's visible settings without changing inputs or navigation.
import {parameterGroups, parameterVisible, parameterPresentation, parameterSearchText} from "./parameter-editor.js";
import {setupSection,setupNavigationGroup} from "./setup-tree.js";

const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const normalize = value => String(value ?? "").normalize("NFKC").trim().toLocaleLowerCase().replace(/\s+/g, " ");

function nameRank(label, query, words) {
  const compactQuery = words.join("");
  if (label === query || label === compactQuery) return 0;
  if (label.startsWith(query) || label.startsWith(compactQuery)) return 1;
  if (words.every(word => label.includes(word))) return 2;
  if (words.some(word => label.includes(word))) return 3;
  return 4;
}

export function findDesignParameters(groups, inputs, query) {
  const search = normalize(query);
  if (!search) return [];
  const words = search.split(" "), matches = [];
  let order = 0;
  for (const group of parameterGroups(groups, inputs)) {
    const section=setupSection(group.id);
    const stage={components:"部件设计与特性建模",results:"校核与记录"}[setupNavigationGroup(group.id)]||"总体方案与循环设计";
    const path = ["configuration","requirements","constraints"].includes(section.id)?`${stage} / ${group.title || group.id}`:`${stage} / ${section.label} / ${group.title || group.id}`;
    for (const field of group.fields) {
      // Retain the explicit visibility guard: mode-dependent or inapplicable
      // fields must never become a result through their help text alone.
      if (!parameterVisible(field, inputs)) continue;
      const view = parameterPresentation(field, inputs);
      const label = view.label || field.key, unit = view.unit || "";
      const name = normalize(label), fullName = normalize(view.full_label);
      const text = normalize(parameterSearchText(field, inputs, group.title));
      const fieldOrder = order++;
      if (!words.every(word => text.includes(word))) continue;
      const rank = Math.min(nameRank(name, search, words), fullName ? nameRank(fullName, search, words) : 4);
      matches.push({groupId:group.id, fieldKey:field.key, label, unit, path, rank, order:fieldOrder});
    }
  }
  matches.sort((left, right) => left.rank - right.rank || left.order - right.order);
  return matches.map(({rank, order, ...match}) => match);
}

export function renderParameterMatches(matches, query) {
  const search = String(query ?? "").trim();
  if (!search) return '<p class="parameter-finder-empty">输入参数名称，查找当前方案中的设置。</p>';
  if (!matches.length) return `<p class="parameter-finder-empty">没有找到与“${escape(search)}”匹配的参数。请尝试参数名称或单位。</p>`;
  return `<ul class="parameter-matches">${matches.map(match => `<li><button type="button" class="parameter-match" data-parameter-match-group="${escape(match.groupId)}" data-parameter-match-field="${escape(match.fieldKey)}"><span class="parameter-match-label">${escape(match.label)}${match.unit ? ` <span class="parameter-match-unit">[${escape(match.unit)}]</span>` : ""}</span><small class="parameter-match-path">${escape(match.path)}</small></button></li>`).join("")}</ul>`;
}
