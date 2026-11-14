"""Fresh Studio reads and bounded, shared near-real-time observation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
from team import utc, integer, event
from eventpush import EventHub

ROOT=Path(__file__).resolve().parent

def schema(tool):
    props={'studio_id':{'type':'string'},'kind':{'enum':['state','console','script','snapshot']},
           'path':{'type':'array','maxItems':64,'items':{'type':'string'}},
           'properties':{'type':'array','maxItems':30,'items':{'type':'string'}},
           'datamodel_type':{'enum':['Edit','Client','Server'],'default':'Edit'},
           'depth':{'type':'integer','minimum':1,'maximum':32,'default':4},
           'limit':{'type':'integer','minimum':1,'maximum':1000,'default':200}}
    return [
        tool('live_read','Query Studio now: state, console, editor source or bounded instance snapshot. Returns collection time, elapsed time and content hash; no cached state.',props,('studio_id','kind'),True),
        tool('live_watch','Start/stop/list a bounded Studio observer or read its latest sample. Changes are shared through team_events. Poll interval is 2..60 seconds; runs only while this connection lives.',
             {**props,'project_id':{'type':'string'},'action':{'enum':['start','stop','list','sample']},'watch_id':{'type':'string'},
              'interval':{'type':'integer','minimum':2,'maximum':60,'default':5}},('project_id','action')),
        tool('live_subscribe','Capture Studio signals between snapshots. Uses authenticated loopback push when Studio permits local HTTP; otherwise drains a bounded signal buffer. Reports delivery mode and sequence gaps.',
             {'project_id':{'type':'string'},'action':{'enum':['start','list','drain','stop']},'subscription_id':{'type':'string'},
              'studio_id':{'type':'string'},'path':{'type':'array','items':{'type':'string'}},'properties':{'type':'array','items':{'type':'string'},'maxItems':20}},('project_id','action')),
    ]

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True).encode()).hexdigest()

def changes(old,new):
    if not old:return {'initial':True}
    if not isinstance(old,dict) or not isinstance(new,dict):return {'changed':True}
    if 'source' in old and 'source' in new:
        return {'source_changed':old['source']!=new['source'],'previous_hash':old.get('hash'),'hash':new.get('hash')}
    if 'instances' in old and 'instances' in new:
        before={json.dumps(v['path']):v for v in old['instances']}
        after={json.dumps(v['path']):v for v in new['instances']}
        if len(before)!=len(old['instances']) or len(after)!=len(new['instances']):
            return {'changed':True,'ambiguous_paths':True,'note':'Duplicate sibling paths prevent reliable per-instance diffs'}
        return {'added':[after[k]['path'] for k in after.keys()-before.keys()][:100],
                'removed':[before[k]['path'] for k in before.keys()-after.keys()][:100],
                'changed':[after[k]['path'] for k in after.keys()&before.keys() if after[k]!=before[k]][:100],
                'bounded':True}
    return {'changed':True}

class Observatory:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS live_watches(id TEXT PRIMARY KEY,project TEXT,owner TEXT,
      query TEXT,interval INTEGER,next_poll REAL,active INTEGER);
    CREATE TABLE IF NOT EXISTS live_samples(watch TEXT PRIMARY KEY,hash TEXT,value TEXT,captured REAL,error TEXT);
    """

    def __init__(self,workbench,team):
        self.wb=workbench;self.team=team;self.store=workbench.store
        self.stop=threading.Event();self.thread=None
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='studio-observer')
        self.running=set();self.lock=threading.Lock()
        self.push=EventHub(workbench)

    def start(self):
        self.thread=threading.Thread(target=self.loop,daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=1)
        self.pool.shutdown(wait=False,cancel_futures=True)
        self.push.close()
        with self.store.connect() as db:
            for identifier in self.wb.agent_ids():db.execute('UPDATE live_watches SET active=0 WHERE owner=?',(identifier,))

    def read(self,args):
        started=time.time();kind=args['kind'];studio=args['studio_id']
        if not isinstance(studio,str) or not studio:raise ValueError('studio_id required')
        mode=args.get('datamodel_type','Edit')
        if mode not in ('Edit','Client','Server'):raise ValueError('Invalid datamodel_type')
        if kind=='script':
            if mode!='Edit':raise ValueError('Live editor script reads require Edit mode')
            value=self.wb.get(studio,args['path'])
        else:
            if kind in ('state','console'):
                params={'name':'get_studio_state' if kind=='state' else 'get_console_output','arguments':{'studio_id':studio}}
            elif kind=='snapshot':
                path=args.get('path',[]);properties=args.get('properties',[])
                if not isinstance(path,list) or len(path)>64 or not all(isinstance(p,str) for p in path):raise ValueError('Invalid path')
                if not isinstance(properties,list) or len(properties)>30 or not all(isinstance(p,str) for p in properties):raise ValueError('Invalid properties')
                payload={'path':path,'properties':properties,'depth':integer(args.get('depth',4),1,32,'depth'),
                         'limit':integer(args.get('limit',200),1,1000,'limit')}
                raw=json.dumps(payload,ensure_ascii=True)
                code='local operation="snapshot"\nlocal payload=game:GetService("HttpService"):JSONDecode('+json.dumps(raw)+')\n'
                code+=(ROOT/'operations.luau').read_text(encoding='utf-8')
                params={'name':'execute_luau','arguments':{'studio_id':studio,'datamodel_type':mode,'code':code}}
            else:raise ValueError('Unknown live query kind')
            response=self.wb.read_rpc('tools/call',params)
            if response.get('isError'):raise ValueError(json.dumps(response.get('content')))
            value={'content':response.get('content',[])}
            if kind=='snapshot':
                texts=[v.get('text','') for v in value['content'] if v.get('type')=='text']
                if len(texts)!=1:raise ValueError('Unexpected snapshot response')
                value=json.loads(texts[0])
            if kind=='console':
                for item in value['content']:
                    if item.get('type')=='text' and len(item.get('text',''))>32000:
                        item['text']=item['text'][-32000:];value['truncated']=True
        if len(json.dumps(value,ensure_ascii=True))>1_000_000:raise ValueError('Live sample exceeds 1 MB; narrow the query')
        return {'studio_id':studio,'kind':kind,'datamodel_type':mode,'captured_at':utc(),
                'collection_started_at':utc(started),'elapsed_ms':round((time.time()-started)*1000),
                'hash':fingerprint(value),'value':value,'cached':False}

    def call(self,name,args):
        if name=='live_subscribe':return self.push.call(args)
        if name=='live_read':return self.read(args)
        project=args['project_id'];action=args['action']
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.team.member(db,project)
            if action=='start':
                interval=integer(args.get('interval',5),2,60,'interval')
                query={k:v for k,v in args.items() if k not in ('project_id','action','watch_id','interval')}
                if query.get('kind') not in ('state','console','script','snapshot') or not query.get('studio_id'):raise ValueError('Watch requires studio_id and kind')
                count=sum(db.execute('SELECT COUNT(*) FROM live_watches WHERE owner=? AND active=1',(identifier,)).fetchone()[0] for identifier in self.wb.agent_ids())
                if count>=8:raise ValueError('Maximum 8 active watches per connection')
                identifier=uuid.uuid4().hex
                db.execute('INSERT INTO live_watches VALUES(?,?,?,?,?,?,1)',(identifier,project,self.wb.owner,json.dumps(query),interval,time.time()))
                event(db,project,self.wb.owner,'watch_started',{'watch_id':identifier,'query':query,'interval':interval})
                return {'watch_id':identifier,'interval_seconds':interval,'delivery':'extra_team_events','next_poll':'immediate'}
            if action=='list':
                out=[]
                for row in db.execute('SELECT w.*,m.heartbeat,m.connected FROM live_watches w LEFT JOIN team_members m ON m.project=w.project AND m.session=w.owner WHERE w.project=? ORDER BY w.next_poll DESC LIMIT 100',(project,)):
                    item=dict(row);item['query']=json.loads(item['query']);item['owner_online']=bool(row['connected'] and time.time()-(row['heartbeat'] or 0)<35);out.append(item)
                return {'watches':out}
            row=db.execute('SELECT * FROM live_watches WHERE id=? AND project=?',(args['watch_id'],project)).fetchone()
            if not row:raise ValueError('Watch not found')
            if action=='stop':
                if row['owner']!=self.wb.owner:raise ValueError('Only the watcher owner can stop it')
                db.execute('UPDATE live_watches SET active=0 WHERE id=?',(row['id'],))
                event(db,project,self.wb.owner,'watch_stopped',{'watch_id':row['id']})
                return {'stopped':True}
            if action!='sample':raise ValueError('Invalid watch action')
            sample=db.execute('SELECT * FROM live_samples WHERE watch=?',(row['id'],)).fetchone()
            if not sample:return {'available':False,'watch_id':row['id']}
            age=time.time()-sample['captured']
            member=db.execute('SELECT connected,heartbeat FROM team_members WHERE project=? AND session=?',(project,row['owner'])).fetchone()
            stale=bool(sample['error']) or not row['active'] or age>row['interval']*2 or not member or not member['connected'] or time.time()-member['heartbeat']>=35
            return {'available':bool(sample['value']),'watch_id':row['id'],'cached':True,'age_seconds':round(age,3),
                    'stale':bool(stale),'error':sample['error'],'sample':json.loads(sample['value']) if sample['value'] else None}

    def loop(self):
        while not self.stop.wait(0.5):
            try:
                with self.store.connect() as db:
                    rows=[]
                    for identifier in self.wb.agent_ids():
                        rows.extend(db.execute('SELECT * FROM live_watches WHERE owner=? AND active=1 AND next_poll<=? LIMIT 8',(identifier,time.time())).fetchall())
                for row in rows:
                    with self.lock:
                        if row['id'] in self.running:continue
                        self.running.add(row['id'])
                    self.pool.submit(self.poll,dict(row))
                for row in self.push.due():
                    key='subscription:'+row['id']
                    with self.lock:
                        if key in self.running:continue
                        self.running.add(key)
                    self.pool.submit(self.poll_subscription,row)
            except Exception:
                if self.stop.is_set():break

    def poll(self,row):
        try:
            try:sample=self.read(json.loads(row['query']));error=None
            except Exception as exc:sample=None;error=str(exc)[:4000]
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                active=db.execute('SELECT active FROM live_watches WHERE id=?',(row['id'],)).fetchone()
                if not active or not active['active'] or self.stop.is_set():return
                prior=db.execute('SELECT * FROM live_samples WHERE watch=?',(row['id'],)).fetchone()
                hashed=sample['hash'] if sample else fingerprint({'error':error})
                if not prior or prior['hash']!=hashed:
                    previous=json.loads(prior['value']) if prior and prior['value'] else None
                    delta=changes(previous['value'] if previous else None,sample['value']) if sample else {'error':error}
                    event(db,row['project'],row['owner'],'live_changed' if sample else 'live_error',
                          {'watch_id':row['id'],'hash':hashed,'captured_at':utc(),'changes':delta})
                db.execute('INSERT OR REPLACE INTO live_samples VALUES(?,?,?,?,?)',
                    (row['id'],hashed,json.dumps(sample) if sample else None,time.time(),error))
                db.execute('UPDATE live_watches SET next_poll=? WHERE id=?',(time.time()+row['interval'],row['id']))
        finally:
            with self.lock:self.running.discard(row['id'])

    def poll_subscription(self,row):
        try:self.push.drain(row)
        finally:
            with self.lock:self.running.discard('subscription:'+row['id'])
