"""Roblox-aware type analysis, live project sourcemaps and engine API reference."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.request
import uuid
from team import utc, text

ROOT=Path(__file__).resolve().parent

def schema(tool):
    definitions = [
        tool('api_lookup','Inspect the bundled/refreshed official Roblox API catalog: inheritance, members, types, deprecation and security metadata. Includes source version and documentation link.',
             {'class_name':{'type':'string'},'member':{'type':'string'},'enum_name':{'type':'string'}},readonly=True),
        tool('api_refresh','Refresh Roblox type definitions and official engine API catalog. Parses and smoke-checks definitions before atomically selecting a new local snapshot. Does not change Studio or permissions.',{}),
        tool('project_check','Read live editor sources and hierarchy WITHOUT executing game scripts; build an isolated sourcemap and analyze cross-module requires and Roblox types. Fails on incomplete export instead of reporting a false pass.',
             {'studio_id':{'type':'string'},'path':{'type':'array','items':{'type':'string'}},'source':{'type':'string'},'include_editor':{'type':'boolean','default':False}},('studio_id',),True),
    ]
    definitions[1]['annotations']['openWorldHint']=True
    return definitions

class RobloxAPI:
    def __init__(self,wb):self.wb=wb

    def directory(self):
        pointer=self.wb.store.file.parent/'api-current.json'
        if pointer.is_file():
            value=json.loads(pointer.read_text(encoding='utf-8'))
            candidate=self.wb.store.file.parent/'api-snapshots'/value['snapshot']
            if not candidate.resolve().is_relative_to((self.wb.store.file.parent/'api-snapshots').resolve()):raise ValueError('Invalid API snapshot pointer')
            return candidate
        return ROOT/'api'

    def analyze(self,files,cwd,sourcemap=None,directory=None):
        executable=self.wb.executable('luau-lsp')
        if not executable:raise ValueError('Roblox analyzer missing: luau-lsp')
        directory=directory or self.directory()
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        definitions=directory/'roblox.d.luau'
        if hashlib.sha256(definitions.read_bytes()).hexdigest()!=manifest['files']['roblox.d.luau']:
            raise ValueError('Roblox definitions checksum mismatch')
        argv=[executable,'analyze','--platform=roblox','--definitions='+str(definitions),'--formatter=plain']
        if sourcemap:argv+=['--sourcemap='+str(sourcemap)]
        result=subprocess.run(argv+[str(Path(p).relative_to(Path(cwd))) for p in files],cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        stderr='\n'.join(line for line in result.stderr.splitlines() if line.strip() and not line.startswith('[INFO]') and not line.startswith('[WARN] client does not allow didChangeWatchedFiles registration'))
        output=(result.stdout+'\n'+stderr).strip()
        return {'available':True,'passed':result.returncode==0 and not output,'exit_code':result.returncode,
                'output':output[:64000],'engine':'luau-lsp Roblox','api_version':manifest['engine_version'],
                'definitions_sha256':manifest['files']['roblox.d.luau'],'project_sourcemap':bool(sourcemap)}

    def export(self,studio,include_editor=False):
        code='-- MCP_PROJECT_EXPORT\nlocal exportOptions = { includeEditor = '+('true' if include_editor else 'false')+' }\n'
        code+=(ROOT/'snapshot_response.luau').read_text(encoding='utf-8')+(ROOT/'project_export.luau').read_text(encoding='utf-8')
        result=self.wb.read_rpc('tools/call',{'name':'execute_luau','arguments':{'studio_id':studio,'datamodel_type':'Edit','code':code}})
        if result.get('isError'):raise ValueError('Project export failed: '+json.dumps(result.get('content')))
        blocks=[v['text'] for v in result.get('content',[]) if v.get('type')=='text']
        if len(blocks)!=1:raise ValueError('Unexpected project export response')
        data=json.loads(blocks[0])
        data=self.assemble(data,studio)
        if not isinstance(data,dict) or not data.get('complete'):raise ValueError('Project export incomplete; nodes='+str(data.get('nodes'))+', scripts='+str(data.get('scripts'))+'. Export budgets: 200000 nodes, 1000 scripts, 8 MB source; no partial pass is returned')
        return data

    def assemble(self,data,studio,binary=False):
        if not isinstance(data,dict):raise ValueError("Invalid chunk envelope")
        if "token" not in data:return data
        token=data['token'];chunks=[];offset=0;done=False;total=data['bytes']
        if not isinstance(total,int) or not 0<total<=32000000:raise ValueError('Project snapshot exceeds 32 MB collection budget')
        try:
            for _ in range(1001):
                block=bytes.fromhex(data['hex'])
                if data.get('token')!=token or data['next_offset']!=offset+len(block) or data['bytes']!=total:
                    raise ValueError('Project chunk sequence mismatch')
                chunks.append(block);offset+=len(block);done=data['done']
                if done:break
                raw=json.dumps({'token':token,'offset':offset})
                query='local payload = game:GetService("HttpService"):JSONDecode('+json.dumps(raw)+')\n'+(ROOT/'project_chunk.luau').read_text(encoding='utf-8')
                response=self.wb.read_rpc('tools/call',{'name':'execute_luau','arguments':{'studio_id':studio,'datamodel_type':'Edit','code':query}})
                if response.get('isError'):raise ValueError('Project chunk read failed: '+str(response.get('content')))
                texts=[v['text'] for v in response.get('content',[]) if v.get('type')=='text']
                if len(texts)!=1:raise ValueError('Invalid chunk response')
                data=json.loads(texts[0])
            if not done or offset!=total:raise ValueError('Incomplete project snapshot')
            assembled=b''.join(chunks)
            data=assembled if binary else json.loads(assembled.decode('utf-8'))
        finally:
            if not done:
                try:
                    raw=json.dumps({'token':token,'discard':True})
                    query='local payload = game:GetService("HttpService"):JSONDecode('+json.dumps(raw)+')\n'+(ROOT/'project_chunk.luau').read_text(encoding='utf-8')
                    self.wb.read_rpc('tools/call',{'name':'execute_luau','arguments':{'studio_id':studio,'datamodel_type':'Edit','code':query}})
                except Exception:pass
        return data

    def project(self,args,*,after_capture=None):
        from project_cache import ProjectCache
        return ProjectCache(self).run(args,after_capture=after_capture)

    def call(self,name,args):
        if name=='project_check':return self.project(args)
        if name=='api_refresh':return self.refresh()
        if name!='api_lookup':raise ValueError('Unknown API tool')
        directory=self.directory();manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        raw=(directory/'API-Dump.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=manifest['files']['API-Dump.json']:raise ValueError('API catalog checksum mismatch')
        data=json.loads(raw);meta={'engine_version':manifest['engine_version'],'source':manifest['source'],'snapshot_at':manifest.get('fetched_at'),'cached_catalog':True}
        if args.get('enum_name'):
            name=text(args['enum_name'],'enum_name',100)
            return {**meta,'enum':next((v for v in data['Enums'] if v['Name']==name),None)}
        if not args.get('class_name'):return {**meta,'classes':[v['Name'] for v in data['Classes']],'enums':[v['Name'] for v in data['Enums']]}
        name=text(args['class_name'],'class_name',100);classes={v['Name']:v for v in data['Classes']}
        if name not in classes:raise ValueError('Class not present in this API version')
        chain=[];members={};current=name
        while current in classes:
            item=classes[current];chain.append(current)
            for member in item['Members']:members.setdefault(member['Name'],{'declared_in':current,**member})
            current=item['Superclass']
        wanted=args.get('member')
        return {**meta,'class_name':name,'inheritance':chain,'tags':classes[name].get('Tags',[]),
                'member':members.get(wanted) if wanted else None,'member_exists':wanted in members if wanted else None,
                'members':None if wanted else [{'name':v['Name'],'kind':v['MemberType'],'declared_in':v['declared_in']} for v in members.values()],
                'documentation':'https://create.roblox.com/docs/reference/engine/classes/'+name+('#'+wanted if wanted else '')}

    def refresh(self):
        def fetch(url,maximum):
            with urllib.request.urlopen(url,timeout=25) as response:
                raw=response.read(maximum+1)
            if len(raw)>maximum:raise ValueError('API update exceeds size limit')
            return raw
        with self.wb.store.lease('api-update',self.wb.owner):
            version=fetch('https://setup.rbxcdn.com/versionQTStudio',200).decode().strip()
            if not version.startswith('version-') or not all(c.isalnum() or c=='-' for c in version):raise ValueError('Unexpected Roblox version identifier')
            source='https://setup.rbxcdn.com/'+version+'-API-Dump.json'
            dump=fetch(source,16000000);catalog=json.loads(dump)
            if not catalog.get('Classes') or not catalog.get('Enums'):raise ValueError('Invalid engine catalog')
            definitions_source='https://raw.githubusercontent.com/JohnnyMorganz/luau-lsp/main/scripts/globalTypes.d.luau'
            definitions=fetch(definitions_source,4000000)
            directory=self.wb.store.file.parent/'api-snapshots'/uuid.uuid4().hex;directory.mkdir(parents=True)
            (directory/'roblox.d.luau').write_bytes(definitions);(directory/'API-Dump.json').write_bytes(dump)
            manifest={'engine_version':version,'source':source,'definitions_source':definitions_source,'fetched_at':utc(),
                      'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}}
            (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            with tempfile.TemporaryDirectory() as temp:
                file=Path(temp)/'probe.luau';file.write_text('local p: Part = Instance.new("Part")\np.Position = Vector3.new(1,2,3)\nprint(p.Position)\n',encoding='utf-8')
                checked=self.analyze([file],temp,directory=directory)
                if not checked['passed']:raise ValueError('Refreshed definitions failed smoke check; previous snapshot retained: '+checked['output'])
            pointer=self.wb.store.file.parent/'api-current.json';temporary=pointer.with_name('api-current-'+uuid.uuid4().hex+'.tmp')
            temporary.write_text(json.dumps({'snapshot':directory.name}),encoding='utf-8');os.replace(temporary,pointer)
            return {'updated':True,**manifest}
