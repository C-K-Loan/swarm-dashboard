import { mountStateGraph } from './v02-state-graph.mjs';
// Uses saved, turn-specific state only. Never substitutes the final dataset graph.
const esc = (value: any) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const values = (obj: any): any[] => Array.isArray(obj) ? obj : Object.values(obj || {});
const raw = (label: string, value: any, id = '') => `<details class="state-raw"><summary>${esc(label)}</summary><pre ${id ? `id="${id}"` : ''}>${esc(JSON.stringify(value, null, 2))}</pre></details>`;
const badge = (label: any) => `<span class="badge">${esc(label ?? 'unknown')}</span>`;
function tasks(items: any[], changed: Set<string>) {
  return `<h3 class="section-label">Tasks · ${items.length}</h3>` + (items.length ? items.map(t => `<article class="entity state-task" data-task-id="${esc(t.id)}">
    <div class="entity-header"><h3>${esc(t.title ?? t.id)}</h3>${changed.has(t.id) ? badge('changed this turn') : ''}</div>
    <div class="tags">${badge(t.status)}${badge('importance: ' + (t.importance ?? 'unknown'))}${badge('complexity: ' + (t.complexity ?? 'unknown'))}</div>
    <p>${esc(t.summary ?? 'No summary recorded.')}</p>
    ${t.blocker ? `<p class="state-blocker">Blocker: ${esc(t.blocker.reason)} · human help: ${esc(t.blocker.need_human_help ?? 'unknown')}${t.blocker.waiting_on_actor ? ' · waiting on ' + esc(t.blocker.waiting_on_actor) : ''}</p>` : ''}
    ${t.identity_ambiguous ? `<p class="state-blocker">Ambiguous identity · candidates: ${esc((t.candidate_ids || []).join(', '))}</p>` : ''}
    <small class="mono">${esc(t.id)}${t.last_turn_id ? ' · last changed ' + esc(t.last_turn_id) : ''}</small>
  </article>`).join('') : '<p class="muted">No tasks recorded in this state.</p>');
}
function actors(items: any[]) {
  return `<details><summary>Actors · ${items.length}</summary><div class="state-table"><table><thead><tr><th>Actor</th><th>Role</th><th>State</th></tr></thead><tbody>${items.map(a => `<tr><td title="${esc(a.id)}">${esc(a.name ?? a.id)}</td><td>${esc(a.role ?? 'unlabeled')}</td><td>${esc(a.state ?? 'unknown')}</td></tr>`).join('')}</tbody></table></div><p class="muted">Catalog identities without a recorded state are shown as unknown.</p></details>`;
}
function relationships(items: any[], names: Map<string,string>) {
  const name = (id: string) => esc(names.get(id) || id);
  return `<details><summary>Relationships · ${items.length}</summary>${items.length ? items.map(r => `<p class="state-relation">${name(r.from_ref)} <strong>${esc(r.type.replaceAll('_',' '))}</strong> ${name(r.to_ref)}${r.task_ref ? ' · task: ' + name(r.task_ref) : ''}</p>`).join('') : '<p class="muted">No relationships recorded.</p>'}</details>`;
}
export function renderTurnState(turn: any) {
  const overall = document.getElementById('overall-state')!;
  const inputPane = document.getElementById('llm-state')!;
  document.getElementById('state-turn-label')!.textContent = `${turn.id} · ${turn.actor_name} · ${new Date(turn.end_ts * 1000).toISOString()}`;
  if (!turn.prediction) {
    overall.innerHTML = '<p class="empty">This turn has not been processed. No overall state after this turn is available.</p>';
    inputPane.innerHTML = '<p class="empty">No LLM input was recorded for this unprocessed turn.</p>';
    return;
  }
  const graph = turn.state_after;
  if (graph) {
    const allTasks = values(graph.tasks), allActors = values(graph.actors), decisions = values(graph.decisions);
    const names = new Map<string,string>([...allTasks.map(t => [t.id,t.title] as [string,string]), ...allActors.map(a => [a.id,a.name] as [string,string])]);
    overall.innerHTML = `<p class="state-counts">${allTasks.length} tasks · ${decisions.length} decisions · ${graph.relationships.length} relationships</p>`
      + '<div id="overall-state-graph"></div>'
      + tasks(allTasks, new Set(turn.resolved_prediction?.tasks?.map((t:any) => t.ref) || []))
      + actors(allActors)
      + `<details><summary>Decisions · ${decisions.length}</summary>${decisions.map(d => `<article class="entity"><p>${esc(d.summary)}</p><div class="tags">${badge(d.importance)}${badge(names.get(d.made_by_actor_id) || d.made_by_actor_id)}</div><small>${esc(d.turn_id)} · tasks: ${esc(d.affected_task_refs.map((id:string) => names.get(id) || id).join(', '))}</small></article>`).join('') || '<p class="muted">No decisions recorded.</p>'}</details>`
      + relationships(graph.relationships, names)
      + raw('Project description known at this turn', graph.project_description)
      + raw('Full accumulated state JSON', graph, 'overall-state-json');
    mountStateGraph(document.getElementById('overall-state-graph')!, graph, {label:'Overall state graph after '+turn.id,changed:new Set(turn.resolved_prediction?.tasks?.map((t:any)=>t.ref)||[])});
  } else overall.innerHTML = '<p class="empty">No historical snapshot is available for this turn.</p>';
  const input = turn.llm_input;
  if (!input) { inputPane.innerHTML = '<p class="empty">The original input was not saved. It has not been reconstructed from later state.</p>'; return; }
  const contextTasks = input.relevant_existing_tasks_with_ids || [];
  const contextActors = input.relevant_actors_and_relationships?.actors || [];
  const names = new Map<string,string>([...contextTasks.map((t:any) => [t.id,t.title] as [string,string]), ...contextActors.map((a:any) => [a.id,a.name] as [string,string])]);
  inputPane.innerHTML = `<p class="state-counts">${contextTasks.length} prior tasks supplied · ${(input.recent_relevant_turns || []).length} recent turns</p>`
    + '<div id="llm-state-graph"></div>'
    + `<article class="entity"><h3>Actor state before this turn</h3><p>${esc(input.actor_id_and_current_state?.name ?? turn.actor_name)}</p><div class="tags">${badge(input.actor_id_and_current_state?.role)}${badge(input.actor_id_and_current_state?.state)}</div></article>`
    + tasks(contextTasks, new Set()) + actors(contextActors)
    + relationships(input.relevant_actors_and_relationships?.relationships || [], names)
    + raw('Project description supplied', input.project_description)
    + raw('Recent turns supplied', input.recent_relevant_turns)
    + raw('Current turn metadata and prose supplied', {metadata:input.current_turn_metadata,prose:input.current_turn_prose})
    + raw('Exact recorded input JSON', input, 'llm-state-json');
  mountStateGraph(document.getElementById('llm-state-graph')!, {tasks:contextTasks,actors:contextActors,relationships:input.relevant_actors_and_relationships?.relationships||[],decisions:[]}, {label:'Recorded LLM input graph before '+turn.id});
}
