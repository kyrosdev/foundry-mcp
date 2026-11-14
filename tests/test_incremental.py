import copy
from pathlib import Path
import tempfile
import unittest
from test_workbench import ROOT
from workbench import Workbench

class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.w=Workbench(None,self.temp.name,self.temp.name)
        self.tree={'name':'game','className':'DataModel','children':[{'name':'ServerScriptService','className':'ServerScriptService','children':[
            {'name':'A','className':'Script','source':'print(1)','children':[]},
            {'name':'B','className':'Script','source':'print(2)','children':[]}]}]}
        self.w.api.export=lambda studio:{'tree':copy.deepcopy(self.tree),'nodes':4,'complete':True,'captured_at':'now'}
        self.calls=[]
        def analyze(files,cwd,sourcemap=None,directory=None):
            self.calls.append([Path(p).name for p in files]);lines=[]
            for file in files:
                if 'BAD' in Path(file).read_text():lines.append(Path(file).name+":1:1-3: (W0) TypeError: Bad fixture type")
            return {'available':True,'passed':not lines,'exit_code':0,'output':'\n'.join(lines),'engine':'fixture'}
        self.w.api.analyze=analyze
    def tearDown(self):self.w.close();self.temp.cleanup()

    def test_identical_fresh_capture_reuses_analysis(self):
        first=self.w.api.project({'studio_id':'test'});second=self.w.api.project({'studio_id':'test'})
        self.assertEqual(len(self.calls),1);self.assertTrue(second['incremental']['baseline_cache_hit'])
        self.assertEqual(first['snapshot_sha256'],second['snapshot_sha256'])

    def test_disjoint_change_only_checks_affected_file(self):
        self.w.api.project({'studio_id':'test'})
        result=self.w.api.project({'studio_id':'test','path':['ServerScriptService','B'],'source':'print(3)'})
        self.assertEqual(len(self.calls[-1]),1);self.assertTrue(result['gate_passed'])
        self.assertEqual(self.tree['children'][0]['children'][1]['source'],'print(2)')

    def test_existing_errors_visible_but_only_regressions_block(self):
        self.tree['children'][0]['children'][0]['source']='BAD'
        result=self.w.api.project({'studio_id':'test','path':['ServerScriptService','B'],'source':'print(3)'})
        self.assertFalse(result['passed']);self.assertTrue(result['gate_passed']);self.assertEqual(result['baseline_diagnostics'],1)
        result=self.w.api.project({'studio_id':'test','path':['ServerScriptService','B'],'source':'BAD'})
        self.assertFalse(result['gate_passed']);self.assertTrue(result['introduced_diagnostics'])

    def test_unknown_require_forces_full_check(self):
        self.tree['children'][0]['children'][0]['source']='local x = require(variable)'
        self.w.api.project({'studio_id':'test'})
        self.w.api.project({'studio_id':'test','path':['ServerScriptService','B'],'source':'print(3)'})
        self.assertEqual(len(self.calls[-1]),2)

    def test_live_change_invalidates_old_result(self):
        before=self.w.api.project({'studio_id':'test'})
        self.tree['children'][0]['children'][0]['source']='BAD'
        after=self.w.api.project({'studio_id':'test'})
        self.assertNotEqual(before['snapshot_sha256'],after['snapshot_sha256']);self.assertFalse(after['passed'])

    def test_cold_plan_compares_separate_baseline_and_proposal(self):
        path = ['ServerScriptService','B']
        proposal = {'studio_id':'test','path':path,'source':'BAD'}
        blocked = self.w.api.project(proposal)
        self.assertFalse(blocked['gate_passed'])
        self.assertEqual(blocked['baseline_diagnostics'],0)
        self.assertEqual(len(blocked['introduced_diagnostics']),1)
        again = self.w.api.project(proposal)
        self.assertFalse(again['gate_passed'])
        self.assertTrue(again['incremental']['baseline_cache_hit'])
        self.assertTrue(again['incremental']['proposal_cache_hit'])

    def test_after_capture_can_run_without_recapturing_during_analysis(self):
        script = self.tree['children'][0]['children'][0]
        result = self.w.api.project({'studio_id':'test'},
                                    after_capture=lambda: script.update(source='BAD'))
        self.assertTrue(result['passed'])
        self.assertFalse(self.w.api.project({'studio_id':'test'})['passed'])

    def test_duplicate_names_resolve_dependency_to_first_sibling(self):
        children=self.tree['children'][0]['children']
        children[0].update(className='ModuleScript',source='return 1')
        children[1]['source']='local m = require(game.ServerScriptService.A)\nprint(m)'
        children.append({'name':'__MCP_Duplicate_1','actualName':'A','className':'ModuleScript','source':'return 2','children':[]})
        self.w.api.project({'studio_id':'test'})
        children[0]['source']='return 3'
        checked=self.w.api.project({'studio_id':'test'})
        self.assertEqual(len(self.calls[-1]),2)
        self.assertEqual(checked['ambiguous_source_paths'],[['ServerScriptService','A']])

if __name__=='__main__':unittest.main()
