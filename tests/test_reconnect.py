from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'enhanced'))
from transport import CAPABILITIES_META, PROTOCOL_META, ResilientBridge

class ReconnectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);self.counter=root/'counter'
        script=root/'upstream.py'
        script.write_text('''import sys,json,os
from pathlib import Path
counter=Path(sys.argv[1])
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m:continue
 if m['method']=='metadata':
  print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':m['params'].get('_meta',{})}),flush=True)
  continue
 if m['method'] in ('read','write'):
  count=int(counter.read_text())+1 if counter.exists() else 1
  counter.write_text(str(count))
  if count==1:os._exit(9)
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'ok':True}}),flush=True)
''')
        self.bridge=ResilientBridge([sys.executable,str(script),str(self.counter)],lambda _:None)
        self.bridge.request('initialize',{'clientInfo':{'name':'test','version':'1'}})
        self.bridge.forward({'jsonrpc':'2.0','method':'notifications/initialized'})
    def tearDown(self):self.bridge.close();self.temp.cleanup()

    def test_read_reconnects_and_retries_once(self):
        self.assertTrue(self.bridge.request('read',{},read_only=True,timeout=5)['ok'])
        self.assertEqual(self.counter.read_text(),'2');self.assertEqual(self.bridge.reconnections,1)

    def test_uncertain_write_is_not_replayed(self):
        with self.assertRaises(ConnectionError):self.bridge.request('write',{},timeout=5)
        self.assertEqual(self.counter.read_text(),'1')
        self.assertTrue(self.bridge.request('status',{},read_only=True,timeout=5)['ok'])
        self.assertEqual(self.counter.read_text(),'1')

    def test_internal_requests_use_stateless_metadata_after_client_discovery(self):
        meta = {PROTOCOL_META:'2026-07-28',CAPABILITIES_META:{'elicitation':{}}}
        self.assertEqual(self.bridge.request('metadata',{'_meta':meta}),meta)
        self.assertEqual(self.bridge.request('metadata',{}),
                         {PROTOCOL_META:'2026-07-28',CAPABILITIES_META:{}})
if __name__=='__main__':unittest.main()
