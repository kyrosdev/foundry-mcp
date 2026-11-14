local Http = game:GetService("HttpService")
local serializer = game:GetService("SerializationService")
local instances = {}
for _, path in payload.paths do
	local instance = game
	for _, name in path do
		local match
		for _, child in instance:GetChildren() do
			if child.Name == name then
				assert(not match, "Ambiguous model path")
				match = child
			end
		end
		assert(match, "Missing model path")
		instance = match
	end
	assert(instance.Archivable, "Selected object is not archivable")
	table.insert(instances, instance)
end
local data = buffer.tostring(serializer:SerializeInstancesAsync(instances))
assert(#data > 0 and #data <= 10000000, "Model export exceeds the 10 MB limit")
shared.__StudioMCPAnalysisSnapshots = shared.__StudioMCPAnalysisSnapshots or {}
local snapshots = shared.__StudioMCPAnalysisSnapshots
for key, snapshot in snapshots do
	if os.clock() - snapshot.created >= 120 then
		snapshots[key] = nil
	end
end
local token = Http:GenerateGUID(false)
snapshots[token] = {json = data, created = os.clock()}
payload = {token = token, offset = 0}
