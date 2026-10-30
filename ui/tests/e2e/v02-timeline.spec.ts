import fs from 'node:fs';
import {test,expect} from '@playwright/test';
import {goTimeline,waitForChart,clickRect} from './_helpers';

// Pin regression checks to the preserved first-hour corpus while day1 labels stream in.
const fixtureRoot=new URL('../../../data/v02/prototype/',import.meta.url);
const prototypeIndex=JSON.parse(fs.readFileSync(new URL('turn-index.json',fixtureRoot),'utf8'));
const prototypeFixture={...prototypeIndex,turns:prototypeIndex.turns.map((t:any)=>t.has_prediction
 ?JSON.parse(fs.readFileSync(new URL(`turn-states/${t.id}.json`,fixtureRoot),'utf8')):{...t,prediction:null})};
test.beforeEach(async({page})=>{
 await page.route('**/api/v02/dataset',route=>route.fulfill({json:{...prototypeIndex,reviews:{},quota:{}}}));
 await page.route('**/api/v02/selection',route=>{
  const windows=route.request().postDataJSON().windows;
  const turns=prototypeFixture.turns.filter((t:any)=>windows.some((w:any)=>w.sid===t.session_id&&t.end_ts>=w.start&&t.end_ts<w.end));
  return route.fulfill({json:{...prototypeFixture,turns}});
 });
});
const url='/timeline?fleet=gamejam&range=all&bucket=86400';
const widget='.timeline-v02-summary';
test('original timeline widgets host scoped state, replay and exact input without changing selection',async({page})=>{
 test.setTimeout(180_000);
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await goTimeline(page,url);await waitForChart(page);await clickRect(page,'#tl-chart rect.col-header-hit');
 await expect(page.locator(widget)).toBeVisible();
 for(const id of ['state-graph','prose','stats','transcript','artifacts','comms','diffs','tree','spawn-tree']) await expect(page.locator(`[data-widget-id="${id}"]`)).toBeAttached();
 await clickRect(page,'#tl-chart rect.col-header-hit');
 await expect(page.locator(widget)).toHaveAttribute('data-turn','turn-0043');
 const scope=await page.evaluate(()=>JSON.stringify((window as any)._selectionBus.lastScope,(_k,v)=>v instanceof Set?[...v]:v));
 await page.locator(`${widget} [data-action="first"]`).click();await expect(page.locator(widget)).toHaveAttribute('data-turn','turn-0001');
 await page.locator(`${widget} [data-action="next"]`).click();await expect(page.locator(widget)).toHaveAttribute('data-turn','turn-0002');
 await page.locator(`${widget} [data-control="view"]`).selectOption('input');
 const data=prototypeFixture;
 expect(JSON.parse((await page.locator(`${widget} [data-state-json]`).textContent())!)).toEqual(data.turns[1].llm_input);
 await page.locator(`${widget} [data-action="last"]`).click();await page.locator(`${widget} [data-control="view"]`).selectOption('state');
 const node=page.locator(`${widget} .sg-node`).first();await node.focus();await page.keyboard.press('Enter');await page.keyboard.press('Escape');
 expect(await page.evaluate(()=>JSON.stringify((window as any)._selectionBus.lastScope,(_k,v)=>v instanceof Set?[...v]:v))).toBe(scope);
 await page.locator(`${widget} [data-control="grain"]`).selectOption('hour');await expect(page.locator(`${widget} [data-cursor]`)).toContainText('1/1 hour endpoints');
 await page.locator(`${widget} [data-control="grain"]`).selectOption('turn');
 await page.locator(`${widget} [data-action="play"]`).click();await expect(page.locator(widget)).toHaveAttribute('data-playing','true');
 await expect.poll(()=>page.locator(widget).getAttribute('data-turn')).not.toBe('turn-0001');
 // Summary is its own widget on the same selection: switching its version leaves the playing task graph alone.
 await expect(page.locator('#prose-head .v01b-tree')).toBeVisible();await expect(page.locator('#prose-head .v01b-info')).not.toHaveText('loading…');
 await page.locator('#summary-version').selectOption('0.1');await expect(page.locator('#prose-body')).toContainText('Hourly source summaries');await expect(page.locator('#prose-head .v01b-tree')).toHaveCount(0);
 await expect(page.locator(widget)).toHaveAttribute('data-playing','true');
 await page.screenshot({path:'/tmp/swarm-v02-timeline-integrated.png',fullPage:false});
 expect(errors).toEqual([]);
});
test('selection changes and restored disjoint windows update the graph without filling gaps',async({page})=>{
 await goTimeline(page,url);await waitForChart(page);await clickRect(page,'#tl-chart rect.col-header-hit');await expect(page.locator(widget)).toBeVisible();
 const selected=await page.evaluate(async()=>{
  const d=await fetch('/api/v02/dataset').then(r=>r.json());const rows=[d.turns[6],d.turns[40]];
  const windows=rows.map((t:any)=>({sid:t.session_id,start:t.end_ts,end:t.end_ts+.0001}));
  (window as any)._selectionBus.publish({agentIds:new Set(rows.map((t:any)=>t.session_id)),titles:null,timeRange:[rows[0].end_ts,rows[1].end_ts+.0001],windows,source:'timeline',restoreSelection:true,stickyElementKey:null});
  return {ids:rows.map((t:any)=>t.id),count:d.turns.filter((t:any)=>t.has_prediction&&windows.some(w=>w.sid===t.session_id&&t.end_ts>=w.start&&t.end_ts<w.end)).length};
 });
 await expect(page.locator('#bs-sub')).toHaveCount(0);await expect(page.locator(widget)).toHaveAttribute('data-turn',selected.ids[1]);
 await page.locator(`${widget} [data-action="first"]`).click();await expect(page.locator(widget)).toHaveAttribute('data-turn',selected.ids[0]);
 await page.locator(`${widget} [data-control="view"]`).selectOption('changes');
 await page.locator(`${widget} [data-action="play"]`).click();
 // Real chart navigation replaces a playing selection and an unprocessed day clears its graph.
 await page.evaluate(()=>{const cols=document.querySelectorAll('#tl-chart rect.col-header-hit');cols[1].dispatchEvent(new MouseEvent('click',{bubbles:true}));});
 await expect(page.locator('#bs-body')).toContainText('No generated V0.2 turns');await expect(page.locator(widget)).toHaveCount(0);
});
test('saved v2 layouts gain the summary widget beside the task graph once',async({page})=>{
 await page.addInitScript(()=>{if(sessionStorage.getItem('seeded'))return;sessionStorage.setItem('seeded','1');
  localStorage.setItem('cct_widget_profiles_v1',JSON.stringify({v:2,active:'default',custom:{},current:{'state-graph':{x:0,y:0,w:6,h:7,hidden:false},comms:{x:6,y:0,w:6,h:7,hidden:false},transcript:{x:0,y:7,w:6,h:6,hidden:false}}}));});
 await goTimeline(page,url);
 const pos=(id:string)=>page.locator(`.grid-stack-item[gs-id="${id}"]`).evaluate((el:any)=>({x:el.gridstackNode.x,y:el.gridstackNode.y,w:el.gridstackNode.w}));
 await expect.poll(()=>pos('prose')).toEqual({x:6,y:0,w:6});
 expect(await pos('comms')).toEqual({x:6,y:7,w:6});
 expect(await pos('state-graph')).toEqual({x:0,y:0,w:6});
 await expect(page.locator('#wt-widgets-n')).toHaveText('9/9');
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('cct_widget_profiles_v1')!).v)).toBe(3);
 await page.reload();await expect.poll(()=>pos('prose')).toEqual({x:6,y:0,w:6});expect(await pos('comms')).toEqual({x:6,y:7,w:6});
});
