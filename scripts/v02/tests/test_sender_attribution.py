import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import sender_attribution as S

def turn(sid,text,ts,kind='human',actor='human'):
 return {'session_id':sid,'actor_id':actor,'kind':kind,'start_ts':ts,'end_ts':ts,'messages':[{'id':f'{sid}{ts}','ts':ts,'text':text}]}
ACTORS=[{'id':'human','name':'CKL','kind':'human'},{'id':'harness','name':'Session harness','kind':'system'},
 {'id':'actor:m','name':'gamejam-master'},{'id':'actor:d','name':'gamejam-dev'},{'id':'actor:t','name':'gamejam-tester'}]

class SenderAttributionTests(unittest.TestCase):
 def setUp(self):
  S._load=lambda name:{}
  self.turns=[turn('m','yo wats cooking?',1),turn('d','[tester] GPU HAZARD',2),turn('d','[Image: original 10x10, displayed at 10x10.]',3),turn('d','please rebuild',4),turn('m','[gamejam-dev] DONE',5),turn('d','x',6,'agent','actor:d')]
  self.actors=[dict(a) for a in ACTORS]
  self.overlay=S.apply(self.turns,self.actors)
 def test_human_only_in_gamejam_master(self):
  self.assertEqual([t['kind'] for t in self.turns],['human','a2a','system','a2a','a2a','agent'])
  self.assertEqual({t['session_id'] for t in self.turns if t['kind']=='human'},{'m'})
 def test_senders_resolve_to_fleet_actors_or_unknown(self):
  self.assertEqual(self.turns[1]['actor_id'],'actor:t');self.assertEqual(self.turns[4]['actor_id'],'actor:d')
  self.assertEqual(self.turns[3]['actor_name'],'unknown sender');self.assertIn(self.turns[3]['actor_id'],{a['id'] for a in self.actors})
 def test_overlay_records_what_changed(self):
  self.assertEqual(set(self.overlay),{1,2,3,4})
  self.assertEqual(self.overlay[2],{'kind':'system','actor_id':'harness','actor_name':'Session harness','why':'harness','was_kind':'human','was_actor_id':'human'})

if __name__=='__main__':unittest.main()
