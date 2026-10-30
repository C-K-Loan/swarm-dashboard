#!/usr/bin/env python3
"""Who sent each v0.2 input turn, via the UI's classifier (ui/src/lib/transcript-roles.mjs, one source).

prepare.py labels every unmarked user turn 'human'; CKL only types into human-facing sessions
(gamejam-master), so other sessions' inputs are re-attributed to the sending agent, the harness, the
spawner or 'unknown sender'. Frozen datasets are never rewritten: --dataset writes an overlay to
data/v02/_sender-attribution/<dataset>.json that the dataset API and exports read.
"""
import argparse,bisect,hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CLI=ROOT/'ui/src/lib/transcript-roles-cli.mjs'
PUBLIC=ROOT/'ui/public/data'
OVERLAYS=ROOT/'data/v02/_sender-attribution'
INPUT_KINDS={'human','system','a2a'}
UNKNOWN='unknown sender'

def _load(name):
 try:return json.loads((PUBLIC/name).read_text())
 except (OSError,ValueError):return {}

def _bus_sender(rows,ts,text,window):
 # Mirrors busSenderFor in transcript-roles.mjs; the bus index is wire-level evidence, not a text rule.
 import re
 key=re.sub(r'^\s*\[from\s+[^\]]+\]\s*','',re.sub(r'^\s*(?:<pasted_content[^>]*>\s*)?','',text or ''),flags=re.I)[:120].strip()[:60]
 if not rows or not key:return None
 i=bisect.bisect_left([r['ts'] for r in rows],ts-window)
 for r in rows[i:]:
  if r['ts']>ts+window:break
  if (r.get('prefix') or '').startswith(key) and r.get('from') and r['from'].lower() not in ('human','ckl'):return r['from']
 return None

def classify(turns,actors):
 """Return {index into turns: {r, a2a, why}} for input turns, in the UI's role vocabulary."""
 names={a['id']:a.get('name') for a in actors}
 parents=_load('spawn_tree.json').get('parents',{});bix=_load('bus_index.json');bus=bix.get('data',{});win=bix.get('window_sec',120)
 first_ts={}
 for t in turns:
  n=names.get('actor:'+t['session_id'])
  if n and t['start_ts']<first_ts.get(n,float('inf')):first_ts[n]=t['start_ts']
 seen=set();rows=[];ids=[]
 for i,t in sorted(enumerate(turns),key=lambda it:it[1]['start_ts']):
  if t['kind'] not in INPUT_KINDS:continue
  title=names.get('actor:'+t['session_id']) or '';p=parents.get(title)
  spawn=p if p and p!=title and first_ts.get(p,float('inf'))<first_ts.get(title,0) else None
  text=t['messages'][0]['text']
  rows.append({'text':text,'first':t['session_id'] not in seen,'spawnParent':spawn,'session':title,'busSender':_bus_sender(bus.get(title),t['messages'][0]['ts'],text,win)})
  seen.add(t['session_id']);ids.append(i)
 out=subprocess.run(['node',str(CLI)],input=json.dumps({'rows':rows}),capture_output=True,text=True,check=True).stdout
 return dict(zip(ids,json.loads(out)))

def actor_for(c,names_to_id):
 """(kind, actor_id, actor_name) a turn should carry for classification c."""
 if c['r']=='u':return 'human','human','CKL'
 if c['r']=='sys' or (c['r']=='spawn' and not c['a2a']):return 'system','harness','Session harness'
 name=c['a2a'] or UNKNOWN;base=name.split('@')[0]
 # Short bracket senders ("tester") resolve to the one fleet actor named "<fleet>-tester".
 suffix=[n for n in names_to_id if n and n.endswith('-'+base)]
 aid=names_to_id.get(name) or names_to_id.get(base) or (names_to_id[suffix[0]] if len(suffix)==1 else None) or 'actor:peer:'+hashlib.sha256(name.encode()).hexdigest()[:12]
 if aid in names_to_id.values() and name not in names_to_id:name=next(n for n,i in names_to_id.items() if i==aid)
 return 'a2a',aid,name

def apply(turns,actors):
 """Rewrite kind/actor in place (actors list gains new peers); returns {turn id or index: change}."""
 names_to_id={a.get('name'):a['id'] for a in actors};by_id={a['id']:a for a in actors};overlay={}
 for i,c in classify(turns,actors).items():
  t=turns[i];tid=t.get('id',i);kind,aid,name=actor_for(c,names_to_id)
  if (kind,aid)==(t['kind'],t['actor_id']):continue
  overlay[tid]={'kind':kind,'actor_id':aid,'actor_name':name,'why':c['why'],'was_kind':t['kind'],'was_actor_id':t['actor_id']}
  if aid not in by_id:by_id[aid]={'id':aid,'name':name,'kind':'agent'};actors.append(by_id[aid])
  t['kind']=kind;t['actor_id']=aid;t['actor_name']=name
 return overlay

def overlay_for(dataset):
 p=OVERLAYS/f'{dataset}.json'
 try:return json.loads(p.read_text()).get('turns',{})
 except (OSError,ValueError):return {}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);a=ap.parse_args()
 d=ROOT/'data/v02'/a.dataset
 turns=json.loads((d/'turns.json').read_text());actors=json.loads((d/'actors.json').read_text())
 before={k:sum(t['kind']==k for t in turns) for k in INPUT_KINDS}
 overlay=apply(turns,actors)  # in memory only; frozen files are not written
 after={k:sum(t['kind']==k for t in turns) for k in INPUT_KINDS}
 title={x['id']:x.get('name') for x in actors}
 human_sessions=sorted({title.get('actor:'+t['session_id'],t['session_id']) for t in turns if t['kind']=='human'})
 OVERLAYS.mkdir(parents=True,exist_ok=True)
 summary={'dataset':a.dataset,'source_turns_sha256':hashlib.sha256((d/'turns.json').read_bytes()).hexdigest(),'classifier':'ui/src/lib/transcript-roles.mjs','before':before,'after':after,'reattributed':len(overlay),'human_sessions':human_sessions}
 tmp=OVERLAYS/f'.{a.dataset}.json.tmp';tmp.write_text(json.dumps({**summary,'turns':overlay},indent=1,ensure_ascii=False)+'\n');os.replace(tmp,OVERLAYS/f'{a.dataset}.json')
 print(json.dumps(summary))
if __name__=='__main__':main()
