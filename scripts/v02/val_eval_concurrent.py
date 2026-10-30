#!/usr/bin/env python3
"""Concurrent teacher-forced eval on the held-out val rows of a multi-teacher export (CKL 2026-10-29: base vs LoRA, bf16).

Each val row is sent exactly as exported (system + user = what the LoRA was trained to see); the teacher's graph before the
turn is replayed from its ledger only to validate and decode the answer with the same strict validator and alias context.
All rows are submitted at once so the server batches them (cpu-offload: one weight-streaming pass serves the whole batch).
Writes <dataset>/teacher-forced.jsonl in the score_teacher_forced.py format.
"""
import argparse,concurrent.futures as cf,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import local_qwen_day1 as lq
q=lq.q

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--val',required=True);ap.add_argument('--dataset',required=True);ap.add_argument('--model',required=True)
 ap.add_argument('--base-url',required=True);ap.add_argument('--api-key-env',default='')
 ap.add_argument('--max-corrections',type=int,default=1);ap.add_argument('--max-tokens',type=int,default=1280)
 ap.add_argument('--workers',type=int,default=131);ap.add_argument('--note',default='')
 ap.add_argument('--openrouter-free',action='store_true',help='$0 OpenRouter free route: openrouter_free.py request shape + zero-cost audit; stops on 429 or any cost')
 ap.add_argument('--min-interval',type=float,default=0,help='seconds between request starts (free-tier pacing)')
 ap.add_argument('--max-requests',type=int,default=0,help='hard cap on model requests this run (0 = no cap)')
 ap.add_argument('--dry-run',action='store_true',help='build and check prompts only; no network')
 ap.add_argument('--or-provider',default='',help='paid OpenRouter: pin this provider slug (only+order, no fallback); every response must come from it')
 ap.add_argument('--or-quant',default='',help='paid OpenRouter: required quantization (e.g. bf16, fp8); sent as provider.quantizations')
 ap.add_argument('--budget-usd',type=float,default=0);ap.add_argument('--spend-ledger',default='')
 ap.add_argument('--price-in',type=float,default=0);ap.add_argument('--price-out',type=float,default=0)
 a=ap.parse_args()
 if a.api_key_env:lq.API_KEY=os.environ[a.api_key_env]
 if a.or_provider:
  if not (a.or_quant and a.budget_usd and a.spend_ledger and a.price_in>0 and a.price_out>0):ap.error('--or-provider needs --or-quant, --budget-usd, --spend-ledger and both prices')
  lq.EXTRA_BODY={'provider':{'only':[a.or_provider],'order':[a.or_provider],'allow_fallbacks':False,'quantizations':[a.or_quant]},'usage':{'include':True}}
 os.umask(0o077);os.environ['CCT_V02_DATASET']=a.dataset
 import pipeline as p,claude_haiku_day1 as h
 vrows=[json.loads(x) for x in Path(a.val).read_text().splitlines() if x.strip()]
 teachers={r['provenance']['dataset'] for r in vrows}
 if len(teachers)!=1:raise SystemExit(f'expected one teacher, got {teachers}')
 teacher=teachers.pop()
 if not (p.DATA/'manifest.json').exists():q.prepare(p,q.ROOT/'data/v02/day1')
 trows=[json.loads(x) for x in (q.ROOT/'data/v02'/teacher/'predictions.jsonl').read_text().splitlines() if x.strip()]
 turns=p.read('turns.json');want={r['turn_id']:r for r in vrows}
 if [r['turn_id'] for r in trows]!=[t['id'] for t in turns][:len(trows)]:raise q.GuardError('Teacher ledger order does not match source turns')
 m=json.loads((p.DATA/'manifest.json').read_text())
 root=next((t for t in turns if t['id']==m['root_turn_id']),None)
 jobs=[];graph=p.initial();mismatch=[]
 for i,turn in enumerate(turns[:len(trows)]):
  if turn['id'] in want:
   pdesc='\n'.join(x['text'] for x in root['messages']) if root and root['ordinal']<=turn['ordinal'] else 'Gamejam project; detailed human brief not yet received.'
   msgs,actx,lean=lq.lean_messages(h,p,turn,graph,pdesc)
   vm=want[turn['id']]['messages']
   if [x['content'] for x in msgs]!=[x['content'] for x in vm[:2]]:mismatch.append(turn['id'])
   jobs.append((turn,graph,actx,lean,vm[:2],trows[i]))
  graph,_=p.apply(graph,trows[i]['patch'],turn)
 m.update(eval_mode='teacher_forced_val_concurrent',val_file=a.val,teacher_dataset=teacher,model=a.model,renderer='v0.3-lean',thinking=False,
  max_corrections=a.max_corrections,max_tokens=a.max_tokens,serving=a.note,prompt_rebuild_mismatches=mismatch,eval_turn_ids=[j[0]['id'] for j in jobs])
 p.atomic(p.DATA/'manifest.json',m)
 print(json.dumps({'rows':len(jobs),'prompt_rebuild_mismatches':len(mismatch)}),flush=True)
 if a.dry_run:print(json.dumps({'dry_run':True,'first_turn':jobs[0][0]['id'] if jobs else None,'prompt_chars':len(jobs[0][4][0]['content'])+len(jobs[0][4][1]['content']) if jobs else 0}));return
 import threading
 free=None;gate=threading.Lock();state={'sent':0,'last':0.0,'stop':None}
 if a.openrouter_free:
  import openrouter_free as free
  if a.model!=free.MODEL:raise SystemExit('--openrouter-free only serves '+free.MODEL)
  key=free.DEFAULT_KEY.read_text().strip()  # read from the free lane's key file; never printed or put in argv
 def call(msgs,temperature):
  """One model request, paced and capped; on the free route every response must audit at $0."""
  with gate:
   if state['stop']:raise RuntimeError('stopped: '+state['stop'])
   if a.max_requests and state['sent']>=a.max_requests:state['stop']='max-requests reached';raise RuntimeError(state['stop'])
   wait=state['last']+a.min_interval-time.time()
   if wait>0:time.sleep(wait)
   state['last']=time.time();state['sent']+=1
  if a.or_provider:  # paid, pinned: worst case reserved on the shared ledger, billed cost recorded from the response
   try:lq.budget_reserve(a,sum(len(m['content']) for m in msgs)/2.5,a.max_tokens)
   except lq.BudgetStop as e:state['stop']='budget: '+str(e);raise
   resp=lq.chat(a.base_url,a.model,msgs,temperature,a.max_tokens,False);resp['_usd']=lq.budget_record(a,resp.get('usage'))
   if (resp.get('provider') or '').lower().replace(' ','')!=a.or_provider.lower():state['stop']='served by unexpected provider '+str(resp.get('provider'));raise RuntimeError(state['stop'])
   return resp
  if not free:return lq.chat(a.base_url,a.model,msgs,temperature,a.max_tokens,False)
  body={'model':free.MODEL,'messages':msgs,'temperature':temperature,'max_tokens':a.max_tokens,'reasoning':{'enabled':False},
   'provider':{'only':[free.PROVIDER],'allow_fallbacks':False,'data_collection':'deny','max_price':dict(free.ZERO_PRICES)}}
  try:resp=free.api('/chat/completions',key,body);free.audit_response(resp);return resp
  except free.RateLimited as e:state['stop']='rate limited';raise
  except free.GuardError as e:state['stop']='cost/provider guard: '+str(e);raise
 out=p.DATA/'teacher-forced.jsonl';done={json.loads(x)['turn_id'] for x in out.read_text().splitlines()} if out.exists() else set()

 def one(job):
  turn,graph,actx,lean,msgs,trow=job
  rec={'turn_id':turn['id'],'model':a.model,'teacher_model':trow['model'],'teacher_patch':trow['patch'],'renderer':'v0.3-lean','attempts':[]}
  candidate=correction=None
  for attempt in range(1+a.max_corrections):
   if attempt:msgs,_,_=lq.lean_messages(h,p,turn,graph,lean_pdesc(turn),candidate,correction)
   t0=time.time()
   try:resp=call(msgs,0 if attempt==0 else 0.4)
   except Exception as e:
    if state['stop']:return None  # not scored: resume later (rows already written are skipped)
    rec['attempts'].append({'attempt':attempt+1,'valid':False,'error':f'request: {e}'[:600]});continue
   raw=(resp.get('choices') or [{}])[0].get('message',{}).get('content') or '';text,renamed=lq.rename_schema_key(lq.strip_think(raw))
   at={'attempt':attempt+1,'seconds':round(time.time()-t0,2),'usage':resp.get('usage'),'finish_reason':(resp.get('choices') or [{}])[0].get('finish_reason'),'raw':raw,**({'provider':resp.get('provider'),'quantization':a.or_quant,'usd':round(resp.get('_usd',0),6)} if a.or_provider else {})}
   try:
    patch,repairs=lq.validate_lean(h,p,text,turn,graph,actx);at.update(valid=True,repairs=repairs);rec.update(patch=patch,valid=True);rec['attempts'].append(at);break
   except (ValueError,KeyError,TypeError,IndexError,p.jsonschema.ValidationError) as e:
    at.update(valid=False,error=str(e)[:600]);rec['attempts'].append(at);candidate=text;correction=actx.encode_text(h.feedback(p,text,turn,graph,e))
  rec.setdefault('valid',False);return rec

 def lean_pdesc(turn):return '\n'.join(x['text'] for x in root['messages']) if root and root['ordinal']<=turn['ordinal'] else 'Gamejam project; detailed human brief not yet received.'

 todo=[j for j in jobs if j[0]['id'] not in done];t0=time.time();n=0
 with cf.ThreadPoolExecutor(a.workers) as ex,out.open('a') as fh:
  for f in cf.as_completed([ex.submit(one,j) for j in todo]):
   r=f.result()
   if r is None:continue
   fh.write(json.dumps(r,ensure_ascii=False)+'\n');fh.flush();n+=1
   print(json.dumps({'tf':r['turn_id'],'valid':r['valid'],'calls':len(r['attempts']),'n':n,'of':len(todo),'elapsed_s':round(time.time()-t0)}),flush=True)
 print(json.dumps({'state':'stopped: '+state['stop'] if state['stop'] else 'done','rows':n,'requests':state['sent'],'elapsed_s':round(time.time()-t0)}))

if __name__=='__main__':main()
