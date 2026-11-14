"""Staged edits spanning editor sources and reversible instance changes."""
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import os
from pathlib import Path
import tempfile

from impact import impact
from team import integer

ROOT = Path(__file__).resolve().parent
PROPERTIES = (ROOT / "operations.luau").read_text(encoding="utf-8").split("local function attributes(instance)", 1)[0]
SNAPSHOT_RESPONSE = (ROOT / "snapshot_response.luau").read_text(encoding="utf-8")
MIXED_OPERATIONS = (ROOT / "mixed.luau").read_text(encoding="utf-8-sig")
PATH = {"type":"array", "items":{"type":"string"}, "minItems":1, "maxItems":64}


def path_arg(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 64 or any(
        not isinstance(part, str) or not part or len(part) > 200 for part in value
    ):
        raise ValueError("Expected 1..64 exact path segments")
    return value


def schema(tool):
    return [
        tool("impact", "Show literal references, possible name matches and transitive require dependents for an instance or script. Returns bounded excerpts.", {
            "studio_id":{"type":"string"}, "path":PATH,
            "limit":{"type":"integer","minimum":1,"maximum":50},
            "depth":{"type":"integer","minimum":1,"maximum":8},
            "include_source":{"type":"boolean","description":"Return the target script's editor source and expected_hash from the same capture."},
            "detail":{"enum":["summary","full"]},
        }, ("studio_id", "path"), True),
        tool("model_export", "Export selected instance subtrees to a new Roblox binary model file in the workspace.", {
            "studio_id":{"type":"string"}, "file":{"type":"string"},
            "paths":{"type":"array","items":PATH,"minItems":1,"maxItems":30},
        }, ("studio_id","file","paths")),
        tool("mixed_changes", "Stage, validate on a local copy, apply and roll back mixed Studio edits.", {
            "action":{"enum":["prepare","status","apply","rollback","validate","compare_plans","export_scene","prepare_scene","remote_handlers"]},
            "studio_id":{"type":"string"}, "plan_id":{"type":"string"}, "format":{"type":"boolean"},
            "detail":{"enum":["summary","full"]},
            "copy_studio_id":{"type":"string","description":"Separate local place window for validation."},
            "peer_plan_id":{"type":"string","description":"Another prepared plan to compare or validate together."},
            "file":{"type":"string"},
            "path":PATH,
            "properties":{"type":"array","items":{"type":"string"},"maxItems":20},
            "scenario":{"type":"object","properties":{"name":{"type":"string"},"version":{"type":"integer"}},"required":["name","version"]},
            "baseline_scenario":{"type":"object","properties":{"name":{"type":"string"},"version":{"type":"integer"}},
                                 "required":["name","version"],"additionalProperties":False,
                                 "description":"On prepare, run this saved scenario version before edits, alongside local analysis. Returns its result."},
            "items":{"type":"array","minItems":1,"maxItems":30,"items":{"type":"object","properties":{
                "kind":{"enum":["script","properties","create","relocate"]}, "path":PATH,
                "destination":PATH,
                "class":{"type":"string"}, "properties":{"type":"object"},
                "source":{"type":"string"}, "expected_hash":{"type":"string"},
            },"required":["kind","path"]}},
        }, ("action",)),
    ]


def mixed_payload(phase, items, plan=None, local_copy=False):
    raw = json.dumps({"phase":phase, "items":items, "plan":plan, "local_copy":local_copy}, ensure_ascii=True, allow_nan=False)
    if len(raw) > 800000:
        raise ValueError("Mixed plan exceeds 800 KB")
    return raw


class StudioPlans:
    def __init__(self, wb):
        self.wb = wb

    def run_luau(self, studio, phase, items, plan=None, local_copy=False):
        raw = mixed_payload(phase, items, plan, local_copy)
        code = 'local payload = game:GetService("HttpService"):JSONDecode(' + json.dumps(raw) + ')\n'
        code += PROPERTIES + SNAPSHOT_RESPONSE + MIXED_OPERATIONS
        rpc = self.wb.read_rpc if phase == "prepare" else self.wb.rpc
        result = rpc("tools/call", {"name":"execute_luau", "arguments":{
            "studio_id":studio, "datamodel_type":"Edit", "code":code,
        }})
        if result.get("isError"):
            raise ValueError(str(result.get("content")))
        return self.wb.api.assemble(json.loads(result["content"][0]["text"]), studio)

    def call(self, name, args):
        if name == "model_export":
            return self.export_model(args)
        if name == "impact":
            target = tuple(path_arg(args["path"]))
            limit = integer(args.get("limit", 10), 1, 50, "limit")
            depth = integer(args.get("depth", 3), 1, 8, "depth")
            studio = args["studio_id"]
            snapshot = self.wb.api.export(studio)
            if self.wb.auto_bind_projects and "place_id" in snapshot and "universe_id" in snapshot:
                self.wb.advanced.auto_bind(studio, observed_identity=snapshot)
            result = impact(snapshot, target, limit, depth, args.get("include_source", False))
            if args.get("detail") == "full":
                return result
            return {key:result[key] for key in ("literal_references","require_dependents","unresolved_requires","counts","source","hash") if key in result}
        action = args["action"]
        if action in ("export_scene","prepare_scene"):
            return self.scene_sync(args)
        if action == "remote_handlers":
            target = tuple(path_arg(args["path"]))
            snapshot = self.wb.api.export(args["studio_id"])
            context = impact(snapshot,target,20,1)
            paths = {tuple(row["path"]) for row in context["literal_references"]+context["possible_name_matches"]}
            candidates = []
            pending = [(snapshot["tree"],())]
            while pending:
                node, path = pending.pop()
                if path in paths:
                    for index,line in enumerate(node.get("source","").splitlines(),1):
                        if "OnServerEvent" in line or "OnServerInvoke" in line:
                            candidates.append({"path":path,"line":index,"excerpt":line.strip()[:160]})
                            if len(candidates) >= 20:
                                break
                if len(candidates) >= 20:
                    break
                pending.extend((child,path+(child.get("actualName",child["name"]),)) for child in node.get("children",[]))
            return {"candidates":candidates,"coverage":"handlers in scripts with a matching path or name"}
        if action == "prepare":
            return self.prepare(args)
        row = self.wb.advanced.operation(args["plan_id"])
        if action == "compare_plans" or (action == "validate" and "peer_plan_id" in args):
            peer = self.wb.advanced.operation(args["peer_plan_id"])
            if (row["id"] == peer["id"] or any(plan["kind"] != "mixed" or plan["state"] != "prepared" for plan in (row,peer))
                    or row["project"] != peer["project"] or row["studio"] != peer["studio"]
                    or self.wb.owner not in (row["owner"],peer["owner"])):
                raise ValueError("Choose distinct prepared mixed plans for the same Studio, including one of your own")
            conflicts = []
            for left in row["after_json"]:
                for right in peer["after_json"]:
                    for a in (left["path"],left.get("destination")):
                        for b in (right["path"],right.get("destination")):
                            if a and b and (a[:len(b)] == b or b[:len(a)] == a):
                                if (a == b and left["kind"] == right["kind"] == "properties"
                                        and not left["properties"].keys() & right["properties"].keys()):
                                    continue
                                conflicts.append({"left":a,"right":b})
            if action == "compare_plans" or conflicts:
                return {"overlap_count":len(conflicts),"overlaps":conflicts[:20],
                        **({"blocked":True} if action == "validate" else {})}
            if row["owner"] != self.wb.owner:
                raise ValueError("Use your own plan as plan_id for combined validation")
            return self.validate_copy(row,args,peer)
        if row["kind"] != "mixed" or row["owner"] != self.wb.owner:
            raise ValueError("Mixed plan belongs to another operation or agent")
        if action == "status":
            return {key:row[key] for key in ("id","studio","state","result")}
        if action == "validate":
            return self.validate_copy(row, args)
        if action not in ("apply", "rollback"):
            raise ValueError("Invalid plan action")
        return self.apply(row, args.get("studio_id", row["studio"]), action)

    def scene_sync(self, args):
        relative = Path(args["file"])
        file = (self.wb.workspace / relative).resolve()
        if relative.is_absolute() or file.suffix.lower() != ".json" or not file.is_relative_to(self.wb.workspace):
            raise ValueError("Use a workspace-relative JSON file")
        studio = args["studio_id"]
        if args["action"] == "export_scene":
            names = args["properties"]
            if not isinstance(names,list) or not 1 <= len(names) <= 20 or any(not isinstance(k,str) for k in names):
                raise ValueError("Select up to 20 property names")
            path = path_arg(args["path"])
            payload = {"path":path,"properties":names,"depth":32,"limit":30,"reversible_only":True}
            prefix = 'local operation = "snapshot"\nlocal payload = game:GetService("HttpService"):JSONDecode('
            code = prefix + json.dumps(json.dumps(payload)) + ')\n' + (ROOT/"operations.luau").read_text(encoding="utf-8")
            result = self.wb.read_rpc("tools/call",{"name":"execute_luau","arguments":{
                "studio_id":studio,"datamodel_type":"Edit","code":code}})
            if result.get("isError"):
                raise ValueError(result["content"][0]["text"])
            data = json.loads(result["content"][0]["text"])
            if data.get("truncated") or any(len(item["path"])-len(path) >= 32 for item in data["instances"]):
                raise ValueError("Select a smaller subtree")
            rows = []
            for item in data["instances"]:
                properties = {k:v for k,v in item["properties"].items()
                              if (not isinstance(v,dict) or v.get("type") in
                                  {"Instance","Vector3","Vector2","Color3","CFrame","UDim","UDim2","Enum"})
                              and k not in {"Parent","Name","Source","Archivable"}}
                if properties:
                    rows.append({"path":item["path"],"class":item["class"],"base":properties,"properties":properties})
            with file.open("x",encoding="utf-8") as stream:
                json.dump({"rows":rows},stream,indent=2,ensure_ascii=False)
            return {"file":str(relative),"rows":len(rows)}
        if file.stat().st_size > 200000:
            raise ValueError("Scene manifest exceeds 200 KB")
        rows = json.loads(file.read_text(encoding="utf-8"))["rows"]
        items = []
        for row in rows:
            if row["base"].keys() != row["properties"].keys():
                raise ValueError("Edit values of existing manifest properties")
            changes = {key:value for key,value in row["properties"].items() if value != row["base"][key]}
            if changes:
                items.append({"kind":"properties","path":row["path"],"class":row["class"],"properties":changes,
                              "expected":{key:row["base"][key] for key in changes}})
        if not items:
            return {"change_count":0}
        return self.prepare({"studio_id":studio,"items":items}, from_scene=True)

    def capture_fingerprint(self, studio):
        tree = self.wb.api.export(studio)["tree"]

        def node_signature(node):
            children = [node_signature(child) for child in node.get("children", [])]
            return (node.get("actualName",node["name"]), node["className"], node.get("source",""),
                    sorted(children))

        services = {"Workspace","Lighting","ReplicatedFirst","ReplicatedStorage","ServerScriptService",
                    "ServerStorage","StarterGui","StarterPack","StarterPlayer","SoundService","Teams",
                    "MaterialService","TextChatService"}
        nodes = [node_signature(node) for node in tree["children"] if node["className"] in services and node.get("children")]
        return hashlib.sha256(json.dumps(sorted(nodes), ensure_ascii=True).encode()).hexdigest()

    def validate_copy(self, row, args, peer=None):
        source, copy = row["studio"], args["copy_studio_id"]
        if args.get("studio_id", source) != source or source == copy or row["state"] != "prepared":
            raise ValueError("Choose a prepared plan and a separate local Studio copy")
        scenario = args["scenario"]
        name = scenario["name"]
        version = integer(scenario["version"],1,2**31-1,"version")
        self.wb.team.authorize_studio_action(source)
        self.wb.team.authorize_studio_action(copy)
        report = {"copy_studio_id":copy}
        with self.wb.store.lease("studio:"+source,self.wb.owner), self.wb.store.lease("studio:"+copy,self.wb.owner):
            row = self.wb.advanced.operation(row["id"])
            if row["state"] != "prepared":
                raise ValueError("Plan is no longer prepared")
            combined = row["after_json"]
            if peer:
                peer = self.wb.advanced.operation(peer["id"])
                if peer["state"] != "prepared":
                    raise ValueError("Peer plan is no longer prepared")
                combined = combined + peer["after_json"]
                report["peer_plan_id"] = peer["id"]
            windows = self.wb.read_rpc("tools/call", {"name":"list_roblox_studios","arguments":{}})
            if windows.get("isError"):
                raise ValueError("Could not list Studio windows")
            windows = {item["id"]:item for item in json.loads(windows["content"][0]["text"])["studios"]}
            target = windows.get(copy)
            if (not target or target.get("mode") != "Edit" or target.get("persistedToCloud")
                    or Path(target.get("file") or "").suffix.lower() not in (".rbxl",".rbxlx")):
                raise ValueError("Open a local .rbxl or .rbxlx copy in a separate Edit window")
            source_window = windows.get(source)
            if not source_window or source_window.get("mode") != "Edit":
                raise ValueError("The source window must also be in Edit mode")
            original = self.capture_fingerprint(source)
            live_check = self.run_luau(source,"prepare",combined)
            if not live_check["success"]:
                return {"blocked":True,"error":live_check["error"]}
            if self.capture_fingerprint(copy) != original:
                raise ValueError("The copy's authored hierarchy or scripts differ. Export a fresh copy of the original.")
            key = "combined_validation" if peer else "validation"
            pending_validation = {**report,"passed":False,"source_changed":False,"source_fingerprint":original}
            self.wb.advanced.finish(row["id"],"prepared",{**row["result"],key:pending_validation})
            items = [{k:v for k,v in item.items() if k not in ("identity","parent_identity","destination_identity")}
                     for item in combined]
            checked = self.run_luau(copy,"prepare",items,local_copy=True)
            if not checked["success"]:
                return {"blocked":True,"error":checked["error"]}
            scenario_args = {"studio_id":copy,"action":"run","name":name,"version":version}
            baseline = self.wb.workflows.scenario(scenario_args,studio_locked=True,scenario_project=row["project"])
            report.update(baseline_run_id=baseline["run_id"],baseline_passed=baseline["passed"])
            if any("Target is not reachable" in str(failure) or "cleanup_error" in failure
                   or failure.get("tool") == "start_stop_play" or not failure.get("tool")
                   for failure in baseline.get("failures",[])):
                return {"blocked":True,"error":"Baseline playtest did not complete",**report}
            result = self.run_luau(copy,"apply",items,row["id"],local_copy=True)
            if not result["success"]:
                return {"blocked":True,"error":result["error"],**report}
            candidate = self.wb.workflows.scenario(scenario_args,studio_locked=True,scenario_project=row["project"])
            final_check = self.run_luau(source,"prepare",combined)
            report.update(candidate_run_id=candidate["run_id"],passed=candidate["passed"],
                          source_fingerprint=original,source_changed=self.capture_fingerprint(source)!=original
                          or not final_check["success"])
            self.wb.advanced.finish(row["id"],"prepared",{**row["result"],key:report})
        return {"plan_id":row["id"],**report,"blocked":not report["passed"] or report["source_changed"]}

    def export_model(self, args):
        relative = Path(args["file"])
        file = (self.wb.workspace / relative).resolve()
        if relative.is_absolute() or file.suffix.lower() != ".rbxm" or not file.is_relative_to(self.wb.workspace):
            raise ValueError("Use a workspace-relative .rbxm file")
        paths = args["paths"]
        if not isinstance(paths,list) or not 1 <= len(paths) <= 30:
            raise ValueError("Select one to thirty model roots")
        paths = sorted((path_arg(path) for path in paths), key=len)
        for index,path in enumerate(paths):
            if any(path[:len(parent)] == parent for parent in paths[:index]):
                raise ValueError("Model export roots overlap")
        if file.exists():
            raise ValueError("Export file already exists")
        payload = json.dumps({"paths":paths},ensure_ascii=True)
        code = 'local payload = game:GetService("HttpService"):JSONDecode(' + json.dumps(payload) + ')\n'
        code += (ROOT/"model_export.luau").read_text(encoding="utf-8") + "\ndo\n"
        code += (ROOT/"project_chunk.luau").read_text(encoding="utf-8") + "\nend"
        studio = args["studio_id"]
        response = self.wb.read_rpc("tools/call",{"name":"execute_luau","arguments":{
            "studio_id":studio,"datamodel_type":"Edit","code":code,
        }})
        if response.get("isError"):
            raise ValueError(str(response["content"]))
        envelope = json.loads(response["content"][0]["text"])
        data = self.wb.api.assemble(envelope,studio,binary=True)
        if not isinstance(data,bytes) or not data.startswith(b"<roblox!"):
            raise ValueError("Studio did not return a binary Roblox model")
        file.parent.mkdir(parents=True,exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=file.parent,prefix=".export-")
        try:
            with os.fdopen(handle,"wb") as stream:
                stream.write(data)
            os.link(temporary,file)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"file":str(file),"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}

    def prepare(self, args, from_scene=False):
        from workbench import diff, digest

        studio = args["studio_id"]
        items = args["items"]
        scenario = args.get("baseline_scenario")
        if scenario is not None and (not isinstance(scenario, dict) or not isinstance(scenario.get("name"), str) or
                                     type(scenario.get("version")) is not int or scenario["version"] < 1):
            raise ValueError("Provide a saved scenario name and exact positive version")

        def run_baseline():
            outcome = self.wb.workflows.scenario({"studio_id":studio,"action":"run","name":scenario["name"],
                                                  "version":scenario["version"]})
            return {key:outcome[key] for key in ("run_id","passed","blocked","failures") if key in outcome}

        if not isinstance(items, list) or not 1 <= len(items) <= 30:
            raise ValueError("Expected 1..30 changes")
        if self.wb.auto_bind_projects:
            self.wb.advanced.auto_bind(studio)
        if not isinstance(args.get("format", True), bool):
            raise ValueError("format must be a boolean")
        prepared, preview, changes = [], [], []
        containers, lint_inputs = [], []
        seen, script_hashes = set(), {}
        for item in items:
            path = path_arg(item["path"])
            kind = item["kind"]
            if tuple(path) in seen:
                raise ValueError("Duplicate plan path")
            seen.add(tuple(path))
            if kind == "relocate":
                destination = path_arg(item["destination"])
                if min(len(path), len(destination)) < 2 or destination[:len(path)] == path:
                    raise ValueError("Relocation needs a distinct destination outside the target subtree")
                prepared.append({"kind":kind, "path":path, "destination":destination})
                preview.append({"kind":kind, "path":path, "destination":destination})
                continue
            if kind == "script":
                script_hashes[tuple(path)] = item["expected_hash"]
                source = item["source"]
                if args.get("format", True):
                    source = self.wb.code_tool(source, True)["source"]
                prepared.append({"kind":kind, "path":path, "source":source})
                changes.append({"path":path, "source":source})
                preview.append({"path":path})
                continue
            if kind not in ("create", "properties"):
                raise ValueError("Unknown mixed operation")
            props = item.get("properties", {})
            if not isinstance(props, dict) or len(props) > 20 or set(props) & {"Parent","Name","Source","Archivable"}:
                raise ValueError("Expected up to 20 reversible properties; identity and source are reserved")
            if kind == "properties" and not props:
                raise ValueError("Property edit is empty")
            entry = {"kind":kind, "path":path, "properties":props}
            if from_scene:
                entry.update({"expected":item["expected"],"class":item["class"]})
            if kind == "create":
                if len(path) < 2:
                    raise ValueError("New instance requires an existing parent")
                entry["class"] = item["class"]
                if entry["class"] in ("Script", "LocalScript", "ModuleScript"):
                    source = item["source"]
                    if args.get("format", True):
                        source = self.wb.code_tool(source, True)["source"]
                    lint_inputs.append((source, None))
                    entry["source"] = source
                    changes.append({"path":path, "source":source, "create_class":entry["class"]})
                elif "source" in item:
                    raise ValueError("Source is only supported for a script")
                containers.append({"path":path, "className":entry["class"]})
            prepared.append(entry)
            preview.append({"path":path, "kind":kind})
        ordered = sorted(zip(prepared, preview, strict=True), key=lambda pair:len(pair[0]["path"]))
        prepared, preview = map(list, zip(*ordered, strict=True))
        for item in prepared:
            if item["kind"] != "relocate":
                continue
            for other in prepared:
                if other is item:
                    continue
                for path in (item["path"], item["destination"]):
                    for peer in (other["path"], other.get("destination", other["path"])):
                        if path[:len(peer)] == peer or peer[:len(path)] == path:
                            raise ValueError("Relocations must be disjoint from other plan paths")
        result = self.run_luau(studio, "prepare", prepared)
        if not result["success"]:
            return {"blocked":True, "error":result["error"]}
        for item, view, captured in zip(prepared, preview, result["items"], strict=True):
            if "identity" in captured:
                item["identity"] = captured["identity"]
            if "parent_identity" in captured:
                item["parent_identity"] = captured["parent_identity"]
            if item["kind"] == "script":
                before = captured["before"]
                if digest(before) != script_hashes[tuple(item["path"])]:
                    raise ValueError("Script source changed before preparation")
                item["expected"] = before
                lint_inputs.append((item["source"], {"baseline_source":before}))
                view["diff"] = diff(before, item["source"])
            if "source" in item and item["kind"] == "create":
                view["diff"] = diff("", item["source"])
            if item["kind"] == "relocate":
                item["destination_identity"] = captured["destination_identity"]
            if item["kind"] in ("create","properties"):
                item["properties"] = captured["proposed"]
                if item["kind"] == "properties":
                    item["expected"] = captured["before"]
                    item["class"] = captured["class"]
                view.update({key:value for key,value in captured.items() if key not in ("identity","parent_identity")})
        mixed_payload("apply", prepared, "0" * 32)
        baseline = None
        if changes:
            with ThreadPoolExecutor(max_workers=min(4, len(lint_inputs)) + bool(scenario)) as workers:
                lint_jobs = [workers.submit(self.wb.code_tool, source, lint_only=True, context=context)
                             for source, context in lint_inputs]
                baseline_job = None

                def start_baseline():
                    nonlocal baseline_job
                    baseline_job = workers.submit(copy_context().run, run_baseline)

                checks = self.wb.api.project({"studio_id":studio, "changes":changes, "containers":containers},
                                             **({"after_capture":start_baseline} if scenario else {}))
                if baseline_job is not None:
                    baseline = baseline_job.result()
                for job in lint_jobs:
                    lint = job.result()
                    if not lint.get("gate_passed", lint["passed"]):
                        return {"blocked":True, "checks":lint, **({"baseline":baseline} if baseline is not None else {})}
            if not checks.get("gate_passed", checks["passed"]):
                return {"blocked":True, "checks":checks, **({"baseline":baseline} if baseline is not None else {})}
        elif scenario:
            baseline = run_baseline()
        plan = self.wb.advanced.begin(studio, [], prepared, kind="mixed")
        self.wb.advanced.finish(plan, "prepared", {"changes":preview, **({"baseline":baseline} if baseline is not None else {})})
        response = {"plan_id":plan, "state":"prepared", **({"baseline":baseline} if baseline is not None else {})}
        if args.get("detail") == "full":
            return {**response, "changes":preview}
        return {**response, "change_count":len(prepared)}

    def apply(self, row, studio, action):
        if self.wb.auto_bind_projects:
            self.wb.advanced.auto_bind(studio)
        if studio != row["studio"] or self.wb.store.project_key(studio) != row["project"]:
            raise ValueError("Mixed plans are bound to the Studio window that prepared them")
        plan = row["id"]
        rollback = action == "rollback"
        with self.wb.store.lease("studio:" + studio, self.wb.owner):
            row = self.wb.advanced.operation(plan)
            if row["state"] != ("committed" if rollback else "prepared"):
                raise ValueError("Plan state does not permit this action")
            validation = row["result"].get("validation")
            if validation and not rollback and (not validation["passed"] or validation["source_changed"] or
                                                self.capture_fingerprint(studio) != validation["source_fingerprint"]):
                raise ValueError("Copy validation failed or is stale. Validate again with a fresh copy.")
            items = row["after_json"]
            for item in items:
                self.wb.assert_script_lock(studio, item["path"])
                if item["kind"] == "relocate":
                    self.wb.assert_script_lock(studio, item["destination"])
            scripts = [{"path":item["path"], "source":item["source"],
                        **({"create_class":item["class"]} if item["kind"] == "create" else {})}
                       for item in items if "source" in item]
            if scripts and not rollback:
                containers = [{"path":item["path"], "className":item["class"]}
                              for item in items if item["kind"] == "create"]
                checks = self.wb.api.project({"studio_id":studio, "changes":scripts, "containers":containers})
                if not checks.get("gate_passed", checks["passed"]):
                    return {"blocked":True, "checks":checks}
            for item in items:
                if item["kind"] == "script":
                    source = item["source"] if rollback else item["expected"]
                    self.wb.store.record(studio, item["path"], source, "before_mixed_change", self.wb.owner)
            if rollback:
                items = [dict(item) for item in reversed(items)]
                for item in items:
                    if item["kind"] == "script":
                        item["source"], item["expected"] = item["expected"], item["source"]
                    elif item["kind"] == "properties":
                        item["properties"], item["expected"] = item["expected"], item["properties"]
                    elif item["kind"] == "relocate":
                        item["path"], item["destination"] = item["destination"], item["path"]
                        item["parent_identity"], item["destination_identity"] = (
                            item["destination_identity"], item["parent_identity"])
                    else:
                        item["expected"] = item["properties"]
            self.wb.advanced.finish(plan, "rolling_back" if rollback else "applying", {})
            try:
                result = self.run_luau(studio, action, items, plan)
            except Exception as error:
                self.wb.advanced.finish(plan, "uncertain", {"error":str(error)})
                raise
            state = row["state"]
            if result["success"]:
                state = "rolled_back" if rollback else "committed"
            elif result.get("conflict"):
                state = "conflict"
            self.wb.advanced.finish(plan, state, result)
        response = {"plan_id":plan, "state":state, "blocked":not result["success"]}
        return {**response, **result} if not result["success"] else {**response, "success":True}
