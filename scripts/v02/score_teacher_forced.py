#!/usr/bin/env python3
"""Score teacher-forced predictions (teacher-forced.jsonl) against the teacher patch per turn.

Teacher-agnostic machine-teacher agreement, not a factual-quality score. Invalid or missing
predictions count as empty patches (so a model that fails validation scores zero on that turn).
Only refs that exist before the turn are comparable; new:* refs are local per patch, so new
tasks/decisions are compared by count.
"""
import argparse,json,sys
from pathlib import Path

def prf(pred,gold):
 tp=len(pred&gold);p=tp/len(pred) if pred else (1.0 if not gold else 0.0);r=tp/len(gold) if gold else (1.0 if not pred else 0.0)
 return p,r,(2*p*r/(p+r) if p+r else 0.0)

def existing(patch):return {t['ref']:t for t in patch.get('tasks',[]) if t.get('resolution')=='existing'}
def rels(patch):
 return {(r['type'],r['from_ref'],r['to_ref'],r.get('task_ref')) for r in patch.get('relationships',[]) if not any(str(r.get(k) or '').startswith('new:') for k in ('from_ref','to_ref','task_ref'))}
def new_count(patch,kind):return sum(1 for x in patch.get(kind,[]) if str(x.get('ref','')).startswith('new:'))

def score(row):
 gold=row['teacher_patch'];pred=row.get('patch') if row.get('valid') else None
 empty={'actor_patch':{},'tasks':[],'decisions':[],'relationships':[]};P=pred or empty
 ge,pe=existing(gold),existing(P)
 _,_,f_touch=prf(set(pe),set(ge));pt,rt,_=prf(set(pe),set(ge))
 both=set(pe)&set(ge)
 status=[pe[k]['changes'].get('status')==ge[k]['changes'].get('status') for k in both if 'status' in ge[k]['changes']]
 rp,rr,rf=prf(rels(P),rels(gold))
 ga,pa=gold.get('actor_patch',{}),P.get('actor_patch',{})
 return {'turn_id':row['turn_id'],'valid':bool(pred),'calls':len(row.get('attempts',[])),
  'actor_state_match':(pa.get('state')==ga.get('state')) if 'state' in ga else None,
  'actor_role_match':(pa.get('role')==ga.get('role')) if 'role' in ga else None,
  'touched_existing_p':pt,'touched_existing_r':rt,'touched_existing_f1':f_touch,
  'status_agreement':(sum(status)/len(status)) if status else None,
  'new_tasks_pred':new_count(P,'tasks'),'new_tasks_gold':new_count(gold,'tasks'),
  'existing_rel_p':rp,'existing_rel_r':rr,'existing_rel_f1':rf}

def summarize(scores):
 def mean(k):
  v=[s[k] for s in scores if s[k] is not None]
  return round(sum(v)/len(v),3) if v else None
 n=len(scores)
 return {'turns':n,'valid_rate':round(sum(s['valid'] for s in scores)/n,3) if n else None,'calls_per_turn':round(sum(s['calls'] for s in scores)/n,2) if n else None,
  **{k:mean(k) for k in ('actor_state_match','actor_role_match','touched_existing_p','touched_existing_r','touched_existing_f1','status_agreement','existing_rel_p','existing_rel_r','existing_rel_f1')},
  'new_tasks_abs_diff_mean':round(sum(abs(s['new_tasks_pred']-s['new_tasks_gold']) for s in scores)/n,2) if n else None,
  'note':'machine-teacher agreement (teacher-forced), not factual quality; invalid predictions scored as empty patches'}

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('dataset_dirs',nargs='+');ap.add_argument('--per-turn',action='store_true');a=ap.parse_args()
 for d in a.dataset_dirs:
  rows=[json.loads(x) for x in (Path(d)/'teacher-forced.jsonl').read_text().splitlines() if x.strip()]
  sc=[score(r) for r in rows];out={'dataset':Path(d).name,**summarize(sc)}
  if a.per_turn:out['per_turn']=sc
  print(json.dumps(out,indent=1))

if __name__=='__main__':main()
