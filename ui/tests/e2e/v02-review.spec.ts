import fs from 'node:fs';
import {test,expect} from '@playwright/test';

// Pin regression checks to the preserved first-hour corpus while day1 labels stream in.
const fixtureRoot=new URL('../../../data/v02/prototype/',import.meta.url);
const prototypeIndex=JSON.parse(fs.readFileSync(new URL('turn-index.json',fixtureRoot),'utf8'));
const sourceTurns=JSON.parse(fs.readFileSync(new URL('turns.json',fixtureRoot),'utf8'));
const prototypeFixture={...prototypeIndex,turns:prototypeIndex.turns.map((entry:any)=>entry.has_prediction
 ?JSON.parse(fs.readFileSync(new URL(`turn-states/${entry.id}.json`,fixtureRoot),'utf8'))
 :{...sourceTurns.find((t:any)=>t.id===entry.id),prediction:null,state_after:null,llm_input:null,task_names:{},task_states:{}})};
test.beforeEach(async({page})=>{
 await page.route('**/api/v02/dataset*',route=>{
  const id=new URL(route.request().url()).searchParams.get('turn');
  return route.fulfill({json:id?{turn:prototypeFixture.turns.find((t:any)=>t.id===id)}:{...prototypeIndex,reviews:{},quota:{}}});
 });
});
test('draft source, correction failure and successful retry preserve edits',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/review?turn=turn-0008',{waitUntil:'domcontentloaded'});
 await expect(page.locator('#generated')).toHaveText('43 / 72');
 await expect(page.locator('#window')).toContainText('quota limit');
 await expect(page.locator('#prediction .entity').first()).toBeVisible();
 await page.locator('#edit').click();
 const input=page.locator('[data-task="0"][data-field="title"]');await input.fill('Reviewed task title');
 let calls=0;
 await page.route('**/api/v02/review',async route=>{
  const body=route.request().postDataJSON();calls++;
  expect(body.correction.tasks[0].changes.title).toBe('Reviewed task title');
  expect(body.base_revision).toBe(0);
  await route.fulfill({status:calls===1?400:200,contentType:'application/json',body:JSON.stringify(calls===1?{error:'Fixture conflict: reload required'}:{review:{...body,revision:1}})});
 });
 await page.locator('#save').click();await expect(page.locator('#save-status')).toContainText('Fixture conflict');await expect(input).toHaveValue('Reviewed task title');
 await page.locator('#save').click();await expect(page.locator('#save-status')).toHaveText('Saved locally');await expect(page.locator('#detail-state')).toContainText('corrected');
 await expect(page.locator('#prediction')).toContainText('Reviewed task title');expect(errors).toEqual([]);
});
test('evaluation does not invent scores before human review',async({page})=>{
 await page.route('**/api/v02/metrics',async route=>{
  const response=await route.fetch();const data=await response.json();data.reviewed_turns=0;data.unreviewed_turns=data.generated_turns;
  data.metrics.tasks.f1=null;data.metrics.hallucination.unsupported_rate=null;
  await route.fulfill({response,json:data});
 });
 await page.goto('/eval-review',{waitUntil:'domcontentloaded'});await expect(page.locator('#reviewed-count')).toHaveText('0');await expect(page.locator('#task-f1')).toHaveText('Not measured');await expect(page.locator('#hallucination-rate')).toHaveText('Not measured');
});
test('mobile review fits viewport and has usable source and navigation',async({page})=>{
 await page.setViewportSize({width:390,height:844});await page.goto('/review?turn=turn-0007',{waitUntil:'domcontentloaded'});await expect(page.locator('#generated')).toHaveText('43 / 72');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await page.screenshot({path:'/tmp/swarm-v02-review-mobile.png',fullPage:true});
});
test('V0.2 summary honors disjoint intervals and half-open boundaries',async({page})=>{
 await page.goto('/review',{waitUntil:'domcontentloaded'});await expect(page.locator('#generated')).toHaveText('43 / 72');
 const result=await page.evaluate(async()=>{
  const {renderV02Summary}=await import('/src/lib/v02-summary.ts');
  const data=await fetch('/api/v02/dataset').then(r=>r.json());const rows=data.turns.filter((t:any)=>t.has_prediction);const t=rows[6],last=rows[40];
  const body=document.createElement('div'),sub=document.createElement('div');
  const windows=[t,last].map((v:any)=>({sid:v.session_id,start:v.end_ts,end:v.end_ts+.0001}));
  const expected=rows.filter((v:any)=>windows.some(w=>w.sid===v.session_id&&v.end_ts>=w.start&&v.end_ts<w.end)).length;
  await renderV02Summary(body,sub,{windows});const selected=sub.textContent;
  await renderV02Summary(body,sub,{windows:[{sid:t.session_id,start:t.end_ts-1,end:t.end_ts}]});
  return {selected,expected,empty:body.textContent};
 });
 expect(result.selected).toContain(`${result.expected} completed turns`);expect(result.empty).toContain('No generated V0.2 turns');
});

test('each turn shows complete historical state and its exact recorded LLM input',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const data=prototypeFixture;
 const completed=data.turns.filter((t:any)=>t.prediction);const last=completed.at(-1);
 await page.goto(`/review?turn=${last.id}`,{waitUntil:'domcontentloaded'});
 await expect(page.locator('#state-turn-label')).toContainText(last.id);
 await expect(page.locator('#overall-state .state-task')).toHaveCount(Object.keys(last.state_after.tasks).length);
 await expect(page.locator('#llm-state .state-task')).toHaveCount(last.llm_input.relevant_existing_tasks_with_ids.length);
 expect(JSON.parse((await page.locator('#overall-state-json').textContent())!)).toEqual(last.state_after);
 expect(JSON.parse((await page.locator('#llm-state-json').textContent())!)).toEqual(last.llm_input);
 const untouched=Object.keys(last.state_after.tasks).filter(id=>!last.resolved_prediction.tasks.some((t:any)=>t.ref===id));
 expect(untouched.length).toBeGreaterThan(0);
 await expect(page.locator(`#overall-state [data-task-id="${untouched[0]}"]`)).toHaveCount(1);
 const first=completed[0];await page.locator(`[data-turn="${first.id}"]`).click();
 await expect(page.locator('#state-turn-label')).toContainText(first.id);
 expect(JSON.parse((await page.locator('#overall-state-json').textContent())!)).toEqual(first.state_after);
 const pending=data.turns.find((t:any)=>!t.prediction);await page.locator(`[data-turn="${pending.id}"]`).click();
 await expect(page.locator('#overall-state')).toContainText('has not been processed');
 await expect(page.locator('#llm-state')).toContainText('No LLM input was recorded');
 await expect(page.locator('#overall-state-json')).toHaveCount(0);expect(errors).toEqual([]);
});

test('both state graphs support hover, pinning, filtering, zoom and historical reset',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/review?turn=turn-0043',{waitUntil:'domcontentloaded'});
 const graph=page.locator('#overall-state-graph'), input=page.locator('#llm-state-graph');
 await expect(graph.locator('.sg-node')).not.toHaveCount(0);await expect(input.locator('.sg-node')).not.toHaveCount(0);
 const task=graph.locator('.sg-node[data-node-kind="task"]').first();const id=await task.getAttribute('data-node-id');
 await task.hover();await expect(graph.locator('.sg-detail')).toContainText(id!);await expect(graph.locator('.sg-node.sg-dim').first()).toBeAttached();
 await task.click();await expect(graph).toHaveAttribute('data-pinned',id!);
 await graph.locator('.sg-hint').hover();await expect(graph).toHaveAttribute('data-pinned',id!);
 await expect(input).toHaveAttribute('data-pinned','');
 const before=await graph.locator('svg').getAttribute('viewBox');await graph.getByRole('button',{name:'+',exact:true}).click();expect(await graph.locator('svg').getAttribute('viewBox')).not.toBe(before);
 await graph.getByRole('button',{name:'Reset',exact:true}).click();await expect(graph).toHaveAttribute('data-pinned','');
 const canvas=graph.locator('svg');const fitView=await canvas.getAttribute('viewBox');const box=(await canvas.boundingBox())!;
 await page.mouse.move(box.x+4,box.y+4);await page.mouse.down();await page.mouse.move(box.x+44,box.y+24,{steps:4});await page.mouse.up();expect(await canvas.getAttribute('viewBox')).not.toBe(fitView);
 await graph.getByRole('button',{name:'Expand',exact:true}).click();await expect.poll(()=>page.evaluate(()=>document.fullscreenElement?.id)).toBe('overall-state-graph');
 await page.evaluate(()=>document.exitFullscreen());
 await graph.getByRole('button',{name:'Tasks',exact:true}).click();await expect(graph.locator('.sg-node[data-node-kind="actor"]')).toHaveCount(0);await expect(graph.locator('.sg-node[data-node-kind="decision"]')).toHaveCount(0);
 const types=await graph.locator('.sg-edge').evaluateAll(els=>els.map(el=>el.getAttribute('data-edge-type')));expect(types.every(t=>['depends_on','is_sub_task'].includes(t!))).toBe(true);
 await graph.getByRole('button',{name:'Actors',exact:true}).click();await expect(graph.locator('.sg-node[data-node-kind="task"]')).toHaveCount(0);
 const actor=graph.locator('.sg-node').first();await actor.focus();await page.keyboard.press('Enter');await expect(graph).not.toHaveAttribute('data-pinned','');await page.keyboard.press('Escape');await expect(graph).toHaveAttribute('data-pinned','');
 await page.locator('[data-turn="turn-0001"]').click();await expect(graph.locator('.sg-node[data-node-kind="task"]')).toHaveCount(0);
 await page.locator('[data-turn="turn-0044"]').click();await expect(page.locator('#overall-state-graph')).toHaveCount(0);await expect(page.locator('#llm-state-graph')).toHaveCount(0);expect(errors).toEqual([]);
});
