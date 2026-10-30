"""Read-only Codex quota meter. No provider API requests; conservative shared-account gate."""
import hashlib,json,time
from datetime import datetime
from pathlib import Path
from dataset import DATA
def measure():
 if not (DATA/'quota-config.json').exists():return {'allowed':False,'reason':'No authorized quota configuration for this dataset'}
 config=json.loads((DATA/'quota-config.json').read_text())
 if config.get('mode')=='complete_frozen_day1':
  source=DATA/'turns.json'
  valid=(DATA.name=='day1' and source.exists() and hashlib.sha256(source.read_bytes()).hexdigest()==config.get('source_sha256') and len(json.loads(source.read_text()))==config.get('max_turns')==1902)
  if not valid:return {'allowed':False,'reason':'Full-day authorization does not match the frozen 1902-turn Day1 source'}
 path=Path(config['session_file']);latest=None
 with path.open() as stream:
  for line in stream:
   try:r=json.loads(line)
   except ValueError:continue
   p=r.get('payload',{})
   if p.get('type')=='token_count' and p.get('rate_limits'):latest=r
 if config.get('mode')=='complete_frozen_day1':
  windows=(latest or {}).get('payload',{}).get('rate_limits',{})
  exhausted=any(w and w.get('used_percent',0)>=100 and w.get('resets_at',0)>time.time() for k,w in windows.items() if k in ('primary','secondary'))
  window=windows.get('primary') or {};used=window.get('used_percent')
  return {'allowed':not exhausted,'mode':'complete_frozen_day1','max_turns':1902,'current_used_percent':used,'budget_percentage_points':None,'scope':'Finish frozen Day1 using included local Codex subscription; no paid API or purchased-credit fallback','reported_at':(latest or {}).get('timestamp'),'reason':'Included Codex quota exhausted; wait for reset, no paid fallback' if exhausted else None}
 if not latest:return {'allowed':False,'reason':'No Codex quota telemetry available'}
 rate=latest['payload']['rate_limits'];window=rate.get('primary') or {}
 used=window.get('used_percent');start=config['baseline_used_percent']
 if window.get('window_minutes')!=config.get('window_minutes',10080):return {'allowed':False,'reason':'Quota window duration changed'}
 if used is None or abs((window.get('resets_at') or 0)-config['resets_at'])>60:
  return {'allowed':False,'reason':'Quota window changed or missing; requires a new bounded run'}
 if time.time()-datetime.fromisoformat(latest['timestamp'].replace('Z','+00:00')).timestamp()>300:return {'allowed':False,'reason':'Quota telemetry stale'}
 delta=max(0,used-start)
 return {'allowed':delta<config['stop_delta_percentage_points'],'baseline_used_percent':start,'current_used_percent':used,'consumed_percentage_points':delta,'budget_percentage_points':5,'stop_at_percentage_points':config['stop_delta_percentage_points'],'reserve_percentage_points':1,'scope':'shared-account conservative bound, includes other sessions','reported_at':latest['timestamp'],'tokens':latest['payload'].get('info',{}).get('total_token_usage',{}),'reason':'Quota guard stopped generation' if delta>=config['stop_delta_percentage_points'] else None}
if __name__=='__main__':print(json.dumps(measure(),indent=2))
