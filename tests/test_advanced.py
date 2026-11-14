import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_workbench
from workbench import digest

class AdvancedTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_workbench.WorkbenchTests();self.fixture.setUp();self.w=self.fixture.wb
        self.path=['ServerScriptService','Demo'];self.studio='test'
        self.w.api.project=lambda args:{'passed':True,'gate_passed':True,'output':'','complete':True}
        original=self.w.rpc
        def rpc(method,params):
            code=params.get('arguments',{}).get('code','')
            if 'MCP coordinated script changes' in code:
                payload=json.loads(json.loads(code.splitlines()[0].split('JSONDecode(',1)[1][:-1]))
                for item in payload['items']:
                    if self.fixture.sources[tuple(item['path'])]!=item['expected']:
                        return {'content':[{'type':'text','text':json.dumps({'success':False,'error':'conflict'})}]}
                for item in payload['items']:self.fixture.sources[tuple(item['path'])]=item['source']
                return {'content':[{'type':'text','text':json.dumps({'success':True,'changed':len(payload['items'])})}]}
            return original(method,params)
        self.w.rpc=rpc
    def tearDown(self):self.w.close();self.fixture.tearDown()

    def test_multi_script_prepare_apply_rollback(self):
        other=['ReplicatedStorage','Config'];self.fixture.sources[tuple(other)]='return 1\n'
        items=[{'path':self.path,'source':'print("new")\n','expected_hash':digest(self.fixture.sources[tuple(self.path)])},
               {'path':other,'source':'return 2\n','expected_hash':digest('return 1\n')}]
        prepared=self.w.call('changes',{'action':'prepare','studio_id':self.studio,'items':items})
        self.assertNotIn('new',self.fixture.sources[tuple(self.path)])
        result=self.w.call('changes',{'action':'apply','plan_id':prepared['plan_id']})
        self.assertTrue(result['success']);self.assertEqual(self.fixture.sources[tuple(other)],'return 2\n')
        result=self.w.call('changes',{'action':'rollback','plan_id':prepared['plan_id']})
        self.assertTrue(result['rolled_back']);self.assertEqual(self.fixture.sources[tuple(other)],'return 1\n')

    def test_stale_plan_does_not_write(self):
        plan=self.w.call('changes',{'action':'prepare','studio_id':'test','items':[{'path':self.path,'source':'print("new")\n','expected_hash':digest(self.fixture.sources[tuple(self.path)])}]})
        self.fixture.sources[tuple(self.path)]='print("manual")\n'
        with self.assertRaises(ValueError):self.w.call('changes',{'action':'apply','plan_id':plan['plan_id']})
        self.assertEqual(self.fixture.sources[tuple(self.path)],'print("manual")\n')

    def test_journal_reconciles_without_replaying(self):
        before=[{'path':self.path,'source':self.fixture.sources[tuple(self.path)]}];after=[{'path':self.path,'source':'print("new")\n'}]
        operation=self.w.advanced.begin('test',before,after,state='uncertain')
        self.fixture.sources[tuple(self.path)]=after[0]['source']
        result=self.w.call('recover',{'operation_id':operation})
        self.assertEqual(result['state'],'committed');self.assertFalse(result['replayed'])

    def test_persistent_project_rebind_keeps_history_and_sync(self):
        self.w.call('project_bind',{'studio_id':'old','project_id':'persistent-game'})
        revision=self.w.store.record('old',self.path,'old source','test',self.w.owner)
        self.w.sync({'studio_id':'old','path':self.path,'file':'demo.luau','action':'pull','preview':False})
        self.w.call('project_bind',{'studio_id':'new','project_id':'persistent-game'})
        history=self.w.call('history',{'studio_id':'new','path':self.path})
        self.assertTrue(any(v['id']==revision for v in history['revisions']))
        self.assertTrue(self.w.sync({'studio_id':'new','path':self.path,'file':'demo.luau','action':'status'})['bound'])
        with self.assertRaises(ValueError):self.w.call('project_bind',{'studio_id':'new','project_id':'other-game'})

    def test_strict_task_requires_passed_current_receipt(self):
        project='verified';self.w.call('team_join',{'project_id':project,'studio_id':'test'})
        task=self.w.call('task_create',{'project_id':project,'title':'Verify code','paths':[self.path]})
        task=self.w.call('task_claim',{'project_id':project,'task_id':task['id']})
        update={'project_id':project,'task_id':task['id'],'expected_version':task['version'],'state':'review','summary':'Checked current code'}
        with self.assertRaises(ValueError):self.w.call('task_update',{**update,'evidence':['trust me']})
        evidence=self.w.call('evidence',{'project_id':project,'task_id':task['id'],'kind':'code_check','arguments':{'path':self.path}})
        self.assertTrue(evidence['passed'])
        self.fixture.sources[tuple(self.path)]='print("changed after test")\n'
        with self.assertRaises(ValueError):self.w.call('task_update',{**update,'evidence':[evidence['evidence_reference']]})
        self.fixture.sources[tuple(self.path)]='print("original")\n'
        task=self.w.call('task_update',{**update,'evidence':[evidence['evidence_reference']]})
        reviewer=self.w.call('agent',{'action':'register','name':'reviewer'})['agent_id']
        with self.w.actor(reviewer):
            self.w.call('team_join',{'project_id':project,'studio_id':'test'})
            done=self.w.call('task_review',{'project_id':project,'task_id':task['id'],'expected_version':task['version'],'approve':True,'notes':'Verified receipt'})
        self.assertEqual(done['state'],'done')

    def test_receipt_tampering_is_detected(self):
        with self.w.store.connect() as db:db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?,?)',('bad','p','t','a','code_check',1,'{}','incorrect',0))
        with self.assertRaises(ValueError):self.w.call('receipt',{'receipt_id':'bad'})

    def test_existing_lint_warning_does_not_block_but_new_warning_does(self):
        baseline='local unused = 1\nprint(1)\n'
        result=self.w.code_tool('local unused = 1\nprint(2)\n',lint_only=True,context={'baseline_source':baseline})
        self.assertFalse(result['passed']);self.assertTrue(result['gate_passed'])
        result=self.w.code_tool('local unused = 1\nlocal extra = 2\nprint(2)\n',lint_only=True,context={'baseline_source':baseline})
        self.assertFalse(result['gate_passed'])

if __name__=='__main__':unittest.main()
