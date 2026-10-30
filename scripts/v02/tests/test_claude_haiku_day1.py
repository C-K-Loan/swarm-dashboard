import json,os,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('CCT_V02_DATASET','prototype')
import claude_haiku_day1 as h

TURN={'id':'turn-0001','messages':[{'id':'m1','text':'Plan: build the **terrain** first.'},{'id':'m2','text':'I’ll run the smoke test.'}]}

class Args:max_weekly_pp=3.0;max_five_hour_utilization=0.8;max_weekly_utilization=None

def rate(week=0.02,five=0.1,reset=100,**kw):
 return {'status':'allowed','isUsingOverage':False,'overageStatus':'rejected','unifiedWindows':{'seven_day':{'utilization':week,'resetsAt':reset},'five_hour':{'utilization':five,'resetsAt':5}},**kw}

class RepairTests(unittest.TestCase):
 def patch(self,mid,quote):
  return {'actor_patch':{},'actor_evidence':[{'message_id':mid,'quote':quote}],'tasks':[],'decisions':[],'relationships':[]}
 def test_repoints_only_a_unique_exact_quote_and_keeps_text(self):
  p=self.patch('m1','run the smoke test');r=h.repoint_message_ids(p,TURN)
  self.assertEqual(p['actor_evidence'][0],{'message_id':'m2','quote':'run the smoke test'});self.assertEqual(len(r),1)
 def test_ambiguous_or_absent_quote_is_left_for_the_validator(self):
  turn={'id':'t','messages':[{'id':'a','text':'same words'},{'id':'b','text':'same words'}]}
  p=self.patch('x','same words');self.assertEqual(h.repoint_message_ids(p,turn),[]);self.assertEqual(p['actor_evidence'][0]['message_id'],'x')
  p=self.patch('m1','invented text');self.assertEqual(h.repoint_message_ids(p,TURN),[])
 def test_straight_apostrophe_restores_unique_curly_source_span(self):
  fixed,repairs=h.deterministic_repairs(self.patch('m1',"I'll run the smoke test."),TURN)
  self.assertEqual(fixed['actor_evidence'][0],{'message_id':'m2','quote':'I’ll run the smoke test.'});self.assertEqual(len(repairs),1)
 def test_quote_mark_restore_never_changes_words(self):
  fixed,repairs=h.deterministic_repairs(self.patch('m2',"I'll run the tests."),TURN)
  self.assertEqual(fixed['actor_evidence'][0]['quote'],"I'll run the tests.");self.assertEqual(repairs,[])
 def test_case_only_difference_restores_unique_source_span(self):
  fixed,repairs=h.deterministic_repairs(self.patch('m1','PLAN: build the'),TURN)
  self.assertEqual(fixed['actor_evidence'][0]['quote'],'Plan: build the');self.assertEqual(len(repairs),1)
 def test_over_long_exact_quote_is_split_into_exact_pieces(self):
  src='A'*200+'. '+'B'*200+'.';turn={'id':'t','messages':[{'id':'m','text':src}]}
  fixed,repairs=h.deterministic_repairs(self.patch('m',src),turn)
  qs=[e['quote'] for e in fixed['actor_evidence']]
  self.assertEqual(len(qs),2);self.assertTrue(all(len(q)<=300 and q in src for q in qs))
 def test_misnumbered_list_marker_is_dropped_only_when_rest_is_unique(self):
  turn={'id':'t','messages':[{'id':'m','text':'6) proto/minimap later, 7) wowcod 0.1.0 release when dev publishes.'}]}
  fixed,repairs=h.deterministic_repairs(self.patch('m','6) wowcod 0.1.0 release when dev publishes'),turn)
  self.assertEqual(fixed['actor_evidence'][0]['quote'],'wowcod 0.1.0 release when dev publishes');self.assertEqual(len(repairs),1)
  fixed,repairs=h.deterministic_repairs(self.patch('m','6) invented words here'),turn)
  self.assertEqual(fixed['actor_evidence'][0]['quote'],'6) invented words here');self.assertEqual(repairs,[])
 def test_noop_task_without_evidence_is_dropped_and_supported_change_kept(self):
  p=self.patch('m1','Plan: build the');noop={'ref':'T1','resolution':'existing','changes':{'status':None,'title':None},'evidence':[]}
  real={'ref':'T2','resolution':'existing','changes':{'status':'done'},'evidence':[{'message_id':'m1','quote':'Plan: build the'}]}
  p['tasks']=[noop,real];fixed,repairs=h.deterministic_repairs(p,TURN)
  self.assertEqual(fixed['tasks'],[real]);self.assertEqual(len(repairs),1)
 def test_new_task_citing_only_earlier_turn_text_is_dropped_with_its_links(self):
  p=self.patch('m1','Plan: build the');old='Spawn some research agents'
  p['tasks']=[{'ref':'new:t1','resolution':'new','changes':{'title':'x'},'evidence':[{'message_id':'m1','quote':old}]}]
  p['relationships']=[{'type':'works_on','from_ref':'actor:a','to_ref':'new:t1','evidence':[{'message_id':'m1','quote':'Plan: build the'}]}]
  fixed,repairs=h.deterministic_repairs(p,TURN,['earlier: '+old])
  self.assertEqual((fixed['tasks'],fixed['relationships']),([],[]));self.assertEqual(len(repairs),2)
  fixed,repairs=h.deterministic_repairs(p,TURN,[]);self.assertEqual(len(fixed['tasks']),1)
 def test_one_word_slip_restores_unique_source_span_only(self):
  src='Starting on (1), visible PvP hits. Most of it can be host-side in a new module of mine, driven by replicated state.'
  turn={'id':'t','messages':[{'id':'m','text':src}]}
  fixed,_=h.deterministic_repairs(self.patch('m','Most of it can be the host-side in a new module of mine, driven by replicated state.'),turn)
  self.assertEqual(fixed['actor_evidence'][0]['quote'],'Most of it can be host-side in a new module of mine, driven by replicated state.')
  fixed,r=h.deterministic_repairs(self.patch('m','Most of it can be the client in an old module of mine'),turn)
  self.assertEqual(r,[]);self.assertEqual(fixed['actor_evidence'][0]['quote'],'Most of it can be the client in an old module of mine')
 def test_evidence_free_actor_and_task_updates_are_dropped(self):
  p={'actor_patch':{'state':'blocked'},'actor_evidence':[],'tasks':[{'ref':'T1','resolution':'existing','changes':{'status':'done'},'evidence':[]},{'ref':'new:t1','resolution':'new','changes':{'title':'x'},'evidence':[{'message_id':'m1','quote':'Plan: build the'}]}],'decisions':[],'relationships':[]}
  fixed,repairs=h.deterministic_repairs(p,TURN)
  self.assertEqual(fixed['actor_patch'],{});self.assertEqual([t['ref'] for t in fixed['tasks']],['new:t1']);self.assertEqual(len(repairs),2)
 def test_bold_restore_reuses_luna_policy(self):
  fixed,repairs=h.deterministic_repairs(self.patch('m1','build the terrain first'),TURN)
  self.assertEqual(fixed['actor_evidence'][0]['quote'],'build the **terrain** first');self.assertEqual(len(repairs),1)

class LeanFingerprintTests(unittest.TestCase):
 def test_default_lean_output_still_matches_the_frozen_v03_fingerprint(self):
  self.assertEqual(h.lean_fingerprint(),h.LEAN_V03_FINGERPRINT)

class QuotaTests(unittest.TestCase):
 def test_allows_within_budget(self):self.assertIsNone(h.quota_stop(rate(),rate(week=0.04),Args))
 def test_stops_on_weekly_budget_reset_overage_and_five_hour_ceiling(self):
  self.assertIn('Weekly checkpoint',h.quota_stop(rate(),rate(week=0.05),Args))
  self.assertIn('reset',h.quota_stop(rate(),rate(reset=200),Args))
  self.assertIn('overage',h.quota_stop(rate(),rate(isUsingOverage=True),Args))
  self.assertIn('overage',h.quota_stop(rate(),rate(status='allowed_warning'),Args))
  self.assertIn('Five-hour',h.quota_stop(rate(),rate(five=0.8),Args))
 def test_absolute_weekly_ceiling(self):
  class A(Args):max_weekly_pp=50;max_weekly_utilization=0.09
  self.assertIsNone(h.quota_stop(rate(week=0.04),rate(week=0.08),A))
  self.assertIn('Absolute weekly',h.quota_stop(rate(week=0.04),rate(week=0.09),A))

class InvocationTests(unittest.TestCase):
 def test_environment_drops_parent_session_and_keys(self):
  old=dict(os.environ)
  try:
   os.environ.update(CLAUDE_CODE_SESSION_ID='s',CLAUDECODE='1',ANTHROPIC_API_KEY='k',CCT_X='1',CLAUDE_CODE_OAUTH_TOKEN='t')
   env=h.environment()
   for k in ('CLAUDE_CODE_SESSION_ID','CLAUDECODE','ANTHROPIC_API_KEY','CCT_X'):self.assertNotIn(k,env)
   self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'],'t');self.assertEqual(env['MAX_THINKING_TOKENS'],'0')
  finally:os.environ.clear();os.environ.update(old)
 def test_command_pins_model_without_tools(self):
  c=h.command('SYS');self.assertEqual(c[c.index('--model')+1],h.MODEL);self.assertEqual(c[c.index('--tools')+1],'');self.assertIn('--no-session-persistence',c)
 def test_user_prompt_lists_only_current_ids_and_carries_correction(self):
  ctx={'current_turn_prose':TURN['messages']}
  self.assertIn('["m1", "m2"]',h.user_prompt(ctx));self.assertNotIn('FAILED',h.user_prompt(ctx))
  self.assertIn('FAILED STRICT',h.user_prompt(ctx,'{}','{"errors":[]}'))

if __name__=='__main__':unittest.main()
