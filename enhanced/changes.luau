local Http = game:GetService("HttpService")
local Editor = game:GetService("ScriptEditorService")
local History = game:GetService("ChangeHistoryService")
assert(#payload.items > 0 and #payload.items <= 30, "Invalid change set size")
local targets, seen = {}, {}
for _, item in payload.items do
	local current = game
	for _, name in item.path do
		local found
		for _, child in current:GetChildren() do
			if child.Name == name then
				assert(not found, "Ambiguous target")
				found = child
			end
		end
		assert(found, "Missing change set target")
		current = found
	end
	assert(current:IsA("LuaSourceContainer") and not seen[current], "Invalid or duplicate script")
	seen[current] = true
	assert(Editor:GetEditorSource(current) == item.expected, "Source changed before commit")
	table.insert(targets, { instance = current, before = item.expected, after = item.source })
end
local recording = History:TryBeginRecording("MCP coordinated script changes")
-- Studio's execute_luau already opens an Undo recording for the command.
assert(recording or History:IsRecordingInProgress(), "Cannot start undo recording")
local applied = {}
local ok, failure = pcall(function()
	for _, item in targets do
		local updated = false
		Editor:UpdateSourceAsync(item.instance, function(source)
			updated = false
			if source ~= item.before then
				return nil
			end
			updated = true
			return item.after
		end)
		assert(updated, "Concurrent source change during commit")
		table.insert(applied, item)
	end
end)
local rollbackErrors = {}
if not ok then
	for i = #applied, 1, -1 do
		local item = applied[i]
		local restored, err = pcall(function()
			local updated = false
			Editor:UpdateSourceAsync(item.instance, function(source)
				updated = false
				if source ~= item.after then
					return nil
				end
				updated = true
				return item.before
			end)
			assert(updated, "Manual edit preserved; rollback needs reconciliation")
		end)
		if not restored then
			table.insert(rollbackErrors, tostring(err))
		end
	end
end
if recording then
	History:FinishRecording(
		recording,
		if ok or #rollbackErrors > 0 then Enum.FinishRecordingOperation.Commit else Enum.FinishRecordingOperation.Cancel
	)
end
return Http:JSONEncode({
	success = ok,
	changed = if ok then #applied else 0,
	error = if ok then nil else tostring(failure),
	rollback_errors = if #rollbackErrors > 0 then rollbackErrors else nil,
})
