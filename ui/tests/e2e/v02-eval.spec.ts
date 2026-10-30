import {test,expect} from '@playwright/test';

test('V0.2 eval shows base vs LoRA, leaderboard windows, training and spend from data',async({page,request})=>{
  test.setTimeout(120000);
  const r=await request.get('/api/v02/eval-summary',{timeout:90000});expect(r.ok()).toBeTruthy();
  const d=await r.json();expect(d.val.lora.turns).toBe(131);
  await page.goto('/eval?schema=lean',{waitUntil:'domcontentloaded'});
  await expect(page.locator('#eval-status')).toHaveText('live',{timeout:60000});
  const best=d.val.lora2?.turns>=d.val.lora.turns?d.val.lora2:d.val.lora;await expect(page.locator('#kpi-f1')).toHaveText((best.touched_existing_f1*100).toFixed(1)+'%');
  await expect(page.locator('#headline-table')).toContainText('Touched-task F1');
  await expect(page.locator('#headline-note')).toContainText('Relationship F1 is not shown');
  const lb=page.locator('#leaderboard-table');
  await expect(page.getByRole('tab',{name:/Eval slice/})).toHaveAttribute('aria-selected','true');await expect(lb).toContainText('LoRA v1 rollout (v2 rollout not run)');await expect(page.locator('#not-run')).not.toContainText('LoRA');await expect(page.locator('#headline-table')).not.toContainText('LoRA v1');await page.getByRole('tab',{name:/Day1 first 4h/}).click();await expect(lb).toContainText('Haiku 4.5 lean');
  await expect(lb.locator('a[href*="labels=haiku45-lean-day1"]')).toHaveCount(1);
  await page.getByRole('tab',{name:/Day1 first 4h/}).click();await expect(page.locator('#window-note')).toContainText('not a holdout');await expect(page.locator('#leaderboard-table')).toContainText('seen in training, not a holdout');await page.getByRole('tab',{name:/Eval slice 1523-1902/}).click();await expect(page.locator('#window-note')).toContainText('holdout for every row');
  await expect(page.getByRole('tab',{name:/Eval slice/})).toHaveAttribute('aria-selected','true');
  await expect(page.locator('#training-cards')).toContainText('LoRA v2');await expect(page.locator('#training-cards')).toContainText('superseded');
  await expect(page.locator('#spend-table')).toContainText('Paid total');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

test('schema dropdown switches the eval page and keeps the choice in the URL',async({page})=>{
  test.setTimeout(120000);
  await page.goto('/eval?schema=prose-v2',{waitUntil:'domcontentloaded'});
  await expect(page.getByLabel('Schema')).toHaveValue('prose-v2');
  await expect(page.locator('#merge-eval')).toContainText('col-4h');
  await expect(page.locator('#merge-eval')).toContainText('Agreement with the reference (Claude Haiku merges)');
  await expect(page.locator('#merge-eval')).toContainText('same vendor');
  await expect(page.locator('#merge-eval')).not.toContainText('indicative');await expect(page.locator('#merge-eval')).not.toContainText('col-day');
  await expect(page.locator('#merge-eval .judge-view:not([hidden])')).toHaveCount(1);
  await page.getByLabel('Schema').selectOption('prose-v1');
  await expect(page).toHaveURL(/schema=prose-v1/);
  await expect(page.locator('#chart-agreement')).toBeVisible();
  await expect(page.locator('#eval-status')).not.toHaveText('loading',{timeout:30000});
  await page.getByLabel('Schema').selectOption('lean');
  await expect(page).toHaveURL(/schema=lean/);
  await expect(page.locator('#headline-table')).toContainText('Touched-task F1',{timeout:60000});
  await page.goto('/eval?schema=bogus',{waitUntil:'domcontentloaded'});
  await expect(page.getByLabel('Schema')).toHaveValue('summary');
});

test('summary is the default schema and fits one 1920x1080 screen',async({page,request})=>{
  test.setTimeout(120000);
  const d=await (await request.get('/api/v02/eval-summary',{timeout:90000})).json();
  await page.setViewportSize({width:1920,height:1080});
  await page.goto('/eval',{waitUntil:'domcontentloaded'});
  await expect(page.getByLabel('Schema')).toHaveValue('summary');
  await expect(page.locator('#sum-status')).toHaveText('live',{timeout:60000});
  const last=d.val.lora2?.turns>=d.val.lora.turns?d.val.lora2:d.val.lora;
  await expect(page.locator('#s3-big')).toHaveText((last.touched_existing_f1*100).toFixed(1)+'%');
  const s1=d.cost_quality.stage1.opus.models.find((m:any)=>m.production);
  await expect(page.locator('#s1-big')).toHaveText((s1.mean_acc*100).toFixed(1)+'%');
  await expect(page.locator('#heads .head').first()).toContainText('vs Claude Opus labels');
  await expect(page.locator('#heads .head').first()).toContainText('majority baseline');
  await expect(page.locator('#eval-summary-screen')).toContainText('F1 vs Claude Haiku 4.5 merges');
  await expect(page.locator('#f-spend')).toContainText('USD');
  await expect(page.locator('#heads .head')).toHaveCount(4);
  await expect(page.locator('#heads')).toContainText('cheaper');
  await expect(page.locator('#scatter svg circle').first()).toBeVisible();
  await expect(page.locator('#heads .head.all')).toContainText(Math.round(d.cost_quality.overall.cost_saving*100)+'% cheaper');
  expect(await page.locator('#heads .hd-n').evaluateAll(es=>es.every(e=>e.scrollWidth<=e.clientWidth+1))).toBe(true);
  await expect(page.locator('header #eval-schema')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1&&document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

test('prose-v1 tab scores Stage 1 per field against the Opus gold set',async({page,request})=>{
  test.setTimeout(120000);
  const o=(await (await request.get('/api/v02/eval-summary',{timeout:90000})).json()).cost_quality.stage1.opus;
  await page.goto('/eval?schema=prose-v1',{waitUntil:'domcontentloaded'});
  const t=page.locator('#s1o-table');
  await expect(t.locator('tr')).toHaveCount(1+o.models.length+o.not_run.length,{timeout:60000});
  await expect(t).toContainText('majority label');
  await expect(t.locator('tr.ours')).toContainText((o.models.find((m:any)=>m.production).mean_acc*100).toFixed(1)+'%');
  await expect(t).toContainText('LoRA not run on the Opus set');
});

test('V0.1 charts name the Qwen3-30B pseudo-label reference, not Opus gold',async({page})=>{
  for(const u of ['/eval?schema=prose-v1','/eval-v01']){
    await page.goto(u,{waitUntil:'domcontentloaded'});
    const b=page.locator('main.eval-page').last();
    await expect(b.locator('#chart-alignment .bar-row').first()).toContainText('Qwen3-30B labels (ref)',{timeout:60000});
    await expect(b).toContainText('Qwen3-30B pseudo-labels (685 held-out msgs)');
    await expect(b.locator('#kpi-rows')).toHaveText('685');
    await expect(b).not.toContainText('Opus gold');
    await expect(b).not.toContainText('Opus 5.5 (truth)');
  }
});

test('Nemotron Ultra shows only where its data is complete; no pending rows anywhere on /eval',async({page,request})=>{
  test.setTimeout(120000);
  const cq=(await (await request.get('/api/v02/eval-summary',{timeout:90000})).json()).cost_quality, n=cq.nemotron;
  await page.setViewportSize({width:1920,height:1080});
  await page.goto('/eval',{waitUntil:'domcontentloaded'});
  await expect(page.locator('#sum-status')).toHaveText('live',{timeout:60000});
  const NM='Nemotron 3 Ultra 550B-A55B';
  await expect(page.locator('#s1-chart .sbar',{hasText:NM})).toHaveCount(n.stage1.status==='done'?1:0);
  await expect(page.locator('#s3-chart .sbar',{hasText:NM})).toHaveCount(n.state.status==='done'?3:0);
  expect(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1)).toBe(true);
  for(const u of ['/eval','/eval?schema=lean','/eval?schema=prose-v1','/eval?schema=prose-v2']){
    await page.goto(u,{waitUntil:'networkidle'});
    await page.waitForTimeout(1500);
    expect(await page.locator('main').last().innerText(),u).not.toMatch(/pending/i);
  }
  if(n.state.status==='done'){await page.goto('/eval?schema=lean');await expect(page.locator('#headline-table')).toContainText(NM,{timeout:60000});}
});
