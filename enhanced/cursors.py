"""Publish viewport activity in the background using the existing Studio bridge."""
import json
from pathlib import Path
import time

DIRECTORY = Path(__file__).parent
CODE = (DIRECTORY / "cursors.luau").read_text(encoding="utf-8").replace(
    "-- CURSOR_ART", (DIRECTORY / "cursor_art.luau").read_text(encoding="utf-8"))
UPDATE = """
if not game:GetService("RunService"):IsEdit() then return false end
local state = shared.__FoundryViewportCursors
if state and state.version == 31 and state.update then
    state.update(payload)
    return true
end
return #payload.rows == 0 and state == nil
"""
LABELS = {
    "inspect_instance":"Inspecting", "search_game_tree":"Exploring", "extra_snapshot":"Inspecting",
    "script_read":"Reading script", "extra_script_get":"Reading script", "script_search":"Finding scripts",
    "script_grep":"Searching source", "multi_edit":"Editing script", "extra_script_put":"Editing script",
    "extra_impact":"Checking references", "extra_mixed_changes":"Working on edit plan",
    "extra_batch_create":"Creating objects", "extra_batch_properties":"Editing properties",
    "extra_instance_actions":"Editing scene", "extra_scenario":"Running scenario",
    "extra_playtest":"Running playtest", "get_console_output":"Reading console",
    "execute_luau":"Running Luau", "start_stop_play":"Switching play mode",
}


def cursor_path(path):
    if isinstance(path,str):
        path = path.removeprefix("game.").split(".")
    if isinstance(path,list) and 0 < len(path) <= 64 and all(
        isinstance(part,str) and 0 < len(part) <= 200 for part in path
    ):
        return path
    return []


def cursor_activity(name, args):
    if not args.get("studio_id") or name in ("get_studio_state","extra_studio_control"):
        return None
    if name == "extra_agent":
        if args.get("action") == "status" and isinstance(args.get("status"),str):
            return {"path":cursor_path(args.get("path")),"stage":" ".join(args["status"].split())[:120]}
        return None
    if name == "extra_ribbon":
        requested = args.get("payload", {}).get("tool") if isinstance(args.get("payload"),dict) else None
        return {"path":[],"stage":f"Studio ribbon · {requested}" if requested else "Reading Studio ribbon"}
    payload = args.get("payload")
    payload = payload if isinstance(payload,dict) else {}
    items = args.get("items") or payload.get("items")
    first = items[0] if isinstance(items,list) and items and isinstance(items[0],dict) else {}
    path = (args.get("path") or args.get("target_file") or args.get("file_path")
            or args.get("instance_path") or payload.get("path") or first.get("path") or first.get("parent"))
    path = cursor_path(path)
    stage = LABELS.get(name,name.removeprefix("extra_").replace("_"," ").capitalize())
    action = args.get("action")
    if name == "extra_mixed_changes":
        stage = {"prepare":"Preparing edit plan","apply":"Applying edit plan","rollback":"Reverting edit plan",
                 "validate":"Validating local copy"}.get(action,stage) if isinstance(action,str) else stage
    if name == "extra_scenario" and action in ("record_start","record_stop"):
        stage = "Recording reproduction" if action == "record_start" else "Saving reproduction"
    result = {"path":path,"stage":stage}
    line = args.get("start_line_one_indexed",args.get("start_line",args.get("line",payload.get("line"))))
    if type(line) is int and 0 < line <= 1_000_000:
        result["line"] = line
    if isinstance(items,list) and name in ("extra_batch_create","extra_batch_properties","extra_instance_actions"):
        targets = []
        for item in items[:8]:
            if not isinstance(item,dict):
                continue
            target = cursor_path(item.get("path"))
            effect = "edit"
            if name == "extra_batch_create":
                parent = cursor_path(item.get("parent"))
                leaf = item.get("name",item.get("class"))
                if parent and isinstance(leaf,str):
                    target = cursor_path(parent+[leaf])
                    effect = "create"
            elif item.get("action") == "move":
                parent = cursor_path(item.get("parent"))
                if parent and target:
                    target = cursor_path(parent+target[-1:])
                    effect = "move"
            elif item.get("action") == "clone":
                parent = cursor_path(item.get("parent",target[:-1]))
                leaf = item.get("name",target[-1] if target else None)
                target = cursor_path(parent+[leaf]) if parent and isinstance(leaf,str) else []
                effect = "create"
            if target:
                targets.append({"path":target,"effect":effect})
        if targets and (name != "extra_instance_actions" or payload.get("preview") is False):
            result["outlines"] = targets
    return result


class ViewportCursors:
    def __init__(self, workbench):
        self.wb = workbench
        self.studios = {}
        self.seen_studios = set()
        self.initialized_studios = set()
        self.hidden = False

    def publish(self, active, history, agents, enabled, *, hidden=False):
        now = time.time()
        names = {agent["id"]:agent["name"] for agent in agents if agent["active"]}
        newest = {}
        if enabled and not hidden:
            for row in sorted([*history,*active],key=lambda row:row["started"]):
                if (row.get("cursor") or row.get("ribbon_cursor")) and row["actor"] in names and (
                    row["tool"] != "extra_agent" or row.get("success")
                ):
                    newest[row["studio"],row["actor"]] = row
        targets = {}
        for (studio,actor), row in newest.items():
            if not row.get("cursor"):
                continue
            if now - row.get("finished",now) > (35 if row["cursor"].get("report") else 5):
                continue
            targets.setdefault(studio,[]).append({
                "id":actor,"name":names[actor][:70],"provider":names.get(self.wb.connection_id,""),
                **row["cursor"],"state":("done" if row["success"] else "failed")
                if "finished" in row and row["tool"] != "extra_agent" else "active",
            })
        self.seen_studios.update(targets)
        if hidden != self.hidden:
            self.hidden = hidden
            for studio in self.seen_studios:
                self.studios[studio] = (None,0)
        for studio in set(targets) | set(self.studios):
            rows = sorted(targets.get(studio,[]),key=lambda row:row["id"])[:16]
            previous, last = self.studios.get(studio,(None,0))
            stamp = time.monotonic()
            if stamp < last or (rows == previous and stamp-last < 2):
                continue
            payload = json.dumps({"connection":self.wb.connection_id,"rows":rows,"hidden":hidden},ensure_ascii=False)
            prefix = "local payload=game:GetService('HttpService'):JSONDecode(" + json.dumps(payload,ensure_ascii=False) + ")\n"
            try:
                body = UPDATE if studio in self.initialized_studios or not rows else CODE
                reply = self.wb.rpc("tools/call",{"name":"execute_luau","arguments":{
                    "studio_id":studio,"datamodel_type":"Edit","code":prefix+body}},timeout=0.8)
                blocks = reply.get("content", [])
                if body == UPDATE and not reply.get("isError") and blocks and blocks[0].get("text") == "false":
                    reply = self.wb.rpc("tools/call",{"name":"execute_luau","arguments":{
                        "studio_id":studio,"datamodel_type":"Edit","code":prefix+CODE}},timeout=0.8)
                success = not reply.get("isError") and reply.get("content", [{}])[0].get("text") != "false"
            except Exception:
                success = False
            if success and not rows:
                self.studios.pop(studio,None)
                self.initialized_studios.discard(studio)
            else:
                self.studios[studio] = (rows,stamp if success else stamp+1)
                if success:
                    self.initialized_studios.add(studio)
