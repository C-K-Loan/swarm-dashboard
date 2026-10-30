import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import score_teacher_forced as s

GOLD={'actor_patch':{'state':'working'},'tasks':[{'ref':'task:a','resolution':'existing','changes':{'status':'done'}},{'ref':'new:t1','resolution':'new','changes':{}}],'decisions':[],'relationships':[{'type':'works_on','from_ref':'actor:x','to_ref':'task:a'}]}

class ScoreTests(unittest.TestCase):
 def test_perfect_prediction(self):
  r=s.score({'turn_id':'t','teacher_patch':GOLD,'patch':GOLD,'valid':True,'attempts':[1]})
  self.assertEqual((r['touched_existing_f1'],r['status_agreement'],r['existing_rel_f1'],r['actor_state_match']),(1.0,1.0,1.0,True))
 def test_invalid_scores_as_empty(self):
  r=s.score({'turn_id':'t','teacher_patch':GOLD,'patch':GOLD,'valid':False,'attempts':[1,2]})
  self.assertFalse(r['valid']);self.assertEqual(r['touched_existing_r'],0.0);self.assertEqual(r['new_tasks_pred'],0)
 def test_new_refs_excluded_from_relationship_match(self):
  self.assertEqual(s.rels({'relationships':[{'type':'works_on','from_ref':'actor:x','to_ref':'new:t1'}]}),set())

if __name__=='__main__':unittest.main()
