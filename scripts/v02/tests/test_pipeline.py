"""Contract tests use temporary data only; never alter the review corpus."""
import contextlib,copy,hashlib,io,json,os,sys,tempfile,time,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pipeline as p
import evaluate as e
import quota as q
import export_training as training
import run_local_codex as runner

class Contract(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.data=Path(self.tmp.name)
  for module in (p,e,q,training):
   mock=patch.object(module,'DATA',self.data);mock.start();self.addCleanup(mock.stop)
  self.turn={'id':'turn-0001','ordinal':1,'actor_id':'actor:a','start_ts':1,'end_ts':2,'messages':[{'id':'m1','text':'Build UI. Blocked on review. Now done.','ts':1}]}
  self.write('actors.json',[{'id':'actor:a','name':'A'},{'id':'human','name':'CKL'}]);self.write('turns.json',[self.turn]);self.write('manifest.json',{'root_turn_id':'turn-0001','teacher_model':'gpt-6-luna'})
  self.ev=[{'message_id':'m1','quote':'Build UI.'}]
  self.new={'ref':'new:t1','resolution':'new','changes':{'title':'Build UI','summary':'Build UI','status':'blocked','importance':'high','complexity':'medium','blocker':{'reason':'Review','waiting_on_actor':'human','need_human_help':True}},'evidence':self.ev}
  self.base={'schema_version':'0.2','turn_id':'turn-0001','actor_patch':{},'actor_evidence':[],'tasks':[self.new],'decisions':[],'relationships':[]}
 def write(self,name,value): (self.data/name).write_text(json.dumps(value))
 def rows(self,name,rows): (self.data/name).write_text(''.join(json.dumps(r)+'\n' for r in rows))
 def test_stable_identity_sparse_patch_and_explicit_clear(self):
  graph=p.initial();p.check(self.base,self.turn,graph);g,r=p.apply(graph,self.base,self.turn);tid=r['tasks'][0]['ref']
  self.assertEqual(tid,p.apply(graph,self.base,self.turn)[1]['tasks'][0]['ref'])
  delta={**self.base,'tasks':[{'ref':tid,'resolution':'existing','changes':{'blocker':None},'evidence':self.ev}]}
  p.check(delta,self.turn,g);out,_=p.apply(g,delta,self.turn)
  self.assertEqual(out['tasks'][tid]['status'],'blocked');self.assertIsNone(out['tasks'][tid]['blocker']);self.assertIsNotNone(g['tasks'][tid]['blocker'])
 def test_evidence_and_typed_edges_reject_invalid_claims(self):
  bad=copy.deepcopy(self.base);bad['tasks'][0]['evidence'][0]['quote']='Invented source'
  with self.assertRaisesRegex(ValueError,'verbatim'):p.check(bad,self.turn,p.initial())
  bad=copy.deepcopy(self.base);bad['relationships']=[{'type':'works_on','from_ref':'new:t1','to_ref':'actor:a','evidence':self.ev}]
  with self.assertRaisesRegex(ValueError,'actor'):p.check(bad,self.turn,p.initial())
 def test_ambiguous_identity_does_not_merge(self):
  g,resolved=p.apply(p.initial(),self.base,self.turn);tid=resolved['tasks'][0]['ref'];b=copy.deepcopy(self.base);b['tasks'][0].update(ref='new:t2',resolution='ambiguous',candidate_refs=[tid])
  p.check(b,self.turn,g);out,r=p.apply(g,b,self.turn);self.assertEqual(len(out['tasks']),2);self.assertTrue(out['tasks'][r['tasks'][0]['ref']]['identity_ambiguous'])
 def test_future_project_brief_not_in_context(self):
  future={**self.turn,'id':'turn-0002','ordinal':2,'messages':[{'id':'future','text':'SECRET FUTURE','ts':3}]}
  self.write('turns.json',[self.turn,future]);self.write('manifest.json',{'root_turn_id':'turn-0002'})
  self.assertNotIn('SECRET FUTURE',json.dumps(p.context(self.turn,p.initial(),[])))
 def seed(self):
  row={'turn_id':self.turn['id'],'patch':self.base,'input':{'current_turn_prose':self.turn['messages']}}
  self.rows('predictions.jsonl',[row]);return row
 def test_review_is_append_only_and_rejects_stale_revision(self):
  self.seed();payload={'turn_id':self.turn['id'],'status':'approved','base_revision':0}
  with contextlib.redirect_stdout(io.StringIO()):p.save_review(payload)
  before=(self.data/'reviews.jsonl').read_text()
  with self.assertRaisesRegex(ValueError,'another tab'):p.save_review(payload)
  self.assertEqual(before,(self.data/'reviews.jsonl').read_text());self.assertEqual(p.ledger()[0]['patch'],self.base)
 def test_no_machine_as_gold_and_supported_extra_is_not_hallucination(self):
  self.seed();self.assertIsNone(e.evaluate()['metrics']['tasks']['f1'])
  self.rows('reviews.jsonl',[{'turn_id':self.turn['id'],'status':'approved'}]);self.rows('judge-verdicts.jsonl',[{'turn_id':self.turn['id'],'unmatched_claims':[{'verdict':'SUPPORTED'}]}])
  metrics=e.evaluate()['metrics'];self.assertEqual(metrics['tasks']['f1'],1);self.assertEqual(metrics['hallucination']['unsupported_rate'],0)
 def test_exports_only_contiguous_reviewed_prefix(self):
  self.seed();self.assertEqual(training.export()['reviewed_contiguous_prefix'],0)
  self.rows('reviews.jsonl',[{'turn_id':self.turn['id'],'status':'approved'}]);report=training.export()
  self.assertEqual(report['reviewed_contiguous_prefix'],1);self.assertFalse(report['training_allowed']);self.assertEqual(report['train_examples'],0)
 def test_published_task_state_does_not_include_future_update(self):
  first=self.seed();g,r=p.apply(p.initial(),self.base,self.turn);tid=r['tasks'][0]['ref'];second={**self.turn,'id':'turn-0002','ordinal':2,'end_ts':4}
  delta={**self.base,'turn_id':second['id'],'tasks':[{'ref':tid,'resolution':'existing','changes':{'title':'Later title'},'evidence':self.ev}]}
  self.write('turns.json',[self.turn,second]);p.publish([first,{'turn_id':second['id'],'patch':delta}]);published={'turns':[p.read('turn-states/'+t['id']+'.json',p.turn_view(t)) for t in p.read('turns.json')]}
  self.assertEqual(published['turns'][0]['task_states'][tid]['title'],'Build UI');self.assertEqual(published['turns'][1]['task_states'][tid]['title'],'Later title')
  self.assertEqual(published['turns'][0]['state_after']['tasks'][tid]['title'],'Build UI')
 def test_full_snapshot_carries_tasks_and_preserves_exact_recorded_input(self):
  first=self.seed();g,r=p.apply(p.initial(),self.base,self.turn);tid=r['tasks'][0]['ref']
  second={**self.turn,'id':'turn-0002','ordinal':2,'end_ts':4}
  pending={**self.turn,'id':'turn-0003','ordinal':3,'end_ts':5}
  no_change={**self.base,'turn_id':second['id'],'tasks':[]}
  recorded={'project_description':'Recorded brief','relevant_existing_tasks_with_ids':[], 'marker':'exact saved context, not reconstructed'}
  self.write('turns.json',[self.turn,second,pending])
  p.publish([first,{'turn_id':second['id'],'patch':no_change,'input':recorded}])
  out=[p.read('turn-states/'+t['id']+'.json',p.turn_view(t)) for t in p.read('turns.json')]
  self.assertEqual(out[1]['task_states'],{})
  self.assertEqual(out[1]['state_after']['tasks'][tid]['title'],'Build UI')
  self.assertEqual(out[1]['llm_input'],recorded)
  self.assertNotIn('marker',out[0]['llm_input'])
  self.assertIsNone(out[2]['state_after']);self.assertIsNone(out[2]['llm_input'])
 def test_quote_restoration_is_exact_unique_and_audited(self):
  patch=copy.deepcopy(self.base);patch['tasks'][0]['evidence']=[{'message_id':'m1','quote':'Build UI. Now done.'}]
  turn={**self.turn,'messages':[{'id':'m1','text':'**Build UI.** Now done.'}]}
  fixed,repairs=runner.restore_bold_quotes(patch,turn)
  self.assertEqual(fixed['tasks'][0]['evidence'][0]['quote'],'Build UI.** Now done.');self.assertEqual(len(repairs),1)
  p.check(fixed,turn,p.initial());self.assertEqual(patch['tasks'][0]['evidence'][0]['quote'],'Build UI. Now done.')
  ambiguous={**turn,'messages':[{'id':'m1','text':'**Build UI.** Now done. **Build UI.** Now done.'}]}
  fixed,repairs=runner.restore_bold_quotes(patch,ambiguous);self.assertEqual(repairs,[])
  with self.assertRaises(ValueError):p.check(fixed,ambiguous,p.initial())
 def test_noncontiguous_evidence_splits_only_exact_unchanged_sentences(self):
  value=copy.deepcopy(self.base);value['tasks'][0]['evidence']=[{'message_id':'m1','quote':'Build UI. Now done.'}]
  fixed,repairs=runner.restore_bold_quotes(value,self.turn);self.assertEqual([e['quote'] for e in fixed['tasks'][0]['evidence']],['Build UI.','Now done.']);p.check(fixed,self.turn,p.initial())
  value['tasks'][0]['evidence'][0]['quote']='Build UI. Definitely done.'
  fixed,repairs=runner.restore_bold_quotes(value,self.turn);self.assertEqual(repairs,[])
  with self.assertRaises(ValueError):p.check(fixed,self.turn,p.initial())
 def test_cli_session_change_preserves_authorized_quota_baseline(self):
  self.write('quota-config.json',{'baseline_used_percent':49,'stop_delta_percentage_points':4,'budget_percentage_points':5,'session_file':'old'})
  folder=self.data/'.codex/sessions';folder.mkdir(parents=True);(folder/'run-test-session.jsonl').write_text('')
  with patch.object(Path,'home',return_value=self.data):runner.attach_session('test-session')
  config=p.read('quota-config.json');self.assertEqual(config['baseline_used_percent'],49);self.assertEqual(config['stop_delta_percentage_points'],4)
 def test_lean_child_drops_parent_identity_and_paid_credentials(self):
  original={'CCT_AGENT_NAME':'parent','CODEX_THREAD_ID':'parent-thread','CODEX_SESSION_ID':'parent-session','CLAUDE_CODE_OAUTH_TOKEN':'test-only','OPENAI_API_KEY':'test-only','HOME':'/normal/home','PATH':'/bin'}
  with patch.dict(os.environ,original,clear=True):
   env=runner.lean_codex.environment()
   self.assertEqual(env,{'HOME':'/normal/home','PATH':'/bin'})
   self.assertEqual(dict(os.environ),original)
 def test_missing_quota_config_stops_generation(self):
  self.assertFalse(q.measure()['allowed'])
  self.assertIn('authorized',q.measure()['reason'])
 def test_full_day_authorization_is_source_bound_and_stops_at_native_limit(self):
  day=self.data/'day1';day.mkdir();source=day/'turns.json';source.write_text(json.dumps([self.turn]*1902))
  transcript=day/'session.jsonl';transcript.write_text(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'payload':{'type':'token_count','rate_limits':{'primary':{'used_percent':54,'resets_at':time.time()+1000}}}})+'\n')
  config={'mode':'complete_frozen_day1','max_turns':1902,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'session_file':str(transcript)}
  (day/'quota-config.json').write_text(json.dumps(config))
  with patch.object(q,'DATA',day):
   self.assertTrue(q.measure()['allowed']);self.assertIsNone(q.measure()['budget_percentage_points'])
   transcript.write_text(transcript.read_text().replace('54','100'));self.assertFalse(q.measure()['allowed'])
   source.write_text('[]');self.assertIn('frozen',q.measure()['reason'])
 def test_quota_guard_and_timestamp_jitter(self):
  transcript=self.data/'session.jsonl';self.write('quota-config.json',{'session_file':str(transcript),'baseline_used_percent':10,'resets_at':1000,'stop_delta_percentage_points':4})
  def sample(used,seconds=0):
   self.rows('session.jsonl',[{'timestamp':datetime.fromtimestamp(time.time()-seconds,timezone.utc).isoformat(),'payload':{'type':'token_count','rate_limits':{'primary':{'used_percent':used,'resets_at':1001,'window_minutes':10080}}}}])
  sample(12);self.assertTrue(q.measure()['allowed']);sample(14);self.assertFalse(q.measure()['allowed']);sample(12,301);self.assertFalse(q.measure()['allowed'])
 def test_blocker_omission_is_not_an_explicit_clear(self):
  self.seed();gold=copy.deepcopy(self.base);gold['tasks'][0]['changes'].pop('blocker')
  self.rows('reviews.jsonl',[{'turn_id':self.turn['id'],'status':'corrected','correction':gold}]);metric=e.evaluate()['metrics']['fields']['blocker.present']
  self.assertIn('UNCHANGED',metric['confusion']);self.assertEqual(metric['accuracy'],0)
 def test_rank_metrics_handle_ties_and_empty(self):
  self.assertEqual(e.ranks([1,1,3]),[1.5,1.5,3]);self.assertEqual(e.spearman([1,2,3],[3,2,1]),-1);self.assertIsNone(e.spearman([1,1],[2,2]));self.assertIsNone(e.prf(0,0,0)['f1'])

if __name__=='__main__':unittest.main()
