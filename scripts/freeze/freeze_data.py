#!/usr/bin/env python3
"""Freeze a curated slice of the dashboard data into static files (no backend).

Reads one data root (this worktree today, swarm-dashboard-release's sanitized staging tree later) and writes:
  <out>/stage/  build-time inputs for the static Astro build (manifests, sliced turn indexes, eval reports)
  <out>/public/ files copied into the static site: sliced /data/*.json plus /frozen/* stand-ins for the API routes
Turn states ship as one full graph per run plus per-turn diffs (state_after is the whole graph, ~0.5 MB a turn).
Local only: nothing here publishes.
"""
import argparse,datetime,json,shutil,sys
from pathlib import Path

def load(p):return json.loads(Path(p).read_text())
def dump(p,obj):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,ensure_ascii=False,separators=(',',':')))
def copy(src,dst):
 dst=Path(dst);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)

def diff(a,b,depth=0):
 """Patch turning a into b: {'o':{k:patch},'x':[deleted]} for dicts, {'a':tail} for appends, {'v':b} otherwise."""
 if isinstance(a,dict) and isinstance(b,dict) and depth<2:
  o={k:diff(a.get(k),v,depth+1) for k,v in b.items() if k not in a or a[k]!=v}
  return {'o':o,'x':[k for k in a if k not in b]}
 if isinstance(a,list) and isinstance(b,list) and len(b)>=len(a) and b[:len(a)]==a:return {'a':b[len(a):]}
 return {'v':b}

def windows(root,cfg):
 turns=load(root/'data/v02'/cfg['day_dataset']/'turns.json');out=[]
 for w in cfg['windows']:
  sl=turns[w['from_turn']-1:w['to_turn']]
  out.append({**w,'start':min(m['ts'] for t in sl for m in t['messages']),'end':max(m['ts'] for t in sl for m in t['messages'])+1,'turn_ids':[t['id'] for t in sl]})
 return out

def inside(ts,wins):return ts is not None and any(w['start']<=ts<w['end'] for w in wins)
def overlaps(a,b,wins):return any((a or 0)<w['end'] and (b or a or 0)>=w['start'] for w in wins)

class Src:
 """Same lookup order as the API routes: <root>/data/<f> first, then ui/public/data/<f>."""
 def __init__(self,*dirs):self.dirs=[Path(d) for d in dirs]
 def __truediv__(self,name):return next((d/name for d in self.dirs if (d/name).exists()),self.dirs[-1]/name)

def slice_ui_data(src,pub,stage,cfg,wins,report):
 tl=load(src/'timeline.json')
 agents=[]
 for a in tl.get('agents',[]):
  if (a.get('fleet') or a.get('fleet_raw'))!=cfg['fleet']:continue
  segs=[s for s in a.get('segments',[]) if overlaps(s.get('start_ts'),s.get('end_ts'),wins)]
  if segs:agents.append({**a,'segments':segs})
 sids={a['id'] for a in agents};titles={a.get('title') for a in agents}
 starts=[s['start_ts'] for a in agents for s in a['segments'] if s.get('start_ts')];ends=[s.get('end_ts') or s['start_ts'] for a in agents for s in a['segments'] if s.get('start_ts')]
 dump(pub/'data/timeline.json',{'schema_v':tl.get('schema_v'),'generated_at':tl.get('generated_at'),'time_range':{'min_ts':min(starts),'max_ts':max(ends),'span_sec':max(ends)-min(starts)},
  'agents':agents,'aggregate':{'agent_count':len(agents),'segment_count':sum(len(a['segments']) for a in agents)},'frozen_slice':[{k:w[k] for k in ('name','start','end')} for w in wins]})
 tx=load(src/'transcript.json');dump(pub/'data/transcript.json',{sid:[m for m in rows if inside(m.get('t'),wins)] for sid,rows in tx.items() if sid in sids})
 MEDIA=('.mp4','.webm','.mov','.gif','.png','.jpg','.jpeg','.webp')
 # Media (game footage, screenshots) stays out unless include_media: drop those artifact rows so no tile points at a missing file.
 keep=lambda r:cfg.get('include_media') or not str(r.get('path','')).lower().endswith(MEDIA)
 for name in ('artifacts.json','file_diffs.json'):
  d=load(src/name);dump(pub/'data'/name,{sid:[r for r in rows if inside(r.get('ts'),wins) and (name!='artifacts.json' or keep(r))] for sid,rows in d.items() if sid in sids})
 bus=load(src/'bus_index.json');data={t:[r for r in rows if inside(r.get('ts'),wins)] for t,rows in bus.get('data',{}).items() if t in titles}
 dump(pub/'data/bus_index.json',{**{k:v for k,v in bus.items() if k!='data'},'receivers':len(data),'rows':sum(map(len,data.values())),'data':data})
 bs={}
 for k,v in load(src/'bucket_summaries.json').items():
  sid,_,start=k.partition('|')
  if sid in sids and start.isdigit() and overlaps(int(start),int(start)+v.get('grain_sec',3600)-1,wins):bs[k]=v
 dump(pub/'data/bucket_summaries.json',bs)
 diffs=load(pub/'data/file_diffs.json');repos={r.get('repo') for rows in diffs.values() for r in rows}
 # Project files only: the fleet's own repo, not shared parents like [REDACTED_PATH] that other fleets also touch.
 tree=load(src/'tree.json');dump(pub/'data/tree.json',{**tree,'repos':{k:v for k,v in tree.get('repos',{}).items() if k in repos and Path(k).name==cfg['fleet']}})
 h=load(src/'timeline_hierarchy.json');a2o={k:v for k,v in h.get('agentToOrg',{}).items() if k in sids}
 dump(pub/'data/timeline_hierarchy.json',{**h,'agentToOrg':a2o,'agentSources':{k:v for k,v in (h.get('agentSources') or {}).items() if k in sids},'orgs':[o for o in h.get('orgs',[]) if o.get('id') in set(a2o.values())]})
 c=load(src/'comms.json');nodes=[n for n in c.get('nodes',[]) if n.get('n') in titles]
 dump(pub/'data/comms.json',{**c,'nodes':nodes,'edges':[e for e in c.get('edges',[]) if e[0] in titles and e[1] in titles]})
 st=load(src/'spawn_tree.json')
 par={k:v for k,v in st.get('parents',{}).items() if k in titles or v in titles}
 dump(pub/'data/spawn_tree.json',{'parents':par,'roots':[r for r in st.get('roots',[]) if r in titles or r in par],'stats':{'agents':len(set(par)|set(par.values())-{None}),'roots':0,'children':sum(v is not None for v in par.values())}})
 for f in cfg['static_files']:
  if (src/f).exists():copy(src/f,pub/'data'/f)
 # training_summary covers several fleets: keep the fleet's own entries only.
 fi=load(src/'fleet_index.json');other={n for f in fi.get('fleets',[]) if f.get('canonical')!=cfg['fleet'] for n in [f.get('canonical')]+list(f.get('sources',[])) if n and cfg['fleet'] not in n}
 bad=lambda x:isinstance(x,str) and any(o in x for o in other)
 def scrub(x):
  if isinstance(x,dict):return {k:scrub(v) for k,v in x.items() if not bad(k)}
  if isinstance(x,list):return [scrub(v) for v in x if not bad(v)]
  return x
 if (src/'training_summary.json').exists():dump(pub/'data/training_summary.json',scrub(load(src/'training_summary.json')))
 # Build-time reads of Layout/timeline frontmatter (ROOT/data): the same slice, plus the fleet list cut to this fleet.
 for f in ('timeline.json','timeline_hierarchy.json','artifacts.json'):copy(pub/'data'/f,stage/'data'/f)
 fi=load(src/'fleet_index.json')
 dump(stage/'data/fleet_index.json',{'fleet_merge':{},'privacy_locked':[],'fleets':[f for f in fi.get('fleets',[]) if f.get('canonical')==cfg['fleet']],'stats':{'total_canonical':1}})
 with open(src/'bucket_summaries.jsonl') as fh,open(stage/'data/bucket_summaries.jsonl','w') as out:
  for line in fh:
   r=json.loads(line) if line.strip() else None
   if r and r.get('sid') in sids and overlaps(r['bucket_start'],r['bucket_start']+r.get('grain_sec',3600)-1,wins):out.write(json.dumps(r,separators=(',',':'))+'\n')
 report['agents']=len(agents)

def freeze_run(root,stage,pub,run,wins,cfg):
 d=root/'data/v02'/run;idx=load(d/'turn-index.json')
 want={tid for w in wins for tid in w['turn_ids']};graph_ids={tid for w in wins if w['state_graph'] for tid in w['turn_ids']}
 rows=[t for t in idx['turns'] if t['id'] in want]
 small={k:v for k,v in idx.items() if k not in ('turns','graph')}
 dump(stage/'data/v02'/run/'turn-index.json',{**small,'turns':rows,'graph':{}})
 if (d/'manifest.json').exists():copy(d/'manifest.json',stage/'data/v02'/run/'manifest.json')
 prev=None;chain=[]
 for t in sorted(rows,key=lambda t:t['ordinal']):
  f=d/'turn-states'/(t['id']+'.json')
  if t['id'] not in graph_ids or not t.get('has_prediction') or not f.exists():continue
  s=load(f)
  if not cfg.get('keep_llm_input',True):s.pop('llm_input',None)
  rec={k:v for k,v in s.items() if k not in ('state_after','task_names')}
  rec['_state']=diff(prev['state_after'],s['state_after']) if prev else {'v':s['state_after']}
  rec['_names']=diff(prev['task_names'],s['task_names']) if prev else {'v':s['task_names']}
  chain.append(rec);prev=s
 # Run picker counts what the demo ships: labeled turns with a state graph / turns in the slice.
 st=load(d/'status.json') if (d/'status.json').exists() else {}
 dump(stage/'data/v02'/run/'status.json',{**st,'completed':len(chain),'total':len(rows),'frozen_slice':True})
 dump(pub/'frozen/v02'/run/'selection.json',{'manifest':idx.get('manifest'),'status':load(d/'status.json') if (d/'status.json').exists() else idx.get('status'),'dataset':run,'turns':rows,'states':chain})
 return len(rows),len(chain)

def main():
 ap=argparse.ArgumentParser(description=__doc__);here=Path(__file__).resolve()
 ap.add_argument('--root',default=str(here.parents[2]),help='data root (worktree or sanitized staging tree)')
 ap.add_argument('--ui-data',default=None,help='ui/public/data dir (default <root>/ui/public/data)')
 ap.add_argument('--config',default=str(here.parent/'slice.json'));ap.add_argument('--out',required=True);a=ap.parse_args()
 root=Path(a.root);cfg=load(a.config);out=Path(a.out);stage,pub=out/'stage',out/'public'
 for p in (stage,pub):shutil.rmtree(p,ignore_errors=True)
 wins=windows(root,cfg);report={'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),'root':str(root),'config':cfg,'windows':[{k:w[k] for k in ('name','start','end','state_graph')} for w in wins]}
 slice_ui_data(Src(root/'data',a.ui_data or root/'ui/public/data'),pub,stage,cfg,wins,report)
 runs=[]
 for r in cfg['v02_runs']:
  n,s=freeze_run(root,stage,pub,r,wins,cfg);runs.append(r);report.setdefault('runs',{})[r]={'turns':n,'states':s}
 dump(stage/'data/v02/active.json',{'dataset':cfg['default_run']})
 d=root/'data/v02'/cfg['default_run']
 for f in ('review.json','training/manifest.json','training/machine_teacher/manifest.json'):
  if (d/f).exists():copy(d/f,stage/'data/v02'/cfg['default_run']/f)
 # API stand-ins: run list, eval summary (computed by the qwen lane's scorers over the full runs), merge trees.
 sys.path.insert(0,str(root/'scripts/v02'));import window_metrics as W,eval_summary as E,io,contextlib
 W.ROOT=E.ROOT=root/'data/v02';buf=io.StringIO()
 with contextlib.redirect_stdout(buf):E.main()
 dump(pub/'frozen/v02/eval-summary.json',json.loads(buf.getvalue()))
 for ds in E.VAL.values():
  if (root/'data/v02'/ds/'manifest.json').exists():copy(root/'data/v02'/ds/'manifest.json',stage/'data/v02'/ds/'manifest.json')
 if (root/'data/v02/_eval/training-cards.json').exists():copy(root/'data/v02/_eval/training-cards.json',stage/'data/v02/_eval/training-cards.json')
 for t in cfg['v01b_trees']:
  f=root/'data/v01b'/t/'merge_tree.json'
  if f.exists():dump(pub/'frozen/v01b'/(t+'.json'),load(f))
 for t in cfg['v01b_reports']:
  for f in (root/'data/v01b'/t).glob('*report*.json'):copy(f,stage/'data/v01b'/t/f.name)
  f=root/'data/v01b'/t/'merge_tree.json'
  if f.exists():dump(stage/'data/v01b'/t/'merge_tree.json',{'meta':load(f).get('meta'),'nodes':{}})
 dump(pub/'frozen/manifest.json',report)
 print(json.dumps({k:report[k] for k in ('windows','runs','agents')}))

if __name__=='__main__':main()
