import json,os,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('CCT_V02_DATASET','prototype')
import nebius_qwen_day1 as q

class BudgetGuardTests(unittest.TestCase):
 def msgs(self,chars):return [{'role':'user','content':'x'*chars}]
 def test_worst_case_assumes_full_output_and_dense_prompt(self):
  wc=q.worst_case(self.msgs(100_000))
  self.assertAlmostEqual(wc,100_000/q.CHARS_PER_TOKEN*q.PRICE['prompt']+q.MAX_TOKENS*q.PRICE['completion'])
 def test_refuses_a_call_that_could_cross_the_cap(self):
  m=self.msgs(100_000);wc=q.worst_case(m)
  self.assertTrue(q.budget_allows(4.75-wc,m,4.75))
  self.assertFalse(q.budget_allows(4.75-wc+1e-6,m,4.75))
  self.assertFalse(q.budget_allows(5.0,self.msgs(1),4.75))
 def test_cost_uses_reported_usage(self):
  self.assertAlmostEqual(q.cost({'prompt_tokens':1_000_000,'completion_tokens':1_000_000}),0.40)

class LeanContextTests(unittest.TestCase):
 def test_caps_tasks_relationships_and_strips_evidence(self):
  ev=[{'message_id':'m','quote':'q'}]
  tasks=[{'id':'task:%d'%i,'title':'t%d'%i,'status':'done' if i%5==0 else 'progressing','updated_at':i,'evidence':ev} for i in range(60)]
  rels=[{'type':'works_on','from_ref':'actor:a','to_ref':'task:%d'%i,'ts':i,'evidence':ev} for i in range(60)]
  c={'actor_id_and_current_state':{'id':'actor:a'},'relevant_existing_tasks_with_ids':tasks,'relevant_actors_and_relationships':{'actors':[{'id':'actor:a','name':'A','role':'dev'}],'relationships':rels},'recent_relevant_turns':[1,2,3]}
  out=q.lean_context(c)
  self.assertEqual(len(out['relevant_existing_tasks_with_ids']),15)
  self.assertNotIn('evidence',json.dumps(out))
  self.assertTrue(all(t['status']!='done' for t in out['relevant_existing_tasks_with_ids']))
  self.assertEqual(len(out['other_open_tasks_index_id_title_status']),48-15)
  self.assertEqual(len(out['relevant_actors_and_relationships']['relationships']),25)
  self.assertEqual(out['relevant_actors_and_relationships']['actors'],[['actor:a','A']])
  self.assertEqual(out['recent_relevant_turns'],[3])

class SplitQuoteTests(unittest.TestCase):
 def test_splits_long_exact_quote_into_exact_pieces(self):
  src=' '.join('Sentence number %d is here.'%i for i in range(40))
  turn={'messages':[{'id':'m','text':src}]}
  patch={'actor_evidence':[{'message_id':'m','quote':src}],'tasks':[],'decisions':[],'relationships':[]}
  r=q.split_long_quotes(patch,turn)
  self.assertEqual(len(r),1)
  self.assertTrue(all(len(e['quote'])<=300 and e['quote'] in src for e in patch['actor_evidence']))
  self.assertGreater(len(patch['actor_evidence']),1)
 def test_leaves_non_verbatim_quotes_for_the_validator(self):
  turn={'messages':[{'id':'m','text':'short'}]}
  patch={'actor_evidence':[{'message_id':'m','quote':'x'*400}],'tasks':[],'decisions':[],'relationships':[]}
  self.assertEqual(q.split_long_quotes(patch,turn),[])

class ParseFallbackTests(unittest.TestCase):
 def test_double_escaped_quotes_parse_after_fallback(self):
  import claude_haiku_day1 as h
  bad='{"a":"say \\\\"hi\\\\" now"}'
  with self.assertRaises(json.JSONDecodeError):json.loads(bad)
  value,repairs=q.parse(h,bad)
  self.assertEqual(value,{'a':'say "hi" now'});self.assertEqual(len(repairs),1)
 def test_valid_json_is_untouched(self):
  import claude_haiku_day1 as h
  self.assertEqual(q.parse(h,'{"a":1}'),({'a':1},[]))

class TrimPrefixTests(unittest.TestCase):
 def test_trims_garbled_tail_to_exact_prefix(self):
  src='Faction is hostile/neutral/friendly via cmangos FactionTemplate rule both sides, matches vanilla behaviour here.'
  turn={'messages':[{'id':'m','text':src}]}
  patch={'actor_evidence':[{'message_id':'m','quote':src[:60]+'Fault'}],'tasks':[],'decisions':[],'relationships':[]}
  r=q.trim_to_exact_prefix(patch,turn)
  self.assertEqual(len(r),1);self.assertIn(patch['actor_evidence'][0]['quote'],src)
 def test_does_not_touch_paraphrase(self):
  turn={'messages':[{'id':'m','text':'a'*100}]}
  patch={'actor_evidence':[{'message_id':'m','quote':'b'*80}],'tasks':[],'decisions':[],'relationships':[]}
  self.assertEqual(q.trim_to_exact_prefix(patch,turn),[])

if __name__=='__main__':unittest.main()
