import json,os,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('CCT_V02_DATASET','prototype')
import local_qwen_day1 as lq

class Stub:
 def read(self,name):return json.loads((lq.ROOT/'schemas/v02/turn-patch.schema.json').read_text())

class LeanSchemaTests(unittest.TestCase):
 def test_pins_turn_and_message_ids_and_drops_max_length(self):
  s=lq.lean_schema(Stub(),{'id':'turn-0042','messages':[{'id':'m1'},{'id':'m2'}]})
  self.assertEqual(s['properties']['turn_id'],{'const':'turn-0042'})
  self.assertNotIn('maxLength',json.dumps(s))
  ev=s['properties']['tasks']['items']['properties']['evidence']['items']['properties']['message_id']
  self.assertEqual(ev,{'enum':['m1','m2']})
  self.assertEqual(s['properties']['actor_evidence']['items']['properties']['message_id'],{'enum':['m1','m2']})
  self.assertEqual(s['properties']['tasks']['items']['properties']['ref'],{'type':'string'})  # refs stay free; decode_patch is the authority

class QuoteSpanTests(unittest.TestCase):
 def test_spans_are_exact_bounded_substrings(self):
  text='First point here. Second one!\n'+'word '*100+'end.'
  sp=lq.quote_spans(text)
  self.assertIn('First point here.',sp);self.assertIn('Second one!',sp)
  self.assertTrue(all(x in text and 0<len(x)<=300 for x in sp))
 def test_schema_pairs_message_with_its_own_spans(self):
  s=lq.lean_schema(Stub(),{'id':'turn-1','messages':[{'id':'m1','text':'Alpha. Beta.'},{'id':'m2','text':''}]},spans=True)
  ev=s['properties']['actor_evidence']['items']
  self.assertEqual(ev['anyOf'],[{'type':'object','additionalProperties':False,'required':['message_id','quote'],'properties':{'message_id':{'const':'m1'},'quote':{'enum':['Alpha.','Beta.']}}}])

class BudgetTests(unittest.TestCase):
 def test_shared_cap_refuses_worst_case_overrun(self):
  import tempfile,types
  d=tempfile.mkdtemp();a=types.SimpleNamespace(budget_usd=1.0,spend_ledger=d+'/s.json',price_in=1e-6,price_out=1e-5,model='m')
  lq.budget_reserve(a,1000,1000)
  lq.budget_record(a,{'prompt_tokens':100000,'completion_tokens':80000})  # 0.1+0.8=0.9
  with self.assertRaises(lq.BudgetStop):lq.budget_reserve(a,1000,10000)  # 0.9+0.0013+0.1>1
  lq.budget_reserve(a,1000,5000)
  self.assertAlmostEqual(json.loads(Path(a.spend_ledger).read_text())['by_model']['m']['usd'],0.9)

if __name__=='__main__':unittest.main()
