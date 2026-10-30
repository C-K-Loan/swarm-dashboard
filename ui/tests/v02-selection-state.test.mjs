import test from 'node:test';
import assert from 'node:assert/strict';
import {selectionWindows,selectedTurns,replayFrames,changesAtCursor,scopedState,inputState} from '../src/lib/v02-selection-state.mjs';
const actor={id:'a',session_id:'s',name:'A'};
const task=(id,status)=>({id,title:id,status});
const edge=(type,from_ref,to_ref)=>({type,from_ref,to_ref});
function turn(ordinal,end_ts,tasks={},relationships=[]){return {id:`t${ordinal}`,ordinal,end_ts,session_id:'s',actor_id:'a',prediction:{},task_states:tasks,actor_state_after:actor,resolved_prediction:{relationships,decisions:[]},state_after:{actors:{a:actor,b:{id:'b',session_id:'other'}},tasks,decisions:{},relationships}};}
const first=turn(1,100,{x:task('x','progressing')});
const gap=turn(2,200,{x:task('x','done'),hidden:task('hidden','done')});
const last=turn(3,300,{y:task('y','progressing')},[edge('works_on','a','y'),edge('is_sub_task','y','parent'),edge('depends_on','parent','dependency')]);
last.state_after.tasks={...gap.task_states,...last.task_states,parent:task('parent','progressing'),dependency:task('dependency','done'),unrelated:task('unrelated','done')};
last.state_after.relationships.push(edge('works_on','b','unrelated'));
test('disjoint windows exclude gaps, other sessions and the upper boundary; overlaps deduplicate',()=>{
 const rows=selectedTurns([last,gap,first,{...first,session_id:'other'}],{windows:[{sid:'s',start:100,end:200},{sid:'s',start:300,end:301},{sid:'s',start:300,end:400}]});
 assert.deepEqual(rows.map(t=>t.id),['t1','t3']);
 assert.deepEqual(selectedTurns([first],{sessionIds:['s'],startTs:0,endTs:100}),[]);
});
test('changes exclude gap updates while state at cursor includes real history and upstream context',()=>{
 const rows=[first,last],changes=changesAtCursor(rows,last),state=scopedState(rows,last,['s']);
 assert.equal(changes.tasks.x.status,'progressing');assert.equal(changes.tasks.hidden,undefined);
 assert.equal(state.tasks.find(t=>t.id==='x').status,'done');
 assert.deepEqual(state.tasks.map(t=>t.id).sort(),['dependency','parent','x','y']);
 assert.equal(state.actors.some(a=>a.id==='b'),false);
 assert.deepEqual(Object.keys(changesAtCursor(rows,first).tasks),['x']);
 assert.deepEqual(scopedState(rows,first,['s']).tasks.map(t=>t.id),['x']);
});
test('hour replay uses the last selected processed turn in each occupied hour, skipping gaps/pending',()=>{
 const future=turn(5,10801);const pending={...turn(6,14401),prediction:null,state_after:null};
 assert.deepEqual(replayFrames([first,gap,last,future,pending],'hour').map(t=>t.id),['t3','t5']);
 assert.deepEqual(replayFrames([first,pending]).map(t=>t.id),['t1']);
});
test('LLM input projection only uses saved context and leaves missing references missing',()=>{
 const input={relevant_existing_tasks_with_ids:[task('x','progressing')],relevant_actors_and_relationships:{actors:[actor],relationships:[edge('depends_on','x','not-in-input')]}};
 const state=inputState(input);assert.equal(state.tasks.length,1);assert.equal(state.tasks[0].status,'progressing');assert.equal(state.relationships[0].to_ref,'not-in-input');assert.deepEqual(state.decisions,[]);
});

test('equivalent cell and bus windows share one replay identity without merging gaps',()=>{
 assert.deepEqual(selectionWindows({sessionIds:['s'],startTs:100,endTs:300}),selectionWindows({windows:[{sid:'s',start:200,end:300},{sid:'s',start:100,end:200},{sid:'s',start:100,end:200}]}));
 assert.equal(selectionWindows({windows:[{sid:'s',start:100,end:200},{sid:'s',start:300,end:400}]}).length,2);
});

test('inputState reads the v0.3-lean aliased shape', () => {
  const s = inputState({context_version: 'v0.3-lean', current_actor: {id: 'A1', name: 'gamejam-dev', state: 'working'},
    active_tasks_connected_to_actor: [{id: 'T1', title: 'Build M1', status: 'progressing'}], other_active_tasks: [['T2', 'Fetch assets']],
    relationships: [['works_on', 'A1', 'T1'], ['delegates_to', 'A2', 'A1', 'T2']], actors: [['A1', 'gamejam-dev'], ['A2', 'gamejam-master']]});
  assert.deepEqual(s.tasks.map(t => [t.id, t.title, !!t.index_only]), [['T1', 'Build M1', false], ['T2', 'Fetch assets', true]]);
  assert.equal(s.actors.find(a => a.id === 'A1').state, 'working');
  assert.deepEqual(s.relationships[1], {type: 'delegates_to', from_ref: 'A2', to_ref: 'A1', task_ref: 'T2'});
  assert.deepEqual(inputState({relevant_existing_tasks_with_ids: [{id: 'x'}]}).tasks, [{id: 'x'}]);
});
