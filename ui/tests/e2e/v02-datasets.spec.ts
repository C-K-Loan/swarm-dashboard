import {test,expect} from '@playwright/test';

test('prediction run picker binds review source and mocked save to Nemotron',async({page})=>{
  let target='';
  await page.route('**/api/v02/review?*',route=>{target=route.request().url();return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({review:{turn_id:'turn-0001',status:'approved',revision:1}})});});
  await page.goto('/review?dataset=day1&turn=turn-0001',{waitUntil:'domcontentloaded'});
  await page.getByLabel('Prediction run',{exact:true}).selectOption('nemotron-day1');
  await expect(page).toHaveURL(/dataset=nemotron-day1/);
  await expect(page.locator('#teacher')).toContainText('nemotron');
  await expect(page.locator('#quota')).toContainText('$0');
  await expect(page.getByRole('button',{name:'Approve',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Approve',exact:true}).click();
  await expect(page.locator('#save-status')).toHaveText('Saved locally');
  expect(new URL(target).searchParams.get('dataset')).toBe('nemotron-day1');
  await expect(page.getByRole('link',{name:'Evaluation',exact:true})).toHaveAttribute('href',/dataset=nemotron-day1/);
});

test('eval and training identify the selected first-day run without first-hour copy',async({page,request})=>{
  const response=await request.get('/api/v02/metrics?dataset=day1');expect(response.ok()).toBeTruthy();
  const data=await response.json();expect(data.dataset).toBe('day1');expect(data.reference_kind).toBe('human_review_audit');expect(data.run_status.total).toBe(1902);
  await page.goto('/eval-review?dataset=day1',{waitUntil:'domcontentloaded'});
  await expect(page.locator('#run-window')).toContainText('day1');
  await page.goto('/training?dataset=day1',{waitUntil:'domcontentloaded'});
  await expect(page.getByLabel('Prediction run',{exact:true})).toHaveValue('day1');
  await expect(page.locator('main')).toContainText('1902');
  await expect(page.locator('main')).not.toContainText('First Gamejam hour');
  await expect(page.locator('main')).toContainText('data/v02/day1/training/');
});
