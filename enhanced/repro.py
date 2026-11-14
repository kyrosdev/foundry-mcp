"""Record bounded input and diagnostics in an explicitly selected Studio playtest."""
import json
from pathlib import Path
import time
import uuid

from team import text

CODE = (Path(__file__).resolve().parent / "repro.luau").read_text(encoding="utf-8")
REPLAY_CODE = Path(__file__).with_name("replay.luau").read_text(encoding="utf-8")


class Reproductions:
    def __init__(self, workflows):
        self.workflows = workflows
        self.wb = workflows.wb

    def studio_call(self, studio, model, payload):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",",":"), allow_nan=False)
        code = "local payload = game:GetService('HttpService'):JSONDecode(" + json.dumps(raw, ensure_ascii=False) + ")\n" + CODE
        response = self.wb.rpc("tools/call", {"name":"execute_luau","arguments":{
            "studio_id":studio,"datamodel_type":model,"code":code}}, timeout=20)
        if response.get("isError"):
            raise ValueError(response["content"][0]["text"])
        return json.loads(response["content"][0]["text"])

    def call(self, args, project):
        studio, action = args["studio_id"], args["action"]
        if action == "record_start":
            self.wb.team.authorize_studio_action(studio)
            identifier = uuid.uuid4().hex
            with self.wb.store.lease("studio:"+studio, self.wb.owner):
                self.studio_call(studio,"Client",{"id":identifier,"action":"start","client":True})
                try:
                    self.studio_call(studio,"Server",{"id":identifier,"action":"start"})
                except Exception:
                    try:
                        self.studio_call(studio,"Client",{"id":identifier,"action":"stop"})
                    except Exception:
                        self.wb.store.event(self.wb.owner,"repro_start_cleanup_failed",{"recording_id":identifier})
                    raise
                with self.wb.store.connect() as db:
                    db.execute("INSERT INTO reproductions VALUES(?,?,?,?,?,?,?)",
                               (identifier,studio,project,self.wb.owner,"recording","{}",time.time()))
            return {"recording_id":identifier,"max_seconds":120}
        identifier = args["recording_id"]
        with self.wb.store.connect() as db:
            row = db.execute("SELECT * FROM reproductions WHERE id=?", (identifier,)).fetchone()
        if not row or (row["studio"] != studio and row["project"] != project):
            raise ValueError("Recording not found for this place")
        report = json.loads(row["report"])
        if action == "record_get":
            return {"state":row["state"],**report}
        if action != "record_stop" or row["studio"] != studio or row["owner"] != self.wb.owner:
            raise ValueError("Stop the recording from its original connection and Studio window")
        if row["state"] == "saved":
            return {k:report[k] for k in ("name","version","events","dropped","errors")}
        name = text(args["name"], "name", 128)
        assertion = args.get("assert_code")
        assertion_model = args.get("assert_model","Server")
        if assertion_model not in ("Client","Server"):
            raise ValueError("Select Client or Server for the recording assertion")
        if assertion is not None and (not isinstance(assertion,str) or not 0 < len(assertion.encode()) <= 50000):
            raise ValueError("Provide an assertion of up to 50 KB")
        self.wb.team.authorize_studio_action(studio)
        with self.wb.store.lease("studio:"+studio, self.wb.owner):
            with self.wb.store.connect() as db:
                current = db.execute("SELECT state,report FROM reproductions WHERE id=?", (identifier,)).fetchone()
            if current["state"] == "saved":
                saved = json.loads(current["report"])
                return {k:saved[k] for k in ("name","version","events","dropped","errors")}
            captures = json.loads(current["report"])
            for model in ("Client","Server"):
                if model not in captures:
                    captures[model] = self.studio_call(studio,model,{"id":identifier,"action":"stop"})
                    with self.wb.store.connect() as db:
                        db.execute("UPDATE reproductions SET report=? WHERE id=?", (json.dumps(captures),identifier))
            events = captures["Client"].get("events") or []
            steps = [{"tool":"start_stop_play","arguments":{"is_start":True}}]
            chunk, elapsed, previous = [], 0, 0
            for event in events:
                gap = min(max(0,event["t"]-previous),5)
                if chunk and (len(chunk) == 40 or elapsed + gap > 8):
                    steps.append(self.replay_step(chunk))
                    chunk, elapsed = [], 0
                elapsed += gap
                previous = event["t"]
                chunk.append({**event,"t":round(elapsed,3)})
            if chunk:
                steps.append(self.replay_step(chunk))
            if assertion:
                steps.append({"assert_code":assertion,"arguments":{"datamodel_type":assertion_model}})
            else:
                for datamodel, capture in captures.items():
                    messages = sorted({log["message"] for log in capture["logs"] if log["kind"] == "MessageError"})
                    if messages:
                        raw = json.dumps(messages,ensure_ascii=False)
                        check = ("task.wait(0.2)\nlocal expected=game:GetService('HttpService'):JSONDecode(" + json.dumps(raw,ensure_ascii=False) + ")\n"
                                 "for _,log in game:GetService('LogService'):GetLogHistory() do\n"
                                 " if log.messageType == Enum.MessageType.MessageError then\n"
                                 "  for _,message in expected do\n"
                                 "   if string.find(log.message,message,1,true) then return false end\n"
                                 "  end\n end\nend\nreturn true")
                        steps.append({"assert_code":check,"arguments":{"datamodel_type":datamodel}})
            steps.append({"tool":"get_console_output"})
            if len(steps)>30:
                raise ValueError("Recording exceeds the replay budget. Save a shorter reproduction.")
            summary = {"name":name,"events":len(events),
                       "dropped":sum(capture["dropped"] for capture in captures.values()),
                       "errors":sum(log["kind"]=="MessageError" for capture in captures.values() for log in capture["logs"])}
            version = self.workflows.save_scenario(project,name,steps,(identifier,{**summary,"capture":captures}))
        return {**summary,"version":version}

    @staticmethod
    def replay_step(events):
        encoded = json.dumps(events, separators=(",",":"))
        code = "local payload={events=game:GetService('HttpService'):JSONDecode(" + json.dumps(encoded) + ")}\n"
        return {"arguments":{"datamodel_type":"Client","code":code+REPLAY_CODE}}
