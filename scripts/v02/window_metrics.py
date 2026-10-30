#!/usr/bin/env python3
"""Per-dataset rollout metrics over the first N source turns (CKL 2026-10-29: Day1 first 4h = 420 turns).

Counts what each model's own causal rollout emitted: valid share, calls/turn, relationships/turn,
task updates/turn, USD and throughput. Throughput uses the median gap between consecutive calls
(pauses between invocations excluded). Machine output only, not a quality score.
"""
import argparse,collections,datetime,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]/'data/v02'
LOGS=('claude-usage.jsonl','free-usage.jsonl','usage.jsonl')

def ts(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()

def calls_log(d):
 """[(turn_id, generated_at, usd)] per model call, from whichever call log the runner wrote."""
 for name in LOGS:
  f=d/name
  if f.exists():
   out=[]
   for x in f.read_text().splitlines():
    if not x.strip():continue
    r=json.loads(x);u=r.get('usage') or {}
    usd=r.get('list_price_equivalent_usd',u.get('cost',r.get('usd',0))) or 0
    out.append((r['turn_id'],r.get('generated_at'),float(usd)))
   return out
 return None

def metrics(name,n,start=1):
 d=ROOT/name;turns=[t['id'] for t in json.loads((d/'turns.json').read_text())][start-1:n];want=set(turns)
 rows=[json.loads(x) for x in (d/'predictions.jsonl').read_text().splitlines() if x.strip()];rows=[r for r in rows if r['turn_id'] in want]
 k=len(rows)
 if not k:return {'dataset':name,'turns':0}
 log=calls_log(d)
 if log is not None:
  log=[c for c in log if c[0] in want];calls=len(log);usd=sum(c[2] for c in log);stamps=sorted(ts(c[1]) for c in log if c[1])
 else:  # local/TF lean runner: attempts live on the row
  att=[a for r in rows for a in r['inference_provenance'].get('attempts',[])];calls=len(att);usd=sum(a.get('usd',0) for a in att)
  stamps=sorted(ts(r['generated_at']) for r in rows)
 gaps=[b-a for a,b in zip(stamps,stamps[1:]) if b>a]
 sec_per_call=statistics.median(gaps) if gaps else None
 return {'dataset':name,'model':rows[0]['model'],'turns':k,'of':len(turns),
  'valid_pct':round(100*sum(r['label_status']=='machine_draft' for r in rows)/k,1),
  'calls_per_turn':round(calls/k,2),
  'relationships_per_turn':round(sum(len(r['patch'].get('relationships',[])) for r in rows)/k,3),
  'task_updates_per_turn':round(sum(len(r['patch'].get('tasks',[])) for r in rows)/k,3),
  'usd':round(usd,4),
  'turns_per_h_median_gap':round(3600/(sec_per_call*calls/k),1) if sec_per_call else None}

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('datasets',nargs='+');ap.add_argument('--first',type=int,default=420);ap.add_argument('--start',type=int,default=1,help='1-based first turn (eval slice: --start 1523 --first 1902)');a=ap.parse_args()
 for name in a.datasets:print(json.dumps(metrics(name,a.first,a.start)))

if __name__=='__main__':main()
