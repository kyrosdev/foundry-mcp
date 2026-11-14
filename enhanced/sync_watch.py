"""Session-scoped bidirectional project sync using the same guards as manual sync."""
import json
import threading
import time


def schema(tool):
    string = {"type":"string"}
    return [tool("project_sync_watch", "Automatically sync a bound project while this MCP connection is open. Stops on conflicts or uncertain writes.", {
        "studio_id":string,"directory":string,"action":{"enum":["start","status","stop"]},
        "interval":{"type":"integer","minimum":3,"maximum":120},
    }, ("studio_id","directory","action"))]


class SyncWatch:
    def __init__(self, wb):
        self.wb = wb
        self.lock = threading.Lock()
        self.jobs = {}

    def call(self, args):
        studio = args["studio_id"]
        sync = self.wb.project_sync
        directory = sync.directory(args["directory"])
        key = (studio, directory)
        with self.lock:
            job = self.jobs.get(key)
            if args["action"] == "status":
                return dict(job["status"]) if job else {"state":"stopped"}
            if args["action"] == "stop":
                if job:
                    job["stop"].set()
                    if job["thread"].is_alive():
                        return {"state":"stopping"}
                return {"state":"stopped"}
            if args["action"] != "start":
                raise ValueError("Unknown sync watch action")
            if self.wb.owner != self.wb.connection_id:
                raise ValueError("Start automatic sync from the main MCP connection")
            if job and job["thread"].is_alive():
                raise ValueError("Automatic sync is already running")
            project = self.wb.store.project_key(studio)
            with self.wb.store.connect() as db:
                binding = db.execute("SELECT scope FROM project_sync_roots WHERE directory=? AND project=?",
                                     (str(directory),project)).fetchone()
            if binding is None:
                raise ValueError("Apply the initial project sync plan before starting automatic sync")
            interval = args.get("interval", 5)
            if type(interval) is not int or not 3 <= interval <= 120:
                raise ValueError("Sync interval must be between 3 and 120 seconds")
            job = {"status":{"state":"waiting"},"stop":threading.Event()}
            watch_args = {"studio_id":studio,"directory":args["directory"],"scope":json.loads(binding["scope"])}
            job["thread"] = threading.Thread(target=self.run, args=(job,watch_args,project,directory,interval), daemon=True)
            self.jobs[key] = job
            job["thread"].start()
            return dict(job["status"])

    def run(self, job, args, project, directory, interval):
        stop = job["stop"]
        sync = self.wb.project_sync
        studio = args["studio_id"]
        last_commit = None
        try:
            with self.wb.store.lease("sync_watch:" + str(directory),self.wb.owner) as token:
                while not stop.wait(interval):
                    self.refresh_lease(token)
                    reply = self.wb.read_rpc("tools/call",{"name":"get_studio_state","arguments":{"studio_id":studio}}, timeout=10)
                    state = " ".join(block.get("text","") for block in reply.get("content",[]))
                    if reply.get("isError") or "Current Studio Mode: Edit" not in state:
                        job["status"] = {"state":"waiting_for_edit","last_commit":last_commit}
                        continue
                    if self.wb.store.project_key(studio) != project:
                        raise ValueError("Studio binding changed")
                    preview = sync.preview(args,project,skip_idle=True)
                    if preview.get("blocked"):
                        job["status"] = {"state":"conflict","last_commit":last_commit,"details":preview}
                        return
                    if stop.is_set():
                        break
                    if preview["state"] == "prepared":
                        result = sync.apply({**args,"plan_id":preview["plan_id"]},project)
                        if result.get("blocked"):
                            job["status"] = {"state":"blocked","last_commit":last_commit,"details":result}
                            return
                        last_commit = {"plan_id":preview["plan_id"],"at":time.time(),"changes":preview["change_count"]}
                    job["status"] = {"state":"watching","last_commit":last_commit}
        except Exception as error:
            job["status"] = {"state":"blocked","last_commit":last_commit,"error":str(error)}
            return
        job["status"] = {"state":"stopped","last_commit":last_commit}

    def refresh_lease(self, token):
        with self.wb.store.connect() as db:
            updated = db.execute("UPDATE locks SET expires=? WHERE token=? AND expires>?",
                                 (time.time()+600,token,time.time()))
            if updated.rowcount != 1:
                raise ValueError("Automatic sync lease expired")

    def close(self):
        with self.lock:
            for job in self.jobs.values():
                job["stop"].set()
