import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'enhanced'))
from workbench import Workbench

class RobloxAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.w=Workbench(None,self.temp.name,self.temp.name)
    def tearDown(self):self.w.close();self.temp.cleanup()

    def test_real_roblox_types_and_nonexistent_api(self):
        valid='local p: Part = Instance.new("Part")\np.Position = Vector3.new(1, 2, 3)\nprint(game:GetService("Players"):GetPlayers(), p.Position)\n'
        self.assertTrue(self.w.code_tool(valid)['passed'])
        wrong_type=self.w.code_tool('local p: Part = Instance.new("Part")\np.Position = "wrong"\n')['tools']['luau-analyze']
        self.assertFalse(wrong_type['passed']);self.assertIn('Vector3',wrong_type['output'])
        nonexistent=self.w.code_tool('local p = Instance.new("Part")\np:ImaginaryMethod()\n')['tools']['luau-analyze']
        self.assertFalse(nonexistent['passed']);self.assertIn('ImaginaryMethod',nonexistent['output'])

    def test_inherited_api_catalog_and_security(self):
        result=self.w.api.call('api_lookup',{'class_name':'Part','member':'Position'})
        self.assertTrue(result['member_exists']);self.assertEqual(result['member']['declared_in'],'BasePart')
        self.assertIn('Security',result['member']);self.assertTrue(result['source'].startswith('https://setup.rbxcdn.com/'))
        self.assertFalse(self.w.api.call('api_lookup',{'class_name':'Part','member':'ImaginaryMethod'})['member_exists'])

    def test_cross_module_analysis_and_replacement(self):
        consumer='local M = require(game:GetService("ReplicatedStorage").MathAPI)\nlocal n: number = M.double("wrong")\nprint(n)\n'
        tree={'name':'game','className':'DataModel','children':[
            {'name':'ReplicatedStorage','className':'ReplicatedStorage','children':[{'name':'MathAPI','className':'ModuleScript','source':'local M = {}\nfunction M.double(value: number): number return value * 2 end\nreturn M\n','children':[]}]},
            {'name':'ServerScriptService','className':'ServerScriptService','children':[{'name':'Consumer','className':'Script','source':consumer,'children':[]}]}]}
        self.w.api.export=lambda studio:{'complete':True,'nodes':5,'captured_at':'test','tree':copy.deepcopy(tree)}
        invalid=self.w.api.project({'studio_id':'test'})
        self.assertFalse(invalid['passed']);self.assertIn('number',invalid['output'])
        valid=self.w.api.project({'studio_id':'test','path':['ServerScriptService','Consumer'],'source':consumer.replace('"wrong"','2')})
        self.assertTrue(valid['passed'],valid['output']);self.assertEqual(valid['scripts_analyzed'],2)
        self.assertIn('"wrong"',tree['children'][1]['children'][0]['source'])
        tree['children'][1]['children'][0]['source']=consumer.replace('"wrong"','2')
        proposed=self.w.api.project({'studio_id':'test','path':['ServerScriptService','NewScript'],'source':'print("new script")\n','create_class':'Script'})
        self.assertTrue(proposed['passed'],proposed['output'])
        self.assertEqual(proposed['scripts_analyzed'],3)
        duplicate=copy.deepcopy(tree['children'][0]['children'][0])
        duplicate.update(name='__MCP_Duplicate_test',actualName='MathAPI')
        tree['children'][0]['children'].append(duplicate)
        with self.assertRaises(ValueError):self.w.api.project({'studio_id':'test','path':['ReplicatedStorage','MathAPI'],'source':'return {}'})

    def test_incomplete_export_fails_closed(self):
        self.w.rpc=lambda method,params:{'content':[{'type':'text','text':'{"complete":false}'}]}
        with self.assertRaises(ValueError):self.w.api.export('test')

    def test_chunked_unicode_snapshot_reassembles_without_truncation(self):
        expected={'complete':True,'tree':{'name':'game','className':'DataModel','children':[]},'extra':'雪'*1000}
        raw=json.dumps(expected,ensure_ascii=False).encode('utf-8');offset=0
        def rpc(method,params):
            nonlocal offset
            block=raw[offset:offset+71];offset+=len(block)
            value={'token':'test-token','hex':block.hex(),'next_offset':offset,'done':offset==len(raw),'bytes':len(raw)}
            return {'content':[{'type':'text','text':json.dumps(value)}]}
        self.w.rpc=rpc
        self.assertEqual(self.w.api.export('test'),expected)

    def test_large_script_read_uses_chunk_transport(self):
        source='-- large 雪\n'*12000
        raw=json.dumps({'source':source,'class':'ModuleScript'},ensure_ascii=False).encode('utf-8');offset=0
        def rpc(method,params):
            nonlocal offset
            block=raw[offset:offset+16000];offset+=len(block)
            packet={'token':'source-token','hex':block.hex(),'next_offset':offset,'done':offset==len(raw),'bytes':len(raw)}
            return {'content':[{'type':'text','text':json.dumps(packet)}]}
        self.w.rpc=rpc
        self.assertEqual(self.w.get('test',['ServerScriptService','Large'])['source'],source)

    def test_refresh_failure_retains_previous_snapshot(self):
        old=self.w.api.directory()
        def fetch(url,**kwargs):
            if url.endswith('versionQTStudio'):return io.BytesIO(b'version-test')
            if url.endswith('API-Dump.json'):return io.BytesIO(b'{"Classes":[{}],"Enums":[{}]}')
            return io.BytesIO(b'invalid definitions')
        with patch('roblox_api.urllib.request.urlopen',side_effect=fetch),patch.object(self.w.api,'analyze',return_value={'passed':False,'output':'bad definitions'}):
            with self.assertRaises(ValueError):self.w.api.refresh()
        self.assertEqual(self.w.api.directory(),old)

if __name__=='__main__':unittest.main()
