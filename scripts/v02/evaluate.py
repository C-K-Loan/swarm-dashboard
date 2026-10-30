#!/usr/bin/env python3
"""Offline review-aligned metrics. No API calls and no model-as-its-own-reference scores."""
import argparse,json,math,statistics
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[2]
from dataset import DATA

def prf(tp,fp,fn):
 p=tp/(tp+fp) if tp+fp else None;r=tp/(tp+fn) if tp+fn else None
 f=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None
 return {'precision':p,'recall':r,'f1':f,'tp':tp,'fp':fp,'fn':fn}
def ranks(values):
 sorted_values=sorted(enumerate(values),key=lambda v:v[1]);out=[0.0]*len(values);i=0
 while i<len(values):
  j=i+1
  while j<len(values) and sorted_values[j][1]==sorted_values[i][1]:j+=1
  rank=(i+1+j)/2
  for k in range(i,j):out[sorted_values[k][0]]=rank
  i=j
 return out

def spearman(a,b):
 if len(a)<2:return None
 x,y=ranks(a),ranks(b);mx,my=statistics.mean(x),statistics.mean(y)
 denom=math.sqrt(sum((v-mx)**2 for v in x)*sum((v-my)**2 for v in y))
 return sum((u-mx)*(v-my) for u,v in zip(x,y))/denom if denom else None

def macro_f1(pairs):
 if not pairs:return {'macro_f1':None,'accuracy':None,'count':0,'confusion':{}}
 labels={json.dumps(x,sort_keys=True) for pair in pairs for x in pair};scores=[];confusion=defaultdict(lambda:defaultdict(int))
 for p,g in pairs:confusion[str(g)][str(p)]+=1
 for lab in labels:
  tp=sum(json.dumps(p,sort_keys=True)==lab and json.dumps(g,sort_keys=True)==lab for p,g in pairs)
  fp=sum(json.dumps(p,sort_keys=True)==lab and json.dumps(g,sort_keys=True)!=lab for p,g in pairs)
  fn=sum(json.dumps(p,sort_keys=True)!=lab and json.dumps(g,sort_keys=True)==lab for p,g in pairs)
  scores.append(prf(tp,fp,fn)['f1'] or 0)
 return {'macro_f1':statistics.mean(scores),'accuracy':sum(p==g for p,g in pairs)/len(pairs),'count':len(pairs),'confusion':dict(confusion)}

def lines(name):
 p=DATA/name
 return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

def evaluate():
 predictions=lines('predictions.jsonl');reviews={r['turn_id']:r for r in lines('reviews.jsonl')};judges={r['turn_id']:r for r in lines('judge-verdicts.jsonl')}
 totals={k:[0,0,0] for k in ('tasks','relationships','decisions','task_identity')};fields=defaultdict(list);ip=[];ig=[];weighted=[0.,0.,0.];transitions=[];compression=[];human=0;rejected=0;claim_counts=defaultdict(int);readability=defaultdict(list)
 ordinal={'low':1,'medium':2,'high':3}
 for row in predictions:
  review=reviews.get(row['turn_id']);p=row['patch']
  if not review:continue
  if review['status']=='rejected':rejected+=1;continue
  if review['status'] not in ('approved','corrected'):continue
  human+=1;g=review.get('correction') or p
  # Human edits preserve refs for the same task. This is a review audit, not
  # an independent semantic match or held-out candidate benchmark.
  pt={t['ref']:t for t in p['tasks']};gt={t['ref']:t for t in g['tasks']}
  common=pt.keys()&gt.keys();extra=pt.keys()-gt.keys();missing=gt.keys()-pt.keys()
  for i,n in enumerate((len(common),len(extra),len(missing))):totals['tasks'][i]+=n
  for ref in common:
   pc,gc=pt[ref]['changes'],gt[ref]['changes'];w=ordinal.get(gc.get('importance'),1)
   weighted[0]+=w
   for key in ('status','importance','complexity'):
    if key in pc or key in gc:fields['task.'+key].append((pc.get(key,'UNCHANGED'),gc.get(key,'UNCHANGED')))
   if pc.get('importance') in ordinal and gc.get('importance') in ordinal:ip.append(ordinal[pc['importance']]);ig.append(ordinal[gc['importance']])
   pb,gb=pc.get('blocker','UNCHANGED'),gc.get('blocker','UNCHANGED')
   fields['blocker.present'].append(('UNCHANGED' if pb=='UNCHANGED' else isinstance(pb,dict),'UNCHANGED' if gb=='UNCHANGED' else isinstance(gb,dict)))
   if isinstance(pb,dict) or isinstance(gb,dict):
    for k in ('need_human_help','waiting_on_actor'):fields['blocker.'+k].append((pb.get(k) if isinstance(pb,dict) else None,gb.get(k) if isinstance(gb,dict) else None))
   if 'status' in pc or 'status' in gc:transitions.append(pc.get('status','UNCHANGED')==gc.get('status','UNCHANGED'))
   same=pt[ref]['resolution']==gt[ref]['resolution'];totals['task_identity'][0]+=int(same);totals['task_identity'][1]+=int(not same);totals['task_identity'][2]+=int(not same)
  weighted[1]+=sum(ordinal.get(pt[r]['changes'].get('importance'),1) for r in extra)
  weighted[2]+=sum(ordinal.get(gt[r]['changes'].get('importance'),1) for r in missing)
  for key in ('role','state'):
   if key in p['actor_patch'] or key in g['actor_patch']:fields['actor.'+key].append((p['actor_patch'].get(key,'UNCHANGED'),g['actor_patch'].get(key,'UNCHANGED')))
  def relset(obj):return {tuple(r.get(k) for k in ('type','from_ref','to_ref','task_ref')) for r in obj['relationships']}
  for metric,ps,gs in [('relationships',relset(p),relset(g)),('decisions',{d['ref'] for d in p['decisions']},{d['ref'] for d in g['decisions']})]:
   for i,n in enumerate((len(ps&gs),len(ps-gs),len(gs-ps))):totals[metric][i]+=n
  prose='\n'.join(m['text'] for m in row['input']['current_turn_prose']);out=json.dumps(p,ensure_ascii=False)
  compression.append({'source_characters':len(prose),'prediction_characters':len(out),'reference_characters':len(json.dumps(g,ensure_ascii=False))})
  judge=judges.get(row['turn_id'])
  if judge:
   for claim in judge.get('unmatched_claims',[]):
    verdict=claim.get('verdict')
    if verdict in ('SUPPORTED','CONTRADICTED','INSUFFICIENT_EVIDENCE'):claim_counts[verdict]+=1
   for key in ('clarity','organization','conciseness'):
    score=judge.get('readability',{}).get(key)
    if isinstance(score,(int,float)) and 1<=score<=5:readability[key].append(score)
 total_claims=sum(claim_counts.values());source_chars=sum(x['source_characters'] for x in compression);out_chars=sum(x['prediction_characters'] for x in compression);ref_chars=sum(x['reference_characters'] for x in compression)
 return {'schema_version':'0.2','mode':'human_review_audit','reference':'CKL-approved or corrected rows only','matching_basis':'Human-maintained task/decision refs; relationship triples. Free-text equivalence requires independent judge verdicts.','generated_turns':len(predictions),'reviewed_turns':human,'rejected_turns':rejected,'unreviewed_turns':len(predictions)-human-rejected,'metrics':{**{k:prf(*v) for k,v in totals.items()},'importance_weighted_tasks':prf(*weighted),'importance':{'mae':statistics.mean(abs(p-g) for p,g in zip(ip,ig)) if ip else None,'spearman':spearman(ip,ig),'count':len(ip)},'fields':{k:macro_f1(v) for k,v in fields.items()},'state_transition_accuracy':sum(transitions)/len(transitions) if transitions else None,'state_transition_count':len(transitions),'compression':{'source_to_prediction_char_ratio':source_chars/out_chars if out_chars else None,'reference_to_prediction_char_ratio':ref_chars/out_chars if out_chars else None,'estimated_input_tokens':source_chars/4,'estimated_prediction_tokens':out_chars/4,'token_estimate_method':'characters / 4; estimate only, not provider token usage'},'hallucination':{'supported_extra':claim_counts['SUPPORTED'],'contradicted':claim_counts['CONTRADICTED'],'insufficient_evidence':claim_counts['INSUFFICIENT_EVIDENCE'],'reviewed_claims':total_claims,'unsupported_rate':(claim_counts['CONTRADICTED']+claim_counts['INSUFFICIENT_EVIDENCE'])/total_claims if total_claims else None},'readability':{k:statistics.mean(v) if v else None for k,v in readability.items()}},'limits':['Metrics cover only processed and reviewed turns in the active dataset.','Machine drafts are not independent ground truth.','No trained model or held-out generalization score exists yet.','Unmatched supported claims are reference-coverage errors, not hallucinations.','A missing metric means no eligible evidence, not a score of zero.']}
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--stdout',action='store_true');a=ap.parse_args();result=evaluate()
 if a.stdout:print(json.dumps(result))
 else:(DATA/'evaluation.json').write_text(json.dumps(result,indent=2)+'\n');print(DATA/'evaluation.json')
