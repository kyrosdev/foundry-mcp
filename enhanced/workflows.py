"""Saved playtests and bounded Roblox Open Cloud workflows."""
import base64
from contextlib import nullcontext
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re
import time
import uuid

from team import integer, text
from cloud_tools import CloudTools, lookup, schema as cloud_schema
from repro import Reproductions


def schema(tool):
    string = {"type":"string"}
    obj = {"type":"object"}
    return [
        tool("scenario", "Record, save, run and compare versioned Studio reproductions.", {
            "action":{"enum":["save","list","get","run","result","compare","record_start","record_stop","record_get"]}, "studio_id":string,
            "name":string, "version":{"type":"integer"}, "steps":{"type":"array","items":obj,"maxItems":30},
            "assert_code":{**string,"description":"On save, use instead of steps for one server assertion in a fresh Studio Play session with one player."},
            "assert_model":{"enum":["Client","Server"],"description":"DataModel for the optional record_stop assertion."},
            "mode":{"enum":["play","run"],"description":"With assert_code on save, choose Play with one player (default) or Run without a player."},
            "run_id":string, "baseline_id":string, "recording_id":string, "detail":{"enum":["summary","full"]},
            "region":{"type":"array","items":{"type":"integer"},"minItems":4,"maxItems":4,
                      "description":"Optional screenshot comparison crop [x,y,width,height] in pixels. Requires Pillow."},
        }, ("action","studio_id")),
        tool("analytics", "Read and compare daily analytics windows using Studio Open Cloud. Missing days stay missing. Optional breakdown and filter use Roblox metric names.", {
            "studio_id":string, "metric":string, "start":string, "end":string,
            "baseline_start":string,"baseline_end":string, "breakdown":{"type":"array","items":string},
            "filter":{"type":"array","items":obj},
            "dimensions":{"type":"array","items":string,"description":"Discover dimension values, for example funnel step IDs, instead of querying metrics."},
        }, ("studio_id","metric","start","end"), True),
        tool("game_pass", "List or read game passes, prepare an exact Open Cloud request, then apply its plan ID and verify by reading the returned pass ID when available.", {
            "studio_id":string,"action":{"enum":["list","get","prepare","apply"]},"pass_id":string,
            "plan_id":string,"body":obj,"page_token":string,
            "icon_path":{**string,"description":"Absolute path to a PNG, JPG or BMP in a Studio-approved upload folder. Required for apply because the Studio tool uses multipart only with a file."},
        }, ("studio_id","action")),
    ] + cloud_schema(tool)


class Workflows:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS scenarios(
        project TEXT, name TEXT, version INTEGER, steps TEXT,
        PRIMARY KEY(project,name,version));
    CREATE TABLE IF NOT EXISTS scenario_runs(
        id TEXT PRIMARY KEY, project TEXT, name TEXT, version INTEGER,
        report TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS scenario_images(
        run TEXT, position INTEGER, mime TEXT, pixels BLOB,
        PRIMARY KEY(run,position));
    CREATE TABLE IF NOT EXISTS reproductions(
        id TEXT PRIMARY KEY, studio TEXT, project TEXT, owner TEXT,
        state TEXT, report TEXT, created REAL);
    """

    def __init__(self, wb):
        self.wb = wb
        self.cloud_tools = CloudTools(self)
        self.reproductions = Reproductions(self)

    def call(self, name, args):
        if name == "scenario":
            return self.scenario(args)
        if name == "roblox_lookup":
            return lookup(args)
        if name == "place_publish" and args["action"] == "open":
            if "file" in args or "plan_id" in args:
                raise ValueError("Publishing the open place takes studio_id and place_id, without a file or plan_id")
            return self.wb.studio_publish.call(args)
        if name == "place_publish" and args.get("transport") == "studio" and args["action"] in ("prepare","apply","status"):
            return self.wb.studio_publish.file_dialog(args)
        studio, universe = self.published(args["studio_id"])
        if name == "analytics":
            return self.analytics(studio, universe, args)
        if name == "cloud_catalog":
            return self.cloud_tools.catalog(studio, universe, args)
        if name == "datastore":
            return self.cloud_tools.datastore(studio, universe, args)
        if name == "cloud_luau":
            return self.cloud_tools.luau(studio, universe, args)
        if name == "place_publish":
            return self.cloud_tools.publish(studio, universe, args)
        if name == "asset_upload":
            return self.cloud_tools.asset_upload(studio, universe, args)
        return self.game_pass(studio, universe, args)

    def save_scenario(self, project, name, value, recording=None):
        encoded = json.dumps(value, allow_nan=False)
        if len(encoded.encode("utf-8")) > 200000:
            raise ValueError("Scenario exceeds 200 KB")
        with self.wb.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM scenarios WHERE project=? AND name=?", (project,name)).fetchone()[0]
            db.execute("INSERT INTO scenarios VALUES(?,?,?,?)", (project,name,version,encoded))
            if recording:
                identifier, report = recording
                db.execute("UPDATE reproductions SET state='saved',report=? WHERE id=?",
                           (json.dumps({**report,"version":version}),identifier))
        return version

    def scenario(self, args, studio_locked=False, scenario_project=None):
        studio = args["studio_id"]
        if self.wb.auto_bind_projects:
            self.wb.advanced.auto_bind(studio)
        project = scenario_project or self.wb.store.project_key(studio)
        action = args["action"]
        if action.startswith("record_"):
            return self.reproductions.call(args, project)

        def report(identifier):
            with self.wb.store.connect() as db:
                row = db.execute("SELECT * FROM scenario_runs WHERE project=? AND id=?", (project, identifier)).fetchone()
            if row is None:
                raise ValueError("Unknown run for this project")
            return {**dict(row), "report":json.loads(row["report"])}

        if action == "list":
            with self.wb.store.connect() as db:
                rows = db.execute("SELECT name,MAX(version) version FROM scenarios WHERE project=? GROUP BY name ORDER BY name LIMIT 100", (project,))
                return {"scenarios":[dict(row) for row in rows]}
        if action == "result":
            result = report(args["run_id"])
            with self.wb.store.connect() as db:
                images = db.execute("SELECT mime,pixels FROM scenario_images WHERE run=? ORDER BY position", (result["id"],)).fetchall()
            result["result"] = {"content":[{"type":"image","mimeType":image["mime"],
                                           "data":base64.b64encode(image["pixels"]).decode("ascii")} for image in images]}
            return result
        if action == "compare":
            old, new = report(args["baseline_id"]), report(args["run_id"])
            if (old["name"], old["version"]) != (new["name"], new["version"]):
                raise ValueError("Compare runs of the same scenario version")
            before, after = old["report"], new["report"]
            result = {"baseline":old["id"], "candidate":new["id"], "before_passed":before["passed"],
                    "after_passed":after["passed"], "duration_delta_ms":after["duration_ms"]-before["duration_ms"],
                    "screenshots_changed":before["screenshots"] != after["screenshots"],
                    "baseline_failures":before["failures"],"candidate_failures":after["failures"],
                    "baseline_logs":before["logs"], "candidate_logs":after["logs"],
                    "baseline_steps":before.get("steps",[]), "candidate_steps":after.get("steps",[])}
            for left, right in zip(before.get("steps",[]),after.get("steps",[])):
                if (left.get("tool"),left.get("passed")) != (right.get("tool"),right.get("passed")):
                    result["first_changed_step"] = {"step":left["step"],"tool":left["tool"],
                                                    "before_passed":left["passed"],"after_passed":right["passed"]}
                    break
            if "region" in args:
                result["visual_comparison"] = self.compare_region(old["id"],new["id"],args["region"])
            performance = []
            previous = {(item["step"],item["datamodel_type"]):item for item in before.get("performance",[])}
            for right in after.get("performance",[]):
                left = previous.get((right["step"],right["datamodel_type"]))
                if left and all(key in left and key in right for key in ("p50_ms","p95_ms","memory_mb")):
                    performance.append({"step":right["step"],"datamodel_type":right["datamodel_type"],
                                        **{f"{key}_delta":round(right[key]-left[key],3)
                                           for key in ("p50_ms","p95_ms","memory_mb")}})
            if performance:
                result["performance"] = performance
            return result
        name = text(args["name"], "name", 128)
        if action == "save":
            assertion = args.get("assert_code")
            mode = args.get("mode", "play")
            if mode not in ("play", "run") or ("mode" in args and "assert_code" not in args):
                raise ValueError("mode applies only to a saved server assertion")
            if "assert_code" in args:
                if "steps" in args or not isinstance(assertion, str) or not 0 < len(assertion.encode("utf-8")) <= 50000:
                    raise ValueError("Supply either steps or one server assertion of up to 50 KB")
                steps = [{"assert_code":assertion}]
            else:
                steps = args["steps"]
            allowed = {"start_stop_play","execute_luau","character_navigation","user_keyboard_input",
                       "user_mouse_input","screen_capture","get_console_output","get_studio_state"}
            if not isinstance(steps,list) or not 1 <= len(steps) <= 30:
                raise ValueError("Expected 1..30 scenario steps")
            for step in steps:
                if not isinstance(step,dict) or step.get("tool","execute_luau") not in allowed:
                    raise ValueError("Invalid scenario step")
                if not isinstance(step.get("arguments",{}),dict):
                    raise ValueError("Step arguments must be an object")
                if "assert_code" in step and (not isinstance(step["assert_code"],str) or len(step["assert_code"]) > 100000):
                    raise ValueError("Invalid scenario assertion")
                if "wait_seconds" in step and ("assert_code" not in step or
                   type(step["wait_seconds"]) not in (int,float) or not 0 <= step["wait_seconds"] <= 10):
                    raise ValueError("wait_seconds requires assert_code and a duration between 0 and 10")
                if "profile_seconds" in step and ("assert_code" in step or
                   step.get("tool","execute_luau") != "execute_luau" or
                   type(step["profile_seconds"]) not in (int,float) or not 0.25 <= step["profile_seconds"] <= 10):
                    raise ValueError("Use a standalone profile_seconds step between 0.25 and 10 seconds")
                if "remote_probe" in step and ("assert_code" in step or "profile_seconds" in step
                   or not isinstance(step["remote_probe"],dict) or step.get("tool","execute_luau") != "execute_luau"):
                    raise ValueError("Use a standalone remote_probe step")
            version = self.save_scenario(project,name,{"assert_code":assertion,"mode":mode} if assertion is not None else steps)
            return {"version":version} if assertion is not None else {"name":name,"version":version,"steps":len(steps)}
        if action not in ("get","run"):
            raise ValueError("Invalid scenario action")
        version = args.get("version")
        with self.wb.store.connect() as db:
            row = db.execute("SELECT version,steps FROM scenarios WHERE project=? AND name=? AND (? IS NULL OR version=?) ORDER BY version DESC LIMIT 1",
                             (project,name,version,version)).fetchone()
        if row is None:
            raise ValueError("Scenario version not found")
        saved = json.loads(row["steps"])
        single_play = isinstance(saved, dict)
        if action == "get":
            return {"name":name,"version":row["version"],**(saved if single_play else {"steps":saved})}
        started = time.monotonic()
        if single_play:
            self.wb.team.authorize_studio_action(studio)
            server_only = saved.get("mode") == "run"
            with (nullcontext() if studio_locked else self.wb.store.lease("studio:"+studio,self.wb.owner)):
                outcome = self.wb.testing.server_assertion(studio,saved["assert_code"],0 if server_only else 1,15,
                                                           single_play=True,server_only=server_only)
            step = {"step":1,"tool":"execute_luau" if outcome.get("asserted") else "start_stop_play",
                    "passed":outcome["passed"],"duration_ms":round((time.monotonic()-started)*1000)}
            if "error" in outcome:
                step["error"] = outcome["error"]
            body = {"passed":outcome["passed"],"steps":[step]}
            result = {"content":[]}
        else:
            result = self.wb.playtest({"studio_id":studio,"steps":saved,"cleanup":True},
                                      **({"studio_locked":True} if studio_locked else {}))
            body = json.loads(result["content"][0]["text"])
        images = []
        total_image_bytes = 0
        omitted_images = 0
        for block in result["content"]:
            if block["type"] != "image":
                continue
            pixels = base64.b64decode(block["data"],validate=True)
            if total_image_bytes + len(pixels) > 8_000_000:
                omitted_images += 1
                continue
            images.append((block,pixels))
            total_image_bytes += len(pixels)
        identifier = uuid.uuid4().hex
        summary = {"passed":body["passed"],"duration_ms":round((time.monotonic()-started)*1000),
                   "failures":[step for step in body["steps"] if step.get("passed") is False or "error" in step or
                               "cleanup_error" in step or step.get("cleanup",{}).get("isError")],
                   "logs":[str(step.get("content",[]))[:3000] for step in body["steps"] if step.get("tool") == "get_console_output"],
                   "steps":[{key:step[key] for key in ("step","tool","passed","duration_ms") if key in step}
                            for step in body["steps"] if "step" in step],
                   "observations":[{"step":step["step"],"output":str(step.get("content",[]))[:1000]}
                                   for step in body["steps"] if step.get("tool") == "execute_luau" and step.get("passed")],
                   "performance":[{"step":step["step"],**step["performance"]} for step in body["steps"]
                                  if "performance" in step],
                   "omitted_images":omitted_images,
                   "screenshots":[hashlib.sha256(pixels).hexdigest() for _,pixels in images]}
        with self.wb.store.connect() as db:
            db.execute("INSERT INTO scenario_runs VALUES(?,?,?,?,?,?)",
                       (identifier,project,name,row["version"],json.dumps(summary),time.time()))
            db.executemany("INSERT INTO scenario_images VALUES(?,?,?,?)",
                           [(identifier,index,block["mimeType"],pixels) for index,(block,pixels) in enumerate(images)])
        failures = summary["failures"]
        if args.get("detail") != "full":
            compact = []
            for step in failures:
                failure = {key:step[key] for key in ("step","tool","error","cleanup_error") if key in step}
                for block in step.get("content", []):
                    if block.get("type") == "text" and block.get("text"):
                        message = block["text"].splitlines()[-1]
                        failure.setdefault("error", message.rsplit("AssistantCommand:",1)[-1][:250])
                        break
                compact.append(failure)
            failures = compact
            if single_play:
                return {"run_id":identifier,"passed":summary["passed"],
                        **({"blocked":True,"failures":failures} if failures else {})}
        return {"run_id":identifier,"name":name,"version":row["version"],"blocked":not summary["passed"],
                "passed":summary["passed"],"duration_ms":summary["duration_ms"],
                "omitted_images":omitted_images,"result":{"content":[block for block,_ in images]},
                "failures":failures}

    def compare_region(self, old, new, region):
        try:
            from PIL import Image, ImageChops
        except ImportError as error:
            raise ValueError("Install Pillow in the MCP server's Python to compare image regions") from error
        if not isinstance(region,list) or len(region)!=4 or any(type(v) is not int or v<0 for v in region):
            raise ValueError("Expected a pixel region [x,y,width,height]")
        x,y,width,height = region
        if not 0 < width*height <= 4_000_000:
            raise ValueError("Use a nonempty region of up to four million pixels")
        with self.wb.store.connect() as db:
            images = [{row["position"]:row["pixels"] for row in db.execute(
                       "SELECT position,pixels FROM scenario_images WHERE run=?", (run,))}
                      for run in (old,new)]
        if not any(images):
            raise ValueError("Neither run contains stored screenshots")
        results = []
        for position in sorted(images[0].keys() | images[1].keys()):
            if not all(position in captures for captures in images):
                results.append({"position":position,"comparable":False,"reason":"Screenshot missing"})
                continue
            with Image.open(io.BytesIO(images[0][position])) as first, Image.open(io.BytesIO(images[1][position])) as second:
                if first.size != second.size or x+width>first.width or y+height>first.height:
                    results.append({"position":position,"comparable":False,"reason":"Different resolution or crop outside image"})
                    continue
                bounds = (x,y,x+width,y+height)
                difference = ImageChops.difference(first.crop(bounds).convert("RGB"),second.crop(bounds).convert("RGB"))
                r,g,b = difference.split()
                mask = ImageChops.lighter(ImageChops.lighter(r,g),b)
                unchanged = mask.histogram()[0]
                results.append({"position":position,"comparable":True,"changed_pixels":width*height-unchanged,
                                "changed_fraction":(width*height-unchanged)/(width*height),"bounds_in_crop":mask.getbbox()})
        return results

    def published(self, studio):
        reply = self.wb.read_rpc("tools/call", {"name":"list_roblox_studios","arguments":{}})
        if reply.get("isError"):
            raise ValueError("Could not list Studio windows")
        data = json.loads(reply["content"][0]["text"])
        row = next((row for row in data["studios"] if row["id"] == studio), {})
        if not row.get("persistedToCloud") or not row.get("universeId"):
            raise ValueError("Select an open published experience for Open Cloud")
        return studio, integer(row["universeId"], 1, 2**63-1, "universe ID")

    def cloud(self, studio, method, endpoint, scope, body=None, readonly=False, files=None):
        args = {"studio_id":studio,"method":method,"endpoint":endpoint,"scopes":[scope] if isinstance(scope,str) else scope}
        if body is not None:
            args["body"] = json.dumps(body, allow_nan=False)
        if files:
            args["files"] = files
        rpc = self.wb.read_rpc if readonly else self.wb.rpc
        result = rpc("tools/call", {"name":"open_cloud_invoke","arguments":args})
        raw = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
        header, _, payload = raw.partition("\n\n")
        status = re.search(r" -> (\d{3})\b", header)
        if result.get("isError") or not status or not 200 <= int(status[1]) < 300:
            raise ValueError(raw[:2500] or "Open Cloud request failed")
        return json.loads(payload) if payload.strip() else {}

    def analytics(self, studio, universe, args):
        scope = "universe.analytics:read"
        metric = text(args["metric"], "metric", 128)
        base = f"/analytics-query-api/v1/universes/{universe}"

        def window(start, end):
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if not 1 <= (last-first).days <= 90:
                raise ValueError("Use a window of 1..90 days with exclusive end")
            discovery = "dimensions" in args
            kind = "dimension-values" if discovery else "metrics"
            body = {"metric":metric,"startTime":str(first)+"T00:00:00Z","endTime":str(last)+"T00:00:00Z"}
            if not discovery:
                body.update(granularity="OneDay",limit=10)
            for key in (("dimensions",) if discovery else ("breakdown","filter")):
                if key in args:
                    body[key] = args[key]
            result = self.cloud(studio,"POST",base+"/"+kind,scope,body,readonly=True)
            for _ in range(5):
                if result.get("done"):
                    break
                path = result.get("path","")
                pattern = rf"v1/universes/{universe}/operations/{kind}/[A-Za-z0-9_-]+"
                if not re.fullmatch(pattern,path):
                    raise ValueError("Unexpected analytics operation path")
                time.sleep(0.4)
                result = self.cloud(studio,"GET","/analytics-query-api/"+path,scope,readonly=True)
            if "error" in result:
                raise ValueError("Analytics operation failed: " + json.dumps(result["error"])[:1000])
            if not result.get("done"):
                return {"start":start,"end":end,"operation":result}
            if discovery:
                return {"start":start,"end":end,"dimensions":result["response"]}
            series = []
            for row in result["response"].get("values", []):
                points = row.get("dataPoints", [])
                values = [point["value"] for point in points if isinstance(point.get("value"),(int,float))]
                series.append({"breakdowns":row.get("breakdowns",[]),"points":points,
                               "observed_days":len(values),"mean_daily_value":sum(values)/len(values) if values else None})
            return {"start":start,"end":end,"series":series}

        if ("baseline_start" in args) != ("baseline_end" in args):
            raise ValueError("Provide both baseline dates")
        current = window(args["start"],args["end"])
        baseline = window(args["baseline_start"],args["baseline_end"]) if "baseline_start" in args else None
        changes = []
        if baseline:
            for current_series in current.get("series", []):
                for prior in baseline.get("series", []):
                    if current_series["breakdowns"] == prior["breakdowns"] and None not in (current_series["mean_daily_value"], prior["mean_daily_value"]):
                        changes.append({"breakdowns":prior["breakdowns"],"daily_mean_delta":current_series["mean_daily_value"]-prior["mean_daily_value"]})
        return {"universe_id":universe,"metric":metric,"current":current,"baseline":baseline,"comparison":changes,
                "note":"Daily averages use returned days only. Retention averages are unweighted by cohort size."}

    def game_pass(self, studio, universe, args):
        from urllib.parse import urlencode

        base = f"/game-passes/v1/universes/{universe}/game-passes"
        action = args["action"]
        pass_id = args.get("pass_id")
        if pass_id is not None and not re.fullmatch(r"[1-9][0-9]{0,19}",pass_id):
            raise ValueError("Invalid game pass ID")
        if action == "list":
            query = "?"+urlencode({"pageToken":args["page_token"]}) if args.get("page_token") else ""
            return self.cloud(studio,"GET",base+"/creator"+query,"game-pass:read",readonly=True)
        if action == "get":
            if not pass_id:
                raise ValueError("pass_id required")
            return self.cloud(studio,"GET",base+"/"+pass_id+"/creator","game-pass:read",readonly=True)
        if action == "prepare":
            body = args["body"]
            fields = {"name":str,"description":str,"isForSale":bool,"price":int,
                      "isRegionalPricingEnabled":bool,"isManagedPricingEnabled":bool}
            if not isinstance(body,dict) or not body or any(type(value) is not fields.get(key) for key,value in body.items()):
                raise ValueError("Invalid game pass request fields")
            if len(json.dumps(body)) > 16000 or body.get("price",0) < 0:
                raise ValueError("Invalid game pass price or request size")
            if not pass_id and not body.get("name","").strip():
                raise ValueError("A new game pass needs a name")
            icon = args.get("icon_path")
            icon_hash = None
            if icon:
                path = Path(icon)
                if not path.is_absolute() or path.suffix.lower() not in (".png",".jpg",".jpeg",".bmp"):
                    raise ValueError("Use an absolute path to a PNG, JPG or BMP")
                if not path.is_file() or path.stat().st_size > 20_000_000:
                    raise ValueError("Icon is missing or exceeds the local upload budget")
                icon_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            current = self.game_pass(studio,universe,{"action":"get","pass_id":pass_id}) if pass_id else None
            plan = {"universe":universe,"pass_id":pass_id,"body":body,"icon_path":icon,"icon_sha256":icon_hash}
            identifier = self.wb.advanced.begin(studio,current,plan,kind="game_pass")
            return {"plan_id":identifier,"action":"update" if pass_id else "create","before":current,**plan}
        if action != "apply":
            raise ValueError("Invalid game pass action")
        self.wb.team.authorize_studio_action(studio)
        identifier = args["plan_id"]
        with self.wb.store.lease("cloud:passes:"+str(universe),self.wb.owner):
            row = self.wb.advanced.operation(identifier)
            plan = row["after_json"]
            if row["kind"] != "game_pass" or row["owner"] != self.wb.owner or row["state"] != "prepared" or plan["universe"] != universe:
                raise ValueError("Plan does not belong to this agent or experience, or was already dispatched")
            pass_id = plan["pass_id"]
            icon = plan.get("icon_path")
            if not icon:
                raise ValueError("Prepare again with icon_path in a Studio-approved upload folder to send multipart data")
            path = Path(icon)
            if not path.is_file() or path.stat().st_size > 20_000_000 or hashlib.sha256(path.read_bytes()).hexdigest() != plan["icon_sha256"]:
                raise ValueError("Icon changed since preview")
            if pass_id and self.game_pass(studio,universe,{"action":"get","pass_id":pass_id}) != row["before_json"]:
                raise ValueError("Game pass changed since preview")
            self.wb.advanced.finish(identifier,"applying",{})
            try:
                result = self.cloud(studio,"PATCH" if pass_id else "POST",base+("/"+pass_id if pass_id else ""),
                                    "game-pass:write",plan["body"],files=[{"name":"imageFile","path":icon}])
            except Exception as error:
                self.wb.advanced.finish(identifier,"uncertain",{"error":str(error)})
                raise
            self.wb.advanced.finish(identifier,"committed",result)
        observed_id = pass_id or result.get("gamePassId") or result.get("id")
        verified, problem = None, None
        if observed_id:
            try:
                verified = self.game_pass(studio,universe,{"action":"get","pass_id":str(observed_id)})
            except Exception as error:
                problem = str(error)
        return {"plan_id":identifier,"state":"committed","response":result,"verified":verified,"verification_error":problem}
