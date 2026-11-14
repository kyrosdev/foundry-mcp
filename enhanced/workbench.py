"""Local coordination, revision history, code tools and explicit two-way script sync."""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from collections import Counter
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import threading
import uuid
from team import Team, schema as team_schema
from live import Observatory, schema as live_schema
from eventpush import EventHub
from webinfo import WebInfo, schema as web_schema
from roblox_api import RobloxAPI, schema as api_schema
from advanced import Advanced, schema as advanced_schema
from studio_plans import StudioPlans, path_arg, schema as studio_plan_schema
from project_sync import ProjectSync, schema as project_sync_schema
from sync_watch import SyncWatch, schema as sync_watch_schema
from studio_testing import StudioTesting, schema as studio_testing_schema
from studio_publish import StudioPublish, schema as studio_publish_schema
from workflows import Workflows, schema as workflow_schema

ROOT = Path(__file__).resolve().parent

def digest(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()

def diff(old, new):
    return ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                      fromfile='current', tofile='proposed'))

def merge(base, local, remote):
    """Conservative line merge: overlapping edits are reported, never guessed."""
    if local == remote: return local
    if local == base: return remote
    if remote == base: return local
    lines = base.splitlines(True)
    changes = []
    for text in (local, remote):
        target = text.splitlines(True)
        edits = [(i, j, target[a:b]) for tag, i, j, a, b in
                 difflib.SequenceMatcher(None, lines, target, autojunk=False).get_opcodes() if tag != 'equal']
        for edit in edits:
            if edit in changes: continue
            i, j, _ = edit
            for a, b, _ in changes:
                if max(i, a) < min(j, b) or (i == j and a <= i <= b) or (a == b and i <= a <= j):
                    raise ValueError('Merge conflict: both sides changed overlapping lines')
            changes.append(edit)
    for i, j, replacement in sorted(changes, reverse=True): lines[i:j] = replacement
    return ''.join(lines)

class Store:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, label TEXT, seen REAL);
    CREATE TABLE IF NOT EXISTS locks(resource TEXT PRIMARY KEY, owner TEXT, token TEXT, expires REAL);
    CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY, studio TEXT, path TEXT,
        source TEXT, hash TEXT, reason TEXT, session TEXT, created REAL);
    CREATE INDEX IF NOT EXISTS revision_path ON revisions(studio,path,created);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, session TEXT, action TEXT, details TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS sync(studio TEXT, path TEXT, file TEXT, base TEXT,
        PRIMARY KEY(studio,path), UNIQUE(file));
    CREATE TABLE IF NOT EXISTS studio_aliases(studio TEXT PRIMARY KEY, project TEXT);
    """

    def __init__(self, directory, *, schemas=(), session=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.file = directory / 'workbench.sqlite3'
        with self.connect() as db:
            # SQLite can return BUSY immediately when two processes first switch
            # an empty database to WAL, even with a busy timeout configured.
            deadline = time.monotonic() + 10
            while True:
                try:
                    db.execute('PRAGMA journal_mode=WAL').fetchone()
                    break
                except sqlite3.OperationalError as error:
                    if 'locked' not in str(error) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
            db.executescript('BEGIN IMMEDIATE;\n' + self.SCHEMA + '\n' + '\n'.join(schemas))
            if schemas:
                columns = {row['name'] for row in db.execute('PRAGMA table_info(team_tasks)')}
                if 'require_receipts' not in columns:
                    db.execute('ALTER TABLE team_tasks ADD COLUMN require_receipts INTEGER NOT NULL DEFAULT 0')
            if session:
                db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?)', (*session, time.time()))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.file, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def touch(self, owner, label):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?)', (owner, label, time.time()))

    def acquire(self, resource, owner, ttl=600, conflicts=()):
        if not isinstance(ttl, (int, float)) or not 5 <= ttl <= 3600:
            raise ValueError('Lock ttl must be 5..3600 seconds')
        token = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM locks WHERE expires < ?', (time.time(),))
            if resource.startswith('script:'):
                # Match ancestor/descendant reservations, not only exact paths.
                boundary=resource.find(':[',len('script:'))
                prefix,serialized=(resource[:boundary],resource[boundary+1:]) if boundary>=0 else ('','')
                try: requested=json.loads(serialized)
                except ValueError: requested=None
                if isinstance(requested,list):
                    for held in db.execute('SELECT resource FROM locks'):
                        if held['resource'].startswith(prefix+':'):
                            try: existing=json.loads(held['resource'][len(prefix)+1:])
                            except ValueError:continue
                            if requested[:len(existing)]==existing or existing[:len(requested)]==requested:
                                raise ValueError('Resource overlaps an existing reservation')
            for key in (resource, *conflicts):
                prior = db.execute('SELECT owner,expires FROM locks WHERE resource=?', (key,)).fetchone()
                if prior: raise ValueError(f'Resource locked by {prior["owner"]} until {prior["expires"]}')
            db.execute('INSERT INTO locks VALUES(?,?,?,?)', (resource, owner, token, time.time()+ttl))
        return token

    def release(self, token, owner):
        with self.connect() as db:
            return db.execute('DELETE FROM locks WHERE token=? AND owner=?', (token, owner)).rowcount > 0

    @contextmanager
    def lease(self, resource, owner):
        token = self.acquire(resource, owner)
        try:
            yield token
        except (TimeoutError, ConnectionError):
            # A timed-out Studio script may still be running: retain its lease.
            raise
        else:
            self.release(token, owner)
        finally:
            # Other exceptions mean the request has completed with a known failure.
            import sys
            error = sys.exc_info()[1]
            if error is not None and not isinstance(error, (TimeoutError, ConnectionError)): self.release(token, owner)

    def record(self, studio, path, source, reason, owner):
        version = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)',
                       (version, self.project_key(studio), json.dumps(path), source, digest(source), reason, owner, time.time()))
        return version

    def project_key(self,studio):
        with self.connect() as db:row=db.execute('SELECT project FROM studio_aliases WHERE studio=?',(studio,)).fetchone()
        return row['project'] if row else studio

    def event(self, owner, action, details):
        with self.connect() as db:
            db.execute('INSERT INTO events(session,action,details,created) VALUES(?,?,?,?)',
                       (owner, action, json.dumps(details), time.time()))

def tool(name, description, properties=None, required=(), readonly=False, *, destructive=None):
    return {'name': 'extra_'+name, 'description': description,
            'inputSchema': {'type': 'object', 'properties': properties or {},
                            'required': list(required), 'additionalProperties': False},
            'annotations': {'readOnlyHint': readonly,
                            'destructiveHint': not readonly if destructive is None else destructive,
                            'openWorldHint': False}}

STR = {'type': 'string'}
PATH = {'type': 'array', 'items': STR, 'minItems': 1, 'maxItems': 64}
SCRIPT = {'studio_id': STR, 'path': PATH}
PREVIEW = {'type': 'boolean', 'default': True}

def definitions():
    return [
        tool('status', 'Inspect local toolchain, workspace, session and storage paths.', readonly=True),
        tool('agent', 'Manage logical agent identities or show a short viewport status. Use as_agent to identify a subagent.',
             {'action':{'enum':['register','list','retire','status']},'name':STR,'agent_id':STR,
              'studio_id':STR,'status':{'type':'string','maxLength':120},'path':PATH},('action',),destructive=False),
        tool('sessions', 'List known sessions, their last activity and current leases.', readonly=True),
        tool('lock', 'Acquire, renew or release a cooperative script lock. Automatic writes also acquire a Studio-wide lease.',
             {**SCRIPT, 'action': {'enum': ['acquire','renew','release']}, 'token': STR,
              'ttl': {'type':'integer','minimum':5,'maximum':3600,'default':600}}, ('studio_id','path','action')),
        tool('history', 'List script revisions or activity. Revision source only returned for explicit revision_id.',
             {**SCRIPT, 'revision_id': STR, 'limit': {'type':'integer','minimum':1,'maximum':100}}, readonly=True),
        tool('script_get', 'Read live editor source and SHA256; use hash for optimistic writes.', SCRIPT, SCRIPT, True),
        tool('script_put', 'Preview or write a script with SHA256 conflict checks, pre-write snapshot, formatting, lint and undo. Defaults to preview.',
             {**SCRIPT, 'source':STR, 'expected_hash':STR, 'preview':PREVIEW,
              'format':{'type':'boolean','default':True}, 'check':{'type':'boolean','default':True}},
             ('studio_id','path','source','expected_hash')),
        tool('script_restore', 'Restore a revision through conflict checks; snapshots current code first. Defaults to preview.',
             {**SCRIPT, 'revision_id':STR, 'expected_hash':STR,'preview':PREVIEW},
             ('studio_id','path','revision_id','expected_hash')),
        tool('sync', 'Explicit Studio/filesystem sync with stored base and conservative three-way merges. No silent overwrite or deletion. Defaults to preview.',
             {**SCRIPT,'file':STR,'action':{'enum':['status','pull','push','merge']},'preview':PREVIEW},
             ('studio_id','path','file','action')),
        tool('code_check', 'Check Luau with Roblox engine types, lint and formatting. Optional studio_id/path adds live hierarchy and cross-module require resolution.',
             {'source':STR,'studio_id':STR,'path':PATH}, ('source',), True),
        tool('code_format', 'Format Luau source with StyLua, returning text without editing Studio.', {'source':STR}, ('source',), True),
        tool('playtest', 'Run an ordered native playtest scenario, collect screenshots/logs and boolean assertions. Stops sessions it starts even on failure. Does not publish.',
             {'studio_id':STR, 'steps':{'type':'array','minItems':1,'maxItems':30,'items':{
                 'type':'object','properties':{'tool':{'enum':['start_stop_play','execute_luau','character_navigation','user_keyboard_input','user_mouse_input','screen_capture','get_console_output','get_studio_state']},
                 'arguments':{'type':'object'}, 'assert_code':STR, 'wait_seconds':{'type':'number','minimum':0,'maximum':10},
                 'profile_seconds':{'type':'number','minimum':0.25,'maximum':10},
                 'remote_probe':{'type':'object'}},'additionalProperties':False}},
              'cleanup':{'type':'boolean','default':True}}, ('studio_id','steps')),
    ] + team_schema(tool) + live_schema(tool) + web_schema(tool) + api_schema(tool) + advanced_schema(tool) + studio_plan_schema(tool) + workflow_schema(tool) + project_sync_schema(tool) + sync_watch_schema(tool) + studio_testing_schema(tool) + studio_publish_schema(tool)

class Workbench:
    def __init__(self, rpc, directory, workspace, label='MCP client'):
        self.rpc = rpc
        self.workspace = Path(workspace).resolve()
        self.connection_id = uuid.uuid4().hex
        self.identity = ContextVar('studio_mcp_agent_'+self.connection_id,default=None)
        self.agent_lock=threading.Lock()
        self.agents={self.connection_id:{'name':label,'active':True}}
        self.verified = {}
        self.store = Store(directory, schemas=(Team.SCHEMA, Observatory.SCHEMA, EventHub.SCHEMA,
                                               Advanced.SCHEMA, Workflows.SCHEMA),
                           session=(self.connection_id, label))
        self.team=Team(self)
        self.live=Observatory(self,self.team)
        self.web=WebInfo()
        self.api=RobloxAPI(self)
        self.advanced=Advanced(self)
        self.plans = StudioPlans(self)
        self.project_sync = ProjectSync(self)
        self.sync_watch = SyncWatch(self)
        self.testing = StudioTesting(self)
        self.studio_publish = StudioPublish(self)
        self.workflows = Workflows(self)
        self.auto_bind_projects=False

    @property
    def owner(self):return self.identity.get() or self.connection_id

    @property
    def label(self):
        with self.agent_lock:return self.agents[self.owner]['name']

    def agent_ids(self):
        with self.agent_lock:return list(self.agents)

    @contextmanager
    def actor(self,identifier=None):
        identifier=identifier or self.connection_id
        with self.agent_lock:
            if identifier not in self.agents or not self.agents[identifier]['active']:
                raise ValueError('Unknown/retired logical agent for this connection; register using extra_agent')
        token=self.identity.set(identifier)
        try:yield
        finally:self.identity.reset(token)

    def agent(self,args):
        action=args['action']
        if action=='status':
            studio, status = args.get('studio_id'), args.get('status')
            if not isinstance(studio,str) or not studio or not isinstance(status,str) or not status.strip() or len(status)>120:
                raise ValueError('status requires studio_id and 1–120 characters')
            return {'status':status.strip()}
        if action=='list':
            with self.agent_lock:return {'connection_id':self.connection_id,'agents':[{'agent_id':key,**value} for key,value in self.agents.items()]}
        if action=='register':
            from team import text
            name=text(args['name'],'name',100)
            with self.agent_lock:
                if len(self.agents)>=33:raise ValueError('Maximum 32 logical subagents per connection')
                identifier=uuid.uuid4().hex
                self.agents[identifier]={'name':name,'active':True}
            self.store.touch(identifier,name)
            return {'agent_id':identifier,'name':name,'connection_id':self.connection_id,'next_step':'Use as_agent=agent_id and extra_team_join to join a shared project'}
        if action!='retire':raise ValueError('Unknown agent action')
        identifier=args['agent_id']
        with self.agent_lock:
            if identifier==self.connection_id or identifier not in self.agents:raise ValueError('Can only retire your registered subagents')
            self.agents[identifier]['active']=False
        with self.store.connect() as db:
            db.execute('UPDATE team_members SET connected=0 WHERE session=?',(identifier,))
            db.execute('UPDATE live_watches SET active=0 WHERE owner=?',(identifier,))
        return {'retired':True,'agent_id':identifier,'note':'Task leases remain until expiry; requeue or hand off before retiring when possible'}

    def start_background(self):
        self.team.start()
        self.live.start()

    def read_rpc(self,method,params,**options):
        bridge=getattr(self.rpc,'__self__',None)
        if getattr(bridge,'supports_read_retry',False):return self.rpc(method,params,read_only=True,**options)
        return self.rpc(method,params,**options)

    def close(self):
        self.sync_watch.close()
        self.live.close()
        self.team.close()

    def executable(self, name):
        bundled = ROOT / 'bin' / (name + ('.exe' if os.name == 'nt' else ''))
        if not bundled.is_file(): return shutil.which(name)
        manifest=json.loads((ROOT/'toolchain-manifest.json').read_text(encoding='utf-8'))
        stat=bundled.stat()
        signature=(stat.st_size,stat.st_mtime_ns)
        if self.verified.get(name)!=signature:
            expected=manifest.get(bundled.name,{}).get('sha256')
            if not expected or hashlib.sha256(bundled.read_bytes()).hexdigest()!=expected:
                raise ValueError('Bundled tool checksum mismatch: '+name)
            self.verified[name]=signature
        return str(bundled)

    def code_tool(self, source, formatting=False, context=None, lint_only=False):
        if not isinstance(source, str) or len(source.encode('utf-8')) > 200_000:
            raise ValueError('source must be a string of at most 200 KB')
        statuses = {}
        formatted = source
        with tempfile.TemporaryDirectory(prefix='studio-mcp-code-') as directory:
            file = Path(directory) / 'script.luau'
            file.write_text(source, encoding='utf-8', newline='')
            (Path(directory)/'.luaurc').write_text('{"languageMode":"strict"}',encoding='utf-8')
            for name in (['stylua'] if formatting else ['stylua','selene'] if lint_only else ['stylua','selene','luau-analyze']):
                if name=='luau-analyze':
                    statuses[name]=self.api.project({**context,'source':source}) if context else self.api.analyze([file],directory)
                    continue
                executable = self.executable(name)
                if not executable:
                    statuses[name] = {'available': False, 'passed': False}
                    continue
                if name == 'stylua':
                    argv = [executable, '--syntax', 'Luau', '--no-editorconfig']
                    if not formatting: argv.append('--check')
                    argv.append(str(file))
                elif name == 'selene':
                    # Pinned Roblox stdlib is bundled: no network during a check.
                    shutil.copyfile(ROOT / 'roblox.yml', Path(directory) / 'roblox.yml')
                    (Path(directory)/'selene.toml').write_text('std = "roblox"\n', encoding='utf-8')
                    argv = [executable, '--display-style', 'Json2', str(file)]
                else: argv = [executable, '--mode=strict', '--formatter=plain', str(file)]
                run = subprocess.run(argv, cwd=directory, capture_output=True, text=True,
                                     encoding='utf-8', errors='replace', timeout=25)
                statuses[name] = {'available': True, 'passed': run.returncode == 0 and (name != 'luau-analyze' or not (run.stdout+run.stderr).strip()),
                                  'exit_code':run.returncode, 'output':(run.stdout+run.stderr)[:24000]}
                if name=='selene' and not statuses[name]['passed'] and context and 'baseline_source' in context:
                    file.write_text(context['baseline_source'],encoding='utf-8',newline='')
                    baseline=subprocess.run(argv,cwd=directory,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=25)
                    file.write_text(source,encoding='utf-8',newline='')
                    def lint_keys(output):
                        keys=[];summary=False;has_findings=False
                        for line in output.splitlines():
                            item=json.loads(line)
                            if item['type']=='Summary':
                                summary=True;has_findings=bool(item.get('errors') or item.get('warnings') or item.get('parse_errors'));continue
                            if item['type']!='Diagnostic':raise ValueError('Unexpected lint output')
                            keys.append(json.dumps({k:item.get(k) for k in ('severity','code','message','notes')},sort_keys=True))
                        if not summary or (has_findings and not keys):raise ValueError('Incomplete lint output')
                        return Counter(keys)
                    try:
                        if run.stderr.strip() or baseline.stderr.strip():raise ValueError('Lint engine error')
                        introduced=lint_keys(run.stdout)-lint_keys(baseline.stdout)
                        statuses[name]['gate_passed']=not introduced
                        statuses[name]['introduced_diagnostics']=list(introduced.elements())
                    except (ValueError,KeyError):statuses[name]['gate_passed']=statuses[name]['passed']
                if formatting and run.returncode == 0: formatted = file.read_text(encoding='utf-8')
        if formatting:
            if not statuses['stylua'].get('passed'): raise ValueError('StyLua could not format source: '+json.dumps(statuses))
            return {'source':formatted,'hash':digest(formatted),'tools':statuses}
        return {'tools':statuses, 'passed':all(v['passed'] for v in statuses.values()),
                'gate_passed':all(v.get('gate_passed',v['passed']) for v in statuses.values()),
                'note':'Formatting/lint only.' if lint_only else 'Roblox engine definitions are loaded. Supply studio_id and path for live hierarchy and cross-module analysis. Static checks do not execute the game.'}

    def script(self, studio, path, source=None, expected=None):
        if not isinstance(studio,str) or not studio: raise ValueError('studio_id is required')
        if not isinstance(path,list) or not path or len(path)>64 or not all(isinstance(s,str) and s for s in path):
            raise ValueError('path must be 1..64 exact child names')
        payload = {'path':path}
        if source is not None: payload.update(source=source, expected=expected)
        raw = json.dumps(payload, ensure_ascii=True, allow_nan=False)
        code = 'local payload = game:GetService("HttpService"):JSONDecode('+json.dumps(raw)+')\n'
        code += (ROOT / 'scripts.luau').read_text(encoding='utf-8')
        caller=self.read_rpc if source is None else self.rpc
        result = caller('tools/call', {'name':'execute_luau', 'arguments':{
            'studio_id':studio,'datamodel_type':'Edit','code':code}})
        if result.get('isError'): raise ValueError(json.dumps(result['content']))
        for content in result.get('content',[]):
            if content.get('type') == 'text':
                try:
                    value=json.loads(content['text'])
                    if isinstance(value,dict) and 'token' in value:value=self.api.assemble(value,studio)
                    if isinstance(value,dict) and isinstance(value.get('source'),str): return value
                except ValueError: pass
        raise ValueError('Unexpected Studio source result; refusing to guess')

    def get(self, studio, path):
        if self.auto_bind_projects:self.advanced.auto_bind(studio)
        result = self.script(studio,path)
        result['hash'] = digest(result['source'])
        return result

    def assert_script_lock(self, studio, path):
        self.team.authorize_write(studio,path)
        prefix = 'script:'+studio+':'
        with self.store.connect() as db:
            rows = db.execute('SELECT resource,owner FROM locks WHERE expires>? AND owner!=?', (time.time(),self.owner)).fetchall()
        for row in rows:
            if row['resource'].startswith(prefix):
                try: held=json.loads(row['resource'][len(prefix):])
                except ValueError:continue
                if path[:len(held)]==held or held[:len(path)]==path:raise ValueError('Script overlaps a resource reserved by another session')

    def put(self, args):
        studio, path = args['studio_id'], args['path']
        current = self.get(studio,path)
        if args['expected_hash'] != current['hash']: raise ValueError('Conflict: Studio source changed. Read again before writing.')
        source = args['source']
        if not isinstance(source,str) or len(source.encode('utf-8'))>200_000: raise ValueError('Source exceeds 200 KB')
        if args.get('format',True): source = self.code_tool(source, True)['source']
        checks = self.code_tool(source,context={'studio_id':studio,'path':path,'baseline_source':current['source']}) if args.get('check',True) else None
        blocked = checks and any(not value.get('gate_passed',value.get('passed')) for value in checks['tools'].values())
        preview = args.get('preview',True)
        result = {'preview':preview,'current_hash':current['hash'],'proposed_hash':digest(source),
                  'diff':diff(current['source'],source),'checks':checks,'blocked':bool(blocked)}
        if preview or blocked: return result
        with self.store.lease('studio:'+studio,self.owner):
            self.assert_script_lock(studio,path)
            # Save before attempting mutation. CAS in Studio rejects concurrent editor changes.
            version=self.store.record(studio,path,current['source'],'before_write',self.owner)
            operation=self.advanced.begin(studio,[{'path':path,'source':current['source']}],[{'path':path,'source':source}],state='applying')
            try:
                written=self.script(studio,path,source,current['source'])
            except Exception as error:
                self.advanced.finish(operation,'uncertain',{'error':str(error),'backup':version})
                self.store.event(self.owner,'script_write_failed',{'studio':studio,'path':path,'backup':version})
                raise
            self.advanced.finish(operation,'committed',{'backup':version})
            self.store.record(studio,path,written['source'],'after_write',self.owner)
        self.store.event(self.owner,'script_write',{'studio':studio,'path':path,'backup':version})
        result.update(backup_revision=version,hash=digest(written['source']),written=True,operation_id=operation)
        return result

    def patch_native(self, args):
        """Route existing native multi_edit writes through the revision/CAS pipeline."""
        studio=args['studio_id']
        path=args['file_path'].split('.')
        if path[0]=='game': path=path[1:]
        try: current=self.get(studio,path)
        except ValueError as error:
            if args.get('className') and 'Missing path segment' in str(error): current=None
            else: raise
        source=current['source'] if current else ''
        edits=args['edits']
        if not isinstance(edits,list) or not 1<=len(edits)<=100: raise ValueError('Expected 1..100 edits')
        for index,edit in enumerate(edits):
            old,new=edit['old_string'],edit['new_string']
            if current is None and index==0 and old=='':
                if not isinstance(new,str): raise ValueError('new_string must be a string')
                source=new
                continue
            if not isinstance(old,str) or not isinstance(new,str) or not old or old==new:
                raise ValueError('Existing-script edits require different nonempty old_string and new_string')
            count=source.count(old)
            if not count: raise ValueError('old_string not found')
            if count>1 and not edit.get('replace_all'): raise ValueError('Ambiguous edit: use replace_all or more context')
            source=source.replace(old,new,-1 if edit.get('replace_all') else 1)
        if current is None:
            source=self.code_tool(source,True)['source']
            checks=self.code_tool(source,context={'studio_id':studio,'path':path,'create_class':args['className']})
            if any(not value.get('gate_passed',value.get('passed')) for value in checks['tools'].values()):
                return {'blocked':True,'checks':checks,'written':False}
            args['edits']=[{'old_string':'','new_string':source}]
            return None
        return self.put({'studio_id':studio,'path':path,'source':source,
                         'expected_hash':current['hash'],'preview':False})

    def safe_file(self, relative):
        if not isinstance(relative,str) or not relative or Path(relative).is_absolute():
            raise ValueError('file must be relative to configured workspace')
        path=(self.workspace/relative).resolve()
        if not path.is_relative_to(self.workspace) or path.suffix not in ('.lua','.luau'):
            raise ValueError('File must be a .lua/.luau inside configured workspace')
        return path

    def sync(self,args):
        studio,path=args['studio_id'],args['path']
        if self.auto_bind_projects:self.advanced.auto_bind(studio)
        namespace=self.store.project_key(studio)
        file=self.safe_file(args['file'])
        key=json.dumps(path)
        with self.store.connect() as db:
            baseline=db.execute('SELECT * FROM sync WHERE studio=? AND path=?',(namespace,key)).fetchone()
            other=db.execute('SELECT * FROM sync WHERE file=?',(str(file),)).fetchone()
        if baseline and baseline['file']!=str(file): raise ValueError('Script already bound to a different file')
        if other and (other['studio']!=namespace or other['path']!=key): raise ValueError('File already bound to another script')
        current=self.get(studio,path)
        local=file.read_text(encoding='utf-8') if file.exists() else None
        base=baseline['base'] if baseline else None
        action=args['action']; preview=args.get('preview',True)
        status={'preview':preview,'file':str(file),'bound':baseline is not None,
                'studio_hash':current['hash'],'file_hash':digest(local) if local is not None else None,
                'base_hash':digest(base) if base is not None else None}
        if action=='status': return status
        if action=='pull':
            if local is not None and local!=current['source'] and (base is None or local!=base):
                raise ValueError('Local edits would be overwritten; use merge or resolve explicitly')
            target=current['source']
        elif action in ('push','merge'):
            if local is None: raise ValueError('Local source file missing')
            if action=='merge':
                if base is None: raise ValueError('Pull first to establish a merge base')
                target=merge(base,local,current['source'])
            else:
                if base is None and local!=current['source']: raise ValueError('Pull first to establish a sync base')
                if base is not None and current['source'] not in (base,local): raise ValueError('Studio edits detected; use merge')
                target=local
        else: raise ValueError('Unknown sync action')
        status['diff']=diff(current['source'],target)
        status['file_diff']=diff(local or '',target)
        if preview: return status
        with self.store.lease('file:'+str(file),self.owner):
            # Recheck disk after acquiring lease; manual editors do not honor our locks.
            latest=file.read_text(encoding='utf-8') if file.exists() else None
            if latest!=local: raise ValueError('File changed during sync')
            if target!=current['source']:
                written=self.put({**args,'source':target,'expected_hash':current['hash'],'preview':False})
                if written.get('blocked'): return written
                target=self.get(studio,path)['source']
            if file.exists() and file.read_text(encoding='utf-8')!=local: raise ValueError('File changed during Studio update; history retained, sync base unchanged')
            file.parent.mkdir(parents=True,exist_ok=True)
            # Preserve previous disk text before replacing it, including when pulling.
            if local is not None: self.store.record(studio,path,local,'before_file_sync',self.owner)
            fd,temp=tempfile.mkstemp(dir=file.parent,prefix='.mcp-',suffix='.tmp')
            try:
                with os.fdopen(fd,'w',encoding='utf-8',newline='') as stream: stream.write(target)
                os.replace(temp,file)
            finally:
                if os.path.exists(temp): os.unlink(temp)
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO sync VALUES(?,?,?,?)',(namespace,key,str(file),target))
        self.store.event(self.owner,'sync',{'studio':studio,'path':path,'file':str(file),'action':action})
        status.update(synced=True,hash=digest(target))
        return status

    def call(self,name,args):
        if name in ('history','script_restore') and args.get('studio_id') and self.auto_bind_projects:self.advanced.auto_bind(args['studio_id'])
        if name=='agent':return self.agent(args)
        if name.startswith('team_') or name.startswith('task_'):return self.team.call(name,args)
        if name.startswith('live_'):return self.live.call(name,args)
        if name.startswith('web_'):return self.web.call(name,args)
        if name.startswith('api_') or name=='project_check':return self.api.call(name,args)
        if name in ('project_bind','changes','recover','evidence','receipt','metrics'):return self.advanced.call(name,args)
        if name in ('impact', 'mixed_changes', 'model_export'): return self.plans.call(name, args)
        if name == 'project_sync': return self.project_sync.call(args)
        if name == 'project_sync_watch': return self.sync_watch.call(args)
        if name == 'multiplayer_test': return self.testing.multiplayer(args)
        if name == 'device_test': return self.testing.devices(args)
        if name == 'publish_open_place': return self.studio_publish.call(args)
        if name in ('scenario', 'analytics', 'game_pass', 'cloud_catalog', 'datastore',
                    'cloud_luau', 'place_publish', 'roblox_lookup', 'asset_upload'):
            return self.workflows.call(name, args)
        if name=='status':
            return {'session_id':self.owner,'connection_id':self.connection_id,'workspace':str(self.workspace),'database':str(self.store.file),
                    'toolchain':{n:self.executable(n) for n in ('stylua','selene','luau-lsp')},
                    'telemetry':False,'version':'1.0.9','team_heartbeat_seconds':10,
                    'live_poll_interval_seconds':[2,60],'project_binding_warnings':self.advanced.binding_warnings,
                    'web_search_provider':'Brave' if os.environ.get('BRAVE_SEARCH_API_KEY') else 'Bing public RSS (best effort)'}
        if name=='sessions':
            with self.store.connect() as db:
                return {'sessions':[dict(r) for r in db.execute('SELECT * FROM sessions ORDER BY seen DESC LIMIT 100')],
                        'locks':[dict(r) for r in db.execute('SELECT resource,owner,expires FROM locks WHERE expires>?',(time.time(),))]}
        if name=='lock':
            # Validate the path without contacting Studio.
            path=args['path']
            if not isinstance(path,list) or not all(isinstance(p,str) for p in path): raise ValueError('Invalid script path')
            resource='script:'+args['studio_id']+':'+json.dumps(path)
            action=args['action']
            if action=='acquire': return {'token':self.store.acquire(resource,self.owner,args.get('ttl',600),conflicts=('studio:'+args['studio_id'],)),'session_id':self.owner}
            token=args['token']
            if action=='release': return {'released':self.store.release(token,self.owner)}
            if action!='renew': raise ValueError('Unknown lock action')
            ttl=args.get('ttl',600)
            if not isinstance(ttl,(int,float)) or not 5<=ttl<=3600: raise ValueError('Invalid ttl')
            with self.store.connect() as db:
                changed=db.execute('UPDATE locks SET expires=? WHERE token=? AND owner=? AND resource=? AND expires>?',
                    (time.time()+ttl,token,self.owner,resource,time.time())).rowcount
            return {'renewed':changed>0}
        if name=='history':
            limit=args.get('limit',25)
            if not isinstance(limit,int) or not 1<=limit<=100: raise ValueError('limit must be 1..100')
            with self.store.connect() as db:
                if args.get('revision_id'):
                    row=db.execute('SELECT * FROM revisions WHERE id=?',(args['revision_id'],)).fetchone()
                    if not row: raise ValueError('Unknown revision')
                    return dict(row)
                if 'path' in args and 'studio_id' in args:
                    return {'revisions':[dict(r) for r in db.execute('SELECT id,hash,reason,session,created FROM revisions WHERE studio=? AND path=? ORDER BY created DESC LIMIT ?',
                        (self.store.project_key(args['studio_id']),json.dumps(args['path']),limit))]}
                return {'events':[dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT ?',(limit,))]}
        if name=='script_get': return self.get(args['studio_id'],args['path'])
        if name=='script_put': return self.put(args)
        if name=='script_restore':
            row=self.call('history',{'revision_id':args['revision_id']})
            if row['studio']!=self.store.project_key(args['studio_id']) or json.loads(row['path'])!=args['path']: raise ValueError('Revision belongs to another script')
            return self.put({**args,'source':row['source']})
        if name=='sync': return self.sync(args)
        if name=='code_check':
            if ('studio_id' in args)!=('path' in args):raise ValueError('Provide both studio_id and path for project context')
            return self.code_tool(args['source'],context={'studio_id':args['studio_id'],'path':args['path']} if 'studio_id' in args else None)
        if name=='code_format': return self.code_tool(args['source'],True)
        if name=='playtest': return self.playtest(args)
        raise ValueError('Unknown workbench tool')

    def playtest(self,args,studio_locked=False):
        self.team.authorize_studio_action(args['studio_id'])
        steps=args['steps']
        if not isinstance(steps,list) or not 1<=len(steps)<=30: raise ValueError('Scenario requires 1..30 steps')
        allowed={'start_stop_play','execute_luau','character_navigation','user_keyboard_input','user_mouse_input','screen_capture','get_console_output','get_studio_state'}
        reports=[]; images=[]; owned=False; passed=True
        with (nullcontext() if studio_locked else self.store.lease('studio:'+args['studio_id'],self.owner)):
            try:
                for index,step in enumerate(steps):
                    step_started = time.monotonic()
                    if not isinstance(step,dict): raise ValueError('Each step must be an object')
                    name=step.get('tool','execute_luau')
                    if name not in allowed: raise ValueError('Unsupported scenario tool')
                    arguments={**step.get('arguments',{}),'studio_id':args['studio_id']}
                    if name=='start_stop_play' and arguments.get('is_start'):
                        state=self.rpc('tools/call',{'name':'get_studio_state','arguments':{'studio_id':args['studio_id']}})
                        text='\n'.join(c.get('text','') for c in state.get('content',[]) if c.get('type')=='text')
                        if state.get('isError') or 'Current Studio Mode: Edit' not in text:
                            raise ValueError('Scenario may only start from confirmed Edit mode; existing play session preserved')
                    if 'assert_code' in step:
                        name='execute_luau'
                        assertion=step['assert_code']
                        if not isinstance(assertion,str) or len(assertion)>100000: raise ValueError('Invalid assertion')
                        seconds = step.get('wait_seconds',0)
                        if type(seconds) not in (int,float) or not 0 <= seconds <= 10:
                            raise ValueError('wait_seconds must be between 0 and 10')
                        arguments['code'] = (
                            'local function check()\n'+assertion+'\nend\n'
                            f'local deadline = os.clock() + {seconds}\n'
                            'repeat\n'
                            '  if check() == true then return "Assertion passed" end\n'
                            '  if os.clock() >= deadline then break end\n'
                            '  task.wait(0.05)\n'
                            'until false\n'
                            'error("Scenario assertion must return true")'
                        )
                    elif "remote_probe" in step:
                        name = "execute_luau"
                        probe = step["remote_probe"]
                        path = path_arg(probe["path"])
                        calls = probe.get("calls", [[]])
                        if not isinstance(calls,list) or not 1 <= len(calls) <= 20 or any(not isinstance(row,list) for row in calls):
                            raise ValueError("Supply 1..20 lists of JSON arguments")
                        if any(value is None for row in calls for value in row):
                            raise ValueError("Use a Luau scenario step for nil arguments")
                        raw = json.dumps({"path":path,"calls":calls},ensure_ascii=False,allow_nan=False)
                        if len(raw.encode()) > 16000:
                            raise ValueError("Remote probe exceeds 16 KB")
                        arguments["datamodel_type"] = "Client"
                        arguments["code"] = (
                            "local p=game:GetService('HttpService'):JSONDecode(" + json.dumps(raw,ensure_ascii=False) + ")\n"
                            "assert(game.PlaceId==0 and game.GameId==0,'Remote probes require an unpublished local copy')\n"
                            "local r=game\nfor _,name in p.path do local found; for _,child in r:GetChildren() do "
                            "if child.Name==name then assert(not found,'Ambiguous remote path'); found=child end end; "
                            "r=assert(found,'Remote path missing') end\n"
                            "assert(r:IsA('RemoteEvent') or r:IsA('UnreliableRemoteEvent'),'Expected a remote event')\n"
                            "for _,args in p.calls do r:FireServer(table.unpack(args)) end\nreturn #p.calls"
                        )
                    elif "profile_seconds" in step:
                        name = "execute_luau"
                        seconds = step["profile_seconds"]
                        if type(seconds) not in (int,float) or not 0.25 <= seconds <= 10:
                            raise ValueError("Profile duration must be between 0.25 and 10 seconds")
                        model = arguments.get("datamodel_type","Client")
                        if model not in ("Client","Server"):
                            raise ValueError("Select Client or Server for a performance sample")
                        arguments["datamodel_type"] = model
                        arguments["code"] = f'''
local run = game:GetService("RunService")
local stats = game:GetService("Stats")
local samples = {{}}
local finish = os.clock() + {seconds}
local signal = if run:IsClient() then run.PreRender else run.Heartbeat
repeat table.insert(samples, signal:Wait()*1000) until os.clock() >= finish or #samples >= 10000
table.sort(samples)
local count = #samples
return {{frames=count,p50_ms=samples[math.ceil(count*0.50)],
        p95_ms=samples[math.ceil(count*0.95)],memory_mb=stats:GetTotalMemoryUsageMb()}}
'''
                    result=self.rpc('tools/call',{'name':name,'arguments':arguments})
                    if name == "execute_luau" and result.get("isError"):
                        # A loadCode transport rejection occurs before Luau execution.
                        for _ in range(2):
                            error = result["content"][0].get("text","")
                            if "ExecuteLuau.loadCode" not in error or "Target is not reachable" not in error:
                                break
                            time.sleep(0.5)
                            result=self.rpc('tools/call',{'name':name,'arguments':arguments})
                            if not result.get("isError"):
                                break
                    failed=bool(result.get('isError'))
                    if name=='start_stop_play' and not failed: owned=bool(arguments.get('is_start'))
                    reports.append({'step':index+1,'tool':name,'passed':not failed,
                        'duration_ms':round((time.monotonic()-step_started)*1000),'content':[
                        c for c in result.get('content',[]) if c.get('type')!='image']})
                    if "profile_seconds" in step and not failed:
                        reports[-1]["performance"] = {"datamodel_type":arguments["datamodel_type"],
                                                      **json.loads(result["content"][0]["text"])}
                    images.extend(c for c in result.get('content',[]) if c.get('type')=='image')
                    if failed:
                        passed=False
                        break
            except (TimeoutError, ConnectionError):
                raise
            except Exception as error:
                passed=False
                reports.append({'error':str(error)})
            finally:
                if owned and args.get('cleanup',True):
                    try:
                        cleanup=self.rpc('tools/call',{'name':'start_stop_play','arguments':{'studio_id':args['studio_id'],'is_start':False}})
                        reports.append({'cleanup':cleanup})
                        if cleanup.get('isError'): passed=False
                    except Exception as error:
                        passed=False
                        reports.append({'cleanup_error':str(error)})
        self.store.event(self.owner,'playtest',{'studio':args['studio_id'],'passed':passed,'steps':len(steps)})
        return {'isError':not passed,'content':[{'type':'text','text':json.dumps({'passed':passed,'steps':reports})}]+images}
