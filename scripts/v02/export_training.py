#!/usr/bin/env python3
"""Prepare local chat-format training data. Never calls an API or starts training."""
import json,hashlib
from pathlib import Path
from pipeline import ROOT,DATA,ledger,read,initial,check,apply,context,atomic

def export():
 records=ledger();turns=read('turns.json');reviews={r['turn_id']:r for r in (json.loads(l) for l in (DATA/'reviews.jsonl').read_text().splitlines())} if (DATA/'reviews.jsonl').exists() else {}
 graph=initial();accepted=[];pairs=[];rubric=(ROOT/'prompts/v02/extract-turn.txt').read_text();stop=None
 for turn,row in zip(turns,records):
  review=reviews.get(turn['id'])
  if not review or review['status'] not in ('approved','corrected'):
   stop=turn['id'];break
  patch=review.get('correction') or row['patch']
  check(patch,turn,graph)
  ctx=context(turn,graph,accepted)
  pairs.append({'messages':[{'role':'system','content':rubric},{'role':'user','content':json.dumps(ctx,ensure_ascii=False)},{'role':'assistant','content':json.dumps(patch,ensure_ascii=False)}]})
  graph,_=apply(graph,patch,turn);accepted.append({**row,'patch':patch})
 out=DATA/'training';out.mkdir(exist_ok=True)
 # Preserve temporal order. A small gap prevents adjacent turns straddling train/eval.
 holdout=max(1,int(len(pairs)*.2)) if len(pairs)>=10 else 0
 gap=min(2,max(0,len(pairs)-holdout-1)) if holdout else 0
 split=len(pairs)-holdout-gap
 partitions={'train':pairs[:split] if holdout else [],'validation':pairs[-holdout:] if holdout else [],'reviewed_all':pairs}
 for name,rows in partitions.items():(out/(name+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
 manifest={'schema_version':'0.2','generated_turns':len(records),'reviewed_contiguous_prefix':len(pairs),'stopped_before_turn':stop,'train_examples':len(partitions['train']),'validation_examples':len(partitions['validation']),'embargo_turns':gap,'training_allowed':False,'reason':'Paid APIs and training disabled by CKL. Export only; review contiguous turns before training.','ready_for_future_training':len(pairs)>=10,'split':'chronological; no future labels in training context','dataset_sha256':hashlib.sha256(json.dumps(pairs,sort_keys=True).encode()).hexdigest()}
 atomic(out/'manifest.json',manifest);return manifest
if __name__=='__main__':print(json.dumps(export(),indent=2))
