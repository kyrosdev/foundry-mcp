local function snapshotResponse(encoded)
	if #encoded <= 32000 then
		return encoded
	end
	local Http = game:GetService("HttpService")
	shared.__StudioMCPAnalysisSnapshots = shared.__StudioMCPAnalysisSnapshots or {}
	local snapshots = shared.__StudioMCPAnalysisSnapshots
	for key, value in snapshots do
		if os.clock() - value.created >= 120 then
			snapshots[key] = nil
		end
	end
	local token = Http:GenerateGUID(false)
	snapshots[token] = { json = encoded, created = os.clock() }
	local block = string.sub(encoded, 1, 32000)
	local hex = (
		string.gsub(block, ".", function(character)
			return string.format("%02x", string.byte(character))
		end)
	)
	return Http:JSONEncode({ token = token, hex = hex, next_offset = #block, done = false, bytes = #encoded })
end
