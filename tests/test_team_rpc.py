"""Two independent MCP processes collaborating over one shared SQLite workspace."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'enhanced'))
from transport import Bridge

class TeamRPCTests(unittest.TestCase):
    def test_two_independent_clients_work_and_review(self):
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory)
            fake=temp/'upstream.py'
            fake.write_text('''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m:continue
 r={'tools':[]} if m['method']=='tools/list' else {'protocolVersion':'2025-03-26','capabilities':{},'serverInfo':{'name':'fake','version':'1'}}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)
''')
            children=[]
            try:
                clients=[]
                for label in ('Builder','Reviewer'):
                    child=subprocess.Popen([sys.executable,str(ROOT/'enhanced/server.py'),
                        '--data-dir',str(temp/'shared'),'--session-label',label,
                        '--upstream',sys.executable,str(fake)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
                    children.append(child);bridge=Bridge(child,lambda _:None)
                    bridge.request('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':label,'version':'1'}},timeout=15)
                    bridge.send({'jsonrpc':'2.0','method':'notifications/initialized'})
                    clients.append(bridge)
                def call(client,name,args):
                    result=client.request('tools/call',{'name':'extra_'+name,'arguments':{'project_id':'integration',**args}},timeout=15)
                    if result.get('isError'):raise ValueError(str(result['content']))
                    return json.loads(result['content'][0]['text'])
                a,b=clients
                first=call(a,'team_join',{'studio_id':'same-studio','capabilities':['luau']})
                second=call(b,'team_join',{'studio_id':'same-studio','capabilities':['luau']})
                self.assertNotEqual(first['session_id'],second['session_id'])
                task=call(a,'task_create',{'title':'Implement combat','require_receipts':False,'paths':[['ServerScriptService','Combat']]})
                task=call(a,'task_claim',{'task_id':task['id']})
                with self.assertRaises(ValueError):call(b,'task_claim',{'task_id':task['id']})
                call(a,'team_message',{'recipient':second['session_id'],'body':'Please verify combat invariants'})
                inbox=call(b,'team_inbox',{})
                self.assertEqual(inbox['messages'][0]['body'],'Please verify combat invariants')
                review=call(a,'task_update',{'task_id':task['id'],'expected_version':task['version'],
                    'state':'review','summary':'Combat changes ready','evidence':['unit-tests: combat invariants passed']})
                done=call(b,'task_review',{'task_id':task['id'],'expected_version':review['version'],
                    'approve':True,'notes':'Reviewed evidence and interface contract'})
                self.assertEqual(done['state'],'done')
                board=call(b,'team_board',{})
                self.assertEqual(len(board['members']),2)
                self.assertEqual(board['tasks'][0]['reviewed_by'],second['session_id'])
                # Two subagents can also collaborate through ONE MCP connection.
                logical=[]
                for name in ('Subagent Builder','Subagent Reviewer'):
                    result=a.request('tools/call',{'name':'extra_agent','arguments':{'action':'register','name':name}},timeout=15)
                    logical.append(json.loads(result['content'][0]['text'])['agent_id'])
                for identifier in logical:call(a,'team_join',{'as_agent':identifier,'studio_id':'same-studio'})
                with self.assertRaises(ValueError):call(b,'team_board',{'as_agent':logical[0]})
                task=call(a,'task_create',{'as_agent':logical[0],'title':'Logical task','require_receipts':False})
                task=call(a,'task_claim',{'as_agent':logical[0],'task_id':task['id']})
                task=call(a,'task_update',{'as_agent':logical[0],'task_id':task['id'],'expected_version':task['version'],
                    'state':'review','summary':'Ready for separate review','evidence':['checked shared contract']})
                done=call(a,'task_review',{'as_agent':logical[1],'task_id':task['id'],'expected_version':task['version'],'approve':True,'notes':'Independent logical review'})
                self.assertEqual(done['owner'],logical[0]);self.assertEqual(done['reviewed_by'],logical[1])
            finally:
                for child in children:
                    child.stdin.close()
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
                    error=child.stderr.read();child.stderr.close();child.stdout.close()
                    self.assertEqual(child.returncode,0,error)

if __name__=='__main__':unittest.main()
