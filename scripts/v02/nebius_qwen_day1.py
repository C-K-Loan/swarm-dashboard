#!/usr/bin/env python3
"""Resumable Qwen3-30B-A3B Day1 labeler on Nebius Token Factory under a hard USD cap.

Independent causal rollout from an empty graph. Same prompt rules, strict validator
and deterministic repairs as the Haiku runner. Auto top-up is ON at the provider, so
the persisted spend ledger here is the only guard: a call that could cross the cap is
never sent. The API key is read from the env file and never printed or put in argv.
"""
import argparse,fcntl,hashlib,json,os,re,shutil,sys,time,urllib.error,urllib.request
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).resolve().parent))
MODEL='Qwen/Qwen3-30B-A3B-Instruct-2507'
ENGINE='nebius-token-factory'
API='https://api.tokenfactory.nebius.com/v1'
KEY_FILE=Path('.env')
PRICE={'prompt':0.10/1e6,'completion':0.30/1e6}  # USD per token, verified against /models at start
MAX_TOKENS=8192
CHARS_PER_TOKEN=2.5  # conservative: overestimates prompt tokens for the worst-case check
PROMPT_CONTRACT_VERSION='qwen-current-turn-evidence-v1+json-schema'
CONTEXT_LAYOUT={'name':'lean-G','full_tasks':15,'relationships':25,'recent_turns':1,
 'note':'Differs from Luna/Haiku pipeline.context: no evidence arrays in context tasks/relationships, actor roster as [id,name], full detail for 15 open tasks (actor-linked first, then most recently updated) plus [id,title,status] index of other open tasks, done/cancelled tasks omitted, 25 newest relevant relationships, 1 recent turn. Validator and graph unchanged.'}

class GuardError(RuntimeError):pass
class Transient(RuntimeError):pass

def now():return datetime.now(timezone.utc).isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def api_key():
 for line in KEY_FILE.read_text().splitlines():
  if line.startswith('NEBIUS_TOKEN_FACTORY_KEY='):return line.split('=',1)[1].strip().strip('"\'')
 raise GuardError('NEBIUS_TOKEN_FACTORY_KEY missing from env file')

def request(path,key,payload=None,timeout=600):
 req=urllib.request.Request(API+path,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},data=json.dumps(payload).encode() if payload is not None else None)
 try:
  with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)
 except urllib.error.HTTPError as e:
  # Never echo headers or the prompt.
  if e.code in (429,500,502,503,504):raise Transient('HTTP '+str(e.code)) from None
  raise GuardError('HTTP '+str(e.code)+'; no automatic retry') from None
 except (urllib.error.URLError,TimeoutError) as e:raise Transient('network: '+type(e).__name__) from None

def verify_price(key):
 models=request('/models?verbose=true',key)['data']
 m=next((x for x in models if x['id']==MODEL),None)
 if not m:raise GuardError('Pinned model unavailable: '+MODEL)
 pr=m.get('pricing',{})
 got={'prompt':float(pr.get('prompt',-1)),'completion':float(pr.get('completion',-1))}
 for k in PRICE:
  if abs(got[k]-PRICE[k])>1e-12:raise GuardError('Price changed for '+k+': '+str(got[k])+' per token; update PRICE after CKL OK')
 return {'model':MODEL,'pricing':got,'context_length':m.get('context_length'),'checked_at':now()}

# ---- budget ledger: persisted across invocations, the only spend guard ----
def ledger_path(p):return p.DATA/'spend.json'
def spend(p):
 f=ledger_path(p)
 return json.loads(f.read_text()) if f.exists() else {'spent_usd':0.0,'calls':0,'prompt_tokens':0,'completion_tokens':0}
def cost(usage):return usage.get('prompt_tokens',0)*PRICE['prompt']+usage.get('completion_tokens',0)*PRICE['completion']
def worst_case(messages):
 chars=sum(len(m['content']) for m in messages)
 return chars/CHARS_PER_TOKEN*PRICE['prompt']+MAX_TOKENS*PRICE['completion']
def budget_allows(spent,messages,cap):
 """True only if even the worst case of the next call keeps total spend within the cap."""
 return spent+worst_case(messages)<=cap
def record_spend(p,usage):
 s=spend(p);s['spent_usd']=round(s['spent_usd']+cost(usage),8);s['calls']+=1
 s['prompt_tokens']+=usage.get('prompt_tokens',0);s['completion_tokens']+=usage.get('completion_tokens',0);s['updated_at']=now()
 p.atomic(ledger_path(p),s);return s

def strip_evidence(x):
 if isinstance(x,dict):return {k:strip_evidence(v) for k,v in x.items() if k!='evidence'}
 if isinstance(x,list):return [strip_evidence(v) for v in x]
 return x

def lean_context(c,K=CONTEXT_LAYOUT['full_tasks'],R=CONTEXT_LAYOUT['relationships']):
 """Layout G: bounded context so cost stays flat as the graph grows (see cost report)."""
 c=json.loads(json.dumps(c));aid=c['actor_id_and_current_state'].get('id')
 open_=[t for t in c['relevant_existing_tasks_with_ids'] if t.get('status') not in ('done','cancelled')]
 rels=strip_evidence(c['relevant_actors_and_relationships']['relationships'])
 mine={r.get('task_ref') or r['to_ref'] for r in rels if aid in (r['from_ref'],r['to_ref'])}
 open_.sort(key=lambda t:t.get('updated_at') or 0,reverse=True)
 full=strip_evidence(([t for t in open_ if t['id'] in mine]+[t for t in open_ if t['id'] not in mine])[:K]);ids={t['id'] for t in full}
 c['relevant_existing_tasks_with_ids']=full
 c['other_open_tasks_index_id_title_status']=[[t['id'],t.get('title'),t.get('status')] for t in open_ if t['id'] not in ids]
 keep=[r for r in rels if aid in (r['from_ref'],r['to_ref']) or r['to_ref'] in ids or r['from_ref'] in ids or r.get('task_ref') in ids]
 c['relevant_actors_and_relationships']={'actors':[[a.get('id'),a.get('name') or a.get('title')] for a in c['relevant_actors_and_relationships']['actors']],
  'relationships':sorted(keep,key=lambda r:r.get('ts') or 0)[-R:]}
 c['recent_relevant_turns']=c['recent_relevant_turns'][-CONTEXT_LAYOUT['recent_turns']:]
 return c

def prepare(p,source):
 d=p.DATA;d.mkdir(parents=True,exist_ok=True,mode=0o700)
 frozen={'turns.json':source/'turns.json','actors.json':source/'actors.json','extract-turn.txt':ROOT/'prompts/v02/extract-turn.txt','turn-patch.schema.json':ROOT/'schemas/v02/turn-patch.schema.json'}
 for name,origin in frozen.items():
  t=d/name
  if t.exists():
   if sha(t)!=sha(origin):raise GuardError('Frozen source/schema/prompt drift: '+name)
  else:shutil.copyfile(origin,t)
 mf=d/'manifest.json'
 if not mf.exists():
  v=json.loads((source/'manifest.json').read_text())
  v.update(dataset_id=d.name,teacher_model=MODEL,teacher_engine=ENGINE,label_status='machine_draft',comparison_source=source.name,comparison_mode='independent causal rollout from empty task graph',source_sha256=sha(frozen['turns.json']),prompt_contract_version=PROMPT_CONTRACT_VERSION,context_layout=CONTEXT_LAYOUT)
  p.atomic(mf,v)
 m=json.loads(mf.read_text())
 if m.get('teacher_model')!=MODEL:raise GuardError('Existing dataset has a different teacher')
 if m.get('context_layout')!=CONTEXT_LAYOUT:
  if p.ledger():raise GuardError('Context layout drift; use a separate dataset')
  m['context_layout']=CONTEXT_LAYOUT;p.atomic(mf,m)

QWEN_RULES='''
Qwen-specific reminders:
- Research, fetch, probe or analysis agents use actor role "other" (never "researcher", "research", "tester"); testers use "reviewer".
- Every evidence quote is at most 300 characters: quote the shortest exact clause that supports the change, never a whole message.
'''
MAX_QUOTE=300

def split_long_quotes(patch,turn):
 """Split an over-long but exact quote into consecutive exact pieces <=300 chars at
 sentence/line ends. No words change; each piece is re-checked against the same message."""
 sources={m['id']:m['text'] for m in turn['messages']};repairs=[]
 groups=[patch.get('actor_evidence',[])]+[o.get('evidence',[]) for k in ('tasks','decisions','relationships') for o in patch.get(k,[]) if isinstance(o,dict)]
 for g in groups:
  if not isinstance(g,list):continue
  for e in list(g):
   q=e.get('quote') if isinstance(e,dict) else None;src=sources.get(e.get('message_id')) if isinstance(e,dict) else None
   if not isinstance(q,str) or len(q)<=MAX_QUOTE or not src or q not in src:continue
   pieces=[];rest=q
   while len(rest)>MAX_QUOTE:
    cut=max(rest.rfind(x,0,MAX_QUOTE) for x in ('. ','\n','; ','! ','? '))
    cut=cut+1 if cut>0 else (rest.rfind(' ',0,MAX_QUOTE) if rest.rfind(' ',0,MAX_QUOTE)>0 else MAX_QUOTE)
    pieces.append(rest[:cut].strip());rest=rest[cut:]
   pieces.append(rest.strip());pieces=[x for x in pieces if x]
   if not all(len(x)<=MAX_QUOTE and x in src for x in pieces):continue
   i=g.index(e);g[i:i+1]=[{**e,'quote':x} for x in pieces]
   repairs.append({'message_id':e['message_id'],'original_length':len(q),'pieces':len(pieces),'method':'split over-long exact quote at sentence/line ends; no words changed'})
 return repairs

def schema_without_max_length(schema):
 """Decoder-side schema: drop maxLength so quotes are not force-cut mid-word (validator still enforces 300)."""
 if isinstance(schema,dict):return {k:schema_without_max_length(v) for k,v in schema.items() if k!='maxLength'}
 if isinstance(schema,list):return [schema_without_max_length(v) for v in schema]
 return schema

def parse(h,text):
 """Strict parse; on failure retry once with over-escaped quotes (\\\\" -> \\") undone.
 Quotes are still checked verbatim against the source by the validator."""
 try:return h.parse_patch(text),[]
 except json.JSONDecodeError:
  fixed=text.replace('\\\\"','\\"')
  if fixed==text:raise
  return h.parse_patch(fixed),[{'method':'undo double-escaped quote marks in JSON (\\\\" -> \\"); quotes re-validated'}]

def trim_to_exact_prefix(patch,turn,tail=20):
 """Constrained decoding can cut a quote at the schema limit and garble its last chars.
 If all but the last <=20 chars are an exact span of the cited message, keep that exact
 prefix up to the last space. Shorter evidence, same words; validator re-checks."""
 sources={m['id']:m['text'] for m in turn['messages']};repairs=[]
 groups=[patch.get('actor_evidence',[])]+[o.get('evidence',[]) for k in ('tasks','decisions','relationships') for o in patch.get(k,[]) if isinstance(o,dict)]
 for g in groups:
  for e in g if isinstance(g,list) else []:
   q=e.get('quote') if isinstance(e,dict) else None;src=sources.get(e.get('message_id')) if isinstance(e,dict) else None
   if not isinstance(q,str) or not src or q in src or len(q)<60:continue
   k=len(q)
   while k>len(q)-tail and q[:k] not in src:k-=1
   if q[:k] not in src or k<=len(q)-tail:continue
   cut=q[:k].rstrip();cut=cut[:cut.rfind(' ')] if ' ' in cut[-15:] else cut
   if len(cut)<40 or cut not in src:continue
   repairs.append({'message_id':e['message_id'],'original_quote':q,'restored_quote':cut,'method':'trimmed garbled tail to longest exact prefix (constrained-decoding cutoff)'})
   e['quote']=cut
 return repairs

def validate(h,p,text,turn,graph):
 patch,repairs0=parse(h,text)
 try:fixed,repairs=h.deterministic_repairs(patch,turn);repairs=repairs0+repairs+split_long_quotes(fixed,turn)+trim_to_exact_prefix(fixed,turn)
 except (KeyError,TypeError,AttributeError):fixed,repairs=patch,[]
 p.check(fixed,turn,graph)
 return fixed,repairs

def messages_for(h,p,context,candidate=None,correction=None):
 return [{'role':'system','content':h.system_prompt(p)+QWEN_RULES},{'role':'user','content':h.user_prompt(context,candidate,correction)}]

def status(p,state,run,**fields):
 rows=p.ledger();s=spend(p)
 r={'state':state,'completed':len(rows),'total':len(p.read('turns.json')),'model':MODEL,'updated_at':now(),'usd_cap':run['cap'],'spent_usd':round(s['spent_usd'],4),
    'run':{'started_at':run['started_at'],'calls':run['calls'],'accepted':run['accepted'],'calls_per_accepted':round(run['calls']/run['accepted'],3) if run['accepted'] else None,'run_spent_usd':round(s['spent_usd']-run['spent_at_start'],4),'usd_per_accepted':round((s['spent_usd']-run['spent_at_start'])/run['accepted'],5) if run['accepted'] else None},**fields}
 p.atomic(p.DATA/'qwen-runner-status.json',r);p.atomic(p.DATA/'status.json',r)
 print(json.dumps({k:r[k] for k in ('state','completed','spent_usd')}|{'reason':fields.get('reason'),'run':r['run']}),flush=True)
 return r

def reuse_paid(h,p,previous,turn,graph,context):
 for job in reversed(previous):
  out=job/'output.txt'
  if not out.exists():continue
  try:patch,repairs=validate(h,p,out.read_text(),turn,graph)
  except (ValueError,KeyError,TypeError,IndexError,p.jsonschema.ValidationError):continue
  rec=json.loads((job/'receipt.json').read_text())
  return {'turn_id':turn['id'],'model':MODEL,'annotator_kind':'LLM','label_status':'machine_draft','generated_at':now(),'input':context,'patch':patch,'inference_provenance':{**rec,'attempts_for_turn':len(previous),'raw_output_file':str(out.relative_to(p.DATA)),'evidence_repairs':repairs,'revalidated_at':now()}},patch
 return None

def run(p,h,args):
 prepare(p,ROOT/'data/v02'/args.source)
 key=api_key();price=verify_price(key);p.atomic(p.DATA/'price-check.json',price)
 max_attempts=1+args.max_corrections;jobs=p.DATA/'nebius-jobs';jobs.mkdir(exist_ok=True)
 with (p.DATA/'.qwen-runner.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  rows=p.ledger();turns=p.read('turns.json');graph=p.initial()
  for turn,row in zip(turns,rows):
   if row['turn_id']!=turn['id'] or row['model']!=MODEL:raise GuardError('Unexpected ledger order or teacher')
   p.check(row['patch'],turn,graph);graph,_=p.apply(graph,row['patch'],turn)
  run={'started_at':now(),'calls':0,'accepted':0,'cap':args.usd_cap,'spent_at_start':spend(p)['spent_usd']};deadline=time.time()+args.max_minutes*60
  for turn in turns[len(rows):]:
   context=lean_context(p.context(turn,graph,rows))
   previous=sorted(x for x in jobs.glob(turn['id']+'-*') if (x/'receipt.json').exists())
   reused=reuse_paid(h,p,previous,turn,graph,context)
   if reused:
    row,patch=reused;h.append_row(p,rows,row);graph,_=p.apply(graph,patch,turn);run['accepted']+=1;p.publish(rows);continue
   if len(previous)>=max_attempts:return status(p,'stopped_validation',run,reason='Turn exhausted '+str(max_attempts)+' attempts: '+turn['id'])
   candidate=correction=None
   if previous and (previous[-1]/'validation-error.json').exists():
    candidate=(previous[-1]/'output.txt').read_text();correction=json.loads((previous[-1]/'validation-error.json').read_text())['report']
   # Identical rejected answers mean the correction loop is stuck: retry fresh, hotter, compact.
   fresh=len(previous)>=2 and all((x/'output.txt').exists() for x in previous[-2:]) and (previous[-1]/'output.txt').read_text()==(previous[-2]/'output.txt').read_text()
   if fresh:candidate=correction=None
   accepted=False
   for attempt in range(len(previous),max_attempts):
    if attempt>len(previous):fresh=False
    if (p.DATA/'STOP').exists():return status(p,'stopped',run,reason='Local STOP file exists')
    if run['accepted']>=args.max_accepted:return status(p,'checkpoint',run,reason='Accepted-turn checkpoint reached')
    if time.time()>=deadline:return status(p,'checkpoint',run,reason='Runtime cap reached')
    msgs=messages_for(h,p,context,candidate,correction)
    if fresh:msgs[1]['content']+='\n\nKeep the patch compact: at most 6 tasks, one-sentence summaries, short quotes. Output must be valid JSON.'
    if not budget_allows(spend(p)['spent_usd'],msgs,args.usd_cap):return status(p,'budget_stopped',run,reason='Next call could cross the USD cap')
    job=jobs/(turn['id']+'-'+str(time.time_ns()));job.mkdir(parents=True)
    (job/'prompt.json').write_text(json.dumps(msgs,ensure_ascii=False))
    # Constrained decoding: provider must emit JSON matching the turn-patch schema (structure only).
    payload={'response_format':{'type':'json_schema','json_schema':{'name':'turn_patch','schema':schema_without_max_length(p.SCHEMA),'strict':True}},'model':MODEL,'messages':msgs,'temperature':0 if attempt==0 else (0.7 if fresh else 0.4),'max_tokens':MAX_TOKENS}
    for backoff in (0,10,30,90,300):
     time.sleep(backoff)
     try:resp=request('/chat/completions',key,payload);break
     except Transient as e:p.atomic(job/'transient-error.json',{'at':now(),'error':str(e)});resp=None
    if resp is None:return status(p,'provider_unavailable',run,reason='Provider kept failing; resume later')
    usage=resp.get('usage') or {}
    if resp.get('model')!=MODEL or not usage.get('prompt_tokens'):raise GuardError('Unexpected model or missing usage; stop')
    s=record_spend(p,usage);run['calls']+=1
    text=(resp.get('choices') or [{}])[0].get('message',{}).get('content') or ''
    (job/'output.txt').write_text(text)
    rec={'turn_id':turn['id'],'attempt':attempt+1,'generated_at':now(),'model':MODEL,'engine':ENGINE,'usage':usage,'usd':round(cost(usage),8),'cumulative_usd':s['spent_usd'],'finish_reason':(resp.get('choices') or [{}])[0].get('finish_reason'),'job':str(job.relative_to(p.DATA)),'temperature':payload['temperature'],'prompt_contract_version':PROMPT_CONTRACT_VERSION,'context_layout':CONTEXT_LAYOUT['name'],'prompt_tokens':usage.get('prompt_tokens'),'runner_sha256':sha(__file__)}
    p.atomic(job/'receipt.json',rec)
    with (p.DATA/'nebius-usage.jsonl').open('a') as f:f.write(json.dumps(rec)+'\n')
    try:patch,repairs=validate(h,p,text,turn,graph)
    except (ValueError,KeyError,TypeError,IndexError,p.jsonschema.ValidationError) as error:
     candidate=text;correction=h.feedback(p,text,turn,graph,error)
     p.atomic(job/'validation-error.json',{'error':str(error)[:1600],'report':correction})
     print(json.dumps({'retry_validation':turn['id'],'attempt':attempt+1,'error':str(error)[:200]}),flush=True);continue
    row={'turn_id':turn['id'],'model':MODEL,'annotator_kind':'LLM','label_status':'machine_draft','generated_at':now(),'input':context,'patch':patch,'inference_provenance':{**rec,'attempts_for_turn':attempt+1,'raw_output_file':str((job/'output.txt').relative_to(p.DATA)),'evidence_repairs':repairs}}
    h.append_row(p,rows,row);graph,_=p.apply(graph,patch,turn);run['accepted']+=1;accepted=True
    p.publish(rows);print(json.dumps({'accepted':turn['id'],'attempt':attempt+1,'usd':rec['usd'],'total_usd':s['spent_usd']}),flush=True);break
   if not accepted:return status(p,'stopped_validation',run,reason='Turn failed after '+str(max_attempts)+' attempts: '+turn['id'])
  return status(p,'generated',run)

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--dataset',default='qwen30b-layoutg-day1');ap.add_argument('--source',default='day1')
 ap.add_argument('--usd-cap',type=float,required=True,help='hard cap on TOTAL spend recorded for this dataset')
 ap.add_argument('--max-accepted',type=int,default=20);ap.add_argument('--max-minutes',type=int,default=60)
 ap.add_argument('--max-corrections',type=int,default=3)
 ap.add_argument('--run',action='store_true')
 a=ap.parse_args()
 if not re.fullmatch(r'qwen[a-zA-Z0-9_-]+',a.dataset):ap.error('Dataset must start qwen')
 if not 0<a.usd_cap<=4.75:ap.error('--usd-cap must be in (0, 4.75] (CKL approved $5 total)')
 os.umask(0o077);os.environ['CCT_V02_DATASET']=a.dataset
 import pipeline as p,claude_haiku_day1 as h
 if not a.run:prepare(p,ROOT/'data/v02'/a.source);print(json.dumps({'prepared':a.dataset,'price':verify_price(api_key()),'spend':spend(p)}));return
 run(p,h,a)

if __name__=='__main__':main()
