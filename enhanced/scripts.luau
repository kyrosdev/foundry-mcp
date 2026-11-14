local Http = game:GetService("HttpService")
local Editor = game:GetService("ScriptEditorService")
local current = game
for _, name in ipairs(payload.path) do
	local found
	for _, child in current:GetChildren() do
		if child.Name == name then
			assert(not found, "Ambiguous sibling: " .. name)
			found = child
		end
	end
	assert(found, "Missing path segment: " .. name)
	current = found
end
assert(current:IsA("LuaSourceContainer"), "Target must be a script")
local before = Editor:GetEditorSource(current)
if payload.source ~= nil then
	assert(before == payload.expected, "Conflict: editor source changed")
	local History = game:GetService("ChangeHistoryService")
	local recording = History:TryBeginRecording("MCP guarded script write")
	-- Reuse the surrounding Studio command's Undo recording when present.
	assert(recording or History:IsRecordingInProgress(), "Cannot begin undo recording")
	local applied = false
	local ok, err = pcall(function()
		Editor:UpdateSourceAsync(current, function(source)
			applied = false
			if source ~= payload.expected then
				return nil
			end
			applied = true
			return payload.source
		end)
	end)
	if recording then
		History:FinishRecording(
			recording,
			if ok and applied then Enum.FinishRecordingOperation.Commit else Enum.FinishRecordingOperation.Cancel
		)
	end
	assert(ok, err)
	assert(applied, "Conflict: editor source changed during update")
end
local encoded = Http:JSONEncode({ source = Editor:GetEditorSource(current), class = current.ClassName })
if #encoded <= 32000 then
	return encoded
end
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
local hex = (string.gsub(block, ".", function(character)
	return string.format("%02x", string.byte(character))
end))
return Http:JSONEncode({ token = token, hex = hex, next_offset = #block, done = false, bytes = #encoded })
