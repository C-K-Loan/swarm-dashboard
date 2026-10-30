#!/usr/bin/env python3
"""Sequential V0.2 ledger: next context → validated delta → deterministic graph."""
import argparse,copy,fcntl,hashlib,json,os,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
import jsonschema
from quota import measure as quota_measure
ROOT=Path(__file__).resolve().parents[2]
from dataset import DATA
SCHEMA=json.loads((ROOT/'schemas/v02/turn-patch.schema.json').read_text())

def read(name,default=None):
 p=DATA/name
 return json.loads(p.read_text()) if p.exists() else default

def atomic(path,obj):
 path.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
 with os.fdopen(fd,'w') as f:json.dump(obj,f,ensure_ascii=False,indent=2);f.write('\n')
 os.replace(tmp,path)

def ledger():
 p=DATA/'predictions.jsonl'
 return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

def initial():
 return {'actors':{a['id']:a for a in read('actors.json')},'tasks':{},'decisions':{},'relationships':[],'ambiguities':[]}

def uid(kind,turn,ref):return kind+':'+hashlib.sha256((turn+':'+ref).encode()).hexdigest()[:16]

def check(patch,turn,graph):
 jsonschema.validate(patch,SCHEMA)
 if patch['turn_id']!=turn['id']:raise ValueError('Out-of-order turn: expected '+turn['id'])
 msgs={m['id']:m['text'] for m in turn['messages']}
 def evidence(items):
  for e in items:
   if e['message_id'] not in msgs or e['quote'] not in msgs[e['message_id']]:raise ValueError('Evidence must quote one contiguous current-source substring verbatim. Do not join separated sentences. Message '+e['message_id']+'; invalid quote: '+repr(e['quote']))
 if patch['actor_patch'] and not patch['actor_evidence']:raise ValueError('Actor update requires evidence')
 evidence(patch['actor_evidence'])
 refs=set(graph['tasks']);local=set()
 for task in patch['tasks']:
  evidence(task['evidence']);ref=task['ref'];res=task['resolution']
  if ref in local:raise ValueError('Duplicate patch task ref')
  local.add(ref)
  if res=='existing' and ref not in refs:raise ValueError('Unknown existing task '+ref)
  if res in ('new','ambiguous'):
   if not ref.startswith('new:'):raise ValueError('New/ambiguous tasks need local new: refs')
   if not all(k in task['changes'] for k in ('title','summary','status','importance','complexity','blocker')):raise ValueError('New tasks require complete fields')
   if not task['changes']['title']:raise ValueError('New task title required')
  if res=='ambiguous' and (not task.get('candidate_refs') or any(r not in refs for r in task['candidate_refs'])):raise ValueError('Ambiguous candidates must exist')
  blocker=task['changes'].get('blocker')
  if blocker and blocker['waiting_on_actor'] and blocker['waiting_on_actor'] not in graph['actors']:raise ValueError('Unknown blocker actor')
 refs|=local
 actors=set(graph['actors'])
 decision_refs=set()
 for d in patch['decisions']:
  if d['ref'] in decision_refs:raise ValueError('Duplicate decision ref')
  decision_refs.add(d['ref'])
  evidence(d['evidence'])
  if not d['ref'].startswith('new:'):raise ValueError('Decision ID must be local')
  if d['made_by_actor_id'] not in actors or any(t not in refs for t in d['affected_task_refs']):raise ValueError('Invalid decision references')
 for rel in patch['relationships']:
  evidence(rel['evidence']);a=rel['from_ref'];b=rel['to_ref'];typ=rel['type']
  if typ in ('works_on','reviews') and not(a in actors and b in refs):raise ValueError('Expected actor → task')
  if typ=='delegates_to' and not(a in actors and b in actors and rel.get('task_ref') in refs):raise ValueError('Expected actor → actor + task')
  if typ!='delegates_to' and 'task_ref' in rel:raise ValueError('task_ref is only valid for delegates_to')
  if typ in ('depends_on','is_sub_task') and not(a in refs and b in refs and a!=b):raise ValueError('Expected two distinct tasks')

def apply(graph,patch,turn):
 g=copy.deepcopy(graph);aid=turn['actor_id'];t=turn['id'];ts=turn['end_ts'];resolved=copy.deepcopy(patch)
 g['actors'][aid].update(patch['actor_patch']);g['actors'][aid]['last_turn_id']=t
 mapping={x['ref']:uid('task',t,x['ref']) for x in patch['tasks'] if x['resolution']!='existing'}
 for task in resolved['tasks']:
  local=task['ref'];tid=mapping.get(local,local);task['ref']=tid
  old=g['tasks'].get(tid,{'id':tid,'created_turn_id':t,'created_at':ts})
  old.update(task['changes']);old.update(last_turn_id=t,updated_at=ts,evidence=task['evidence'])
  if task['resolution']=='ambiguous':
   old['identity_ambiguous']=True;old['candidate_ids']=task['candidate_refs'];g['ambiguities'].append({'turn_id':t,'task_id':tid,'candidate_ids':task['candidate_refs']})
  g['tasks'][tid]=old
 for d in resolved['decisions']:
  d['ref']=uid('decision',t,d['ref']);d['affected_task_refs']=[mapping.get(x,x) for x in d['affected_task_refs']]
  g['decisions'][d['ref']]={**d,'id':d['ref'],'turn_id':t,'ts':ts}
 for rel in resolved['relationships']:
  for key in ('from_ref','to_ref','task_ref'):
   if key in rel:rel[key]=mapping.get(rel[key],rel[key])
  key=tuple(rel.get(k) for k in ('type','from_ref','to_ref','task_ref'))
  if not any(tuple(r.get(k) for k in ('type','from_ref','to_ref','task_ref'))==key for r in g['relationships']):
   g['relationships'].append({**rel,'turn_id':t,'ts':ts})
 return g,resolved

def replay(records=None):
 g=initial();turns=read('turns.json')
 for turn,row in zip(turns,ledger() if records is None else records):g,_=apply(g,row['patch'],turn)
 return g

def context(turn,graph,records):
 aid=turn['actor_id'];related=set()
 peers={aid}
 for rel in graph['relationships']:
  if aid in (rel['from_ref'],rel['to_ref']):
   if rel['to_ref'] in graph['tasks']:related.add(rel['to_ref'])
   if rel.get('task_ref'):related.add(rel['task_ref'])
   peers|={x for x in (rel['from_ref'],rel['to_ref']) if x in graph['actors']}
 for task in graph['tasks'].values():
  if task.get('last_turn_id') in [r['turn_id'] for r in records[-8:]]:related.add(task['id'])
 # Shared roots and ancestors keep human-defined project tasks visible to new workers.
 for rel in graph['relationships']:
  if rel['type']=='is_sub_task' and rel['from_ref'] in related:related.add(rel['to_ref'])
 children={r['from_ref'] for r in graph['relationships'] if r['type']=='is_sub_task'}
 related|={t for t,v in graph['tasks'].items() if t not in children and v.get('status') not in ('done','cancelled')}
 relevant=[v for k,v in graph['tasks'].items() if k in related]
 allturns=read('turns.json');root_id=read('manifest.json')['root_turn_id'];root=next((t for t in allturns if t['id']==root_id and t['ordinal']<=turn['ordinal']),None)
 return {'project_description':'\n'.join(m['text'] for m in root['messages']) if root else 'Gamejam project; detailed human brief not yet received.',
  'actor_id_and_current_state':graph['actors'][aid], 'current_turn_metadata':{k:v for k,v in turn.items() if k!='messages'},
  'relevant_existing_tasks_with_ids':relevant,
  'relevant_actors_and_relationships':{'actors':list(graph['actors'].values()),'relationships':[r for r in graph['relationships'] if r['from_ref'] in peers|related or r['to_ref'] in peers|related]},
  'recent_relevant_turns':[{'turn_id':r['turn_id'],'patch':r['patch']} for r in records[-3:]],
  'current_turn_prose':turn['messages']}

def publication_hash(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def turn_view(turn,row=None,graph=None,resolved=None):
 """One historical turn, with recorded input rather than reconstructed context."""
 return {**turn,'prediction':row['patch'] if row else None,'resolved_prediction':resolved if row else None,
  'generated_at':row.get('generated_at') if row else None,
  'task_states':{x['ref']:graph['tasks'][x['ref']] for x in resolved['tasks']} if row else {},
  'actor_state_after':graph['actors'].get(turn['actor_id']) if row else None,
  'task_names':{k:v.get('title') for k,v in graph['tasks'].items()} if row else {},
  'state_after':{**graph,'project_description':row.get('input',{}).get('project_description')} if row else None,
  'llm_input':row.get('input') if row else None}

def publish(records=None):
 """Append immutable turn snapshots, then atomically advance the compact index.

 The index is the commit point. Resume from its final graph; never replay/rewrite
 earlier snapshots on ordinary append. A crash may leave orphan snapshots, which
 are checked against the source/row hashes before reuse. The caller's writer lock
 is separate from this lock (accept already holds it).
 """
 DATA.mkdir(parents=True,exist_ok=True)
 with (DATA/'.publish.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  records=ledger() if records is None else records
  turns=read('turns.json');manifest=read('manifest.json');actors=read('actors.json')
  if len(records)>len(turns):raise ValueError('Publication ledger longer than source')
  for turn,row in zip(turns,records):
   if row['turn_id']!=turn['id'] or row['patch']['turn_id']!=turn['id']:raise ValueError('Publication ledger desync')
  # Source IDs become filenames, including during an offline migration.
  import re
  if any(not re.fullmatch(r'[A-Za-z0-9_-]+',t['id']) for t in turns) or len({t['id'] for t in turns})!=len(turns):
   raise ValueError('Unsafe or duplicate source turn ID')
  source_hash=publication_hash({'turns':turns,'actors':actors})
  hashes=[publication_hash(row) for row in records]
  previous=read('turn-index.json',{})
  checkpoint=previous.get('_publication',{})
  done=checkpoint.get('row_sha256',[])
  if previous:
   if previous.get('layout_version')!=2 or checkpoint.get('source_sha256')!=source_hash:
    raise ValueError('Published source changed; use a separate dataset')
   if hashes[:len(done)]!=done:raise ValueError('Published ledger prefix changed or shrank')
  directory=DATA/'turn-states';directory.mkdir(exist_ok=True)
  missing=next((i for i,t in enumerate(turns[:len(done)]) if not (directory/(t['id']+'.json')).exists()),len(done))
  # Missing old files are recoverable without rewriting any surviving snapshots.
  start=missing
  if start==len(done) and previous:
   g=previous['graph']
  elif start:
   prior=read('turn-states/'+turns[start-1]['id']+'.json')
   g={k:v for k,v in prior['state_after'].items() if k!='project_description'}
  else:g=initial()
  for i in range(start,len(records)):
   turn,row=turns[i],records[i];path=directory/(turn['id']+'.json')
   marker={'source_sha256':source_hash,'row_sha256':hashes[i]}
   if path.exists():
    saved=json.loads(path.read_text())
    if saved.get('_publication')!=marker:raise ValueError('Published turn differs from ledger: '+turn['id'])
    g={k:v for k,v in saved['state_after'].items() if k!='project_description'}
   else:
    g,resolved=apply(g,row['patch'],turn)
    atomic(path,{**turn_view(turn,row,g,resolved),'_publication':marker})
  out=[]
  for i,turn in enumerate(turns):
   row=records[i] if i<len(records) else None
   # Search retains source text and this turn's changes, never accumulated graphs.
   search='\n'.join(m['text'] for m in turn['messages'])
   if row:search+='\n'+json.dumps(row['patch'],ensure_ascii=False,separators=(',',':'))
   out.append({**{k:turn.get(k) for k in ('id','session_id','end_ts','ordinal','actor_id','actor_name')},
               'has_prediction':row is not None,'search_text':search})
  quota=read('quota.json',{})
  status={'state':'generated' if len(records)==len(turns) else ('awaiting_budget' if quota.get('awaiting_budget') else 'budget_stopped' if quota.get('allowed') is False else 'generating'),'completed':len(records),'total':len(turns),'updated_at':datetime.now(timezone.utc).isoformat(),'model':manifest['teacher_model']}
  index={'layout_version':2,'manifest':manifest,'status':status,'actors':actors,'turns':out,'graph':g,
         '_publication':{'source_sha256':source_hash,'row_sha256':hashes}}
  atomic(DATA/'turn-index.json',index)
  # Small compatibility mirror for existing training/run-picker consumers.
  atomic(DATA/'review.json',index)
  atomic(DATA/'status.json',status)
  return status

def accept(path,provenance=None):
 patch=json.loads(Path(path).read_text());DATA.mkdir(parents=True,exist_ok=True)
 with (DATA/'.writer.lock').open('w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);records=ledger();turns=read('turns.json')
  if len(records)>=len(turns):raise ValueError('Dataset complete')
  turn=turns[len(records)];g=replay(records);check(patch,turn,g)
  row={'turn_id':turn['id'],'model':'gpt-6-luna','annotator_kind':'LLM','label_status':'machine_draft','generated_at':datetime.now(timezone.utc).isoformat(),'input':context(turn,g,records),'patch':patch}
  if provenance:row['inference_provenance']=provenance
  with (DATA/'predictions.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
  print(json.dumps(publish(records+[row])))

def save_review(payload):
 records=ledger();turns=read('turns.json');ids={t['id']:i for i,t in enumerate(turns)}
 tid=payload.get('turn_id');status=payload.get('status')
 if tid not in ids or ids[tid]>=len(records):raise ValueError('Prediction not available')
 if status not in ('approved','corrected','rejected'):raise ValueError('Invalid review status')
 i=ids[tid];correction=payload.get('correction')
 if status=='corrected':
  if not correction:raise ValueError('Correction is required')
  check(correction,turns[i],replay(records[:i]))
 with (DATA/'.review.lock').open('w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  path=DATA/'reviews.jsonl';rows=[json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
  prior=[r for r in rows if r['turn_id']==tid];revision=len(prior)
  if payload.get('base_revision',0)!=revision:raise ValueError('Review changed in another tab. Reload before saving.')
  row={'turn_id':tid,'status':status,'revision':revision+1,'reviewer':'CKL','reviewed_at':datetime.now(timezone.utc).isoformat(),'note':str(payload.get('note',''))[:4000],'correction':correction if status=='corrected' else None,'prediction_sha256':hashlib.sha256(json.dumps(records[i]['patch'],sort_keys=True).encode()).hexdigest()}
  with path.open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
 print(json.dumps(row))

def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['next','accept','publish','status','review']);ap.add_argument('path',nargs='?');a=ap.parse_args()
 if a.command=='review':save_review(json.load(sys.stdin))
 elif a.command=='accept':accept(a.path)
 elif a.command=='next':
  quota=quota_measure();atomic(DATA/'quota.json',quota)
  if not quota['allowed']:
   message='BUDGET_STOP: '+quota['reason']
   if a.path:Path(a.path).write_text(message+'\n')
   print(message);sys.exit(3)
  records=ledger();turns=read('turns.json')
  if len(records)>=len(turns):print('COMPLETE');return
  c=context(turns[len(records)],replay(records),records)
  value=(ROOT/'prompts/v02/extract-turn.txt').read_text()+'\n\n'+json.dumps(c,ensure_ascii=False,indent=2)
  if a.path:Path(a.path).write_text(value);print(a.path)
  else:print(value)
 elif a.command=='publish':print(json.dumps(publish()))
 else:print(json.dumps(read('status.json',{})))
if __name__=='__main__':main()
