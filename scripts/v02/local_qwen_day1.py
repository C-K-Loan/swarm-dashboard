#!/usr/bin/env python3
"""Local $0 Day1 labeler against an OpenAI-compatible server on gpu-host (e.g. Qwen3.5-4B bf16).

Base-vs-LoRA baseline: same strict validator, repairs and bounded corrections as the
Token Factory runner, but layout G8k so prompt + output fit the 8192 training context.
Thinking stays on (trainer contract); <think>...</think> is stripped before parsing.
"""
import argparse,fcntl,json,os,re,sys,time,urllib.error,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import nebius_qwen_day1 as q

ROOT=q.ROOT
CTX=8192
PROMPT_BUDGET=6000   # leaves ~2k for thinking + patch inside the 8k training context
PROSE_CHARS=7500     # ~2.5k tokens of current prose; quotes are still checked against the full message
LAYOUT={'name':'lean-G8k','full_tasks':6,'relationships':10,'recent_turns':0,'task_index':30,'index_title_chars':40,'prose_chars':PROSE_CHARS,'ctx':CTX,
 'note':'G with 6 full tasks, 10 rels, actors limited to those in shown relationships + self + human, 30 most recent other open tasks with 40-char titles, no recent turn, prose clipped for display only. Comparable to a LoRA trained at 8192.'}

def lean_context_8k(c):
 PROSE=LAYOUT['prose_chars']
 c=q.lean_context(c,LAYOUT['full_tasks'],LAYOUT['relationships'])
 R=LAYOUT['relationships']
 rels=c['relevant_actors_and_relationships']['relationships']=c['relevant_actors_and_relationships']['relationships'][-R:] if R else []  # [-0:] would keep all
 aid=c['actor_id_and_current_state'].get('id')
 used={aid,'human'}|{r['from_ref'] for r in rels}|{r['to_ref'] for r in rels}
 c['relevant_actors_and_relationships']['actors']=[a for a in c['relevant_actors_and_relationships']['actors'] if a[0] in used]
 c['other_open_tasks_index_id_title_status']=[[i,(t or '')[:LAYOUT['index_title_chars']],s] for i,t,s in c['other_open_tasks_index_id_title_status'][:LAYOUT['task_index']]]
 c['recent_relevant_turns']=[]
 out=[];total=0
 for m in c['current_turn_prose']:
  cap=PROSE if total<2*PROSE else 120  # bound total prose for turns with many messages
  t=m['text'][:cap];total+=len(t);out.append({'id':m['id'],'role':m.get('role'),'text':t+(' …[display truncated]' if len(m['text'])>cap else '')})  # drop source path/line/ts
 c['current_turn_prose']=out
 return c

TOKENIZER=Path.home()/'.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B/snapshots/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/tokenizer.json'
def token_counter():
 try:
  from tokenizers import Tokenizer
  tk=Tokenizer.from_file(str(TOKENIZER));return lambda s:len(tk.encode(s,add_special_tokens=False).ids)
 except Exception:return lambda s:int(len(s)/2.5)+1  # conservative fallback
COUNT=None
# Shrink steps tried in order until system+user fit PROMPT_BUDGET; the step index is recorded per turn.
SHRINK=[{},{'task_index':15},{'task_index':10,'relationships':6},{'task_index':5,'relationships':4,'full_tasks':4,'prose_chars':4000},{'task_index':0,'relationships':2,'full_tasks':2,'prose_chars':2500},{'task_index':0,'relationships':0,'full_tasks':1,'prose_chars':1200}]

def fit_context(h,p,c_full):
 global COUNT
 COUNT=COUNT or token_counter();saved=dict(LAYOUT);best=None
 try:
  for step,over in enumerate(SHRINK):
   LAYOUT.update(saved);LAYOUT.update(over)
   global PROSE_CHARS;PROSE_CHARS=LAYOUT['prose_chars']
   c=lean_context_8k(c_full);n=sum(COUNT(m['content']) for m in q.messages_for(h,p,c))
   best=(c,step,n)
   if n<=PROMPT_BUDGET:return best
  return best
 finally:LAYOUT.clear();LAYOUT.update(saved);PROSE_CHARS=saved['prose_chars']

def rename_schema_key(text):
 """Untrained Qwen3.5 writes "$schema":"0.2" for "schema_version":"0.2"; structural key fix only."""
 return re.sub(r'^(\s*\{\s*)"\$schema"(\s*:\s*"0\.2")',r'\1"schema_version"\2',text),('"$schema"' in text[:40])

def strip_think(text):
 return re.sub(r'<think>.*?</think>','',text or '',flags=re.S).strip()

API_KEY=None  # set from --api-key-env (paid endpoints only); never logged or put in argv
QUANT={}  # provider_name -> quantization from --endpoints-url
EXTRA_BODY={}  # --extra-body, e.g. OpenRouter provider routing (quantizations, allow_fallbacks) and reasoning off
def chat(base_url,model,messages,temperature,max_tokens,thinking=True,schema=None):
 body={'model':model,'messages':messages,'temperature':temperature,'max_tokens':max_tokens,'chat_template_kwargs':{'enable_thinking':thinking},**EXTRA_BODY}
 if schema:body['response_format']={'type':'json_schema','json_schema':{'name':'turn_patch','schema':schema,'strict':True}}
 headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+API_KEY} if API_KEY else {})}
 req=urllib.request.Request(base_url.rstrip('/')+'/chat/completions',headers=headers,data=json.dumps(body).encode())
 with urllib.request.urlopen(req,timeout=900) as r:return json.load(r)

def run(p,h,args):
 mf=p.DATA/'manifest.json'
 if not mf.exists():q.prepare(p,ROOT/'data/v02'/args.source)  # shared frozen-copy step, first run only
 m=json.loads(mf.read_text())
 m.update(thinking=not args.no_thinking,teacher_model=args.model,teacher_engine='local-openai-compatible',context_layout=LAYOUT,serving=args.serving_note,base_weights=args.weights_note)
 if p.ledger() and json.loads(mf.read_text()).get('teacher_model')!=args.model:raise q.GuardError('Existing dataset has a different teacher')
 p.atomic(mf,m)
 max_attempts=1+args.max_corrections;jobs=p.DATA/'local-jobs';jobs.mkdir(exist_ok=True)
 with (p.DATA/'.local-runner.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  rows=p.ledger();turns=p.read('turns.json');graph=p.initial()
  for turn,row in zip(turns,rows):p.check(row['patch'],turn,graph);graph,_=p.apply(graph,row['patch'],turn)
  started=time.time();accepted=calls=0
  for turn in turns[len(rows):]:
   if args.stop_at and len(rows)>=args.stop_at:print(json.dumps({'state':'stop_at_reached','completed':len(rows)}));return
   context,shrink_step,prompt_tokens=fit_context(h,p,p.context(turn,graph,rows))
   previous=sorted(x for x in jobs.glob(turn['id']+'-*') if (x/'receipt.json').exists())
   candidate=correction=None;ok=False
   for attempt in range(len(previous),max_attempts):
    if (p.DATA/'STOP').exists():print(json.dumps({'state':'stopped'}));return
    if accepted>=args.max_accepted:print(json.dumps({'state':'checkpoint','accepted':accepted,'calls':calls,'sec_per_turn':round((time.time()-started)/max(accepted,1),1)}));return
    msgs=q.messages_for(h,p,context,candidate,correction)
    job=jobs/(turn['id']+'-'+str(time.time_ns()));job.mkdir(parents=True);(job/'prompt.json').write_text(json.dumps(msgs,ensure_ascii=False))
    t0=time.time()
    room=CTX-prompt_tokens-64
    if room<512:print(json.dumps({'state':'context_overflow','turn':turn['id'],'prompt_tokens':prompt_tokens}));return
    try:resp=chat(args.base_url,args.model,msgs,0 if attempt==0 else 0.4,min(args.max_tokens,room),not args.no_thinking)
    except urllib.error.HTTPError as e:
     # 400 here usually means prompt+max_tokens exceeds the served context: record, do not retry blindly.
     p.atomic(job/'server-error.json',{'code':e.code,'body':e.read()[:2000].decode('utf8','replace')});print(json.dumps({'state':'server_error','turn':turn['id'],'code':e.code}));return
    calls+=1;raw=(resp.get('choices') or [{}])[0].get('message',{}).get('content') or '';text,renamed=rename_schema_key(strip_think(raw))
    (job/'output.txt').write_text(raw)
    rec={'turn_id':turn['id'],'attempt':attempt+1,'generated_at':q.now(),'model':args.model,'engine':'local','usage':resp.get('usage'),'seconds':round(time.time()-t0,2),'job':str(job.relative_to(p.DATA)),'thinking':not args.no_thinking,'context_layout':LAYOUT['name'],'shrink_step':shrink_step,'prompt_tokens_est':prompt_tokens,'runner_sha256':q.sha(__file__)}
    p.atomic(job/'receipt.json',rec)
    try:
     patch,repairs=q.validate(h,p,text,turn,graph)
     if renamed:repairs=[{'method':'renamed $schema key to schema_version (structural)'}]+repairs
    except (ValueError,KeyError,TypeError,IndexError,p.jsonschema.ValidationError) as error:
     candidate=text;correction=h.feedback(p,text,turn,graph,error);p.atomic(job/'validation-error.json',{'error':str(error)[:1600],'report':correction});continue
    row={'turn_id':turn['id'],'model':args.model,'annotator_kind':'LLM','label_status':'machine_draft','generated_at':q.now(),'input':context,'patch':patch,'inference_provenance':{**rec,'attempts_for_turn':attempt+1,'evidence_repairs':repairs}}
    h.append_row(p,rows,row);graph,_=p.apply(graph,patch,turn);accepted+=1;ok=True;p.publish(rows)
    print(json.dumps({'accepted':turn['id'],'attempt':attempt+1,'sec':rec['seconds']}),flush=True);break
   if not ok:
    if not args.record_failures:print(json.dumps({'state':'stopped_validation','turn':turn['id'],'accepted':accepted,'calls':calls}));return
    # Baseline mode: record the failure as an empty patch so the rollout continues; failure rate is a measured result.
    empty={'schema_version':'0.2','turn_id':turn['id'],'actor_patch':{},'actor_evidence':[],'tasks':[],'decisions':[],'relationships':[]}
    p.check(empty,turn,graph)
    attempts=sorted(x for x in jobs.glob(turn['id']+'-*') if (x/'receipt.json').exists())
    last=json.loads((attempts[-1]/'validation-error.json').read_text())['error'][:600] if attempts and (attempts[-1]/'validation-error.json').exists() else 'no candidate'
    row={'turn_id':turn['id'],'model':args.model,'annotator_kind':'LLM','label_status':'model_failed','generated_at':q.now(),'input':context,'patch':empty,'inference_provenance':{'attempts_for_turn':len(attempts),'failure':last,'jobs':[str(x.relative_to(p.DATA)) for x in attempts],'thinking':not args.no_thinking,'context_layout':LAYOUT['name']}}
    h.append_row(p,rows,row);p.publish(rows);print(json.dumps({'failed':turn['id'],'attempts':len(attempts)}),flush=True)
  print(json.dumps({'state':'generated','accepted':accepted,'calls':calls}))

def lean_messages(h,p,turn,graph,project_description,candidate=None,correction=None):
 """v0.3-lean prompt (house schema, CKL 2026-10-29). Shared by base eval, LoRA training export and LoRA eval:
 import this instead of rebuilding prompts. Returns (messages, alias_ctx, lean_input)."""
 import lean_context as L
 data,actx=L.build(turn,graph,project_description)
 prev=h.LEAN;h.LEAN=True  # identical system string to the Haiku --context lean runner
 try:system=h.system_prompt(p)
 finally:h.LEAN=prev
 msgs=[{'role':'system','content':system},{'role':'user','content':h.user_prompt(data,candidate,correction)}]
 return msgs,actx,data

def validate_lean(h,p,text,turn,graph,actx):
 """parse -> decode aliases -> recorded repairs -> strict validator (same order as the lean notes)."""
 import lean_context as L
 patch,repairs=q.parse(h,text)
 decoded=L.decode_patch(patch,actx)
 fixed,more=h.deterministic_repairs(decoded,turn)
 more+=q.split_long_quotes(fixed,turn)+q.trim_to_exact_prefix(fixed,turn)
 p.check(fixed,turn,graph)
 return fixed,repairs+more

def run_teacher_forced(p,h,args):
 """Base-vs-LoRA eval mode: each eval turn sees the TEACHER's graph after t-1 (not the model's own),
 rendered with lean-G8k; outputs go to teacher-forced.jsonl next to the teacher patch. No ledger writes."""
 if not (p.DATA/'manifest.json').exists():q.prepare(p,ROOT/'data/v02'/args.source)  # frozen turns/actors/prompt copies before any read
 teacher=ROOT/'data/v02'/args.teacher_forced
 trows=[json.loads(x) for x in (teacher/'predictions.jsonl').read_text().splitlines() if x.strip()]
 turns=p.read('turns.json');ids=[t['id'] for t in turns]
 if [r['turn_id'] for r in trows]!=ids[:len(trows)]:raise q.GuardError('Teacher ledger order does not match source turns')
 want=args.eval_turn_ids.split(',') if args.eval_turn_ids else []
 if args.eval_last_frac:
  n=len(trows);hold=max(1,int(n*args.eval_last_frac));want=[r['turn_id'] for r in trows[n-hold:]]
 out=p.DATA/'teacher-forced.jsonl';done={json.loads(x)['turn_id'] for x in out.read_text().splitlines()} if out.exists() else set()
 m=p.DATA/'manifest.json';man=json.loads(m.read_text())
 man.update(renderer='v0.3-lean',eval_mode='teacher_forced',teacher_dataset=args.teacher_forced,teacher_model=trows[0]['model'] if trows else None,eval_turn_ids=want,model=args.model,context_layout=LAYOUT,thinking=not args.no_thinking,serving=args.serving_note,base_weights=args.weights_note)
 p.atomic(m,man)
 graph=p.initial();k=0;jobs=p.DATA/'tf-jobs';jobs.mkdir(exist_ok=True)
 for i,turn in enumerate(turns[:len(trows)]):
  if turn['id'] in want and turn['id'] not in done:
   if (p.DATA/'STOP').exists():print(json.dumps({'state':'stopped'}));return
   import lean_context as L
   pdesc=p.context(turn,graph,trows[:i])['project_description']
   msgs,actx,lean=lean_messages(h,p,turn,graph,pdesc);COUNTER=COUNT or token_counter()
   ptok=sum(COUNTER(m['content']) for m in msgs)
   rec={'turn_id':turn['id'],'model':args.model,'teacher_model':trows[i]['model'],'teacher_patch':trows[i]['patch'],'renderer':'v0.3-lean',**L.provenance(),'prompt_tokens_est':ptok,'attempts':[]}
   candidate=correction=None
   for attempt in range(1+args.max_corrections):
    if attempt:msgs,actx,lean=lean_messages(h,p,turn,graph,pdesc,candidate,correction)
    t0=time.time()
    resp=chat(args.base_url,args.model,msgs,0 if attempt==0 else 0.4,max(256,min(args.max_tokens,CTX-ptok-64)),not args.no_thinking)
    raw=(resp.get('choices') or [{}])[0].get('message',{}).get('content') or '';text,renamed=rename_schema_key(strip_think(raw))
    a={'attempt':attempt+1,'seconds':round(time.time()-t0,2),'usage':resp.get('usage'),'raw':raw}
    try:
     patch,repairs=validate_lean(h,p,text,turn,graph,actx);a.update(valid=True,repairs=repairs+([{'method':'renamed $schema key'}] if renamed else []));rec.update(patch=patch,valid=True)
     rec['attempts'].append(a);break
    except (ValueError,KeyError,TypeError,IndexError,p.jsonschema.ValidationError) as e:
     a.update(valid=False,error=str(e)[:600]);rec['attempts'].append(a);candidate=text;correction=actx.encode_text(h.feedback(p,text,turn,graph,e))
   rec.setdefault('valid',False);rec['input']=lean
   with out.open('a') as fh:fh.write(json.dumps(rec,ensure_ascii=False)+'\n')
   k+=1;print(json.dumps({'tf':turn['id'],'valid':rec['valid'],'calls':len(rec['attempts'])}),flush=True)
  graph,_=p.apply(graph,trows[i]['patch'],turn)
 print(json.dumps({'state':'tf_done','evaluated':k}))

def quote_spans(text,cap=q.MAX_QUOTE,limit=600):
 """Sentence/line spans of one message, each an exact substring of at most cap chars (long ones cut at word boundaries)."""
 out=[]
 for piece in re.split(r'(?<=[.!?])\s+|\n+',text):
  piece=piece.strip()
  while len(piece)>cap:
   cut=piece.rfind(' ',0,cap);cut=cut if cut>cap//2 else cap
   out.append(piece[:cut].strip());piece=piece[cut:].strip()
  if piece:out.append(piece)
 seen=set();return [x for x in out if x and x in text and not (x in seen or seen.add(x))][:limit]

def lean_schema(p,turn,spans=False):
 """Decoder-side schema for one turn: validator schema minus maxLength, turn_id pinned, message_id limited to this turn's messages.
 spans=True also limits each quote to exact sentence spans of its message (copy fidelity forced by the decoder, recorded in the manifest).
 Refs stay free strings (aliases, human, new:*); decode_patch + p.check remain the authority."""
 s=q.schema_without_max_length(p.read('turn-patch.schema.json'))
 s.pop('$schema',None);s.pop('$id',None);s['properties']['turn_id']={'const':turn['id']}
 ids=[m['id'] for m in turn.get('messages',[])]
 per=[(m['id'],quote_spans(m.get('text') or '')) for m in turn.get('messages',[])] if spans else []
 per=[(i,x) for i,x in per if x]
 def pin(node):
  if isinstance(node,dict):
   if node.get('required')==['message_id','quote']:
    if per:
     node.clear();node['anyOf']=[{'type':'object','additionalProperties':False,'required':['message_id','quote'],'properties':{'message_id':{'const':i},'quote':{'enum':x}}} for i,x in per];return
    node['properties']['message_id']={'enum':ids}
   for v in node.values():pin(v)
  elif isinstance(node,list):
   for v in node:pin(v)
 pin(s);return s

ROTATE=[]  # --rotate-providers: each request pins ONE provider (only+order, fallbacks off); 429/5xx moves to the next
def chat_rotating(args,msgs,temperature,max_tokens,schema):
 global EXTRA_BODY
 if not ROTATE:return chat(args.base_url,args.model,msgs,temperature,max_tokens,not args.no_thinking,schema)
 for k in range(3*len(ROTATE)):
  prov=ROTATE[k%len(ROTATE)];base=dict(EXTRA_BODY);EXTRA_BODY={**base,'provider':{**base.get('provider',{}),'only':[prov],'order':[prov],'allow_fallbacks':False}}
  try:return chat(args.base_url,args.model,msgs,temperature,max_tokens,not args.no_thinking,schema)
  except urllib.error.HTTPError as e:
   if e.code!=429 and e.code<500 or k==3*len(ROTATE)-1:raise
   time.sleep(3*(1+k//len(ROTATE)))
  finally:EXTRA_BODY=base

class BudgetStop(Exception):pass
def budget_reserve(args,ptok,max_out):
 """Shared paid-spend ledger (flock): refuse the call unless the worst case keeps total spend within --budget-usd."""
 if not args.budget_usd:return
 led=Path(args.spend_ledger);led.parent.mkdir(parents=True,exist_ok=True)
 with open(str(led)+'.lock','a') as lk:
  fcntl.flock(lk,fcntl.LOCK_EX)
  s=json.loads(led.read_text()) if led.exists() else {'spent_usd':0.0,'calls':0,'by_model':{}}
  worst=ptok*1.3*args.price_in+max_out*args.price_out  # 1.3: token-estimate slack
  if s['spent_usd']+worst>args.budget_usd:raise BudgetStop(f"spent {s['spent_usd']:.4f} + worst {worst:.4f} > cap {args.budget_usd}")
def budget_record(args,usage):
 if not args.budget_usd:return 0.0
 led=Path(args.spend_ledger);usage=usage or {}
 c=usage['cost'] if isinstance(usage.get('cost'),(int,float)) else usage.get('prompt_tokens',0)*args.price_in+usage.get('completion_tokens',0)*args.price_out  # provider-billed cost when reported (OpenRouter)
 with open(str(led)+'.lock','a') as lk:
  fcntl.flock(lk,fcntl.LOCK_EX)
  s=json.loads(led.read_text()) if led.exists() else {'spent_usd':0.0,'calls':0,'by_model':{}}
  s['spent_usd']=round(s['spent_usd']+c,8);s['calls']+=1;m=s['by_model'].setdefault(args.model,{'usd':0.0,'calls':0,'prompt_tokens':0,'completion_tokens':0})
  m['usd']=round(m['usd']+c,8);m['calls']+=1;m['prompt_tokens']+=usage.get('prompt_tokens',0);m['completion_tokens']+=usage.get('completion_tokens',0);s['updated_at']=q.now()
  tmp=Path(str(led)+'.tmp');tmp.write_text(json.dumps(s,indent=1));os.replace(tmp,led)
 return c

def run_lean(p,h,args):
 """Causal rollout on the model's OWN graph with the v0.3-lean renderer (CKL 2026-10-29: base models over the full gamejam).
 Same strict validator, repairs and bounded corrections; exhausted turns become model_failed empty patches; layout v2 publish."""
 import lean_context as L
 mf=p.DATA/'manifest.json'
 if not mf.exists():q.prepare(p,ROOT/'data/v02'/args.source)
 m=json.loads(mf.read_text())
 if p.ledger() and m.get('teacher_model')!=args.model:raise q.GuardError('Existing dataset has a different teacher')
 m.update(teacher_model=args.model,teacher_engine='local-openai-compatible',renderer='v0.3-lean',**L.provenance(),context_layout={'name':'v0.3-lean','ctx':args.ctx},
  thinking=not args.no_thinking,extra_body=EXTRA_BODY or None,rotate_providers=ROTATE or None,provider_quantization=QUANT or None,structured_decoding=('json_schema (turn-patch minus maxLength; turn_id const; message_id enum'+('; quote = enum of exact sentence spans of that message' if args.quote_spans else '')+')') if args.structured else None,
  serving=args.serving_note,base_weights=args.weights_note,paid=({'budget_usd_shared':args.budget_usd,'spend_ledger':args.spend_ledger,'price_in_per_token':args.price_in,'price_out_per_token':args.price_out} if args.budget_usd else None),failure_policy='exhausted turns recorded as label_status=model_failed empty patches')
 p.atomic(mf,m)
 jobs=p.DATA/'local-jobs';jobs.mkdir(exist_ok=True);progress=p.DATA/'progress.jsonl'
 with (p.DATA/'.local-runner.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  rows=p.ledger();turns=p.read('turns.json');graph=p.initial()
  for turn,row in zip(turns,rows):p.check(row['patch'],turn,graph);graph,_=p.apply(graph,row['patch'],turn)
  started=time.time();done=valid=calls=0
  root=next((t for t in turns if t['id']==m['root_turn_id']),None);count=token_counter()  # same project_description rule as pipeline.context, without re-reading turns.json per turn
  for turn in turns[len(rows):]:
   if (p.DATA/'STOP').exists():print(json.dumps({'state':'stopped'}));return
   if args.stop_at and len(rows)>=args.stop_at:print(json.dumps({'state':'stop_at_reached','completed':len(rows)}));return
   pdesc='\n'.join(x['text'] for x in root['messages']) if root and root['ordinal']<=turn['ordinal'] else 'Gamejam project; detailed human brief not yet received.'
   schema=lean_schema(p,turn,args.quote_spans) if args.structured else None
   candidate=correction=None;attempts=[];patch=None
   for attempt in range(1+args.max_corrections):
    msgs,actx,lean=lean_messages(h,p,turn,graph,pdesc,candidate,correction)
    ptok=sum(count(x['content']) for x in msgs);room=args.ctx-ptok-64
    job=jobs/(turn['id']+'-'+str(time.time_ns()));job.mkdir(parents=True);(job/'prompt.json').write_text(json.dumps(msgs,ensure_ascii=False))
    if room<256:attempts.append({'attempt':attempt+1,'error':f'context_overflow prompt_tokens_est={ptok}'});break
    t0=time.time()
    try:budget_reserve(args,ptok,min(args.max_tokens,room))
    except BudgetStop as e:print(json.dumps({'state':'budget_stop','turn':turn['id'],'why':str(e)}),flush=True);p.publish(rows);return
    try:resp=chat_rotating(args,msgs,0 if attempt==0 else 0.4,min(args.max_tokens,room),schema)
    except urllib.error.HTTPError as e:
     body=e.read()[:2000].decode('utf8','replace');p.atomic(job/'server-error.json',{'code':e.code,'body':body})
     attempts.append({'attempt':attempt+1,'error':f'http {e.code}: {body[:300]}'});break
    calls+=1;usd=budget_record(args,resp.get('usage'));raw=(resp.get('choices') or [{}])[0].get('message',{}).get('content') or '';text,renamed=rename_schema_key(strip_think(raw))
    (job/'output.txt').write_text(raw)
    a={'attempt':attempt+1,'usd':round(usd,6),**({'provider':resp.get('provider'),'quantization':QUANT.get(resp.get('provider'),'not_listed')} if QUANT else {}),'seconds':round(time.time()-t0,2),'usage':resp.get('usage'),'finish_reason':(resp.get('choices') or [{}])[0].get('finish_reason'),'job':str(job.relative_to(p.DATA)),'prompt_tokens_est':ptok}
    try:
     patch,repairs=validate_lean(h,p,text,turn,graph,actx);a.update(valid=True,evidence_repairs=repairs+([{'method':'renamed $schema key'}] if renamed else []));attempts.append(a);break
    except (ValueError,KeyError,TypeError,IndexError,p.jsonschema.ValidationError) as e:
     a.update(valid=False,error=str(e)[:600]);attempts.append(a);candidate=text;correction=actx.encode_text(h.feedback(p,text,turn,graph,e));p.atomic(job/'validation-error.json',{'error':str(e)[:1600]})
   prov={'model':args.model,'engine':'local','renderer':'v0.3-lean',**L.provenance(),'thinking':not args.no_thinking,'structured':bool(args.structured),'quote_spans':bool(args.quote_spans),'attempts_for_turn':len(attempts),'attempts':attempts,'runner_sha256':q.sha(__file__),'lean_aliases':actx.to_real}
   if patch is not None:
    row={'turn_id':turn['id'],'model':args.model,'annotator_kind':'LLM','label_status':'machine_draft','generated_at':q.now(),'input':lean,'patch':patch,'inference_provenance':{**prov,'evidence_repairs':attempts[-1]['evidence_repairs']}};valid+=1
   else:
    patch={'schema_version':'0.2','turn_id':turn['id'],'actor_patch':{},'actor_evidence':[],'tasks':[],'decisions':[],'relationships':[]};p.check(patch,turn,graph)
    row={'turn_id':turn['id'],'model':args.model,'annotator_kind':'LLM','label_status':'model_failed','generated_at':q.now(),'input':lean,'patch':patch,'inference_provenance':{**prov,'failure':(attempts[-1].get('error') if attempts else 'no attempt')}}
   h.append_row(p,rows,row);graph,_=p.apply(graph,patch,turn);done+=1
   if done%args.publish_every==0 or turn is turns[-1]:p.publish(rows)
   el=time.time()-started
   ev={'turn':turn['id'],'n':len(rows),'valid':row['label_status']=='machine_draft','calls':len(attempts),'run_done':done,'run_valid_rate':round(valid/done,3),'turns_per_h':round(done/el*3600,1),'eta_h':round((len(turns)-len(rows))/(done/el)/3600,1),'at':q.now()}
   with progress.open('a') as fh:fh.write(json.dumps(ev)+'\n')
   print(json.dumps(ev),flush=True)
  p.publish(rows);print(json.dumps({'state':'generated','done':done,'valid':valid,'calls':calls}))

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--dataset',default='qwen35-4b-base-day1');ap.add_argument('--source',default='day1')
 ap.add_argument('--model',default='Qwen/Qwen3.5-4B');ap.add_argument('--base-url',default='http://127.0.0.1:8000/v1')
 ap.add_argument('--max-tokens',type=int,default=2048);ap.add_argument('--max-accepted',type=int,default=20);ap.add_argument('--max-corrections',type=int,default=3)
 ap.add_argument('--serving-note',default='');ap.add_argument('--weights-note',default='')
 ap.add_argument('--no-thinking',action='store_true',help='enable_thinking=False in the chat template (speed); recorded as a contract difference')
 ap.add_argument('--record-failures',action='store_true',help='baseline mode: exhausted turns become label_status=model_failed empty patches')
 ap.add_argument('--stop-at',type=int,default=0,help='stop once the ledger holds this many turns (CKL plan B: 4B to 500, 9B to 250)')
 ap.add_argument('--teacher-forced',default='',help='teacher dataset name: eval mode on teacher state, no ledger writes')
 ap.add_argument('--eval-turn-ids',default='');ap.add_argument('--eval-last-frac',type=float,default=0)
 ap.add_argument('--renderer',choices=['g8k','v0.3-lean'],default='g8k',help='rollout renderer; v0.3-lean = causal rollout via run_lean')
 ap.add_argument('--structured',action='store_true',help='send response_format json_schema (vLLM guided decoding)')
 ap.add_argument('--quote-spans',action='store_true',help='with --structured: each evidence quote must be an exact sentence span of its message')
 ap.add_argument('--ctx',type=int,default=CTX,help='served max_model_len (prompt + output must fit)')
 ap.add_argument('--publish-every',type=int,default=1,help='layout-v2 publish cadence in turns (always on the last turn)')
 ap.add_argument('--api-key-env',default='',help='env var holding the bearer key (paid endpoints)')
 ap.add_argument('--budget-usd',type=float,default=0,help='hard cap on the shared --spend-ledger; worst case checked before every call')
 ap.add_argument('--spend-ledger',default='',help='JSON spend ledger shared by parallel paid runs')
 ap.add_argument('--price-in',type=float,default=0);ap.add_argument('--price-out',type=float,default=0)
 ap.add_argument('--extra-body',default='',help='JSON merged into every request body (no secrets)')
 ap.add_argument('--rotate-providers',default='',help='comma list of provider slugs, all at the required quantization')
 ap.add_argument('--endpoints-url',default='',help='OpenRouter /endpoints URL: maps served provider -> quantization in provenance')
 ap.add_argument('--hourly-billed',action='store_true',help='GPU-hour endpoint (e.g. Modal): no per-token cap; the caller owns a wall-clock hard stop')
 ap.add_argument('--run',action='store_true')
 a=ap.parse_args()
 if a.budget_usd and not (a.spend_ledger and a.price_in>0 and a.price_out>0):ap.error('--budget-usd needs --spend-ledger and both prices')
 if a.extra_body:
  global EXTRA_BODY;EXTRA_BODY=json.loads(a.extra_body)
 if a.rotate_providers:
  global ROTATE;ROTATE=a.rotate_providers.split(',')
 if a.endpoints_url:
  global QUANT;e=json.load(urllib.request.urlopen(a.endpoints_url,timeout=60))['data']['endpoints'];QUANT={x['provider_name']:x.get('quantization') for x in e}
 if a.api_key_env:
  global API_KEY;API_KEY=os.environ.get(a.api_key_env) or ap.error('missing env '+a.api_key_env)
  if not (a.budget_usd or a.hourly_billed):ap.error('paid endpoint needs --budget-usd (or --hourly-billed with an external hard stop)')
 if not re.fullmatch(r'qwen[a-zA-Z0-9_-]+',a.dataset):ap.error('Dataset must start qwen')
 os.umask(0o077);os.environ['CCT_V02_DATASET']=a.dataset
 import pipeline as p,claude_haiku_day1 as h
 if not a.run:print(json.dumps({'dataset':a.dataset,'layout':LAYOUT['name']}));return
 run_teacher_forced(p,h,a) if a.teacher_forced else (run_lean(p,h,a) if a.renderer=='v0.3-lean' else run(p,h,a))

if __name__=='__main__':main()
