import {mountStateGraph} from './v02-state-graph.mjs';
import {selectionWindows,selectedTurns,replayFrames,changesAtCursor,scopedState,inputState,values} from './v02-selection-state.mjs';
// Server filters by exact windows; full review.json is too big to ship (quadratic state copies).
// The labels run is global: read it from the selection bus, falling back to the header dropdown.
const teacherRun=()=>(window as any)._selectionBus?.labels||(document.getElementById('v02-teacher') as HTMLSelectElement|null)?.value||'';
const controllers=new WeakMap<HTMLElement,{dispose:()=>void}>();
const esc=(s:any)=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const stamp=(t:number)=>new Date(t*1000).toISOString().replace('T',' ').replace(/\.\d+Z$/,' UTC');
export function disposeV02Summary(body:HTMLElement){
 controllers.get(body)?.dispose();controllers.delete(body);delete body.dataset.v02Selection;delete body.dataset.v02Loading;
}
export async function renderV02Summary(body:HTMLElement,sub:HTMLElement|null,selection:any){
 const run=teacherRun(),key=JSON.stringify([run,selectionWindows(selection)]);
 if(body.dataset.v02Selection===key&&(controllers.has(body)||body.dataset.v02Loading===key))return; // same selection already shown or loading
 disposeV02Summary(body);body.dataset.v02Selection=key;
 body.innerHTML='<p class="hint">Loading V0.2 task states…</p>';
 body.dataset.v02Loading=key;
 try{
  const res=await fetch('/api/v02/selection',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset:run||null,windows:selectionWindows(selection)})});
  if(!res.ok)throw new Error('Dataset unavailable');
  const data=await res.json();if(body.dataset.v02Selection!==key)return;
  let all=selectedTurns(data.turns,selection),rows=replayFrames(all);const total=all.length+(data.omitted||0);
  if(sub)sub.textContent=` · ${rows.length} completed turns · ${total-rows.length} pending`;
  if(!rows.length){body.innerHTML='<p class="hint">No generated V0.2 turns in this selection. Only turns this model has labeled so far have a state graph.</p><a href="/review'+(run?'?dataset='+encodeURIComponent(run):'')+'">Open prediction review →</a>';return;}
  // Window for display pruning: selection span, cut at the cursor turn when drawing.
  const wins=selectionWindows(selection),winStart=Math.min(...wins.map((w:any)=>w.start)),winEnd=Math.max(...wins.map((w:any)=>w.end));
  const sessions=[...new Set((selection.windows||[]).map((w:any)=>w.sid).concat(selection.sessionIds||[]))];
  let frames=rows,index=frames.length-1,timer:ReturnType<typeof setInterval>|null=null,disposed=false;
  // Wide selections arrive capped at the newest page of labeled turns; older pages load when stepping past the start.
  let more=data.capped||0,paging:Promise<void>|null=null;const predictedTotal=data.predicted||rows.length;
  const loadOlder=()=>paging||(paging=(async()=>{
   try{
    const r=await fetch('/api/v02/selection',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset:run||null,windows:selectionWindows(selection),offset:data.turns.length})});
    if(!r.ok)throw new Error('page '+r.status);const d=await r.json();if(disposed)return;
    const at=frames[index]?.id;data.turns=d.turns.concat(data.turns);more=d.turns.length?d.capped||0:0;
    all=selectedTurns(data.turns,selection);rows=replayFrames(all);frames=replayFrames(rows,control('grain').value);
    index=Math.max(0,frames.findIndex((t:any)=>t.id===at));
   }finally{paging=null;}
  })());
  body.innerHTML=`<section class="v02 timeline-v02-summary" data-fullscreen-root aria-label="Selected state and replay">
   <div class="v02-bar" role="group" aria-label="View and replay controls">
    <select data-control="view" aria-label="State view" title="state view"><option value="state">State at cursor</option><option value="changes">Changes in selection</option><option value="input">Recorded LLM input</option></select>
    <label data-whole-label title="show the whole project state instead of the selected agents"><input type="checkbox" data-control="whole"> whole</label>
    <span class="v02-replay" role="group" aria-label="Replay selected turns"><button data-action="first" aria-label="First replay frame" title="first">⏮</button><button data-action="prev" aria-label="Previous replay frame" title="previous">←</button><button data-action="play" aria-label="Play selected turns">Play</button><button data-action="next" aria-label="Next replay frame" title="next">→</button><button data-action="last" aria-label="Last replay frame" title="last">⏭</button></span>
    <select data-control="grain" aria-label="Replay step" title="replay step"><option value="turn">turn</option><option value="hour">hour (UTC)</option></select>
    <select data-control="speed" aria-label="Replay speed" title="replay speed"><option value="1500">slow</option><option value="800" selected>normal</option><option value="300">fast</option></select>
    <input class="v02-scrubber" type="range" min="0" data-control="cursor" aria-label="Replay cursor"><span class="mono" data-cursor aria-live="polite"></span>
    <a data-review href="/review" title="open this turn in prediction review">Review ↗</a>
   </div>
   <div data-state-content></div>
   <details><summary>Selection semantics and coverage</summary><p class="muted" data-explanation></p><p class="muted" data-coverage></p></details>
   <p class="muted v02-history-note">Original machine history · human corrections remain in Review until a reviewed replay is built. Replay is local; no model calls.</p>
  </section>`;
  const root=body.querySelector('section')!;
  const el=(s:string)=>root.querySelector(s) as HTMLElement;
  const control=(name:string)=>el(`[data-control="${name}"]`) as HTMLInputElement;
  const action=(name:string)=>el(`[data-action="${name}"]`) as HTMLButtonElement;
  const stop=()=>{if(timer)clearInterval(timer);timer=null;action('play').textContent='Play';action('play').setAttribute('aria-label','Play selected turns');root.dataset.playing='false';};
  controllers.set(body,{dispose:()=>{disposed=true;stop();bodyResize?.disconnect();}});
  // Keyboard interaction inside this widget must not move/clear Agent Timeline selection.
  root.addEventListener('keydown',e=>{if(['Escape','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown',' '].includes(e.key))e.stopPropagation();});
  // Selection rows are light; the cursor turn's state_after/llm_input/messages load on demand (cached per turn).
  const full=new Map<string,any>(),loaded=new Set<string>();
  const loadTurn=(id:string)=>{if(!full.has(id))full.set(id,fetch(`/api/v02/turn?id=${encodeURIComponent(id)}${data.dataset?'&dataset='+encodeURIComponent(data.dataset):''}`).then(r=>{if(!r.ok)throw new Error('turn '+r.status);return r.json();}).then(v=>{loaded.add(id);return v;}).catch(e=>{full.delete(id);throw e;}));return full.get(id);};
  let drawSeq=0;
  async function draw(){
   if(disposed)return;
   const lite=frames[index],seq=++drawSeq;
   let t=lite;
   if(lite.lazy){
    if(!loaded.has(lite.id))el('[data-cursor]').textContent=`${lite.id} · loading…`;
    try{const v=await loadTurn(lite.id);t={...lite,...v};}catch{if(seq===drawSeq)el('[data-state-content]').innerHTML='<p class="hint">Could not load this turn. Step again to retry.</p>';return;}
    if(disposed||seq!==drawSeq)return;
    for(const nb of [frames[index+1],frames[index-1]])if(nb?.lazy)loadTurn(nb.id).catch(()=>{}); // prefetch neighbors for replay/stepping
   }
   const mode=control('view').value,whole=control('whole').checked;
   root.dataset.turn=t.id;root.dataset.mode=mode;
   control('cursor').max=String(frames.length-1);control('cursor').value=String(index);control('cursor').disabled=frames.length<2;
   action('first').disabled=action('prev').disabled=index===0;
   action('last').disabled=action('next').disabled=index===frames.length-1;action('play').disabled=frames.length<2;
   el('[data-whole-label]').hidden=mode!=='state';
   const cur=el('[data-cursor]');cur.textContent=`${t.id} · ${index+1}/${frames.length} ${control('grain').value==='hour'?'hour endpoints':'turns'}${more?` · ${rows.length} of ${predictedTotal} loaded`:''}`;
   cur.title=`${t.id} · ${t.actor_name} · ${stamp(t.end_ts)} · ${index+1}/${frames.length}`;
   el('[data-review]').setAttribute('href',`/review?turn=${encodeURIComponent(t.id)}${run?'&dataset='+encodeURIComponent(run):''}`);
   el('[data-coverage]').textContent=`${rows.length}/${total} selected turns processed. Latest available: ${stamp(rows.at(-1).end_ts)}. Dataset window: ${stamp(data.manifest.window_start)} – ${stamp(data.manifest.window_end)}. Unprocessed turns have no inferred state.${more?` ${rows.length} of ${predictedTotal} labeled turns loaded; stepping back past the first, ⏮ or Play from the start loads the older ones.`:''}`;
   el('[data-explanation]').textContent=mode==='input'
    ? 'Exact context saved before this turn. Related entities left out of that input are loaded from the state after this turn and marked context.'
    :mode==='changes'?'Only changes recorded in selected turns through this cursor. Gaps are excluded; task cards retain prior fields needed to understand those changes.'
    :`${whole?'Whole-project':'Selected agents and related tasks'} state after this turn. Includes earlier history, including gaps between selected intervals. Connected actors and upstream tasks provide context.`;
   const state=mode==='input'?inputState(t.llm_input):mode==='changes'?changesAtCursor(rows,t):whole?t.state_after:scopedState(rows,t,sessions);
   const changed=new Set<string>(t.resolved_prediction?.tasks?.map((v:any)=>v.ref)||[]);
   const tasks=values(state.tasks),actors=values(state.actors),decisions=values(state.decisions);
   el('[data-state-content]').innerHTML=`<div data-selected-graph></div>
    <details class="v02-task-list" open><summary>Task list · ${tasks.length}</summary>${tasks.map((task:any)=>`<article class="entity" data-task-id="${esc(task.id)}"><div class="entity-header"><h3>${esc(task.title||task.id)}</h3><span class="badge">${esc(task.status||'unknown')}</span></div><p>${esc(task.summary||'')}</p><small>${esc(task.importance||'unrated')} importance · ${esc(task.complexity||'unrated')} complexity${changed.has(task.id)&&mode!=='input'?' · changed this turn':''}</small>${task.blocker?`<p class="state-blocker">${esc(task.blocker.reason)}${task.blocker.need_human_help?' · human help needed':''}</p>`:''}</article>`).join('')||'<p class="muted">No tasks recorded in this view.</p>'}</details>
    <details><summary>Actor labels · ${actors.length}</summary>${actors.map((a:any)=>`<p>${esc(a.name||a.id)} · ${esc(a.role||'unlabeled')} / ${esc(a.state||'unknown')}</p>`).join('')}</details>
    <details><summary>Decisions · ${decisions.length}</summary>${decisions.map((d:any)=>`<p>${esc(d.summary)}</p>`).join('')||'<p class="muted">No decisions recorded in this view.</p>'}</details>
    <details><summary>Source prose for ${esc(t.id)}</summary><p class="muted">Complete turn; it may start before the selected interval.</p><pre>${esc((t.messages||[]).map((m:any)=>m.text).join('\n\n'))}</pre></details>
    <details><summary>${mode==='input'?'Exact recorded input JSON':'Displayed state JSON'}</summary><pre data-state-json>${esc(JSON.stringify(mode==='input'?t.llm_input:state,null,2))}</pre></details>`;
   mountStateGraph(el('[data-selected-graph]'),state,{label:`${mode==='input'?'Recorded input before':'State after'} ${t.id}`,changed:mode==='input'?new Set():changed,lookup:t.state_after,prune:mode==='input'?null:{start:winStart,end:Math.min(winEnd,t.end_ts)},note:`${tasks.length} tasks · ${actors.length} actors · ${decisions.length} decisions · ${state.relationships.length} relations`});
   fitGraphHeight();
  }
  // The graph canvas takes the widget's remaining visible height so labels are not shrunk into a fixed 280px strip.
  function fitGraphHeight(){
   const c=root.querySelector('.sg-canvas') as SVGElement|null, sc=root.closest('.col-body') as HTMLElement|null;if(!c||!sc)return;
   const top=c.getBoundingClientRect().top-sc.getBoundingClientRect().top+sc.scrollTop;
   root.style.setProperty('--sg-fit-h',Math.max(240,Math.round(sc.clientHeight-top-8))+'px');
  }
  const bodyResize=typeof ResizeObserver!=='undefined'?new ResizeObserver(()=>fitGraphHeight()):null;
  {const sc=root.closest('.col-body');if(bodyResize&&sc)bodyResize.observe(sc);}
  const seek=(n:number)=>{stop();index=Math.max(0,Math.min(frames.length-1,n));draw();};
  const loadAll=async()=>{while(more&&!disposed)await loadOlder();};
  action('first').onclick=async()=>{stop();await loadAll().catch(()=>{});seek(0);};
  action('prev').onclick=async()=>{if(index===0&&more){stop();await loadOlder().catch(()=>{});}seek(index-1);};action('next').onclick=()=>seek(index+1);action('last').onclick=()=>seek(frames.length-1);
  action('play').onclick=async()=>{
   if(timer){stop();return;}if(index===frames.length-1){if(more){action('play').disabled=true;await loadAll().catch(()=>{});action('play').disabled=false;}index=0;}
   action('play').textContent='Pause';action('play').setAttribute('aria-label','Pause replay');root.dataset.playing='true';draw();
   timer=setInterval(()=>{if(disposed||!body.isConnected||document.hidden){stop();return;}if(index<frames.length-1){index++;draw();}if(index===frames.length-1)stop();},Number(control('speed').value));
  };
  control('cursor').oninput=async()=>{const n=Number(control('cursor').value);if(n===0&&more){stop();index=0;await loadOlder().catch(()=>{});seek(index);return;}seek(n);};
  control('grain').onchange=()=>{const ordinal=frames[index].ordinal;stop();frames=replayFrames(rows,control('grain').value);index=frames.findIndex((t:any)=>t.ordinal>=ordinal);if(index<0)index=frames.length-1;draw();};
  control('speed').onchange=stop;control('view').onchange=draw;control('whole').onchange=draw;
  root.dataset.playing='false';draw();
 }catch(error){if(body.dataset.v02Selection===key){disposeV02Summary(body);body.innerHTML='<p class="hint">V0.2 data could not be loaded. Reselect a cell to retry, or <a href="/review">open review</a>.</p>';console.error('V0.2 summary',error);}}
}
