// Finish-time selection; never use the dataset's final graph for historical views.
export const values = value => Array.isArray(value) ? value : Object.values(value || {});
export function selectionWindows(selection) {
  const raw=selection.windows ?? (selection.sessionIds || []).map(sid => ({sid,start:selection.startTs,end:selection.endTs}));
  const sorted=raw.filter(w=>w.sid&&Number.isFinite(w.start)&&Number.isFinite(w.end)&&w.start<w.end).map(w=>({...w})).sort((a,b)=>a.sid.localeCompare(b.sid)||a.start-b.start||a.end-b.end);
  const merged=[];
  for(const w of sorted){const prev=merged.at(-1);if(prev&&prev.sid===w.sid&&w.start<=prev.end)prev.end=Math.max(prev.end,w.end);else merged.push({sid:w.sid,start:w.start,end:w.end});}
  return merged;
}
export function selectedTurns(turns, selection) {
  const windows = selectionWindows(selection);
  return turns.filter(t => windows.some(w => w.sid === t.session_id && t.end_ts >= w.start && t.end_ts < w.end))
    .sort((a,b) => a.end_ts-b.end_ts || a.ordinal-b.ordinal);
}
export function replayFrames(rows, grain = 'turn') {
  const ready = rows.filter(t => t.prediction && (t.state_after || t.lazy)); // lazy: state_after fetched per cursor turn
  if (grain === 'turn') return ready;
  // One endpoint per occupied UTC hour, not an invented snapshot at hour-end.
  return [...new Map(ready.map(t => [Math.floor(t.end_ts / 3600),t])).values()];
}
export function changesAtCursor(rows, cursor) {
  const tasks = {}, actors = {}, decisions = {}, relationships = new Map();
  for (const t of rows) {
    if (t.ordinal > cursor.ordinal || !t.prediction) continue;
    Object.assign(tasks,t.task_states || {});
    if (t.actor_state_after) actors[t.actor_id] = t.actor_state_after;
    for (const d of t.resolved_prediction?.decisions || []) decisions[d.id || d.ref] = d;
    for (const r of t.resolved_prediction?.relationships || []) relationships.set(JSON.stringify([r.type,r.from_ref,r.to_ref,r.task_ref || null]),r);
  }
  return {tasks,actors,decisions,relationships:[...relationships.values()]};
}
export function scopedState(rows, cursor, sessionIds) {
  const state = cursor.state_after;
  const actors = new Set(values(state.actors).filter(a => sessionIds.includes(a.session_id)).map(a => a.id));
  const tasks = new Set(Object.keys(changesAtCursor(rows,cursor).tasks));
  for (const r of state.relationships || []) {
    if (actors.has(r.from_ref) || actors.has(r.to_ref)) {
      if (r.task_ref) tasks.add(r.task_ref);
      if (state.tasks[r.to_ref]) tasks.add(r.to_ref);
    }
  }
  // Include upstream task context only; don't fan out into unrelated sibling tasks.
  let grew = true;
  while (grew) {
    grew = false;
    for (const r of state.relationships || []) if (['depends_on','is_sub_task'].includes(r.type) && tasks.has(r.from_ref) && !tasks.has(r.to_ref)) { tasks.add(r.to_ref); grew = true; }
  }
  const relationships = (state.relationships || []).filter(r => ['depends_on','is_sub_task'].includes(r.type)
    ? tasks.has(r.from_ref) && tasks.has(r.to_ref)
    : r.task_ref ? tasks.has(r.task_ref) : tasks.has(r.to_ref));
  const decisions = values(state.decisions).filter(d => actors.has(d.made_by_actor_id) || d.affected_task_refs?.some(id => tasks.has(id)));
  for (const r of relationships) for (const id of [r.from_ref,r.to_ref]) if (state.actors[id]) actors.add(id);
  for (const d of decisions) actors.add(d.made_by_actor_id);
  return {...state,tasks:values(state.tasks).filter(t => tasks.has(t.id)),actors:values(state.actors).filter(a => actors.has(a.id)),decisions,relationships};
}
export function inputState(input) {
  if (input?.context_version === 'v0.3-lean') return leanInputState(input);
  return {tasks:input?.relevant_existing_tasks_with_ids || [],actors:input?.relevant_actors_and_relationships?.actors || [],relationships:input?.relevant_actors_and_relationships?.relationships || [],decisions:[]};
}
// v0.3-lean input uses aliases (T3, A2): connected tasks in full, other active tasks as [alias,title], relationships as [type,from,to,task_ref?].
function leanInputState(input) {
  const tasks = [...(input.active_tasks_connected_to_actor || []), ...(input.other_active_tasks || []).map(([id, title]) => ({id, title, index_only: true}))];
  const actors = (input.actors || []).map(([id, name]) => ({id, name}));
  const me = input.current_actor;
  if (me?.id) { const i = actors.findIndex(a => a.id === me.id); if (i >= 0) actors[i] = {...actors[i], ...me}; else actors.push({...me}); }
  const relationships = (input.relationships || []).map(([type, from_ref, to_ref, task_ref]) => ({type, from_ref, to_ref, ...(task_ref ? {task_ref} : {})}));
  return {tasks, actors, relationships, decisions: []};
}
