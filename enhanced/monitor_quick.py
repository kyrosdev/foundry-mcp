"""Qt Quick data adapter. The MCP and its snapshot format are unchanged."""
from collections import defaultdict
import re
import time
from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from monitor import action_name, duration

ORANGE = "#ffab7d"
WHITE = "#f3f2f0"
NEUTRAL = "#aaa7a4"


def identity(label):
    words = set(re.findall(r"[a-z0-9]+", label.lower()))
    if words & {"claude", "anthropic"}:
        return "Claude", ORANGE
    if words & {"codex", "chatgpt", "openai"}:
        return "Codex / ChatGPT", WHITE
    return label, NEUTRAL


class MonitorModel(QObject):
    changed = Signal()

    def __init__(self, feed):
        super().__init__()
        self.feed = feed
        self.last = None
        self.selection = ""
        self.payload = {}
        self.timer = QTimer(self, interval=200)
        self.timer.timeout.connect(self.refresh)
        self.refresh()
        self.timer.start()

    @Property("QVariantMap", notify=changed)
    def state(self):
        return self.payload

    @Slot(str)
    def select(self, key):
        self.selection = key
        self.refresh()

    @Slot()
    def toggleCursors(self):
        preference = self.feed.cursor_preference
        if preference.exists():
            preference.unlink(missing_ok=True)
        else:
            preference.parent.mkdir(parents=True, exist_ok=True)
            preference.touch()
        self.refresh()

    def refresh(self):
        sessions, teams = self.feed.latest
        cursors_visible = not self.feed.cursor_preference.exists()
        signature = ([(s["connection"],s["connected"],s["agents"],s["active"],s["history"])
                      for s in sessions],teams,self.selection,cursors_visible)
        if signature == self.last:
            return
        agents, events, groups, clients = {}, [], [], {}
        online = 0
        for session in sessions:
            primary = next((a for a in session["agents"] if a["id"] == session["connection"]), None)
            if primary is None:
                continue
            client, color = identity(primary["name"])
            live = session["connected"]
            if live:
                online += 1
                clients[client] = color
            running = {row["actor"]:row for row in session["active"]} if live else {}
            group = []
            for agent in sorted(session["agents"],key=lambda a:a["id"] != primary["id"]):
                key = agent["id"]
                if not agent["active"]:
                    continue
                action = running.get(key)
                row = {"id":key,"name":agent["name"],"color":color,"online":live,
                       "main":key==primary["id"],"action":action_name(action) if action else "",
                       "place":(action.get("place") or "") if action else "",
                       "started":action["started"] if action else 0}
                group.append(row)
                agents[key] = row
            if group:
                groups.append(group)
            events.extend(session["history"])
        if self.selection not in agents:
            self.selection = ""
        rooms, details = defaultdict(list), []
        for member,tasks in teams:
            key = member["session"]
            if key in agents and agents[key]["online"]:
                rooms[member["project"]].append(key)
                if self.selection == key:
                    details.extend({
                        "project":member["project"],"title":task["title"],
                        "status":{"active":"In progress","review":"In review","blocked":"Blocked"}.get(task["state"],task["state"]),
                        "paths":[" / ".join(path) for path in task["paths"][:4]],
                    } for task in tasks[:4])
        history = []
        for row in sorted(events,key=lambda event:event["finished"],reverse=True):
            if self.selection and self.selection != row["actor"]:
                continue
            actor = agents.get(row["actor"],{})
            history.append({
                "id":row["id"],"time":time.strftime("%H:%M:%S",time.localtime(row["finished"])),
                "name":action_name(row),"actor":actor.get("name","Agent"),
                "color":actor.get("color",NEUTRAL),"ok":row["success"],
                "place":row.get("place") or "", "duration":duration(row["finished"]-row["started"]),
            })
            if len(history) == 60:
                break
        groups.sort(key=lambda group:(identity(group[0]["name"])[0], group[0]["id"]))
        self.payload = {
            "agents":[agent for group in groups for agent in group],"groups":groups,
            "rooms":[{"name":name,"members":sorted(set(members))}
                     for name,members in sorted(rooms.items()) if len(set(members))>1],
            "online":online,"count":sum(a["online"] for a in agents.values()),
            "running":sum(bool(a["action"]) for a in agents.values()),"history":history,
            "clients":[{"name":name,"color":color} for name,color in sorted(clients.items())],
            "accent":next(iter(clients.values())) if len(clients)==1 else WHITE,
            "warm":"Claude" in clients,"selected":agents.get(self.selection,{}),"tasks":details,
            "cursorsVisible":cursors_visible,
        }
        self.last = signature
        self.changed.emit()
