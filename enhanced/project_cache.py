"""Fresh captures with reusable analysis workspaces and conservative dependency invalidation."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

SCHEMA=2

def sha(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()

def diagnostic_key(line):
    # Locations can move when fixing a file. Preserve file, diagnostic category
    # and message; Counter preserves duplicate occurrences instead of hiding them.
    line=re.sub(r'^.*?[\\/](script_[a-f0-9]+\.luau)',r'\1',line)
    return re.sub(r':\d+:\d+(?:-\d+)?:',':',line.strip())

def diagnostics(output):return [line for line in output.splitlines() if line.strip()]

def regressions(before,after):
    old=Counter(diagnostic_key(v) for v in diagnostics(before))
    new=Counter(diagnostic_key(v) for v in diagnostics(after))
    return list((new-old).elements()),list((old-new).elements())

class ProjectCache:
    def __init__(self,api):self.api=api;self.wb=api.wb

    def run(self,args,*,after_capture=None):
        # A cross-process lease protects the persistent scratch workspace. It is
        # separate from the Studio mutation lease, so checks do not change game state.
        resource='analysis:'+args['studio_id']+':'+str(bool(args.get('include_editor')))
        deadline=time.monotonic()+30;token=None
        while token is None:
            try:token=self.wb.store.acquire(resource,self.wb.owner)
            except ValueError:
                if time.monotonic()>=deadline:raise TimeoutError('Another analysis is still using this workspace')
                time.sleep(0.05)
        try:return self._run(args,after_capture)
        finally:self.wb.store.release(token,self.wb.owner)

    def _run(self,args,after_capture):
        with tempfile.TemporaryDirectory(prefix="foundry-analysis-") as scratch, ThreadPoolExecutor(max_workers=1) as worker:
            return self._analyze_project(args,worker,Path(scratch),after_capture)

    def _analyze_project(self,args,worker,scratch,after_capture):
        started=time.monotonic()
        data=self.api.export(args['studio_id'],True) if args.get('include_editor') else self.api.export(args['studio_id'])
        if after_capture is not None:
            after_capture()
        tree=copy.deepcopy(data['tree']);sources={};paths={}
        def visit(node,actual,virtual):
            node.pop('actualName',None)
            if 'source' in node:
                key='script_'+sha(json.dumps(virtual,ensure_ascii=True))[:16]+'.luau'
                if key in sources:raise ValueError('Analysis filename collision; no partial analysis is accepted')
                sources[key]=node.pop('source');paths[key]=actual
                node['filePaths']=[key]
            for child in node.get('children',[]):visit(child,actual+[child.get('actualName',child['name'])],virtual+[child['name']])
        visit(tree,[],[])
        api_directory=self.api.directory()
        manifest=json.loads((api_directory/'manifest.json').read_text(encoding='utf-8'))
        toolchain=json.loads((Path(__file__).resolve().parent/'toolchain-manifest.json').read_text(encoding='utf-8'))
        namespace=sha(json.dumps([args['studio_id'],bool(args.get('include_editor')),manifest['files']['roblox.d.luau'],toolchain.get('luau-lsp.exe'),SCHEMA]))
        folder=self.wb.store.file.parent/'analysis-cache'/namespace;folder.mkdir(parents=True,exist_ok=True)
        (folder/'.luaurc').write_text('{"languageMode":"strict"}',encoding='utf-8')
        statefile=folder/'cache.json'
        try:state=json.loads(statefile.read_text(encoding='utf-8'))
        except (FileNotFoundError,ValueError):state={'results':{}}
        structural=sha(json.dumps(tree,sort_keys=True))
        hashes={key:sha(value) for key,value in sources.items()}
        baseline_id=sha(json.dumps([structural,hashes],sort_keys=True))
        mapfile=folder/'sourcemap.json'
        mapfile.write_text(json.dumps(tree),encoding='utf-8')
        for key,value in sources.items():
            file=folder/key
            if not file.exists() or sha(file.read_text(encoding='utf-8'))!=hashes[key]:file.write_text(value,encoding='utf-8',newline='')
        for stale in folder.glob('script_*.luau'):
            if stale.name not in sources:stale.unlink()

        def roots(changed):
            if state.get('structure')!=structural:return set(sources)
            reverse={key:set() for key in sources};by_path={}
            for key,path in paths.items():by_path.setdefault(tuple(path),key)
            for key,source in sources.items():
                # Unresolved/dynamic requires force a full check, rather than
                # pretending we know the dependency graph.
                for call in re.findall(r'\brequire\s*\(([^\n;]*)\)',source):
                    expression=call.strip();path=None
                    service=re.fullmatch(r'game:GetService\(["\']([^"\']+)["\']\)((?:\.[A-Za-z_]\w*)*)',expression)
                    if service:path=[service[1]]+[p for p in service[2].split('.') if p]
                    elif re.fullmatch(r'(?:game|script)(?:\.[A-Za-z_]\w*)+',expression):
                        parts=expression.split('.');path=[] if parts[0]=='game' else list(paths[key])
                        for part in parts[1:]:
                            if part=='Parent':path=path[:-1]
                            else:path.append(part)
                    if path is None or tuple(path) not in by_path:return set(sources)
                    reverse[by_path[tuple(path)]].add(key)
                if len(re.findall(r'\brequire\b',source))!=len(re.findall(r'\brequire\s*\(([^\n;]*)\)',source)) or re.search(r'\b(?:getfenv|setfenv|loadstring)\b',source):return set(sources)
            affected=set(changed);pending=list(changed)
            while pending:
                for dependent in reverse.get(pending.pop(),()):
                    if dependent not in affected:affected.add(dependent);pending.append(dependent)
            return affected

        def analyze(identifier,current_sources,rootset,previous=None):
            cached=state['results'].get(identifier)
            if cached:return copy.deepcopy(cached),True,0
            if not current_sources:
                result={'available':True,'passed':True,'exit_code':0,'output':'','engine':'luau-lsp Roblox','project_sourcemap':True}
            else:
                result=self.api.analyze([folder/key for key in sorted(rootset)],folder,mapfile,directory=api_directory)
                if previous and rootset!=set(current_sources):
                    # Keep old diagnostics outside affected roots; never report a
                    # partial analysis as a complete clean project.
                    keep=[line for line in diagnostics(previous.get('output','')) if not any(key in line for key in rootset)]
                    result['output']='\n'.join(dict.fromkeys(keep+diagnostics(result['output'])))
                    result['passed']=result['exit_code']==0 and not result['output']
            state['results'][identifier]=copy.deepcopy(result)
            return result,False,len(rootset)

        previous=state.get('baseline_result')
        changed={key for key in sources if state.get('hashes',{}).get(key)!=hashes[key]}
        affected=roots(changed) if changed or state.get('structure')!=structural else set()
        if not affected and baseline_id not in state['results']:affected=set(sources)
        updates=args.get('changes')
        if updates is None and args.get('source') is not None:updates=[{'path':args.get('path'),'source':args['source'],'create_class':args.get('create_class')}]
        updates=updates or []
        pending = None
        if updates and sources and affected == set(sources) and baseline_id not in state['results']:
            for name in ('.luaurc','sourcemap.json',*sources):
                shutil.copyfile(folder/name,scratch/name)
            pending = worker.submit(self.api.analyze,[scratch/key for key in sorted(sources)],
                                    scratch,scratch/'sourcemap.json',directory=api_directory)
            baseline,hit,checked = None,False,len(affected)
        else:
            baseline,hit,checked=analyze(baseline_id,sources,affected,previous)
        proposed=copy.deepcopy(baseline);proposed_hit=hit;proposal_checked=0
        original_tree=copy.deepcopy(tree);modified=set()
        try:
            for entry in sorted(args.get('containers', []), key=lambda row:len(row['path'])):
                parent=tree
                for segment in entry['path'][:-1]:
                    children=[child for child in parent.get('children',[]) if child['name']==segment]
                    if len(children)!=1:raise ValueError('Proposed parent must resolve exactly once')
                    parent=children[0]
                children=parent.setdefault('children',[])
                if any(child['name']==entry['path'][-1] for child in children):
                    raise ValueError('Proposed creation already exists')
                children.append({'name':entry['path'][-1],'className':entry['className'],'children':[]})
            for update in updates:
                path=update['path'];source=update['source']
                if not isinstance(path,list) or not path or not all(isinstance(p,str) and p for p in path):raise ValueError('Invalid proposed script path')
                if not isinstance(source,str) or len(source.encode())>200000:raise ValueError('Proposed source exceeds 200 KB')
                matches=[key for key,value in paths.items() if value==path]
                if len(matches)>1:raise ValueError('Replacement script path must match exactly one live script')
                if not matches:
                    if update.get('create_class') not in ('Script','LocalScript','ModuleScript'):raise ValueError('Replacement script path not present in live export')
                    parent=tree
                    for part in path[:-1]:
                        children=[v for v in parent.get('children',[]) if v['name']==part]
                        if len(children)!=1:raise ValueError('New script parent must resolve exactly once')
                        parent=children[0]
                    key='script_'+sha(json.dumps(path))[:16]+'.luau'
                    if key in sources:raise ValueError('Analysis filename collision')
                    children=parent.setdefault('children',[])
                    node=next((child for child in children if child['name']==path[-1]),None)
                    if node is None:
                        node={'name':path[-1],'className':update['create_class'],'children':[]}
                        children.append(node)
                    if node['className']!=update['create_class']:raise ValueError('Proposed script class mismatch')
                    node['filePaths']=[key]
                    paths[key]=path
                else:key=matches[0]
                modified.add(key);(folder/key).write_text(source,encoding='utf-8',newline='')
            if updates:
                candidate=dict(sources)
                for key in modified:candidate[key]=(folder/key).read_text(encoding='utf-8')
                proposal_id=sha(json.dumps([tree,{key:sha(value) for key,value in candidate.items()}],sort_keys=True))
                mapfile.write_text(json.dumps(tree),encoding='utf-8')
                # New hierarchy changes affect all scripts. Dynamic requires do too.
                selected=set(candidate) if tree!=original_tree else roots(modified)
                if pending and selected != set(candidate):
                    baseline = pending.result()
                proposed,proposed_hit,proposal_checked=analyze(proposal_id,candidate,selected,baseline)
        finally:
            mapfile.write_text(json.dumps(original_tree),encoding='utf-8')
            for key in modified:
                if key in sources:(folder/key).write_text(sources[key],encoding='utf-8',newline='')
                elif (folder/key).exists():(folder/key).unlink()
        if pending:
            baseline = pending.result()
            state['results'][baseline_id] = copy.deepcopy(baseline)
        introduced,resolved=regressions(baseline.get('output',''),proposed.get('output',''))
        engine_ok=baseline.get('exit_code',0)==0 and proposed.get('exit_code',0)==0
        # Unclassified engine warnings/errors cannot be grandfathered as old code errors.
        unclassified=[v for v in diagnostics(proposed.get('output','')) if '.luau' not in v]
        gate=engine_ok and not introduced and not unclassified if updates else proposed['passed']
        state.update(structure=structural,hashes=hashes,baseline_result=baseline)
        state['results']=dict(list(state['results'].items())[-12:])
        temp=statefile.with_name(uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(state),encoding='utf-8');temp.replace(statefile)
        path_counts=Counter(json.dumps(paths[key]) for key in sources)
        return {**proposed,'gate_passed':gate,'regression_free':engine_ok and not introduced and not unclassified,
                'introduced_diagnostics':introduced,'resolved_diagnostics':resolved,'baseline_diagnostics':len(diagnostics(baseline.get('output',''))),
                'scripts_analyzed':len(sources)+len(modified-set(sources)),'instances_mapped':data['nodes'],'complete':True,'captured_at':data['captured_at'],
                'snapshot_sha256':baseline_id,'source_manifest':{json.dumps(paths[key]):hashes[key] for key in sources if path_counts[json.dumps(paths[key])]==1},
                'ambiguous_source_paths':[json.loads(path) for path,count in path_counts.items() if count>1],
                'incremental':{'baseline_cache_hit':hit,'proposal_cache_hit':proposed_hit,'baseline_roots_checked':checked,
                               'proposal_roots_checked':proposal_checked,'elapsed_ms':round((time.monotonic()-started)*1000)},
                'duplicate_nodes_aliased':data.get('duplicate_aliases',0),'excluded_editor_services':data.get('excluded_editor_services',[]),
                'note':'Capture is fresh. Cached results require identical source/hierarchy/API fingerprints. Unresolved dependencies trigger full checking. Existing diagnostics remain visible; proposed writes gate on new diagnostics.'}
