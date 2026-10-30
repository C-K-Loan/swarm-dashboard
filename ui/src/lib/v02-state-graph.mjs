/** Local SVG state explorer. No network, graph library, or inferred dependencies. */
const list = value => Array.isArray(value) ? value : Object.values(value || {});
const relationNames = {depends_on:'depends on',is_sub_task:'subtask of',works_on:'works on',reviews:'reviews',delegates_to:'delegates to',made_decision:'made decision',affects_task:'affects task'};
// lookup: a fuller state (the turn's state_after) used to fill relationship endpoints the view left out.
// prune: display-only window pruning (see pruneGraph); data is never changed.
export function stateGraphData(state, mode = 'all', lookup = null, prune = null) {
  const nodes = new Map(), edges = [], seen = new Set();
  const pool = {actor:lookup?.actors,task:lookup?.tasks,decision:lookup?.decisions};
  const find = (id,kind) => (Array.isArray(pool[kind]) ? pool[kind].find(v => (v.id || v.ref) === id) : pool[kind]?.[id]) || (id === 'human' && kind === 'actor' ? {id,name:'human',role:'human'} : null);
  for (const [kind,items] of [['actor',state.actors],['task',state.tasks],['decision',state.decisions]]) {
    for (const value of list(items)) {
      const id = value.id || value.ref;
      if (id) nodes.set(id,{id,kind,label:value.title || value.name || value.summary || id,value,stub:false});
    }
  }
  function addEdge(type,from,to,task,value,derived = false) {
    if (!from || !to) return;
    const key = JSON.stringify([type,from,to,task || null]);if(seen.has(key))return;seen.add(key);
    const kinds = type === 'delegates_to' ? ['actor','actor'] : ['depends_on','is_sub_task'].includes(type) ? ['task','task'] : type === 'made_decision' ? ['actor','decision'] : type === 'affects_task' ? ['decision','task'] : ['actor','task'];
    const ends=[[from,kinds[0]],[to,kinds[1]],...(task?[[task,'task']]:[])];
    // Fill missing endpoints from lookup; an endpoint nothing can resolve drops the edge instead of drawing a stub.
    for(const [id,kind] of ends)if(!nodes.has(id)){const v=find(id,kind);if(!v)return;nodes.set(id,{id,kind,label:v.title || v.name || v.summary || id,value:v,stub:false,context:true});}
    edges.push({id:key,type,from,to,task,label:relationNames[type] || type,value,derived});
  }
  for(const r of state.relationships || []) addEdge(r.type,r.from_ref,r.to_ref,r.task_ref,r);
  for(const d of list(state.decisions)) {
    const id=d.id || d.ref;
    addEdge('made_decision',d.made_by_actor_id,id,null,d,true);
    for(const task of d.affected_task_refs || [])addEdge('affects_task',id,task,null,d,true);
  }
  // active: what a lean prompt still sees, so done/cancelled tasks and decisions that only touch them are dropped.
  const closedTask=id=>{const n=nodes.get(id);return n?.kind==='task'&&CLOSED.has(n.value?.status);};
  const keep=n=>mode==='all'?true:mode==='active'?(n.kind==='task'?!CLOSED.has(n.value?.status):n.kind==='decision'?!((n.value?.affected_task_refs||[]).length&&(n.value.affected_task_refs).every(closedTask)):true):n.kind===(mode==='tasks'?'task':'actor');
  const visibleNodes=[...nodes.values()].filter(keep), ids=new Set(visibleNodes.map(n=>n.id));
  const visibleEdges=edges.filter(e=>ids.has(e.from)&&ids.has(e.to));
  if(prune)return pruneGraph(visibleNodes,visibleEdges,prune);
  return {nodes:visibleNodes,edges:visibleEdges,stubCount:visibleNodes.filter(n=>n.stub).length};
}

/** Display pruning for wide windows (CKL: a full Day1 state is ~770 tasks). Keeps open tasks changed inside
 * [start,end]; stale ones (no update for staleSec before end) fold into one group per agent; windows >= 3h fold
 * each agent's tasks into a collapsible cluster; done tasks only if finished in the window (newest DONE_CAP);
 * decisions by importance for the window length, only when they touch a drawn task. expanded holds actor ids
 * and 'stale:<actor>' ids the user opened. Hidden counts come back as chips. */
// importance: 'auto' (decisions by window length, tasks unfiltered) | 'high' | 'hm' (high+medium) | 'all'; applies to tasks and decisions.
// fold=false (the timeline default, CKL) never folds agents into clusters, at any window length.
export function pruneGraph(nodes,edges,{start,end,staleSec=7200,expanded=new Set(),importance='auto',fold=true}){
  const span=end-start,inWin=ts=>Number.isFinite(ts)&&ts>=start&&ts<=end;
  const byId=new Map(nodes.map(n=>[n.id,n])),owner=new Map();
  for(const type of ['works_on','reviews'])for(const e of edges)if(e.type===type&&!owner.has(e.to)&&byId.get(e.from)?.kind==='actor')owner.set(e.to,e.from);
  const keep=new Set(),done=[],stale=new Map(),cluster=new Map();let unchanged=0,doneEarlier=0;
  const clustering=fold&&span>=3*3600;
  const pick=importance==='high'?new Set(['high']):importance==='hm'?new Set(['high','medium']):null;let lowTasks=0;
  for(const n of nodes){
    if(n.kind!=='task')continue;
    const v=n.value||{},changed=inWin(v.updated_at)||inWin(v.created_at);
    if(pick&&!pick.has(v.importance)){if(changed)lowTasks++;continue;}
    if(CLOSED.has(v.status)){if(changed)done.push(n);else doneEarlier++;continue;}
    if(!changed){unchanged++;continue;}
    const who=owner.get(n.id)||UNASSIGNED,isStale=staleSec>0&&end-(v.updated_at??v.created_at??end)>staleSec;
    // A folded agent cluster carries its stale count too; the stale group only appears once the agent is opened.
    if(clustering&&!expanded.has(who)){const c=cluster.get(who)||{open:[],stale:0};c.open.push(n);if(isStale)c.stale++;cluster.set(who,c);continue;}
    if(staleSec>0&&end-(v.updated_at??v.created_at??end)>staleSec&&!expanded.has('stale:'+who)){stale.set(who,[...(stale.get(who)||[]),n]);continue;}
    keep.add(n.id);
  }
  done.sort((a,b)=>(b.value.updated_at||0)-(a.value.updated_at||0));
  for(const n of done.slice(0,DONE_CAP))keep.add(n.id);
  const doneMore=Math.max(0,done.length-DONE_CAP);
  // Decisions: all at <=1h, high+medium below 4h, high only from 4h; only when they touch a drawn task.
  const allowed=importance==='all'?null:pick||(span<=3600?null:span<4*3600?new Set(['high','medium']):new Set(['high']));
  let hiddenDecisions=0;
  const decisions=nodes.filter(n=>n.kind==='decision').filter(n=>{const ok=(!allowed||allowed.has(n.value?.importance))&&(n.value?.affected_task_refs||[]).some(id=>keep.has(id));if(!ok)hiddenDecisions++;return ok;});
  decisions.sort((a,b)=>(IMP_RANK[a.value?.importance]??3)-(IMP_RANK[b.value?.importance]??3)).slice(DECISION_CAP).forEach(n=>{hiddenDecisions++;n._drop=true;});
  for(const n of decisions)if(!n._drop)keep.add(n.id);
  // Pseudo nodes: one stale group per agent, one cluster badge per folded agent.
  const extra=[],extraEdges=[];
  const actorFor=id=>{if(byId.has(id))return byId.get(id);const a={id,kind:'actor',label:'Unowned tasks',value:{id,name:'Unowned tasks',role:'no owning agent',state:'tasks no agent works on'},stub:false,unowned:true};byId.set(id,a);return a;};
  for(const [who,list] of stale){actorFor(who);keep.add(who);const id='stale:'+who;extra.push({id,kind:'group',label:`stale (${list.length})`,value:{owner:who,tasks:list.map(t=>t.value),staleSec},stub:false,expand:id});extraEdges.push({id:JSON.stringify(['stale',who,id]),type:'stale',from:who,to:id,label:'stale tasks',value:{},derived:true});}
  for(const [who,c] of cluster){const a=actorFor(who);keep.add(who);a.cluster={count:c.open.length,stale:c.stale,expanded:false};}
  for(const who of expanded)if(byId.get(who)?.kind==='actor'&&!cluster.has(who)&&clustering){byId.get(who).cluster={count:0,expanded:true};}
  // Actors stay only when linked to something drawn (or carrying a cluster/stale group).
  const isActor=id=>byId.get(id)?.kind==='actor';
  for(const e of edges){if(isActor(e.from)&&keep.has(e.to)&&!isActor(e.to))keep.add(e.from);if(isActor(e.to)&&keep.has(e.from)&&!isActor(e.from))keep.add(e.to);}
  const out=[...byId.values()].filter(n=>keep.has(n.id)).concat(extra);
  const ids=new Set(out.map(n=>n.id));
  const chips=[];
  if(unchanged)chips.push(`+${unchanged} unchanged open task${unchanged===1?'':'s'}`);
  if(doneEarlier)chips.push(`${doneEarlier} done before window`);
  if(doneMore)chips.push(`+${doneMore} more done in window`);
  if(lowTasks)chips.push(`+${lowTasks} lower-importance task${lowTasks===1?'':'s'}`);
  if(hiddenDecisions)chips.push(`${hiddenDecisions} decision${hiddenDecisions===1?'':'s'} hidden`);
  return {nodes:out,edges:edges.filter(e=>ids.has(e.from)&&ids.has(e.to)).concat(extraEdges),stubCount:0,chips,pruned:true};
}
const UNASSIGNED='~unassigned',DONE_CAP=16,DECISION_CAP=10,IMP_RANK={high:0,medium:1,low:2};

/** Stable category columns, then task dependency depth; cycles cannot recurse forever.
 * aspect (canvas width/height) wraps each column into as many rows as keep the fitted text largest. */
export function graphLayout(graph, aspect = 0) {
  const parents=new Map(), depth=new Map();
  for(const e of graph.edges)if(['depends_on','is_sub_task'].includes(e.type))parents.set(e.from,[...(parents.get(e.from)||[]),e.to]);
  function level(id,visiting=new Set()) {
    if(depth.has(id))return depth.get(id);if(visiting.has(id))return 0;
    const next=new Set(visiting);next.add(id);
    const result=Math.min(4,Math.max(0,...(parents.get(id)||[]).map(p=>level(p,next)+1)));depth.set(id,result);return result;
  }
  const positions=new Map();let offset=0;
  // Done/cancelled tasks go to a compact side grid so open work keeps the main column short.
  const closed=n=>n.kind==='task'&&!n.stub&&CLOSED.has(n.value?.status);
  const groups=['actor','group','task','decision'].map(kind=>graph.nodes.filter(n=>n.kind===kind&&!closed(n)).sort((a,b)=>kind==='task'?(level(a.id)-level(b.id)||a.id.localeCompare(b.id)):a.id.localeCompare(b.id))).filter(g=>g.length);
  const done=graph.nodes.filter(closed).sort((a,b)=>a.label.localeCompare(b.label));
  if(aspect>0){
    const doneRows=rows=>Math.max(1,Math.floor(((rows-1)*PITCH.y+66)/COMPACT.pitchY));
    const size=rows=>{let w=0;for(const g of groups)w+=Math.ceil(g.length/rows)*PITCH.x+PITCH.gap;if(done.length)w+=Math.ceil(done.length/doneRows(rows))*COMPACT.pitchX;return {w:w+220,h:(rows-1)*PITCH.y+130};};
    let rows=1,best=0;
    for(let r=1;r<=Math.max(1,...groups.map(g=>g.length));r++){const s=size(r),scale=Math.min(aspect/s.w,1/s.h);if(scale>best+1e-9){best=scale;rows=r;}}
    for(const g of groups){g.forEach((n,i)=>positions.set(n.id,{x:offset+Math.floor(i/rows)*PITCH.x,y:(i%rows)*PITCH.y}));offset+=Math.ceil(g.length/rows)*PITCH.x+PITCH.gap;}
    const dr=doneRows(rows);
    done.forEach((n,i)=>positions.set(n.id,{x:offset+Math.floor(i/dr)*COMPACT.pitchX,y:(i%dr)*COMPACT.pitchY-PITCH.y/2+COMPACT.h,compact:true}));
    if(done.length)positions.closedHeader={x:offset-COMPACT.w/2,y:-PITCH.y/2+COMPACT.h/2-14,count:done.length};
    return positions;
  }
  for(const items of groups) {
    const columns=items.length>6?2:1;
    items.forEach((n,i)=>positions.set(n.id,{x:offset+(i%columns)*174,y:Math.floor(i/columns)*100}));
    offset+=columns*174+70;
  }
  if(done.length){
    const cols=done.length>8?4:2;
    done.forEach((n,i)=>positions.set(n.id,{x:offset+(i%cols)*COMPACT.pitchX,y:Math.floor(i/cols)*COMPACT.pitchY,compact:true}));
    positions.closedHeader={x:offset-COMPACT.w/2,y:-COMPACT.h-14,count:done.length};
  }
  return positions;
}
/** Labeled region boxes (Agents / Tasks / Key decisions / Completed) around laid-out nodes, in graph units.
 * Each box pads the node halo by 4 and leaves 20 above the top row for its label; column gaps keep boxes apart. */
export function regionBoxes(graph,positions){
  const regions=[['Agents',n=>n.kind==='actor'],['Tasks',n=>(n.kind==='task'||n.kind==='group')&&!positions.get(n.id)?.compact],['Key decisions',n=>n.kind==='decision'],['Completed',n=>positions.get(n.id)?.compact]];
  const out=[];
  for(const [label,match] of regions){
    const ps=graph.nodes.filter(match).map(n=>positions.get(n.id)).filter(Boolean);if(!ps.length)continue;
    const hx=p=>p.compact?COMPACT.w/2+8:86,hy=p=>p.compact?COMPACT.h/2+8:43;
    const x0=Math.min(...ps.map(p=>p.x-hx(p))),x1=Math.max(...ps.map(p=>p.x+hx(p))),y0=Math.min(...ps.map(p=>p.y-hy(p)))-20,y1=Math.max(...ps.map(p=>p.y+hy(p)));
    out.push({label:label==='Completed'?`Completed · ${ps.length}`:label,x:x0,y:y0,w:x1-x0,h:y1-y0});
  }
  return out;
}
const PITCH={x:174,y:84,gap:56};
const CLOSED=new Set(['done','cancelled']);
const COMPACT={w:112,h:30,pitchX:120,pitchY:38};
const NS='http://www.w3.org/2000/svg';let graphSerial=0;
function svg(tag,attrs={},text) {const el=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,String(value));if(text!==undefined)el.textContent=text;return el;}
function html(tag,className,text) {const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;}
function short(text,length=22){return text.length>length?text.slice(0,length-1)+'…':text;}
// Word-wrap a label into at most two lines; the full text stays in the node's <title>.
function wrap(text,width=19){const words=String(text).split(/\s+/).filter(Boolean),lines=[''];for(const w of words){const cur=lines.at(-1);if(!cur)lines[lines.length-1]=w;else if((cur+' '+w).length<=width)lines[lines.length-1]=cur+' '+w;else if(lines.length<2)lines.push(w);else{lines[1]=short(lines[1]+' '+w,width);break;}}return lines.map(l=>short(l,width));}
// Task status and decision importance: glyph + color so state survives without color.
const STATUS={not_started:['○','not started'],progressing:['▶','progressing'],waiting:['⏸','waiting'],testing:['◎','testing'],blocked:['⛔','blocked'],done:['✓','done'],cancelled:['⊘','cancelled']};
const IMPORTANCE={high:['▲▲▲','high importance'],medium:['▲▲','medium importance'],low:['▲','low importance']};
function tone(n){
  if(n.stub)return {cls:'',text:'state not supplied'};
  if(n.kind==='group')return {cls:' sg-group-node',text:'▸ click to show'};
  if(n.cluster)return {cls:' sg-cluster',text:n.cluster.expanded?'▾ open · click to fold':`▸ ${n.cluster.count} task${n.cluster.count===1?'':'s'}${n.cluster.stale?` · ${n.cluster.stale} stale`:''}`};
  const v=n.value;
  if(n.kind==='task'&&v.index_only)return {cls:'',text:'title only (index)'};
  if(n.kind==='task'){const s=STATUS[v.status];return s?{cls:' sg-st-'+v.status,text:s[0]+' '+s[1]+(n.context?' · context':'')}:{cls:' sg-st-unknown',text:'? unknown status'};}
  if(n.kind==='decision'){const i=IMPORTANCE[v.importance];return i?{cls:' sg-imp-'+v.importance,text:i[0]+' '+i[1]}:{cls:' sg-imp-unknown',text:'? importance unknown'};}
  return {cls:'',text:short(v.state||'unknown',25)};
}

// prune {start,end} (unix s): wide-window display pruning with stale/cluster groups; omitted = draw everything.
export function mountStateGraph(container,state,{label='State graph',changed=new Set(),lookup=null,note='',prune=null}={}) {
  const serial=++graphSerial;
  container.className='state-graph';container.setAttribute('aria-label',label);
  const toolbar=html('div','sg-toolbar'), modes=html('div','sg-modes'), actions=html('div','sg-actions');
  toolbar.append(modes,...(note?[html('span','sg-note',note)]:[]),actions);
  const chips=html('div','sg-chips');
  const store=(k,v)=>{try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v);}catch{}return null;};
  let pruneOn=store('v02-sg-prune')!=='off',staleSec=Number(store('v02-sg-stale')??7200),foldOn=store('v02-sg-fold')==='on';
  // Key and fold survive turn steps (each step remounts) and reloads: ?sgkey=1 wins over localStorage.
  const keyOn=(()=>{try{const v=new URLSearchParams(location.search).get('sgkey');if(v!=null)return v==='1';}catch{}return store('v02-sg-key')==='on';})();
  container.classList.toggle('sg-key-open',keyOn);
  let importance=(()=>{try{const v=new URLSearchParams(location.search).get('sgimp');return ['high','hm','all'].includes(v)?v:'auto';}catch{return 'auto';}})();
  const expanded=container._sgExpanded||(container._sgExpanded=new Set()); // survives cursor steps (same container)
  const legend=html('div','sg-legend');
  for(const [kind,name] of [['actor','Actor'],['task','Task'],['decision','Decision']]){const item=html('span','sg-key sg-'+kind,name);legend.append(item);}
  legend.append(html('span','sg-key sg-changed','Changed this turn'));
  const statusKey=html('div','sg-legend sg-legend-states');
  for(const [k,[g,name]] of Object.entries(STATUS))statusKey.append(html('span','sg-chip sg-st-'+k,g+' '+name));
  for(const [k,[g,name]] of Object.entries(IMPORTANCE))statusKey.append(html('span','sg-chip sg-imp-'+k,'decision '+g+' '+k));
  const stage=html('div','sg-stage'), canvas=svg('svg',{class:'sg-canvas',viewBox:'0 0 800 540',role:'group','aria-label':label,tabindex:0});stage.append(canvas);
  const summary=html('p','sg-summary'), hint=html('p','sg-hint','Hover or focus to inspect · click to pin · drag background to pan · wheel to zoom');
  const detail=html('div','sg-detail');detail.setAttribute('role','status');detail.setAttribute('aria-live','polite');
  container.replaceChildren(toolbar,chips,legend,statusKey,stage,summary,hint,detail);
  let mode='all', graph, positions, nodeElements=new Map(), edgeElements=new Map(), edgeLabels=new Map(), labelLayer=null, pinned=null, camera={x:0,y:0,w:800,h:540}, drag=null;
  const modeButtons=new Map();
  function button(parent,text,title,action){const b=html('button','',text);b.type='button';b.title=title;b.addEventListener('click',action);parent.append(b);return b;}
  for(const [key,name] of [['all','Whole state'],['active','Active state'],['tasks','Tasks'],['actors','Actors']]) {
    const b=button(modes,name,'Show '+name.toLowerCase(),()=>{mode=key;pinned=null;draw();});b.dataset.graphMode=key;modeButtons.set(key,b);
  }
  function aspect(){const r=canvas.getBoundingClientRect();return r.width>0&&r.height>0?r.width/r.height:0;}
  function setCamera(){canvas.setAttribute('viewBox',`${camera.x} ${camera.y} ${camera.w} ${camera.h}`);}
  function fit(){const pts=[...positions.values()];const xs=pts.map(p=>p.x),ys=pts.map(p=>p.y);camera={x:Math.min(0,...xs)-110,y:Math.min(0,...ys)-75,w:Math.max(200,Math.max(0,...xs)-Math.min(0,...xs)+220),h:Math.max(160,Math.max(0,...ys)-Math.min(0,...ys)+140)};setCamera();}
  function zoom(factor){const width=Math.max(100,Math.min(10000,camera.w*factor)),ratio=width/camera.w;camera={x:camera.x+(camera.w-width)/2,y:camera.y+(camera.h-camera.h*ratio)/2,w:width,h:camera.h*ratio};setCamera();}
  button(actions,'−','Zoom out',()=>zoom(1.25)).classList.add('sg-zoombtn');button(actions,'+','Zoom in',()=>zoom(.8)).classList.add('sg-zoombtn');button(actions,'Fit','Fit entire graph',fit).classList.add('sg-zoombtn');
  button(actions,'Reset','Clear pinned node and reset view',()=>{pinned=null;highlight(null);fit();});
  if(prune){
    const pb=button(actions,'Prune','Prune for the window: changed tasks only, stale and per-agent groups (display only)',()=>{pruneOn=!pruneOn;store('v02-sg-prune',pruneOn?'on':'off');pb.setAttribute('aria-pressed',String(pruneOn));sel.disabled=imp.disabled=!pruneOn;pinned=null;draw();});pb.setAttribute('aria-pressed',String(pruneOn));pb.classList.add('sg-prunebtn');
    const sel=html('select','sg-stale');sel.title='open tasks with no update for longer than this collapse into a stale group';sel.setAttribute('aria-label','Stale after');
    for(const [v,t] of [[1800,'stale 30m'],[3600,'stale 1h'],[7200,'stale 2h'],[21600,'stale 6h'],[0,'stale off']]){const o=html('option','',t);o.value=String(v);if(v===staleSec)o.selected=true;sel.append(o);}
    const imp=html('select','sg-stale sg-importance');imp.title='importance filter for tasks and decisions; auto = by window length (decisions only)';imp.setAttribute('aria-label','Importance filter');
    for(const [v,t] of [['auto','imp: auto'],['high','imp: high'],['hm','imp: high+med'],['all','imp: all']]){const o=html('option','',t);o.value=v;if(v===importance)o.selected=true;imp.append(o);}
    imp.disabled=!pruneOn;imp.addEventListener('change',()=>{importance=imp.value;try{const u=new URL(location.href);if(importance==='auto')u.searchParams.delete('sgimp');else u.searchParams.set('sgimp',importance);history.replaceState(history.state,'',u);}catch{}pinned=null;draw();});actions.prepend(imp);
    sel.disabled=!pruneOn;sel.addEventListener('change',()=>{staleSec=Number(sel.value);store('v02-sg-stale',sel.value);draw();});actions.prepend(sel);
    const fb=button(actions,'Fold','Fold each agent\'s tasks into one "▸ N tasks" node on windows of 3h or more (off: every task shows)',()=>{foldOn=!foldOn;store('v02-sg-fold',foldOn?'on':'off');fb.setAttribute('aria-pressed',String(foldOn));pinned=null;draw();});
    fb.setAttribute('aria-pressed',String(foldOn));fb.classList.add('sg-foldbtn');fb.disabled=!pruneOn;
    pb.addEventListener('click',()=>{fb.disabled=!pruneOn;});
  }
  button(actions,'Key','Show or hide the color key',()=>{const on=container.classList.toggle('sg-key-open');store('v02-sg-key',on?'on':'off');try{const u=new URL(location.href);if(on)u.searchParams.set('sgkey','1');else u.searchParams.delete('sgkey');history.replaceState(history.state,'',u);}catch{}}).classList.add('sg-keybtn');
  button(actions,'Expand','Open graph fullscreen',async()=>{const target=container.closest('[data-fullscreen-root]')||container;try{if(document.fullscreenElement===target)await document.exitFullscreen();else await target.requestFullscreen();}catch{hint.textContent='Fullscreen is unavailable in this browser. Use + and drag to explore.';}});
  function name(id){return graph.nodes.find(n=>n.id===id)?.label || list(state.tasks).find(t=>t.id===id)?.title || id;}
  function defaultDetail(){detail.replaceChildren(html('p','muted','Select a node or arrow to inspect its state and recorded relationships.'));}
  function showNode(n){
    detail.replaceChildren(html('strong','',n.label),html('small','mono',n.id));
    if(n.stub){detail.append(html('p','sg-warning','Referenced by a relationship, but its state was not supplied in this snapshot.'));return;}
    if(n.kind==='group'){detail.append(html('p','',`Open tasks of ${name(n.value.owner)} with no update for over ${Math.round(n.value.staleSec/60)} min. Click to show them.`));const ul=html('ul','sg-related');for(const t of n.value.tasks)ul.append(html('li','',`${STATUS[t.status]?.[0]||''} ${t.title||t.id}`));detail.append(ul);return;}
    if(n.unowned)detail.append(html('p','','Open tasks with no owning agent: no works_on or reviews relationship points at them.'));
    if(n.cluster&&!n.cluster.expanded)detail.append(html('p','',`${n.cluster.count} open tasks changed in this window are folded into this agent${n.cluster.stale?` (${n.cluster.stale} stale)`:''}. Click to open.`));
    const v=n.value;detail.append(html('p','',n.kind==='actor'?`Actor · ${v.role ?? 'unlabeled'} · ${v.state ?? 'unknown'}`:n.kind==='task'?`Task · ${v.status ?? 'unknown'} · importance ${v.importance ?? 'unknown'} · complexity ${v.complexity ?? 'unknown'}`:`Decision · importance ${v.importance ?? 'unknown'}`));
    if(n.context)detail.append(html('p','muted','Not part of this view; loaded from the state after this turn because a relationship points to it.'));
    if(v.summary)detail.append(html('p','',v.summary));
    if(v.blocker)detail.append(html('p','sg-warning','Blocker: '+v.blocker.reason));
    const related=graph.edges.filter(e=>[e.from,e.to,e.task].includes(n.id));
    if(related.length){const ul=html('ul','sg-related');for(const e of related)ul.append(html('li','',`${name(e.from)} → ${e.label} → ${name(e.to)}${e.task?' · task: '+name(e.task):''}`));detail.append(ul);}else detail.append(html('p','muted','No relationships recorded in this view.'));
  }
  function showEdge(e){
    detail.replaceChildren(html('strong','',`${name(e.from)} → ${e.label} → ${name(e.to)}`));
    if(e.task)detail.append(html('p','','Delegated task: '+name(e.task)));
    if(e.derived)detail.append(html('p','muted','Connection from the recorded decision’s author or affected-task fields.'));
    for(const ev of e.value.evidence || [])detail.append(html('p','sg-evidence','“'+ev.quote+'”'));
  }
  function highlight(target){
    const active=target || pinned;const connected=new Set();const selectedEdges=new Set();
    if(active?.node){connected.add(active.node.id);for(const e of graph.edges)if([e.from,e.to,e.task].includes(active.node.id)){selectedEdges.add(e.id);for(const id of [e.from,e.to,e.task])if(id)connected.add(id);}showNode(active.node);}
    else if(active?.edge){selectedEdges.add(active.edge.id);for(const id of [active.edge.from,active.edge.to,active.edge.task])if(id)connected.add(id);showEdge(active.edge);}
    else defaultDetail();
    for(const [id,el] of nodeElements){el.classList.toggle('sg-dim',!!active&&!connected.has(id));el.classList.toggle('sg-active',id===active?.node?.id);el.classList.toggle('sg-neighbor',!!active&&connected.has(id));el.setAttribute('aria-pressed',String(pinned?.node?.id===id));}
    for(const [id,lab] of edgeLabels)lab.classList.toggle('sg-on',selectedEdges.has(id));
    for(const [id,el] of edgeElements){el.classList.toggle('sg-dim',!!active&&!selectedEdges.has(id));el.classList.toggle('sg-active',selectedEdges.has(id));el.setAttribute('aria-pressed',String(pinned?.edge?.id===id));}
    container.dataset.pinned=pinned?.node?.id || pinned?.edge?.id || '';
  }
  function wire(el,target){
    el.addEventListener('pointerenter',()=>highlight(target));el.addEventListener('pointerleave',()=>highlight(null));
    el.addEventListener('focus',()=>highlight(target));el.addEventListener('blur',()=>highlight(null));
    const toggle=()=>{const same=target.node?pinned?.node?.id===target.node.id:pinned?.edge?.id===target.edge.id;pinned=same?null:target;highlight(pinned);};
    const open=target.node?.expand||(target.node?.cluster&&target.node.id);
    el.addEventListener('click',e=>{e.stopPropagation();if(open){if(expanded.has(open))expanded.delete(open);else expanded.add(open);pinned=null;draw();return;}toggle();});
    el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.stopPropagation();toggle();}});
  }
  function draw(){
    graph=stateGraphData(state,mode,lookup,prune&&pruneOn?{...prune,staleSec,expanded,importance,fold:foldOn}:null);
    chips.replaceChildren(...(graph.chips||[]).map(c=>html('span','sg-chip sg-hidden-chip',c)));chips.hidden=!graph.chips?.length;
    positions=graphLayout(graph,aspect());nodeElements=new Map();edgeElements=new Map();edgeLabels=new Map();labelLayer=svg('g',{class:'sg-label-layer'});
    for(const [key,b] of modeButtons)b.setAttribute('aria-pressed',String(mode===key));
    canvas.replaceChildren();const defs=svg('defs');const boxes=svg('g',{class:'sg-regions','pointer-events':'none'});
    for(const b of regionBoxes(graph,positions)){boxes.append(svg('rect',{x:b.x,y:b.y,width:b.w,height:b.h,rx:10,class:'sg-region'}),svg('text',{x:b.x+10,y:b.y+14,class:'sg-region-label'},b.label));}const markerId='sg-arrow-'+serial;
    const marker=svg('marker',{id:markerId,viewBox:'0 -5 10 10',refX:9,refY:0,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'});marker.append(svg('path',{d:'M0,-4L9,0L0,4',fill:'#8191a7'}));defs.append(marker);canvas.append(defs,boxes);
    for(const [i,e] of graph.edges.entries()){
      const a=positions.get(e.from),b=positions.get(e.to);const dx=b.x-a.x,dy=b.y-a.y;const length=Math.hypot(dx,dy)||1;
      const half=p=>p.compact?[COMPACT.w/2+2,COMPACT.h/2+2]:[78,35];
      const inset=(p,vx,vy)=>{const [hx,hy]=half(p);return Math.min(hx/Math.max(Math.abs(vx),.001),hy/Math.max(Math.abs(vy),.001));};
      const ux=dx/length,uy=dy/length,ra=inset(a,ux,uy),rb=inset(b,ux,uy);const start={x:a.x+ux*ra,y:a.y+uy*ra},end={x:b.x-ux*rb,y:b.y-uy*rb};
      const bend=(i%3-1)*24;const mid={x:(start.x+end.x)/2-uy*bend,y:(start.y+end.y)/2+ux*bend};
      const path=`M${start.x},${start.y} Q${mid.x},${mid.y} ${end.x},${end.y}`;
      // Edges into the completed grid only show on hover/pin; drawn always they turn the graph into a hairball.
      const toClosed=a.compact||b.compact;
      const group=svg('g',{class:'sg-edge'+(toClosed?' sg-edge-closed':''),tabindex:0,role:'button','aria-label':`${name(e.from)} ${e.label} ${name(e.to)}`,'data-edge-type':e.type});
      group.append(svg('path',{d:path,class:'sg-edge-hit'}),svg('path',{d:path,class:'sg-edge-line','marker-end':`url(#${markerId})`}));
      // Labels live in a top layer: under the nodes their dark halo poked out past node edges as black bars (CKL).
      const lab=svg('text',{x:mid.x,y:mid.y-7,class:'sg-edge-label','text-anchor':'middle'},e.label);labelLayer.append(lab);edgeLabels.set(e.id,lab);
      canvas.append(group);edgeElements.set(e.id,group);wire(group,{edge:e});
    }
    for(const n of graph.nodes){
      const p=positions.get(n.id),look=tone(n);const group=svg('g',{class:`sg-node sg-${n.kind}${n.stub?' sg-stub':''}${changed.has(n.id)?' sg-updated':''}${look.cls}`,transform:`translate(${p.x},${p.y})`,tabindex:0,role:'button','aria-label':`${n.kind}: ${n.label}`,'data-node-id':n.id,'data-node-kind':n.kind});
      const rx=n.kind==='actor'?28:n.kind==='decision'?3:9;
      if(p.compact){
        const {w,h}=COMPACT;group.classList.add('sg-compact');
        if(changed.has(n.id))group.append(svg('rect',{x:-w/2-4,y:-h/2-4,width:w+8,height:h+8,rx:8,class:'sg-halo'}));
        group.append(svg('rect',{x:-w/2,y:-h/2,width:w,height:h,rx:6,class:'sg-node-shape'}),svg('rect',{x:-w/2,y:-h/2,width:5,height:h,rx:2.5,class:'sg-stripe'}));
        group.append(svg('title',{},n.label));
        group.append(svg('text',{x:-w/2+11,y:4,class:'sg-node-name sg-compact-name'},(STATUS[n.value.status]?.[0]||'')+' '+short(n.label,15)));
        canvas.append(group);nodeElements.set(n.id,group);wire(group,{node:n});continue;
      }
      if(changed.has(n.id))group.append(svg('rect',{x:-82,y:-39,width:164,height:78,rx:rx+4,class:'sg-halo'}));
      group.append(svg('rect',{x:-76,y:-33,width:152,height:66,rx,class:'sg-node-shape'}));
      // Status/importance stripe is for tasks and decisions only; agents and clusters have no status color (was black).
      if(look.cls&&!n.stub&&(n.kind==='task'||n.kind==='decision'))group.append(svg('rect',{x:-76,y:-33,width:6,height:66,rx:3,class:'sg-stripe'}));
      // Shape and color carry the kind; the space goes to a two-line name (CKL: graph was unreadable).
      group.append(svg('title',{},`${n.kind}: ${n.label}`));
      const lines=wrap(n.label,n.kind==='actor'?16:19);lines.forEach((line,k)=>group.append(svg('text',{x:0,y:(lines.length===1?-2:-10)+k*15,'text-anchor':'middle',class:'sg-node-name'},line)));
      group.append(svg('text',{x:0,y:22,'text-anchor':'middle',class:'sg-node-state'},n.stub?'reference only':look.text));
      canvas.append(group);nodeElements.set(n.id,group);wire(group,{node:n});
    }
    canvas.append(labelLayer);
    const empty=graph.nodes.length===0;
    if(empty)canvas.append(svg('text',{x:0,y:0,'text-anchor':'middle',fill:'#a4a9b9'},'No entities recorded in this state.'));
    summary.textContent=`${graph.nodes.length} nodes · ${graph.edges.length} connections${graph.stubCount?' · '+graph.stubCount+' references without supplied state':''}`+(mode==='tasks'&&!graph.edges.length?' · No task dependencies or subtask links recorded.':'');
    fit();highlight(null);
  }
  canvas.addEventListener('keydown',e=>{if(e.key==='Escape'){e.stopPropagation();pinned=null;highlight(null);}if(e.key==='+'||e.key==='='){e.preventDefault();zoom(.8);}if(e.key==='-'){e.preventDefault();zoom(1.25);}});
  // Plain wheel zooms around the pointer (CKL); the page/widget scrolls everywhere outside the canvas.
  canvas.addEventListener('wheel',e=>{e.preventDefault();const inverse=canvas.getScreenCTM()?.inverse();if(!inverse){zoom(e.deltaY>0?1.12:1/1.12);return;}const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(inverse);const width=Math.max(100,Math.min(10000,camera.w*(e.deltaY>0?1.12:1/1.12))),ratio=width/camera.w;camera={x:p.x-(p.x-camera.x)*ratio,y:p.y-(p.y-camera.y)*ratio,w:width,h:camera.h*ratio};setCamera();},{passive:false});
  canvas.addEventListener('pointerdown',e=>{if(e.target.closest('.sg-node,.sg-edge'))return;const inverse=canvas.getScreenCTM()?.inverse();if(!inverse)return;const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(inverse);drag={id:e.pointerId,x:p.x,y:p.y,camera:{...camera},inverse,moved:false};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(!drag)return;const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(drag.inverse);const dx=p.x-drag.x,dy=p.y-drag.y;if(Math.abs(dx)+Math.abs(dy)>3)drag.moved=true;camera={...drag.camera,x:drag.camera.x-dx,y:drag.camera.y-dy};setCamera();});
  canvas.addEventListener('pointerup',e=>{if(!drag)return;if(!drag.moved){pinned=null;highlight(null);}drag=null;if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);});
  canvas.addEventListener('pointercancel',()=>{drag=null;});
  draw();
  // Re-wrap columns when the widget changes shape, so the fitted text stays as large as possible.
  container._sgResize?.disconnect();let shape=aspect();
  if(typeof ResizeObserver!=='undefined'){container._sgResize=new ResizeObserver(()=>{const a=aspect();if(a&&Math.abs(a-shape)/a>.08){shape=a;draw();}});container._sgResize.observe(canvas);}
}
