from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'enhanced'))
from workbench import Workbench

class TeamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.agents=[Workbench(None,self.root/'data',self.root,label) for label in ('builder','reviewer','observer')]
        for agent in self.agents:agent.call('team_join',{'project_id':'game','studio_id':'studio','capabilities':['luau','ui']})
        self.a,self.b,self.c=self.agents

    def tearDown(self):
        for agent in self.agents:agent.close()
        self.temp.cleanup()

    def create(self,**args):return self.a.call('task_create',{'project_id':'game','title':'Build feature','require_receipts':False,**args})
    def claim(self,agent,task):return agent.call('task_claim',{'project_id':'game','task_id':task['id']})
    def update(self,agent,task,**args):return agent.call('task_update',{'project_id':'game','task_id':task['id'],'expected_version':task['version'],**args})

    def test_atomic_claim_race(self):
        task=self.create(paths=[['ServerScriptService','Combat']])
        def claim(agent):
            try:return self.claim(agent,task)['owner']
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,[self.a,self.b]))
        self.assertEqual(len([v for v in results if v]),1)

    def test_create_and_claim_conflict_leaves_no_queued_task(self):
        path = [['ServerScriptService', 'Combat']]
        active = self.create(paths=path, claim_now=True)
        self.assertEqual(active['state'], 'active')
        with self.assertRaises(ValueError):
            self.b.call('task_create', {'project_id':'game','title':'Conflicting task','paths':path,'claim_now':True})
        packet = self.b.call('task_review_packet', {'project_id':'game','task_id':active['id']})
        self.assertEqual(packet['state'], 'active')
        board = self.b.call('team_board', {'project_id':'game'})
        self.assertEqual([task['id'] for task in board['tasks']], [active['id']])

    def test_dependencies_independent_review_and_evidence(self):
        first=self.create();second=self.create(depends_on=[first['id']])
        with self.assertRaises(ValueError):self.claim(self.b,second)
        active=self.claim(self.a,first)
        with self.assertRaises(ValueError):self.update(self.a,active,state='review',summary='finished')
        with self.assertRaises(ValueError):self.update(self.a,active,state='done',summary='finished',evidence=['test output'])
        review=self.update(self.a,active,state='review',summary='finished',evidence=['test output'])
        with self.assertRaises(ValueError):self.a.call('task_review',{'project_id':'game','task_id':review['id'],'expected_version':review['version'],'approve':True,'notes':'self'})
        done=self.b.call('task_review',{'project_id':'game','task_id':review['id'],'expected_version':review['version'],'approve':True,'notes':'verified test evidence'})
        self.assertEqual(done['state'],'done');self.assertEqual(done['progress'],100)
        self.assertEqual(self.claim(self.b,second)['state'],'active')

    def test_scope_reservations_and_authorized_writes(self):
        first=self.create(paths=[['ServerScriptService']]);second=self.create(paths=[['ServerScriptService','Combat']])
        active=self.claim(self.a,first)
        self.a.assert_script_lock('studio',['ServerScriptService','Combat'])
        with self.assertRaises(ValueError):self.b.assert_script_lock('studio',['ServerScriptService','Combat'])
        with self.assertRaises(ValueError):self.claim(self.b,second)
        with self.assertRaises(ValueError):self.a.assert_script_lock('studio',['ReplicatedStorage','Other'])
        self.update(self.a,active,state='review',summary='review now',evidence=['test1'])
        with self.assertRaises(ValueError):self.a.assert_script_lock('studio',['ServerScriptService','Combat'])

    def test_disjoint_task_scopes_can_work_in_parallel(self):
        self.claim(self.a,self.create(paths=[['ServerScriptService','Combat']]))
        self.claim(self.b,self.create(paths=[['StarterGui','HUD']]))
        self.a.assert_script_lock('studio',['ServerScriptService','Combat'])
        self.b.assert_script_lock('studio',['StarterGui','HUD'])
        with self.assertRaises(ValueError):self.a.assert_script_lock('studio',['StarterGui','HUD'])

    def test_handoff_transfers_path_locks(self):
        task=self.claim(self.a,self.create(paths=[['Workspace','Model']]))
        handed=self.a.call('task_handoff',{'project_id':'game','task_id':task['id'],'expected_version':task['version'],'recipient':self.b.owner,'summary':'Geometry ready; finish properties'})
        self.assertEqual(handed['owner'],self.b.owner)
        self.b.assert_script_lock('studio',['Workspace','Model','Child'])
        with self.assertRaises(ValueError):self.a.assert_script_lock('studio',['Workspace','Model'])

    def test_stale_versions_and_expired_claims(self):
        task=self.claim(self.a,self.create(paths=[['Workspace','Model']]))
        renewed=self.update(self.a,task,summary='working',progress=30)
        with self.assertRaises(ValueError):self.update(self.a,task,progress=50)
        with self.a.store.connect() as db:db.execute('UPDATE team_tasks SET expires=0 WHERE id=?',(task['id'],))
        reclaimed=self.claim(self.b,task)
        self.assertEqual(reclaimed['owner'],self.b.owner)
        self.assertGreater(reclaimed['version'],renewed['version'])

    def test_capability_requirements(self):
        task=self.create(required_capabilities=['blender'])
        with self.assertRaises(ValueError):self.claim(self.a,task)

    def test_private_messages_reply_and_ack(self):
        message=self.a.call('team_message',{'project_id':'game','recipient':self.b.owner,'body':'Use contract v2'})
        self.assertEqual(self.c.call('team_inbox',{'project_id':'game'})['messages'],[])
        with self.assertRaises(ValueError):self.c.call('team_message',{'project_id':'game','body':'spy reply','reply_to':message['message_id']})
        inbox=self.b.call('team_inbox',{'project_id':'game','acknowledge':True})
        self.assertEqual(inbox['messages'][0]['body'],'Use contract v2')
        self.assertTrue(self.b.call('team_inbox',{'project_id':'game'})['messages'][0]['seen'])
        self.assertEqual(self.b.call('team_inbox',{'project_id':'game','after_cursor':inbox['cursor']})['messages'],[])
        events=self.c.call('team_events',{'project_id':'game'})['events']
        self.assertNotIn('Use contract v2',json.dumps(events))

    def test_shared_context_cas_and_room_isolation(self):
        result=self.a.call('team_context',{'project_id':'game','key':'interface','value':{'version':'v2'},'expected_version':0})
        self.assertEqual(result['version'],1)
        with self.assertRaises(ValueError):self.b.call('team_context',{'project_id':'game','key':'interface','value':{},'expected_version':0})
        self.assertEqual(self.b.call('team_context',{'project_id':'game','key':'interface'})['value']['version'],'v2')
        self.b.call('team_context',{'project_id':'game','key':'interface','value':{'version':'v3'},'expected_version':1})
        self.assertEqual(self.a.call('team_context',{'project_id':'game','key':'interface','version':1})['value']['version'],'v2')
        self.assertEqual(len(self.a.call('team_context',{'project_id':'game','key':'interface','history':True})['versions']),2)
        with self.assertRaises(ValueError):self.b.call('team_board',{'project_id':'unjoined'})
        self.b.call('team_join',{'project_id':'other'})
        task=self.create()
        with self.assertRaises(ValueError):self.b.call('task_claim',{'project_id':'other','task_id':task['id']})

    def test_long_poll_delivers_new_event(self):
        cursor=self.a.call('team_board',{'project_id':'game'})['cursor']
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.b.call,'team_events',{'project_id':'game','after_cursor':cursor,'wait_seconds':2})
            time.sleep(0.1)
            self.a.call('team_message',{'project_id':'game','body':'New work available'})
            value=future.result(timeout=3)
        self.assertFalse(value['timed_out']);self.assertGreater(value['cursor'],cursor)

    def test_presence_staleness_and_disconnect(self):
        with self.a.store.connect() as db:db.execute('UPDATE team_members SET heartbeat=0 WHERE session=?',(self.b.owner,))
        members=self.a.call('team_board',{'project_id':'game'})['members']
        self.assertFalse(next(v for v in members if v['session']==self.b.owner)['online'])
        self.c.close()
        members=self.a.call('team_board',{'project_id':'game'})['members']
        self.assertFalse(next(v for v in members if v['session']==self.c.owner)['online'])

    def test_auto_mode_activates_for_same_project_and_studio(self):
        room = {'project_id': 'auto-demo', 'studio_id': 'target'}
        result = self.a.call('team_join', room)
        self.assertEqual(result['effective_mode'], 'individual')
        self.a.assert_script_lock('target', ['Workspace', 'Coin'])
        self.b.call('team_join', {**room, 'studio_id': 'other-target'})
        self.c.call('team_join', {**room, 'project_id': 'different-project'})
        self.a.team.authorize_studio_action('target')

        self.assertEqual(self.b.call('team_join', room)['effective_mode'], 'team')
        self.assertEqual(self.a.call('team_board', {'project_id': 'auto-demo'})['effective_mode'], 'team')
        with self.assertRaisesRegex(ValueError, 'claim an active task'):
            self.a.assert_script_lock('target', ['Workspace', 'Coin'])
        task = self.b.call('task_create', {'project_id': 'auto-demo', 'title': 'Coin',
                                         'paths': [['Workspace', 'Coin']], 'claim_now': True})
        self.b.assert_script_lock('target', ['Workspace', 'Coin'])
        self.b.close()
        with self.assertRaises(ValueError):
            self.a.assert_script_lock('target', ['Workspace', 'Coin'])
        with self.a.store.connect() as db:
            db.execute('UPDATE team_tasks SET expires=0 WHERE id=?', (task['id'],))
        board = self.a.call('team_board', {'project_id': 'auto-demo'})
        self.assertEqual(board['effective_mode'], 'individual')
        self.a.assert_script_lock('target', ['Workspace', 'Coin'])

    def test_explicit_team_mode_retains_solo_reservation_requirement(self):
        room = {'project_id': 'strict-team', 'studio_id': 'target'}
        self.assertEqual(self.a.call('team_join', {**room, 'mode': 'team'})['effective_mode'], 'team')
        with self.assertRaisesRegex(ValueError, 'claim an active task'):
            self.a.team.authorize_studio_action('target')
        self.assertEqual(self.a.call('team_join', room)['mode'], 'team')
        self.assertEqual(self.a.call('team_join', {**room, 'mode': 'auto'})['effective_mode'], 'individual')

    def test_manual_lock_colon_and_ancestor_conflicts(self):
        self.a.store.acquire('script:other:'+json.dumps(['Folder:Name']),'a')
        with self.assertRaises(ValueError):self.b.store.acquire('script:other:'+json.dumps(['Folder:Name','Child']),'b')

    def test_logical_subagents_have_isolated_identities_and_reservations(self):
        identifiers=[self.a.call('agent',{'action':'register','name':name})['agent_id'] for name in ('server builder','UI builder')]
        def work(pair):
            identifier,path=pair
            with self.a.actor(identifier):
                self.a.call('team_join',{'project_id':'logical','studio_id':'studio'})
                task=self.a.call('task_create',{'project_id':'logical','title':'Build '+path,'paths':[[path]]})
                claimed=self.a.call('task_claim',{'project_id':'logical','task_id':task['id']})
                self.a.assert_script_lock('studio',[path,'Child'])
                return self.a.owner,claimed['owner']
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,zip(identifiers,['ServerScriptService','StarterGui'])))
        self.assertEqual(results,[(v,v) for v in identifiers])
        self.assertEqual(self.a.owner,self.a.connection_id)
        with self.assertRaises(ValueError):
            with self.b.actor(identifiers[0]):pass
        self.a.call('agent',{'action':'retire','agent_id':identifiers[0]})
        with self.assertRaises(ValueError):
            with self.a.actor(identifiers[0]):pass

if __name__=='__main__':unittest.main()
