import type {APIRoute} from 'astro';
import fs from 'node:fs';
import path from 'node:path';
import {ROOT,headers} from '../../../lib/v02-store';
export const prerender=false;
export const GET:APIRoute=()=>{
  const report=path.join(ROOT,'data/v02/haiku45-matched/report.json');
  if(!fs.existsSync(report))return new Response(JSON.stringify({state:'not_started'}),{headers});
  try{const data=JSON.parse(fs.readFileSync(report,'utf8'));const comparison=path.join(ROOT,'data/v02/haiku45-matched/matched-comparison.json');if(fs.existsSync(comparison)){const c=JSON.parse(fs.readFileSync(comparison,'utf8'));if(c.total_haiku_calls===data.completed_calls)data.comparison=c;}return new Response(JSON.stringify(data),{headers});}
  catch{return new Response(JSON.stringify({error:'Benchmark status unavailable'}),{status:503,headers});}
};
