#!/usr/bin/env python3
"""Offline final comparison of identical-prompt native CLI receipts; no inference."""
import json
from pathlib import Path
import pipeline as p
import benchmark_claude_quota as b
from run_local_codex import parse_patch

def summarize():
 report=json.loads((b.OUT/'report.json').read_text())
 baseline=[json.loads(l) for l in (b.OUT/'luna-baseline.snapshot.jsonl').read_text().splitlines()]
 turns=p.read('turns.json');graph=p.initial();matched=[];sessions=list(Path.home().joinpath('.codex/sessions').rglob('*.jsonl'))
 for index,row in enumerate(baseline):
  turn=turns[index];tid=turn['id'];hf=b.OUT/(tid+'.receipt.json')
  if index>=86 and hf.exists():
   h=json.loads(hf.read_text());events=sorted((p.DATA/'local-codex-jobs').glob(tid+'-*.events.jsonl'))
   if events and h.get('luna_first_attempt_usage'):
    es=b.events(events[0]);thread=next(e['thread_id'] for e in es if e.get('type')=='thread.started');session=next(f for f in sessions if thread in f.name)
    limits=[r['payload']['rate_limits'] for r in b.events(session) if r.get('payload',{}).get('type')=='token_count' and r['payload'].get('rate_limits')]
    raw=Path(str(events[0]).replace('.events.jsonl','.json'))
    valid=True
    try:p.check(parse_patch(raw.read_text()),turn,graph)
    except Exception:valid=False
    original=Path(str(events[0]).replace('.events.jsonl','.prompt.txt'))
    if original.read_bytes()!=(b.OUT/(tid+'.prompt.txt')).read_bytes():raise ValueError('Prompt mismatch '+tid)
    matched.append({'turn_id':tid,'luna_first_pass_valid':valid,'haiku_first_pass_valid':h['valid'],'luna_quota':limits[-1]['primary'],'haiku_quota':h['quota']})
  graph,_=p.apply(graph,row['patch'],turn)
 result={'status':report['state'],'total_haiku_calls':report['completed_calls'],'fully_lean_matched_turns':len(matched),'same_prompt_bytes_verified':True,'haiku_strict_first_pass':sum(r['haiku_first_pass_valid'] for r in matched),'luna_strict_first_pass':sum(r['luna_first_pass_valid'] for r in matched),'token_totals':report['matched_fully_lean_calls'],'quota_caveat':'Native shared-account rounded meters; different subscription capacities, other sessions and Luna validation retries included. First and last are post-call readings, not isolated model charges.','whole_haiku_usage':report['usage'],'whole_haiku_weekly_delta_pp':report['weekly_delta_pp'],'whole_haiku_five_hour_delta_pp':report['five_hour_delta_pp']}
 if matched:
  first,last=matched[0],matched[-1];a,z=first['luna_quota'],last['luna_quota']
  result.update(first_turn=first['turn_id'],last_turn=last['turn_id'],luna_weekly_start_used_percent=a['used_percent'],luna_weekly_end_used_percent=z['used_percent'],luna_shared_weekly_delta_pp=z['used_percent']-a['used_percent'] if a['resets_at']==z['resets_at'] else None,haiku_shared_weekly_delta_pp=b.same_window_delta(first['haiku_quota'],last['haiku_quota'],'seven_day'),haiku_shared_five_hour_delta_pp=b.same_window_delta(first['haiku_quota'],last['haiku_quota'],'five_hour'))
 p.atomic(b.OUT/'matched-comparison.json',result)
 print(json.dumps(result,indent=2))
 return result
if __name__=='__main__':summarize()
