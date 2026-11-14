"""Native Studio multiplayer and device simulation."""
import json
from pathlib import Path
import time
import uuid

from team import integer

ROOT = Path(__file__).resolve().parent


def schema(tool):
    string = {"type":"string"}
    return [
        tool("multiplayer_test", "Run a server assertion with real simulated Studio clients. The test ends when the assertion passes or times out.", {
            "studio_id":string,"players":{"type":"integer","minimum":2,"maximum":8},
            "assert_code":string,"timeout":{"type":"integer","minimum":5,"maximum":60},
        }, ("studio_id","players","assert_code")),
        tool("device_test", "List Studio device presets or run one saved scenario sequentially across presets and restore the prior device configuration.", {
            "studio_id":string,"action":{"enum":["list","run"]},"name":string,
            "version":{"type":"integer"},"devices":{"type":"array","minItems":1,"maxItems":6,"items":string},
        }, ("studio_id","action")),
    ]


class StudioTesting:
    def __init__(self, wb):
        self.wb = wb

    def luau(self, studio, payload, code, readonly=False, timeout=None):
        raw = json.dumps(payload, ensure_ascii=True, allow_nan=False)
        prefix = 'local payload=game:GetService("HttpService"):JSONDecode(' + json.dumps(raw) + ')\n'
        rpc = self.wb.read_rpc if readonly else self.wb.rpc
        result = rpc("tools/call",{"name":"execute_luau","arguments":{
            "studio_id":studio,"datamodel_type":"Edit","code":prefix+code,
        }}, **({"timeout":timeout} if timeout is not None else {}))
        if result.get("isError"):
            raise ValueError(str(result.get("content")))
        return json.loads(result["content"][0]["text"])

    def multiplayer(self, args):
        studio = args["studio_id"]
        count = integer(args["players"],2,8,"players")
        timeout = integer(args.get("timeout",25),5,60,"timeout")
        assertion = args["assert_code"]
        if not isinstance(assertion,str) or not 0 < len(assertion.encode("utf-8")) <= 50000:
            raise ValueError("Expected a server-side Luau assertion of at most 50 KB")
        self.wb.team.authorize_studio_action(studio)
        with self.wb.store.lease("studio:"+studio,self.wb.owner):
            result = self.server_assertion(studio, assertion, count, timeout)
        return {"requested_players":count,"blocked":not result["passed"],**result}

    def server_assertion(self, studio, assertion, players, timeout, single_play=False, server_only=False):
        token = uuid.uuid4().hex
        payload = {"assertion":assertion,"players":players,"timeout":timeout,"token":token,
                   "singlePlay":single_play,"serverOnly":server_only}
        code = (ROOT/"studio_test.luau").read_text(encoding="utf-8")
        deadline = time.monotonic()+timeout+45
        launch_error = None
        if single_play:
            # The Edit command returns after Studio restores Edit and removes the runner.
            try:
                return self.luau(studio,payload,code,timeout=timeout+40)
            except ValueError as error:
                if "Target is not reachable" not in str(error):
                    raise
                # Studio can lose the original reply during a DataModel transition.
                # Read the same test receipt. Never launch the assertion a second time.
                launch_error = error
        else:
            self.luau(studio,payload,code)
        while time.monotonic() < deadline:
            time.sleep(0.2 if single_play else 0.75)
            try:
                status = self.luau(studio,{"token":token},'''
local result = shared.__StudioMCPMultiplayerResults and shared.__StudioMCPMultiplayerResults[payload.token]
return game:GetService("HttpService"):JSONEncode(result or {state = "unavailable"})
''', readonly=True, timeout=4)
            except (ValueError,ConnectionError,TimeoutError):
                continue
            if status["state"] == "finished":
                return status["result"]
            if launch_error is not None and status["state"] == "unavailable":
                raise launch_error
        raise TimeoutError(f"Studio did not return a test result (test {token}). Check the Studio session.")

    def device(self, studio, action, config=None):
        return self.luau(studio,{"action":action,"config":config},'''
local Http = game:GetService("HttpService")
local simulator = game:GetService("StudioDeviceSimulatorService")
if payload.action == "list" then
	return Http:JSONEncode(simulator:GetDeviceListAsync())
end
if payload.action == "set" then
	assert(table.find(simulator:GetDeviceListAsync(), payload.config.device), "Unknown Studio device")
	simulator:SetDeviceAsync(payload.config.device)
elseif payload.action == "restore" then
	local config = payload.config
	if config.device then
		simulator:SetDeviceAsync(config.device)
		simulator:SetOrientationAsync(Enum.ScreenOrientation[config.orientation])
		simulator:SetResolutionAsync(config.width, config.height)
		simulator:SetPixelDensityAsync(config.dpi)
		simulator:SetScalingModeAsync(Enum.DeviceSimulatorScalingMode[config.scaling])
	else
		simulator:StopSimulationAsync()
	end
	return Http:JSONEncode({restored = true})
end
local active, id = pcall(simulator.GetDeviceAsync, simulator)
if not active or not id or id == "" or id == "default" then
	assert(payload.action == "get", "Device selection did not activate simulation")
	return Http:JSONEncode({})
end
local resolution = simulator:GetResolutionAsync()
return Http:JSONEncode({
	device = simulator:GetDeviceAsync(), width = resolution.X, height = resolution.Y,
	orientation = simulator:GetOrientationAsync().Name,
	dpi = simulator:GetPixelDensityAsync(), scaling = simulator:GetScalingModeAsync().Name,
})
''', readonly=action in ("list","get"))

    def devices(self, args):
        studio = args["studio_id"]
        if args["action"] == "list":
            return {"devices":self.device(studio,"list")}
        if args["action"] != "run":
            raise ValueError("Unknown device test action")
        self.wb.team.authorize_studio_action(studio)
        devices = args["devices"]
        if not isinstance(devices,list) or not 1 <= len(devices) <= 6:
            raise ValueError("Choose 1..6 presets from the Studio device list")
        reports = []
        with self.wb.store.lease("studio:"+studio,self.wb.owner):
            state = self.wb.read_rpc("tools/call",{"name":"get_studio_state","arguments":{"studio_id":studio}})
            if state.get("isError") or not any("Current Studio Mode: Edit" in block.get("text","") for block in state["content"]):
                raise ValueError("Device tests must start in Edit mode")
            available = self.device(studio,"list")
            if any(device not in available for device in devices):
                raise ValueError("Choose presets from the Studio device list")
            original = self.device(studio,"get")
            try:
                for device in devices:
                    configured = self.device(studio,"set",{"device":device})
                    result = self.wb.workflows.scenario({**args,"action":"run"}, studio_locked=True)
                    reports.append({"device":configured,"run_id":result["run_id"],"passed":result["passed"],
                                    "failures":result.get("failures", [])})
            finally:
                self.device(studio,"restore",original)
        passed = all(report["passed"] for report in reports)
        return {"passed":passed,"blocked":not passed,"runs":reports}
