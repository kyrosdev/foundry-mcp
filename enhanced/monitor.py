"""Native Windows activity monitor. The MCP publishes snapshots independently."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import threading
import time


def read_sessions(directory):
    now = time.time()
    sessions = []
    candidates = []
    for path in (directory / "monitor").glob("*.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if now - stat.st_mtime < 3600 and stat.st_size <= 1_000_000:
            candidates.append((stat.st_mtime, path))
    for _, path in sorted(candidates, reverse=True)[:24]:
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
            session["connected"] = session["connected"] and now - session["updated"] < 7
            sessions.append(session)
        except (OSError, ValueError, KeyError):
            continue
    return sessions


def read_teams(directory):
    uri = (directory / "workbench.sqlite3").resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as db:
            db.row_factory = sqlite3.Row
            members = db.execute(
                "SELECT project,session,label,role FROM team_members WHERE connected=1 "
                "AND heartbeat>? ORDER BY heartbeat DESC LIMIT 100", (time.time()-35,)
            ).fetchall()
            tasks = db.execute(
                "SELECT id,project,owner,title,state,paths FROM team_tasks "
                "WHERE state IN ('active','review','blocked') ORDER BY updated DESC LIMIT 100"
            ).fetchall()
            return [
                (dict(member), [{**dict(task), "paths":json.loads(task["paths"])[:8]}
                                for task in tasks if task["owner"] == member["session"]
                                and task["project"] == member["project"]][:8])
                for member in members
            ]
    except (sqlite3.Error, ValueError):
        return []


class Feed:
    def __init__(self, directory):
        self.cursor_preference = directory / "hide-studio-cursors"
        self.latest = ([], [])
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.run, args=(directory,), daemon=True)
        self.worker.start()

    def run(self, directory):
        teams = []
        next_team_read = 0
        while not self.stop.is_set():
            if time.monotonic() >= next_team_read:
                teams = read_teams(directory)
                next_team_read = time.monotonic() + 1
            self.latest = (read_sessions(directory), teams)
            self.stop.wait(0.18)

    def close(self):
        self.stop.set()


LABELS = {
    "extra_impact":"Mapping references", "extra_project_check":"Analyzing Luau",
    "extra_script_get":"Reading script", "extra_script_put":"Editing script",
    "extra_playtest":"Running playtest", "get_studio_state":"Querying Studio",
    "list_roblox_studios":"Listing Studio windows", "extra_status":"Checking connection",
    "extra_analytics":"Querying analytics", "extra_task_create":"Creating task",
    "extra_team_join":"Joining team", "extra_agent":"Managing agents",
    "extra_live_read":"Reading place", "execute_luau":"Running Luau",
    "start_stop_play":"Controlling playtest", "script_read":"Reading script",
    "extra_scenario":"Preparing scenario", "extra_game_pass":"Managing passes",
    "extra_mixed_changes":"Preparing changes", "extra_changes":"Preparing changes",
    "open_cloud_invoke":"Calling Open Cloud", "get_console_output":"Reading console",
}


def action_name(action):
    tool = action["tool"]
    verb = action.get("action")
    if tool == "extra_scenario" and verb == "run":
        return "Running saved scenario"
    if tool in ("extra_mixed_changes", "extra_changes"):
        if verb == "apply":
            return "Applying changes"
        if verb == "rollback":
            return "Reverting changes"
    return LABELS.get(tool, tool.removeprefix("extra_").replace("_", " "))


def duration(seconds):
    seconds = max(0, seconds)
    if seconds < 1:
        return f"{seconds*1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{int(seconds//60)} min {int(seconds%60)} s"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", str(Path.home()/".local/share")))/"StudioMCPExtra")
    parser.add_argument("--view", choices=("activity","graph"), default="graph")
    parser.add_argument("--ribbon-only", action="store_true", help="Run only the Studio ribbon cursor overlay")
    args = parser.parse_args()
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from monitor_quick import MonitorModel
    from ribbon_overlay import RibbonOverlay

    application = QApplication([])
    application.setQuitOnLastWindowClosed(False if args.ribbon_only else True)
    feed = Feed(args.data_dir)
    try:
        model = MonitorModel(feed)
        overlay = RibbonOverlay(feed, application)
        if args.ribbon_only:
            application.exec()
            return
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("monitorModel", model)
        engine.rootContext().setContextProperty("startView", args.view)
        motion = True
        if os.name == "nt":
            import ctypes
            enabled = ctypes.c_int()
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                motion = bool(enabled.value)
        engine.rootContext().setContextProperty("allowMotion", motion)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("Monitor.qml"))))
        if not engine.rootObjects():
            raise RuntimeError("Could not load the Qt Quick interface")
        application.exec()
    finally:
        feed.close()


if __name__ == "__main__":
    main()
