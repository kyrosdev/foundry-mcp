import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'enhanced'))
from workbench import Workbench
from live import changes
from webinfo import WebInfo, validate_url, public_addresses, PageText, PublicHTTPS

class RealtimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.state='Edit';self.fail=False
        def rpc(method,params):
            if self.fail:return {'isError':True,'content':[{'type':'text','text':'Studio disconnected'}]}
            return {'content':[{'type':'text','text':'Current Studio Mode: '+self.state}]}
        self.wb=Workbench(rpc,Path(self.temp.name)/'data',self.temp.name)
        self.wb.call('team_join',{'project_id':'game','studio_id':'studio'})
    def tearDown(self):self.wb.close();self.temp.cleanup()

    def watch(self):
        result=self.wb.call('live_watch',{'project_id':'game','action':'start','studio_id':'studio','kind':'state','interval':2})
        with self.wb.store.connect() as db:
            return dict(db.execute('SELECT * FROM live_watches WHERE id=?',(result['watch_id'],)).fetchone())

    def test_watch_changes_not_duplicate_polls(self):
        watch=self.watch();self.wb.live.poll(watch);self.wb.live.poll(watch)
        events=self.wb.call('team_events',{'project_id':'game'})['events']
        self.assertEqual(len([e for e in events if e['kind']=='live_changed']),1)
        self.state='Play';self.wb.live.poll(watch)
        sample=self.wb.call('live_watch',{'project_id':'game','action':'sample','watch_id':watch['id']})
        self.assertTrue(sample['available']);self.assertFalse(sample['stale'])
        self.assertIn('Play',str(sample['sample']['value']))
        events=self.wb.call('team_events',{'project_id':'game'})['events']
        self.assertEqual(len([e for e in events if e['kind']=='live_changed']),2)

    def test_live_errors_and_stale_samples(self):
        watch=self.watch();self.wb.live.poll(watch)
        with self.wb.store.connect() as db:db.execute('UPDATE live_samples SET captured=0')
        sample=self.wb.call('live_watch',{'project_id':'game','action':'sample','watch_id':watch['id']})
        self.assertTrue(sample['stale'])
        self.fail=True;self.wb.live.poll(watch)
        sample=self.wb.call('live_watch',{'project_id':'game','action':'sample','watch_id':watch['id']})
        self.assertFalse(sample['available']);self.assertTrue(sample['stale']);self.assertIn('disconnected',sample['error'])

    def test_stopped_watch_cannot_publish_late_sample(self):
        watch=self.watch()
        self.wb.call('live_watch',{'project_id':'game','action':'stop','watch_id':watch['id']})
        self.wb.live.poll(watch)
        sample=self.wb.call('live_watch',{'project_id':'game','action':'sample','watch_id':watch['id']})
        self.assertFalse(sample['available'])

    def test_snapshot_diffs(self):
        old={'instances':[{'path':['A'],'class':'Part'}]}
        new={'instances':[{'path':['B'],'class':'Part'}]}
        value=changes(old,new)
        self.assertEqual(value['added'],[['B']]);self.assertEqual(value['removed'],[['A']])
        self.assertTrue(changes(old,{'instances':[{'path':['A']},{'path':['A']}]})['ambiguous_paths'])

    def test_web_url_and_dns_restrictions(self):
        for url in ('http://example.com','file:///secret','https://u:p@example.com','https://example.com:8443','https://example.com/\nsecret'):
            with self.assertRaises(ValueError):validate_url(url)
        for address in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','192.168.1.1'):
            with patch('webinfo.socket.getaddrinfo',return_value=[(2,1,6,'',(address,443))]):
                with self.assertRaises(ValueError):public_addresses('example.com')
        with patch('webinfo.socket.getaddrinfo',return_value=[(2,1,6,'',('1.1.1.1',443))]):
            self.assertTrue(public_addresses('example.com'))

    def test_html_does_not_execute_or_include_script(self):
        parser=PageText();parser.feed('<title>Title</title><script>steal()</script><p>Current data</p>')
        self.assertNotIn('steal',''.join(parser.parts));self.assertIn('Current data',''.join(parser.parts))

    def test_fetch_and_search_return_provenance(self):
        web=WebInfo()
        page={'url':'https://example.com/','status':200,'content_type':'text/html','fetched_at':'2026-11-05T00:00:00Z','body':'<title>Example</title><p>Current facts</p><a href="/docs">Docs</a>'}
        with patch('webinfo.download',return_value=page.copy()):
            result=web.call('web_fetch',{'url':'https://example.com/'})
        self.assertFalse(result['cached']);self.assertEqual(result['fetched_at'],page['fetched_at']);self.assertTrue(result['untrusted_content'])
        self.assertIn('https://example.com/docs',result['links'])
        rss={**page,'body':'<rss><channel><item><title>Source</title><link>https://example.com/article</link><description>Fresh facts</description></item></channel></rss>'}
        with patch.dict('os.environ',{'BRAVE_SEARCH_API_KEY':''}),patch('webinfo.download',return_value=rss):
            result=web.call('web_search',{'query':'facts'})
        self.assertEqual(result['results'][0]['url'],'https://example.com/article')

    def test_web_failure_is_not_cached_or_fabricated(self):
        with patch('webinfo.download',side_effect=TimeoutError('offline')):
            with self.assertRaises(TimeoutError):WebInfo().call('web_search',{'query':'facts'})

    def test_background_events_keep_logical_agent_author(self):
        identifier=self.wb.call('agent',{'action':'register','name':'observer'})['agent_id']
        with self.wb.actor(identifier):
            self.wb.call('team_join',{'project_id':'game','studio_id':'studio'})
            watch=self.watch()
        self.wb.live.poll(watch)
        events=self.wb.call('team_events',{'project_id':'game'})['events']
        changed=next(value for value in events if value['kind']=='live_changed')
        self.assertEqual(changed['actor'],identifier)

    def test_loopback_event_push_checks_token_and_deduplicates(self):
        hub=self.wb.live.push
        with patch.object(hub,'query',return_value={'mode':'push','active':True}):
            subscription=self.wb.call('live_subscribe',{'project_id':'game','action':'start','studio_id':'studio','path':[]})
        with self.wb.store.connect() as db:row=dict(db.execute('SELECT * FROM subscriptions WHERE id=?',(subscription['subscription_id'],)).fetchone())
        url='http://127.0.0.1:'+str(hub.server.server_port)+'/events/'+row['id']
        body=json.dumps({'events':[{'seq':1,'kind':'added','path':['Workspace','Part']}]}).encode()
        with self.assertRaises(urllib.error.HTTPError) as denied:urllib.request.urlopen(urllib.request.Request(url,data=body,headers={'X-MCP-Token':'wrong'}),timeout=3)
        denied.exception.close()
        for _ in range(2):
            with urllib.request.urlopen(urllib.request.Request(url,data=body,headers={'X-MCP-Token':row['token']}),timeout=3) as response:self.assertEqual(response.status,200)
        events=self.wb.call('team_events',{'project_id':'game'})['events']
        self.assertEqual(len([value for value in events if value['kind']=='studio_events']),1)

if __name__=='__main__':unittest.main()
