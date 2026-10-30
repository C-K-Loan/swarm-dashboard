import {test,expect} from '@playwright/test';
test('active first-day dataset exposes full dates, preserved inputs and pending coverage',async({page})=>{
 const data=await (await page.request.get('/api/v02/dataset')).json();
 expect(data.manifest.dataset_id).toBe('gamejam-first-24h-v02');expect(data.turns).toHaveLength(1902);
 expect(data.status.completed).toBeGreaterThanOrEqual(43);
 await page.goto('/review?turn=turn-0043',{waitUntil:'domcontentloaded'});
 await expect(page.locator('#generated')).toContainText('/ 1,902');
 await expect(page.locator('#window')).toContainText('2026-10-10');await expect(page.locator('#window')).toContainText('2026-10-11');
 await expect(page.locator('#state-turn-label')).toContainText('turn-0043');
 const detail=await (await page.request.get('/api/v02/dataset?turn=turn-0043')).json();
 expect(JSON.parse((await page.locator('#llm-state-json').textContent())!)).toEqual(detail.turn.llm_input);
 await page.locator('[data-turn="turn-1902"]').click();
 await expect(page.locator('#state-turn-label')).toContainText('turn-1902');
 if(!data.turns.at(-1).has_prediction)await expect(page.locator('#overall-state')).toContainText('has not been processed');
});
