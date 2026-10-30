import type { APIRoute } from 'astro';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import { ROOT, headers } from '../../../lib/v02-store';
export const prerender=false;
const run=promisify(execFile);
// The summary reads every run (~10 s), so serve the last one at once and recompute behind it; ?fresh=1 waits.
let cached:{at:number,body:string}|null=null;let pending:Promise<string>|null=null;
const compute=()=>pending??=run('python3',[path.join(ROOT,'scripts/v02/eval_summary.py')],{cwd:ROOT,timeout:60000,maxBuffer:20_000_000})
 .then(({stdout})=>{JSON.parse(stdout);cached={at:Date.now(),body:stdout};return stdout;}).finally(()=>{pending=null;});
export const GET:APIRoute=async({url})=>{
 try{
  if(cached&&url.searchParams.get('fresh')!=='1'){if(Date.now()-cached.at>30000)compute().catch(()=>{});return new Response(cached.body,{headers});}
  return new Response(await compute(),{headers});
 }catch{return new Response(JSON.stringify({error:'Evaluation summary is unavailable. Retry shortly.'}),{status:500,headers});}
};
