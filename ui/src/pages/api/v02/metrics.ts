import type { APIRoute } from 'astro';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import { ROOT, headers, dataDir, jsonFile } from '../../../lib/v02-store';
export const prerender=false;
const run=promisify(execFile);
export const GET:APIRoute=async({url})=>{
 try{const selected=path.basename(dataDir(url.searchParams.get('dataset')));const {stdout}=await run('python3',[path.join(ROOT,'scripts/v02/evaluate.py'),'--stdout'],{cwd:ROOT,env:{...process.env,CCT_V02_DATASET:selected},timeout:15000,maxBuffer:2_000_000});return new Response(JSON.stringify({...JSON.parse(stdout),dataset:selected,manifest:jsonFile('manifest.json',{},selected),run_status:jsonFile('status.json',{},selected),reference_kind:'human_review_audit',evaluation_mode:'shared_prior_context'}),{headers});}
 catch{return new Response(JSON.stringify({error:'Evaluation is unavailable. Retry shortly.'}),{status:500,headers});}
};
