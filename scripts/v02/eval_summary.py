#!/usr/bin/env python3
"""One JSON for the V0.2 /eval page: base-vs-LoRA val headline, rollout leaderboard, training cards, spend.

Read-only over data/v02. Scores come from the qwen lane's own scorers (score_teacher_forced.py,
window_metrics.py); nothing here is hand-typed except the run roster and the training cards file.
"""
import datetime,functools,json,pathlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import score_teacher_forced as S
import window_metrics as W
ROOT=W.ROOT

VAL={'base':'qwen30b-bf16-val-base-modal','lora':'qwen30b-bf16-val-lora-r16-modal','lora2':'qwen30b-bf16-val-lora-mt2-modal',
 'nemotron':'nemotron-ultra-val-tf'}  # Nemotron Ultra, untuned, $0 NVIDIA build trial route (CKL option B), same exported prompts
# Leaderboard roster: label, host, dataset (None = placeholder). Order is display order.
ROSTER=[
 ('Haiku 4.5 lean','Claude subscription','haiku45-lean-day1'),
 ('Nemotron 3 Ultra lean','OpenRouter free','nemotron-ultra-lean-day1'),
 ('Qwen3.5 4B','local RTX 3090 vLLM','qwen35-4b-base-gamejam'),
 ('Qwen3.5 9B','OpenRouter bf16','qwen35-9b-or-lean-day1-4h'),
 ('Qwen3.5 9B','local RTX 3090 vLLM','qwen35-9b-base-gamejam'),
 ('Qwen3.8 27B','Token Factory','qwen38-27b-tf-lean-day1-4h'),
 ('Qwen3 30B-A3B base','Token Factory','qwen30b-tf-lean-day1-full'),
 ('Qwen3 30B-A3B base','Modal H100 bf16','qwen30b-bf16-base-lean-day1-modal'),
 # Only v1 has a causal rollout; v2 was evaluated teacher-forced only (CKL 2026-10-30: v1 shown only here).
 ('LoRA v1 rollout (v2 rollout not run)','Modal H100 bf16','qwen30b-bf16-lora-r16-lean-day1-modal')]
WINDOWS=[('evalslice','Eval slice 1523-1902',1523,1902),('first4h','Day1 first 4h',1,420),('day1','Full Day1',1,1902)]
LEDGERS='_*-spend-*.json'

def turn_no(tid):return int(str(tid).split('-')[-1])

def train_turns(card):
 """[first,last] source turn any train row was built from, read from the training file itself."""
 f=ROOT/card.get('dataset','')/'train.jsonl'
 if card.get('state')!='done' or not f.is_file():return None
 ids=[turn_no(json.loads(x)['provenance']['turn_id']) for x in f.read_text().splitlines() if x.strip()]
 return [min(ids),max(ids)]

def val_arm(name):
 rows=[json.loads(x) for x in (ROOT/name/'teacher-forced.jsonl').read_text().splitlines() if x.strip()]
 sc=[S.score(r) for r in rows];out=S.summarize(sc);n=len(rows)
 pred=lambda r:(r.get('patch') or {}) if r.get('valid') else {}
 out['rels_per_turn']=round(sum(len(pred(r).get('relationships',[])) for r in rows)/n,3) if n else None
 out['teacher_rels_per_turn']=round(sum(len(r['teacher_patch'].get('relationships',[])) for r in rows)/n,3) if n else None
 # Scorer counts an omitted actor field as a match when the teacher's value is null; break it down.
 actor={}
 for k in ('state','role'):
  c={'teacher_sets':0,'match':0,'omitted':0,'wrong':0,'invalid':0}
  for r in rows:
   g=r['teacher_patch'].get('actor_patch',{})
   if k not in g:continue
   c['teacher_sets']+=1;p=pred(r).get('actor_patch',{}) if r.get('valid') else None
   c['invalid' if p is None else 'omitted' if k not in p else 'match' if p[k]==g[k] else 'wrong']+=1
  actor[k]=c
 out['actor_breakdown']=actor
 # Turns where neither side touches an existing task score F1 = 1; report the turns where the teacher did touch one.
 tt=[x for x,r in zip(sc,rows) if any(t.get('resolution')=='existing' for t in r['teacher_patch'].get('tasks',[]))]
 out['teacher_touched_turns']=len(tt);out['touched_f1_teacher_touched']=round(sum(x['touched_existing_f1'] for x in tt)/len(tt),3) if tt else None
 out['teachers']=sorted({r.get('teacher_model') for r in rows})
 ids=[turn_no(r['turn_id']) for r in rows];out['turn_range']=[min(ids),max(ids)] if ids else None
 out['models']=sorted({r.get('model') for r in rows})
 return out

def leaderboard():
 out=[]
 for label,host,ds in ROSTER:
  row={'label':label,'host':host,'dataset':ds,'windows':{}}
  if ds and (ROOT/ds/'predictions.jsonl').exists():
   man=json.loads((ROOT/ds/'manifest.json').read_text());row['serving']=man.get('serving');row['run_state']=man.get('run_state')
   st=ROOT/ds/'status.json';row['status']=json.loads(st.read_text()) if st.exists() else None
   for key,_,a,b in WINDOWS:row['windows'][key]=W.metrics(ds,b,a)
  out.append(row)
 return out

def spend():
 ledgers=[]
 for f in sorted(ROOT.glob(LEDGERS)):
  d=json.loads(f.read_text())
  ledgers.append({'file':f.name,'provider':'Token Factory' if '_tf-' in f.name else 'OpenRouter' if '_or-' in f.name else '?',
   'usd':d.get('spent_usd'),'calls':d.get('calls'),'by_model':{m:{'usd':v.get('usd'),'calls':v.get('calls')} for m,v in (d.get('by_model') or {}).items()},
   'seeded_from':d.get('seeded_from'),'updated_at':d.get('updated_at')})
 # A seeded ledger re-counts its source's spend for the models it continued; count that spend once.
 by_file={l['file']:l for l in ledgers};total=sum(l['usd'] or 0 for l in ledgers)
 for src in {l['seeded_from'].split()[0] for l in ledgers if l['seeded_from']}:
  continued={m for l in ledgers if (l['seeded_from'] or '').startswith(src) for m in l['by_model']}
  total-=sum(v['usd'] or 0 for m,v in by_file.get(src,{'by_model':{}})['by_model'].items() if m in continued)
 runs=[]
 for label,host,ds in ROSTER:
  if not ds or not (ROOT/ds/'predictions.jsonl').exists():continue
  m=W.metrics(ds,10**6,1);man=json.loads((ROOT/ds/'manifest.json').read_text())
  paid=man.get('paid') or {}
  runs.append({'label':label,'host':host,'dataset':ds,'turns':m.get('turns'),'usd':m.get('usd'),
   'billing':'list price, subscription' if 'claude' in str(man.get('teacher_engine')) else 'GPU-hour' if 'Modal' in host else 'free tier' if 'free' in host else 'own GPU' if 'local' in host else 'per token',
   'budget_usd':paid.get('budget_usd_shared'),'ledger':Path(paid['spend_ledger']).name if paid.get('spend_ledger') else None})
 # Modal bills GPU-hours; the rollouts' first/last row bound the endpoint time (val rows carry no timestamps).
 modal=[]
 for ds in ('qwen30b-bf16-base-lean-day1-modal','qwen30b-bf16-lora-r16-lean-day1-modal'):
  p=ROOT/ds/'predictions.jsonl'
  if p.exists():
   st=[W.ts(r['generated_at']) for r in map(json.loads,filter(str.strip,p.read_text().splitlines())) if r.get('generated_at')]
   if st:modal.append({'dataset':ds,'first':min(st),'last':max(st),'rows':len(st)})
 return {'ledgers':ledgers,'paid_total_usd':round(total,4),'runs':runs,'modal':modal}


def ledger_rates(f):
 """USD per token (input, cache write, cache read, output) solved from a Claude Code ledger's list-price equivalents."""
 import numpy as np
 A,b=[],[]
 for x in f.read_text().splitlines():
  r=json.loads(x) if x.strip() else None
  if not r or not r.get('list_price_equivalent_usd'):continue
  u=r['usage'];A.append([u.get('input_tokens',0),u.get('cache_creation_input_tokens',0),u.get('cache_read_input_tokens',0),u.get('output_tokens',0)]);b.append(r['list_price_equivalent_usd'])
 return [float(v) for v in np.linalg.lstsq(np.array(A,float),np.array(b),rcond=None)[0]]

# Nemotron 3 Ultra: ran on a free NVIDIA trial, priced at its OpenRouter list rate (USD per 1M in/out) so cost compares fairly.
NEMO_LABEL='Nemotron 3 Ultra 550B-A55B (untuned; also a LoRA teacher)';NEMO_RATE=(0.60,2.40);NEMO_NOTE='run on a free NVIDIA trial; priced at the OpenRouter list rate'
S1F=('user_intent','agent_intent','agent_role','blocker_type','urgency')
S1AGENT={'agent_intent','agent_role','blocker_type'}  # agent-message fields; user messages carry user_intent + urgency only
S1MODELS=(('qwen_30b','Qwen3-30B-A3B',True),('qwen_235b_fp8','Qwen3-235B (fp8)',False),('gemma_27b','Gemma-3-27B',False),('qwen_235b','Qwen3-235B',False),('deepseek_flash','DeepSeek-V4-Flash',False),('nemotron_30b','Nemotron-30B',False),
 ('nemotron_ultra',NEMO_LABEL,False),('sonnet','Claude Sonnet',False),('haiku','Claude Haiku',False),('fable','Claude Fable',False))
def stage1_opus(ev):
 """Stage 1 vs the 1000-message Claude Opus 5.5 gold set (nebius-hack-dev data/eval), per field: accuracy + macro-F1, plus all-fields exact match."""
 import collections
 L=lambda p:[json.loads(x) for x in p.read_text().splitlines() if x.strip()]
 g=L(ev/'gold_v2.jsonl');user=[r['_bundle'].startswith('user_') for r in g]
 rows={f:[i for i in range(len(g)) if not(user[i] and f in S1AGENT)] for f in S1F}
 role=lambda v:frozenset(v if isinstance(v,list) else [v] if v else [])
 def field(f,P):
  G=[g[i].get(f) for i in rows[f]];Q=[P[i].get(f) for i in rows[f]]
  if f=='agent_role':  # multi-label: exact set match; macro-F1 over roles
   G,Q=[role(x) for x in G],[role(x) for x in Q];cl=set().union(*G,*Q);hit=lambda a,c:c in a
  else:cl=(set(G)|set(Q))-{None};hit=lambda a,c:a==c  # None counts for accuracy, not as a macro class (as compare_models.py)
  f1=[(lambda tp,fp,fn:2*tp/(2*tp+fp+fn) if tp else 0.0)(sum(hit(a,c) and hit(b,c) for a,b in zip(G,Q)),sum(not hit(a,c) and hit(b,c) for a,b in zip(G,Q)),sum(hit(a,c) and not hit(b,c) for a,b in zip(G,Q))) for c in cl]
  return {'acc':sum(a==b for a,b in zip(G,Q))/len(G),'macro_f1':sum(f1)/len(f1) if f1 else 0.0,'n':len(G)}
 def score(P):
  F={f:field(f,P) for f in S1F};same=lambda i,f:(role if f=='agent_role' else (lambda v:v))(g[i].get(f))==(role if f=='agent_role' else (lambda v:v))(P[i].get(f))
  return {'fields':F,'mean_acc':sum(v['acc'] for v in F.values())/len(F),'mean_macro_f1':sum(v['macro_f1'] for v in F.values())/len(F),
   'all_fields':sum(all(same(i,f) for f in S1F if not(user[i] and f in S1AGENT)) for i in range(len(g)))/len(g)}
 maj={f:collections.Counter(json.dumps(g[i].get(f)) for i in rows[f]).most_common(1)[0][0] for f in S1F}
 out=[{'label':'majority label','baseline':True,**score([{f:json.loads(maj[f]) for f in S1F}]*len(g))}]
 pending=[]
 for slug,lab,prod in S1MODELS:
  p=ev/f'ref_{slug}.jsonl'
  if not p.exists():p=ROOT.parent/'eval'/f'ref_{slug}.jsonl'  # rows produced in this worktree (data/eval), same gold order
  if slug=='nemotron_ultra' and not p.exists():p=next(iter(sorted(ev.glob('ref_nemotron*ultra*.jsonl'))),p)  # producer's file name not fixed yet
  P=L(p) if p.exists() else []
  if len(P)!=len(g) or [r.get('_bundle') for r in P]!=[r['_bundle'] for r in g]:  # rows align by position within bundle
   if slug in ('nemotron_ultra','qwen_235b_fp8'):pending.append({'label':lab,'rows':len(P),'of':len(g)})  # free-cooker run in progress
   continue
  out.append({'label':lab,'production':prod,'claude':lab.startswith('Claude'),'parse_errors':sum(1 for r in P if r.get('_parse_error')),**score(P)})
 return {'reference':'Claude Opus 5.5','n':len(g),'n_agent':len(g)-sum(user),'majority':{f:json.loads(v) for f,v in maj.items()},'models':out,
  'not_run':['Qwen3-14B base','Qwen3-14B LoRA r16','Qwen3-14B LoRA r32'],'pending':pending,'cost':None}

def cost_quality(val,board):
 """Quality vs the frontier reference (reference = 100%) and USD per 1k units, per stage. est=True marks priced estimates."""
 v01b=ROOT.parent/'v01b';ui=ROOT.parents[1]/'ui/public/data'
 if not (ui/'eval/live_results.json').exists():ui=ROOT.parent/'v01-eval'  # public dataset copy of the V0.1 aggregates
 sys.path.insert(0,str(ROOT.parents[1]/'scripts'));from compare_models import PRICING
 tok=lambda i,o,pr:(i*pr[0]+o*pr[1])/1e6  # PRICING is USD per 1M tokens
 haiku=ledger_rates(ROOT/'haiku45-lean-day1/claude-usage.jsonl');opus=ledger_rates(ROOT/'opus45-day1/claude-usage.jsonl')
 def per1k(ds,win='day1'):
  m=next((r['windows'].get(win) for r in board if r['dataset']==ds),None)
  return 1000*m['usd']/m['turns'] if m and m.get('turns') else None
 tf30=json.loads((ROOT/'qwen30b-tf-lean-day1-full/manifest.json').read_text())['paid']
 def tf_val(name):  # teacher-forced arm priced at the Qwen3-30B-A3B Token Factory rate
  R=[json.loads(x) for x in (ROOT/name/'teacher-forced.jsonl').read_text().splitlines() if x.strip()]
  i=sum(a['usage']['prompt_tokens'] for r in R for a in r['attempts']);o=sum(a['usage']['completion_tokens'] for r in R for a in r['attempts'])
  return 1000*(i*tf30['price_in_per_token']+o*tf30['price_out_per_token'])/len(R)
 # All state-graph points on the same 131 held-out turns: Haiku from its ledger for those turns, Qwen arms from their own tokens.
 ids={json.loads(x)['turn_id'] for x in (ROOT/VAL['base']/'teacher-forced.jsonl').read_text().splitlines() if x.strip()}
 # Haiku priced like an API call without caching: every input token at the plain input rate (the harness wrote cache at 2x).
 hu=[json.loads(x) for x in (ROOT/'haiku45-lean-day1/claude-usage.jsonl').read_text().splitlines() if x.strip()]
 hu=[r['usage'] for r in hu if r['turn_id'] in ids]
 h_usd=sum((u.get('input_tokens',0)+u.get('cache_creation_input_tokens',0)+u.get('cache_read_input_tokens',0))*haiku[0]+u.get('output_tokens',0)*haiku[3] for u in hu)
 sg=[{'label':'Haiku 4.5 (reference)','usd':1000*h_usd/len(ids),'q':1.0,'ref':True,'est':True,'note':'same turns, ledger tokens x plain Haiku input/output rate'}]
 ours='lora2' if val.get('lora2',{}).get('turns',0)>=val['lora']['turns'] else 'lora'  # v1 is superseded once v2's val run is complete
 for k,lab in (('base','Qwen3-30B base'),(ours,'LoRA v2' if ours=='lora2' else 'LoRA v1')):
  if k in val and val[k]['turns']>=val['lora']['turns']:sg.append({'label':lab,'usd':tf_val(VAL[k]),'q':val[k]['touched_existing_f1'],'est':True,'note':'own tokens x Qwen3-30B-A3B TF rate'})
 merge=lambda d:json.loads((v01b/d/'merge_tree.json').read_text())['meta']
 agr=json.loads((v01b/'gamejam-day1-haiku/agreement_report.json').read_text())
 LV=('row-4h','row-day','col-1h','col-4h');mean=lambda v:sum(v)/len(v) if v else None
 f1=lambda d:mean([agr['levels'][l][d]['f1'] for l in LV if d in agr['levels'].get(l,{})])
 sf1=lambda d:mean([(lambda x:2*x['recall']*x['strict_precision']/(x['recall']+x['strict_precision']) if x['recall']+x['strict_precision'] else 0)(agr['levels'][l][d]) for l in LV if d in agr['levels'].get(l,{})])
 hm=merge('gamejam-day1-haiku');cu=hm['claude_usage']
 s2=[{'label':'Haiku 4.5 merges (reference)','usd':1000*((cu['in']+cu.get('cache_write',0)+cu.get('cache_read',0))*haiku[0]+cu['out']*haiku[3])/hm['usage']['calls'],'q':1.0,'ref':True,'est':True,'note':'subscription tokens x plain Haiku input/output rate'}]
 q=merge('gamejam-day1-q235b');s2.append({'label':'Qwen3-235B','usd':1000*tok(q['usage']['in'],q['usage']['out'],PRICING['qwen_235b'])/q['usage']['calls'],'q':f1('gamejam-day1-q235b'),'est':True,'note':'tokens x Token Factory list rate'})
 l=merge('gamejam-day1-llama8b');s2.append({'label':'Llama-3.1-8B','usd':1000*l['usage']['usd']/l['usage']['calls'],'q':f1('gamejam-day1-llama8b'),'note':'OpenRouter ledger'})
 s2.append({'label':'Qwen3-8B','usd':None,'q':f1('gamejam-day1'),'note':'Modal GPU-hours, no ledger'})
 nd=next((d.name for d in sorted(v01b.glob('*nemotron*')) if (d/'merge_tree.json').exists()),None);nf=f1(nd) if nd else None
 if nf is not None:nu=merge(nd)['usage'];s2.append({'label':NEMO_LABEL,'usd':1000*tok(nu.get('in',0),nu.get('out',0),NEMO_RATE)/max(1,nu.get('calls',1)),'q':nf,'est':True,'nemo':True,'note':NEMO_NOTE})
 nemo={'stage2':{'status':'done' if nf is not None else 'merged, not scored' if nd else 'pending','dir':nd,'f1':nf}}
 live=json.loads((ui/'eval/live_results.json').read_text()) if (ui/'eval/live_results.json').exists() else {};row=lambda k:next((v for key,v in live.items() if key.replace('baseline_','').startswith(k+'@') and v.get('status')=='real'),None)
 ref30=row('qwen3_30b_a3b') or {'input_tokens':0,'output_tokens':0,'rows_scored':1};ti,to=ref30['input_tokens']/ref30['rows_scored'],ref30['output_tokens']/ref30['rows_scored']
 # The 685 held-out V0.1 labels are Qwen3-30B-A3B pseudo-labels (audit 2026-10-30), not a frontier reference: no headline.
 s1=[]
 base=Path(ui/'compare_v2.json').resolve().parents[3] if (ui/'compare_v2.json').exists() else ROOT.parents[1]
 hold=base/'data/sft/gamejam_holdout.jsonl';maj=None
 if hold.exists():
  import collections
  L=[json.loads(json.loads(x)['messages'][-1]['content']) for x in hold.read_text().splitlines() if x.strip()];F=('user_intent','agent_intent','agent_role','blocker_type','urgency')
  m={f:collections.Counter(json.dumps(l.get(f)) for l in L).most_common(1)[0][0] for f in F};maj=sum(all(json.dumps(l.get(f))==m[f] for f in F) for l in L)/len(L)
 ev=base/'data/eval'
 if not (ev/'gold_v2.jsonl').exists():ev=ROOT.parent/'eval-stage1-gamejam'  # public: the 30-message game-jam subset
 s1opus=stage1_opus(ev) if (ev/'gold_v2.jsonl').exists() else None
 for k,lab in (('qwen3_30b_a3b','Qwen3-30B-A3B'),('qwen3_235b','Qwen3-235B'),('gemma3_27b','Gemma-3-27B'),('nemotron_nano_30b','Nemotron-Nano-30B'),('deepseek_v4','DeepSeek-V4-Flash')):
  r=row(k)
  if r:s1.append({'label':lab,'usd':r['cost_per_1k_rows_usd'],'q':r['exact_match_pct']/100,'macro_f1':r.get('macro_f1_avg'),'note':'Token Factory run'})
 for k,lab,ours in (('qwen3_14b','Qwen3-14B base',False),('qwen14b_r16','Qwen3-14B LoRA r16',True),('qwen14b_r32','Qwen3-14B LoRA r32',True)):
  r=row(k)
  if r:s1.append({'label':lab,'usd':1000*tok(ti,to,PRICING['qwen_sft']),'q':r['exact_match_pct']/100,'macro_f1':r.get('macro_f1_avg'),'est':True,'ours':ours,'note':'own GPU; priced at the SFT placeholder TF rate'})
 for x in sg:x['ours']=x['label'].startswith('LoRA')
 sg[1]['note']='own tokens x Qwen3-30B-A3B TF rate'
 vr=[json.loads(x) for x in (ROOT/VAL['lora2' if 'lora2' in val else 'lora']/'teacher-forced.jsonl').read_text().splitlines() if x.strip()]
 first=lambda r:r['attempts'][0]['usage']['prompt_tokens'] if r['attempts'] and r['attempts'][0].get('usage') else 0
 hout={}
 for x in (ROOT/'haiku45-lean-day1/claude-usage.jsonl').read_text().splitlines():
  r=json.loads(x) if x.strip() else None
  if r and r['turn_id'] in ids and r.get('attempt',1)==1:hout[r['turn_id']]=r['usage'].get('output_tokens',0)
 same=1000*sum(first(r)*haiku[0]+hout.get(r['turn_id'],0)*haiku[3] for r in vr)/len(vr)
 nm=val.get('nemotron');nemo['state']={'status':'done' if nm and nm['turns']>=val['base']['turns'] else 'pending','rows':nm['turns'] if nm else 0,'of':val['base']['turns']}
 if nm and nm['turns']>=val['base']['turns']:
  R=[json.loads(x) for x in (ROOT/VAL['nemotron']/'teacher-forced.jsonl').read_text().splitlines() if x.strip()]
  ni=sum((a.get('usage') or {}).get('prompt_tokens',0) for r in R for a in r['attempts']);no=sum((a.get('usage') or {}).get('completion_tokens',0) for r in R for a in r['attempts'])
  sg.append({'label':NEMO_LABEL,'usd':1000*tok(ni,no,NEMO_RATE)/len(R),'q':nm['touched_existing_f1'],'est':True,'nemo':True,'note':NEMO_NOTE+', thinking off'})
 s2[1]['ours']=True
 def head(pts,ours):
  ref=next(p for p in pts if p.get('ref'));o=next(p for p in pts if p['label']==ours)
  return {'ours':ours,'reference':ref['label'],'quality':o['q']/ref['q'],'cost_saving':1-o['usd']/ref['usd'] if o['usd'] and ref['usd'] else None,'est':bool(o.get('est') or ref.get('est'))}
 stages={'stage1':{'unit':'1k messages','points':s1,'headline':None,'reference':'Qwen3-30B-A3B pseudo-labels','majority_baseline':maj,'opus':s1opus},'nemotron':{**nemo,'stage1':(lambda m,pd:{'status':'done','rows':s1opus['n'],'of':s1opus['n']} if m else {'status':'pending','rows':pd['rows'] if pd else 0,'of':s1opus['n']})(next((x for x in s1opus['models'] if x['label'].startswith('Nemotron 3 Ultra')),None),next((x for x in s1opus['pending'] if x['label'].startswith('Nemotron')),None))},
  'stage2':{'unit':'1k merged nodes','points':s2,'headline':{**head(s2,'Qwen3-235B'),'strict_f1':sf1('gamejam-day1-q235b')}},
  'state':{'unit':'1k turns','points':sg,'headline':head(sg,next(x['label'] for x in sg if x['label'].startswith('LoRA'))),'rels_ratio':(val.get('lora2') or val['lora'])['rels_per_turn']/val['lora']['teacher_rels_per_turn'],'same_prompt_saving':1-next(x['usd'] for x in sg if x['label'].startswith('LoRA'))/same}}
 H=[stages[k]['headline'] for k in ('stage2','state')]
 # Metered inference on record vs not; this is not the project cost (training bill, Modal GPU-hours, subscription runs excluded).
 mt=lambda d:merge(d) if (v01b/d/'merge_tree.json').exists() else None
 q235=[mt(d) for d in ('gamejam-day1-q235b','gamejam-full-q235b') if mt(d)]
 judges=sum(json.loads(f.read_text()).get('judge_usd_est') or 0 for f in v01b.glob('*/judge_report.json'))
 spend={'merge_235b_est':sum(tok(m['usage']['in'],m['usage']['out'],PRICING['qwen_235b']) for m in q235),'merge_llama':l['usage']['usd'],'judges_est':judges,'v01_baselines':sum(v.get('cost_usd') or 0 for v in live.values()),'unledgered':['Qwen3-8B merge (Modal)','LoRA serving (Modal H100)','Token Factory training jobs','subscription runs (Haiku, Sonnet judge)']}
 return {**stages,'overall':{'cost_saving':sum(h['cost_saving'] for h in H)/len(H),'stages':['stage2','state'],'est':any(h['est'] for h in H)},'metered':spend,
  'rates':{'haiku_per_mtok':[round(r*1e6,3) for r in haiku],'opus_per_mtok':[round(r*1e6,3) for r in opus]}}

def main():
 # window_metrics re-reads each run per window; one process reads each file once.
 _read=pathlib.Path.read_text;cached=functools.lru_cache(maxsize=None)(lambda p:_read(p));pathlib.Path.read_text=lambda self,*a,**k:cached(self)
 W.json=type('J',(),{'loads':staticmethod(functools.lru_cache(maxsize=None)(json.loads))})  # rows are only read, never mutated
 cards=ROOT/'_eval/training-cards.json';training=json.loads(cards.read_text()) if cards.exists() else []
 for c in training:c['train_turns']=train_turns(c)
 seen=[c['train_turns'] for c in training if c.get('train_turns')]
 out={'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
  'val':{k:val_arm(v) for k,v in VAL.items() if (ROOT/v/'teacher-forced.jsonl').exists() and (ROOT/v/'teacher-forced.jsonl').read_text().strip()},'val_datasets':VAL,
  'windows':[{'key':k,'label':l,'start':a,'end':b,'overlaps_training':[[max(a,lo),min(b,hi)] for lo,hi in seen if a<=hi and lo<=b]} for k,l,a,b in WINDOWS],'leaderboard':leaderboard(),
  'training':training,'spend':spend()}
 val=out['val']
 if val.get('base') and val.get('lora'):out['cost_quality']=cost_quality(val,out['leaderboard'])
 json.dump(out,sys.stdout,separators=(',',':'))

if __name__=='__main__':main()
