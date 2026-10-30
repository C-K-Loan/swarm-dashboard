#!/usr/bin/env python3
"""Stage 1 per-message labels on the 1000-message Opus gold set via paid OpenRouter, pinned provider (CKL 2026-10-30: Qwen3-235B fp8).

Same prompt, temperature, max_tokens, parser and output layout as scripts/classify_bundles.py (only the transport changes):
provider pinned with no fallback and a required quantization; every response must come from that provider; spend is
recorded from OpenRouter's billed usage.cost on the shared ledger and the worst case is reserved before each call.
Output: data/eval/ref_<slug>/<bundle>.jsonl and data/eval/ref_<slug>.jsonl (aligned to gold_v2.jsonl); a response from any other provider stops the run.
"""
import argparse,json,os,sys,threading,types,urllib.error,urllib.request
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent));sys.path.insert(0,str(HERE))
import classify_bundles as cb
import local_qwen_day1 as lq

ROOT=HERE.parents[1]
cb.EVAL=ROOT/'data/eval';cb.BUNDLES=cb.EVAL/'bundles'  # this checkout (bundles, gold and prompt verified identical to nebius-hack-dev)

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--model',required=True);ap.add_argument('--slug',required=True)
 ap.add_argument('--provider',required=True);ap.add_argument('--quant',required=True)
 ap.add_argument('--api-key-env',default='OPENROUTER_API_KEY')
 ap.add_argument('--budget-usd',type=float,required=True);ap.add_argument('--spend-ledger',required=True)
 ap.add_argument('--price-in',type=float,required=True);ap.add_argument('--price-out',type=float,required=True)
 ap.add_argument('--workers',type=int,default=8)
 a=ap.parse_args()
 key=os.environ[a.api_key_env];stop={'why':None};lock=threading.Lock()
 led=types.SimpleNamespace(budget_usd=a.budget_usd,spend_ledger=a.spend_ledger,price_in=a.price_in,price_out=a.price_out,model=a.model)

 def call(model,prompt,timeout=90):
  if stop['why']:return {'error':'stopped: '+stop['why']}
  try:lq.budget_reserve(led,len(prompt)/2.5,180)
  except lq.BudgetStop as e:stop['why']='budget: '+str(e);return {'error':stop['why']}
  body={'model':model,'messages':[{'role':'user','content':prompt}],'max_tokens':180,'temperature':0.0,'usage':{'include':True},
   'provider':{'only':[a.provider],'order':[a.provider],'allow_fallbacks':False,'quantizations':[a.quant]}}
  req=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
  try:
   with urllib.request.urlopen(req,timeout=timeout) as r:resp=json.loads(r.read())
  except urllib.error.HTTPError as e:return {'error':f'HTTP {e.code}: {e.read().decode()[:200]}'}
  except Exception as e:return {'error':str(e)}
  lq.budget_record(led,resp.get('usage'))
  if (resp.get('provider') or '').lower().replace(' ','')!=a.provider.lower():
   stop['why']='served by unexpected provider '+str(resp.get('provider'));return {'error':stop['why']}
  return resp
 cb._call=call

 order=cb.gold_bundle_order();state={'in':0,'out':0,'cost':0.0}
 for i,b in enumerate(order,1):
  out=cb.EVAL/f'ref_{a.slug}'/f'{b}.jsonl'
  if out.exists():continue
  rows=cb.classify_bundle(cb.BUNDLES/f'{b}.txt',a.model,a.slug,out,a.budget_usd,state,lock,a.workers)
  errs=sum(1 for r in rows if r.get('_api_error') or r.get('_parse_error'))
  print(json.dumps({'bundle':b,'n':i,'of':len(order),'rows':len(rows),'errors':errs,'ledger_usd':json.loads(Path(a.spend_ledger).read_text())['spent_usd']}),flush=True)
  if stop['why']:
   out.unlink();print(json.dumps({'state':'stopped','why':stop['why']}));return  # partial bundle discarded so a rerun redoes it
 n=cb.flatten(a.slug,order)
 print(json.dumps({'state':'done','rows':n,'provider':a.provider,'quantization':a.quant,'ledger_usd':json.loads(Path(a.spend_ledger).read_text())['spent_usd']}))

if __name__=='__main__':main()
