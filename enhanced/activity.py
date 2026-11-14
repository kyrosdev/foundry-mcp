"""Publish bounded tool activity off the MCP request path."""
from collections import deque
import json
from pathlib import Path
import threading
import time
import uuid
from cursors import ViewportCursors, cursor_activity


class Activity:
    def __init__(self, directory, workbench, viewport=False):
        self.workbench = workbench
        folder = Path(directory) / "monitor"
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / (workbench.connection_id + ".json")
        self.cursor_preference = Path(directory) / "hide-studio-cursors"
        self.lock = threading.Lock()
        self.active = {}
        self.history = deque(maxlen=100)
        self.places = {}
        self.dirty = True
        self.stopping = threading.Event()
        self.viewport = ViewportCursors(workbench) if viewport else None
        self.thread = threading.Thread(target=self.run, name="monitor-snapshot", daemon=True)
        self.thread.start()

    def begin(self, name, args, *, started=None):
        identifier = uuid.uuid4().hex
        studio = args.get("studio_id")
        action = args.get("action")
        row = {"id":identifier, "actor":self.workbench.owner, "tool":str(name)[:100],
               "studio":studio[:200] if isinstance(studio,str) else None,
               "action":action[:48] if isinstance(action,str) else None,
               "started":time.time() if started is None else started}
        if self.viewport:
            row["cursor"] = cursor_activity(name,args)
        with self.lock:
            self.active[identifier] = row
            self.dirty = True
        return identifier

    def observe_places(self, result):
        try:
            rows = json.loads(result["content"][0]["text"])["studios"]
            places = {row["id"]:row["name"][:200] for row in rows[:100]}
        except (ValueError, KeyError, IndexError, TypeError):
            return
        with self.lock:
            self.places = places
            self.dirty = True

    def finish(self, identifier, success, result=None, *, finished=None):
        with self.lock:
            row = self.active.pop(identifier)
            if result and "studio_control" in result:
                control = result["studio_control"]
                row["ribbon_cursor"] = control
                row["cursor"] = None
            elif result and row.get("cursor"):
                row["cursor"] = {**row["cursor"],"report":result,"stage":"Playtest results"}
            self.history.appendleft({**row,"finished":time.time() if finished is None else finished,
                                     "success":bool(success)})
            self.dirty = True

    def publish(self, connected):
        with self.lock:
            def with_place(row):
                return {**row, "place":self.places.get(row["studio"])}
            active = [with_place(row) for row in self.active.values()]
            history = [with_place(row) for row in self.history]
            self.dirty = False
        with self.workbench.agent_lock:
            agents = [{"id":identifier, "name":info["name"], "active":info["active"]}
                      for identifier,info in self.workbench.agents.items()]
        payload = {"connection":self.workbench.connection_id,"connected":connected,
                   "updated":time.time(),"agents":agents,"active":active,"history":history}
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(payload,ensure_ascii=True),encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            with self.lock:
                self.dirty = True
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        if self.viewport:
            self.viewport.publish(active,history,agents,connected,hidden=self.cursor_preference.exists())

    def run(self):
        cutoff = time.time() - 86400
        for snapshot in self.path.parent.glob("*.json"):
            try:
                if snapshot.stat().st_mtime < cutoff:
                    snapshot.unlink()
            except OSError:
                continue
        previous = 0
        while not self.stopping.wait(0.10):
            now = time.monotonic()
            if self.dirty or now - previous >= 2:
                self.publish(True)
                previous = now
        self.publish(False)

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=1)
