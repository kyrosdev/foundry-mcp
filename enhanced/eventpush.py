"""Loopback-authenticated Studio event delivery with a signal-buffer fallback."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
import uuid
from team import event, integer, utc

ROOT=Path(__file__).resolve().parent

class EventHub:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS subscriptions(
                id TEXT PRIMARY KEY,project TEXT,owner TEXT,studio TEXT,query TEXT,token TEXT,active INTEGER,
                mode TEXT,last_seq INTEGER,last_drain REAL,error TEXT);
    """

    def __init__(self,wb):
        self.wb=wb;self.store=wb.store;self.server=None;self.thread=None;self.lock=threading.Lock()
        self.running=set();self.closed=False

    def start_server(self):
        with self.lock:
            if self.server:return
            hub=self
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_POST(self):
                    try:
                        count=int(self.headers.get('Content-Length','0'))
                        if not 0<count<=131072:raise ValueError('Invalid body size')
                        identifier=self.path.removeprefix('/events/')
                        if self.path!='/events/'+identifier:raise ValueError('Invalid endpoint')
                        body=json.loads(self.rfile.read(count))
                        hub.accept(identifier,self.headers.get('X-MCP-Token',''),body)
                        self.send_response(200);self.end_headers();self.wfile.write(b'OK')
                    except Exception:
                        self.send_response(403);self.end_headers()
            self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            self.server.daemon_threads=True
            self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def accept(self,identifier,token,body):
        events=body.get('events')
        if not isinstance(events,list) or len(events)>100:raise ValueError('Invalid event batch')
        for value in events:
            if not isinstance(value,dict) or not isinstance(value.get('seq'),int):raise ValueError('Invalid event sequence')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM subscriptions WHERE id=?',(identifier,)).fetchone()
            if not row or not row['active'] or row['owner'] not in self.wb.agent_ids() or not hmac.compare_digest(row['token'],token):raise ValueError('Unknown subscription')
            fresh=[value for value in events if value['seq']>row['last_seq']]
            if fresh:
                sequence=max(value['seq'] for value in fresh)
                event(db,row['project'],row['owner'],'studio_events',{'subscription_id':identifier,'events':fresh,
                    'sequence_gap':min(v['seq'] for v in fresh)>row['last_seq']+1,'dropped':body.get('dropped',0),'source_coverage_limited':bool(body.get('source_coverage_limited')),'received_at':utc()})
                db.execute('UPDATE subscriptions SET last_seq=?,error=NULL WHERE id=?',(sequence,identifier))

    def query(self,studio,payload,file):
        code='local payload=game:GetService("HttpService"):JSONDecode('+json.dumps(json.dumps(payload,ensure_ascii=True))+')\n'+(ROOT/file).read_text(encoding='utf-8')
        response=self.wb.read_rpc('tools/call',{'name':'execute_luau','arguments':{'studio_id':studio,'datamodel_type':'Edit','code':code}})
        if response.get('isError'):raise ValueError(str(response.get('content')))
        return json.loads(next(v['text'] for v in response['content'] if v.get('type')=='text'))

    def call(self,args):
        project=args['project_id'];action=args['action']
        with self.store.connect() as db:self.wb.team.member(db,project)
        if action=='start':
            path=args.get('path',[]);properties=args.get('properties',[])
            if not isinstance(path,list) or not all(isinstance(v,str) for v in path) or len(path)>64:raise ValueError('Invalid path')
            if not isinstance(properties,list) or len(properties)>20 or not all(isinstance(v,str) for v in properties):raise ValueError('Invalid properties')
            self.start_server();identifier=uuid.uuid4().hex;token=secrets.token_urlsafe(32)
            payload={'id':identifier,'path':path,'properties':properties,'token':token,
                     'url':'http://127.0.0.1:'+str(self.server.server_port)+'/events/'+identifier}
            with self.store.connect() as db:
                count=sum(db.execute('SELECT COUNT(*) FROM subscriptions WHERE owner=? AND active=1',(owner,)).fetchone()[0] for owner in self.wb.agent_ids())
                if count>=8:raise ValueError('Maximum 8 signal subscriptions per connection')
                db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,1,?,0,0,NULL)',(identifier,project,self.wb.owner,args['studio_id'],json.dumps(payload),token,'starting'))
            try:
                result=self.query(args['studio_id'],payload,'subscribe.luau')
                with self.store.connect() as db:db.execute('UPDATE subscriptions SET mode=? WHERE id=?',(result['mode'],identifier))
            except Exception:
                with self.store.connect() as db:db.execute('UPDATE subscriptions SET active=0 WHERE id=?',(identifier,))
                raise
            return {'subscription_id':identifier,**result,'delivery':'extra_team_events','buffer_capacity':256}
        with self.store.connect() as db:
            if action=='list':return {'subscriptions':[dict(row) for row in db.execute('SELECT id,owner,studio,mode,last_seq,last_drain,active,error FROM subscriptions WHERE project=?',(project,))]}
            row=db.execute('SELECT * FROM subscriptions WHERE id=? AND project=?',(args['subscription_id'],project)).fetchone()
        if not row:raise ValueError('Subscription not found')
        if row['owner']!=self.wb.owner:raise ValueError('Only the subscription owner can manage it')
        if action=='stop':
            result=self.query(row['studio'],{'id':row['id'],'stop':True},'subscription_read.luau')
            with self.store.connect() as db:db.execute('UPDATE subscriptions SET active=0 WHERE id=?',(row['id'],))
            return result
        if action!='drain':raise ValueError('Invalid subscription action')
        return self.drain(dict(row))

    def due(self):
        if self.closed:return []
        with self.store.connect() as db:
            rows=[]
            for owner in self.wb.agent_ids():rows.extend(db.execute('SELECT * FROM subscriptions WHERE owner=? AND active=1',(owner,)).fetchall())
        return [dict(row) for row in rows if time.time()-row['last_drain']>=(5 if row['mode']=='push' else 1)]

    def drain(self,row):
        try:
            result=self.query(row['studio'],{'id':row['id'],'after':row['last_seq']},'subscription_read.luau')
            if not result.get('active'):raise ValueError('Studio listener expired or target was destroyed; subscribe again')
            self.accept(row['id'],row['token'],result)
            mode=result.get('mode',row['mode'])
            with self.store.connect() as db:db.execute('UPDATE subscriptions SET last_drain=?,error=NULL,mode=? WHERE id=?',(time.time(),mode,row['id']))
            return {'active':True,'events_received':len(result['events']),'mode':mode,'source_coverage_limited':bool(result.get('source_coverage_limited'))}
        except Exception as error:
            with self.store.connect() as db:
                if row['error']!=str(error):event(db,row['project'],row['owner'],'studio_listener_error',{'subscription_id':row['id'],'error':str(error)})
                db.execute('UPDATE subscriptions SET last_drain=?,error=? WHERE id=?',(time.time(),str(error),row['id']))
            return {'active':False,'error':str(error)}

    def close(self):
        self.closed=True
        with self.store.connect() as db:
            for owner in self.wb.agent_ids():db.execute('UPDATE subscriptions SET active=0 WHERE owner=?',(owner,))
        if self.server:self.server.shutdown();self.server.server_close();self.thread.join(timeout=1)
