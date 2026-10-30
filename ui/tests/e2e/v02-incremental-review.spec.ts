import fs from 'node:fs';
import {test,expect} from '@playwright/test';

const root=new URL('../../../data/v02/prototype/',import.meta.url);
const index=JSON.parse(fs.readFileSync(new URL('turn-index.json',root),'utf8'));
const detail=(id:string)=>JSON.parse(fs.readFileSync(new URL(`turn-states/${id}.json`,root),'utf8'));

test('dataset API returns a compact index and one exact historical turn on demand',async({request})=>{
 const response=await request.get('/api/v02/dataset?dataset=day1');
 expect(response.ok()).toBeTruthy();const data=await response.json();
 expect(data.layout_version).toBe(2);expect(data.turns).toHaveLength(1902);
 expect(data.turns.every((t:any)=>!('state_after'in t)&&!('llm_input'in t)&&!('prediction'in t))).toBeTruthy();
 expect((await response.body()).byteLength).toBeLessThan(10_000_000);
 const full=await (await request.get('/api/v02/dataset?dataset=day1&turn=turn-0043')).json();
 const saved=JSON.parse(fs.readFileSync(new URL('../../../data/v02/day1/turn-states/turn-0043.json',import.meta.url),'utf8'));
 expect(full.turn.state_after).toEqual(saved.state_after);expect(full.turn.llm_input).toEqual(saved.llm_input);
 expect(full.turn).not.toHaveProperty('_publication');
 const pending=await (await request.get('/api/v02/dataset?dataset=day1&turn=turn-1902')).json();
 expect(pending.turn.messages.length).toBeGreaterThan(0);expect(pending.turn.prediction).toBeNull();expect(pending.turn.state_after).toBeNull();
 expect((await request.get('/api/v02/dataset?dataset=day1&turn=..%2Fmanifest')).status()).toBe(400);
 expect((await request.get('/api/v02/dataset?dataset=day1&turn=turn-9999')).status()).toBe(404);
});

test('review loads only selected turn details and ignores an older response after navigation',async({page})=>{
 const requests:string[]=[];const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 let releaseSlow:()=>void=()=>{};
 const slow=new Promise<void>(resolve=>releaseSlow=resolve);
 await page.route('**/api/v02/dataset*',async route=>{
  const id=new URL(route.request().url()).searchParams.get('turn');
  if(!id)return route.fulfill({json:{...index,dataset:'prototype',reviews:{},quota:{}}});
  requests.push(id);
  if(id==='turn-0007')await slow;
  await route.fulfill({json:{dataset:'prototype',turn:detail(id)}});
 });
 await page.goto('/review?dataset=prototype&turn=turn-0001',{waitUntil:'domcontentloaded'});
 await expect(page.locator('#detail-state')).toHaveText('machine draft');
 expect(requests).toEqual(['turn-0001']);
 await page.locator('[data-turn="turn-0007"]').click();
 await expect.poll(()=>requests.includes('turn-0007')).toBe(true);
 await expect(page.locator('#approve')).toBeDisabled();
 await page.locator('[data-turn="turn-0008"]').click();
 await expect(page.locator('#detail-meta')).toContainText('turn-0008');
 await expect(page.locator('#approve')).toBeEnabled();
 releaseSlow();
 await expect.poll(()=>requests.length).toBe(3);
 await page.waitForTimeout(150);
 await expect(page.locator('#detail-meta')).toContainText('turn-0008');
 expect(JSON.parse((await page.locator('#llm-state-json').textContent())!)).toEqual(detail('turn-0008').llm_input);
 expect(errors).toEqual([]);
});
