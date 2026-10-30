import type { APIRoute } from 'astro';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { ROOT, headers, dataDir } from '../../../lib/v02-store';
export const prerender = false;
export const POST: APIRoute = async ({request,url}) => {
  if (request.headers.get('origin') !== new URL(request.url).origin)
    return new Response(JSON.stringify({error:'Review requests must originate from this dashboard.'}),{status:403,headers});
  let selected:string;
  try{selected=path.basename(dataDir(url.searchParams.get('dataset')));}catch{return new Response(JSON.stringify({error:'Invalid dataset'}),{status:400,headers});}
  const text = await request.text();
  if (Buffer.byteLength(text)>256_000) return new Response(JSON.stringify({error:'Review exceeds the size limit.'}),{status:413,headers});
  try { JSON.parse(text); } catch { return new Response(JSON.stringify({error:'Invalid review JSON.'}),{status:400,headers}); }
  return await new Promise<Response>(resolve => {
    const child=spawn('python3',[path.join(ROOT,'scripts/v02/pipeline.py'),'review'],{cwd:ROOT,env:{...process.env,CCT_V02_DATASET:selected},stdio:['pipe','pipe','pipe']});
    let output='',error=''; let settled=false;
    const finish=(body:any,status:number)=>{if(settled)return;settled=true;clearTimeout(timer);resolve(new Response(JSON.stringify(body),{status,headers}));};
    const timer=setTimeout(()=>{child.kill();finish({error:'Review validation timed out. Your edit is preserved.'},504);},20_000);
    child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>error+=chunk);
    child.on('error',()=>finish({error:'Review validator unavailable.'},503));
    child.on('close',code=>{
      if(code!==0) return finish({error:error.trim().split('\n').slice(-1)[0] || 'Review validation failed.'},400);
      try {finish({review:JSON.parse(output)},200);} catch {finish({error:'Invalid validator response.'},500);}
    });
    child.stdin.end(text);
  });
};
