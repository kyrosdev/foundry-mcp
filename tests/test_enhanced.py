import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('enhanced', ROOT / 'enhanced/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

class ExtensionTests(unittest.TestCase):
    def test_windows_launcher_uses_pinned_binary_and_preserves_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / 'mcp.bat'
            executable = launcher.parent / 'Versions' / 'version-test' / 'StudioMCP.exe'
            executable.parent.mkdir(parents=True)
            executable.touch()
            fallback = ['cmd.exe', '/d', '/c', str(launcher)]
            launcher.write_text(
                f'@echo off\nif exist "{executable}" ( "{executable}" %* ) else (for /f fallback)\n',
                encoding='utf-8',
            )
            self.assertEqual(server.windows_upstream(launcher), [str(executable)])
            executable.unlink()
            self.assertEqual(server.windows_upstream(launcher), fallback)
            launcher.write_text(f'@echo off\n"{executable}" --custom %*\n', encoding='utf-8')
            self.assertEqual(server.windows_upstream(launcher), fallback)

    def test_bundled_catalog_matches_live_definitions(self):
        from workbench import definitions
        bundled = json.loads((ROOT / 'enhanced/workbench_catalog.json').read_text(encoding='utf-8'))
        self.assertEqual(bundled, definitions())

    def test_validation(self):
        for args in ({}, {'studio_id': 'x', 'payload': []},
                     {'studio_id': 'x', 'payload': {}, 'datamodel_type': 'Server'}):
            with self.assertRaises(ValueError):
                server.build_call('batch_create', args)
        self.assertEqual(server.operation_paths('batch_create',{'items':[{'parent':['Workspace'],'class':'Model','name':'Owned'}]}),[['Workspace','Owned']])
        self.assertEqual(server.operation_paths('instance_actions',{'items':[{'action':'move','path':['Workspace','Old'],'parent':['ServerStorage']}]}),[['Workspace','Old'],['ServerStorage','Old']])

    def test_payload_is_data(self):
        hostile = '\"); error("injected") --\n雪'
        result = server.build_call('snapshot', {'studio_id': 'x', 'payload': {'path': [hostile]}})
        line = result['arguments']['code'].splitlines()[1]
        quoted = line.split('JSONDecode(', 1)[1][:-1]
        self.assertEqual(json.loads(json.loads(quoted)), {'path': [hostile]})
        self.assertEqual(result['arguments']['studio_id'], 'x')

    def test_catalog_compacts_prose_without_changing_argument_constraints(self):
        tool = {'name':'search_game_tree','description':'Complete upstream notes',
                'annotations':{'readOnlyHint':True},
                'inputSchema':{'type':'object','required':['studio_id'],
                               'properties':{'studio_id':{'type':'string','description':'Original field notes'},
                                             'max_depth':{'type':'integer','maximum':10,'description':'Original limit notes'}}}}
        compact = server.advertise(tool,native=True)
        full = server.advertise(tool,full=True)
        self.assertEqual(compact['annotations'],tool['annotations'])
        self.assertEqual(compact['inputSchema']['required'],tool['inputSchema']['required'])
        self.assertEqual(compact['inputSchema']['properties']['max_depth']['maximum'],10)
        self.assertEqual(compact['inputSchema']['properties']['as_agent'],{'type':'string'})
        self.assertEqual(full['description'],'Complete upstream notes')
        self.assertEqual(full['inputSchema']['properties']['max_depth']['description'],'Original limit notes')
        self.assertNotIn('as_agent',tool['inputSchema']['properties'])

    def test_end_to_end_forwarding(self):
        fake = '''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 if m.get('method')=='tools/list':
  if m.get('params',{}).get('cursor')=='page2':
   r={'tools':[{'name':'inspect_instance','description':'Full official inspection notes'}]}
  else:
   r={'tools':[{'name':'native_tool'}],'nextCursor':'page2'}
 else: r={'content':[{'type':'image','data':'abc','mimeType':'image/png'}], 'echo':m}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fake.py'
            path.write_text(fake)
            requests = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
                    'name': 'extra_snapshot', 'arguments': {'studio_id': 'abc', 'payload': {}}}},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'native_tool'}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'extra_help'}},
                {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'extra_help','arguments':{'tool':'inspect_instance'}}},
            ]
            proc = subprocess.run([sys.executable, str(ROOT / 'enhanced/server.py'),
                '--data-dir', str(Path(directory)/'data'), '--upstream', sys.executable, str(path)], input=''.join(json.dumps(m)+'\n' for m in requests),
                text=True, capture_output=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            replies = {r['id']: r for r in map(json.loads, proc.stdout.splitlines())}
            self.assertEqual(len(replies), 5)
            listing = replies[1]['result']
            self.assertEqual(listing['nextCursor'], 'page2')
            self.assertEqual(len(listing['tools']), 1 + len(server.definitions()))
            self.assertEqual(replies[2]['result']['echo']['params']['name'], 'execute_luau')
            self.assertEqual(replies[3]['result']['echo']['params'], requests[2]['params'])
            self.assertEqual(replies[3]['result']['content'][0]['type'], 'image')
            self.assertIn('Payload examples', replies[4]['result']['content'][0]['text'])
            full_tool = json.loads(replies[5]['result']['content'][0]['text'])
            self.assertEqual(full_tool['description'], 'Full official inspection notes')

if __name__ == '__main__':
    unittest.main()
