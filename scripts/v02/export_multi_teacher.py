#!/usr/bin/env python3
"""Multi-teacher v0.3-lean SFT export (chat triples) for a student LoRA.

Each teacher's ledger is replayed through its own graph, the lean context is rebuilt
and must equal the recorded row['input'] byte for byte, and the accepted patch is
alias-encoded with that turn's alias table (round trip checked). Rows that fail any
check are refused, not repaired. Nothing is uploaded and no model is called.

Split by source turn number (shared across teachers, so no turn leaks across sides):
  holdout eval : turns >= --eval-from (Day1 last 20%), never in train or validation
  validation   : [--val-from, --eval-from - embargo)
  train        : < --val-from - embargo
"""
import argparse,collections,copy,hashlib,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
os.environ.setdefault('CCT_V02_DATASET','haiku45-lean-day1')
sys.path.insert(0,str(Path(__file__).resolve().parent))
import claude_haiku_day1 as h  # noqa: E402
import lean_context as L  # noqa: E402
import pipeline  # noqa: E402
import sender_attribution  # noqa: E402

RENDERER='v0.3-lean/chat-triple-v1'
# Fit mode: (detail_cap, index_cap) steps tried in order until the prompt fits the input budget.
FIT_LADDER=[(None,60),(10,60),(8,40),(6,30),(5,20),(4,15),(3,10),(2,6),(1,3)]
DEFAULT_TEACHERS=['haiku45-lean-day1','nemotron-ultra-lean-day1']
OUT=ROOT/'data/v02/_multi-teacher'

def turn_no(tid):return int(tid.split('-')[1])
def sha(b):return hashlib.sha256(b).hexdigest()

def system_prompt():
 h.LEAN=True
 return h.system_prompt(pipeline)

def project_description(turn,turns_by_id,root_id):
 root=turns_by_id.get(root_id)
 return '\n'.join(m['text'] for m in root['messages']) if root and root['ordinal']<=turn['ordinal'] else 'Gamejam project; detailed human brief not yet received.'

def teacher_rows(dataset,system,limit=None,fit=None):
 """fit = (tokenizer, input_budget): step down FIT_LADDER on input length only (never on the target)."""
 d=ROOT/'data/v02'/dataset
 turns=json.loads((d/'turns.json').read_text());actors=json.loads((d/'actors.json').read_text())
 manifest=json.loads((d/'manifest.json').read_text());by_id={t['id']:t for t in turns}
 ledger=[json.loads(l) for l in (d/'predictions.jsonl').read_text().splitlines() if l.strip()][:limit]
 senders=sender_attribution.overlay_for(dataset)
 graph={'actors':{a['id']:a for a in actors},'tasks':{},'decisions':{},'relationships':[],'ambiguities':[]}
 rows=[];hashes={'turns':sha((d/'turns.json').read_bytes()),'actors':sha((d/'actors.json').read_bytes()),'ledger_prefix':sha('\n'.join(json.dumps(r,sort_keys=True) for r in ledger).encode())}
 for turn,row in zip(turns,ledger):
  if row['turn_id']!=turn['id']:raise SystemExit(f'{dataset}: ledger desync at {turn["id"]}')
  desc=project_description(turn,by_id,manifest.get('root_turn_id'))
  ctx,al=L.build(turn,graph,desc)
  if ctx!=row['input']:raise SystemExit(f'{dataset} {turn["id"]}: rebuilt lean context != recorded input')
  rec=row['inference_provenance'].get('lean_aliases')
  if rec is not None and rec!=al.to_real:raise SystemExit(f'{dataset} {turn["id"]}: alias table differs from recorded lean_aliases')
  target=al.encode_text(json.dumps(row['patch'],ensure_ascii=False,separators=(',',':')))
  if L.decode_patch(json.loads(target),al)!=row['patch']:raise SystemExit(f'{dataset} {turn["id"]}: alias round trip failed')
  user=h.user_prompt(ctx);fitted=None
  if fit:
   tok,budget=fit;step=0
   size=lambda u:len(tok.apply_chat_template([{'role':'system','content':system},{'role':'user','content':u}],tokenize=True,add_generation_prompt=True))
   while size(user)>budget and step+1<len(FIT_LADDER):
    step+=1;dc,ic=FIT_LADDER[step];ctx,al=L.build(turn,graph,desc,index_cap=ic,detail_cap=dc);user=h.user_prompt(ctx)
   target=al.encode_text(json.dumps(row['patch'],ensure_ascii=False,separators=(',',':')))
   # Every id the target uses must be an alias shown in this context; otherwise the row cannot fit.
   resolvable='task:' not in target and 'actor:' not in target and L.decode_patch(json.loads(target),al)==row['patch']
   fitted={'step':step,'detail_cap':FIT_LADDER[step][0],'index_cap':FIT_LADDER[step][1],'input_budget':budget,'resolvable':resolvable}
  reps=row['inference_provenance'].get('evidence_repairs') or []
  prov={'teacher':row['model'],'teacher_engine':manifest.get('teacher_engine'),'dataset':dataset,'turn_id':turn['id'],
   'renderer':RENDERER,'context_version':ctx.get('context_version'),'lean_context_sha256':L.SOURCE_SHA256,
   'system_prompt_sha256':sha(system.encode()),'label_status':row.get('label_status'),'generated_at':row.get('generated_at'),
   'repairs_applied':sorted(collections.Counter(r.get('method','unknown') for r in reps).items()),
   'source_hashes':hashes}
  if turn['id'] in senders:prov['sender_attribution']=senders[turn['id']]
  if fitted:prov['fit']=fitted
  rows.append({'turn_id':turn['id'],'provenance':prov,'messages':[{'role':'system','content':system},{'role':'user','content':user},{'role':'assistant','content':target}]})
  graph,_=pipeline.apply(graph,row['patch'],turn)
 return rows,len(turns)

def filter_export(src,max_tokens,name):
 """Strict-drop rows over max_tokens from an existing export (same snapshot), never truncate."""
 src=Path(src);man=json.loads((src/'manifest.json').read_text());out=OUT/name;out.mkdir(parents=True,exist_ok=True)
 files={};stats={};dropped={}
 for k in ('train','validation','holdout_eval'):
  keep=[];gone=[]
  for line in (src/f'{k}.jsonl').read_text().splitlines():
   x=json.loads(line);(keep if x['provenance']['tokens']['total']<=max_tokens else gone).append(x)
  body=''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in keep).encode()
  tmp=out/f'.{k}.jsonl.tmp';tmp.write_bytes(body);os.replace(tmp,out/f'{k}.jsonl')
  files[k]={'path':str(out/f'{k}.jsonl'),'sha256':sha(body),'rows':len(keep)}
  dropped[k]=[{'dataset':x['provenance']['dataset'],'turn_id':x['turn_id'],'tokens':x['provenance']['tokens']['total']} for x in gone]
  by=collections.defaultdict(lambda:{'rows':0,'tokens':0,'assistant_tokens':0,'max_tokens':0,'sender_reattributed':0})
  for x in keep:
   s_=by[x['provenance']['dataset']];tk=x['provenance']['tokens'];s_['rows']+=1;s_['tokens']+=tk['total'];s_['assistant_tokens']+=tk['assistant'];s_['max_tokens']=max(s_['max_tokens'],tk['total']);s_['sender_reattributed']+='sender_attribution' in x['provenance']
  stats[k]=dict(by)
 man.update(name=name,filtered_at=datetime.now(timezone.utc).isoformat(),filtered_from={'dir':str(src),'files':man['files']},
  max_total_tokens={'limit':max_tokens,'policy':'strict drop of rows whose chat-templated length exceeds the limit; no truncation'},
  files=files,stats=stats,dropped=dropped,exporter_sha256=sha(Path(__file__).read_bytes()))
 (out/'manifest.json').write_text(json.dumps(man,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps({'out':str(out),'files':files,'stats':stats,'dropped':{k:len(v) for k,v in dropped.items()}},indent=1))

def main():
 ap=argparse.ArgumentParser()
 ap.add_argument('--filter-from',help='existing export dir: emit a token-capped copy of that snapshot instead of re-exporting')
 ap.add_argument('--max-tokens',type=int,default=8192)
 ap.add_argument('--fit-reserve',type=int,default=0,help='fit mode: trim context until the prompt fits max-tokens minus this output reserve; rows still over max-tokens or with unresolvable target aliases are dropped')
 ap.add_argument('--snapshot-from',help='cap each teacher at the labeled_turns of this earlier export (same snapshot)')
 ap.add_argument('--teachers',nargs='+',default=DEFAULT_TEACHERS)
 ap.add_argument('--eval-from',type=int,default=1523);ap.add_argument('--val-from',type=int,default=1203);ap.add_argument('--embargo',type=int,default=2)
 ap.add_argument('--name',default='qwen3-30b-a3b-lora-v1');ap.add_argument('--tokenizer',default='Qwen/Qwen3-30B-A3B-Instruct-2507')
 a=ap.parse_args()
 if a.filter_from:return filter_export(a.filter_from,a.max_tokens,a.name)
 if not a.val_from<=a.eval_from-a.embargo:ap.error('--val-from must leave the embargo before --eval-from')
 from transformers import AutoTokenizer
 tok=AutoTokenizer.from_pretrained(a.tokenizer)
 system=system_prompt();rows=[];per={};seen={};dupes=0;total_turns=None
 for t in a.teachers:
  if a.snapshot_from:limit=json.loads((Path(a.snapshot_from)/'manifest.json').read_text())['teachers'][t]['labeled_turns']
  else:limit=None
  r,total_turns=teacher_rows(t,system,limit,(tok,a.max_tokens-a.fit_reserve) if a.fit_reserve else None);per[t]={'labeled_turns':len(r),'last_turn':r[-1]['turn_id'] if r else None}
  for x in r:
   # Teachers replay their own graphs, so inputs differ; only an identical (input, target) pair is a duplicate.
   key=sha((x['messages'][1]['content']+'\0'+x['messages'][2]['content']).encode())
   if key in seen:dupes+=1;seen[key]['provenance'].setdefault('also_taught_by',[]).append(t);continue
   seen[key]=x;rows.append(x)
 rows.sort(key=lambda x:(turn_no(x['turn_id']),a.teachers.index(x['provenance']['dataset'])))  # interleaved by source time
 for x in rows:
  full=len(tok.apply_chat_template(x['messages'],tokenize=True))
  x['provenance']['tokens']={'total':full,'assistant':len(tok(x['messages'][2]['content'])['input_ids'])}
 def fits(x):
  f=x['provenance'].get('fit')
  return f is None or (f['resolvable'] and x['provenance']['tokens']['total']<=a.max_tokens)
 unfit=[x for x in rows if not fits(x)];rows=[x for x in rows if fits(x)]
 def side(n):
  if n>=a.eval_from:return 'holdout_eval'
  if a.eval_from-a.embargo<=n:return 'embargo'
  if n>=a.val_from:return 'validation'
  if a.val_from-a.embargo<=n:return 'embargo'
  return 'train'
 out=OUT/a.name;out.mkdir(parents=True,exist_ok=True);files={};stats={}
 split={k:[x for x in rows if side(turn_no(x['turn_id']))==k] for k in ('train','validation','holdout_eval','embargo')}
 for k in ('train','validation','holdout_eval'):
  body=''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in split[k]).encode()
  tmp=out/f'.{k}.jsonl.tmp';tmp.write_bytes(body);os.replace(tmp,out/f'{k}.jsonl')
  files[k]={'path':str(out/f'{k}.jsonl'),'sha256':sha(body),'rows':len(split[k])}
  by=collections.defaultdict(lambda:{'rows':0,'tokens':0,'assistant_tokens':0,'max_tokens':0,'sender_reattributed':0})
  for x in split[k]:
   s=by[x['provenance']['dataset']];tk=x['provenance']['tokens'];s['rows']+=1;s['tokens']+=tk['total'];s['assistant_tokens']+=tk['assistant'];s['max_tokens']=max(s['max_tokens'],tk['total']);s['sender_reattributed']+='sender_attribution' in x['provenance']
  stats[k]=dict(by)
 manifest={'name':a.name,'generated_at':datetime.now(timezone.utc).isoformat(),'renderer':RENDERER,'tokenizer':a.tokenizer,
  'system_prompt_sha256':sha(system.encode()),'lean_context_sha256':L.SOURCE_SHA256,'exporter_sha256':sha(Path(__file__).read_bytes()),
  'teachers':per,'source_turns':total_turns,
  'split':{'basis':'source turn number, shared across teachers','train':f'turn < {a.val_from-a.embargo}','embargo_turns':a.embargo,
   'validation':f'{a.val_from} <= turn < {a.eval_from-a.embargo}','holdout_eval':f'turn >= {a.eval_from} (never in train/validation)','embargoed_rows':len(split['embargo'])},
  'fit':{'ladder':FIT_LADDER,'max_tokens':a.max_tokens,'reserve':a.fit_reserve,'dropped':[{'dataset':x['provenance']['dataset'],'turn_id':x['turn_id'],'tokens':x['provenance']['tokens']['total'],'resolvable':x['provenance']['fit']['resolvable']} for x in unfit]} if a.fit_reserve else None,
  'dedupe':{'policy':'keep every teacher row; drop only byte-identical (user, assistant) pairs, crediting the dropped teacher in also_taught_by','dropped':dupes},
  'files':files,'stats':stats,
  'sender_attribution':'provenance.sender_attribution marks turns whose recorded actor was human but CKL only types in gamejam-master; inputs/targets left as the teacher saw them',
  'training_allowed':False,'note':'Export only. No upload, no training launch.'}
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps({'out':str(out),'files':files,'stats':stats,'dupes':dupes,'teachers':per},indent=1))

if __name__=='__main__':main()
