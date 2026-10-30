#!/usr/bin/env python3
"""Freeze a bounded Gamejam window from provider logs, ordered by turn finish."""
import argparse,json,os,re,hashlib
import sender_attribution
from pathlib import Path
from datetime import datetime,timezone
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[2]
LEGACY=Path('.')
OUT=ROOT/'data/v02/prototype'
def stamp(v):
 try:return datetime.fromisoformat(v.replace('Z','+00:00')).timestamp()
 except (ValueError,AttributeError):return 0

def prepare(hours=1):
 rows=[json.loads(l) for p in (LEGACY/'data/prose').glob('*gamejam*.jsonl') for l in p.open()]
 lo=min(r['ts'] for r in rows);hi=lo+hours*3600
 selected=[r for r in rows if lo<=r['ts']<hi]
 metas={r['session_id']:r for r in selected}
 bundles={}
 for kind in ('projects','fleets'):
  for p in Path(os.environ.get('SESSION_BUNDLE_ROOT','data/bundles'),kind).glob('*gamejam*.json'):
   for s in json.loads(p.read_text()).get('sessions',[]):bundles[s['id']]=s
 actors={'human':{'id':'human','name':'CKL','kind':'human'},'harness':{'id':'harness','name':'Session harness','kind':'system'}}
 for sid,m in metas.items():actors['actor:'+sid]={'id':'actor:'+sid,'session_id':sid,'name':m['agent_title'],'kind':'agent'}
 names={a['name']:a['id'] for a in actors.values()}
 turns=[];excluded=[];paths=[]
 def emit(sid,actor,kind,msgs,finish,boundary):
  if not msgs or not lo<=finish<hi:return
  turns.append({'session_id':sid,'actor_id':actor,'kind':kind,'start_ts':min(x['ts'] for x in msgs),'end_ts':finish,'boundary':boundary,'messages':msgs})
 for sid,meta in metas.items():
  path=Path(bundles.get(sid,{}).get('path','/nonexistent'))
  if not path.is_file():path=next(Path.home().joinpath('.codex/sessions').rglob('*'+sid+'*.jsonl'),path)
  if not path.is_file():path=next((LEGACY/'data/remote-codex').rglob('*'+sid+'*.jsonl'),path)
  if not path.is_file():raise RuntimeError('Missing raw source for '+sid)
  paths.append(str(path));pending=[];seen=set();last_user={}
  aid='actor:'+sid
  for lineno,line in enumerate(path.open(),1):
   try:r=json.loads(line)
   except ValueError:continue
   ts=stamp(r.get('timestamp'));typ=r.get('type')
   if ts<lo:continue
   if ts>=hi:
    # Turns completed beyond the cutoff must not leak future state into the hour.
    if pending:excluded.append({'session_id':sid,'reason':'turn_not_finished_within_window','messages':len(pending)});pending=[]
    break
   if meta['provider']=='codex':
    p=r.get('payload',{})
    if typ=='event_msg' and p.get('type') in ('task_complete','task_completed','turn_aborted'):
     emit(sid,aid,'agent',pending,ts,p['type']);pending=[]
     continue
    if typ!='response_item' or p.get('type')!='message':continue
    role=p.get('role');content=p.get('content',[]);final=p.get('phase')=='final_answer'
   else:
    p=r.get('message',{});role=p.get('role');content=p.get('content',[]);final=p.get('stop_reason')=='end_turn'
    if typ=='system' and r.get('subtype')=='turn_duration':
     emit(sid,aid,'agent',pending,ts,'provider_turn_duration');pending=[]
     continue
   if role not in ('assistant','user'):continue
   text=content if isinstance(content,str) else '\n'.join(c.get('text','') for c in content if isinstance(c,dict) and c.get('type') in ('text','input_text','output_text'))
   if not text.strip():continue
   if role=='user' and re.match(r'^\s*(?:<pasted_content[^>]*>\s*)?(?:<environment_context|<system-reminder|<task-notification|<local-command|<command-|\[Request interrupted)',text):
    excluded.append({'session_id':sid,'reason':'system_message','ts':ts});continue
   mid=r.get('uuid') or 'msg:'+hashlib.sha256(f'{sid}:{lineno}:{text}'.encode()).hexdigest()[:20]
   if mid in seen:continue
   seen.add(mid)
   msg={'id':mid,'ts':ts,'text':text,'source_line':lineno,'source_file':str(path),'role':role}
   if role=='assistant':
    pending.append(msg)
    if final:
     emit(sid,aid,'agent',pending,ts,'provider_end_turn');pending=[]
   else:
    if pending:
     emit(sid,aid,'agent',pending,pending[-1]['ts'],'interrupted_before_next_input');pending=[]
    body=re.sub(r'^\s*<pasted_content[^>]*>\s*','',text)
    match=re.match(r'^\[from ([^\]]+)\]',body)
    if match:
     name=match.group(1);actor=names.get(name,'actor:peer:'+hashlib.sha256(name.encode()).hexdigest()[:12]);kind='a2a'
     actors.setdefault(actor,{'id':actor,'name':name,'kind':'agent'})
    elif re.match(r'^You are\s',body):actor='harness';kind='system'
    else:actor='human';kind='human'
    # Repeated pasted human input during an interrupted send is one instruction.
    digest=hashlib.sha256(text.encode()).hexdigest()
    if kind=='human' and digest in last_user and ts-last_user[digest]<30:
     excluded.append({'session_id':sid,'reason':'duplicate_input_within_30s','message_id':mid});continue
    last_user[digest]=ts
    emit(sid,actor,kind,[msg],ts,'instantaneous_input')
  if pending:excluded.append({'session_id':sid,'reason':'unfinished_source_tail','messages':len(pending)})
 # Human only where CKL types (gamejam-master); other inputs get their real sender. Same classifier as the UI.
 merged=list(actors.values());attribution=sender_attribution.apply(turns,merged)
 for a in merged:actors.setdefault(a['id'],a)
 turns.sort(key=lambda t:(t['end_ts'],0 if t['kind'] in ('human','system','a2a') else 1,t['session_id'],t['messages'][0]['id']))
 for i,t in enumerate(turns):t['id']=f'turn-{i+1:04d}';t['ordinal']=i;t['actor_name']=actors[t['actor_id']]['name']
 root=next((t for t in turns if t['kind']=='human' and 'Warcraft' in t['messages'][0]['text']),None)
 # Root description becomes available only at/after this turn; never future context.
 manifest={'schema_version':'0.2','dataset_id':'gamejam-first-hour-v02' if hours==1 else f'gamejam-first-{hours:g}h-v02','window_start':lo,'window_end':hi,'window_start_iso':datetime.fromtimestamp(lo,timezone.utc).isoformat(),'window_end_iso':datetime.fromtimestamp(hi,timezone.utc).isoformat(),'source_prose_records':len(selected),'turn_count':len(turns),'session_count':len(metas),'actor_count':len(actors),'teacher_model':'gpt-6-luna','teacher_engine':'cct/codex','label_status':'machine_draft','root_turn_id':root['id'] if root else None,'source_files':paths,'excluded':excluded,'ordering':'end_ts, instantaneous-input-first, session_id, message_id','scope':f'first {hours*3600:g} seconds from earliest Gamejam prose; half-open [start,end)','sender_attribution':{'classifier':'ui/src/lib/transcript-roles.mjs','reattributed_inputs':len(attribution)}}
 OUT.mkdir(parents=True,exist_ok=True)
 for name,obj in [('manifest.json',manifest),('actors.json',list(actors.values())),('turns.json',turns)]:
  p=OUT/name
  if p.exists() and p.read_text()!=json.dumps(obj,indent=2,ensure_ascii=False)+'\n':raise RuntimeError('Frozen input already exists and differs: '+name)
  p.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps({k:manifest[k] for k in ['dataset_id','turn_count','session_count','window_start_iso','window_end_iso']}))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--hours',type=float,default=1);ap.add_argument('--out',type=Path,default=OUT);args=ap.parse_args()
 if not 0<args.hours<=24:ap.error('hours must be >0 and <=24')
 OUT=args.out;prepare(args.hours)
