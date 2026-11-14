"""Act on a named Studio ribbon control through Windows accessibility."""
import json
from pathlib import Path
import subprocess
import sys
import ctypes
from ctypes import wintypes
import re
import shutil
import tempfile
import time


def ribbon_words(window, shell, *, labels_only=False):
    capture = shutil.which("ffmpeg")
    if not capture:
        raise ValueError("Install FFmpeg to recognize custom Studio ribbon buttons")
    flags = subprocess.CREATE_NO_WINDOW
    with tempfile.TemporaryDirectory(prefix="foundry-ribbon-") as directory:
        image = Path(directory)/"ribbon.png"
        filters = ("crop=iw:23:0:102,scale=iw*2:ih*2,format=gray,eq=contrast=1.5"
                   if labels_only else "crop=iw:130:0:0,scale=iw*2:ih*2")
        subprocess.run([capture,"-v","error","-f","gdigrab","-draw_mouse","0","-i",f"hwnd={window}",
                        "-frames:v","1","-vf",filters,"-update","1",str(image)],
                       capture_output=True,check=True,timeout=8,creationflags=flags)
        read = subprocess.run(shell+["-File",str(Path(__file__).with_name("ribbon_ocr.ps1")),"-Image",str(image)],
                              capture_output=True,encoding="utf-8",check=True,timeout=8,creationflags=flags)
    lines = json.loads(read.stdout)["lines"]
    if labels_only:
        for line in lines:
            for word in line:
                word["top"] += 204
                word["bottom"] += 204
    return lines


class RibbonLabelNotFound(ValueError):
    pass


def find_ribbon_text(lines, name):
    def normalize(text):
        return re.findall(r"[^\W_]+",text.casefold())

    requested = "".join(normalize(name))
    matches = []
    for line in lines:
        words = [word for word in line if 58 <= word["top"] <= 248]
        for start in range(len(words)):
            candidate = ""
            for end in range(start,len(words)):
                word = words[end]
                if end > start and word["left"]-words[end-1]["right"] > 22:
                    break
                candidate += "".join(normalize(word["text"]))
                if candidate == requested and candidate:
                    first = words[start]
                    matches.append(((first["left"]+word["right"])/4,(first["top"]+word["bottom"])/4))
                    break
                if not requested.startswith(candidate):
                    break
    if not matches:
        raise RibbonLabelNotFound(f"No visible ribbon label matches {name!r}")
    if len(matches) != 1:
        raise ValueError(f"Expected one visible ribbon label matching {name!r}; found {len(matches)}")
    return matches[0]


def visual_control(window, control, shell):
    try:
        x,y = find_ribbon_text(ribbon_words(window,shell),control)
    except RibbonLabelNotFound:
        x,y = find_ribbon_text(ribbon_words(window,shell,labels_only=True),control)
    user = ctypes.WinDLL("user32",use_last_error=True)
    user.GetWindowRect.argtypes = (wintypes.HWND,ctypes.POINTER(wintypes.RECT))
    frame, bounds = wintypes.RECT(), wintypes.RECT()
    if (ctypes.windll.dwmapi.DwmGetWindowAttribute(window,9,ctypes.byref(frame),ctypes.sizeof(frame))
            or not user.GetWindowRect(window,ctypes.byref(bounds))):
        raise ValueError("Studio ribbon window moved or closed")
    return round(x+frame.left-bounds.left),round(y+frame.top-bounds.top)


def click_at(window, x, y):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.ChildWindowFromPointEx.argtypes = (wintypes.HWND,wintypes.POINT,wintypes.UINT)
    user.ChildWindowFromPointEx.restype = wintypes.HWND
    user.GetWindowRect.argtypes = (wintypes.HWND,ctypes.POINTER(wintypes.RECT))
    user.ScreenToClient.argtypes = (wintypes.HWND,ctypes.POINTER(wintypes.POINT))
    user.GetClientRect.argtypes = (wintypes.HWND,ctypes.POINTER(wintypes.RECT))
    user.PostMessageW.argtypes = (wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
    outer = wintypes.RECT()
    if not user.GetWindowRect(window,ctypes.byref(outer)) or not 0 <= x < outer.right-outer.left:
        raise ValueError("Point is outside the selected Studio window")
    target = window
    absolute = wintypes.POINT(outer.left+x,outer.top+y)
    while True:
        local = wintypes.POINT(absolute.x,absolute.y)
        if not user.ScreenToClient(target,ctypes.byref(local)):
            raise ValueError("Studio ribbon moved during targeting")
        child = user.ChildWindowFromPointEx(target,local,7)
        if not child or child == target:
            break
        target = child
    if target == window:
        raise ValueError("Point does not identify a Studio ribbon widget")
    client = wintypes.RECT()
    if not user.GetClientRect(target,ctypes.byref(client)) or not (0 <= local.x < client.right and 0 <= local.y < client.bottom):
        raise ValueError("Point is outside the Studio ribbon widget")
    packed = local.x | (local.y << 16)
    for message, buttons in ((0x200,0),(0x201,1),(0x202,0)):
        if not user.PostMessageW(target,message,buttons,packed):
            raise ctypes.WinError(ctypes.get_last_error())


def call_controls(workbench, studio, payload):
    if sys.platform != "win32":
        raise ValueError("Studio ribbon controls require Windows")
    action = payload.get("action")
    control = payload.get("control", "")
    if action not in ("list", "point", "click", "point_at", "click_at"):
        raise ValueError("Use list, point, click, point_at or click_at")
    if not isinstance(control, str) or len(control) > 180 or (action != "list" and not control.strip()):
        raise ValueError("Supply the exact ribbon control name or automation ID")
    for attempt in range(2):
        reply = workbench.read_rpc("tools/call", {"name":"list_roblox_studios","arguments":{}})
        windows = json.loads(reply["content"][0]["text"])["studios"]
        place = next((entry for entry in windows if entry["id"] == studio), None)
        name = (place.get("name") or place.get("file")) if place else None
        if (name and place.get("mode")) or attempt:
            break
        time.sleep(0.2)
    if not name or place.get("mode") != "Edit":
        raise ValueError("Select a named Studio window in Edit mode")
    title = name.split(" (placeId: ", 1)[0] + " - Roblox Studio"
    titles = [(entry.get("name") or entry.get("file") or "").split(" (placeId: ", 1)[0]
              for entry in windows]
    if titles.count(title.removesuffix(" - Roblox Studio")) != 1:
        raise ValueError("Studio windows with identical titles cannot be targeted")
    # Use exact title matching and pass user strings only as subprocess arguments.
    shell = ["powershell.exe","-NoProfile","-NonInteractive"]
    lookup = subprocess.run(shell + ["-Command",
        "Get-Process RobloxStudioBeta | ForEach-Object { "
        "if ($_.MainWindowHandle) { @{title=$_.MainWindowTitle;window=[long]$_.MainWindowHandle} | ConvertTo-Json -Compress } }"],
        capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    handles = [row["window"] for line in lookup.stdout.splitlines() if line.strip()
               if (row := json.loads(line))["title"] == title]
    if len(handles) != 1:
        raise ValueError("Expected one local Studio window with title " + title)
    if action.endswith("_at"):
        x,y = payload.get("x"),payload.get("y")
        if type(x) is not int or type(y) is not int or not 0 <= x < 16384 or not 28 <= y <= 130:
            raise ValueError("Use a window-relative pixel within the top Studio ribbon (y 28–130)")
        if action == "click_at":
            click_at(handles[0],x,y)
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(handles[0],ctypes.byref(pid))
        return {"name":control,"window":handles[0],"studio_id":studio,"pid":pid.value,"x":x,"y":y,
                "dispatch":"posted" if action == "click_at" else "pointed"}
    command = shell + ["-File",str(Path(__file__).with_suffix(".ps1")),
                       "-Window",str(handles[0]),"-Action",action]
    if control:
        command += ["-Control",control]
    result = subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace",
                            timeout=12,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError(result.stderr.strip()[:700] or "Studio did not expose that control")
    data = json.loads(result.stdout)
    if data.get("missing") or (action == "click" and not data.get("actionable")):
        x,y = visual_control(handles[0],control,shell)
        if action == "click":
            if not 28 <= y <= 150:
                raise ValueError("Recognized text is outside the Studio ribbon")
            click_at(handles[0],x,y)
        data = {"name":control,"pid":data["pid"],"x":x,"y":y,"resolution":"visible_label",
                "dispatch":"posted" if action == "click" else "pointed"}
    return {**data,"window":handles[0],"studio_id":studio}
