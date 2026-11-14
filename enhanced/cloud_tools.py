"""Typed workflows for the documented Roblox APIs."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


def schema(tool):
    string = {"type":"string"}
    obj = {"type":"object"}
    return [
        tool("cloud_catalog", "Read developer products, subscriptions and user restrictions. Preview and apply product or restriction changes through Studio permissions.", {
            "studio_id":string, "resource":{"enum":["developer_product","subscription","user_restriction"]},
            "action":{"enum":["list","get","prepare","apply"]}, "id":string, "subscription_product_id":string,
            "page_token":string, "body":obj, "plan_id":string, "icon_path":string,
        }, ("studio_id","resource","action")),
        tool("datastore", "Read DataStores, leaderboards and MemoryStore sorted maps using Studio permissions by default. Prepare and apply a guarded standard DataStore update. Use transport api_key for REST.", {
            "studio_id":string, "action":{"enum":["stores","list","get","revisions","prepare","apply"]},
            "store":string, "scope":string, "key":string, "page_token":string,
            "ordered":{"type":"boolean"}, "memory":{"type":"boolean"},
            "transport":{"enum":["studio","api_key"]},
            "page_size":{"type":"integer","minimum":1,"maximum":25},
            "value":{}, "plan_id":string,
        }, ("studio_id","action")),
        tool("cloud_luau", "Run Luau on a published place, read logs, or verify a pinned version with a boolean assertion.", {
            "studio_id":string, "action":{"enum":["start","status","logs","verify","verified_versions"]}, "place_id":string,
            "version":string, "source":string, "task_path":string, "page_token":string,
        }, ("studio_id","action","place_id")),
        tool("place_publish", "Publish a Studio place or file, or restore its latest verified version.", {
            "studio_id":string, "action":{"enum":["open","prepare","apply","status","restore_latest","restore_status"]}, "place_id":string,
            "file":string,"plan_id":string,"transport":{"enum":["studio","api_key"],
                "description":"Studio opens the file and starts the native dialog for you to select the destination. Use the same transport for prepare, apply and status."},
        }, ("studio_id","action","place_id")),
        tool("roblox_lookup", "Look up a public Roblox user, group or group memberships by ID.", {
            "resource":{"enum":["user","group","group_memberships"]},"id":string,"page_token":string,
        }, ("resource","id"), True),
        tool("asset_upload", "Preview a batch of asset uploads, apply it through Studio-approved folders, and check processing or moderation. Completed uploads are recorded in the plan journal.", {
            "studio_id":string, "action":{"enum":["prepare","apply","status","operation"]},
            "plan_id":string, "operation_id":string,
            "creator_id":string,"creator_type":{"enum":["user","group"]},
            "items":{"type":"array","minItems":1,"maxItems":10,"items":{"type":"object","properties":{
                "file":string,"type":{"enum":["Model","Image","Decal","Audio","Animation"]},
                "name":string,"description":string,
            },"required":["file","type","name"]}},
        }, ("studio_id","action")),
    ]


def numeric(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise ValueError("Expected a positive Roblox ID")
    return value


def segment(value):
    if not isinstance(value,str) or not 1 <= len(value) <= 300 or value in (".",".."):
        raise ValueError("Invalid resource identifier")
    return quote(value, safe="")


def query(args):
    return "?" + urlencode({"pageToken":args["page_token"]}) if args.get("page_token") else ""


def icon_digest(file):
    path = Path(file)
    if not path.is_absolute() or path.suffix.lower() not in (".png",".jpg",".jpeg",".bmp") or not path.is_file():
        raise ValueError("Use an absolute image path inside a Studio-approved upload folder")
    with path.open("rb") as stream:
        data = stream.read(20_000_001)
    if not 0 < len(data) <= 20_000_000:
        raise ValueError("Icon is empty or exceeds 20 MB")
    return hashlib.sha256(data).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_key_request(endpoint, data=None, content_type=None, method=None):
    key = os.environ.get("STUDIO_MCP_EXTRA_ROBLOX_API_KEY")
    if not key:
        raise ValueError("Set STUDIO_MCP_EXTRA_ROBLOX_API_KEY on the MCP server with access to this experience")
    headers = {"x-api-key":key}
    if content_type:
        headers["Content-Type"] = content_type
    return request_json("https://apis.roblox.com" + endpoint, data, headers, method)


def request_json(url, data=None, headers=None, method=None):
    request = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as reply:
            raw = reply.read(2_000_001)
    except HTTPError as error:
        raise ValueError(f"Roblox Open Cloud returned HTTP {error.code}") from None
    if len(raw) > 2_000_000:
        raise ValueError("Open Cloud response exceeds the 2 MB budget")
    return json.loads(raw)


def lookup(args):
    target = numeric(args["id"])
    resource = args["resource"]
    if resource == "user":
        return request_json(f"https://users.roblox.com/v1/users/{target}")
    if resource == "group":
        return request_json(f"https://apis.roblox.com/cloud/v2/groups/{target}")
    if resource == "group_memberships":
        return request_json(f"https://apis.roblox.com/cloud/v2/groups/{target}/memberships" + query(args))
    raise ValueError("Unknown lookup resource")


class CloudTools:
    def __init__(self, workflows):
        self.workflows = workflows
        self.wb = workflows.wb
        with self.wb.store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS release_checks(
                universe TEXT, place TEXT, version TEXT, task TEXT, verified REAL,
                PRIMARY KEY(universe,place,version))""")

    def asset_upload(self, studio, universe, args):
        formats = {
            "Model":{".fbx",".glb",".gltf",".rbxm",".rbxmx"}, "Image":{".png",".jpg",".jpeg",".bmp",".tga"},
            "Decal":{".png",".jpg",".jpeg",".bmp",".tga"}, "Audio":{".mp3",".ogg",".wav",".flac"},
            "Animation":{".rbxm",".rbxmx"},
        }

        def fingerprint(file):
            path = Path(file)
            if not path.is_absolute() or not path.is_file() or not 0 < path.stat().st_size <= 20_000_000:
                raise ValueError("Use an existing absolute file in a Studio-approved folder (up to 20 MB)")
            return hashlib.sha256(path.read_bytes()).hexdigest()

        action = args["action"]
        if action == "operation":
            operation = segment(args["operation_id"])
            return self.workflows.cloud(studio,"GET","/assets/v1/operations/"+operation,"asset:read",readonly=True)
        if action == "prepare":
            creator_type = args["creator_type"]
            if creator_type not in ("user","group"):
                raise ValueError("Creator type must be user or group")
            creator = {creator_type+"Id":numeric(args["creator_id"])}
            items = args["items"]
            if not isinstance(items,list) or not 1 <= len(items) <= 10:
                raise ValueError("Expected one to ten assets")
            manifest = []
            for item in items:
                kind, file, name = item["type"], item["file"], item["name"]
                description = item.get("description","")
                if Path(file).suffix.lower() not in formats.get(kind,set()):
                    raise ValueError("Asset type and file extension do not match")
                if not isinstance(name,str) or not name.strip() or len(name) > 100 or not isinstance(description,str) or len(description) > 1000:
                    raise ValueError("Invalid asset name or description")
                manifest.append({"file":file,"sha256":fingerprint(file),"body":{
                    "assetType":kind,"displayName":name,"description":description,"creationContext":{"creator":creator}}})
            plan = {"universe":universe,"manifest":manifest}
            identifier = self.wb.advanced.begin(studio,None,plan,kind="asset_upload")
            return {"plan_id":identifier,"state":"prepared",**plan}
        identifier = args["plan_id"]
        with self.wb.store.lease("cloud:assets:"+str(universe),self.wb.owner):
            row = self.wb.advanced.operation(identifier)
            plan = row["after_json"]
            if row["kind"] != "asset_upload" or row["owner"] != self.wb.owner or plan["universe"] != universe:
                raise ValueError("Upload plan belongs to another agent or experience")
            if action == "status":
                return {"plan_id":identifier,"state":row["state"],"result":row["result"]}
            if action != "apply" or row["state"] != "prepared":
                raise ValueError("Upload plan is not ready")
            self.wb.team.authorize_studio_action(studio)
            for item in plan["manifest"]:
                if fingerprint(item["file"]) != item["sha256"]:
                    raise ValueError("Asset changed since preview")
            operations = []
            for index,item in enumerate(plan["manifest"]):
                self.wb.advanced.finish(identifier,"applying",{"uploads":operations,"next_index":index})
                try:
                    if fingerprint(item["file"]) != item["sha256"]:
                        raise ValueError("Asset changed before upload")
                    result = self.workflows.cloud(studio,"POST","/assets/v1/assets","asset:write",
                                                  {"request":json.dumps(item["body"],ensure_ascii=True)},
                                                  files=[{"name":"fileContent","path":item["file"]}])
                    operations.append({"file":item["file"],"result":result})
                except Exception as error:
                    self.wb.advanced.finish(identifier,"uncertain",{"uploads":operations,"index":index,"error":str(error)})
                    raise
            result = {"uploads":operations}
            self.wb.advanced.finish(identifier,"committed",result)
            return {"plan_id":identifier,"state":"committed",**result}

    def datastore(self, studio, universe, args):
        action = args["action"]
        if action == "apply":
            return self.apply_datastore(studio, universe, args["plan_id"])
        if args.get("transport","studio") == "studio":
            return self.studio_datastore(studio, universe, args)
        if args["transport"] != "api_key":
            raise ValueError("Unknown DataStore transport")
        base = f"/cloud/v2/universes/{universe}"
        if action == "stores":
            return api_key_request(base + "/data-stores" + query(args))
        store = segment(args["store"])
        if args.get("memory"):
            endpoint = base + f"/memory-store/sorted-maps/{store}/items"
            if action not in ("list","get"):
                raise ValueError("Sorted maps support list and get")
        elif args.get("ordered"):
            scope = segment(args.get("scope","global"))
            endpoint = base + f"/ordered-data-stores/{store}/scopes/{scope}/entries"
            if action not in ("list","get"):
                raise ValueError("Ordered DataStores support list and get")
        else:
            endpoint = base + f"/data-stores/{store}"
            if "scope" in args:
                endpoint += "/scopes/" + segment(args["scope"])
            endpoint += "/entries"
        if action in ("get","revisions","prepare"):
            endpoint += "/" + segment(args["key"])
        if action == "prepare":
            current = api_key_request(endpoint)
            if not current.get("etag"):
                raise ValueError("Roblox did not return an ETag for this entry")
            body = {field:current[field] for field in ("etag","attributes","users") if field in current}
            body["value"] = args["value"]
            if len(json.dumps(body,allow_nan=False)) > 250000:
                raise ValueError("DataStore update exceeds the 250 KB budget")
            plan = {"universe":universe,"endpoint":endpoint,"body":body}
            plan_id = self.wb.advanced.begin(studio,current,plan,kind="datastore")
            return {"plan_id":plan_id,"before":current,"proposed":body}
        if action == "revisions":
            endpoint += ":listRevisions"
        return api_key_request(endpoint + query(args))

    def datastore_luau(self, studio, universe, args):
        payload = json.dumps({**args,"universe":str(universe)},ensure_ascii=False,allow_nan=False)
        if len(payload.encode("utf-8")) > 250000:
            raise ValueError("Studio DataStore request exceeds 250 KB")
        prefix = 'local payload = game:GetService("HttpService"):JSONDecode(' + json.dumps(payload,ensure_ascii=False) + ')\n'
        code = prefix + Path(__file__).with_name("studio_datastore.luau").read_text(encoding="utf-8")
        rpc = self.wb.rpc if args["action"] == "apply" else self.wb.read_rpc
        reply = rpc("tools/call",{"name":"execute_luau","arguments":{
            "studio_id":studio,"datamodel_type":"Edit","code":code,
        }})
        if reply.get("isError"):
            raise ValueError(str(reply["content"][0]["text"]))
        return json.loads(reply["content"][0]["text"])

    def studio_datastore(self, studio, universe, args):
        action = args["action"]
        if action not in ("stores","list","get","revisions","prepare"):
            raise ValueError("Unknown DataStore action")
        memory, ordered = bool(args.get("memory")), bool(args.get("ordered"))
        if (memory and ordered) or ((memory or ordered) and action not in ("list","get")):
            raise ValueError("MemoryStore and ordered DataStore support list and get")
        payload = {"action":action,"memory":memory,"ordered":ordered}
        page_size = args.get("page_size", 10)
        if type(page_size) is not int or not 1 <= page_size <= 25:
            raise ValueError("page_size must be 1..25")
        payload.update(page_size=page_size,page=0)
        if action != "stores":
            segment(args["store"])
            scope = args.get("scope","global")
            segment(scope)
            payload.update(store=args["store"],scope=scope)
        if action in ("get","revisions","prepare"):
            segment(args["key"])
            payload["key"] = args["key"]
        token = args.get("page_token")
        if token and action in ("stores","list","revisions"):
            if memory:
                bound = json.loads(token)
                if not isinstance(bound,dict) or set(bound)-{"key","sortKey"}:
                    raise ValueError("Invalid MemoryStore page token")
                segment(bound["key"])
                if "sortKey" in bound and type(bound["sortKey"]) not in (str,int,float):
                    raise ValueError("Invalid MemoryStore sort key")
                payload["bound"] = bound
            elif isinstance(token,str) and re.fullmatch(r"[0-9]{1,2}",token) and int(token) <= 20:
                payload["page"] = int(token)
            else:
                raise ValueError("Studio listing supports page offsets 0..20; use REST for deeper pagination")
        current = self.datastore_luau(studio,universe,payload)
        if action != "prepare":
            for field in ("entries","items","revisions","dataStores"):
                if current.get(field) == {}:
                    current[field] = []
            return current
        if not current.get("found") or not current.get("revisionId"):
            raise ValueError("Studio updates require an existing DataStore key")
        value = args["value"]
        if value is None or len(json.dumps(value,allow_nan=False).encode("utf-8")) > 100000:
            raise ValueError("Use a non-null JSON value up to 100 KB")
        pending = [value]
        while pending:
            item = pending.pop()
            if item is None:
                raise ValueError("Studio DataStore values cannot contain JSON null; use the REST transport if required")
            if isinstance(item,dict):
                pending.extend(item.values())
            elif isinstance(item,list):
                pending.extend(item)
        write = {**payload,"action":"apply","expected":current,"value":value}
        plan = {"universe":universe,"transport":"studio","write":write}
        plan_id = self.wb.advanced.begin(studio,current,plan,kind="datastore")
        return {"plan_id":plan_id,"transport":"studio","before":current,"proposed":{"value":value}}

    def apply_datastore(self, studio, universe, identifier):
        self.wb.team.authorize_studio_action(studio)
        with self.wb.store.lease(f"cloud:datastore:{universe}",self.wb.owner):
            row = self.wb.advanced.operation(identifier)
            plan = row["after_json"]
            if row["kind"] != "datastore" or row["owner"] != self.wb.owner or row["state"] != "prepared" or plan["universe"] != universe:
                raise ValueError("DataStore plan belongs to another agent or experience or was already used")
            self.wb.advanced.finish(identifier,"applying",{})
            try:
                if plan.get("transport") == "studio":
                    result = self.datastore_luau(studio,universe,plan["write"])
                    if result.get("conflict"):
                        self.wb.advanced.finish(identifier,"conflict",result)
                        return {"plan_id":identifier,"state":"conflict","blocked":True,
                                "error":"DataStore entry changed since preview"}
                    response = result["entry"]
                else:
                    response = api_key_request(plan["endpoint"],json.dumps(plan["body"]).encode("utf-8"),"application/json","PATCH")
            except Exception as error:
                self.wb.advanced.finish(identifier,"uncertain",{"error":str(error)})
                raise
            self.wb.advanced.finish(identifier,"committed",response)
            return {"plan_id":identifier,"state":"committed","entry":response}

    def luau(self, studio, universe, args):
        place = numeric(args["place_id"])
        root = f"universes/{universe}/places/{place}"
        action = args["action"]
        if action == "verified_versions":
            with self.wb.store.connect() as db:
                rows = db.execute("""SELECT version,task,verified FROM release_checks WHERE universe=? AND place=?
                    ORDER BY length(version) DESC,version DESC LIMIT 10""",(str(universe),place)).fetchall()
            return {"versions":[dict(row) for row in rows]}
        if action in ("start","verify"):
            self.wb.team.authorize_studio_action(studio)
            version = "/versions/" + numeric(args["version"]) if "version" in args else ""
            source = args["source"]
            if not isinstance(source,str) or not 1 <= len(source.encode("utf-8")) <= 200000:
                raise ValueError("Expected Luau source up to 200 KB")
            if action == "verify":
                if not version:
                    raise ValueError("Specify the exact published version to verify")
                expected = int(args["version"])
                if expected >= 2**53:
                    raise ValueError("Version cannot be represented exactly by Luau")
                source = (f"assert(game.PlaceVersion == {expected}, 'Unexpected place version')\n"
                          f"local function verify()\n{source}\nend\n"
                          "assert(verify() == true, 'Release assertion must return true')\nreturn game.PlaceVersion")
            endpoint = f"/cloud/v2/{root}{version}/luau-execution-session-tasks"
            result = self.workflows.cloud(studio,"POST",endpoint,"universe.place.luau-execution-session:write",
                                          {"script":source,"timeout":"60s"})
            if action == "start":
                return result
            path = result.get("path","")
            if not re.fullmatch(rf"{root}{version}/luau-execution-sessions/[\w-]+/tasks/[\w-]+",path):
                raise ValueError("Roblox returned a task for an unexpected place version")
            deadline = time.monotonic()+70
            while result.get("state") not in ("COMPLETE","FAILED","CANCELLED") and time.monotonic() < deadline:
                time.sleep(0.75)
                result = self.luau(studio,universe,{"action":"status","place_id":place,"task_path":path})
            passed = result.get("state") == "COMPLETE" and result.get("output",{}).get("results") == [expected]
            with self.wb.store.connect() as db:
                previous = db.execute("""SELECT version FROM release_checks WHERE universe=? AND place=?
                    ORDER BY length(version) DESC,version DESC LIMIT 1""",(str(universe),place)).fetchone()
                if passed:
                    db.execute("INSERT OR REPLACE INTO release_checks VALUES(?,?,?,?,?)",
                               (str(universe),place,str(expected),path,time.time()))
            return {"version":expected,"passed":passed,"blocked":not passed,"task_path":path,
                    "state":result.get("state"),"previous_verified_version":int(previous[0]) if previous else None}
        path = args["task_path"]
        task = r"[A-Za-z0-9_-]{1,80}"
        if not re.fullmatch(rf"{root}/versions/[1-9][0-9]*/luau-execution-sessions/{task}/tasks/{task}",path):
            raise ValueError("Task path must refer to the selected place and universe")
        if action not in ("status","logs"):
            raise ValueError("Unknown Luau task action")
        endpoint = "/cloud/v2/" + path + ("/logs" + query(args) if action == "logs" else "")
        return self.workflows.cloud(studio,"GET",endpoint,"universe.place.luau-execution-session:read",readonly=True)

    def place_file(self, relative):
        candidate = Path(relative)
        path = (self.wb.workspace / candidate).resolve()
        if candidate.is_absolute() or not path.is_relative_to(self.wb.workspace) or path.suffix.lower() not in (".rbxl",".rbxlx"):
            raise ValueError("Use a workspace-relative .rbxl or .rbxlx file")
        with path.open("rb") as stream:
            data = stream.read(100_000_001)
        if not 0 < len(data) <= 100_000_000:
            raise ValueError("Place file is empty or exceeds the 100 MB transfer budget")
        return data, "application/xml" if path.suffix.lower() == ".rbxlx" else "application/octet-stream"

    def publish(self, studio, universe, args):
        place, action = numeric(args["place_id"]), args["action"]
        if action in ("restore_latest","restore_status"):
            return self.restore_release(studio,universe,place,args)
        if action == "prepare":
            relative = args["file"]
            data, _ = self.place_file(relative)
            plan = {"place_id":place,"universe":universe,"file":relative,"sha256":hashlib.sha256(data).hexdigest(),
                    "bytes":len(data)}
            return {"plan_id":self.wb.advanced.begin(studio,None,plan,kind="place_publish"),"state":"prepared",**plan}
        identifier = args["plan_id"]
        with self.wb.store.lease(f"cloud:publish:{universe}:{place}",self.wb.owner):
            row = self.wb.advanced.operation(identifier)
            plan = row["after_json"]
            if row["kind"] != "place_publish" or row["owner"] != self.wb.owner or (
                plan["universe"],plan["place_id"]
            ) != (universe,place):
                raise ValueError("Plan belongs to another agent or place")
            if action == "status":
                return {"plan_id":identifier,"state":row["state"],"result":row["result"]}
            if action != "apply" or row["state"] != "prepared":
                raise ValueError("The publish plan is not ready to apply")
            self.wb.team.authorize_studio_action(studio)
            data, content_type = self.place_file(plan["file"])
            if hashlib.sha256(data).hexdigest() != plan["sha256"]:
                raise ValueError("Place file changed since preview")
            self.wb.advanced.finish(identifier,"applying",{})
            try:
                result = api_key_request(f"/universes/v1/{universe}/places/{place}/versions?versionType=Published",
                                         data,content_type)
                if "versionNumber" not in result:
                    raise ValueError("Roblox did not return a published version number")
            except Exception as error:
                self.wb.advanced.finish(identifier,"uncertain",{"error":str(error)})
                raise
            self.wb.advanced.finish(identifier,"committed",result)
            return {"plan_id":identifier,"state":"committed",**result}

    def restore_release(self, studio, universe, place, args):
        publisher = self.wb.studio_publish
        with self.wb.store.lease(f"cloud:publish:{universe}:{place}",self.wb.owner):
            if args["action"] == "restore_status":
                row = self.wb.advanced.operation(args["plan_id"])
                plan = row["after_json"]
                if row["kind"] != "place_restore" or (plan.get("universe_id"),plan.get("place_id")) != (universe,place):
                    raise ValueError("Restore receipt belongs to another place")
                return self.confirm_restore(studio, row["id"], row["state"], row["result"])
            if publisher.target(studio,place,require_title=False)[1] != str(universe):
                raise ValueError("The destination universe changed")
            self.wb.team.authorize_studio_action(studio)
            with self.wb.store.connect() as db:
                checkpoint = db.execute("""SELECT version FROM release_checks WHERE universe=? AND place=?
                    ORDER BY length(version) DESC,version DESC LIMIT 1""",(str(universe),place)).fetchone()
                pending = db.execute("""SELECT id FROM operations WHERE kind='place_restore'
                    AND state IN ('applying','uncertain','awaiting_verification')
                    AND json_extract(after_json,'$.place_id')=? AND json_extract(after_json,'$.universe_id')=?
                    ORDER BY created DESC LIMIT 1""",(place,universe)).fetchone()
            if pending:
                raise ValueError("Inspect the earlier restore with restore_status and plan_id " + pending["id"])
            if not checkpoint:
                raise ValueError("Run extra_cloud_luau verify for this place before restoring")
            version = numeric(str(checkpoint["version"]))
            current = publisher.published_version(studio,universe,place)
            if current == int(version):
                return {"state":"already_published","restored_from":current,"published_version":current}
            transport = args.get("transport","studio")
            if transport not in ("studio","api_key") or "file" in args or "plan_id" in args:
                raise ValueError("Restore uses a verified revision and a supported transport")

            def asset_request(endpoint, body=None):
                if transport == "api_key":
                    return api_key_request(endpoint,None if body is None else json.dumps(body).encode(),
                                           None if body is None else "application/json")
                return self.workflows.cloud(studio,"GET" if body is None else "POST",endpoint,
                                            "asset:read" if body is None else ["asset:read","asset:write"],
                                            body,readonly=body is None)

            asset = asset_request(f"/assets/v1/assets/{place}")
            if asset.get("path") != f"assets/{place}" or asset.get("assetType") != "Place":
                raise ValueError("Roblox did not confirm the destination place asset")
            asset_revision = int(numeric(str(asset["revisionId"])))
            version_path = f"assets/{place}/versions/{version}"
            if asset_request("/assets/v1/"+version_path).get("path") != version_path:
                raise ValueError("Roblox did not confirm the validated asset revision")
            if publisher.published_version(studio,universe,place) != current:
                raise ValueError("The published version changed during restore preparation. Run restore_latest again.")
            plan = {"universe_id":universe,"place_id":place,"restored_from":int(version),
                    "previous_version":current,"previous_asset_revision":asset_revision,"transport":transport}
            identifier = self.wb.advanced.begin(studio,None,plan,kind="place_restore",state="applying")
            self.wb.advanced.finish(identifier,"applying",plan)
            try:
                result = asset_request(f"/assets/v1/assets/{place}/versions:rollback",{"assetVersion":version_path})
                match = re.fullmatch(rf"assets/{place}/versions/([1-9][0-9]*)",result.get("path",""))
                if match and int(match[1]) == asset_revision == current:
                    receipt = {**plan,"published_version":current}
                    self.wb.advanced.finish(identifier,"already_published",receipt)
                    return {"plan_id":identifier,"state":"already_published",**receipt}
                if not match or int(match[1]) <= asset_revision:
                    raise ValueError("Roblox did not return a new restore revision")
            except Exception as error:
                result = {**plan,"error":str(error)}
                rejected = re.search(r"(?:returned HTTP | -> )(400|401|403|404|405|409|415|422)\b",str(error))
                state = "failed" if rejected else "uncertain"
                self.wb.advanced.finish(identifier,state,result)
                return {"plan_id":identifier,"state":state,"blocked":True,**result}
            result = {**plan,"restored_revision":int(match[1])}
            self.wb.advanced.finish(identifier,"awaiting_verification",result)
            return self.confirm_restore(studio,identifier,"awaiting_verification",result)

    def confirm_restore(self, studio, identifier, state, result):
        if state == "awaiting_verification":
            try:
                observed = self.wb.studio_publish.published_version(studio,result["universe_id"],result["place_id"])
            except Exception as error:
                return {"plan_id":identifier,"state":state,"blocked":True,**result,"verification_error":str(error)}
            if observed == result["restored_revision"]:
                state = "published"
                result = {**result,"published_version":observed}
                self.wb.advanced.finish(identifier,state,result)
            else:
                result = {**result,"observed_published_version":observed}
        return {"plan_id":identifier,"state":state,"blocked":state not in ("published","already_published"),**result}

    def catalog(self, studio, universe, args):
        resource = args["resource"]
        action = args["action"]
        identifier = args.get("id")
        if resource == "subscription":
            if action != "get":
                raise ValueError("Subscriptions support lookup by product and subscription ID")
            endpoint = (f"/cloud/v2/universes/{universe}/subscription-products/" +
                        segment(args["subscription_product_id"]) + "/subscriptions/" + segment(args["id"]))
            return self.workflows.cloud(studio,"GET",endpoint,"universe.subscription-product.subscription:read",readonly=True)
        if resource == "developer_product":
            base = f"/developer-products/v2/universes/{universe}/developer-products"
            scope, suffix = "developer-product", "/creator"
            fields = {"name":str,"description":str,"price":int,"isForSale":bool,
                      "isRegionalPricingEnabled":bool,"isManagedPricingEnabled":bool}
        elif resource == "user_restriction":
            base = f"/cloud/v2/universes/{universe}/user-restrictions"
            scope, suffix = "universe.user-restriction", ""
            fields = {"gameJoinRestriction":dict}
        else:
            raise ValueError("Unknown cloud catalog resource")
        if identifier is not None:
            identifier = numeric(identifier)

        def endpoint(target):
            return base + ("/" + target if target else "")

        def get(target, parameters=""):
            return self.workflows.cloud(studio,"GET",endpoint(target)+suffix+parameters,scope+":read",readonly=True)

        if action == "list":
            return get(None, query(args))
        if action == "get":
            return get(numeric(identifier))
        if action == "prepare":
            body = args["body"]
            if not isinstance(body,dict) or not body or any(type(value) is not fields.get(key) for key,value in body.items()):
                raise ValueError("Invalid request body")
            if len(json.dumps(body,allow_nan=False)) > 16000 or body.get("price",0) < 0:
                raise ValueError("Invalid request size or price")
            if resource == "user_restriction":
                numeric(identifier)
            elif not identifier and not body.get("name","").strip():
                raise ValueError("A new developer product needs a name")
            before = get(identifier) if identifier else None
            plan = {"resource":resource,"universe":universe,"id":identifier,"body":body}
            if resource == "developer_product":
                icon = args.get("icon_path")
                if not icon:
                    raise ValueError("Studio needs an icon in an approved upload folder to send the multipart request")
                plan.update(icon_path=icon,icon_hash=icon_digest(icon))
            return {"plan_id":self.wb.advanced.begin(studio,before,plan,kind="cloud_catalog"),"before":before,**plan}
        if action != "apply":
            raise ValueError("Unknown cloud catalog action")
        self.wb.team.authorize_studio_action(studio)
        plan_id = args["plan_id"]
        with self.wb.store.lease(f"cloud:{resource}:{universe}",self.wb.owner):
            row = self.wb.advanced.operation(plan_id)
            plan = row["after_json"]
            if row["kind"] != "cloud_catalog" or row["owner"] != self.wb.owner or row["state"] != "prepared" or (
                plan["universe"],plan["resource"]
            ) != (universe,resource):
                raise ValueError("Plan does not belong to this resource, agent or experience")
            target = plan["id"]
            if target and get(target) != row["before_json"]:
                raise ValueError("Cloud resource changed since preview")
            files = None
            if resource == "developer_product":
                icon = plan.get("icon_path")
                if not icon or icon_digest(icon) != plan.get("icon_hash"):
                    raise ValueError("Product icon is missing or changed; prepare a new plan")
                files = [{"name":"imageFile","path":icon}]
            self.wb.advanced.finish(plan_id,"applying",{})
            try:
                response = self.workflows.cloud(studio,"PATCH" if target else "POST",endpoint(target),
                                                scope+":write",plan["body"],files=files)
            except Exception as error:
                self.wb.advanced.finish(plan_id,"uncertain",{"error":str(error)})
                raise
            self.wb.advanced.finish(plan_id,"committed",response)
        observed = target or response.get("productId") or response.get("id")
        verification = {}
        if observed:
            try:
                verification["observed"] = get(numeric(str(observed)))
            except ValueError as error:
                verification["verification_error"] = str(error)
        return {"plan_id":plan_id,"state":"committed","response":response,**verification}
