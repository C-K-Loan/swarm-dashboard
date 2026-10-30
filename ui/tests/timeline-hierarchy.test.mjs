import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import '../public/timeline-hierarchy.js';
const H = globalThis.TimelineHierarchy;
const source = fs.readFileSync(new URL('../src/pages/timeline.astro', import.meta.url), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('    const STATE_RANK'), source.indexOf('    // v15-E: spawn hierarchy')) + '\nthis.bucketize = bucketizeAgent;', context);
const bucketize = context.bucketize;
const seg = (start, turns = 1, end = start + 100) => ({ start_ts: start, end_ts: end, turn_count: turns, cost_usd: turns / 100, tokens_in: turns * 10, tokens_out: turns, state: 'working' });
const agent = (id, fleet, segments) => ({ id, title: id, fleet, segments });
const agents = [agent('g1','gamejam',[seg(3600,2),seg(7199,3),seg(7200,5)]), agent('g2','gamejam',[seg(3600,7)]), agent('t1','other-project',[seg(3600,11)]), agent('loose','',[seg(3601,13)])];

test('fleet grouping deduplicates canonical sessions and publishes only real members', () => {
 const rows = H.buildRows([...agents, agents[0]], 'fleet');
 assert.deepEqual(rows.map(r => [r.title, r.members.length]), [['gamejam',2],['other-project',1],['Unassigned fleet',1]]);
 const scope = H.scopeForRows(rows.slice(0,2),[3600,7200]);
 assert.deepEqual([...scope.agentIds],['g1','g2','t1']);
 assert.deepEqual(scope.timeRange,[3600,7200]);
 assert(![...scope.agentIds].some(id=>id.startsWith('group:')));
});
test('orgs combine fleets and explicit loose members; assignment overrides and unassigned stay honest', () => {
 const rows = H.buildRows(agents, 'org', { orgs:[{id:'o',label:'Org A'}], fleetToOrg:{gamejam:'o'}, agentToOrg:{loose:'o',g2:'other'} });
 assert.deepEqual(rows.map(r=>[r.title,r.members.map(a=>a.id)]),[['Org A',['g1','loose']],['other',['g2']],['Unassigned org',['t1']]]);
 assert.equal(H.buildRows(agents,'agent'),agents);
});
test('group bucket expands all members with exact half-open assignment and no duplicate selected cells', () => {
 const row=H.buildRows(agents,'fleet')[0];
 const cell=bucketize(row,3600,7200,3600)[0];
 const picked=H.expandPicked([{a:row,cell},{a:row,cell}],bucketize);
 assert.equal(picked.length,2);
 assert.equal(picked.reduce((n,p)=>n+p.cell.turn_count,0),12);
 assert.equal(cell.turn_count,12);
 const only=H.expandPicked([{a:row,cell}],bucketize,{titles:new Set(['g1'])});
 assert.deepEqual(only.map(p=>p.a.id),['g1']);
 assert.equal(only[0].cell.turn_count,5);
});
test('group raw intervals do not overlap or count long segments twice', () => {
 const row=H.buildRows([agent('x','f',[seg(3600,2,8000),seg(4000,3,8500)]),agent('y','f',[seg(4000,5,9000)])],'fleet')[0];
 const cells=H.rawGroupCells(row,bucketize);
 assert.equal(cells.length,2);
 assert.equal(cells[0].end_ts,cells[1].start_ts);
 const picked=H.expandPicked(cells.map(cell=>({a:row,cell})),bucketize);
 assert.equal(picked.reduce((n,p)=>n+p.cell.turn_count,0),10);
});
test('comms interval includes lower bound and excludes upper bound', () => {
 const msg=t=>({t,a2a:true,x:'[from g1] hello'});
 const graph=H.commsInWindow({g2:[msg(3599),msg(3600),msg(7199),msg(7200)]},agents,[3600,7200]);
 assert.equal(graph.edges.length,1);
 assert.deepEqual(graph.edges[0].slice(0,3),['g1','g2',2]);
});
test('summary rollup retains every bullet and provenance on duplicate text', () => {

 const rows=[{_sid:'g1',_hb:3600,summary:{started:Array.from({length:12},(_,i)=>`task ${i}`)}},{_sid:'g2',_hb:3600,summary:{started:['task 0','你好','こんにちは']}}];
 const bullets=H.mergeBullets(rows,'started');
 assert.equal(bullets.length,14);
 assert.equal(bullets[0].sources.length,2);
});

test('canonical IDs take precedence over shared historical titles, including comms', () => {
 const sameTitle=[{...agents[0],title:'shared'},{...agents[2],title:'shared'}];
 const scope={agentIds:new Set(['g1']),titles:new Set(['shared'])};
 assert.deepEqual(sameTitle.filter(a=>H.matches(a,scope)).map(a=>a.id),['g1']);
 assert.equal(sameTitle.filter(a=>H.matches(a,{titles:new Set(['shared'])})).length,2);
 const msg={t:3600,a2a:true,x:'[from other] hello'};
 const graph=H.commsInWindow({g1:[msg],t1:[msg]},sameTitle,[3600,7200],scope);
 assert.equal(graph.edges[0][2],1);
});

test('real CCT sender peers survive synthetic recipient titles within canonical time/member scope', () => {
 const agents=[agent('sid','gamejam',[seg(3600)]),agent('outside','other',[seg(3600)])];
 agents[0].title='codex-gamejam-synthetic-history';
 const msg=t=>({t,a2a:'gamejam-codex-producer',x:'[from gamejam-codex-producer] finished'});
 const graph=H.commsInWindow({sid:[msg(3600),msg(7199),msg(7200)],outside:[msg(3600)]},agents,[3600,7200],{agentIds:new Set(['sid'])});
 assert.deepEqual(graph.edges.map(e=>e.slice(0,3)),[['gamejam-codex-producer','codex-gamejam-synthetic-history',2]]);
 assert.deepEqual(graph.nodes.map(n=>n.n),['gamejam-codex-producer','codex-gamejam-synthetic-history']);
});
test('daily summary accumulates all 24 hours and uses identical collection for a group and multi-agent selection', () => {
 const day=86400,index={};
 for (let hour=0;hour<25;hour++) for (const sid of ['a','b']) index[`${sid}|${day+hour*3600}`]={summary:{started:[`Hour ${hour} task`, 'Same exact bullet'],finished:[],blocked:[],key_decisions:[]}};
 const rollup=H.summaryRollup(index,['a','b','a'],day,day+86400);
 assert.equal(rollup.rows.length,48);
 assert.equal(rollup.rawBullets,96);
 assert.equal(rollup.uniqueBullets,25);
 assert.equal(rollup.duplicates,71);
 assert.equal(rollup.coveredAgents,2);
 assert.equal(rollup.fields.started.find(b=>b.text==='Same exact bullet').sources.length,48);
 assert(!rollup.fields.started.some(b=>b.text==='Hour 24 task'));
 const selected=H.collectSummaries(index,['b','a'],day,day+86400);
 assert.deepEqual(H.mergeBullets(selected,'started'),rollup.fields.started);
});
test('exact normalization merges only case/whitespace variants and preserves materially different punctuation/numbers', () => {
 const rows=[{_sid:'a',_hb:0,summary:{started:['Hello  world','hello world','A-B','A B','Budget 10','Budget 100']}}];
 const bullets=H.mergeBullets(rows,'started');
 assert.equal(bullets.length,5);
 assert.deepEqual(bullets.map(b=>b.text),['Hello  world','A-B','A B','Budget 10','Budget 100']);
});
test('local known Gamejam day retains all 160 source hours and 2170 raw bullets', t => {
 const indexPath=new URL('../public/data/bucket_summaries.json',import.meta.url);
 const timelinePath=new URL('../../data/timeline.json',import.meta.url);
 if(!fs.existsSync(indexPath)||!fs.existsSync(timelinePath)) return t.skip('local ignored source indexes unavailable');
 const index=JSON.parse(fs.readFileSync(indexPath,'utf8'));
 const agents=JSON.parse(fs.readFileSync(timelinePath,'utf8')).agents.filter(a=>a.fleet==='gamejam');
 const day=Date.parse('2026-10-12T00:00:00Z')/1000;
 const result=H.summaryRollup(index,agents.map(a=>a.id),day,day+86400);
 assert.equal(result.rows.length,160);
 assert.equal(result.coveredAgents,23);
 assert.equal(result.rawBullets,2170);
 assert(result.uniqueBullets>=2165);
 assert.equal(result.uniqueBullets+result.duplicates,result.rawBullets);
});
