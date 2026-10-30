import test from 'node:test';
import assert from 'node:assert/strict';
import {stateGraphData,graphLayout,regionBoxes} from '../src/lib/v02-state-graph.mjs';
const fixture={actors:[{id:'a',name:'Alice'},{id:'b',name:'Bob'}],tasks:[{id:'t1',title:'Ship UI'},{id:'t2',title:'Review UI'}],decisions:[{id:'d',summary:'Use SVG',made_by_actor_id:'a',affected_task_refs:['t1']}],relationships:[{type:'works_on',from_ref:'b',to_ref:'t1'},{type:'depends_on',from_ref:'t1',to_ref:'t2'},{type:'delegates_to',from_ref:'a',to_ref:'b',task_ref:'t1'}]};
test('whole-state graph preserves directed typed edges and decision field connections',()=>{
 const g=stateGraphData(fixture);assert.equal(g.nodes.length,5);assert.equal(g.edges.length,5);
 assert.equal(g.edges.find(e=>e.type==='delegates_to').task,'t1');
 assert.deepEqual(g.edges.filter(e=>e.derived).map(e=>e.type),['made_decision','affects_task']);
});
test('task and actor modes retain only relevant entities and relationship types',()=>{
 const tasks=stateGraphData(fixture,'tasks');assert.equal(tasks.nodes.length,2);assert.deepEqual(tasks.edges.map(e=>e.type),['depends_on']);
 const actors=stateGraphData(fixture,'actors');assert.equal(actors.nodes.length,2);assert.deepEqual(actors.edges.map(e=>e.type),['delegates_to']);assert.equal(actors.edges[0].task,'t1');
});
test('missing references load from lookup as context, else the edge is not drawn',()=>{
 const input={actors:[{id:'a',name:'Alice'}],tasks:[],relationships:[{type:'works_on',from_ref:'a',to_ref:'missing'},{type:'works_on',from_ref:'a',to_ref:'gone'},{type:'delegates_to',from_ref:'human',to_ref:'a'}]};
 const g=stateGraphData(input,'all',{tasks:{missing:{id:'missing',title:'Old task',status:'done'}}});
 const t=g.nodes.find(n=>n.id==='missing');assert.equal(t.stub,false);assert.equal(t.context,true);assert.equal(t.label,'Old task');
 assert.equal(g.nodes.some(n=>n.id==='gone'),false);assert.equal(g.edges.length,2);assert.equal(g.nodes.find(n=>n.id==='human').label,'human');assert.equal(g.stubCount,0);
 assert.equal(stateGraphData(input).nodes.some(n=>n.stub),false);
});
test('layout is deterministic, separates nodes, and terminates for dependency cycles',()=>{
 const g=stateGraphData({...fixture,relationships:[...fixture.relationships,{type:'depends_on',from_ref:'t2',to_ref:'t1'}]});
 const a=graphLayout(g),b=graphLayout(g);assert.deepEqual(a,b);assert.equal(new Set([...a.values()].map(p=>`${p.x},${p.y}`)).size,g.nodes.length);
 assert.ok([...a.values()].every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
});
test('empty states stay empty and duplicate relationships do not duplicate edges',()=>{
 assert.deepEqual(stateGraphData({}),{nodes:[],edges:[],stubCount:0});
 const r=fixture.relationships[0];assert.equal(stateGraphData({actors:fixture.actors,tasks:fixture.tasks,relationships:[r,r]}).edges.length,1);
});

test('active mode drops done/cancelled tasks and decisions that only affect them',()=>{
 const st={actors:[{id:'a',name:'A'}],tasks:[{id:'t1',title:'open',status:'progressing'},{id:'t2',title:'shut',status:'done'},{id:'t3',title:'gone',status:'cancelled'}],
  decisions:[{id:'d1',summary:'old',affected_task_refs:['t2','t3']},{id:'d2',summary:'live',affected_task_refs:['t1','t2']},{id:'d3',summary:'free'}],
  relationships:[{type:'works_on',from_ref:'a',to_ref:'t1'},{type:'works_on',from_ref:'a',to_ref:'t2'}]};
 const g=stateGraphData(st,'active');const ids=g.nodes.map(n=>n.id).sort();
 assert.deepEqual(ids,['a','d2','d3','t1']);assert.ok(g.edges.every(e=>ids.includes(e.from)&&ids.includes(e.to)));
});

test('window pruning: changed-only tasks, stale and agent clusters, done in window, decision importance',()=>{
 const H=3600,end=100*H;
 const st={actors:[{id:'a',name:'A'},{id:'b',name:'B'},{id:'idle',name:'Idle'}],
  tasks:[{id:'fresh',title:'fresh',status:'progressing',updated_at:end-60},{id:'old',title:'old',status:'progressing',updated_at:end-3*H},
   {id:'outside',title:'outside',status:'waiting',updated_at:end-50*H},{id:'shut',title:'shut',status:'done',updated_at:end-120},{id:'shutEarly',title:'shut early',status:'done',updated_at:end-50*H}],
  decisions:[{id:'dh',summary:'h',importance:'high',affected_task_refs:['fresh']},{id:'dl',summary:'l',importance:'low',affected_task_refs:['fresh']}],
  relationships:[{type:'works_on',from_ref:'a',to_ref:'fresh'},{type:'works_on',from_ref:'a',to_ref:'old'},{type:'works_on',from_ref:'b',to_ref:'outside'}]};
 // 1h window: no clustering; tasks last updated before the window count as unchanged; all importances allowed.
 const g=stateGraphData(st,'all',null,{start:end-H,end,staleSec:2*H});const ids=new Set(g.nodes.map(n=>n.id));
 assert.ok(ids.has('fresh')&&ids.has('shut')&&ids.has('dh')&&ids.has('dl'));
 assert.ok(!ids.has('outside')&&!ids.has('shutEarly')&&!ids.has('idle')&&!ids.has('b'));
 assert.ok(!ids.has('old')&&!ids.has('stale:a'));
 assert.ok(g.edges.every(e=>ids.has(e.from)&&ids.has(e.to)));
 assert.ok(g.chips.some(c=>c.includes('2 unchanged open'))&&g.chips.some(c=>c.includes('1 done before window')));
 // 6h window: agent a folds into a cluster; low decisions hidden; expanding a shows its tasks again.
 const d=stateGraphData(st,'all',null,{start:end-6*H,end,staleSec:2*H});const dn=new Map(d.nodes.map(n=>[n.id,n]));
 assert.equal(dn.get('a').cluster.count,2);assert.equal(dn.get('a').cluster.stale,1);assert.ok(!dn.has('fresh')&&!dn.has('dl'));
 const e=stateGraphData(st,'all',null,{start:end-6*H,end,staleSec:2*H,expanded:new Set(['a'])});const en=new Set(e.nodes.map(n=>n.id));
 assert.ok(en.has('fresh')&&en.has('stale:a')&&en.has('dh')&&!en.has('dl'));
});

test('importance filter hides lower-importance tasks and decisions into a chip',()=>{
 const H=3600,end=100*H;
 const st={actors:[{id:'a',name:'A'}],tasks:[{id:'hi',title:'hi',status:'progressing',importance:'high',updated_at:end-60},{id:'lo',title:'lo',status:'progressing',importance:'low',updated_at:end-60}],
  decisions:[{id:'dm',summary:'m',importance:'medium',affected_task_refs:['hi']}],relationships:[{type:'works_on',from_ref:'a',to_ref:'hi'},{type:'works_on',from_ref:'a',to_ref:'lo'}]};
 const ids=imp=>stateGraphData(st,'all',null,{start:end-H,end,importance:imp}).nodes.map(n=>n.id).sort().join(',');
 assert.equal(ids('auto'),'a,dm,hi,lo');assert.equal(ids('all'),'a,dm,hi,lo');assert.equal(ids('hm'),'a,dm,hi');assert.equal(ids('high'),'a,hi');
 assert.ok(stateGraphData(st,'all',null,{start:end-H,end,importance:'high'}).chips.includes('+1 lower-importance task'));
});
test('fold:false never clusters agents, even on wide windows',()=>{
 const H=3600,end=100*H;
 const st={actors:[{id:'a',name:'A'}],tasks:[{id:'t1',title:'t1',status:'progressing',updated_at:end-60},{id:'t2',title:'t2',status:'progressing',updated_at:end-120}],
  decisions:[],relationships:[{type:'works_on',from_ref:'a',to_ref:'t1'},{type:'works_on',from_ref:'a',to_ref:'t2'}]};
 const off=stateGraphData(st,'all',null,{start:end-8*H,end,staleSec:0,fold:false}),ids=new Set(off.nodes.map(n=>n.id));
 assert.ok(ids.has('t1')&&ids.has('t2'));assert.ok(!off.nodes.find(n=>n.id==='a').cluster);
 const on=stateGraphData(st,'all',null,{start:end-8*H,end,staleSec:0});assert.equal(on.nodes.find(n=>n.id==='a').cluster.count,2);
});
test('region boxes wrap each region, contain their nodes and never overlap each other',()=>{
 const st={actors:[{id:'a',name:'A'},{id:'b',name:'B'}],
  tasks:[{id:'t1',title:'t1',status:'progressing'},{id:'t2',title:'t2',status:'waiting'},{id:'d1',title:'d1',status:'done'},{id:'d2',title:'d2',status:'done'}],
  decisions:[{id:'k',summary:'k',importance:'high',affected_task_refs:['t1']}],
  relationships:[{type:'works_on',from_ref:'a',to_ref:'t1'},{type:'works_on',from_ref:'b',to_ref:'t2'}]};
 for(const aspect of [0,1.2,2.5]){
  const g=stateGraphData(st,'all'),pos=graphLayout(g,aspect),boxes=regionBoxes(g,pos);
  assert.deepEqual(boxes.map(b=>b.label),['Agents','Tasks','Key decisions','Completed · 2']);
  for(const n of g.nodes){const p=pos.get(n.id),inside=boxes.filter(b=>p.x>b.x&&p.x<b.x+b.w&&p.y>b.y+20&&p.y<b.y+b.h);assert.equal(inside.length,1,n.id+' in one box');}
  for(const [i,a] of boxes.entries())for(const b of boxes.slice(i+1))assert.ok(a.x+a.w<=b.x||b.x+b.w<=a.x||a.y+a.h<=b.y||b.y+b.h<=a.y,`${a.label} overlaps ${b.label}`);
 }
});
