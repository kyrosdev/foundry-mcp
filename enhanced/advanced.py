"""Persistent project identity, staged script transactions and verifiable test receipts."""
import hashlib
import json
from pathlib import Path
import time
import uuid
import threading
from team import text, utc

ROOT=Path(__file__).resolve().parent

def schema(tool):
    string={'type':'string'}
    return [
        tool('project_bind','Bind a Studio window to a permanent project key for history/sync. Rebind new windows to the same key after restarting Studio. Optional adopt_from migrates explicit legacy window IDs.',
             {'studio_id':string,'project_id':string,'action':{'enum':['bind','status'],'default':'bind'},'adopt_from':{'type':'array','items':string,'maxItems':20}},('studio_id',)),
        tool('changes','Prepare, inspect, apply or roll back a multi-script change set. Preparation checks the combined proposal. Apply rechecks live sources and uses one Studio undo recording with conditional rollback on failure.',
             {'action':{'enum':['prepare','status','apply','rollback']},'studio_id':string,'plan_id':string,
              'items':{'type':'array','minItems':1,'maxItems':30,'items':{'type':'object','properties':{
                  'path':{'type':'array','items':string,'minItems':1},'source':string,'expected_hash':string},'required':['path','source','expected_hash'],'additionalProperties':False}}},('action',)),
        tool('recover','List operation journal entries or reconcile a script operation against current Studio sources. Uncertain writes are never blindly replayed.',
             {'operation_id':string,'studio_id':string,'limit':{'type':'integer','minimum':1,'maximum':100}},readonly=True),
        tool('evidence','Run a real code/project check or playtest for your active task and persist an immutable, hashed receipt. Task completion can require passed receipts; source hashes are rechecked during review.',
             {'project_id':string,'task_id':string,'kind':{'enum':['code_check','project_check','playtest']},'arguments':{'type':'object'}},('project_id','task_id','kind')),
        tool('receipt','Read and integrity-check a server-generated evidence receipt.',{'receipt_id':string},('receipt_id',),True),
        tool('metrics','Summarize measured tool calls and latency. No claim of superiority is inferred from tool counts.',{},readonly=True),
    ]

class Advanced:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,studio TEXT,project TEXT,owner TEXT,
      state TEXT,kind TEXT,before_json TEXT,after_json TEXT,result TEXT,created REAL,updated REAL);
    CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,project TEXT,task TEXT,owner TEXT,
      kind TEXT,passed INTEGER,body TEXT,hash TEXT,created REAL);
    CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY,tool TEXT,owner TEXT,elapsed REAL,success INTEGER,created REAL);
    """

    def __init__(self,wb):
        self.wb=wb;self.store=wb.store
        self.binding_lock=threading.Lock();self.checked_bindings=set();self.binding_warnings={}


    def auto_bind(self,studio,observed_identity=None):
        with self.binding_lock:
            if studio in self.checked_bindings:return
            if self.store.project_key(studio)!=studio:
                self.checked_bindings.add(studio);self.binding_warnings.pop(studio,None);return
            try:
                value=observed_identity
                if value is None:
                    code='return game:GetService("HttpService"):JSONEncode({place_id=game.PlaceId,universe_id=game.GameId})'
                    reply=self.wb.read_rpc('tools/call',{'name':'execute_luau','arguments':{'studio_id':studio,'datamodel_type':'Edit','code':code}})
                    if reply.get('isError'):raise ValueError('Studio identity query failed')
                    value=json.loads(next(v['text'] for v in reply['content'] if v.get('type')=='text'))
                place=value['place_id'];universe=value['universe_id']
                if not isinstance(place,int) or not isinstance(universe,int):raise ValueError('Invalid Studio identity')
                if place>0:self.bind({'studio_id':studio,'project_id':'roblox:'+str(universe)+':'+str(place)})
                self.checked_bindings.add(studio)
                self.binding_warnings.pop(studio,None)
            except Exception as error:
                self.binding_warnings[studio]=str(error)

    def bind(self,args):
        studio=text(args['studio_id'],'studio_id',200)
        if args.get('action')=='status':
            if self.wb.auto_bind_projects:self.auto_bind(studio)
            key=self.store.project_key(studio)
            return {'studio_id':studio,'history_namespace':key,'persistent':key!=studio,'project_id':key.removeprefix('project:') if key!=studio else None}
        if args.get('action','bind')!='bind':raise ValueError('Invalid binding action')
        project='project:'+text(args.get('project_id'),'project_id',128)
        legacy=args.get('adopt_from',[])
        if not isinstance(legacy,list) or len(legacy)>20 or not all(isinstance(v,str) for v in legacy):raise ValueError('Invalid adopt_from list')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT project FROM studio_aliases WHERE studio=?',(studio,)).fetchone()
            if old and old['project']!=project:raise ValueError('This window is already bound to another project')
            for identifier in [studio]+legacy:
                prior=db.execute('SELECT project FROM studio_aliases WHERE studio=?',(identifier,)).fetchone()
                if prior and prior['project']!=project:raise ValueError('Cannot adopt a window bound to another project')
                rows=db.execute('SELECT * FROM sync WHERE studio=?',(identifier,)).fetchall()
                for row in rows:
                    if db.execute('SELECT 1 FROM sync WHERE studio=? AND path=?',(project,row['path'])).fetchone():
                        raise ValueError('Conflicting sync mappings require explicit resolution; migration cancelled')
                db.execute('UPDATE revisions SET studio=? WHERE studio=?',(project,identifier))
                db.execute('UPDATE sync SET studio=? WHERE studio=?',(project,identifier))
                db.execute('UPDATE operations SET project=? WHERE project=?',(project,identifier))
                db.execute('INSERT OR REPLACE INTO studio_aliases VALUES(?,?)',(identifier,project))
        return {'studio_id':studio,'project_id':args['project_id'],'history_namespace':project,'persistent':True,
                'note':'Locks and live observations remain scoped to the runtime Studio window.'}

    def begin(self,studio,before,after,kind='scripts',state='prepared'):
        identifier=uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identifier,studio,self.store.project_key(studio),self.wb.owner,
                state,kind,json.dumps(before),json.dumps(after),'{}',time.time(),time.time()))
        return identifier

    def finish(self,identifier,state,result):
        with self.store.connect() as db:db.execute('UPDATE operations SET state=?,result=?,updated=? WHERE id=?',(state,json.dumps(result),time.time(),identifier))

    def operation(self,identifier):
        with self.store.connect() as db:row=db.execute('SELECT * FROM operations WHERE id=?',(identifier,)).fetchone()
        if not row:raise ValueError('Unknown operation')
        out=dict(row)
        for field in ('before_json','after_json','result'):out[field]=json.loads(out[field])
        return out

    def reconcile(self,row,studio):
        if self.wb.auto_bind_projects:self.auto_bind(studio)
        if self.store.project_key(studio)!=row['project']:raise ValueError('Rebind this window to the operation project before recovery')
        if row['kind']!='scripts':return {'state':'uncertain','operation_id':row['id'],'note':'Opaque operation: inspect the actual Studio state; no automatic replay is safe.'}
        states=[]
        for old,new in zip(row['before_json'],row['after_json']):
            source=self.wb.get(studio,old['path'])['source']
            states.append('before' if source==old['source'] else 'after' if source==new['source'] else 'other')
        state='not_applied' if all(v=='before' for v in states) else 'committed' if all(v=='after' for v in states) else 'conflict'
        self.finish(row['id'],state,{'reconciled':True,'path_states':states})
        return {'operation_id':row['id'],'state':state,'path_states':states,'replayed':False,
                'note':'Sources were observed at reconciliation time. This does not cancel a dispatched call; retained mutation leases are not forcibly released.'}

    def prepare(self,args):
        studio=args['studio_id'];items=args['items']
        if not isinstance(items,list) or not 1<=len(items)<=30:raise ValueError('Expected 1..30 script changes')
        before=[];after=[];seen=set()
        for item in items:
            path=item['path'];key=json.dumps(path)
            if key in seen:raise ValueError('Duplicate script path in change set')
            seen.add(key)
            current=self.wb.get(studio,path)
            if current['hash']!=item['expected_hash']:raise ValueError('Source changed before preparation: '+key)
            source=self.wb.code_tool(item['source'],True)['source']
            # Lint individual files, then type-check the coordinated proposal once.
            lint=self.wb.code_tool(source,lint_only=True,context={'baseline_source':current['source']})
            if not lint.get('gate_passed',lint['passed']):return {'blocked':True,'path':path,'checks':lint}
            before.append({'path':path,'source':current['source']});after.append({'path':path,'source':source})
        if len(json.dumps([before,after],ensure_ascii=True))>800000:raise ValueError('Change set exceeds 800 KB transport budget')
        checks=self.wb.api.project({'studio_id':studio,'changes':after})
        if not checks.get('gate_passed',checks['passed']):return {'blocked':True,'checks':checks}
        identifier=self.begin(studio,before,after)
        from workbench import diff
        return {'plan_id':identifier,'state':'prepared','checks':checks,'changes':[{'path':b['path'],'diff':diff(a['source'],b['source'])} for a,b in zip(before,after)]}

    def apply(self,row,studio,rollback=False):
        if self.wb.auto_bind_projects:self.auto_bind(studio)
        if self.store.project_key(studio)!=row['project']:raise ValueError('Change set belongs to a different project')
        if row['state'] not in (('committed',) if rollback else ('prepared','not_applied')):raise ValueError('Reconcile an uncertain operation before applying or rolling it back')
        before=row['after_json'] if rollback else row['before_json'];after=row['before_json'] if rollback else row['after_json']
        with self.store.lease('studio:'+studio,self.wb.owner):
            for item in before:
                self.wb.assert_script_lock(studio,item['path'])
                if self.wb.get(studio,item['path'])['source']!=item['source']:raise ValueError('Live source changed; change set was not applied')
            checks=self.wb.api.project({'studio_id':studio,'changes':after})
            if not rollback and not checks.get('gate_passed',checks['passed']):return {'blocked':True,'checks':checks}
            operation=self.begin(studio,before,after,state='applying') if rollback else row['id']
            self.finish(operation,'applying',{'checks':checks})
            for old in before:self.store.record(studio,old['path'],old['source'],'before_change_set',self.wb.owner)
            payload={'items':[{'path':old['path'],'expected':old['source'],'source':new['source']} for old,new in zip(before,after)]}
            code='local payload=game:GetService("HttpService"):JSONDecode('+json.dumps(json.dumps(payload,ensure_ascii=True))+')\n'+(ROOT/'changes.luau').read_text(encoding='utf-8')
            try:
                result=self.wb.rpc('tools/call',{'name':'execute_luau','arguments':{'studio_id':studio,'datamodel_type':'Edit','code':code}})
                if result.get('isError'):raise RuntimeError(str(result.get('content')))
                body=json.loads(next(v['text'] for v in result['content'] if v.get('type')=='text'))
            except (TimeoutError,ConnectionError):
                self.finish(operation,'uncertain',{'note':'Connection lost after dispatch; reconcile before any retry'})
                raise
            except Exception as error:
                # Backend may have executed before an invalid/truncated reply.
                self.finish(operation,'uncertain',{'error':str(error)})
                raise
            if not body['success']:
                self.finish(operation,'conflict' if body.get('rollback_errors') else 'not_applied',body)
                return {'operation_id':operation,'blocked':True,**body}
            self.finish(operation,'committed',body)
            for new in after:self.store.record(studio,new['path'],new['source'],'after_change_set',self.wb.owner)
            if rollback:self.finish(row['id'],'rolled_back',{'rollback_operation':operation})
        return {'operation_id':operation,'state':'committed','rolled_back':rollback,'checks':checks,**body}

    def evidence(self,args):
        project=args['project_id'];task_id=args['task_id'];kind=args['kind'];arguments=dict(args.get('arguments',{}))
        with self.store.connect() as db:
            task=self.wb.team.task(db,project,task_id)
            if task['owner']!=self.wb.owner or task['state']!='active' or task['expires']<time.time():raise ValueError('Evidence requires your active task')
            studio=task['studio'];scopes=json.loads(task['paths'])
        if studio and self.wb.auto_bind_projects:self.auto_bind(studio)
        if kind not in ('code_check','project_check','playtest'):raise ValueError('Unsupported evidence type')
        if studio:arguments['studio_id']=studio
        bound=[]
        if kind=='code_check':
            path=arguments.get('path')
            if not path or not any(path[:len(p)]==p for p in scopes):raise ValueError('Code evidence path must be inside the task scope')
            current=self.wb.get(studio,path);arguments['source']=current['source'];bound=[{'path':path,'hash':current['hash']}]
        elif kind=='project_check':
            for field in ('source','changes','path','create_class'):arguments.pop(field,None)
        elif kind=='playtest':
            for path in scopes:
                try:bound.append({'path':path,'hash':self.wb.get(studio,path)['hash']})
                except ValueError:pass
        result=self.wb.call(kind,arguments)
        passed=not result.get('isError') if kind=='playtest' else bool(result.get('passed'))
        if kind=='project_check':
            if result.get('ambiguous_source_paths'):raise ValueError('Project has ambiguous script paths; use uniquely addressed code evidence for this task')
            bound=[{'path':json.loads(path),'hash':hashed} for path,hashed in result.get('source_manifest',{}).items()]
        body={'project':project,'task':task_id,'owner':self.wb.owner,'kind':kind,'passed':passed,'studio':studio,
              'sources':bound,'result':result,'created_at':utc(),'origin':'server_generated'}
        raw=json.dumps(body,sort_keys=True,ensure_ascii=True);identifier=uuid.uuid4().hex
        with self.store.connect() as db:db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?,?)',(identifier,project,task_id,self.wb.owner,kind,int(passed),raw,hashlib.sha256(raw.encode()).hexdigest(),time.time()))
        return {'receipt_id':identifier,'evidence_reference':'receipt:'+identifier,'passed':passed,'result':result}

    def validate_receipts(self,row,evidence,check_sources=True):
        ids=[v[8:] for v in evidence if v.startswith('receipt:')]
        if not ids:raise ValueError('This task requires a passed server-generated receipt')
        good=False
        for identifier in ids:
            receipt=self.receipt(identifier)
            if receipt['project']!=row['project'] or receipt['task']!=row['id'] or receipt['owner']!=row['owner']:raise ValueError('Receipt belongs to another task or owner')
            if not receipt['passed']:continue
            if check_sources:
                for source in receipt['sources']:
                    live=self.wb.script(receipt['studio'],source['path'])['source']
                    if hashlib.sha256(live.encode('utf-8')).hexdigest()!=source['hash']:raise ValueError('Receipt is stale: source changed after the check')
            good=True
        if not good:raise ValueError('No passed receipt was provided')

    def receipt(self,identifier):
        with self.store.connect() as db:row=db.execute('SELECT * FROM receipts WHERE id=?',(identifier,)).fetchone()
        if not row:raise ValueError('Receipt not found')
        if hashlib.sha256(row['body'].encode()).hexdigest()!=row['hash']:raise ValueError('Receipt integrity check failed')
        return {'receipt_id':identifier,'sha256':row['hash'],**json.loads(row['body'])}

    def call(self,name,args):
        if name=='project_bind':return self.bind(args)
        if name=='evidence':return self.evidence(args)
        if name=='receipt':return self.receipt(args['receipt_id'])
        if name=='metrics':
            with self.store.connect() as db:rows=db.execute('SELECT tool,COUNT(*) calls,AVG(elapsed) average_ms,MAX(elapsed) max_ms,SUM(success) successes FROM metrics GROUP BY tool').fetchall()
            return {'tools':[dict(r) for r in rows]}
        if name=='recover':
            if args.get('operation_id'):
                row=self.operation(args['operation_id']);return self.reconcile(row,args.get('studio_id',row['studio']))
            limit=args.get('limit',25)
            if not isinstance(limit,int) or not 1<=limit<=100:raise ValueError('Invalid limit')
            with self.store.connect() as db:rows=db.execute('SELECT id,studio,project,owner,state,kind,created,updated FROM operations ORDER BY created DESC LIMIT ?',(limit,)).fetchall()
            return {'operations':[dict(row) for row in rows]}
        if name!='changes':raise ValueError('Unknown advanced tool')
        action=args['action']
        if action=='prepare':return self.prepare(args)
        row=self.operation(args['plan_id'])
        if action=='status':return {key:value for key,value in row.items() if key not in ('before_json','after_json')}
        if action not in ('apply','rollback'):raise ValueError('Invalid change set action')
        return self.apply(row,args.get('studio_id',row['studio']),rollback=action=='rollback')
