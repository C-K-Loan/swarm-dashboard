#!/usr/bin/env python3
"""Side-by-side teacher-forced val table (score_teacher_forced metrics + rels/turn) for any number of eval datasets.
Usage: val_table.py <label>=<dataset> ... ; rel F1 counts empty-vs-empty as 1.0, so rels/turn is printed next to the teacher's."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import score_teacher_forced as st
R=Path(__file__).resolve().parents[2]/'data/v02'
cols=[a.split('=',1) for a in sys.argv[1:]];out={};teach=None
for label,name in cols:
 rows=[json.loads(x) for x in (R/name/'teacher-forced.jsonl').read_text().splitlines() if x.strip()]
 out[label]=st.summarize([st.score(r) for r in rows])
 out[label]['rels_per_turn']=round(sum(len((r.get('patch') or {}).get('relationships',[])) for r in rows if r.get('valid'))/len(rows),3)
 teach=round(sum(len(r['teacher_patch'].get('relationships',[])) for r in rows)/len(rows),3)
keys=[('turns','rows'),('valid_rate','valid'),('calls_per_turn','calls/turn'),('actor_state_match','actor state'),('actor_role_match','actor role'),('touched_existing_f1','touched-task F1'),('status_agreement','status agree'),('existing_rel_f1','rel F1 existing refs (empty=1.0)'),('new_tasks_abs_diff_mean','new-task count |err|'),('rels_per_turn',f'rels/turn (teacher {teach})')]
print('metric | '+' | '.join(l for l,_ in cols))
for k,n in keys:print(n+' | '+' | '.join(str(out[l][k]) for l,_ in cols))
