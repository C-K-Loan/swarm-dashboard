#!/usr/bin/env python3
"""v0.3-lean turn context: what the labeler SEES, not what is stored.

Schema and strict validator are unchanged. Compared with pipeline.context (v0.2-full):
done/cancelled tasks and their relationships are dropped, evidence quotes are never sent,
only the current actor's connected active tasks get full detail (others get [id,title]),
and long task:/actor: ids become per-prompt aliases T<n>/A<n> that decode_patch() maps
back before validation. An unknown alias is a validation error, never a guess.
"""
import copy,hashlib,json,re
from pathlib import Path

VERSION='v0.3-lean'
SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CLOSED={'done','cancelled'}
DETAIL_FIELDS=('title','status','summary','importance','complexity','blocker')
ALIAS=re.compile(r'^[TA]\d+$')
LEAN_RULES='''
v0.3-lean input format: tasks and actors use short per-turn aliases (T1, T2, A1, ...).
Use exactly these aliases (or "human") for every existing task/actor reference in your patch;
new tasks still use new:t1 etc. Done and cancelled tasks are omitted; only active work is shown.
active_tasks_connected_to_actor has full detail; other_active_tasks lists [alias,title] only.
relationships are [type,from,to,task_ref?]. No evidence quotes are shown: cite current_turn_prose only.
'''

class LeanContext:
 """Per-prompt alias table. alias(real) -> short; decode(short) -> real."""
 def __init__(self):self.to_alias={};self.to_real={};self.counts={'T':0,'A':0}
 def alias(self,real,kind):
  if real is None or real=='human' or real=='harness':return real
  if real not in self.to_alias:
   self.counts[kind]+=1;short=kind+str(self.counts[kind])
   self.to_alias[real]=short;self.to_real[short]=real
  return self.to_alias[real]
 def decode(self,value,field):
  if not isinstance(value,str) or value in ('human','harness') or value.startswith('new:'):return value
  if value in self.to_real:return self.to_real[value]
  if ALIAS.match(value):raise ValueError(f'Unknown alias {value} in {field}: use only aliases shown in the input')
  return value
 def encode_text(self,text):
  """Rewrite real ids in validator feedback so the model sees the aliases it was given."""
  for real,short in sorted(self.to_alias.items(),key=lambda kv:-len(kv[0])):text=text.replace(real,short)
  return text

def _compact(task,ctx):
 out={'id':ctx.alias(task['id'],'T')}
 for k in DETAIL_FIELDS:
  v=task.get(k)
  if v is None or v=='' or v==[] or v=={}:continue
  if k=='blocker':
   v={bk:bv for bk,bv in v.items() if bv is not None}
   if 'waiting_on_actor' in v:v['waiting_on_actor']=ctx.alias(v['waiting_on_actor'],'A')
   if not v:continue
  out[k]=v
 return out

def build(turn,graph,project_description,index_cap=60,detail_cap=None):
 """Return (context_dict, LeanContext). graph is the pipeline graph before this turn.
 detail_cap (fit mode): only the N most recently updated connected tasks keep full detail; the rest
 lead the [alias,title] index so their aliases stay visible. None = every connected task in detail."""
 ctx=LeanContext();aid=turn['actor_id'];tasks=graph['tasks'];actors=graph['actors']
 active={t:v for t,v in tasks.items() if v.get('status') not in CLOSED}
 rels=[r for r in graph['relationships'] if not any(r.get(k) in tasks and r.get(k) not in active for k in ('from_ref','to_ref','task_ref'))]
 connected=set()
 for r in rels:
  if aid in (r['from_ref'],r['to_ref']):
   for k in ('to_ref','task_ref','from_ref'):
    if r.get(k) in active:connected.add(r[k])
 for t,v in active.items():
  if (v.get('blocker') or {}).get('waiting_on_actor')==aid:connected.add(t)
 parents={r['from_ref']:r['to_ref'] for r in rels if r['type']=='is_sub_task'}
 frontier=list(connected)
 while frontier:
  p=parents.get(frontier.pop())
  if p in active and p not in connected:connected.add(p);frontier.append(p)
 # Stable order: most recently updated first, then id.
 order=lambda ids:sorted(ids,key=lambda t:(-(active[t].get('updated_at') or 0),t))
 ranked=order(connected)
 if detail_cap is not None:connected=set(ranked[:detail_cap]);demoted=ranked[detail_cap:]
 else:demoted=[]
 detail=[_compact(active[t],ctx) for t in order(connected)]
 others=demoted+order(set(active)-connected-set(demoted))
 index=[[ctx.alias(t,'T'),active[t].get('title') or '(untitled)'] for t in others[:index_cap]]
 keep=connected|{aid}
 rel_out=[]
 for r in rels:
  if not ({r['from_ref'],r['to_ref'],r.get('task_ref')}&keep):continue
  row=[r['type'],ctx.alias(r['from_ref'],'T' if r['from_ref'] in tasks else 'A'),ctx.alias(r['to_ref'],'T' if r['to_ref'] in tasks else 'A')]
  if r.get('task_ref'):row.append(ctx.alias(r['task_ref'],'T'))
  rel_out.append(row)
 prose=[{'id':m['id'],'text':m['text']} for m in turn['messages']]
 text=' '.join(m['text'] for m in turn['messages'])
 referenced={aid,'human'}|{a for a in actors if a in ctx.to_alias}
 referenced|={a for a,v in actors.items() if v.get('name') and len(v['name'])>3 and v['name'] in text}
 me=actors[aid]
 actor_rows=[[ctx.alias(a,'A'),actors[a].get('name')] for a in sorted(referenced) if a in actors]
 data={'context_version':VERSION,'project_description':project_description,
  'current_actor':{k:v for k,v in {'id':ctx.alias(aid,'A'),'name':me.get('name'),'role':me.get('role'),'state':me.get('state')}.items() if v is not None},
  'current_turn':{'turn_id':turn['id'],'kind':turn.get('kind')},
  'active_tasks_connected_to_actor':detail,'other_active_tasks':index,
  'relationships':rel_out,'actors':actor_rows,'current_turn_prose':prose}
 if len(others)>index_cap:data['other_active_tasks_omitted']=len(others)-index_cap
 if demoted:data['connected_tasks_shown_as_index']=len(demoted)
 return data,ctx

def decode_patch(patch,ctx):
 """Map aliases in every id-bearing field back to real ids. Raises ValueError on unknown aliases."""
 out=copy.deepcopy(patch)
 for i,t in enumerate(out.get('tasks') or []):
  if not isinstance(t,dict):continue
  t['ref']=ctx.decode(t.get('ref'),f'tasks[{i}].ref')
  if isinstance(t.get('candidate_refs'),list):t['candidate_refs']=[ctx.decode(c,f'tasks[{i}].candidate_refs') for c in t['candidate_refs']]
  b=(t.get('changes') or {}).get('blocker') if isinstance(t.get('changes'),dict) else None
  if isinstance(b,dict) and 'waiting_on_actor' in b:b['waiting_on_actor']=ctx.decode(b['waiting_on_actor'],f'tasks[{i}].changes.blocker.waiting_on_actor')
 for i,d in enumerate(out.get('decisions') or []):
  if not isinstance(d,dict):continue
  if 'made_by_actor_id' in d:d['made_by_actor_id']=ctx.decode(d['made_by_actor_id'],f'decisions[{i}].made_by_actor_id')
  if isinstance(d.get('affected_task_refs'),list):d['affected_task_refs']=[ctx.decode(x,f'decisions[{i}].affected_task_refs') for x in d['affected_task_refs']]
 for i,r in enumerate(out.get('relationships') or []):
  if not isinstance(r,dict):continue
  for k in ('from_ref','to_ref','task_ref'):
   if k in r:r[k]=ctx.decode(r[k],f'relationships[{i}].{k}')
 return out

def provenance():
 return {'prompt_contract_version':VERSION,'lean_context_sha256':SOURCE_SHA256}

def estimate_tokens(obj):
 """Rough size gauge (chars/4); provider receipts remain the real measure."""
 return len(json.dumps(obj,ensure_ascii=False,separators=(',',':')))//4
