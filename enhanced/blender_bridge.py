"""Blender tools exposed through Foundry's local bridge."""
import json
import urllib.error
import urllib.request

FIELDS = {
    "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    "rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    "scale": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    "color": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1},
              "minItems": 3, "maxItems": 3},
}


def schema(tool):
    session = {"session_id": {"type": "string"}}
    target = {**session, "name": {"type": "string"}}
    return [
        tool("blender_sessions", "List local Blender bridge sessions.", {}, readonly=True),
        tool("blender_scene", "Inspect the live Blender scene and its objects.",
             {**session, "offset": {"type": "integer", "minimum": 0},
              "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ("session_id",), True),
        tool("blender_object", "Inspect a Blender object and its materials.", target, ("session_id", "name"), True),
        tool("blender_create", "Create a mesh primitive in the live Blender scene.",
             {**target, "primitive": {"enum": ["cube", "sphere", "cylinder", "plane"]}, **FIELDS},
             ("session_id", "name", "primitive")),
        tool("blender_edit", "Set transforms or assign a new material to a mesh in the live Blender scene. Rotation is in radians.",
             {**target, **FIELDS}, ("session_id", "name")),
    ]


def descriptors(directory):
    for file in sorted(directory.glob("*.json")):
        try:
            entry = json.loads(file.read_text(encoding="utf-8"))
            if (entry["id"] == file.stem and type(entry["port"]) is int
                    and 0 < entry["port"] <= 65535 and isinstance(entry["token"], str)):
                yield entry
        except (OSError, ValueError, KeyError, TypeError):
            continue


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(entry, command, data, timeout):
    url = f'http://127.0.0.1:{entry["port"]}/rpc'
    body = json.dumps({"command": command, "data": data}, allow_nan=False).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + entry["token"],
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as reply:
            payload = reply.read(1_000_001)
            if len(payload) > 1_000_000:
                raise ConnectionError("Blender response exceeds the bridge limit")
            result = json.loads(payload)
    except (OSError, urllib.error.URLError) as error:
        raise ConnectionError("Blender bridge unavailable. Verify the add-on is enabled.") from error
    if "error" in result:
        raise ValueError(result["error"])
    return result["result"]


def call(wb, name, args):
    directory = wb.store.file.parent / "blender"
    if name == "blender_sessions":
        sessions = []
        for entry in list(descriptors(directory))[:16]:
            try:
                info = request(entry, "status", {}, 0.4)
            except (ConnectionError, ValueError):
                continue
            studio = "blender:" + entry["id"]
            with wb.store.connect() as db:
                mode = wb.team.coordination_mode(db, studio)
            sessions.append({"session_id": entry["id"], "studio_id": studio, "effective_mode": mode, **info})
        return {"sessions": sessions}
    entry = next((row for row in descriptors(directory) if row["id"] == args["session_id"]), None)
    if entry is None:
        raise ValueError("Choose a Blender session with extra_blender_sessions")
    data = {key: value for key, value in args.items() if key != "session_id"}
    action = name.removeprefix("blender_")
    if action in ("scene", "object"):
        return request(entry, action, data, 12)
    studio = "blender:" + entry["id"]
    path = ["Objects", args["name"]]
    with wb.store.lease("studio:" + studio, wb.owner):
        wb.assert_script_lock(studio, path)
        plan = wb.advanced.begin(studio, [], [], kind=name, state="applying")
        try:
            result = request(entry, action, data, 12)
        except ConnectionError as error:
            wb.advanced.finish(plan, "uncertain", {"error": str(error), "path": path})
            raise
        except Exception as error:
            wb.advanced.finish(plan, "failed", {"error": str(error), "path": path})
            raise
        wb.advanced.finish(plan, "committed", result)
    return {"operation_id": plan, **result}
