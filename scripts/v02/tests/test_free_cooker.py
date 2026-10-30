import json,os,sys,tempfile,unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('CCT_V02_DATASET','prototype')
import jsonschema
import free_cooker as f

SPEC=f.MODELS['gemma-31b']
TURN={'id':'turn-0001','messages':[{'id':'m1','text':'I’ll build the terrain first.'}]}

def response(**kw):
 base={'model':SPEC['model'],'provider':'Google AI Studio','usage':{'cost':0,'cost_details':{'upstream_inference_cost':0}},'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]}
 base.update(kw);return base

def patch(quote):
 return {'schema_version':'0.2','turn_id':'turn-0001','actor_patch':{},'actor_evidence':[{'message_id':'m1','quote':quote}],'tasks':[],'decisions':[],'relationships':[]}

def fake_pipeline(data):
 def check(p,turn,graph):
  for e in p['actor_evidence']:
   if e['quote'] not in {m['id']:m['text'] for m in turn['messages']}[e['message_id']]:raise ValueError('not verbatim')
 return SimpleNamespace(check=check,jsonschema=jsonschema,DATA=data,ledger=lambda:[])

class LaneTests(unittest.TestCase):
 def test_single_sequential_lane_lean_ultra_then_lean_gemma_31b(self):
  self.assertEqual(f.LANES,{'A':['nemotron-ultra-lean','gemma-31b-lean']})
  self.assertEqual([f.MODELS[k]['dataset'] for k in f.LANES['A']],['nemotron-ultra-lean-day1','gemma4-31b-lean-day1'])
  self.assertEqual([f.contract(f.MODELS[k]) for k in f.LANES['A']],['v0.3-lean']*2)
  self.assertEqual(f.contract(f.MODELS['nemotron-ultra']),f.PROMPT_CONTRACT_VERSION)
  self.assertEqual(set(f.DROPPED),{'nemotron-ultra','gemma-31b','nemotron-super-vl','nemotron-lightning','nemotron-nano-reasoning','gemma-26b'})
  self.assertNotIn('content-safety',json.dumps(f.MODELS))
 def test_cooling_model_is_waited_for_never_skipped(self):
  at=f.utc();soon=at+timedelta(minutes=5)
  state={'models':{'nemotron-ultra-lean':{'state':'cooldown','cooldown_until':soon.isoformat()}}}
  self.assertEqual(f.pick_model('A',state,at),(None,soon))
  self.assertEqual(f.pick_model('A',state,at+timedelta(minutes=6)),('nemotron-ultra-lean',None))
 def test_guard_stop_holds_the_lane_instead_of_skipping_ahead(self):
  at=f.utc();state={'models':{'nemotron-ultra-lean':{'state':'guard_stopped'}}}
  self.assertEqual(f.pick_model('A',state,at),(None,at+timedelta(hours=1)))
 def test_stall_pauses_the_lane_rechecks_then_moves_on_after_grace(self):
  at=f.utc();state={'models':{'nemotron-ultra-lean':{'state':'stalled_validation','stalled_at':at.isoformat(),'cooldown_until':(at+f.STALL_RECHECK).isoformat()}}}
  self.assertEqual(f.pick_model('A',state,at)[0],None)
  self.assertEqual(f.pick_model('A',state,at+f.STALL_RECHECK),('nemotron-ultra-lean',None))
  self.assertEqual(f.pick_model('A',state,at+f.STALL_GRACE),('gemma-31b-lean',None))
 def test_lane_finishes_when_every_model_is_terminal(self):
  state={'models':{k:{'state':'generated'} for k in f.LANES['A']}}
  self.assertEqual(f.pick_model('A',state),(None,None))

class RouteTests(unittest.TestCase):
 def test_request_pins_provider_zero_price_no_fallback(self):
  r=f.make_request(SPEC,'SYS',{'current_turn_prose':TURN['messages']})
  self.assertEqual(r['model'],SPEC['model']);self.assertEqual(r['provider']['only'],['google-ai-studio']);self.assertFalse(r['provider']['allow_fallbacks'])
  self.assertTrue(all(v==0 for v in r['provider']['max_price'].values()));self.assertEqual(r['temperature'],0)
  self.assertIn('["m1"]',r['messages'][1]['content']);self.assertEqual(len(r['messages']),2)
 def test_correction_carries_candidate_and_enum_hint(self):
  r=f.make_request(SPEC,'SYS',{'current_turn_prose':TURN['messages']},'{"x":1}','{"first_error":"{\\"validator\\": \\"enum\\"}","errors":[{"rule":"enum"}]}')
  self.assertEqual(r['messages'][2],{'role':'assistant','content':'{"x":1}'});self.assertIn('enum value was rejected',r['messages'][3]['content'])
 def test_reasoning_only_for_reasoning_models(self):
  self.assertFalse(f.make_request(SPEC,'S',{'current_turn_prose':[]})['reasoning']['enabled'])
  self.assertTrue(f.make_request(f.MODELS['nemotron-nano-reasoning'],'S',{'current_turn_prose':[]})['reasoning']['exclude'])
 def test_audit_rejects_wrong_model_provider_or_any_cost(self):
  f.audit_response(response(),SPEC)
  with self.assertRaisesRegex(f.GuardError,'model'):f.audit_response(response(model='google/gemma-4-31b-it'),SPEC)
  with self.assertRaisesRegex(f.GuardError,'provider'):f.audit_response(response(provider='Other'),SPEC)
  with self.assertRaisesRegex(f.GuardError,'cost'):f.audit_response(response(usage={'cost':0.0001}),SPEC)
  with self.assertRaisesRegex(f.GuardError,'cost'):f.audit_response(response(usage={'cost':0,'cost_details':{'x_cost':1}}),SPEC)
 def test_upstream_outage_is_a_cooldown_not_an_attempt(self):
  with self.assertRaises(f.Cooldown):f.audit_response({'error':{'code':502}},SPEC)
  with self.assertRaises(f.GuardError):f.audit_response({'error':{'code':502},'usage':{'cost':1}},SPEC)
 def test_quota_reset_is_after_next_utc_midnight(self):
  t=f.next_utc_midnight(f.utc());self.assertEqual((t.hour,t.minute),(0,2));self.assertGreater(t,f.utc())

class LedgerTests(unittest.TestCase):
 def test_only_current_contract_jobs_count_toward_the_bound(self):
  with tempfile.TemporaryDirectory() as d:
   jobs=Path(d)
   for i,contract in enumerate(['old',f.PROMPT_CONTRACT_VERSION,f.PROMPT_CONTRACT_VERSION]):
    j=jobs/f'turn-0008-{i}';j.mkdir();(j/'request.json').write_text('{}');(j/'response.json').write_text(json.dumps(response()))
    if contract!='old':(j/'contract.json').write_text(json.dumps({'prompt_contract_version':contract}))
   all_jobs,used=f.attempts_for(jobs,'turn-0008');self.assertEqual((len(all_jobs),used),(3,2))
 def test_reuse_accepts_newest_paid_candidate_that_now_validates_with_recorded_repair(self):
  with tempfile.TemporaryDirectory() as d:
   jobs=Path(d);p=fake_pipeline(jobs)
   good=response(choices=[{'message':{'content':json.dumps(patch("I'll build the terrain"))},'finish_reason':'stop'}])
   bad=response(choices=[{'message':{'content':json.dumps(patch('invented words'))},'finish_reason':'stop'}])
   for name,body in [('turn-0001-1',good),('turn-0001-2',bad)]:(jobs/name).mkdir();(jobs/name/'response.json').write_text(json.dumps(body))
   job,fixed,repairs=f.reuse_paid_candidate(p,sorted(jobs.iterdir()),TURN,{},SPEC)
   self.assertEqual(job.name,'turn-0001-1');self.assertEqual(fixed['actor_evidence'][0]['quote'],'I’ll build the terrain');self.assertEqual(len(repairs),1)
 def test_reuse_ignores_other_models_errors_and_truncated_output(self):
  with tempfile.TemporaryDirectory() as d:
   jobs=Path(d);p=fake_pipeline(jobs);text=json.dumps(patch('build the terrain'))
   for name,body in [('a',response(model='x',choices=[{'message':{'content':text},'finish_reason':'stop'}])),('b',{'error':{'code':502}}),('c',response(choices=[{'message':{'content':text},'finish_reason':'length'}]))]:
    (jobs/name).mkdir();(jobs/name/'response.json').write_text(json.dumps(body))
   self.assertIsNone(f.reuse_paid_candidate(p,sorted(jobs.iterdir()),TURN,{},SPEC))
 def test_config_freezes_and_refuses_drift(self):
  with tempfile.TemporaryDirectory() as d:
   p=SimpleNamespace(DATA=Path(d),ledger=lambda:[])
   v=f.freeze_config(p,SPEC,'SYSTEM');self.assertEqual(v['started_at_turn_index'],0)
   f.freeze_config(p,SPEC,'SYSTEM')
   with self.assertRaises(f.GuardError):f.freeze_config(p,SPEC,'CHANGED SYSTEM')
 def test_continuing_an_older_ledger_records_the_contract_change(self):
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'inference-config.json').write_text(json.dumps({'prompt_contract_version':'current-turn-evidence-v2'}))
   p=SimpleNamespace(DATA=Path(d),ledger=lambda:[{}]*7)
   v=f.freeze_config(p,f.MODELS['nemotron-ultra'],'S');self.assertEqual((v['started_at_turn_index'],v['continues_earlier_contract']),(7,'current-turn-evidence-v2'))

class CliTests(unittest.TestCase):
 def test_only_day1_source_and_supervise_needs_run(self):
  with self.assertRaises(SystemExit):f.main(['cook','--model-key','gemma-31b','--source','gamejam-full-source'])
  with self.assertRaises(SystemExit):f.main(['supervise','--lane','A'])

if __name__=='__main__':unittest.main()

class ActorPrefixTests(unittest.TestCase):
 GRAPH={'actors':{'human':{},'actor:peer:dc2':{}},'tasks':{'task:1':{}}}
 def test_restores_only_a_missing_prefix_of_a_known_actor(self):
  p={'relationships':[{'type':'works_on','from_ref':'peer:dc2','to_ref':'task:1'},{'type':'works_on','from_ref':'peer:zzz','to_ref':'task:1'},{'type':'depends_on','from_ref':'peer:dc2','to_ref':'task:1'}],'decisions':[{'made_by_actor_id':'peer:dc2'}],'tasks':[{'changes':{'blocker':{'waiting_on_actor':'peer:dc2'}}}]}
  r=f.restore_actor_prefixes(p,self.GRAPH)
  self.assertEqual(p['relationships'][0]['from_ref'],'actor:peer:dc2');self.assertEqual(p['relationships'][1]['from_ref'],'peer:zzz')
  self.assertEqual(p['relationships'][2]['from_ref'],'peer:dc2');self.assertEqual(p['decisions'][0]['made_by_actor_id'],'actor:peer:dc2')
  self.assertEqual(p['tasks'][0]['changes']['blocker']['waiting_on_actor'],'actor:peer:dc2');self.assertEqual(len(r),3)
 def test_known_ids_are_untouched(self):
  p={'relationships':[{'type':'works_on','from_ref':'human','to_ref':'task:1'}]}
  self.assertEqual(f.restore_actor_prefixes(p,self.GRAPH),[]);self.assertEqual(p['relationships'][0]['from_ref'],'human')

class AmendmentTests(unittest.TestCase):
 def test_appended_repair_is_logged_but_removal_is_drift(self):
  with tempfile.TemporaryDirectory() as d:
   p=SimpleNamespace(DATA=Path(d),ledger=lambda:[{}]*3)
   f.freeze_config(p,SPEC,'S');cfg=json.loads((Path(d)/'free-cooker-config.json').read_text())
   cfg['deterministic_repairs']=cfg['deterministic_repairs'][:-1];(Path(d)/'free-cooker-config.json').write_text(json.dumps(cfg))
   v=f.freeze_config(p,SPEC,'S');self.assertEqual(v['amendments'][0]['added_repairs'],[f.REPAIRS[-1]]);self.assertEqual(v['amendments'][0]['from_turn_index'],3)
   cfg=json.loads((Path(d)/'free-cooker-config.json').read_text());cfg['deterministic_repairs']=['other']+f.REPAIRS;(Path(d)/'free-cooker-config.json').write_text(json.dumps(cfg))
   with self.assertRaises(f.GuardError):f.freeze_config(p,SPEC,'S')

class SplitQuoteTests(unittest.TestCase):
 SRC='First sentence is here and fairly long. '*12
 def turn(self):return {'id':'t','messages':[{'id':'m','text':self.SRC}]}
 def test_long_verbatim_quote_becomes_consecutive_exact_pieces(self):
  q=self.SRC[:489];p={'actor_evidence':[{'message_id':'m','quote':q}],'tasks':[],'decisions':[],'relationships':[]}
  r=f.split_long_quotes(p,self.turn());pieces=[e['quote'] for e in p['actor_evidence']]
  self.assertEqual(len(r),1);self.assertGreater(len(pieces),1)
  self.assertTrue(all(len(x)<=300 and x in self.SRC for x in pieces))
  self.assertEqual(' '.join(pieces).replace('  ',' ').strip(),q.strip())
 def test_non_verbatim_or_short_quotes_are_untouched(self):
  p={'actor_evidence':[{'message_id':'m','quote':'x'*400},{'message_id':'m','quote':'First sentence'}],'tasks':[],'decisions':[],'relationships':[]}
  self.assertEqual(f.split_long_quotes(p,self.turn()),[]);self.assertEqual(len(p['actor_evidence']),2)

class CorrectionTests(unittest.TestCase):
 REPORT=json.dumps({'first_error':json.dumps({'path':['tasks',0,'evidence',0,'quote'],'validator':'maxLength','constraint':300,'actual_length':489}),'errors':[]})
 def test_first_attempt_greedy_corrections_sampled_with_length_hint(self):
  ctx={'current_turn_prose':TURN['messages']}
  self.assertEqual(f.make_request(SPEC,'S',ctx)['temperature'],0)
  r=f.make_request(SPEC,'S',ctx,'{}',self.REPORT);self.assertEqual(r['temperature'],f.CORRECTION_TEMPERATURE)
  self.assertIn('tasks.0.evidence.0.quote is 489 characters; the limit is 300',r['messages'][-1]['content'])
 def test_identical_greedy_request_and_repeated_answer_are_detected(self):
  with tempfile.TemporaryDirectory() as d:
   jobs=Path(d);req=f.make_request(SPEC,'S',{'current_turn_prose':TURN['messages']})
   for n,body in [('turn-0010-1',response(choices=[{'message':{'content':'{"a": 1}'},'finish_reason':'stop'}])),('turn-0010-2',response(choices=[{'message':{'content':'```json\n{"a":1}\n```'},'finish_reason':'stop'}]))]:
    (jobs/n).mkdir();(jobs/n/'request.json').write_text(json.dumps(req));(jobs/n/'response.json').write_text(json.dumps(body))
   all_jobs=f.jobs_for(jobs,'turn-0010')
   self.assertIn(f.request_digest(req),f.sent_digests(all_jobs,greedy_only=True))
   self.assertEqual(f.previous_candidate_match(all_jobs,all_jobs[1],'{"a":1}'),'turn-0010-1')
   self.assertIsNone(f.previous_candidate_match(all_jobs,all_jobs[1],'{"a":2}'))
 def test_provider_error_envelope_is_not_a_sent_request(self):
  with tempfile.TemporaryDirectory() as d:
   jobs=Path(d);req=f.make_request(SPEC,'S',{'current_turn_prose':TURN['messages']});(jobs/'turn-0089-1').mkdir()
   (jobs/'turn-0089-1'/'request.json').write_text(json.dumps(req))
   (jobs/'turn-0089-1'/'response.json').write_text(json.dumps({'error':{'code':503,'message':'Upstream error from Nvidia: Service temporarily overloaded'}}))
   self.assertEqual(f.sent_digests(f.jobs_for(jobs,'turn-0089'),greedy_only=True),set())
 def test_correction_temperature_is_a_logged_amendment(self):
  with tempfile.TemporaryDirectory() as d:
   p=SimpleNamespace(DATA=Path(d),ledger=lambda:[{}]*10)
   f.freeze_config(p,SPEC,'S');path=Path(d)/'free-cooker-config.json';cfg=json.loads(path.read_text());cfg.pop('correction_temperature');path.write_text(json.dumps(cfg))
   v=f.freeze_config(p,SPEC,'S');self.assertEqual(v['amendments'][-1]['correction_temperature'],{'from':0,'to':f.CORRECTION_TEMPERATURE})

class MarkupTests(unittest.TestCase):
 T={'id':'t','messages':[{'id':'m','text':'Status: **`gamejam-research-wow`** is 10 min deep in crate research.'}]}
 def p(self,q):return {'actor_evidence':[{'message_id':'x','quote':q}],'tasks':[],'decisions':[],'relationships':[]}
 def test_restores_dropped_bold_and_backticks_as_exact_source_span(self):
  p=self.p('gamejam-research-wow is 10 min deep');r=f.restore_markup(p,self.T);e=p['actor_evidence'][0]
  self.assertEqual(e['message_id'],'m');self.assertIn(e['quote'],self.T['messages'][0]['text']);self.assertEqual(e['quote'],'gamejam-research-wow`** is 10 min deep');self.assertEqual(len(r),1)
 def test_changed_words_are_not_repaired(self):
  p=self.p('gamejam-research-wow is 20 min deep');self.assertEqual(f.restore_markup(p,self.T),[])

class CaseTests(unittest.TestCase):
 T={'id':'t','messages':[{'id':'m','text':"ACQUISITION (per CKL's CDN policy): GO. Then build."}]}
 def p(self,q):return {'actor_evidence':[{'message_id':'m','quote':q}],'tasks':[],'decisions':[],'relationships':[]}
 def test_restores_source_case_for_a_unique_match(self):
  p=self.p("Acquisition (per CKL's CDN policy): GO.");r=f.restore_case(p,self.T)
  self.assertEqual(p['actor_evidence'][0]['quote'],"ACQUISITION (per CKL's CDN policy): GO.");self.assertEqual(len(r),1)
 def test_different_words_are_not_repaired(self):
  p=self.p("Acquisition (per CKL's CDN policy): NO.");self.assertEqual(f.restore_case(p,self.T),[])


class LeanTests(unittest.TestCase):
 GRAPH={'actors':{'actor:human':{'id':'actor:human','name':'CKL'},'actor:peer:abc':{'id':'actor:peer:abc','name':'worker'}},
        'tasks':{'task:t-0001-1':{'id':'task:t-0001-1','title':'Build terrain','status':'in_progress'}},
        'relationships':[{'type':'works_on','from_ref':'actor:peer:abc','to_ref':'task:t-0001-1'}]}
 TURN={'id':'turn-0002','actor_id':'actor:peer:abc','messages':[{'id':'m1','text':'I’ll build the terrain first.'}]}
 def setUp(self):
  import lean_context as L
  self.ctx,self.al=L.build(self.TURN,self.GRAPH,'brief')
 def lean_patch(self,ref):
  return {'actor_evidence':[{'message_id':'m1','quote':'I’ll build the terrain first.'}],'tasks':[{'ref':ref,'evidence':[{'message_id':'m1','quote':'build the terrain'}]}],'decisions':[],'relationships':[]}
 def test_aliases_are_decoded_before_the_strict_check(self):
  seen=[]
  p=SimpleNamespace(check=lambda patch,turn,graph:seen.append(patch),jsonschema=jsonschema,DATA=Path('.'))
  t=self.al.to_alias['task:t-0001-1'];out,_=f.validate(p,json.dumps(self.lean_patch(t)),self.TURN,self.GRAPH,'stop',self.al)
  self.assertEqual(out['tasks'][0]['ref'],'task:t-0001-1');self.assertEqual(seen[0]['tasks'][0]['ref'],'task:t-0001-1')
 def test_unknown_alias_is_a_validation_failure_not_a_guess(self):
  p=SimpleNamespace(check=lambda *a:None,jsonschema=jsonschema,DATA=Path('.'))
  with self.assertRaises(ValueError):f.validate(p,json.dumps(self.lean_patch('T99')),self.TURN,self.GRAPH,'stop',self.al)
 def test_feedback_shows_the_model_its_own_aliases(self):
  import openrouter_free as o
  real=[]
  def fake(p,text,turn,graph,error):real.append(text);return 'bad ref task:t-0001-1'
  orig=o.complete_feedback;o.complete_feedback=fake
  try:out=f.feedback(None,json.dumps(self.lean_patch(self.al.to_alias['task:t-0001-1'])),self.TURN,self.GRAPH,ValueError('x'),self.al)
  finally:o.complete_feedback=orig
  self.assertIn('task:t-0001-1',real[0]);self.assertEqual(out,'bad ref '+self.al.to_alias['task:t-0001-1'])
 def test_lean_system_prompt_adds_alias_rules_only_for_lean(self):
  import lean_context as L
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'extract-turn.txt').write_text('X');p=SimpleNamespace(DATA=Path(d),SCHEMA={})
   self.assertIn(L.LEAN_RULES,f.system_prompt(p,f.MODELS['nemotron-ultra-lean']));self.assertNotIn(L.LEAN_RULES,f.system_prompt(p,SPEC))


class StaleUpdateTests(unittest.TestCase):
 T={'id':'turn-0010','messages':[{'id':'m10','text':'Theme decided: native Rust FPS.'}]}
 OLD=['read [REDACTED_PATH] abnd  [REDACTED_PATH] --> talk']
 def p(self,res,q):return {'tasks':[{'ref':'task:x','resolution':res,'evidence':[{'message_id':'m10','quote':q}]},{'ref':'task:y','resolution':'existing','evidence':[{'message_id':'m10','quote':'native Rust FPS'}]}]}
 def test_existing_update_citing_only_earlier_turn_text_is_dropped(self):
  p=self.p('existing','read [REDACTED_PATH] abnd  [REDACTED_PATH]');r=f.drop_stale_task_updates(p,self.T,self.OLD)
  self.assertEqual([x['ref'] for x in p['tasks']],['task:y']);self.assertEqual(r[0]['dropped_task_update'],'task:x')
 def test_new_tasks_and_unknown_text_are_never_dropped(self):
  for res,q in [('new','read [REDACTED_PATH]'),('existing','text found nowhere')]:
   p=self.p(res,q);self.assertEqual(f.drop_stale_task_updates(p,self.T,self.OLD),[]);self.assertEqual(len(p['tasks']),2)


class ResumeTests(unittest.TestCase):
 def test_resume_skips_a_trailing_provider_error_job(self):
  with tempfile.TemporaryDirectory() as d:
   jobs=Path(d)
   for n,body in [('turn-0011-1',response()),('turn-0011-2',{'error':{'code':504}})]:
    (jobs/n).mkdir();(jobs/n/'response.json').write_text(json.dumps(body))
   self.assertEqual(f.last_answered_job(f.jobs_for(jobs,'turn-0011')).name,'turn-0011-1')
   self.assertIsNone(f.last_answered_job(f.jobs_for(jobs,'turn-0011')[1:]))


class ElisionTests(unittest.TestCase):
 T={'id':'t','messages':[{'id':'m','text':'Deliverable: docs/X.md. Cover: 1. Extraction toolchain (Greyhound). 2. Which title fits best. Send one message.'}]}
 def p(self,q):return {'actor_evidence':[{'message_id':'m','quote':q}],'tasks':[],'decisions':[],'relationships':[]}
 def test_elided_quote_becomes_exact_pieces(self):
  p=self.p('Deliverable: docs/X.md. Cover: 1. Extraction toolchain... 2. Which title fits... Send one message.');r=f.split_elided_quotes(p,self.T)
  self.assertEqual([e['quote'] for e in p['actor_evidence']],['Deliverable: docs/X.md. Cover: 1. Extraction toolchain','2. Which title fits','Send one message.']);self.assertEqual(len(r),1)
 def test_a_changed_piece_blocks_the_repair(self):
  p=self.p('Deliverable: docs/X.md... 2. Which game fits');self.assertEqual(f.split_elided_quotes(p,self.T),[]);self.assertEqual(len(p['actor_evidence']),1)

 def test_whitespace_only_difference_restores_the_exact_span(self):
  T={'id':'t','messages':[{'id':'m','text':'Cover:\n1. Extraction toolchain: Greyhound. 2. Which title fits.'}]}
  p=self.p('Cover: 1. Extraction toolchain... 2. Which title fits');f.split_elided_quotes(p,T)
  self.assertEqual([e['quote'] for e in p['actor_evidence']],['Cover:\n1. Extraction toolchain','2. Which title fits'])


class OmissionTests(unittest.TestCase):
 SRC='NEXT TASK: build a prototype at /p/skate/ (own package; reuse your scratch target; do NOT use wowcod/target, dev is building there). Do NOT touch wowcod/. '+'x'*250
 T={'id':'t','messages':[{'id':'m','text':SRC}]}
 def p(self,q):return {'actor_evidence':[{'message_id':'m','quote':q}],'tasks':[],'decisions':[],'relationships':[]}
 def test_unmarked_omission_in_a_long_quote_becomes_exact_ordered_pieces(self):
  q='NEXT TASK: build a prototype at /p/skate/ (own package; reuse your scratch target; do NOT use wowcod/target). Do NOT touch wowcod/. '+'filler words here, '*12
  p=self.p(q);f.split_unmarked_omissions(p,self.T)
  self.assertEqual(len(p['actor_evidence']),1)  # filler is not source text: refused
  q2='NEXT TASK: build a prototype at /p/skate/ (own package; reuse your scratch target; do NOT use wowcod/target). Do NOT touch wowcod/. '+'x'*250
  p=self.p(q2);r=f.split_unmarked_omissions(p,self.T)
  self.assertTrue(r);self.assertTrue(all(e['quote'] in self.SRC for e in p['actor_evidence']))
  self.assertIn('do NOT use wowcod/target',[e['quote'] for e in p['actor_evidence']])
 def test_out_of_order_pieces_are_refused(self):
  q='Do NOT touch wowcod/. NEXT TASK: build a prototype at /p/skate/ (own package; '+'x'*250
  p=self.p(q);self.assertEqual(f.split_unmarked_omissions(p,self.T),[])


class SmallGapTests(unittest.TestCase):
 T={'id':'t','messages':[{'id':'m','text':'- [S4] recoil recovery overshoots at the pitch clamp (accum adds climb); worth documenting.'}]}
 def p(self,q):return {'actor_evidence':[{'message_id':'m','quote':q}],'tasks':[],'decisions':[],'relationships':[]}
 def test_dropped_article_becomes_two_exact_pieces(self):
  p=self.p('recoil recovery overshoots at pitch clamp (accum adds climb)');r=f.split_small_gap(p,self.T)
  self.assertEqual([e['quote'] for e in p['actor_evidence']],['recoil recovery overshoots at','pitch clamp (accum adds climb)']);self.assertEqual(r[0]['skipped_source_words'],['the'])
 def test_changed_words_or_short_halves_are_refused(self):
  for q in ['recoil recovery undershoots at pitch clamp (accum adds climb)','overshoots at pitch clamp']:
   p=self.p(q);self.assertEqual(f.split_small_gap(p,self.T),[]);self.assertEqual(len(p['actor_evidence']),1)


class JsonCloserTests(unittest.TestCase):
 def test_mismatched_closer_is_swapped_and_strings_are_untouched(self):
  bad='{"evidence": [{"quote": "a } ] b"}}, "x": 1}'
  fixed,swaps=f.swap_mismatched_closers(bad)
  self.assertEqual(json.loads(fixed),{'evidence':[{'quote':'a } ] b'}],'x':1});self.assertEqual(len(swaps),1)
 def test_valid_json_is_unchanged(self):
  ok='{"a": [1, {"b": "]"}]}';self.assertEqual(f.swap_mismatched_closers(ok),(ok,[]))


class SegmentTests(unittest.TestCase):
 SRC='Brief: report to master the exact host-side cause (n.fire with P.fire excluding it).\nLater: send it with the exact host-side cause (n.fire + P.fire excluding it) and the crate fix. Then resume. '+'z'*250
 T={'id':'t','messages':[{'id':'m','text':SRC}]}
 def p(self,q):return {'actor_evidence':[{'message_id':'m','quote':q}],'tasks':[],'decisions':[],'relationships':[]}
 def test_join_of_two_exact_passages_is_segmented_without_dropping_words(self):
  q='Brief: report to master the exact host-side cause (n.fire + P.fire excluding it) and the crate fix. Then resume. '+'z'*250
  p=self.p(q);r=f.split_unmarked_omissions(p,self.T);qs=[e['quote'] for e in p['actor_evidence']]
  self.assertTrue(r);self.assertTrue(all(x in self.SRC for x in qs));self.assertEqual(' '.join(qs).split(),q.split())
 def test_a_reworded_word_blocks_segmentation(self):
  q='Brief: report to master the exact host-side cause (n.fire PLUS P.fire excluding it) and the crate fix. '+'z'*250
  p=self.p(q);self.assertEqual(f.split_unmarked_omissions(p,self.T),[])


class SelfQuoteTests(unittest.TestCase):
 T={'id':'t','messages':[{'id':'m','text':'record_demo.sh is shipped.'}]}
 def test_update_citing_only_its_own_wording_is_dropped(self):
  p={'tasks':[{'ref':'task:a','resolution':'existing','changes':{'blocker':{'reason':'All testing now headless. More.'}},'evidence':[{'message_id':'m','quote':'All testing now headless.'}]},
              {'ref':'task:b','resolution':'existing','changes':{},'evidence':[{'message_id':'m','quote':'record_demo.sh is shipped.'}]}]}
  r=f.drop_self_quoted_task_updates(p,self.T);self.assertEqual([x['ref'] for x in p['tasks']],['task:b']);self.assertEqual(r[0]['dropped_task_update'],'task:a')
 def test_new_tasks_and_non_self_quotes_are_kept(self):
  for res,q in [('new','All testing now headless.'),('existing','some other words')]:
   p={'tasks':[{'ref':'x','resolution':res,'changes':{'summary':'All testing now headless.'},'evidence':[{'message_id':'m','quote':q}]}]}
   self.assertEqual(f.drop_self_quoted_task_updates(p,self.T),[]);self.assertEqual(len(p['tasks']),1)


class MarkupTypographyTests(unittest.TestCase):
 SRC='Menu’s first autoplay passed: `MENU_OK`, exit 0, four settings changes. The MP4 is 53.6 seconds.'
 T={'id':'t','messages':[{'id':'m','text':SRC}]}
 def p(self,q):return {'tasks':[{'ref':'task:x','evidence':[{'message_id':'m','quote':q}]}]}
 def test_curly_apostrophe_and_backticks_restored_together(self):
  p=self.p("Menu's first autoplay passed: MENU_OK, exit 0, four settings changes.")
  self.assertEqual(f.restore_markup(p,self.T),[]);r=f.restore_markup_typography(p,self.T)
  self.assertEqual(p['tasks'][0]['evidence'][0]['quote'],'Menu’s first autoplay passed: `MENU_OK`, exit 0, four settings changes.');self.assertEqual(len(r),1)
 def test_changed_words_are_not_repaired(self):
  p=self.p("Menu's first autoplay failed: MENU_OK, exit 0.");self.assertEqual(f.restore_markup_typography(p,self.T),[])


class MarkupWhitespaceTests(unittest.TestCase):
 SRC='I resumed **wowfmt-dev** on Opus to:\n\n1. **Find each chain** in the data.\n2. Next.'
 T={'id':'t','messages':[{'id':'m','text':SRC}]}
 def p(self,q):return {'tasks':[{'ref':'task:x','evidence':[{'message_id':'m','quote':q}]}]}
 def test_markup_and_collapsed_blank_line_restored(self):
  p=self.p('I resumed wowfmt-dev on Opus to: 1. Find each chain in the data.')
  self.assertEqual(f.restore_markup(p,self.T),[]);self.assertEqual(len(f.restore_markup_whitespace(p,self.T)),1)
  self.assertEqual(p['tasks'][0]['evidence'][0]['quote'],'I resumed **wowfmt-dev** on Opus to:\n\n1. **Find each chain** in the data.')
 def test_changed_words_are_not_repaired(self):
  p=self.p('I resumed wowfmt-dev on Sonnet to: 1. Find each chain');self.assertEqual(f.restore_markup_whitespace(p,self.T),[])


class ElidedMarkupTests(unittest.TestCase):
 T={'id':'t','messages':[{'id':'m','text':'Plan:\n\n1. **Find each chain** in the data.\n2. **Teach our reader** particles.'}]}
 def p(self,q):return {'tasks':[{'ref':'task:x','evidence':[{'message_id':'m','quote':q}]}]}
 def test_elided_pieces_without_markup_restored(self):
  p=self.p('1. Find each chain... 2. Teach our reader particles.')
  self.assertEqual(f.split_elided_quotes(p,self.T),[]);self.assertEqual(len(f.split_elided_quotes(p,self.T,span=f.markup_span)),1)
  self.assertEqual([e['quote'] for e in p['tasks'][0]['evidence']],['1. **Find each chain','2. **Teach our reader** particles.'])
 def test_changed_words_are_not_repaired(self):
  p=self.p('1. Find every chain... 2. Teach our reader particles.');self.assertEqual(f.split_elided_quotes(p,self.T,span=f.markup_span),[])


class DecisionTaskRefTests(unittest.TestCase):
 def p(self,ref,typ='delegates_to'):
  return {'tasks':[{'ref':'new:t1'}],'decisions':[{'ref':'new:d1'}],'relationships':[{'type':typ,'from_ref':'actor:a','to_ref':'actor:b','task_ref':ref}]}
 def test_delegation_pointing_at_a_decision_is_dropped_and_decision_kept(self):
  p=self.p('new:d1');r=f.drop_decision_task_ref_relationships(p)
  self.assertEqual(r[0]['task_ref'],'new:d1');self.assertEqual(p['relationships'],[]);self.assertEqual(p['decisions'],[{'ref':'new:d1'}])
 def test_task_refs_unknown_refs_and_other_types_are_kept(self):
  for ref,typ in [('new:t1','delegates_to'),('task:zzz','delegates_to'),('new:d1','reviews')]:
   p=self.p(ref,typ);self.assertEqual(f.drop_decision_task_ref_relationships(p),[]);self.assertEqual(len(p['relationships']),1)


class ActorEndpointEdgeTests(unittest.TestCase):
 G={'actors':{'actor:a':{},'actor:b':{}}}
 def p(self,typ,a,b):return {'tasks':[{'ref':'new:t1'},{'ref':'new:t2'}],'relationships':[{'type':typ,'from_ref':a,'to_ref':b}]}
 def test_task_edge_to_an_actor_is_dropped(self):
  for typ,a,b in [('depends_on','new:t1','actor:b'),('is_sub_task','actor:a','new:t2')]:
   p=self.p(typ,a,b);self.assertEqual(len(f.drop_actor_endpoint_task_edges(p,self.G)),1);self.assertEqual(p['relationships'],[]);self.assertEqual(len(p['tasks']),2)
 def test_task_edges_and_actor_edges_are_kept(self):
  for typ,a,b in [('depends_on','new:t2','new:t1'),('works_on','actor:a','new:t1')]:
   p=self.p(typ,a,b);self.assertEqual(f.drop_actor_endpoint_task_edges(p,self.G),[]);self.assertEqual(len(p['relationships']),1)


class AnchoredStaleTests(unittest.TestCase):
 T={'id':'t','messages':[{'id':'m','text':'The release build is still compiling and nothing shipped.'}]}
 OLD=["0.3.0 status, as sent to master:\n- **In it:** remote players get the WoW body, since the playerbody feature is on."]
 def p(self,res,q):return {'tasks':[{'ref':'task:x','resolution':res,'evidence':[{'message_id':'m','quote':q}]}]}
 def test_garbled_recitation_of_earlier_text_drops_an_existing_update(self):
  p=self.p('existing','0.3.0 status: In it: remote players get the WoW body, since the playerbody feature is on.')
  self.assertEqual(f.drop_anchored_stale_task_updates(p,self.T,self.OLD)[0]['dropped_task_update'],'task:x');self.assertEqual(p['tasks'],[])
 def test_new_tasks_short_runs_and_current_quotes_are_kept(self):
  for res,q in [('new','0.3.0 status: In it: remote players get the WoW body, since the playerbody feature is on.'),
                ('existing','remote players get the WoW body today, really.'),('existing','The release build is still compiling and nothing shipped.')]:
   p=self.p(res,q);self.assertEqual(f.drop_anchored_stale_task_updates(p,self.T,self.OLD),[]);self.assertEqual(len(p['tasks']),1)


class JoinedStaleTests(unittest.TestCase):
 T={'id':'t','messages':[{'id':'m','text':'Summary of the earlier conversation.'}]}
 OLD=['qa2 proto/progression PASS, soak clean today.','Progression passes QA with a clean soak now.']
 def p(self,res,q):return {'tasks':[{'ref':'task:x','resolution':res,'evidence':[{'message_id':'m','quote':q}]}]}
 def test_joined_earlier_passages_drop_an_existing_update(self):
  p=self.p('existing','qa2 proto/progression PASS, soak clean today. Progression passes QA with a clean soak now.')
  self.assertEqual(f.drop_joined_stale_task_updates(p,self.T,self.OLD)[0]['dropped_task_update'],'task:x');self.assertEqual(p['tasks'],[])
 def test_new_tasks_or_uncoverable_quotes_are_kept(self):
  for res,q in [('new','qa2 proto/progression PASS, soak clean today.'),('existing','qa2 proto/progression FAIL, invented words here.')]:
   p=self.p(res,q);self.assertEqual(f.drop_joined_stale_task_updates(p,self.T,self.OLD),[]);self.assertEqual(len(p['tasks']),1)
