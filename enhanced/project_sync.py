"""Bidirectional sync of the scripts in a Studio project."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile


SCRIPT_SUFFIX = {"Script": ".server.luau", "LocalScript": ".client.luau", "ModuleScript": ".luau"}


def schema(tool):
    string = {"type": "string"}
    return [tool("project_sync", "Preview or apply bidirectional sync of a Studio project's scripts. Generates a Rojo project; conflicting or deleted sources require explicit resolution.", {
        "studio_id": string, "directory": string,
        "scope": {"type": "array", "items": string},
        "action": {"enum": ["preview", "apply", "status", "diff"]}, "plan_id": string,
        "path": {"type": "array", "items": string},
        "resolutions": {"type":"array","maxItems":50,"items":{"type":"object","properties":{
            "path":{"type":"array","items":string},"use":{"enum":["studio","disk","merged"]},
            "studio_hash":string,"disk_hash":string,"source":string,
        },"required":["path","use","studio_hash","disk_hash"]}},
        "offset": {"type": "integer", "minimum": 0},
    }, ("studio_id", "action"))]


def read(file, limit=200_000):
    try:
        with file.open("rb") as stream:
            data = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(data) > limit:
        raise ValueError(f"File exceeds sync size limit: {file.name}")
    return data.decode("utf-8").replace("\r\n", "\n")


def write(file, source, previous):
    file.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=file.parent, prefix=".sync-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(source)
        if read(file, 8_000_000) != previous:
            raise ValueError(f"File changed during sync: {file.name}")
        if previous is None:
            # An exclusive link prevents replacing a new file created after the check.
            os.link(temporary, file)
        else:
            os.replace(temporary, file)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ProjectSync:
    def __init__(self, wb):
        self.wb = wb
        with wb.store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS project_sync_roots(
                       directory TEXT PRIMARY KEY, project TEXT NOT NULL, scope TEXT NOT NULL, mapping TEXT NOT NULL)""")

    def directory(self, relative):
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError("Provide a directory relative to the configured workspace")
        directory = (self.wb.workspace / relative).resolve()
        if directory == self.wb.workspace or not directory.is_relative_to(self.wb.workspace):
            raise ValueError("Use a dedicated sync directory within the workspace")
        return directory

    def file(self, directory, relative):
        file = directory / relative
        if file.resolve() != file or not file.is_relative_to(directory):
            raise ValueError("Sync paths must stay inside the project without symlinks")
        return file

    def live(self, studio, scope):
        tree = self.wb.api.export(studio)["tree"]
        scripts = {}
        stack = [(tree, (), (), False)]
        while stack:
            node, path, classes, ambiguous = stack.pop()
            if path[:len(scope)] != scope and scope[:len(path)] != path:
                continue
            if "source" in node and path[:len(scope)] == scope:
                if ambiguous:
                    raise ValueError(f"Ambiguous Studio script path: {list(path)}")
                if node["className"] not in SCRIPT_SUFFIX:
                    raise ValueError(f"Unsupported script class at {list(path)}")
                scripts[path] = (node["source"], classes)
            children = node.get("children", [])
            counts = {}
            for child in children:
                name = child.get("actualName", child["name"])
                counts[name] = counts.get(name, 0) + 1
            for child in children:
                name = child.get("actualName", child["name"])
                stack.append((child, (*path, name), (*classes, child["className"]), ambiguous or counts[name] > 1))
        return scripts

    def binding(self, directory, project, scope):
        with self.wb.store.connect() as db:
            row = db.execute("SELECT * FROM project_sync_roots WHERE directory=?", (str(directory),)).fetchone()
            if row is not None and (row["project"] != project or json.loads(row["scope"]) != list(scope)):
                raise ValueError("This directory is bound to a different place or scope")
            rows = db.execute("SELECT path,file,base FROM sync WHERE studio=?", (project,)).fetchall()
        return row, {tuple(json.loads(row["path"])): (row["file"], row["base"]) for row in rows}

    def preview(self, args, project, skip_idle=False):
        from studio_plans import path_arg
        from workbench import digest, merge

        studio = args["studio_id"]
        directory = self.directory(args["directory"])
        scope = tuple(path_arg(args["scope"])) if args.get("scope") else ()
        if project == studio:
            raise ValueError("Bind the unpublished place with extra_project_bind before syncing")
        binding, baselines = self.binding(directory, project, scope)
        with self.wb.store.connect() as db:
            claimed = {Path(row["file"]).resolve():(row["studio"],tuple(json.loads(row["path"])))
                       for row in db.execute("SELECT studio,path,file FROM sync")
                       if Path(row["file"]).is_relative_to(directory)}
        current = self.live(studio, scope)
        if not current and not binding:
            raise ValueError("No scripts found in the requested Studio scope")
        tree = {"$className":"DataModel", "$ignoreUnknownInstances":True}
        rows, conflicts = [], []
        resolutions = {}
        for resolution in args.get("resolutions", []):
            path = tuple(path_arg(resolution["path"]))
            if path in resolutions:
                raise ValueError("Duplicate sync resolution")
            resolutions[path] = resolution
        if len(resolutions) > 50:
            raise ValueError("Expected up to 50 conflict resolutions")
        for path in sorted(set(current) | {p for p,(file,_) in baselines.items()
                                          if Path(file).is_relative_to(directory)}):
            if path not in current:
                conflicts.append({"path":path, "reason":"Studio script removed or moved"})
                continue
            source, classes = current[path]
            relative = "scripts/" + digest(json.dumps(path))[:32] + SCRIPT_SUFFIX[classes[-1]]
            file = self.file(directory, relative)
            if file in claimed and claimed[file] != (project,path):
                conflicts.append({"path":path, "file":relative, "reason":"Local file is bound to another project"})
                continue
            prior = baselines.get(path)
            if prior and prior[0] != str(file):
                conflicts.append({"path":path, "reason":"Script already mapped to another file"})
                continue
            base = prior[1] if prior else None
            local = read(file)
            try:
                if path in resolutions:
                    resolution = resolutions.pop(path)
                    if local is None or resolution["studio_hash"] != digest(source) or resolution["disk_hash"] != digest(local):
                        raise ValueError("Sources changed after the conflict was observed")
                    choice = resolution["use"]
                    if choice == "merged":
                        target = resolution["source"]
                        if not isinstance(target,str) or len(target.encode("utf-8")) > 200000:
                            raise ValueError("Merged source must be Luau text up to 200 KB")
                    elif choice in ("studio","disk"):
                        target = source if choice == "studio" else local
                    else:
                        raise ValueError("Unknown sync resolution")
                elif local is None and prior:
                    raise ValueError("Local script removed or moved")
                elif local is None or local == source:
                    target = source
                elif base is None:
                    raise ValueError("Existing file differs from Studio and has no common base")
                else:
                    target = merge(base, local, source)
            except ValueError as error:
                conflicts.append({"path":path, "file":relative, "reason":str(error),
                                  "studio_hash":digest(source), "disk_hash":digest(local) if local is not None else None})
                continue
            node = tree
            for segment, class_name in zip(path, classes, strict=True):
                if segment.startswith("$"):
                    raise ValueError(f"Rojo reserves instance names starting with $: {list(path)}")
                node = node.setdefault(segment, {"$className":class_name, "$ignoreUnknownInstances":True})
            node.pop("$className", None)
            node["$path"] = relative
            rows.append({"path":path, "classes":classes, "file":relative, "studio":source,
                         "disk":local, "target":target, "base":base})
        if resolutions:
            raise ValueError("A resolution refers to an unavailable or unmapped script")
        mapping = json.dumps({"name":directory.name, "tree":tree}, ensure_ascii=False, indent=2) + "\n"
        mapping_file = self.file(directory, "default.project.json")
        previous_mapping = read(mapping_file, 8_000_000)
        if previous_mapping is not None and previous_mapping not in (mapping, binding["mapping"] if binding else None):
            conflicts.append({"file":mapping_file.name, "reason":"Rojo mapping has manual changes"})
        summary = [{"path":row["path"], "file":row["file"],
                    "direction":"merge" if row["target"] not in (row["studio"], row["disk"]) else
                    "push" if row["target"] != row["studio"] else "pull" if row["target"] != row["disk"] else "bind"}
                   for row in rows if row["target"] != row["base"] or row["disk"] != row["studio"]]
        offset = args.get("offset", 0)
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid page offset")
        response = {"scripts":len(current), "changes":summary[offset:offset+50], "change_count":len(summary),
                    "conflicts":conflicts[offset:offset+50], "conflict_count":len(conflicts)}
        if conflicts:
            return {"blocked":True, **response}
        if skip_idle and not summary and mapping == previous_mapping:
            return {"state":"idle", **response}
        pushes = [row for row in rows if row["studio"] != row["target"]]
        if len(pushes) > 30 or len(json.dumps(pushes, ensure_ascii=True)) > 600_000:
            raise ValueError("Use a smaller scope for this write. Studio transactions accept 30 scripts and 600 KB.")
        plan = self.wb.advanced.begin(studio, [], {"directory":str(directory), "scope":scope, "rows":rows,
                                                 "mapping":mapping, "previous_mapping":previous_mapping}, kind="project_sync")
        return {"plan_id":plan, "state":"prepared", **response}

    def apply(self, args, project):
        from workbench import digest

        studio, identifier = args["studio_id"], args["plan_id"]
        operation = self.wb.advanced.operation(identifier)
        if (operation["kind"], operation["project"], operation["studio"], operation["owner"]) != (
            "project_sync", project, studio, self.wb.owner
        ):
            raise ValueError("Sync plan belongs to another agent, project or window")
        plan = operation["after_json"]
        directory = Path(plan["directory"])
        with ExitStack() as leases:
            leases.enter_context(self.wb.store.lease("project_sync_project:" + project, self.wb.owner))
            leases.enter_context(self.wb.store.lease("project_sync:" + str(directory), self.wb.owner))
            if self.wb.advanced.operation(identifier)["state"] != "prepared":
                raise ValueError("Sync plan has already been used")
            _, baselines = self.binding(directory, project, plan["scope"])
            with self.wb.store.connect() as db:
                claimed = {row["file"]:(row["studio"],json.loads(row["path"]))
                           for row in db.execute("SELECT studio,path,file FROM sync")
                           if Path(row["file"]).is_relative_to(directory)}
            rows = plan["rows"]
            mapping = self.file(directory, "default.project.json")
            for row in rows:
                file = self.file(directory, row["file"])
                baseline = baselines.get(tuple(row["path"]))
                if baseline != ((str(file), row["base"]) if row["base"] is not None else None):
                    raise ValueError("Sync base changed; preview again")
                if any(row["target"] != row[field] for field in ("base","disk","studio")):
                    leases.enter_context(self.wb.store.lease("file:" + str(file), self.wb.owner))
                if read(file) != row["disk"]:
                    raise ValueError("Local file changed after preview")
                if str(file) in claimed and claimed[str(file)] != (project,row["path"]):
                    raise ValueError("Local file is bound to another project")
            if read(mapping, 8_000_000) != plan["previous_mapping"]:
                raise ValueError("Rojo mapping changed after preview")
            live = self.live(studio, tuple(plan["scope"]))
            if live != {tuple(row["path"]):(row["studio"],tuple(row["classes"])) for row in rows}:
                raise ValueError("Studio changed after preview")
            items = [{"path":row["path"], "expected_hash":digest(row["studio"]), "source":row["target"],
                      "kind":"script"} for row in rows if row["target"] != row["studio"]]
            self.wb.advanced.finish(identifier, "applying", {})
            studio_plan = None
            written_files = []
            try:
                if items:
                    prepared = self.wb.plans.prepare({"studio_id":studio, "items":items, "format":False})
                    if prepared.get("blocked"):
                        self.wb.advanced.finish(identifier, "not_applied", prepared)
                        return prepared
                    studio_plan = prepared["plan_id"]
                    result = self.wb.plans.call("mixed_changes", {"action":"apply", "plan_id":studio_plan})
                    if result.get("blocked"):
                        self.wb.advanced.finish(identifier, "blocked", result)
                        return result
                for row in rows:
                    if row["target"] != row["disk"]:
                        if row["disk"] is not None:
                            self.wb.store.record(studio, row["path"], row["disk"], "before_project_sync", self.wb.owner)
                        write(self.file(directory, row["file"]), row["target"], row["disk"])
                        written_files.append((row["file"], row["target"], row["disk"]))
                if plan["mapping"] != plan["previous_mapping"]:
                    write(mapping, plan["mapping"], plan["previous_mapping"])
                    written_files.append((mapping.name, plan["mapping"], plan["previous_mapping"]))
                with self.wb.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("INSERT INTO project_sync_roots VALUES(?,?,?,?) ON CONFLICT(directory) DO UPDATE SET mapping=excluded.mapping",
                               (str(directory), project, json.dumps(plan["scope"]), plan["mapping"]))
                    db.executemany("INSERT INTO sync VALUES(?,?,?,?) ON CONFLICT(studio,path) DO UPDATE SET base=excluded.base",
                                   [(project,json.dumps(row["path"]),str(self.file(directory,row["file"])),row["target"])
                                    for row in rows if row["base"] != row["target"]])
            except Exception as error:
                failed = []
                for relative, source, prior in reversed(written_files):
                    try:
                        file = self.file(directory, relative)
                        if read(file, 8_000_000) != source:
                            raise ValueError("File changed after sync write")
                        if prior is None:
                            file.unlink()
                        else:
                            write(file, prior, source)
                    except (OSError, ValueError):
                        failed.append(relative)
                if studio_plan:
                    try:
                        studio_state = self.wb.advanced.operation(studio_plan)["state"]
                        if studio_state == "committed":
                            result = self.wb.plans.call("mixed_changes", {"action":"rollback","plan_id":studio_plan})
                            studio_state = result["state"]
                        if studio_state not in ("rolled_back", "prepared"):
                            failed.append("Studio: " + studio_state)
                    except Exception as rollback_error:
                        failed.append("Studio: " + str(rollback_error))
                self.wb.advanced.finish(identifier, "uncertain" if failed else "not_applied",
                                        {"error":str(error), "studio_plan":studio_plan, "recovery_errors":failed})
                raise
            response = {"plan_id":identifier, "state":"committed", "scripts":len(rows), "studio_plan":studio_plan}
            self.wb.advanced.finish(identifier, "committed", response)
            return response

    def call(self, args):
        studio = args["studio_id"]
        if self.wb.auto_bind_projects:
            self.wb.advanced.auto_bind(studio)
        project = self.wb.store.project_key(studio)
        if args["action"] == "preview":
            return self.preview(args, project)
        if args["action"] == "apply":
            return self.apply(args, project)
        if args["action"] in ("status","diff"):
            operation = self.wb.advanced.operation(args["plan_id"])
            if operation["kind"] != "project_sync" or operation["project"] != project:
                raise ValueError("Sync plan belongs to another project")
            if args["action"] == "diff":
                from workbench import diff

                for row in operation["after_json"]["rows"]:
                    if row["path"] == args["path"]:
                        return {"path":row["path"], "file":row["file"],
                                "studio_diff":diff(row["studio"],row["target"])[:20000],
                                "disk_diff":diff(row["disk"] or "",row["target"])[:20000]}
                raise ValueError("Script does not belong to this plan")
            return {key:operation[key] for key in ("id","state","result")}
        raise ValueError("Unknown project sync action")
