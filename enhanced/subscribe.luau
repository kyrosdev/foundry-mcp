local Http = game:GetService("HttpService")
local Editor = game:GetService("ScriptEditorService")
shared.__StudioMCPSubscriptions = shared.__StudioMCPSubscriptions or {}
local subscriptions = shared.__StudioMCPSubscriptions
local previousSequence = if subscriptions[payload.id] then subscriptions[payload.id].seq else 0
if subscriptions[payload.id] then
	subscriptions[payload.id].close()
end
local root = game
for _, name in payload.path do
	local found
	for _, child in root:GetChildren() do
		if child.Name == name then
			assert(not found, "Ambiguous subscription path")
			found = child
		end
	end
	assert(found, "Subscription root not found")
	root = found
end
local propertySignals = {}
for _, property in payload.properties do
	local ok, signal = pcall(function()
		return root:GetPropertyChangedSignal(property)
	end)
	assert(ok, "Invalid observed property: " .. property)
	propertySignals[property] = signal
end
local state = {
	active = true,
	seq = previousSequence,
	ring = {},
	connections = {},
	expires = os.clock() + 90,
	dropped = 0,
	pending = false,
}
subscriptions[payload.id] = state
function state.close()
	state.active = false
	for connection in state.connections do
		connection:Disconnect()
	end
	subscriptions[payload.id] = nil
end
local function pathOf(instance)
	local path = {}
	while instance and instance ~= game do
		table.insert(path, 1, instance.Name)
		instance = instance.Parent
	end
	return path
end
local send
send = function()
	state.pending = false
	if not state.active then
		return false
	end
	if state.sending then
		return false
	end
	local events = {}
	for _, value in state.ring do
		if value.seq > (state.sent_seq or 0) then
			table.insert(events, value)
		end
		if #events >= 100 then
			break
		end
	end
	if #events == 0 then
		return true
	end
	state.sending = true
	local ok, response = pcall(function()
		return Http:RequestAsync({
			Url = payload.url,
			Method = "POST",
			Headers = { ["X-MCP-Token"] = payload.token, ["Content-Type"] = "application/json" },
			Body = Http:JSONEncode({
				events = events,
				dropped = state.dropped,
				source_coverage_limited = state.coverage_limited or false,
			}),
		})
	end)
	state.sending = false
	local success = ok and response.Success
	if success then
		state.sent_seq = events[#events].seq
		if state.active and state.seq > state.sent_seq and not state.pending then
			state.pending = true
			task.delay(0.1, send)
		end
	elseif state.mode == "push" then
		state.mode = "signal_buffer"
	end
	return success
end
local function record(kind, instance, property)
	if not state.active then
		return
	end
	state.seq += 1
	table.insert(
		state.ring,
		{ seq = state.seq, kind = kind, path = pathOf(instance), property = property, at = DateTime.now():ToIsoDate() }
	)
	if #state.ring > 256 then
		table.remove(state.ring, 1)
		state.dropped += 1
	end
	if state.mode == "push" and not state.pending then
		state.pending = true
		task.delay(0.1, send)
	end
end
local function connect(signal, callback)
	local connection = signal:Connect(callback)
	state.connections[connection] = true
	return connection
end
local sourceConnections = {}
local sourceCount = 0
local function attachSource(instance)
	if not instance:IsA("LuaSourceContainer") or sourceConnections[instance] then
		return
	end
	if sourceCount >= 1000 then
		state.coverage_limited = true
		record("source_coverage_limit", instance)
		return
	end
	local ok, signal = pcall(function()
		return instance:GetPropertyChangedSignal("Source")
	end)
	if not ok then
		state.coverage_limited = true
		record("source_observation_unavailable", instance)
		return
	end
	sourceConnections[instance] = connect(signal, function()
		record("source", instance, "Source")
	end)
	sourceCount += 1
end
connect(root.DescendantAdded, function(instance)
	attachSource(instance)
	record("added", instance)
end)
connect(root.DescendantRemoving, function(instance)
	local connection = sourceConnections[instance]
	if connection then
		connection:Disconnect()
		state.connections[connection] = nil
		sourceConnections[instance] = nil
		sourceCount -= 1
	end
	record("removing", instance)
end)
attachSource(root)
for _, instance in root:GetDescendants() do
	attachSource(instance)
end
connect(root.AttributeChanged, function(name)
	record("attribute", root, name)
end)
connect(root.Destroying, function()
	record("destroying", root)
	task.defer(state.close)
end)
for _, property in payload.properties do
	connect(propertySignals[property], function()
		record("property", root, property)
	end)
end
connect(Editor.TextDocumentDidChange, function(document)
	local ok, instance = pcall(function()
		return document:GetScript()
	end)
	if ok and instance and (instance == root or instance:IsDescendantOf(root)) then
		record("source", instance)
	end
end)
record("ready", root)
state.mode = if send() then "push" else "signal_buffer"
task.spawn(function()
	while state.active and os.clock() < state.expires do
		task.wait(5)
	end
	if state.active then
		state.close()
	end
end)
return Http:JSONEncode({
	mode = state.mode,
	active = true,
	expires_without_renewal_seconds = 90,
	source_coverage_limited = state.coverage_limited or false,
})
