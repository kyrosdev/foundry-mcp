from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'enhanced'))
from workbench import Workbench, Store, digest, merge

class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.sources={('ServerScriptService','Demo'):'print("original")\n'}
        self.calls=[]
        def rpc(method,params):
            self.calls.append(params)
            if params['name']!='execute_luau': return {'content':[]}
            code=params['arguments']['code']
            if code.startswith('-- MCP_PROJECT_EXPORT'):
                tree={'name':'game','className':'DataModel','children':[]}
                for path,source in self.sources.items():
                    node=tree
                    for index,part in enumerate(path):
                        child=next((v for v in node['children'] if v['name']==part),None)
                        if child is None:
                            child={'name':part,'className':'Script' if index==len(path)-1 else part,'children':[]}
                            node['children'].append(child)
                        node=child
                    node['source']=source
                return {'content':[{'type':'text','text':json.dumps({'tree':tree,'complete':True,'nodes':3,'captured_at':'test'})}]}
            raw=code.splitlines()[0].split('JSONDecode(',1)[1][:-1]
            payload=json.loads(json.loads(raw))
            path=tuple(payload['path'])
            if 'source' in payload:
                if self.sources[path]!=payload['expected']: return {'isError':True,'content':[{'type':'text','text':'CAS conflict'}]}
                self.sources[path]=payload['source']
            return {'content':[{'type':'text','text':json.dumps({'source':self.sources[path],'class':'Script'})}]}
        self.wb=Workbench(rpc,self.root/'data',self.root/'project')
        self.wb.workspace.mkdir()
        self.args={'studio_id':'test','path':['ServerScriptService','Demo']}

    def tearDown(self): self.temp.cleanup()

    def test_cross_session_locks_and_expiry(self):
        other=Store(self.root/'data')
        token=self.wb.store.acquire('resource','a')
        with self.assertRaises(ValueError): other.acquire('resource','b')
        self.assertFalse(other.release(token,'b'))
        with other.connect() as db: db.execute('UPDATE locks SET expires=0')
        new=other.acquire('resource','b')
        self.assertNotEqual(new,token)
        self.assertTrue(other.release(new,'b'))

    def test_lease_releases_failure_but_retains_timeout(self):
        with self.assertRaises(ValueError):
            with self.wb.store.lease('known','a'): raise ValueError('failure')
        self.wb.store.acquire('known','b')
        with self.assertRaises(TimeoutError):
            with self.wb.store.lease('uncertain','a'): raise TimeoutError()
        with self.assertRaises(ValueError): self.wb.store.acquire('uncertain','b')

    def test_history_survives_new_instance(self):
        revision=self.wb.store.record('test',self.args['path'],'old','test','owner')
        other=Workbench(self.wb.rpc,self.root/'data',self.wb.workspace)
        self.assertEqual(other.call('history',{'revision_id':revision})['source'],'old')

    def test_existing_database_migrates_without_losing_history(self):
        revision = self.wb.store.record('test', self.args['path'], 'old', 'test', 'owner')
        with self.wb.store.connect() as db:
            db.execute('ALTER TABLE team_tasks DROP COLUMN require_receipts')
        other = Workbench(self.wb.rpc, self.root/'data', self.wb.workspace, label='Cold start')
        try:
            self.assertEqual(other.call('history', {'revision_id':revision})['source'], 'old')
            with other.store.connect() as db:
                columns = {row['name'] for row in db.execute('PRAGMA table_info(team_tasks)')}
                self.assertIn('require_receipts', columns)
                self.assertEqual(db.execute('SELECT label FROM sessions WHERE id=?', (other.owner,)).fetchone()[0],
                                 'Cold start')
        finally:
            other.close()

    def test_concurrent_starts_share_a_fresh_database(self):
        directory = self.root/'simultaneous'
        with ThreadPoolExecutor(max_workers=4) as pool:
            workbenches = list(pool.map(lambda _: Workbench(self.wb.rpc, directory, self.root), range(4)))
        try:
            with workbenches[0].store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0], 4)
        finally:
            for workbench in workbenches:
                workbench.close()

    def test_guarded_write_preview_and_restore(self):
        original=self.wb.get(**{'studio':self.args['studio_id'],'path':self.args['path']})
        args={**self.args,'source':'print("new")\n','expected_hash':original['hash'],'format':False,'check':False}
        preview=self.wb.put(args)
        self.assertTrue(preview['preview'])
        self.assertEqual(self.sources[tuple(self.args['path'])],original['source'])
        result=self.wb.put({**args,'preview':False})
        self.assertTrue(result['written'])
        self.assertEqual(self.wb.call('history',{'revision_id':result['backup_revision']})['source'],original['source'])
        with self.assertRaises(ValueError): self.wb.put({**args,'preview':False})
        self.wb.call('script_restore',{**self.args,'revision_id':result['backup_revision'],'expected_hash':result['hash'],'preview':False})
        self.assertEqual(self.sources[tuple(self.args['path'])],original['source'])

    def test_foreign_script_lock_blocks_write(self):
        self.wb.store.acquire('script:test:'+json.dumps(self.args['path']),'other')
        with self.assertRaises(ValueError): self.wb.put({**self.args,'source':'return 1','expected_hash':digest(self.sources[tuple(self.args['path'])]),'preview':False,'check':False,'format':False})

    def test_nonoverlap_merge_and_conflict(self):
        self.assertEqual(merge('a\nb\nc\n','A\nb\nc\n','a\nb\nC\n'),'A\nb\nC\n')
        with self.assertRaises(ValueError): merge('a\n','b\n','c\n')
        with self.assertRaises(ValueError): merge('a\n','x\na\n','y\na\n')

    def test_workspace_boundary(self):
        for path in ('../escape.luau',str(self.root/'absolute.luau'),'scripts/file.txt'):
            with self.assertRaises(ValueError): self.wb.safe_file(path)

    def test_pull_and_status_and_conflict(self):
        args={**self.args,'file':'scripts/demo.luau','action':'pull'}
        self.wb.sync(args)
        self.assertFalse((self.wb.workspace/'scripts/demo.luau').exists())
        self.wb.sync({**args,'preview':False})
        file=self.wb.workspace/'scripts/demo.luau'
        self.assertEqual(file.read_text(),self.sources[tuple(self.args['path'])])
        self.assertTrue(self.wb.sync({**args,'action':'status'})['bound'])
        file.write_text('print("local change")')
        self.sources[tuple(self.args['path'])]='print("remote change")'
        with self.assertRaises(ValueError): self.wb.sync({**args,'preview':False})
        with self.assertRaises(ValueError): self.wb.sync({**args,'action':'merge','preview':False})
        self.assertEqual(file.read_text(),'print("local change")')

    def test_sync_rejects_file_remapping(self):
        args={**self.args,'file':'demo.luau','action':'pull','preview':False}
        self.wb.sync(args)
        with self.assertRaises(ValueError): self.wb.sync({**args,'file':'other.luau'})

    def test_real_toolchain(self):
        source='--!strict\nlocal value: number = 1\nprint(value)\n'
        formatted=self.wb.code_tool(source,True)
        checked=self.wb.code_tool(formatted['source'])
        self.assertTrue(checked['passed'],checked)
        invalid=self.wb.code_tool('local value: number = "wrong"\nprint(value)\n')
        self.assertFalse(invalid['tools']['luau-analyze']['passed'])
        with self.assertRaises(ValueError): self.wb.code_tool('local = !!!',True)

    def test_playtest_cleans_up_after_failed_assertion(self):
        def rpc(method,params):
            self.calls.append(params)
            if params['name']=='get_studio_state':return {'content':[{'type':'text','text':'- Current Studio Mode: Edit'}]}
            if params['name']=='execute_luau':return {'isError':True,'content':[{'type':'text','text':'assert failed'}]}
            return {'content':[]}
        self.wb.rpc=rpc
        result=self.wb.playtest({'studio_id':'test','steps':[
            {'tool':'start_stop_play','arguments':{'is_start':True}},
            {'assert_code':'return false','arguments':{'datamodel_type':'Server'}}]})
        self.assertTrue(result['isError'])
        self.assertEqual(self.calls[-1]['arguments'],{'studio_id':'test','is_start':False})

    def test_native_patch_creates_history_and_rejects_ambiguous_edits(self):
        result=self.wb.patch_native({'studio_id':'test','file_path':'game.ServerScriptService.Demo',
            'edits':[{'old_string':'original','new_string':'patched'}]})
        self.assertTrue(result.get('written'),result)
        self.assertIn('patched',self.sources[tuple(self.args['path'])])
        self.assertTrue(self.wb.call('history',self.args)['revisions'])
        with self.assertRaises(ValueError):
            self.wb.patch_native({'studio_id':'test','file_path':'game.ServerScriptService.Demo',
                'edits':[{'old_string':'absent','new_string':'x'}]})

    def test_playtest_preserves_existing_session(self):
        def rpc(method,params):
            self.calls.append(params)
            return {'content':[{'type':'text','text':'- Current Studio Mode: Play'}]}
        self.wb.rpc=rpc
        result=self.wb.playtest({'studio_id':'test','steps':[{'tool':'start_stop_play','arguments':{'is_start':True}}]})
        self.assertTrue(result['isError'])
        self.assertEqual([c['name'] for c in self.calls],['get_studio_state'])

if __name__=='__main__': unittest.main()
