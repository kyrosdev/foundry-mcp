"""Local Foundry bridge for the currently open Blender scene."""
bl_info = {
    "name": "Foundry Blender Bridge",
    "blender": (4, 2, 0),
    "version": (1, 1, 0),
    "category": "Interface",
    "description": "Connect the live Blender scene to Foundry MCP",
}

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import secrets
from threading import Event, Thread
from time import monotonic
import uuid

import bpy

SERVER = None
SESSION_FILE = None
REQUESTS = Queue(maxsize=16)


def describe(obj):
    return {
        "name": obj.name, "type": obj.type,
        "location": list(obj.location), "rotation": list(obj.rotation_euler),
        "scale": list(obj.scale),
        "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
    }


def vector(data, key):
    values = data[key]
    if (not isinstance(values, list) or len(values) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e6 for v in values)):
        raise ValueError(f"{key} needs three finite numbers")
    if key == "color" and any(not 0 <= value <= 1 for value in values):
        raise ValueError("color values must be between 0 and 1")
    return values


def dispatch(command, data):
    scene = bpy.context.scene
    if command == "status":
        return {"blender_version": bpy.app.version_string, "scene": scene.name}
    if command == "scene":
        offset, limit = data.get("offset", 0), data.get("limit", 50)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid scene page")
        return {"scene": scene.name, "count": len(scene.objects),
                "objects": [describe(o) for o in scene.objects[offset:offset + limit]],
                "next_offset": offset + limit if offset + limit < len(scene.objects) else None}
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.encode("utf-8")) > 60 or "\0" in name:
        raise ValueError("Object name must be nonempty and at most 60 bytes")
    if command == "object":
        obj = scene.objects.get(name)
        if obj is None:
            raise ValueError("Object not found in the active scene")
        return describe(obj)
    if command not in ("create", "edit"):
        raise ValueError("Unknown Blender action")
    values = {key: vector(data, key) for key in ("location", "rotation", "scale", "color") if key in data}
    if not values and command == "edit":
        raise ValueError("Supply a transform or color")
    if bpy.context.mode != "OBJECT":
        raise ValueError("Switch Blender to Object Mode")
    created = command == "create"
    if created:
        if name in bpy.data.objects:
            raise ValueError("Object name already exists")
        operators = {
            "cube": bpy.ops.mesh.primitive_cube_add,
            "sphere": bpy.ops.mesh.primitive_uv_sphere_add,
            "cylinder": bpy.ops.mesh.primitive_cylinder_add,
            "plane": bpy.ops.mesh.primitive_plane_add,
        }
        operator = operators.get(data.get("primitive"))
        if operator is None:
            raise ValueError("Unknown mesh primitive")
        if operator() != {"FINISHED"}:
            raise ValueError("Blender could not create the primitive")
        obj = bpy.context.object
        obj.name = name
    else:
        obj = scene.objects.get(name)
        if obj is None or obj.library is not None:
            raise ValueError("Choose a local object in the active scene")
    previous = {key: tuple(getattr(obj, "rotation_euler" if key == "rotation" else key))
                for key in values if key != "color"}
    material = None
    slots_changed = False
    old_material = None
    appended = False
    try:
        if "color" in values:
            if obj.type != "MESH" or obj.data.library is not None or obj.data.users > 1:
                raise ValueError("Color requires a local mesh with single-user data")
            material = bpy.data.materials.new(name + " Foundry")
            material.diffuse_color = (*values["color"], 1.0)
            material.use_nodes = True
            shader = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
            shader.inputs["Base Color"].default_value = material.diffuse_color
            if obj.material_slots:
                old_material = obj.material_slots[0].material
                obj.material_slots[0].material = material
            else:
                obj.data.materials.append(material)
                appended = True
            slots_changed = True
        for key, value in values.items():
            if key != "color":
                setattr(obj, "rotation_euler" if key == "rotation" else key, value)
    except Exception:
        if created:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        else:
            for key, value in previous.items():
                setattr(obj, "rotation_euler" if key == "rotation" else key, value)
            if slots_changed:
                if appended:
                    obj.data.materials.pop(index=0)
                else:
                    obj.material_slots[0].material = old_material
        if material is not None and material.users == 0:
            bpy.data.materials.remove(material)
        raise
    return {"object": describe(obj)}


def drain():
    for _ in range(8):
        try:
            packet, done, reply, deadline = REQUESTS.get_nowait()
        except Empty:
            break
        try:
            if monotonic() >= deadline:
                raise ValueError("Blender was busy and the queued edit expired")
            reply["result"] = dispatch(packet["command"], packet["data"])
        except Exception as error:
            reply["error"] = str(error)
        finally:
            done.set()
    return 0.05


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        self.connection.settimeout(3)
        authorization = self.headers.get("Authorization", "")
        if self.path != "/rpc" or not hmac.compare_digest(authorization, "Bearer " + self.server.token):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768:
                raise ValueError("Invalid request size")
            packet = json.loads(self.rfile.read(length))
            if not isinstance(packet, dict) or not isinstance(packet.get("data"), dict):
                raise ValueError("Invalid request")
        except (OSError, ValueError):
            self.send_error(400)
            return
        done, reply = Event(), {}
        try:
            REQUESTS.put_nowait((packet, done, reply, monotonic() + 10))
        except Full:
            self.send_error(503)
            return
        if not done.wait(10):
            # Do not report a failed write if Blender has not confirmed its outcome.
            self.send_error(504)
            return
        body = json.dumps(reply, allow_nan=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def register():
    global SERVER, SESSION_FILE
    if SERVER is not None:
        return
    default = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "StudioMCPExtra"
    folder = Path(os.environ.get("FOUNDRY_DATA_DIR", default)) / "blender"
    folder.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.token = secrets.token_urlsafe(32)
    path = folder / (identifier + ".json")
    temp = path.with_suffix(".tmp")
    try:
        with os.fdopen(os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as file:
            json.dump({"id": identifier, "port": server.server_port, "token": server.token}, file)
        temp.replace(path)
        bpy.app.timers.register(drain, persistent=True)
        Thread(target=server.serve_forever, daemon=True).start()
    except Exception:
        if bpy.app.timers.is_registered(drain):
            bpy.app.timers.unregister(drain)
        server.server_close()
        path.unlink(missing_ok=True)
        temp.unlink(missing_ok=True)
        raise
    SERVER, SESSION_FILE = server, path


def unregister():
    global SERVER, SESSION_FILE
    if SERVER is None:
        return
    SESSION_FILE.unlink(missing_ok=True)
    if bpy.app.timers.is_registered(drain):
        bpy.app.timers.unregister(drain)
    while True:
        try:
            _, done, reply, _ = REQUESTS.get_nowait()
        except Empty:
            break
        reply["error"] = "Blender bridge disabled"
        done.set()
    SERVER.shutdown()
    SERVER.server_close()
    SERVER, SESSION_FILE = None, None
