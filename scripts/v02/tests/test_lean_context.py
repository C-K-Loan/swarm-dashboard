import json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lean_context as L

ME='actor:me';PEER='actor:peer';T_OPEN='task:open1';T_PARENT='task:parent';T_DONE='task:done1';T_OTHER='task:other'
EV=[{'message_id':'m0','quote':'old evidence must never be sent'}]

def graph():
 return {'actors':{'human':{'id':'human','name':'CKL','kind':'human'},ME:{'id':ME,'name':'gamejam-dev','role':'dev','state':'working'},PEER:{'id':PEER,'name':'gamejam-tester','kind':'agent'},'actor:quiet':{'id':'actor:quiet','name':'zz-quiet-agent'}},
  'tasks':{T_OPEN:{'id':T_OPEN,'title':'Build terrain','status':'progressing','summary':None,'importance':'high','blocker':{'reason':'needs GPU','need_human_help':None,'waiting_on_actor':PEER},'evidence':EV,'updated_at':5},
   T_PARENT:{'id':T_PARENT,'title':'Ship M1','status':'waiting','evidence':EV,'updated_at':3},
   T_DONE:{'id':T_DONE,'title':'Old fix','status':'done','evidence':EV,'updated_at':9},
   T_OTHER:{'id':T_OTHER,'title':'Unrelated','status':'not_started','evidence':EV,'updated_at':1}},
  'decisions':{},'relationships':[
   {'type':'works_on','from_ref':ME,'to_ref':T_OPEN,'evidence':EV},
   {'type':'is_sub_task','from_ref':T_OPEN,'to_ref':T_PARENT,'evidence':EV},
   {'type':'works_on','from_ref':ME,'to_ref':T_DONE,'evidence':EV},
   {'type':'depends_on','from_ref':T_OTHER,'to_ref':T_DONE,'evidence':EV},
   {'type':'delegates_to','from_ref':ME,'to_ref':PEER,'task_ref':T_OPEN,'evidence':EV}]}

TURN={'id':'turn-0009','actor_id':ME,'kind':'agent','messages':[{'id':'m1','ts':1.0,'role':'assistant','source_line':4,'source_file':'/x','text':'Pinging gamejam-tester about the terrain.'}]}

class LeanContextTests(unittest.TestCase):
 def setUp(self):self.ctx,self.al=L.build(TURN,graph(),'brief')
 def test_done_tasks_and_their_relationships_are_dropped(self):
  blob=json.dumps(self.ctx)
  self.assertNotIn('Old fix',blob);self.assertNotIn(T_DONE,self.al.to_alias)
  self.assertFalse(any(T_DONE in r for r in self.ctx['relationships']))
 def test_no_evidence_or_real_ids_or_source_fields_are_sent(self):
  blob=json.dumps(self.ctx)
  for s in ('old evidence','task:','actor:','source_file','source_line','"ts"','"role": "assistant"'):self.assertNotIn(s,blob)
  self.assertEqual(self.ctx['current_turn_prose'],[{'id':'m1','text':TURN['messages'][0]['text']}])
 def test_connected_tasks_get_detail_with_active_parents_others_get_index(self):
  detail={t['title']:t for t in self.ctx['active_tasks_connected_to_actor']}
  self.assertEqual(set(detail),{'Build terrain','Ship M1'})
  self.assertNotIn('summary',detail['Build terrain'])
  self.assertEqual(self.al.to_real[detail['Build terrain']['blocker']['waiting_on_actor']],PEER)
  self.assertEqual([r[1] for r in self.ctx['other_active_tasks']],['Unrelated'])
 def test_actors_are_referenced_or_named_in_prose_only(self):
  names={n for _,n in self.ctx['actors']}
  self.assertIn('gamejam-tester',names);self.assertIn('CKL',names);self.assertNotIn('zz-quiet-agent',names)
 def test_aliases_are_deterministic_and_collision_free(self):
  again,al2=L.build(TURN,graph(),'brief')
  self.assertEqual(again,self.ctx);self.assertEqual(al2.to_alias,self.al.to_alias)
  self.assertEqual(len(set(self.al.to_alias.values())),len(self.al.to_alias))
  self.assertTrue(all(L.ALIAS.match(a) for a in self.al.to_real))
 def test_index_cap_is_recorded(self):
  ctx,_=L.build(TURN,graph(),'brief',index_cap=0)
  self.assertEqual(ctx['other_active_tasks'],[]);self.assertEqual(ctx['other_active_tasks_omitted'],1)

 def test_detail_cap_demotes_to_index_and_keeps_aliases_visible(self):
  ctx,al=L.build(TURN,graph(),'brief',detail_cap=1)
  self.assertEqual([t['title'] for t in ctx['active_tasks_connected_to_actor']],['Build terrain'])
  self.assertEqual([r[1] for r in ctx['other_active_tasks']],['Ship M1','Unrelated'])
  self.assertEqual(ctx['connected_tasks_shown_as_index'],1);self.assertIn(T_PARENT,al.to_alias)
  self.assertEqual(L.build(TURN,graph(),'brief',detail_cap=None)[0],L.build(TURN,graph(),'brief')[0])

class DecodeTests(unittest.TestCase):
 def setUp(self):self.ctx,self.al=L.build(TURN,graph(),'brief');self.a=self.al.to_alias
 def test_every_id_field_round_trips(self):
  real={'tasks':[{'ref':T_OPEN,'resolution':'existing','changes':{'blocker':{'reason':'r','need_human_help':None,'waiting_on_actor':PEER}},'evidence':[]},
                 {'ref':'new:t1','resolution':'ambiguous','candidate_refs':[T_OPEN,T_PARENT],'changes':{},'evidence':[]}],
        'decisions':[{'ref':'new:d1','made_by_actor_id':ME,'affected_task_refs':[T_OPEN,'new:t1'],'evidence':[]}],
        'relationships':[{'type':'delegates_to','from_ref':ME,'to_ref':PEER,'task_ref':T_OPEN,'evidence':[]},{'type':'works_on','from_ref':'human','to_ref':T_PARENT,'evidence':[]}]}
  enc=json.loads(self.al.encode_text(json.dumps(real)))
  self.assertNotIn('task:',json.dumps(enc));self.assertNotIn('actor:',json.dumps(enc))
  self.assertEqual(L.decode_patch(enc,self.al),real)
 def test_unknown_alias_is_a_validation_error(self):
  for patch in ({'tasks':[{'ref':'T99','resolution':'existing','changes':{}}]},{'decisions':[{'made_by_actor_id':'A42','affected_task_refs':[]}]},{'relationships':[{'type':'works_on','from_ref':'A1','to_ref':'T77'}]}):
   with self.assertRaisesRegex(ValueError,'Unknown alias'):L.decode_patch(patch,self.al)
 def test_new_refs_and_human_are_untouched(self):
  p={'tasks':[{'ref':'new:t1','resolution':'new','changes':{}}],'relationships':[{'type':'works_on','from_ref':'human','to_ref':'new:t1'}]}
  self.assertEqual(L.decode_patch(p,self.al),p)
 def test_provenance_tags_version_and_source_hash(self):
  p=L.provenance();self.assertEqual(p['prompt_contract_version'],'v0.3-lean');self.assertEqual(len(p['lean_context_sha256']),64)

if __name__=='__main__':unittest.main()
