"""Publish an open place through the native Windows Studio command."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from cloud_tools import numeric


def schema(tool):
    return [tool("publish_open_place", "Run Roblox Studio's native File > Publish command for an explicitly selected published place on Windows, then verify the cloud version. Requires a local interactive Studio window.", {
        "studio_id":{"type":"string"},
        "place_id":{"type":"string","description":"Expected Roblox place ID for the selected window."},
    }, ("studio_id","place_id"))]


def publish_key(title, *, launch_file=None):
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.IsWindowEnabled.argtypes = (wintypes.HWND,)
    user.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user.IsIconic.argtypes = (wintypes.HWND,)
    user.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    user.AttachThreadInput.argtypes = (wintypes.DWORD,wintypes.DWORD,wintypes.BOOL)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.EnumWindows.argtypes = (callback,wintypes.LPARAM)
    matches = []

    @callback
    def collect(window, _):
        label = ctypes.create_unicode_buffer(1024)
        user.GetWindowTextW(window,label,len(label))
        if label.value != title or not user.IsWindowVisible(window):
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(window,ctypes.byref(pid))
        handle = kernel.OpenProcess(0x1000,False,pid.value)
        if handle:
            try:
                name = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(name))
                if kernel.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(size)) and Path(name.value).name.lower() == "robloxstudiobeta.exe":
                    matches.append(window)
            finally:
                kernel.CloseHandle(handle)
        return True

    user.EnumWindows(collect,0)
    if launch_file is not None:
        if matches:
            raise ValueError("Close any existing Studio window for this local file before opening the publish plan")
        candidates = sorted((Path(os.environ["LOCALAPPDATA"])/"Roblox"/"Versions").glob("*/RobloxStudioBeta.exe"),
                            key=lambda path:path.stat().st_mtime,reverse=True)
        if not candidates:
            raise ValueError("Roblox Studio installation was not found")
        subprocess.Popen([str(candidates[0]),"--task","EditFile","--localPlaceFile",str(launch_file)],
                         stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline = time.monotonic()+40
        while not matches and time.monotonic() < deadline:
            time.sleep(0.25)
            user.EnumWindows(collect,0)
    if len(matches) != 1:
        raise ValueError("Expected exactly one local Roblox Studio window named " + title)
    window = matches[0]
    if launch_file is not None:
        deadline = time.monotonic()+20
        while not user.IsWindowEnabled(window) and time.monotonic() < deadline:
            time.sleep(0.25)
        if not user.IsWindowEnabled(window):
            raise ValueError("Studio is still loading the local file")
    if user.IsIconic(window):
        user.ShowWindowAsync(window,9)
    user.SetForegroundWindow(window)
    foreground = user.GetForegroundWindow()
    if foreground and foreground != window:
        foreground_thread = user.GetWindowThreadProcessId(foreground,None)
        caller = kernel.GetCurrentThreadId()
        if foreground_thread and foreground_thread != caller and user.AttachThreadInput(caller,foreground_thread,True):
            try:
                user.SetForegroundWindow(window)
            finally:
                user.AttachThreadInput(caller,foreground_thread,False)
    until = time.monotonic()+2
    while user.GetForegroundWindow() != window:
        if time.monotonic() >= until:
            raise ValueError("Windows did not activate the selected Roblox Studio window")
        time.sleep(0.05)

    class Keyboard(ctypes.Structure):
        _fields_ = [("key",wintypes.WORD),("scan",wintypes.WORD),("flags",wintypes.DWORD),
                    ("time",wintypes.DWORD),("extra",ctypes.c_size_t)]

    class Mouse(ctypes.Structure):
        _fields_ = [("x",wintypes.LONG),("y",wintypes.LONG),("data",wintypes.DWORD),
                    ("flags",wintypes.DWORD),("time",wintypes.DWORD),("extra",ctypes.c_size_t)]

    class Event(ctypes.Union):
        _fields_ = [("keyboard",Keyboard),("mouse",Mouse)]

    class Input(ctypes.Structure):
        _fields_ = [("type",wintypes.DWORD),("event",Event)]

    user.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
    user.SendInput.restype = wintypes.UINT
    label = ctypes.create_unicode_buffer(1024)
    user.GetWindowTextW(window,label,len(label))
    if user.GetForegroundWindow() != window or label.value != title:
        raise ValueError("Studio window or focus changed before publish")
    if any(user.GetAsyncKeyState(key) & 0x8000 for key in (0x10,0x11,0x12,0x50,0x5B,0x5C)):
        raise ValueError("Release the keyboard modifiers before publishing")
    events = (Input*4)(*(Input(1,Event(keyboard=Keyboard(key,0,flags,0,0)))
                          for key,flags in ((0x12,0),(0x50,0),(0x50,2),(0x12,2))))
    if user.SendInput(len(events),events,ctypes.sizeof(Input)) != len(events):
        raise RuntimeError("Windows did not send the complete Studio Publish shortcut")


def log_paths():
    return (Path(os.environ["LOCALAPPDATA"])/"Roblox"/"logs").glob("*_Studio_*_last.log")


def log_offsets():
    return {path:path.stat().st_size for path in log_paths() if path.is_file()}


def publish_confirmation(offsets, place):
    success = f"Publish succeeded for placeId {place};"
    for path in log_paths():
        try:
            with path.open("rb") as stream:
                stream.seek(offsets.get(path,0))
                data = stream.read(1_000_000).decode("utf-8","replace")
        except OSError:
            continue
        for line in data.splitlines():
            if success in line:
                return line.split(",",1)[0]
    return None


def file_publish_confirmation(offsets, file, universe, place):
    destination = f"PublishToDifferentGame srcPlaceId=0, dstPlaceId={place}, srcUniverse=0, dstUniverse={universe}"
    for path in log_paths():
        try:
            with path.open("rb") as stream:
                stream.seek(offsets.get(path,0))
                data = stream.read(2_000_000).decode("utf-8","replace")
        except OSError:
            continue
        opened = data.replace("\\","/").lower().find(str(file).replace("\\","/").lower())
        if opened < 0:
            continue
        for line in data[opened:].splitlines():
            if destination in line:
                return line.split(",",1)[0]
    return None


class StudioPublish:
    def __init__(self, wb):
        self.wb = wb

    def target(self, studio, place, *, require_title=True):
        reply = self.wb.read_rpc("tools/call",{"name":"list_roblox_studios","arguments":{}})
        if reply.get("isError"):
            raise ValueError("Could not list Roblox Studio windows")
        windows = json.loads(reply["content"][0]["text"])["studios"]
        row = next((window for window in windows if window["id"] == studio),None)
        if not row or not row.get("persistedToCloud") or str(row.get("placeId")) != place or row.get("mode") != "Edit":
            raise ValueError("Select the expected published place in an open Studio Edit session")
        if not require_title:
            return None, numeric(str(row["universeId"]))
        title = (row.get("name") or "").removesuffix(f" (placeId: {place})")
        if not title or sum((window.get("name") or "").split(" (placeId: ",1)[0] == title for window in windows) != 1:
            raise ValueError("The Studio place name is ambiguous")
        return title, numeric(str(row["universeId"]))

    def published_version(self, studio, universe, place):
        cloud = self.wb.workflows.cloud
        root = f"universes/{universe}/places/{place}"
        scope = "universe.place.luau-execution-session:"
        result = cloud(studio,"POST",f"/cloud/v2/{root}/luau-execution-session-tasks",
                       scope+"write",{"script":"return game.PlaceVersion","timeout":"15s"})
        path = result.get("path","")
        match = re.fullmatch(rf"{root}/versions/([1-9][0-9]*)/luau-execution-sessions/[\w-]+/tasks/[\w-]+",path)
        if not match:
            raise ValueError("Roblox did not return a versioned task for this place")
        until = time.monotonic()+20
        while result.get("state") not in ("COMPLETE","FAILED","CANCELLED") and time.monotonic() < until:
            time.sleep(0.5)
            result = cloud(studio,"GET","/cloud/v2/"+path,scope+"read",readonly=True)
        version = int(match[1])
        if result.get("state") != "COMPLETE" or result.get("output",{}).get("results") != [version]:
            raise ValueError("Roblox did not confirm the running place version")
        return version

    def file_dialog(self, args):
        if os.name != "nt":
            raise ValueError("Studio file publication is supported on Windows")
        studio, place = args["studio_id"], numeric(args["place_id"])
        action = args["action"]
        if action == "prepare":
            title, universe = self.target(studio,place)
            if Path(args["file"]).is_absolute():
                raise ValueError("Use a workspace-relative place file")
            data, _ = self.wb.workflows.cloud_tools.place_file(args["file"])
            plan = {"file":args["file"],"sha256":hashlib.sha256(data).hexdigest(),"place_id":place,
                    "universe":universe,"destination":title}
            identifier = self.wb.advanced.begin(studio,None,plan,kind="publish_file_dialog")
            return {"plan_id":identifier,"state":"prepared",**plan}
        identifier = args["plan_id"]
        with self.wb.store.lease("studio:"+studio,self.wb.owner):
            row = self.wb.advanced.operation(identifier)
            plan = row["after_json"]
            if (row["kind"],row["owner"],plan.get("place_id")) != (
                "publish_file_dialog",self.wb.owner,place
            ):
                raise ValueError("Publication plan belongs to another session or place")
            universe, title = plan["universe"], plan["destination"]
            if action == "apply":
                if row["studio"] != studio or self.target(studio,place)[1] != universe:
                    raise ValueError("Apply the plan against the original destination window")
                self.wb.team.authorize_studio_action(studio)
                if row["state"] != "prepared":
                    raise ValueError("Publication plan is not ready")
                data, _ = self.wb.workflows.cloud_tools.place_file(plan["file"])
                if hashlib.sha256(data).hexdigest() != plan["sha256"]:
                    raise ValueError("Place file changed since preview")
                path = (self.wb.workspace/plan["file"]).resolve()
                offsets = {str(path):offset for path,offset in log_offsets().items()}
                result = {"offsets":offsets,"place_id":place,"source":str(path),
                          "previous_version":self.published_version(studio,universe,place)}
                self.wb.advanced.finish(identifier,"awaiting_destination",result)
                try:
                    publish_key(str(path)+" - Roblox Studio", launch_file=path)
                except Exception as error:
                    self.wb.advanced.finish(identifier,"awaiting_destination",{**result,"dialog_error":str(error)})
                    return {"plan_id":identifier,"state":"awaiting_destination","destination":title,
                            "instruction":"If the file is open, choose File > Publish As, then select the expected place and choose Overwrite.",
                            "dialog_error":str(error)}
                return {"plan_id":identifier,"state":"awaiting_destination","destination":title,"place_id":place,
                        "instruction":"Choose Update existing experience, select the expected place and choose Overwrite."}
            if action != "status":
                raise ValueError("Unknown Studio file publication action")
            if row["state"] != "awaiting_destination":
                return {"plan_id":identifier,"state":row["state"],**row["result"]}
            confirmed = file_publish_confirmation({Path(path):offset for path,offset in row["result"]["offsets"].items()},
                                                  row["result"]["source"],universe,place)
            if not confirmed:
                return {"plan_id":identifier,"state":"awaiting_destination","destination":title}
            if self.target(studio,place)[1] != universe:
                raise ValueError("Select the reopened destination window to verify the published version")
            version = self.published_version(studio,universe,place)
            if version <= row["result"]["previous_version"]:
                return {"plan_id":identifier,"state":"awaiting_destination","destination":title,
                        "observed_version":version}
            result = {"place_id":place,"published_version":version,"studio_confirmed_at":confirmed,
                      "previous_version":row["result"]["previous_version"]}
            self.wb.advanced.finish(identifier,"published",result)
            return {"plan_id":identifier,"state":"published",**result}

    def call(self, args):
        if os.name != "nt":
            raise ValueError("Publishing the open Studio window is supported on Windows")
        studio, place = args["studio_id"], numeric(args["place_id"])
        self.wb.team.authorize_studio_action(studio)
        with self.wb.store.lease("studio:"+studio,self.wb.owner):
            title, universe = self.target(studio,place)
            identifier = self.wb.advanced.begin(studio,None,{"place_id":place,"universe":universe},
                                                kind="publish_open_place",state="applying")
            try:
                offsets = log_offsets()
                publish_key(title+" - Roblox Studio")
                until = time.monotonic()+25
                confirmed = None
                while time.monotonic() < until:
                    confirmed = publish_confirmation(offsets,place)
                    if confirmed:
                        break
                    time.sleep(0.25)
                if not confirmed:
                    raise ValueError("Studio did not log a successful publication; inspect Studio before retrying")
            except Exception as error:
                result = {"plan_id":identifier,"state":"uncertain","blocked":True,"error":str(error)}
                self.wb.advanced.finish(identifier,"uncertain",result)
                return result
            result = {"plan_id":identifier,"state":"published","place_id":place,"universe_id":universe,
                      "studio_confirmed_at":confirmed}
            try:
                result["published_version"] = self.published_version(studio,universe,place)
            except Exception as error:
                result["verification_error"] = str(error)
            self.wb.advanced.finish(identifier,"committed",result)
            return result
