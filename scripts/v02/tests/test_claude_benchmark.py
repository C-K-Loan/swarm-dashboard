import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import benchmark_claude_quota as b

class BenchmarkGuardTests(unittest.TestCase):
 def test_only_pinned_model_without_tools_and_confirmed_no_overage(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'events.jsonl'
   rows=[{'type':'system','subtype':'init','model':b.MODEL,'tools':[],'apiKeySource':'none'},{'type':'rate_limit_event','rate_limit_info':{'isUsingOverage':False,'overageStatus':'rejected'}},{'type':'result','subtype':'success','is_error':False,'modelUsage':{b.MODEL:{}},'subagent_stats':{'spawned':0}}]
   def save():p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
   save();b.receipt(p)
   rows[1]['rate_limit_info']['isUsingOverage']=True;save()
   with self.assertRaisesRegex(RuntimeError,'subscription'):b.receipt(p)
   rows[1]['rate_limit_info']['isUsingOverage']=False;rows[0]['tools']=['Bash'];save()
   with self.assertRaisesRegex(RuntimeError,'model/tools/auth'):b.receipt(p)
 def test_quota_delta_not_compared_across_window_resets(self):
  first={'unifiedWindows':{'seven_day':{'resetsAt':1,'utilization':0.1}}};last={'unifiedWindows':{'seven_day':{'resetsAt':1,'utilization':0.12}}}
  self.assertEqual(b.same_window_delta(first,last,'seven_day'),2)
  last['unifiedWindows']['seven_day']['resetsAt']=2
  self.assertIsNone(b.same_window_delta(first,last,'seven_day'))
if __name__=='__main__':unittest.main()
