import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'enhanced'))
from workbench import Workbench

class MetricsFaultTests(unittest.TestCase):
    def test_metrics_failure_does_not_duplicate_tool_responses(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'data';wb=Workbench(None,data,root)
            with wb.store.connect() as db:db.execute("CREATE TRIGGER fail_metrics BEFORE INSERT ON metrics BEGIN SELECT RAISE(ABORT,'injected metric failure'); END")
            wb.close()
            fake=root/'fake.py';fake.write_text('''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m:continue
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'capabilities':{},'serverInfo':{'name':'fake','version':'1'}}}),flush=True)
''')
            requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
                {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'extra_status','arguments':{}}},
                {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'extra_status','arguments':{}}}]
            result=subprocess.run([sys.executable,str(ROOT/'enhanced/server.py'),'--data-dir',str(data),'--upstream',sys.executable,str(fake)],
                input=''.join(json.dumps(v)+'\n' for v in requests),text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            replies=[json.loads(v) for v in result.stdout.splitlines()]
            self.assertEqual(sorted(v['id'] for v in replies),[1,2,3])
            self.assertFalse(any(v.get('result',{}).get('isError') for v in replies))
            self.assertIn('tool response preserved',result.stderr)

if __name__=='__main__':unittest.main()
