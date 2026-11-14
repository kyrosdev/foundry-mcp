local Http = game:GetService("HttpService")
local Tests = game:GetService("StudioTestService")
assert(Tests.EditModeActive, "Studio tests must start in Edit mode")
local storage = game:GetService("ServerScriptService")
local name = "__StudioMCPTest_" .. payload.token
local scriptSource = [==[
local tests = game:GetService("StudioTestService")
local args = tests:GetTestArgs()
if type(args) ~= "table" or args.token ~= script.Name then return end
local players = game:GetService("Players")
task.wait()
local deadline = os.clock() + args.timeout
local result = { passed = false, players = 0 }
local finished = false
local playerAdded, playerRemoving
local function finish()
	if finished then return end
	finished = true
	if playerAdded then playerAdded:Disconnect() end
	if playerRemoving then playerRemoving:Disconnect() end
	tests:EndTest(result)
end
task.delay(args.timeout, function()
	if finished then return end
	result.error = "Server assertion timed out"
	finish()
end)
local loaded, verify = pcall(require, script.Assertion)
if not loaded then
	result.error = tostring(verify)
	finish()
	return
end
local function verifyPlayers()
	result.players = #players:GetPlayers()
	if result.players == args.players then
		result.asserted = true
		local ok, value = pcall(verify)
		if finished then return true end
		if not ok then
			result.error = tostring(value)
			return true
		end
		if value == true then
			result.passed = true
			return true
		end
		if args.singlePlay then
			result.error = "Scenario assertion must return true"
			return true
		end
	end
	return false
end
if args.singlePlay then
	local function check()
		if finished or result.asserted then return end
		if verifyPlayers() then finish() end
	end
	-- PlayerRemoving fires before the player leaves the service.
	playerAdded = players.PlayerAdded:Connect(function() task.defer(check) end)
	playerRemoving = players.PlayerRemoving:Connect(function() task.defer(check) end)
	check()
	return
end
repeat
	if verifyPlayers() then break end
	task.wait(0.1)
until os.clock() >= deadline
if not result.passed and not result.error then
	result.error = "Expected player count and assertion were not observed before the timeout"
end
finish()
]==]
local runner = Instance.new("Script")
runner.Name = name
runner.Source = scriptSource
local assertion = Instance.new("ModuleScript")
assertion.Name = "Assertion"
assertion.Source = "return function()\n" .. payload.assertion .. "\nend"
assertion.Parent = runner
runner.Parent = storage
local args = {
	token = name,
	players = payload.players,
	timeout = payload.timeout,
	singlePlay = payload.singlePlay == true,
}
local function launchTest()
	local ok, result
	if payload.singlePlay then
		local launch = if payload.serverOnly then Tests.ExecuteRunModeAsync else Tests.ExecutePlayModeAsync
		ok, result = pcall(launch, Tests, args)
	else
		ok, result = pcall(Tests.ExecuteMultiplayerTestAsync, Tests, payload.players, args)
	end
	runner:Destroy()
	if ok and type(result) == "table" then
		return result
	end
	return { passed = false, error = if ok then "Studio ended without an assertion result" else tostring(result) }
end
shared.__StudioMCPMultiplayerResults = shared.__StudioMCPMultiplayerResults or {}
local results = shared.__StudioMCPMultiplayerResults
for token, result in results do
	if os.clock() - result.time > 600 then results[token] = nil end
end
results[payload.token] = { state = "running", time = os.clock() }
local function finishTest()
	local result = launchTest()
	results[payload.token] = {
		state = "finished",
		time = os.clock(),
		result = result,
	}
	return result
end
if payload.singlePlay then
	return Http:JSONEncode(finishTest())
end
task.delay(0.3, finishTest)
return Http:JSONEncode({ accepted = true })
